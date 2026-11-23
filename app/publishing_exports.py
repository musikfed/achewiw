"""Print-proof PDF, editable DOCX, FB2 for author-approved books only.

All formats share author/year/imprint and exact chapter-art placement with EPUB.
A5 PDF is a reading/print PROOF, not certified PDF/X or print-ready cover wrap.
"""
from __future__ import annotations

import base64
from html import escape
from io import BytesIO
from xml.etree import ElementTree as ET
from book_layout_core import (info, colophon, paragraphs, cover_image,
    group_images, before_images, after_images, end_images, verified_illustrations)
from manuscript_core import chapter_listing


def _chapters(project):
    book=chapter_listing(project)
    if not book["chapters"]:
        raise ValueError("Для экспорта требуется текст хотя бы одной сохранённой главы")
    return book


def manuscript_fb2(project, illustrations=None) -> bytes:
    book=_chapters(project);meta=info(project)
    illustrations=verified_illustrations(illustrations)
    ns="http://www.gribuser.ru/xml/fictionbook/2.0"
    link="http://www.w3.org/1999/xlink"
    ET.register_namespace("",ns)
    ET.register_namespace("l",link)
    root=ET.Element(f"{{{ns}}}FictionBook")
    description=ET.SubElement(root,f"{{{ns}}}description")
    ti=ET.SubElement(description,f"{{{ns}}}title-info")
    ET.SubElement(ti,f"{{{ns}}}genre").text=(
        "sf" if getattr(project,"work_type","fiction")=="fiction" else "nonfiction")
    if meta["author"]:
        a=ET.SubElement(ti,f"{{{ns}}}author")
        ET.SubElement(a,f"{{{ns}}}first-name").text=meta["author"]
    ET.SubElement(ti,f"{{{ns}}}book-title").text=book["title"]
    ET.SubElement(ti,f"{{{ns}}}lang").text="ru"
    if meta["year"] is not None:
        ET.SubElement(ti,f"{{{ns}}}date",{"value":str(meta["year"])}).text=str(meta["year"])
    cover=cover_image(illustrations)
    if cover:
        coverpage=ET.SubElement(ti,f"{{{ns}}}coverpage")
        ET.SubElement(coverpage,f"{{{ns}}}image",{f"{{{link}}}href":"#img-"+cover["id"]})
    body=ET.SubElement(root,f"{{{ns}}}body")
    title=ET.SubElement(body,f"{{{ns}}}title")
    ET.SubElement(title,f"{{{ns}}}p").text=book["title"]
    for chapter in book["chapters"]:
        sec=ET.SubElement(body,f"{{{ns}}}section")
        heading=ET.SubElement(sec,f"{{{ns}}}title")
        ET.SubElement(heading,f"{{{ns}}}p").text=chapter["title"]
        arts=group_images(illustrations,chapter["scene_id"])
        def put_art(art):
            ET.SubElement(sec,f"{{{ns}}}image",{f"{{{link}}}href":"#img-"+art["id"]})
            if art.get("caption"):
                ET.SubElement(sec,f"{{{ns}}}p").text=art["caption"]
        for art in before_images(arts):put_art(art)
        parts=paragraphs(chapter["text"])
        for n,part in enumerate(parts,1):
            ET.SubElement(sec,f"{{{ns}}}p").text=part
            for art in after_images(arts,n):put_art(art)
        for art in end_images(arts,len(parts)):put_art(art)
    tail=ET.SubElement(body,f"{{{ns}}}section")
    h=ET.SubElement(tail,f"{{{ns}}}title")
    ET.SubElement(h,f"{{{ns}}}p").text="Выходные сведения"
    for line in colophon(meta):ET.SubElement(tail,f"{{{ns}}}p").text=line
    for art in illustrations:
        ET.SubElement(root,f"{{{ns}}}binary",
            {"id":"img-"+art["id"],"content-type":"image/png"}).text=(
                base64.b64encode(art["path"].read_bytes()).decode("ascii"))
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)


