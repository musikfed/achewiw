"""R3.5: experimental Potrace SVG conversion without source mutation.

All files synthetic. No authored work, model or GPU touched.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import shutil
from xml.etree import ElementTree as ET
import pytest
from PIL import Image, ImageDraw
from docx import Document
from fastapi.testclient import TestClient

import main
from docwork_vector import (
    convert_picture, raster_to_svg, svg_to_png, VectorExperimentError
)

POTRACE_READY=shutil.which("potrace") is not None


def synthetic_diagram():
    image=Image.new("RGB",(300,160),"white")
    d=ImageDraw.Draw(image)
    d.rectangle((25,30,125,125),fill=(32,110,70))
    d.rectangle((175,35,280,120),fill=(25,50,135))
    d.line((125,80,175,80),fill=(15,15,15),width=6)
    raw=BytesIO();image.save(raw,"PNG")
    return raw.getvalue()


def sample_docx():
    doc=Document()
    doc.add_heading("Инженерная схема",1)
    doc.add_paragraph().add_run().add_picture(BytesIO(synthetic_diagram()))
    out=BytesIO();doc.save(out)
    return out.getvalue()


@pytest.mark.skipif(not POTRACE_READY,reason="potrace optional CLI unavailable")
def test_svg_contains_real_paths_not_embedded_pixels():
    raw=synthetic_diagram()
    svg=raster_to_svg(raw,4)
    assert svg.startswith(b"<?xml")
    root=ET.fromstring(svg)
    assert root.tag.endswith("svg")
    paths=[x for x in root.iter() if x.tag.endswith("path")]
    assert len(paths)>=1
    assert b"data:image" not in svg
    assert b"<image" not in svg
    png=svg_to_png(svg,2)
    with Image.open(BytesIO(png)) as im:
        assert im.size==(600,320)
        assert im.getpixel((20,20))==(255,255,255)


@pytest.mark.skipif(not POTRACE_READY,reason="potrace optional CLI unavailable")
def test_docx_original_is_byte_identical_after_svg_and_png_experiments():
    source=sample_docx()
    original_hash=sha256(source).hexdigest()
    svg=convert_picture(source,0,colors=4,output="svg")
    png=convert_picture(source,0,colors=2,output="png",scale=2)
    assert b"<path" in svg
    with Image.open(BytesIO(png)) as raster:
        assert raster.size==(600,320)
    assert sha256(source).hexdigest()==original_hash


@pytest.mark.parametrize("colors,output,scale",[
    (8,"svg",2),(True,"svg",2),(1,"svg",2),
    (4,"jpeg",2),(4,"png",0),(4,"png",5)
])
@pytest.mark.skipif(not POTRACE_READY,reason="potrace optional CLI unavailable")
def test_invalid_vector_params_are_rejected(colors,output,scale):
    with pytest.raises(VectorExperimentError):
        convert_picture(sample_docx(),0,colors=colors,output=output,scale=scale)


@pytest.mark.skipif(not POTRACE_READY,reason="potrace optional CLI unavailable")
def test_private_token_controls_vector_export_and_no_book_changes(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"test.sqlite3")
    source=sample_docx()
    with TestClient(main.app,base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1",12345)) as c:
        created=c.post("/api/docwork/drafts",files={"file":("diagram.docx",source)})
        assert created.status_code==201,created.text
        draft=created.json()
        url="/api/docwork/drafts/"+draft["id"]
        endpoint=url+"/images/0/vectorize"
        payload={"colors":4,"output":"svg","scale":2}
        assert c.post(endpoint,json=payload).status_code==404
        h={"X-ACHEWIW-Docwork-Token":draft["token"]}
        assert c.post(endpoint,json={"colors":8,"output":"svg","scale":2},
                      headers=h).status_code==422
        response=c.post(endpoint,json=payload,headers=h)
        assert response.status_code==200,response.text
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert b"<path" in response.content
        raster=c.post(endpoint,headers=h,
                      json={"colors":4,"output":"png","scale":2})
        assert raster.status_code==200,raster.text
        assert raster.headers["content-type"].startswith("image/png")
        with Image.open(BytesIO(raster.content)) as img:
            assert img.size==(600,320)
        assert c.get(url+"/source",headers=h).content==source
        assert c.get("/api/projects").json()==[]
        confirmed={**h,"X-ACHEWIW-Confirm-Close-Draft":"yes-delete-only-this-draft"}
        assert c.delete(url,headers=confirmed).status_code==204


@pytest.mark.skipif(not POTRACE_READY,reason="potrace optional CLI unavailable")
def test_raster_picture_inside_table_can_be_vectorized_without_reformatting_table():
    doc=Document()
    table=doc.add_table(rows=1,cols=1)
    table.cell(0,0).paragraphs[0].add_run().add_picture(
        BytesIO(synthetic_diagram()))
    out=BytesIO();doc.save(out)
    src=out.getvalue()
    info=__import__("document_workshop").preview(src,"table.docx")
    assert len(info["images"])==1
    assert info["images"][0]["editable"] is False
    svg=convert_picture(src,0,colors=4,output="svg")
    assert b"<path" in svg
    assert Document(BytesIO(src)).tables[0].cell(0,0)._tc.xml
    assert b"data:image" not in svg


def test_vector_ui_is_explicit_and_does_not_replace_document():
    from main import STATIC_DIR
    html=(STATIC_DIR/"docwork.html").read_text(encoding="utf-8")
    js=(STATIC_DIR/"docwork.js").read_text(encoding="utf-8")
    for marker in ('id="downloadVectorSvg"','id="downloadVectorPng"',
                   'id="vectorColors"','id="vectorStatus"','id="vectorPreview"'):
        assert marker in html
    assert "exportTracedPicture(" in js
    assert "button.disabled=!Number.isFinite(item.width_px)" in js
    assert "downloadVectorSvg" in js and "downloadVectorPng" in js
    assert "В книге" not in js # no false claim of source replacement
