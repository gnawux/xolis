# Xolis

Xolis is a Kubernetes-native sandbox service for AI agents that need to run untrusted workloads with strong isolation and fast provisioning.

## Architecture

Xolis combines the following components:

- Kubernetes Agent Sandbox for declarative sandbox lifecycle management.
- Kata Containers with the Rust runtime and Dragonball VMM for VM-level workload isolation.
- containerd for OCI image management, with the Nydus snapshotter as an optional
  lazy-loading optimization.
- Optional PVM support for selected nested-virtualization environments.
- Optional Confidential Containers support for confidential-computing workloads.

The high-level architecture is described in [Docs/General-Arch.md](Docs/General-Arch.md).
The initial service decomposition, API boundary, and image plan are described in
[Docs/Initial-Sandbox-Service-Design.md](Docs/Initial-Sandbox-Service-Design.md).
The consolidated implementation status, measured performance, availability
boundary, and roadmap are described in
[Docs/Project-Status-and-Roadmap.md](Docs/Project-Status-and-Roadmap.md).

## Status

The current AWS lab includes OpenTofu infrastructure, a disposable test-cycle
tool, a custom Kata 4.0.0 runtime-rs and Dragonball AMI, Agent Sandbox v0.5.3,
and the Rust Xolis API with a bounded Python runtime. The automated service
acceptance test validates Kata placement, buffered and streaming commands,
interactive PTY sessions, file operations, tenant isolation, request limits,
network policy, deletion, and TTL cleanup. The Lab tool also automates
cold-versus-warm `SandboxWarmPool` measurements with structured JSON reports.
An independently selected Nydus path and Hermes Agent profile have passed
bounded single-node validation; ordinary OCI remains the default. See
[Docs/AWS-Lab-Tooling.md](Docs/AWS-Lab-Tooling.md) for prerequisites and test
workflows.

## Intended Languages

Xolis uses Rust for the service API, Python for the initial sandbox runtime and
lab automation, and Shell for focused build and deployment tasks.

## License

Licensed under the [Apache License 2.0](LICENSE).
