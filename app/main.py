"""ACHEWIW StoryEngine — single-user writing studio; Ollama runs on the Linux host."""
from __future__ import annotations

import asyncio
import json
import ipaddress
import os
import sqlite3
import time
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from monitoring_core import load_snapshot, summarize_ollama_running
from publishing_exports import manuscript_pdf, manuscript_docx, manuscript_fb2
from manuscript_core import chapter_listing, manuscript_markdown, manuscript_epub
from generation_core import (init_jobs, mark_interrupted, create_job, update_job,
                             recent_jobs, get_job, active_job, estimate_progress, save_audit_decision,
                             LIMIT, MAX_OUTPUT)

DATA_DIR = Path(os.getenv("ACHEWIW_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "achewiw.sqlite3"
STATIC_DIR = Path(__file__).parent / "static"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("ACHEWIW_MODEL", "qwen3:8b")
# Password-free private LAN/VPN mode. No unauthenticated access from public IPs.
# Connections forwarded by a reverse proxy must preserve a trusted source address;
# X-Forwarded-For is intentionally ignored.
PRIVATE_CLIENT_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "::1/128", "fc00::/7"
))


def require_auth(request: Request) -> None:
    host = request.client.host if request.client else ""
    try:
        peer = ipaddress.ip_address(host)
    except ValueError:
        raise HTTPException(403, "Доступ разрешён только из локальной сети или VPN")
    if not any(peer in network for network in PRIVATE_CLIENT_NETWORKS):
        raise HTTPException(403, "Внешний IP запрещён — нужен доверенный VPN")
    # Block DNS rebinding and accidental alternative public hostnames.
    from urllib.parse import urlsplit
    raw_host = request.headers.get("host", "")
    try:
        hostname = urlsplit("http://" + raw_host).hostname
    except ValueError:
        hostname = None
    allowed_hosts = {name.strip().lower() for name in
                     os.getenv("ACHEWIW_ALLOWED_HOSTS",
                               "192.168.0.100,127.0.0.1,localhost").split(",")
                     if name.strip()}
    if not hostname or hostname.lower() not in allowed_hosts:
        raise HTTPException(403, "Недопустимый адрес сервера: используйте доверенный IP по VPN")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        # Same-origin protection for browser requests without a password.
        if origin:
            from urllib.parse import urlsplit
            parsed = urlsplit(origin)
            if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host", ""):
                raise HTTPException(403, "Межсайтовая операция запрещена")



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
        init_jobs(connection)
        mark_interrupted(connection)


_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    try:
        yield
    finally:
        for task in list(_background_tasks):
            task.cancel()
        if _background_tasks:
            await asyncio.gather(*_background_tasks, return_exceptions=True)


app = FastAPI(title="ACHEWIW StoryEngine", version="0.2.1", lifespan=lifespan)
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
    work_type: Literal["fiction", "nonfiction", "technical", "speech"] = "fiction"
    sources: str = Field(default="", max_length=15000)
    claims: str = Field(default="", max_length=15000)
    duration_minutes: int = Field(default=10, ge=1, le=480)
    speaker_notes: str = Field(default="", max_length=10000)
    premise: str = Field(default="", max_length=10000)
    twist: str = Field(default="", max_length=4000)
    reveal: str = Field(default="", max_length=2000)
    anchors: str = Field(default="", max_length=4000)
    scenes: list[Scene] = Field(default_factory=list, max_length=100)
    updated_at: str = ""


