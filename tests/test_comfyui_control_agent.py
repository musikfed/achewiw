"""Tests for the loopback-only host ComfyUI control bridge; no host processes run."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import comfyui_control_agent as agent


class ComfyUIControlAgentTests(unittest.TestCase):
    def setUp(self):
        agent.JOBS.clear()

    def tearDown(self):
        agent.JOBS.clear()

    def test_status_parser_recognizes_owned_comfyui_and_checkpoint(self):
        output = (
            "ACHEWIW ComfyUI: ComfyUI установлен в /1tbdisk/achewiw/data/comfyui-runtime/src\n"
            "ACHEWIW ComfyUI: SDXL присутствует\n"
            "ACHEWIW ComfyUI: API ComfyUI на localhost:8188 отвечает\n"
            "ACHEWIW ComfyUI: Наш ComfyUI работает PID=12345\n"
        )
        self.assertEqual(agent.parse_status(output), {
            "ok": True, "installed": True, "model_present": True,
            "api_ready": True, "owned_running": True, "port_busy": False,
        })

    def test_unknown_port_owner_is_not_claimed_as_ours(self):
        result = agent.parse_status(
            "ACHEWIW ComfyUI: Порт 8188 занят, процесс не идентифицирован\n"
        )
        self.assertTrue(result["port_busy"])
        self.assertFalse(result["owned_running"])

    def test_control_actions_are_strictly_allowlisted(self):
        self.assertEqual(agent.ACTIONS, {"install", "model-sdxl", "start", "stop"})
        for invalid in ("status", "doctor", "bash -c id", "../stop", "", []):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    agent.start_job(invalid)

    def test_start_job_uses_only_fixed_script_and_no_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "achewiw"
            data = root / "data"
            scripts = root / "scripts"
            data.mkdir(parents=True)
            scripts.mkdir()
            script = scripts / "comfyui-local.sh"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            token = data / "comfyui-control.token"
            token.write_text("x" * 64, encoding="utf-8")
            token.chmod(0o600)
            jobs = data / "comfyui-control-jobs"
            calls = []

            class FakeProcess:
                def poll(self):
                    return None

            def fake_popen(args, **kwargs):
                calls.append((args, kwargs))
                return FakeProcess()

            with (
                patch.object(agent, "ROOT", root),
                patch.object(agent, "DATA_DIR", data),
                patch.object(agent, "SCRIPT", script),
                patch.object(agent, "TOKEN_FILE", token),
                patch.object(agent, "JOBS_DIR", jobs),
                patch.object(agent, "BASH", "/bin/bash"),
                patch.object(agent.subprocess, "Popen", fake_popen),
            ):
                result = agent.start_job("start")
                self.assertEqual(result["action"], "start")
                self.assertEqual(calls[0][0], ["/bin/bash", str(script), "start"])
                self.assertEqual(calls[0][1]["cwd"], str(root))
                self.assertNotIn("shell", calls[0][1])
                self.assertEqual(calls[0][1]["stdin"], agent.subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
