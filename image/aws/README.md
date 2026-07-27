# Xolis Sandbox AMI

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
- Optional Nydus static archive URL and SHA-256. Leave all Nydus inputs empty for the initial Kata-only baseline.

Create a local variable file from the example and fill it with release URLs and checksums verified from the upstream release assets:

    cp xolis-sandbox.pkrvars.hcl.example xolis-sandbox.pkrvars.hcl
    packer init packer.pkr.hcl
    packer validate -var-file=xolis-sandbox.pkrvars.hcl packer.pkr.hcl
    packer build -var-file=xolis-sandbox.pkrvars.hcl packer.pkr.hcl

Record the AMI ID in `infra/aws/minimal/terraform.tfvars` as `sandbox_ami_id`, then run `tofu plan` and `tofu apply`. The ASG will remain at zero until a Lab cycle starts it.

## Kata Version Policy

The example pins Kata Containers 4.0.0 as the validated baseline, including its
immutable release-asset digest and source commit. This makes the AMI and smoke
results reproducible. Do not replace it with an unversioned `main` build in this
file.

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

The baseline was validated on an AWS M8i self-managed EKS node: a pod using
`RuntimeClass/xolis-kata` reached Ready, and the test cycle then deleted the pod
and returned the sandbox Auto Scaling group to zero. Nydus was disabled for this
baseline; its performance path is a separate follow-up.

## Validation Boundary

The smoke test uses `RuntimeClass/xolis-kata`, which selects the `xolis-kata` handler. The initial build uses an ordinary OCI image and does not require Nydus. Add a pinned Nydus artifact and Nydus-formatted image only after the Kata execution path is stable.
