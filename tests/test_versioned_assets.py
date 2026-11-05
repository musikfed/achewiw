"""Safety and regression tests for Studio cache-busted static assets."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify-versioned-assets.py"
spec = importlib.util.spec_from_file_location("achewiw_assets", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AssetVerifierTests(unittest.TestCase):
    def make_fixture(self, directory: Path, overrides=None):
        overrides = overrides or {}
        static = directory / "static"
        static.mkdir()
        tags = []
        for asset in sorted(module.EXPECTED):
            file_name = asset.removeprefix("/static/")
            (static / file_name).write_text("/* test */", encoding="utf-8")
            ver = overrides.get(asset, "20261105-conveyor-v1" if file_name == "conveyor.js" else "20261105-editorial-v19")
            src = asset + "?v=" + ver
            if file_name.endswith(".css"):
                tags.append(f'<link rel="stylesheet" href="{src}">')
            else:
                tags.append(f'<script src="{src}"></script>')
        index = static / "index.html"
        index.write_text("<!doctype html><html><head>" + "".join(tags) +
                         "</head><body></body></html>", encoding="utf-8")
        return index, static

    def test_current_release_passes(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("6 CSS/JS", result.stdout)
        self.assertIn("конвейер=", result.stdout)

    def test_different_conveyor_version_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            index, static = self.make_fixture(Path(tmp))
            shared, versions = module.check_assets(index, static)
            self.assertEqual(shared, "20261105-editorial-v19")
            self.assertEqual(versions["/static/conveyor.js"], "20261105-conveyor-v1")

    def test_divergent_editorial_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            index, static = self.make_fixture(
                Path(tmp), {"/static/app.js": "accidentally-outdated"}
            )
            with self.assertRaisesRegex(ValueError, "Непоследовательные версии"):
                module.check_assets(index, static)

    def test_missing_or_duplicate_resource_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            index, static = self.make_fixture(Path(tmp))
            source = index.read_text(encoding="utf-8")
            index.write_text(source.replace('<script src="/static/conveyor.js?v=20261105-conveyor-v1"></script>', ""), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Ожидалось 6"):
                module.check_assets(index, static)
            index.write_text(source + '<script src="/static/conveyor.js?v=extra"></script>', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Ожидалось 6"):
                module.check_assets(index, static)


if __name__ == "__main__":
    unittest.main()
