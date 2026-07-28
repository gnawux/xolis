import unittest
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).parents[1]


class AmiAssetTests(unittest.TestCase):
    def test_kata_handler_uses_an_explicit_dragonball_configuration(self) -> None:
        configuration = (ROOT / "image/aws/files/containerd-xolis-kata.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn('runtime_type = "io.containerd.kata.v2"', configuration)
        self.assertIn("configuration-xolis-dragonball.toml", configuration)

    def test_ami_installer_requires_dragonball_and_containerd_import(self) -> None:
        installer = (ROOT / "image/aws/scripts/install-runtime.sh").read_text(encoding="utf-8")
        self.assertIn("configuration-dragonball.toml", installer)
        self.assertIn("xolis-enable-containerd-import", installer)
        self.assertIn("containerd.service.d/10-xolis-runtime.conf", installer)
        self.assertIn("REUSE_EXISTING_KATA_RUNTIME", installer)
        self.assertIn("Reusing the validated Kata runtime", installer)

    def test_nydus_handler_is_opt_in(self) -> None:
        configuration = (ROOT / "image/aws/files/containerd-xolis-kata.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn("runtimes.xolis-kata]", configuration)
        self.assertIn("runtimes.xolis-kata-nydus]", configuration)
        self.assertIn('snapshotter = "nydus"', configuration)
        self.assertIn("[proxy_plugins.nydus]", configuration)

        installer = (ROOT / "image/aws/scripts/install-runtime.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("/etc/xolis/nydus-version", installer)
        self.assertIn("nydus-snapshotter.service", installer)
        self.assertIn("containerd-nydus-grpc", installer)
        self.assertIn("nydusd --version", installer)
        self.assertIn("/etc/eks/image-credential-provider/ecr-credential-provider", installer)

        snapshotter = (ROOT / "image/aws/files/nydus-snapshotter.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn('nydusd_config = "/etc/nydus/nydusd-config.fusedev.json"', snapshotter)
        self.assertIn("enable_kata_volume = true", snapshotter)
        self.assertIn("enable_kubelet_credential_providers = true", snapshotter)
        self.assertIn("credential_renewal_interval = \"4h\"", snapshotter)

    def test_nydus_profile_does_not_replace_the_oci_profile(self) -> None:
        oci = (ROOT / "deploy/xolis/python-profile.yaml").read_text(encoding="utf-8")
        nydus = (ROOT / "deploy/xolis/python-profile-nydus.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("name: python-basic-v1", oci)
        self.assertIn("runtimeClassName: xolis-kata", oci)
        self.assertIn("name: python-nydus-v1", nydus)
        self.assertIn("runtimeClassName: xolis-kata-nydus", nydus)

    def test_containerd_import_helper_is_idempotent(self) -> None:
        helper = (ROOT / "image/aws/scripts/enable-containerd-import").read_text(
            encoding="utf-8"
        )
        self.assertIn('grep -Fq "${fragment_reference}"', helper)
        self.assertIn("imports =", helper)
        self.assertIn("[[:space:]]*\\][[:space:]]*$", helper)

        with tempfile.TemporaryDirectory() as temporary_directory:
            configuration_path = Path(temporary_directory) / "config.toml"
            configuration_path.write_text("version = 2\nimports = []\n", encoding="utf-8")
            environment = os.environ | {"XOLIS_CONTAINERD_CONFIG_PATH": str(configuration_path)}
            helper_path = ROOT / "image/aws/scripts/enable-containerd-import"
            subprocess.run(["bash", str(helper_path)], check=True, env=environment)
            subprocess.run(["bash", str(helper_path)], check=True, env=environment)
            configuration = configuration_path.read_text(encoding="utf-8")
            self.assertEqual(configuration.count("xolis-kata.toml"), 1)


if __name__ == "__main__":
    unittest.main()
