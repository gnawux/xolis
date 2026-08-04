# PVM Kernel Artifacts

This directory prepares the matched host and guest kernel artifacts used by the
experimental Xolis PVM node image. The runtime target remains Kata Containers
runtime-rs with upstream Dragonball. The existing native-KVM AMI is independent
and is not modified by this build.

## Pinned Inputs

`versions.sh` records the immutable PVM, EKS, and Kata baseline. The source AMI
is the EKS 1.35 Optimized Amazon Linux 2023 x86_64 Standard release
`1.35.6-20260728` in `ap-northeast-1`. The PVM source is pinned to a commit on
the Linux 6.12.33-based `pvm-612` branch.

The EKS host config digest was collected from that exact source AMI and is
pinned as `EKS_HOST_CONFIG_SHA256`. The build script rejects an empty or
mismatched digest.

## Build

Run the build only on an x86_64 Amazon Linux 2023 machine created from the
pinned source AMI:

```console
sudo image/aws/pvm/scripts/install-build-dependencies.sh
sudo PVM_BUILD_JOBS=4 image/aws/pvm/scripts/build-kernels.sh
```

The source AMI provides the `curl` command through `curl-minimal`. The
dependency installer intentionally does not replace it with the conflicting
full `curl` package.

The default host config is `/boot/config-$(uname -r)`. Override
`PVM_HOST_BASE_CONFIG` only when validating an explicitly pinned copy of the
same config. The default output is `/opt/xolis-artifacts/pvm/<PVM-commit>/`.

The output contains:

- installable host kernel and module RPMs;
- the PVM guest `vmlinux-pvm`;
- final host and guest configs; and
- a manifest with source, toolchain, build-host, and artifact digests.

Build intermediates use a unique directory under `/opt/xolis-build` and are
removed after success or failure. Artifact output is retained for collection by
the image pipeline.

## Runtime Integration

After installing the PVM host kernel and the pinned Kata runtime-rs build, add
the PVM guest kernel and isolated containerd handler with:

```console
sudo image/aws/pvm/scripts/install-runtime-integration.sh
sudo image/aws/pvm/scripts/validate-runtime.sh
```

The installer registers `xolis-kata-pvm` without changing the existing
`xolis-kata` handler. It installs the guest kernel at
`/opt/kata/share/kata-containers/vmlinux-pvm`, creates the PVM-specific
Dragonball configuration, removes the unsupported `announce_submounts`
inline-virtio-fs option, and loads the PVM containerd fragment through the
existing import helper. The handler passes only the explicitly supported Kata
CPU and memory annotations.

Run the standalone functional smoke test with:

```console
sudo PVM_SMOKE_VCPUS=2 image/aws/pvm/scripts/smoke-runtime.sh
```

The default skips the network transfer because an unjoined builder has no CNI
data plane. On a node with a working CNI, set
`PVM_SMOKE_REQUIRE_NETWORK=true`; the test then uses a Pod network namespace
and requires DNS plus an HTTP transfer to pass.

`package-runtime.sh` validates the installed combination and creates a
compressed archive plus machine-readable manifest. It does not upload the
archive or select a bucket; the image pipeline or operator must copy the output
from `/opt/xolis-artifacts/runtime/` to durable artifact storage.

## Required Validation

`validate-kernel-config.py` checks the final configs after Kconfig dependency
resolution. It verifies the PVM settings and the AWS, EKS, container, network,
storage, vsock, and inline virtio-fs prerequisites required by the first Xolis
baseline.

The PVM host must eventually boot with `pti=off`; enabling the configuration
symbol alone is not sufficient. The AMI phase must reboot the builder and
verify `/proc/cmdline`, the running kernel, `kvm-pvm`, and `/dev/kvm` before it
creates an image.

Any temporary builder must be terminated after artifact collection, including
after failures or interrupted runs. Verify the final instance state from AWS
before considering the build complete.

## Verified Build

