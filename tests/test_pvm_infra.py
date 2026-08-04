from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PvmInfrastructureTests(unittest.TestCase):
    def test_base_deployment_converges_api_to_the_native_profile(self) -> None:
        deployment = (ROOT / "deploy/xolis/api.yaml").read_text(encoding="utf-8")
        self.assertIn("name: XOLIS_PROFILE\n              value: python-basic-v1", deployment)
        self.assertIn(
            "name: XOLIS_WARM_POOL\n              value: python-basic-v1-pool",
            deployment,
        )

    def test_runtime_profiles_restart_services_after_node_loss(self) -> None:
        profiles = (
            ROOT / "deploy/xolis/python-profile.yaml",
            ROOT / "deploy/xolis/python-profile-nydus.yaml",
            ROOT / "deploy/xolis/python-profile-pvm.yaml",
            ROOT / "deploy/xolis/hermes-profile.yaml.in",
        )
        for profile in profiles:
            with self.subTest(profile=profile.name):
                contents = profile.read_text(encoding="utf-8")
                self.assertIn("restartPolicy: Always", contents)
                self.assertNotIn("restartPolicy: OnFailure", contents)

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
        self.assertIn('imports = ["/etc/containerd/conf.d/*.toml"]', pvm_launch_template)
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
