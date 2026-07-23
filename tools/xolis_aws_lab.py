#!/usr/bin/env python3
"""Run repeatable Xolis minimal-deployment checks on Amazon EKS."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


REQUIRED_EXECUTABLES = ("aws", "kubectl", "tofu")


@dataclass(frozen=True)
class LabConfig:
    region: str
    cluster_name: str
    sandbox_nodegroup: str
    sandbox_node_selector: str
    sandbox_min_size: int
    sandbox_desired_size: int
    sandbox_max_size: int
    tofu_directory: Path
    bootstrap_manifests: tuple[Path, ...]
    test_manifest: Path
    ready_command: tuple[str, ...]
    artifact_directory: Path
    node_ready_timeout_seconds: int
    node_stop_timeout_seconds: int

    @classmethod
    def load(cls, path: Path) -> "LabConfig":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError(f"Configuration file does not exist: {path}") from error
        except json.JSONDecodeError as error:
            raise ValueError(f"Configuration is not valid JSON: {error}") from error

        required = (
            "region",
            "cluster_name",
            "sandbox_nodegroup",
            "sandbox_node_selector",
            "tofu_directory",
            "test_manifest",
            "ready_command",
            "artifact_directory",
        )
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise ValueError(f"Configuration is missing required fields: {', '.join(missing)}")

        base = path.parent
        config = cls(
            region=str(data["region"]),
            cluster_name=str(data["cluster_name"]),
            sandbox_nodegroup=str(data["sandbox_nodegroup"]),
            sandbox_node_selector=str(data["sandbox_node_selector"]),
            sandbox_min_size=int(data.get("sandbox_min_size", 0)),
            sandbox_desired_size=int(data.get("sandbox_desired_size", 1)),
            sandbox_max_size=int(data.get("sandbox_max_size", 1)),
            tofu_directory=(base / data["tofu_directory"]).resolve(),
            bootstrap_manifests=tuple((base / item).resolve() for item in data.get("bootstrap_manifests", [])),
            test_manifest=(base / data["test_manifest"]).resolve(),
            ready_command=tuple(str(item) for item in data["ready_command"]),
            artifact_directory=(base / data["artifact_directory"]).resolve(),
            node_ready_timeout_seconds=int(data.get("node_ready_timeout_seconds", 900)),
            node_stop_timeout_seconds=int(data.get("node_stop_timeout_seconds", 900)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not 0 <= self.sandbox_min_size <= self.sandbox_desired_size <= self.sandbox_max_size:
            raise ValueError("Sandbox scaling values must satisfy 0 <= min <= desired <= max.")
        if not self.ready_command:
            raise ValueError("ready_command must not be empty.")


class CommandRunner:
    def __init__(self, dry_run: bool, log_file: Path) -> None:
        self.dry_run = dry_run
        self.log_file = log_file

    def run(self, command: Sequence[str], *, capture_output: bool = False) -> str:
        rendered = " ".join(command)
        self._log(f"$ {rendered}")
        if self.dry_run:
            print(f"DRY RUN: {rendered}")
            return ""
        result = subprocess.run(
            list(command),
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.STDOUT if capture_output else None,
        )
        if capture_output and result.stdout:
            self._log(result.stdout.rstrip())
        return result.stdout or ""

    def _log(self, message: str) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as output:
            output.write(message + "\n")


class AwsLab:
    def __init__(self, config: LabConfig, dry_run: bool) -> None:
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.config = config
        self.run_directory = config.artifact_directory / timestamp
        self.runner = CommandRunner(dry_run, self.run_directory / "commands.log")

    def doctor(self) -> None:
        missing = [name for name in REQUIRED_EXECUTABLES if shutil.which(name) is None]
        if missing:
            raise RuntimeError(f"Missing required executables: {', '.join(missing)}")
        self.runner.run(
            (
                "aws",
                "sts",
                "get-caller-identity",
                "--region",
                self.config.region,
                "--output",
                "json",
            ),
            capture_output=True,
        )
        self.runner.run(("tofu", "-chdir=" + str(self.config.tofu_directory), "version"))
        print("Dependency and AWS credential checks completed.")

    def infra(self, action: str) -> None:
        directory = self.config.tofu_directory
        if not self.runner.dry_run and not directory.is_dir():
            raise RuntimeError(f"OpenTofu directory does not exist: {directory}")
        prefix = ("tofu", "-chdir=" + str(directory))
        self.runner.run(prefix + ("init",))
        if action == "plan":
            self.runner.run(prefix + ("plan",))
        elif action == "apply":
            self.runner.run(prefix + ("apply",))
        elif action == "destroy":
            self.runner.run(prefix + ("destroy",))
        else:
            raise ValueError(f"Unsupported infrastructure action: {action}")

    def bootstrap(self) -> None:
        self.update_kubeconfig()
        for manifest in self.config.bootstrap_manifests:
            self.require_file(manifest)
            self.runner.run(("kubectl", "apply", "-f", str(manifest)))

    def start_node(self) -> None:
        self.update_kubeconfig()
        self.scale_nodegroup(self.config.sandbox_min_size, self.config.sandbox_desired_size, self.config.sandbox_max_size)
        self.wait_for_nodes(expected_at_least=1, timeout_seconds=self.config.node_ready_timeout_seconds)

    def stop_node(self) -> None:
        self.update_kubeconfig()
        self.scale_nodegroup(0, 0, self.config.sandbox_max_size)
        self.wait_for_nodes(expected_at_least=0, timeout_seconds=self.config.node_stop_timeout_seconds)

    def cycle(self) -> None:
        self.start_node()
        try:
            self.bootstrap()
            self.require_file(self.config.test_manifest)
            self.runner.run(("kubectl", "apply", "-f", str(self.config.test_manifest)))
            self.runner.run(self.config.ready_command)
            self.snapshot_resources()
        finally:
            self.cleanup_test_resources()
            self.stop_node()

    def update_kubeconfig(self) -> None:
        self.runner.run(
            (
                "aws",
                "eks",
                "update-kubeconfig",
                "--region",
                self.config.region,
                "--name",
                self.config.cluster_name,
            )
        )

    def scale_nodegroup(self, minimum: int, desired: int, maximum: int) -> None:
        scaling = f"minSize={minimum},desiredSize={desired},maxSize={maximum}"
        self.runner.run(
            (
                "aws",
                "eks",
                "update-nodegroup-config",
                "--region",
                self.config.region,
                "--cluster-name",
                self.config.cluster_name,
                "--nodegroup-name",
                self.config.sandbox_nodegroup,
                "--scaling-config",
                scaling,
            )
        )
        self.runner.run(
            (
                "aws",
                "eks",
                "wait",
                "nodegroup-active",
                "--region",
                self.config.region,
                "--cluster-name",
                self.config.cluster_name,
                "--nodegroup-name",
                self.config.sandbox_nodegroup,
            )
        )

    def wait_for_nodes(self, expected_at_least: int, timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            output = self.runner.run(
                ("kubectl", "get", "nodes", "-l", self.config.sandbox_node_selector, "-o", "json"),
                capture_output=True,
            )
            if self.runner.dry_run:
                return
            nodes = json.loads(output).get("items", [])
            ready = sum(self.node_is_ready(node) for node in nodes)
            if expected_at_least == 0 and not nodes:
                return
            if expected_at_least > 0 and ready >= expected_at_least:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for sandbox nodes. Expected {expected_at_least}, found {ready} Ready nodes."
                )
            time.sleep(10)

    def snapshot_resources(self) -> None:
        output = self.runner.run(
            ("kubectl", "get", "-f", str(self.config.test_manifest), "-o", "yaml"),
            capture_output=True,
        )
        if not self.runner.dry_run:
            snapshot = self.run_directory / "resource-snapshot.yaml"
            snapshot.write_text(output, encoding="utf-8")
            print(f"Wrote Kubernetes resource snapshot: {snapshot}")

    def cleanup_test_resources(self) -> None:
        if self.config.test_manifest.is_file():
            try:
                self.runner.run(("kubectl", "delete", "-f", str(self.config.test_manifest), "--wait=true"))
            except subprocess.CalledProcessError as error:
                self.runner._log(f"Cleanup failed: {error}")
                print("WARNING: Test resource cleanup failed. Inspect the command log.", file=sys.stderr)

    @staticmethod
    def node_is_ready(node: dict[str, Any]) -> bool:
        conditions = node.get("status", {}).get("conditions", [])
        return any(condition.get("type") == "Ready" and condition.get("status") == "True" for condition in conditions)

    def require_file(self, path: Path) -> None:
        if self.runner.dry_run:
            return
        if not path.is_file():
            raise RuntimeError(f"Required manifest does not exist: {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Xolis AWS minimal-deployment checks.")
    parser.add_argument("--config", type=Path, required=True, help="Path to the lab JSON configuration.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check local dependencies and AWS credentials.")
    infra = commands.add_parser("infra", help="Delegate persistent infrastructure actions to OpenTofu.")
    infra.add_argument("action", choices=("plan", "apply", "destroy"))
    node = commands.add_parser("node", help="Scale the dedicated sandbox node group.")
    node.add_argument("action", choices=("start", "stop"))
    commands.add_parser("bootstrap", help="Apply configured node bootstrap manifests.")
    cycle = commands.add_parser("cycle", help="Run a disposable sandbox workload test.")
    cycle.add_argument("action", choices=("run",))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        lab = AwsLab(LabConfig.load(args.config), args.dry_run)
        if args.command == "doctor":
            lab.doctor()
        elif args.command == "infra":
            lab.infra(args.action)
        elif args.command == "node":
            if args.action == "start":
                lab.start_node()
            else:
                lab.stop_node()
        elif args.command == "bootstrap":
            lab.bootstrap()
        elif args.command == "cycle":
            lab.cycle()
        return 0
    except (RuntimeError, ValueError, TimeoutError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
