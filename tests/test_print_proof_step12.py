"""Step 12: real PDF page measurements and native Word TOC fields."""
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from reportlab.pdfgen import canvas

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from print_proof_preflight import inspect_pdf
from publishing_exports import manuscript_docx
from page_fit_advisor import PageEvidence,recommend


def dummy_pdf(*,claimed=3,include_final_blank=False):
    buf=BytesIO()
    pdf=canvas.Canvas(buf,pagesize=(420,595))
    pdf.setTitle("Synthetic ACHEWIW HDD")
    pdf.drawString(40,480,"Synthetic HDD Title")
    pdf.showPage()
    pdf.drawString(40,480,"Contents")
    # Use Latin titles in this portable fixture, with standard dotted leader.
    pdf.drawString(40,440,f"Chapter One ............................................. {claimed}")
    pdf.showPage()
    pdf.drawString(40,520,"Chapter One")
    for i in range(12):
        pdf.drawString(40,480-i*19,"Disk head read/write cycle.")
    if include_final_blank:
        pdf.showPage()
        pdf.drawString(190,18,"4")
    pdf.save()
    return buf.getvalue()


class PDFPreflightTests(unittest.TestCase):
    def test_measured_pdf_and_declared_toc_page(self):
        r=inspect_pdf(dummy_pdf(),chapter_titles=["Chapter One"],expected_pages=3)
        self.assertEqual(r["page_count"],3)
        self.assertEqual(r["source"],"measured-pdf")
        self.assertFalse(r["manuscript_mutated"])
        self.assertEqual(r["headings"][0]["actual_pdf_page"],3)
        self.assertEqual(r["headings"][0]["toc_page"],3)
        self.assertFalse(any(x["code"]=="TOC_PAGE_MISMATCH" for x in r["findings"]))
        self.assertEqual(r["page_size_pt"],[420.0,595.0])

    def test_outdated_toc_blocks_release(self):
        r=inspect_pdf(dummy_pdf(claimed=2),chapter_titles=["Chapter One"])
        self.assertFalse(r["ready"])
        self.assertIn("TOC_PAGE_MISMATCH",[x["code"] for x in r["findings"]])

    def test_final_empty_page_is_warning_not_destructive_deletion(self):
        r=inspect_pdf(dummy_pdf(include_final_blank=True))
        self.assertEqual(r["page_count"],4)
        self.assertIn("FINAL_EMPTY_PAGE",[x["code"] for x in r["findings"]])
        self.assertTrue(r["requires_editor_review"])

    def test_reject_unknown_page_counts_and_invalid_input(self):
        with self.assertRaises(ValueError):
            inspect_pdf(b"Not a PDF")
        with self.assertRaises(ValueError):
            inspect_pdf(dummy_pdf(),max_pages=2)


class WordTOCTests(unittest.TestCase):
    def test_export_contains_real_updateable_toc_field_not_manual_numbers(self):
        scene=SimpleNamespace(id="scene-a",title="Магнитная головка",text="Данные на пластине")
        # Only the manuscript metadata and scene fields used by chapter_listing
        # are required; default the optional publisher fields.
        class Draft:
            id="hdd-test"
            title="Гантова зебра"
            author_name="Греп Пайп"
            publication_year=2026
            publisher_name=""
            edition_label="r04"
            work_type="technical"
            scenes=[scene]
        blob=manuscript_docx(Draft())
        with ZipFile(BytesIO(blob)) as package:
            document=package.read("word/document.xml").decode()
            settings=package.read("word/settings.xml").decode()
        self.assertIn(' TOC \\o "1-2" \\h \\z \\u ',document)
        self.assertIn("Обновите поле",document)
        self.assertIn("updateFields",settings)
        self.assertNotIn("Магнитная головка ................",document)


class AdvisorTests(unittest.TestCase):
    def test_preserves_intentional_part_break(self):
        self.assertEqual(recommend(PageEvidence(223,0.12,next_page_starts_part=True))["action"],
                         "preserve_break")

    def test_does_not_fill_justification_gaps(self):
        self.assertEqual(recommend(PageEvidence(222,0.44,justified_large_word_spaces=True))["action"],
                         "fix_layout")


if __name__=="__main__":
    unittest.main()
