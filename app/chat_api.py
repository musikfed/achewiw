"""Opt-in routes for the two ACHEWIW chat workspaces.

Registered on the EXISTING protected /api router: no second FastAPI app,
listener, or server. All original chapter text is resolved server-side.
No LLM is invoked; a candidate is an explicit editable preview only.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import chat_conversations as chats
import chat_editorial_version_bridge as editions
import chat_linked_edits as linked
import chat_ollama_editor as ollama_editor
import chat_book_control as book_control
import chat_plan_proposals as plan_proposals
import chat_plan_ollama as plan_ollama
import chat_outline_builder as outline_builder
import chat_outline_batch_ollama as outline_batch


class ChatThreadRequest(BaseModel):
    workspace: Literal["generation", "editorial"]
    mode: Literal["clarify", "autonomous"] = "clarify"


class ChatModeRequest(BaseModel):
    mode: Literal["clarify", "autonomous"]


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    scope: Literal["selection", "chapter", "book", "document"] | None = None
    scene_id: str | None = Field(default=None, max_length=200)
    base_version_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ChatEditionAttach(BaseModel):
    version_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class ChatCandidateRequest(BaseModel):
    chat_proposal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    base_version_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    scene_id: str = Field(min_length=1, max_length=200)
    candidate_text: str = Field(min_length=1, max_length=150000)


class ChatOllamaRequest(BaseModel):
    chat_proposal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    base_version_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    scene_id: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)


class ChatBookControlRequest(BaseModel):
    action: Literal["start", "pause", "resume"]
    confirm: Literal[True]
    model: str | None = Field(default=None, min_length=1, max_length=200)


class ChatOutlineRequest(BaseModel):
    chat_proposal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    model: str = Field(min_length=1,max_length=200)


class ChatNewOutlineRequest(BaseModel):
    chat_proposal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    count: int = Field(ge=1,le=100)
    total_words: int = Field(ge=500,le=60000)


class ChatNewOutlineBatchRequest(BaseModel):
    model: str = Field(min_length=1,max_length=200)


class ChatOutlineDecision(BaseModel):
    approve: bool


class ChatDecisionRequest(BaseModel):
    approve: bool


def install(api: APIRouter, namespace: dict) -> None:
    """Use host's get_project and DATA_DIR to retain existing auth and isolation."""
    def context(project_id: str):
        namespace["get_project"](project_id)  # host's canonical 404 check
        return namespace["DATA_DIR"]

    def fail(exc: Exception):
        if isinstance(exc, (linked.LinkedEditConflict, editions.EditorialConflict)):
            raise HTTPException(409,str(exc)) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(422,str(exc)) from exc
        raise exc

    @api.post("/projects/{project_id}/chats",status_code=201)
    def chat_create(project_id: str, body: ChatThreadRequest):
        root=context(project_id)
        return chats.create_thread(root,project_id=project_id,
                                   workspace=body.workspace,mode=body.mode)

    @api.get("/projects/{project_id}/chats/{thread_id}")
    def chat_read(project_id: str, thread_id: str):
        root=context(project_id)
        try:
            return chats.conversation(root,project_id=project_id,thread_id=thread_id)
        except ValueError as exc:
            raise HTTPException(404,str(exc)) from exc

    @api.put("/projects/{project_id}/chats/{thread_id}/mode")
    def chat_set_mode(project_id: str, thread_id: str, body: ChatModeRequest):
        root=context(project_id)
        try:
            return chats.switch_mode(root,project_id=project_id,thread_id=thread_id,
                                     mode=body.mode)
        except ValueError as exc:
            raise HTTPException(404,str(exc)) from exc

    @api.post("/projects/{project_id}/chats/{thread_id}/messages",status_code=201)
    def chat_send(project_id: str, thread_id: str, body: ChatMessageRequest):
        root=context(project_id)
        scene=None
        text=None
        if body.scene_id is not None or body.base_version_id is not None:
            if not body.scene_id or not body.base_version_id or body.scope!="chapter":
                raise HTTPException(422,"Для адресной правки укажи главу и исходную редакцию")
            try:
                head=editions.current(root,project_id=project_id)
                if head["version_id"]!=body.base_version_id:
                    raise linked.LinkedEditConflict("Редакция изменилась; обнови выбранную главу")
                chapter=next((item for item in head["chapters"] if
                              item["scene_id"]==body.scene_id),None)
                if chapter is None:
                    raise ValueError("Глава не найдена в выбранной редакции")
                scene=body.scene_id
                text=chapter["text"]
            except (ValueError,linked.LinkedEditConflict) as exc:
                fail(exc)
        try:
            return chats.submit(root,project_id=project_id,thread_id=thread_id,
                                message=body.message,scope=body.scope,
                                source_ref=scene,source_text=text)
        except ValueError as exc:
            fail(exc)

    @api.get("/projects/{project_id}/chats/{thread_id}/book-job")
    def chat_book_status(project_id: str, thread_id: str):
        root=context(project_id)
        try:
            book_control.require_generation_thread(root,project_id,thread_id)
        except (ValueError,book_control.BookControlConflict) as exc:
            raise HTTPException(409,str(exc)) from exc
        with namespace["db"]() as db:
            job_id=namespace["_current_book_job_id"](db,project_id)
            job=namespace["_book_record"](db,job_id) if job_id else None
        return {"job":job,"events":book_control.history(root,project_id=project_id,
                    thread_id=thread_id),"source":"canonical_ai_jobs",
                "independent_generator":False}

    @api.post("/projects/{project_id}/chats/{thread_id}/book-control",status_code=202)
    async def chat_book_control(project_id: str, thread_id: str,
                                body: ChatBookControlRequest):
        """Explicit author action forwarded to the exact existing book workflow."""
        root=context(project_id)
        try:
            book_control.require_generation_thread(
                root,project_id,thread_id,has_instruction=body.action=="start")
        except (ValueError,book_control.BookControlConflict) as exc:
            raise HTTPException(409,str(exc)) from exc
        with namespace["db"]() as db:
            current_id=namespace["_current_book_job_id"](db,project_id)
            current=namespace["_book_record"](db,current_id) if current_id else None

        if body.action=="start":
            # The existing starter is idempotent and validates the saved chapter
            # plan, current GPU jobs and the manuscript hashes.
            selected_model=body.model or namespace["DEFAULT_MODEL"]
            job=await namespace["start_full_book"](
                namespace["BookRequest"](project_id=project_id,model=selected_model))
        else:
            if not current or current["project_id"]!=project_id:
                raise HTTPException(409,"No resumable book job for this project")
            if body.action=="pause":
                if current["status"] not in ("queued","running"):
                    raise HTTPException(409,"Only a running book can be paused")
                await_or_result=namespace["pause_full_book"](current["id"])
                # Pause returns acknowledgement, not the full job record.
                if hasattr(await_or_result,"__await__"):
                    await await_or_result
            else:
                if current["status"] not in ("interrupted","error"):
                    raise HTTPException(409,"Only an interrupted book can resume")
                await namespace["resume_full_book"](current["id"])
            with namespace["db"]() as db:
                job=namespace["_book_record"](db,current["id"])

        if not isinstance(job,dict) or job.get("project_id")!=project_id:
            raise HTTPException(409,"Canonical job was not scoped to the selected project")
        book_control.record(root,project_id=project_id,thread_id=thread_id,
                            action=body.action,job=job)
        return {"job":job,"action":body.action,"source":"canonical_ai_jobs",
                "independent_generator":False,"author_confirmed":True}

    @api.post("/projects/{project_id}/chats/{thread_id}/new-outline",status_code=201)
    def new_outline_begin(project_id: str,thread_id: str,body: ChatNewOutlineRequest):
        root=context(project_id)
        from editor_workspace import _open
        try:
            book_control.require_generation_thread(root,project_id,thread_id)
            with _open(root/"work.sqlite3") as work:
                message=work.execute("""SELECT p.message_id,m.role,p.status
                  FROM work_chat_proposals p JOIN work_chat_messages m
                  ON m.id=p.message_id AND m.thread_id=p.thread_id
                  WHERE p.id=? AND p.thread_id=?""",
                  (body.chat_proposal_id,thread_id)).fetchone()
                if message is None or message["status"]!="proposed" or message["role"]!="author":
                    raise outline_builder.OutlineConflict("Select a saved author instruction")
            with namespace["db"]() as db:
                db.execute("BEGIN IMMEDIATE")
                if namespace["_current_book_job_id"](db,project_id):
                    raise outline_builder.OutlineConflict("Unfinished book job blocks outline changes")
                result=outline_builder.begin(db,project_id=project_id,
                    thread_id=thread_id,message_id=message["message_id"],
                    total_words=body.total_words,count=body.count,
                    Project=namespace["Project"])
            return result
        except outline_builder.OutlineConflict as exc:
            raise HTTPException(409,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @api.get("/projects/{project_id}/chats/{thread_id}/new-outline/{build_id}")
    def new_outline_status(project_id: str,thread_id: str,build_id: str):
        root=context(project_id)
        try:
            book_control.require_generation_thread(root,project_id,thread_id)
            with namespace["db"]() as db:
                return outline_builder.state(db,project_id=project_id,
                    thread_id=thread_id,build_id=build_id)
        except (ValueError,outline_builder.OutlineConflict) as exc:
            raise HTTPException(409,str(exc)) from exc

    @api.post("/projects/{project_id}/chats/{thread_id}/new-outline/{build_id}/batch")
    async def new_outline_batch(project_id: str,thread_id: str,build_id: str,
                                body: ChatNewOutlineBatchRequest):
        root=context(project_id)
        from editor_workspace import _open
        from gpu_scheduling import reserve_gpu,release_gpu,image_operation_active
        from generation_core import active_job
        import uuid
        try:
            book_control.require_generation_thread(root,project_id,thread_id)
            with namespace["db"]() as db:
                baseline=outline_builder.state(db,project_id=project_id,
                    thread_id=thread_id,build_id=build_id)
                row=outline_builder._row(db,project_id,thread_id,build_id)
                project,raw=outline_builder._project(db,project_id,namespace["Project"])
                if baseline["status"]!="draft" or project.scenes or outline_builder._digest(raw)!=row["base_sha256"]:
                    raise outline_builder.OutlineConflict("Outline is stale or complete")
                if namespace["_current_book_job_id"](db,project_id):
                    raise outline_builder.OutlineConflict("Unfinished book job blocks outline changes")
            with _open(root/"work.sqlite3") as work:
                instruction=work.execute("""SELECT content FROM work_chat_messages
                   WHERE id=? AND thread_id=? AND role='author'""",
                    (row["message_id"],thread_id)).fetchone()
                if instruction is None:
                    raise outline_builder.OutlineConflict("Missing author instruction")
                text=instruction["content"]
            lease=uuid.uuid4().hex
            with namespace["db"]() as db:
                db.execute("BEGIN IMMEDIATE")
                if active_job(db) or image_operation_active(db) or not reserve_gpu(db,lease,"outline_build"):
                    raise outline_builder.OutlineConflict("Shared GPU is occupied")
            try:
                generated=await outline_batch.generate_batch(
                    base_url=namespace["OLLAMA_BASE_URL"],model=body.model,
                    instruction=text,previous=baseline["chapters"],
                    offset=baseline["created"],count=baseline["next_batch_size"],
                    total_words=baseline["total_words"],work_type=project.work_type)
            finally:
                with namespace["db"]() as db:
                    release_gpu(db,lease)
            with namespace["db"]() as db:
                db.execute("BEGIN IMMEDIATE")
                if namespace["_current_book_job_id"](db,project_id):
                    raise outline_builder.OutlineConflict("Book job started during proposal")
                refreshed=outline_builder.state(db,project_id=project_id,
                    thread_id=thread_id,build_id=build_id)
                if refreshed["created"]!=baseline["created"]:
                    raise outline_builder.OutlineConflict("Another batch was saved first")
                result=outline_builder.append(db,project_id=project_id,
                    thread_id=thread_id,build_id=build_id,
                    items=generated["chapters"],Project=namespace["Project"])
            return {**result,"model":generated["model"],
                    "input_tokens":generated["input_tokens"],
                    "output_tokens":generated["output_tokens"]}
        except outline_builder.OutlineConflict as exc:
            raise HTTPException(409,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @api.post("/projects/{project_id}/chats/{thread_id}/new-outline/{build_id}/decision")
    def new_outline_decide(project_id: str,thread_id: str,build_id: str,
                           body: ChatOutlineDecision):
        root=context(project_id)
        try:
            book_control.require_generation_thread(root,project_id,thread_id)
            with namespace["db"]() as db:
                db.execute("BEGIN IMMEDIATE")
                return outline_builder.decide(db,project_id=project_id,
                    thread_id=thread_id,build_id=build_id,approve=body.approve,
                    Project=namespace["Project"],Scene=namespace["Scene"],
                    has_pending_book=namespace["_current_book_job_id"])
        except outline_builder.OutlineConflict as exc:
            raise HTTPException(409,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @api.get("/projects/{project_id}/chats/{thread_id}/outline")
    def chat_outline_read(project_id: str, thread_id: str):
        root=context(project_id)
        try:
            book_control.require_generation_thread(root,project_id,thread_id)
        except (ValueError,book_control.BookControlConflict) as exc:
            raise HTTPException(409,str(exc)) from exc
        with namespace["db"]() as db:
            plan_proposals.initialize(db)
            baseline=plan_proposals.snapshot(db,project_id,namespace["Project"])
            latest=plan_proposals.latest(db,project_id=project_id,thread_id=thread_id,
                                          project_type=namespace["Project"])
        return {"baseline":baseline,"latest":latest}

    @api.post("/projects/{project_id}/chats/{thread_id}/outline",status_code=201)
    async def chat_outline_prepare(project_id: str, thread_id: str,
                                   body: ChatOutlineRequest):
        """Generate a reviewable outline, no canonical mutation before acceptance."""
        root=context(project_id)
        from editor_workspace import _open
        from gpu_scheduling import reserve_gpu,release_gpu,image_operation_active
        from generation_core import active_job
        import uuid
        try:
            book_control.require_generation_thread(root,project_id,thread_id)
            with _open(root/"work.sqlite3") as work:
                proposal=work.execute("""SELECT p.status,m.content,m.role
                    FROM work_chat_proposals p
                    JOIN work_chat_messages m ON m.id=p.message_id AND m.thread_id=p.thread_id
                    WHERE p.id=? AND p.thread_id=?""",
                    (body.chat_proposal_id,thread_id)).fetchone()
                if not proposal or proposal["status"]!="proposed" or proposal["role"]!="author":
                    raise plan_proposals.PlanConflict("Select an existing author instruction")
                instruction=proposal["content"]
            with namespace["db"]() as db:
                if namespace["_current_book_job_id"](db,project_id):
                    raise plan_proposals.PlanConflict("Existing unfinished book job blocks outline changes")
                baseline=plan_proposals.snapshot(db,project_id,namespace["Project"])
                token=uuid.uuid4().hex
                db.execute("BEGIN IMMEDIATE")
                if active_job(db) or image_operation_active(db) or not reserve_gpu(db,token,"chat_outline"):
                    raise plan_proposals.PlanConflict("GPU is occupied by an existing job")
            try:
                generated=await plan_ollama.propose(
                    base_url=namespace["OLLAMA_BASE_URL"],model=body.model,
                    instruction=instruction,snapshot=baseline)
            finally:
                with namespace["db"]() as db:
                    release_gpu(db,token)
            with namespace["db"]() as db:
                db.execute("BEGIN IMMEDIATE")
                if namespace["_current_book_job_id"](db,project_id):
                    raise plan_proposals.PlanConflict("Book job started while outline was prepared")
                result=plan_proposals.create(db,project_id=project_id,thread_id=thread_id,
                    chat_proposal_id=body.chat_proposal_id,
                    candidate=generated["candidate"],
                    expected_digest=baseline["base_digest"],
                    project_type=namespace["Project"])
            return {**result,"model":generated["model"],
                    "input_tokens":generated["input_tokens"],
                    "output_tokens":generated["output_tokens"],
                    "requires_approval":True}
        except (plan_proposals.PlanConflict,book_control.BookControlConflict) as exc:
            raise HTTPException(409,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @api.post("/projects/{project_id}/chats/{thread_id}/outline/{proposal_id}/decision")
    def chat_outline_decision(project_id: str, thread_id: str,
                              proposal_id: str, body: ChatOutlineDecision):
        root=context(project_id)
        try:
            book_control.require_generation_thread(root,project_id,thread_id)
            with namespace["db"]() as db:
                db.execute("BEGIN IMMEDIATE")
                result=plan_proposals.decide(db,project_id=project_id,
                    thread_id=thread_id,proposal_id=proposal_id,approve=body.approve,
                    project_type=namespace["Project"],
                    has_pending_book=namespace["_current_book_job_id"])
            return result
        except (plan_proposals.PlanConflict,book_control.BookControlConflict) as exc:
            raise HTTPException(409,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @api.post("/projects/{project_id}/chat-edition",status_code=201)
    def chat_attach_edition(project_id: str, body: ChatEditionAttach):
        root=context(project_id)
        try:
            return editions.attach(root,project_id=project_id,version_id=body.version_id)
        except ValueError as exc:
            fail(exc)

    @api.get("/projects/{project_id}/chat-edition")
    def chat_current_edition(project_id: str):
        root=context(project_id)
        try:
            return editions.current(root,project_id=project_id)
        except ValueError as exc:
            raise HTTPException(404,str(exc)) from exc

    @api.post("/projects/{project_id}/chats/{thread_id}/candidates",status_code=201)
    def chat_candidate(project_id: str, thread_id: str, body: ChatCandidateRequest):
        root=context(project_id)
        try:
            return linked.prepare(root,project_id=project_id,thread_id=thread_id,
                    chat_proposal_id=body.chat_proposal_id,
                    base_version_id=body.base_version_id,scene_id=body.scene_id,
                    candidate_text=body.candidate_text)
        except (ValueError,linked.LinkedEditConflict,editions.EditorialConflict) as exc:
            fail(exc)

    @api.post("/projects/{project_id}/chats/{thread_id}/ollama-candidate",status_code=201)
    async def chat_ollama_candidate(project_id: str, thread_id: str,
                                    body: ChatOllamaRequest):
        """One bounded, synchronous draft. Never changes the selected edition."""
        root=context(project_id)
        # First invocation must create link tables before read-only preflight.
        linked.initialize(root)
        from editor_workspace import _open
        from gpu_scheduling import reserve_gpu, release_gpu, image_operation_active
        from generation_core import active_job
        import uuid
        lease=uuid.uuid4().hex
        try:
            with _open(root/"work.sqlite3") as work:
                row=linked._linked(work,project_id,thread_id,body.chat_proposal_id)
                if (row["status"]!="proposed" or row["scope"]!="chapter"
                    or row["source_ref"]!=body.scene_id or row["source_digest"] is None):
                    raise linked.LinkedEditConflict("No matching chapter-bound author instruction")
                instruction=work.execute("""SELECT content FROM work_chat_messages
                    WHERE id=? AND thread_id=?""",
                    (row["message_id"],thread_id)).fetchone()["content"]
                head=work.execute("""SELECT version_id FROM work_chat_book_heads
                    WHERE project_id=?""",(project_id,)).fetchone()
                if head is None or head["version_id"]!=body.base_version_id:
                    raise linked.LinkedEditConflict("Stale edition: refresh before editing")
                _,payload=editions._version(work,project_id,body.base_version_id)
                original=editions._scene(payload,body.scene_id)["text"]
                if editions._hash(original)!=row["source_digest"]:
                    raise linked.LinkedEditConflict("Source chapter no longer matches instruction")
                existing=work.execute("""SELECT id FROM work_chat_edit_links
                    WHERE chat_proposal_id=?""",(body.chat_proposal_id,)).fetchone()
                if existing:
                    raise linked.LinkedEditConflict("This instruction already has a candidate")
            with namespace["db"]() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if active_job(connection) or image_operation_active(connection):
                    raise linked.LinkedEditConflict("Generation or image task is using the shared GPU")
                if not reserve_gpu(connection,lease,"chat_editorial"):
                    raise linked.LinkedEditConflict("The shared GPU is busy")
            try:
                draft=await ollama_editor.generate_candidate(
                    base_url=namespace["OLLAMA_BASE_URL"],model=body.model,
                    original=original,instruction=instruction)
            finally:
                with namespace["db"]() as connection:
                    release_gpu(connection,lease)
            result=linked.prepare(root,project_id=project_id,thread_id=thread_id,
                chat_proposal_id=body.chat_proposal_id,
                base_version_id=body.base_version_id,scene_id=body.scene_id,
                candidate_text=draft["candidate_text"])
            return {**result,"model":draft["model"],"engine":draft["engine"],
                    "input_tokens":draft["input_tokens"],
                    "output_tokens":draft["output_tokens"],
                    "approval_required":True}
        except ollama_editor.ModelEditorialError as exc:
            raise HTTPException(502,str(exc)) from exc
        except (ValueError,linked.LinkedEditConflict,editions.EditorialConflict) as exc:
            fail(exc)

    @api.get("/projects/{project_id}/chats/{thread_id}/candidates/{proposal_id}")
    def chat_candidate_read(project_id: str, thread_id: str, proposal_id: str):
        root=context(project_id)
        try:
            return linked.pending_candidate(root,project_id=project_id,thread_id=thread_id,
                                            chat_proposal_id=proposal_id)
        except (ValueError,linked.LinkedEditConflict) as exc:
            fail(exc)

    @api.post("/projects/{project_id}/chats/{thread_id}/candidates/{proposal_id}/decision")
    def chat_candidate_decision(project_id: str, thread_id: str, proposal_id: str,
                                body: ChatDecisionRequest):
        root=context(project_id)
        try:
            return linked.decide(root,project_id=project_id,thread_id=thread_id,
                                 chat_proposal_id=proposal_id,approve=body.approve)
        except (ValueError,linked.LinkedEditConflict,editions.EditorialConflict) as exc:
            fail(exc)
