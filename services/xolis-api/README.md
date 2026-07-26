# Xolis API

`xolis-api` is the Rust control-plane service for Xolis sandboxes. It exposes
the public lifecycle API and delegates runtime lifecycle to Kubernetes Agent
Sandbox.

The service provides the HTTP contract, validation, tenant scoping, idempotent
creation, a Kubernetes `SandboxClaim` store, and a client for the Agent Sandbox
router. An in-memory store remains available for local API tests.

Run the local service:

    cargo run -p xolis-api

Run its tests and static checks:

    cargo test -p xolis-api
    cargo clippy -p xolis-api --all-targets -- -D warnings

The local API listens on `127.0.0.1:8080` by default. Lifecycle requests must
include `X-Xolis-Tenant`; create requests may also include `Idempotency-Key`.
Command and file requests are accepted only after the claim is running. Use
`?list=true` on a file `GET` request to list a directory instead of downloading
a file.

Set `XOLIS_STORE=kubernetes` to use in-cluster or kubeconfig credentials. The
Kubernetes backend defaults to namespace `xolis-sandboxes`, warm pool
`python-basic-v1-pool`, and profile `python-basic-v1`.

Runtime traffic defaults to `http://sandbox-router:8080`, sandbox namespace
`xolis-sandboxes`, and runtime port `8888`. Override these with
`XOLIS_ROUTER_URL`, `XOLIS_SANDBOX_NAMESPACE`, and `XOLIS_RUNTIME_PORT`.
`XOLIS_MAXIMUM_COMMAND_TIMEOUT_SECONDS` and `XOLIS_MAXIMUM_UPLOAD_BYTES`
configure the public request limits; their defaults are 300 seconds and 10 MiB.
