"""Step 14 APIs: review measured PDF fit, explicitly accept to canonical, safe undo.

Reuses existing PDF exporter and project database; no alternate writing worker.
Every state change is guarded inside a BEGIN IMMEDIATE SQLite transaction.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field

from semantic_page_fit import get as suggestion_get, FitProposalConflict
from page_fit_editorial_acceptance import prepare,read,accept,undo
from publishing_exports import manuscript_pdf
from print_proof_preflight import inspect_pdf


class ReviewCreate(BaseModel):
    suggestion_id: str=Field(pattern=r"^[0-9a-f]{32}$")


class ReviewDecision(BaseModel):
    confirm_sources: bool
    confirm_pagination: bool
    expected_preview_digest: str=Field(pattern=r"^[0-9a-f]{64}$")


def install(api:APIRouter,namespace:dict):
    def database():
        return namespace["db"]()

    def get_project(project_id):
        return namespace["get_project"](project_id)

    def _proof(project,illustrations):
        pdf=manuscript_pdf(project,print_proof=True,illustrations=illustrations)
        titles=[scene.title for scene in project.scenes if scene.text.strip()]
        return inspect_pdf(pdf,chapter_titles=titles,
            edition_label=getattr(project,"edition_label","") or None)

    @api.post("/projects/{project_id}/print-proof/fit-reviews",status_code=201)
    def create(project_id:str,body:ReviewCreate):
        p=get_project(project_id)
        with database() as db:
            try:
                source=suggestion_get(db,project_id=project_id,suggestion_id=body.suggestion_id)
            except ValueError as exc:
                raise HTTPException(404,str(exc)) from exc
        scene=next((x for x in p.scenes if x.id==source["scene_id"]),None)
        if scene is None or source["status"]!="proposed":
            raise HTTPException(409,"The candidate was rejected or its chapter is gone")
        from semantic_page_fit import digest
        if digest(scene.text)!=source["source_digest"] or digest(p.model_dump_json())!=source["base_project_digest"]:
            raise HTTPException(409,"Project changed; regenerate the proposal")
        proposed=deepcopy(p)
        revised=next(x for x in proposed.scenes if x.id==scene.id)
        revised.text=scene.text.rstrip()+"\n\n"+source["paragraph"]
        if len(source["paragraph"].split())>180:
            raise HTTPException(422,"Exceeded 180-word editorial limit")
        try:
            art=namespace["illustrations_for_book"](project_id)
            before=_proof(p,art)
            after=_proof(proposed,art)
        except (RuntimeError,ValueError) as exc:
            raise HTTPException(422,"Unable to render both PDF proofs: "+str(exc)) from exc
        earlier={x["title"]:x for x in before["headings"]}
        moved=[]
        for heading in after["headings"]:
            old=earlier.get(heading["title"])
            if old and old["actual_pdf_page"]!=heading["actual_pdf_page"]:
                moved.append({"title":heading["title"],
                   "before":old["actual_pdf_page"],"after":heading["actual_pdf_page"]})
        preview={"source_project_id":project_id,"scene_id":scene.id,
            "source_hash":sha256(p.model_dump_json().encode()).hexdigest(),
            "saved":False,"fact_check":"not_verified",
            "before":{"pages":before["page_count"],"findings":before["findings"]},
            "after":{"pages":after["page_count"],"findings":after["findings"]},
            "page_delta":after["page_count"]-before["page_count"],
            "moved_headings":moved,"candidate_pdf_sha256":after["pdf_sha256"]}
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                result=prepare(db,project_id=project_id,suggestion_id=body.suggestion_id,
                    Project=namespace["Project"],preview=preview)
                review=read(db,project_id=project_id,review_id=result["review_id"])
            except (ValueError,FitProposalConflict) as exc:
                raise HTTPException(409,str(exc)) from exc
        return {**result,"preview_digest":review["preview_digest"],
            "preview_sha256":after["pdf_sha256"],"toc_needs_update":True,
            "warnings":after["findings"],"author_fact_check_required":True}

    @api.get("/projects/{project_id}/print-proof/fit-reviews/{review_id}")
    def get_review(project_id:str,review_id:str):
        get_project(project_id)
        try:
            with database() as db:
                return read(db,project_id=project_id,review_id=review_id)
        except ValueError as exc:
            raise HTTPException(404,str(exc)) from exc

    @api.post("/projects/{project_id}/print-proof/fit-reviews/{review_id}/accept")
    def accept_review(project_id:str,review_id:str,body:ReviewDecision):
        from generation_core import active_job
        get_project(project_id)
        try:
            with database() as db:
                db.execute("BEGIN IMMEDIATE")
                result=accept(db,project_id=project_id,review_id=review_id,
                    Project=namespace["Project"],
                    confirm_sources=body.confirm_sources,
                    confirm_pagination=body.confirm_pagination,
                    expected_preview_digest=body.expected_preview_digest,
                    has_active_job=active_job)
        except (ValueError,FitProposalConflict) as exc:
            raise HTTPException(409,str(exc)) from exc
        # Rebuild after the committed edit, without silently approving release.
        p=get_project(project_id)
        try:
            proof=_proof(p,namespace["illustrations_for_book"](project_id))
            result["final_pdf_preflight"]={
                "pages":proof["page_count"],"pdf_sha256":proof["pdf_sha256"],
                "ready":proof["ready"],"requires_editor_review":proof["requires_editor_review"],
                "findings":proof["findings"]}
            result["release_ready"]=bool(proof["ready"] and not proof["requires_editor_review"])
        except (ValueError,RuntimeError) as exc:
            result["final_pdf_preflight"]={"error":str(exc)}
            result["release_ready"]=False
        result["docx_toc_update_required"]=True
        return result

    @api.post("/projects/{project_id}/print-proof/fit-reviews/{review_id}/undo")
    def undo_review(project_id:str,review_id:str):
        from generation_core import active_job
        get_project(project_id)
        try:
            with database() as db:
                db.execute("BEGIN IMMEDIATE")
                result=undo(db,project_id=project_id,review_id=review_id,
                    Project=namespace["Project"],has_active_job=active_job)
        except (ValueError,FitProposalConflict) as exc:
            raise HTTPException(409,str(exc)) from exc
        return {**result,"docx_toc_update_required":True,
                "release_ready":False}
