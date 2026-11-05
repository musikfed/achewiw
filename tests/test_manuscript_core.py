"""Offline book exports: no Docker, Ollama or PostgreSQL required."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_STORED
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from manuscript_core import chapter_listing, manuscript_markdown, manuscript_epub


class ManuscriptTests(unittest.TestCase):
    def project(self):
        return SimpleNamespace(
            id="e" * 32, title='Космо-мартышки <script>alert(1)</script>',
            premise="Это тайное техническое задание, не текст книги",
            scenes=[
                SimpleNamespace(id="1"*32, title='Прибытие & начало',
                                text='Мартышка увидела <ракета>.\nВсё началось.\n\n"Доброе утро!"'),
                SimpleNamespace(id="2"*32, title="Сообщение",
                                text=""),
                SimpleNamespace(id="3"*32, title='Возвращение',
                                text='Дом. Конец приключений.')
            ]
        )

    def test_reader_counts_written_and_unwritten_scenes(self):
        data = chapter_listing(self.project())
        self.assertEqual(data["total_scenes"], 3)
        self.assertEqual(data["written_scenes"], 2)
        self.assertEqual([x["index"] for x in data["chapters"]], [1, 3])
        self.assertEqual([x["title"] for x in data["unwritten"]], ["Сообщение"])
        self.assertNotIn("Техническое задание", str(data))

    def test_clean_markdown_contains_only_approved_prose(self):
        md = manuscript_markdown(self.project())
        self.assertIn("## 1. Прибытие & начало", md)
        self.assertIn("## 3. Возвращение", md)
        self.assertNotIn("## 2. Сообщение", md)
        self.assertNotIn("Это тайное техническое задание", md)
        self.assertNotIn("QA", md)

    def test_epub_is_valid_zip_with_mimetype_first_and_xhtml_escaped(self):
        raw = manuscript_epub(self.project())
        with ZipFile(BytesIO(raw)) as z:
            self.assertEqual(z.namelist()[0], "mimetype")
            self.assertEqual(z.getinfo("mimetype").compress_type, ZIP_STORED)
            self.assertEqual(z.read("mimetype"), b"application/epub+zip")
            opf = z.read("EPUB/content.opf").decode("utf-8")
            first = z.read("EPUB/chapter-001.xhtml").decode("utf-8")
            self.assertIn("Прибытие &amp; начало", first)
            self.assertIn("&lt;ракета&gt;", first)
            self.assertNotIn("<script>", opf)
            self.assertIn("&lt;script&gt;", opf)
            self.assertNotIn("Техническое задание", first)
            self.assertNotIn("QA", first)
            self.assertIn('nav.xhtml', z.namelist()[3] + opf)
            self.assertIn('version="3.0"', opf)

    def test_unwritten_project_has_no_fake_book(self):
        project=self.project()
        for scene in project.scenes:
            scene.text=""
        self.assertFalse(chapter_listing(project)["chapters"])
        with self.assertRaises(ValueError):
            manuscript_markdown(project)
        with self.assertRaises(ValueError):
            manuscript_epub(project)


if __name__ == "__main__":
    unittest.main()
