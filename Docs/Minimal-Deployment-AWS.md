# Minimal Deployment on AWS

This document defines the smallest practical AWS environment for validating Xolis. It is a development and architecture-validation deployment, not a production reference architecture.

## Decision Summary

Amazon EKS is a suitable control plane for Xolis when sandbox workloads run on Amazon EC2 worker nodes.

Do not use EKS Fargate or EKS Auto Mode for the Xolis sandbox pool. Neither option provides the host-level control required to install Kata Containers, configure containerd runtime handlers and snapshotters, or select a custom kernel and AMI.

Use the following initial design:

- An EKS-managed Kubernetes control plane.
- One standard EKS managed node group for system workloads.
- One self-managed EC2 node group dedicated to sandbox workloads.
- A custom, EKS-compatible Amazon Linux 2023 AMI for the sandbox node group.
- A supported EC2 instance type with nested virtualization enabled, such as M7i or C7i.
- Kata Containers with the Rust runtime and Dragonball as a separate containerd runtime handler.
- containerd with the Nydus snapshotter configured on the sandbox node group.

PVM is not an initial requirement on AWS. AWS supports nested KVM virtualization on selected virtual EC2 instance families. PVM remains an optional fallback or research path for environments without usable hardware virtualization extensions.

## EKS Compatibility

### Container Runtime

EKS worker nodes use containerd as their Kubernetes CRI runtime. Kubernetes selects an alternative sandbox runtime through a RuntimeClass. Its handler must correspond to a handler configured in containerd on every eligible node.

Xolis should retain containerd as the node CRI and add a Kata handler, for example, kata-dragonball. It should not attempt to replace Kubernetes CRI with an unrelated runtime.

The sandbox AMI must provide all of the following:

- containerd with a working CRI plugin.
- A Kata Containers shim and configuration for the selected VMM.
- A containerd runtime handler for Kata.
- A Nydus snapshotter configuration, if Nydus is enabled.
- Node labels and taints that restrict the Kata runtime to the sandbox node group.

Managed node groups support custom AMIs through a launch template, but a custom AMI becomes the operator's patching responsibility. AWS recommends self-managed node groups when full control of the AMI, kernel parameters, container runtime configuration, or bootstrap process is required. Therefore, a self-managed sandbox node group is the recommended first implementation.

### Operating System and Kernel

Amazon Linux 2023 is the default operating system family for new EKS managed node groups on supported EKS versions and uses cgroup v2. It is a suitable base for a custom sandbox AMI.

The initial AMI should start from EKS-optimized AL2023 build inputs and add Kata Containers, Nydus, and Xolis node configuration. It must preserve the EKS node bootstrap process. For AL2023, node initialization is performed by nodeadm; do not invoke nodeadm init a second time in the AMI build or EC2 user data.

Do not introduce a patched PVM kernel in the first AWS proof of concept. First verify the standard AL2023 kernel with KVM exposed by nested virtualization. Build a separate experimental AMI only if this validation fails or if PVM is explicitly being evaluated.

### Nested Virtualization

Kata with an in-node VMM requires hardware virtualization. AWS supports nested virtualization for KVM on selected virtual EC2 instance types, including C7i and M7i families. Enable nested virtualization in the EC2 launch template through CPU options:

    NestedVirtualization=enabled

Validate the resulting node before installing the Xolis runtime:

    test -e /dev/kvm
    lsmod | grep '^kvm'
    egrep -c '(vmx|svm)' /proc/cpuinfo

The first command must succeed. The final command should report one or more virtualization CPU flags. If either check fails, do not schedule Kata workloads on the node.

## Minimal Topology

The EKS control plane is managed by AWS and does not require user-operated control-plane instances.

One EC2 worker node is technically sufficient for an end-to-end demonstration because Kubernetes system components, the Agent Sandbox controller, and one sandbox workload can share that node. This configuration is not recommended because a node fault removes both the control components and sandbox capacity.

The recommended minimum is two worker nodes:

| Pool | Count | Suggested starting instance | Purpose |
| --- | ---: | --- | --- |
| System managed node group | 1 | t3.large | CoreDNS, EKS add-ons, Agent Sandbox controller, observability, and general workloads. |
| Sandbox self-managed node group | 1 | m7i.xlarge | Kata, Nydus, and one or more isolated sandbox Pods. Enable nested virtualization. |

The node counts above are for a non-production proof of concept. For availability testing, use at least two system nodes across two Availability Zones and at least two sandbox nodes. Size sandbox nodes from measured Kata VM memory overhead and concurrent sandbox demand rather than from the example instance size.

## AWS Prerequisites

Before creating the cluster, prepare the following:

1. An AWS account with billing enabled and a target Region where EKS and the selected nested-virtualization instance type are available.
2. An IAM principal permitted to create or manage EKS, EC2, VPC, IAM, CloudFormation, ECR, and CloudWatch resources.
3. A distinct EKS cluster IAM role and EC2 node IAM role. The node role needs at least EKS worker-node and ECR pull permissions.
4. A VPC with non-overlapping IPv4 ranges, at least two subnets in separate Availability Zones for the EKS control plane, and DNS support enabled.
5. Network egress from worker nodes to the EKS API, ECR, S3, and required public registries. For private subnets, provide a NAT gateway or the required VPC endpoints.
6. Local tooling: AWS CLI, eksctl, kubectl, helm, Packer or an equivalent AMI build tool, and a container image build tool.
7. An image registry for Xolis runtime images. Amazon ECR is the default AWS choice.

