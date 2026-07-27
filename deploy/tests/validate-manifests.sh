#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

kubectl kustomize "${repository_root}/deploy" >/dev/null

for manifest in \
    bootstrap/xolis-runtime.yaml \
    bootstrap/xolis-runtime-nydus.yaml \
    xolis/namespaces.yaml \
    xolis/api-rbac.yaml \
    xolis/api.yaml \
    xolis/router-rbac.yaml \
    xolis/router.yaml \
    xolis/python-profile-nydus.yaml \
    xolis/network-policies.yaml
do
    kubectl create --dry-run=client --validate=false \
        -f "${repository_root}/deploy/${manifest}" >/dev/null
done

curl -fsSL \
    https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.5.3/sandbox-with-extensions.yaml |
    kubectl create --dry-run=client --validate=false -f - >/dev/null
