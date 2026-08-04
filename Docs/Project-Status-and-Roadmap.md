# Project Status and Roadmap

This document summarizes the Xolis implementation status, measured behavior,
current availability boundary, and prioritized roadmap as of 2026-08-04.

The `v0.1.0` tag identifies the first validated MVP. The `v0.2.0` tag adds the
automated service lifecycle, cold-versus-warm benchmark workflow, optional
Nydus path, and interactive Hermes Agent demonstration described below. The
`v0.2.1` patch release vendors the two Dragonball seccomp fixes merged through
Kata Containers PR #13510 so the fixed Kata 4.0.0 AMI build supports the inline
virtio-fs metadata operations exercised by Hermes.

The `v0.3.0` release identifies the first verified PVM host, runtime, immutable
AMI, isolated EKS node pool, and Xolis lifecycle milestone. The custom kernel,
runtime-rs, and Dragonball combination has passed standalone host and CRI
qualification; the EKS CNI, scheduling, lifecycle, SSE, interactive PTY,
warm-pool, node-loss recovery, and independent native-KVM regression gates have
also passed. This is an experimental qualification release, not a production
PVM support claim. A model-backed Hermes workflow and broader failure injection
remain in progress.

## 1. Current Development Progress

### Delivered Architecture

The AWS lab can reproducibly provision:

- An Amazon EKS control plane in `ap-northeast-1`.
- One EKS managed node group for system components.
- One dedicated self-managed Auto Scaling group for sandbox nodes, with explicit
  `0 -> 1 -> 0` test capacity instead of workload-driven autoscaling.
- An EKS-compatible Amazon Linux 2023 AMI with Kata Containers 4.0.0
  runtime-rs, Dragonball, the upstream PR #13510 seccomp backport, and the
  `xolis-kata` containerd handler.
- A documented Dragonball CPUID workaround for the AWS M8i nested-KVM
  environment.
- Amazon VPC CNI NetworkPolicy enforcement for sandbox traffic isolation.
- Immutable, scan-on-push private ECR repositories for Xolis images.

The disposable EKS cluster used for the August 4 qualification was destroyed
after the tests. The two validated AMIs and their snapshots, the private ECR
images, and the versioned PVM S3 artifacts were retained so the environment can
be recreated without repeating the kernel and runtime builds.

The validated AMI now also contains an opt-in Nydus path. Ordinary OCI remains
the default and fallback; Nydus requires the separate `xolis-kata-nydus`
RuntimeClass and an image converted with Nydus metadata.

### Delivered Experimental PVM Foundation

The PVM investigation has moved beyond source-build feasibility:

- pinned Linux 6.12.33 PVM host and guest kernels build with validated Amazon
  Linux 2023, EKS, container, storage, network, vsock, and virtio-fs settings;
- the PVM host boots on an AWS `c7i.4xlarge` without `vmx` or `svm`, requires
  the documented host `pti=off` setting, loads `kvm-pvm`, and exposes KVM API
  version 12;
- Kata 4.0.0 runtime-rs with upstream Dragonball boots the PVM guest through
  direct containerd and the dedicated `xolis-kata-pvm` CRI handler;
- one-vCPU and two-vCPU boot, block rootfs, vsock, memory, time,
  inline virtio-fs, full xattr operations, output, exit status, repeated
  teardown, and cleanup have passed; and
- the matched kernel and 2.26 GB runtime bundles, manifests, and checksums are
  retained in a private versioned S3 artifact bucket under immutable PVM and
  Kata commit prefixes; and
- a separate Packer pipeline verifies those artifacts, reboots into the PVM
  kernel, validates the isolated runtime handler, and has published the first
  account-local PVM AMI, `ami-01772ceec96a8fa48` in `ap-northeast-1`; and
- an isolated EKS node pool and `RuntimeClass/xolis-kata-pvm` have passed node
  registration, PVM guest boot, VPC CNI addressing, cluster DNS, public egress,
  DNS-only NetworkPolicy denial, containerd and kubelet restart, and cold node
  replacement; and
