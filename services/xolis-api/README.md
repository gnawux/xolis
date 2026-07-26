# Xolis API

`xolis-api` is the Rust control-plane service for Xolis sandboxes. It exposes
the public lifecycle API and delegates runtime lifecycle to Kubernetes Agent
Sandbox.

The service provides the HTTP contract, validation, tenant scoping, idempotent
creation, and a Kubernetes `SandboxClaim` store. An in-memory store remains
available for local API tests. The runtime router client is added in a
subsequent stage.

Run the local service:

    cargo run -p xolis-api

Run its tests and static checks:

    cargo test -p xolis-api
    cargo clippy -p xolis-api --all-targets -- -D warnings

The local API listens on `127.0.0.1:8080` by default. Lifecycle requests must
include `X-Xolis-Tenant`; create requests may also include `Idempotency-Key`.

Set `XOLIS_STORE=kubernetes` to use in-cluster or kubeconfig credentials. The
Kubernetes backend defaults to namespace `xolis-sandboxes`, warm pool
`python-basic-v1-pool`, and profile `python-basic-v1`.
