"""Only the new graceful book pause transition is tested, no model calls."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest

path=Path(__file__).resolve().parents[1]/"app"/"book_pause_control.py"
spec=importlib.util.spec_from_file_location("achewiw_pause",path)
pause=importlib.util.module_from_spec(spec)
spec.loader.exec_module(pause)


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.db.row_factory=sqlite3.Row
        self.db.execute("""CREATE TABLE ai_jobs (
            id TEXT PRIMARY KEY,mode TEXT,status TEXT,stage TEXT,
            output TEXT,error TEXT,updated_at TEXT,finished_at TEXT)""")
        self.db.execute("""CREATE TABLE ai_book_chapters (
            job_id TEXT,scene_id TEXT,text TEXT,word_count INTEGER,
            attempts INTEGER,original_sha256 TEXT)""")
        self.db.execute("""INSERT INTO ai_jobs VALUES
            ('job','book','running','Глава 2','сохранено',NULL,'t0',NULL)""")
        self.db.execute("""INSERT INTO ai_book_chapters VALUES
            ('job','scene','Уже написанный текст',60,3,'fingerprint')""")
        pause.init_pause_controls(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def original(self):
        return tuple(self.db.execute(
            "SELECT text,word_count,attempts,original_sha256 FROM ai_book_chapters"
        ).fetchone())

    def test_request_does_not_cancel_or_modify_text(self):
        before=self.original()
        result=pause.request_pause(self.db,"job")
        self.assertTrue(result["pause_requested"])
        row=self.db.execute("SELECT status FROM ai_jobs WHERE id='job'").fetchone()
        self.assertEqual(row["status"],"running")
        self.assertEqual(self.original(),before)
        self.assertTrue(pause.pause_requested(self.db,"job"))

    def test_acknowledge_transitions_to_existing_resume_state(self):
        before=self.original()
        pause.request_pause(self.db,"job")
        self.assertTrue(pause.acknowledge_pause(self.db,"job"))
        self.assertFalse(pause.pause_requested(self.db,"job"))
        row=self.db.execute("SELECT status,stage FROM ai_jobs WHERE id='job'").fetchone()
        self.assertEqual(row["status"],"interrupted")
        self.assertIn("Пауза",row["stage"])
        self.assertEqual(before,self.original())
        self.assertFalse(pause.acknowledge_pause(self.db,"job"))

    def test_idempotent_pause_and_clearing_on_resume(self):
        pause.request_pause(self.db,"job")
        pause.request_pause(self.db,"job")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM ai_book_pause_requests").fetchone()[0],1)
        pause.acknowledge_pause(self.db,"job")
        pause.clear_pause(self.db,"job")
        self.db.execute("UPDATE ai_jobs SET status='queued' WHERE id='job'")
        self.assertFalse(pause.pause_requested(self.db,"job"))

    def test_reject_non_active_or_non_book_job(self):
        for state in ("interrupted","ready","error"):
            with self.subTest(state=state):
                self.db.execute("UPDATE ai_jobs SET status=?",(state,))
                with self.assertRaises(pause.PauseConflict):
                    pause.request_pause(self.db,"job")
        self.db.execute("UPDATE ai_jobs SET mode='audit',status='running'")
        with self.assertRaises(pause.PauseConflict):
            pause.request_pause(self.db,"job")

    def test_does_not_acknowledge_finished_job(self):
        pause.request_pause(self.db,"job")
        self.db.execute("UPDATE ai_jobs SET status='ready' WHERE id='job'")
        self.assertFalse(pause.acknowledge_pause(self.db,"job"))
        self.assertEqual(self.db.execute(
            "SELECT status FROM ai_jobs WHERE id='job'").fetchone()[0],"ready")

if __name__=="__main__":
    unittest.main()
