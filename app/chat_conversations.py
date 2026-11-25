"""Durable, proposal-only conversations for ACHEWIW Studio and DOCWORK.

Stored in the existing work.sqlite3; canonical manuscripts and generation jobs
are never read or modified. No model, network or background worker is started.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from editor_workspace import _open
from chat_command_planner import plan_command

WORKSPACES = frozenset(("generation", "editorial"))
MODES = frozenset(("clarify", "autonomous"))
SCOPES = frozenset(("selection", "chapter", "book", "document"))


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _id() -> str:
    return uuid4().hex


def _validated_text(text: str, max_length: int, field: str) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > max_length:
        raise ValueError(f"{field}: expected nonempty text up to {max_length} characters")
    return text.strip()


def _db(data_dir: Path) -> Path:
    return Path(data_dir) / "work.sqlite3"


def initialize(data_dir: Path) -> None:
    """Add new tables only; do not alter prior ACHEWIW schemas."""
    with _open(_db(data_dir)) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS work_chat_threads (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            workspace TEXT NOT NULL CHECK(workspace IN ('generation','editorial')),
            mode TEXT NOT NULL CHECK(mode IN ('clarify','autonomous')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_work_chat_threads_project
            ON work_chat_threads(project_id,updated_at);
        CREATE TABLE IF NOT EXISTS work_chat_messages (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES work_chat_threads(id),
            role TEXT NOT NULL CHECK(role IN ('author','assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_work_chat_messages_thread
            ON work_chat_messages(thread_id,created_at,id);
        CREATE TABLE IF NOT EXISTS work_chat_proposals (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES work_chat_threads(id),
            message_id TEXT NOT NULL REFERENCES work_chat_messages(id),
            source_ref TEXT,
            source_digest TEXT,
            scope TEXT,
            plan_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed'
                CHECK(status IN ('proposed','rejected')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(source_digest IS NULL OR length(source_digest)=64)
        );
        CREATE INDEX IF NOT EXISTS idx_work_chat_proposals_thread
            ON work_chat_proposals(thread_id,status,created_at);
        """)


def create_thread(data_dir: Path, *, project_id: str, workspace: str,
                  mode: str = "clarify") -> dict:
    _validated_text(project_id, 180, "project_id")
    if workspace not in WORKSPACES or mode not in MODES:
        raise ValueError("Invalid workspace or mode")
    initialize(data_dir)
    result = {"id": _id(), "project_id": project_id,
              "workspace": workspace, "mode": mode}
    now = _stamp()
    with _open(_db(data_dir)) as db:
        db.execute("""INSERT INTO work_chat_threads
            (id,project_id,workspace,mode,created_at,updated_at)
            VALUES (?,?,?,?,?,?)""",
            (result["id"],project_id,workspace,mode,now,now))
    return result


def _thread(db: sqlite3.Connection, thread_id: str, project_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM work_chat_threads WHERE id=? AND project_id=?",
        (thread_id,project_id)).fetchone()
    if row is None:
        raise ValueError("Conversation does not belong to this project")
    return row


def switch_mode(data_dir: Path, *, project_id: str, thread_id: str,
                mode: str) -> dict:
    if mode not in MODES:
        raise ValueError("Invalid assistant mode")
    with _open(_db(data_dir)) as db:
        row = _thread(db,thread_id,project_id)
        db.execute("UPDATE work_chat_threads SET mode=?,updated_at=? WHERE id=?",
                   (mode,_stamp(),thread_id))
    return {"thread_id":thread_id, "mode":mode, "workspace":row["workspace"]}


