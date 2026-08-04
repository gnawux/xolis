# Xolis Minimal AWS Lab Infrastructure

This OpenTofu root creates the persistent infrastructure for the first Xolis AWS lab:

- A two-AZ VPC with public subnets and Internet egress.
- An EKS control plane with public endpoint access.
- A one-node EKS managed system node group.
- A self-managed sandbox Auto Scaling group with capacity fixed at zero until the lab tool starts a test.
- An optional, independent PVM sandbox Auto Scaling group that also starts at
  zero and never requests EC2 nested virtualization.
- Amazon VPC CNI network-policy enforcement for sandbox ingress and egress isolation.
- Three immutable, scan-on-push private ECR repositories.
- A no-ingress security group and least-privilege instance profile for
  temporary, SSM-managed image build instances.

The checked-in baseline uses EKS 1.35, which remains in standard support until
2027-03-27. Do not select an EKS version in extended support for a persistent
lab because its control-plane hourly charge is substantially higher. The cluster
upgrade policy is set to `STANDARD` so the lab does not intentionally remain on
an expired release and incur extended-support pricing.

The public-subnet layout intentionally avoids a NAT gateway during early
experiments. It is not a production network design. Set `sandbox_ami_id` to the
versioned custom Kata AMI built from `image/aws`. The validated baseline does not
require Nydus.

Set `pvm_ami_id` to a validated PVM AMI to create the isolated PVM launch
template and ASG. PVM nodes receive `xolis.io/virtualization=pvm` and
`xolis.io/pvm-ready=true`, plus a dedicated `xolis.io/pvm=true:NoSchedule`
taint. They remain distinct from the native-KVM sandbox ASG and can be scaled
or destroyed independently.

The nodeadm configuration explicitly imports `/etc/containerd/conf.d/*.toml`.
This is required because EKS bootstrap regenerates `/etc/containerd/config.toml`;
without the node-level merge, runtime fragments installed in a custom AMI are
present on disk but absent from the effective CRI configuration.

## Prepare Configuration

Copy the example and set the IAM Identity Center role ARN that is used through the `xolis-lab` AWS CLI profile:

    cp terraform.tfvars.example terraform.tfvars
    aws sts get-caller-identity --profile xolis-lab

The `Arn` reported by `get-caller-identity` is normally an STS assumed-role ARN. Convert it to its IAM role ARN for `admin_principal_arn`:

    arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_AdministratorAccess_example/user@example.com
    arn:aws:iam::123456789012:role/aws-reserved/sso.amazonaws.com/ap-northeast-1/AWSReservedSSO_AdministratorAccess_example

Do not commit `terraform.tfvars`. Configure a remote locked state backend before the root is shared. The first single-operator experiment may use local state only long enough to create and validate the lab.

To use S3 state locking, create a dedicated state bucket through your account bootstrap process and copy the supplied example:

    cp backend.hcl.example backend.hcl
    tofu init -backend-config=backend.hcl

`backend.hcl` contains no AWS credentials and is ignored because bucket names and state paths are account-specific. This root uses S3 lockfiles (`use_lockfile = true`); it does not require DynamoDB locking.

## Validate and Apply

    export AWS_PROFILE=xolis-lab
    tofu init
    tofu fmt -check -recursive
    tofu validate
    tofu plan
    tofu apply

After apply, update the Lab tool configuration with the output values:

    tofu output

The sandbox ASG remains at zero after apply. The checked-in configuration and
manifests support either the minimal `tools/xolis_aws_lab.py ... cycle run` or the
full service acceptance workflow documented in `deploy/README.md`.

The image builder instance is not persistent infrastructure. Run
`tools/xolis_image_builder.py` when images need to be built; the tool launches
an on-demand x86_64 instance in a public subnet and terminates it after pushing
the images to ECR. Keeping a builder instance requires an explicit command-line
option and continues to incur EC2 charges.

## Destroy

Destroying this root deletes the EKS cluster, node groups, ASG, VPC, and attached lab infrastructure:

    tofu destroy

Review the destruction plan carefully. Empty the cluster and stop lab activity before destroying it.
