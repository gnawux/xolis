import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "xolis_aws_lab.py"
SPEC = importlib.util.spec_from_file_location("xolis_aws_lab", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class NodeReadyTests(unittest.TestCase):
    def test_ready_node_is_detected(self) -> None:
        node = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        self.assertTrue(MODULE.AwsLab.node_is_ready(node))

    def test_non_ready_node_is_rejected(self) -> None:
        node = {"status": {"conditions": [{"type": "Ready", "status": "False"}]}}
        self.assertFalse(MODULE.AwsLab.node_is_ready(node))

    def test_aws_instance_id_is_read_from_node_provider_id(self) -> None:
        node = {"spec": {"providerID": "aws:///ap-northeast-1c/i-0123456789"}}
        self.assertEqual(MODULE.AwsLab.node_instance_id(node), "i-0123456789")

    def test_non_aws_provider_id_is_not_accepted(self) -> None:
        node = {"spec": {"providerID": "kind://docker/xolis-worker"}}
        self.assertIsNone(MODULE.AwsLab.node_instance_id(node))


class DryRunTests(unittest.TestCase):
    def make_config(self, directory: Path) -> Path:
        config_path = directory / "lab.json"
        config_path.write_text(
            json.dumps(
                {
                    "region": "ap-northeast-1",
                    "cluster_name": "example-cluster",
                    "sandbox_autoscaling_group": "sandbox",
                    "sandbox_node_selector": "xolis.io/sandbox=true",
                    "tofu_directory": "missing-infra",
                    "bootstrap_manifests": ["missing-bootstrap.yaml"],
                    "test_manifest": "missing-test.yaml",
                    "ready_command": ["kubectl", "wait", "--for=condition=Ready", "pod/example"],
                    "artifact_directory": "artifacts",
                }
            ),
            encoding="utf-8",
        )
        return config_path

    def test_cycle_dry_run_does_not_require_deployment_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = MODULE.LabConfig.load(self.make_config(Path(temporary_directory)))
            lab = MODULE.AwsLab(config, dry_run=True)

            lab.cycle()

            command_log = next(config.artifact_directory.glob("*/commands.log"))
            log = command_log.read_text(encoding="utf-8")
            self.assertIn("kubectl apply -f", log)
            self.assertIn("missing-bootstrap.yaml", log)
            self.assertIn("missing-test.yaml", log)
            self.assertIn("kubectl delete -f", log)
            self.assertIn("aws autoscaling update-auto-scaling-group", log)
            self.assertIn("--auto-scaling-group-name sandbox", log)

    def test_infra_dry_run_does_not_require_opentofu_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = MODULE.LabConfig.load(self.make_config(Path(temporary_directory)))
            lab = MODULE.AwsLab(config, dry_run=True)

            lab.infra("plan")

            command_log = next(config.artifact_directory.glob("*/commands.log"))
            self.assertIn("tofu", command_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