class CreateProject(BaseModel):
    title: str = Field(default="Новая работа", min_length=1, max_length=200)
    work_type: Literal["fiction", "nonfiction", "technical", "speech"] = "fiction"


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
    guidance = {
        "fiction": "Ты опытный редактор фантастики и прозы. Соблюдай причинность, канон мира, развитие персонажей; не подменяй сцену отчётом.",
        "nonfiction": "Ты редактор нонфикшн. Давай ясные тезисы, проверяемые объяснения, примеры и различай факт и интерпретацию.",
        "technical": "Ты научно-технический редактор. Отделяй утверждения от доказательств, не выдумывай DOI, библиографию и исследования; отмечай непроверенные факты и условия применения.",
        "speech": "Ты спичрайтер и режиссёр выступлений. Пиши текст для произнесения вслух, с ясными переходами, паузами и таймингом; не подменяй речь планом.",
    }
    instruction = guidance[project.work_type] + " Ответ строго на русском, содержательно, без вводных оправданий.\\n"

    spec = {
        "название": project.title,
        "тип": project.work_type,
        "проверенные источники, внесённые автором": project.sources,
        "тезисы/гипотезы": project.claims,
        "тайминг выступления, минуты": project.duration_minutes,
        "заметки выступающего": project.speaker_notes,
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
            ("Создай последовательный план разделов технической книги (5–8 разделов). "
             if project.work_type == "technical" else
             "Создай последовательный план выступления с таймингом и переходами. "
             if project.work_type == "speech" else
             "Создай последовательный план глав/сцен произведения (5–8). ")
            + "Верни СТРОГО валидный JSON без markdown: "
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
            ("Напиши полноценный раздел технической книги. Используй только предоставленные автором источники; любые неподтверждённые факты явно маркируй. "
             if project.work_type == "technical" else
             "Напиши произносимый текст части выступления, с переходами, паузами и ясными тезисами. "
             if project.work_type == "speech" else
             "Напиши полноценный содержательный текст раздела книги с аргументами и примерами. "
             if project.work_type == "nonfiction" else
             "Напиши полноценный художественный текст выбранной сцены, с каноном и причинностью. ")
            + "Не включай комментарии о процессе. Материал текущей части:\\n"
            + json.dumps(selected.model_dump(), ensure_ascii=False)
        )
    else:
        written = sum(bool(scene.text.strip()) for scene in project.scenes)
        scope = (
            f"В проекте написаны {written} из {len(project.scenes)} сцен. "
            "Тебе доступны только первые 1000 символов текста каждой сцены. "
            "Не делай выводов об отсутствующем тексте. "
        )
        if not written:
            scope += (
                "Внимание: текст книги ещё НЕ НАПИСАН, есть лишь карточки сцен. "
                "Проверяй только архитектуру и причинность по карточкам. "
                "Не утверждай, что читал художественную книгу. "
            )
        task = (
            scope +
            "Это редакторский ОТЧЁТ, а не художественный текст книги. "
            "Для каждой проблемы приведи название сцены и конкретную правку. "
            "Не повторяй исходный текст в качестве исправления; не выдумывай сцены. "
            "Если нет достаточных данных — отметь неопределённость. "
            ("Проведи научно-техническую проверку: тезисы, достоверность источников, неподтверждённые утверждения, терминология и логика. "
             if project.work_type == "technical" else
             "Проверь речь: тайминг, аудитория, ясность, произносимость, переходы и аргументация. "
             if project.work_type == "speech" else
             "Проверь нонфикшн: структура аргументов, подтверждение фактов, логика и доступность. "
             if project.work_type == "nonfiction" else
             "Проведи QA истории: хронология, причинность, подготовка предметов, канон, эмоциональная траектория, клиффхэнгеры. ")
            + "Отметь конкретные проблемы и предложи короткие исправления. "
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
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store",
                 "X-ACHEWIW-App": "studio",
                 "X-ACHEWIW-Auth": "private-vpn-no-password"},
    )


@app.get("/static/{filename}")
def asset(filename: Literal["app.js", "style.css", "monitor.js"], _=Depends(require_auth)):
    return FileResponse(STATIC_DIR / filename, headers={"Cache-Control": "no-store"})


@api.get("/projects")
def list_projects():
    with db() as connection:
        rows = connection.execute(
            "SELECT data, updated_at FROM projects ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {"id": (p := json.loads(row["data"]))["id"],
         "title": p["title"], "genre": p.get("genre", ""),
         "work_type": p.get("work_type", "fiction"),
         "updated_at": row["updated_at"]}
        for row in rows
    ]


