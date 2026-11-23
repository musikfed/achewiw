"""R3.3: paragraph alignment and document-level editable style acceptance.

All documents synthetic; no server data or user books accessed.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from fastapi.testclient import TestClient

import main
import document_workshop as workshop


def sample_docx():
    doc = Document()
    first = doc.add_paragraph()
    first.add_run("Курс ")  # preserve distinct source runs for alignment-only.
    bold = first.add_run("Linux")
    bold.bold = True
    doc.add_paragraph("Вторая строка — можно выровнять по центру.")
    doc.add_paragraph("Третья строка — по правому краю.")
    doc.add_paragraph("Четвёртая строка — по ширине.")
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def test_four_paragraph_alignments_change_real_docx_without_flattening_runs():
    source = sample_docx()
    sha = sha256(source).hexdigest()
    edit = {
        "paragraph_alignments": {
            "0": "left", "1": "center",
            "2": "right", "3": "justify",
        }
    }
    output = workshop.transform_docx(source, edit)
    document = Document(BytesIO(output))
    assert [p.alignment for p in document.paragraphs] == [
        WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.CENTER,
        WD_ALIGN_PARAGRAPH.RIGHT, WD_ALIGN_PARAGRAPH.JUSTIFY
    ]
    assert document.paragraphs[0].runs[1].text == "Linux"
    assert document.paragraphs[0].runs[1].bold is True
    assert sha256(source).hexdigest() == sha


@pytest.mark.parametrize("bad", [
    [],
    {"0": "diagonal"},
    {"0": 1},
    {"-1": "center"},
    {"1000": "center"},
    {"x": "justify"},
    {0: "left"},
    {"0": None},
    {"0": True},
])
def test_invalid_alignment_maps_fail_closed(bad):
    with pytest.raises(workshop.DocumentWorkshopError):
        workshop.transform_docx(sample_docx(), {"paragraph_alignments": bad})


def test_preview_includes_real_original_alignment():
    source = sample_docx()
    doc = Document(BytesIO(source))
    doc.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    out = BytesIO()
    doc.save(out)
    preview = workshop.preview(out.getvalue(), "example.docx")
    assert preview["paragraphs"][0]["alignment"] == "justify"
    assert preview["paragraphs"][1]["alignment"] == "inherit"


def test_alignment_round_trip_via_r3_session_and_export(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.sqlite3")
    source = sample_docx()
    with TestClient(main.app, base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1", 12345)) as client:
        created = client.post("/api/docwork/drafts",
                              files={"file": ("sample.docx", source)})
        assert created.status_code == 201, created.text
        draft = created.json()
        base = "/api/docwork/drafts/" + draft["id"]
        headers = {"X-ACHEWIW-Docwork-Token": draft["token"]}
        state = {
            "replacements": {"1": "Исправленный абзац."},
            "alignments": {"1": "center", "2": "right"},
            "settings": {"preset": "preserve"}
        }
        saved = client.put(base, headers=headers, json={"revision": 1, "state": state})
        assert saved.status_code == 200, saved.text
        restored = client.get(base, headers=headers).json()
        assert restored["revision"] == 2 and restored["state"] == state
        exported = client.post("/api/docwork/export",
            files={"file": ("sample.docx", source)},
            data={"target": "docx",
                  "options": json.dumps({
                      "replacements": state["replacements"],
                      "paragraph_alignments": state["alignments"],
                  })})
        assert exported.status_code == 200, exported.text
        doc = Document(BytesIO(exported.content))
        assert doc.paragraphs[1].text == "Исправленный абзац."
        assert doc.paragraphs[1].alignment == WD_ALIGN_PARAGRAPH.CENTER
        assert doc.paragraphs[2].alignment == WD_ALIGN_PARAGRAPH.RIGHT
        assert client.get(base + "/source", headers=headers).content == source


def test_ui_explains_direct_editing_and_exposes_real_toolbar():
    page = (main.STATIC_DIR / "docwork.html").read_text(encoding="utf-8")
    js = (main.STATIC_DIR / "docwork.js").read_text(encoding="utf-8")
    for ident in (
        "editToolbar", "undoEdit", "redoEdit", "undoDepth",
        "paragraphAlignTools", "alignLeft", "alignCenter", "alignRight",
        "alignJustify", "alignReset", "canvasRole", "editingHelp"
    ):
        assert 'id="' + ident + '"' in page
    assert "Ctrl+Z" in page and "Ctrl+Y" in page
    assert "paragraph_alignments" in js
    assert "alignments:{...paragraphAlignments}" in js
    assert "MAX_UNDO_STEPS = 200" in js
    assert "editWithUndo('type-text:'" in js
    assert "editWithUndo('picture-move:'" in js
    assert "undoEditAction(redo)" in js
    assert "area.addEventListener('focus',()=>selectTextParagraph(row.index))" in js
