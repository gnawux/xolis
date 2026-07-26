#!/usr/bin/env python3
"""Run the Xolis service acceptance test against a Kubernetes cluster."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


class SmokeFailure(RuntimeError):
    """The deployment failed an acceptance requirement."""


class BenchmarkRecorder:
    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.metrics: dict[str, float] = {}
        self.status = "failed"
        self.error: str | None = None

    def record(self, name: str, started_at: float) -> None:
        self.metrics[name] = round(time.monotonic() - started_at, 3)

    def write(self, path: Path) -> None:
        report = {
            "schema_version": 1,
            "workflow": "service_smoke",
            "status": self.status,
            "error": self.error,
            "duration_seconds": round(time.monotonic() - self.started_at, 3),
            "metrics": self.metrics,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


class ApiError(RuntimeError):
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Xolis API returned HTTP {status}: {body.decode(errors='replace')}")


class XolisClient:
    def __init__(
        self,
        base_url: str,
        tenant: str,
        timeout_seconds: float = 30,
        reconnect: Callable[[], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.tenant = tenant
        self.timeout_seconds = timeout_seconds
        self.reconnect = reconnect
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected: Iterable[int] = (200,),
    ) -> tuple[int, bytes]:
        request_headers = {"x-xolis-tenant": self.tenant, **(headers or {})}
        if json_body is not None:
            body = json.dumps(json_body).encode()
            request_headers["content-type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        maximum_transport_attempts = 10
        for attempt in range(maximum_transport_attempts):
            try:
                try:
                    with self.opener.open(
                        request, timeout=self.timeout_seconds
                    ) as response:
                        status = response.status
                        response_body = response.read()
                except urllib.error.HTTPError as error:
                    status = error.code
                    try:
                        response_body = error.read()
                    finally:
                        error.close()
                break
            except (ConnectionError, OSError, urllib.error.URLError):
                if self.reconnect is None or attempt == maximum_transport_attempts - 1:
                    raise
                self.reconnect()
                time.sleep(1)
        if status not in set(expected):
            raise ApiError(status, response_body)
        return status, response_body

    def json_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        _, body = self.request(method, path, **kwargs)
        value = json.loads(body)
        if not isinstance(value, dict):
            raise SmokeFailure(f"expected a JSON object from {path}")
        return value

    def create(self, profile: str, ttl_seconds: int, idempotency_key: str) -> dict[str, Any]:
        return self.json_request(
            "POST",
            "/v1/sandboxes",
            json_body={
                "profile": profile,
                "ttlSeconds": ttl_seconds,
                "metadata": {"smokeRun": idempotency_key},
            },
            headers={"idempotency-key": idempotency_key},
            expected=(202,),
        )

    def get(self, sandbox_id: str) -> dict[str, Any]:
        return self.json_request("GET", f"/v1/sandboxes/{sandbox_id}")

    def delete(self, sandbox_id: str) -> None:
        self.request("DELETE", f"/v1/sandboxes/{sandbox_id}", expected=(204, 404))

    def execute(
        self, sandbox_id: str, command: str, timeout_seconds: int
    ) -> dict[str, Any]:
        return self.json_request(
            "POST",
            f"/v1/sandboxes/{sandbox_id}/commands",
            json_body={"command": command, "timeoutSeconds": timeout_seconds},
        )


class Kubernetes:
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace

    def run(self, arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["kubectl", *arguments], check=False, text=True, capture_output=True
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise SmokeFailure(f"kubectl {' '.join(arguments)} failed: {detail}")
        return result

    def get_json(self, kind: str, name: str) -> dict[str, Any] | None:
        result = self.run(
            ["-n", self.namespace, "get", kind, name, "-o", "json"], check=False
        )
        if result.returncode != 0:
            if "NotFound" in result.stderr or "not found" in result.stderr:
                return None
            raise SmokeFailure(result.stderr.strip() or result.stdout.strip())
        return json.loads(result.stdout)

    def node_json(self, name: str) -> dict[str, Any]:
        result = self.run(["get", "node", name, "-o", "json"])
        return json.loads(result.stdout)

    def delete_claim(self, name: str) -> None:
        self.run(
            [
                "-n",
                self.namespace,
                "delete",
                "sandboxclaim",
                name,
                "--ignore-not-found",
                "--wait=false",
            ],
            check=False,
        )


class PortForward:
    def __init__(self, namespace: str, service: str, local_port: int) -> None:
        self.namespace = namespace
        self.service = service
        self.local_port = local_port
        self.process: subprocess.Popen[str] | None = None
        self.started_at: float | None = None

    def __enter__(self) -> PortForward:
        self.start()
        return self

    def start(self) -> None:
        self.process = subprocess.Popen(
            [
                "kubectl",
                "-n",
                self.namespace,
                "port-forward",
                f"service/{self.service}",
                f"{self.local_port}:8080",
            ],
            stdout=None,
            stderr=None,
            text=True,
        )
        self.started_at = time.monotonic()

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process = None
        self.started_at = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def ensure_running(self) -> None:
        process_dead = self.process is None or self.process.poll() is not None
        startup_finished = (
            self.started_at is not None and time.monotonic() - self.started_at >= 2
        )
        if process_dead or startup_finished:
            self.restart()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def __exit__(self, *_: object) -> None:
        self.stop()


@dataclass(frozen=True)
class SmokeConfig:
    profile: str
    tenant: str
    namespace: str
    runtime_class: str
    node_label_key: str
    node_label_value: str
    ready_timeout_seconds: int
    cleanup_timeout_seconds: int
    maximum_upload_bytes: int
    ttl_test_seconds: int
    ttl_timeout_seconds: int
    skip_ttl: bool
    ttl_only: bool


class ServiceSmokeTest:
    def __init__(
        self,
        client: XolisClient,
        kubernetes: Kubernetes,
        config: SmokeConfig,
        benchmark: BenchmarkRecorder | None = None,
    ) -> None:
        self.client = client
        self.kubernetes = kubernetes
        self.config = config
        self.claims: set[str] = set()
        self.benchmark = benchmark or BenchmarkRecorder()

    def report(self, message: str) -> None:
        print(f"[PASS] {message}", flush=True)

    def wait_for_state(self, sandbox_id: str, wanted: set[str], timeout: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last = self.client.get(sandbox_id)
            state = last.get("state")
            if state in wanted:
                return last
            if state == "Failed":
                raise SmokeFailure(f"sandbox {sandbox_id} failed: {last.get('reason')}")
            time.sleep(2)
        raise SmokeFailure(f"sandbox {sandbox_id} did not reach {sorted(wanted)}: {last}")

    def wait_until_missing(self, kind: str, name: str, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.kubernetes.get_json(kind, name) is None:
                return
            time.sleep(2)
        raise SmokeFailure(f"{kind}/{name} was not deleted")

    def claim_runtime(self, claim_name: str) -> tuple[str, str]:
        claim = self.kubernetes.get_json("sandboxclaim", claim_name)
        if claim is None:
            raise SmokeFailure(f"sandboxclaim/{claim_name} is missing")
        sandbox = claim.get("status", {}).get("sandbox", {})
        runtime_name = sandbox.get("name")
        pod_ips = sandbox.get("podIPs", [])
        if not runtime_name or not pod_ips:
            raise SmokeFailure(f"sandboxclaim/{claim_name} has no runtime identity")
        return runtime_name, pod_ips[0]

    def validate_placement(self, claim_name: str) -> str:
        runtime_name, _ = self.claim_runtime(claim_name)
        pod = self.kubernetes.get_json("pod", runtime_name)
        if pod is None:
            raise SmokeFailure(f"pod/{runtime_name} is missing")
        spec = pod.get("spec", {})
        if spec.get("runtimeClassName") != self.config.runtime_class:
            raise SmokeFailure(f"pod/{runtime_name} does not use {self.config.runtime_class}")
        node_name = spec.get("nodeName")
        if not node_name:
            raise SmokeFailure(f"pod/{runtime_name} is not scheduled")
        node = self.kubernetes.node_json(node_name)
        labels = node.get("metadata", {}).get("labels", {})
        actual = labels.get(self.config.node_label_key)
        if actual != self.config.node_label_value:
            raise SmokeFailure(
                f"node/{node_name} label {self.config.node_label_key} is {actual!r}"
            )
        self.report(f"Kata pod is Ready on sandbox node {node_name}")
        return runtime_name

    def validate_commands(self, sandbox_id: str) -> None:
        started = time.monotonic()
        result = self.client.execute(
            sandbox_id,
            "python3 -c \"import sys; print('xolis-stdout'); "
            "print('xolis-stderr', file=sys.stderr); sys.exit(7)\"",
            10,
        )
        if result != {
            "stdout": "xolis-stdout\n",
            "stderr": "xolis-stderr\n",
            "exit_code": 7,
        }:
            raise SmokeFailure(f"unexpected command result: {result}")
        self.benchmark.record("first_command_seconds", started)

        timed_out = self.client.execute(
            sandbox_id,
            "python3 -c \"import time; time.sleep(5)\"",
            1,
        )
        if timed_out.get("exit_code") != 124 or "timed out" not in timed_out.get(
            "stderr", ""
        ):
            raise SmokeFailure(f"command timeout was not enforced: {timed_out}")
        self.report("stdout, stderr, exit status, and command timeout are enforced")

    def validate_files(self, sandbox_id: str) -> None:
        contents = b"hello from the Xolis smoke test\n"
        prefix = f"/v1/sandboxes/{sandbox_id}/files"
        self.client.request(
            "PUT", f"{prefix}/smoke/hello.txt", body=contents, expected=(204,)
        )
        _, list_body = self.client.request("GET", f"{prefix}/smoke?list=true")
        entries = json.loads(list_body)
        if not any(
            entry.get("name") == "hello.txt" and entry.get("size") == len(contents)
            for entry in entries
        ):
            raise SmokeFailure(f"uploaded file is absent from directory listing: {entries}")
        _, downloaded = self.client.request("GET", f"{prefix}/smoke/hello.txt")
        if downloaded != contents:
            raise SmokeFailure("downloaded file contents differ from the upload")

        self.client.request(
            "PUT",
            f"{prefix}/%2e%2e%2Fescape.txt",
            body=b"escape",
            expected=(400,),
        )
        self.client.request(
            "PUT",
            f"{prefix}/smoke/oversized.bin",
            body=b"0" * (self.config.maximum_upload_bytes + 1),
            expected=(413,),
        )
        self.report("file transfer, traversal rejection, and upload limits work")

    def validate_egress(self, sandbox_id: str) -> None:
        result = self.client.execute(
            sandbox_id,
            "python3 -c \"import socket,sys; s=socket.socket(); s.settimeout(2); "
            "result=s.connect_ex(('1.1.1.1',443)); print(result); "
            "sys.exit(0 if result else 42)\"",
            5,
        )
        if result.get("exit_code") != 0:
            raise SmokeFailure(f"unapproved public egress was reachable: {result}")
        self.report("unapproved public egress is denied")

    def validate_tenant_and_idempotency(
        self, sandbox_id: str, idempotency_key: str
    ) -> None:
        repeated = self.client.create(self.config.profile, 300, idempotency_key)
        if repeated.get("id") != sandbox_id:
            raise SmokeFailure("idempotent create returned a different sandbox")
        other = XolisClient(
            self.client.base_url,
            f"{self.config.tenant}-other",
            reconnect=self.client.reconnect,
        )
        other.request("GET", f"/v1/sandboxes/{sandbox_id}", expected=(404,))
        self.report("create is idempotent and tenant isolation hides the sandbox")

    def delete_and_verify(self, sandbox_id: str, runtime_name: str) -> None:
        started = time.monotonic()
        self.client.delete(sandbox_id)
        self.wait_until_missing("sandboxclaim", sandbox_id, self.config.cleanup_timeout_seconds)
        self.wait_until_missing("sandbox", runtime_name, self.config.cleanup_timeout_seconds)
        self.wait_until_missing("pod", runtime_name, self.config.cleanup_timeout_seconds)
        self.claims.discard(sandbox_id)
        self.benchmark.record("explicit_cleanup_seconds", started)
        self.report("explicit deletion removes the claim, Sandbox, and Pod")

    def validate_ttl(self) -> None:
        started = time.monotonic()
        key = f"ttl-{uuid.uuid4().hex}"
        created = self.client.create(self.config.profile, self.config.ttl_test_seconds, key)
        sandbox_id = created["id"]
        self.claims.add(sandbox_id)
        self.wait_for_state(sandbox_id, {"Running"}, self.config.ready_timeout_seconds)
        runtime_name, _ = self.claim_runtime(sandbox_id)

        deadline = time.monotonic() + self.config.ttl_timeout_seconds
        last_state = "Running"
        while time.monotonic() < deadline:
            try:
                last_state = self.client.get(sandbox_id).get("state", "Unknown")
            except ApiError as error:
                if error.status == 404:
                    last_state = "Deleted"
                    break
                raise
            if last_state == "Expired":
                break
            time.sleep(2)
        else:
            raise SmokeFailure(f"sandbox TTL did not expire; last state was {last_state}")

        self.wait_until_missing("sandbox", runtime_name, self.config.cleanup_timeout_seconds)
        self.wait_until_missing("pod", runtime_name, self.config.cleanup_timeout_seconds)
        self.benchmark.record("ttl_lifecycle_seconds", started)
        self.report("absolute TTL removes the abandoned Sandbox and Pod")

    def cleanup(self) -> None:
        for claim_name in list(self.claims):
            try:
                self.client.delete(claim_name)
            except Exception as error:  # best-effort fallback during failure cleanup
                print(f"warning: API cleanup failed for {claim_name}: {error}", file=sys.stderr)
            self.kubernetes.delete_claim(claim_name)
            self.claims.discard(claim_name)

    def run(self) -> None:
        if self.config.ttl_only:
            try:
                self.validate_ttl()
            finally:
                self.cleanup()
            return

        key = f"smoke-{uuid.uuid4().hex}"
        ready_started = time.monotonic()
        created = self.client.create(self.config.profile, 300, key)
        sandbox_id = created["id"]
        self.claims.add(sandbox_id)
        try:
            self.validate_tenant_and_idempotency(sandbox_id, key)
            self.wait_for_state(sandbox_id, {"Running"}, self.config.ready_timeout_seconds)
            self.benchmark.record("sandbox_ready_seconds", ready_started)
            runtime_name = self.validate_placement(sandbox_id)
            self.validate_commands(sandbox_id)
            self.validate_files(sandbox_id)
            self.validate_egress(sandbox_id)
            self.delete_and_verify(sandbox_id, runtime_name)
            if not self.config.skip_ttl:
                self.validate_ttl()
        finally:
            self.cleanup()


def parse_label(value: str) -> tuple[str, str]:
    key, separator, label_value = value.partition("=")
    if not separator or not key or not label_value:
        raise argparse.ArgumentTypeError("node label must use KEY=VALUE")
    return key, label_value


def wait_for_health(client: XolisClient, port_forward: PortForward | None) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        process = port_forward.process if port_forward is not None else None
        if process is not None and process.poll() is not None:
            port_forward.restart()
        try:
            client.request("GET", "/healthz")
            return
        except (OSError, urllib.error.URLError, ApiError):
            time.sleep(0.5)
    raise SmokeFailure("Xolis API did not become reachable")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Use an existing API endpoint instead of port-forward")
    parser.add_argument("--api-namespace", default="xolis-system")
    parser.add_argument("--api-service", default="xolis-api")
    parser.add_argument("--local-port", type=int, default=18080)
    parser.add_argument("--sandbox-namespace", default="xolis-sandboxes")
    parser.add_argument("--profile", default="python-basic-v1")
    parser.add_argument("--tenant", default="xolis-smoke")
    parser.add_argument("--runtime-class", default="xolis-kata")
    parser.add_argument(
        "--sandbox-node-label", type=parse_label, default=("xolis.io/kata-ready", "true")
    )
    parser.add_argument("--ready-timeout-seconds", type=int, default=300)
    parser.add_argument("--cleanup-timeout-seconds", type=int, default=120)
    parser.add_argument("--maximum-upload-bytes", type=int, default=10 * 1024 * 1024)
    parser.add_argument("--ttl-test-seconds", type=int, default=60)
    parser.add_argument("--ttl-timeout-seconds", type=int, default=180)
    parser.add_argument("--skip-ttl", action="store_true")
    parser.add_argument("--ttl-only", action="store_true")
    parser.add_argument("--report", type=Path, help="Write structured timing results as JSON")
    return parser


def execute(
    args: argparse.Namespace,
    base_url: str,
    port_forward: PortForward | None,
    benchmark: BenchmarkRecorder,
) -> None:
    client = XolisClient(
        base_url,
        args.tenant,
        reconnect=port_forward.ensure_running if port_forward is not None else None,
    )
    wait_for_health(client, port_forward)
    key, value = args.sandbox_node_label
    config = SmokeConfig(
        profile=args.profile,
        tenant=args.tenant,
        namespace=args.sandbox_namespace,
        runtime_class=args.runtime_class,
        node_label_key=key,
        node_label_value=value,
        ready_timeout_seconds=args.ready_timeout_seconds,
        cleanup_timeout_seconds=args.cleanup_timeout_seconds,
        maximum_upload_bytes=args.maximum_upload_bytes,
        ttl_test_seconds=args.ttl_test_seconds,
        ttl_timeout_seconds=args.ttl_timeout_seconds,
        skip_ttl=args.skip_ttl,
        ttl_only=args.ttl_only,
    )
    ServiceSmokeTest(
        client, Kubernetes(args.sandbox_namespace), config, benchmark
    ).run()
    print("Xolis service smoke test passed", flush=True)


def main() -> int:
    args = build_parser().parse_args()
    benchmark = BenchmarkRecorder()
    exit_code = 0
    try:
        if args.skip_ttl and args.ttl_only:
            raise SmokeFailure("--skip-ttl and --ttl-only cannot be used together")
        if args.base_url:
            execute(args, args.base_url, None, benchmark)
        else:
            with PortForward(args.api_namespace, args.api_service, args.local_port) as forward:
                execute(args, forward.base_url, forward, benchmark)
    except (ApiError, SmokeFailure, OSError, ValueError) as error:
        benchmark.error = str(error)
        print(f"smoke test failed: {error}", file=sys.stderr)
        exit_code = 1
    else:
        benchmark.status = "passed"
    finally:
        if args.report:
            benchmark.write(args.report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
