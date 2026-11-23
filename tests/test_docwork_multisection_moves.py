"""Multi-section DOCX move safety for real books with many section breaks.

Uses synthetic two-section DOCX; exercises paragraph section mapping, moving
inside a section, refusing cross-section moves, and preserving section breaks.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image
from docx import Document
from docx.enum.section import WD_SECTION_START
import document_workshop as workshop


def multisection_book():
    image=Image.new("RGB",(120,70),(53,121,76))
    raw=BytesIO();image.save(raw,"PNG")
    document=Document()
    document.add_paragraph("Глава А — начало")
    document.add_paragraph().add_run().add_picture(BytesIO(raw.getvalue()))
    document.add_paragraph("Рисунок 1 — A",style="Caption")
    document.add_paragraph("Глава А — конец")
    document.add_section(WD_SECTION_START.NEW_PAGE)
    document.add_paragraph("Глава Б — начало")
    document.add_paragraph().add_run().add_picture(BytesIO(raw.getvalue()))
    document.add_paragraph("Рисунок 2 — Б",style="Caption")
    document.add_paragraph("Глава Б — конец")
    output=BytesIO();document.save(output)
    return output.getvalue()


def test_move_in_two_sections_preserves_boundaries_and_linked_captions():
    original=multisection_book()
    meta=workshop.preview(original,"two_sections.docx")
    assert meta["sections"]==2
    images=meta["images"]
    assert len(images)==2
    assert [x["section_index"] for x in images]==[0,1]
    first=next(x["index"] for x in meta["paragraphs"]
               if x["text"]=="Глава А — начало")
    second=next(x["index"] for x in meta["paragraphs"]
                if x["text"]=="Глава Б — начало")
    assert next(x["section_index"] for x in meta["paragraphs"]
                if x["index"]==first)==0
    assert next(x["section_index"] for x in meta["paragraphs"]
                if x["index"]==second)==1
    edited=workshop.transform_docx(original,{
        "image_moves":{
            "0":{"before_paragraph":first,"order":1},
            "1":{"before_paragraph":second,"order":2}
        }
    })
    document=Document(BytesIO(edited))
    assert len(document.sections)==2
    assert len(document.inline_shapes)==2
    assert "w:drawing" in document.paragraphs[0]._p.xml
    assert document.paragraphs[1].text=="Рисунок 1 — A"
    second_heading=next(i for i,p in enumerate(document.paragraphs)
                        if p.text=="Глава Б — начало")
    assert "w:drawing" in document.paragraphs[second_heading-2]._p.xml
    assert document.paragraphs[second_heading-1].text=="Рисунок 2 — Б"
    assert Document(BytesIO(original)).paragraphs[0].text=="Глава А — начало"


def test_cross_section_figure_move_fails_closed():
    original=multisection_book()
    meta=workshop.preview(original,"two_sections.docx")
    second=next(x["index"] for x in meta["paragraphs"]
                if x["text"]=="Глава Б — начало")
    with pytest.raises(workshop.DocumentWorkshopError,match="только внутри одного раздела"):
        workshop.transform_docx(original,{
            "image_moves":{"0":{"before_paragraph":second,"order":1}}
        })


def test_earlier_section_cannot_move_to_end_of_document():
    original=multisection_book()
    meta=workshop.preview(original,"two_sections.docx")
    with pytest.raises(workshop.DocumentWorkshopError,match="только внутри одного раздела"):
        workshop.transform_docx(original,{
            "image_moves":{"0":{"before_paragraph":meta["paragraph_count"],"order":1}}
        })
