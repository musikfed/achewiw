"""R3.2: moving a DOCX picture/caption between editable text paragraphs.

All images and books are synthesized in memory. Test the exported OOXML order,
caption co-movement, input constraints, and persisted state across F5.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO

import pytest
from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient
from PIL import Image

import document_workshop as workshop
import main


def document_with_figures():
    png=BytesIO()
    Image.new("RGB",(180,100),(180,100,50)).save(png,"PNG")
    doc=Document()
    doc.add_heading("Глава 1",level=1)       # 0
    doc.add_paragraph("Текст А. Введение.")  # 1
    a=doc.add_paragraph()                   # 2
    a.add_run().add_picture(BytesIO(png.getvalue()))
    doc.add_paragraph("Рисунок 1 — Схема А",style="Caption")  # 3
    doc.add_paragraph("Текст Б. Объяснение.")                # 4
    b=doc.add_paragraph()                   # 5
    b.add_run().add_picture(BytesIO(png.getvalue()))
    doc.add_paragraph("Рисунок 2 — Схема Б",style="Caption")  # 6
    doc.add_paragraph("Текст В. Итоги.")                     # 7
    doc.add_table(rows=1,cols=1).cell(0,0).text="Не двигать эту таблицу"
    stream=BytesIO();doc.save(stream)
    return stream.getvalue()


def document_order(blob):
    doc=Document(BytesIO(blob))
    result=[]
    for p in doc.paragraphs:
        if p._p.xpath(".//w:drawing"):
            result.append("picture")
        elif p.text.strip():
            result.append(p.text.strip())
    return result


def test_inventory_maps_figures_to_text_paragraphs_and_captions():
    source=document_with_figures()
    info=workshop.preview(source,"pictures.docx")
    assert info["images"][0]["editable"] is True
    assert info["images"][0]["paragraph_index"]==2
    assert info["images"][0]["caption_index"]==3
    assert info["images"][1]["paragraph_index"]==5
    assert info["images"][1]["caption_index"]==6
    assert info["text_width_cm"]>8


def test_move_picture_with_caption_to_new_position_without_mutating_source():
    source=document_with_figures()
    original=sha256(source).hexdigest()
    result=workshop.transform_docx(source,{
        "preset":"book",
        "replacements":{"1":"Исправленный текст А"},
        "image_moves":{"0":{"before_paragraph":7,"order":1}}
    })
    order=document_order(result)
    assert order.index("Исправленный текст А")<order.index("Текст Б. Объяснение.")
    assert order==[
        "Глава 1","Исправленный текст А","Текст Б. Объяснение.",
        "picture","Рисунок 2 — Схема Б",
        "picture","Рисунок 1 — Схема А","Текст В. Итоги.",
    ]
    doc=Document(BytesIO(result))
    assert doc.tables[0].cell(0,0).text=="Не двигать эту таблицу"
    assert sha256(source).hexdigest()==original
    assert doc.paragraphs[5].paragraph_format.keep_with_next is True


def test_multiple_images_can_move_to_same_target_in_explicit_order():
    result=workshop.transform_docx(document_with_figures(),{
        "image_moves":{
            "1":{"before_paragraph":1,"order":1},
            "0":{"before_paragraph":1,"order":2},
        }
    })
    assert document_order(result)==[
        "Глава 1",
        "picture","Рисунок 2 — Схема Б",
        "picture","Рисунок 1 — Схема А",
        "Текст А. Введение.","Текст Б. Объяснение.","Текст В. Итоги."
    ]


def test_picture_can_be_moved_to_document_end_without_losing_section_properties():
    result=workshop.transform_docx(document_with_figures(),{
        "image_moves":{"0":{"before_paragraph":8,"order":7}}
    })
    order=document_order(result)
    assert order[-2:]==["picture","Рисунок 1 — Схема А"]
    doc=Document(BytesIO(result))
    assert len(doc.sections)==1
    assert len(doc.inline_shapes)==2


@pytest.mark.parametrize("moves",[
    {"0":{"before_paragraph":3,"order":1}},     # own caption
    {"0":{"before_paragraph":2,"order":1}},     # image para
    {"0":{"before_paragraph":5,"order":1}},     # second image para
    {"0":{"before_paragraph":6,"order":1}},     # another image's caption
    {"0":{"before_paragraph":-1,"order":1}},
    {"0":{"before_paragraph":100,"order":1}},
    {"999":{"before_paragraph":1,"order":1}},
    {"0":{"before_paragraph":1,"order":True}},
    {"0":{"before_paragraph":"1","order":1}},
    {"0":{"before_paragraph":1}},
    {"zero":{"before_paragraph":1,"order":1}},
])
def test_unsafe_image_anchors_are_rejected(moves):
    with pytest.raises(workshop.DocumentWorkshopError):
        workshop.transform_docx(document_with_figures(),{"image_moves":moves})


def test_r3_session_stores_image_position_and_server_exports_moved_copy(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"test.sqlite3")
    source=document_with_figures()
    with TestClient(main.app,base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1",12345)) as c:
        created=c.post("/api/docwork/drafts",files={"file":("story.docx",source)})
        assert created.status_code==201
        draft=created.json()
        h={"X-ACHEWIW-Docwork-Token":draft["token"]}
        url="/api/docwork/drafts/"+draft["id"]
        state={"moves":{"0":{"before_paragraph":7,"order":1}},
               "replacements":{"1":"Новый текст через редактор"}}
        saved=c.put(url,headers=h,json={"revision":1,"state":state})
        assert saved.status_code==200,saved.text
        restored=c.get(url,headers=h).json()
        assert restored["state"]["moves"]==state["moves"]
        result=c.post("/api/docwork/export",
                      data={"target":"docx","options":__import__("json").dumps({
                          "image_moves":state["moves"],"replacements":state["replacements"]
                      })},
                      files={"file":("story.docx",source)})
        assert result.status_code==200,result.text
        assert document_order(result.content)[-3:]==[
            "picture","Рисунок 1 — Схема А","Текст В. Итоги."
        ]
        assert c.get(url+"/source",headers=h).content==source


def test_caption_text_can_be_edited_and_moves_with_its_figure():
    source=document_with_figures()
    revised=workshop.transform_docx(source,{
        "image_moves":{"0":{"before_paragraph":0,"order":2}},
        "replacements":{"3":"Рисунок 1 — Обновлённая подпись в редакторе"},
        "image_edits":{"0":{"width_percent":70,"align":"center","corner_percent":8}}
    })
    order=document_order(revised)
    assert order[:3]==[
        "picture","Рисунок 1 — Обновлённая подпись в редакторе","Глава 1"
    ]
    assert order.count("Рисунок 1 — Обновлённая подпись в редакторе")==1


def test_no_moves_keeps_original_picture_and_caption_order():
    source=document_with_figures()
    revised=workshop.transform_docx(source,{"preset":"preserve","image_moves":{}})
    assert document_order(revised)==document_order(source)


def test_non_dictionary_moves_fail_closed():
    with pytest.raises(workshop.DocumentWorkshopError):
        workshop.transform_docx(document_with_figures(),{"image_moves":[]})


def test_integrated_canvas_is_on_same_docwork_page_not_a_new_tab():
    html=(main.STATIC_DIR/"docwork.html").read_text(encoding="utf-8")
    js=(main.STATIC_DIR/"docwork.js").read_text(encoding="utf-8")
    for marker in ('id="documentCanvas"','id="documentEditor"','id="canvasMore"'):
        assert marker in html
    assert 'draggable=true' in js
    assert "dataTransfer.setData('application/x-achewiw-image'" in js
    assert "image_moves:{...imageMoves}" in js
    assert "moves:{...imageMoves}" in js
    assert "renderDocumentCanvas()" in js
    assert "canvasText(row)" in js
    assert "replacements[String(row.index)]=area.value" in js
