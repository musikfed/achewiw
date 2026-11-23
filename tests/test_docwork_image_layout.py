"""R3.1 DOCX illustration styling: synthetic images only, no authored books.

Covers rounded PNG pixels, independent shared picture relationships, text-area
width, alignment, thumbnails, hostile input and R3 persistence.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from fastapi.testclient import TestClient
from PIL import Image
import main
import document_workshop as workshop


def make_picture():
    out=BytesIO()
    Image.new("RGB",(320,180),(230,140,46)).save(out,format="PNG")
    return out.getvalue()


def make_book():
    image=make_picture()
    doc=Document()
    doc.add_heading("Глава 1. Рисунки",level=1)
    p=doc.add_paragraph()
    p.add_run().add_picture(BytesIO(image))
    doc.add_paragraph("Рисунок 1 — тестовая иллюстрация",style="Caption")
    q=doc.add_paragraph()
    q.add_run().add_picture(BytesIO(image))
    doc.add_paragraph("Рисунок 2 — тот же исходный PNG",style="Caption")
    mixed=doc.add_paragraph("Значимый текст рядом: ")
    mixed.add_run().add_picture(BytesIO(image))
    table=doc.add_table(rows=1,cols=1)
    table.cell(0,0).paragraphs[0].add_run().add_picture(BytesIO(image))
    out=BytesIO();doc.save(out)
    return out.getvalue()


def picture_blob(document,index):
    shape=document.inline_shapes[index]
    rid=shape._inline.xpath(".//a:blip")[0].get(qn("r:embed"))
    return document.part.related_parts[rid].blob


def test_image_inventory_detects_editable_and_protected_pictures():
    raw=make_book()
    info=workshop.preview(raw,"book.docx")
    images=info["images"]
    assert len(images)==4
    assert [item["editable"] for item in images]==[True,True,False,False]
    assert images[0]["width_px"]==320 and images[0]["height_px"]==180
    assert images[2]["reason"] and images[3]["reason"]


def test_proportional_alignment_and_rounded_pixels_do_not_mutate_original():
    raw=make_book()
    digest=sha256(raw).hexdigest()
    options={"preset":"book","image_edits":{
        "0":{"width_percent":65,"align":"center","corner_percent":16},
        "1":{"width_percent":40,"align":"right","corner_percent":0}
    }}
    edited=workshop.transform_docx(raw,options)
    original=Document(BytesIO(raw))
    new=Document(BytesIO(edited))
    assert sha256(raw).hexdigest()==digest
    text_width=int(new.sections[0].page_width-new.sections[0].left_margin-new.sections[0].right_margin)
    assert abs(new.inline_shapes[0].width / text_width - 0.65)<0.01
    assert abs(new.inline_shapes[1].width / text_width - 0.40)<0.01
    assert abs(new.inline_shapes[0].height/new.inline_shapes[0].width-(180/320))<0.015
    assert new.paragraphs[1].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert new.paragraphs[3].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert new.paragraphs[1].paragraph_format.keep_with_next is True
    assert new.paragraphs[2].text == "Рисунок 1 — тестовая иллюстрация"
    with Image.open(BytesIO(picture_blob(new,0))) as img:
        assert img.mode=="RGBA"
        assert img.getpixel((0,0))[3]==0
        assert img.getpixel((160,90))[3]==255
    assert picture_blob(new,1)==picture_blob(original,1)
    assert picture_blob(new,2)==picture_blob(original,2)
    assert picture_blob(new,3)==picture_blob(original,3)
    assert new.tables[0].cell(0,0).paragraphs[0]._p.xml
    with ZipFile(BytesIO(edited)) as archive:
        assert archive.testzip() is None


@pytest.mark.parametrize("images",[
    {"0":{"width_percent":101,"align":"center","corner_percent":16}},
    {"0":{"width_percent":10,"align":"center","corner_percent":16}},
    {"0":{"width_percent":True,"align":"center","corner_percent":0}},
    {"0":{"width_percent":70,"align":"middle","corner_percent":0}},
    {"0":{"width_percent":70,"align":"left","corner_percent":18}},
    {"0":{"width_percent":70,"align":"left","corner_percent":0,"unknown":1}},
    {"99":{"width_percent":70,"align":"center","corner_percent":0}},
    {"2":{"width_percent":70,"align":"center","corner_percent":0}},
    {"3":{"width_percent":70,"align":"center","corner_percent":0}},
])
def test_invalid_or_complex_picture_edits_fail_closed(images):
    with pytest.raises(workshop.DocumentWorkshopError):
        workshop.transform_docx(make_book(),{"image_edits":images})


def test_no_image_edits_preserves_embedded_original_media():
    raw=make_book()
    result=workshop.transform_docx(raw,{"preset":"preserve"})
    doc=Document(BytesIO(result))
    original=Document(BytesIO(raw))
    for i in range(4):
        assert picture_blob(doc,i)==picture_blob(original,i)


def test_thumbnail_api_is_token_protected_and_source_is_unmodified(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"test.sqlite3")
    src=make_book()
    with TestClient(main.app,base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1",12345)) as client:
        created=client.post("/api/docwork/drafts",
            files={"file":("pictures.docx",src)}).json()
        assert len(created["preview"]["images"])==4
        endpoint="/api/docwork/drafts/"+created["id"]+"/images/0"
        assert client.get(endpoint).status_code==404
        h={"X-ACHEWIW-Docwork-Token":created["token"]}
        result=client.get(endpoint,headers=h)
        assert result.status_code==200,result.text
        assert result.headers["content-type"]=="image/png"
        assert result.headers["cache-control"]=="no-store"
        with Image.open(BytesIO(result.content)) as im:
            assert im.width<=360 and im.height<=240
        assert client.get("/api/docwork/drafts/"+created["id"]+"/images/100",
                          headers=h).status_code==422
        source=client.get("/api/docwork/drafts/"+created["id"]+"/source",
                          headers=h)
        assert source.content==src


def test_image_editor_roundtrips_through_r3_session_and_export(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"test.sqlite3")
    raw=make_book()
    with TestClient(main.app,base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1",12345)) as c:
        draft=c.post("/api/docwork/drafts",files={"file":("img.docx",raw)}).json()
        h={"X-ACHEWIW-Docwork-Token":draft["token"]}
        state={"images":{"0":{"width_percent":80,"align":"left","corner_percent":8}},
               "settings":{"preset":"book"}}
        url="/api/docwork/drafts/"+draft["id"]
        response=c.put(url,headers=h,json={"revision":1,"state":state})
        assert response.status_code==200,response.text
        restored=c.get(url,headers=h).json()
        assert restored["revision"]==2
        assert restored["state"]["images"]==state["images"]
        options={"preset":"book","image_edits":state["images"]}
        response=c.post("/api/docwork/export",
            files={"file":("img.docx",raw)},
            data={"target":"docx","options":__import__("json").dumps(options)})
        assert response.status_code==200,response.text
        out=Document(BytesIO(response.content))
        assert out.paragraphs[1].alignment==WD_ALIGN_PARAGRAPH.LEFT
        with Image.open(BytesIO(picture_blob(out,0))) as im:
            assert im.getpixel((0,0))[3]==0


def test_ui_contains_real_image_controls_and_storage_hooks():
    html=(main.STATIC_DIR/"docwork.html").read_text(encoding="utf-8")
    js=(main.STATIC_DIR/"docwork.js").read_text(encoding="utf-8")
    for ident in ("imageList","imageListStatus","imageControls","imageWidth",
                  "imageAlign","imageCorners","imagePreview","applyImageEdit",
                  "resetImageEdit"):
        assert f'id="{ident}"' in html
    assert "image_edits" in js and "images:{...imageEdits}" in js
    assert "renderImageGallery()" in js and "markDraftDirty()" in js
    assert "'X-ACHEWIW-Docwork-Token':draft.token" in js
