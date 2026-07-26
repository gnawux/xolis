# Xolis API

`xolis-api` is the Rust control-plane service for Xolis sandboxes. It exposes
the public lifecycle API and delegates runtime lifecycle to Kubernetes Agent
Sandbox.

The current first-stage implementation provides the HTTP contract, validation,
tenant scoping, idempotent creation, and an in-memory store used for local
tests. The Kubernetes `SandboxClaim` store and runtime router client are added
in subsequent stages.

Run the local service:

    cargo run -p xolis-api

Run its tests and static checks:

    cargo test -p xolis-api
    cargo clippy -p xolis-api --all-targets -- -D warnings

The local API listens on `127.0.0.1:8080` by default. Lifecycle requests must
include `X-Xolis-Tenant`; create requests may also include `Idempotency-Key`.
