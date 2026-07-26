#!/usr/bin/env bash
set -euo pipefail

required_variables=(
  KATA_VERSION
  KATA_ARCHIVE_URL
  KATA_ARCHIVE_SHA256
  NYDUS_VERSION
  NYDUS_ARCHIVE_URL
  NYDUS_ARCHIVE_SHA256
)

for variable in "${required_variables[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    echo "${variable} must be set" >&2
    exit 1
  fi
done

dnf install -y curl tar xz zstd

work_directory="$(mktemp -d)"
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

kata_archive="${work_directory}/kata-static.tar.zst"
nydus_archive="${work_directory}/nydus-static.tar.zst"

download_and_verify "${KATA_ARCHIVE_URL}" "${KATA_ARCHIVE_SHA256}" "${kata_archive}"
download_and_verify "${NYDUS_ARCHIVE_URL}" "${NYDUS_ARCHIVE_SHA256}" "${nydus_archive}"

extract_archive "${kata_archive}" "${work_directory}/kata"
extract_archive "${nydus_archive}" "${work_directory}/nydus"
install_release_root "${work_directory}/kata" "kata-runtime" "/opt/kata"
install_release_root "${work_directory}/nydus" "nydus-snapshotter" "/opt/nydus"

kata_dragonball_config="/opt/kata/share/defaults/kata-containers/configuration-dragonball.toml"
if [[ ! -f "${kata_dragonball_config}" ]]; then
  echo "The Kata archive does not provide ${kata_dragonball_config}; use a static release built with Dragonball support" >&2
  exit 1
fi

install -d -m 0755 /usr/local/bin /usr/local/sbin /etc/containerd/conf.d /etc/kata-containers /etc/nydus
install -m 0755 /opt/kata/bin/containerd-shim-kata-v2 /usr/local/bin/containerd-shim-kata-v2
install -m 0755 /opt/kata/bin/kata-runtime /usr/local/bin/kata-runtime
install -m 0755 /opt/nydus/bin/nydus-snapshotter /usr/local/bin/nydus-snapshotter
install -m 0755 /opt/nydus/bin/nydusd /usr/local/bin/nydusd
install -m 0644 "${kata_dragonball_config}" /etc/kata-containers/configuration-xolis-dragonball.toml
install -m 0644 /tmp/containerd-xolis-kata.toml /etc/containerd/conf.d/xolis-kata.toml
install -m 0644 /tmp/nydus-snapshotter.toml /etc/nydus/snapshotter.toml
install -m 0755 /tmp/enable-containerd-import /usr/local/sbin/xolis-enable-containerd-import

install -d -m 0755 /etc/systemd/system/containerd.service.d
cat >/etc/systemd/system/containerd.service.d/10-xolis-runtime.conf <<'EOF'
[Service]
ExecStartPre=/usr/local/sbin/xolis-enable-containerd-import
EOF

cat >/etc/systemd/system/nydus-snapshotter.service <<'EOF'
[Unit]
Description=Nydus containerd snapshotter for Xolis
After=containerd.service
Wants=containerd.service

[Service]
Type=simple
ExecStart=/usr/local/bin/nydus-snapshotter --config /etc/nydus/snapshotter.toml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nydus-snapshotter.service

# Do not run nodeadm in an AMI build. The launch template supplies the NodeConfig.
kata-runtime --version
nydus-snapshotter --version
test -x /usr/local/bin/containerd-shim-kata-v2
test -f /etc/kata-containers/configuration-xolis-dragonball.toml
test -x /usr/local/sbin/xolis-enable-containerd-import
