#!/usr/bin/env python3
"""Inventory Xolis AWS resources and estimate their current storage cost."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Sequence


BYTES_PER_GIB = 1024**3
HOURS_PER_YEAR = 365 * 24
MONTHS_PER_YEAR = 12


class AwsCommandError(RuntimeError):
    """An AWS CLI command failed."""


class AwsCli:
    def __init__(self, profile: str, region: str) -> None:
        self.profile = profile
        self.region = region

    def command(self, arguments: Sequence[str]) -> list[str]:
        command = ["aws"]
        if self.profile:
            command.extend(("--profile", self.profile))
        if self.region:
            command.extend(("--region", self.region))
        command.extend(arguments)
        return command

    def run_json(self, arguments: Sequence[str]) -> dict[str, Any]:
        command = self.command([*arguments, "--output", "json"])
        environment = {**os.environ, "AWS_PAGER": ""}
        result = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            env=environment,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise AwsCommandError(f"{' '.join(command)} failed: {detail}")
        value = json.loads(result.stdout) if result.stdout.strip() else {}
        if not isinstance(value, dict):
            raise AwsCommandError(f"{' '.join(command)} did not return a JSON object")
        return value

    def login(self) -> None:
        command = ["aws", "sso", "login"]
        if self.profile:
            command.extend(("--profile", self.profile))
        environment = {**os.environ, "AWS_PAGER": ""}
        result = subprocess.run(command, check=False, env=environment)
        if result.returncode != 0:
            raise AwsCommandError(f"{' '.join(command)} failed")


@dataclass(frozen=True)
class StoragePrices:
    snapshot_gib_month: float
    ecr_gib_month: float
    s3_gib_month: float


def require_aws_cli() -> None:
    if shutil.which("aws") is None:
        raise RuntimeError("aws CLI is not installed or is not on PATH")


def authenticate(aws: AwsCli, login_mode: str) -> dict[str, Any]:
    if login_mode == "always":
        aws.login()
    try:
        return aws.run_json(["sts", "get-caller-identity"])
    except AwsCommandError:
        if login_mode != "auto":
            raise
        print("AWS authentication failed; starting AWS SSO login", file=sys.stderr)
        aws.login()
        return aws.run_json(["sts", "get-caller-identity"])


def flatten_instances(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]


def snapshot_allocated_bytes(aws: AwsCli, snapshot_id: str) -> int:
    token: str | None = None
    block_size: int | None = None
    block_count = 0
    while True:
        arguments = [
            "ebs",
            "list-snapshot-blocks",
            "--snapshot-id",
            snapshot_id,
            "--max-results",
            "10000",
            "--no-paginate",
        ]
        if token:
            arguments.extend(("--next-token", token))
        response = aws.run_json(arguments)
        current_block_size = int(response.get("BlockSize", 0))
        if current_block_size <= 0:
            raise AwsCommandError(f"snapshot {snapshot_id} returned no block size")
        if block_size is not None and current_block_size != block_size:
            raise AwsCommandError(f"snapshot {snapshot_id} changed block size between pages")
        block_size = current_block_size
        block_count += len(response.get("Blocks", []))
        token = response.get("NextToken")
        if not token:
            break
    return block_count * (block_size or 0)


def price_per_unit(
    response: dict[str, Any], unit: str, usage_type_suffix: str | None = None
) -> float:
    candidates: list[float] = []
    for encoded in response.get("PriceList", []):
        product = json.loads(encoded) if isinstance(encoded, str) else encoded
        usage_type = product.get("product", {}).get("attributes", {}).get("usagetype", "")
        if usage_type_suffix and not usage_type.endswith(usage_type_suffix):
            continue
        for term in product.get("terms", {}).get("OnDemand", {}).values():
            for dimension in term.get("priceDimensions", {}).values():
                if dimension.get("unit") != unit:
                    continue
                if dimension.get("beginRange", "0") != "0":
                    continue
                candidates.append(float(dimension["pricePerUnit"]["USD"]))
    if not candidates:
        raise AwsCommandError(f"AWS Pricing returned no {unit} price")
    return min(candidates)


def get_storage_prices(pricing: AwsCli, region: str) -> StoragePrices:
    common = ["Type=TERM_MATCH,Field=regionCode,Value=" + region]
    snapshot = pricing.run_json(
        [
            "pricing",
            "get-products",
            "--service-code",
            "AmazonEC2",
            "--filters",
            *common,
            "Type=TERM_MATCH,Field=productFamily,Value=Storage Snapshot",
            "--max-results",
            "100",
        ]
    )
    ecr = pricing.run_json(
        [
            "pricing",
            "get-products",
            "--service-code",
            "AmazonECR",
            "--filters",
            *common,
            "Type=TERM_MATCH,Field=productFamily,Value=EC2 Container Registry",
            "--max-results",
            "100",
        ]
    )
    s3 = pricing.run_json(
        [
            "pricing",
            "get-products",
            "--service-code",
            "AmazonS3",
            "--filters",
            *common,
            "Type=TERM_MATCH,Field=productFamily,Value=Storage",
            "Type=TERM_MATCH,Field=volumeType,Value=Standard",
            "Type=TERM_MATCH,Field=storageClass,Value=General Purpose",
            "--max-results",
            "100",
        ]
    )
    return StoragePrices(
        snapshot_gib_month=price_per_unit(
            snapshot, "GB-Mo", usage_type_suffix="EBS:SnapshotUsage"
        ),
        ecr_gib_month=price_per_unit(ecr, "GB-Mo"),
        s3_gib_month=price_per_unit(s3, "GB-Mo"),
    )


def monthly_and_daily_cost(size_bytes: int, gib_month_price: float) -> tuple[float, float]:
    monthly = size_bytes / BYTES_PER_GIB * gib_month_price
    daily = monthly * MONTHS_PER_YEAR / 365
    return monthly, daily


def collect_active_resources(aws: AwsCli) -> dict[str, Any]:
    clusters = aws.run_json(["eks", "list-clusters"]).get("clusters", [])
    instances = flatten_instances(
        aws.run_json(
            [
                "ec2",
                "describe-instances",
                "--filters",
                "Name=instance-state-name,Values=pending,running,stopping,stopped",
            ]
        )
    )
    volumes = aws.run_json(["ec2", "describe-volumes"]).get("Volumes", [])
    nat_gateways = aws.run_json(
        [
            "ec2",
            "describe-nat-gateways",
            "--filter",
            "Name=state,Values=pending,available",
        ]
    ).get("NatGateways", [])
    addresses = aws.run_json(["ec2", "describe-addresses"]).get("Addresses", [])
    load_balancers = aws.run_json(["elbv2", "describe-load-balancers"]).get(
        "LoadBalancers", []
    )
    databases = aws.run_json(["rds", "describe-db-instances"]).get("DBInstances", [])
    filesystems = aws.run_json(["efs", "describe-file-systems"]).get(
        "FileSystems", []
    )
    fsx_filesystems = aws.run_json(["fsx", "describe-file-systems"]).get(
        "FileSystems", []
    )
    vpc_endpoints = aws.run_json(["ec2", "describe-vpc-endpoints"]).get(
        "VpcEndpoints", []
    )
    hosted_zones = aws.run_json(["route53", "list-hosted-zones"]).get(
        "HostedZones", []
    )
    log_groups = aws.run_json(["logs", "describe-log-groups"]).get("logGroups", [])
    secrets = aws.run_json(["secretsmanager", "list-secrets"]).get("SecretList", [])
    return {
        "eks_clusters": clusters,
        "ec2_instances": [
            {
                "id": item.get("InstanceId"),
                "state": item.get("State", {}).get("Name"),
                "type": item.get("InstanceType"),
            }
            for item in instances
        ],
        "ebs_volumes": [
            {
                "id": item.get("VolumeId"),
                "state": item.get("State"),
                "size_gib": item.get("Size"),
                "type": item.get("VolumeType"),
            }
            for item in volumes
        ],
        "nat_gateways": [item.get("NatGatewayId") for item in nat_gateways],
        "elastic_ips": [item.get("AllocationId") for item in addresses],
        "load_balancers": [item.get("LoadBalancerArn") for item in load_balancers],
        "rds_instances": [item.get("DBInstanceIdentifier") for item in databases],
        "efs_filesystems": [item.get("FileSystemId") for item in filesystems],
        "fsx_filesystems": [item.get("FileSystemId") for item in fsx_filesystems],
        "vpc_endpoints": [item.get("VpcEndpointId") for item in vpc_endpoints],
        "route53_hosted_zones": [item.get("Id") for item in hosted_zones],
        "cloudwatch_log_groups": [item.get("logGroupName") for item in log_groups],
        "secrets_manager_secrets": [item.get("Name") for item in secrets],
    }


def collect_snapshots(aws: AwsCli, prefix: str) -> dict[str, Any]:
    response = aws.run_json(
        [
            "ec2",
            "describe-images",
            "--owners",
            "self",
            "--filters",
            f"Name=name,Values={prefix}*",
        ]
    )
    images = response.get("Images", [])
    snapshot_ids = sorted(
        {
            mapping["Ebs"]["SnapshotId"]
            for image in images
            for mapping in image.get("BlockDeviceMappings", [])
            if mapping.get("Ebs", {}).get("SnapshotId")
        }
    )
    snapshots = []
    for snapshot_id in snapshot_ids:
        snapshots.append(
            {
                "id": snapshot_id,
                "allocated_bytes": snapshot_allocated_bytes(aws, snapshot_id),
                "image_ids": [
                    image.get("ImageId")
                    for image in images
                    if snapshot_id
                    in {
                        mapping.get("Ebs", {}).get("SnapshotId")
                        for mapping in image.get("BlockDeviceMappings", [])
                    }
                ],
            }
        )
    return {
        "images": [
            {"id": image.get("ImageId"), "name": image.get("Name")}
            for image in images
        ],
        "snapshots": snapshots,
        "allocated_bytes": sum(item["allocated_bytes"] for item in snapshots),
    }


def collect_ecr(aws: AwsCli, prefix: str) -> dict[str, Any]:
    repositories = aws.run_json(["ecr", "describe-repositories"]).get(
        "repositories", []
    )
    names = sorted(
        item["repositoryName"]
        for item in repositories
        if item.get("repositoryName", "").startswith(prefix + "/")
    )
    details = []
    for name in names:
        images = aws.run_json(
            ["ecr", "describe-images", "--repository-name", name]
        ).get("imageDetails", [])
        details.append(
            {
                "name": name,
                "image_count": len(images),
                "logical_bytes": sum(
                    int(item.get("imageSizeInBytes", 0)) for item in images
                ),
            }
        )
    return {
        "repositories": details,
        "repository_count": len(details),
        "image_count": sum(item["image_count"] for item in details),
        "logical_bytes": sum(item["logical_bytes"] for item in details),
    }


def collect_s3(aws: AwsCli, prefix: str) -> dict[str, Any]:
    buckets = aws.run_json(["s3api", "list-buckets"]).get("Buckets", [])
    names = sorted(
        item["Name"] for item in buckets if item.get("Name", "").startswith(prefix)
    )
    details = []
    for name in names:
        versions = aws.run_json(
            ["s3api", "list-object-versions", "--bucket", name]
        ).get("Versions", [])
        uploads = aws.run_json(
            ["s3api", "list-multipart-uploads", "--bucket", name]
        ).get("Uploads") or []
        multipart_bytes = 0
        for upload in uploads:
            parts = aws.run_json(
                [
                    "s3api",
                    "list-parts",
                    "--bucket",
                    name,
                    "--key",
                    upload["Key"],
                    "--upload-id",
                    upload["UploadId"],
                ]
            ).get("Parts", [])
            multipart_bytes += sum(int(part.get("Size", 0)) for part in parts)
        version_bytes = sum(int(version.get("Size", 0)) for version in versions)
        details.append(
            {
                "name": name,
                "version_count": len(versions),
                "version_bytes": version_bytes,
                "multipart_upload_count": len(uploads),
                "multipart_bytes": multipart_bytes,
                "total_bytes": version_bytes + multipart_bytes,
            }
        )
    return {
        "buckets": details,
        "bucket_count": len(details),
        "version_count": sum(item["version_count"] for item in details),
        "multipart_upload_count": sum(
            item["multipart_upload_count"] for item in details
        ),
        "total_bytes": sum(item["total_bytes"] for item in details),
    }


def collect_cost_history(aws: AwsCli, days: int) -> list[dict[str, Any]]:
    today = dt.datetime.now(dt.timezone.utc).date()
    response = aws.run_json(
        [
            "ce",
            "get-cost-and-usage",
            "--time-period",
            f"Start={today - dt.timedelta(days=days)},End={today + dt.timedelta(days=1)}",
            "--granularity",
            "DAILY",
            "--metrics",
            "UnblendedCost",
            "--group-by",
            "Type=DIMENSION,Key=SERVICE",
        ]
    )
    history = []
    for period in response.get("ResultsByTime", []):
        services = {
            group["Keys"][0]: float(group["Metrics"]["UnblendedCost"]["Amount"])
            for group in period.get("Groups", [])
        }
        history.append(
            {
                "date": period["TimePeriod"]["Start"],
                "estimated": bool(period.get("Estimated")),
                "total_usd": sum(services.values()),
                "services": services,
            }
        )
    return history


def build_report(
    aws: AwsCli,
    pricing: AwsCli,
    identity: dict[str, Any],
    prefix: str,
    history_days: int,
) -> dict[str, Any]:
    active = collect_active_resources(aws)
    snapshots = collect_snapshots(aws, prefix)
    ecr = collect_ecr(aws, prefix)
    s3 = collect_s3(aws, prefix)
    prices = get_storage_prices(pricing, aws.region)

    snapshot_monthly, snapshot_daily = monthly_and_daily_cost(
        snapshots["allocated_bytes"], prices.snapshot_gib_month
    )
    ecr_monthly, ecr_daily = monthly_and_daily_cost(
        ecr["logical_bytes"], prices.ecr_gib_month
    )
    s3_monthly, s3_daily = monthly_and_daily_cost(
        s3["total_bytes"], prices.s3_gib_month
    )
    return {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "account": identity.get("Account"),
        "principal_arn": identity.get("Arn"),
        "profile": aws.profile,
        "region": aws.region,
        "resource_prefix": prefix,
        "active_resources": active,
        "storage": {
            "snapshots": snapshots,
            "ecr": ecr,
            "s3": s3,
        },
        "prices_usd_per_gib_month": {
            "ebs_snapshot": prices.snapshot_gib_month,
            "ecr": prices.ecr_gib_month,
            "s3_standard": prices.s3_gib_month,
        },
        "estimated_idle_storage_cost": {
            "snapshot_monthly_usd": snapshot_monthly,
            "snapshot_daily_usd": snapshot_daily,
            "ecr_monthly_upper_bound_usd": ecr_monthly,
            "ecr_daily_upper_bound_usd": ecr_daily,
            "s3_monthly_usd": s3_monthly,
            "s3_daily_usd": s3_daily,
            "total_monthly_upper_bound_usd": snapshot_monthly
            + ecr_monthly
            + s3_monthly,
            "total_daily_upper_bound_usd": snapshot_daily + ecr_daily + s3_daily,
        },
        "recent_unblended_cost": collect_cost_history(aws, history_days),
        "not_estimated": [
            "compute, EKS, NAT, load balancer, RDS, EFS, FSx, VPC endpoint, "
            "Route 53, CloudWatch Logs, Secrets Manager, and EBS volume run rate",
            "network transfer and request charges",
            "ECR layer deduplication; the ECR estimate sums logical image sizes",
            "taxes, credits, Savings Plans, and Reserved Instance adjustments",
        ],
    }


def gib(value: int) -> float:
    return value / BYTES_PER_GIB


def print_report(report: dict[str, Any]) -> None:
    active = report["active_resources"]
    storage = report["storage"]
    cost = report["estimated_idle_storage_cost"]
    print(
        f"AWS account {report['account']} via profile {report['profile']} "
        f"in {report['region']}"
    )
    print("\nActive resources")
    for name, values in active.items():
        print(f"  {name}: {len(values)}")

    print("\nPersistent Xolis storage")
    print(
        f"  AMIs: {len(storage['snapshots']['images'])}; "
        f"snapshots: {len(storage['snapshots']['snapshots'])}; "
        f"allocated: {gib(storage['snapshots']['allocated_bytes']):.3f} GiB"
    )
    print(
        f"  ECR: {storage['ecr']['repository_count']} repositories, "
        f"{storage['ecr']['image_count']} images, "
        f"logical size: {gib(storage['ecr']['logical_bytes']):.3f} GiB"
    )
    print(
        f"  S3: {storage['s3']['bucket_count']} buckets, "
        f"{storage['s3']['version_count']} object versions, "
        f"{storage['s3']['multipart_upload_count']} incomplete multipart uploads, "
        f"size: {gib(storage['s3']['total_bytes']):.3f} GiB"
    )

    print("\nEstimated idle storage cost")
    print(
        f"  EBS snapshots: ${cost['snapshot_daily_usd']:.4f}/day, "
        f"${cost['snapshot_monthly_usd']:.2f}/month"
    )
    print(
        f"  ECR upper bound: ${cost['ecr_daily_upper_bound_usd']:.4f}/day, "
        f"${cost['ecr_monthly_upper_bound_usd']:.2f}/month"
    )
    print(
        f"  S3 Standard: ${cost['s3_daily_usd']:.4f}/day, "
        f"${cost['s3_monthly_usd']:.2f}/month"
    )
    print(
        f"  Total upper bound: ${cost['total_daily_upper_bound_usd']:.4f}/day, "
        f"${cost['total_monthly_upper_bound_usd']:.2f}/month"
    )

    print("\nRecent Cost Explorer unblended cost")
    for item in report["recent_unblended_cost"]:
        marker = " estimated" if item["estimated"] else ""
        print(f"  {item['date']}: ${item['total_usd']:.4f}{marker}")

    if any(active.values()):
        print(
            "\nWARNING: active resources exist. Their current run rate is not included "
            "in the idle storage estimate; use the Cost Explorer history above."
        )
    print("\nCost exclusions")
    for item in report["not_estimated"]:
        print(f"  - {item}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="xolis-lab")
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument("--resource-prefix", default="xolis")
    parser.add_argument("--history-days", type=int, default=7)
    parser.add_argument(
        "--sso-login",
        choices=("auto", "always", "never"),
        default="auto",
        help="Start AWS SSO login automatically when credentials are unavailable",
    )
    parser.add_argument("--output", choices=("text", "json"), default="text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.history_days < 1 or args.history_days > 90:
        raise ValueError("--history-days must be between 1 and 90")
    require_aws_cli()
    aws = AwsCli(args.profile, args.region)
    pricing = AwsCli(args.profile, "us-east-1")
    identity = authenticate(aws, args.sso_login)
    report = build_report(aws, pricing, identity, args.resource_prefix, args.history_days)
    if args.output == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AwsCommandError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
