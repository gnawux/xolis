"""FastAPI adapter for the Agent Sandbox runtime protocol."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .core import (
    InvalidCommand,
    PathViolation,
    RuntimeSettings,
    UploadTooLarge,
    execute_command,
    list_directory,
    resolve_workspace_path,
    write_upload,
)


class ExecuteRequest(BaseModel):
    command: str
    timeout_seconds: int = 30


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


def create_app(settings: RuntimeSettings | None = None) -> FastAPI:
    runtime_settings = settings or RuntimeSettings.from_environment()
    application = FastAPI(title="Xolis Python Runtime", version="0.1.0")

    @application.get("/")
    @application.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/execute", response_model=ExecuteResponse)
    async def execute(request: ExecuteRequest) -> ExecuteResponse:
        try:
            result = await execute_command(
                runtime_settings, request.command, request.timeout_seconds
            )
        except InvalidCommand as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return ExecuteResponse(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
        )

    @application.post("/upload")
    async def upload(file: UploadFile = File(...)) -> dict[str, str]:
        if not file.filename:
            raise HTTPException(status_code=400, detail="upload filename is required")
        try:
            await run_in_threadpool(
                write_upload, runtime_settings, file.filename, file.file
            )
        except PathViolation as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except UploadTooLarge as error:
            raise HTTPException(status_code=413, detail=str(error)) from error
        return {"message": f"File '{file.filename}' uploaded successfully."}

    def requested_file(path: str) -> Path:
        try:
            return resolve_workspace_path(runtime_settings.workspace, path)
        except PathViolation as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

    @application.get("/download/{path:path}")
    async def download(path: str) -> FileResponse:
        file_path = requested_file(path)
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(file_path, media_type="application/octet-stream")

    @application.get("/list/{path:path}")
    async def list_files(path: str) -> list[dict]:
        try:
            return list_directory(runtime_settings, path)
        except PathViolation as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @application.get("/exists/{path:path}")
    async def exists(path: str) -> dict[str, str | bool]:
        return {"path": path, "exists": requested_file(path).exists()}

    return application


app = create_app()
