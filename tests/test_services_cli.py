"""Smoke checks for the ACHEWIW-only lifecycle CLI.

No Docker daemon, NVIDIA driver, or running server is needed.
Run: python3 -m unittest discover -s tests -p 'test_services_cli.py'
"""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ServicesCliTests(unittest.TestCase):
    def read_script(self, path: str) -> str:
        p = ROOT / path
        self.assertTrue(p.is_file(), f"Missing {path}")
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return p.read_text(encoding="utf-8")

    def test_unified_service_commands_are_bash_valid_and_scoped(self):
        script = self.read_script("scripts/achewiw-services.sh")
        for cmd in ("status", "start", "stop", "start-all", "studio-start", "studio-stop",
                    "graphics-start", "graphics-stop"):
            self.assertIn(cmd, script)
        self.assertIn("assert_studio_idle", script)
        self.assertIn("assert_controller_idle", script)
        self.assertIn("assert_image_queue_idle", script)
        self.assertIn("studio studio-stop", script)
        self.assertIn("image_engine stop", script)
        self.assertNotIn("docker system prune", script)
        self.assertNotIn("ollama stop", script)
        self.assertNotIn("kill -9", script)

    def test_studio_stop_is_independent_of_foreign_monitor(self):
        script = self.read_script("scripts/achewiw-control.sh")
        self.assertIn("stop|studio-stop|restart", script)
        self.assertIn("stop|monitor-start)", script)
        self.assertIn('verify_owned "$id"', script)

    def test_clean_update_preserves_stopped_state(self):
        script = self.read_script("scripts/update-studio-clean.sh")
        self.assertIn('if [[ -n "$running" ]]; then', script)
        self.assertIn("build --no-cache studio", script)
        self.assertIn("Studio была остановлена", script)
        self.assertIn("bash scripts/achewiw-control.sh rebuild-fresh", script)
        self.assertNotIn("docker compose down", script)


if __name__ == "__main__":
    unittest.main()
