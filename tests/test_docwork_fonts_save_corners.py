"""R3.4: fine-grained corners and font catalog preserve original document.

Only synthetic bytes; never accesses Huananzhi, user books or real projects.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import pytest
from PIL import Image
from docx import Document
from docx.oxml.ns import qn

import document_workshop as workshop
from docwork_image_layout import actual_corner_percent, aspect_radius_cap


def synthetic_book(size=(1200, 300)):
    pic=BytesIO()
    Image.new("RGB",size,(232,120,70)).save(pic,"PNG")
    doc=Document()
    doc.add_paragraph("Текстовый абзац")
    para=doc.add_paragraph()
    para.add_run().add_picture(BytesIO(pic.getvalue()))
    doc.add_paragraph("Рисунок 1 — Тест",style="Caption")
    out=BytesIO();doc.save(out)
    return out.getvalue()


def image_in_docx(data):
    doc=Document(BytesIO(data))
    rid=doc.inline_shapes[0]._inline.xpath(".//a:blip")[0].get(qn("r:embed"))
    return doc.part.related_parts[rid].blob


def test_radius_aspect_limits_are_less_aggressive_for_wide_schemes():
    assert aspect_radius_cap(1200, 300)==8.0
    assert aspect_radius_cap(600, 400)==12.0
    assert aspect_radius_cap(500, 450)==24.0
    assert actual_corner_percent(1200,300,24)==8
    assert actual_corner_percent(450,450,24)==24
    assert actual_corner_percent(1200,300,0.5)==0.5


def test_half_percent_corners_are_exported_and_original_is_unchanged():
    original=synthetic_book()
    checksum=sha256(original).hexdigest()
    edited=workshop.transform_docx(original,{
        "image_edits":{"0":{"width_percent":75,"align":"center","corner_percent":0.5}}
    })
    assert sha256(original).hexdigest()==checksum
    with Image.open(BytesIO(image_in_docx(edited))) as im:
        assert im.mode=="RGBA"
        assert im.getpixel((0,0))[3]==0
        assert im.getpixel((600,150))[3]==255


@pytest.mark.parametrize("radius",[-0.5,24.5,3.1,"1.5",True,None])
def test_invalid_corner_radius_fails_closed(radius):
    with pytest.raises(workshop.DocumentWorkshopError):
        workshop.transform_docx(synthetic_book(),{
            "image_edits":{"0":{"width_percent":65,"align":"center",
                                "corner_percent":radius}}
        })


def test_new_openly_licensed_fonts_apply_as_real_docx_style_changes():
    original=synthetic_book((320,180))
    edited=workshop.transform_docx(original,{
        "body_font":"FreeSerif", "chapter_font":"Caladea",
        "subchapter_font":"Carlito"
    })
    doc=Document(BytesIO(edited))
    assert doc.styles["Normal"].font.name=="FreeSerif"
    assert doc.styles["Heading 1"].font.name=="Caladea"
    assert doc.styles["Heading 2"].font.name=="Carlito"


def test_font_and_manual_autosave_controls_are_on_same_screen():
    from main import STATIC_DIR
    html=(STATIC_DIR/"docwork.html").read_text(encoding="utf-8")
    js=(STATIC_DIR/"docwork.js").read_text(encoding="utf-8")
    css=(STATIC_DIR/"docwork.css").read_text(encoding="utf-8")
    for name in ("fontCollections","fontStandardChoices","fontOtherChoices",
                 "fontApplyTarget","quickSaveDraft","saveCanvasNow",
                 "saveIndicator","toggleActionBar","toggleEditToolbar",
                 "imageCornersNumber","imageCornerHint"):
        assert ('class="'+name+'"' in html or 'id="'+name+'"' in html)
    for snippet in ("populateFontSelects()","applyCatalogFont(",
                    "controlledRadius(","saveIndicator","draftMessage(",
                    "quickSaveDraft","toggleEditToolbar"):
        assert snippet in js
    assert ".workshop-actions.is-compact" in css
    assert ".editToolbar.is-compact" in css
    assert 'step="0.5"' in html