- the `python-pvm-v1` profile has passed idempotent and tenant-scoped creation,
  PVM placement, buffered commands, timeouts, file operations and bounds,
  denied egress, explicit deletion, TTL cleanup, cold claims, a one-replica
  warm pool, reset, SSE streaming, interactive PTY, no-fallback scheduling,
  and ASG scale-to-zero cleanup.

The native-KVM fallback was rebuilt from the EKS 1.35 AL2023 baseline as
`ami-06afb1bb0f61cf03b` and passed the automated service lifecycle regression
on August 4, 2026. The run covered placement, idempotency and tenant isolation,
buffered command behavior, file operations and bounds, denied egress, explicit
cleanup, and TTL cleanup. Sandbox Ready took 18.158 seconds in this single cold
sample; this is a functional regression result, not a performance baseline.
Packer returned an AWS API `EOF` during final cleanup after the AMI and snapshot
were already available; the builder, temporary IAM objects, security group, and
key pair were then cleaned manually and verified absent.

Five consecutive cached-image two-vCPU CRI smoke runs completed in 4.457 to
4.528 seconds. This is a single-host integration measurement, not a sandbox
Ready latency, density result, or large-cluster performance claim.

The final bounded PVM comparison ran five sequential cold samples and five
sequential warm samples on one already-Ready PVM node. It is summarized in the
performance section below. The sample count, cluster size, and lack of
concurrency make it useful for validating warm-pool behavior only, not for a
production performance or scalability claim.

### Delivered Service Components

The functional MVP includes:

| Component | Current implementation |
| --- | --- |
| Lifecycle API | Kubernetes Agent Sandbox v0.5.3 with `SandboxTemplate`, `SandboxWarmPool`, and `SandboxClaim`. |
| Public service boundary | Rust `xolis-api` using Axum, Tokio, and kube-rs. |
| Request routing | Pinned Agent Sandbox Go router. |
| Initial runtime | Bounded Python runtime with buffered and SSE command execution, WebSocket/PTTY sessions, and file operations under `/workspace`. |
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

### Bounded PVM Cold and Warm Comparison

On 2026-08-04, Xolis ran five sequential samples per mode in
`ap-northeast-1` on EKS 1.35. The system pool had one `t3.large` node and the
isolated PVM pool had one `c7i.xlarge` node using
`ami-01772ceec96a8fa48`, Linux `6.12.33-xolis-pvm`, Kata Containers 4.0.0
runtime-rs, upstream Dragonball, and `RuntimeClass/xolis-kata-pvm`. Both modes
used Python runtime digest
`sha256:7dedcfad0e74773d7ec68a613ec76909efc70cbb5351c4f6c8a7df89d32be027`
and the same already-Ready PVM node. Tests ran one at a time with no competing
sandbox workload.

Here, cold means `SandboxWarmPool.spec.replicas=0`; it does not mean a cold EC2
node or an empty containerd image cache. Warm means one complete Sandbox was
Ready before the claim. Each sample measured the interval from the Xolis create
request to the public `Running` state, then exercised placement, commands,
files, denied egress, and explicit cleanup. TTL was excluded.

| Mode | Samples | Minimum | Mean | Median | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cold, pool size 0 | 5 | 8.489 s | 8.806 s | 8.587 s | 9.547 s |
| Warm, pool size 1 | 5 | 1.286 s | 1.385 s | 1.354 s | 1.565 s |

The mean warm result was 6.36 times faster and 7.421 seconds lower than the
mean cold result in this bounded run. This is a small-cluster, low-concurrency
functional comparison only. Five sequential samples cannot establish p95 or
p99 latency, throughput, density, behavior under image-cache misses, or
production performance.

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
- no inbound sandbox service or externally exposed interactive endpoint;
- Nydus is validated only as an opt-in single-node comparison path, and
  Dragonfly distribution is not implemented;
- PVM is validated through the immutable AMI, isolated EKS scheduling, and
  CNI-backed guest network and core Xolis lifecycle boundary, including SSE
  streaming, interactive PTY, Hermes CLI bootstrap, and node-loss recovery;
  the native-KVM lifecycle regression also passes, but a model-backed Hermes
  workflow, broader failure injection, and kernel operations policy have not
  completed; and
