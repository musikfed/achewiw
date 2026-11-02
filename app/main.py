"""ACHEWIW StoryEngine — single-user writing studio; Ollama runs on the Linux host."""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

DATA_DIR = Path(os.getenv("ACHEWIW_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "achewiw.sqlite3"
STATIC_DIR = Path(__file__).parent / "static"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
USERNAME = os.getenv("ACHEWIW_USERNAME", "author")
PASSWORD = os.getenv("ACHEWIW_PASSWORD", "")
DEFAULT_MODEL = os.getenv("ACHEWIW_MODEL", "qwen3:8b")
basic = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(basic)) -> None:
    if not PASSWORD:
        raise HTTPException(503, "Configure ACHEWIW_PASSWORD in .env")
    if not (
        secrets.compare_digest(credentials.username.encode(), USERNAME.encode())
        and secrets.compare_digest(credentials.password.encode(), PASSWORD.encode())
    ):
        raise HTTPException(
            401, "Invalid credentials", headers={"WWW-Authenticate": "Basic"}
        )


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS projects "
            "(id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not PASSWORD:
        raise RuntimeError("ACHEWIW_PASSWORD must be set in .env")
    init_db()
    yield


app = FastAPI(title="ACHEWIW StoryEngine", version="0.1.0", lifespan=lifespan)
api = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


class Scene(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    title: str = Field(default="Новая сцена", max_length=200)
    active_objects: list[str] = Field(default_factory=list, max_length=30)
    anchor: str = Field(default="", max_length=2000)
    tempo: str = Field(default="Ровный", max_length=200)
    reader_info: str = Field(default="", max_length=4000)
    hook: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=150000)


class Project(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    title: str = Field(default="Без названия", max_length=200)
    genre: str = Field(default="Фантастика", max_length=200)
    atmosphere: str = Field(default="", max_length=2000)
    audience: str = Field(default="", max_length=1000)
    target_length: str = Field(default="", max_length=200)
    format: Literal["reading", "performance"] = "reading"
    premise: str = Field(default="", max_length=10000)
    twist: str = Field(default="", max_length=4000)
    reveal: str = Field(default="", max_length=2000)
    anchors: str = Field(default="", max_length=4000)
    scenes: list[Scene] = Field(default_factory=list, max_length=100)
    updated_at: str = ""


class CreateProject(BaseModel):
    title: str = Field(default="Новая история", min_length=1, max_length=200)


class AIRequest(BaseModel):
    project_id: str
    mode: Literal["outline", "scene", "audit"]
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=200)
    scene_id: str | None = None
    instructions: str = Field(default="", max_length=2000)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_project(project_id: str) -> Project:
    with db() as connection:
        row = connection.execute(
            "SELECT data FROM projects WHERE id=?", (project_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Проект не найден")
    return Project.model_validate_json(row["data"])


def persist(project: Project) -> Project:
    project.updated_at = utc_now()
    with db() as connection:
        connection.execute(
            "INSERT INTO projects (id, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, "
            "updated_at=excluded.updated_at",
            (project.id, project.model_dump_json(), project.updated_at),
        )
    return project


def count_words(text: str) -> int:
    return len(re.findall(r"\b[\w-]+\b", text, flags=re.UNICODE))


def parse_outline(raw: str) -> list[Scene]:
    cleaned = raw.strip()
    cleaned = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", cleaned, flags=re.I)
    value = json.loads(cleaned)
    if not isinstance(value, dict) or not isinstance(value.get("scenes"), list):
        raise ValueError("Expected a JSON object containing scenes[]")
    scenes = [Scene.model_validate(scene) for scene in value["scenes"]]
    if not 1 <= len(scenes) <= 30:
        raise ValueError("Expected between 1 and 30 scenes")
    return scenes


def build_prompt(project: Project, request: AIRequest) -> str:
    instruction = (
        "Ты StoryEngine — русскоязычный литературный архитектор и редактор. "
        "Пиши ясно, образно и структурно. Не придумывай случайных спасительных предметов. "
        "Читатель — активный соавтор. Желательно не более пяти активных сущностей на сцену. "
        "Не путай якорь локации с подробным повторным описанием.\n"
    )
    spec = {
        "название": project.title,
        "жанр": project.genre,
        "атмосфера": project.atmosphere,
        "аудитория": project.audience,
        "объём": project.target_length,
        "формат": "вслух" if project.format == "performance" else "глазами",
        "замысел": project.premise,
        "твист": project.twist,
        "момент раскрытия": project.reveal,
        "якоря": project.anchors,
    }
    context = json.dumps(spec, ensure_ascii=False)
    summaries = [
        {
            "id": scene.id,
            "название": scene.title,
            "якорь": scene.anchor,
            "темп": scene.tempo,
            "информация": scene.reader_info,
            "крючок": scene.hook,
            "объекты": scene.active_objects,
            "отрывок": scene.text[:1000],
        }
        for scene in project.scenes[:40]
    ]
    extra = f"\nДополнительное пожелание автора: {request.instructions}" if request.instructions else ""
    if request.mode == "outline":
        task = (
            "Создай последовательный Story.prj (5–8 сцен, если ТЗ не требует другого). "
            "Верни СТРОГО валидный JSON без markdown: "
            '{"scenes":[{"title":"...","active_objects":["..."],"anchor":"...",'
            '"tempo":"...","reader_info":"...","hook":"...","text":""}]}. '
            "Не более 5 активных объектов у каждой сцены. "
            "Подсказки, раскрытие тайны, кульминация и завершение должны быть логичны."
        )
    elif request.mode == "scene":
        selected = next((s for s in project.scenes if s.id == request.scene_id), None)
        if selected is None:
            raise HTTPException(422, "Сначала выберите сцену")
        task = (
            "Напиши полноценный художественный текст выбранной сцены. "
            "Сохраняй хронологию и предустановленные якоря. "
            "Не включай комментарии о процессе. Текст сцены:\n"
            + json.dumps(selected.model_dump(), ensure_ascii=False)
        )
    else:
        task = (
            "Проведи QA истории по сценам: хронология, причинность, подготовка "
            "используемых предметов, повторение якорей, загрузка кадра, "
            "эмоциональная траектория, клиффхэнгеры, ветка feature/what-if. "
            "Отметь конкретные проблемы и предложи короткие исправления. "
            "Не выдавай выдуманное за установленный факт."
        )
    return (instruction + "\nТЗ:\n" + context + "\nСцены:\n"
            + json.dumps(summaries, ensure_ascii=False) + "\nЗадача:\n"
            + task + extra)[:35000]


@app.get("/api/health")
def health():
    return {"status": "ok", "ollama": "external-host"}


@app.get("/")
def index(_=Depends(require_auth)):
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/static/{filename}")
def asset(filename: Literal["app.js", "style.css"], _=Depends(require_auth)):
    return FileResponse(STATIC_DIR / filename)


@api.get("/projects")
def list_projects():
    with db() as connection:
        rows = connection.execute(
            "SELECT data, updated_at FROM projects ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {"id": (p := json.loads(row["data"]))["id"],
         "title": p["title"], "genre": p["genre"],
         "updated_at": row["updated_at"]}
        for row in rows
    ]


@api.post("/projects", status_code=201)
def create_project(request: CreateProject):
    return persist(Project(title=request.title))


@api.get("/projects/{project_id}")
def read_project(project_id: str):
    return get_project(project_id)


@api.put("/projects/{project_id}")
def update_project(project_id: str, project: Project):
    get_project(project_id)
    if project.id != project_id:
        raise HTTPException(400, "Project ID mismatch")
    return persist(project)


@api.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str):
    get_project(project_id)
    with db() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (project_id,))
    return Response(status_code=204)


