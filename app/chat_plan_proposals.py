"""Reviewable chat changes to the *existing* Studio brief, outline and book plan.

All canonical writes (projects + ai_book_plans + proposal status) share a
single BEGIN IMMEDIATE transaction on achewiw.sqlite3. No chapter prose,
story graph, RAG index or generated book job is overwritten.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from uuid import uuid4

from chapter_planning import get_plan, validate_plan, store_plan

BRIEF_FIELDS=("premise","atmosphere","audience","twist","reveal","anchors")
SCENE_FIELDS=("title","anchor","hook")
PLAN_FIELDS=("target_words","tension","reveal_level","editorial_goal")


class PlanConflict(ValueError):
    pass


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def initialize(db: sqlite3.Connection):
    db.execute("""CREATE TABLE IF NOT EXISTS ai_chat_plan_proposals(
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        chat_proposal_id TEXT NOT NULL UNIQUE,
        base_digest TEXT NOT NULL,
        candidate_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('proposed','accepted','rejected')),
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_chat_plan_proposals_project
       ON ai_chat_plan_proposals(project_id,thread_id,status,created_at)""")


def _load(db: sqlite3.Connection, project_id: str, project_type):
    row=db.execute("SELECT data,updated_at FROM projects WHERE id=?",(project_id,)).fetchone()
    if not row:
        raise ValueError("Project not found")
    project=project_type.model_validate_json(row["data"])
    return project,row


def _baseline(db, project, row):
    saved=db.execute("""SELECT plan_json,target_words,scene_ids FROM ai_book_plans
        WHERE project_id=?""",(project.id,)).fetchone()
    plan=get_plan(db,project)
    payload={"project_data":row["data"],
             "plan_row":list(saved) if saved else None}
    return plan,_hash(_json(payload))


def _validate_candidate(project, current_plan, candidate):
    if not isinstance(candidate,dict) or set(candidate)!={"brief","chapters"}:
        raise ValueError("Expected brief and chapters only")
    brief=candidate["brief"]
    if not isinstance(brief,dict) or set(brief)!=set(BRIEF_FIELDS):
        raise ValueError("Brief must contain only the six editable fields")
    limits={"premise":10000,"atmosphere":2000,"audience":1000,
            "twist":4000,"reveal":2000,"anchors":4000}
    for key,value in brief.items():
        if not isinstance(value,str) or len(value)>limits[key]:
            raise ValueError("Invalid brief field: "+key)
    given=candidate["chapters"]
    if not isinstance(given,list) or len(given)!=len(project.scenes) or not given:
        raise ValueError("Chapter count must match the existing outline")
    clone=project.model_copy(deep=True)
    for field,value in brief.items():
        setattr(clone,field,value)
    rows=[]
    for index,(item,scene) in enumerate(zip(given,clone.scenes)):
        fields={"scene_id",*SCENE_FIELDS,*PLAN_FIELDS}
        if not isinstance(item,dict) or set(item)!=fields or item["scene_id"]!=scene.id:
            raise ValueError("Chapter identifiers/order or field set changed")
        for key,max_length in [("title",200),("anchor",2000),("hook",2000)]:
            if not isinstance(item[key],str) or not item[key].strip() and key=="title" or len(item[key])>max_length:
                raise ValueError("Invalid chapter "+key)
            setattr(scene,key,item[key])
        rows.append({k:item[k] for k in ("scene_id",*PLAN_FIELDS)})
    clean=validate_plan(clone,rows)
    return clone,clean


def snapshot(db,project_id,project_type):
    project,row=_load(db,project_id,project_type)
    plan,digest=_baseline(db,project,row)
    chapters=[]
    for scene,unit in zip(project.scenes,plan["chapters"]):
        chapters.append({"scene_id":scene.id,**{key:getattr(scene,key) for key in SCENE_FIELDS},
                         **{key:unit[key] for key in PLAN_FIELDS}})
    return {"project_id":project_id,"base_digest":digest,
            "candidate":{"brief":{key:getattr(project,key) for key in BRIEF_FIELDS},
                         "chapters":chapters},
            "target_words":plan["target_words"],
            "plan_source":plan["source"],
            "existing_scene_ids":[s.id for s in project.scenes]}


def create(db,*,project_id,thread_id,chat_proposal_id,candidate,project_type):
    initialize(db)
    if not isinstance(chat_proposal_id,str) or len(chat_proposal_id)!=32:
        raise ValueError("Missing saved chat proposal")
    baseline=snapshot(db,project_id,project_type)
    original_project,_=_load(db,project_id,project_type)
    _,validated=_validate_candidate(original_project,None,candidate)
    if candidate==baseline["candidate"]:
        raise ValueError("Proposed outline has no changes")
    old=db.execute("SELECT id FROM ai_chat_plan_proposals WHERE chat_proposal_id=?",
                   (chat_proposal_id,)).fetchone()
    if old:raise PlanConflict("This chat instruction already has a proposed outline")
    ident=uuid4().hex
    stamp=_now()
    db.execute("""INSERT INTO ai_chat_plan_proposals
      (id,project_id,thread_id,chat_proposal_id,base_digest,candidate_json,
       status,created_at,updated_at) VALUES(?,?,?,?,?,?,'proposed',?,?)""",
       (ident,project_id,thread_id,chat_proposal_id,baseline["base_digest"],
        _json(candidate),stamp,stamp))
    return {"id":ident,"status":"proposed","base_digest":baseline["base_digest"],
            "before":baseline["candidate"],"after":candidate,
            "target_words":validated["target_words"],"applied":False}


def get(db,*,project_id,thread_id,proposal_id,project_type):
    initialize(db)
    row=db.execute("""SELECT * FROM ai_chat_plan_proposals
       WHERE id=? AND project_id=? AND thread_id=?""",
       (proposal_id,project_id,thread_id)).fetchone()
    if row is None:raise ValueError("Plan proposal not in this chat")
    baseline=snapshot(db,project_id,project_type)
    return {"id":proposal_id,"status":row["status"],
            "base_digest":row["base_digest"],
            "current_digest":baseline["base_digest"],
            "source_unchanged":row["base_digest"]==baseline["base_digest"],
            "current":baseline["candidate"],"after":json.loads(row["candidate_json"]),
            "applied":row["status"]=="accepted"}


def latest(db,*,project_id,thread_id,project_type):
    initialize(db)
    row=db.execute("""SELECT id FROM ai_chat_plan_proposals
       WHERE project_id=? AND thread_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
       (project_id,thread_id)).fetchone()
    return get(db,project_id=project_id,thread_id=thread_id,
               proposal_id=row["id"],project_type=project_type) if row else None


