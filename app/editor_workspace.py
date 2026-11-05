"""ACHEWIW storage v1: safe canonical/derived-index/working-space boundaries.

The legacy SQLite remains the authority, including previously saved draft jobs.
This module NEVER moves or erases those tables during upgrade. Derived FTS5
can be rebuilt at any time; WORK holds durable experiment/check results.
No additional database servers, Docker containers or GPU workloads.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

@contextmanager
def _open(path: Path):
    """Commit and CLOSE every derived SQLite connection after each operation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("SQLite symlink is not allowed")
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        with connection:
            yield connection
    finally:
        connection.close()

def initialize(data_dir: Path) -> dict:
    """Add only separate disposable index + durable working DB, never rename canon."""
    search_path, work_path = data_dir / "search.sqlite3", data_dir / "work.sqlite3"
    with _open(search_path) as search:
        search.executescript("""
        CREATE TABLE IF NOT EXISTS search_manifest (
            source_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL, scene_index INTEGER NOT NULL DEFAULT 0,
            source_kind TEXT NOT NULL, revision_id TEXT NOT NULL, digest TEXT NOT NULL,
            text TEXT NOT NULL, indexed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_search_project
            ON search_manifest(project_id,branch_id,source_kind);
        CREATE TABLE IF NOT EXISTS search_embeddings (
            source_id TEXT NOT NULL, model_id TEXT NOT NULL,
            digest TEXT NOT NULL, dimension INTEGER NOT NULL,
            vector BLOB NOT NULL, PRIMARY KEY(source_id,model_id));
        """)
        columns={row[1] for row in search.execute("PRAGMA table_info(search_manifest)")}
        if "scene_index" not in columns:
            search.execute("ALTER TABLE search_manifest ADD COLUMN scene_index INTEGER NOT NULL DEFAULT 0")
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
        CREATE TABLE IF NOT EXISTS work_story_state (
            job_id TEXT NOT NULL, scene_id TEXT NOT NULL,
            project_id TEXT NOT NULL, scene_index INTEGER NOT NULL,
            source_digest TEXT NOT NULL, ending TEXT NOT NULL,
            current_goal TEXT NOT NULL, words INTEGER NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(job_id,scene_id));
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
        "SELECT id,project_id,branch_id,scene_index,job_id,source,content,digest "
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
                (source_id,project_id,branch_id,scene_index,source_kind,revision_id,digest,text,indexed_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_id) DO UPDATE SET
                 project_id=excluded.project_id,branch_id=excluded.branch_id,
                 scene_index=excluded.scene_index,source_kind=excluded.source_kind,revision_id=excluded.revision_id,
                 digest=excluded.digest,text=excluded.text,indexed_at=excluded.indexed_at""",
                 (sid,record["project_id"],record["branch_id"],record["scene_index"],record["source"],
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

def search_snippets(data_dir: Path, project_id: str, job_id: str,
                    scene_index: int, question: str, limit: int = 4) -> list[dict]:
    """One safe RAG gateway over the derived index, excluding future/foreign drafts."""
    import re
    from story_memory import STOPWORDS
    tokens=list(dict.fromkeys(x for x in re.findall(
        r"[^\W_]{3,}",question.casefold(),flags=re.UNICODE)
        if x not in STOPWORDS))[:12]
    if not tokens:
        return []
    query=" OR ".join('"'+x+'"' for x in tokens)
    path=data_dir/"search.sqlite3"
    if not path.is_file() or path.is_symlink():
        return []
    with _open(path) as conn:
        try:
            rows=conn.execute("""
                SELECT m.source_id,m.scene_index,m.source_kind,m.revision_id,
                       m.digest,substr(m.text,1,850) AS excerpt
                FROM search_fts f JOIN search_manifest m ON m.source_id=f.source_id
                WHERE search_fts MATCH ? AND m.project_id=? AND m.branch_id='main'
                  AND m.scene_index<=?
                  AND ((m.revision_id='author' AND m.source_kind IN ('approved','synopsis'))
                    OR (m.revision_id=? AND m.source_kind='draft'))
                ORDER BY bm25(search_fts) LIMIT ?""",
                (query,project_id,scene_index,job_id,max(1,min(8,limit)))
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]



def pending_embeddings(data_dir: Path, project_id: str, job_id: str,
                       model_id: str, limit: int = 12) -> list[dict]:
    """Only source-approved and same-job drafts, never another draft or branch."""
    path=data_dir/"search.sqlite3"
    if not path.is_file() or path.is_symlink(): return []
    with _open(path) as conn:
        rows=conn.execute("""
          SELECT m.source_id,m.digest,m.text
            FROM search_manifest m
            LEFT JOIN search_embeddings e
              ON e.source_id=m.source_id AND e.model_id=? AND e.digest=m.digest
           WHERE m.project_id=? AND m.branch_id='main'
             AND ((m.revision_id='author' AND m.source_kind IN ('approved','synopsis'))
               OR (m.revision_id=? AND m.source_kind='draft'))
             AND e.source_id IS NULL
           ORDER BY m.scene_index,m.indexed_at,m.source_id LIMIT ?""",
          (model_id,project_id,job_id,max(1,min(32,limit)))).fetchall()
    return [dict(row) for row in rows]


def _vector_blob(values: list[float]) -> bytes:
    if not 8 <= len(values) <= 4096:
        raise ValueError("Вектор embeddings имеет недопустимую размерность")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Embeddings содержит NaN/inf")
    norm=math.sqrt(sum(float(v)*float(v) for v in values))
    if norm < 1e-9: raise ValueError("Нулевой вектор embeddings")
    return struct.pack("<"+str(len(values))+"f",*(float(x)/norm for x in values))


def save_embeddings(data_dir: Path, model_id: str, items: list[dict],
                    vectors: list[list[float]]) -> int:
    """Cache immutable model/digest-qualified vectors; reject stale index sources."""
    if len(items)!=len(vectors):
        raise ValueError("Количество ответов embeddings не совпало с запросом")
    path=data_dir/"search.sqlite3"
    if not path.is_file() or path.is_symlink(): return 0
    saved=0
    with _open(path) as conn:
        for item,vector in zip(items,vectors):
            sid,digest=item["source_id"],item["digest"]
            row=conn.execute("SELECT digest FROM search_manifest WHERE source_id=?",(sid,)).fetchone()
            if not row or row["digest"]!=digest: continue
            blob=_vector_blob(vector)
            conn.execute("""INSERT INTO search_embeddings
               (source_id,model_id,digest,dimension,vector) VALUES(?,?,?,?,?)
               ON CONFLICT(source_id,model_id) DO UPDATE SET
                 digest=excluded.digest,dimension=excluded.dimension,vector=excluded.vector""",
                 (sid,model_id,digest,len(vector),blob))
            saved+=1
    return saved


def semantic_snippets(data_dir: Path, project_id: str, job_id: str,
                      scene_index: int, model_id: str, query_vector: list[float],
                      limit: int = 4) -> list[dict]:
    """Brute-force cosine on a single author's small SQLite corpus.

    Every candidate is filtered by PROJECT, main branch, chapter timeline,
    original source, *current job* and current digest BEFORE similarity ranking.
    No ANN extension required: ~1000 chunks * 768 float32 is fine in RAM.
    """
    path=data_dir/"search.sqlite3"
    if not path.is_file() or path.is_symlink(): return []
    qblob=_vector_blob(query_vector)
    dim=len(query_vector)
    q=struct.unpack("<"+str(dim)+"f",qblob)
    with _open(path) as conn:
        rows=conn.execute("""
          SELECT m.source_id,m.scene_index,m.source_kind,m.revision_id,
                 m.digest,substr(m.text,1,850) AS excerpt,e.vector
            FROM search_manifest m
            JOIN search_embeddings e ON e.source_id=m.source_id
             AND e.digest=m.digest AND e.model_id=? AND e.dimension=?
           WHERE m.project_id=? AND m.branch_id='main' AND m.scene_index<=?
             AND ((m.revision_id='author' AND m.source_kind IN ('approved','synopsis'))
                OR (m.revision_id=? AND m.source_kind='draft'))
           LIMIT 4000""",(model_id,dim,project_id,scene_index,job_id)).fetchall()
    ranked=[]
    for row in rows:
        if len(row["vector"])!=dim*4:continue
        v=struct.unpack("<"+str(dim)+"f",row["vector"])
        score=sum(a*b for a,b in zip(q,v))
        record={key:row[key] for key in ("source_id","scene_index","source_kind",
                                         "revision_id","digest","excerpt")}
        record["similarity"]=round(score,4)
        ranked.append(record)
    ranked.sort(key=lambda v:v["similarity"],reverse=True)
    return ranked[:max(1,min(10,limit))]


def checkpoint_story_state(data_dir: Path, project_id: str, job_id: str,
                           scene_id: str, scene_index: int,
                           text: str, current_goal: str) -> None:
    """A provenance-checked rolling location, NOT model-invented world facts."""
    paragraphs=[part.strip() for part in text.split("\n\n") if part.strip()]
    ending="\n\n".join(paragraphs[-3:])[-2200:]
    digest=hashlib.sha256(text.encode("utf-8")).hexdigest()
    with _open(data_dir/"work.sqlite3") as db:
        db.execute("""INSERT INTO work_story_state
          (job_id,scene_id,project_id,scene_index,source_digest,ending,current_goal,words,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?)
          ON CONFLICT(job_id,scene_id) DO UPDATE SET
            source_digest=excluded.source_digest,ending=excluded.ending,
            current_goal=excluded.current_goal,words=excluded.words,
            updated_at=excluded.updated_at""",
          (job_id,scene_id,project_id,scene_index,digest,ending[:2200],
           current_goal[:500],len(text.split()),utc()))


def read_story_state(data_dir: Path, project_id: str, job_id: str,
                     scene_id: str, current_text: str) -> dict | None:
    path=data_dir/"work.sqlite3"
    if not path.is_file() or path.is_symlink(): return None
    with _open(path) as db:
        row=db.execute("SELECT * FROM work_story_state WHERE project_id=? "
                       "AND job_id=? AND scene_id=?",(project_id,job_id,scene_id)).fetchone()
    if row and row["source_digest"]==hashlib.sha256(current_text.encode("utf-8")).hexdigest():
        return dict(row)
    return None


def vector_index_status(data_dir: Path, project_id: str | None = None) -> dict:
    """Actual vector counts and last-known model, never claim semantic RAG is active by schema alone."""
    path=data_dir/"search.sqlite3"
    if not path.is_file() or path.is_symlink():
        return {"stored_vectors":0,"sources":0,"models":[]}
    with _open(path) as db:
        conditions=" WHERE project_id=?" if project_id else ""
        args=(project_id,) if project_id else ()
        count=db.execute("SELECT count(*) FROM search_manifest"+conditions,args).fetchone()[0]
        rows=db.execute("""SELECT e.model_id,count(*) AS n FROM search_embeddings e
             JOIN search_manifest m ON m.source_id=e.source_id AND m.digest=e.digest"""
             +(" WHERE m.project_id=?" if project_id else "")+
             " GROUP BY e.model_id ORDER BY e.model_id",args).fetchall()
    return {"stored_vectors":sum(row["n"] for row in rows),"sources":count,
            "models":[{"name":row["model_id"],"vectors":row["n"]} for row in rows]}


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
