# Xolis AWS Lab Tool

The AWS lab tool orchestrates repeatable minimal-deployment checks. It requires Python 3.11, Terraform, AWS CLI, and kubectl.

Copy the example configuration and replace all placeholder resource names:

    cp tools/xolis_aws_lab.example.json tools/xolis_aws_lab.json

The example configuration targets the Tokyo Region (`ap-northeast-1`). Change `region` if the lab is hosted elsewhere.

Run a read-only dependency check:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json doctor

Preview infrastructure changes:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json infra plan

Run the complete disposable test cycle:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json cycle run

Use the dry-run option to print mutating commands without executing them:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json --dry-run cycle run

Dry-run validates the JSON configuration but does not require the configured Terraform directory, bootstrap manifests, or test workload manifest to exist. This makes it safe to preview a configuration before its deployment inputs have been created locally.

The tool writes command logs and Kubernetes resource snapshots to the configured artifact directory. The snapshot is diagnostic metadata, not a filesystem or VM checkpoint.
