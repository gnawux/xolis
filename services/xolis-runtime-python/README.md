# Xolis Python Runtime

This service implements the Agent Sandbox runtime HTTP protocol for the
`python-basic-v1` profile. It runs commands without a shell, confines file APIs
to `/workspace`, limits command duration and captured output, bounds uploads,
and terminates the command process group when a timeout occurs.

The supported endpoints are:

- `GET /` and `GET /healthz` for health checks;
- `POST /execute` for bounded, non-interactive commands;
- `POST /upload` for multipart file uploads;
- `GET /download/{path}` for file downloads;
- `GET /list/{path}` for directory listings;
- `GET /exists/{path}` for existence checks.

Configuration is provided through `XOLIS_WORKSPACE`,
`XOLIS_MAXIMUM_COMMAND_TIMEOUT_SECONDS`, `XOLIS_MAXIMUM_OUTPUT_BYTES`, and
`XOLIS_MAXIMUM_UPLOAD_BYTES`. Defaults are `/workspace`, 300 seconds, 1 MiB per
output stream, and 10 MiB per upload.

Run locally with a writable workspace:

    XOLIS_WORKSPACE=/tmp/xolis-workspace uvicorn xolis_runtime.app:app \
        --app-dir src --host 127.0.0.1 --port 8888

Run the tests from this directory:

    python -m unittest discover -s tests -v
