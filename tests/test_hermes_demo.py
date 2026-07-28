import base64
import importlib.util
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("hermes_demo", TOOLS / "hermes_demo.py")
assert SPEC and SPEC.loader
hermes_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hermes_demo)


class WebSocketFrameTests(unittest.TestCase):
    def test_client_frame_is_masked_and_round_trips(self) -> None:
        left, right = socket.socketpair()
        try:
            payload = json.dumps({"type": "input", "data": base64.b64encode(b"hi").decode()}).encode()
            left.sendall(hermes_demo.encode_frame(payload))
            opcode, decoded = hermes_demo.read_frame(right)
        finally:
            left.close()
            right.close()
        self.assertEqual(opcode, 1)
        self.assertEqual(decoded, payload)

    def test_large_frame_round_trips(self) -> None:
        left, right = socket.socketpair()
        sender = threading.Thread(
            target=left.sendall,
            args=(hermes_demo.encode_frame(b"x" * 70000, opcode=2),),
        )
        try:
            payload = b"x" * 70000
            sender.start()
            opcode, decoded = hermes_demo.read_frame(right)
            sender.join(timeout=2)
        finally:
            left.close()
            right.close()
        self.assertFalse(sender.is_alive())
        self.assertEqual(opcode, 2)
        self.assertEqual(decoded, payload)


class ClusterPreparationTests(unittest.TestCase):
    @mock.patch.object(hermes_demo, "run_kubectl")
    @mock.patch.object(hermes_demo, "deployment_environment")
    def test_api_configuration_can_be_restored(self, environment, run_kubectl) -> None:
        environment.return_value = {
            "XOLIS_WARM_POOL": "python-basic-v1-pool",
            "XOLIS_MAXIMUM_COMMAND_TIMEOUT_SECONDS": "300",
        }
        original = hermes_demo.configure_api("hermes-agent-v1", "hermes-agent-v1-pool")
        self.assertEqual(original["XOLIS_PROFILE"], None)
        hermes_demo.restore_api(original)
        set_env_calls = [
            call.args[0]
            for call in run_kubectl.call_args_list
            if "set" in call.args[0] and "env" in call.args[0]
        ]
        self.assertIn("XOLIS_PROFILE=hermes-agent-v1", set_env_calls[0])
        self.assertIn("XOLIS_PROFILE-", set_env_calls[1])
        self.assertIn("XOLIS_WARM_POOL=python-basic-v1-pool", set_env_calls[1])

    @mock.patch.object(hermes_demo.time, "sleep")
    @mock.patch.object(hermes_demo, "kubectl_json")
    def test_warm_pool_waits_for_ready_replica(self, kubectl_json, _sleep) -> None:
        kubectl_json.side_effect = [
            {"spec": {"replicas": 1}, "status": {"readyReplicas": 0}},
            {"spec": {"replicas": 1}, "status": {"readyReplicas": 1}},
        ]
        hermes_demo.wait_for_warm_pool("hermes-agent-v1-pool", 1, 30)
        self.assertEqual(kubectl_json.call_count, 2)


if __name__ == "__main__":
    unittest.main()
