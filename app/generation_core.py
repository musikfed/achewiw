"""Persistent ACHEWIW Ollama jobs in the SAME SQLite database as projects.

Only one active task at a time. Browser refresh never controls task lifetime.
On a container restart unfinished tasks are marked interrupted, retaining text.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone

LIMIT = 2400
ACTIVE = ("queued", "running")
MAX_OUTPUT = 150000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_jobs(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS ai_jobs (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, mode TEXT NOT NULL,
            model TEXT NOT NULL, scene_id TEXT,
            status TEXT NOT NULL, stage TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            finished_at TEXT, output TEXT NOT NULL DEFAULT '',
            estimated_tokens INTEGER NOT NULL DEFAULT 0,
            token_budget INTEGER NOT NULL DEFAULT 2400,
            percent INTEGER, eta_seconds INTEGER,
            parse_error TEXT, suggested_scenes TEXT, error TEXT
        )
    """)
    connection.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS one_achewiw_generation
        ON ai_jobs ((1)) WHERE status IN ('queued', 'running')
    """)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ai_jobs_for_project "
        "ON ai_jobs(project_id, created_at DESC)"
    )
    # Separate decision records keep existing ai_jobs and existing books intact.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS ai_job_reviews (
            job_id TEXT PRIMARY KEY, decision TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
        )
    """)


def mark_interrupted(connection: sqlite3.Connection) -> None:
    """Run ONLY at start of a single-process service, not on each request."""
    connection.execute("""
        UPDATE ai_jobs
           SET status='interrupted', stage='Прервано перезапуском сервера',
               error='Соединение с Ollama прервано при перезапуске ACHEWIW. Черновик сохранён.',
               finished_at=?, updated_at=?
         WHERE status IN ('queued','running')
    """, (utc_now(), utc_now()))


def create_job(connection: sqlite3.Connection, job_id: str, project_id: str,
               mode: str, model: str, scene_id: str | None) -> dict:
    stamp = utc_now()
    connection.execute("""
        INSERT INTO ai_jobs (id, project_id, mode, model, scene_id, status,
                             stage, created_at, updated_at, token_budget)
        VALUES (?, ?, ?, ?, ?, 'queued', 'Ожидание Ollama', ?, ?, ?)
    """, (job_id, project_id, mode, model, scene_id, stamp, stamp, LIMIT))
    return get_job(connection, job_id)


def update_job(connection: sqlite3.Connection, job_id: str, *,
               status: str = "running", stage: str = "Генерация",
               output: str = "", percent: int | None = None,
               eta_seconds: int | None = None, error: str | None = None,
               parse_error: str | None = None,
               suggested_scenes: list[dict] | None = None,
               estimated_tokens: int = 0) -> None:
    if len(output) > MAX_OUTPUT:
        raise ValueError("Ollama returned text exceeds the configured limit")
    done = status in ("ready", "error", "interrupted", "cancelled")
    connection.execute("""
        UPDATE ai_jobs
        SET status=?, stage=?, updated_at=?, finished_at=?,
            output=?, percent=?, eta_seconds=?, estimated_tokens=?,
            error=?, parse_error=?, suggested_scenes=?
        WHERE id=?
    """, (status, stage, utc_now(), utc_now() if done else None, output,
          percent, eta_seconds, estimated_tokens, (error or "")[:800] or None,
          (parse_error or "")[:800] or None,
          json.dumps(suggested_scenes, ensure_ascii=False) if suggested_scenes is not None else None,
          job_id))


def get_job(connection: sqlite3.Connection, job_id: str) -> dict | None:
    row = connection.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["suggested_scenes"] = (
        json.loads(data["suggested_scenes"]) if data["suggested_scenes"] else None
    )
    data["result"] = data.pop("output")
    if data["mode"] == "audit":
        review = connection.execute(
            "SELECT decision, notes, updated_at FROM ai_job_reviews WHERE job_id=?",
            (job_id,)
        ).fetchone()
        data["author_decision"] = review["decision"] if review else "unreviewed"
        data["author_notes"] = review["notes"] if review else ""
        data["reviewed_at"] = review["updated_at"] if review else None
    return data


def recent_jobs(connection: sqlite3.Connection, project_id: str,
                mode: str | None = None) -> list[dict]:
    if mode is None:
        ids = connection.execute(
            "SELECT id FROM ai_jobs WHERE project_id=? ORDER BY created_at DESC, rowid DESC LIMIT 12",
            (project_id,),
        ).fetchall()
    else:
        if mode not in ("outline", "scene", "audit"):
            raise ValueError("Unknown job mode")
        ids = connection.execute(
            "SELECT id FROM ai_jobs WHERE project_id=? AND mode=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 30",
            (project_id, mode),
        ).fetchall()
    return [get_job(connection, row["id"]) for row in ids]


def active_job(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM ai_jobs WHERE status IN ('queued','running') LIMIT 1"
    ).fetchone() is not None


def estimate_progress(characters: int, elapsed_seconds: float) -> tuple[int | None, int | None, int]:
    """Only approximate progress toward the *maximum* generated token budget.

    Ollama sends an accurate eval_count at the end, not on each partial frame.
    Thus never claim this is actual completion percent or an exact ETA.
    """
    if characters < 40 or elapsed_seconds <= 0:
        return None, None, 0
    guessed_tokens = min(LIMIT, round(characters / 3.4))
    percent = max(1, min(95, round(guessed_tokens / LIMIT * 100)))
    if elapsed_seconds < 3:
        return percent, None, guessed_tokens
    rate = characters / elapsed_seconds
    seconds = round(max(0, LIMIT * 3.4 - characters) / max(rate, 1))
    return percent, min(seconds, 7200), guessed_tokens


def save_audit_decision(connection: sqlite3.Connection, job_id: str,
                        decision: str, notes: str) -> dict:
    """Acknowledge QA suggestions. Never mutate scene text or the manuscript."""
    if decision not in ("unreviewed", "in_progress", "completed", "reference"):
        raise ValueError("Unknown audit decision")
    if len(notes) > 3000:
        raise ValueError("Audit notes too long")
    job = get_job(connection, job_id)
    if job is None or job["mode"] != "audit" or job["status"] != "ready":
        raise ValueError("Only completed audit jobs can be reviewed")
    connection.execute("""
        INSERT INTO ai_job_reviews (job_id, decision, notes, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            decision=excluded.decision, notes=excluded.notes, updated_at=excluded.updated_at
    """, (job_id, decision, notes, utc_now()))
    return get_job(connection, job_id)
