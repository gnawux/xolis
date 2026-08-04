#!/usr/bin/env python3
"""Prepare a warm Hermes sandbox and attach an interactive terminal to it."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import secrets
import select
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import time
import tty
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from render_hermes_profile import render


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SANDBOX_NAMESPACE = "xolis-sandboxes"
SYSTEM_NAMESPACE = "xolis-system"
SECRET_NAME = "hermes-agent-credentials"
TENANT_HEADER = "x-xolis-tenant"
DEFAULT_IMAGES = {
    "oci": "479874045111.dkr.ecr.ap-northeast-1.amazonaws.com/xolis/xolis-runtime-hermes@sha256:768be3793bc15cd06500890be65beccc76371e12ea03ba5e4e805b3f507accb9",
    "nydus": "479874045111.dkr.ecr.ap-northeast-1.amazonaws.com/xolis/xolis-runtime-hermes@sha256:3171982074bed7c91830958318d5ed41066b7f7b44aeeb1d584a6ae01d676353",
    "pvm": "479874045111.dkr.ecr.ap-northeast-1.amazonaws.com/xolis/xolis-runtime-hermes@sha256:768be3793bc15cd06500890be65beccc76371e12ea03ba5e4e805b3f507accb9",
}
MODES = {
    "oci": ("hermes-agent-v1", "hermes-agent-v1-pool", "xolis-kata"),
    "nydus": ("hermes-agent-nydus-v1", "hermes-agent-nydus-v1-pool", "xolis-kata-nydus"),
    "pvm": ("hermes-agent-pvm-v1", "hermes-agent-pvm-v1-pool", "xolis-kata-pvm"),
}
API_ENV_NAMES = (
    "XOLIS_PROFILE",
    "XOLIS_WARM_POOL",
    "XOLIS_MAXIMUM_COMMAND_TIMEOUT_SECONDS",
)


class DemoError(RuntimeError):
    """A user-actionable demo preparation or execution failure."""


def status(message: str) -> None:
    print(f"==> {message}", file=sys.stderr)


def run_kubectl(
    arguments: list[str], *, input_text: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["kubectl", *arguments],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise DemoError(f"kubectl {' '.join(arguments)} failed: {detail}")
    return result


def kubectl_json(arguments: list[str]) -> dict[str, Any]:
    result = run_kubectl([*arguments, "-o", "json"])
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise DemoError(f"kubectl {' '.join(arguments)} did not return a JSON object")
    return value


def ensure_prerequisites(runtime_class: str) -> None:
    if shutil.which("kubectl") is None:
        raise DemoError("kubectl is not installed or is not on PATH")
    run_kubectl(["version", "--request-timeout=10s"])
    run_kubectl(["-n", SYSTEM_NAMESPACE, "get", "deployment/xolis-api"])
    run_kubectl(["get", f"runtimeclass/{runtime_class}"])
    run_kubectl(["-n", SANDBOX_NAMESPACE, "get", f"secret/{SECRET_NAME}"])
    nodes = kubectl_json(
        ["get", "nodes", "-l", "xolis.io/kata-ready=true"]
    ).get("items", [])
    ready_nodes = [
        node
        for node in nodes
        if any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in node.get("status", {}).get("conditions", [])
        )
    ]
    if not ready_nodes:
        raise DemoError("no Ready node has the label xolis.io/kata-ready=true")


def apply_manifest(contents: str) -> None:
    run_kubectl(["apply", "-f", "-"], input_text=contents)


def prepare_runtime_class(image_mode: str) -> None:
    if image_mode == "nydus":
        status("Applying the opt-in Nydus RuntimeClass")
        run_kubectl(
            ["apply", "-f", str(REPOSITORY_ROOT / "deploy/bootstrap/xolis-runtime-nydus.yaml")]
        )


def prepare_profile(image_mode: str, image_reference: str) -> None:
    status(f"Applying the Hermes {image_mode.upper()} profile")
    apply_manifest(render(image_reference, REPOSITORY_ROOT, image_mode))


def deployment_environment() -> dict[str, str]:
    deployment = kubectl_json(
        ["-n", SYSTEM_NAMESPACE, "get", "deployment/xolis-api"]
    )
    containers = deployment["spec"]["template"]["spec"]["containers"]
    api = next(container for container in containers if container["name"] == "api")
    return {
        item["name"]: item.get("value", "")
        for item in api.get("env", [])
        if "value" in item
    }


def configure_api(profile: str, warm_pool: str) -> dict[str, str | None]:
    current = deployment_environment()
    original = {name: current.get(name) for name in API_ENV_NAMES}
    run_kubectl(
        [
            "-n",
            SYSTEM_NAMESPACE,
            "set",
            "env",
            "deployment/xolis-api",
            f"XOLIS_PROFILE={profile}",
            f"XOLIS_WARM_POOL={warm_pool}",
            "XOLIS_MAXIMUM_COMMAND_TIMEOUT_SECONDS=900",
        ]
    )
    run_kubectl(
        [
            "-n",
            SYSTEM_NAMESPACE,
            "rollout",
            "status",
            "deployment/xolis-api",
            "--timeout=180s",
        ]
    )
    return original


def verify_prepared_environment(
    profile: str, warm_pool: str, timeout_seconds: int
) -> None:
    current = deployment_environment()
    expected = {
        "XOLIS_PROFILE": profile,
        "XOLIS_WARM_POOL": warm_pool,
    }
    mismatches = [
        f"{name}={current.get(name, '<unset>')} (expected {value})"
        for name, value in expected.items()
        if current.get(name) != value
    ]
    if mismatches:
        raise DemoError(
            "xolis-api is not configured for the prepared Hermes service: "
            + "; ".join(mismatches)
        )
    wait_for_warm_pool(warm_pool, 1, timeout_seconds)


def restore_api(original: dict[str, str | None]) -> None:
    assignments = [
        f"{name}={value}" if value is not None else f"{name}-"
        for name, value in original.items()
    ]
    run_kubectl(
        ["-n", SYSTEM_NAMESPACE, "set", "env", "deployment/xolis-api", *assignments]
    )
    run_kubectl(
        [
            "-n",
            SYSTEM_NAMESPACE,
            "rollout",
            "status",
            "deployment/xolis-api",
            "--timeout=180s",
        ]
    )


def set_warm_pool(name: str, replicas: int) -> None:
    patch = json.dumps({"spec": {"replicas": replicas}}, separators=(",", ":"))
    run_kubectl(
        [
            "-n",
            SANDBOX_NAMESPACE,
            "patch",
            "sandboxwarmpool",
            name,
            "--type=merge",
            "--patch",
            patch,
        ]
    )


def wait_for_warm_pool(name: str, replicas: int, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        pool = kubectl_json(
            ["-n", SANDBOX_NAMESPACE, "get", f"sandboxwarmpool/{name}"]
        )
        desired = int(pool.get("spec", {}).get("replicas", 0))
        ready = int(pool.get("status", {}).get("readyReplicas", 0))
        if desired == replicas and ready == replicas:
            return
        time.sleep(2)
    raise DemoError(f"timed out waiting for warm pool {name} to have {replicas} Ready replica(s)")


def unused_local_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class PortForward:
    def __init__(self) -> None:
        self.port = unused_local_port()
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> "PortForward":
        self.process = subprocess.Popen(
            [
                "kubectl",
                "-n",
                SYSTEM_NAMESPACE,
                "port-forward",
                "service/xolis-api",
                f"{self.port}:8080",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 20
        url = f"http://127.0.0.1:{self.port}/healthz"
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise DemoError("xolis-api port-forward exited before becoming ready")
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        return self
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        raise DemoError("timed out waiting for the xolis-api port-forward")

    def __exit__(self, *_: object) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


class ApiClient:
    def __init__(self, port: int, tenant: str) -> None:
        self.base_url = f"http://127.0.0.1:{port}"
        self.tenant = tenant
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
        headers: dict[str, str] | None = None,
    ) -> bytes:
        data = json.dumps(payload).encode() if payload is not None else None
        request_headers = {TENANT_HEADER: self.tenant, **(headers or {})}
        if payload is not None:
            request_headers["content-type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                status_code, body = response.status, response.read()
        except urllib.error.HTTPError as error:
            status_code, body = error.code, error.read()
            error.close()
        if status_code not in expected:
            raise DemoError(
                f"Xolis API returned HTTP {status_code}: {body.decode(errors='replace')}"
            )
        return body

    def create(self, profile: str, ttl_seconds: int) -> str:
        key = str(uuid.uuid4())
        body = self.request(
            "POST",
            "/v1/sandboxes",
            payload={
                "profile": profile,
                "ttlSeconds": ttl_seconds,
                "metadata": {"purpose": "hermes-interactive-demo"},
            },
            expected=(202,),
            headers={"idempotency-key": key},
        )
        value = json.loads(body)
        return str(value["id"])

    def wait_running(self, sandbox_id: str, timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            value = json.loads(self.request("GET", f"/v1/sandboxes/{sandbox_id}"))
            if value.get("state") == "Running":
                return
            if value.get("state") == "Failed":
                raise DemoError(f"sandbox failed: {value.get('reason', 'no reason reported')}")
            time.sleep(1)
        raise DemoError(f"timed out waiting for sandbox {sandbox_id} to become Running")

    def delete(self, sandbox_id: str) -> None:
        self.request("DELETE", f"/v1/sandboxes/{sandbox_id}", expected=(204, 404))


def encode_frame(payload: bytes, opcode: int = 1) -> bytes:
    """Encode one final, masked client WebSocket frame."""
    first = 0x80 | opcode
    length = len(payload)
    if length < 126:
        header = bytes((first, 0x80 | length))
    elif length < 65536:
        header = bytes((first, 0x80 | 126)) + struct.pack("!H", length)
    else:
        header = bytes((first, 0x80 | 127)) + struct.pack("!Q", length)
    mask = secrets.token_bytes(4)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return header + mask + masked


def read_exact(connection: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = connection.recv(length - len(chunks))
        if not chunk:
            raise DemoError("interactive session connection closed unexpectedly")
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame(connection: socket.socket) -> tuple[int, bytes]:
    first, second = read_exact(connection, 2)
    if not first & 0x80:
        raise DemoError("fragmented WebSocket frames are not supported")
    opcode = first & 0x0F
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", read_exact(connection, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(connection, 8))[0]
    mask = read_exact(connection, 4) if second & 0x80 else b""
    payload = read_exact(connection, length)
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return opcode, payload


def websocket_connect(port: int, tenant: str, sandbox_id: str) -> socket.socket:
    connection = socket.create_connection(("127.0.0.1", port), timeout=30)
    connection.settimeout(None)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    path = f"/v1/sandboxes/{sandbox_id}/sessions"
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"{TENANT_HEADER}: {tenant}\r\n\r\n"
    )
    connection.sendall(request.encode("ascii"))
    response = bytearray()
    while b"\r\n\r\n" not in response:
        response.extend(read_exact(connection, 1))
        if len(response) > 16384:
            raise DemoError("WebSocket upgrade response was too large")
    header = response.decode("iso-8859-1")
    expected_accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    if not header.startswith("HTTP/1.1 101 ") or f"Sec-WebSocket-Accept: {expected_accept}".lower() not in header.lower():
        connection.close()
        raise DemoError(f"WebSocket upgrade failed: {header.splitlines()[0]}")
    return connection


def terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size((120, 30))
    return size.lines, size.columns


def send_json(connection: socket.socket, value: dict[str, Any]) -> None:
    connection.sendall(encode_frame(json.dumps(value, separators=(",", ":")).encode()))


def interactive_session(
    port: int,
    tenant: str,
    sandbox_id: str,
    ttl_seconds: int,
    hermes_model: str | None = None,
    hermes_provider: str | None = None,
) -> int:
    connection = websocket_connect(port, tenant, sandbox_id)
    rows, columns = terminal_size()
    command = ["hermes"]
    if hermes_model is not None and hermes_provider is not None:
        command.extend(("--model", hermes_model, "--provider", hermes_provider))
    send_json(
        connection,
        {
            "type": "start",
            "command": shlex.join(command),
            "ttl_seconds": ttl_seconds,
            "rows": rows,
            "columns": columns,
        },
    )
    stdin_fd = sys.stdin.fileno()
    old_attributes = termios.tcgetattr(stdin_fd) if os.isatty(stdin_fd) else None
    resize_requested = True

    def request_resize(*_: object) -> None:
        nonlocal resize_requested
        resize_requested = True

    old_winch = signal.signal(signal.SIGWINCH, request_resize)
    try:
        if old_attributes is not None:
            tty.setraw(stdin_fd)
        while True:
            if resize_requested:
                rows, columns = terminal_size()
                send_json(connection, {"type": "resize", "rows": rows, "columns": columns})
                resize_requested = False
            readable, _, _ = select.select([connection, stdin_fd], [], [], 0.25)
            if stdin_fd in readable:
                data = os.read(stdin_fd, 4096)
                if not data:
                    send_json(connection, {"type": "close"})
                else:
                    send_json(
                        connection,
                        {"type": "input", "data": base64.b64encode(data).decode("ascii")},
                    )
            if connection in readable:
                opcode, payload = read_frame(connection)
                if opcode == 8:
                    return 0
                if opcode == 9:
                    connection.sendall(encode_frame(payload, opcode=10))
                    continue
                if opcode != 1:
                    continue
                message = json.loads(payload)
                message_type = message.get("type")
                if message_type == "output":
                    os.write(sys.stdout.fileno(), base64.b64decode(message["data"]))
                elif message_type == "exit":
                    return int(message.get("exit_code", 0))
                elif message_type in {"error", "timeout"}:
                    raise DemoError(message.get("message", "interactive Hermes session timed out"))
    except KeyboardInterrupt:
        with contextlib.suppress(OSError):
            send_json(connection, {"type": "cancel"})
        return 130
    finally:
        signal.signal(signal.SIGWINCH, old_winch)
        if old_attributes is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attributes)
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-mode", choices=tuple(MODES), default="oci")
    parser.add_argument(
        "--image-reference",
        help="Immutable Hermes ECR reference (defaults to the validated reference for the mode)",
    )
    parser.add_argument("--tenant", default="xolis-hermes-demo")
    parser.add_argument("--session-ttl", type=int, default=900)
    parser.add_argument("--sandbox-ready-timeout", type=int, default=180)
    parser.add_argument("--warm-pool-timeout", type=int, default=300)
    parser.add_argument(
        "--hermes-model",
        help="Model ID passed to Hermes for this ephemeral session",
    )
    parser.add_argument(
        "--hermes-provider",
        help="Provider passed to Hermes for this ephemeral session",
    )
    parser.add_argument(
        "--egress-manifest",
        type=Path,
        help="Reviewed NetworkPolicy that permits only the selected model provider",
    )
    parser.add_argument(
        "--keep-prepared",
        action="store_true",
        help="Keep the warm pool and Hermes API configuration after the session",
    )
    parser.add_argument(
        "--use-prepared",
        action="store_true",
        help="Claim an existing Ready warm sandbox without reapplying or scaling the profile",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.session_ttl <= 7200:
        raise DemoError("--session-ttl must be between 1 and 7200 seconds")
    if bool(args.hermes_model) != bool(args.hermes_provider):
        raise DemoError("--hermes-model and --hermes-provider must be supplied together")
    profile, warm_pool, runtime_class = MODES[args.image_mode]
    image_reference = args.image_reference or DEFAULT_IMAGES[args.image_mode]
    original_api: dict[str, str | None] | None = None
    sandbox_id: str | None = None
    client: ApiClient | None = None
    forwarding: PortForward | None = None
    try:
        if shutil.which("kubectl") is None:
            raise DemoError("kubectl is not installed or is not on PATH")
        if not args.use_prepared:
            prepare_runtime_class(args.image_mode)
        status("Checking the cluster, runtime, sandbox node, and credential Secret")
        ensure_prerequisites(runtime_class)
        if args.use_prepared:
            status("Using the existing Hermes profile and Ready warm pool")
            verify_prepared_environment(profile, warm_pool, args.warm_pool_timeout)
        else:
            prepare_profile(args.image_mode, image_reference)
        if args.egress_manifest is not None:
            status("Applying the reviewed model-provider egress policy")
            run_kubectl(["apply", "-f", str(args.egress_manifest.resolve())])
        elif args.use_prepared:
            status("No egress manifest supplied; leaving the existing policy unchanged")
        else:
            status("No egress manifest supplied; the checked-in profile permits DNS only")
        if not args.use_prepared:
            status("Configuring xolis-api for the Hermes profile")
            original_api = configure_api(profile, warm_pool)
            status("Warming one Hermes sandbox")
            set_warm_pool(warm_pool, 1)
            wait_for_warm_pool(warm_pool, 1, args.warm_pool_timeout)
        forwarding = PortForward()
        forwarding.__enter__()
        client = ApiClient(forwarding.port, args.tenant)
        status("Creating a sandbox from the warm pool")
        sandbox_id = client.create(profile, args.session_ttl)
        client.wait_running(sandbox_id, args.sandbox_ready_timeout)
        status(f"Starting Hermes in sandbox {sandbox_id}; type /quit to exit")
        return interactive_session(
            forwarding.port,
            args.tenant,
            sandbox_id,
            args.session_ttl,
            args.hermes_model,
            args.hermes_provider,
        )
    finally:
        if client is not None and sandbox_id is not None:
            status(f"Deleting sandbox {sandbox_id}")
            try:
                client.delete(sandbox_id)
            except Exception as error:
                status(f"Warning: sandbox cleanup failed: {error}")
        if forwarding is not None:
            forwarding.__exit__(None, None, None)
        if not args.keep_prepared and not args.use_prepared:
            status("Scaling the Hermes warm pool to zero")
            try:
                set_warm_pool(warm_pool, 0)
            except Exception as error:
                status(f"Warning: warm-pool cleanup failed: {error}")
            if original_api is not None:
                status("Restoring the previous xolis-api configuration")
                try:
                    restore_api(original_api)
                except Exception as error:
                    status(f"Warning: xolis-api restoration failed: {error}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DemoError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
