"""Safe, deterministic preflight for ACHEWIW's two chat workspaces.

This module only classifies a request and prepares a reviewable plan. It does
not invoke Ollama, modify a manuscript, publish files or launch a generation job.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Literal

Mode = Literal["clarify", "autonomous"]
Workspace = Literal["generation", "editorial"]

@dataclass(frozen=True)
class PlannedAction:
    kind: str
    target: str
    instruction: str
    requires_approval: bool = True

def plan_command(message: str, *, workspace: Workspace, mode: Mode = "clarify",
                 project_id: str | None = None, scope: str | None = None) -> dict:
    """Prepare proposed actions, never apply them.

    Exact keywords are only routing hints, not LLM interpretation. A real
    executor must validate permissions, versions and user approval separately.
    """
    if workspace not in ("generation", "editorial"):
        raise ValueError("Unsupported workspace")
    if mode not in ("clarify", "autonomous"):
        raise ValueError("Unsupported assistant mode")
    if not isinstance(message, str) or not message.strip() or len(message) > 10000:
        raise ValueError("Message must contain 1..10000 characters")
    if scope not in (None, "selection", "chapter", "book", "document"):
        raise ValueError("Unsupported scope")
    cleaned = message.strip()
    lower = cleaned.casefold()
    actions: list[PlannedAction] = []

    def add(kind: str, target: str):
        if not any(item.kind == kind for item in actions):
            actions.append(PlannedAction(kind, target, cleaned))

    if workspace == "generation":
        add("generation_brief", "01 · Задание")
        if re.search(r"план|структур|глав|каркас|outline", lower):
            add("outline_proposal", "02 · План")
        if re.search(r"напиши|сгенерир|продолж|текст|write|generate", lower):
            add("draft_proposal", "03 · Редактор")
    else:
        add("editorial_proposal", "03 · Редактор / DOCWORK")
    if re.search(r"стил|повтор|формулиров|обороты|rewrite|style", lower) and workspace == "generation":
        add("style_guidance", "01 · Задание")
    if re.search(r"абзац|выравн|шрифт|отступ|пол[яе]|типограф|оформ|layout|font", lower):
        add("typesetting_proposal", "06 · Типография / DOCWORK")
    if re.search(r"рисунк|иллюстрац|картин|изображен|скругл|image|illustration", lower):
        add("illustration_proposal", "04 · Иллюстрации / DOCWORK")
    if re.search(r"библиограф|источник|ссылк|doi|citation|reference", lower):
        add("source_review", "05 · Проверка / Источники")
    if re.search(r"экспорт|epub|pdf|docx|публик|export|publish", lower):
        add("export_proposal", "06 · Типография / Экспорт")

    needs_target = workspace == "editorial" and not scope
    question = None
    if needs_target and mode == "clarify":
        question = "Что редактируем: выделенный фрагмент, главу, книгу или внешний документ?"
    warnings = []
    if needs_target and mode == "autonomous":
        warnings.append("Область редактирования не определена; никакие правки не применяются.")
    if not project_id:
        warnings.append("Проект не выбран; план не связан с рукописью.")
    return {
        "workspace": workspace, "mode": mode, "project_id": project_id,
        "scope": scope, "message": cleaned,
        "actions": [asdict(action) for action in actions],
        "question": question, "warnings": warnings,
        "status": "proposal_only", "applied": False,
    }
