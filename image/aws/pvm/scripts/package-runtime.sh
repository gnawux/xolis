#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
pvm_directory="$(cd -- "${script_directory}/.." && pwd)"
source "${pvm_directory}/versions.sh"

output_directory="${PVM_RUNTIME_OUTPUT_DIRECTORY:-/opt/xolis-artifacts/runtime}"
bundle_id="kata-${KATA_VERSION}-${KATA_SOURCE_COMMIT}-pvm-${PVM_SOURCE_COMMIT}"
archive_path="${output_directory}/${bundle_id}.tar.zst"
manifest_path="${output_directory}/${bundle_id}.manifest.json"
validation_path="${output_directory}/${bundle_id}.validation.txt"

if [[ "$(id -u)" != "0" ]]; then
  echo "The PVM runtime bundle must be created as root" >&2
  exit 1
fi

install -d -m 0755 "${output_directory}"
"${script_directory}/validate-runtime.sh" | tee "${validation_path}"

bundle_paths=(
  etc/containerd/conf.d/xolis-kata-pvm.toml
  etc/kata-containers/configuration-xolis-pvm-dragonball.toml
  etc/modules-load.d/xolis-pvm.conf
  etc/xolis/kata-build
  opt/kata
  usr/local/bin/containerd-shim-kata-v2
  usr/local/bin/kata-runtime
)
for path in "${bundle_paths[@]}"; do
  if [[ ! -e "/${path}" ]]; then
    echo "Required runtime bundle path does not exist: /${path}" >&2
    exit 1
  fi
done

tar --create --zstd --file "${archive_path}" --directory / "${bundle_paths[@]}"

python3 - \
  "${manifest_path}" \
  "${archive_path}" \
  "${validation_path}" \
  "${PVM_SOURCE_COMMIT}" \
  "${KATA_VERSION}" \
  "${KATA_SOURCE_COMMIT}" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys

manifest_path = Path(sys.argv[1])
archive_path = Path(sys.argv[2])
validation_path = Path(sys.argv[3])


def describe(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


manifest = {
    "schema_version": 1,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "host": {"architecture": platform.machine(), "kernel": platform.release()},
    "pvm_source_commit": sys.argv[4],
    "kata": {"version": sys.argv[5], "source_commit": sys.argv[6]},
    "archive": describe(archive_path),
    "validation": describe(validation_path),
    "installed_files": {
        path: describe(Path(path))
        for path in [
            "/opt/kata/share/kata-containers/vmlinux-pvm",
            "/usr/local/bin/kata-runtime",
            "/usr/local/bin/containerd-shim-kata-v2",
            "/etc/kata-containers/configuration-xolis-pvm-dragonball.toml",
            "/etc/xolis/kata-build",
        ]
    },
}
manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

printf 'Created %s and %s\n' "${archive_path}" "${manifest_path}"
