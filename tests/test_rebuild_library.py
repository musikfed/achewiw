"""Offline verification of project-only backed-up story library reset.

No host Docker, camera, Ollama, other SQLite, or actual story data is accessed.
"""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("library_admin",ROOT/"scripts/library_admin.py")
lib=importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)


class StoryResetTests(unittest.TestCase):
    def test_sqlite_backup_precedes_scoped_purge_and_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"achewiw"
            data=root/"data"
            data.mkdir(parents=True)
            db=data/"achewiw.sqlite3"
            other=Path(tmp)/"factory.sqlite3"
            other.write_bytes(b"untouched unrelated file")
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, data TEXT, updated_at TEXT)")
                con.execute("CREATE TABLE ai_jobs(id TEXT PRIMARY KEY, project_id TEXT)")
                con.execute("CREATE TABLE ai_job_reviews(job_id TEXT, review TEXT)")
                con.execute("INSERT INTO projects VALUES('p1','novel','2026')")
                con.execute("INSERT INTO ai_jobs VALUES('a1','p1')")
                con.execute("INSERT INTO ai_job_reviews VALUES('a1','review')")
            with patch.object(lib,"PROJECT",root),patch.object(lib,"DATABASE",db):
                lib.check_scope(db)
                with sqlite3.connect(db) as con:
                    before=lib.counts(con)
                    saved=lib.archive(con,db)
                    self.assertEqual(saved.stat().st_mode&0o777,0o600)
                    with con:
                        lib.purge(con,project_id=None)
                    self.assertEqual(lib.counts(con),{name:0 for name in before})
                with sqlite3.connect(saved) as restored:
                    self.assertEqual(lib.counts(restored),before)
            self.assertEqual(other.read_bytes(),b"untouched unrelated file")

    def test_only_known_tables_and_expected_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"achewiw"
            data=root/"data"
            data.mkdir(parents=True)
            db=data/"achewiw.sqlite3"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, data TEXT, updated_at TEXT)")
                con.execute("CREATE TABLE unrelated_settings(key TEXT, value TEXT)")
                con.execute("INSERT INTO projects VALUES('x','text','today')")
                con.execute("INSERT INTO unrelated_settings VALUES('setting','keep')")
                with con:lib.purge(con,project_id=None)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM projects").fetchone()[0],0)
                self.assertEqual(con.execute("SELECT value FROM unrelated_settings").fetchone()[0],"keep")
            with patch.object(lib,"PROJECT",root),patch.object(lib,"DATABASE",db):
                with self.assertRaises(RuntimeError):
                    lib.check_scope(data/"other.sqlite3")

    def test_rebuild_script_preserves_ownership_and_never_global_prunes(self):
        script=(ROOT/"scripts/rebuild-library.sh").read_text()
        for token in ("--check","--apply","--reset-library","--no-cache",
                      "bash scripts/achewiw-control.sh stop",
                      "bash scripts/achewiw-control.sh update",
                      "library_admin.py --reset-all --confirm DELETE_ALL_ACHEWIW_LIBRARY",
                      "docker compose --project-name achewiw",
                      "bash scripts/achewiw-control.sh start"):
            self.assertIn(token,script)
        self.assertNotIn("docker system prune",script)
        self.assertNotIn("docker compose down",script)
        self.assertNotIn("rm -rf",script)


if __name__=="__main__":
    unittest.main()
