# Xolis Sandbox AMI

This directory is the stable AWS native-KVM image path. The experimental PVM
path is intentionally separate under [`pvm`](pvm/README.md): its pinned host
and guest kernels plus Kata runtime-rs and upstream Dragonball have passed
standalone host and CRI qualification, but the immutable PVM AMI and EKS node
pool are still roadmap work. Do not add the PVM kernel or `pti=off` to this
native-KVM image and do not apply the AWS M8i CPUID workaround to the PVM build.

This Packer build creates an immutable sandbox-node AMI from an EKS-optimized Amazon Linux 2023 AMI. It installs pinned Kata Containers, adds the `xolis-kata` containerd runtime handler, and can optionally install the Nydus snapshotter service. The Kata static artifact provides guest kernel and root filesystem assets; the build separately compiles Kata 4 `runtime-rs` with its built-in Dragonball VMM from a fixed source commit. The builder uses an M8i instance because the current AWS nested-virtualization capability requires a supported generation-eight Intel family.

The preferred base AMI matches the Kubernetes version configured in
`infra/aws/minimal`. The currently validated Kata AMI uses the EKS 1.32 base and
remains within the supported three-minor kubelet skew of the EKS 1.35 control
plane; rebuild it from the EKS 1.35 base before upgrading the control plane
beyond 1.35. Do not run `nodeadm` during the build. The self-managed ASG launch
template supplies the EKS NodeConfig when it starts an instance.

The builder connects through AWS Systems Manager Session Manager, not a public SSH rule. Install the AWS Session Manager Plugin and make `session-manager-plugin` available on `PATH` before running Packer. Packer creates and removes a temporary instance profile with only the Systems Manager permissions required for the build.

The resulting AMI has a 200 GiB encrypted gp3 root volume. This capacity is needed while building the full fixed Kata 4.0.0 Rust dependency graph alongside the static guest assets; the build expands the source AMI's root partition and XFS filesystem before compiling. The build output remains on the root volume for the sandbox node.

## Build Inputs

The builder intentionally requires complete immutable artifact inputs instead of downloading an unpinned `latest` release:

- EKS-optimized AL2023 source AMI ID.
- Public build-subnet ID from the lab VPC.
- Kata Containers static archive URL and SHA-256.
- Kata Containers source commit, fixed to the commit behind the chosen release tag. The builder verifies the exact checkout and generates the Dragonball runtime configuration from it.
- The two vendored Dragonball seccomp patches from Kata Containers PR #13510.
  The builder checks and applies both patches and records their upstream commit
  IDs in `/etc/xolis/kata-build`.
- Optional Nydus snapshotter and image-service archive URLs, versions, and
  SHA-256 values. Leave all six Nydus inputs empty for the Kata-only baseline.
  The snapshotter archive provides `containerd-nydus-grpc`; the separate
  image-service archive provides `nydusd` and `nydus-image`.

Create a local variable file from the example and fill it with release URLs and checksums verified from the upstream release assets:

    cp xolis-sandbox.pkrvars.hcl.example xolis-sandbox.pkrvars.hcl
    packer init packer.pkr.hcl
    packer validate -var-file=xolis-sandbox.pkrvars.hcl packer.pkr.hcl
    packer build -var-file=xolis-sandbox.pkrvars.hcl packer.pkr.hcl

For an iterative Nydus-only rebuild, `source_ami_id` may identify an immutable
Xolis AMI whose `KataVersion` and `KataCommit` tags exactly match the requested
inputs. Set `reuse_existing_kata_runtime = true` only in that case. The build
validates the installed Kata binaries, Dragonball configuration, source commit,
and patch provenance before installing Nydus; the default remains a complete
Kata rebuild from the EKS AMI. An older AMI without `/etc/xolis/kata-build` or
without both required patch commit IDs cannot be reused.

Record the AMI ID in `infra/aws/minimal/terraform.tfvars` as `sandbox_ami_id`, then run `tofu plan` and `tofu apply`. The ASG will remain at zero until a Lab cycle starts it.

## Kata Version Policy

The example pins Kata Containers 4.0.0 as the validated baseline, including its
immutable release-asset digest and source commit. This makes the AMI and smoke
results reproducible. Do not replace it with an unversioned `main` build in this
file.

