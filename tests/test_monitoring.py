"""Read-only monitoring parsing: test without NVIDIA, Docker or host access."""
import json
import tempfile
import time
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from monitoring_core import load_snapshot, summarize_ollama_running


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "host.json"
        self.now = time.time()
        self.sample = {
            "schema_version": 1,
            "captured_at": self.now,
            "cpu": {"usage_percent": 37.2, "logical_processors": 32, "load_1": 4},
            "memory": {"usage_percent": 60, "used_bytes": 1234, "total_bytes": 4096},
            "gpu_status": "ok",
            "gpus": [{
                "index": 0, "name": "RTX 3060", "utilization_percent": 20.0,
                "memory_used_bytes": 4194304, "memory_total_bytes": 12582912,
                "temperature_c": 49,
            }],
            "extra_secret": "must not leak",
        }

    def write(self):
        self.file.write_text(json.dumps(self.sample), encoding="utf-8")

    def test_fresh_host_sample_is_sanitized(self):
        self.write()
        data = load_snapshot(self.file, now=self.now + 4)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["gpus"][0]["name"], "RTX 3060")
        self.assertEqual(data["cpu"]["usage_percent"], 37.2)
        self.assertNotIn("extra_secret", json.dumps(data))

    def test_missing_is_not_zero(self):
        data = load_snapshot(self.file, now=self.now)
        self.assertEqual(data["status"], "unavailable")
        self.assertNotIn("cpu", data)

    def test_stale_is_not_presented_as_current(self):
        self.write()
        self.assertEqual(load_snapshot(self.file, now=self.now + 45)["status"], "stale")

    def test_bogus_values_are_not_displayed(self):
        self.sample["cpu"]["usage_percent"] = -11
        self.sample["memory"]["used_bytes"] = "secret"
        self.sample["gpus"][0]["utilization_percent"] = 1200
        self.write()
        data = load_snapshot(self.file, now=self.now)
        self.assertIsNone(data["cpu"]["usage_percent"])
        self.assertIsNone(data["gpus"][0]["utilization_percent"])
        self.assertIsNone(data["memory"]["used_bytes"])

    def test_invalid_file_is_rejected(self):
        self.file.write_text("not json", encoding="utf-8")
        self.assertEqual(load_snapshot(self.file, now=self.now)["status"], "unavailable")

    def test_symlink_is_rejected(self):
        target = Path(self.tmp.name) / "origin"
        self.write()
        self.file.rename(target)
        self.file.symlink_to(target)
        self.assertEqual(load_snapshot(self.file, now=self.now)["status"], "unavailable")

    def test_only_ollama_name_and_vram_exposed(self):
        model = {"name": "qwen3:8b", "size_vram": 3456,
                 "details": {"secret": "never send details"}}
        result = summarize_ollama_running({"models": [model]})
        self.assertEqual(result, [{"name": "qwen3:8b", "vram_bytes": 3456}])


if __name__ == "__main__":
    unittest.main()
