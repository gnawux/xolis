# Xolis Hermes Live Demo — Architecture Image Brief

## Purpose

Create one conference-quality 16:9 infographic that explains how an
infrastructure stack goes from zero sandbox capacity to an interactive Hermes
Agent, then safely returns to zero. The audience is cloud-native and
infrastructure engineers. The image should make the infrastructure lifecycle,
isolation boundary, Nydus image path, warm-pool handoff, and external model call
understandable within ten seconds.

## Suggested Title

**From Zero Capacity to an Interactive AI Agent Sandbox**

Suggested subtitle:

**AWS · Kubernetes · Agent Sandbox · Kata Containers · Nydus · Hermes Agent · ZenMux**

## Projects and Products

### Xolis

Xolis is the demonstrated sandbox service. It provides the tenant-facing API,
selects a sandbox profile and warm pool, routes runtime operations, enforces
bounded execution, and cleans up every sandbox.

Relevant Xolis components:

- `xolis-api`: lifecycle and tenant boundary.
- `sandbox-router`: routes command, file, streaming, and interactive traffic.
- Hermes runtime image: contains Hermes Agent and the bounded Xolis runtime.
- Hermes demo client: prepares the profile, warms a sandbox, creates a claim,
  and bridges the local terminal to the sandbox WebSocket/PTTY session.

### Kubernetes Agent Sandbox

The Kubernetes Agent Sandbox project supplies the declarative lifecycle API:

- `SandboxTemplate`: defines the isolated runtime profile.
- `SandboxWarmPool`: keeps one complete sandbox ready before the request.
- `SandboxClaim`: transfers a warm sandbox to the requesting tenant.

### Kata Containers and Dragonball

Kata Containers runtime-rs creates a VM-backed Pod. Dragonball is the VMM used
for this AWS nested-virtualization environment. The Kata VM is the primary
workload isolation boundary between Hermes and the Kubernetes host.

### containerd and Nydus

containerd selects the opt-in `xolis-kata-nydus` runtime path. The Nydus
snapshotter mounts a converted RAFS image and fetches image data lazily from
Amazon ECR. Ordinary OCI remains a separate stable fallback path.

### AWS Infrastructure

- Amazon EKS provides the Kubernetes control plane.
- A managed system node runs Xolis, the router, and control-plane components.
- A dedicated self-managed Auto Scaling group provides M8i sandbox nodes.
- The sandbox-node AMI contains Kata runtime-rs, Dragonball, containerd, and
  Nydus.
- Amazon ECR stores immutable OCI and Nydus-formatted Hermes images.
- Amazon VPC CNI NetworkPolicy restricts sandbox ingress and egress.

### Hermes Agent, ZenMux, and GPT-5.4

Hermes Agent runs inside the Kata sandbox and uses terminal and file tools under
`/workspace`. It calls the ZenMux OpenAI-compatible API over HTTPS. ZenMux routes
the request to `openai/gpt-5.4`. A temporary ZenMux key is injected from a
Kubernetes Secret; no model credential is built into the image.

## Architecture Layout

Use a left-to-right main flow with three clearly separated zones.

### Zone 1 — Presenter and Control Plane

- Presenter laptop and terminal.
- Xolis demo client.
- `xolis-api` and `sandbox-router` on the EKS system node.
- Kubernetes Agent Sandbox controller and the three lifecycle objects.

### Zone 2 — Dedicated Sandbox Infrastructure

- Sandbox Auto Scaling group shown changing from `0 → 1 → 0`.
- One EC2 M8i node using the custom Kata/Nydus AMI.
- containerd and Nydus snapshotter on the host.
- A visually strong nested Kata VM boundary.
- Hermes Agent and `/workspace/demo` inside the Kata VM.
- Amazon ECR connected to Nydus with a dashed “lazy image reads” arrow.

### Zone 3 — External Inference

- ZenMux API at `https://zenmux.ai/api/v1`.
- GPT-5.4 behind ZenMux.
- One outbound HTTPS arrow from Hermes to ZenMux.
- A small lock or Secret icon near the arrow, labelled “temporary API key”.

## Numbered Live-Demo Flow

Show these numbers directly on the arrows or lifecycle path:

1. **Zero capacity** — sandbox ASG desired capacity is zero.
2. **Start node** — Xolis changes the dedicated ASG from zero to one.
3. **Join EKS** — the custom Kata/Nydus node boots, registers, and becomes Ready.
4. **Prepare runtime** — apply the opt-in Nydus RuntimeClass and Hermes profile.
5. **Warm sandbox** — `SandboxWarmPool` creates one complete Kata sandbox.
6. **Lazy image access** — Nydus mounts the Hermes RAFS image and reads required
   data from ECR on demand.
7. **Claim** — `SandboxClaim` transfers the ready sandbox to the demo tenant.
8. **Interactive session** — the presenter connects through Xolis using a
   tenant-scoped WebSocket/PTTY session; this is not SSH.
