#!/bin/sh
set -eu

version="v0.5.3"
manifest_url="https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${version}/sandbox-with-extensions.yaml"

kubectl apply --server-side --field-manager=xolis-bootstrap -f "${manifest_url}"
kubectl --namespace agent-sandbox-system patch deployment agent-sandbox-controller \
    --type merge \
    --patch '{"spec":{"template":{"spec":{"nodeSelector":{"node-role.xolis.io/system":"true"}}}}}'
kubectl --namespace agent-sandbox-system rollout status deployment/agent-sandbox-controller \
    --timeout=180s
