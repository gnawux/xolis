#!/usr/bin/env bash
set -euo pipefail

required_variables=(
  KATA_VERSION
  KATA_ARCHIVE_URL
  KATA_ARCHIVE_SHA256
  KATA_SOURCE_COMMIT
)

kata_patch_commits=(
  "7381d8eee0089a454bf6a67dc4a068faabfd1a78"
  "dbcd740dcb5be9f0d60019a2f18e74cdde4821af"
)
kata_patch_files=(
  "/tmp/0001-runtime-rs-allow-listxattr.patch"
  "/tmp/0002-runtime-rs-allow-name-to-handle-at.patch"
)
kata_build_provenance="/etc/xolis/kata-build"

for variable in "${required_variables[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    echo "${variable} must be set" >&2
    exit 1
  fi
done

nydus_variables=(
  NYDUS_SNAPSHOTTER_VERSION
  NYDUS_SNAPSHOTTER_ARCHIVE_URL
  NYDUS_SNAPSHOTTER_ARCHIVE_SHA256
  NYDUS_DAEMON_VERSION
  NYDUS_DAEMON_ARCHIVE_URL
  NYDUS_DAEMON_ARCHIVE_SHA256
)
nydus_variable_count=0
for variable in "${nydus_variables[@]}"; do
  if [[ -n "${!variable:-}" ]]; then
    ((nydus_variable_count += 1))
  fi
done

if (( nydus_variable_count != 0 && nydus_variable_count != ${#nydus_variables[@]} )); then
  echo "NYDUS_VERSION, NYDUS_ARCHIVE_URL, and NYDUS_ARCHIVE_SHA256 must be set together" >&2
  exit 1
fi

# EKS-optimized AL2023 already provides curl-minimal. Installing the full curl
# package conflicts with it, so retain the supplied curl binary.
dnf install -y clang gcc gcc-c++ git make openssl-devel pkgconf-pkg-config python3 tar xz zstd cloud-utils-growpart xfsprogs

root_device="$(readlink -f "$(findmnt --noheadings --output SOURCE /)")"
root_disk="$(lsblk --noheadings --output PKNAME "${root_device}" | tr -d '[:space:]')"
root_fstype="$(findmnt --noheadings --output FSTYPE / | tr -d '[:space:]')"
if [[ "${root_device}" =~ p([0-9]+)$ ]]; then
  root_partition="${BASH_REMATCH[1]}"
else
  root_partition="${root_device##*[!0-9]}"
fi
if [[ -z "${root_disk}" || -z "${root_partition}" ]]; then
  echo "Unable to determine the root disk and partition for ${root_device}" >&2
  exit 1
fi
if ! growpart "/dev/${root_disk}" "${root_partition}"; then
  # EKS-optimized AL2023 can already have expanded the root partition. The
  # growpart utility exits non-zero and prints NOCHANGE in that case.
  echo "Root partition is already at its maximum size; continuing"
fi
case "${root_fstype}" in
  xfs)
    xfs_growfs /
    ;;
  ext4)
    resize2fs "${root_device}"
    ;;
  *)
    echo "Unsupported root filesystem for expansion: ${root_fstype}" >&2
    exit 1
    ;;
esac

# The runtime-rs + built-in Dragonball build requires more than the source
# EKS AMI's default filesystem size, even when Packer attaches a larger EBS
# volume. Fail early if the image did not expand its root filesystem.
root_available_kib="$(df --portability --block-size=1024 / | awk 'NR == 2 { print $4 }')"
if (( root_available_kib < 94371840 )); then
  echo "Root filesystem has only ${root_available_kib} KiB free after expansion; at least 90 GiB is required" >&2
  exit 1
fi

# EKS-optimized AL2023 mounts /tmp separately with a small capacity.  Keep the
# source tree and Cargo artifacts on the expanded root filesystem instead.
install -d -m 0755 /opt/xolis-build
work_directory="$(mktemp -d /opt/xolis-build/kata.XXXXXX)"
trap 'rm -rf "${work_directory}"' EXIT

download_and_verify() {
  local url="$1"
  local checksum="$2"
  local output="$3"

  curl --fail --location --retry 3 --proto '=https' --tlsv1.2 --output "${output}" "${url}"
  printf '%s  %s\n' "${checksum}" "${output}" | sha256sum --check --status
}

