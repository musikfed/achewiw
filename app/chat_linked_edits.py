"""Bind saved chat messages to real immutable editorial proposals.

One work.sqlite3 transaction handles author decision, edition CAS and chat
proposal status. The synthetic chapter never touches the project canon.
This is a service-layer operation; no network endpoints are exposed yet.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import json

from editor_workspace import _open
import chat_conversations as chats
import chat_editorial_version_bridge as editions
from book_generation_core import count_words


class LinkedEditConflict(ValueError):
    pass


def initialize(root: Path):
    chats.initialize(root)
    editions.initialize(root)
    with _open(Path(root) / "work.sqlite3") as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS work_chat_edit_links(
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            chat_proposal_id TEXT NOT NULL UNIQUE,
            edition_edit_id TEXT NOT NULL UNIQUE,
            base_version_id TEXT NOT NULL,
            scene_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chat_edit_links_thread
            ON work_chat_edit_links(project_id,thread_id);
        """)


def _linked(db, project_id: str, thread_id: str, chat_proposal_id: str):
    thread=chats._thread(db,thread_id,project_id)
    if thread["workspace"]!="editorial":
        raise LinkedEditConflict("Generation chat cannot edit an existing manuscript")
    proposal=db.execute("""SELECT p.*,m.role FROM work_chat_proposals p
       JOIN work_chat_messages m ON m.id=p.message_id AND m.thread_id=p.thread_id
       WHERE p.id=? AND p.thread_id=?""",
       (chat_proposal_id,thread_id)).fetchone()
    if proposal is None or proposal["role"]!="author":
        raise LinkedEditConflict("No matching author instruction")
    return proposal


