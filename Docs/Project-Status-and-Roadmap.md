# Project Status and Roadmap

This document summarizes the Xolis implementation status, measured behavior,
current availability boundary, and prioritized roadmap as of 2026-07-27.

The `v0.1.0` tag identifies the first validated MVP. Current `main` adds the
automated service lifecycle and cold-versus-warm benchmark workflow described
below.

## 1. Current Development Progress

### Delivered Architecture

The AWS lab currently provides:

- An Amazon EKS control plane in `ap-northeast-1`.
- One EKS managed node group for system components.
- One dedicated self-managed Auto Scaling group for sandbox nodes, with explicit
  `0 -> 1 -> 0` test capacity instead of workload-driven autoscaling.
- An EKS-compatible Amazon Linux 2023 AMI with Kata Containers 4.0.0
  runtime-rs, Dragonball, and the `xolis-kata` containerd handler.
- A documented Dragonball CPUID workaround for the AWS M8i nested-KVM
  environment.
- Amazon VPC CNI NetworkPolicy enforcement for sandbox traffic isolation.
- Immutable, scan-on-push private ECR repositories for Xolis images.

Nydus is supported by the AMI build as an optional input, but it is disabled in
the validated baseline. The current service uses ordinary OCI images.

### Delivered Service Components

The functional MVP includes:

| Component | Current implementation |
| --- | --- |
| Lifecycle API | Kubernetes Agent Sandbox v0.5.3 with `SandboxTemplate`, `SandboxWarmPool`, and `SandboxClaim`. |
| Public service boundary | Rust `xolis-api` using Axum, Tokio, and kube-rs. |
| Request routing | Pinned Agent Sandbox Go router. |
| Initial runtime | Bounded Python runtime with command execution and file operations under `/workspace`. |
| Isolation | Kata runtime-rs and Dragonball on a dedicated labelled and tainted node. |
| Network policy | Default deny, DNS-only sandbox egress, and router-only runtime ingress. |
| Resource policy | CPU, memory, ephemeral storage, upload, output, command-time, and TTL limits. |
| Ownership model | Tenant-scoped API operations and idempotent sandbox creation. |

The automated acceptance test validates:

- claim creation and Kata placement;
- stdout, stderr, exit status, and command timeout;
- file upload, listing, download, path confinement, and upload bounds;
- tenant isolation and idempotency;
- denied unapproved public egress;
- explicit foreground deletion; and
- absolute TTL cleanup.

### Delivered Operations and Test Tooling

The Python Lab tool now supports three validation levels:

| Command | Purpose |
| --- | --- |
| `cycle run` | Start a sandbox node, run the minimal Kata Pod check, collect evidence, clean up, and return the ASG to zero. |
| `service run` | Install or reconcile the complete service, run acceptance tests, write structured reports, and return the ASG to zero. |
| `benchmark run` | Compare repeated cold and one-replica warm-pool samples on the same node and write a statistical summary. |

Cleanup paths attempt to return disposable capacity to zero after both success
and failure. Timestamped artifacts include command logs, Kubernetes resource
snapshots, workflow phase timings, per-sample service metrics, and benchmark
summaries.

## 2. Performance and Availability

### Initial Measured Performance

The first real cold-versus-warm validation ran in the Tokyo AWS lab on
2026-07-27. It used one sample per mode, so the results validate the workflow and
show direction; they are not statistically sufficient for an SLA or a published
performance claim.

| Metric | Cold, pool size 0 | Warm, pool size 1 | Observation |
| --- | ---: | ---: | --- |
| Sandbox Ready | 25.546 s | 1.405 s | Warm was 18.2 times faster, a 94.5% reduction in this sample. |
| First command after Ready | 0.450 s | 0.390 s | Similar once the runtime was Ready. |
| Explicit cleanup | 5.597 s | 6.652 s | No demonstrated warm-pool advantage. |
| Complete acceptance sample | 45.957 s | 19.321 s | TTL validation was excluded from benchmark samples. |

Infrastructure lifecycle was measured separately:

| Infrastructure phase | Measured time |
| --- | ---: |
| EC2 start through labelled EKS node Ready | 97.526 s |
| Runtime bootstrap | 2.215 s |
| Agent Sandbox installation or reconciliation | 11.286 s |
| Xolis service deployment or reconciliation | 5.705 s |
| Warm-pool creation and Ready | 7.646 s |
| Sandbox-node termination and disappearance | 82.959 s |

From zero sandbox-node capacity, the observed path to a cold Ready sandbox was
approximately 123 seconds: about 98 seconds for node capacity and 26 seconds for
the sandbox. Keeping capacity available removes the EC2 and EKS node-start phase;
keeping one sandbox warm additionally reduced claim-to-Ready latency to about
1.4 seconds in this sample.

### Current Availability Boundary

The MVP is usable as a reproducible development and architecture-validation
environment. It is not yet a production service and has no availability SLA.

Validated behavior includes:

- repeatable creation, execution, file access, deletion, and TTL expiry;
- VM-backed Kata isolation on the tested AWS M8i environment;
- enforced Kubernetes network policy in the tested EKS configuration;
- failure-safe sandbox-claim cleanup in the acceptance test;
- failure-safe attempts to reset the warm pool and sandbox ASG to zero; and
- reproducible, machine-readable diagnostic and performance artifacts.

