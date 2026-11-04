"""Actual book artifacts and safe SQLite cleanup tests, offline."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import importlib.util
import sqlite3
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from publishing_exports import manuscript_pdf, manuscript_fb2, manuscript_docx


class ExportTests(unittest.TestCase):
    def example(self, content="Первый абзац.\n\nВторой абзац с кириллицей."):
        return SimpleNamespace(
            id="1" * 32, title="Русская техническая книга", work_type="technical",
            scenes=[SimpleNamespace(id="2"*32, title="Глава 1: система", text=content)]
        )

    def test_fb2_contains_only_authored_prose(self):
        data = manuscript_fb2(self.example())
        root = ET.fromstring(data)
        self.assertTrue(root.tag.endswith("FictionBook"))
        self.assertIn("Второй абзац", data.decode())
        self.assertNotIn("QA-отчёт", data.decode())

    def test_docx_is_a_real_office_zip(self):
        blob = manuscript_docx(self.example())
        with ZipFile(BytesIO(blob)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("Глава 1", xml)
            self.assertIn("кириллицей", xml)

    def test_reading_pdf_is_real_pdf(self):
        blob = manuscript_pdf(self.example())
        self.assertTrue(blob.startswith(b"%PDF"))
        self.assertGreater(len(blob), 3000)

    def test_print_pdf_is_real_pdf(self):
        blob = manuscript_pdf(self.example(), print_proof=True)
        self.assertTrue(blob.startswith(b"%PDF"))

    def test_no_fabricated_empty_book(self):
        for fn in (manuscript_pdf, manuscript_fb2, manuscript_docx):
            with self.assertRaises(ValueError):
                fn(self.example(""))


class CleanupTests(unittest.TestCase):
    def test_archives_full_database_before_targeted_purge(self):
        spec = importlib.util.spec_from_file_location(
            "achewiw_library_admin", ROOT / "scripts" / "library_admin.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            database = base / "data" / "achewiw.sqlite3"
            database.parent.mkdir()
            with sqlite3.connect(database) as conn:
                conn.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, data TEXT, updated_at TEXT)")
                conn.execute("CREATE TABLE ai_jobs(id TEXT PRIMARY KEY, project_id TEXT)")
                conn.execute("CREATE TABLE ai_job_reviews(job_id TEXT)")
                conn.execute("INSERT INTO projects VALUES('keep','{}','today')")
                conn.execute("INSERT INTO projects VALUES('drop','{}','today')")
                conn.execute("INSERT INTO ai_jobs VALUES('job','drop')")
                conn.execute("INSERT INTO ai_job_reviews VALUES('job')")
                backup = module.archive(conn, database)
                module.purge(conn, project_id="drop")
                conn.commit()
                self.assertEqual(module.counts(conn)["projects"], 1)
                self.assertEqual(module.counts(conn)["ai_jobs"], 0)
                self.assertEqual(module.counts(conn)["ai_job_reviews"], 0)
            with sqlite3.connect(backup) as original:
                self.assertEqual(module.counts(original)["projects"], 2)
                self.assertEqual(module.counts(original)["ai_jobs"], 1)
            self.assertTrue(backup.exists())


if __name__ == "__main__":
    unittest.main()