extract_archive() {
  local archive="$1"
  local destination="$2"

  mkdir -p "${destination}"
  tar --extract --file "${archive}" --directory "${destination}"
}

install_release_root() {
  local staging_directory="$1"
  local binary_name="$2"
  local destination="$3"
  local binary_path
  local release_root

  binary_path="$(find "${staging_directory}" -type f -path "*/bin/${binary_name}" -print -quit)"
  if [[ -z "${binary_path}" ]]; then
    echo "${binary_name} was not found in the verified archive" >&2
    exit 1
  fi

  release_root="$(dirname "$(dirname "${binary_path}")")"
  mkdir -p "${destination}"
  cp -a "${release_root}/." "${destination}/"
}

kata_dragonball_config="/opt/kata/share/defaults/kata-containers/runtime-rs/configuration-dragonball.toml"

if [[ "${REUSE_EXISTING_KATA_RUNTIME:-false}" == "true" ]]; then
  test -x /opt/kata/bin/kata-runtime
  test -x /opt/kata/runtime-rs/bin/containerd-shim-kata-v2
  test -f "${kata_dragonball_config}"
  grep -q '^\[hypervisor\.dragonball\]' "${kata_dragonball_config}"
  test -f "${kata_build_provenance}"
  grep -Fxq "version=${KATA_VERSION}" "${kata_build_provenance}"
  grep -Fxq "source_commit=${KATA_SOURCE_COMMIT}" "${kata_build_provenance}"
  for patch_commit in "${kata_patch_commits[@]}"; do
    grep -Fxq "patch_commit=${patch_commit}" "${kata_build_provenance}"
  done
  echo "Reusing the validated Kata runtime from the immutable source AMI"
else
  kata_archive="${work_directory}/kata-static.tar.zst"

  download_and_verify "${KATA_ARCHIVE_URL}" "${KATA_ARCHIVE_SHA256}" "${kata_archive}"

  extract_archive "${kata_archive}" "${work_directory}/kata"
  install_release_root "${work_directory}/kata" "kata-runtime" "/opt/kata"

  kata_source_directory="${work_directory}/kata-containers"
  git clone --filter=blob:none --no-checkout https://github.com/kata-containers/kata-containers.git "${kata_source_directory}"
  git -C "${kata_source_directory}" checkout --detach "${KATA_SOURCE_COMMIT}"
  if [[ "$(git -C "${kata_source_directory}" rev-parse HEAD)" != "${KATA_SOURCE_COMMIT}" ]]; then
    echo "Kata source checkout did not resolve to ${KATA_SOURCE_COMMIT}" >&2
    exit 1
  fi

  for patch_file in "${kata_patch_files[@]}"; do
    if [[ ! -f "${patch_file}" ]]; then
      echo "Required Kata patch does not exist: ${patch_file}" >&2
      exit 1
    fi
    git -C "${kata_source_directory}" apply --check "${patch_file}"
    git -C "${kata_source_directory}" apply "${patch_file}"
  done

  dragonball_seccomp_source="${kata_source_directory}/src/runtime-rs/crates/hypervisor/src/dragonball/seccomp.rs"
  grep -Fq '(libc::SYS_listxattr, vec![])' "${dragonball_seccomp_source}"
  grep -Fq '(libc::SYS_name_to_handle_at, vec![])' "${dragonball_seccomp_source}"

  if [[ "${APPLY_AWS_NESTED_KVM_CPUID_WORKAROUND:-true}" == "true" ]]; then
# AWS M8i nested KVM exposes a modern XSAVE feature set (including AVX-512 and
# AMX) through KVM_GET_SUPPORTED_CPUID. Dragonball 4.0.0 passes those leaves to
# the guest but does not provide a compatible CR4/XCR0 virtualization path.
# The Kata guest kernel then faults in XSETBV before the agent starts. Keep the
# ordinary x86 CPU features and mask only the xstate-dependent CPUID leaves.
# PVM does not expose that nested-KVM feature set and must disable this
# workaround so its upstream CPUID behavior is tested without an unrelated
# AWS-specific source change.
  dragonball_cpuid_source="${kata_source_directory}/src/dragonball/crates/dbs_arch/src/x86_64/cpuid/transformer/intel.rs"
  python3 - "${dragonball_cpuid_source}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
