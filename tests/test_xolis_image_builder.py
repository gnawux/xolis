import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "tools" / "xolis_image_builder.py"
SPEC = importlib.util.spec_from_file_location("xolis_image_builder", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class BuildCommandTests(unittest.TestCase):
    def test_empty_successful_json_response_is_an_empty_object(self) -> None:
        completed = MODULE.subprocess.CompletedProcess(
            args=["aws"], returncode=0, stdout="", stderr=""
        )
        with patch.object(MODULE.subprocess, "run", return_value=completed):
            response = MODULE.AwsCli("test-profile", "test-region").run(
                ["s3api", "delete-objects"], json_output=True
            )
        self.assertEqual(response, {})

    def test_aws_cli_can_use_environment_credentials(self) -> None:
        client = MODULE.AwsCli("", "ap-northeast-1")
        self.assertEqual(client.base, ["aws", "--region", "ap-northeast-1"])

    def test_commands_build_all_images_and_push_private_references(self) -> None:
        registry = "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com"
        commands = MODULE.build_commands(
            "https://example.com/source.tar.gz?signature=test",
            registry,
            "build-123",
            tuple(MODULE.REPOSITORIES),
            ("xolis-runtime-hermes",),
        )
        script = "\n".join(commands)

        self.assertIn("systemctl enable --now docker", script)
        self.assertIn("xolis/xolis-api:build-123", script)
        self.assertIn("xolis/xolis-runtime-python:build-123", script)
        self.assertIn("xolis/xolis-runtime-hermes:build-123", script)
        self.assertIn("xolis/sandbox-router:build-123", script)
        self.assertEqual(script.count("docker push"), 4)
        self.assertIn("nydusify convert", script)
        self.assertIn("nydusify check --target", script)
        self.assertIn("/usr/local/bin/nydusd", script)
        self.assertIn("xolis/xolis-runtime-hermes:build-123-nydus", script)
        self.assertIn(MODULE.NYDUS_ARCHIVE_SHA256, script)

    def test_invalid_registry_and_tag_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.build_commands(
                "https://example.com/source", "docker.io", "build-123", (), ()
            )
        with self.assertRaises(ValueError):
            MODULE.build_commands(
                "https://example.com/source",
                "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com",
                "bad tag",
                (),
                (),
            )

    def test_nydus_conversion_requires_selected_oci_image(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.build_commands(
                "https://example.com/source",
                "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com",
                "build-123",
                (),
                ("xolis-runtime-hermes",),
            )

    def test_presigned_url_is_shell_quoted(self) -> None:
        quoted = MODULE.shell_quote("https://example.com/a?x=one&y='two'")
        self.assertEqual(quoted, "'https://example.com/a?x=one&y='\"'\"'two'\"'\"''")

    def test_versioned_source_cleanup_is_implemented(self) -> None:
        class FakeAws:
            def __init__(self) -> None:
                self.calls: list[list[str]] = []

            def run(self, arguments: list[str], *, json_output: bool = False) -> object:
                self.calls.append(arguments)
                if "list-object-versions" in arguments:
                    return {
                        "Versions": [
                            {"Key": "build/source.tar.gz", "VersionId": "version"}
                        ],
                        "DeleteMarkers": [
                            {"Key": "build/source.tar.gz", "VersionId": "marker"}
                        ],
                    }
                if "delete-objects" in arguments:
                    return {}
                return ""

        builder = object.__new__(MODULE.ImageBuilder)
        builder.instance_id = None
        builder.source_uri = "s3://test-bucket/build/source.tar.gz"
        builder.aws = FakeAws()

        builder.cleanup()

        delete_call = next(
            call for call in builder.aws.calls if "delete-objects" in call
        )
        payload = json.loads(delete_call[delete_call.index("--delete") + 1])
        self.assertEqual(
            payload["Objects"],
            [
                {"Key": "build/source.tar.gz", "VersionId": "version"},
                {"Key": "build/source.tar.gz", "VersionId": "marker"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