def prepare(root: Path, *, project_id: str, thread_id: str,
            chat_proposal_id: str, base_version_id: str,
            scene_id: str, candidate_text: str) -> dict:
    """Store proposed text only for an exact chapter-bound author instruction."""
    if not isinstance(candidate_text,str) or not candidate_text.strip() or len(candidate_text)>150000:
        raise ValueError("Invalid proposed text")
    initialize(root)
    with _open(Path(root)/"work.sqlite3") as db:
        proposal=_linked(db,project_id,thread_id,chat_proposal_id)
        if proposal["status"]!="proposed":
            raise LinkedEditConflict("Instruction has already been decided")
        if proposal["scope"]!="chapter" or proposal["source_ref"]!=scene_id:
            raise LinkedEditConflict("Author did not select this exact chapter")
        if proposal["source_digest"] is None:
            raise LinkedEditConflict("Missing source fingerprint")
        old=db.execute("SELECT id FROM work_chat_edit_links WHERE chat_proposal_id=?",
                       (chat_proposal_id,)).fetchone()
        if old:
            raise LinkedEditConflict("Instruction already has an editorial candidate")
        head=db.execute("SELECT version_id FROM work_chat_book_heads WHERE project_id=?",
                        (project_id,)).fetchone()
        if head is None or head["version_id"]!=base_version_id:
            raise LinkedEditConflict("Outdated base edition")
        _,payload=editions._version(db,project_id,base_version_id)
        original=editions._scene(payload,scene_id)["text"]
        if editions._hash(original)!=proposal["source_digest"]:
            raise LinkedEditConflict("Instruction fingerprint differs from the selected chapter")
        if original==candidate_text:
            raise ValueError("Candidate matches source text")
        edit_id,link_id=uuid4().hex,uuid4().hex
        db.execute("""INSERT INTO work_chat_book_edits
          (id,project_id,base_version_id,scene_id,source_sha256,candidate_text,created_at)
          VALUES(?,?,?,?,?,?,?)""",
          (edit_id,project_id,base_version_id,scene_id,proposal["source_digest"],
           candidate_text,editions._now()))
        db.execute("""INSERT INTO work_chat_edit_links
          (id,project_id,thread_id,message_id,chat_proposal_id,edition_edit_id,
           base_version_id,scene_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
          (link_id,project_id,thread_id,proposal["message_id"],
           chat_proposal_id,edit_id,base_version_id,scene_id,editions._now()))
    from difflib import unified_diff
    return {"chat_proposal_id":chat_proposal_id,"edit_id":edit_id,
            "thread_id":thread_id,"scene_id":scene_id,"base_version_id":base_version_id,
            "diff":"\n".join(unified_diff(original.splitlines(),candidate_text.splitlines(),
                    fromfile="before",tofile="proposal",lineterm="")),
            "status":"proposed","applied":False}


def decide(root: Path, *, project_id: str, thread_id: str,
           chat_proposal_id: str, approve: bool) -> dict:
    """Atomic author decision across message, proposal and immutable revision."""
    if type(approve) is not bool:
        raise ValueError("Explicit boolean confirmation required")
    with _open(Path(root)/"work.sqlite3") as db:
        proposal=_linked(db,project_id,thread_id,chat_proposal_id)
        link=db.execute("""SELECT * FROM work_chat_edit_links
            WHERE chat_proposal_id=? AND project_id=? AND thread_id=?""",
            (chat_proposal_id,project_id,thread_id)).fetchone()
        if link is None or proposal["status"]!="proposed":
            raise LinkedEditConflict("Instruction missing, rejected or already decided")
        edit=db.execute("""SELECT * FROM work_chat_book_edits
            WHERE id=? AND project_id=?""",(link["edition_edit_id"],project_id)).fetchone()
        if edit is None or edit["status"]!="proposed":
            raise LinkedEditConflict("Editorial proposal already decided")
        if not approve:
            db.execute("UPDATE work_chat_book_edits SET status='rejected' WHERE id=?",(edit["id"],))
            db.execute("""UPDATE work_chat_proposals SET status='rejected',updated_at=?
                WHERE id=? AND status='proposed'""",(editions._now(),chat_proposal_id))
            return {"status":"rejected","manuscript_changed":False}
        head=db.execute("SELECT version_id FROM work_chat_book_heads WHERE project_id=?",
                        (project_id,)).fetchone()
        if head is None or head["version_id"]!=link["base_version_id"]:
            raise LinkedEditConflict("Stale edit: another revision became active")
        if edit["base_version_id"]!=link["base_version_id"] or edit["scene_id"]!=link["scene_id"]:
            raise LinkedEditConflict("Broken link metadata")
        base,payload=editions._version(db,project_id,link["base_version_id"])
        current=editions._scene(payload,link["scene_id"])
        if editions._hash(current["text"])!=edit["source_sha256"] or edit["source_sha256"]!=proposal["source_digest"]:
            raise LinkedEditConflict("Chapter has changed since instruction")
        payload=json.loads(editions._encode(payload))
        chapter=editions._scene(payload,link["scene_id"])
        chapter["text"]=edit["candidate_text"]
        chapter["words"]=count_words(chapter["text"])
        raw=editions._encode(payload)
        fingerprint=editions._hash(raw)
        existing=db.execute("""SELECT id FROM work_book_versions
           WHERE project_id=? AND source_kind=? AND source_job_id=? AND digest=?""",
           (project_id,base["source_kind"],base["source_job_id"],fingerprint)).fetchone()
        version_id=existing["id"] if existing else uuid4().hex
        if not existing:
            db.execute("""INSERT INTO work_book_versions
              (id,project_id,source_kind,source_job_id,digest,label,payload_json,created_at)
              VALUES(?,?,?,?,?,?,?,?)""",
              (version_id,project_id,base["source_kind"],base["source_job_id"],fingerprint,
               "Чат-редактура "+editions._now(),raw,editions._now()))
        result=db.execute("""UPDATE work_chat_book_heads SET version_id=?
            WHERE project_id=? AND version_id=?""",
            (version_id,project_id,link["base_version_id"]))
        if result.rowcount!=1:
            raise LinkedEditConflict("Concurrent edit")
        db.execute("""UPDATE work_chat_book_edits SET status='accepted',accepted_version_id=?
            WHERE id=? AND status='proposed'""",(version_id,edit["id"]))
        db.execute("""UPDATE work_chat_proposals SET status='accepted',updated_at=?
            WHERE id=? AND status='proposed'""",(editions._now(),chat_proposal_id))
        return {"status":"accepted","manuscript_changed":False,
                "new_immutable_version":version_id,"previous_version":base["id"],
                "editorial_work_head_changed":True}


def pending_candidate(root: Path, *, project_id: str, thread_id: str,
                      chat_proposal_id: str) -> dict:
    """Read back exact linked diff after browser refresh, scoped to the chat."""
    from difflib import unified_diff
    with _open(Path(root)/"work.sqlite3") as db:
        proposal=_linked(db,project_id,thread_id,chat_proposal_id)
        link=db.execute("""SELECT * FROM work_chat_edit_links
            WHERE project_id=? AND thread_id=? AND chat_proposal_id=?""",
            (project_id,thread_id,chat_proposal_id)).fetchone()
        if link is None:
            raise LinkedEditConflict("No editorial candidate for this instruction")
        edit=db.execute("""SELECT * FROM work_chat_book_edits
            WHERE id=? AND project_id=?""",(link["edition_edit_id"],project_id)).fetchone()
        if edit is None:
            raise LinkedEditConflict("No saved editorial candidate")
        _,payload=editions._version(db,project_id,edit["base_version_id"])
        original=editions._scene(payload,edit["scene_id"])["text"]
        diff="\n".join(unified_diff(original.splitlines(),edit["candidate_text"].splitlines(),
                   fromfile="before",tofile="proposed",lineterm=""))
        return {"chat_proposal_id":chat_proposal_id,"scene_id":edit["scene_id"],
                "base_version_id":edit["base_version_id"],
                "status":edit["status"],"instruction_status":proposal["status"],
                "diff":diff,"applied":edit["status"]=="accepted"}
