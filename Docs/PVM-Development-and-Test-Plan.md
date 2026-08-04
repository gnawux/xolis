# PVM Development and Test Plan

This document defines the implementation sequence and qualification gates for
running Xolis with Pagetable-based Virtual Machine (PVM), Kata Containers
runtime-rs, and the upstream Dragonball VMM. It turns the PVM roadmap item into
an executable development plan. It does not claim that the resulting path is
production-ready before every release gate in this document passes.

## 1. Objective and Boundaries

The target stack is fixed:

> PVM host and guest kernels, Kata Containers runtime-rs, and upstream
> Dragonball

Upstream runtime-rs guidance confirms that the PVM, Kata runtime-rs, and
Dragonball combination works. Xolis therefore treats that combination as a
valid implementation baseline. Development should reproduce the known-good
upstream path with pinned inputs, solve concrete integration defects, and
report generally applicable defects upstream.

Cloud Hypervisor is not an implementation target. QEMU with qboot may be used
only as a diagnostic reference when a failure cannot be assigned to the PVM
kernel and ABI or to Dragonball. Xolis will not build a QEMU or Cloud
Hypervisor deployment path as a prerequisite for Dragonball.

Native KVM with Dragonball remains the stable baseline and independent
fallback. PVM must use separate artifacts, configuration, node capabilities,
and scheduling. A PVM failure must never silently select native KVM, and a
native-KVM workload must never silently select PVM.

Initial scope is functional qualification on x86_64 without provider-supplied
VT-x or AMD-V. Large-cluster performance, density, Spot-market economics,
migration, snapshotting, and PMU virtualization are deferred.

## Current Qualification Status

As of August 4, 2026, the pinned PVM host and guest kernels boot successfully
on a `c7i.4xlarge` instance that exposes neither `vmx` nor `svm`. Kata 4.0.0
runtime-rs with upstream Dragonball has passed direct containerd and CRI
testing with one and two guest vCPUs, block rootfs, vsock, memory and time,
inline virtio-fs, xattrs, output and exit-code propagation, repeated lifecycle,
and cleanup. The PVM-specific handler is `xolis-kata-pvm`; the stable handler
remains unchanged.

The matching kernel and runtime bundles are retained in the private,
versioned bucket `s3://xolis-pvm-artifacts-479874045111-ap-northeast-1/` under
the exact PVM and Kata commit prefixes. A separate Packer pipeline has consumed
those artifacts, rebooted into the PVM kernel, passed runtime and AMI readiness
checks, and published the first immutable PVM AMI. The AMI has also joined an
EKS 1.35 cluster through an isolated node pool and passed the CNI-backed
virtio-net, DNS, public and denied egress, NetworkPolicy, Kubernetes scheduling,
service restart, and cold node replacement gates. The complete Xolis lifecycle
has passed through the buffered command, SSE streaming, interactive PTY, file,
policy, deletion, TTL, and warm-pool paths. The Hermes image and CLI bootstrap
have also passed through both buffered and PTY paths without a model call. A
model-backed Hermes workflow, broader failure injection, and native-KVM
regression remain open.

## 2. Confirmed Baseline and Constraints

