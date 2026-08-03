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