old = '''        common::use_host_cpuid_function(cpuid, cpu_leaf::leaf_0x0::LEAF_NUM, false)?;
        self.process_entries(cpuid, vm_spec)
'''
new = '''        common::use_host_cpuid_function(cpuid, cpu_leaf::leaf_0x0::LEAF_NUM, false)?;
        self.process_entries(cpuid, vm_spec)?;

        // Dragonball 4.0.0 cannot safely virtualize the modern XSAVE state
        // exposed by nested KVM on AWS M8i. Hide all xstate-dependent CPU
        // capabilities so the guest uses its baseline FPU/SSE path.
        for entry in cpuid.as_mut_slice().iter_mut() {
            match entry.function {
                0x1 => entry.ecx &= !((1 << 26) | (1 << 27) | (1 << 28) | (1 << 29)),
                0x7 => {
                    entry.ebx &= !((1 << 5) | (1 << 16) | (1 << 17) | (1 << 21) | (1 << 26) | (1 << 27) | (1 << 28) | (1 << 30) | (1 << 31));
                    entry.ecx &= !((1 << 1) | (1 << 6) | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (1 << 12) | (1 << 14));
                    entry.edx &= !((1 << 2) | (1 << 3) | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (1 << 14) | (1 << 22) | (1 << 23));
                }
                0xd => {
                    entry.eax = 0;
                    entry.ebx = 0;
                    entry.ecx = 0;
                    entry.edx = 0;
                }
                _ => {}
            }
        }

        Ok(())
'''
if old not in source:
    raise SystemExit("Dragonball CPUID insertion point was not found")
path.write_text(source.replace(old, new), encoding="utf-8")
PY
  fi

  curl --fail --location --retry 3 --proto '=https' --tlsv1.2 https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.95
  source /root/.cargo/env
# Kata defaults to a musl target, but its upstream runtime-rs guide documents
# musl as optional. AL2023 does not ship a musl-gcc package, so build the
# supported GNU target for the EKS node AMI instead.
# Limit parallel compilation so the temporary Rust and native-build artifacts
# have a bounded peak disk footprint on the AMI build volume.
# Keep Cargo's extremely verbose output off the Session Manager SSH tunnel.
# Packer can otherwise stall after a successful remote build while draining
# that output. Preserve the last lines when a build does fail.
  runtime_build_log="${work_directory}/runtime-rs-build.log"
  CARGO_BUILD_JOBS=2 make -C "${kata_source_directory}/src/runtime-rs" LIBC=gnu HYPERVISOR=dragonball PREFIX=/opt/kata install >"${runtime_build_log}" 2>&1 &
  runtime_build_pid=$!
  (
    while kill -0 "${runtime_build_pid}" 2>/dev/null; do
      sleep 30
      if kill -0 "${runtime_build_pid}" 2>/dev/null; then
        echo "Kata runtime-rs Dragonball build is still running"
      fi
    done
  ) &
  runtime_build_heartbeat_pid=$!
  if ! wait "${runtime_build_pid}"; then
    wait "${runtime_build_heartbeat_pid}" || true
    tail -n 200 "${runtime_build_log}" >&2
    exit 1
  fi
  wait "${runtime_build_heartbeat_pid}" || true

  if [[ ! -f "${kata_dragonball_config}" ]]; then
    echo "The Kata runtime-rs Dragonball build did not generate ${kata_dragonball_config}" >&2
    exit 1
  fi
fi

install -d -m 0755 /usr/local/bin /usr/local/sbin /etc/containerd/conf.d /etc/kata-containers /etc/nydus /etc/xolis
install -m 0755 /opt/kata/runtime-rs/bin/containerd-shim-kata-v2 /usr/local/bin/containerd-shim-kata-v2
install -m 0755 /opt/kata/bin/kata-runtime /usr/local/bin/kata-runtime
install -m 0644 "${kata_dragonball_config}" /etc/kata-containers/configuration-xolis-dragonball.toml
install -m 0644 /tmp/containerd-xolis-kata.toml /etc/containerd/conf.d/xolis-kata.toml
install -m 0755 /tmp/enable-containerd-import /usr/local/sbin/xolis-enable-containerd-import

