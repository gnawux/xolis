from __future__ import annotations

import asyncio
import io
import shlex
import sys
import tempfile
import time
import unittest
from pathlib import Path

from xolis_runtime.core import (
    InvalidCommand,
    PathViolation,
    RuntimeSettings,
    UploadTooLarge,
    execute_command,
    list_directory,
    resolve_workspace_path,
    write_upload,
)


class RuntimeCoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name) / "workspace"
        self.workspace.mkdir()
        self.settings = RuntimeSettings(
            workspace=self.workspace,
            maximum_command_timeout_seconds=2,
            maximum_output_bytes=16,
            maximum_upload_bytes=5,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def test_execute_returns_status_and_caps_each_output_stream(self) -> None:
        script = "import sys; print('x' * 100); print('y' * 100, file=sys.stderr)"
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
        result = await execute_command(self.settings, command, 1)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(result.stdout.encode()), 16)
        self.assertEqual(len(result.stderr.encode()), 16)

    async def test_timeout_kills_the_child_process_group(self) -> None:
        marker = self.workspace / "child-finished"
        child_script = (
            "import pathlib,time;time.sleep(1.5);"
            f"pathlib.Path({str(marker)!r}).write_text('not killed')"
        )
        parent_script = (
            "import subprocess,sys,time;"
            f"subprocess.Popen([sys.executable,'-c',{child_script!r}]);"
            "time.sleep(5)"
        )
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(parent_script)}"

        started = time.monotonic()
        result = await execute_command(self.settings, command, 1)
        self.assertEqual(result.exit_code, 124)
        self.assertLess(time.monotonic() - started, 2)
        await asyncio.sleep(0.7)
        self.assertFalse(marker.exists())

    async def test_invalid_timeout_is_rejected(self) -> None:
        with self.assertRaises(InvalidCommand):
            await execute_command(self.settings, "python -V", 3)

    def test_paths_are_confined_even_through_symlinks(self) -> None:
        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        (self.workspace / "escape").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(PathViolation):
            resolve_workspace_path(self.workspace, "../outside/secret")
        with self.assertRaises(PathViolation):
            resolve_workspace_path(self.workspace, "escape/secret")
        with self.assertRaises(PathViolation):
            resolve_workspace_path(self.workspace, "/etc/passwd")

    def test_upload_is_atomic_bounded_and_listed(self) -> None:
        destination = write_upload(self.settings, "data/hello.txt", io.BytesIO(b"hello"))
        self.assertEqual(destination.read_bytes(), b"hello")
        self.assertEqual(list_directory(self.settings, "data")[0]["name"], "hello.txt")

        with self.assertRaises(UploadTooLarge):
            write_upload(self.settings, "data/large.txt", io.BytesIO(b"larger"))
        self.assertFalse((self.workspace / "data/large.txt").exists())


if __name__ == "__main__":
    unittest.main()
