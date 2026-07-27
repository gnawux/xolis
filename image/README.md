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
