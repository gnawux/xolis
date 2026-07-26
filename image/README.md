# Xolis Container Images

All Dockerfiles use the repository root as their build context:

    docker build -f image/xolis-api/Dockerfile -t xolis-api:dev .
    docker build -f image/xolis-runtime-python/Dockerfile -t xolis-runtime-python:dev .
    docker build -f image/sandbox-router/Dockerfile -t sandbox-router-go:v0.5.3 .

The router build fetches the commit behind Agent Sandbox `v0.5.3` and verifies
the full commit ID before compiling. Release automation must push all three
images to private ECR and replace the development image references in the
Kustomize output with immutable digest references before deployment.

For the AWS lab, apply `infra/aws/minimal` once to create the repositories and
temporary-builder instance profile. Then build all AMD64 images on an ephemeral
EC2 instance and print their digest references:

    python3 tools/xolis_image_builder.py \
        --source-bucket xolis-lab-tfstate-ACCOUNT-apne1

The tool sends commands through Systems Manager, opens no inbound ports, and
terminates the instance after the images are pushed. Use `--keep-instance`
only for interactive diagnostics and terminate the instance manually afterward.
