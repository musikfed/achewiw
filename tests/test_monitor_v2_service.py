"""Regression: systemd linked FragmentPath; never start an unrelated user unit."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "monitor-v2.sh"


class LinkedMonitorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.workspace = root / "achewiw"
        self.workspace.mkdir()
        (self.workspace / "data").mkdir()
        (self.workspace / "scripts").mkdir()
        (self.workspace / "ops").mkdir()
        (self.workspace / "scripts/achewiw_host_monitor.py").write_text("# read only\n")
        self.unit = self.workspace / "ops/achewiw-metrics-v2.service"
        self.unit.write_text("[Service]\nWorkingDirectory=" + str(self.workspace) + "\n")
        manager = root / "unit-manager"
        manager.mkdir()
        self.link = manager / "achewiw-metrics-v2.service"
        self.started = manager / "started"
        self.bin = root / "bin"
        self.bin.mkdir()
        self.control = root / "monitor-v2.sh"
        self.control.write_text(SCRIPT.read_text().replace("/1tbdisk/achewiw", str(self.workspace)))
        self.fake("id", '#!/bin/sh\nif [ "$1" = -u ]; then echo 1000; else /usr/bin/id "$@"; fi\n')
        self.fake("systemctl", r'''#!/bin/bash
set -euo pipefail
[[ "${1:-}" == --user ]] || exit 98
shift
case "${1:-}" in
  show)
    key="${4:-}"
    [[ "${2:-}" == achewiw-metrics-v2.service && "${3:-}" == -p ]] || exit 97
    case "$key" in
      LoadState) [[ -e "$TEST_LINK" ]] && echo loaded || echo not-found ;;
      FragmentPath) [[ -e "$TEST_LINK" ]] && echo "$TEST_LINK" || : ;;
      WorkingDirectory) echo "$TEST_WORKSPACE" ;;
      ExecStart) echo "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 $TEST_WORKSPACE/scripts/achewiw_host_monitor.py --watch --interval 5 }" ;;
      ActiveState) [[ -e "$TEST_STARTED" ]] && echo active || echo inactive ;;
    esac
    ;;
  link)
    [[ "$2" == "$TEST_WORKSPACE/ops/achewiw-metrics-v2.service" ]]
    ln -s -- "$2" "$TEST_LINK"
    ;;
  daemon-reload) ;;
  start) [[ "$2" == achewiw-metrics-v2.service ]]; touch "$TEST_STARTED" ;;
  --no-pager) [[ "$2" == status ]]; echo "test: active" ;;
  *) exit 96 ;;
esac
''')
        self.env = dict(os.environ, PATH=str(self.bin) + ":" + os.environ.get("PATH",""),
                        TEST_WORKSPACE=str(self.workspace),
                        TEST_LINK=str(self.link), TEST_STARTED=str(self.started))

    def fake(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_control(self, action):
        return subprocess.run(["bash", str(self.control), action],
                              capture_output=True, text=True, env=self.env)

    def test_linked_fragment_path_is_accepted_and_started(self):
        self.link.symlink_to(self.unit)
        result = self.run_control("start")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.started.exists())
        self.assertIn("подтверждён", result.stdout)

    def test_first_link_then_start_works(self):
        result = self.run_control("start")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.link.resolve(), self.unit)
        self.assertTrue(self.started.exists())

    def test_foreign_unit_is_never_started(self):
        foreign = self.link.parent / "foreign.service"
        foreign.write_text("[Service]\nExecStart=/bin/true\n")
        self.link.symlink_to(foreign)
        result = self.run_control("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("неподтверждён", result.stderr)
        self.assertFalse(self.started.exists())
        self.assertEqual(self.link.resolve(), foreign)

    def test_foreign_status_is_read_only(self):
        foreign = self.link.parent / "foreign.service"
        foreign.write_text("foreign\n")
        self.link.symlink_to(foreign)
        result = self.run_control("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("неподтверждённый", result.stdout)
        self.assertFalse(self.started.exists())
        self.assertEqual(self.link.resolve(), foreign)


if __name__ == "__main__":
    unittest.main()