def decide(db,*,project_id,thread_id,proposal_id,approve,project_type,
           has_pending_book):
    if type(approve) is not bool:
        raise ValueError("Explicit author decision required")
    initialize(db)
    row=db.execute("""SELECT * FROM ai_chat_plan_proposals
       WHERE id=? AND project_id=? AND thread_id=?""",
       (proposal_id,project_id,thread_id)).fetchone()
    if row is None or row["status"]!="proposed":
        raise PlanConflict("Proposal missing or already decided")
    if not approve:
        db.execute("""UPDATE ai_chat_plan_proposals SET status='rejected',updated_at=?
                     WHERE id=? AND status='proposed'""",(_now(),proposal_id))
        return {"status":"rejected","changed":False}
    if has_pending_book(db,project_id):
        raise PlanConflict("Finish or archive the existing book job before changing its plan")
    project,source_row=_load(db,project_id,project_type)
    _,digest=_baseline(db,project,source_row)
    if digest!=row["base_digest"]:
        raise PlanConflict("Project or chapter plan changed; refresh the proposal")
    candidate=json.loads(row["candidate_json"])
    edited,validated=_validate_candidate(project,None,candidate)
    edited.updated_at=_now()
    db.execute("UPDATE projects SET data=?,updated_at=? WHERE id=?",
               (edited.model_dump_json(),edited.updated_at,project_id))
    store_plan(db,edited,validated["chapters"])
    db.execute("""UPDATE ai_chat_plan_proposals SET status='accepted',updated_at=?
                WHERE id=? AND status='proposed'""",(_now(),proposal_id))
    return {"status":"accepted","changed":True,"project_id":project_id,
            "chapters":len(edited.scenes),"target_words":validated["target_words"],
            "project_updated_at":edited.updated_at}
