"""Safety checks for ACHEWIW shutdown; Docker and ss are replaced by fake commands.

Run: python3 -m unittest discover -s tests -p 'test_achewiw_control.py'
"""
from __future__ import annotations
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/achewiw-control.sh"

FAKE_DOCKER = r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_LOG"
case "${1:-}" in
  info)
    [[ "${TEST_NO_DOCKER:-}" == 1 ]] && exit 50
    echo 28.0
    ;;
  ps)
    [[ "$*" == *'label=com.docker.compose.project=achewiw'* || "$*" == *'name=^/achewiw-studio'* ]] || exit 52
    [[ "$*" == *" -a "* || "$(cat "$TEST_STATE")" == running ]] || exit 0
    printf 'a11c0ffee\n'
    if [[ "${TEST_ROGUE:-}" == 1 ]]; then printf 'r0gue\n'; fi
    ;;
  inspect)
    id="${@: -1}"
    if [[ "$id" == r0gue ]]; then
      printf 'achewiw|/other/workspace|studio|managed|/other/workspace\n'
    elif [[ "${TEST_LEGACY:-}" == 1 ]]; then
      printf "achewiw|$TEST_WORKSPACE|studio|<no value>|<no value>\n"
    elif [[ "${TEST_WRONG_ROLE:-}" == 1 ]]; then
      printf 'achewiw|/1tbdisk/achewiw|unregistered|managed|$TEST_WORKSPACE\n'
    else
      printf "achewiw|$TEST_WORKSPACE|studio|managed|$TEST_WORKSPACE\n"
    fi
    ;;
  compose)
    if [[ "$*" == *" config --quiet" ]]; then
      [[ "$TEST_BAD_COMPOSE" != 1 ]] || exit 33
    elif [[ "$*" == *" config --services" ]]; then
      echo studio
    elif [[ "$*" == *" up -d --build --no-deps studio" ]]; then
      printf running > "$TEST_STATE"
    else exit 55; fi
    ;;
  restart)
    [[ "$*" == *" a11c0ffee" ]] || exit 53
    printf running > "$TEST_STATE"
    ;;
  stop)
    [[ " $* " == *' --time 60 '* ]] || exit 51
    [[ "${@: -1}" == a11c0ffee ]] || exit 53
    printf stopped > "$TEST_STATE"
    ;;
  *) exit 54 ;;
esac
'''

FAKE_SYSTEMCTL = r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_SYSTEMCTL_LOG"
case "$*" in
  *'-p LoadState --value'*) printf '%s\n' "${TEST_UNIT_STATE:-not-found}" ;;
  *'-p FragmentPath --value'*) printf '%s\n' "${TEST_UNIT_FRAGMENT:-${TEST_WORKSPACE}/ops/achewiw-host-monitor.service}" ;;
  *'-p WorkingDirectory --value'*) printf '%s\n' "${TEST_UNIT_WORKDIR:-${TEST_WORKSPACE}}" ;;
  *'-p ExecStart --value'*) printf '%s\n' "argv[]=/usr/bin/python3 ${TEST_WORKSPACE}/scripts/achewiw_host_monitor.py --watch --interval 5" ;;
  *'--user stop achewiw-host-monitor.service'*) printf 'stopped' > "$TEST_UNIT_STOPPED" ;;
  *'--user start achewiw-host-monitor.service'*) printf 'started' > "$TEST_UNIT_STARTED" ;;
  *) exit 56 ;;
esac
'''

class ShutdownTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.workspace = root / "achewiw"
        self.workspace.mkdir()
        (self.workspace / "compose.yaml").write_text("name: achewiw\nservices:\n  studio:\n    image: test\n")
        (self.workspace / ".env").write_text("ACHEWIW_PASSWORD=test-private\n")
        (self.workspace / "data").mkdir()
        (self.workspace / ".git").mkdir()
        self.script = root / "control.sh"
        self.script.write_text(SCRIPT.read_text().replace("/1tbdisk/achewiw", str(self.workspace)))
        self.unit_started = root / "unit.started"
        for name, script in (("docker", FAKE_DOCKER), ("ss", "#!/bin/bash\nif [[ \"$TEST_PORT_BUSY\" == 1 ]]; then echo LISTEN; fi\n"), ("systemctl", FAKE_SYSTEMCTL)):
            file = root / name
            file.write_text(script, encoding="utf-8")
            file.chmod(0o755)
        self.log = root / "docker.log"
        self.systemctl_log = root / "systemctl.log"
        self.unit_stopped = root / "unit.stopped"
        self.state = root / "state"
        self.state.write_text("running")
        self.env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                        TEST_WORKSPACE=str(self.workspace), TEST_PORT_BUSY="0", TEST_BAD_COMPOSE="0",
                        TEST_UNIT_STARTED=str(self.unit_started),
                        TEST_LOG=str(self.log), TEST_STATE=str(self.state),
                        TEST_SYSTEMCTL_LOG=str(self.systemctl_log), TEST_UNIT_STOPPED=str(self.unit_stopped))

    def run_switch(self, action="stop", **env):
        return subprocess.run(["bash", str(self.script), action], env={**self.env, **env},
                              capture_output=True, text=True)

    def commands(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_managed_container_stops(self):
        result = self.run_switch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text(), "stopped")
        self.assertEqual(self.commands().count("stop --time 60 a11c0ffee"), 1)

    def test_repeated_stop_is_safe(self):
        self.assertEqual(self.run_switch().returncode, 0)
        self.assertEqual(self.run_switch().returncode, 0)
        self.assertEqual(self.commands().count("stop --time"), 1)

    def test_foreign_container_blocks_all(self):
        self.assertNotEqual(self.run_switch(TEST_ROGUE="1").returncode, 0)
        self.assertEqual(self.state.read_text(), "running")
        self.assertNotIn("stop --time", self.commands())

    def test_unmarked_container_is_protected(self):
        self.assertNotEqual(self.run_switch(TEST_LEGACY="1").returncode, 0)
        self.assertNotIn("stop --time", self.commands())

    def test_unauthorized_service_is_protected(self):
        self.assertNotEqual(self.run_switch(TEST_WRONG_ROLE="1").returncode, 0)
        self.assertNotIn("stop --time", self.commands())

    def test_status_readonly(self):
        result = self.run_switch("status", TEST_ROGUE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("НЕ подтверждён", result.stdout)
        self.assertNotIn("stop --time", self.commands())

    def test_no_docker_access_fails_closed(self):
        self.assertNotEqual(self.run_switch(TEST_NO_DOCKER="1").returncode, 0)
        self.assertNotIn("stop --time", self.commands())

    def test_owned_host_unit_is_stopped_along_with_studio(self):
        result = self.run_switch(TEST_UNIT_STATE="loaded")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.unit_stopped.read_text(), "stopped")
        self.assertEqual(self.state.read_text(), "stopped")

    def test_foreign_host_unit_blocks_all_docker_stops(self):
        result = self.run_switch(TEST_UNIT_STATE="loaded",
                                 TEST_UNIT_FRAGMENT="/other/project/monitor.service")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_text(), "running")
        self.assertFalse(self.unit_stopped.exists())
        self.assertNotIn("stop --time", self.commands())

    def test_start_stopped_is_safe_and_idempotent(self):
        self.state.write_text("stopped")
        result = self.run_switch("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text(), "running")
        self.assertIn("up -d --build --no-deps studio", self.commands())
        result = self.run_switch("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands().count("up -d --build --no-deps studio"), 1)

    def test_start_refuses_foreign_container_or_port(self):
        self.state.write_text("stopped")
        self.assertNotEqual(self.run_switch("start", TEST_ROGUE="1").returncode, 0)
        self.assertNotIn("up -d", self.commands())
        self.assertNotEqual(self.run_switch("start", TEST_PORT_BUSY="1").returncode, 0)
        self.assertNotIn("up -d", self.commands())

    def test_start_requires_password_config_and_own_unit(self):
        (self.workspace / ".env").unlink()
        self.assertNotEqual(self.run_switch("start").returncode, 0)
        self.assertNotIn("up -d", self.commands())
        (self.workspace / ".env").write_text("ACHEWIW_PASSWORD=test-private\n")
        self.assertNotEqual(self.run_switch("start", TEST_UNIT_STATE="loaded",
                            TEST_UNIT_FRAGMENT="/other/unit").returncode, 0)
        self.assertNotIn("up -d", self.commands())

    def test_restart_only_verified_studio_and_monitor(self):
        result = self.run_switch("restart", TEST_UNIT_STATE="loaded")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("restart --time 60 a11c0ffee", self.commands())
        self.assertTrue(self.unit_started.exists())
        self.assertNotIn("up -d", self.commands())

if __name__ == "__main__":
    unittest.main()
