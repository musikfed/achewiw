"""DocWork R3.1: editorial styling of *existing inline* Word illustrations.

- Never mutates source DOCX or shared parts.
- Only explicitly selected single-image paragraphs outside tables are editable.
- Controls relative width, horizontal alignment and smooth transparent corners.
- Rounding creates a NEW embedded PNG per edited picture to avoid altering
  pictures elsewhere in the document that share the same original image part.
- Floating drawings, diagrams, charts, grouped figures and tables are left
  untouched and reported as unsupported.
"""
from __future__ import annotations

from io import BytesIO

from docx import Document
from docx.enum.shape import WD_INLINE_SHAPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from PIL import Image, ImageChops, ImageDraw, UnidentifiedImageError

MAX_IMAGES = 100
MAX_IMAGE_PIXELS = 16_000_000
MAX_THUMBNAIL_BYTES = 250_000
ALIGNS = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}
MAX_RADIUS_PERCENT = 24.0


def aspect_radius_cap(width: int, height: int) -> float:
    """Keep very wide/very tall diagrams subtly rounded, squares may use more."""
    if not width or not height:
        return 0.0
    aspect = max(width, height) / min(width, height)
    return 8.0 if aspect >= 2.3 else 12.0 if aspect >= 1.5 else 18.0 if aspect >= 1.2 else 24.0


def actual_corner_percent(width: int, height: int, chosen: float) -> float:
    return min(float(chosen), aspect_radius_cap(width, height))



class IllustrationLayoutError(ValueError):
    pass


def _ancestor(node, tag: str):
    while node is not None:
        if node.tag == qn(tag):
            return node
        node = node.getparent()
    return None


def _picture_entry(doc, index: int, shape):
    """Return picture metadata and source bytes; never follow external links."""
    inline = shape._inline
    paragraph = _ancestor(inline, "w:p")
    table = _ancestor(inline, "w:tbl")
    blips = inline.xpath(".//a:blip")
    rid = blips[0].get(qn("r:embed")) if len(blips) == 1 else None
    part = doc.part.related_parts.get(rid) if rid else None
    if not part or not getattr(part, "content_type", "").startswith("image/"):
        return {"index": index, "editable": False,
                "reason": "Необычное изображение или ссылка, редактирование отключено"}, None
    try:
        raw = part.blob
        with Image.open(BytesIO(raw)) as img:
            width, height = img.size
            if width * height > MAX_IMAGE_PIXELS:
                return {"index": index, "editable": False,
                        "reason": "Изображение слишком велико для стилизации"}, None
            img.verify()
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
        return {"index": index, "editable": False,
                "reason": "Встроенное изображение повреждено"}, None

    # A paragraph with any text/more than one picture must not be realigned
    # because the operation would move unrelated editorial content.
    picture_count = len(paragraph.xpath(".//w:drawing")) if paragraph is not None else 0
    editable = (
        shape.type == WD_INLINE_SHAPE.PICTURE
        and paragraph is not None
        and table is None
        and not "".join(paragraph.itertext()).strip()
        and picture_count == 1
    )
    top_paragraphs = doc.paragraphs
    paragraph_index = next(
        (i for i, p in enumerate(top_paragraphs) if p._p == paragraph), None
    ) if paragraph is not None else None
    caption_index = None
    if editable and paragraph_index is not None:
        following = paragraph.getnext()
        if following is not None and following.tag == qn("w:p"):
            for i, p in enumerate(top_paragraphs):
                if p._p == following:
                    if (p.style and p.style.name.lower() in ("caption", "подпись")) or (
                        p.text.strip().lower().startswith(("рисунок ", "рис. ", "figure ", "fig. "))
                    ):
                        caption_index = i
                    break
    return {
        "index": index, "editable": editable,
        "paragraph_index": paragraph_index,
        "caption_index": caption_index,
        "reason": "" if editable else
                  "Текст, несколько объектов, таблица или сложная фигура: оставлено без изменений",
        "width_px": width, "height_px": height,
        "ratio": round(width / height, 3) if height else 1,
        "corner_max_percent": aspect_radius_cap(width, height),
        "inline_width_cm": round(shape.width / 360000, 2),
        "inline_height_cm": round(shape.height / 360000, 2),
    }, raw


def inventory(doc) -> list[dict]:
    count = len(doc.inline_shapes)
    result = []
    for index, shape in enumerate(doc.inline_shapes):
        if index >= MAX_IMAGES:
            break
        record, _ = _picture_entry(doc, index, shape)
        result.append(record)
    return result


def inventory_docx(data: bytes) -> list[dict]:
    return inventory(Document(BytesIO(data)))


def thumbnail(data: bytes, index: int) -> bytes:
    doc = Document(BytesIO(data))
    if type(index) is not int or index < 0 or index >= min(len(doc.inline_shapes), MAX_IMAGES):
        raise IllustrationLayoutError("Иллюстрация не найдена")
    record, raw = _picture_entry(doc, index, doc.inline_shapes[index])
    if raw is None:
        raise IllustrationLayoutError(record["reason"])
    with Image.open(BytesIO(raw)) as im:
        im.thumbnail((360, 240), Image.Resampling.LANCZOS)
        if im.mode not in {"RGB", "RGBA"}:
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        stream = BytesIO()
        im.save(stream, format="PNG", optimize=True)
        blob = stream.getvalue()
        if len(blob) > MAX_THUMBNAIL_BYTES:
            # Bound transport and browser memory for very detailed images.
            im.thumbnail((220, 150), Image.Resampling.LANCZOS)
            stream = BytesIO()
            im.save(stream, format="PNG", optimize=True)
            blob = stream.getvalue()
    return blob


