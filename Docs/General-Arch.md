This is the general architecture design of an sandbox service for agents.

## Highlights

There are several featured project or technologies will be used in the system:
- [**Kata Containers**](https://katacontainers.io/) for secure container sandbox with built-in rust-vmm dragonball for lower overhead.
- [**PVM (Pagetable-based Virtual Machine)**,](https://lpc.events/event/18/contributions/1766/) a KVM driver that enables run VMM on any host even those virtual machine without hardware VT extensions.
- [**Dragonfly**](https://d7y.io/), the CNCF project for container image P2P download and lazy load acceleration.
- Kubernetes SIG[ **Agent Sandbox**](https://agent-sandbox.sigs.k8s.io/) for standardized sandbox life-cycle management.

## Deployment Architecture

The system will be deployed on a cloud-native architecture:
- The whole system is built upon Kubernetes, could be provided by the cloud provider.
- The container image format is defined by Nydus, a sub-project of Dragonfly, which is based on the OCI image artifact and could be stored any modern image registry, such as harbor.
- The sandbox could be launched by Agent Sandbox of Kubernetes.
- On the node, Kata Containers are employed to provide sandbox isolation.
## Node Architecture

On the node, we use standarded containerd + Kata Containers for sandboxes:
- Linux Kernel with pvm patch, which enables Kata Containers for any virtual machines, such as EC2 instances.
- containerd + Nydus snapshotter as CRI runtime management.
- Kata Containers equipped with runtime-rs, which has embedded VMM.
- Kata Containers template technology could be enabled to accelerating the boot procedure of sandboxes.
- Kata Containers has a feature named as TSI, which could forward network traffic inside the sandbox. This could be an advanced feature.
- Confidential Containers (CoCo) is optional.
