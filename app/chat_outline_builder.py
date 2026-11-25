"""Staged, reviewable first outline for an empty ACHEWIW project.

Uses existing Project/Scene, chapter_planning and the canonical projects table.
A batch is capped at twelve chapters to keep local Ollama context bounded.
No canonical mutation until explicit final acceptance.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from book_generation_core import MIN_BOOK_WORDS, MAX_BOOK_WORDS, MAX_CHAPTER_WORDS
from chapter_planning import store_plan, suggested_plan

BATCH_SIZE=12
MAX_CHAPTERS=100

class OutlineConflict(ValueError):
    pass

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

def _digest(data):
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def _encode(data):
    return json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS ai_chat_outline_builds(
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, thread_id TEXT NOT NULL,
      message_id TEXT NOT NULL, base_sha256 TEXT NOT NULL,
      total_words INTEGER NOT NULL, count INTEGER NOT NULL,
      chapters_json TEXT NOT NULL, status TEXT NOT NULL
        CHECK(status IN ('draft','ready','accepted','rejected')),
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_chat_outline_builds
      ON ai_chat_outline_builds(project_id,thread_id,created_at)""")

def _project(db,pid,Project):
    row=db.execute("SELECT data FROM projects WHERE id=?",(pid,)).fetchone()
    if row is None:raise ValueError("Project not found")
    return Project.model_validate_json(row["data"]),row["data"]

def _row(db,pid,tid,bid):
    row=db.execute("""SELECT * FROM ai_chat_outline_builds
      WHERE id=? AND project_id=? AND thread_id=?""",(bid,pid,tid)).fetchone()
    if not row:raise OutlineConflict("No matching outline build in this chat")
    return row

def _words(total,count):
    if type(total) is not int or not MIN_BOOK_WORDS<=total<=MAX_BOOK_WORDS:
        raise ValueError("Book word count out of limits")
    if type(count) is not int or not 1<=count<=MAX_CHAPTERS:
        raise ValueError("Chapter count must be from 1 to 100")
    if total<150*count or total>MAX_CHAPTER_WORDS*count:
        raise ValueError("Chapter count does not support requested book length")

def begin(db,*,project_id,thread_id,message_id,total_words,count,Project):
    initialize(db)
    _words(total_words,count)
    project,raw=_project(db,project_id,Project)
    if project.scenes:
        raise OutlineConflict("This builder is for an empty project only")
    active=db.execute("""SELECT id FROM ai_chat_outline_builds
      WHERE project_id=? AND status IN ('draft','ready') LIMIT 1""",(project_id,)).fetchone()
    if active:raise OutlineConflict("Resolve the previous outline proposal first")
    now=_now()
    ident=uuid4().hex
    db.execute("""INSERT INTO ai_chat_outline_builds
      (id,project_id,thread_id,message_id,base_sha256,total_words,count,
      chapters_json,status,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,'[]','draft',?,?)""",
      (ident,project_id,thread_id,message_id,_digest(raw),total_words,count,now,now))
    return state(db,project_id=project_id,thread_id=thread_id,build_id=ident)

def state(db,*,project_id,thread_id,build_id):
    row=_row(db,project_id,thread_id,build_id)
    chapters=json.loads(row["chapters_json"])
    return {"id":row["id"],"status":row["status"],"count":row["count"],
        "total_words":row["total_words"],"chapters":chapters,
        "created":len(chapters),"remaining":row["count"]-len(chapters),
        "next_batch_size":min(BATCH_SIZE,row["count"]-len(chapters)),
        "applied":row["status"]=="accepted"}

def _clean(items,expected):
    if not isinstance(items,list) or len(items)!=expected:raise ValueError("Wrong batch size")
    clean=[]
    for item in items:
        if not isinstance(item,dict) or set(item)!={"title","anchor","hook","editorial_goal"}:
            raise ValueError("Only chapter title/anchor/hook/editorial_goal allowed")
        limits={"title":200,"anchor":2000,"hook":2000,"editorial_goal":500}
        for key,max_len in limits.items():
            value=item[key]
            if not isinstance(value,str) or (key in ("title","editorial_goal") and not value.strip()) or len(value)>max_len:
                raise ValueError("Invalid chapter "+key)
        clean.append({k:item[k].strip() for k in limits})
    if len(set(item["title"].casefold() for item in clean))!=len(clean):
        raise ValueError("Duplicate chapter titles in batch")
    return clean

def append(db,*,project_id,thread_id,build_id,items,Project):
    row=_row(db,project_id,thread_id,build_id)
    if row["status"]!="draft":raise OutlineConflict("Build is not accepting chapters")
    project,raw=_project(db,project_id,Project)
    if project.scenes or _digest(raw)!=row["base_sha256"]:
        raise OutlineConflict("Project changed since the outline was started")
    chapters=json.loads(row["chapters_json"])
    expected=min(BATCH_SIZE,row["count"]-len(chapters))
    if expected<=0:raise OutlineConflict("All chapters have been proposed")
    new=_clean(items,expected)
    titles={x["title"].casefold() for x in chapters}
    if any(x["title"].casefold() in titles for x in new):
        raise ValueError("Duplicate chapter title across batches")
    chapters.extend(new)
    status="ready" if len(chapters)==row["count"] else "draft"
    db.execute("""UPDATE ai_chat_outline_builds SET chapters_json=?,status=?,updated_at=?
       WHERE id=? AND status='draft'""",(_encode(chapters),status,_now(),build_id))
    return state(db,project_id=project_id,thread_id=thread_id,build_id=build_id)

def decide(db,*,project_id,thread_id,build_id,approve,Project,Scene,has_pending_book):
    if type(approve) is not bool:raise ValueError("Explicit boolean decision required")
    row=_row(db,project_id,thread_id,build_id)
    if row["status"] not in ("draft","ready"):
        raise OutlineConflict("Outline already decided")
    if not approve:
        db.execute("UPDATE ai_chat_outline_builds SET status='rejected',updated_at=? WHERE id=?",
                   (_now(),build_id))
        return {"status":"rejected","changed":False}
    if row["status"]!="ready":
        raise OutlineConflict("Complete every chapter batch before approval")
    if has_pending_book(db,project_id):
        raise OutlineConflict("Existing unfinished book job blocks outline changes")
    project,raw=_project(db,project_id,Project)
    if project.scenes or _digest(raw)!=row["base_sha256"]:
        raise OutlineConflict("Project changed since proposal")
    chapters=json.loads(row["chapters_json"])
    _words(row["total_words"],len(chapters))
    project.target_length=f'{row["total_words"]} слов'
    project.scenes=[Scene(id=uuid4().hex,title=c["title"],anchor=c["anchor"],
                     hook=c["hook"]) for c in chapters]
    proposed=suggested_plan(project)
    for planned,chapter in zip(proposed["chapters"],chapters):
        planned["editorial_goal"]=chapter["editorial_goal"]
    # Single transaction with canonical plan. If validation fails no partial write.
    project.updated_at=_now()
    db.execute("UPDATE projects SET data=?,updated_at=? WHERE id=?",
               (project.model_dump_json(),project.updated_at,project_id))
    store_plan(db,project,proposed["chapters"])
    db.execute("""UPDATE ai_chat_outline_builds SET status='accepted',updated_at=?
      WHERE id=?""",(_now(),build_id))
    return {"status":"accepted","changed":True,"project_id":project_id,
            "count":len(chapters),"target_words":proposed["target_words"]}
