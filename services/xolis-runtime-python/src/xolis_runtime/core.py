"""Bounded command and workspace operations for the Python runtime."""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class RuntimeOperationError(Exception):
    """Base class for errors that can be returned to runtime clients."""


class InvalidCommand(RuntimeOperationError):
    """The command cannot be executed under the configured policy."""


class PathViolation(RuntimeOperationError):
    """A requested path is outside the workspace."""


class UploadTooLarge(RuntimeOperationError):
    """An upload exceeds the configured size limit."""


@dataclass(frozen=True)
class RuntimeSettings:
    workspace: Path
    maximum_command_timeout_seconds: int = 300
    maximum_output_bytes: int = 1024 * 1024
    maximum_upload_bytes: int = 10 * 1024 * 1024

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        return cls(
            workspace=Path(os.environ.get("XOLIS_WORKSPACE", "/workspace")),
            maximum_command_timeout_seconds=int(
                os.environ.get("XOLIS_MAXIMUM_COMMAND_TIMEOUT_SECONDS", "300")
            ),
            maximum_output_bytes=int(
                os.environ.get("XOLIS_MAXIMUM_OUTPUT_BYTES", str(1024 * 1024))
            ),
            maximum_upload_bytes=int(
                os.environ.get("XOLIS_MAXIMUM_UPLOAD_BYTES", str(10 * 1024 * 1024))
            ),
        )


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int


@dataclass(frozen=True)
class CommandEvent:
    event: str
    data: dict[str, str | int]


def _parse_command(
    settings: RuntimeSettings, command: str, timeout_seconds: int
) -> list[str]:
    if not command.strip():
        raise InvalidCommand("command cannot be empty")
    if not 1 <= timeout_seconds <= settings.maximum_command_timeout_seconds:
        raise InvalidCommand(
            "timeout_seconds must be between 1 and "
            f"{settings.maximum_command_timeout_seconds}"
        )
    try:
        arguments = shlex.split(command)
    except ValueError as error:
        raise InvalidCommand(f"command cannot be parsed: {error}") from error
    if not arguments:
        raise InvalidCommand("command cannot be empty")
    return arguments


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def resolve_workspace_path(workspace: Path, requested_path: str) -> Path:
    """Resolve a relative path and reject traversal outside the workspace."""
    if not requested_path or "\x00" in requested_path:
        raise PathViolation("path must be a non-empty workspace-relative path")
    relative_path = Path(requested_path)
    if relative_path.is_absolute():
        raise PathViolation("absolute paths are not allowed")

    workspace = workspace.resolve()
    resolved = (workspace / relative_path).resolve(strict=False)
    if not resolved.is_relative_to(workspace) or resolved == workspace:
        raise PathViolation("path must stay within the workspace")
    return resolved


async def _read_limited(
    stream: asyncio.StreamReader | None, maximum_bytes: int
) -> bytes:
    if stream is None:
        return b""
    captured = bytearray()
    while chunk := await stream.read(64 * 1024):
        remaining = maximum_bytes - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
    return bytes(captured)


async def execute_command(
    settings: RuntimeSettings, command: str, timeout_seconds: int
) -> CommandResult:
    """Execute one command, drain bounded output, and kill its group on timeout."""
    arguments = _parse_command(settings, command, timeout_seconds)

    settings.workspace.mkdir(parents=True, exist_ok=True)
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=settings.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return CommandResult("", f"Failed to execute command: {error}", 127)

    stdout_task = asyncio.create_task(
        _read_limited(process.stdout, settings.maximum_output_bytes)
    )
    stderr_task = asyncio.create_task(
        _read_limited(process.stderr, settings.maximum_output_bytes)
    )
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except TimeoutError:
        timed_out = True
        _kill_process_group(process)
        await process.wait()

    stdout_bytes, stderr_bytes = await asyncio.gather(stdout_task, stderr_task)
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if timed_out:
        timeout_message = f"command timed out after {timeout_seconds} seconds"
        stderr = f"{stderr}\n{timeout_message}" if stderr else timeout_message
        return CommandResult(stdout, stderr, 124)
    return CommandResult(stdout, stderr, process.returncode or 0)


async def stream_command(
    settings: RuntimeSettings, command: str, timeout_seconds: int
) -> AsyncIterator[CommandEvent]:
    """Stream bounded command output and always clean up the process group."""
    arguments = _parse_command(settings, command, timeout_seconds)
    settings.workspace.mkdir(parents=True, exist_ok=True)
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=settings.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        yield CommandEvent("error", {"message": f"Failed to execute command: {error}"})
        return

    queue: asyncio.Queue[tuple[str, bytes] | None] = asyncio.Queue()

    async def read_stream(name: str, stream: asyncio.StreamReader | None) -> None:
        if stream is not None:
            while chunk := await stream.read(64 * 1024):
                await queue.put((name, chunk))
        await queue.put(None)

    readers = [
        asyncio.create_task(read_stream("stdout", process.stdout)),
        asyncio.create_task(read_stream("stderr", process.stderr)),
    ]
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    emitted = {"stdout": 0, "stderr": 0}
    completed_readers = 0
    timed_out = False
    try:
        yield CommandEvent("start", {"pid": process.pid})
        while completed_readers < len(readers):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                timed_out = True
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                timed_out = True
                break
            if item is None:
                completed_readers += 1
                continue
            name, chunk = item
            allowed = settings.maximum_output_bytes - emitted[name]
            if allowed <= 0:
                continue
            chunk = chunk[:allowed]
            emitted[name] += len(chunk)
            yield CommandEvent(name, {"data": chunk.decode("utf-8", errors="replace")})

        if timed_out:
            _kill_process_group(process)
            await process.wait()
            yield CommandEvent(
                "timeout", {"message": f"command timed out after {timeout_seconds} seconds"}
            )
        else:
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            try:
                await asyncio.wait_for(process.wait(), timeout=remaining)
            except TimeoutError:
                timed_out = True
                _kill_process_group(process)
                await process.wait()
                yield CommandEvent(
                    "timeout",
                    {"message": f"command timed out after {timeout_seconds} seconds"},
                )
        yield CommandEvent("exit", {"exit_code": 124 if timed_out else process.returncode or 0})
    finally:
        if process.returncode is None:
            _kill_process_group(process)
            await process.wait()
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


def write_upload(
    settings: RuntimeSettings, requested_path: str, source: BinaryIO
) -> Path:
    """Write an upload atomically while enforcing the configured byte limit."""
    destination = resolve_workspace_path(settings.workspace, requested_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    total = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            while chunk := source.read(64 * 1024):
                total += len(chunk)
                if total > settings.maximum_upload_bytes:
                    raise UploadTooLarge(
                        f"upload cannot exceed {settings.maximum_upload_bytes} bytes"
                    )
                temporary.write(chunk)
        os.replace(temporary_name, destination)
        temporary_name = None
        return destination
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def list_directory(settings: RuntimeSettings, requested_path: str) -> list[dict]:
    directory = resolve_workspace_path(settings.workspace, requested_path)
    if not directory.is_dir():
        raise FileNotFoundError("path is not a directory")
    entries = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        stat = entry.stat(follow_symlinks=False)
        if entry.is_symlink():
            entry_type = "other"
        elif entry.is_dir():
            entry_type = "directory"
        else:
            entry_type = "file"
        entries.append(
            {
                "name": entry.name,
                "size": stat.st_size,
                "type": entry_type,
                "mod_time": stat.st_mtime,
            }
        )
    return entries
