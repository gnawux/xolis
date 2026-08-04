#!/usr/bin/env bash
set -euo pipefail

runtime_handler="${PVM_RUNTIME_HANDLER:-xolis-kata-pvm}"
runtime_endpoint="${PVM_CRI_ENDPOINT:-unix:///run/containerd/containerd.sock}"
image="${PVM_SMOKE_IMAGE:-docker.io/library/python:3.12-alpine}"
require_network="${PVM_SMOKE_REQUIRE_NETWORK:-false}"
expected_vcpus="${PVM_SMOKE_VCPUS:-1}"
work_directory="$(mktemp -d /tmp/xolis-pvm-runtime-smoke.XXXXXX)"
shared_directory="${work_directory}/shared"
pod_config="${work_directory}/pod.json"
container_config="${work_directory}/container.json"
pod_id=""
container_id=""

crictl_command=(
  crictl
  --runtime-endpoint "${runtime_endpoint}"
  --image-endpoint "${runtime_endpoint}"
)

cleanup() {
  if [[ -n "${container_id}" ]]; then
    "${crictl_command[@]}" stop "${container_id}" >/dev/null 2>&1 || true
    "${crictl_command[@]}" rm "${container_id}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${pod_id}" ]]; then
    "${crictl_command[@]}" stopp "${pod_id}" >/dev/null 2>&1 || true
    "${crictl_command[@]}" rmp "${pod_id}" >/dev/null 2>&1 || true
  fi
  rm -rf -- "${work_directory}"
}
trap cleanup EXIT

for command_name in crictl python3; do
  if ! command -v "${command_name}" >/dev/null; then
    echo "Required smoke-test command is missing: ${command_name}" >&2
    exit 1
  fi
done
if [[ ! "${expected_vcpus}" =~ ^[1-9][0-9]*$ ]]; then
  echo "PVM_SMOKE_VCPUS must be a positive integer" >&2
  exit 1
fi

mkdir -p "${shared_directory}"
python3 - "${pod_config}" "${container_config}" "${shared_directory}" "${image}" "${require_network}" "${expected_vcpus}" <<'PY'
import json
from pathlib import Path
import sys

pod_path = Path(sys.argv[1])
container_path = Path(sys.argv[2])
shared_path = sys.argv[3]
image = sys.argv[4]
require_network = sys.argv[5].lower() == "true"
expected_vcpus = int(sys.argv[6])

pod = {
    "metadata": {"name": "xolis-pvm-runtime-smoke", "namespace": "default", "uid": "xolis-pvm-runtime-smoke", "attempt": 1},
    "log_directory": "/tmp",
    "annotations": {"io.katacontainers.config.hypervisor.default_vcpus": str(expected_vcpus)},
    "linux": {"security_context": {"namespace_options": {"network": 0 if require_network else 2}}},
}
program = r'''
import os
from pathlib import Path
import socket
import time
import urllib.request

REQUIRE_NETWORK = __REQUIRE_NETWORK__
EXPECTED_VCPUS = __EXPECTED_VCPUS__

shared = Path("/shared/runtime-smoke")
shared.write_text("dragonball-pvm\n", encoding="utf-8")
os.setxattr(shared, "user.xolis", b"runtime-smoke")
names = os.listxattr(shared)
assert "user.xolis" in names, names
assert os.getxattr(shared, "user.xolis") == b"runtime-smoke"
os.removexattr(shared, "user.xolis")

payload = bytearray(64 * 1024 * 1024)
for offset in range(0, len(payload), 4096):
    payload[offset] = offset // 4096 % 251
assert sum(payload[::4096]) > 0

start = time.monotonic()
time.sleep(0.05)
assert time.monotonic() > start

print("GUEST_KERNEL=" + os.uname().release)
print("GUEST_CPUS=" + str(os.cpu_count()))
assert os.cpu_count() >= EXPECTED_VCPUS, (os.cpu_count(), EXPECTED_VCPUS)
print("MEMORY_OK=67108864")
print("XATTR_OK=" + ",".join(names))
if REQUIRE_NETWORK:
    response = urllib.request.urlopen("http://example.com", timeout=10)
    assert response.status == 200, response.status
    print("DNS_OK=" + socket.gethostbyname("example.com"))
    print("NETWORK_OK=200")
else:
    print("NETWORK_SKIPPED=no-cni")
print("PVM_RUNTIME_SMOKE_OK")
'''
program = program.replace("__REQUIRE_NETWORK__", repr(require_network))
program = program.replace("__EXPECTED_VCPUS__", repr(expected_vcpus))
container = {
    "metadata": {"name": "runtime-smoke", "attempt": 1},
    "image": {"image": image},
    "command": ["python3", "-c", program],
    "log_path": "xolis-pvm-runtime-smoke.log",
    "mounts": [{"container_path": "/shared", "host_path": shared_path}],
    "linux": {
        "resources": {"cpu_period": 100000, "cpu_quota": 200000, "memory_limit_in_bytes": 536870912},
        "security_context": {"privileged": False},
    },
}
pod_path.write_text(json.dumps(pod), encoding="utf-8")
container_path.write_text(json.dumps(container), encoding="utf-8")
PY

"${crictl_command[@]}" pull "${image}" >/dev/null
started_at="$(date +%s%3N)"
pod_id="$("${crictl_command[@]}" runp --runtime "${runtime_handler}" "${pod_config}")"
container_id="$("${crictl_command[@]}" create "${pod_id}" "${container_config}" "${pod_config}")"
"${crictl_command[@]}" start "${container_id}" >/dev/null

for _ in $(seq 1 60); do
  state="$("${crictl_command[@]}" inspect "${container_id}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"]["state"])')"
  if [[ "${state}" == "CONTAINER_EXITED" ]]; then
    break
  fi
  sleep 1
done

inspect_output="$("${crictl_command[@]}" inspect "${container_id}")"
state="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"]["state"])' <<<"${inspect_output}")"
exit_code="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"]["exitCode"])' <<<"${inspect_output}")"
if [[ "${state}" != "CONTAINER_EXITED" || "${exit_code}" != "0" ]]; then
  "${crictl_command[@]}" logs "${container_id}" >&2 || true
  echo "PVM runtime smoke test ended in state ${state} with exit code ${exit_code}" >&2
  exit 1
fi

"${crictl_command[@]}" logs "${container_id}"
printf 'CRI_DURATION_MS=%s\n' "$(($(date +%s%3N) - started_at))"
