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

    @api.post("/projects/{project_id}/chats/edition",status_code=201)
    def chat_attach_edition(project_id: str, body: ChatEditionAttach):
        root=context(project_id)
        try:
            return editions.attach(root,project_id=project_id,version_id=body.version_id)
        except ValueError as exc:
            fail(exc)

    @api.get("/projects/{project_id}/chats/edition")
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

    @api.post("/projects/{project_id}/chats/{thread_id}/candidates/{proposal_id}/decision")
    def chat_candidate_decision(project_id: str, thread_id: str, proposal_id: str,
                                body: ChatDecisionRequest):
        root=context(project_id)
        try:
            return linked.decide(root,project_id=project_id,thread_id=thread_id,
                                 chat_proposal_id=proposal_id,approve=body.approve)
        except (ValueError,linked.LinkedEditConflict,editions.EditorialConflict) as exc:
            fail(exc)
