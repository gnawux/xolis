from __future__ import annotations

import tempfile
import unittest
import shlex
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from xolis_runtime.app import create_app
from xolis_runtime.core import RuntimeSettings


class RuntimeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name) / "workspace"
        self.workspace.mkdir()
        settings = RuntimeSettings(
            workspace=self.workspace,
            maximum_command_timeout_seconds=2,
            maximum_output_bytes=1024,
            maximum_upload_bytes=5,
        )
        self.client = TestClient(create_app(settings))

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_health_execute_and_file_protocol(self) -> None:
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        command = f"{shlex.quote(sys.executable)} -c 'print(42)'"
        executed = self.client.post(
            "/execute", json={"command": command, "timeout_seconds": 1}
        )
        self.assertEqual(executed.status_code, 200)
        self.assertEqual(executed.json()["stdout"], "42\n")

        uploaded = self.client.post(
            "/upload", files={"file": ("data/hello.txt", b"hello")}
        )
        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(self.client.get("/download/data%2Fhello.txt").content, b"hello")
        listing = self.client.get("/list/data")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["name"], "hello.txt")
        self.assertTrue(self.client.get("/exists/data%2Fhello.txt").json()["exists"])

    def test_upload_limit_and_traversal_are_rejected(self) -> None:
        oversized = self.client.post(
            "/upload", files={"file": ("large.txt", b"larger")}
        )
        self.assertEqual(oversized.status_code, 413)
        traversal = self.client.get("/download/%2E%2E%2Fsecret")
        self.assertEqual(traversal.status_code, 403)


if __name__ == "__main__":
    unittest.main()
