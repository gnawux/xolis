import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "tools/render_hermes_profile.py"
SPEC = importlib.util.spec_from_file_location("render_hermes_profile", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class HermesProfileTests(unittest.TestCase):
    def test_profile_requires_and_renders_an_immutable_private_image(self) -> None:
        reference = (
            "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/"
            "xolis/xolis-runtime-hermes@sha256:" + "a" * 64
        )
        rendered = MODULE.render(reference, ROOT)
        self.assertIn(f"image: {reference}", rendered)
        self.assertNotIn("__HERMES_IMAGE_REFERENCE__", rendered)
        self.assertIn("name: hermes-agent-credentials", rendered)
        self.assertIn("replicas: 0", rendered)
        self.assertIn("runtimeClassName: xolis-kata", rendered)

        nydus = MODULE.render(reference, ROOT, "nydus")
        self.assertIn("name: hermes-agent-nydus-v1", nydus)
        self.assertIn("runtimeClassName: xolis-kata-nydus", nydus)
        self.assertIn("xolis.io/image-mode: nydus", nydus)

        with self.assertRaises(ValueError):
            MODULE.render("docker.io/xolis-runtime-hermes:latest", ROOT)

    def test_image_pins_hermes_and_does_not_embed_credentials(self) -> None:
        dockerfile = (ROOT / "image/xolis-runtime-hermes/Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("846b14ab01a84483d2c3dd429579173040474585", dockerfile)
        self.assertIn("python:3.13-slim", dockerfile)
        self.assertNotIn("API_KEY=", dockerfile)


if __name__ == "__main__":
    unittest.main()
