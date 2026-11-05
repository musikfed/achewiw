"""ACHEWIW storage v1: safe canonical/derived-index/working-space boundaries.

The legacy SQLite remains the authority, including previously saved draft jobs.
This module NEVER moves or erases those tables during upgrade. Derived FTS5
can be rebuilt at any time; WORK holds durable experiment/check results.
No additional database servers, Docker containers or GPU workloads.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("SQLite symlink is not allowed")
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection

def initialize(data_dir: Path) -> dict:
    """Add only separate disposable index + durable working DB, never rename canon."""
    search_path, work_path = data_dir / "search.sqlite3", data_dir / "work.sqlite3"
    with _open(search_path) as search:
        search.executescript("""
        CREATE TABLE IF NOT EXISTS search_manifest (
            source_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL, source_kind TEXT NOT NULL,
            revision_id TEXT NOT NULL, digest TEXT NOT NULL,
            text TEXT NOT NULL, indexed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_search_project
            ON search_manifest(project_id,branch_id,source_kind);
        CREATE TABLE IF NOT EXISTS search_embeddings (
            source_id TEXT NOT NULL, model_id TEXT NOT NULL,
            digest TEXT NOT NULL, dimension INTEGER NOT NULL,
            vector BLOB NOT NULL, PRIMARY KEY(source_id,model_id));
        """)
        search.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
            source_id UNINDEXED, text, tokenize='unicode61')""")
    with _open(work_path) as work:
        work.executescript("""
        CREATE TABLE IF NOT EXISTS work_proposals (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, branch_id TEXT NOT NULL,
            source_revision TEXT NOT NULL, kind TEXT NOT NULL,
            payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed',
            created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_work_project
            ON work_proposals(project_id,branch_id,status);
        CREATE TABLE IF NOT EXISTS work_qa (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, branch_id TEXT NOT NULL,
            source_revision TEXT NOT NULL, checker TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS work_checkpoints (
            job_id TEXT NOT NULL, scene_id TEXT NOT NULL,
            project_id TEXT NOT NULL, source_digest TEXT NOT NULL,
            words INTEGER NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(job_id,scene_id));
        """)
    return {"canon":str(data_dir/"achewiw.sqlite3"),"search":str(search_path),
            "work":str(work_path)}

def sync_search(data_dir: Path, canon: sqlite3.Connection, project_id: str | None = None) -> dict:
    """Copy only changed approved/synopsis/draft chunks; delete stale derived rows.

    Source-of-truth is the legacy story_chunks table. No whole-book re-embedding,
    no network, and no changes to canon/author manuscripts.
    """
    conditions = " WHERE project_id=?" if project_id else ""
    args = (project_id,) if project_id else ()
    found = canon.execute("SELECT name FROM sqlite_master WHERE name='story_chunks'").fetchone()
    if not found:
        return {"indexed":0,"unchanged":0,"removed":0,"total":0,"note":"No source chunks yet"}
    records=canon.execute(
        "SELECT id,project_id,branch_id,job_id,source,content,digest "
        "FROM story_chunks"+conditions+" ORDER BY id", args).fetchall()
    added=0
    unchanged=0
    alive=set()
    with _open(data_dir / "search.sqlite3") as search:
        for record in records:
            sid=record["id"]
            alive.add(sid)
            prev=search.execute("SELECT digest FROM search_manifest WHERE source_id=?",(sid,)).fetchone()
            if prev and prev["digest"]==record["digest"]:
                unchanged+=1
                continue
            search.execute("DELETE FROM search_fts WHERE source_id=?",(sid,))
            search.execute("DELETE FROM search_embeddings WHERE source_id=?",(sid,))
            search.execute("""INSERT INTO search_manifest
                (source_id,project_id,branch_id,source_kind,revision_id,digest,text,indexed_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(source_id) DO UPDATE SET
                 project_id=excluded.project_id,branch_id=excluded.branch_id,
                 source_kind=excluded.source_kind,revision_id=excluded.revision_id,
                 digest=excluded.digest,text=excluded.text,indexed_at=excluded.indexed_at""",
                 (sid,record["project_id"],record["branch_id"],record["source"],
                  record["job_id"],record["digest"],record["content"],utc()))
            search.execute("INSERT INTO search_fts(source_id,text) VALUES(?,?)",
                           (sid,record["content"]))
            added+=1
        all_ids = [r[0] for r in search.execute(
            "SELECT source_id FROM search_manifest"+conditions,args).fetchall()]
        obsolete=[sid for sid in all_ids if sid not in alive]
        for sid in obsolete:
            search.execute("DELETE FROM search_fts WHERE source_id=?",(sid,))
            search.execute("DELETE FROM search_embeddings WHERE source_id=?",(sid,))
            search.execute("DELETE FROM search_manifest WHERE source_id=?",(sid,))
        total=search.execute("SELECT count(*) FROM search_manifest"+conditions,args).fetchone()[0]
    return {"indexed":added,"unchanged":unchanged,"removed":len(obsolete),"total":total}

def checkpoint_work(data_dir: Path, project_id: str, job_id: str, scene_id: str,
                    text: str, words: int) -> None:
    """Record a resumable workspace manifest; original prose stays in legacy job table."""
    digest=hashlib.sha256(text.encode("utf-8")).hexdigest()
    with _open(data_dir/"work.sqlite3") as db:
        db.execute("""INSERT INTO work_checkpoints
            (job_id,scene_id,project_id,source_digest,words,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(job_id,scene_id) DO UPDATE SET
              source_digest=excluded.source_digest,words=excluded.words,
              updated_at=excluded.updated_at""",
            (job_id,scene_id,project_id,digest,words,utc()))

def status(data_dir: Path) -> dict:
    result={}
    for key,name in (("canon","achewiw.sqlite3"),("search","search.sqlite3"),
                     ("work","work.sqlite3")):
        p=data_dir/name
        result[key]={"exists":p.is_file() and not p.is_symlink(),
                     "size_bytes":p.stat().st_size if p.is_file() and not p.is_symlink() else 0}
    return result
