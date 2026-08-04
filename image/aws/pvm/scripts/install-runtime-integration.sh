#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
pvm_directory="$(cd -- "${script_directory}/.." && pwd)"
source "${pvm_directory}/versions.sh"

guest_kernel_source="${PVM_GUEST_KERNEL_SOURCE:-/opt/xolis-artifacts/pvm/${PVM_SOURCE_COMMIT}/guest/vmlinux-pvm}"
dragonball_config_source="${PVM_DRAGONBALL_CONFIG_SOURCE:-/opt/kata/share/defaults/kata-containers/runtime-rs/configuration-dragonball.toml}"
guest_image_source="${PVM_GUEST_IMAGE_SOURCE:-/opt/kata/share/kata-containers/kata-containers.img}"
guest_kernel_destination="/opt/kata/share/kata-containers/vmlinux-pvm"
dragonball_config_destination="/etc/kata-containers/configuration-xolis-pvm-dragonball.toml"
containerd_fragment_destination="/etc/containerd/conf.d/xolis-kata-pvm.toml"

if [[ "$(id -u)" != "0" ]]; then
  echo "PVM runtime integration must be installed as root" >&2
  exit 1
fi

for path in \
  "${guest_kernel_source}" \
  "${dragonball_config_source}" \
  "${guest_image_source}" \
  /usr/local/bin/containerd-shim-kata-v2 \
  /usr/local/bin/kata-runtime \
  /usr/local/sbin/xolis-enable-containerd-import; do
  if [[ ! -e "${path}" ]]; then
    echo "Required PVM runtime input does not exist: ${path}" >&2
    exit 1
  fi
done

install -d -m 0755 \
  /etc/containerd/conf.d \
  /etc/kata-containers \
  /etc/modules-load.d \
  /opt/kata/share/kata-containers
install -m 0644 "${guest_kernel_source}" "${guest_kernel_destination}"
install -m 0644 "${pvm_directory}/files/xolis-pvm.modules-load.conf" \
  /etc/modules-load.d/xolis-pvm.conf
install -m 0644 "${pvm_directory}/files/containerd-xolis-kata-pvm.toml" \
  "${containerd_fragment_destination}"

python3 - \
  "${dragonball_config_source}" \
  "${dragonball_config_destination}" \
  "${guest_kernel_destination}" \
  "${guest_image_source}" <<'PY'
import ast
from pathlib import Path
import re
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
kernel = sys.argv[3]
image = sys.argv[4]
configuration = source.read_text(encoding="utf-8")

configuration, kernel_replacements = re.subn(
    r'^kernel\s*=.*$', f'kernel = "{kernel}"', configuration, count=1, flags=re.MULTILINE
)
configuration, image_replacements = re.subn(
    r'^image\s*=.*$', f'image = "{image}"', configuration, count=1, flags=re.MULTILINE
)
if kernel_replacements != 1 or image_replacements != 1:
    raise SystemExit("Dragonball kernel or image setting was not found exactly once")

extra_args_pattern = re.compile(r'^virtio_fs_extra_args\s*=\s*(\[.*\])$', re.MULTILINE)
match = extra_args_pattern.search(configuration)
if match:
    args = ast.literal_eval(match.group(1))
    filtered = []
    index = 0
    while index < len(args):
        if index + 1 < len(args) and args[index:index + 2] == ["-o", "announce_submounts"]:
            index += 2
            continue
        if args[index] == "announce_submounts":
            index += 1
            continue
        filtered.append(args[index])
        index += 1
    rendered = "[" + ", ".join(f'"{value}"' for value in filtered) + "]"
    configuration = extra_args_pattern.sub(
        f"virtio_fs_extra_args = {rendered}", configuration, count=1
    )

destination.write_text(configuration, encoding="utf-8")
PY

XOLIS_CONTAINERD_FRAGMENT_PATH="${containerd_fragment_destination}" \
  /usr/local/sbin/xolis-enable-containerd-import

if [[ "${PVM_INSTALL_RUNTIME_DEFAULT:-false}" == "true" ]]; then
  install -m 0644 "${dragonball_config_destination}" \
    /opt/kata/share/defaults/kata-containers/runtime-rs/configuration.toml
fi

if [[ "${PVM_RESTART_CONTAINERD:-true}" == "true" ]]; then
  systemctl restart containerd
fi

printf 'Installed the PVM Dragonball runtime integration with handler xolis-kata-pvm\n'
