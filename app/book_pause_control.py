"""Controlled author pause of an existing book job (SQLite only).

A pause REQUEST never interrupts Ollama mid-response or alters manuscript
prose. The generator acknowledges it at the next safe boundary and moves the
job to its existing resumable 'interrupted' state. Database transactions are
managed by the caller; the helper does not commit implicitly.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone


class PauseConflict(ValueError):
    pass


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_pause_controls(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS ai_book_pause_requests (
        job_id TEXT PRIMARY KEY, requested_at TEXT NOT NULL
    )""")


def pause_requested(db: sqlite3.Connection, job_id: str) -> bool:
    return db.execute(
        "SELECT 1 FROM ai_book_pause_requests WHERE job_id=?", (job_id,)
    ).fetchone() is not None


def request_pause(db: sqlite3.Connection, job_id: str) -> dict:
    row=db.execute("SELECT mode,status FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
    if not row or row["mode"]!="book":
        raise PauseConflict("Задание книги не найдено")
    if row["status"] not in ("queued","running"):
        raise PauseConflict("Остановку можно запросить только для выполняемой книги")
    db.execute("INSERT OR IGNORE INTO ai_book_pause_requests(job_id,requested_at) VALUES(?,?)",
               (job_id,stamp()))
    return {"job_id":job_id,"pause_requested":True,
            "status":"pause_requested",
            "note":"Завершим текущий ответ, сохраним принятый текст, затем остановимся"}


def acknowledge_pause(db: sqlite3.Connection, job_id: str) -> bool:
    """Called only by the owning book generator at a safe chapter boundary.

    Returns True if it has safely stopped the job. Does not touch existing
    ai_book_chapters, project text, fingerprints, plans, or stream buffers.
    """
    if not pause_requested(db,job_id):
        return False
    row=db.execute("SELECT status FROM ai_jobs WHERE id=?",(job_id,)).fetchone()
    if not row or row["status"] not in ("queued","running"):
        return False
    now=stamp()
    db.execute("""UPDATE ai_jobs SET status='interrupted',
        stage='Пауза по запросу автора · сохранённые главы не изменены',
        output='Можно уточнить задачу незавершённой главы и продолжить.',
        error=NULL,updated_at=?,finished_at=? WHERE id=? AND status IN ('queued','running')""",
        (now,now,job_id))
    changed=db.execute("SELECT changes()").fetchone()[0]
    if changed!=1:
        return False
    db.execute("DELETE FROM ai_book_pause_requests WHERE job_id=?",(job_id,))
    return True


def clear_pause(db: sqlite3.Connection, job_id: str) -> None:
    """Explicit resume clears any stale request within its transaction."""
    db.execute("DELETE FROM ai_book_pause_requests WHERE job_id=?",(job_id,))
