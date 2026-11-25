"""Step 13 routes: short evidence-linked PDF fit proposal, no second book engine."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel,Field
from page_fit_advisor import PageEvidence,recommend
from print_proof_preflight import inspect_pdf
from story_memory import retrieve as story_retrieve
from gpu_scheduling import reserve_gpu,release_gpu,image_operation_active
from generation_core import active_job
from semantic_page_fit import (collect_evidence,propose_with_ollama,
                               save,digest,get,reject,FitProposalConflict)
from publishing_exports import manuscript_pdf
import uuid


class GroundedRequest(BaseModel):
    scene_id: str=Field(min_length=1,max_length=160)
    page_number: int=Field(ge=1,le=1200)
    model: str=Field(min_length=1,max_length=200)
    instruction: str=Field(default="",max_length=1200)


def install(api: APIRouter, namespace:dict):
    def project(pid):
        return namespace["get_project"](pid)

    def database():
        return namespace["db"]()

    @api.post("/projects/{project_id}/print-proof/grounded-fit",status_code=201)
    async def propose(project_id:str,request:GroundedRequest):
        current=project(project_id)
        index=next((i for i,s in enumerate(current.scenes)
                    if s.id==request.scene_id and s.text.strip()),None)
        if index is None:
            raise HTTPException(409,"Select a written chapter")
        scene=current.scenes[index]
        try:
            images=namespace["illustrations_for_book"](project_id)
            proof=inspect_pdf(manuscript_pdf(current,print_proof=True,illustrations=images),
                 chapter_titles=[s.title for s in current.scenes if s.text.strip()])
            if request.page_number>proof["page_count"]:
                raise HTTPException(422,"Page is outside this PDF proof")
            line=proof["pages"][request.page_number-1]
            matched=next((h for h in proof["headings"] if h["title"]==scene.title),None)
            if not matched or matched["actual_pdf_page"] is None or request.page_number<matched["actual_pdf_page"]:
                raise HTTPException(409,"Page precedes the chosen chapter")
            later=[h["actual_pdf_page"] for h in proof["headings"]
                   if h["title"]!=scene.title and h["actual_pdf_page"] and
                   h["actual_pdf_page"]>matched["actual_pdf_page"]]
            if later and request.page_number>=min(later):
                raise HTTPException(409,"Page belongs to another chapter")
            next_boundary=bool(later and request.page_number+1==min(later))
            decision=recommend(PageEvidence(request.page_number,
                line["occupied_ratio"],next_page_starts_part=next_boundary,
                justified_large_word_spaces=line["wide_spaces_detected"]))
            if decision["action"]!="offer_semantic_expansion":
                raise HTTPException(409,"Text expansion is inappropriate: "+decision["action"])
        except (ValueError,RuntimeError) as exc:
            raise HTTPException(422,str(exc)) from exc

        query=(request.instruction+" "+scene.title+" "+scene.text[-400:]).strip()
        try:
            with database() as db:
                memory=story_retrieve(db,project_id,"author",index,query)
        except Exception as exc:
            raise HTTPException(409,"Book RAG/Graph evidence unavailable") from exc
        evidence=collect_evidence(memory,scene.id)
        if not evidence:
            raise HTTPException(409,"No author-approved evidence; cannot invent an expansion")
        lease=uuid.uuid4().hex
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            if active_job(db) or image_operation_active(db) or not reserve_gpu(db,lease,"semantic_page_fit"):
                raise HTTPException(409,"Shared ACHEWIW GPU busy")
        try:
            result=await propose_with_ollama(base_url=namespace["OLLAMA_BASE_URL"],
                model=request.model,original=scene.text,
                instruction=request.instruction,evidence=evidence)
        except (FitProposalConflict,ValueError) as exc:
            raise HTTPException(422,str(exc)) from exc
        finally:
            with database() as db:
                release_gpu(db,lease)

        updated=project(project_id)
        updated_scene=next((s for s in updated.scenes if s.id==scene.id),None)
        if updated_scene is None or digest(updated.model_dump_json())!=digest(current.model_dump_json()):
            raise HTTPException(409,"The source project changed during inference")
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            saved=save(db,project_id=project_id,scene_id=scene.id,
                source_digest=digest(scene.text),
                project_digest=digest(current.model_dump_json()),
                paragraph=result["paragraph"],evidence=evidence,
                evidence_ids=result["evidence_ids"],instruction=request.instruction,
                model=request.model)
        return {**saved,"page_number":request.page_number,"fit":decision,
            "preview_only":True,
            "warning":"Evidence is authored project context, not independent fact verification"}

    @api.get("/projects/{project_id}/print-proof/grounded-fit/{suggestion_id}")
    def read(project_id:str,suggestion_id:str):
        p=project(project_id)
        try:
            with database() as db:
                item=get(db,project_id=project_id,suggestion_id=suggestion_id)
        except ValueError as exc:
            raise HTTPException(404,str(exc)) from exc
        scene=next((s for s in p.scenes if s.id==item["scene_id"]),None)
        item["stale"]=scene is None or digest(scene.text)!=item["source_digest"] or (
            digest(p.model_dump_json())!=item["base_project_digest"])
        return item

    @api.post("/projects/{project_id}/print-proof/grounded-fit/{suggestion_id}/reject")
    def discard(project_id:str,suggestion_id:str):
        project(project_id)
        try:
            with database() as db:
                db.execute("BEGIN IMMEDIATE")
                return reject(db,project_id=project_id,suggestion_id=suggestion_id)
        except (ValueError,FitProposalConflict) as exc:
            raise HTTPException(409,str(exc)) from exc
