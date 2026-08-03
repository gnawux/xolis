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
| Host kernel RPM | 397,200,338 bytes | `990dff8111d2165909678c837f515c82f237d8c4d9b6d8ab38b823d638d9d720` |
| Guest `vmlinux-pvm` | 275,187,768 bytes | `b0c84dfe6b5b80fa42c6979eb51a34eac74813ec329ca8b7d7753383327caa49` |
| Final host config | 160,535 bytes | `a1e1eeb653ee1922f876d367d4a0e59865c7577488802086f876cd26e2fe7a61` |
| Final guest config | 89,566 bytes | `3f943fb0d8e5477aaf50fcc5db8ebe3512a4cd03fcf2136271aa3cd414c1f8b0` |

The RPM metadata includes both `kvm-pvm.ko` and `ena.ko`. All temporary probe
and builder instances and their attached volumes were terminated after the
test. The next phase must rebuild or publish the artifacts through the AMI
pipeline rather than depending on this disposable test output.
