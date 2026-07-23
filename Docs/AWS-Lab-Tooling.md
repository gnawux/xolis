# AWS Lab Tooling

This document defines the tooling strategy for repeatable Xolis minimal-deployment tests on AWS.

## Decision

Use a hybrid design:

- Terraform manages persistent AWS infrastructure.
- A Python command-line tool manages node bootstrap and each disposable test cycle.
- AWS CLI, kubectl, and Helm remain the underlying execution interfaces.

Do not use an unstructured collection of shell scripts as the primary workflow. Do not use CloudFormation as the first implementation for this project.

## Rationale

### Terraform for Persistent Infrastructure

The persistent layer includes the VPC, subnets, IAM roles, EKS control plane, system node group, sandbox node group launch template, security groups, ECR repositories, and optional state backend.

Terraform provides a reviewed plan before mutation, tracks the remote objects it owns in state, and can intentionally destroy temporary environments. This is important because an EKS test environment has interdependent resources and can otherwise leave chargeable infrastructure behind.

Terraform should not perform dynamic in-cluster test actions. Kubernetes resources, test artifacts, and node scaling have a shorter lifecycle and need runtime assertions, log collection, and guaranteed cleanup.

### Python CLI for Test Cycles

The test-cycle tool owns the imperative sequence:

1. Initialize or apply the Terraform environment.
2. Create kubeconfig access.
3. Start the sandbox node group.
4. Wait until a labelled sandbox node is Ready.
5. Apply node bootstrap manifests.
6. Apply a test workload manifest.
7. Wait for the configured readiness check.
8. Save a Kubernetes resource snapshot and command log.
9. Delete the test resources.
10. Stop the sandbox node group.

Python is selected because the initial tool can use the standard library, has good process and JSON handling, and can later use AWS and Kubernetes SDKs without changing its command interface.

### Why Not Shell as the Primary Tool

Shell remains useful inside AMI builds and short bootstrap actions. It is not the primary orchestration layer because robust retries, structured configuration, command logging, cleanup in failure paths, and JSON inspection become difficult to maintain as the test matrix grows.

### Why Not CloudFormation First

CloudFormation can create EKS and EC2 resources, but it does not simplify the imperative Kubernetes test loop. Terraform provides a more portable module ecosystem and a familiar plan, state, and destroy workflow for a future multi-cloud project. CloudFormation can remain an integration option for AWS-only customers.

## Tool Boundary

The first tool does not create an AMI or implement an Agent Sandbox snapshot API. It treats AMI creation as an input to Terraform and records a Kubernetes resource snapshot after a test starts.

The resource snapshot is diagnostic evidence. It is not a filesystem or VM checkpoint. A real sandbox checkpoint must be added later through a validated Agent Sandbox or Kata snapshot capability.

## Commands

The xolis_aws_lab.py tool uses a JSON configuration file and supports:

    doctor
    infra plan
    infra apply
    infra destroy
    node start
    node stop
    bootstrap
    cycle run

The infra commands delegate to Terraform. The node commands scale only the configured sandbox EKS managed node group. The cycle command runs start, test, snapshot, cleanup, and stop in order. Cleanup and node stop run even if the workload readiness check fails.

All mutating commands support a dry-run flag. Dry-run validates the JSON configuration but does not require the configured Terraform directory or Kubernetes manifests to exist, so an operator can preview a new configuration before deployment inputs are available. The tool does not pass automatic approval to Terraform; the operator must explicitly approve Terraform apply or destroy.

## Configuration

Use the example configuration at tools/xolis_aws_lab.example.json as a starting point. It defaults to the Tokyo Region (`ap-northeast-1`) and identifies the cluster, Region, sandbox node group, node selector, Terraform directory, bootstrap manifests, workload manifest, readiness command, and artifact directory.

The readiness command is intentionally configurable because Xolis has not yet fixed its Agent Sandbox manifest or readiness contract.

## Quick Start

This tool orchestrates an existing Terraform root and Kubernetes manifests; it does not yet supply the EKS Terraform modules or the Kata, Nydus, Agent Sandbox, and smoke-test manifests. Provide those inputs before attempting a real deployment. A dry run can be used before they exist.

### 1. Install Local Tools

Install the following tools and make them available on `PATH`:

