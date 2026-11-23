"""PDF -> editable DOCX for straightforward pages, or fail closed.

No writes to the source PDF, book library or SQLite. Complicated vector layouts,
links and annotations are rejected instead of being represented as a faithful
editable document. An explicitly confirmed text-only mode is available.

This is a reflowed document, NOT a pixel-exact PDF recreation.
"""
from __future__ import annotations

from io import BytesIO
from math import isfinite
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, Inches
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 80 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_PAGE_BLOCKS = 2500
CAPTION = re.compile(r"^(?:рис(?:унок|\.)|фиг(?:ура|\.)|figure|fig\.|таблица|table)\s*(?:№\s*)?\d", re.I)


class UnsupportedPDFLayout(ValueError):
    """This PDF requires an explicit lossy text-only export, or a better importer."""


def _boxes(page):
    """Read text and displayed image blocks without silently removing any images."""
    data = page.get_text("dict", sort=False)
    result = []
    if len(data["blocks"]) > MAX_PAGE_BLOCKS:
        raise UnsupportedPDFLayout("PDF: слишком много объектов на странице")
    for item in data["blocks"]:
        typ = item.get("type")
        bbox = tuple(float(x) for x in item.get("bbox", ()))
        if len(bbox) != 4 or not all(isfinite(x) for x in bbox):
            raise UnsupportedPDFLayout("PDF: некорректная геометрия страницы")
        if typ == 0:
            lines = []
            for line in item.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", []))
                if text:
                    lines.append(text)
            value = "\n".join(lines).strip()
            if value:
                result.append({"type": "text", "bbox": bbox, "text": value})
        elif typ == 1:
            picture = item.get("image")
            if not isinstance(picture, bytes) or not picture:
                raise UnsupportedPDFLayout("PDF: рисунок без доступных данных изображения")
            result.append({
                "type": "image", "bbox": bbox, "image": picture,
                "width": item.get("width", 0), "height": item.get("height", 0),
            })
        else:
            raise UnsupportedPDFLayout("PDF: неподдерживаемый тип встроенного объекта")
    return result


def _reading_order(blocks, page_width: float):
    """Column-major ordering for a verifiable two-column layout.

    A clear gap between x-start clusters plus non-overlapping column widths is
    required. Ambiguous vertical overlap, wide images or three columns are
    rejected in fidelity mode rather than silently shuffled.
    """
    if len(blocks) < 2:
        return blocks
    by_y = lambda b: (round(b["bbox"][1], 2), round(b["bbox"][0], 2))
    starts = sorted(set(round(b["bbox"][0], 1) for b in blocks if b["type"] == "text"))
    candidates = []
    for a, b in zip(starts, starts[1:]):
        if b - a >= max(65, page_width * 0.20):
            candidates.append((a, b))
    if not candidates:
        return sorted(blocks, key=by_y)

    for a, b in sorted(candidates, key=lambda p: p[1] - p[0], reverse=True):
        left_text = [x for x in blocks if x["type"] == "text" and x["bbox"][0] <= a]
        right_text = [x for x in blocks if x["type"] == "text" and x["bbox"][0] >= b]
        if not left_text or not right_text:
            continue
        left_y = (min(x["bbox"][1] for x in left_text),
                  max(x["bbox"][3] for x in left_text))
        right_y = (min(x["bbox"][1] for x in right_text),
                   max(x["bbox"][3] for x in right_text))
        # If text ranges do not overlap vertically, ordinary y-order is safe.
        if max(left_y[0], right_y[0]) >= min(left_y[1], right_y[1]):
            continue

        right_x = min(x["bbox"][0] for x in right_text)
        left_x = max(x["bbox"][2] for x in left_text)
        if left_x + 12 >= right_x:
            raise UnsupportedPDFLayout(
                "PDF: пересекающиеся или неоднозначные колонки; точный порядок чтения не гарантирован"
            )
        split = (left_x + right_x) / 2
        left, right, spanning = [], [], []
        for item in blocks:
            x0, _, x1, _ = item["bbox"]
            if x1 < split:
                left.append(item)
            elif x0 > split:
                right.append(item)
            else:
                spanning.append(item)
        # Blocks across columns cannot be placed safely unless entirely above/below.
        above, below = [], []
        top = min(left_y[0], right_y[0])
        bottom = max(left_y[1], right_y[1])
        for item in spanning:
            if item["bbox"][3] <= top:
                above.append(item)
            elif item["bbox"][1] >= bottom:
                below.append(item)
            else:
                raise UnsupportedPDFLayout(
                    "PDF: объект пересекает две колонки; требуется проверка исходной вёрстки"
                )
        # More than 2 distinct vertical columns are not safely recoverable here.
        for group in (left, right):
            xs = sorted(set(round(x["bbox"][0], 1) for x in group if x["type"] == "text"))
            if any(y - x >= max(65, page_width * 0.20)
                   for x, y in zip(xs, xs[1:])):
                raise UnsupportedPDFLayout("PDF: более двух или вложенные колонки не поддерживаются")
        return (sorted(above, key=by_y) + sorted(left, key=by_y)
                + sorted(right, key=by_y) + sorted(below, key=by_y))
    return sorted(blocks, key=by_y)


