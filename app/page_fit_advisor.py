"""Non-destructive print page-gap triage for editorial/typography workflow.

Only proposes an action; does not change DOCX/PDF/EPUB or generate filler.
A document layout engine must supply measured page occupancy for reliable use.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class PageEvidence:
    page: int
    occupied_ratio: float
    next_page_starts_part: bool = False
    explicit_page_break: bool = False
    section_break: bool = False
    paragraph_has_manual_line_breaks: bool = False
    justified_large_word_spaces: bool = False
    measured: bool = True

def recommend(evidence: PageEvidence, *, words_per_page: int = 275) -> dict:
    """A proposal cannot infer real pagination from raw text alone."""
    if evidence.page < 1 or not 0<=evidence.occupied_ratio<=1:
        raise ValueError("Invalid page layout evidence")
    if not evidence.measured:
        return {"action":"measure_first","max_added_words":0,
                "reason":"Нужны координаты строк и разрывов из PDF/Word"}
    if evidence.justified_large_word_spaces or evidence.paragraph_has_manual_line_breaks:
        return {"action":"fix_layout","max_added_words":0,
                "reason":"Проверить ручные переносы и распределённое выравнивание; не дописывать текст"}
    if evidence.next_page_starts_part or evidence.section_break or evidence.explicit_page_break:
        return {"action":"preserve_break","max_added_words":0,
                "reason":"Граница части/раздела намеренная: пустота не является дефектом"}
    if evidence.occupied_ratio>=0.70:
        return {"action":"leave","max_added_words":0,
                "reason":"Недостаточного заполнения страницы не обнаружено"}
    missing=max(0,round((0.85-evidence.occupied_ratio)*words_per_page))
    return {"action":"offer_semantic_expansion",
            "max_added_words":min(180,missing),
            "reason":"Только связанный с главой полезный абзац после проверки фактов и редакторского согласования",
            "requires_approval":True,"must_repaginate":True,
            "forbid":["повторение предыдущего текста","несуществующие ссылки",
                      "текст ради количества символов","разрыв логики главы"]}
