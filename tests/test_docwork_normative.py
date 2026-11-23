"""R4.0: evidence-only DOCX normative preflight, never certifying GOST.

All source documents are synthetic; the API is exercised in isolated SQLite.
"""
from __future__ import annotations
from hashlib import sha256
from io import BytesIO
import zipfile

from docx import Document
from docx.shared import Cm
from fastapi.testclient import TestClient
import pytest
import main
from docwork_normative import (
    audit_docx, available_profiles, NormativePreflightError, PROFILES,
)


def sample_docx():
    document=Document()
    sec=document.sections[0]
    sec.page_width=Cm(14.8)
    sec.page_height=Cm(21)
    sec.top_margin=Cm(2.0)
    sec.left_margin=Cm(2.1)
    document.add_heading("Техническая книга", 1)
    document.add_paragraph("Сведения для автора и читателя.")
    document.add_paragraph("Рисунок 1 — Контурная иллюстрация.")
    document.add_paragraph("Список литературы")
    document.add_paragraph("1. Пример условного источника.")
    document.add_paragraph("2. Ещё один условный источник.")
    table=document.add_table(rows=2,cols=3)
    table.cell(0,0).text="A"
    result=BytesIO()
    document.save(result)
    return result.getvalue()


def test_preflight_is_read_only_and_uses_scoped_normative_profiles():
    raw=sample_docx()
    before=sha256(raw).hexdigest()
    audited=audit_docx(raw,"author_book")
    assert sha256(raw).hexdigest()==before==audited["source_sha256"]
    assert audited["status"]=="needs_editor_review"
    assert audited["is_gost_certified"] is False
    assert audited["metrics"]["paragraphs"]>=5
    assert audited["metrics"]["tables"]==1
    assert audited["metrics"]["captions_candidates"]==1
    assert audited["metrics"]["bibliography_heading_found"] is True
    assert audited["metrics"]["bibliography_entry_candidates"]==2
    assert audited["metrics"]["footnotes"]==0
    assert audited["page_geometry"][0]["width_cm"]==14.8
    assert audited["page_geometry"][0]["height_cm"]==21.0
    assert len(audited["standards"])==2
    assert all(x["url"].startswith("https://protect.gost.ru/") for x in audited["standards"])


def test_report_only_recommends_relevant_profiles_not_universal_a4_or_font():
    raw=sample_docx()
    report=audit_docx(raw,"technical_report")
    assert any(x["number"]=="ГОСТ 7.32-2017" for x in report["standards"])
    assert report["is_gost_certified"] is False
    assert not any("обязательный формат А4" in x["message"] for x in report["observations"])
    office=audit_docx(raw,"office_document")
    assert len(office["standards"])==1
    assert office["standards"][0]["number"]=="ГОСТ Р 7.0.97-2025"
    assert len(audit_docx(raw,"unclassified")["standards"])==0
    assert len(available_profiles())==len(PROFILES)==4


def test_warnings_distinguish_missing_toc_and_missing_bibliography():
    empty=Document()
    empty.add_paragraph("Обычная глава без библиографии.")
    out=BytesIO();empty.save(out)
    report=audit_docx(out.getvalue(),"author_book")
    codes={x["code"] for x in report["observations"]}
    assert "BIBLIOGRAPHY_NOT_DETECTED" in codes
    assert "TOC_NOT_DETECTED" in codes
    assert "MANUAL_LAYOUT_REQUIRED" in codes


@pytest.mark.parametrize("profile",["random",None,3,".."])
def test_invalid_profile_is_rejected(profile):
    with pytest.raises(NormativePreflightError):
        audit_docx(sample_docx(),profile)


def test_bad_content_and_oversized_files_are_rejected():
    for raw in [b"", b"not-docx",b"PK\x03\x04bad"]:
        with pytest.raises((NormativePreflightError,zipfile.BadZipFile)):
            audit_docx(raw,"author_book")
    with pytest.raises(NormativePreflightError):
        audit_docx(b"z"*(20*1024*1024+1),"author_book")


def test_stateless_http_api_and_editor_controls(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"editor-r4.sqlite3")
    raw=sample_docx()
    with TestClient(main.app,base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1",12345)) as client:
        profiles=client.get("/api/docwork/normative-profiles")
        assert profiles.status_code==200
        assert len(profiles.json()["profiles"])==4
        response=client.post(
            "/api/docwork/preflight",
            data={"profile":"author_book"},
            files={"file":("book.docx",raw)}
        )
        assert response.status_code==200,response.text
        assert response.json()["is_gost_certified"] is False
        assert response.json()["source_sha256"]==sha256(raw).hexdigest()
        invalid=client.post(
            "/api/docwork/preflight",
            data={"profile":"author_book"},
            files={"file":("book.pdf",raw)}
        )
        assert invalid.status_code==422
        assert client.get("/api/projects").json()==[]
        assert client.get("/api/docwork/normative-profiles").status_code==200


def test_ui_checks_current_exported_copy_and_can_download_json_report():
    html=(main.STATIC_DIR/"docwork.html").read_text(encoding="utf-8")
    js=(main.STATIC_DIR/"docwork.js").read_text(encoding="utf-8")
    for key in ("normativePreflight","normativeProfile","runNormative",
                "downloadNormative","normativeStatus","normativeReport"):
        assert 'id="'+key+'"' in html
    assert "requestForm('docx',optionsForExport())" in js
    assert "/api/docwork/preflight" in js
    assert "is_gost_certified" not in html
    assert "new Blob([JSON.stringify(lastNormativeReport,null,2)]" in js
