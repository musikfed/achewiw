"""Long-form book generation: volume planning and durable, unapproved chapter drafts.

Every chapter is a separate SQLite checkpoint. Never write these drafts into an
author's manuscript without a separate explicit acceptance transaction.
"""
from __future__ import annotations

import math
import re
import sqlite3
from datetime import datetime, timezone

WORDS_PER_MINUTE = 150
MIN_BOOK_WORDS = 500
MAX_BOOK_WORDS = 60000
CHUNK_WORDS = 420  # Smaller local-model continuations reduce looping and preserve scene control
MAX_CHAPTER_WORDS = 12000


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def count_words(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:[-’'][^\W_]+)*", text, flags=re.UNICODE))


def target_words(raw: str, *, speech_minutes: int | None = None) -> int:
    """Explicit words beat minutes. Bare numerals mean words, not tokens.

    Examples: 20000, '20 000 слов', '150 минут', '150 min',
    '150 минут / 20000 слов'. Missing and ambiguous units fail closed.
    """
    value = (raw or "").strip().lower().replace("\u00a0", " ")
    word_match = re.search(r"(?<!\d)(\d[\d\s]*\d|\d+)\s*(?:слов(?:а|о)?|words?)(?!\w)", value)
    minute_match = re.search(r"(?<!\d)(\d[\d\s]*\d|\d+)\s*(?:мин(?:ут(?:а|ы)?)?\.?|minutes?|mins?)(?!\w)", value)
    if word_match:
        count = int(re.sub(r"\s+", "", word_match.group(1)))
    elif minute_match:
        count = int(re.sub(r"\s+", "", minute_match.group(1))) * WORDS_PER_MINUTE
    elif re.fullmatch(r"\d[\d\s]*", value):
        count = int(re.sub(r"\s+", "", value))
    elif not value and speech_minutes is not None:
        count = speech_minutes * WORDS_PER_MINUTE
    else:
        raise ValueError("Укажи объём числом: например, «20000 слов» или «150 минут»")
    if not MIN_BOOK_WORDS <= count <= MAX_BOOK_WORDS:
        raise ValueError(f"Объём книги должен быть от {MIN_BOOK_WORDS} до {MAX_BOOK_WORDS} слов")
    return count


def distribute_words(total: int, scene_ids: list[str]) -> list[int]:
    """Deterministic distribution across approved chapter cards."""
    if not scene_ids:
        raise ValueError("Сначала утверди каркас: в нём нет глав")
    parts = len(scene_ids)
    base, remainder = divmod(total, parts)
    if base + bool(remainder) > MAX_CHAPTER_WORDS:
        raise ValueError("Для такого объёма добавь больше глав в каркас")
    if base < 150:
        raise ValueError("Слишком много глав для заданного объёма: минимум 150 слов на главу")
    return [base + (i < remainder) for i in range(parts)]


def chunk_size(remaining: int) -> int:
    return min(CHUNK_WORDS, max(180, remaining))


def max_attempts(chapter_target: int) -> int:
    # Guard against a model that returns only a sentence for every call.
    # Smaller local models often stop early; allow enough segments to reach
    # the requested volume without ever declaring a short chapter complete.
    return min(160, max(8, math.ceil(chapter_target / 140) + 8))


def init_book_tables(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_book_chapters (
            job_id TEXT NOT NULL, scene_id TEXT NOT NULL,
            scene_index INTEGER NOT NULL, title TEXT NOT NULL,
            target_words INTEGER NOT NULL,
            original_sha256 TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            word_count INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_epoch INTEGER NOT NULL DEFAULT 0,
            complete INTEGER NOT NULL DEFAULT 0,
            accepted_at TEXT,
            tension INTEGER NOT NULL DEFAULT 3,
            reveal_level TEXT NOT NULL DEFAULT 'none',
            editorial_goal TEXT NOT NULL DEFAULT '',
            stage_name TEXT NOT NULL DEFAULT 'development',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (job_id, scene_id)
        )
    """)
    # Additive migration, preserving previously generated books and prose.
    existing = {row[1] for row in db.execute("PRAGMA table_info(ai_book_chapters)")}
    for name, definition in (("tension", "INTEGER NOT NULL DEFAULT 3"),
                             ("reveal_level", "TEXT NOT NULL DEFAULT 'none'"),
                             ("editorial_goal", "TEXT NOT NULL DEFAULT ''"),
                             ("stage_name", "TEXT NOT NULL DEFAULT 'development'"),
                             ("retry_epoch", "INTEGER NOT NULL DEFAULT 0")):
        if name not in existing:
            db.execute(f"ALTER TABLE ai_book_chapters ADD COLUMN {name} {definition}")
    db.execute("CREATE INDEX IF NOT EXISTS ai_book_chapters_job "
               "ON ai_book_chapters(job_id, scene_index)")


def create_book_chapters(db: sqlite3.Connection, job_id: str, chapters: list[dict]) -> None:
    for index, item in enumerate(chapters):
        db.execute("""
            INSERT INTO ai_book_chapters
                (job_id,scene_id,scene_index,title,target_words,original_sha256,
                 tension,reveal_level,editorial_goal,stage_name,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (job_id, item["id"], index, item["title"], item["target_words"],
              item["original_sha256"], item.get("tension", 3),
              item.get("reveal_level", "none"), item.get("editorial_goal", ""),
              item.get("stage", "development"), now()))


def chapters_for_job(db: sqlite3.Connection, job_id: str, *, include_text: bool = True) -> list[dict]:
    columns = "*" if include_text else (
        "job_id,scene_id,scene_index,title,target_words,word_count,"
        "attempts,retry_epoch,complete,accepted_at,tension,reveal_level,editorial_goal,stage_name,updated_at"
    )
    return [dict(row) for row in db.execute(
        f"SELECT {columns} FROM ai_book_chapters WHERE job_id=? ORDER BY scene_index",
        (job_id,)
    ).fetchall()]


def book_summary(db: sqlite3.Connection, job_id: str) -> dict:
    chapters = chapters_for_job(db, job_id, include_text=False)
    total_target = sum(c["target_words"] for c in chapters)
    done = sum(c["word_count"] for c in chapters)
    accepted = sum(c["accepted_at"] is not None for c in chapters)
    return {
        "target_words": total_target, "generated_words": done,
        "minutes_read_aloud": round(done / WORDS_PER_MINUTE, 1),
        "chapters": len(chapters),
        "chapters_complete": sum(bool(c["complete"]) for c in chapters),
        "chapters_accepted": accepted,
        "shortfall": max(0, total_target - done),
        "percent_words": round(done / total_target * 100, 1) if total_target else 0,
    }


def save_chapter(db: sqlite3.Connection, job_id: str, scene_id: str,
                 text: str, attempts: int, *, complete: bool = False) -> None:
    if len(text) > 150000:
        raise ValueError("Глава превысила предел 150000 символов; разбей её на части")
    db.execute("""
        UPDATE ai_book_chapters
        SET text=?, word_count=?, attempts=?, complete=?, updated_at=?
        WHERE job_id=? AND scene_id=? AND accepted_at IS NULL
    """, (text, count_words(text), attempts, int(complete), now(), job_id, scene_id))
    if db.execute("SELECT changes()").fetchone()[0] != 1:
        raise ValueError("Глава не найдена или уже принята автором")


def trim_repeated_tail(existing: str, proposed: str) -> str:
    """Remove a word-for-word echo of the LAST lines, not previous story events.

    Many local models start their completion by copying the final paragraph
    of the prompt. Remove only a matching suffix/prefix of >=12 full words;
    the separate repetition guard rejects echoes of earlier passages.
    Never modify a stored chapter or claim that a semantic paraphrase is new.
    """
    if not existing or not proposed:
        return proposed.strip()
    pattern = r"[^\W_]+(?:[-’'][^\W_]+)*"
    prior = [m.group().casefold() for m in re.finditer(pattern, existing, re.UNICODE)]
    incoming = list(re.finditer(pattern, proposed, re.UNICODE))
    words = [m.group().casefold() for m in incoming]
    maximum = min(150, len(prior), len(incoming))
    for length in range(maximum, 11, -1):
        if prior[-length:] == words[:length]:
            return proposed[incoming[length-1].end():].lstrip(" \t\r\n.,;:—–-")
    return proposed.strip()


def continuation_repair_note(retry: int, existing: str, last_failure: str = "") -> str:
    """Escalating, bounded prompts for a distinct next scene beat.

    Concrete actions and a different narrative entry point are more effective
    than replaying the same instruction with a different random seed.
    """
    if retry == 0:
        return ""
    last_line = re.split(r"(?<=[.!?])\s+", existing.strip())[-1][-260:]
    notes = [
        "",
        "\nРЕДАКТОРСКАЯ ПРАВКА 1: Продолжи строго ПОСЛЕ последнего события. "
        "Первый абзац должен описывать новую реакцию или действие, не пересказывай завязку.",
        "\nРЕДАКТОРСКАЯ ПРАВКА 2: Перейди к короткому предметному разговору "
        "или конкретной проверке уже введённой загадки. Новое действие обязательно "
        "меняет знание, риск или положение персонажа; никаких общих рассуждений.",
        "\nРЕДАКТОРСКАЯ ПРАВКА 3: Начни с нового наблюдаемого последствия "
        "последнего поступка героя. Покажи действие, препятствие и решение, "
        "не повторяя описания места и не делая преждевременной развязки.",
        "\nРЕДАКТОРСКАЯ ПРАВКА 4: Выбери другой ритм: короткие диалоги "
        "с конкретными целями и жестами. Начни с момента ПОСЛЕ имеющегося текста.",
        "\nРЕДАКТОРСКАЯ ПРАВКА 5: Короткий новый микроконфликт внутри "
        "утверждённой сцены. Пиши только события, которых ещё не было, "
        "действие должно иметь явную причину и следствие.",
    ]
    return notes[min(retry, len(notes)-1)] + (
        "\nПоследнее состояние текста (не цитировать): " + last_line
        if retry >= 2 and last_line else ""
    ) + ("\nПричина отклонения предыдущей попытки: " + last_failure[:170]
         if retry >= 2 and last_failure else "")


def check_new_prose(existing: str, proposed: str) -> None:
    """Reject gross repetition before persisting the next generated chapter chunk.

    This is a narrow deterministic guard, NOT an automatic literary or factual
    correctness assessment. The approved manuscript and partial draft survive
    a failed chunk unchanged.
    """
    if count_words(proposed) < 35:
        raise ValueError("Модель вернула слишком короткий отрывок")
    normalize = lambda value: re.sub(r"\s+", " ", value).strip().casefold()
    earlier = normalize(existing)
    current = normalize(proposed)
    if len(earlier) > 160 and len(current) > 160 and current[:160] in earlier:
        raise ValueError("ИИ повторил ранее написанный отрывок: глава сохранена до повтора")
    paragraphs = [normalize(value) for value in re.split(r"\n\s*\n", proposed)]
    seen = set()
    for paragraph in paragraphs:
        if len(paragraph) < 180:
            continue
        if paragraph in seen or (earlier and paragraph in earlier):
            raise ValueError("Обнаружен повтор длинного абзаца; черновик сохранён до повтора")
        seen.add(paragraph)
