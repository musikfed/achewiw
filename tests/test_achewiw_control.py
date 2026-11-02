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
    [[ "$*" == *'label=com.docker.compose.project=achewiw'* ]] || exit 52
    [[ "$(cat "$TEST_STATE")" == running ]] || exit 0
    printf 'a11c0ffee\n'
    if [[ "${TEST_ROGUE:-}" == 1 ]]; then printf 'r0gue\n'; fi
    ;;
  inspect)
    id="${@: -1}"
    if [[ "$id" == r0gue ]]; then
      printf 'achewiw|/other/workspace|studio|managed|/other/workspace\n'
    elif [[ "${TEST_LEGACY:-}" == 1 ]]; then
      printf 'achewiw|/1tbdisk/achewiw|studio|<no value>|<no value>\n'
    elif [[ "${TEST_WRONG_ROLE:-}" == 1 ]]; then
      printf 'achewiw|/1tbdisk/achewiw|unregistered|managed|/1tbdisk/achewiw\n'
    else
      printf 'achewiw|/1tbdisk/achewiw|studio|managed|/1tbdisk/achewiw\n'
    fi
    ;;
  stop)
    [[ " $* " == *' --time 60 '* ]] || exit 51
    [[ "${@: -1}" == a11c0ffee ]] || exit 53
    printf stopped > "$TEST_STATE"
    ;;
  *) exit 54 ;;
esac
'''

class ShutdownTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, script in (("docker", FAKE_DOCKER), ("ss", "#!/bin/bash\nexit 0\n")):
            file = root / name
            file.write_text(script, encoding="utf-8")
            file.chmod(0o755)
        self.log = root / "docker.log"
        self.state = root / "state"
        self.state.write_text("running")
        self.env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                        TEST_LOG=str(self.log), TEST_STATE=str(self.state))

    def run_switch(self, action="stop", **env):
        return subprocess.run(["bash", str(SCRIPT), action], env={**self.env, **env},
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

    def test_start_not_supported(self):
        self.assertNotEqual(self.run_switch("start").returncode, 0)
        self.assertFalse(self.log.exists())

if __name__ == "__main__":
    unittest.main()
