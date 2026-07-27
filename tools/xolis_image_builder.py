#!/usr/bin/env python3
"""Build and publish Xolis AMD64 images on a temporary AWS EC2 instance."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


REPOSITORIES = {
    "xolis-api": ("xolis/xolis-api", "image/xolis-api/Dockerfile"),
    "xolis-runtime-python": (
        "xolis/xolis-runtime-python",
        "image/xolis-runtime-python/Dockerfile",
    ),
    "xolis-runtime-hermes": (
        "xolis/xolis-runtime-hermes",
        "image/xolis-runtime-hermes/Dockerfile",
    ),
    "sandbox-router-go": ("xolis/sandbox-router", "image/sandbox-router/Dockerfile"),
}
NYDUS_VERSION = "2.4.4"
NYDUS_ARCHIVE_SHA256 = "e95a0d1984ef507c0cf4c7766a9cde8433215e1007556e03d4cc211725c68c59"
TERMINAL_COMMAND_STATES = {"Success", "Cancelled", "Failed", "TimedOut"}


@dataclass(frozen=True)
class BuilderConfig:
    profile: str
    region: str
    cluster_name: str
    source_bucket: str
    instance_type: str
    repository_root: Path
    images: tuple[str, ...]
    nydus_images: tuple[str, ...]
    keep_instance: bool
    timeout_seconds: int


class AwsCommandError(RuntimeError):
    pass


class AwsCli:
    def __init__(self, profile: str, region: str) -> None:
        self.base = ["aws", "--profile", profile, "--region", region]

    def run(self, arguments: Sequence[str], *, json_output: bool = False) -> Any:
        command = [*self.base, *arguments]
        result = subprocess.run(command, check=False, text=True, capture_output=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise AwsCommandError(f"{' '.join(command)} failed: {detail}")
        if json_output:
            return json.loads(result.stdout)
        return result.stdout.strip()


def require_executables() -> None:
    missing = [name for name in ("aws", "git") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(f"required executables are missing: {', '.join(missing)}")


def build_commands(
    source_url: str,
    registry: str,
    tag: str,
    images: Sequence[str],
    nydus_images: Sequence[str],
) -> list[str]:
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", tag):
        raise ValueError("image tag contains unsupported characters")
    if not re.fullmatch(r"[0-9]+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com", registry):
        raise ValueError("registry is not a private Amazon ECR hostname")
    unknown = (set(images) | set(nydus_images)) - REPOSITORIES.keys()
    if unknown:
        raise ValueError(f"unknown image names: {', '.join(sorted(unknown))}")
    if not set(nydus_images).issubset(images):
        raise ValueError("Nydus images must also be selected as OCI images")

    commands = [
        "set -euo pipefail",
        "echo 'Installing the container build runtime'",
        "dnf install -q -y docker",
        "systemctl enable --now docker",
        "rm -rf /opt/xolis-build && mkdir -p /opt/xolis-build",
        "echo 'Downloading the committed source archive'",
        f"curl --fail --silent --show-error --location {shell_quote(source_url)} --output /opt/xolis-source.tar.gz",
        "tar -xzf /opt/xolis-source.tar.gz -C /opt/xolis-build",
        "cd /opt/xolis-build",
        f"aws ecr get-login-password | docker login --username AWS --password-stdin {registry} >/dev/null",
    ]
    for local_name in images:
        repository, dockerfile = REPOSITORIES[local_name]
        reference = f"{registry}/{repository}:{tag}"
        commands.extend(
            [
                f"echo 'Building {local_name}'",
                f"docker build --pull --file {dockerfile} --tag {local_name}:{tag} .",
                f"docker tag {local_name}:{tag} {reference}",
                f"echo 'Pushing {local_name}'",
                f"docker push --quiet {reference}",
            ]
        )
    if nydus_images:
        archive = f"nydus-static-v{NYDUS_VERSION}-linux-amd64.tgz"
        commands.extend(
            [
                "echo 'Installing the pinned Nydus conversion tools'",
                f"curl --fail --silent --show-error --location https://github.com/dragonflyoss/nydus/releases/download/v{NYDUS_VERSION}/{archive} --output /opt/{archive}",
                f"echo '{NYDUS_ARCHIVE_SHA256}  /opt/{archive}' | sha256sum --check --strict",
                f"tar -xzf /opt/{archive} -C /opt",
                "install -m 0755 /opt/nydus-static/nydus-image /usr/local/bin/nydus-image",
                "install -m 0755 /opt/nydus-static/nydusify /usr/local/bin/nydusify",
                "install -m 0755 /opt/nydus-static/nydusd /usr/local/bin/nydusd",
            ]
        )
        for local_name in nydus_images:
            repository, _ = REPOSITORIES[local_name]
            source = f"{registry}/{repository}:{tag}"
            target = f"{registry}/{repository}:{tag}-nydus"
            commands.extend(
                [
                    f"echo 'Converting {local_name} to Nydus format'",
                    f"nydusify convert --source {source} --target {target}",
                    f"echo 'Checking {local_name} Nydus root filesystem'",
                    f"nydusify check --source {source} --target {target}",
                ]
            )
    return commands


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


class ImageBuilder:
    def __init__(self, config: BuilderConfig) -> None:
        self.config = config
        self.aws = AwsCli(config.profile, config.region)
        self.instance_id: str | None = None
        self.source_uri: str | None = None

    def run(self) -> dict[str, str]:
        require_executables()
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.config.repository_root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        tag = f"{commit[:12]}-{dt.datetime.now(dt.UTC).strftime('%Y%m%d%H%M%S')}"

        try:
            source_url = self.upload_source(commit)
            cluster = self.aws.run(
                ["eks", "describe-cluster", "--name", self.config.cluster_name],
                json_output=True,
            )["cluster"]
            subnet_id = cluster["resourcesVpcConfig"]["subnetIds"][0]
            account_id = self.aws.run(
                ["sts", "get-caller-identity", "--query", "Account", "--output", "text"]
            )
            registry = f"{account_id}.dkr.ecr.{self.config.region}.amazonaws.com"
            security_group_id = self.lookup_security_group()
            ami_id = self.aws.run(
                [
                    "ssm",
                    "get-parameter",
                    "--name",
                    "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
                    "--query",
                    "Parameter.Value",
                    "--output",
                    "text",
                ]
            )
            self.instance_id = self.launch_instance(ami_id, subnet_id, security_group_id)
            self.wait_for_instance()
            command_id = self.send_build(source_url, registry, tag)
            self.wait_for_command(command_id)
            return self.image_references(tag)
        finally:
            self.cleanup()

    def upload_source(self, commit: str) -> str:
        key = f"xolis/image-builds/{commit}/{int(time.time())}/source.tar.gz"
        self.source_uri = f"s3://{self.config.source_bucket}/{key}"
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / "source.tar.gz"
            subprocess.run(
                ["git", "archive", "--format=tar.gz", f"--output={archive}", "HEAD"],
                cwd=self.config.repository_root,
                check=True,
            )
            self.aws.run(["s3", "cp", str(archive), self.source_uri, "--only-show-errors"])
        return self.aws.run(["s3", "presign", self.source_uri, "--expires-in", "7200"])

    def lookup_security_group(self) -> str:
        groups = self.aws.run(
            [
                "ec2",
                "describe-security-groups",
                "--filters",
                f"Name=group-name,Values={self.config.cluster_name}-image-builder",
            ],
            json_output=True,
        )["SecurityGroups"]
        if len(groups) != 1:
            raise RuntimeError("expected exactly one image-builder security group")
        return groups[0]["GroupId"]

    def launch_instance(self, ami_id: str, subnet_id: str, security_group_id: str) -> str:
        response = self.aws.run(
            [
                "ec2",
                "run-instances",
                "--image-id",
                ami_id,
                "--instance-type",
                self.config.instance_type,
                "--subnet-id",
                subnet_id,
                "--security-group-ids",
                security_group_id,
                "--associate-public-ip-address",
                "--iam-instance-profile",
                f"Name={self.config.cluster_name}-image-builder",
                "--metadata-options",
                "HttpTokens=required,HttpEndpoint=enabled",
                "--block-device-mappings",
                "DeviceName=/dev/xvda,Ebs={VolumeSize=50,VolumeType=gp3,DeleteOnTermination=true,Encrypted=true}",
                "--tag-specifications",
                (
                    "ResourceType=instance,Tags=["
                    f"{{Key=Name,Value={self.config.cluster_name}-image-builder}},"
                    "{Key=Purpose,Value=xolis-image-build}]"
                ),
            ],
            json_output=True,
        )
        return response["Instances"][0]["InstanceId"]

    def wait_for_instance(self) -> None:
        assert self.instance_id is not None
        self.aws.run(["ec2", "wait", "instance-status-ok", "--instance-ids", self.instance_id])
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            response = self.aws.run(
                [
                    "ssm",
                    "describe-instance-information",
                    "--filters",
                    f"Key=InstanceIds,Values={self.instance_id}",
                ],
                json_output=True,
            )
            if response["InstanceInformationList"]:
                return
            time.sleep(10)
        raise TimeoutError("image builder did not register with Systems Manager")

    def send_build(self, source_url: str, registry: str, tag: str) -> str:
        assert self.instance_id is not None
        response = self.aws.run(
            [
                "ssm",
                "send-command",
                "--instance-ids",
                self.instance_id,
                "--document-name",
                "AWS-RunShellScript",
                "--timeout-seconds",
                str(self.config.timeout_seconds),
                "--parameters",
                json.dumps(
                    {
                        "commands": build_commands(
                            source_url,
                            registry,
                            tag,
                            self.config.images,
                            self.config.nydus_images,
                        )
                    }
                ),
            ],
            json_output=True,
        )
        return response["Command"]["CommandId"]

    def wait_for_command(self, command_id: str) -> None:
        assert self.instance_id is not None
        deadline = time.monotonic() + self.config.timeout_seconds + 120
        while time.monotonic() < deadline:
            try:
                invocation = self.aws.run(
                    [
                        "ssm",
                        "get-command-invocation",
                        "--command-id",
                        command_id,
                        "--instance-id",
                        self.instance_id,
                    ],
                    json_output=True,
                )
            except AwsCommandError as error:
                if "InvocationDoesNotExist" not in str(error):
                    raise
                time.sleep(5)
                continue
            status = invocation["Status"]
            if status in TERMINAL_COMMAND_STATES:
                if invocation.get("StandardOutputContent"):
                    print(invocation["StandardOutputContent"], end="")
                if invocation.get("StandardErrorContent"):
                    print(invocation["StandardErrorContent"], file=sys.stderr, end="")
                if status != "Success":
                    raise RuntimeError(f"remote image build ended with status {status}")
                return
            time.sleep(15)
        raise TimeoutError("remote image build did not finish before the deadline")

    def image_references(self, tag: str) -> dict[str, str]:
        references = {}
        image_tags = [(name, tag) for name in self.config.images]
        image_tags.extend(
            (f"{name}-nydus", f"{tag}-nydus") for name in self.config.nydus_images
        )
        for output_name, image_tag in image_tags:
            repository_name = output_name.removesuffix("-nydus")
            repository, _ = REPOSITORIES[repository_name]
            image = self.aws.run(
                [
                    "ecr",
                    "describe-images",
                    "--repository-name",
                    repository,
                    "--image-ids",
                    f"imageTag={image_tag}",
                ],
                json_output=True,
            )["imageDetails"][0]
            registry = image["registryId"]
            references[output_name] = (
                f"{registry}.dkr.ecr.{self.config.region}.amazonaws.com/"
                f"{repository}@{image['imageDigest']}"
            )
        return references

    def cleanup(self) -> None:
        if self.instance_id and not self.config.keep_instance:
            self.aws.run(["ec2", "terminate-instances", "--instance-ids", self.instance_id])
            self.aws.run(
                ["ec2", "wait", "instance-terminated", "--instance-ids", self.instance_id]
            )
            state = self.aws.run(
                [
                    "ec2",
                    "describe-instances",
                    "--instance-ids",
                    self.instance_id,
                    "--query",
                    "Reservations[0].Instances[0].State.Name",
                    "--output",
                    "text",
                ]
            )
            if state != "terminated":
                raise RuntimeError(
                    f"image builder {self.instance_id} cleanup ended in state {state}"
                )
        if self.source_uri:
            self.aws.run(["s3", "rm", self.source_uri, "--only-show-errors"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="xolis-lab")
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument("--cluster-name", default="xolis-lab")
    parser.add_argument("--source-bucket", required=True)
    parser.add_argument("--instance-type", default="c6i.xlarge")
    parser.add_argument(
        "--image",
        action="append",
        choices=sorted(REPOSITORIES),
        dest="images",
        help="image to build; repeat for multiple images (default: all)",
    )
    parser.add_argument(
        "--nydus",
        action="append",
        choices=sorted(REPOSITORIES),
        dest="nydus_images",
        default=[],
        help="selected image to additionally convert to Nydus format",
    )
    parser.add_argument("--keep-instance", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    images = tuple(args.images or REPOSITORIES)
    nydus_images = tuple(args.nydus_images)
    if not set(nydus_images).issubset(images):
        print("error: every --nydus image must also be selected with --image", file=sys.stderr)
        return 2
    repository_root = Path(__file__).resolve().parents[1]
    config = BuilderConfig(
        profile=args.profile,
        region=args.region,
        cluster_name=args.cluster_name,
        source_bucket=args.source_bucket,
        instance_type=args.instance_type,
        repository_root=repository_root,
        images=images,
        nydus_images=nydus_images,
        keep_instance=args.keep_instance,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        references = ImageBuilder(config).run()
    except (AwsCommandError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"imageReferences": references}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
