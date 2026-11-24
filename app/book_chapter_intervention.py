"""A safe author intervention into ONE paused, unfinished book chapter.

Unlike editing a project outline, this changes the instructions used by the
existing job when the author explicitly resumes it. No prose, word count,
original fingerprint, or approval state is touched.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

REVEAL_LEVELS = {"none", "hint", "partial", "full", "aftermath"}


class InterventionConflict(ValueError):
    pass


def apply_paused_guidance(connection: sqlite3.Connection, *, job_id: str,
                          scene_id: str, expected_goal: str,
                          expected_tension: int, expected_reveal: str,
                          goal: str, tension: int, reveal: str) -> dict:
    if not isinstance(goal,str) or not 1 <= len(goal.strip()) <= 500:
        raise ValueError("Задача главы должна содержать 1–500 символов")
    if type(tension) is not int or not 0 <= tension <= 5:
        raise ValueError("Напряжение главы: 0–5")
    if reveal not in REVEAL_LEVELS:
        raise ValueError("Неизвестный режим раскрытия")
    status=connection.execute(
        "SELECT mode,status FROM ai_jobs WHERE id=?",(job_id,)
    ).fetchone()
    if not status or status["mode"]!="book":
        raise InterventionConflict("Задание книги не найдено")
    if status["status"] not in ("interrupted","error"):
        raise InterventionConflict("Уточнение доступно только после остановки генерации")
    chapter=connection.execute(
        "SELECT editorial_goal,tension,reveal_level,complete,accepted_at "
        "FROM ai_book_chapters WHERE job_id=? AND scene_id=?",
        (job_id,scene_id)
    ).fetchone()
    if chapter is None:
        raise InterventionConflict("Глава не принадлежит заданию")
    if chapter["complete"] or chapter["accepted_at"] is not None:
        raise InterventionConflict("Глава уже завершена или принята")
    if (chapter["editorial_goal"] != expected_goal or
        chapter["tension"] != expected_tension or
        chapter["reveal_level"] != expected_reveal):
        raise InterventionConflict("Задача главы изменилась в другой вкладке")
    connection.execute(
        "UPDATE ai_book_chapters SET editorial_goal=?,tension=?,"
        "reveal_level=?,updated_at=? WHERE job_id=? AND scene_id=? "
        "AND complete=0 AND accepted_at IS NULL",
        (goal.strip(),tension,reveal,
         datetime.now(timezone.utc).isoformat(timespec="seconds"),job_id,scene_id)
    )
    if connection.execute("SELECT changes()").fetchone()[0]!=1:
        raise InterventionConflict("Не удалось обновить незавершённую главу")
    return {
        "status":"saved_for_next_resume",
        "job_id":job_id,"scene_id":scene_id,
        "editorial_goal":goal.strip(),"tension":tension,"reveal_level":reveal,
        "generated_prose_changed":False
    }
