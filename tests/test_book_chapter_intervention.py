"""Targeted author steering regression: no repeat of earlier Studio tests."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest

path=Path(__file__).resolve().parents[1]/"app"/"book_chapter_intervention.py"
spec=importlib.util.spec_from_file_location("book_chapter_intervention",path)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class ChapterInterventionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.db.row_factory=sqlite3.Row
        self.db.execute("CREATE TABLE ai_jobs (id TEXT PRIMARY KEY,mode TEXT,status TEXT)")
        self.db.execute("CREATE TABLE ai_book_chapters (job_id TEXT,scene_id TEXT,"
                        "editorial_goal TEXT,tension INTEGER,reveal_level TEXT,"
                        "complete INTEGER,accepted_at TEXT,updated_at TEXT,"
                        "text TEXT,word_count INTEGER,attempts INTEGER,"
                        "original_sha256 TEXT,target_words INTEGER)")
        self.db.execute("INSERT INTO ai_jobs VALUES ('job','book','interrupted')")
        self.db.execute("INSERT INTO ai_book_chapters VALUES "
                        "('job','scene','old goal',3,'hint',0,NULL,'old',"
                        "'Existing paragraphs',23,7,'original',1500)")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def apply(self,**overrides):
        data=dict(job_id="job",scene_id="scene",expected_goal="old goal",
                  expected_tension=3,expected_reveal="hint",
                  goal="Add a new scene beat",tension=4,reveal="partial")
        data.update(overrides)
        return mod.apply_paused_guidance(self.db,**data)

    def test_changes_only_future_instructions(self):
        before=self.db.execute("SELECT text,word_count,attempts,"
                               "original_sha256,target_words FROM ai_book_chapters").fetchone()
        result=self.apply()
        after=self.db.execute("SELECT text,word_count,attempts,"
                              "original_sha256,target_words FROM ai_book_chapters").fetchone()
        self.assertEqual(tuple(before),tuple(after))
        self.assertEqual(result["editorial_goal"],"Add a new scene beat")
        self.assertFalse(result["generated_prose_changed"])
        row=self.db.execute("SELECT editorial_goal,tension,reveal_level "
                            "FROM ai_book_chapters").fetchone()
        self.assertEqual(tuple(row),("Add a new scene beat",4,"partial"))

    def test_stale_browser_rejected(self):
        self.apply()
        with self.assertRaises(mod.InterventionConflict):
            self.apply()
        row=self.db.execute("SELECT editorial_goal FROM ai_book_chapters").fetchone()
        self.assertEqual(row[0],"Add a new scene beat")

    def test_running_and_ready_blocked(self):
        for state in ("queued","running","ready"):
            self.db.execute("UPDATE ai_jobs SET status=?",(state,))
            with self.subTest(state=state),self.assertRaises(mod.InterventionConflict):
                self.apply()

    def test_completed_or_accepted_blocked(self):
        self.db.execute("UPDATE ai_book_chapters SET complete=1")
        with self.assertRaises(mod.InterventionConflict):
            self.apply()
        self.db.execute("UPDATE ai_book_chapters SET complete=0,accepted_at='now'")
        with self.assertRaises(mod.InterventionConflict):
            self.apply()

    def test_wrong_scene_and_invalid_guidance(self):
        with self.assertRaises(mod.InterventionConflict):
            self.apply(scene_id="different")
        for invalid in ("","   ","a"*501):
            with self.subTest(text=invalid[:20]),self.assertRaises(ValueError):
                self.apply(goal=invalid)
        row=self.db.execute("SELECT text,editorial_goal FROM ai_book_chapters").fetchone()
        self.assertEqual(tuple(row),("Existing paragraphs","old goal"))

if __name__=="__main__":
    unittest.main()
