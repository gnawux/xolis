# Xolis Container Images

All Dockerfiles use the repository root as their build context:

    docker build -f image/xolis-api/Dockerfile -t xolis-api:dev .
    docker build -f image/xolis-runtime-python/Dockerfile -t xolis-runtime-python:dev .
    docker build -f image/sandbox-router/Dockerfile -t sandbox-router-go:v0.5.3 .

The router build fetches the commit behind Agent Sandbox `v0.5.3` and verifies
the full commit ID before compiling. Release automation must push all three
images to private ECR and replace the development image references in the
Kustomize output with immutable digest references before deployment.