@api.post("/projects", status_code=201)
def create_project(request: CreateProject):
    labels = {"fiction": "Фантастика", "nonfiction": "Нонфикшн",
              "technical": "Техническая литература", "speech": "Выступление"}
    return persist(Project(title=request.title, work_type=request.work_type,
                           genre=labels[request.work_type],
                           format="performance" if request.work_type == "speech" else "reading"))


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
        running = connection.execute(
            "SELECT 1 FROM ai_jobs WHERE project_id=? AND status IN ('queued','running') LIMIT 1",
            (project_id,),
        ).fetchone()
        if running:
            raise HTTPException(409, "Нельзя удалить проект во время его генерации")
        # Reversible by design: snapshot the entire SQLite before deleting.
        # This stays inside the ignored ACHEWIW data/ directory, never on GitHub.
        backups = DATA_DIR / "backups"
        if backups.is_symlink():
            raise HTTPException(409, "Небезопасный путь резервных копий")
        backups.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup_path = backups / ("achewiw-before-delete-" + uuid.uuid4().hex + ".sqlite3")
        try:
            with sqlite3.connect(backup_path) as backup_connection:
                connection.backup(backup_connection)
            backup_path.chmod(0o600)
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(503, "Резервная копия не создана — удаление отменено") from exc
        connection.execute(
            "DELETE FROM ai_job_reviews WHERE job_id IN "
            "(SELECT id FROM ai_jobs WHERE project_id=?)", (project_id,)
        )
        connection.execute("DELETE FROM ai_jobs WHERE project_id=?", (project_id,))
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


@api.get("/projects/{project_id}/manuscript")
def manuscript_preview(project_id: str):
    """Read-only view of approved scene texts; missing chapters stay explicit."""
    return chapter_listing(get_project(project_id))


@api.get("/projects/{project_id}/manuscript.md")
def download_manuscript_markdown(project_id: str):
    project = get_project(project_id)
    try:
        body = manuscript_markdown(project)
    except ValueError as exc:
        raise HTTPException(
            409, "Книга пока не написана. Открой 03 · Редактор и сохрани текст хотя бы одной сцены."
        ) from exc
    return PlainTextResponse(
        body, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="achewiw-book.md"',
                 "Cache-Control": "no-store"},
    )


@api.get("/projects/{project_id}/manuscript.epub")
def download_manuscript_epub(project_id: str):
    project = get_project(project_id)
    try:
        body = manuscript_epub(project)
    except ValueError as exc:
        raise HTTPException(
            409, "Для EPUB сначала сохрани текст хотя бы одной сцены в редакторе."
        ) from exc
    return Response(
        content=body, media_type="application/epub+zip",
        headers={"Content-Disposition": 'attachment; filename="achewiw-book.epub"',
                 "Cache-Control": "no-store"},
    )


