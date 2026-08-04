#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
pvm_directory="$(cd -- "${script_directory}/.." && pwd)"
source "${pvm_directory}/versions.sh"

expected_kernel="${PVM_EXPECTED_HOST_KERNEL:-${PVM_KERNEL_VERSION}-xolis-pvm}"
configuration="/etc/kata-containers/configuration-xolis-pvm-dragonball.toml"
guest_kernel="/opt/kata/share/kata-containers/vmlinux-pvm"

if [[ "$(uname -r)" != "${expected_kernel}" ]]; then
  echo "Running kernel is $(uname -r), expected ${expected_kernel}" >&2
  exit 1
fi
if ! grep -qw -- "${PVM_HOST_REQUIRED_CMDLINE}" /proc/cmdline; then
  echo "Host command line does not contain ${PVM_HOST_REQUIRED_CMDLINE}" >&2
  exit 1
fi
if grep -qwE 'vmx|svm' /proc/cpuinfo; then
  echo "The PVM qualification host unexpectedly exposes vmx or svm" >&2
  exit 1
fi
for module in kvm kvm_pvm; do
  if [[ ! -d "/sys/module/${module}" ]]; then
    echo "Required kernel module is not loaded: ${module}" >&2
    exit 1
  fi
done
for path in \
  /dev/kvm \
  "${configuration}" \
  "${guest_kernel}" \
  /opt/kata/share/kata-containers/kata-containers.img \
  /etc/xolis/kata-build \
  /usr/local/bin/containerd-shim-kata-v2 \
  /usr/local/bin/kata-runtime; do
  if [[ ! -e "${path}" ]]; then
    echo "Required PVM runtime path does not exist: ${path}" >&2
    exit 1
  fi
done

grep -Fq "kernel = \"${guest_kernel}\"" "${configuration}"
grep -Fq 'image = "/opt/kata/share/kata-containers/kata-containers.img"' "${configuration}"
grep -Fq 'shared_fs = "inline-virtio-fs"' "${configuration}"
if grep -Fq 'announce_submounts' "${configuration}"; then
  echo "The unsupported announce_submounts option remains enabled" >&2
  exit 1
fi
if grep -Fq 'nested_kvm_cpuid_workaround=true' /etc/xolis/kata-build; then
  echo "The AWS nested-KVM CPUID workaround must not be enabled for PVM" >&2
  exit 1
fi

python3 - <<'PY'
import fcntl
import os

KVM_GET_API_VERSION = 0xAE00
descriptor = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC)
try:
    version = fcntl.ioctl(descriptor, KVM_GET_API_VERSION, 0)
finally:
    os.close(descriptor)
if version != 12:
    raise SystemExit(f"KVM API version is {version}, expected 12")
PY

printf 'PVM_RUNTIME_READY host=%s guest=%s handler=xolis-kata-pvm\n' \
  "$(uname -r)" "$(sha256sum "${guest_kernel}" | cut -d' ' -f1)"
