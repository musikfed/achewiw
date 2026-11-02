"""Safeguards for the project-only ACHEWIW command-line switch.

Run: python3 -m unittest discover -s tests -p 'test_achewiw_control.py'
Docker and ss are faked; these tests never touch the real Docker daemon.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/achewiw-control.sh"

FAKE_DOCKER = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$ACHEWIW_TEST_LOG"
case "$1" in
  info)
    [[ "${ACHEWIW_TEST_DENY:-}" == "1" ]] && exit 1
    echo "28.0"
    ;;
  ps)
    [[ " $* " == *" --filter label=com.docker.compose.project=achewiw "* ]] || exit 41
    if [[ " $* " == *" -a "* ]]; then
      echo "achewiw-studio — Up 10 minutes"
    elif [[ "$(cat "$ACHEWIW_TEST_STATE")" == "running" ]]; then
      echo "a11c0ffee"
    fi
    ;;
  stop)
    [[ " $* " == *" --time 60 "* ]] || exit 42
    [[ "${*: -1}" == "a11c0ffee" ]] || exit 43
    printf 'stopped' > "$ACHEWIW_TEST_STATE"
    echo "a11c0ffee"
    ;;
  *)
    exit 44
    ;;
esac
"""

FAKE_SS = "#!/usr/bin/env bash\nexit 0\n"


class ProjectShutdownTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tmp = Path(self.directory.name)
        for filename, content in (("docker", FAKE_DOCKER), ("ss", FAKE_SS)):
            target = self.tmp / filename
            target.write_text(content, encoding="utf-8")
            target.chmod(0o755)
        self.state = self.tmp / "state"
        self.state.write_text("running", encoding="utf-8")
        self.log = self.tmp / "docker.log"
        self.environment = os.environ.copy()
        self.environment.update({
            "PATH": str(self.tmp) + os.pathsep + os.environ["PATH"],
            "ACHEWIW_TEST_LOG": str(self.log),
            "ACHEWIW_TEST_STATE": str(self.state),
        })

    def run_control(self, action, **extra_env):
        env = {**self.environment, **extra_env}
        return subprocess.run(["bash", str(SCRIPT), action], capture_output=True,
                              text=True, check=False, env=env)

    def test_stop_uses_compose_project_label_and_only_expected_container(self):
        result = self.run_control("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a11c0ffee", result.stdout)
        self.assertEqual(self.state.read_text(), "stopped")
        self.assertIn("--filter label=com.docker.compose.project=achewiw",
                      self.log.read_text())
        self.assertIn("stop --time 60 a11c0ffee", self.log.read_text())

    def test_second_stop_is_idempotent(self):
        first = self.run_control("stop")
        self.assertEqual(first.returncode, 0)
        second = self.run_control("stop")
        self.assertEqual(second.returncode, 0)
        self.assertEqual(self.log.read_text().count("stop --time"), 1)

    def test_status_is_readonly(self):
        result = self.run_control("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("achewiw", result.stdout)
        self.assertNotIn("stop --time", self.log.read_text())

    def test_no_docker_access_refuses_stop(self):
        result = self.run_control("stop", ACHEWIW_TEST_DENY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Нет доступа", result.stderr)
        self.assertNotIn("stop --time", self.log.read_text())

    def test_start_command_is_not_implemented(self):
        result = self.run_control("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_no_running_project_container_does_not_stop_anything(self):
        self.state.write_text("stopped")
        result = self.run_control("stop")
        self.assertEqual(result.returncode, 0)
        self.assertIn("уже остановлены", result.stdout)
        self.assertNotIn("stop --time", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
