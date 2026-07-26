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
