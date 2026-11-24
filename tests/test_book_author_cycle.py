"""Author path: safe pause -> edit chapter -> resume the same job, no model.
This checks one production transition, rather than rerunning historic suites.
"""
import importlib.util
from pathlib import Path
import sqlite3
import unittest


def load(name):
    path=Path(__file__).resolve().parents[1]/"app"/name
    spec=importlib.util.spec_from_file_location(name[:-3],path)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

pause=load("book_pause_control.py")
edit=load("book_chapter_intervention.py")


class AuthorCycleTests(unittest.TestCase):
    def test_pause_edit_resume_same_saved_book(self):
        with sqlite3.connect(":memory:") as con:
            con.row_factory=sqlite3.Row
            con.executescript("""
                CREATE TABLE ai_jobs (
                  id TEXT PRIMARY KEY,mode TEXT,status TEXT,stage TEXT,output TEXT,
                  error TEXT,updated_at TEXT,finished_at TEXT
                );
                CREATE TABLE ai_book_chapters (
                  job_id TEXT,scene_id TEXT,editorial_goal TEXT,tension INTEGER,
                  reveal_level TEXT,complete INTEGER,accepted_at TEXT,updated_at TEXT,
                  text TEXT,word_count INTEGER,attempts INTEGER,
                  original_sha256 TEXT,target_words INTEGER
                );
                INSERT INTO ai_jobs VALUES
                  ('book-1','book','running','Глава 1','',NULL,'now',NULL);
                INSERT INTO ai_book_chapters VALUES
                  ('book-1','chapter-1','Ввести героя',2,'hint',0,NULL,'now',
                   'Первый абзац сохранён. Второй абзац сохранён.',7,2,'unchanged-sha',1200);
            """)
            pause.init_pause_controls(con)
            before=tuple(con.execute(
                "SELECT text,word_count,attempts,original_sha256,target_words "
                "FROM ai_book_chapters").fetchone())
            pause.request_pause(con,"book-1")
            self.assertEqual(con.execute(
                "SELECT status FROM ai_jobs").fetchone()[0],"running")
            self.assertTrue(pause.acknowledge_pause(con,"book-1"))
            self.assertEqual(con.execute(
                "SELECT status FROM ai_jobs").fetchone()[0],"interrupted")
            updated=edit.apply_paused_guidance(
                con, job_id="book-1",scene_id="chapter-1",
                expected_goal="Ввести героя",expected_tension=2,
                expected_reveal="hint",goal="Пусть герой проверит улику у двери",
                tension=4,reveal="partial"
            )
            self.assertFalse(updated["generated_prose_changed"])
            self.assertEqual(before,tuple(con.execute(
                "SELECT text,word_count,attempts,original_sha256,target_words "
                "FROM ai_book_chapters").fetchone()))
            # Simulate the existing /resume update: same ID, no new chapter.
            pause.clear_pause(con,"book-1")
            con.execute("UPDATE ai_jobs SET status='queued' WHERE id='book-1'")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0],1)
            self.assertEqual(con.execute(
                "SELECT editorial_goal FROM ai_book_chapters").fetchone()[0],
                "Пусть герой проверит улику у двери")
            self.assertEqual(before,tuple(con.execute(
                "SELECT text,word_count,attempts,original_sha256,target_words "
                "FROM ai_book_chapters").fetchone()))
            self.assertFalse(pause.pause_requested(con,"book-1"))


if __name__=="__main__":
    unittest.main()
