from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PvmInfrastructureTests(unittest.TestCase):
    def test_pvm_pool_is_optional_isolated_and_does_not_request_nested_kvm(self) -> None:
        main = (ROOT / "infra/aws/minimal/main.tf").read_text(encoding="utf-8")
        variables = (ROOT / "infra/aws/minimal/variables.tf").read_text(
            encoding="utf-8"
        )
        pvm_launch_template = main.split(
            'resource "aws_launch_template" "pvm"', maxsplit=1
        )[1].split('resource "aws_autoscaling_group" "pvm"', maxsplit=1)[0]

        self.assertIn('variable "pvm_ami_id"', variables)
        self.assertIn("count = var.pvm_ami_id == null ? 0 : 1", pvm_launch_template)
        self.assertNotIn("nested_virtualization", pvm_launch_template)
        self.assertIn("xolis.io/virtualization=pvm", pvm_launch_template)
        self.assertIn("xolis.io/pvm-ready=true", pvm_launch_template)
        self.assertIn("xolis.io/pvm=true:NoSchedule", pvm_launch_template)

    def test_pvm_runtime_class_cannot_fall_back_to_native_kvm(self) -> None:
        runtime_class = (
            ROOT / "deploy/bootstrap/xolis-runtime-pvm.yaml"
        ).read_text(encoding="utf-8")
        profile = (ROOT / "deploy/xolis/python-profile-pvm.yaml").read_text(
            encoding="utf-8"
        )
        smoke = (ROOT / "deploy/tests/smoke-pvm-pod.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("handler: xolis-kata-pvm", runtime_class)
        self.assertIn('xolis.io/pvm-ready: "true"', runtime_class)
        self.assertIn("xolis.io/virtualization: pvm", runtime_class)
        self.assertIn("key: xolis.io/pvm", runtime_class)
        self.assertIn("runtimeClassName: xolis-kata-pvm", profile)
        self.assertIn("name: python-pvm-v1", profile)
        self.assertIn("runtimeClassName: xolis-kata-pvm", smoke)


if __name__ == "__main__":
    unittest.main()