def manuscript_docx(project, illustrations=None) -> bytes:
    from docx import Document
    from docx.shared import Cm, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    book=_chapters(project);meta=info(project)
    illustrations=verified_illustrations(illustrations)
    from PIL import Image as PILImage
    doc=Document()
    sec=doc.sections[0]
    sec.page_height,sec.page_width=Cm(21),Cm(14.8)
    sec.left_margin,sec.right_margin=Cm(2.1),Cm(1.8)
    sec.top_margin,sec.bottom_margin=Cm(2),Cm(2)
    normal=doc.styles["Normal"]
    normal.font.name="DejaVu Serif";normal.font.size=Pt(11)
    normal.paragraph_format.space_after=Pt(6)
    doc.core_properties.title=book["title"]
    doc.core_properties.author=meta["author"]
    doc.core_properties.comments=meta["credit"]
    cover=cover_image(illustrations)
    if cover and cover.get("path"):
        p=doc.add_paragraph();p.alignment=WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(str(cover["path"]),width=Cm(10.7))
        doc.add_page_break()
    h=doc.add_paragraph(book["title"],"Title");h.alignment=WD_ALIGN_PARAGRAPH.CENTER
    for label in (meta["author"],meta["edition"],meta["publisher"],
                  str(meta["year"]) if meta["year"] is not None else ""):
        if label:
            p=doc.add_paragraph(label);p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()
    doc.add_heading("Содержание",level=1)
    for ch in book["chapters"]:
        entry=doc.add_paragraph(f"{ch['index']}. {ch['title']}")
        entry.paragraph_format.left_indent=Cm(0.5)
    credits_entry=doc.add_paragraph("Выходные сведения")
    credits_entry.paragraph_format.left_indent=Cm(0.5)
    doc.add_page_break()
    def put_art(art):
        # Word: image paragraph must stay with its caption on the same page.
        # Cap the image height so portrait artwork can fit beside a caption.
        with PILImage.open(art["path"]) as im:
            iw, ih = im.size
        ratio=min(Cm(10.5)/iw, Cm(12.0)/ih)
        p=doc.add_paragraph()
        p.alignment=WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_together=True
        p.paragraph_format.keep_with_next=bool(art.get("caption"))
        p.add_run().add_picture(str(art["path"]),width=Pt(iw*ratio))
        if art.get("caption"):
            caption=doc.add_paragraph(art["caption"],style="Caption")
            caption.paragraph_format.keep_together=True
    for i,ch in enumerate(book["chapters"]):
        if i:doc.add_page_break()
        doc.add_heading(ch["title"],level=1)
        arts=group_images(illustrations,ch["scene_id"])
        for a in before_images(arts):put_art(a)
        parts=paragraphs(ch["text"])
        for n,part in enumerate(parts,1):
            doc.add_paragraph(part)
            for a in after_images(arts,n):put_art(a)
        for a in end_images(arts,len(parts)):put_art(a)
    doc.add_page_break()
    doc.add_heading("Выходные сведения",level=1)
    for line in colophon(meta):doc.add_paragraph(line)
    out=BytesIO();doc.save(out)
    return out.getvalue()