def _rounded_image(raw: bytes, radius_pct: float) -> BytesIO:
    try:
        with Image.open(BytesIO(raw)) as source:
            source.load()
            rgb = source.convert("RGBA")
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise IllustrationLayoutError("Не удалось открыть PNG/JPEG для скругления") from exc
    width, height = rgb.size
    if width * height > MAX_IMAGE_PIXELS:
        raise IllustrationLayoutError("Изображение превышает лимит для скругления")
    # Supersampled mask where affordable; preserve native pixels otherwise.
    scale = 2 if width * height <= 1_500_000 else 1
    bounds = (width * scale, height * scale)
    safe_pct = actual_corner_percent(width, height, radius_pct)
    radius = round(min(width, height) * safe_pct / 100 * scale)
    mask = Image.new("L", bounds, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, bounds[0] - 1, bounds[1] - 1),
        radius=radius, fill=255
    )
    if scale > 1:
        mask = mask.resize((width, height), Image.Resampling.LANCZOS)
    rgb.putalpha(ImageChops.multiply(rgb.getchannel("A"), mask))
    output = BytesIO()
    rgb.save(output, "PNG", optimize=True)
    output.seek(0)
    return output


def _check_changes(changes):
    if not isinstance(changes, dict) or len(changes) > MAX_IMAGES:
        raise IllustrationLayoutError("Неверные параметры оформления изображений")
    for raw_index, setting in changes.items():
        if not isinstance(raw_index, str) or not raw_index.isdecimal():
            raise IllustrationLayoutError("Неверный номер изображения")
        if not isinstance(setting, dict) or not set(setting).issubset(
            {"width_percent", "align", "corner_percent"}
        ):
            raise IllustrationLayoutError("Неверные параметры изображения")
        percent = setting.get("width_percent", 70)
        if type(percent) is not int or not 25 <= percent <= 100:
            raise IllustrationLayoutError("Размер рисунка должен быть 25–100% текстовой области")
        if setting.get("align", "center") not in ALIGNS:
            raise IllustrationLayoutError("Неизвестное выравнивание рисунка")
        corner = setting.get("corner_percent", 0)
        if (type(corner) not in (int, float) or not 0 <= corner <= MAX_RADIUS_PERCENT
                or corner * 2 != int(corner * 2)):
            raise IllustrationLayoutError("Скругление должно быть от 0 до 24% с шагом 0,5%")


def apply_image_layout(doc, changes: dict):
    """Apply only explicitly selected changes to a newly loaded DOCX."""
    if not changes:
        return
    _check_changes(changes)
    if len(doc.inline_shapes) > MAX_IMAGES:
        raise IllustrationLayoutError("Для оформления доступно не более 100 иллюстраций за один проход")

    # Validate ALL targeted objects before changing any in-memory output object.
    selected = []
    for key, setting in changes.items():
        index = int(key)
        if index >= len(doc.inline_shapes):
            raise IllustrationLayoutError("Иллюстрация №" + key + " отсутствует в документе")
        shape = doc.inline_shapes[index]
        record, original = _picture_entry(doc, index, shape)
        if not record["editable"] or original is None:
            raise IllustrationLayoutError("Иллюстрация №" + key + ": " + record["reason"])
        paragraph = _ancestor(shape._inline, "w:p")
        selected.append((shape, Paragraph(paragraph, doc), original, setting))

    for shape, paragraph, original, setting in selected:
        # Width uses the current document's text area AFTER the selected preset.
        # Simple inline pictures are reflowed with text, not floating/wrapped.
        section = doc.sections[0]
        usable = int(section.page_width - section.left_margin - section.right_margin)
        if usable <= 0:
            raise IllustrationLayoutError("Не удалось определить текстовую область листа")
        width = int(usable * setting.get("width_percent", 70) / 100)
        ratio = shape.height / shape.width if shape.width else 1
        height = int(width * ratio)
        max_height = int(section.page_height - section.top_margin -
                         section.bottom_margin - 1_000_000)
        if height > max_height > 0:
            height = max_height
            width = max(1, int(height / ratio))
        if height <= 0 or width <= 0:
            raise IllustrationLayoutError("Невозможный размер иллюстрации")
        shape.width = width
        shape.height = height
        paragraph.alignment = ALIGNS[setting.get("align", "center")]
        paragraph.paragraph_format.keep_together = True
        # Keep the picture beside an immediately following caption.
        following = paragraph._p.getnext()
        if following is not None and following.tag == qn("w:p"):
            style_el = following.find(qn("w:pPr"))
            style_el = style_el.find(qn("w:pStyle")) if style_el is not None else None
            if style_el is not None and (style_el.get(qn("w:val")) or "").lower() == "caption":
                paragraph.paragraph_format.keep_with_next = True

        radius = setting.get("corner_percent", 0)
        if radius:
            # Register a distinct PNG and rebind only the current picture's
            # blip. Shared image parts elsewhere remain untouched.
            new_rid, _ = doc.part.get_or_add_image(_rounded_image(original, radius))
            shape._inline.xpath(".//a:blip")[0].set(qn("r:embed"), new_rid)