@api.get("/projects/{project_id}/manuscript.docx")
def download_manuscript_docx(project_id: str):
    try:
        body = manuscript_docx(get_project(project_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(
        content=body, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="achewiw-book.docx"',
                 "Cache-Control": "no-store"},
    )


@api.get("/projects/{project_id}/manuscript.fb2")
def download_manuscript_fb2(project_id: str):
    try:
        body = manuscript_fb2(get_project(project_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(
        content=body, media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="achewiw-book.fb2"',
                 "Cache-Control": "no-store"},
    )


@api.get("/projects/{project_id}/manuscript.pdf")
def download_manuscript_pdf(project_id: str):
    try:
        body = manuscript_pdf(get_project(project_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(content=body, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="achewiw-book.pdf"',
                             "Cache-Control": "no-store"})


@api.get("/projects/{project_id}/manuscript.print.pdf")
def download_print_proof_pdf(project_id: str):
    try:
        body = manuscript_pdf(get_project(project_id), print_proof=True)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(content=body, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="achewiw-print-proof-A5.pdf"',
                             "X-Print-Status": "proof-not-pdfx",
                             "Cache-Control": "no-store"})


@api.get("/projects/{project_id}/publishing-preflight")
def publishing_preflight(project_id: str):
    from manuscript_core import chapter_listing
    p = get_project(project_id)
    book = chapter_listing(p)
    return {
        "work_type": p.work_type,
        "title": p.title,
        "written_sections": book["written_scenes"],
        "total_sections": book["total_scenes"],
        "unwritten": book["unwritten"],
        "ready_for_reading_export": book["written_scenes"] > 0,
        "print_proof": "available" if book["written_scenes"] else "not_ready",
        "pdfx_status": "not_certified",
        "notes": "A5 print PDF is a proof, NOT PDF/X. Confirm printer trim/bleed/ICC/fonts before publication.",
    }


@api.get("/monitoring")
async def monitor_status():
    """Authenticated, read-only dashboard; no Docker/NVIDIA command execution."""
    host = load_snapshot(DATA_DIR / "monitor" / "host.json")
    ollama = {
        "status": "unavailable",
        "configured_default_model": DEFAULT_MODEL,
        "loaded_models": [],
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=1.0)) as client:
            result = await client.get(OLLAMA_BASE_URL + "/api/ps")
            result.raise_for_status()
            loaded = result.json()
        ollama["status"] = "ok"
        ollama["loaded_models"] = summarize_ollama_running(loaded)
    except (httpx.HTTPError, ValueError, KeyError):
        # Do not expose host paths, prompts, tokens or upstream error details.
        pass
    return {"host": host, "ollama": ollama}


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


class UnloadRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    confirm_shared_model: bool = False


@api.post("/models/unload")
async def unload_model(request: UnloadRequest):
    """Unloads ONE explicitly selected Ollama model; never resets/clears GPU.

    Shared Ollama may have other clients using the same model. No API can
    establish exclusive ownership: require an explicit informed confirmation.
    """
    if not request.confirm_shared_model:
        raise HTTPException(422, "Подтверди, что модель в общей Ollama может использоваться другими программами")
    with db() as connection:
        if active_job(connection):
            raise HTTPException(409, "Генерация ACHEWIW выполняется; освобождение памяти запрещено")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=3)) as client:
            state = await client.get(OLLAMA_BASE_URL + "/api/ps")
            state.raise_for_status()
            names = [m["name"] for m in state.json().get("models", [])
                     if isinstance(m, dict) and isinstance(m.get("name"), str)]
            if request.model not in names:
                return {"status": "already_unloaded", "model": request.model}
            response = await client.post(
                OLLAMA_BASE_URL + "/api/generate",
                json={"model": request.model, "keep_alive": 0, "stream": False},
            )
            response.raise_for_status()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(502, "Не удалось запросить выгрузку модели через Ollama") from exc
    return {"status": "unload_requested", "model": request.model,
            "note": "Обнови мониторинг: фактическая свободная VRAM зависит и от других процессов"}


