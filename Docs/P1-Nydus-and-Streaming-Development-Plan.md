# P1 Nydus and Streaming Development Plan

This plan prioritizes two demonstrable product capabilities before large-scale
performance and reliability testing:

1. an optional Nydus image path that preserves ordinary OCI as the default and
   fallback; and
2. streaming and interactive command execution suitable for agent demos,
   including a bounded Hermes Agent evaluation.

The statistically useful cold/warm baseline remains necessary, but it should be
run after the lab has enough nodes and capacity to support representative
concurrency, density, and failure testing.

## Implementation Status

All three delivery steps were implemented and received bounded AWS lab
validation on 2026-07-28. Ordinary OCI remains the default. The optional Nydus
path passed the complete service smoke test with AMI `ami-0ec0906871c3a9d9b`,
and the Hermes profile passed CLI, SSE, WebSocket/PTTY, file, network-policy,
and cleanup checks without model credentials. Repeated performance,
concurrency, soak, and credentialed inference tests remain intentionally out of
scope for this milestone.

## Delivery Sequence

### 1. Optional Nydus Runtime Path

Deliver a second, explicitly selected runtime path rather than changing the
validated OCI baseline.

- Keep `python-basic-v1` and `xolis-kata` on ordinary OCI.
- Add a separate Nydus-enabled containerd/Kata handler and RuntimeClass.
- Add a separate SandboxTemplate and warm pool for Nydus evaluation.
- Make Nydus deployment opt-in through the lab configuration and fail clearly
  when the selected AMI does not contain the snapshotter.
- Add static validation and dry-run coverage for both OCI and Nydus paths.
- Add a bounded comparison workflow that records image mode, cache state,
  image digest, Ready latency, command latency, and cleanup result.

Acceptance criteria:

- the existing OCI manifests and validation remain unchanged by default;
- Nydus can be selected without editing the OCI profile;
- selecting Nydus on an incompatible node fails before a sandbox claim is run;
- both profiles can be reconciled and tested independently; and
- evidence clearly labels results as OCI or Nydus.

### 2. Streaming Command Execution

Add a one-way streaming execution contract first, using Server-Sent Events
(SSE), while preserving the existing buffered command endpoint.

- Stream stdout and stderr chunks as they are produced.
- Emit structured start, output, exit, timeout, and error events.
- Bound captured/streamed bytes and command duration with the existing policy.
- Stop the process group when the client disconnects or requests cancellation.
- Proxy the stream through `xolis-api` without buffering the complete response.
- Extend unit and acceptance tests for ordering, timeout, disconnect, and
  compatibility with the buffered endpoint.

Acceptance criteria:

- existing command clients continue to work;
- a client receives output before a long-running command exits;
- timeout and cancellation terminate the complete process group;
- stream events have a documented stable schema; and
- resource and output limits remain enforced.

### 3. Interactive Session and Hermes Demo

Build the demo layer after streaming is stable.

- Add an explicit session API with create, input, resize, output, cancel, and
  close semantics. Use WebSocket only for this bidirectional PTY contract.
- Keep sessions tenant-scoped, time-bounded, and limited to one configured
  workspace and command policy.
- Add an opt-in agent-oriented runtime profile with the dependencies required
  by the selected Hermes Agent implementation.
- Run Hermes without host credentials baked into the image. Supply demo tokens
  only at runtime through a reviewed secret path and restrict network egress to
  explicitly required endpoints.
- Provide a deterministic demonstration task that streams progress, edits files
  under `/workspace`, and leaves inspectable output artifacts.

Acceptance criteria:

- the demo does not weaken the default sandbox profile;
- session disconnect and TTL cleanup leave no running child process;
- secrets are not returned in logs, files, or stream events;
- Hermes can complete the documented task in a fresh sandbox; and
- the same streaming/session primitives remain useful for non-Hermes agents.

## Verification Strategy

Each delivery step must include local unit tests, manifest/static validation,
and a dry-run of the AWS lab workflow. Use the development cluster only when a
local or dry-run test cannot establish the result. Any temporary development or
image-build machine must be terminated in a success/failure cleanup path, and
its stopped or terminated state must be verified before the step is complete.

Large-sample latency, concurrency, soak, density, and cost testing is deferred
until the cluster is expanded. Small tests during these P1 steps validate
correctness and produce demo evidence; they are not SLA claims.