For a low-cost development experiment, worker nodes can use public subnets with tightly restricted security groups. A persistent environment should use private worker subnets, private EKS endpoint access where feasible, and VPC endpoints instead of general Internet egress.

## Deployment Sequence

### 1. Create the EKS Control Plane

Create a standard EKS cluster with IPv4 networking and the default EKS add-ons:

- Amazon VPC CNI.
- CoreDNS.
- kube-proxy.

Disable EKS Auto Mode. Do not create a Fargate profile for Xolis namespaces.

### 2. Create the System Node Group

Create a one-node EKS managed node group using the current EKS-optimized AL2023 AMI. Reserve this pool for Kubernetes and Xolis control-plane workloads.

Apply a system label and, if required, a taint that prevents sandbox workloads from landing on it:

    node-role.xolis.io/system=true

### 3. Build the Sandbox AMI

Build and publish a versioned AMI from the EKS-compatible AL2023 base. The build should:

1. Install a pinned Kata Containers release and its guest kernel and root filesystem.
2. Install and configure the Nydus snapshotter when lazy loading is part of the test.
3. Configure containerd with the Nydus proxy plugin and the Kata runtime handler.
4. Enable and validate required system services.
5. Add node bootstrap configuration without re-running nodeadm init.
6. Record the AMI, kernel, containerd, Kata, and Nydus versions as image metadata.

Treat the AMI as an immutable release artifact. Rebuild and roll out a new version for operating-system or runtime security updates.

### 4. Create the Sandbox Node Group

Create a self-managed Auto Scaling group with a launch template that:

- Uses the custom sandbox AMI.
- Uses a supported C7i or M7i instance type.
- Enables nested virtualization through EC2 CPU options.
- Attaches the EKS node instance profile.
- Places the node in the sandbox subnet.
- Sets a minimum, desired, and maximum size of one for the initial test.

Bootstrap the node into the EKS cluster and label it:

    node-role.xolis.io/sandbox=true
    xolis.io/kata-ready=true

Taint the node so only sandbox workloads with a matching toleration can run there:

    xolis.io/sandbox=true:NoSchedule

### 5. Install Runtime Components

Install the Agent Sandbox CRDs and controller on the system node group. Install Xolis node configuration and any required DaemonSets only on the sandbox node group.

Create a Kubernetes RuntimeClass that refers to the Kata containerd handler and restricts scheduling to sandbox nodes:

    apiVersion: node.k8s.io/v1
    kind: RuntimeClass
    metadata:
      name: xolis-kata
    handler: kata-dragonball
    scheduling:
      nodeSelector:
        xolis.io/kata-ready: "true"
      tolerations:
        - key: xolis.io/sandbox
          operator: Equal
          value: "true"
          effect: NoSchedule

The handler name is an example. It must exactly match the containerd configuration in the custom AMI.

### 6. Validate the First Sandbox

Before testing agent execution, verify the host and runtime in this order:

1. The sandbox node is Ready and has the expected labels and taint.
2. The /dev/kvm device exists on the sandbox node.
3. containerd reports the Kata runtime handler and Nydus snapshotter as healthy.
4. A minimal Pod with runtimeClassName set to xolis-kata starts successfully.
5. The Pod runs inside a Kata VM, as shown by Kata runtime logs and the guest environment.
6. A Nydus image is pulled and read on demand, if Nydus is enabled.
7. An Agent Sandbox resource creates, reaches readiness, executes a harmless command, and is deleted.
8. A network policy denies unapproved egress from the sandbox namespace.

Do not expose a general code-execution endpoint until these checks pass.

## Security Baseline

Apply the following controls from the first deployment:

- Use a dedicated sandbox node group and do not co-locate production application workloads there.
- Use RuntimeClass, node selectors, taints, and tolerations to prevent runtime bypass.
- Use namespace-scoped RBAC and separate IAM roles for the control plane, nodes, and Pods.
- Default-deny egress from sandbox namespaces; explicitly allow only required registry, package-mirror, and service destinations.
- Restrict Kubernetes API endpoint access and EC2 security-group ingress.
- Pin and scan runtime images and AMI build inputs.
- Send EKS control-plane, node, containerd, Kata, and Agent Sandbox logs to CloudWatch or an equivalent centralized system.
- Configure a sandbox TTL and verify that storage, credentials, and temporary artifacts are removed on termination.

## Explicit Non-Goals for the First Deployment

The first deployment does not require:

- PVM or a custom patched kernel.
- Confidential Containers or a key broker service.
- GPU passthrough.
- Multi-region deployment.
- High availability for sandbox capacity.
- A production-grade image cache or peer-to-peer Dragonfly deployment.

These capabilities should be added only after the basic Kata and Nydus execution path is reproducible.

## References

- [Amazon EKS optimized Amazon Linux AMIs](https://docs.aws.amazon.com/eks/latest/userguide/eks-optimized-ami.html)
- [Amazon EKS managed node groups](https://docs.aws.amazon.com/eks/latest/userguide/managed-node-groups.html)
- [Amazon EKS guidance on managed and self-managed node groups](https://docs.aws.amazon.com/eks/latest/userguide/ml-node-groups.html)
- [Amazon Linux 2023 node initialization on EKS](https://docs.aws.amazon.com/eks/latest/userguide/al2023.html)
- [Nested virtualization on Amazon EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html)
- [Amazon EKS node IAM role](https://docs.aws.amazon.com/eks/latest/userguide/create-node-role.html)
- [Amazon EKS Fargate limitations](https://docs.aws.amazon.com/eks/latest/userguide/fargate.html)
