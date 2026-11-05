"""Host collector unit tests: no real nvidia-smi, no writes to /1tbdisk."""
from __future__ import annotations
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "scripts" / "achewiw_host_monitor.py"
spec = importlib.util.spec_from_file_location("achewiw_host_monitor", PATH)
monitor = importlib.util.module_from_spec(spec)
sys.modules["achewiw_host_monitor"] = monitor
spec.loader.exec_module(monitor)


class CollectorTests(unittest.TestCase):
    def test_parses_nvidia_vram_mib_into_bytes(self):
        completed = subprocess.CompletedProcess(
            args=["nvidia-smi"], returncode=0,
            stdout="0, NVIDIA GeForce RTX 3060, 87, 5120, 12288, 64\n",
            stderr="")
        with patch.object(monitor.subprocess, "run", return_value=completed) as call:
            cards, status = monitor.gpu_sample()
        self.assertEqual(status, "ok")
        self.assertEqual(cards[0]["utilization_percent"], 87)
        self.assertEqual(cards[0]["memory_used_bytes"], 5120 * 1024**2)
        self.assertEqual(cards[0]["memory_total_bytes"], 12288 * 1024**2)
        self.assertEqual(cards[0]["temperature_c"], 64)
        self.assertIn("--query-gpu=", call.call_args.args[0][1])

    def test_no_nvidia_driver_is_missing_not_zero(self):
        with patch.object(monitor.subprocess, "run", side_effect=FileNotFoundError):
            cards, status = monitor.gpu_sample()
        self.assertEqual(status, "unavailable")
        self.assertEqual(cards, [])

    def test_nvidia_na_temperature_does_not_crash(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="0, GPU, 0, 0, 12288, N/A\n", stderr="")
        with patch.object(monitor.subprocess, "run", return_value=completed):
            cards, status = monitor.gpu_sample()
        self.assertEqual(status, "ok")
        self.assertIsNone(cards[0]["temperature_c"])

    def test_cpu_delta(self):
        with patch.object(monitor, "_cpu_times", side_effect=[(1000, 700), (1100, 730)]), \
             patch.object(monitor.time, "sleep"), \
             patch.object(monitor.os, "getloadavg", return_value=(1., 2., 3.)):
            data = monitor.cpu_sample()
        self.assertEqual(data["usage_percent"], 70.0)
        self.assertEqual(data["load_1"], 1.0)


if __name__ == "__main__":
    unittest.main()