def manuscript_pdf(project, *, print_proof=False, illustrations=None, draft_notice: str | None = None) -> bytes:
    """A5 Unicode PDF proof: real TOC page numbers via ReportLab multiBuild."""
    from pathlib import Path
    from PIL import Image as PILImage
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER,TA_JUSTIFY,TA_LEFT
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (SimpleDocTemplate,Paragraph,Spacer,
        PageBreak,Image as RLImage,KeepTogether)
    from reportlab.platypus.tableofcontents import TableOfContents

    book=_chapters(project);meta=info(project)
    illustrations=verified_illustrations(illustrations)
    regular=Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
    bold=Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")
    if not regular.is_file() or not bold.is_file():
        raise RuntimeError("Для PDF требуется fonts-dejavu-core с поддержкой кириллицы")
    if "ACHEWIW-Serif" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ACHEWIW-Serif",str(regular)))
        pdfmetrics.registerFont(TTFont("ACHEWIW-Serif-Bold",str(bold)))

    body=ParagraphStyle("Body",fontName="ACHEWIW-Serif",
        fontSize=10.5 if print_proof else 11,leading=16,alignment=TA_JUSTIFY,spaceAfter=7)
    chapterstyle=ParagraphStyle("Chapter",fontName="ACHEWIW-Serif-Bold",
        fontSize=15,leading=22,spaceBefore=12,spaceAfter=17)
    title=ParagraphStyle("BookTitle",parent=chapterstyle,alignment=TA_CENTER,
        fontSize=20,leading=28,spaceAfter=25)
    center=ParagraphStyle("Centered",parent=body,alignment=TA_CENTER)
    tocstyle=ParagraphStyle("ToC",fontName="ACHEWIW-Serif",fontSize=10,
        leading=15,leftIndent=11,firstLineIndent=-11)

    class TocDocument(SimpleDocTemplate):
        def afterFlowable(self,flowable):
            if isinstance(flowable,Paragraph) and getattr(flowable,"_achewiw_chapter",False):
                self.notify("TOCEntry",(0,flowable.getPlainText(),self.page))

    output=BytesIO()
    doc=TocDocument(output,pagesize=A5,leftMargin=19*mm,rightMargin=19*mm,
        topMargin=19*mm,bottomMargin=21*mm,title=book["title"],
        author=meta["author"] or "ACHEWIW")
    story=[]
    def put_art(art,cover=False):
        # ReportLab's KeepTogether keeps picture and caption on the same page.
        # Reserve enough frame height for the caption before sizing artwork.
        caption=None
        caption_height=0
        if art.get("caption") and not cover:
            caption=Paragraph(escape(art["caption"]),center)
            _, caption_height=caption.wrap(doc.width, doc.height)
        free_height=doc.height-caption_height-24
        if free_height<35:
            raise ValueError("Подпись иллюстрации "+art["id"]+" не помещается на странице A5")
        with PILImage.open(art["path"]) as image:
            iw,ih=image.size
        max_width=(110 if cover else 105)*mm
        max_height=min((150 if cover else 113)*mm, free_height)
        ratio=min(max_width/iw,max_height/ih,1.0)
        image_flowable=RLImage(str(art["path"]),width=iw*ratio,height=ih*ratio)
        image_flowable.hAlign="CENTER"
        if caption:
            story.append(KeepTogether([image_flowable, caption]))
        else:
            story.append(image_flowable)
    cover=cover_image(illustrations)
    if cover and cover.get("path"):
        put_art(cover,cover=True)
        story.append(PageBreak())
    story.extend([Spacer(1,25*mm),Paragraph(escape(book["title"]),title)])
    if draft_notice:
        proof_note=ParagraphStyle("DraftNotice",parent=center,
            fontName="ACHEWIW-Serif-Bold",fontSize=10,
            leading=15,textColor=colors.HexColor("#8A3434"),spaceAfter=18)
        story.append(Paragraph(escape(draft_notice),proof_note))
    for field in (meta["author"],meta["edition"],meta["publisher"],
                  str(meta["year"]) if meta["year"] is not None else ""):
        if field:story.append(Paragraph(escape(field),center))
    story.append(PageBreak())
    story.append(Paragraph("Содержание",chapterstyle))
    toc=TableOfContents()
    toc.levelStyles=[tocstyle]
    story.append(toc)
    story.append(PageBreak())
    for i,ch in enumerate(book["chapters"]):
        if i:story.append(PageBreak())
        title_para=Paragraph(escape(ch["title"]),chapterstyle)
        title_para._achewiw_chapter=True
        story.append(title_para)
        arts=group_images(illustrations,ch["scene_id"])
        for art in before_images(arts):put_art(art)
        parts=paragraphs(ch["text"])
        for n,part in enumerate(parts,1):
            story.append(Paragraph("<br/>".join(escape(x) for x in part.splitlines()),body))
            for art in after_images(arts,n):put_art(art)
        for art in end_images(arts,len(parts)):put_art(art)
    story.append(PageBreak())
    info_p=Paragraph("Выходные сведения",chapterstyle)
    info_p._achewiw_chapter=True
    story.append(info_p)
    for line in colophon(meta):story.append(Paragraph(escape(line),body))

    def footer(canvas,doc):
        canvas.saveState();canvas.setFont("ACHEWIW-Serif",8)
        canvas.setFillColor(colors.grey)
        canvas.drawCentredString(A5[0]/2,11*mm,str(doc.page))
        if draft_notice:
            canvas.setFont("ACHEWIW-Serif",7)
            canvas.setFillColor(colors.HexColor("#A04444"))
            canvas.drawCentredString(A5[0]/2,A5[1]-11*mm,
                "ЧЕРНОВИК ИИ — НЕ ДЛЯ ПУБЛИКАЦИИ")
        canvas.restoreState()

    doc.multiBuild(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
