"""Persistent SQLite generation safety, no Ollama/GPU/server dependencies."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from generation_core import (init_jobs, create_job, update_job, get_job,
                             recent_jobs, mark_interrupted, active_job,
                             estimate_progress)


class DurableGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "achewiw.sqlite3"
        with sqlite3.connect(self.file) as connection:
            connection.row_factory = sqlite3.Row
            init_jobs(connection)

    def connect(self):
        connection = sqlite3.connect(self.file)
        connection.row_factory = sqlite3.Row
        return connection

    def test_restart_preserves_partial_draft_and_marks_interrupted(self):
        with self.connect() as db:
            create_job(db, "a" * 32, "project1", "outline", "qwen3:8b", None)
            update_job(db, "a" * 32, stage="Генерация", output="Начало истории",
                       percent=11, eta_seconds=90, estimated_tokens=20)
        with self.connect() as db:
            self.assertTrue(active_job(db))
            self.assertEqual(get_job(db, "a" * 32)["result"], "Начало истории")
            mark_interrupted(db)
        with self.connect() as db:
            job = get_job(db, "a" * 32)
            self.assertEqual(job["status"], "interrupted")
            self.assertEqual(job["result"], "Начало истории")
            self.assertFalse(active_job(db))
            self.assertEqual(recent_jobs(db, "project1")[0]["id"], "a" * 32)

    def test_single_active_job_enforced_even_with_parallel_db_connections(self):
        with self.connect() as db:
            create_job(db, "a" * 32, "one", "scene", "qwen3:8b", "s1")
        with self.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                create_job(db, "b" * 32, "other", "outline", "qwen3:8b", None)
        with self.connect() as db:
            update_job(db, "a" * 32, status="ready", stage="Готово",
                       output="Доброе утро", percent=100, eta_seconds=0)
            create_job(db, "b" * 32, "other", "audit", "qwen3:8b", None)
        with self.connect() as db:
            self.assertEqual(get_job(db, "a" * 32)["result"], "Доброе утро")
            self.assertEqual(get_job(db, "b" * 32)["status"], "queued")

    def test_suggested_scenes_and_errors_are_persisted(self):
        with self.connect() as db:
            create_job(db, "a" * 32, "project1", "outline", "qwen3:8b", None)
            update_job(db, "a" * 32, status="ready", stage="Готово",
                       output='{"scenes":[]}', suggested_scenes=[{"title": "Ракета"}],
                       percent=100)
        with self.connect() as db:
            self.assertEqual(get_job(db, "a" * 32)["suggested_scenes"], [{"title": "Ракета"}])
            self.assertIsNone(get_job(db, "nonexistent"))
        with self.connect() as db:
            create_job(db, "b" * 32, "project1", "scene", "qwen3:8b", None)
            update_job(db, "b" * 32, status="error", stage="Неудача", error="server down")
        with self.connect() as db:
            self.assertEqual(recent_jobs(db, "project1")[0]["status"], "error")

    def test_percent_and_eta_are_explicit_approximations(self):
        self.assertEqual(estimate_progress(0, 1)[0], None)
        self.assertIsNone(estimate_progress(70, 1)[1])
        percent, eta, tokens = estimate_progress(350, 10)
        self.assertGreater(percent, 0)
        self.assertLess(percent, 100)
        self.assertGreater(eta, 0)
        self.assertGreater(tokens, 0)


if __name__ == "__main__":
    unittest.main()