Important current limitations are:

- one system node and at most one sandbox node, with no high-availability test;
- explicit operator or test-tool capacity changes rather than automatic scaling;
- a development public-subnet topology and public EKS endpoint;
- header-based lab tenant identity rather than OIDC authentication;
- no public Gateway API or load-balanced service endpoint;
- no persistent workspace, suspend/resume, or VM checkpoint;
- no interactive terminal, streaming output, or inbound sandbox service;
- no Nydus or Dragonfly acceleration in the validated baseline; and
- no statistically useful latency distribution, soak test, concurrency test,
  failure-rate measurement, or cost-per-sandbox result.

## 3. Next Work and Expected Improvements

Expected improvements below are engineering hypotheses or initial targets. They
must be confirmed by controlled repeated tests before they become project
claims.

### Priority 0: Harden the Performance and Reliability Baseline

Run at least 20 to 30 cold and warm samples under controlled conditions and add
p50, p95, and p99 reporting. Record AMI ID, instance type, image digest, node
cache state, Kubernetes version, Kata version, and failure reason with each run.
Add concurrent sandbox tests, repeated lifecycle soak tests, and cost estimates.

Expected outcome:

- confidence intervals and tail latency instead of a single favorable sample;
- a defensible warm-pool target, initially evaluating whether Ready p95 can stay
  below 2 seconds on an already-running node; and
- measured success rate, cleanup reliability, resource density, and cost per
  successful sandbox.

### Priority 1: Evaluate Nydus Without Replacing the Stable OCI Path

Build a second, version-pinned AMI and runtime image path with the Nydus
snapshotter. Compare ordinary OCI and Nydus on fresh nodes, cached nodes, and
larger realistic agent images. Preserve ordinary OCI as the fallback until
compatibility and cleanup behavior are proven.

Expected outcome:

- less image data downloaded before execution and lower cold Ready latency for
  large or uncached images;
- the largest benefit on fresh nodes and large images, with limited benefit for
  the current small cached Python image; and
- an initial experimental target of a 30% or greater reduction in uncached
  image-to-Ready time for a representative large image. This is a test target,
  not a forecast or current capability.

Dragonfly distribution and cache benchmarks should follow only after a single
node Nydus path is stable.

### Priority 1: Productize Access and Multi-Tenant Policy

Add OIDC authentication, tenant quotas, rate limits, audit export, and a Gateway
API or equivalent ingress. Move from one shared sandbox namespace toward a
reviewed multi-tenant namespace and policy model. Add explicit egress profiles
instead of accepting arbitrary destinations.

Expected outcome:

- a service boundary suitable for controlled multi-user evaluation;
- predictable tenant resource consumption and clearer auditability; and
- no expected sandbox startup improvement, because this work targets security
  and operability rather than runtime latency.

### Priority 1: Streaming and Interactive Agent Workloads

Add streaming command output, cancellation, an interactive terminal protocol,
and optionally controlled port forwarding or inbound application services.

Expected outcome:

- useful feedback before long commands complete;
- lower perceived latency even when Sandbox Ready time is unchanged; and
- broader compatibility with coding agents and tool-execution frameworks.

### Priority 2: Capacity Automation and Production AWS Topology

Introduce an explicit capacity-management policy after cold/warm behavior and
cost are measured. Evaluate scheduled minimum capacity, queue-depth-driven ASG
changes, Karpenter, or another controller without coupling the public API to AWS.
Add private subnets, VPC endpoints, reusable OpenTofu modules, multiple system
nodes, controlled upgrades, CloudWatch collection, and release automation.

Expected outcome:

- removal of the approximately 98-second node-start penalty when spare capacity
  is already available;
- higher availability and safer shared operation; and
- a direct cost-versus-latency tradeoff: standby node or warm-sandbox capacity
  consumes resources even when no request is active.

### Priority 2: Persistent Workspaces and Checkpoint Research

Add an EBS-backed profile with `WaitForFirstConsumer`, then evaluate
filesystem-only suspend/resume. Treat Kata VM memory snapshots or runtime-native
checkpoint and restore as a separate research milestone.

Expected outcome:

- durable agent workspaces across sandbox replacement;
- faster task continuation when filesystem state is sufficient; and
- no committed VM-resume target until compatibility, security, and snapshot
  size have been measured.

### Later Evaluation

Later work includes PVM, Confidential Containers, GPUs, multi-region operation,
Dragonfly peer-to-peer distribution, a billing ledger, and a separate scheduler
or event bus. These should not delay the controlled Nydus comparison,
multi-tenant access controls, or reliability baseline.

## Recommended Immediate Milestone

The next milestone should combine two bounded deliverables:

1. Produce a statistically useful ordinary-OCI cold/warm baseline with tail
   latency, reliability, density, and cost data.
2. Implement Nydus as an optional comparison path and measure it against exactly
   the same workload and environment.

This sequence turns Nydus integration into a measurable performance result while
preserving the validated Kata and ordinary-OCI MVP as the fallback.
