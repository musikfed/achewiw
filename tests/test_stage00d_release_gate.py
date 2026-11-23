"""00-Д: safety semantics of the read-only document release auditor.

No real project, DOCX, SQLite, services or network access.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-document-release-00d.py"
REGISTER = ROOT / "docs" / "quality" / "document-release-00d.json"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True, text=True, check=False, timeout=15,
    )


def test_current_gate_is_explicitly_blocked():
    result = _run("--json", "--expect-blocked")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["result"] == "BLOCKED"
    assert any("D04" in x for x in report["blockers"])
    assert any("D05" in x for x in report["blockers"])
    assert any("MANUAL" in x for x in report["blockers"])
    assert report["statuses"]["known_fail"] >= 2


def test_release_mode_fails_closed_with_current_issues():
    result = _run("--release")
    assert result.returncode == 2
    assert "BLOCKED" in result.stdout


def test_invalid_status_does_not_forge_release(tmp_path):
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    register["checks"][0]["status"] = "approved_without_evidence"
    test_copy = tmp_path / "invalid.json"
    test_copy.write_text(json.dumps(register), encoding="utf-8")
    result = _run("--release", "--manifest", test_copy)
    assert result.returncode == 4
    assert "INVALID ACCEPTANCE REGISTER" in result.stdout


def test_missing_evidence_does_not_forge_release(tmp_path):
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    register["checks"][0]["evidence"] = []
    test_copy = tmp_path / "missing-evidence.json"
    test_copy.write_text(json.dumps(register), encoding="utf-8")
    assert _run("--release", "--manifest", test_copy).returncode == 4


def test_ci_success_without_human_signoff_is_not_publication_approval(tmp_path):
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    for check in register["checks"]:
        check["status"] = "pass"
    # Deliberately keep the explicit manual acceptance false.
    test_copy = tmp_path / "fake-all-green.json"
    test_copy.write_text(json.dumps(register), encoding="utf-8")
    result = _run("--json", "--release", "--manifest", test_copy)
    assert result.returncode == 2
    assert "MANUAL" in json.loads(result.stdout)["blockers"]


def test_unavailable_register_fails_closed(tmp_path):
    absent = tmp_path / "absent.json"
    result = _run("--release", "--manifest", absent)
    assert result.returncode == 4
