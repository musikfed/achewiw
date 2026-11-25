"""Step 14: transactional editorial acceptance of page-fit proposals.

Distinct from LLM proposal generation. Requires evidence acknowledgment and a
measured preview. Stores before/after project snapshots in canonical SQLite
for safe compare-and-swap undo; never alters DOCWORK or unpublished jobs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4
import json

from semantic_page_fit import digest, get as source_proposal, FitProposalConflict


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(",",":"))


def _project(db, project_id, Project):
    row=db.execute("SELECT data FROM projects WHERE id=?",(project_id,)).fetchone()
    if row is None:
        raise ValueError("Project not found")
    return Project.model_validate_json(row["data"]),row["data"]


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS ai_page_fit_reviews (
       id TEXT PRIMARY KEY,
       project_id TEXT NOT NULL,
       suggestion_id TEXT NOT NULL UNIQUE,
       scene_id TEXT NOT NULL,
       base_project_digest TEXT NOT NULL,
       reviewed_text TEXT NOT NULL,
       preview_json TEXT NOT NULL,
       preview_digest TEXT NOT NULL,
       status TEXT NOT NULL CHECK(status IN ('prepared','accepted','undone')),
       before_json TEXT,
       after_json TEXT,
       accepted_at TEXT,
       undone_at TEXT,
       created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_fit_reviews_project
       ON ai_page_fit_reviews(project_id,created_at)""")


def _data(project):
    return project.model_dump(mode="json")


def _find_scene(project, scene_id):
    scene=next((s for s in project.scenes if s.id==scene_id),None)
    if scene is None:raise FitProposalConflict("Target chapter was deleted")
    return scene


def prepare(db,*,project_id,suggestion_id,Project,preview):
    """Persist exact page-shift preview only if candidate and source remain intact."""
    initialize(db)
    proposal=source_proposal(db,project_id=project_id,suggestion_id=suggestion_id)
    if proposal["status"]!="proposed":
        raise FitProposalConflict("Suggestion was rejected")
    p,raw=_project(db,project_id,Project)
    if digest(p.model_dump_json())!=proposal["base_project_digest"]:
        raise FitProposalConflict("Project changed since suggestion was generated")
    scene=_find_scene(p,proposal["scene_id"])
    if digest(scene.text)!=proposal["source_digest"]:
        raise FitProposalConflict("Chapter changed since suggestion was generated")
    if not isinstance(preview,dict) or preview.get("source_project_id")!=project_id or preview.get("scene_id")!=scene.id or preview.get("saved") is not False:
        raise FitProposalConflict("Measured PDF preview is missing or not read-only")
    if preview.get("source_hash")!=sha256(p.model_dump_json().encode()).hexdigest():
        raise FitProposalConflict("PDF preview was rendered from a different project")
    if preview.get("fact_check")!="not_verified":
        raise FitProposalConflict("Unexpected provenance marker")
    if not isinstance(preview.get("before"),dict) or not isinstance(preview.get("after"),dict):
        raise FitProposalConflict("Missing before/after PDF measurements")
    if not isinstance(preview.get("moved_headings"),list):
        raise FitProposalConflict("Missing chapter pagination comparisons")
    candidate=scene.text.rstrip()+"\n\n"+proposal["paragraph"]
    if len(proposal["paragraph"].split())>180 or proposal["paragraph"] in scene.text:
        raise FitProposalConflict("Repeated or oversized candidate")
    stored=db.execute("SELECT id FROM ai_page_fit_reviews WHERE suggestion_id=?",(suggestion_id,)).fetchone()
    if stored:raise FitProposalConflict("This candidate already has an editorial review")
    uid=uuid4().hex
    db.execute("""INSERT INTO ai_page_fit_reviews
       (id,project_id,suggestion_id,scene_id,base_project_digest,reviewed_text,
        preview_json,preview_digest,status,created_at)
       VALUES(?,?,?,?,?,?,?,?,'prepared',?)""",
       (uid,project_id,suggestion_id,scene.id,digest(p.model_dump_json()),
        candidate,_json(preview),digest(_json(preview)),_now()))
    return {"review_id":uid,"status":"prepared","scene_id":scene.id,
        "before_text":scene.text,"candidate_text":candidate,
        "paragraph":proposal["paragraph"],"evidence":proposal["evidence"],
        "page_delta":preview["page_delta"],
        "moved_headings":preview["moved_headings"],
        "before_pages":preview["before"]["pages"],"after_pages":preview["after"]["pages"],
        "source_verification_required":True,"requires_final_pdf_preflight":True,
        "canonical_changed":False}