@api.get("/projects/{project_id}/stats")
def stats(project_id: str):
    project = get_project(project_id)
    rows = [
        {"scene_id": scene.id, "title": scene.title,
         "active_objects": len(scene.active_objects),
         "overloaded": len(scene.active_objects) > 5,
         "words": count_words(scene.text),
         "minutes_read_aloud": round(count_words(scene.text) / 150, 1)}
        for scene in project.scenes
    ]
    return {"scenes": rows, "total_words": sum(s["words"] for s in rows),
            "minutes_read_aloud": round(sum(s["words"] for s in rows) / 150, 1)}


@api.get("/projects/{project_id}/export")
def export_markdown(project_id: str):
    p = get_project(project_id)
    chunks = [
        f"# {p.title}",
        f"**Жанр:** {p.genre}",
        f"**Атмосфера:** {p.atmosphere}",
        f"**Аудитория:** {p.audience}",
        f"**Объём:** {p.target_length}",
        f"**Формат:** {'Выступление' if p.format == 'performance' else 'Чтение'}",
        f"**Замысел:** {p.premise}",
        f"**Твист:** {p.twist}",
        f"**Раскрытие:** {p.reveal}",
        f"**Якоря:** {p.anchors}",
    ]
    for index, s in enumerate(p.scenes, 1):
        chunks.extend([
            f"## {index}. {s.title}",
            f"*Активные объекты:* {', '.join(s.active_objects)}",
            f"*Якорь:* {s.anchor}",
            f"*Темп:* {s.tempo}",
            f"*Информация читателю:* {s.reader_info}",
            f"*Крючок:* {s.hook}",
            s.text,
        ])
    return PlainTextResponse(
        "\n\n".join(chunks) + "\n",
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="story.md"'},
    )


@api.get("/models")
async def list_models():
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(OLLAMA_BASE_URL + "/api/tags")
            response.raise_for_status()
        return {"models": [item["name"] for item in response.json().get("models", [])],
                "endpoint": "Ollama на хосте"}
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise HTTPException(503, f"Ollama недоступна на хосте: {error}") from error


@api.post("/ai")
async def generate(request: AIRequest):
    project = get_project(request.project_id)
    prompt = build_prompt(project, request)
    payload = {
        "model": request.model, "prompt": prompt,
        "stream": False, "think": False,
        "options": {"temperature": 0.7, "num_predict": 2400},
    }
    if request.mode == "outline":
        payload["format"] = "json"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=8)) as client:
            response = await client.post(OLLAMA_BASE_URL + "/api/generate", json=payload)
            response.raise_for_status()
        raw = response.json().get("response", "").strip()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(502, f"Ошибка Ollama на хосте: {error}") from error
    if not raw:
        raise HTTPException(502, "Ollama вернула пустой ответ")
    result = {"result": raw, "mode": request.mode, "model": request.model}
    if request.mode == "outline":
        try:
            result["suggested_scenes"] = [
                scene.model_dump() for scene in parse_outline(raw)
            ]
        except (ValueError, TypeError) as error:
            result["parse_error"] = str(error)
    return result


app.include_router(api)
