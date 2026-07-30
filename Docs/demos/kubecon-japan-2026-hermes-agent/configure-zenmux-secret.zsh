#!/bin/zsh
set -euo pipefail

namespace="xolis-sandboxes"
secret_name="hermes-agent-credentials"
credential_file="$(mktemp)"
trap 'rm -f "$credential_file"; unset ZENMUX_API_KEY' EXIT
chmod 600 "$credential_file"

read -r -s "ZENMUX_API_KEY?Paste ZenMux API Key (input is hidden): "
print
if [[ -z "${ZENMUX_API_KEY}" ]]; then
  print -u2 "The key cannot be empty."
  exit 1
fi

printf '%s' "${ZENMUX_API_KEY}" >"$credential_file"
kubectl --namespace "$namespace" create secret generic "$secret_name" \
  --from-file="OPENAI_API_KEY=$credential_file" \
  --from-literal="CUSTOM_BASE_URL=https://zenmux.ai/api/v1" \
  --dry-run=client \
  --output=yaml \
  | kubectl apply --filename=-

print "Configured the ZenMux endpoint in Secret/$secret_name without printing the key."
