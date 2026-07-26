# Xolis Sandbox AMI

This Packer build creates an immutable sandbox-node AMI from an EKS-optimized Amazon Linux 2023 AMI. It installs pinned Kata Containers and Nydus static artifacts, adds the `xolis-kata` containerd runtime handler, and enables the Nydus snapshotter service. The Kata artifact must include `configuration-dragonball.toml`; the build fails otherwise. The builder uses an M8i instance because the current AWS nested-virtualization capability requires a supported generation-eight Intel family.

The base AMI must match the Kubernetes version configured in `infra/aws/minimal`. Do not run `nodeadm` during the build. The self-managed ASG launch template supplies the EKS NodeConfig when it starts an instance.

## Build Inputs

The builder intentionally requires complete immutable artifact inputs instead of downloading an unpinned `latest` release:

- EKS-optimized AL2023 source AMI ID.
- Public build-subnet ID from the lab VPC.
- Kata Containers static archive URL and SHA-256.
- Nydus static archive URL and SHA-256.

Create a local variable file from the example and fill it with release URLs and checksums verified from the upstream release assets:

    cp xolis-sandbox.pkrvars.hcl.example xolis-sandbox.pkrvars.hcl
    packer init packer.pkr.hcl
    packer validate -var-file=xolis-sandbox.pkrvars.hcl packer.pkr.hcl
    packer build -var-file=xolis-sandbox.pkrvars.hcl packer.pkr.hcl

Record the AMI ID in `infra/aws/minimal/terraform.tfvars` as `sandbox_ami_id`, then run `tofu plan` and `tofu apply`. The ASG will remain at zero until a Lab cycle starts it.

## Validation Boundary

The smoke test uses `RuntimeClass/xolis-kata`, which selects the `xolis-kata` handler. Nydus is installed and its service is enabled, but the first smoke image is an ordinary OCI image. Add a pinned Nydus-formatted image after the Kata execution path is stable.
