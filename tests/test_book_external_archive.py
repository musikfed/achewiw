"""A1.3 targeted external-book-asset integration tests; no Ollama/GPU/server."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from hashlib import sha256


def module(filename, name):
    path = Path(__file__).resolve().parents[1] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


backup = module("backup-book-job.py", "achewiw_backup_job")
external = module("archive-book-external.py", "achewiw_external_asset_archive")


def fixture(db_path):
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, data TEXT)")
        db.execute("CREATE TABLE ai_jobs (id TEXT PRIMARY KEY, project_id TEXT,"
                   "mode TEXT, model TEXT, status TEXT, stage TEXT,"
                   "created_at TEXT, updated_at TEXT, finished_at TEXT, error TEXT)")
        db.execute("CREATE TABLE ai_book_chapters (job_id TEXT, scene_id TEXT,"
                   "scene_index INTEGER, title TEXT, target_words INTEGER,"
                   "word_count INTEGER, attempts INTEGER, retry_epoch INTEGER,"
                   "complete INTEGER, accepted_at TEXT, text TEXT, updated_at TEXT)")
        db.execute("INSERT INTO projects VALUES(?,?)",
                   ("p123",json.dumps({"id":"p123","title":"Книга"})))
        db.execute("INSERT INTO ai_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                   ("j123","p123","book","local","interrupted","Глава 5",
                    "now","now",None,None))
        db.execute("INSERT INTO ai_book_chapters VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("j123","s123",0,"Глава 1",2000,1200,2,0,0,None,
                    "До следующей главы", "now"))


class ExternalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "original.sqlite3"
        fixture(self.db)
        self.before = sha256(self.db.read_bytes()).hexdigest()
        self.rescue = self.root / "rescue.zip"
        backup.create_archive(self.db, self.rescue, "j123")
        self.rescue_hash = sha256(self.rescue.read_bytes()).hexdigest()
        self.images = self.root / "illustrations"
        self.images.mkdir()
        (self.images / "cover.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        (self.images / "spread.png").write_bytes(b"TEST PNG FIXTURE")
        self.result = self.root / "complete-scope.zip"

    def tearDown(self):
        self.temp.cleanup()

    def test_external_archive_preserves_originals_and_manifests(self):
        result = external.create_extended_archive(self.rescue, self.result, [
            "art=" + str(self.images)
        ])
        self.assertEqual(result["assets"], 2)
        self.assertFalse(result["publication_snapshot_complete"])
        self.assertEqual(result["sha256"], sha256(self.result.read_bytes()).hexdigest())
        with zipfile.ZipFile(self.result) as z:
            manifest = json.loads(z.read("report/external-manifest.json"))
            self.assertFalse(manifest["coverage_verified"])
            self.assertFalse(manifest["publication_snapshot_complete"])
            files = manifest["files"]
            self.assertEqual(len(files), 2)
            for item in files:
                raw = z.read(item["archive_path"])
                self.assertEqual(len(raw), item["size_bytes"])
                self.assertEqual(sha256(raw).hexdigest(), item["sha256"])
            self.assertIn("database/achewiw-consistent.sqlite3",z.namelist())
        self.assertEqual(self.rescue_hash,sha256(self.rescue.read_bytes()).hexdigest())
        self.assertEqual(self.before,sha256(self.db.read_bytes()).hexdigest())

    def test_existing_output_is_never_overwritten(self):
        self.result.write_bytes(b"KEEP EXISTING")
        with self.assertRaises(FileExistsError):
            external.create_extended_archive(self.rescue,self.result,[
                "art="+str(self.images)])
        self.assertEqual(self.result.read_bytes(),b"KEEP EXISTING")

    def test_invalid_scope_does_not_create_output(self):
        for name in ("../oops", "a/b", "slash\\name", "scope space"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    external.create_extended_archive(self.rescue,self.result,[
                        name+"="+str(self.images)])
                self.assertFalse(self.result.exists())

    def test_does_not_follow_symlinks(self):
        link = self.images / "linked.txt"
        try:
            link.symlink_to(self.db)
        except (NotImplementedError,OSError):
            self.skipTest("Symlinks unavailable on this host")
        with self.assertRaises(ValueError):
            external.create_extended_archive(self.rescue,self.result,[
                "art="+str(self.images)])
        self.assertFalse(self.result.exists())

    def test_bad_source_archive_does_not_create_output(self):
        corrupt=self.root/"fake.zip"
        with zipfile.ZipFile(corrupt,"w") as z:
            z.writestr("database/achewiw-consistent.sqlite3",b"wrong")
            z.writestr("report/job-rescue-report.json",
                       json.dumps({"sqlite_sha256":"wrong"}))
        with self.assertRaises(ValueError):
            external.create_extended_archive(corrupt,self.result,[
                "art="+str(self.images)])
        self.assertFalse(self.result.exists())


if __name__ == "__main__":
    unittest.main()
