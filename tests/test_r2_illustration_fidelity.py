"""R2: non-destructive illustrated-book publication fidelity.

Uses only tmp_path, in-memory generated books, and synthetic PNG files.
This does not access the real ACHEWIW data directory or other applications.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import fitz
import pytest
from docx import Document
from docx.shared import Cm
from fastapi.testclient import TestClient
from PIL import Image

import main
from book_layout_core import verified_illustrations
from manuscript_core import manuscript_epub
from publishing_exports import manuscript_docx, manuscript_fb2, manuscript_pdf

SID = "a" * 32
AID = "b" * 32
CAPTION = "Рисунок 7 — Тестовый измерительный стенд"


def _project(count=15):
    paragraphs = [
        f"Измерение {i}: синтетический текст главы с кириллицей и определением структуры. "
        "Стенд работает в тестовом режиме, человек проверяет результаты. " * 2
        for i in range(1, count + 1)
    ]
    return SimpleNamespace(
        id="1" * 32, title="Атлас лабораторных испытаний",
        work_type="technical", author_name="Тестовый автор",
        scenes=[SimpleNamespace(
            id=SID, title="Глава 1. Испытания",
            text="\n\n".join(paragraphs)
        )],
    )


def _art(tmp_path, *, portrait=False, caption=CAPTION):
    path = tmp_path / "synthetic.png"
    Image.new("RGB", (240, 1050) if portrait else (360, 150), "#d17b21").save(path)
    return {
        "id": AID, "scene_id": SID, "caption": caption,
        "placement": "paragraph", "paragraph_index": 15, "path": path,
    }


def _pdf_image_and_caption_pages(blob, caption=CAPTION):
    with fitz.open(stream=blob, filetype="pdf") as pdf:
        pictures = []
        captions = []
        for index, page in enumerate(pdf):
            for picture in page.get_image_info():
                pictures.append((index, picture["bbox"]))
            matches = page.search_for(caption)
            if matches:
                captions.extend((index, box) for box in matches)
        return pictures, captions


def test_r2_docx_picture_caption_keep_next_and_portrait_fits_page(tmp_path):
    art = _art(tmp_path, portrait=True)
    raw = manuscript_docx(_project(), illustrations=[art])
    doc = Document(BytesIO(raw))
    paragraphs = doc.paragraphs
    picture_index = next(i for i, p in enumerate(paragraphs)
                         if "w:drawing" in p._p.xml)
    pic = paragraphs[picture_index]
    label = paragraphs[picture_index + 1]
    assert pic.paragraph_format.keep_with_next is True
    assert pic.paragraph_format.keep_together is True
    assert label.style.name == "Caption"
    assert label.text == CAPTION
    assert label.paragraph_format.keep_together is True
    assert doc.inline_shapes[0].height <= Cm(12.0)
    assert doc.inline_shapes[0].width <= Cm(10.5)
    assert (tmp_path / "synthetic.png").is_file()


@pytest.mark.parametrize("paragraphs_before", [15, 20, 25, 34, 45])
def test_r2_pdf_picture_and_caption_remain_on_same_page_near_breaks(tmp_path, paragraphs_before):
    art = _art(tmp_path)
    raw = manuscript_pdf(_project(paragraphs_before), illustrations=[art])
    images, captions = _pdf_image_and_caption_pages(raw)
    assert len(images) == 1, "Exported PDF must contain exactly one real illustration"
    assert len(captions) == 1, "Exported PDF must contain exactly one caption"
    assert images[0][0] == captions[0][0], "Picture and caption were split across pages"
    assert images[0][1][3] <= captions[0][1].y0 + 4, "Caption must follow its picture"


def test_r2_pdf_portrait_illustration_and_long_caption_fit_together(tmp_path):
    art = _art(tmp_path, portrait=True,
               caption="Рисунок 8 — " + "Проверка читаемости подписи и привязки. " * 11)
    raw = manuscript_pdf(_project(16), illustrations=[art])
    # Search the stable caption prefix: PyMuPDF search_for() cannot match
    # a full caption that wraps across several PDF lines.
    images, captions = _pdf_image_and_caption_pages(raw, "Рисунок 8")
    assert len(images) == 1 and len(captions) == 1
    assert images[0][0] == captions[0][0]
    with fitz.open(stream=raw, filetype="pdf") as pdf:
        assert "Проверка читаемости подписи" in pdf[images[0][0]].get_text()


@pytest.mark.parametrize("which", ["docx", "pdf", "fb2", "epub"])
def test_r2_missing_illustration_never_disappears_from_export(tmp_path, which):
    art = _art(tmp_path)
    art["path"].unlink()
    funcs = {
        "docx": manuscript_docx, "pdf": manuscript_pdf,
        "fb2": manuscript_fb2, "epub": manuscript_epub
    }
    with pytest.raises(ValueError, match="Иллюстрация "+AID+".*отсутствует"):
        funcs[which](_project(), illustrations=[art])


def test_r2_corrupt_and_symlinked_illustrations_fail_before_export(tmp_path):
    art = _art(tmp_path)
    art["path"].write_bytes(b"NOT A PNG")
    with pytest.raises(ValueError, match="повреждённый PNG"):
        verified_illustrations([art])
    outside = tmp_path / "outside.png"
    Image.new("RGB", (32, 32)).save(outside)
    art["path"].unlink()
    art["path"].symlink_to(outside)
    with pytest.raises(ValueError, match="небезопасен"):
        verified_illustrations([art])
    assert outside.is_file(), "Rejecting symlinks must not remove their targets"


def test_r2_duplicate_ids_refuse_ambiguous_export(tmp_path):
    art = _art(tmp_path)
    with pytest.raises(ValueError, match="Повтор идентификатора"):
        verified_illustrations([art, dict(art)])


def test_r2_epub_and_fb2_assets_and_captions_are_not_orphans(tmp_path):
    art = _art(tmp_path)
    project = _project()
    epub = manuscript_epub(project, illustrations=[art])
    with ZipFile(BytesIO(epub)) as archive:
        assert "EPUB/image-"+AID+".png" in archive.namelist()
        chapter = archive.read("EPUB/chapter-001.xhtml").decode("utf-8")
        assert CAPTION in chapter and "<figure><img" in chapter and "<figcaption>" in chapter
        assert "break-inside:avoid" in archive.read("EPUB/style.css").decode("utf-8")
    fb2 = manuscript_fb2(project, illustrations=[art])
    root = ET.fromstring(fb2)
    bin_elements = [e for e in root.iter() if e.tag.endswith("binary")]
    assert len(bin_elements) == 1
    assert bin_elements[0].attrib["id"] == "img-"+AID
    assert CAPTION in fb2.decode("utf-8")


def test_r2_api_missing_file_blocks_all_book_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.sqlite3")
    with TestClient(main.app,base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1",12345)) as client:
        project=client.post("/api/projects",json={
            "title":"Книга с контрольной иллюстрацией",
            "work_type":"technical",
        }).json()
        project["scenes"]=[{"id":SID,"title":"Глава 1","text":"Текст главы для проверки."}]
        assert client.put("/api/projects/"+project["id"],json=project).status_code==200
        path=f"/api/projects/{project['id']}/illustrations"
        content=(tmp_path / "upload.png")
        Image.new("RGB",(24,18),"white").save(content)
        upload=client.post(path,data={"scene_id":SID,"caption":CAPTION,"placement":"end"},
            files={"file":("diagram.png",content.read_bytes(),"image/png")})
        assert upload.status_code == 201, upload.text
        asset_id=upload.json()["id"]
        path_on_disk=tmp_path/"illustrations"/project["id"]/(asset_id+".png")
        assert path_on_disk.is_file()
        path_on_disk.unlink()  # A synthetic fixture, not a production file
        for suffix in ("manuscript.docx","manuscript.pdf","manuscript.fb2","manuscript.epub"):
            response=client.get(f"/api/projects/{project['id']}/{suffix}")
            assert response.status_code==409, (suffix, response.status_code, response.text)
            assert asset_id in response.json()["detail"]
        assert len(client.get(path).json())==1, "Catalogue entry must be retained for repair"
