"""Only new recovery behavior is tested; no repeat of R3/Vision/Corel suites."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from hashlib import sha256

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup-book-job.py"
spec = importlib.util.spec_from_file_location("achewiw_book_rescue", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def fixture(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE projects (id TEXT PRIMARY KEY,data TEXT,updated_at TEXT)")
        db.execute("CREATE TABLE ai_jobs (id TEXT PRIMARY KEY,project_id TEXT,"
                   "mode TEXT,model TEXT,status TEXT,stage TEXT,created_at TEXT,"
                   "updated_at TEXT,finished_at TEXT,error TEXT)")
        db.execute("CREATE TABLE ai_book_chapters (job_id TEXT,scene_id TEXT,"
                   "scene_index INTEGER,title TEXT,word_count INTEGER,"
                   "target_words INTEGER,attempts INTEGER,retry_epoch INTEGER,"
                   "complete INTEGER,accepted_at TEXT,text TEXT,updated_at TEXT)")
        db.execute("CREATE TABLE ai_book_stream_buffers (job_id TEXT,scene_id TEXT,"
                   "base_digest TEXT,partial TEXT,retry INTEGER,updated_at TEXT)")
        db.execute("INSERT INTO projects VALUES(?,?,?)",
                   ("p123", '{"id":"p123","title":"Тест"}', "now"))
        db.execute("INSERT INTO ai_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                   ("j123","p123","book","test-model","interrupted","Глава 2","now",
                    "now",None,"some error"))
        db.execute("INSERT INTO ai_book_chapters VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("j123","s123",0,"Глава 1",600,1000,3,0,0,None,
                    "The text stays here without deletion.","now"))
        db.execute("INSERT INTO ai_book_stream_buffers VALUES(?,?,?,?,?,?)",
                   ("j123","s123","digest","Incomplete sentence",1,"now"))


class RescueTests(unittest.TestCase):
    def test_consistent_zip_and_immutability(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/"source.sqlite3"
            archive=Path(d)/"rescue.zip"
            fixture(source)
            before=sha256(source.read_bytes()).hexdigest()
            jobs=tool.list_book_jobs(source)
            self.assertEqual(len(jobs),1)
            self.assertEqual(jobs[0]["job_id"],"j123")
            result=tool.create_archive(source,archive,"j123")
            self.assertFalse(result["publication_snapshot_complete"])
            self.assertEqual(before,sha256(source.read_bytes()).hexdigest())
            with zipfile.ZipFile(archive) as z:
                report=json.loads(z.read("report/job-rescue-report.json"))
                self.assertEqual(report["chapters"][0]["text_sha256"],
                    sha256(b"The text stays here without deletion.").hexdigest())
                self.assertEqual(len(report["stream_buffers"]),1)
                self.assertFalse(report["external_files_archived"])
                self.assertEqual(sha256(z.read(
                    "database/achewiw-consistent.sqlite3")).hexdigest(),
                    report["sqlite_sha256"])
            with self.assertRaises(FileExistsError):
                tool.create_archive(source,archive,"j123")
            self.assertEqual(before,sha256(source.read_bytes()).hexdigest())
            self.assertTrue(archive.exists())

    def test_bad_job_does_not_leave_archive(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/"source.sqlite3"
            archive=Path(d)/"rescue.zip"
            fixture(source)
            with self.assertRaises(ValueError):
                tool.create_archive(source,archive,"wrong")
            self.assertFalse(archive.exists())


if __name__=="__main__":
    unittest.main()