9. **Agent work** — Hermes asks GPT-5.4 through ZenMux, invokes terminal tools,
   and creates `status.md` and `result.json` under `/workspace/demo`.
10. **Convergent cleanup** — exit the session, delete the claim and sandbox,
    scale the warm pool to zero, delete the Kubernetes Secret, revoke the ZenMux
    key, and return the sandbox ASG to zero.

## Security and Lifecycle Callouts

Use compact badges or callout labels rather than paragraphs:

- **Kata VM isolation**
- **Tenant-scoped API**
- **WebSocket/PTTY — not SSH**
- **CPU · memory · storage · output limits**
- **Command timeout + absolute TTL**
- **DNS + reviewed HTTPS egress**
- **Temporary Secret; no key in image**
- **Success and failure share the same cleanup path**
- **OCI remains the fallback**

## Important Accuracy Constraints

- Do not draw Hermes directly on the EC2 host; it runs inside a Kata VM.
- Do not draw the presenter connecting directly to the sandbox or using SSH;
  traffic passes through `xolis-api` and `sandbox-router`.
- Do not imply that Nydus replaces OCI. Nydus is an opt-in path and OCI remains
  the fallback.
- Do not draw ZenMux or GPT-5.4 inside the cluster. They are external services.
- Do not depict a long-lived model key in the image or node. The key is
  temporary and injected through a Kubernetes Secret.
- Do not depict the warm pool as an ordinary container cache. It contains a
  complete ready Kata sandbox that is transferred by a claim.
- Do not imply that the measured demo is a statistically valid performance
  benchmark.

## Direct Prompt for gpt-image-2

Create a polished 16:9 conference infographic titled “From Zero Capacity to an
Interactive AI Agent Sandbox”. Use a clean cloud-native architecture style,
dark navy background, crisp white labels, cyan and teal infrastructure lines,
orange numbered lifecycle accents, subtle depth, and generous spacing. Make it
look suitable for a KubeCon infrastructure presentation, not like marketing
clip art.

Build one left-to-right architecture with three zones.

LEFT ZONE, “Presenter + EKS Control Plane”: show a presenter laptop and terminal
connected to “Xolis Demo Client”, then to “xolis-api” and “sandbox-router” on an
Amazon EKS system node. Include a Kubernetes Agent Sandbox controller with
three small resources: “SandboxTemplate”, “SandboxWarmPool”, and
“SandboxClaim”.

CENTER ZONE, “Dedicated Sandbox Infrastructure”: show a dedicated EC2 M8i Auto
Scaling Group with a prominent lifecycle ribbon “0 → 1 → 0”. Inside the running
node show “containerd” and an opt-in “Nydus snapshotter”. Nest a strong glowing
boundary labelled “Kata VM · runtime-rs + Dragonball”. Inside that VM place
“Hermes Agent”, “Xolis Runtime”, and a workspace containing “status.md” and
“result.json”. Connect Amazon ECR to Nydus using a dashed arrow labelled “lazy
RAFS image reads”. Add a small side label “OCI fallback remains available”.

RIGHT ZONE, “External Inference”: show “ZenMux · OpenAI-compatible API” outside
the Kubernetes cluster, connected onward to “GPT-5.4”. Draw exactly one outbound
arrow from Hermes Agent to ZenMux labelled “HTTPS”. Place a small lock beside
the arrow labelled “temporary API key from Kubernetes Secret”.

Overlay a clear numbered flow with ten compact steps:
1 Zero capacity,
2 Start sandbox node,
3 Node joins EKS,
4 Apply Nydus profile,
5 Warm one Kata sandbox,
6 Lazy image reads from ECR,
7 Claim ready sandbox,
8 WebSocket/PTTY — not SSH,
9 Hermes creates demo artifacts through ZenMux and GPT-5.4,
10 Delete sandbox, revoke key, scale to zero.

Add small security badges along the bottom: “Kata VM isolation”, “tenant-scoped
API”, “resource limits”, “command timeout + TTL”, “restricted egress”, and
“success or failure → cleanup”. Make the final cleanup arrow loop back visually
to the initial zero-capacity state.

Keep all text horizontal and legible. Use exact product and project names.
Prefer simple recognizable infrastructure symbols and restrained Kubernetes,
AWS, Kata, Nydus, Hermes, ZenMux, and GPT labels; do not invent or distort
official logos. Avoid tiny body text, decorative server racks, humanoid robots,
cyberpunk imagery, fake code, unexplained arrows, and performance claims.

## Optional Simpler Prompt

If the first result is too dense, generate a simpler version with only six
numbered stages:

1. ASG `0 → 1`
2. Kata/Nydus node joins EKS
3. Warm one complete Kata sandbox
4. Claim + WebSocket/PTTY
5. Hermes → ZenMux → GPT-5.4
6. Delete everything + ASG `1 → 0`

Retain the Kata VM nesting, ECR-to-Nydus lazy-read arrow, temporary Secret, OCI
fallback note, and cleanup loop.
