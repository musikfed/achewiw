"""Deterministic manuscript evidence and safe acceptance of AI block revisions.

No invented truth: this module validates only facts explicitly described by the
author's block metadata. It reports suspected contradictions, not their proof.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any


AI_CLICHES = (
    "в этот момент", "казалось, что", "словно по волшебству",
    "не мог поверить своим глазам", "сердце пропустило удар",
    "по спине пробежал холодок", "внезапно", "неожиданно",
    "всё изменилось", "нечто большее", "как никогда прежде",
    "мир вокруг замер", "на секунду повисла тишина",
)


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def words(text: str) -> int:
    return len(re.findall(r"[\w]+(?:[-’'][\w]+)*", text, re.UNICODE))


def normalize(item: str) -> str:
    return re.sub(r"\s+", " ", item.strip().casefold())


def editorial_lint(text: str) -> dict[str, Any]:
    """Counts only literal repetitions/cliches; no claim of AI authorship."""
    normalized = normalize(text)
    cliches = []
    for phrase in AI_CLICHES:
        hits = len(re.findall(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", normalized))
        if hits:
            cliches.append({"phrase": phrase, "occurrences": hits})
    paras = [s.strip() for s in re.split(r"\n\s*\n", normalized) if s.strip()]
    identical = [{"paragraph": p[:100], "occurrences": n} for p, n in
                 Counter(paras).items() if n > 1]
    return {"words": words(text), "cliches": cliches,
            "repeated_paragraphs": identical,
            "disclaimer": "Это стилистические сигналы, не детектор ИИ и не доказательство ошибки."}


def preflight(project: Any, scene_id: str | None = None) -> dict[str, Any]:
    """Explicit block ledger: prerequisite/setup and cause cannot be silently guessed.

    Known facts are author-supplied introductions only, in declared scene order.
    'requires' never implies existence. Earlier blocks without accepted text do
    not introduce facts into the accepted manuscript.
    """
    issues = []
    known: dict[str, dict] = {}
    accepted: dict[str, dict] = {}
    all_blocks = {}
    scene_ids = {scene.id for scene in project.scenes}
    for scene in project.scenes:
        for block in getattr(scene, "blocks", []):
            all_blocks[block.id] = block
    for scene_index, scene in enumerate(project.scenes, 1):
        for block_index, block in enumerate(getattr(scene, "blocks", []), 1):
            here = {"scene_id": scene.id, "block_id": block.id,
                    "scene_title": scene.title, "block_number": block_index}
            if block.accepted_job_id:
                for item in block.requires:
                    if normalize(item) not in known:
                        issues.append({**here, "severity": "warning",
                            "code": "unprepared_item", "subject": item,
                            "detail": "Блок использует предмет/факт без явно принятого введения в предыдущих блоках."})
                for cause_id in block.caused_by:
                    if cause_id not in accepted:
                        issues.append({**here, "severity": "warning",
                            "code": "missing_cause", "subject": cause_id,
                            "detail": "Причина не указана среди ранее принятых блоков."})
                for item in block.introduces:
                    key=normalize(item)
                    if key in known:
                        issues.append({**here, "severity": "notice",
                            "code": "reintroduced_item", "subject": item,
                            "detail": "Предмет/факт уже вводился; проверь, оправдано ли повторное представление."})
                    else:
                        known[key] = here
                accepted[block.id] = here
            else:
                for cause_id in block.caused_by:
                    if cause_id not in all_blocks:
                        issues.append({**here, "severity": "warning",
                            "code": "unknown_cause", "subject": cause_id,
                            "detail": "Блок с таким ID не найден в плане."})
            if block.id in block.caused_by:
                issues.append({**here, "severity": "error",
                    "code": "self_cause", "subject": block.id,
                    "detail": "Блок не может быть причиной самого себя."})
    if scene_id is not None:
        if scene_id not in scene_ids:
            raise ValueError("Сцена не найдена")
        issues = [i for i in issues if i["scene_id"] == scene_id]
    return {"status": "partial", "issues": issues,
            "accepted_blocks": len(accepted),
            "registered_introductions": len(known),
            "disclaimer": ("Проверка основана на явно введённых автором фактах и ID блоков. "
                           "Семантические противоречия требуют чтения рукописи и решения автора.")}


def block_prompt(project: Any, scene: Any, block: Any, instructions: str) -> str:
    index = project.scenes.index(scene)
    preceding = project.scenes[max(0, index-2):index]
    previous_context = [{"title": s.title, "ending": s.text[-2200:],
                         "anchor": s.anchor} for s in preceding]
    earlier_blocks = scene.blocks[:scene.blocks.index(block)]
    approved = [{"description": b.description, "introduced": b.introduces,
                 "requires": b.requires, "caused_by": b.caused_by}
                for b in earlier_blocks if b.accepted_job_id]
    import json
    instruction = {
        "title": project.title, "genre": project.genre,
        "premise": project.premise, "atmosphere": project.atmosphere,
        "canon": project.anchors, "scene_title": scene.title,
        "scene_goal": scene.anchor, "scene_known_objects": scene.active_objects,
        "scene_reader_info": scene.reader_info, "scene_text_tail": scene.text[-5000:],
        "previous_scenes": previous_context,
        "previous_accepted_blocks": approved,
        "block": {
            "id": block.id, "description": block.description,
            "target_words": block.target_words,
            "requires": block.requires, "introduces": block.introduces,
            "caused_by": block.caused_by, "forbidden_inventions": block.forbidden_inventions,
            "style": block.style, "author_instruction": instructions,
        }
    }
    system = (
        "Ты пишешь ТОЛЬКО один самостоятельный фрагмент художественного текста на русском. "
        f"Ориентир: {block.target_words} слов, допустимое отклонение ±15%. "
        "Сохраняй согласованность с утверждённой сценой и её предысторией. "
        "Новые предметы, персонажи и способности не возникают без установления причины, "
        "действия или разрешения автора. Не добавляй новые факты, требующие изменения канона. "
        "Используй заданные предпосылки и покажи последствия действий. "
        "Если необходимой предпосылки нет, НЕ выдумывай её: опиши только допустимое развитие. "
        "Не повторяй уже написанные абзацы, не добавляй заголовков, списков, комментариев "
        "редактора, объяснений процесса, служебных пометок и самовосхваления ИИ. "
        "Избегай штампов, одинаковой длины предложений, риторических итогов, "
        "пустых эмоциональных формул и искусственных клиффхэнгеров. "
        "Разрешены естественные паузы, короткие фразы, диалоги и индивидуальный голос автора.\n"
    )
    return (system + json.dumps(instruction, ensure_ascii=False))[:34000]


def review_prompt(project: Any, instructions: str, *,
                  scene_id: str | None = None) -> str:
    """Focus semantic review on one whole scene when possible, not arbitrary
    first 1000 characters from all scenes. Explicitly warn about truncation.
    """
    import json
    selected = next((i for i, s in enumerate(project.scenes) if s.id == scene_id), None)
    if scene_id is not None and selected is None:
        raise ValueError("Сцена для проверки не найдена")
    if selected is None:
        included = [(i, s, "outline") for i, s in enumerate(project.scenes)]
    else:
        included = [
            (i, project.scenes[i], "context" if i != selected else "focus")
            for i in range(max(0, selected-2), min(len(project.scenes), selected+2))
        ]
    sections = []
    for i, scene, role in included:
        raw = scene.text
        if role == "focus":
            limit = 22500
            if len(raw) > limit:
                text = raw[:11000] + "\n[ВНУТРЕННИЙ ФРАГМЕНТ ТЕКСТА ОПУЩЕН — проверка неполная]\n" + raw[-11000:]
            else:
                text = raw
        else:
            limit = 1300 if role == "outline" else 1800
            text = (raw[:limit] if len(raw) <= limit else
                    raw[:limit//2] + "\n[СРЕДИНА ОПУЩЕНА]\n" + raw[-limit//2:])
        sections.append({
            "scene_id": scene.id, "title": scene.title, "order": i+1,
            "role": role, "anchor": scene.anchor, "active_objects": scene.active_objects,
            "accepted_blocks": [
                {"id": b.id, "requires": b.requires,
                 "introduces": b.introduces, "caused_by": b.caused_by}
                for b in scene.blocks if b.accepted_job_id
            ],
            "text": text, "truncated": len(raw) > limit
        })
    context = json.dumps({"canon": project.anchors, "premise": project.premise,
                          "scenes": sections}, ensure_ascii=False)
    if len(context) > 28000:
        context = context[:28000] + "\n[КОНТЕКСТ ОБРЕЗАН — нельзя делать общие выводы]"
    rule = (
        "Ты литературный редактор причинно-следственной целостности. "
        "Проверяй ТОЛЬКО доступный текст. Отделяй гипотезы от доказанных расхождений. "
        "Ищи внезапное появление предметов, людей, способностей, неожиданные знания, "
        "нарушения порядка событий, не мотивированные действия, исчезающие предметы, "
        "ссылки на несостоявшиеся события, противоречия местоположениям, дубли событий. "
        "Проверяй также повторяющиеся клише, искусственную назидательность, "
        "механический ритм и речевую шаблонность, но не утверждай, что это ИИ-текст. "
        "Для каждой гипотезы напиши ID сцены, точную краткую цитату, "
        "что противоречит чему, уверенность 'подтверждено/предположение/неясно' "
        "и минимальное предлагаемое исправление. Не обнаруженное не выдумывай. "
        "Никогда не подменяй отчёт новой прозой и не изменяй авторский текст. "
        "Если часть сцены отсутствует или помечена как усечённая, "
        "не выдавай анализ за полную проверку. "
    )
    return (rule + "Пожелания автора: " + instructions[:2000] +
            "\nКонтекст:\n" + context)[:35000]
