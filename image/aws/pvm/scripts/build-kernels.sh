#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
pvm_directory="$(cd -- "${script_directory}/.." && pwd)"
source "${pvm_directory}/versions.sh"

host_base_config="${PVM_HOST_BASE_CONFIG:-/boot/config-$(uname -r)}"
output_directory="${PVM_OUTPUT_DIRECTORY:-/opt/xolis-artifacts/pvm}"
build_parent="${PVM_BUILD_PARENT:-/opt/xolis-build}"
build_jobs="${PVM_BUILD_JOBS:-$(nproc)}"

required_commands=(
  bison
  curl
  flex
  gcc
  git
  make
  pahole
  python3
  rpmbuild
  rsync
  sha256sum
)

for command_name in "${required_commands[@]}"; do
  if ! command -v "${command_name}" >/dev/null; then
    echo "Required build command is missing: ${command_name}" >&2
    exit 1
  fi
done

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "PVM kernel artifacts must be built on x86_64" >&2
  exit 1
fi

if [[ ! -f "${host_base_config}" ]]; then
  echo "Pinned EKS host config does not exist: ${host_base_config}" >&2
  exit 1
fi

if [[ -z "${EKS_HOST_CONFIG_SHA256}" ]]; then
  echo "EKS_HOST_CONFIG_SHA256 must be pinned in versions.sh before a build" >&2
  exit 1
fi

printf '%s  %s\n' "${EKS_HOST_CONFIG_SHA256}" "${host_base_config}" | sha256sum --check --status

install -d -m 0755 "${build_parent}" "${output_directory}"
work_directory="$(mktemp -d "${build_parent}/pvm.XXXXXX")"

cleanup() {
  rm -rf -- "${work_directory}"
}
trap cleanup EXIT

source_directory="${work_directory}/linux"
host_build_directory="${work_directory}/host"
guest_build_directory="${work_directory}/guest"
guest_config="${work_directory}/pvm-guest.config"
host_base_config_copy="${work_directory}/eks-host-base.config"
guest_base_config_copy="${work_directory}/pvm-guest-base.config"
artifact_directory="${output_directory}/${PVM_SOURCE_COMMIT}"

git clone --filter=blob:none --no-checkout "${PVM_SOURCE_URL}" "${source_directory}"
git -C "${source_directory}" checkout --detach "${PVM_SOURCE_COMMIT}"
if [[ "$(git -C "${source_directory}" rev-parse HEAD)" != "${PVM_SOURCE_COMMIT}" ]]; then
  echo "PVM source checkout did not resolve to ${PVM_SOURCE_COMMIT}" >&2
  exit 1
fi

kernel_version="$(make -s -C "${source_directory}" kernelversion)"
if [[ "${kernel_version}" != "${PVM_KERNEL_VERSION}" ]]; then
  echo "PVM kernel version is ${kernel_version}, expected ${PVM_KERNEL_VERSION}" >&2
  exit 1
fi

curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
  --output "${guest_config}" "${PVM_GUEST_CONFIG_URL}"
printf '%s  %s\n' "${PVM_GUEST_CONFIG_SHA256}" "${guest_config}" | sha256sum --check --status

mkdir -p "${host_build_directory}" "${guest_build_directory}"
cp "${host_base_config}" "${host_base_config_copy}"
"${source_directory}/scripts/kconfig/merge_config.sh" \
  -m -O "${host_build_directory}" \
  "${host_base_config_copy}" \
  "${pvm_directory}/config/host-pvm.fragment"
make -C "${source_directory}" O="${host_build_directory}" olddefconfig
python3 "${script_directory}/validate-kernel-config.py" \
  --role host --config "${host_build_directory}/.config"

cp "${guest_config}" "${guest_base_config_copy}"
"${source_directory}/scripts/kconfig/merge_config.sh" \
  -m -O "${guest_build_directory}" \
  "${guest_base_config_copy}" \
  "${pvm_directory}/config/guest-pvm.fragment"
make -C "${source_directory}" O="${guest_build_directory}" olddefconfig
python3 "${script_directory}/validate-kernel-config.py" \
  --role guest --config "${guest_build_directory}/.config"

make -C "${source_directory}" O="${host_build_directory}" \
  -j"${build_jobs}" LOCALVERSION=-xolis-pvm binrpm-pkg
make -C "${source_directory}" O="${guest_build_directory}" \
  -j"${build_jobs}" LOCALVERSION=-xolis-pvm-guest vmlinux

install -d -m 0755 \
  "${artifact_directory}/host-rpms" \
  "${artifact_directory}/guest" \
  "${artifact_directory}/config"
find "${host_build_directory}/rpmbuild/RPMS" -type f -name '*.rpm' \
  -exec install -m 0644 {} "${artifact_directory}/host-rpms/" \;
install -m 0644 "${guest_build_directory}/vmlinux" \
  "${artifact_directory}/guest/vmlinux-pvm"
install -m 0644 "${host_build_directory}/.config" \
  "${artifact_directory}/config/host.config"
install -m 0644 "${guest_build_directory}/.config" \
  "${artifact_directory}/config/guest.config"

if ! find "${artifact_directory}/host-rpms" -type f -name '*.rpm' -print -quit | grep -q .; then
  echo "The host kernel build did not produce RPM packages" >&2
  exit 1
fi

manifest_path="${artifact_directory}/manifest.json"
python3 - \
  "${manifest_path}" \
  "${artifact_directory}" \
  "${host_base_config}" \
  "${kernel_version}" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone

manifest_path = Path(sys.argv[1])
artifact_directory = Path(sys.argv[2])
host_base_config = Path(sys.argv[3])
kernel_version = sys.argv[4]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def package_versions() -> list[str]:
    names = [
        "bc",
        "binutils",
        "bison",
        "dwarves",
        "elfutils-libelf-devel",
        "flex",
        "gcc",
        "make",
        "openssl-devel",
        "rpm-build",
        "rsync",
    ]
    result = subprocess.run(
        ["rpm", "-q", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\\n", *names],
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(result.stdout.splitlines())


files = {}
for path in sorted(artifact_directory.rglob("*")):
    if path.is_file() and path != manifest_path:
        files[str(path.relative_to(artifact_directory))] = digest(path)

manifest = {
    "schema_version": 1,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "build_host": {
        "architecture": platform.machine(),
        "kernel": platform.release(),
        "packages": package_versions(),
    },
    "pvm": {
        "source_url": os.environ["PVM_SOURCE_URL"],
        "source_commit": os.environ["PVM_SOURCE_COMMIT"],
        "kernel_version": kernel_version,
        "guest_config_url": os.environ["PVM_GUEST_CONFIG_URL"],
        "guest_config_sha256": os.environ["PVM_GUEST_CONFIG_SHA256"],
    },
    "eks_source": {
        "kubernetes_version": os.environ["EKS_KUBERNETES_VERSION"],
        "ami_release_version": os.environ["EKS_AMI_RELEASE_VERSION"],
        "ami_id": os.environ["EKS_SOURCE_AMI_ID"],
        "ami_name": os.environ["EKS_SOURCE_AMI_NAME"],
        "host_config_path": str(host_base_config),
        "host_config_sha256": digest(host_base_config),
    },
    "kata": {
        "version": os.environ["KATA_VERSION"],
        "source_commit": os.environ["KATA_SOURCE_COMMIT"],
        "rust_toolchain": os.environ["RUST_TOOLCHAIN_VERSION"],
    },
    "host_required_cmdline": os.environ["PVM_HOST_REQUIRED_CMDLINE"],
    "files": files,
}
manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

printf 'Built PVM kernel artifacts in %s\n' "${artifact_directory}"