def read(db,*,project_id,review_id):
    initialize(db)
    row=db.execute("SELECT * FROM ai_page_fit_reviews WHERE id=? AND project_id=?",
                   (review_id,project_id)).fetchone()
    if row is None:raise ValueError("No review for this project")
    return {"review_id":row["id"],"status":row["status"],"scene_id":row["scene_id"],
            "preview":json.loads(row["preview_json"]),
            "preview_digest":row["preview_digest"],
            "candidate_text":row["reviewed_text"],
            "source_verification_required":True,
            "undo_available":row["status"]=="accepted"}


def accept(db,*,project_id,review_id,Project,confirm_sources,confirm_pagination,
           expected_preview_digest,has_active_job):
    """Must be called within caller-owned BEGIN IMMEDIATE transaction."""
    if confirm_sources is not True or confirm_pagination is not True:
        raise FitProposalConflict("Author must review sources and page changes explicitly")
    initialize(db)
    row=db.execute("SELECT * FROM ai_page_fit_reviews WHERE id=? AND project_id=?",
                   (review_id,project_id)).fetchone()
    if row is None or row["status"]!="prepared":
        raise FitProposalConflict("Review is absent or already decided")
    if row["preview_digest"]!=expected_preview_digest:
        raise FitProposalConflict("The displayed pagination preview changed")
    proposal=source_proposal(db,project_id=project_id,suggestion_id=row["suggestion_id"])
    if proposal["status"]!="proposed":
        raise FitProposalConflict("Original suggestion was rejected")
    if has_active_job(db):
        raise FitProposalConflict("Finish or pause active book generation before manual edits")
    project,old_raw=_project(db,project_id,Project)
    if digest(project.model_dump_json())!=row["base_project_digest"] or (
        digest(project.model_dump_json())!=proposal["base_project_digest"]):
        raise FitProposalConflict("Canonical project changed; regenerate the PDF preview")
    scene=_find_scene(project,row["scene_id"])
    if digest(scene.text)!=proposal["source_digest"]:
        raise FitProposalConflict("Original chapter no longer matches")
    if row["reviewed_text"]!=scene.text.rstrip()+"\n\n"+proposal["paragraph"]:
        raise FitProposalConflict("Proposed chapter and reviewed text differ")
    scene.text=row["reviewed_text"]
    project.updated_at=_now()
    after=project.model_dump_json()
    db.execute("UPDATE projects SET data=?,updated_at=? WHERE id=?",
               (after,project.updated_at,project_id))
    db.execute("""UPDATE ai_page_fit_reviews
        SET status='accepted',before_json=?,after_json=?,accepted_at=?
        WHERE id=? AND project_id=? AND status='prepared'""",
        (old_raw,after,_now(),review_id,project_id))
    return {"status":"accepted","review_id":review_id,"project_id":project_id,
        "scene_id":scene.id,"previous_digest":digest(old_raw),
        "new_digest":digest(after),"text_changed":True,
        "requires_final_pdf_preflight":True,"requires_toc_update":True,
        "fact_review_acknowledged":True}


def undo(db,*,project_id,review_id,Project,has_active_job):
    """CAS restore of precisely the earlier canonical project, not an unguarded undo."""
    initialize(db)
    row=db.execute("SELECT * FROM ai_page_fit_reviews WHERE id=? AND project_id=?",
                   (review_id,project_id)).fetchone()
    if row is None or row["status"]!="accepted":
        raise FitProposalConflict("No accepted edit to undo")
    if has_active_job(db):
        raise FitProposalConflict("Cannot undo during book generation")
    project,current_raw=_project(db,project_id,Project)
    if _data(project)!=json.loads(row["after_json"]):
        raise FitProposalConflict("Another edit happened after acceptance; manual conflict resolution required")
    restored=Project.model_validate_json(row["before_json"])
    restored.updated_at=_now()
    db.execute("UPDATE projects SET data=?,updated_at=? WHERE id=?",
               (restored.model_dump_json(),restored.updated_at,project_id))
    db.execute("UPDATE ai_page_fit_reviews SET status='undone',undone_at=? WHERE id=?",
               (_now(),review_id))
    return {"status":"undone","review_id":review_id,
            "text_restored":True,"requires_final_pdf_preflight":True}
