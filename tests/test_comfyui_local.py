"""Never touch user host on GitHub Actions: test ComfyUI control in fake workspace."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "comfyui-local.sh"


class IsolatedComfyUIControl(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.workspace = root / "achewiw"
        (self.workspace / "data").mkdir(parents=True)
        self.mock_bin = root / "bin"
        self.mock_bin.mkdir()
        self.fake("nvidia-smi", "#!/bin/sh\necho 'NVIDIA GeForce RTX 3060, 550.163.01, 12288 MiB, 19 MiB, 0 %'\n")
        self.fake("curl", "#!/bin/sh\nexit 7\n")
        self.fake("ss", "#!/bin/sh\nexit 0\n")
        self.control = root / "comfyui-local.sh"
        self.control.write_text(SOURCE.read_text().replace("/1tbdisk/achewiw", str(self.workspace)))
        self.env = dict(os.environ, PATH=str(self.mock_bin) + ":" + os.environ.get("PATH", ""))

    def fake(self, name, content):
        path = self.mock_bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_cmd(self, action):
        return subprocess.run(["bash", str(self.control), action], text=True,
                              capture_output=True, env=self.env, timeout=20)

    def test_syntax(self):
        result = subprocess.run(["bash", "-n", str(self.control)], capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_doctor_does_not_install_or_start_anything(self):
        result = self.run_cmd("doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RTX 3060", result.stdout)
        self.assertIn("не отвечает", result.stdout)
        self.assertFalse((self.workspace / "data/comfyui-runtime").exists())

    def test_stop_refuses_without_owned_process(self):
        result = self.run_cmd("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("не найден", result.stderr)
        self.assertFalse((self.workspace / "data/comfyui-runtime").exists())

    def test_start_requires_explicit_install_and_model(self):
        result = self.run_cmd("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("install и model-sdxl", result.stderr)
        self.assertFalse((self.workspace / "data/comfyui-runtime").exists())


if __name__ == "__main__":
    unittest.main()
