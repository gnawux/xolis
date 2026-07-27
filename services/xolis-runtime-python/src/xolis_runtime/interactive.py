"""Bounded WebSocket/PTTY sessions for interactive agent workloads."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import os
import pty
import signal
import struct
import termios
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from .core import InvalidCommand, RuntimeSettings, parse_command


def _resize_terminal(file_descriptor: int, rows: int, columns: int) -> None:
    if not 1 <= rows <= 500 or not 1 <= columns <= 500:
        raise InvalidCommand("terminal rows and columns must be between 1 and 500")
    fcntl.ioctl(
        file_descriptor,
        termios.TIOCSWINSZ,
        struct.pack("HHHH", rows, columns, 0, 0),
    )


async def run_interactive_session(
    websocket: WebSocket, settings: RuntimeSettings
) -> None:
    """Run one TTL-bound PTY session and clean it up on every exit path."""
    process: asyncio.subprocess.Process | None = None
    master_fd: int | None = None
    tasks: list[asyncio.Task] = []
    try:
        try:
            start = await asyncio.wait_for(websocket.receive_json(), timeout=10)
            if start.get("type") != "start":
                raise InvalidCommand("the first message must have type 'start'")
            ttl_seconds = int(start.get("ttl_seconds", 300))
            arguments = parse_command(settings, str(start.get("command", "")), ttl_seconds)
            rows = int(start.get("rows", 24))
            columns = int(start.get("columns", 80))
        except (TypeError, ValueError) as error:
            raise InvalidCommand("session fields have invalid types") from error

        settings.workspace.mkdir(parents=True, exist_ok=True)
        master_fd, slave_fd = pty.openpty()
        _resize_terminal(slave_fd, rows, columns)
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                cwd=settings.workspace,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
            )
        finally:
            os.close(slave_fd)
        await websocket.send_json({"type": "start", "pid": process.pid})

        async def send_output() -> str:
            emitted = 0
            while True:
                try:
                    chunk = await asyncio.to_thread(os.read, master_fd, 16 * 1024)
                except OSError:
                    return "eof"
                if not chunk:
                    return "eof"
                allowed = settings.maximum_output_bytes - emitted
                if allowed <= 0:
                    await websocket.send_json(
                        {"type": "error", "message": "session output limit exceeded"}
                    )
                    return "output_limit"
                chunk = chunk[:allowed]
                emitted += len(chunk)
                await websocket.send_json(
                    {
                        "type": "output",
                        "data": base64.b64encode(chunk).decode("ascii"),
                    }
                )

        async def receive_input() -> str:
            while True:
                message = await websocket.receive_json()
                message_type = message.get("type")
                if message_type == "input":
                    data = base64.b64decode(message.get("data", ""), validate=True)
                    await asyncio.to_thread(os.write, master_fd, data)
                elif message_type == "resize":
                    _resize_terminal(
                        master_fd, int(message["rows"]), int(message["columns"])
                    )
                elif message_type in {"cancel", "close"}:
                    return message_type
                else:
                    await websocket.send_json(
                        {"type": "error", "message": "unsupported session message"}
                    )

        output_task = asyncio.create_task(send_output())
        input_task = asyncio.create_task(receive_input())
        wait_task = asyncio.create_task(process.wait())
        tasks = [output_task, input_task, wait_task]
        done, _ = await asyncio.wait(
            tasks, timeout=ttl_seconds, return_when=asyncio.FIRST_COMPLETED
        )

        timed_out = not done
        input_result = input_task.result() if input_task in done else None
        output_result = output_task.result() if output_task in done else None
        if wait_task not in done:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

        if not output_task.done():
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(output_task, timeout=1)
        exit_code = process.returncode or 0
        if timed_out:
            exit_code = 124
            await websocket.send_json({"type": "timeout", "ttl_seconds": ttl_seconds})
        elif input_result in {"cancel", "close"}:
            exit_code = 130
        elif output_result == "output_limit":
            exit_code = 125
        await websocket.send_json({"type": "exit", "exit_code": exit_code})
    except InvalidCommand as error:
        await websocket.send_json({"type": "error", "message": str(error)})
    except (WebSocketDisconnect, asyncio.CancelledError):
        raise
    except Exception as error:
        await websocket.send_json({"type": "error", "message": str(error)})
    finally:
        if process is not None and process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if master_fd is not None:
            with suppress(OSError):
                os.close(master_fd)