if [[ "${REUSE_EXISTING_KATA_RUNTIME:-false}" != "true" ]]; then
  {
    printf 'version=%s\n' "${KATA_VERSION}"
    printf 'source_commit=%s\n' "${KATA_SOURCE_COMMIT}"
    for patch_commit in "${kata_patch_commits[@]}"; do
      printf 'patch_commit=%s\n' "${patch_commit}"
    done
    printf 'nested_kvm_cpuid_workaround=%s\n' \
      "${APPLY_AWS_NESTED_KVM_CPUID_WORKAROUND:-true}"
  } >"${kata_build_provenance}"
  chmod 0644 "${kata_build_provenance}"
fi

install -d -m 0755 /etc/systemd/system/containerd.service.d
cat >/etc/systemd/system/containerd.service.d/10-xolis-runtime.conf <<'EOF'
[Service]
ExecStartPre=/usr/local/sbin/xolis-enable-containerd-import
EOF

if (( nydus_variable_count == ${#nydus_variables[@]} )); then
  if [[ ! -x /etc/eks/image-credential-provider/ecr-credential-provider ]]; then
    echo "The EKS ECR credential-provider binary is missing or not executable" >&2
    find /etc/eks -maxdepth 3 -print >&2 || true
    exit 1
  fi
  snapshotter_archive="${work_directory}/nydus-snapshotter.tar.gz"
  daemon_archive="${work_directory}/nydus-daemon.tar.gz"
  download_and_verify "${NYDUS_SNAPSHOTTER_ARCHIVE_URL}" "${NYDUS_SNAPSHOTTER_ARCHIVE_SHA256}" "${snapshotter_archive}"
  download_and_verify "${NYDUS_DAEMON_ARCHIVE_URL}" "${NYDUS_DAEMON_ARCHIVE_SHA256}" "${daemon_archive}"
  extract_archive "${snapshotter_archive}" "${work_directory}/nydus-snapshotter"
  extract_archive "${daemon_archive}" "${work_directory}/nydus-daemon"
  install_release_root "${work_directory}/nydus-snapshotter" "containerd-nydus-grpc" "/opt/nydus-snapshotter"
  install -m 0755 /opt/nydus-snapshotter/bin/containerd-nydus-grpc /usr/local/bin/containerd-nydus-grpc
  daemon_root="$(dirname "$(find "${work_directory}/nydus-daemon" -type f -name nydusd -print -quit)")"
  if [[ "${daemon_root}" == "." ]]; then
    echo "nydusd was not found in the verified archive" >&2
    exit 1
  fi
  install -m 0755 "${daemon_root}/nydusd" /usr/local/bin/nydusd
  install -m 0755 "${daemon_root}/nydus-image" /usr/local/bin/nydus-image
  install -m 0644 /tmp/nydus-snapshotter.toml /etc/nydus/snapshotter.toml
  install -m 0644 /tmp/nydusd-config.fusedev.json /etc/nydus/nydusd-config.fusedev.json
  install -m 0644 /tmp/ecr-credential-provider-config.json /etc/nydus/ecr-credential-provider-config.json

  cat >/etc/systemd/system/nydus-snapshotter.service <<'EOF'
[Unit]
Description=Nydus containerd snapshotter for Xolis
After=network.target
Before=containerd.service

[Service]
Type=simple
ExecStart=/usr/local/bin/containerd-nydus-grpc --config /etc/nydus/snapshotter.toml --log-to-stdout
Restart=always
RestartSec=1
KillMode=process

[Install]
WantedBy=multi-user.target
EOF
  systemctl enable nydus-snapshotter.service

  install -d -m 0755 /etc/xolis
  printf 'snapshotter=%s\ndaemon=%s\n' "${NYDUS_SNAPSHOTTER_VERSION}" "${NYDUS_DAEMON_VERSION}" >/etc/xolis/nydus-version
fi

systemctl daemon-reload

# Do not run nodeadm in an AMI build. The launch template supplies the NodeConfig.
kata-runtime --version
containerd-shim-kata-v2 --version
test -x /usr/local/bin/containerd-shim-kata-v2
test -f /etc/kata-containers/configuration-xolis-dragonball.toml
grep -q '^\[hypervisor\.dragonball\]' /etc/kata-containers/configuration-xolis-dragonball.toml
test -x /usr/local/sbin/xolis-enable-containerd-import
test -f "${kata_build_provenance}"
if (( nydus_variable_count == ${#nydus_variables[@]} )); then
  containerd-nydus-grpc --version
  nydusd --version
  test -f /etc/xolis/nydus-version
fi
