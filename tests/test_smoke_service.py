import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "deploy" / "tests" / "smoke_service.py"
SPEC = importlib.util.spec_from_file_location("smoke_service", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RecordingHandler(BaseHTTPRequestHandler):
    requests = []
    response_status = 200
    response_body = b"{}"

    def handle_request(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        self.__class__.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": self.rfile.read(length),
            }
        )
        self.send_response(self.__class__.response_status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(self.__class__.response_body)

    do_GET = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request

    def log_message(self, *_: object) -> None:
        pass


class XolisClientTests(unittest.TestCase):
    def setUp(self) -> None:
        RecordingHandler.requests = []
        RecordingHandler.response_status = 200
        RecordingHandler.response_body = b"{}"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.client = MODULE.XolisClient(f"http://{host}:{port}", "tenant-a")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_create_sends_tenant_idempotency_and_camel_case_request(self) -> None:
        RecordingHandler.response_status = 202
        RecordingHandler.response_body = b'{"id":"sandbox-1"}'

        response = self.client.create("python-basic-v1", 300, "request-1")

        self.assertEqual(response["id"], "sandbox-1")
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/v1/sandboxes")
        self.assertEqual(request["headers"]["X-Xolis-Tenant"], "tenant-a")
        self.assertEqual(request["headers"]["Idempotency-Key"], "request-1")
        self.assertEqual(
            json.loads(request["body"]),
            {
                "profile": "python-basic-v1",
                "ttlSeconds": 300,
                "metadata": {"smokeRun": "request-1"},
            },
        )

    def test_expected_policy_rejection_is_returned_to_the_test(self) -> None:
        RecordingHandler.response_status = 413
        RecordingHandler.response_body = b'{"code":"payload_too_large"}'

        status, body = self.client.request(
            "PUT", "/v1/sandboxes/id/files/large", body=b"large", expected=(413,)
        )

        self.assertEqual(status, 413)
        self.assertIn(b"payload_too_large", body)

    def test_unexpected_http_status_raises_api_error(self) -> None:
        RecordingHandler.response_status = 502
        RecordingHandler.response_body = b"router unavailable"

        with self.assertRaises(MODULE.ApiError) as context:
            self.client.request("GET", "/v1/sandboxes/id")

        self.assertEqual(context.exception.status, 502)

    def test_encoded_traversal_is_not_normalized_by_the_client(self) -> None:
        RecordingHandler.response_status = 400

        self.client.request(
            "PUT",
            "/v1/sandboxes/id/files/%2e%2e%2Fescape.txt",
            body=b"escape",
            expected=(400,),
        )

        self.assertEqual(
            RecordingHandler.requests[0]["path"],
            "/v1/sandboxes/id/files/%2e%2e%2Fescape.txt",
        )

    def test_transport_failure_reconnects_and_retries(self) -> None:
        reconnects = []
        client = MODULE.XolisClient(
            self.client.base_url,
            "tenant-a",
            reconnect=lambda: reconnects.append(True),
        )
        real_opener = client.opener

        class FlakyOpener:
            attempts = 0

            def open(self, *args: object, **kwargs: object) -> object:
                self.attempts += 1
                if self.attempts == 1:
                    raise ConnectionResetError("temporary port-forward failure")
                return real_opener.open(*args, **kwargs)

        client.opener = FlakyOpener()

        status, _ = client.request("GET", "/healthz")

        self.assertEqual(status, 200)
        self.assertEqual(reconnects, [True])


class ArgumentTests(unittest.TestCase):
    def test_node_label_requires_key_and_value(self) -> None:
        self.assertEqual(MODULE.parse_label("xolis.io/kata-ready=true"), ("xolis.io/kata-ready", "true"))
        with self.assertRaises(Exception):
            MODULE.parse_label("xolis.io/kata-ready")

    def test_stale_live_port_forward_is_restarted_after_transport_failure(self) -> None:
        forward = MODULE.PortForward("xolis-system", "xolis-api", 18080)

        class HungProcess:
            def poll(self) -> None:
                return None

        forward.process = HungProcess()
        forward.started_at = time.monotonic() - 3
        restarted = []
        forward.restart = lambda: restarted.append(True)

        forward.ensure_running()

        self.assertEqual(restarted, [True])

    def test_benchmark_report_is_structured_json(self) -> None:
        recorder = MODULE.BenchmarkRecorder()
        recorder.record("sandbox_ready_seconds", recorder.started_at)
        recorder.status = "passed"
        with tempfile.TemporaryDirectory() as temporary_directory:
            report_path = Path(temporary_directory) / "metrics.json"
            recorder.write(report_path)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["workflow"], "service_smoke")
        self.assertEqual(report["status"], "passed")
        self.assertIn("sandbox_ready_seconds", report["metrics"])


if __name__ == "__main__":
    unittest.main()