- no statistically useful latency distribution, soak test, concurrency test,
  failure-rate measurement, or cost-per-sandbox result.

## 3. Roadmap Decisions and Sequence

Xolis is not optimizing for an immediate commercial launch. Large-cluster
evaluation, production access controls, and statistically defensible performance
claims can wait until the architecture is ready to benefit from them. Before
moving toward scale, the main path has two priorities:

1. separate cloud-neutral service behavior from cloud-specific infrastructure
   and operations; and
2. turn the validated PVM host/runtime proof into a reproducible, isolated EKS
   node path and qualify the full Xolis lifecycle on it.

Expected improvements below are engineering hypotheses or qualification goals.
They must be confirmed by implementation and controlled tests before they
become project claims.

### Pre-Scale Workstream A: Separate Cloud-Neutral and Cloud-Specific Layers

The public API, sandbox lifecycle, runtime protocol, profile policy, ownership
rules, and cleanup semantics should remain independent of a cloud provider. The
current AWS lab mixes reusable orchestration with EKS, EC2 Auto Scaling, ECR,
Amazon Linux, VPC CNI, and AWS identity assumptions. Refactor this boundary
before adding another provider or a production-scale capacity controller.

Cloud-neutral responsibilities:

- the Xolis HTTP contract, tenant and idempotency semantics, and runtime client;
- `SandboxTemplate`, `SandboxWarmPool`, and `SandboxClaim` reconciliation;
- Kubernetes labels, taints, RuntimeClass selection, network-policy intent, and
  cleanup verification;
- workload profiles, OCI image references, optional Nydus selection, and
  provider-neutral lifecycle metrics; and
- a conformance workflow that can exercise lifecycle behavior without calling
  a cloud API directly.

Cloud-specific responsibilities:

- cluster and network provisioning;
- node-pool creation, scale-to-zero, capacity discovery, and instance selection;
- host image construction, registry credentials, and workload identity;
- CNI implementation, persistent-storage classes, load balancers, and DNS; and
- provider-native logging, metrics, cost, and failure diagnostics.

Planned development:

1. Define a small capacity-provider contract for discovering, starting, stopping,
   and observing sandbox node pools. Keep provider calls outside `xolis-api`.
2. Split the reusable lifecycle workflow from `tools/xolis_aws_lab.py`; retain AWS
   as one adapter rather than the orchestration model itself.
3. Move provider inputs out of the common configuration schema and define a
   provider-neutral node-capability contract based on Kubernetes labels, taints,
   RuntimeClasses, and conditions.
4. Keep `infra/aws`, AWS image construction, ECR integration, and VPC CNI setup
   explicitly provider-specific. Define equivalent directories and interfaces
   before implementing a second cloud.
5. Add unit tests with a fake capacity provider and a Kubernetes conformance path
   that verifies the same create, execute, interactive, TTL, and cleanup behavior
   independently of AWS provisioning.
6. Validate the boundary with one non-AWS or private-cluster deployment after the
   interfaces stabilize; do not require feature parity with AWS in the first test.

Exit criteria:

- `xolis-api` and the sandbox profiles contain no AWS API dependency;
- the lifecycle acceptance suite runs through a provider-neutral entry point;
- AWS provisioning and capacity changes are isolated behind an adapter; and
- adding another provider does not require changing the public sandbox API.

### Pre-Scale Workstream B: Complete PVM Platform Integration

The standalone PVM functional proof is complete for the tested host and runtime
scope. The remaining priority is to make that matched artifact set
reproducible as an operator-selectable Kubernetes capability without weakening
or replacing the native-KVM baseline. The executable sequence and detailed
release gates remain in
[PVM Development and Test Plan](PVM-Development-and-Test-Plan.md).

Remaining development, in order:

1. Complete the remaining provider-neutral lifecycle gates on PVM: a
   model-backed Hermes workflow and broader failure injection.
   Preserve the already passing buffered and SSE commands, interactive PTY,
   Hermes CLI bootstrap, file, policy, deletion, TTL, warm-pool reset,
   no-fallback, and repeated cleanup paths as regressions.
2. Preserve the passing native-KVM lifecycle regression and ordinary OCI PVM
   path on each release candidate. Treat Nydus as a later optional PVM test and
   keep Dragonfly outside the first PVM release gate.
