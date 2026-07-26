import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "xolis_image_builder.py"
SPEC = importlib.util.spec_from_file_location("xolis_image_builder", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class BuildCommandTests(unittest.TestCase):
    def test_commands_build_all_images_and_push_private_references(self) -> None:
        registry = "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com"
        commands = MODULE.build_commands(
            "https://example.com/source.tar.gz?signature=test", registry, "build-123"
        )
        script = "\n".join(commands)

        self.assertIn("systemctl enable --now docker", script)
        self.assertIn("xolis/xolis-api:build-123", script)
        self.assertIn("xolis/xolis-runtime-python:build-123", script)
        self.assertIn("xolis/sandbox-router:build-123", script)
        self.assertEqual(script.count("docker push"), 3)

    def test_invalid_registry_and_tag_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.build_commands("https://example.com/source", "docker.io", "build-123")
        with self.assertRaises(ValueError):
            MODULE.build_commands(
                "https://example.com/source",
                "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com",
                "bad tag",
            )

    def test_presigned_url_is_shell_quoted(self) -> None:
        quoted = MODULE.shell_quote("https://example.com/a?x=one&y='two'")
        self.assertEqual(quoted, "'https://example.com/a?x=one&y='\"'\"'two'\"'\"''")


if __name__ == "__main__":
    unittest.main()
