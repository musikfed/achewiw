"""R1: confirm preserved embedded pictures, ordered columns and explicit loss consent.

All inputs synthesized in memory; no manuscript, database, cloud or server access.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import fitz
import pytest
from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
import document_workshop as workshop
import main


def _picture():
    stream = BytesIO()
    Image.new("RGB", (65, 49), "#ee8844").save(stream, "PNG")
    return stream.getvalue()


def _build_pdf(*, add_vector=False, add_link=False, add_image=True, columns=True):
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((40, 75), "LEFT FIRST: text has the right reading order.")
    page.insert_text((40, 125), "LEFT SECOND: another text block in same column.")
    if columns:
        page.insert_text((320, 75), "RIGHT FIRST: second column must follow left.")
        page.insert_text((320, 125), "RIGHT SECOND: the final part of the article.")
    if add_image:
        page.insert_image(fitz.Rect(40, 175, 175, 276), stream=_picture())
        page.insert_text((40, 301), "Figure 1 - Sample picture caption.")
    if add_vector:
        page.draw_rect(fitz.Rect(41, 410, 220, 470), color=(0, 0, 0))
    if add_link:
        page.insert_link({
            "kind": fitz.LINK_URI, "from": fitz.Rect(41, 60, 160, 80),
            "uri": "https://example.org",
        })
    contents = pdf.tobytes()
    pdf.close()
    return contents


def _find_embedded_images(docx):
    with ZipFile(BytesIO(docx)) as z:
        return [z.read(name) for name in z.namelist()
                if name.startswith("word/media/") and not name.endswith("/")]


def test_r1_preserves_raster_pixels_and_caption_sequence_and_columns():
    src = _build_pdf()
    digest = sha256(src).hexdigest()
    result, _, _ = workshop.export(src, "test.pdf", {"preset": "technical"}, "docx")
    assert sha256(src).hexdigest() == digest
    images = _find_embedded_images(result)
    assert len(images) == 1
    with Image.open(BytesIO(images[0])) as im:
        assert im.size == (65, 49)
        assert im.convert("RGB").getpixel((10, 10)) == (238, 136, 68)
    doc = Document(BytesIO(result))
    text = "\n".join(x.text for x in doc.paragraphs)
    for marker in ("LEFT FIRST", "LEFT SECOND", "RIGHT FIRST", "RIGHT SECOND", "Figure 1"):
        assert marker in text
    assert text.index("LEFT FIRST") < text.index("LEFT SECOND") < text.index("Figure 1")
    assert text.index("Figure 1") < text.index("RIGHT FIRST") < text.index("RIGHT SECOND")
    caption = next(i for i, p in enumerate(doc.paragraphs) if p.text.startswith("Figure 1"))
    assert caption > 0
    assert "w:drawing" in doc.paragraphs[caption - 1]._p.xml
    assert doc.paragraphs[caption].style.name == "Caption"


def test_r1_preview_discloses_image_inventory():
    src = _build_pdf(add_vector=True)
    info = workshop.preview(src, "complex.pdf")
    assert info["type"] == "pdf"
    assert info["images"] >= 1
    assert info["vector_pages"] == 1
    assert any("вектор" in item for item in info["warnings"])


@pytest.mark.parametrize("complexity", ["vector", "link"])
def test_r1_complex_pdf_fails_closed_without_confirmed_loss(complexity):
    src = _build_pdf(add_vector=complexity == "vector",
                     add_link=complexity == "link")
    with pytest.raises(workshop.DocumentWorkshopError, match="PDF содержит"):
        workshop.export(src, "complex.pdf", {}, "docx")
    # Choosing text-only WITHOUT acknowledging the loss is still rejected.
    with pytest.raises(workshop.DocumentWorkshopError, match="подтвержден"):
        workshop.export(src, "complex.pdf", {"pdf_mode": "text_only"}, "docx")
    explicit, _, _ = workshop.export(
        src, "complex.pdf",
        {"pdf_mode": "text_only", "confirm_text_loss": True}, "docx",
    )
    assert Document(BytesIO(explicit)).paragraphs
    assert _find_embedded_images(explicit) == []


def test_r1_text_only_needs_boolean_true_not_string():
    src = _build_pdf()
    with pytest.raises(workshop.DocumentWorkshopError):
        workshop.export(src, "simple.pdf", {
            "pdf_mode": "text_only", "confirm_text_loss": "yes"
        }, "docx")


def test_r1_plain_text_pdf_still_convertible_without_opt_in():
    src = _build_pdf(add_image=False, columns=False)
    out, _, _ = workshop.export(src, "plain.pdf", {}, "docx")
    text = " ".join(x.text for x in Document(BytesIO(out)).paragraphs)
    assert "LEFT FIRST" in text and "LEFT SECOND" in text


def test_r1_original_pdf_passthrough_never_converts_or_drops_image():
    src = _build_pdf(add_vector=True)
    out, _, mime = workshop.export(src, "graphic.pdf", {}, "pdf")
    assert out == src and mime == "application/pdf"


def test_r1_public_api_rejects_complex_pdf_without_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.sqlite3")
    src = _build_pdf(add_vector=True)
    with TestClient(main.app, base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1", 12345)) as client:
        inspect = client.post("/api/docwork/inspect",
                              files={"file": ("case.pdf", src, "application/pdf")})
        assert inspect.status_code == 200
        assert inspect.json()["vector_pages"] == 1
        rejected = client.post(
            "/api/docwork/export",
            data={"target": "docx", "options": "{}"},
            files={"file": ("case.pdf", src, "application/pdf")},
        )
        assert rejected.status_code == 422
        explicit = client.post(
            "/api/docwork/export",
            data={"target": "docx", "options":
                  '{"pdf_mode":"text_only","confirm_text_loss":true}'},
            files={"file": ("case.pdf", src, "application/pdf")},
        )
        assert explicit.status_code == 200 and explicit.content.startswith(b"PK")


def test_r1_ui_exposes_explicit_loss_acknowledgment():
    html = (main.STATIC_DIR / "docwork.html").read_text(encoding="utf-8")
    js = (main.STATIC_DIR / "docwork.js").read_text(encoding="utf-8")
    for ident in ("pdfModeControls", "pdfTextOnly", "pdfLossConfirmed"):
        assert f'id="{ident}"' in html
    assert "confirm_text_loss" in js and "pdf_mode" in js
