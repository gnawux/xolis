# Xolis AWS Lab Tool

The AWS lab tool orchestrates repeatable minimal-deployment checks. It requires
Python 3.11, OpenTofu, AWS CLI, and kubectl. Packer is additionally required when
building the custom Kata sandbox AMI; Nydus inputs are optional.

Copy the example configuration and replace all placeholder resource names:

    cp tools/xolis_aws_lab.example.json tools/xolis_aws_lab.json

The example configuration targets the Tokyo Region (`ap-northeast-1`). Change `region` if the lab is hosted elsewhere.

Run a read-only dependency check:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json doctor

Preview infrastructure changes:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json infra plan

Run the complete disposable test cycle. It explicitly starts the configured
self-managed sandbox Auto Scaling group, runs the test, then removes the test
resources and scales that group back to zero. It does not use workload-driven
node autoscaling:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json cycle run

This cycle applies `deploy/tests/smoke-kata-pod.yaml`; it validates node startup,
RuntimeClass scheduling, Kata readiness, evidence collection, cleanup, and
scale-to-zero. It does not run the full Xolis service acceptance suite.

Use the dry-run option to print mutating commands without executing them:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json --dry-run cycle run

Dry-run validates the JSON configuration but does not require the configured OpenTofu directory, bootstrap manifests, or test workload manifest to exist. This makes it safe to preview a configuration before its deployment inputs have been created locally.

The tool writes command logs and Kubernetes resource snapshots to the configured artifact directory. The snapshot is diagnostic metadata, not a filesystem or VM checkpoint.

After deploying Agent Sandbox and the Xolis stack, run the separate self-cleaning
service acceptance test while the sandbox ASG has one Ready node:

    python3 deploy/tests/smoke_service.py

See `deploy/README.md` for the complete service deployment and validation
sequence.

The Lab tool can run that sequence and guarantee that the sandbox ASG is
returned to zero even when installation or validation fails:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json service run

The command starts the sandbox node, applies the runtime bootstrap, installs the
pinned Agent Sandbox controller, applies the Xolis manifests, runs the service
acceptance test, records a Kubernetes resource snapshot, and stops the sandbox
node. The control-plane components remain installed for subsequent runs; the
acceptance test deletes every sandbox claim it creates.

Each service run writes two machine-readable reports under its timestamped
artifact directory:

- `workflow-report.json` records the status and duration of node startup,
  bootstrap, installation, deployment, acceptance, snapshot, and node stop.
- `service-smoke-metrics.json` records Sandbox Ready latency, first-command
  latency, explicit cleanup latency, TTL lifecycle duration, and overall test
  duration.

Failed runs also write reports and mark the failed phase, which allows repeated
runs to be compared without parsing console output.

Compare cold claim creation with a one-replica Agent Sandbox warm pool after the
functional service run is stable:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json \
        benchmark run --iterations 3

The benchmark starts one sandbox node, deploys the service once, runs the same
acceptance workload without the TTL case for each cold and warm sample, then
returns the warm pool and sandbox ASG to zero. It waits for
`.status.readyReplicas` before every warm sample so replenishment time is not
mistaken for claim latency. Per-sample JSON files and `benchmark-summary.json`
record minimum, mean, median, and maximum metrics for both modes.

## Hermes Interactive Demo

`hermes_demo.py` prepares the validated Hermes profile, warms one sandbox,
creates a claim through `xolis-api`, and attaches the local terminal to an
interactive Hermes process:

    python3 tools/hermes_demo.py \
        --egress-manifest /path/to/reviewed-provider-egress.yaml

The cluster must already contain `Secret/hermes-agent-credentials` in
`xolis-sandboxes`. The tool verifies the Secret but never reads or prints its
values. The egress manifest must allow TCP 443 only to the selected model
provider; without it, the checked-in profile permits DNS only and Hermes can
start but cannot contact a model service.

Use `--image-mode nydus` to apply and use the opt-in Nydus RuntimeClass and
validated Nydus image. By default the tool deletes the demo sandbox, scales the
warm pool to zero, and restores the previous API environment when the session
ends. Pass `--keep-prepared` to leave the API and warm pool ready for another
demo. Run `python3 tools/hermes_demo.py --help` for all timeout and image
options.

For an ephemeral OpenAI-compatible endpoint, store `OPENAI_API_KEY` and
`CUSTOM_BASE_URL` in the credential Secret, then pass both
`--hermes-provider custom` and `--hermes-model PROVIDER/MODEL`. Supplying the
provider and model on the session command avoids writing a persistent Hermes
configuration into the disposable sandbox workspace.