3. Complete readiness diagnostics, kernel CVE and rebase ownership, host
   `pti=off` risk documentation, AMI upgrade and rollback, and node replacement
   procedures before exposing PVM as an operator-selectable capability.

Exit criteria:

- a Kata sandbox boots without provider-supplied nested virtualization through
  the immutable PVM AMI and dedicated Kubernetes scheduling path;
- the provider-neutral lifecycle suite passes with the required host `pti=off`
  constraint and residual risk explicitly documented, without weakening the
  public authorization or cleanup contract;
- native KVM remains an independent fallback; and
- remaining performance, density, and security questions are recorded for the
  later large-cluster qualification phase.

### Before Scale, Not Urgent: Dragonfly Distribution Qualification

Dragonfly peer-to-peer distribution is expected to be primarily a deployment
and operations task. It has little value in the current one-node lab and should
not block the cloud-boundary or PVM work.

Planned work:

- choose and pin the Dragonfly deployment topology and Helm release;
- define scheduler, manager, seed-peer, and peer placement, persistent cache
  storage, certificates, authentication, ports, and NetworkPolicies;
- integrate Dragonfly with the optional Nydus profile while retaining direct
  registry pull as a tested fallback;
- add provider-neutral configuration for selecting the distribution path;
- expose peer, cache-hit, origin-fallback, transfer, and failure metrics; and
- validate correctness and cache behavior on at least three sandbox nodes before
  running distribution benchmarks.

Expected Xolis code changes are limited to configuration, profile selection,
health reporting, test automation, and fallback behavior unless integration
testing identifies a missing upstream interface.

### Scale-Entry Decision: Kubernetes State and an External Database

The current `xolis-api` deliberately has no external database. Durable control
state is represented by Kubernetes `SandboxClaim` objects. Tenant and
idempotency hashes are stored in labels, request metadata is stored in an
annotation, and API instances reconstruct state by listing labelled claims.
This keeps Kubernetes as the lifecycle source of truth and avoids database and
cluster-state divergence at the current scale.

Revisit this design when scale introduces one or more of these conditions:

- claim list/watch traffic creates measurable Kubernetes API Server pressure;
- idempotency records must outlive deleted claims;
- audit, usage, billing, or failure history requires durable retention;
- operators need indexed queries that labels and annotations cannot support; or
- multiple clusters or Regions require a service-level view of ownership and
  placement.

If a database is introduced, Kubernetes should remain authoritative for desired
and observed sandbox lifecycle state. The database should initially own
service-level idempotency, audit history, usage records, and cross-cluster query
indexes. Define reconciliation, retention, backup, and disaster-recovery rules
before storing the same mutable lifecycle field in both systems.

### Deferred Until Scale: Performance, Reliability, and Productization

Large-cluster evaluation is intentionally deferred. When the project enters
this phase:

- run controlled cold and warm latency distributions with p50, p95, and p99;
- add concurrent lifecycle, soak, node-failure, cleanup, and density tests;
- measure cost per successful sandbox and compare native KVM with qualified PVM;
- evaluate automatic capacity management without coupling `xolis-api` to a
  provider;
- add OIDC, tenant quotas, rate limits, audit export, and a public service
  ingress only when multi-user operation requires them; and
- qualify Dragonfly distribution under representative multi-node image demand.

Persistent workspaces, filesystem-only suspend/resume, VM checkpoint research,
Confidential Containers, GPUs, multi-region placement, billing, and a separate
scheduler or event bus remain later capabilities. They do not block the two
pre-scale priorities.

## Recommended Immediate Milestone

The next milestone has two bounded deliverables:

1. define and implement the cloud-neutral capacity and lifecycle boundary while
   retaining AWS as the first provider adapter; and
2. turn the verified PVM AMI and isolated EKS path into a repeatable release
   qualification workflow, including the remaining model-backed Hermes,
   failure-injection, kernel maintenance, upgrade, and rollback gates.

No large-cluster performance claim is required for this milestone. The bounded
PVM cold/warm result validates the warm-pool mechanism only; scale and tail
latency remain deferred.