def submit(data_dir: Path, *, project_id: str, thread_id: str,
           message: str, scope: str | None = None,
           source_ref: str | None = None,
           source_text: str | None = None) -> dict:
    """Atomically store an author message and proposed operations.

    source_text is used solely to fingerprint the selected original fragment;
    it is deliberately not stored. No approval or manuscript write occurs.
    """
    _validated_text(message,10000,"message")
    if scope is not None and scope not in SCOPES:
        raise ValueError("Invalid editing scope")
    if source_ref is not None:
        _validated_text(source_ref,200,"source_ref")
    if source_text is not None and (not isinstance(source_text,str) or len(source_text)>150000):
        raise ValueError("Source fragment too long")
    if source_text is not None and not source_ref:
        raise ValueError("A source_ref is needed to bind a fingerprint")
    fingerprint = (hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                   if source_text is not None else None)
    with _open(_db(data_dir)) as db:
        thread = _thread(db,thread_id,project_id)
        plan = plan_command(message,workspace=thread["workspace"],
                            mode=thread["mode"],project_id=project_id,scope=scope)
        message_id, proposal_id, now = _id(), _id(), _stamp()
        db.execute("""INSERT INTO work_chat_messages
            (id,thread_id,role,content,created_at) VALUES (?,?,?,?,?)""",
            (message_id,thread_id,"author",message.strip(),now))
        db.execute("""INSERT INTO work_chat_proposals
            (id,thread_id,message_id,source_ref,source_digest,scope,plan_json,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,'proposed',?,?)""",
            (proposal_id,thread_id,message_id,source_ref,fingerprint,
             scope,json.dumps(plan,ensure_ascii=False),now,now))
        db.execute("UPDATE work_chat_threads SET updated_at=? WHERE id=?",
                   (now,thread_id))
    return {"message_id":message_id,"proposal_id":proposal_id,
            "source_digest":fingerprint, **plan}


def conversation(data_dir: Path, *, project_id: str, thread_id: str) -> dict:
    with _open(_db(data_dir)) as db:
        row = _thread(db,thread_id,project_id)
        messages = db.execute("""SELECT id,role,content,created_at
            FROM work_chat_messages WHERE thread_id=?
            ORDER BY created_at,id""",(thread_id,)).fetchall()
        proposals = db.execute("""SELECT id,message_id,source_ref,source_digest,
            scope,status,plan_json,created_at FROM work_chat_proposals
            WHERE thread_id=? ORDER BY created_at,id""",(thread_id,)).fetchall()
    return {
        "thread":{k:row[k] for k in ("id","project_id","workspace","mode","created_at","updated_at")},
        "messages":[dict(item) for item in messages],
        "proposals":[{**{k:item[k] for k in
            ("id","message_id","source_ref","source_digest","scope","status","created_at")},
            "plan":json.loads(item["plan_json"])} for item in proposals],
    }


def proposal_preflight(data_dir: Path, *, project_id: str, thread_id: str,
                       proposal_id: str, current_text: str) -> dict:
    """Read-only stale-source guard, NOT approval or execution."""
    if not isinstance(current_text,str):
        raise ValueError("Current source text must be provided")
    with _open(_db(data_dir)) as db:
        _thread(db,thread_id,project_id)
        row = db.execute("""SELECT source_ref,source_digest,status FROM work_chat_proposals
            WHERE id=? AND thread_id=?""",(proposal_id,thread_id)).fetchone()
    if row is None:
        raise ValueError("Proposal is not part of this conversation")
    current = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
    return {"proposal_id":proposal_id,"status":row["status"],
            "source_ref":row["source_ref"],
            "has_source_snapshot":row["source_digest"] is not None,
            "source_unchanged":row["source_digest"] is not None and
                               row["source_digest"] == current,
            "can_apply":False}


def reject(data_dir: Path, *, project_id: str, thread_id: str,
           proposal_id: str) -> dict:
    """Reject a draft; this never edits the underlying document."""
    with _open(_db(data_dir)) as db:
        _thread(db,thread_id,project_id)
        row = db.execute("SELECT status FROM work_chat_proposals WHERE id=? AND thread_id=?",
                         (proposal_id,thread_id)).fetchone()
        if row is None:
            raise ValueError("Proposal is not part of this conversation")
        if row["status"] == "proposed":
            db.execute("""UPDATE work_chat_proposals SET status='rejected',updated_at=?
                WHERE id=? AND thread_id=? AND status='proposed'""",
                (_stamp(),proposal_id,thread_id))
    return {"proposal_id":proposal_id,"status":"rejected","manuscript_changed":False}
