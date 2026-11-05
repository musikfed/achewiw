"""Editorial chapter-length and revelation planning for an approved StoryEngine outline.

The plan is a separate production layer. The author's outline and canon are never
rewritten here. No LLM, GPU or network dependency.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from book_generation_core import MAX_CHAPTER_WORDS, target_words as parse_target

WORDS_PER_PAGE = 275
REVEALS = ("none", "hint", "partial", "full", "aftermath")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stage(title: str, index: int, count: int) -> str:
    value = title.casefold()
    if re.search(r"развяз|финал|заверш|эпилог|послеслов", value):
        return "resolution"
    if re.search(r"кульминац|схватк|решени|битва|пик", value):
        return "climax"
    if re.search(r"раскры|разоблач|открыти|разгад", value):
        return "reveal"
    if re.search(r"погон|поиск|расслед|конфликт|преслед|ловуш|поворот", value):
        return "conflict"
    if index == 0:
        return "opening"
    if index == count - 1:
        return "resolution"
    if index >= count - 2:
        return "climax"
    return "development"


STAGE_WEIGHT = {
    "opening": 0.90, "development": 1.02, "conflict": 1.18,
    "reveal": 1.03, "climax": 1.22, "resolution": 0.88,
}
STAGE_TENSION = {
    "opening": 2, "development": 3, "conflict": 4, "reveal": 4,
    "climax": 5, "resolution": 2,
}
STAGE_GOAL = {
    "opening": "Показать мир и героя действием, поставить первый честный вопрос читателя",
    "development": "Дать новые действия и причинные следствия без повторения известного",
    "conflict": "Развить столкновение, преследование или расследование с ощутимым изменением",
    "reveal": "Изменить понимание известного через подготовленные подсказки, без реткона",
    "climax": "Довести причинный конфликт до выбора или необратимого последствия",
    "resolution": "Показать последствия решений, завершить обещанные линии без поспешного пересказа",
}


def _first_reveal_index(raw: str, count: int) -> int:
    """Honor a hint such as 'с третьей главы' when it is explicitly present."""
    value = (raw or "").casefold()
    match = re.search(r"(?:с|со|начиная\s+с|в)\s*(\d+)\s*[-–]?(?:й|ой|ей)?\s*глав", value)
    if match:
        return min(count - 1, max(0, int(match.group(1)) - 1))
    words = {"перв": 0, "втор": 1, "трет": 2, "четв": 3,
             "пят": 4, "шест": 5, "седьм": 6, "восьм": 7}
    for word, index in words.items():
        if re.search(r"(?:с|со|в|начиная\s+с)\s+" + word + r"\w*\s+глав", value):
            return min(count - 1, index)
    return max(1, count // 2)


def _allocate_weighted(total: int, weights: list[float]) -> list[int]:
    if not weights:
        raise ValueError("Нет глав в утверждённом каркасе")
    if total < 150 * len(weights) or total > MAX_CHAPTER_WORDS * len(weights):
        raise ValueError("Укажи другой общий объём или измени количество глав")
    remaining = total - 150 * len(weights)
    # Give every chapter a minimum prose length, then distribute by role.
    weighted = [remaining * w / sum(weights) for w in weights]
    shares = [min(MAX_CHAPTER_WORDS - 150, int(v)) for v in weighted]
    left = remaining - sum(shares)
    while left:
        candidates = [i for i in range(len(shares)) if shares[i] < MAX_CHAPTER_WORDS - 150]
        if not candidates:
            raise ValueError("Объём превышает возможности утверждённого каркаса")
        # Largest remainder, deterministic tie-break; can accommodate saturation.
        i = max(candidates, key=lambda j: (weighted[j] - shares[j], -j))
        shares[i] += 1
        left -= 1
    return [150 + n for n in shares]


def suggested_plan(project) -> dict:
    total = parse_target(project.target_length,
                         speech_minutes=project.duration_minutes if project.work_type == "speech" else None)
    scenes = list(project.scenes)
    if not scenes:
        raise ValueError("Сначала утверди каркас глав")
    roles = [_stage(scene.title, i, len(scenes)) for i, scene in enumerate(scenes)]
    targets = _allocate_weighted(total, [STAGE_WEIGHT[r] for r in roles])
    first_reveal = _first_reveal_index(project.reveal, len(scenes))
    has_twist = bool((project.twist or "").strip())
    chapters = []
    for index, (scene, role, count) in enumerate(zip(scenes, roles, targets)):
        if not has_twist:
            reveal = "none"
        elif index < first_reveal:
            reveal = "hint" if index else "none"
        elif index == len(scenes) - 1 and role == "resolution":
            reveal = "aftermath"
        elif role in ("climax", "reveal") and index >= len(scenes) - 2:
            reveal = "full"
        else:
            reveal = "partial"
        chapters.append({
            "scene_id": scene.id, "title": scene.title,
            "stage": role, "target_words": count, "pages_estimate": round(count / WORDS_PER_PAGE, 1),
            "tension": STAGE_TENSION[role], "reveal_level": reveal,
            "editorial_goal": STAGE_GOAL[role],
        })
    return {"target_words": total, "words_per_page": WORDS_PER_PAGE,
            "pages_estimate": round(total / WORDS_PER_PAGE, 1),
            "chapters": chapters, "source": "suggested"}


def validate_plan(project, rows: list[dict]) -> dict:
    base = suggested_plan(project)
    expected = base["chapters"]
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("План не соответствует числу глав каркаса")
    result = []
    for given, suggested in zip(rows, expected):
        if not isinstance(given, dict) or given.get("scene_id") != suggested["scene_id"]:
            raise ValueError("Порядок или ID глав изменён: не трогаем утверждённый каркас")
        count = given.get("target_words")
        if type(count) is not int or count < 150 or count > MAX_CHAPTER_WORDS:
            raise ValueError("Объём каждой главы должен быть 150–12000 слов")
        tension = given.get("tension", suggested["tension"])
        if type(tension) is not int or not 0 <= tension <= 5:
            raise ValueError("Напряжение главы: от 0 до 5")
        reveal = given.get("reveal_level", suggested["reveal_level"])
        if reveal not in REVEALS:
            raise ValueError("Неизвестный режим раскрытия")
        goal = str(given.get("editorial_goal", suggested["editorial_goal"])).strip()
        if not goal or len(goal) > 500:
            raise ValueError("Редакторская задача главы: 1–500 символов")
        result.append({**suggested, "target_words": count, "pages_estimate": round(count / WORDS_PER_PAGE, 1),
                       "tension": tension, "reveal_level": reveal, "editorial_goal": goal})
    if sum(item["target_words"] for item in result) != base["target_words"]:
        raise ValueError(f"Сумма объёмов глав должна равняться {base['target_words']} словам")
    return {**base, "chapters": result, "source": "author"}


def init_plan_tables(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_book_plans (
            project_id TEXT PRIMARY KEY, target_words INTEGER NOT NULL,
            scene_ids TEXT NOT NULL, plan_json TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)


def get_plan(db: sqlite3.Connection, project) -> dict:
    default = suggested_plan(project)
    row = db.execute(
        "SELECT plan_json,target_words,scene_ids FROM ai_book_plans WHERE project_id=?",
        (project.id,)
    ).fetchone()
    if not row:
        return default
    ids = [s.id for s in project.scenes]
    if row["target_words"] != default["target_words"] or json.loads(row["scene_ids"]) != ids:
        return {**default, "stale_saved_plan": True}
    try:
        result = validate_plan(project, json.loads(row["plan_json"]))
    except (ValueError, TypeError, json.JSONDecodeError):
        return {**default, "stale_saved_plan": True}
    return result


def store_plan(db: sqlite3.Connection, project, chapters: list[dict]) -> dict:
    plan = validate_plan(project, chapters)
    db.execute("""
        INSERT INTO ai_book_plans(project_id,target_words,scene_ids,plan_json,updated_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(project_id) DO UPDATE SET
          target_words=excluded.target_words, scene_ids=excluded.scene_ids,
          plan_json=excluded.plan_json,updated_at=excluded.updated_at
    """, (project.id, plan["target_words"],
          json.dumps([s.id for s in project.scenes]),
          json.dumps(plan["chapters"], ensure_ascii=False), now()))
    return plan


def prose_phase(written: int, target: int) -> str:
    fraction = written / max(1, target)
    if fraction < .18:
        return "Вход в действие: цель героя, место и ближайшая причинная задача"
    if fraction < .45:
        return "Развитие: новые действия, сопротивление, цена поступка"
    if fraction < .72:
        return "Углубление: осложнение, действующие предметы и проверка гипотезы"
    if fraction < .9:
        return "Локальный поворот: последствия, изменение знания или отношений"
    return "Выход из главы: сцена завершается осмысленно, без ложного финала книги"
