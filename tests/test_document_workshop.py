"""Document workshop regression: source files stay untouched."""
from __future__ import annotations
from io import BytesIO
import os
os.environ.setdefault("ACHEWIW_DATA_DIR","/tmp/achewiw-docwork-tests")
import pytest
from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from fastapi.testclient import TestClient
import document_workshop as workshop
import main

def make_docx():
    doc=Document()
    doc.add_paragraph("Глава 1")
    doc.add_paragraph("Основы Linux")
    para=doc.add_paragraph("1.1. Компьютер и операционная система")
    run=para.add_run(" — описание")
    run.font.highlight_color=WD_COLOR_INDEX.YELLOW
    shade=OxmlElement("w:shd")
    shade.set(qn("w:fill"),"B0B0B0")
    para._p.get_or_add_pPr().append(shade)
    doc.add_paragraph("Обычный текст с выделениями")
    doc.add_table(rows=1,cols=2).cell(0,0).text="Таблица сохранится"
    output=BytesIO()
    doc.save(output)
    return output.getvalue()

def test_preview_and_chapters():
    info=workshop.preview(make_docx(),"Book.docx")
    assert info["type"]=="docx" and info["paragraph_count"]==4
    assert info["tables"]==1
    assert info["paragraphs"][0]["kind"]=="heading1"
    assert info["paragraphs"][2]["kind"]=="heading2"

def test_format_cleanup_edit_is_copy_only():
    original=make_docx()
    revised=workshop.transform_docx(original,{
        "preset":"karate","clear_highlights":True,"hide_proofing":True,
        "auto_headings":True,"replacements":{"3":"Совершенно новый текст"},
        "overrides":{"1":"heading1"},
    })
    before=Document(BytesIO(original))
    after=Document(BytesIO(revised))
    assert before.paragraphs[3].text=="Обычный текст с выделениями"
    assert after.paragraphs[3].text=="Совершенно новый текст"
    assert after.paragraphs[0].style.name=="Heading 1"
    assert after.paragraphs[1].style.name=="Heading 1"
    assert after.paragraphs[2].style.name=="Heading 2"
    assert after.tables[0].cell(0,0).text=="Таблица сохранится"
    assert "w:highlight" not in after.paragraphs[2]._p.xml
    assert "w:shd" not in after.paragraphs[2]._p.xml
    assert "hideSpellingErrors" in after.settings.element.xml

def test_bad_format_and_safe_names():
    assert workshop.output_filename("old.docx","../Новое * Имя.docx","pdf")=="Новое Имя.pdf"
    for data,name in ((b"not DOCX","bad.docx"),(b"%PDF-garbage","garbage.docx"),
                      (b"not a PDF","bad.pdf"),(b"x"*(workshop.MAX_UPLOAD+1),"huge.pdf")):
        with pytest.raises(workshop.DocumentWorkshopError):
            workshop.validate_upload(data,name)

def test_pdf_to_docx_extracts_text():
    import fitz
    pdf=fitz.open()
    pdf.new_page().insert_text((70,70),"Chapter one. PDF text.")
    raw=pdf.tobytes()
    info=workshop.preview(raw,"text.pdf")
    assert info["type"]=="pdf" and info["characters"]>10
    converted, filename, mime=workshop.export(raw,"text.pdf",{"preset":"book"},"docx")
    assert filename.endswith(".docx") and mime.endswith("wordprocessingml.document")
    doc=Document(BytesIO(converted))
    assert "Chapter one." in " ".join(p.text for p in doc.paragraphs)

def test_docx_to_pdf_if_libreoffice_installed():
    import shutil
    if not (shutil.which("libreoffice") or shutil.which("soffice")):
        pytest.skip("LibreOffice in production Docker only")
    pdf,name,mime=workshop.export(make_docx(),"source.docx",{"preset":"preserve"},"pdf")
    assert pdf.startswith(b"%PDF") and name.endswith(".pdf") and mime=="application/pdf"

def test_api_and_private_access(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"test.sqlite3")
    raw=make_docx()
    with TestClient(main.app,base_url="http://127.0.0.1:7080",client=("127.0.0.1",12345)) as client:
        result=client.get("/docwork/")
        assert result.status_code==200 and "Умный редактор" in result.text
        for name in ("docwork.css","docwork.js"):
            assert client.get("/static/"+name).status_code==200
        data=client.post("/api/docwork/inspect",files={"file":("book.docx",raw)})
        assert data.status_code==200 and data.json()["paragraph_count"]==4
        export=client.post("/api/docwork/export",data={"target":"docx",
              "options":'{"preset":"karate","output_name":"Книга 1"}'},
              files={"file":("book.docx",raw)})
        assert export.status_code==200 and export.content[:2]==b"PK"
        assert "filename*=UTF-8" in export.headers["content-disposition"]
        assert client.get("/api/projects").json()==[]
    with TestClient(main.app,base_url="http://127.0.0.1:7080",client=("203.0.113.15",12345)) as outside:
        assert outside.get("/docwork/").status_code==403
        assert outside.post("/api/docwork/inspect",files={"file":("book.docx",raw)}).status_code==403



def test_preview_reaches_later_chapters_not_just_first_160():
    doc = Document()
    for i in range(185):
        doc.add_paragraph("Обычный абзац книги " + str(i))
    doc.add_paragraph("Глава 12. Дальнейшая история")
    output = BytesIO()
    doc.save(output)
    info = workshop.preview(output.getvalue(), "large.docx")
    assert info["paragraph_count"] == 186
    assert len(info["paragraphs"]) == 186
    assert info["paragraphs"][-1]["index"] == 185
    assert info["paragraphs"][-1]["kind"] == "heading1"


def test_workshop_navigation_and_pdf_preview_ui():
    html = (main.STATIC_DIR / "docwork.html").read_text(encoding="utf-8")
    js = (main.STATIC_DIR / "docwork.js").read_text(encoding="utf-8")
    for name in ("quickPreviewPdf", "quickDownloadDocx", "quickDownloadPdf",
                 "pdfFrame", "paragraphFilter", "paragraphSearch", "loadMoreParagraphs"):
        assert 'id="' + name + '"' in html
    assert "buildPdfPreview" in js
    assert "application/json" in js