async def execute_generation(job_id: str, payload: dict, mode: str) -> None:
    """Stream tokens into SQLite without depending on the browser connection."""
    began = time.monotonic()
    output = ""
    estimated_tokens = 0
    last_saved = 0.0
    try:
        with db() as connection:
            update_job(connection, job_id, stage="Ожидание ответа Ollama")
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=8, read=240, write=30, pool=10)) as client:
            async with client.stream("POST", OLLAMA_BASE_URL + "/api/generate", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError("Неверный формат ответа Ollama")
                    if item.get("error"):
                        raise ValueError("Ollama вернула ошибку во время генерации")
                    piece = item.get("response", "")
                    if not isinstance(piece, str):
                        raise ValueError("Ожидался текст ответа Ollama")
                    output += piece
                    if len(output) > MAX_OUTPUT:
                        raise ValueError("Достигнут предел длины черновика")
                    elapsed = time.monotonic() - began
                    percent, eta, estimated_tokens = estimate_progress(len(output), elapsed)
                    if elapsed - last_saved >= 1.0 and (piece or item.get("done")):
                        with db() as connection:
                            update_job(connection, job_id, stage="Генерация текста",
                                       output=output, percent=percent, eta_seconds=eta,
                                       estimated_tokens=estimated_tokens)
                        last_saved = elapsed
                    if item.get("done"):
                        break
        if not output.strip():
            raise ValueError("Ollama вернула пустой ответ")
        suggested = None
        parse_error = None
        if mode == "outline":
            try:
                suggested = [scene.model_dump() for scene in parse_outline(output)]
            except (ValueError, TypeError) as exc:
                parse_error = "Ответ не является корректной структурой сцен"
        with db() as connection:
            update_job(connection, job_id, status="ready", stage=("Отчёт QA готов · выбери действия по правкам" if mode == "audit" else "Черновик готов · ожидает принятия автором"),
                       output=output, percent=100, eta_seconds=0,
                       estimated_tokens=estimated_tokens, parse_error=parse_error,
                       suggested_scenes=suggested)
    except asyncio.CancelledError:
        with db() as connection:
            update_job(connection, job_id, status="interrupted", stage="Остановлено при закрытии сервера",
                       output=output, error="Часть черновика сохранена; сервер был остановлен",
                       estimated_tokens=estimated_tokens)
        raise
    except Exception:
        # Unexpected failures must close the durable job instead of leaving a
        # permanent phantom 'running' record. Never expose upstream secrets.
        with db() as connection:
            update_job(connection, job_id, status="error", stage="Генерация завершилась ошибкой",
                       output=output, error="Не удалось завершить запрос к Ollama; сохранён полученный текст",
                       estimated_tokens=estimated_tokens)


@api.post("/ai", status_code=202)
async def generate(request: AIRequest):
    """Enqueue durable generation before returning. Page reload cannot abort it."""
    project = get_project(request.project_id)
    prompt = build_prompt(project, request)
    payload = {
        "model": request.model, "prompt": prompt,
        "stream": True, "think": False, "keep_alive": "5m",
        "options": {"temperature": 0.7, "num_predict": LIMIT},
    }
    if request.mode == "outline":
        payload["format"] = "json"
    job_id = uuid.uuid4().hex
    try:
        with db() as connection:
            record = create_job(connection, job_id, request.project_id,
                                request.mode, request.model, request.scene_id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Уже выполняется другая генерация ACHEWIW. Дождись её завершения.") from exc
    task = asyncio.create_task(execute_generation(job_id, payload, request.mode))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return record


@api.get("/ai/jobs/{job_id}")
def generation_status(job_id: str):
    with db() as connection:
        result = get_job(connection, job_id)
    if result is None:
        raise HTTPException(404, "Задание генерации не найдено")
    return result


class AuditDecisionRequest(BaseModel):
    decision: Literal["unreviewed", "in_progress", "completed", "reference"]
    notes: str = Field(default="", max_length=3000)


@api.put("/ai/jobs/{job_id}/decision")
def decide_audit(job_id: str, request: AuditDecisionRequest):
    with db() as connection:
        try:
            return save_audit_decision(connection, job_id, request.decision, request.notes)
        except ValueError as exc:
            raise HTTPException(422, "Можно отмечать только завершённые редакторские аудиты") from exc


@api.get("/ai/jobs/{job_id}/report.md")
def download_audit_report(job_id: str):
    with db() as connection:
        record = get_job(connection, job_id)
    if record is None or record["mode"] != "audit" or record["status"] != "ready":
        raise HTTPException(404, "Готовый отчёт QA не найден")
    text = (
        f"# Редакторский аудит: {get_project(record['project_id']).title}\n\n"
        f"**Модель:** {record['model']}\n\n"
        f"**Решение автора:** {record['author_decision']}\n\n"
        f"**Заметки автора:** {record['author_notes']}\n\n---\n\n"
        f"{record['result']}\n"
    )
    return PlainTextResponse(
        text, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="achewiw-qa-report.md"',
                 "Cache-Control": "no-store"},
    )


@api.get("/projects/{project_id}/ai/jobs")
def project_generation_history(project_id: str,
                               mode: Literal["outline", "scene", "audit"] | None = None,
                               summary: bool = False):
    get_project(project_id)
    with db() as connection:
        items = recent_jobs(connection, project_id, mode=mode)
    if summary:
        return [
            {"id": j["id"], "mode": j["mode"], "status": j["status"],
             "updated_at": j["updated_at"],
             "author_decision": j.get("author_decision", "unreviewed")}
            for j in items
        ]
    return items


app.include_router(api)
