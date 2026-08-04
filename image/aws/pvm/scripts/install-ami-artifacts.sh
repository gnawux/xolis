#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
pvm_directory="$(cd -- "${script_directory}/.." && pwd)"
source "${pvm_directory}/versions.sh"

artifact_bucket="${PVM_ARTIFACT_BUCKET:-}"
kernel_prefix="pvm/${PVM_SOURCE_COMMIT}"
runtime_prefix="kata/${KATA_VERSION}/${KATA_SOURCE_COMMIT}/pvm-${PVM_SOURCE_COMMIT}"
work_directory="$(mktemp -d /opt/xolis-pvm-ami.XXXXXX)"
kernel_directory="${work_directory}/kernel"
runtime_directory="${work_directory}/runtime"
kernel_manifest="${kernel_directory}/manifest.json"
runtime_manifest_name="kata-${KATA_VERSION}-${KATA_SOURCE_COMMIT}-pvm-${PVM_SOURCE_COMMIT}.manifest.json"
runtime_archive_name="kata-${KATA_VERSION}-${KATA_SOURCE_COMMIT}-pvm-${PVM_SOURCE_COMMIT}.tar.zst"
runtime_manifest="${runtime_directory}/${runtime_manifest_name}"
runtime_archive="${runtime_directory}/${runtime_archive_name}"
host_release="${PVM_KERNEL_VERSION}-xolis-pvm"

cleanup() {
  rm -rf -- "${work_directory}"
}
trap cleanup EXIT

if [[ "$(id -u)" != "0" ]]; then
  echo "PVM AMI artifacts must be installed as root" >&2
  exit 1
fi
if [[ -z "${artifact_bucket}" ]]; then
  echo "PVM_ARTIFACT_BUCKET must identify the private artifact bucket" >&2
  exit 1
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "PVM AMI artifacts require an x86_64 host" >&2
  exit 1
fi

for command_name in aws grubby python3 sha256sum tar; do
  if ! command -v "${command_name}" >/dev/null; then
    echo "Required PVM AMI installation command is missing: ${command_name}" >&2
    exit 1
  fi
done

install -d -m 0755 "${kernel_directory}" "${runtime_directory}" /etc/xolis
aws s3 cp --only-show-errors --recursive \
  "s3://${artifact_bucket}/${kernel_prefix}/" "${kernel_directory}/"
aws s3 cp --only-show-errors --recursive \
  "s3://${artifact_bucket}/${runtime_prefix}/" "${runtime_directory}/"

printf '%s  %s\n' "${PVM_KERNEL_MANIFEST_SHA256}" "${kernel_manifest}" |
  sha256sum --check --status
printf '%s  %s\n' "${PVM_RUNTIME_MANIFEST_SHA256}" "${runtime_manifest}" |
  sha256sum --check --status
printf '%s  %s\n' "${PVM_RUNTIME_ARCHIVE_SHA256}" "${runtime_archive}" |
  sha256sum --check --status

python3 - "${kernel_manifest}" "${kernel_directory}" "${runtime_manifest}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

kernel_manifest = Path(sys.argv[1])
kernel_directory = Path(sys.argv[2])
runtime_manifest = Path(sys.argv[3])


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


kernel = json.loads(kernel_manifest.read_text(encoding="utf-8"))
for relative_path, expected in kernel["files"].items():
    path = kernel_directory / relative_path
    if not path.is_file():
        raise SystemExit(f"Published PVM kernel artifact is missing: {relative_path}")
    actual = digest(path)
    if actual != expected:
        raise SystemExit(
            f"PVM kernel artifact digest mismatch for {relative_path}: {actual} != {expected}"
        )

runtime = json.loads(runtime_manifest.read_text(encoding="utf-8"))
archive = runtime_manifest.parent / runtime["archive"]["name"]
if digest(archive) != runtime["archive"]["sha256"]:
    raise SystemExit("PVM runtime archive does not match its manifest")
PY

kernel_rpm="${kernel_directory}/host-rpms/kernel-${PVM_KERNEL_VERSION//-/_}_xolis_pvm-1.x86_64.rpm"
if [[ ! -f "${kernel_rpm}" ]]; then
  kernel_rpm="$(find "${kernel_directory}/host-rpms" -maxdepth 1 -type f -name 'kernel-*.rpm' ! -name 'kernel-devel-*' ! -name 'kernel-headers-*' -print -quit)"
fi
if [[ -z "${kernel_rpm}" || ! -f "${kernel_rpm}" ]]; then
  echo "The verified artifact set does not contain the PVM host kernel RPM" >&2
  exit 1
fi

dnf install -y "${kernel_rpm}"
depmod -a "${host_release}"
if [[ ! -f "/boot/initramfs-${host_release}.img" ]]; then
  dracut --force "/boot/initramfs-${host_release}.img" "${host_release}"
fi
test -f "/boot/vmlinuz-${host_release}"
test -f "/boot/initramfs-${host_release}.img"

if ! grubby --info=ALL | grep -Fq "/boot/vmlinuz-${host_release}"; then
  grubby \
    --add-kernel="/boot/vmlinuz-${host_release}" \
    --initrd="/boot/initramfs-${host_release}.img" \
    --title="Xolis PVM ${host_release}" \
    --copy-default
fi
grubby --update-kernel="/boot/vmlinuz-${host_release}" \
  --args="${PVM_HOST_REQUIRED_CMDLINE} module_blacklist=kvm_intel,kvm_amd"
grubby --set-default="/boot/vmlinuz-${host_release}"

tar --extract --zstd --file "${runtime_archive}" --directory /
install -m 0755 /tmp/xolis-enable-containerd-import \
  /usr/local/sbin/xolis-enable-containerd-import
XOLIS_CONTAINERD_FRAGMENT_PATH=/etc/containerd/conf.d/xolis-kata-pvm.toml \
  /usr/local/sbin/xolis-enable-containerd-import

install -m 0644 "${kernel_manifest}" /etc/xolis/pvm-build.json
install -m 0644 "${runtime_manifest}" /etc/xolis/pvm-runtime-build.json
install -m 0644 "${pvm_directory}/files/xolis-pvm.modules-load.conf" \
  /etc/modules-load.d/xolis-pvm.conf
install -d -m 0755 /usr/local/lib/xolis-pvm/scripts
install -m 0644 "${pvm_directory}/versions.sh" \
  /usr/local/lib/xolis-pvm/versions.sh
install -m 0755 "${pvm_directory}/scripts/validate-runtime.sh" \
  /usr/local/lib/xolis-pvm/scripts/validate-runtime.sh
install -m 0755 "${pvm_directory}/scripts/validate-ami-host.sh" \
  /usr/local/lib/xolis-pvm/scripts/validate-ami-host.sh

systemctl daemon-reload

printf 'PVM_AMI_ARTIFACTS_INSTALLED host=%s kata=%s pvm=%s\n' \
  "${host_release}" "${KATA_SOURCE_COMMIT}" "${PVM_SOURCE_COMMIT}"
