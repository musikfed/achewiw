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
    if [[ "$*" == *".HostConfig.NetworkMode"* ]]; then
      if [[ "${TEST_LEGACY_FOREIGN_MOUNT:-}" == 1 ]]; then
        printf '/achewiw-studio|host|/other-workspace/data\n'
      else
        if [[ "${TEST_MOVED_LEGACY:-}" == 1 ]]; then
          printf '/achewiw-studio|host|%s/data\n' "$TEST_OLD_WORKSPACE"
        else
          printf '/achewiw-studio|host|%s/data\n' "$TEST_WORKSPACE"
        fi
      fi
      exit 0
    fi
    if [[ "$id" == r0gue ]]; then
      printf 'achewiw|/other/workspace|studio|managed|/other/workspace\n'
    elif [[ "${TEST_LEGACY:-}" == 1 && ! -e "$TEST_MIGRATED" ]]; then
      if [[ "${TEST_MOVED_LEGACY:-}" == 1 ]]; then
        printf "achewiw|$TEST_OLD_WORKSPACE|studio|<no value>|<no value>\n"
      else
        printf "achewiw|$TEST_WORKSPACE|studio|<no value>|<no value>\n"
      fi
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
    elif [[ "$*" == *" up -d --build --no-deps studio" || "$*" == *" up -d --build --force-recreate --no-deps studio" ]]; then
      printf running > "$TEST_STATE"
      printf migrated > "$TEST_MIGRATED"
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

FAKE_CURL = r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_CURL_LOG"
if [[ "${TEST_HTTP_FAIL:-}" == 1 ]]; then exit 7; fi
if [[ "${TEST_HTTP_UNIDENTIFIED:-}" == 1 ]]; then
  printf 'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n'
elif [[ "${TEST_HTTP_BASIC:-}" == 1 ]]; then
  printf 'HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm="ACHEWIW"\r\n\r\n'
else
  printf 'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nX-ACHEWIW-App: studio\r\nX-ACHEWIW-Auth: private-vpn-no-password\r\n\r\n'
fi
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
  *'--user link '* ) printf 'linked' > "$TEST_UNIT_LINKED" ;;
  *'--user daemon-reload'*) true ;;
  *) exit 56 ;;
esac
'''


FAKE_GIT = r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_GIT_LOG"
case "$*" in
  *'remote get-url origin'*) echo "$TEST_GIT_ORIGIN" ;;
  *'symbolic-ref --quiet --short HEAD'*) echo "$TEST_GIT_BRANCH" ;;
  *'status --porcelain --untracked-files=normal'*)
    if [[ "$TEST_GIT_DIRTY" == 1 ]]; then echo " M app/main.py"; fi ;;
  *'rev-parse --short HEAD'*) cat "$TEST_GIT_SHA_FILE" ;;
  *'fetch --no-tags origin'*) true ;;
  *'merge-base --is-ancestor HEAD'*) [[ "$TEST_GIT_DIVERGED" != 1 ]] ;;
  *'merge --ff-only'*) echo newsha > "$TEST_GIT_SHA_FILE" ;;
  *) exit 66 ;;
esac
'''

class ShutdownTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.old_workspace = root / "legacy-projects" / "achewiw"
        self.workspace = root / "achewiw"
        self.workspace.mkdir()
        (self.workspace / "compose.yaml").write_text("name: achewiw\nservices:\n  studio:\n    image: test\n")
        (self.workspace / ".env").write_text("ACHEWIW_PASSWORD=test-private\n")
        (self.workspace / "data").mkdir()
        (self.workspace / ".git").mkdir()
        (self.workspace / "ops").mkdir()
        (self.workspace / "ops/achewiw-host-monitor.service").write_text("[Unit]\nDescription=Test\n")
        self.script = root / "control.sh"
        self.script.write_text(
            SCRIPT.read_text()
            .replace("/1tbdisk/PegineLab/projects/achewiw", str(self.old_workspace))
            .replace("/1tbdisk/achewiw", str(self.workspace))
        )
        self.unit_started = root / "unit.started"
        self.unit_linked = root / "unit.linked"
        self.gitlog = root / "git.log"
        self.gitsha = root / "sha"
        self.gitsha.write_text("oldsha\n")
        for name, script in (("docker", FAKE_DOCKER), ("ss", "#!/bin/bash\nif [[ \"$TEST_PORT_BUSY\" == 1 ]]; then echo LISTEN; fi\n"), ("systemctl", FAKE_SYSTEMCTL), ("git", FAKE_GIT), ("curl", FAKE_CURL)):
            file = root / name
            file.write_text(script, encoding="utf-8")
            file.chmod(0o755)
        self.curl_log = root / "curl.log"
        self.migrated = root / "migrated"
        self.log = root / "docker.log"
        self.systemctl_log = root / "systemctl.log"
        self.unit_stopped = root / "unit.stopped"
        self.state = root / "state"
        self.state.write_text("running")
        self.env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                        TEST_WORKSPACE=str(self.workspace), TEST_OLD_WORKSPACE=str(self.old_workspace), TEST_PORT_BUSY="0", TEST_BAD_COMPOSE="0",
                        TEST_UNIT_STARTED=str(self.unit_started), TEST_UNIT_LINKED=str(self.unit_linked),
                        TEST_GIT_LOG=str(self.gitlog), TEST_GIT_SHA_FILE=str(self.gitsha),
                        TEST_GIT_ORIGIN="https://github.com/musikfed/achewiw.git",
                        TEST_GIT_BRANCH="feature/technical-epub-first",
                        TEST_GIT_DIRTY="0", TEST_GIT_DIVERGED="0",
                        TEST_LOG=str(self.log), TEST_STATE=str(self.state),
                        TEST_MIGRATED=str(self.migrated),
                        TEST_CURL_LOG=str(self.curl_log),
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

    def test_rebuild_ignores_foreign_monitor_unit_and_does_not_touch_it(self):
        result = self.run_switch(
            "rebuild", TEST_UNIT_STATE="loaded",
            TEST_UNIT_FRAGMENT="/other/monitor.service")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("user-unit", result.stdout)
        self.assertIn("up -d --build --force-recreate --no-deps studio", self.commands())
        self.assertFalse(self.unit_stopped.exists())
        self.assertFalse(self.unit_started.exists())

    def test_update_ignores_foreign_monitor_unit_but_updates_studio(self):
        result = self.run_switch(
            "update", TEST_UNIT_STATE="loaded",
            TEST_UNIT_FRAGMENT="/other/monitor.service")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("merge --ff-only", self.gitlog.read_text())
        self.assertIn("up -d --build --force-recreate --no-deps studio", self.commands())
        self.assertFalse(self.unit_stopped.exists())
        self.assertFalse(self.unit_started.exists())

    def test_auth_check_is_read_only_with_foreign_monitor_unit(self):
        result = self.run_switch(
            "auth-check", TEST_UNIT_STATE="loaded",
            TEST_UNIT_FRAGMENT="/other/monitor.service")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("без HTTP Basic", result.stdout)
        self.assertNotIn("up -d", self.commands())
        self.assertFalse(self.unit_started.exists())
        self.assertFalse(self.unit_stopped.exists())

    def test_stop_still_refuses_foreign_monitor_unit(self):
        result = self.run_switch(
            "stop", TEST_UNIT_STATE="loaded",
            TEST_UNIT_FRAGMENT="/other/monitor.service")
        self.assertNotEqual(result.returncode, 0)
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

    def test_missing_env_is_allowed_but_foreign_unit_is_not(self):
        (self.workspace / ".env").unlink()
        result = self.run_switch("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("up -d", self.commands())
        # First start legitimately started the own monitor. Reset that
        # observation before the second run with a foreign unit.
        self.unit_started.unlink(missing_ok=True)
        self.unit_stopped.unlink(missing_ok=True)
        # Starting/inspecting verified Studio must not be blocked by a
        # foreign unit; the unknown unit must NOT be started or stopped.
        result = self.run_switch("start", TEST_UNIT_STATE="loaded",
                                 TEST_UNIT_FRAGMENT="/other/unit")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.unit_started.exists())
        self.assertFalse(self.unit_stopped.exists())
        self.assertNotIn("stop --time", self.commands())

    def test_blank_legacy_password_does_not_block_private_studio(self):
        env_file = self.workspace / ".env"
        env_file.write_text(
            "ACHEWIW_USERNAME=author\nACHEWIW_PASSWORD=\nACHEWIW_MODEL=qwen3:8b\n"
        )
        self.state.write_text("stopped")
        result = self.run_switch("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text(), "running")
        self.assertIn("up -d", self.commands())
        self.assertNotIn("test-private", result.stderr)

    def test_monitor_start_links_only_own_user_unit(self):
        result=self.run_switch("monitor-start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.unit_linked.exists())
        self.assertTrue(self.unit_started.exists())
        self.assertNotIn("up -d", self.commands())
        self.assertNotIn("stop --time", self.commands())

    def test_monitor_start_refuses_foreign_loaded_unit(self):
        result=self.run_switch("monitor-start", TEST_UNIT_STATE="loaded",
                               TEST_UNIT_FRAGMENT="/elsewhere/evil.service")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.unit_started.exists())
        self.assertNotIn("up -d", self.commands())

    def test_restart_only_verified_studio_and_monitor(self):
        result = self.run_switch("restart", TEST_UNIT_STATE="loaded")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("restart --time 60 a11c0ffee", self.commands())
        self.assertTrue(self.unit_started.exists())
        self.assertNotIn("up -d", self.commands())

    def test_update_stopped_only_git_fast_forward(self):
        self.state.write_text("stopped")
        result = self.run_switch("update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text(), "stopped")
        self.assertNotIn("up -d", self.commands())
        self.assertIn("merge --ff-only", self.gitlog.read_text())

    def test_update_running_rebuilds_only_studio(self):
        result = self.run_switch("update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("up -d --build --force-recreate --no-deps studio", self.commands())
        self.assertEqual(self.state.read_text(), "running")
        self.assertNotIn("stop --time", self.commands())

    def test_auth_check_no_password_on_local_and_vpn_listener(self):
        result = self.run_switch("auth-check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("без HTTP Basic", result.stdout)
        self.assertEqual(self.curl_log.read_text().count("http://"), 2)
        self.assertNotIn("up -d", self.commands())

    def test_auth_check_detects_old_basic_image(self):
        result = self.run_switch("auth-check", TEST_HTTP_BASIC="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("запросом пароля", result.stderr)
        self.assertNotIn("up -d", self.commands())
        self.assertNotIn("stop --time", self.commands())

    def test_rebuild_recreates_only_achewiw_studio(self):
        result = self.run_switch("rebuild")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("up -d --build --force-recreate --no-deps studio", self.commands())
        self.assertNotIn("stop --time", self.commands())
        self.assertTrue((self.workspace/"data/.achewiw-deployed-commit").exists())

    def test_rebuild_refuses_foreign_or_unverified_legacy_container(self):
        for extra in ({"TEST_ROGUE":"1"}, {"TEST_LEGACY":"1", "TEST_LEGACY_FOREIGN_MOUNT":"1"}):
            with self.subTest(extra=extra):
                self.log.unlink(missing_ok=True)
                result = self.run_switch("rebuild", **extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("up -d", self.commands())

    def test_verified_legacy_studio_is_migrated_only_by_rebuild(self):
        result = self.run_switch("rebuild", TEST_LEGACY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("старый контейнер ACHEWIW", result.stdout)
        self.assertIn("up -d --build --force-recreate --no-deps studio", self.commands())
        self.assertTrue(self.migrated.exists())
        self.assertTrue((self.workspace / "data/.achewiw-deployed-commit").exists())
        self.assertNotIn("stop --time", self.commands())

    def test_verified_legacy_is_migrated_via_update(self):
        result = self.run_switch("update", TEST_LEGACY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("merge --ff-only", self.gitlog.read_text())
        self.assertIn("up -d --build --force-recreate --no-deps studio", self.commands())
        self.assertTrue(self.migrated.exists())

    def test_orphaned_studio_from_pre_move_workspace_can_migrate(self):
        self.assertFalse(self.old_workspace.exists())
        result = self.run_switch("rebuild", TEST_LEGACY="1", TEST_MOVED_LEGACY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("старый контейнер ACHEWIW", result.stdout)
        self.assertIn("up -d --build --force-recreate --no-deps studio", self.commands())
        self.assertTrue(self.migrated.exists())

    def test_old_workspace_still_exists_cannot_be_adopted(self):
        self.old_workspace.mkdir(parents=True)
        result = self.run_switch("rebuild", TEST_LEGACY="1", TEST_MOVED_LEGACY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("up -d", self.commands())
        self.assertTrue(self.old_workspace.is_dir())

    def test_unverified_legacy_cannot_be_adopted_by_update(self):
        result = self.run_switch("update", TEST_LEGACY="1", TEST_LEGACY_FOREIGN_MOUNT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("up -d", self.commands())

    def test_unidentified_http_200_is_not_accepted(self):
        result = self.run_switch("auth-check", TEST_HTTP_UNIDENTIFIED="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("НЕ подтверждает", result.stderr)
        self.assertNotIn("up -d", self.commands())

    def test_rebuild_fails_if_basic_still_present(self):
        result = self.run_switch("rebuild", TEST_HTTP_BASIC="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("запросом пароля", result.stderr)
        self.assertNotIn("stop --time", self.commands())

    def test_update_refuses_uncommitted_wrong_branch_or_divergence(self):
        variants = (
            {"TEST_GIT_DIRTY": "1"},
            {"TEST_GIT_ORIGIN": "https://github.com/other/achewiw.git"},
            {"TEST_GIT_BRANCH": "main"},
            {"TEST_GIT_DIVERGED": "1"},
        )
        for case in variants:
            with self.subTest(case=case):
                self.gitsha.write_text("oldsha\n")
                self.log.unlink(missing_ok=True)
                result = self.run_switch("update", **case)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.gitsha.read_text().strip(), "oldsha")
                self.assertNotIn("up -d", self.commands())

if __name__ == "__main__":
    unittest.main()