The pinned build completed successfully on August 4, 2026, using a temporary
`c7i.4xlarge` builder in `ap-northeast-1a`. Both final configuration checks
passed. The build produced the following primary artifacts:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| Host kernel RPM | 396,955,961 bytes | `6b1d89f9b32929715bcad6e76d973b66b25491e77a93554bd80d8090c8f8f8fc` |
| Guest `vmlinux-pvm` | 275,187,768 bytes | `c483d65f4882e7e6a879716a46faffd04941f902224d6fcf069765af796c1bce` |
| Final host config | 160,535 bytes | `a1e1eeb653ee1922f876d367d4a0e59865c7577488802086f876cd26e2fe7a61` |
| Final guest config | 89,566 bytes | `3f943fb0d8e5477aaf50fcc5db8ebe3512a4cd03fcf2136271aa3cd414c1f8b0` |

The RPM metadata includes both `kvm-pvm.ko` and `ena.ko`. The published build
manifest is authoritative for artifact identity; build timestamps mean that a
separate rebuild from the same source and configuration need not be bitwise
identical.

The private, versioned artifact prefix is:

```text
s3://xolis-pvm-artifacts-479874045111-ap-northeast-1/pvm/91e9c9be4472756890844b2c982d7c72252dbfe6/
```

## Verified Host Boot

The host kernel was installed and booted on a standard `c7i.4xlarge` instance
in `ap-northeast-1a` on August 4, 2026. The instance did not expose `vmx` or
`svm` CPU flags. Validation confirmed:

- the running kernel was `6.12.33-xolis-pvm`;
- `/proc/cmdline` contained `pti=off`;
- the ENA network device, NVMe root volume, XFS filesystem, and SSM access
  survived the reboot;
- `kvm-pvm.ko` loaded without `kvm-intel` or `kvm-amd`;
- `/dev/kvm` was created; and
- `KVM_GET_API_VERSION` returned `12`.

The PVM module is not loaded automatically by the kernel package. The AMI
pipeline must install `files/xolis-pvm.modules-load.conf` as
`/etc/modules-load.d/xolis-pvm.conf`; otherwise `/dev/kvm` is absent after a
fresh start until an operator runs `modprobe kvm-pvm`.

## Verified Runtime-rs and Dragonball Path

The pinned Kata 4.0.0 runtime-rs build at commit
`cf82bb35c80320178bf7570252fe75d6fb263209`, with upstream Dragonball and the
two seccomp fixes recorded by `/etc/xolis/kata-build`, was validated on the PVM
host on August 4, 2026. The AWS nested-KVM CPUID workaround was disabled.

Validation covered:

- repeated Dragonball guest start and teardown through containerd;
- the dedicated `xolis-kata-pvm` CRI handler;
- one-vCPU and two-vCPU guest boot;
- Kata Agent communication over vsock and block-backed guest rootfs;
- a 64 MiB memory write-and-verify workload and monotonic time;
- host-to-guest and guest-to-host inline-virtio-fs access;
- `setxattr`, `getxattr`, `listxattr`, and `removexattr`, including the prior
  Dragonball seccomp regression area;
- command output and exit-code propagation; and
- teardown with no remaining Pod sandbox, container, shim, or mount.

Five consecutive cached-image CRI runs of the two-vCPU functional smoke test
completed in 4,457, 4,487, 4,494, 4,528, and 4,487 milliseconds. This is a
single-node integration measurement, not a density or large-cluster benchmark.

The standalone node has no CNI binaries or CNI configuration, so guest
virtio-net, DNS, and egress remain a required gate on the first PVM EKS node.
The failed standalone DNS attempt is not evidence of a Dragonball network
defect because no Pod data plane existed.

The validated runtime bundle is stored at:

```text
s3://xolis-pvm-artifacts-479874045111-ap-northeast-1/kata/4.0.0/cf82bb35c80320178bf7570252fe75d6fb263209/pvm-91e9c9be4472756890844b2c982d7c72252dbfe6/
```

The compressed archive is 2,262,616,605 bytes with SHA-256
`ca4781bea4684834c6dda05f8b030795114b7be48868e210b6149944376b538b`.
Its adjacent manifest records the hashes of `kata-runtime`,
`containerd-shim-kata-v2`, the PVM guest kernel, runtime configuration, and
build provenance. The retained validation instance is stopped after testing;
its 200 GiB root EBS volume is retained only to accelerate the next AMI and
network qualification session.
