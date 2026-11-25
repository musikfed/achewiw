"""Bridge chat editorial proposals to real ACHEWIW immutable book versions.

Only work.sqlite3 work_book_versions are changed: no canonical project, draft
job or imported DOCWORK original is edited. An author must explicitly approve
each proposal. The atomic acceptance creates a version and moves a work-only
head pointer in one transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from difflib import unified_diff
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from editor_workspace import _open
from editorial_review import initialize as init_book_versions
from book_generation_core import count_words


class EditorialConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _encode(payload):
    return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))


def initialize(root: Path):
    init_book_versions(root)
    with _open(Path(root)/"work.sqlite3") as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS work_chat_book_heads(
            project_id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS work_chat_book_edits(
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            base_version_id TEXT NOT NULL,
            scene_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            candidate_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed'
                CHECK(status IN ('proposed','accepted','rejected')),
            accepted_version_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chat_book_edits_project
            ON work_chat_book_edits(project_id,status,created_at);
        """)


def _version(db, project_id, version_id):
    row=db.execute("""SELECT * FROM work_book_versions
        WHERE id=? AND project_id=?""",(version_id,project_id)).fetchone()
    if row is None:
        raise ValueError("Book version not found in this project")
    return row, json.loads(row["payload_json"])


def _scene(payload, scene_id):
    matches=[ch for ch in payload["chapters"] if ch["scene_id"]==scene_id]
    if len(matches)!=1:
        raise ValueError("Scene not found or ambiguous in this edition")
    return matches[0]


def attach(root: Path, *, project_id: str, version_id: str):
    """Attach once to an existing immutable snapshot; never silently rebase."""
    initialize(root)
    with _open(Path(root)/"work.sqlite3") as db:
        _version(db,project_id,version_id)
        db.execute("""INSERT OR IGNORE INTO work_chat_book_heads(project_id,version_id)
            VALUES(?,?)""",(project_id,version_id))
        current=db.execute("""SELECT version_id FROM work_chat_book_heads
            WHERE project_id=?""",(project_id,)).fetchone()["version_id"]
    return {"project_id":project_id,"head_version_id":current,
            "requested_version_id":version_id, "already_attached":current!=version_id}


def propose(root: Path, *, project_id: str, base_version_id: str,
            scene_id: str, candidate_text: str):
    if not isinstance(candidate_text,str) or not candidate_text.strip() or len(candidate_text)>150000:
        raise ValueError("Invalid candidate text")
    initialize(root)
    with _open(Path(root)/"work.sqlite3") as db:
        head=db.execute("""SELECT version_id FROM work_chat_book_heads
            WHERE project_id=?""",(project_id,)).fetchone()
        if head is None or head["version_id"]!=base_version_id:
            raise EditorialConflict("Selected edition is no longer the active work revision")
        _,payload=_version(db,project_id,base_version_id)
        before=_scene(payload,scene_id)["text"]
        if before==candidate_text:
            raise ValueError("No change requested")
        proposal_id=uuid4().hex
        db.execute("""INSERT INTO work_chat_book_edits
            (id,project_id,base_version_id,scene_id,source_sha256,candidate_text,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (proposal_id,project_id,base_version_id,scene_id,
             _hash(before),candidate_text,_now()))
    return {"proposal_id":proposal_id,"base_version_id":base_version_id,
            "scene_id":scene_id,"source_sha256":_hash(before),
            "diff":"\n".join(unified_diff(before.splitlines(),candidate_text.splitlines(),
                                fromfile="before",tofile="proposed",lineterm="")),
            "applied":False}


def decide(root: Path, *, project_id: str, proposal_id: str, approve: bool):
    if type(approve) is not bool:
        raise ValueError("Explicit boolean author decision required")
    with _open(Path(root)/"work.sqlite3") as db:
        proposal=db.execute("""SELECT * FROM work_chat_book_edits
            WHERE id=? AND project_id=?""",(proposal_id,project_id)).fetchone()
        if proposal is None or proposal["status"]!="proposed":
            raise EditorialConflict("Proposal absent or already decided")
        head=db.execute("SELECT version_id FROM work_chat_book_heads WHERE project_id=?",
                        (project_id,)).fetchone()
        if not approve:
            db.execute("UPDATE work_chat_book_edits SET status='rejected' WHERE id=?",
                       (proposal_id,))
            return {"status":"rejected","changed":False}
        if head is None or head["version_id"]!=proposal["base_version_id"]:
            raise EditorialConflict("Proposal is stale: work edition changed")
        base,payload=_version(db,project_id,proposal["base_version_id"])
        chapter=_scene(payload,proposal["scene_id"])
        if _hash(chapter["text"])!=proposal["source_sha256"]:
            raise EditorialConflict("Source chapter changed")
        payload=json.loads(_encode(payload))  # Copy, do not touch previous snapshot
        chapter=_scene(payload,proposal["scene_id"])
        chapter["text"]=proposal["candidate_text"]
        chapter["words"]=count_words(chapter["text"])
        raw=_encode(payload)
        digest=_hash(raw)
        revision_id=uuid4().hex
        existing=db.execute("""SELECT id FROM work_book_versions WHERE project_id=?
          AND source_kind=? AND source_job_id=? AND digest=?""",
          (project_id,base["source_kind"],base["source_job_id"],digest)).fetchone()
        if existing is not None:
            revision_id=existing["id"]
        else:
            db.execute("""INSERT INTO work_book_versions
              (id,project_id,source_kind,source_job_id,digest,label,payload_json,created_at)
              VALUES(?,?,?,?,?,?,?,?)""",
              (revision_id,project_id,base["source_kind"],base["source_job_id"],
               digest,"Чат-редактура "+_now(),raw,_now()))
        changed=db.execute("""UPDATE work_chat_book_heads SET version_id=?
             WHERE project_id=? AND version_id=?""",
             (revision_id,project_id,proposal["base_version_id"]))
        if changed.rowcount!=1:
            raise EditorialConflict("Concurrent edition update")
        db.execute("""UPDATE work_chat_book_edits SET status='accepted',accepted_version_id=?
          WHERE id=?""",(revision_id,proposal_id))
    return {"status":"accepted","changed":True,
            "version_id":revision_id,"previous_version_id":proposal["base_version_id"]}


def current(root: Path, *, project_id: str):
    initialize(root)
    with _open(Path(root)/"work.sqlite3") as db:
        head=db.execute("SELECT version_id FROM work_chat_book_heads WHERE project_id=?",
                        (project_id,)).fetchone()
        if head is None:
            raise ValueError("Project has no chat editorial head")
        version,payload=_version(db,project_id,head["version_id"])
    return {"version_id":version["id"],"digest":version["digest"],"chapters":payload["chapters"]}


def prepare_restore(root: Path, *, project_id: str, restore_version_id: str,
                    scene_id: str):
    """Restore requires the same preview/author accept path as a normal edit."""
    with _open(Path(root)/"work.sqlite3") as db:
        _,past=_version(db,project_id,restore_version_id)
        original_text=_scene(past,scene_id)["text"]
    head=current(root,project_id=project_id)
    return propose(root,project_id=project_id,base_version_id=head["version_id"],
                   scene_id=scene_id,candidate_text=original_text)
