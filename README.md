# Xolis

Xolis is a Kubernetes-native sandbox service for AI agents that need to run untrusted workloads with strong isolation and fast provisioning.

## Architecture

Xolis combines the following components:

- Kubernetes Agent Sandbox for declarative sandbox lifecycle management.
- Kata Containers with the Rust runtime and Dragonball VMM for VM-level workload isolation.
- containerd and the Nydus snapshotter for OCI image management and lazy image loading.
- Optional PVM support for selected nested-virtualization environments.
- Optional Confidential Containers support for confidential-computing workloads.

The high-level architecture is described in [Docs/General-Arch.md](Docs/General-Arch.md).

## Status

The project is in its architecture and design phase. Runtime components, deployment manifests, APIs, and operational documentation have not been implemented yet.

## Intended Languages

Xolis may use Rust, Python, and Shell as implementation work begins.

## License

Licensed under the [Apache License 2.0](LICENSE).
