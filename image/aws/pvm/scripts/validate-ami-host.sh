#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
pvm_directory="$(cd -- "${script_directory}/.." && pwd)"
source "${pvm_directory}/versions.sh"

"${script_directory}/validate-runtime.sh"

for path in \
  /etc/xolis/pvm-build.json \
  /etc/xolis/pvm-runtime-build.json \
  /usr/bin/nodeadm \
  /usr/bin/kubelet \
  /usr/bin/containerd; do
  if [[ ! -e "${path}" ]]; then
    echo "Required PVM AMI path is missing: ${path}" >&2
    exit 1
  fi
done
if ! grep -Fq 'xolis-kata-pvm.toml' /etc/containerd/config.toml; then
  echo "containerd does not import the PVM runtime fragment" >&2
  exit 1
fi
containerd_dump="$(mktemp /tmp/xolis-containerd-config.XXXXXX)"
trap 'rm -f "${containerd_dump}"' EXIT
containerd config dump >"${containerd_dump}"
if ! grep -Fq 'runtimes.xolis-kata-pvm' "${containerd_dump}"; then
  echo "containerd cannot parse the xolis-kata-pvm runtime handler" >&2
  exit 1
fi

root_fstype="$(findmnt --noheadings --output FSTYPE / | tr -d '[:space:]')"
root_source="$(findmnt --noheadings --output SOURCE / | tr -d '[:space:]')"
if [[ "${root_fstype}" != "xfs" && "${root_fstype}" != "ext4" ]]; then
  echo "Unexpected PVM AMI root filesystem: ${root_fstype}" >&2
  exit 1
fi

printf 'PVM_AMI_READY host=%s root=%s:%s kata=%s pvm=%s\n' \
  "$(uname -r)" "${root_source}" "${root_fstype}" \
  "${KATA_SOURCE_COMMIT}" "${PVM_SOURCE_COMMIT}"
