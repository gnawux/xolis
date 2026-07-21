# PVM Evaluation on AWS

This document records the initial case for evaluating Pagetable-based Virtual Machine (PVM) in Xolis on AWS. It compares the infrastructure flexibility offered by PVM with AWS nested virtualization. It does not make performance or production-readiness claims.

## Scope

Xolis uses Kata Containers to isolate untrusted agent workloads in lightweight virtual machines. A Kata VMM normally needs usable hardware virtualization extensions on the Kubernetes worker node.

AWS now supports nested virtualization for KVM on selected virtual EC2 instance families. PVM is relevant when that capability is unavailable, unsuitable, or too capacity-constrained for a target deployment.

## What PVM Changes

PVM is a software-implemented paravirtualized flavor of KVM for x86. Its intended use case includes running secure containers when nested virtualization is disabled and hardware virtualization assistance is unavailable.

PVM does not remove the need for a guest kernel, guest root filesystem, virtual devices, or VM memory. It solves a host virtualization compatibility problem. It does not, by itself, make individual Kata sandboxes smaller or less expensive.

## Benefits of PVM

### Broader Node Selection

Without PVM, the initial AWS design must select an EC2 family that AWS explicitly supports for nested virtualization. At the time of this assessment, the supported list includes C7i, M7i, R7i, C8i, M8i, R8i, and selected related families.

With PVM, Xolis may be able to evaluate x86 node families that do not expose hardware virtualization extensions. This may increase the number of compatible instance families, Regions, Availability Zones, and capacity pools.

### Better Capacity and Spot Flexibility

PVM could provide a fallback when an otherwise suitable EC2 capacity pool does not support nested virtualization or when the supported families have insufficient Spot capacity.

This is a capacity-flexibility benefit, not a fixed price reduction. Spot prices and available capacity vary by Availability Zone and over time. A separate runtime qualification is required before adding an instance type to a PVM-backed node group.

### Reduced Dependence on Provider-Specific Virtualization Features

The same PVM design could be evaluated on other x86 cloud providers or private-cloud environments that do not expose VT-x or AMD-V to guest instances. This is useful for portability and for a multi-cloud capacity strategy.

### Research Path for Restricted Environments

PVM preserves a path to Kata-style sandbox isolation in environments where native nested virtualization cannot be enabled because of provider policy, tenancy constraints, or instance-family limitations.

## AWS Price Snapshot

The following snapshot compares same-size Linux On-Demand instances. The generation-seven instance supports nested virtualization according to the current AWS support list. The generation-six counterpart is not listed as nested-virtualization capable.

Prices are in USD per hour and exclude EBS, data transfer, NAT gateways, load balancers, CloudWatch, EKS control-plane charges, Savings Plans, Reserved Instances, and Spot discounts. Monthly values use 730 hours.

| Region | General purpose, 2 vCPU / 8 GiB | Compute optimized, 2 vCPU / 4 GiB |
| --- | --- | --- |
| US East (N. Virginia), us-east-1 | m6i.large: 0.0960; m7i.large: 0.1008; premium: 5.0% | c6i.large: 0.0850; c7i.large: 0.0892; premium: 4.9% |
| Asia Pacific (Tokyo), ap-northeast-1 | m6i.large: 0.1240; m7i.large: 0.1302; premium: 5.0% | c6i.large: 0.1070; c7i.large: 0.1124; premium: 5.0% |
| Asia Pacific (Singapore), ap-southeast-1 | m6i.large: 0.1200; m7i.large: 0.1260; premium: 5.0% | c6i.large: 0.0980; c7i.large: 0.1029; premium: 5.0% |

For comparable On-Demand instances, the native nested-virtualization premium is approximately five percent. This is a small infrastructure-price difference and does not, on its own, justify the operational cost of PVM.

## Recommendation

Use native AWS nested virtualization as the default Xolis path. Begin with supported M7i or C7i nodes, a standard Amazon Linux 2023 kernel, and Kata Containers.

Keep PVM as an experimental capability with a separate AMI and node group. Consider activating it only when one or more of the following conditions are true:

- A target Region or Availability Zone lacks viable capacity for supported nested-virtualization instances.
- A Spot-capacity analysis shows a material and sustained advantage for a PVM-compatible pool.
- Xolis must run on an x86 provider or private-cloud environment that cannot expose hardware virtualization extensions.
- A validated PVM implementation provides an operational advantage that exceeds its custom-kernel and lifecycle cost.

Do not mix native-KVM Kata nodes and PVM-backed nodes in the same unlabelled capacity pool. Use separate labels, taints, RuntimeClasses, AMIs, and observability dimensions.

## Deferred Evaluation

The following questions are intentionally deferred to a controlled benchmark and compatibility effort:

- Sandbox cold-start latency.
- Steady-state CPU overhead.
- Memory overhead and sandbox density per node.
- I/O, network, and image-lazy-loading performance.
- Kernel and Kata compatibility.
- Security behavior and vulnerability response process.
- Upgrade, rollback, and node-replacement operations.
- Instance-family, Availability Zone, and Spot-capacity coverage.

The benchmark must compare like-for-like workloads, images, resource requests, Kubernetes versions, and Kata versions. It must report both per-sandbox performance and total cost per successful sandbox execution.

## References

- [PVM presentation at Linux Plumbers Conference 2024](https://lpc.events/event/18/contributions/1766/attachments/1498/3306/LPC-PVM.pdf)
- [AWS nested virtualization support](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html)
- [AWS Price List Query API](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-price-list-query-api.html)
