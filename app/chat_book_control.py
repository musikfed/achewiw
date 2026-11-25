"""Chat-to-book control: reuse the existing ACHEWIW book job, not a new runner.

Store only a link and audit events in work.sqlite3. The real job remains in
achewiw.sqlite3, controlled by the canonical generate/pause/resume functions.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from editor_workspace import _open
import chat_conversations as chats


class BookControlConflict(ValueError):
    pass


def initialize(root: Path):
    chats.initialize(root)
    with _open(Path(root) / "work.sqlite3") as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS work_chat_book_control_events(
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            action TEXT NOT NULL
                CHECK(action IN ('start','pause','resume')),
            job_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_work_chat_book_control_events
            ON work_chat_book_control_events(project_id,thread_id,created_at);
        """)


def require_generation_thread(root: Path, project_id: str,
                              thread_id: str, *, has_instruction: bool = False):
    initialize(root)
    with _open(Path(root)/"work.sqlite3") as db:
        thread=chats._thread(db,thread_id,project_id)
        if thread["workspace"]!="generation":
            raise BookControlConflict("This job is controlled from the generation chat only")
        if has_instruction and db.execute(
            """SELECT 1 FROM work_chat_messages
               WHERE thread_id=? AND role='author' LIMIT 1""",
            (thread_id,)).fetchone() is None:
            raise BookControlConflict("Save a book instruction in this chat before starting")
    return True


def record(root: Path, *, project_id: str, thread_id: str,
           action: str, job: dict):
    """Record a confirmed canonical job action; never create a second job."""
    if action not in ("start","pause","resume"):
        raise ValueError("Unsupported book control action")
    if not isinstance(job,dict) or job.get("project_id")!=project_id or not job.get("id"):
        raise BookControlConflict("Canonical book job does not belong to this project")
    require_generation_thread(root,project_id,thread_id)
    with _open(Path(root)/"work.sqlite3") as db:
        db.execute("""INSERT INTO work_chat_book_control_events
            (id,project_id,thread_id,action,job_id,created_at)
            VALUES (?,?,?,?,?,?)""",
            (uuid4().hex,project_id,thread_id,action,job["id"],chats._stamp()))
    return {"job_id":job["id"],"action":action,"recorded":True}


def history(root: Path, *, project_id: str, thread_id: str):
    require_generation_thread(root,project_id,thread_id)
    with _open(Path(root)/"work.sqlite3") as db:
        rows=db.execute("""SELECT id,action,job_id,created_at
            FROM work_chat_book_control_events
            WHERE project_id=? AND thread_id=?
            ORDER BY created_at,id""",(project_id,thread_id)).fetchall()
    return [dict(row) for row in rows]
