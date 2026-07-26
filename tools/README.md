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
