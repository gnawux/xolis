# Xolis Container Images

All Dockerfiles use the repository root as their build context:

    docker build -f image/xolis-api/Dockerfile -t xolis-api:dev .
    docker build -f image/xolis-runtime-python/Dockerfile -t xolis-runtime-python:dev .
    docker build -f image/xolis-runtime-hermes/Dockerfile -t xolis-runtime-hermes:dev .
    docker build -f image/sandbox-router/Dockerfile -t sandbox-router-go:v0.5.3 .

The Hermes image pins an upstream Hermes Agent commit and remains separate from
the default Python runtime. The router build fetches the commit behind Agent
Sandbox `v0.5.3` and verifies the full commit ID before compiling. Release
automation must push all images to private ECR and use immutable digest
references before deployment.

For the AWS lab, apply `infra/aws/minimal` once to create the repositories and
temporary-builder instance profile. Then build all AMD64 images on an ephemeral
EC2 instance and print their digest references:

    python3 tools/xolis_image_builder.py \
        --source-bucket xolis-lab-tfstate-ACCOUNT-apne1

The tool sends commands through Systems Manager, opens no inbound ports, and
terminates the instance after the images are pushed. Use `--keep-instance`
only for interactive diagnostics and terminate the instance manually afterward.

Build only Hermes and publish both ordinary OCI and Nydus-formatted tags:

    python3 tools/xolis_image_builder.py \
        --source-bucket xolis-lab-tfstate-ACCOUNT-apne1 \
        --image xolis-runtime-hermes \
        --nydus xolis-runtime-hermes

The builder downloads Nydus `v2.4.4` with a pinned SHA-256 checksum, converts
the pushed OCI image with `nydusify`, checks the converted manifest and RAFS
bootstrap, and returns immutable digest references for both modes. Runtime and
file-content validation is performed on the Nydus-enabled sandbox node; a
source/target mount comparison on the SELinux-enabled builder reports host-added
`security.selinux` xattrs that are not part of the independent Nydus mount.

## PVM Host and Runtime Artifacts

The experimental PVM path is separate from the ordinary application-image
builder and the native-KVM sandbox AMI. `image/aws/pvm` pins and builds the
matched Linux 6.12.33 host and guest kernels, installs the dedicated
`xolis-kata-pvm` runtime integration, validates the host and CRI path, and
packages the verified Kata runtime-rs and Dragonball installation.

The first pinned artifact set has passed standalone one- and two-vCPU runtime
qualification on a host without `vmx` or `svm` and is retained in private,
versioned S3 storage with manifests and SHA-256 checksums. A separate PVM
Packer pipeline now consumes those artifacts, reboots and validates the host,
and publishes an AMI for an isolated EKS node pool. That image has passed its
first EKS runtime, network-policy, and core Xolis lifecycle qualification. See
[`image/aws/pvm/README.md`](aws/pvm/README.md) for commands and exact artifact
identities.

The temporary application-image builder removes its source archive, all object
versions, and delete markers from a versioned source bucket before completing.
It also verifies that its EC2 builder reached the terminated state.