| Item | Development baseline |
| --- | --- |
| PVM source | [`virt-pvm/linux`](https://github.com/virt-pvm/linux), `pvm-612` branch; pin an exact commit before building artifacts. |
| PVM kernel base | Linux 6.12.33 for both host and guest PVM changes. |
| Host reference | EKS 1.35 Optimized Amazon Linux 2023 x86_64 Standard. AWS AMI release `v20260728` uses kernel package `6.12.94-123.192.amzn2023`. |
| VMM and runtime | Pinned Kata Containers runtime-rs with upstream Dragonball. |
| Host PTI | PVM upstream requires `pti=off` on the host. This is a fixed runtime and security constraint. |
| Guest PTI | Keep guest PTI enabled. Host and guest PTI protect different boundaries. |
| Cloud virtualization | The PVM node must work without enabling EC2 nested virtualization. |
| Stable fallback | The existing native-KVM Dragonball AMI and `xolis-kata` handler remain unchanged. |

The close 6.12 kernel versions reduce configuration migration risk, but do not
guarantee driver, ABI, or security equivalence. The PVM kernel remains a custom
kernel and must carry its own build provenance, vulnerability response, and
upgrade process.

Before implementation starts, pin the exact PVM, Kata, runtime-rs, Dragonball,
toolchain, and EKS source AMI inputs. Release builds must not consume a floating
branch, `latest`, or the mutable EKS `recommended` parameter.

## 3. Artifact Model

The PVM path requires a matched set of artifacts rather than an isolated kernel
module:

1. A PVM host kernel package.
2. Host kernel modules built from the identical source and configuration,
   including `kvm.ko` and `kvm-pvm.ko`.
3. A PVM guest kernel built from the pinned PVM source.
4. A pinned runtime-rs and upstream Dragonball build.
5. A PVM-specific Kata configuration and containerd handler.
6. A PVM-specific node AMI.

Every build must produce a machine-readable manifest containing:

- source repository URLs and exact commits;
- host and guest kernel versions;
- complete host and guest configuration digests;
- compiler, linker, binutils, pahole, and build-host information;
- host kernel and module package digests;
- guest kernel and debug-symbol digests;
- Kata, runtime-rs, and Dragonball commits;
- AMI source ID and release version;
- host kernel command line, including `pti=off`; and
- creation time and build identifier.

Install the relevant manifest on the node as `/etc/xolis/pvm-build` and copy
the immutable identifiers into AMI tags. Reuse of an earlier build stage must
fail if any required identifier or digest is absent or different.

Build products and test evidence are not source files. Keep local build
intermediates under `.codex-tmp/pvm/` and structured test evidence under
`artifacts/pvm/<UTC-run-id>/`; both locations are ignored by Git.

## 4. Host Kernel Requirements

PVM changes KVM, x86 entry code, memory management, guest support, and the PVM
ABI. `kvm-pvm.ko` cannot be copied into the stock Amazon Linux kernel. Xolis
must build and boot the complete pinned PVM host kernel with its matching
modules.

### 4.1 Configuration Strategy

Do not use the generic upstream PVM host example as the AWS node configuration.
For example, that example does not enable every storage and filesystem option
needed by an EKS AL2023 node. Instead:

1. Export the complete config from the pinned EKS 1.35 AL2023 source AMI.
2. Apply it to the pinned PVM 6.12.33 source.
3. Run `olddefconfig` with the pinned toolchain.
4. Apply a reviewed PVM config fragment.
5. Check every required symbol in automation and fail on an unexpected value.
6. Store the final complete config and its digest with the artifacts.

At minimum, the final host config must cover:

- `CONFIG_KVM=m` and `CONFIG_KVM_PVM=m`;
- module loading and unloading;
- ENA and NVMe root-device support;
- XFS, ext4, device mapper, and initramfs dependencies;
- overlayfs and container storage prerequisites;
- cgroup v2 and namespace support;
- SELinux;
- BPF, BTF, JIT, and cgroup BPF;
- netfilter, conntrack, traffic control, and the networking features used by
  the EKS VPC CNI;
- vhost-net, vhost-vsock, and virtio-vsock; and
- serial console, debugfs, SysRq, and diagnostics required during
  qualification.

Build installable kernel and module packages. Do not rely on an unrecorded
`make install` result from a temporary builder.

### 4.2 Boot and Module Policy

The PVM AMI must:

- retain the source AL2023 kernel as a bootloader rollback entry;
- set the pinned PVM kernel as the default;
- build an initramfs that contains the drivers required to mount the EBS root
  volume and bring up the ENA interface;
- add `pti=off` explicitly to the host kernel command line;
- load `kvm` and `kvm-pvm` through a deterministic boot service;
- prevent `kvm-intel` and `kvm-amd` from loading on the PVM node until upstream
  confirms a supported coexistence model; and
- fail the PVM node readiness check if the expected kernel, command line,
  modules, or `/dev/kvm` are missing.

Host `pti=off` is a documented security tradeoff, not a temporary command-line
experiment. Qualification must record the tested CPU model, reported hardware
vulnerability state, kernel mitigation state, and the residual risk accepted
for an untrusted-workload node. PVM nodes must remain dedicated and must not
host general workloads.

## 5. Guest Kernel Requirements

Build the guest kernel from the same pinned PVM source revision as the host
unless upstream publishes an explicit cross-version ABI guarantee and a
different supported pairing.

The guest config must include:

- `CONFIG_PVM_GUEST=y`;
- `CONFIG_X86_PIE=y`;
- `CONFIG_KVM_GUEST=y`;
- `CONFIG_PARAVIRT=y` and `CONFIG_PARAVIRT_XXL=y`;
- guest PTI;
- virtio block, network, console, RNG, and vsock;
- virtio-fs and FUSE;
- required PCI and MMIO support; and
- the filesystem used by the Kata guest image.

The upstream example disables guest kernel modules. For the first Xolis
baseline, compile every required driver into the guest kernel. Install the
runtime artifact at a stable path such as
`/opt/kata/share/kata-containers/vmlinux-pvm`, and preserve a matching unstripped
debug image outside the runtime path.

Initially reuse the pinned Kata guest image, root filesystem, and Kata Agent.
Change only the guest kernel before introducing a different image format,
Nydus, or a custom guest root filesystem. This keeps the first failure boundary
small.

## 6. Runtime-rs and Dragonball Integration

The first integration target is upstream Dragonball, not an alternative VMM.
Create a separate configuration, for example:

`/etc/kata-containers/configuration-xolis-pvm-dragonball.toml`

Register a separate containerd handler named `xolis-kata-pvm`. Do not modify the
meaning of the existing `xolis-kata` handler.

The PVM configuration must pin or verify:

- the runtime-rs and Dragonball binary;
- the PVM guest kernel;
- the guest image;
- the root filesystem and block-device driver;
- kernel parameters;
- shared-filesystem mode;
- guest physical-address and MMIO constraints required by the validated
  upstream combination; and
- valid hypervisor paths and the Dragonball seccomp policy.

Do not apply the existing AWS M8i nested-KVM CPUID workaround to PVM without
evidence. Prefer the upstream PVM behavior. If Xolis encounters a reproducible
defect, make the smallest local change, add a regression test, and report the
general fix upstream.

Bring up the device paths in this order:

1. one vCPU, memory, console, and clean shutdown;
2. multiple vCPUs, interrupts, APIC, clock, and TSC;
3. virtio block root filesystem;
4. virtio-vsock and Kata Agent communication;
5. virtio network and vhost-net behavior;
6. inline virtio-fs, including metadata and xattrs; and
7. repeated startup, teardown, and failure cleanup.

Use QEMU/qboot only if a failure cannot otherwise be assigned to the PVM
kernel/ABI or Dragonball. A QEMU result is diagnostic evidence, not a second
Xolis runtime implementation.

## 7. AMI and Kubernetes Isolation

Build the PVM node as a separate Packer pipeline from the native-KVM AMI. The
PVM pipeline starts from a pinned EKS 1.35 Optimized AL2023 x86_64 Standard AMI,
installs the pinned PVM artifacts, reboots into the new kernel, and validates
the running kernel before creating the output AMI.

The PVM launch template must not request EC2 nested virtualization. Use an x86
instance family that does not expose VT-x or AMD-V for the qualification run.

Use a separate PVM Auto Scaling group and the following node capabilities:

- `xolis.io/kata-ready=true`;
- `xolis.io/virtualization=pvm`; and
- `xolis.io/pvm-ready=true`.

Add a dedicated taint, `RuntimeClass/xolis-kata-pvm`, and PVM sandbox profile.
The RuntimeClass scheduling rules must require the PVM capability. The native
and PVM pools must remain separate capacity-provider targets even if they use
the same public Xolis API.

## 8. Implementation Sequence

Each phase ends with its own validation and signed-off Git commit. Do not merge
an unvalidated artifact pipeline, AMI, runtime handler, and infrastructure
change into one commit.

Status as of August 4, 2026:

| Phase | Status | Remaining gate |
| --- | --- | --- |
| 0. Inputs and provenance | Complete for the first pinned baseline; source and artifact identities are carried into AMI tags. | Carry the same identities into later release metadata. |
| 1. Kernel artifacts | Complete for the pinned host and guest build; artifacts and manifests are archived in versioned S3 storage and digest-enforced by the AMI pipeline. | Rebuild only for an intentional pinned-input update. |
| 2. Host AMI | Complete for the first baseline. The Packer pipeline retained the rollback entry, rebooted into PVM, passed runtime and host checks, published `ami-01772ceec96a8fa48`, and terminated its builder. | Repeat EKS bootstrap and operational replacement during release qualification. |
| 3. Dragonball bring-up | CPU, memory, block, vsock, time, inline virtio-fs, xattrs, repeated start, teardown, VPC CNI networking, DNS, and egress policy passed. | Add focused failure injection beyond service and node replacement. |
| 4. Runtime-rs integration | Dedicated `xolis-kata-pvm` handler, readiness validation, direct CRI and kubelet execution, one/two vCPUs, service restart, and cleanup passed. | Run the native-KVM regression gate. |
| 5. Isolated node pool | Launch template, ASG, labels, taints, RuntimeClass, profile, cold replacement, independent scheduling, no-fallback behavior, and scale-to-zero cleanup passed. | Repeat during release qualification. |
| 6. Xolis lifecycle | Core create, placement, buffered and SSE commands, interactive PTY, file, policy, explicit deletion, TTL, cold claim, warm-pool claim, reset, cleanup, and Hermes CLI bootstrap paths passed. | Qualify a model-backed Hermes workflow, node loss, and broader failure injection. |
| 7. Operations and security | Artifact provenance and initial diagnostics are documented. | Complete CVE ownership, `pti=off` risk acceptance, upgrade, rollback, replacement, and incident procedures. |

The critical path is therefore the remaining Phase 6 model-backed Hermes and
failure gates, followed by the native-KVM regression.
Large-cluster performance and density remain outside this milestone.

### Phase 0: Pin Inputs and Provenance

Work:

- pin PVM, Kata, runtime-rs, Dragonball, toolchain, and EKS source AMI inputs;
- define host and guest config fragments;
- define the artifact manifest and `/etc/xolis/pvm-build`; and
- record host `pti=off`, module policy, and native-KVM fallback as design
  constraints.

Exit criteria:

- every remote input is identified by an immutable version, commit, or digest;
- release builds do not depend on a floating branch or AMI parameter; and
- the provenance schema can reject an incomplete or mismatched artifact set.

Suggested commit: `docs: define the PVM build baseline`

### Phase 1: Build Reproducible Kernel Artifacts

Work:

- add host and guest config fragments and final-config checks;
- build host kernel and module packages;
- build the PVM guest kernel and debug image; and
- generate checksums and a machine-readable manifest.

Exit criteria:

- a clean x86_64 builder produces the complete artifact set;
- the host packages contain matching `kvm.ko` and `kvm-pvm.ko`;
- required AWS, EKS, container, and PVM config checks pass; and
- repeated builds preserve the declared source and configuration identity.

Suggested commit: `build: add reproducible PVM kernel artifacts`

### Phase 2: Build and Boot the PVM Host AMI

Work:

- install the kernel packages into the pinned EKS AL2023 base;
- build the initramfs and bootloader entries;
- apply `pti=off` and the module policy;
- reboot during the Packer workflow; and
- run host readiness checks before creating the AMI.

Exit criteria:

- the instance boots the pinned PVM kernel from the EBS root volume;
- ENA, SSM, containerd, and node bootstrap prerequisites work;
- `/proc/cmdline` and the kernel log confirm `pti=off`;
- `kvm-pvm` loads and `/dev/kvm` reports the expected KVM API; and
- the temporary builder is terminated and verified absent from the provider.

Suggested commit: `image: add the PVM host AMI pipeline`

### Phase 3: Bring Up Dragonball on PVM

Work:

- reproduce the upstream-supported runtime-rs and Dragonball combination;
- boot the PVM guest kernel outside Kubernetes;
- validate CPU, memory, interrupts, block, vsock, network, and inline
  virtio-fs; and
- add only the local fixes and tests required by reproducible defects.

Exit criteria:

- Dragonball repeatedly starts and stops the PVM guest;
- Kata Agent communication over vsock works;
- block, network, and shared-filesystem smoke tests pass; and
- no VMM process, mount, tap, or guest resource remains after teardown.

Suggested commit when repository changes are required:
`runtime: enable Dragonball on the PVM host`

### Phase 4: Integrate Kata runtime-rs

Work:

- install the PVM guest kernel and configuration;
- register the `xolis-kata-pvm` containerd handler;
- add PVM readiness diagnostics; and
- test through `ctr` or `nerdctl` before Kubernetes.

Exit criteria:

- a Kata container demonstrably uses PVM and Dragonball;
- agent, network, block, and shared-filesystem paths work;
- repeated create and delete operations leave no resources; and
- the native `xolis-kata` handler still passes its regression test.

Suggested commit: `runtime: add the PVM Dragonball handler`

### Phase 5: Add the Isolated PVM Node Pool

Work:

- add the PVM launch template and Auto Scaling group;
- omit nested-virtualization CPU options;
- add capability labels, taint, RuntimeClass, and profile; and
- expose the PVM pool as a distinct capacity-provider capability.

Exit criteria:

- native and PVM pools scale independently;
- an invalid RuntimeClass and node-capability combination remains unscheduled
  instead of falling back; and
- the PVM group reliably returns to zero.

Suggested commit: `infra: add an isolated PVM sandbox node pool`

### Phase 6: Qualify the Xolis Lifecycle

Run the complete functional and failure matrix in Section 9. Fix defects at
the lowest responsible layer and preserve a regression test.

Exit criteria:

- the provider-neutral lifecycle suite passes on PVM;
- the Hermes workload completes a real interactive workflow;
- native-KVM regression remains green; and
- every success and failure path returns temporary capacity to zero.

Suggested commit: `test: qualify the PVM Dragonball path`

### Phase 7: Complete Operations and Security Documentation

Work:

- document build, deployment, diagnostics, upgrade, and rollback;
- record the `pti=off` risk and tested CPU mitigation state;
- define PVM kernel CVE and rebase ownership; and
- document node replacement and native-KVM fallback.

Exit criteria:

- operators can identify the exact host kernel, guest kernel, runtime, VMM,
  configuration, and AMI provenance;
- an earlier PVM AMI or native-KVM pool can be selected without changing the
  public sandbox API; and
- all task-created development and test machines are verified terminated.

Suggested commit: `docs: document PVM operations and qualification`

## 9. Test Plan

### 9.1 Static and Artifact Tests

- verify every pinned URL, commit, version, and SHA-256;
- verify the final host and guest configs, not only config fragments;
- verify package contents and kernel/module version agreement;
- verify the guest kernel and debug image share the build identity;
- verify `/etc/xolis/pvm-build` contains every required field;
- reject reuse of artifacts with missing or mismatched provenance;
- run shell syntax, Packer formatting and validation, Terraform/OpenTofu
  validation, and repository unit tests; and
- verify the native-KVM AMI inputs and handler are unchanged.

### 9.2 Host Boot Tests

- boot from the EBS NVMe root volume;
- verify ENA networking and SSM access;
- verify the pinned kernel with `uname -r`;
- verify `/proc/cmdline` contains `pti=off`;
- capture kernel mitigation and vulnerability status;
- verify `kvm` and `kvm-pvm` are loaded and conflicting vendor modules are not;
- verify `/dev/kvm` ownership, permissions, and API version;
- verify containerd and kubelet prerequisites; and
- reboot once and repeat every check.

### 9.3 Dragonball and Guest Tests

| Area | Minimum test |
| --- | --- |
| CPU | Boot with one vCPU, then multiple vCPUs; execute basic integer, FPU, and context-switch workloads. |
| Memory | Boot at the minimum sandbox size and a larger size; allocate, write, verify, and release memory. |
| Interrupts and time | Verify timers, sleep, monotonic time, wall-clock behavior, and SMP interrupt delivery. |
| Block | Mount the guest root device, read and write data, sync, and cleanly unmount. |
| Vsock | Establish Kata Agent communication and repeated request/response sessions. |
| Network | Configure the guest interface, resolve DNS, enforce the expected egress policy, and transfer data. |
| Inline virtio-fs | Create, rename, chmod, list, read, write, and delete files and directories. |
| Xattrs | Exercise get, set, list, and removal operations, including the Dragonball seccomp regression. |
| Shutdown | Graceful shutdown, forced termination, repeated start/stop, and cleanup after a failed boot. |

### 9.4 Runtime and Kubernetes Tests

- run a minimal Kata container through `ctr` or `nerdctl`;
- verify the runtime configuration and guest kernel actually identify the PVM
  path;
- run a minimal Pod using `RuntimeClass/xolis-kata-pvm`;
- verify PVM node selection and taint tolerance;
- verify the Pod cannot schedule on a native-KVM node;
- verify a native-KVM Pod cannot schedule onto a PVM-only profile; and
- repeat create and delete operations while checking processes, mounts,
  namespaces, devices, and Kubernetes objects.

### 9.5 Xolis Acceptance Tests

Run, in order:

1. claim creation and PVM placement;
2. stdout, stderr, exit status, and command timeout;
3. buffered and SSE execution;
4. file upload, listing, download, metadata, xattrs, and path confinement;
5. WebSocket/PTTY behavior;
6. tenant isolation and idempotent creation;
7. default-deny network policy and denied unapproved egress;
8. explicit foreground deletion;
9. absolute TTL cleanup;
10. warm-pool creation, claim, replenishment, and reset;
11. node restart and node-loss cleanup; and
12. the Hermes Agent interactive workload.

### 9.6 Failure and Cleanup Tests

Inject failures at these points:

- kernel package installation;
- initramfs generation;
- reboot into the PVM kernel;
- `kvm-pvm` module loading;
- `/dev/kvm` access;
- Dragonball startup;
- guest kernel boot;
- Kata Agent readiness;
- virtio-fs initialization;
- Pod startup and sandbox claim readiness; and
- test interruption while the PVM node is running.

After each failure, require:

- no orphaned VM or runtime process;
- no stale mount, tap device, namespace, claim, sandbox, Pod, or warm-pool
  allocation;
- no task-created image-builder or test instance still running; and
- the sandbox Auto Scaling group returned to its intended capacity, normally
  zero.

### 9.7 Native-KVM Regression

The existing native-KVM path must retain:

- the `xolis-kata` handler and Dragonball configuration;
- its dedicated AMI and nested-virtualization launch template;
- OCI and optional Nydus behavior;
- lifecycle and cleanup acceptance; and
- its rollback role when PVM is unavailable.

Run the native minimal runtime test after every PVM change that touches common
Kata, containerd, deployment, profile, or lifecycle code.

## 10. Evidence and Diagnostics

Every cloud test run must record:

- input manifest and AMI identifiers;
- instance type, CPU model, Region, and Availability Zone;
- host kernel config digest and command line;
- `uname`, `lsmod`, `modinfo`, `/dev/kvm`, mitigation, and vulnerability state;
- host dmesg from boot through teardown;
- runtime-rs and Dragonball logs;
- complete guest console output;
- containerd, kubelet, Pod, Sandbox, and SandboxClaim events;
- test phase timing and result; and
- cleanup commands, provider state, and final ASG capacity.

Diagnostics must distinguish:

1. wrong host kernel or command line;
2. host module or `/dev/kvm` failure;
3. KVM API or capability failure;
4. Dragonball startup or device failure;
5. guest kernel boot failure;
6. Kata Agent or vsock failure;
7. runtime/containerd failure;
8. Kubernetes scheduling or node readiness failure; and
9. Xolis lifecycle or cleanup failure.

## 11. Upstream Defect Workflow

When a concrete PVM, runtime-rs, or Dragonball issue appears:

1. reproduce it outside Kubernetes when practical;
2. record exact host and guest PVM commits and config digests;
3. record Kata, runtime-rs, and Dragonball commits;
4. capture host dmesg, VMM/runtime logs, and guest console;
5. identify the failing KVM ioctl, CPUID rule, boot phase, or device;
6. compare native KVM with Dragonball;
7. use QEMU/qboot only if it materially isolates the responsible layer;
8. create a minimal regression test;
9. report the defect to the responsible upstream module; and
10. carry only a minimal, documented local patch until the upstream fix is in
   a pinned dependency.

## 12. Temporary Machine Safety

Kernel builds, AMI builds, and PVM runtime tests may use temporary x86
development machines. Every automation path that creates a machine must install
a cleanup path before beginning mutable work. Cleanup must run after success,
failure, timeout, and interruption.

Before a development phase is considered complete:

- terminate the image builder and temporary test instances;
- return the PVM sandbox Auto Scaling group to zero;
- query the infrastructure provider and verify that no task-created instance
  remains running or pending; and
- record the final provider state in the test evidence.

Do not retain a machine for convenience. A retained machine requires explicit
user direction and a reported identifier, purpose, and expected shutdown time.

## 13. Initial Release Gate

The first PVM milestone is achieved only when:

> A pinned EKS 1.35 AL2023 x86 node that does not expose provider nested
> virtualization boots the pinned PVM host kernel with `pti=off`, runs a Xolis
> sandbox through Kata runtime-rs and upstream Dragonball with the pinned PVM
> guest kernel, completes the provider-neutral functional and cleanup suite,
> and returns all disposable capacity to zero while native KVM remains usable.

This gate is a functional qualification. It does not imply production security,
performance, density, cost, or availability claims.

## References

- [PVM source](https://github.com/virt-pvm/linux)
- [PVM Kata getting-started guide](https://github.com/virt-pvm/misc/blob/main/pvm-get-started-with-kata.md)
- [PVM presentation at Linux Plumbers Conference 2024](https://lpc.events/event/18/contributions/1766/attachments/1498/3306/LPC-PVM.pdf)
- [EKS AMI release v20260728](https://github.com/awslabs/amazon-eks-ami/releases/tag/v20260728)
- [PVM Evaluation on AWS](PVM-AWS-Evaluation.md)
- [Project Status and Roadmap](Project-Status-and-Roadmap.md)
