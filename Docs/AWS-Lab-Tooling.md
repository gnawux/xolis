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
