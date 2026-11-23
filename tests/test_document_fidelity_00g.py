"""00-Г: synthetic, non-destructive document fidelity regressions.

No author manuscripts, live SQLite databases, GPU services or network calls.
Known failures use strict xfail so CI exposes them without presenting them as fixes.
Run: PYTHONPATH=app python3 -m pytest -q tests/test_document_fidelity_00g.py
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile
import re

import fitz
import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Cm
from PIL import Image

import document_workshop as workshop


def _png(color="orange") -> bytes:
    out = BytesIO()
    Image.new("RGB", (64, 48), color).save(out, format="PNG")
    return out.getvalue()


def _docx_with_complex_content() -> tuple[bytes, bytes]:
    """Build a synthetic engineering chapter with real image, caption and XML objects."""
    picture = _png()
    doc = Document()
    doc.core_properties.title = "Испытание издательского конвейера"
    doc.add_heading("Глава 1. Испытательный стенд", 1)
    doc.add_paragraph("Технический текст: UTF-8, кириллица, № 001 и E=mc².")
    image_p = doc.add_paragraph()
    image_p.add_run().add_picture(BytesIO(picture), width=Cm(3))
    doc.add_paragraph("Рисунок 1 — Иллюстрация испытательного стенда", style="Caption")
    tab = doc.add_table(rows=2, cols=2)
    tab.cell(0, 0).text = "Параметр"
    tab.cell(0, 1).text = "Значение"
    tab.cell(1, 0).text = "Поток"
    tab.cell(1, 1).text = "32"
    doc.sections[0].header.paragraphs[0].text = "Верхний колонтитул"
    doc.sections[0].footer.paragraphs[0].text = "Нижний колонтитул"

    # A genuine OOXML hyperlink (plain paragraph.text does not expose its target).
    link_p = doc.add_paragraph("Первичный источник: ")
    link_id = doc.part.relate_to("https://example.org/source", RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), link_id)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Открыть описание"
    run.append(text)
    link.append(run)
    link_p._p.append(link)

    # Synthetic OfficeMath element, no evaluation of the formula.
    math_p = doc.add_paragraph("Формула: ")
    math = OxmlElement("m:oMath")
    math_run = OxmlElement("m:r")
    math_text = OxmlElement("m:t")
    math_text.text = "E=mc²"
    math_run.append(math_text)
    math.append(math_run)
    math_p._p.append(math)

    out = BytesIO()
    doc.save(out)
    return out.getvalue(), picture


def _media(data: bytes) -> dict[str, str]:
    with ZipFile(BytesIO(data)) as archive:
        return {
            name: sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if name.startswith("word/media/") and not name.endswith("/")
        }


def _word_xml(data: bytes) -> str:
    with ZipFile(BytesIO(data)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


@pytest.mark.parametrize("preset", ["preserve", "technical", "book"])
def test_docx_roundtrip_retains_figure_caption_table_hyperlink_math(preset):
    original, picture = _docx_with_complex_content()
    signature = sha256(original).hexdigest()
    converted = workshop.transform_docx(original, {
        "preset": preset,
        "auto_headings": True,
    })
    doc = Document(BytesIO(converted))
    text = "\n".join(p.text for p in doc.paragraphs)
    word_xml = _word_xml(converted)
    assert sha256(original).hexdigest() == signature, "Source DOCX must be untouched"
    assert "Рисунок 1 — Иллюстрация испытательного стенда" in text
    assert "Глава 1. Испытательный стенд" in text
    assert doc.tables[0].cell(1, 1).text == "32"
    assert doc.sections[0].header.paragraphs[0].text == "Верхний колонтитул"
    assert doc.sections[0].footer.paragraphs[0].text == "Нижний колонтитул"
    assert _media(original) == _media(converted)
    assert sha256(picture).hexdigest() in _media(converted).values()
    assert "w:hyperlink" in word_xml and "Открыть описание" in word_xml
    assert "m:oMath" in word_xml and "E=mc²" in word_xml
    with ZipFile(BytesIO(converted)) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "https://example.org/source" in rels


@pytest.mark.parametrize("protected_tag", ["drawing", "hyperlink", "oMath"])
def test_docx_replacement_rejects_complex_paragraph(protected_tag):
    data, _ = _docx_with_complex_content()
    doc = Document(BytesIO(data))
    exact_tag = qn({
        "drawing": "w:drawing",
        "hyperlink": "w:hyperlink",
        "oMath": "m:oMath",
    }[protected_tag])
    index = next(
        i for i, para in enumerate(doc.paragraphs)
        if any(True for _ in para._p.iter(exact_tag))
    )
    with pytest.raises(workshop.DocumentWorkshopError, match="Нельзя менять текст абзаца"):
        workshop.transform_docx(
            data, {"preset": "preserve", "replacements": {str(index): "УДАЛИТЬ"}}
        )


def test_docx_inspection_reports_tables_but_no_false_figure_inventory():
    data, _ = _docx_with_complex_content()
    info = workshop.preview(data, "technical.docx")
    assert info["type"] == "docx"
    assert info["tables"] == 1
    assert info["paragraph_count"] >= 5
    assert "Рисунок 1" in " ".join(x["text"] for x in info["paragraphs"])
    # R3.1 now inventories inline pictures for editing. The count is grounded
    # in actual OOXML blips, not a guess from caption text.
    assert len(info["images"]) == 1
    assert info["images"][0]["width_px"] == 64
    assert info["images"][0]["height_px"] == 48


def _pdf_with_two_columns_and_png() -> bytes:
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((40, 70), "LEFT-ONE: overview and introduction.")
    page.insert_text((40, 130), "LEFT-TWO: methodology and methods.")
    page.insert_text((320, 70), "RIGHT-ONE: measurement results.")
    page.insert_text((320, 130), "RIGHT-TWO: final discussion.")
    page.insert_image(fitz.Rect(40, 200, 210, 330), stream=_png())
    page.insert_text((40, 350), "Figure 1 - experimental sample and caption.")
    result = pdf.tobytes()
    pdf.close()
    return result


def test_pdf_original_pass_through_is_byte_identical():
    original = _pdf_with_two_columns_and_png()
    output, name, mime = workshop.export(
        original, "source.pdf", {"output_name": "Проверка"}, "pdf"
    )
    assert output == original
    assert name == "Проверка.pdf"
    assert mime == "application/pdf"


def test_pdf_image_is_present_in_synthetic_source():
    original = _pdf_with_two_columns_and_png()
    with fitz.open(stream=original, filetype="pdf") as pdf:
        assert len(pdf[0].get_images(full=True)) == 1
    converted, _, _ = workshop.export(original, "source.pdf", {}, "docx")
    with ZipFile(BytesIO(converted)) as archive:
        assert archive.testzip() is None
    text = " ".join(p.text for p in Document(BytesIO(converted)).paragraphs)
    assert "Figure 1" in text


def test_pdf_to_docx_must_preserve_images():
    original = _pdf_with_two_columns_and_png()
    converted, _, _ = workshop.export(original, "source.pdf", {}, "docx")
    assert _media(converted), "PDF figure was silently discarded in editable DOCX"


def test_pdf_to_docx_must_preserve_two_column_reading_order():
    original = _pdf_with_two_columns_and_png()
    converted, _, _ = workshop.export(original, "source.pdf", {}, "docx")
    text = " ".join(p.text for p in Document(BytesIO(converted)).paragraphs)
    positions = [text.index(tag) for tag in
                 ("LEFT-ONE", "LEFT-TWO", "RIGHT-ONE", "RIGHT-TWO")]
    assert positions == sorted(positions), "Reading order was changed"


def test_scan_only_pdf_is_rejected_without_ocr():
    pdf = fitz.open()
    pdf.new_page().insert_image(fitz.Rect(40, 40, 400, 400), stream=_png())
    source = pdf.tobytes()
    pdf.close()
    info = workshop.preview(source, "scan.pdf")
    assert any("OCR" in warning for warning in info["warnings"])
    with pytest.raises(workshop.DocumentWorkshopError, match="OCR"):
        workshop.pdf_to_docx(source)


def test_preview_cutoff_does_not_truncate_exported_docx():
    doc = Document()
    for i in range(3005):
        doc.add_paragraph("Контрольный абзац номер " + str(i))
    out = BytesIO()
    doc.save(out)
    source = out.getvalue()
    preview = workshop.preview(source, "long.docx")
    assert preview["paragraph_count"] == 3005
    assert len(preview["paragraphs"]) == 3000
    assert any("3000" in w for w in preview["warnings"])
    revised = workshop.transform_docx(source, {"preset": "preserve"})
    result = Document(BytesIO(revised))
    assert len(result.paragraphs) == 3005
    assert result.paragraphs[-1].text == "Контрольный абзац номер 3004"


def test_pdf_more_than_400_pages_is_rejected():
    pdf = fitz.open()
    for _ in range(401):
        pdf.new_page()
    original = pdf.tobytes()
    pdf.close()
    with pytest.raises(workshop.DocumentWorkshopError, match="400"):
        workshop.preview(original, "long.pdf")
