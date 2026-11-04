"""Actual downloadable ACHEWIW book files from saved manuscript text.

EPUB is implemented in manuscript_core.py. These are additional print-proof,
editorial DOCX and FB2 outputs; no AI text or audit is added to the manuscript.
PDF print proof is NOT PDF/X-certified artwork.
"""
from __future__ import annotations

from html import escape
from io import BytesIO
from xml.etree import ElementTree as ET

from manuscript_core import chapter_listing


def _chapters(project):
    book = chapter_listing(project)
    if not book["chapters"]:
        raise ValueError("Для экспорта требуется текст хотя бы одной сохранённой главы")
    return book


def manuscript_fb2(project) -> bytes:
    book = _chapters(project)
    ns = "http://www.gribuser.ru/xml/fictionbook/2.0"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}FictionBook")
    description = ET.SubElement(root, f"{{{ns}}}description")
    title_info = ET.SubElement(description, f"{{{ns}}}title-info")
    ET.SubElement(title_info, f"{{{ns}}}genre").text = "sf" if getattr(project, "work_type", "fiction") == "fiction" else "nonfiction"
    ET.SubElement(title_info, f"{{{ns}}}book-title").text = book["title"]
    ET.SubElement(title_info, f"{{{ns}}}lang").text = "ru"
    body = ET.SubElement(root, f"{{{ns}}}body")
    title = ET.SubElement(body, f"{{{ns}}}title")
    ET.SubElement(title, f"{{{ns}}}p").text = book["title"]
    for chapter in book["chapters"]:
        section = ET.SubElement(body, f"{{{ns}}}section")
        heading = ET.SubElement(section, f"{{{ns}}}title")
        ET.SubElement(heading, f"{{{ns}}}p").text = chapter["title"]
        for paragraph in chapter["text"].strip().split("\n\n"):
            if paragraph.strip():
                ET.SubElement(section, f"{{{ns}}}p").text = paragraph.strip()
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def manuscript_docx(project, illustrations=None) -> bytes:
    from docx import Document
    from docx.shared import Cm, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    book = _chapters(project)
    document = Document()
    sec = document.sections[0]
    sec.page_height, sec.page_width = Cm(21), Cm(14.8)
    sec.left_margin, sec.right_margin = Cm(2.1), Cm(1.8)
    sec.top_margin, sec.bottom_margin = Cm(2), Cm(2)
    normal = document.styles["Normal"]
    normal.font.name = "DejaVu Serif"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(6)
    heading = document.add_paragraph(book["title"], "Title")
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for i, chapter in enumerate(book["chapters"]):
        if i:
            document.add_page_break()
        document.add_heading(chapter["title"], level=1)
        for art in illustrations or []:
            if art["scene_id"] == chapter["scene_id"]:
                document.add_picture(str(art["path"]), width=Cm(10.5))
                if art.get("caption"):
                    document.add_paragraph(art["caption"], style="Caption")
        for paragraph in chapter["text"].strip().split("\n\n"):
            if paragraph.strip():
                document.add_paragraph(paragraph.strip())
    doc = BytesIO()
    document.save(doc)
    return doc.getvalue()


def manuscript_pdf(project, *, print_proof=False, illustrations=None) -> bytes:
    """A5 reading PDF / A5 print proof with Unicode font, basic page numbers.

    This does not claim PDF/X-4, bleed boxes, ICC or printer conformance.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image as RLImage

    book = _chapters(project)
    # Licensed Unicode DejaVu Serif from fonts-dejavu-core system package.
    from pathlib import Path
    regular = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
    bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")
    if not regular.is_file() or not bold.is_file():
        raise RuntimeError("Для PDF требуется fonts-dejavu-core с поддержкой кириллицы")
    if "ACHEWIW-Serif" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ACHEWIW-Serif", str(regular)))
        pdfmetrics.registerFont(TTFont("ACHEWIW-Serif-Bold", str(bold)))

    style = ParagraphStyle(
        "Body", fontName="ACHEWIW-Serif", fontSize=10.5 if print_proof else 11,
        leading=16, alignment=TA_JUSTIFY, spaceAfter=7,
    )
    chapter_style = ParagraphStyle(
        "Chapter", fontName="ACHEWIW-Serif-Bold", fontSize=15, leading=22,
        spaceBefore=12, spaceAfter=17,
    )
    title_style = ParagraphStyle(
        "BookTitle", parent=chapter_style, alignment=TA_CENTER,
        fontSize=20, leading=28, spaceAfter=25,
    )
    output = BytesIO()
    doc = SimpleDocTemplate(
        output, pagesize=A5, leftMargin=19 * mm, rightMargin=19 * mm,
        topMargin=19 * mm, bottomMargin=21 * mm, title=book["title"],
        author="ACHEWIW — редакционная проба" if print_proof else "ACHEWIW",
    )
    story = [Spacer(1, 35 * mm), Paragraph(escape(book["title"]), title_style), PageBreak()]
    for i, chapter in enumerate(book["chapters"]):
        if i:
            story.append(PageBreak())
        story.append(Paragraph(escape(chapter["title"]), chapter_style))
        for art in illustrations or []:
            if art["scene_id"] == chapter["scene_id"]:
                from PIL import Image as PILImage
                with PILImage.open(art["path"]) as picture:
                    iw, ih = picture.size
                ratio = min(105 * mm / iw, 113 * mm / ih, 1.0)
                story.append(RLImage(str(art["path"]), width=iw * ratio, height=ih * ratio))
                if art.get("caption"):
                    story.append(Paragraph(escape(art["caption"]), style))
        for paragraph in chapter["text"].strip().split("\n\n"):
            if paragraph.strip():
                p = "<br/>".join(escape(line) for line in paragraph.splitlines())
                story.append(Paragraph(p, style))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("ACHEWIW-Serif", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawCentredString(A5[0] / 2, 11 * mm, str(doc.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
