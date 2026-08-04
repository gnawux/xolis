# General Architecture

Xolis is a cloud-native sandbox service for agents that execute untrusted
workloads.

The first service-layer design for the validated AWS Kata environment is
documented in [Initial Sandbox Service Design](Initial-Sandbox-Service-Design.md).

## Highlights

The architecture combines:

- [Kata Containers](https://katacontainers.io/) with runtime-rs and Dragonball
  for VM-backed workload isolation.
- Kubernetes SIG [Agent Sandbox](https://agent-sandbox.sigs.k8s.io/) for
  declarative sandbox lifecycle management.
- containerd as the Kubernetes CRI runtime.
- [Nydus](https://nydus.dev/) and [Dragonfly](https://d7y.io/) as optional image
  lazy-loading and distribution optimizations.
- [PVM (Pagetable-based Virtual Machine)](https://lpc.events/event/18/contributions/1766/)
  as an experimental alternative host-virtualization path for environments
  that cannot expose hardware virtualization extensions.

The implementation and qualification sequence for the PVM path is documented
in [PVM Development and Test Plan](PVM-Development-and-Test-Plan.md).

## Deployment Architecture

The system is deployed on Kubernetes, using a cloud-managed control plane where
available. Agent Sandbox manages the lifecycle objects, while Kata Containers
provides the workload isolation boundary on a dedicated sandbox node pool.
Ordinary OCI images are the validated baseline and can be stored in any
compatible registry, including Amazon ECR or Harbor. Nydus-formatted images can
be introduced later without changing the public sandbox API.

## Node Architecture

The validated AWS node architecture uses:

- An EKS-compatible Amazon Linux 2023 kernel with nested KVM exposed on a
  supported M8i instance.
- containerd with the `xolis-kata` runtime handler.
- Kata Containers 4.0.0 runtime-rs with its built-in Dragonball VMM.
- A dedicated self-managed Auto Scaling group with labels and taints that prevent
  ordinary workloads from selecting the sandbox runtime accidentally.
- Amazon VPC CNI network-policy enforcement for sandbox ingress and egress.

Nydus is a validated opt-in image path, while PVM is an experimental
host-virtualization path. Dragonfly, Kata template support, TSI, and
Confidential Containers remain follow-up capabilities. None is required by the
stable native-KVM functional baseline.

## Experimental PVM Node Architecture

The verified PVM foundation keeps the public sandbox API and Kata guest model
unchanged while replacing the host virtualization path. The matched stack is:

- a pinned Linux 6.12.33 PVM host kernel booted with `pti=off` and `kvm-pvm`;
- a guest kernel built from the same pinned PVM source revision;
- Kata Containers 4.0.0 runtime-rs with upstream Dragonball and the dedicated
  `xolis-kata-pvm` containerd handler; and
- versioned kernel and runtime bundles with manifests and SHA-256 checksums.

This stack has passed standalone host, containerd, and CRI qualification on an
AWS `c7i.4xlarge` that exposed neither `vmx` nor `svm`. The immutable PVM AMI
has also passed EKS 1.35 node registration, CNI, Kubernetes scheduling, Xolis
lifecycle, streaming, interactive PTY, warm-pool, node-loss recovery, and
native-KVM regression qualification on an isolated `c7i.xlarge` pool. The
deployment architecture keeps PVM in a separate AMI, node group, label and
taint domain, RuntimeClass, sandbox profile, and observability dimension. There
is no automatic fallback between PVM and native KVM.

The remaining architectural work is to formalize the provider-neutral capacity
contract while AWS provisioning remains an adapter outside the public service
API, and to complete model-backed agent, failure-injection, and PVM kernel
operations gates. A bounded one-node cold/warm comparison validates the
warm-pool mechanism but does not establish scale or production performance.
