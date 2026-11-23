"""ACHEWIW Document Workshop: non-destructive DOCX styling and PDF conversion.

An isolated utility: no manuscript canon, SQLite schema, Ollama or GPU access.
DOCX->PDF uses LibreOffice Writer (visual layout as available fonts permit).
PDF->DOCX extracts selectable text; complex pagination/images are NOT reversible.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote
import zipfile

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docwork_image_layout import (
    inventory_docx, apply_image_layout, apply_image_replacements, IllustrationLayoutError
)
from docwork_image_moves import apply_image_moves, paragraph_section_indices

MAX_UPLOAD = 20 * 1024 * 1024
MAX_UNPACKED = 180 * 1024 * 1024
MAX_PARAGRAPHS = 20000
# GOST is a document-formatting requirement, not a universal font family.
# These are permitted choices; standards-specific profiles are introduced in R4.
# The Docker image installs the open families below. Microsoft families are
# retained for DOCX compatibility and may be substituted by LibreOffice on Linux.
FONT_NAMES = {
    "Liberation Serif", "Liberation Sans", "Liberation Mono",
    "DejaVu Serif", "DejaVu Sans", "DejaVu Sans Mono",
    "Carlito", "Caladea", "FreeSerif", "FreeSans", "FreeMono",
    "Times New Roman", "Arial", "Calibri",
}
PRESETS = {
    "preserve": {"name": "Сохранить исходное", "body": None, "head": None, "size": None},
    "book": {"name": "Книжная классика", "body": "Liberation Serif", "head": "Liberation Serif", "size": 11},
    "technical": {"name": "Техническое издание", "body": "DejaVu Serif", "head": "DejaVu Sans", "size": 10.5},
    "modern": {"name": "Современная редакция", "body": "Arial", "head": "Arial", "size": 11},
    "karate": {"name": "KARATE LINUX", "body": "Liberation Serif", "head": "DejaVu Sans", "size": 11},
}
CHAPTER = re.compile(r"^(?:глава|часть|урок|chapter|part|lesson)\s*(?:№\s*)?\d+(?:[.:\s]|$)", re.I)
SUBHEADING = re.compile(r"^\d+(?:\.\d+){1,4}\.?\s+\S", re.U)
SIMPLE_TITLE = re.compile(r"^(?:введение|заключение|содержание|предисловие|послесловие|список литературы|источники)$", re.I)
FORMATS = {"keep", "body", "heading1", "heading2"}
PARAGRAPH_ALIGNMENTS = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}
DANGEROUS_PARTS = {"word/vbaproject.bin", "word/activeX/"}


class DocumentWorkshopError(ValueError):
    pass


def validate_upload(data: bytes, filename: str) -> str:
    if not data or len(data) > MAX_UPLOAD:
        raise DocumentWorkshopError("Файл пуст или превышает 20 МиБ")
    ext = Path(filename or "").suffix.lower()
    if ext not in {".docx", ".pdf"}:
        raise DocumentWorkshopError("Поддерживаются только DOCX и PDF")
    if ext == ".pdf":
        if not data.lstrip()[:8].startswith(b"%PDF-"):
            raise DocumentWorkshopError("Недопустимый PDF")
        return "pdf"
    if not zipfile.is_zipfile(BytesIO(data)):
        raise DocumentWorkshopError("Некорректный DOCX: отсутствует ZIP-контейнер")
    with zipfile.ZipFile(BytesIO(data)) as archive:
        names = [entry.filename.lower() for entry in archive.infolist()]
        if "[content_types].xml" not in names or "word/document.xml" not in names:
            raise DocumentWorkshopError("Это ZIP, но не документ DOCX")
        if sum(entry.file_size for entry in archive.infolist()) > MAX_UNPACKED:
            raise DocumentWorkshopError("Слишком большой распакованный DOCX")
        if any("vbaproject" in part or "activex/" in part for part in names):
            raise DocumentWorkshopError("Документ содержит активное содержимое; используйте обычный DOCX")
    return "docx"


def output_filename(source: str, requested: str, suffix: str) -> str:
    base = requested.strip() or (Path(source).stem + "_редакция")
    base = re.sub(r"\.(docx|pdf)$", "", base, flags=re.I)
    base = "".join(c for c in base if c.isalnum() or c in " _-.()")
    base = re.sub(r"\s+", " ", base).strip(" ._-")
    if not base:
        base = "ACHEWIW_document"
    return base[:100] + "." + suffix


def content_disposition(name: str) -> str:
    return 'attachment; filename="ACHEWIW.' + Path(name).suffix.lstrip(".") + '"; filename*=UTF-8\'\'' + quote(name)


def inferred_kind(paragraph) -> str:
    text = paragraph.text.strip()
    style = paragraph.style.name.lower() if paragraph.style is not None else ""
    if "heading 1" in style or style in {"заголовок 1", "heading1"}:
        return "heading1"
    if "heading 2" in style or style in {"заголовок 2", "heading2"}:
        return "heading2"
    if CHAPTER.match(text) or SIMPLE_TITLE.match(text):
        return "heading1"
    if SUBHEADING.match(text) and len(text) <= 160:
        return "heading2"
    return "body"


def preview_docx(data: bytes) -> dict:
    doc = Document(BytesIO(data))
    if len(doc.paragraphs) > MAX_PARAGRAPHS:
        raise DocumentWorkshopError("Документ слишком велик для интерактивной редакции")
    rows = []
    sections_by_index = paragraph_section_indices(doc.paragraphs)
    kinds = {"heading1": 0, "heading2": 0, "body": 0}
    for idx, paragraph in enumerate(doc.paragraphs):
        kind = inferred_kind(paragraph)
        kinds[kind] += 1
        # The full manuscript must be navigable, not just its first 160 paragraphs.
        # Limit excessive API payloads for unusually large documents.
        if len(rows) < 3000 and (paragraph.text.strip() or kind != "body"):
            protected = _complex_paragraph(paragraph) or len(paragraph.text) > 5000
            explicit_alignment = next(
                (key for key, value in PARAGRAPH_ALIGNMENTS.items()
                 if paragraph.alignment == value), "inherit"
            )
            rows.append({"index": idx, "kind": kind, "style": paragraph.style.name,
                         "section_index": sections_by_index.get(idx, 0),
                         "alignment": explicit_alignment,
                         "text": paragraph.text if len(paragraph.text) <= 5000 else paragraph.text[:300],
                         "editable": not protected})
    warnings = [
        "Список абзацев — структурный просмотр. Для настоящей вёрстки нажмите «Посмотреть страницы PDF».",
        "Таблицы, изображения и колонтитулы остаются в DOCX; массовые стили применяются к обычным абзацам.",
    ]
    if len(rows) >= 3000:
        warnings.append("Список ограничен первыми 3000 непустыми абзацами; остальной документ сохраняется в экспорте.")
    images = inventory_docx(data)
    for item in images:
        item["section_index"] = sections_by_index.get(item.get("paragraph_index"))
    if images:
        warnings.append(
            f"Найдено встроенных иллюстраций: {len(images)}. "
            "Для отдельных рисунков можно изменить размер, выравнивание и скругление."
        )
    first_section = doc.sections[0]
    usable = first_section.page_width - first_section.left_margin - first_section.right_margin
    return {"type": "docx", "paragraph_count": len(doc.paragraphs),
            "text_width_cm": round(usable / 360000, 2),
            "tables": len(doc.tables), "sections": len(doc.sections), "kinds": kinds,
            "images": images, "paragraphs": rows, "warnings": warnings}


def preview_pdf(data: bytes) -> dict:
    import fitz
    with fitz.open(stream=data, filetype="pdf") as pdf:
        if pdf.needs_pass:
            raise DocumentWorkshopError("Защищённый PDF: требуется снять пароль")
        if len(pdf) > 400:
            raise DocumentWorkshopError("PDF более 400 страниц не поддерживается")
        snippets = []
        chars = 0
        for i, page in enumerate(pdf):
            text = page.get_text("text")
            chars += len(text)
            if i < 8:
                snippets.append({"page": i+1, "text": text[:1100]})
        # Inspection is not a promise of perfect PDF layout reconstruction.
        images = sum(
            sum(1 for b in page.get_text("dict", sort=False)["blocks"] if b.get("type") == 1)
            for page in pdf
        )
        vector_pages = sum(bool(page.get_drawings()) for page in pdf)
        warnings = [
            "PDF → DOCX: текст и встроенные растровые иллюстрации переносятся в редактируемый документ "
            "для поддерживаемых страниц; точная геометрия исходного PDF не гарантируется.",
            "Сложная векторная графика, таблицы, неоднозначные колонки или аннотации блокируют "
            "точный перенос. Отдельный режим «Только текст» доступен после подтверждения потерь.",
        ]
        if images:
            warnings.append(f"В PDF найдено отображений растровых изображений: {images}.")
        if vector_pages:
            warnings.append(f"Страниц с векторными рисунками/линиями: {vector_pages}; без потерь импорт не гарантирован.")
        if chars < 20:
            warnings.append("Не найден текстовый слой. Для сканированного PDF необходим OCR.")
        return {"type": "pdf", "pages": len(pdf), "characters": chars, "images": images,
                "vector_pages": vector_pages, "sample_pages": snippets,
                "warnings": warnings}


def preview(data: bytes, filename: str) -> dict:
    fmt = validate_upload(data, filename)
    result = preview_docx(data) if fmt == "docx" else preview_pdf(data)
    result["source_name"] = Path(filename).name
    result["presets"] = [{"id": key, "name": value["name"]} for key, value in PRESETS.items()]
    return result


def _complex_paragraph(p) -> bool:
    # Never flatten inline pictures, hyperlinks, bookmarks, field instructions or content controls.
    if any(child.tag not in {qn("w:pPr"), qn("w:r")} for child in p._p):
        return True
    return any(item.tag not in {qn("w:rPr"), qn("w:t"), qn("w:br"), qn("w:tab"),
                               qn("w:cr"), qn("w:lastRenderedPageBreak")}
               for run in p._p.findall(qn("w:r")) for item in run)


def _remove_shading(root):
    # Direct properties, not the coloring of tables or floating figures.
    for properties_tag in ("w:rPr", "w:pPr"):
        for properties in root.iter(qn(properties_tag)):
            for tag in ("w:highlight", "w:shd"):
                for element in list(properties.findall(qn(tag))):
                    properties.remove(element)


def _remove_proofing(doc):
    settings = doc.settings.element
    for name in ("w:hideSpellingErrors", "w:hideGrammaticalErrors"):
        elem = settings.find(qn(name))
        if elem is None:
            elem = OxmlElement(name)
            settings.append(elem)
        elem.set(qn("w:val"), "1")
    # Existing proofing markers must not remain embedded in the saved document.
    for paragraph in doc.paragraphs:
        for elem in list(paragraph._p.iter(qn("w:proofErr"))):
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)


def _set_east_asia(style, family):
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for name in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(name), family)


def _apply_preset(doc, name: str):
    if name == "preserve":
        return
    spec = PRESETS[name]
    sec = doc.sections[0]
    if name in ("book", "karate"):
        sec.page_width, sec.page_height = Cm(14.8), Cm(21)
        sec.top_margin, sec.bottom_margin = Cm(2), Cm(2)
        sec.left_margin, sec.right_margin = Cm(2.1), Cm(1.8)
    body = doc.styles["Normal"]
    body.font.name = spec["body"]
    body.font.size = Pt(spec["size"])
    _set_east_asia(body, spec["body"])
    body.paragraph_format.space_after = Pt(6)
    body.paragraph_format.line_spacing = 1.25 if name == "modern" else 1.2
    if name in ("book", "karate"):
        body.paragraph_format.first_line_indent = Cm(0.5)
        body.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    for level, size in ((1, 19 if name == "karate" else 17), (2, 13 if name == "karate" else 13)):
        h = doc.styles[f"Heading {level}"]
        h.font.name = spec["head"]
        _set_east_asia(h, spec["head"])
        h.font.size = Pt(size)
        h.font.bold = True
        h.font.color.rgb = RGBColor(20, 78, 44) if name == "karate" else RGBColor(35, 47, 58)
        h.paragraph_format.space_before = Pt(15 if level == 1 else 11)
        h.paragraph_format.space_after = Pt(10 if level == 1 else 6)
        h.paragraph_format.keep_with_next = True


def _custom_heading_fonts(doc, options: dict):
    """Override font family/size per role without rewriting paragraph text."""
    font_names = {
        "body_font": "Normal",
        "chapter_font": "Heading 1",
        "subchapter_font": "Heading 2",
    }
    for key, stylename in font_names.items():
        family = options.get(key, "keep")
        if family in (None, "keep", ""):
            continue
        if not isinstance(family, str) or family not in FONT_NAMES:
            raise DocumentWorkshopError("Шрифт вне разрешённого списка")
        style = doc.styles[stylename]
        style.font.name = family
        _set_east_asia(style, family)
    sizes = {"body_size": ("Normal", 8, 16),
             "chapter_size": ("Heading 1", 11, 30),
             "subchapter_size": ("Heading 2", 9, 25)}
    for key, (stylename, lower, upper) in sizes.items():
        size = options.get(key)
        if size in (None, ""):
            continue
        try:
            numeric = float(size)
        except (TypeError, ValueError) as exc:
            raise DocumentWorkshopError("Размер шрифта должен быть числом") from exc
        if not lower <= numeric <= upper:
            raise DocumentWorkshopError("Размер шрифта вне допустимого диапазона")
        doc.styles[stylename].font.size = Pt(numeric)
    if options.get("heading_bold", True):
        doc.styles["Heading 1"].font.bold = True
        doc.styles["Heading 2"].font.bold = True


def _replace_simple_text(paragraph, new_text):
    if _complex_paragraph(paragraph):
        raise DocumentWorkshopError("Нельзя менять текст абзаца с изображением, полем или гиперссылкой")
    if len(new_text) > 5000:
        raise DocumentWorkshopError("Слишком длинный заменяемый абзац")
    # Only explicitly edited paragraph loses local run-by-run formatting.
    runs = paragraph.runs
    if runs:
        runs[0].text = new_text
        for run in runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(new_text)


def transform_docx(data: bytes, options: dict) -> bytes:
    if not isinstance(options, dict):
        raise DocumentWorkshopError("Некорректные параметры")
    preset = options.get("preset", "preserve")
    if preset not in PRESETS:
        raise DocumentWorkshopError("Неизвестный стиль оформления")
    overrides = options.get("overrides", {})
    replacements = options.get("replacements", {})
    alignments = options.get("paragraph_alignments", {})
    if not isinstance(overrides, dict) or not isinstance(replacements, dict) or not isinstance(alignments, dict):
        raise DocumentWorkshopError("Некорректная карта изменений")
    if len(overrides) > 1000 or len(replacements) > 100 or len(alignments) > 1000:
        raise DocumentWorkshopError("Слишком много изменений за один проход")
    doc = Document(BytesIO(data))
    if len(doc.paragraphs) > MAX_PARAGRAPHS:
        raise DocumentWorkshopError("Слишком большой документ")
    # Reject all invalid paragraph indices before touching the output copy.
    for key, alignment in alignments.items():
        if (not isinstance(key, str) or not key.isdecimal()
                or int(key) >= len(doc.paragraphs)
                or alignment not in PARAGRAPH_ALIGNMENTS):
            raise DocumentWorkshopError("Недопустимый номер или выравнивание абзаца")
    _apply_preset(doc, preset)
    _custom_heading_fonts(doc, options)
    if options.get("clear_highlights", False):
        _remove_shading(doc.element.body)
        # Also clear direct run shading in table content without changing cell shading.
    if options.get("hide_proofing", False):
        _remove_proofing(doc)
    for idx, paragraph in enumerate(doc.paragraphs):
        manual = overrides.get(str(idx), "keep")
        if manual not in FORMATS:
            raise DocumentWorkshopError("Неизвестная роль абзаца")
        # Direct paragraph alignment preserves text runs, fields and images.
        if str(idx) in alignments:
            paragraph.alignment = PARAGRAPH_ALIGNMENTS[alignments[str(idx)]]
        if str(idx) in replacements:
            new_text = replacements[str(idx)]
            if not isinstance(new_text, str):
                raise DocumentWorkshopError("Текст абзаца должен быть строкой")
            _replace_simple_text(paragraph, new_text)
        kind = (manual if manual != "keep" else
                inferred_kind(paragraph) if options.get("auto_headings", False) else "keep")
        if kind in ("heading1", "heading2"):
            paragraph.style = doc.styles["Heading " + kind[-1]]
            if options.get("heading_bold", True):
                for run in paragraph.runs:
                    run.bold = True
        elif kind == "body":
            paragraph.style = doc.styles["Normal"]
    try:
        # First bind replacement pixels, then style the NEW picture so corner
        # rounding and aspect-safe sizing are not lost on replaced images.
        apply_image_replacements(doc, options.get("_replacement_blobs", {}))
        apply_image_layout(doc, options.get("image_edits", {}))
        apply_image_moves(doc, options.get("image_moves", {}))
    except IllustrationLayoutError as exc:
        raise DocumentWorkshopError(str(exc)) from exc
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def pdf_to_docx(data: bytes, options: dict | None = None) -> bytes:
    """Reflow uncomplicated PDF pages, preserving images and column reading order.

    For complex PDF documents, refuse incomplete 'faithful' conversion and
    require the explicit, confirmed pdf_mode='text_only' opt-in.
    """
    import fitz
    from pdf_layout_import import convert_pdf_to_docx, UnsupportedPDFLayout

    settings = options or {}
    with fitz.open(stream=data, filetype="pdf") as pdf:
        if pdf.needs_pass:
            raise DocumentWorkshopError("Защищённый PDF")
        if len(pdf) > 400:
            raise DocumentWorkshopError("PDF более 400 страниц не поддерживается")
        try:
            return convert_pdf_to_docx(
                pdf, mode=settings.get("pdf_mode", "preserve"),
                confirmed_loss=settings.get("confirm_text_loss", False),
            )
        except UnsupportedPDFLayout as exc:
            raise DocumentWorkshopError(str(exc)) from exc


def docx_to_pdf(data: bytes) -> bytes:
    cmd = shutil.which("libreoffice") or shutil.which("soffice")
    if not cmd:
        raise DocumentWorkshopError("LibreOffice Writer отсутствует на сервере; конвертация в PDF недоступна")
    with tempfile.TemporaryDirectory(prefix="achewiw_docwork_") as work:
        folder = Path(work)
        source = folder / "manuscript.docx"
        source.write_bytes(data)
        profile = (folder / "lo-profile").as_uri()
        try:
            run = subprocess.run(
                [cmd, "--headless", "-env:UserInstallation=" + profile,
                 "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(folder), str(source)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=150, check=False,
                cwd=folder,
            )
        except subprocess.TimeoutExpired as e:
            raise DocumentWorkshopError("Превышено время ожидания LibreOffice (150 секунд)") from e
        target = folder / "manuscript.pdf"
        if run.returncode or not target.is_file() or target.stat().st_size == 0:
            raise DocumentWorkshopError("LibreOffice не смог преобразовать DOCX в PDF")
        return target.read_bytes()


def export(data: bytes, filename: str, options: dict, target: str) -> tuple[bytes, str, str]:
    source = validate_upload(data, filename)
    if target not in {"docx", "pdf"}:
        raise DocumentWorkshopError("Доступные форматы: DOCX и PDF")
    requested = str(options.get("output_name", "") or "")
    if source == "pdf":
        if target == "pdf":
            # No recompression or hidden PDF mutation.
            return data, output_filename(filename, requested, "pdf"), "application/pdf"
        result = pdf_to_docx(data, options)
        result = transform_docx(result, options)
        return result, output_filename(filename, requested, "docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    revised = transform_docx(data, options)
    if target == "docx":
        return revised, output_filename(filename, requested, "docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return docx_to_pdf(revised), output_filename(filename, requested, "pdf"), "application/pdf"