The baseline additionally backports the two commits merged through upstream
[Kata Containers PR #13510](https://github.com/kata-containers/kata-containers/pull/13510):
`7381d8eee0089a454bf6a67dc4a068faabfd1a78` permits `listxattr`, and
`dbcd740dcb5be9f0d60019a2f18e74cdde4821af` permits `name_to_handle_at` in
Dragonball's seccomp policy. These calls are required by the inline virtio-fs
path used by the Hermes workload. Keeping the patches in this repository makes
the fixed 4.0.0 build reproducible until a later pinned Kata release contains
the upstream changes.

The runtime-rs build uses the GNU Rust target (`LIBC=gnu`). Kata's runtime-rs documentation lists the musl target as an optional fully-static build, while Amazon Linux 2023 does not provide the `musl-gcc` package needed by Kata's default musl build. This does not change the built-in Dragonball VMM.

To evaluate Kata main, create a separate variable file that identifies an immutable source commit and records the builder provenance, archive SHA-256, guest kernel, guest root filesystem, and Dragonball configuration. Build it into a separately tagged AMI and keep it out of the baseline comparison.

## AWS Nested-KVM Compatibility

The Kata 4.0.0 Dragonball source receives a small, documented build-time patch for
the M8i nested-KVM environment. AWS exposes modern XSAVE features, including
AVX-512 and AMX, through the nested KVM CPUID interface. Dragonball 4.0.0 passes
those leaves into the guest without a compatible CR4/XCR0 virtualization path, so
the bundled guest kernel faults in `XSETBV` during early FPU initialization and
the Kata agent never starts.

The build masks only XSAVE and xstate-dependent CPUID capabilities (leaves `0x1`,
`0x7`, and `0xD`); ordinary baseline x86 CPU capabilities remain available. This
workaround is deliberately applied to the fixed Kata source checkout rather than
through an undocumented runtime override, because the Dragonball configuration
does not expose a CPU feature filter. Re-evaluate and remove the patch when an
upstream Dragonball release provides a compatible nested-KVM xstate path.

The ordinary baseline and optional Nydus path were validated on an AWS M8i
self-managed EKS node. The final 2026-07-28 comparison AMI is
`ami-0ec0906871c3a9d9b`; both `RuntimeClass/xolis-kata` and
`RuntimeClass/xolis-kata-nydus` completed the service smoke test, and the Auto
Scaling group was returned to zero afterward.

## Validation Boundary

The smoke test uses `RuntimeClass/xolis-kata`, which selects the ordinary
`xolis-kata` handler. Supplying all six pinned Nydus inputs additionally
installs the snapshotter and daemon, registers `xolis-kata-nydus`, and records
both pinned versions in `/etc/xolis/nydus-version`. The snapshotter reuses the
EKS AMI's ECR credential provider and renews credentials for active mounts. It
does not change the ordinary handler.

Containerd 2.2 requires kubelet to enable `RuntimeClassInImageCriApi` so the
runtime handler reaches `PullImageRequest`. The CRI image-service fragment maps
`xolis-kata-nydus` to `linux/amd64` and the `nydus` snapshotter. It also uses
local image pull with snapshot annotations enabled; the default Transfer
Service unpack configuration does not include Nydus. The tested RAFS v6 image
uses `digest_validate=false` because nydusd 2.4.4 rejects integrity validation
for RAFS v6.

The Nydus profile must use an image that includes Nydus bootstrap metadata.
Merely selecting the Nydus handler does not convert an ordinary OCI image. Keep
the digest-pinned OCI image as the fallback while publishing and validating a
separately tagged Nydus image for `python-nydus-v1`.

The final Hermes validation used Nydus pull digest
`sha256:3171982074bed7c91830958318d5ed41066b7f7b44aeeb1d584a6ae01d676353`.
One fresh-node sample pulled it in 0.585 seconds and reached Ready in 16.601
seconds. The corresponding OCI sample pulled in 4.696 seconds and reached Ready
in 10.415 seconds. These single, ordered samples are diagnostic only; they show
that current Nydus mount startup can outweigh the pull reduction.

## Nydus Integration Findings

The first AWS integration required several settings that were not apparent from
the basic conversion and snapshotter workflow. This table records the observed
boundary so future upgrades can distinguish Nydus behavior from Kubernetes,
containerd, and cloud-registry integration.

| Observed issue | Required action in the validated configuration | Most relevant upstream area |
| --- | --- | --- |
| `nydusify check` with both source and target reported differences for `security.selinux` attributes added by the builder host. | Validate the target manifest and bootstrap with `nydusify check --target`, then validate runtime contents on the Nydus-enabled SELinux node. | Nydus tooling: make host-added xattrs easier to exclude or identify in comparison output. |
| The static conversion workflow needed `nydusd` even though conversion primarily uses `nydusify` and `nydus-image`. | Install `nydusd` from the pinned image-service archive on the builder. | Nydus release packaging and `nydusify check` dependency documentation. |
| A private ECR target converted successfully only after the builder could read image manifests as well as upload layers. | Grant the image builder `ecr:BatchGetImage` and authenticate before conversion. | AWS/ECR integration documentation rather than the Nydus runtime. |
| The snapshotter could not reuse private ECR credentials without its own credential-provider configuration. | Enable kubelet credential providers in the snapshotter, provide a complete ECR `CredentialProviderConfig`, point it at the EKS provider binary, and renew credentials for active mounts. | Nydus snapshotter documentation and diagnostics for private registries. |
| Selecting the Nydus Kata runtime handler did not by itself route CRI image pulls to Nydus on containerd 2.2. | Enable kubelet `RuntimeClassInImageCriApi`; add a `runtime_platforms` mapping with both `platform = "linux/amd64"` and `snapshotter = "nydus"`. | Kubernetes/containerd CRI integration, with a useful Nydus deployment prerequisite check. |
| Containerd's default Transfer Service pull path did not preserve the annotations needed by the Nydus snapshotter. | Set `use_local_image_pull = true`, keep snapshot annotations enabled, and retain unpacked layers for this runtime-specific path. | Containerd image-service integration; Nydus could detect and explain missing annotations earlier. |
| nydusd 2.4.4 rejected digest validation for the tested RAFS v6 image. | Set `digest_validate=false` for this pinned compatibility combination. | Nydus daemon: RAFS v6 integrity-validation support or a clearer compatibility error and version matrix. |
| Nydus reduced the measured pull phase but the sandbox reached Ready later than OCI in the single ordered sample. | Treat the result as diagnostic and profile daemon startup, remote mount, Kata volume setup, and first reads before claiming a latency improvement. | Nydus observability: expose phase-level mount and prefetch timing that can be correlated with CRI events. |

The strongest Nydus-specific feedback candidates are the SELinux comparison
false positive, the implicit `nydusd` validation dependency, RAFS v6 digest
validation compatibility, and phase-level startup diagnostics. RuntimeClass
routing and Transfer Service behavior primarily belong to Kubernetes and
containerd, while ECR IAM belongs to the deployment integration.
