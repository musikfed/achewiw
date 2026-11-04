"""Tests for the loopback-only host ComfyUI control bridge; no host processes run."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import comfyui_control_agent as agent


def test_status_parser_recognizes_only_owned_comfyui_and_checkpoint():
    output = (
        "ACHEWIW ComfyUI: ComfyUI установлен в /1tbdisk/achewiw/data/comfyui-runtime/src\n"
        "ACHEWIW ComfyUI: SDXL присутствует\n"
        "ACHEWIW ComfyUI: API ComfyUI на localhost:8188 отвечает\n"
        "ACHEWIW ComfyUI: Наш ComfyUI работает PID=12345\n"
    )
    result = agent.parse_status(output)
    assert result == {
        "ok": True, "installed": True, "model_present": True,
        "api_ready": True, "owned_running": True, "port_busy": False,
    }


def test_unknown_port_owner_is_not_claimed_as_ours():
    result = agent.parse_status(
        "ACHEWIW ComfyUI: Порт 8188 занят, процесс не идентифицирован\n"
    )
    assert result["port_busy"] is True
    assert result["owned_running"] is False


def test_control_actions_are_strictly_allowlisted():
    assert agent.ACTIONS == {"install", "model-sdxl", "start", "stop"}
    for invalid in ("status", "doctor", "bash -c id", "../stop", "", []):
        try:
            agent.start_job(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Unexpected action accepted: " + str(invalid))


def test_start_job_uses_only_fixed_script_and_no_shell(tmp_path, monkeypatch):
    root = tmp_path / "achewiw"
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
    monkeypatch.setattr(agent, "ROOT", root)
    monkeypatch.setattr(agent, "DATA_DIR", data)
    monkeypatch.setattr(agent, "SCRIPT", script)
    monkeypatch.setattr(agent, "TOKEN_FILE", token)
    monkeypatch.setattr(agent, "JOBS_DIR", jobs)
    monkeypatch.setattr(agent, "BASH", "/bin/bash")
    agent.JOBS.clear()
    calls = []

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)
    result = agent.start_job("start")
    assert result["action"] == "start"
    assert calls[0][0] == ["/bin/bash", str(script), "start"]
    assert calls[0][1]["cwd"] == str(root)
    assert "shell" not in calls[0][1]
    assert calls[0][1]["stdin"] == agent.subprocess.DEVNULL
    agent.JOBS.clear()
