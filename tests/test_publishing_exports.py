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
from manuscript_core import manuscript_epub


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

    def test_docx_uses_author_approved_a5_body_typography(self):
        blob = manuscript_docx(self.example())
        with ZipFile(BytesIO(blob)) as archive:
            styles = ET.fromstring(archive.read("word/styles.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            normal = next(style for style in styles.findall("w:style", ns)
                          if style.get("{%s}styleId" % ns["w"]) == "Normal")
            run = normal.find("w:rPr", ns)
            fonts = run.find("w:rFonts", ns)
            assert fonts.get("{%s}ascii" % ns["w"]) == "Times New Roman"
            size = run.find("w:sz", ns)
            assert size.get("{%s}val" % ns["w"]) == "24"
            props = normal.find("w:pPr", ns)
            assert props.find("w:jc", ns).get("{%s}val" % ns["w"]) == "both"
            spacing = props.find("w:spacing", ns)
            assert spacing.get("{%s}line" % ns["w"]) == "312"
            assert spacing.get("{%s}lineRule" % ns["w"]) == "auto"

    def test_reading_pdf_is_real_pdf(self):
        blob = manuscript_pdf(self.example())
        self.assertTrue(blob.startswith(b"%PDF"))
        self.assertGreater(len(blob), 3000)

    def test_print_pdf_is_real_pdf(self):
        blob = manuscript_pdf(self.example(), print_proof=True)
        self.assertTrue(blob.startswith(b"%PDF"))

    def test_three_chapter_artifacts_keep_order_and_epub_navigation(self):
        titles = ["Глава 1: Устройство", "Глава 2: Развитие", "Глава 3: Практика"]
        project = SimpleNamespace(
            id="1" * 32, title="Проверка книжного выпуска", work_type="technical",
            scenes=[SimpleNamespace(
                id=f"{n}" * 32, title=title,
                text=(f"Раздел {n}. Подробное объяснение устройства. " * 18))
                for n, title in enumerate(titles, 1)],
        )
        docx = manuscript_docx(project)
        with ZipFile(BytesIO(docx)) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
            positions = [document.index(title) for title in titles]
            self.assertEqual(positions, sorted(positions))
        epub = manuscript_epub(project)
        with ZipFile(BytesIO(epub)) as archive:
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
            paths = [f"chapter-{n:03}.xhtml" for n in range(1, 4)]
            self.assertEqual([path in nav for path in paths], [True] * 3)
            self.assertEqual([nav.index(path) for path in paths],
                             sorted(nav.index(path) for path in paths))
            for n, title in enumerate(titles, 1):
                chapter = archive.read(f"EPUB/chapter-{n:03}.xhtml").decode("utf-8")
                self.assertIn(title, chapter)
        pdf = manuscript_pdf(project)
        import fitz
        with fitz.open(stream=pdf, filetype="pdf") as document:
            self.assertGreaterEqual(len(document), 4)
            pages = [page.get_text() for page in document]
            for title in titles:
                self.assertTrue(any(title in text for text in pages),
                                f"PDF is missing chapter title: {title}")
            contents = next((text for text in pages if "Содержание" in text), "")
            for title in titles:
                self.assertIn(title, contents)

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
                conn.commit()
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
