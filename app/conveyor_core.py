"""Durable, non-destructive ACHEWIW book-production conveyor.

Only this module owns the persistent state machine. The runtime executor lives
in main.py and uses existing Ollama, ComfyUI, book and export services.
A completed stage and its artifacts are committed in one SQLite transaction.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone

STAGES = (
    "book", "mechanical_qa", "literary_qa", "editorial_proposals",
    "illustration", "proof", "author_review", "archive",
)
ROLES = {
    "book": "writer", "mechanical_qa": "cpu_validator",
    "literary_qa": "literary_critic", "editorial_proposals": "editor",
    "illustration": "comfyui_artist", "proof": "cpu_typesetter",
    "author_review": "author", "archive": "corpus_curator",
}
ACTIVE = ("queued", "running", "waiting_author")
ID = re.compile(r"^[0-9a-f]{32}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def initialize(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS production_jobs (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        book_job_id TEXT NOT NULL, writer_model TEXT NOT NULL,
        critic_model TEXT NOT NULL, image_mode TEXT NOT NULL,
        image_style TEXT NOT NULL, allow_shared_unload INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'queued', current_step INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '', cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        CHECK(image_mode IN ('none','cover','cover_chapters')),
        CHECK(state IN ('queued','running','waiting_author','ready','error','interrupted','cancelled'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS production_steps (
        job_id TEXT NOT NULL, stage TEXT NOT NULL,
        ordinal INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        input_sha TEXT NOT NULL DEFAULT '', output_sha TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
        PRIMARY KEY(job_id,stage), FOREIGN KEY(job_id) REFERENCES production_jobs(id),
        CHECK(status IN ('pending','running','done','blocked','error'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS production_artifacts (
        job_id TEXT NOT NULL, stage TEXT NOT NULL, asset_key TEXT NOT NULL,
        source_sha TEXT NOT NULL, content_sha TEXT NOT NULL,
        body TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(job_id,stage,asset_key),
        FOREIGN KEY(job_id) REFERENCES production_jobs(id)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS production_project_idx ON production_jobs(project_id,created_at)")
    # A terminated process must not leave phantom running tasks. Recovery is
    # explicit because a ComfyUI image call may have completed externally.
    db.execute("UPDATE production_jobs SET state='interrupted', updated_at=? WHERE state='running'", (now(),))
    db.execute("""UPDATE production_steps SET status='pending', updated_at=?
                  WHERE status='running'""", (now(),))


def create(db: sqlite3.Connection, *, job_id: str, project_id: str, book_job_id: str,
           writer_model: str, critic_model: str, image_mode: str,
           image_style: str, allow_shared_unload: bool) -> dict:
    if not all(ID.fullmatch(v) for v in (job_id,project_id,book_job_id)):
        raise ValueError("Invalid production/project/book identifier")
    if image_mode not in ("none", "cover", "cover_chapters"):
        raise ValueError("Invalid image mode")
    if any(not m or len(m)>200 for m in (writer_model,critic_model)):
        raise ValueError("Model names must be 1..200 characters")
    if len(image_style)>400:
        raise ValueError("Image style exceeds 400 characters")
    # One unfinished conveyor per book, but old editions remain available.
    if db.execute("""SELECT 1 FROM production_jobs WHERE project_id=?
                     AND state IN ('queued','running','waiting_author')""",
                  (project_id,)).fetchone():
        raise ValueError("This project already has an unfinished conveyor")
    stamp=now()
    db.execute("""INSERT INTO production_jobs
        (id,project_id,book_job_id,writer_model,critic_model,image_mode,
         image_style,allow_shared_unload,created_at,updated_at)
         VALUES (?,?,?,?,?,?,?,?,?,?)""",
         (job_id,project_id,book_job_id,writer_model,critic_model,
          image_mode,image_style,int(allow_shared_unload),stamp,stamp))
    db.executemany("""INSERT INTO production_steps(job_id,stage,ordinal,updated_at)
                      VALUES (?,?,?,?)""",
                   ((job_id,stage,i,stamp) for i,stage in enumerate(STAGES)))
    return status(db,job_id)


def status(db: sqlite3.Connection, job_id: str) -> dict:
    row=db.execute("SELECT * FROM production_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown conveyor job")
    d=dict(row)
    d["allow_shared_unload"]=bool(d["allow_shared_unload"])
    d["steps"]=[{**dict(x),"role":ROLES[x["stage"]]} for x in
                db.execute("SELECT * FROM production_steps WHERE job_id=? ORDER BY ordinal",
                           (job_id,)).fetchall()]
    d["artifacts"]=[{k:x[k] for k in ("stage","asset_key","source_sha","content_sha","created_at")}
                    for x in db.execute("""SELECT stage,asset_key,source_sha,content_sha,created_at
                                          FROM production_artifacts WHERE job_id=?
                                          ORDER BY created_at,stage,asset_key""",(job_id,))]
    d["percent"]=round(100*sum(x["status"]=="done" for x in d["steps"])/len(STAGES))
    return d


def stage_start(db: sqlite3.Connection, job_id: str, stage: str, input_sha: str) -> None:
    if stage not in STAGES:
        raise ValueError("Unknown stage")
    row=db.execute("SELECT state,current_step,cancel_requested FROM production_jobs WHERE id=?",
                   (job_id,)).fetchone()
    if not row or row["state"] not in ("queued","running") or row["cancel_requested"]:
        raise ValueError("Conveyor stopped or already awaiting review")
    if STAGES[row["current_step"]] != stage:
        raise ValueError("Out-of-order stage")
    db.execute("""UPDATE production_steps SET status='running',input_sha=?,updated_at=?
                  WHERE job_id=? AND stage=? AND status IN ('pending','error','blocked')""",
               (input_sha,now(),job_id,stage))
    if db.execute("SELECT changes()").fetchone()[0]!=1:
        raise ValueError("Stage already completed")
    db.execute("UPDATE production_jobs SET state='running',error='',updated_at=? WHERE id=?",
               (now(),job_id))


def put_artifact(db: sqlite3.Connection, job_id: str, stage: str,
                 asset_key: str, source_sha: str, body: str | dict) -> str:
    if stage not in STAGES or not asset_key or len(asset_key)>128:
        raise ValueError("Invalid artifact identity")
    text=json.dumps(body,ensure_ascii=False,sort_keys=True) if isinstance(body,dict) else body
    if len(text)>300000:
        raise ValueError("Artifact is too large")
    digest=fingerprint(text)
    prior=db.execute("""SELECT source_sha,content_sha FROM production_artifacts
                        WHERE job_id=? AND stage=? AND asset_key=?""",
                     (job_id,stage,asset_key)).fetchone()
    if prior:
        if prior["source_sha"]!=source_sha or prior["content_sha"]!=digest:
            raise ValueError("Immutable production artifact collision")
        return digest
    db.execute("""INSERT INTO production_artifacts VALUES (?,?,?,?,?,?,?)""",
               (job_id,stage,asset_key,source_sha,digest,text,now()))
    return digest


def artifact(db: sqlite3.Connection, job_id: str, stage: str, asset_key: str) -> dict:
    row=db.execute("""SELECT * FROM production_artifacts
                      WHERE job_id=? AND stage=? AND asset_key=?""",
                   (job_id,stage,asset_key)).fetchone()
    if not row:
        raise ValueError("Artifact not found")
    return dict(row)


def stage_done(db: sqlite3.Connection, job_id: str, stage: str,
               output_sha: str, note: str="") -> None:
    row=db.execute("SELECT current_step,state,cancel_requested FROM production_jobs WHERE id=?", (job_id,)).fetchone()
    if not row or row["state"]!="running" or row["cancel_requested"] or row["current_step"]>=len(STAGES) or STAGES[row["current_step"]]!=stage:
        raise ValueError("Stage cancelled or out-of-order completion")
    db.execute("""UPDATE production_steps SET status='done',output_sha=?,
                  note=?,updated_at=? WHERE job_id=? AND stage=? AND status='running'""",
               (output_sha,note[:1500],now(),job_id,stage))
    if db.execute("SELECT changes()").fetchone()[0]!=1:
        raise ValueError("Stage was not running")
    i=row["current_step"]+1
    db.execute("UPDATE production_jobs SET current_step=?,state=?,updated_at=? WHERE id=?",
               (i,"ready" if i==len(STAGES) else "running",now(),job_id))


def pause(db: sqlite3.Connection, job_id: str, reason: str) -> None:
    db.execute("""UPDATE production_jobs SET state='waiting_author',error=?,updated_at=?
                  WHERE id=? AND state IN ('queued','running','waiting_author')""",
               (reason[:2000],now(),job_id))
    row=db.execute("SELECT current_step FROM production_jobs WHERE id=?", (job_id,)).fetchone()
    if row and row["current_step"]<len(STAGES):
        db.execute("""UPDATE production_steps SET status='blocked',note=?,updated_at=?
                      WHERE job_id=? AND stage=? AND status!='done'""",
                   (reason[:1500],now(),job_id,STAGES[row["current_step"]]))


def fail(db: sqlite3.Connection, job_id: str, reason: str) -> None:
    db.execute("UPDATE production_jobs SET state='error',error=?,updated_at=? WHERE id=?",
               (reason[:2000],now(),job_id))
    row=db.execute("SELECT current_step FROM production_jobs WHERE id=?", (job_id,)).fetchone()
    if row and row["current_step"]<len(STAGES):
        db.execute("""UPDATE production_steps SET status='error',note=?,updated_at=?
                      WHERE job_id=? AND stage=? AND status!='done'""",
                   (reason[:1500],now(),job_id,STAGES[row["current_step"]]))


def resume(db: sqlite3.Connection, job_id: str, *, author_approved: bool=False) -> dict:
    job=status(db,job_id)
    if job["state"] not in ("error","interrupted","waiting_author"):
        raise ValueError("Only interrupted/error/review conveyors can resume")
    stage=STAGES[job["current_step"]]
    if stage=="author_review" and not author_approved:
        raise ValueError("Explicit author approval required")
    db.execute("""UPDATE production_jobs SET state='queued',cancel_requested=0,
                  error='',updated_at=? WHERE id=?""",(now(),job_id))
    db.execute("""UPDATE production_steps SET status='pending',note='',updated_at=?
                  WHERE job_id=? AND stage=? AND status!='done'""",
               (now(),job_id,stage))
    return status(db,job_id)


def cancel(db: sqlite3.Connection, job_id: str) -> dict:
    db.execute("""UPDATE production_jobs SET cancel_requested=1,state='cancelled',
                  updated_at=? WHERE id=? AND state!='ready'""", (now(),job_id))
    return status(db,job_id)
