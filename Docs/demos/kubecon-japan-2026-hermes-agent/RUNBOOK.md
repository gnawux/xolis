# KubeCon Japan 2026 Hermes Agent Live Demo Runbook

This runbook uses the validated Nydus Hermes image and ZenMux's OpenAI-compatible
endpoint. Run every command from the SandboxService repository unless noted
otherwise.

The original AWS lab was deleted after the event. Recreate the documented lab,
publish fresh immutable images, and update the image references before reusing
this runbook. The original Kata 4.0.0 Dragonball build also had a seccomp
allowlist bug that killed inline virtio-fs on `listxattr`; use a Kata build that
contains the fix from
[kata-containers/kata-containers#13510](https://github.com/kata-containers/kata-containers/pull/13510).

## Files

- `configure-zenmux-secret.zsh`: prompts for the ZenMux key without echoing it and
  updates the Kubernetes Secret without putting the key in shell history or
  process arguments.
- `hermes-temporary-egress.yaml`: temporarily permits public HTTPS while
  excluding private, loopback, link-local, carrier-grade NAT, and multicast
  address ranges. Delete it immediately after the demo.
- `demo-prompt.txt`: deterministic prompt to paste into Hermes.

## Before Screen Sharing

Verify the lab while the sandbox Auto Scaling group remains at zero. Do not
start the sandbox node or create the temporary ZenMux key yet:

```console
cd /path/to/xolis
git status --short
aws sts get-caller-identity
python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json doctor
kubectl -n xolis-system rollout status deployment/xolis-api --timeout=180s
```

Before going on stage, sign in to ZenMux and leave its API-key management page
open, but do not create the demo key. This removes login, MFA, and navigation
latency from the live sequence.

Verify the starting point is zero sandbox capacity:

```console
aws autoscaling describe-auto-scaling-groups \
  --region ap-northeast-1 \
  --auto-scaling-group-names xolis-lab-sandbox \
  --query 'AutoScalingGroups[0].{Min:MinSize,Desired:DesiredCapacity,Max:MaxSize,Instances:Instances[*].LifecycleState}'
kubectl get nodes -l xolis.io/kata-ready=true
```

Expected ASG values are `Min=0`, `Desired=0`, and `Max=1`, with no sandbox
node returned by kubectl.

## On Stage: Start Infrastructure

Show the zero-capacity state above, then start the dedicated sandbox node:

```console
time python3 tools/xolis_aws_lab.py \
  --config tools/xolis_aws_lab.json node start
```

The recorded node-start time was 97.526 seconds. During this wait, explain that
the tool changes the dedicated self-managed sandbox ASG from `0` to `1`, waits
for the custom Kata/Nydus AMI to boot and join EKS, and verifies the labelled
node is Ready. The EKS control plane and system node remain running throughout.

While the first terminal waits for the node, use the already-open ZenMux page to
create a temporary demo key. Use a second terminal to store it in Kubernetes:

```console
cd /path/to/xolis
Docs/demos/kubecon-japan-2026-hermes-agent/configure-zenmux-secret.zsh
```

Paste at the hidden prompt. Do not display, select, read aloud, or put the key
in a shell command. After the Secret is configured, clear the macOS clipboard:

```console
pbcopy </dev/null
```

Optionally verify only the Secret's key names; this command does not print
either value:

```console
kubectl -n xolis-sandboxes get secret/hermes-agent-credentials \
  -o go-template='{{.metadata.name}} key-present={{if index .data "OPENAI_API_KEY"}}yes{{else}}no{{end}} endpoint-present={{if index .data "CUSTOM_BASE_URL"}}yes{{else}}no{{end}}{{"\n"}}'
```

Return to the first terminal. It should be close to reporting that the sandbox
node is Ready.

When the command completes, show the runtime-capable node:

```console
kubectl get nodes -l xolis.io/kata-ready=true -o wide
kubectl get runtimeclass xolis-kata xolis-kata-nydus
```

## On Stage: Warmup and Start Hermes

Run the demo command after the node is Ready. It applies the Nydus RuntimeClass
and Hermes profile, rolls out the API configuration, warms one sandbox, claims
that warm sandbox, and opens an interactive Hermes PTY. In the recorded lab
run, the Nydus sandbox reached Ready in 16.601 seconds:

```console
python3 tools/hermes_demo.py \
  --image-mode nydus \
  --hermes-model openai/gpt-5.4 \
  --hermes-provider custom \
  --egress-manifest Docs/demos/kubecon-japan-2026-hermes-agent/hermes-temporary-egress.yaml
```

Expected preparation messages include:

```text
==> Checking the cluster, runtime, sandbox node, and credential Secret
==> Applying the Hermes NYDUS profile
==> Configuring xolis-api for the Hermes profile
==> Warming one Hermes sandbox
==> Creating a sandbox from the warm pool
==> Starting Hermes in sandbox ...
Welcome to Hermes Agent! Type your message or /help for commands.
❯
```

Suggested narration while it warms:

1. The Nydus profile is separate from the stable OCI profile; selecting it does
   not replace the fallback path.
2. The warm pool creates a complete Kata-isolated sandbox before a user request.
3. A claim transfers ownership of that prepared sandbox instead of booting a new
   VM-backed Pod on the request path.
4. The API then opens a tenant-scoped WebSocket/PTTY session to the runtime.

The command selects the ephemeral `custom` provider and `openai/gpt-5.4` model,
while the Secret exposes the ZenMux endpoint as `CUSTOM_BASE_URL` and its key as
`OPENAI_API_KEY`. This is the non-persistent equivalent of the custom-endpoint
configuration in ZenMux's Hermes Agent guide. Do not run `hermes setup` or
`hermes model` during the live segment.

## Live Interaction

After the Hermes prompt appears, paste the complete contents of
`demo-prompt.txt`. A presenter copy is:

```text
You are running inside an isolated Xolis sandbox backed by Kata Containers.

Complete this short demonstration without asking follow-up questions:

1. Work only under /workspace/demo.
2. Create /workspace/demo/status.md containing a concise English summary of this session. Mention that the workload is isolated, time-bounded, and cleaned up after the session.
3. Create /workspace/demo/result.json with exactly these fields:
   - "agent": "hermes"
   - "task": "interactive-sandbox-demo"
   - "completed": true
4. Validate result.json with Python's JSON parser.
5. Display the working directory, a long listing of /workspace/demo, and the complete contents of both files.

Do not inspect or print environment variables, tokens, credentials, or files outside /workspace/demo. Do not install packages. Briefly describe each action while you work.
```

Approve only commands that operate under `/workspace/demo`, such as `mkdir`,
file writes, `python -m json.tool`, `ls`, and `cat`. Reject any request to inspect
environment variables, credentials, or unrelated paths.

Suggested narration:

1. The sandbox was prepared from a Nydus image and claimed from a one-replica
   warm pool.
2. The terminal is a WebSocket/PTTY session proxied through the tenant-scoped
   Xolis API; it is not direct SSH access.
3. Hermes can use ordinary terminal tools, but the workload remains inside a
   Kata VM with resource, network, command-time, and absolute TTL limits.
4. The output files prove that useful work happened inside the ephemeral
   workspace. Exiting the session deletes the claim and sandbox.

When the result has been displayed, type `/quit` and press Enter. Wait for the
script to print sandbox deletion, warm-pool scale-down, and API restoration
messages. `Ctrl-C` interrupts active work; use `/quit` for a clean shutdown.

## Fallback

If Nydus preparation fails but the node and API are healthy, rerun with the
ordinary OCI image:

```console
python3 tools/hermes_demo.py \
  --image-mode oci \
  --hermes-model openai/gpt-5.4 \
  --hermes-provider custom \
  --egress-manifest Docs/demos/kubecon-japan-2026-hermes-agent/hermes-temporary-egress.yaml
```

If model inference fails, keep the terminal visible, explain that the sandbox,
warm pool, and interactive PTY path are already running, then press `Ctrl-C` and
use the cleanup commands below. Do not troubleshoot credentials on screen.

## Mandatory Cleanup

Run these commands after the demo even if the interactive command failed:

```console
kubectl delete -f Docs/demos/kubecon-japan-2026-hermes-agent/hermes-temporary-egress.yaml \
  --ignore-not-found
kubectl -n xolis-sandboxes delete secret/hermes-agent-credentials \
  --ignore-not-found
for pool in hermes-agent-v1-pool hermes-agent-nydus-v1-pool; do
  if kubectl -n xolis-sandboxes get sandboxwarmpool "$pool" >/dev/null 2>&1; then
    kubectl -n xolis-sandboxes patch sandboxwarmpool "$pool" \
      --type=merge --patch '{"spec":{"replicas":0}}'
  fi
done
python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json node stop
pbcopy </dev/null
```

Finally, return to the ZenMux key-management page and revoke the temporary demo
key. Kubernetes Secret deletion and ZenMux key revocation are separate cleanup
steps; complete both even if the demo failed.

Verify cleanup:

```console
kubectl -n xolis-sandboxes get sandboxclaims,sandboxwarmpools
aws autoscaling describe-auto-scaling-groups \
  --region ap-northeast-1 \
  --auto-scaling-group-names xolis-lab-sandbox \
  --query 'AutoScalingGroups[0].{Min:MinSize,Desired:DesiredCapacity,Max:MaxSize,Instances:Instances[*].LifecycleState}'
```

Expected ASG values are `Min=0`, `Desired=0`, `Max=1`, with no remaining
instances after termination completes.