def _check_unhandled(page):
    if page.get_drawings():
        raise UnsupportedPDFLayout(
            "PDF содержит векторную графику или линии таблиц: восстановление DOCX без потерь не подтверждено"
        )
    if page.get_links():
        raise UnsupportedPDFLayout(
            "PDF содержит гиперссылки: сохранение их адресов в DOCX пока не поддерживается"
        )
    if page.first_annot:
        raise UnsupportedPDFLayout(
            "PDF содержит аннотации: сохранение комментариев в DOCX пока не поддерживается"
        )
    if page.first_widget:
        raise UnsupportedPDFLayout(
            "PDF содержит поля формы: полный перенос в DOCX пока не поддерживается"
        )


def _text_only_legacy(pdf) -> bytes:
    """Explicitly lossy text extraction with the previous deterministic behavior."""
    doc = Document()
    doc.styles["Normal"].font.name = "Liberation Serif"
    doc.styles["Normal"].font.size = Pt(11)
    total_text = 0
    for i, page in enumerate(pdf):
        if i:
            doc.add_page_break()
        blocks = sorted(page.get_text("blocks"), key=lambda b: (round(b[1] / 8), b[0]))
        for block in blocks:
            if len(block) < 7 or block[6] != 0:
                continue
            text = block[4].strip()
            if not text:
                continue
            total_text += len(text)
            for part in re.split(r"\n\s*\n", text):
                merged = re.sub(r"(?<!-)\n", " ", part).strip()
                if merged:
                    doc.add_paragraph(merged)
    if total_text < 20:
        raise UnsupportedPDFLayout("У PDF нет текстового слоя: нужен OCR")
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def _picture_as_stream(raw: bytes) -> BytesIO:
    try:
        with Image.open(BytesIO(raw)) as im:
            if im.width * im.height > MAX_IMAGE_PIXELS:
                raise UnsupportedPDFLayout("PDF: изображение слишком велико для безопасного импорта")
            # PNG/JPEG are natively accepted by python-docx; convert everything else.
            if im.format in ("PNG", "JPEG"):
                return BytesIO(raw)
            im.load()
            out = BytesIO()
            if im.mode not in ("RGB", "RGBA", "L"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            im.save(out, format="PNG")
            out.seek(0)
            return out
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnsupportedPDFLayout("PDF: невозможно восстановить встроенное изображение") from exc


def _write_block(doc, block, usable_width_pt, remaining_budget):
    if block["type"] == "text":
        text = block["text"]
        paragraph = doc.add_paragraph()
        if CAPTION.match(text):
            paragraph.style = doc.styles["Caption"]
        for i, line in enumerate(text.split("\n")):
            if i:
                paragraph.add_run(" ")
            paragraph.add_run(line)
        return remaining_budget

    raw = block["image"]
    if len(raw) > MAX_IMAGE_BYTES or len(raw) > remaining_budget:
        raise UnsupportedPDFLayout("PDF: превышен безопасный размер переносимых изображений")
    stream = _picture_as_stream(raw)
    x0, y0, x1, y1 = block["bbox"]
    width_pt = min(max(x1 - x0, 8), usable_width_pt)
    image_para = doc.add_paragraph()
    image_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    image_para.paragraph_format.keep_with_next = True
    try:
        image_para.add_run().add_picture(stream, width=Pt(width_pt))
    except (ValueError, OSError) as exc:
        raise UnsupportedPDFLayout("PDF: не удалось вставить изображение в DOCX") from exc
    return remaining_budget - len(raw)


def convert_pdf_to_docx(pdf, *, mode="preserve", confirmed_loss=False) -> bytes:
    """Return an editable, reflowed DOCX. Fail closed for unsupported PDF objects."""
    if mode not in {"preserve", "text_only"}:
        raise UnsupportedPDFLayout("Неизвестный режим импорта PDF")
    if mode == "text_only":
        if confirmed_loss is not True:
            raise UnsupportedPDFLayout(
                "Для извлечения только текста требуется явное подтверждение потери рисунков и вёрстки"
            )
        return _text_only_legacy(pdf)

    # Preflight ALL pages before creating the output. No partial exports.
    text_count = 0
    picture_count = 0
    for page in pdf:
        _check_unhandled(page)
        for block in _boxes(page):
            if block["type"] == "text":
                text_count += len(block["text"])
            else:
                picture_count += 1
        # The order detection can also reject complex overlapping layouts.
        _reading_order(_boxes(page), page.rect.width)
    if text_count < 20:
        raise UnsupportedPDFLayout("У PDF нет достаточного текстового слоя: нужен OCR")
    if picture_count > 1000:
        raise UnsupportedPDFLayout("PDF: слишком много отдельных изображений")

    doc = Document()
    doc.styles["Normal"].font.name = "Liberation Serif"
    doc.styles["Normal"].font.size = Pt(11)
    # The result is editable text; page layout is deliberately not claimed exact.
    remaining_budget = MAX_TOTAL_IMAGE_BYTES
    for i, page in enumerate(pdf):
        if i:
            doc.add_page_break()
        usable_width = max(100, min(page.rect.width - 72, 500))
        blocks = _reading_order(_boxes(page), page.rect.width)
        for block in blocks:
            remaining_budget = _write_block(doc, block, usable_width, remaining_budget)

    out = BytesIO()
    doc.save(out)
    return out.getvalue()
