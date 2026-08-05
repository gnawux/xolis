---
name: xolis-aws-cost
description: Audit current Xolis AWS resources and costs by running the repository's read-only cost tool. Use when the user asks about AWS spend, daily or monthly cost, chargeable resources, resource cleanup verification, idle infrastructure cost, or what remains in the xolis-lab AWS account.
---

# Xolis AWS Cost

Run a fresh AWS inventory and cost audit with the checked-in tool. Do not reuse
figures from an earlier conversation when the user asks about current state.

## Audit

1. Work from the repository root and verify that `tools/xolis_aws_cost.py`
   exists.
2. Run:

       python3 tools/xolis_aws_cost.py --output json

   Keep the defaults unless the user explicitly requests another profile,
   Region, prefix, or history window. The defaults are profile `xolis-lab`,
   Region `ap-northeast-1`, prefix `xolis`, seven days of history, and automatic
   SSO login after an authentication failure.
3. If AWS SSO is required, let the tool start `aws sso login` and tell the user
   to complete the browser login if interaction is needed. Then allow the tool
   to continue; do not replace SSO with long-lived access keys.
4. Treat the command as read-only. Do not create, stop, delete, or resize any
   resource as part of a cost audit.

## Report

Summarize:

- AWS account, profile, Region, and audit time.
- Counts of active or potentially chargeable resources, calling out any
  nonzero category and its identifiers when available.
- Retained AMIs and snapshots, ECR images, S3 versions, and their sizes.
- Estimated idle-storage cost per day and month.
- Recent Cost Explorer totals, including whether AWS marks them estimated.

State the limitations when material: the estimate covers EBS snapshot, ECR,
and S3 storage; ECR logical sizes can double-count shared layers; active
compute, network, requests, taxes, credits, and discounts are not priced by the
tool. If active resources exist, do not present the idle-storage estimate as
the account's total current run rate.

On failure, report the specific AWS service or permission that failed and the
relevant error. Do not silently substitute remembered values.
