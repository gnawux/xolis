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
  as an optional research path for environments that cannot expose hardware
  virtualization extensions.

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

Nydus, Dragonfly, Kata template support, TSI, PVM, and Confidential Containers
are optional follow-up capabilities rather than requirements for the current
functional baseline.