- Python 3.11 or later.
- AWS CLI version 2.
- Terraform.
- `kubectl` compatible with the target EKS cluster version.
- Helm when the bootstrap procedure uses Helm.

Confirm the local installation:

    python3 --version
    aws --version
    terraform version
    kubectl version --client
    helm version

### 2. Prepare an Isolated AWS Account and Identity

Use a dedicated AWS account for the lab, or at minimum an isolated development VPC, in `ap-northeast-1`. Do not use the AWS root user or root access keys.

Use AWS IAM Identity Center (SSO) or short-lived credentials from an IAM role. Create a named AWS CLI profile for the lab and select it before invoking the tool:

    aws configure sso --profile xolis-lab
    aws sso login --profile xolis-lab
    export AWS_PROFILE=xolis-lab
    aws sts get-caller-identity

Long-lived access keys (`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`) are not required and are discouraged. If an IAM user must be used temporarily, use a dedicated least-privilege user, store its credentials only in the AWS CLI credential store or a secret manager, and never place them in the lab JSON file or Git.

The identity used by Terraform needs permissions appropriate to the supplied infrastructure modules, typically for VPC, EC2, IAM, EKS, ECR, CloudWatch, and the configured Terraform state backend. The identity used for test cycles needs at least permission to describe the EKS cluster and node group and to update the dedicated node group's scaling configuration. Grant the authenticated IAM principal Kubernetes access to apply, get, and delete the configured bootstrap and test resources. Configure these permissions through IAM policies and EKS access entries or Kubernetes RBAC before the first test.

### 3. Provide Lab Inputs

Before a non-dry-run command, prepare all of the following:

- A Terraform root that creates or references the target VPC, EKS cluster, and a dedicated sandbox managed node group. Configure a remote, locked Terraform state backend for shared use.
- Bootstrap manifests that install and configure the required sandbox runtime components.
- A test workload manifest and a readiness command that reflects its actual readiness contract.
- A sandbox node selector that matches nodes in the dedicated node group. Do not use a shared or production node group.

Copy the example configuration, then replace every placeholder with these paths and resource names:

    cp tools/xolis_aws_lab.example.json tools/xolis_aws_lab.json

### 4. Validate and Run

Preview the full command sequence first. Dry-run does not contact AWS or require the configured paths to exist:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json --dry-run cycle run

After the Terraform root and manifests are available, validate the local tools and AWS identity, review the Terraform plan, and explicitly approve only the intended changes:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json doctor
    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json infra plan
    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json infra apply
    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json cycle run

The cycle removes its test resources and stops the sandbox node group even if its readiness check fails. Destroying the infrastructure remains an explicit operator action:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json infra destroy

## Required Local Dependencies

- Python 3.11 or later.
- Terraform.
- AWS CLI authenticated to the target account.
- kubectl configured by the tool from the target EKS cluster.
- Helm when a bootstrap manifest or installation process requires it.

## Safety Rules

- Use a dedicated AWS account or an isolated development VPC for the first tests.
- Terraform state must be remote and locked before shared use.
- The sandbox node group must be dedicated to Xolis and have a maximum size appropriate for the test.
- Do not use the stop command against a shared or production node group.
- Review Terraform plans before apply and destroy.
- Store test logs and snapshots outside of Git.
- Test destructive cleanup in an isolated account before using the tool for cost-sensitive environments.

## Deferred Work

The first version intentionally defers:

- Terraform modules for the full EKS environment.
- AMI image build automation.
- Installation of Kata, Nydus, and Agent Sandbox from version-pinned packages.
- Agent Sandbox API-level snapshots.
- Concurrent test scheduling and distributed test reporting.
- CloudWatch log retrieval and cost reporting.

## References

- [Terraform plan](https://developer.hashicorp.com/terraform/cli/commands/plan)
- [Terraform destroy](https://developer.hashicorp.com/terraform/cli/commands/destroy)
- [Terraform state](https://developer.hashicorp.com/terraform/cli/state)
- [Amazon EKS launch templates](https://docs.aws.amazon.com/eks/latest/userguide/launch-templates.html)
- [Amazon EKS managed and self-managed node groups](https://docs.aws.amazon.com/eks/latest/userguide/ml-node-groups.html)
- [AWS CLI EKS node group waiter](https://docs.aws.amazon.com/cli/latest/reference/eks/wait/nodegroup-deleted.html)
