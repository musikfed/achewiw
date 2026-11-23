"""R3.2: move a standalone DOCX picture and its caption between text paragraphs.

Move operations are constrained to top-level Word body paragraphs and validated
against the ORIGINAL document structure. We do not edit the original bytes,
flatten mixed inline elements, or move objects out of tables/section boundaries.
An image's linked caption is transferred atomically alongside the picture.
"""
from __future__ import annotations

from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from docwork_image_layout import (
    MAX_IMAGES, _ancestor, _picture_entry, IllustrationLayoutError
)


def _caption_index(paragraphs, source_el):
    following = source_el.getnext()
    if following is None or following.tag != qn("w:p"):
        return None
    for i, p in enumerate(paragraphs):
        if p._p == following:
            text = p.text.strip().lower()
            if (p.style is not None and p.style.name.lower() in ("caption", "подпись")) or (
                text.startswith(("рисунок ", "рис. ", "figure ", "fig. "))
            ):
                return i
            break
    return None


def paragraph_section_indices(paragraphs) -> dict[int, int]:
    """Map original top-level Word paragraph indices to their section.

    In OOXML a section break is carried by w:pPr/w:sectPr of the *last*
    paragraph in the preceding section; do not relocate that paragraph.
    """
    mapping = {}
    section = 0
    for index, paragraph in enumerate(paragraphs):
        mapping[index] = section
        if paragraph._p.xpath("./w:pPr/w:sectPr"):
            section += 1
    return mapping


def apply_image_moves(doc, moves):
    """Reposition editable picture+caption pairs before numbered text paragraphs.

    The target index refers to the PRE-EDIT source document top-level paragraph
    index (0..count), not a mutable live Word index. count = end-of-document.
    Multiple moves to the same anchor use an explicit increasing order value.
    """
    if moves is None:
        return
    if not isinstance(moves, dict) or len(moves) > MAX_IMAGES:
        raise IllustrationLayoutError("Некорректная карта перемещений иллюстраций")
    if not moves:
        return
    paragraphs = list(doc.paragraphs)
    section_of = paragraph_section_indices(paragraphs)
    body = doc.element.body
    count = len(paragraphs)
    shapes = list(doc.inline_shapes)
    selected = []
    protected_caption_indices = set()
    image_paragraph_indices = set()

    for i, shape in enumerate(shapes):
        el = _ancestor(shape._inline, "w:p")
        for j, paragraph in enumerate(paragraphs):
            if paragraph._p == el:
                image_paragraph_indices.add(j)
                caption = _caption_index(paragraphs, el)
                if caption is not None:
                    protected_caption_indices.add(caption)
                break

    for raw_index, setting in moves.items():
        if not isinstance(raw_index, str) or not raw_index.isdecimal():
            raise IllustrationLayoutError("Неверный индекс перемещаемой картинки")
        index = int(raw_index)
        if index >= len(shapes):
            raise IllustrationLayoutError("Иллюстрация №" + raw_index + " отсутствует")
        if not isinstance(setting, dict) or set(setting) != {"before_paragraph", "order"}:
            raise IllustrationLayoutError("Для рисунка нужна точная позиция и порядок")
        dest = setting["before_paragraph"]
        order = setting["order"]
        if type(dest) is not int or not 0 <= dest <= count:
            raise IllustrationLayoutError("Позиция рисунка вне документа")
        if type(order) is not int or not 0 <= order <= 1000000:
            raise IllustrationLayoutError("Некорректный порядок рисунка")
        shape = shapes[index]
        record, source = _picture_entry(doc, index, shape)
        if not record.get("editable") or source is None:
            raise IllustrationLayoutError(
                "Рисунок №" + raw_index + " нельзя двигать: " + record.get("reason", "")
            )
        source_el = _ancestor(shape._inline, "w:p")
        if source_el is None or source_el.getparent() != body:
            raise IllustrationLayoutError("Перемещение рисунка в таблице запрещено")
        caption_idx = _caption_index(paragraphs, source_el)
        linked = paragraphs[caption_idx]._p if caption_idx is not None else None
        source_para_index=next(
            (i for i, p in enumerate(paragraphs) if p._p == source_el), None
        )
        if source_para_index is None:
            raise IllustrationLayoutError("Исходный абзац рисунка не найден")
        if source_el.xpath("./w:pPr/w:sectPr"):
            raise IllustrationLayoutError("Рисунок содержит границу раздела; перенос запрещён")
        section=section_of[source_para_index]
        if caption_idx is not None:
            if section_of[caption_idx]!=section or linked.xpath("./w:pPr/w:sectPr"):
                raise IllustrationLayoutError("Подпись входит в границу другого раздела")
        target_section=section_of[dest] if dest<count else len(doc.sections)-1
        if target_section!=section:
            raise IllustrationLayoutError(
                "Перенос разрешён только внутри одного раздела Word. "
                "Для другого раздела сначала проверь разметку и колонтитулы."
            )
        if dest < count:
            target = paragraphs[dest]
            if target._p.getparent() != body or not target.text.strip():
                raise IllustrationLayoutError("Размещать рисунок можно перед текстовым абзацем")
            if dest in image_paragraph_indices or dest in protected_caption_indices:
                raise IllustrationLayoutError("Нельзя вставлять рисунок между картинкой и её подписью")
            if target._p == source_el or (linked is not None and target._p == linked):
                raise IllustrationLayoutError("Нельзя переносить рисунок внутрь самого себя")
        selected.append((dest, order, index, source_el, linked))

    # All operations validated BEFORE moving any element. Stable user-order
    # preserves relative order when multiple pictures target the same location.
    selected.sort(key=lambda x: (x[0], x[1], x[2]))
    for dest, _, _, source_el, caption_el in selected:
        if dest == count:
            # Body sectPr must remain the final OOXML child.
            sect_pr = body.find(qn("w:sectPr"))
            if sect_pr is None:
                body.append(source_el)
                if caption_el is not None:
                    body.append(caption_el)
            else:
                sect_pr.addprevious(source_el)
                if caption_el is not None:
                    sect_pr.addprevious(caption_el)
        else:
            target_el = paragraphs[dest]._p
            target_el.addprevious(source_el)
            if caption_el is not None:
                target_el.addprevious(caption_el)
        picture_para = Paragraph(source_el, doc)
        picture_para.paragraph_format.keep_together = True
        if caption_el is not None:
            picture_para.paragraph_format.keep_with_next = True
