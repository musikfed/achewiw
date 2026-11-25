"""Synthetic chapter editorial acceptance: immutable revisions, CAS and rollback.

Only isolated work.sqlite3 sandbox records. Never writes real ACHEWIW canon.
"""
from __future__ import annotations
from datetime import datetime, timezone
from difflib import unified_diff
import hashlib
from pathlib import Path
import sqlite3
from uuid import uuid4
from editor_workspace import _open


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def initialize(root: Path):
    with _open(Path(root) / "work.sqlite3") as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS work_chat_synthetic_chapters(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, head_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS work_chat_synthetic_revisions(
            id TEXT PRIMARY KEY, chapter_id TEXT NOT NULL,
            previous_id TEXT, text TEXT NOT NULL, digest TEXT NOT NULL,
            origin TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS work_chat_synthetic_edits(
            id TEXT PRIMARY KEY, chapter_id TEXT NOT NULL,
            base_id TEXT NOT NULL, base_digest TEXT NOT NULL,
            candidate_text TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL);
        """)


def create(root: Path, project_id: str, text: str) -> dict:
    if not project_id or not isinstance(text, str) or not text.strip():
        raise ValueError("Missing project or original chapter")
    if len(text)>150000:
        raise ValueError("Chapter too long")
    initialize(root)
    chapter,revision=uuid4().hex,uuid4().hex
    with _open(Path(root)/"work.sqlite3") as db:
        db.execute("INSERT INTO work_chat_synthetic_revisions VALUES(?,?,?,?,?,?,?)",
                   (revision,chapter,None,text,sha(text),"original",now()))
        db.execute("INSERT INTO work_chat_synthetic_chapters VALUES(?,?,?)",
                   (chapter,project_id,revision))
    return {"chapter_id":chapter,"revision_id":revision,"digest":sha(text)}


def _head(db, project_id, chapter_id):
    row=db.execute("""SELECT r.id,r.text,r.digest FROM work_chat_synthetic_chapters c
      JOIN work_chat_synthetic_revisions r ON r.id=c.head_id
      WHERE c.id=? AND c.project_id=?""",(chapter_id,project_id)).fetchone()
    if row is None:
        raise ValueError("Unknown chapter for this project")
    return row


def propose(root: Path, project_id: str, chapter_id: str, candidate: str):
    if not isinstance(candidate,str) or not candidate.strip() or len(candidate)>150000:
        raise ValueError("Invalid proposal text")
    with _open(Path(root)/"work.sqlite3") as db:
        head=_head(db,project_id,chapter_id)
        if candidate==head["text"]:
            raise ValueError("No proposed changes")
        ident=uuid4().hex
        db.execute("INSERT INTO work_chat_synthetic_edits VALUES(?,?,?,?,?,?,?)",
                   (ident,chapter_id,head["id"],head["digest"],candidate,"proposed",now()))
        diff="\n".join(unified_diff(head["text"].splitlines(),candidate.splitlines(),
                            fromfile="original",tofile="proposal",lineterm=""))
        return {"proposal_id":ident,"diff":diff,"base_digest":head["digest"],
                "applied":False}


def decide(root: Path, project_id: str, chapter_id: str,
           proposal_id: str, approve: bool):
    if type(approve) is not bool:
        raise ValueError("Explicit approval boolean required")
    with _open(Path(root)/"work.sqlite3") as db:
        head=_head(db,project_id,chapter_id)
        edit=db.execute("""SELECT * FROM work_chat_synthetic_edits
            WHERE id=? AND chapter_id=?""",(proposal_id,chapter_id)).fetchone()
        if edit is None or edit["status"]!="proposed":
            raise ValueError("Proposal missing or already decided")
        if not approve:
            db.execute("UPDATE work_chat_synthetic_edits SET status='rejected' WHERE id=?",
                       (proposal_id,))
            return {"status":"rejected","head_id":head["id"],"changed":False}
        if edit["base_id"]!=head["id"] or edit["base_digest"]!=head["digest"]:
            raise ValueError("Stale proposal: chapter changed")
        revision=uuid4().hex
        db.execute("INSERT INTO work_chat_synthetic_revisions VALUES(?,?,?,?,?,?,?)",
                   (revision,chapter_id,head["id"],edit["candidate_text"],
                    sha(edit["candidate_text"]),"accepted",now()))
        cursor=db.execute("""UPDATE work_chat_synthetic_chapters SET head_id=?
            WHERE id=? AND project_id=? AND head_id=?""",
            (revision,chapter_id,project_id,head["id"]))
        if cursor.rowcount!=1:
            raise ValueError("Concurrent update")
        db.execute("UPDATE work_chat_synthetic_edits SET status='accepted' WHERE id=?",
                   (proposal_id,))
        return {"status":"accepted","head_id":revision,"changed":True}


def restore(root: Path, project_id: str, chapter_id: str, revision_id: str):
    """Rollback is a NEW revision, never destructive deletion of history."""
    with _open(Path(root)/"work.sqlite3") as db:
        head=_head(db,project_id,chapter_id)
        past=db.execute("""SELECT text FROM work_chat_synthetic_revisions
            WHERE id=? AND chapter_id=?""",(revision_id,chapter_id)).fetchone()
        if past is None:
            raise ValueError("Revision does not belong to chapter")
        new_id=uuid4().hex
        db.execute("INSERT INTO work_chat_synthetic_revisions VALUES(?,?,?,?,?,?,?)",
                   (new_id,chapter_id,head["id"],past["text"],sha(past["text"]),"restore",now()))
        db.execute("UPDATE work_chat_synthetic_chapters SET head_id=? WHERE id=? AND project_id=?",
                   (new_id,chapter_id,project_id))
    return {"head_id":new_id,"restored_from":revision_id,"digest":sha(past["text"])}


def read(root: Path, project_id: str, chapter_id: str):
    with _open(Path(root)/"work.sqlite3") as db:
        return dict(_head(db,project_id,chapter_id))
