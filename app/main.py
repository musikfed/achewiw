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
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from gpu_scheduling import init_gpu_leases, image_operation_active, reserve_gpu, release_gpu
from monitoring_core import load_snapshot, summarize_ollama_running
from illustrations_core import initialize as init_illustrations, save as save_illustration, listing as list_illustrations, checked_path as illustration_path, asset_for_export as export_illustrations
from image_engine import status as illustration_engine_status, generate as generate_illustration_image
from publishing_exports import manuscript_pdf, manuscript_docx, manuscript_fb2
from manuscript_core import chapter_listing, manuscript_markdown, manuscript_epub
from writing_blocks_core import fingerprint, words, editorial_lint, preflight as story_preflight, block_prompt, review_prompt
from generation_core import (init_jobs, mark_interrupted, create_job, update_job,
                             recent_jobs, get_job, active_job, estimate_progress, save_audit_decision,
                             LIMIT, MAX_OUTPUT, authored_revisions, revision_detail)

DATA_DIR = Path(os.getenv("ACHEWIW_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "achewiw.sqlite3"
STATIC_DIR = Path(__file__).parent / "static"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("ACHEWIW_MODEL", "qwen3:8b")
COMFYUI_CONTROL_URL = "http://127.0.0.1:8190"
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
        init_gpu_leases(connection)
        init_illustrations(connection)
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


app = FastAPI(title="ACHEWIW StoryEngine", version="0.5.0", lifespan=lifespan)
api = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


class SceneBlock(BaseModel):
    """Author-described segment; generated text never auto-accepted."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    description: str = Field(default="", max_length=4000)
    target_words: int = Field(default=350, ge=80, le=1000)
    requires: list[str] = Field(default_factory=list, max_length=20)
    introduces: list[str] = Field(default_factory=list, max_length=20)
    caused_by: list[str] = Field(default_factory=list, max_length=15)
    forbidden_inventions: list[str] = Field(default_factory=list, max_length=20)
    style: str = Field(default="", max_length=1000)
    accepted_job_id: str | None = None
    accepted_at: str | None = None
    evidence_stale: bool = False


class Scene(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    title: str = Field(default="Новая сцена", max_length=200)
    active_objects: list[str] = Field(default_factory=list, max_length=30)
    anchor: str = Field(default="", max_length=2000)
    tempo: str = Field(default="Ровный", max_length=200)
    reader_info: str = Field(default="", max_length=4000)
    hook: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=150000)
    blocks: list[SceneBlock] = Field(default_factory=list, max_length=80)


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
    mode: Literal["outline", "scene", "audit", "block", "continuity", "polish"]
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=200)
    scene_id: str | None = None
    block_id: str | None = None
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
    project.updated_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
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
            "отрывок": scene.text[:9500] if request.mode == "audit" else scene.text[:1000],
        }
        for scene in project.scenes[:40]
    ]
    extra = f"\nДополнительное пожелание автора: {request.instructions}" if request.instructions else ""

    if request.mode == "block":
        if project.work_type != "fiction":
            raise HTTPException(422, "Поблочная художественная генерация доступна в мастерской прозы")
        selected = next((s for s in project.scenes if s.id == request.scene_id), None)
        block = next((b for b in selected.blocks if b.id == request.block_id), None) if selected else None
        if not block:
            raise HTTPException(422, "Сначала опиши блок сцены и его объём")
        if block.accepted_job_id:
            raise HTTPException(409, "Этот блок уже принят. Создай новый блок для дальнейшего текста.")
        return block_prompt(project, selected, block, request.instructions)
    if request.mode == "polish":
        selected = next((s for s in project.scenes if s.id == request.scene_id), None)
        if not selected or not selected.text.strip():
            raise HTTPException(422, "Сначала сохрани текст сцены для литературной редактуры")
        if len(selected.text) > 16000:
            raise HTTPException(422, "Для качественной редактуры выдели сцену до 16000 символов")
        return (
            "Ты литературный редактор. Верни только исправленную русскую прозу, "
            "без заголовков, списков, объяснений, рецензии и оценки своей работы. "
            "Убери речевые штампы, повторы синтаксиса, безликие эмоции, чрезмерные "
            "вводные, искусственные финалы и выспренность. Сохрани авторскую интонацию, "
            "все факты, логику событий, хронологию, имена, предметы и мотивы. "
            "Новых предметов, персонажей, событий или способностей не добавляй. "
            "Автор сам решает, принимать ли правку. Пожелание: "
            + request.instructions[:2000] + "\nИсходный текст:\n" + selected.text
        )
    if request.mode == "continuity":
        if project.work_type != "fiction":
            raise HTTPException(422, "Сюжетная проверка предназначена для художественной прозы")
        findings = story_preflight(project, request.scene_id)
        return (review_prompt(project, request.instructions, scene_id=request.scene_id) +
                "\nРеестр явно заявленных автором фактов (не доказательства ошибок):\n" +
                json.dumps(findings, ensure_ascii=False))[:35000]
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
            "Для аудита переданы тексты в пределах общего контекстного окна. "
            "Не утверждай, что прочёл целиком обрезанные главы. "
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
def asset(filename: Literal["app.js", "style.css", "monitor.js", "illustrations.js", "writing_blocks.js"], _=Depends(require_auth)):
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
    """Protect authored text and accepted blocks from stale browser tabs."""
    if project.id != project_id:
        raise HTTPException(400, "Project ID mismatch")
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row=connection.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Проект не найден")
        previous=Project.model_validate_json(row["data"])
        if previous.updated_at != project.updated_at:
            raise HTTPException(409, "Рукопись изменена в другой вкладке или после принятия ИИ-правки. Перезагрузи проект.")
        existing_blocks={b.id:b for s in previous.scenes for b in s.blocks}
        for scene in project.scenes:
            for block in scene.blocks:
                accepted=existing_blocks.get(block.id)
                if block.accepted_job_id and (
                        accepted is None or accepted.model_dump() != block.model_dump()):
                    raise HTTPException(409, "Принятые блоки нельзя незаметно менять в плане. Создай новый фрагмент.")
        # Direct author editing can remove a previously introduced object.
        # Do not silently treat the old accepted-block ledger as fresh.
        original_scenes={s.id:s for s in previous.scenes}
        for scene in project.scenes:
            old_scene=original_scenes.get(scene.id)
            if old_scene and old_scene.text != scene.text:
                for block in scene.blocks:
                    if block.accepted_job_id:
                        block.evidence_stale = True
        project.updated_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        connection.execute(
            "UPDATE projects SET data=?,updated_at=? WHERE id=?",
            (project.model_dump_json(), project.updated_at, project_id),
        )
    return project


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
        # Files retained inside local data/ for recovery from SQLite backup.
        # A deliberate project-scoped cleanup can remove illustrations later.
        connection.execute("DELETE FROM illustrations WHERE project_id=?", (project_id,))
    return Response(status_code=204)


@api.get("/projects/{project_id}/illustrations")
def illustrations_list(project_id: str):
    get_project(project_id)
    with db() as connection:
        return list_illustrations(connection, project_id)


@api.post("/projects/{project_id}/illustrations", status_code=201)
async def illustrations_upload(project_id: str, file: UploadFile = File(...),
                               scene_id: str = Form(...), caption: str = Form(""),
                               placement: str = Form("start")):
    project = get_project(project_id)
    if scene_id not in {scene.id for scene in project.scenes}:
        raise HTTPException(422, "Для иллюстрации выбери существующую главу или сцену")
    if file.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(415, "Загрузи PNG, JPEG или WebP")
    if len(caption) > 1000:
        raise HTTPException(422, "Слишком длинная подпись")
    payload = await file.read(12 * 1024 * 1024 + 1)
    try:
        with db() as connection:
            return save_illustration(DATA_DIR, connection,
                 project_id=project_id, scene_id=scene_id,
                 caption=caption, content=payload, created_at=utc_now(), placement=placement)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@api.get("/projects/{project_id}/illustrations/{asset_id}.png")
def illustrations_image(project_id: str, asset_id: str):
    get_project(project_id)
    with db() as connection:
        if not any(rec["id"] == asset_id for rec in list_illustrations(connection, project_id)):
            raise HTTPException(404, "Иллюстрация не найдена")
    try:
        path = illustration_path(DATA_DIR, project_id, asset_id)
    except ValueError:
        raise HTTPException(404, "Иллюстрация не найдена") from None
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, "Файл иллюстрации отсутствует")
    return FileResponse(path, media_type="image/png",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@api.delete("/projects/{project_id}/illustrations/{asset_id}", status_code=204)
def illustrations_delete(project_id: str, asset_id: str):
    get_project(project_id)
    try:
        path = illustration_path(DATA_DIR, project_id, asset_id)
    except ValueError:
        raise HTTPException(404, "Иллюстрация не найдена") from None
    with db() as connection:
        removed = connection.execute(
            "DELETE FROM illustrations WHERE id=? AND project_id=?",
            (asset_id, project_id)).rowcount
    if not removed:
        raise HTTPException(404, "Иллюстрация не найдена")
    if not path.is_symlink():
        path.unlink(missing_ok=True)
    return Response(status_code=204)


def illustrations_for_book(project_id: str) -> list[dict]:
    with db() as connection:
        return export_illustrations(DATA_DIR, connection, project_id)


@api.get("/illustration-engine")
async def get_illustration_engine():
    """Only local image-generation engines; Ollama vision models are not image creators."""
    return await illustration_engine_status()


class ComfyUIControlRequest(BaseModel):
    action: Literal["install", "model-sdxl", "start", "stop"]


async def _comfyui_control_request(method: str, path: str,
                                   payload: dict | None = None) -> dict:
    token_path = DATA_DIR / "comfyui-control.token"
    if token_path.is_symlink() or not token_path.is_file():
        raise HTTPException(
            503, "Кнопочное управление ComfyUI ещё не установлено. Обнови ACHEWIW с GitHub."
        )
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        raise HTTPException(503, "Не удалось прочитать закрытый токен host-control") from None
    if len(token) < 40:
        raise HTTPException(503, "Некорректный токен host-control")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=20.0, write=5.0, pool=2.0),
            trust_env=False,
        ) as client:
            response = await client.request(
                method, COMFYUI_CONTROL_URL + path,
                headers={"X-ACHEWIW-Control-Token": token}, json=payload,
            )
    except httpx.HTTPError:
        raise HTTPException(
            503, "Хостовый контроллер ComfyUI недоступен. Проверь обновление ACHEWIW."
        ) from None
    try:
        result = response.json()
    except ValueError:
        result = {"detail": "Хостовый контроллер вернул некорректный ответ"}
    if response.status_code >= 400:
        raise HTTPException(response.status_code,
                            result.get("detail", "Команда ComfyUI не выполнена"))
    return result


@api.get("/illustration-engine/control")
async def illustration_engine_control_status():
    return await _comfyui_control_request("GET", "/status")


@api.post("/illustration-engine/control", status_code=202)
async def illustration_engine_control(request: ComfyUIControlRequest):
    with db() as connection:
        if active_job(connection) or image_operation_active(connection):
            raise HTTPException(
                409, "Сначала дождись окончания текущей задачи ACHEWIW на GPU."
            )
    return await _comfyui_control_request(
        "POST", "/control", {"action": request.action}
    )


@api.get("/illustration-engine/control/jobs/{job_id}")
async def illustration_engine_control_job(job_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "Задача управления не найдена")
    return await _comfyui_control_request("GET", "/jobs/" + job_id)


class ImageGenerationRequest(BaseModel):
    scene_id: str
    prompt: str = Field(min_length=1, max_length=2500)
    caption: str = Field(default="", max_length=1000)
    placement: Literal["start", "end"] = "start"
    release_comfy_vram: bool = False


class ImagePromptRequest(BaseModel):
    description: str = Field(min_length=3, max_length=2000)
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=200)
    confirm_shared_model: bool = False


@api.post("/illustration-prompt/refine")
async def refine_illustration_prompt(request: ImagePromptRequest):
    """Optional one-pass Ollama translation/composition, BEFORE ComfyUI work.
    Selected shared Ollama model is released with keep_alive=0 only on consent.
    Author gets editable result and explicitly launches image generation later.
    """
    if not request.confirm_shared_model:
        raise HTTPException(422, "Подтверди использование и последующую выгрузку общей Ollama-модели")
    token = uuid.uuid4().hex
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if active_job(connection) or not reserve_gpu(connection, token, "prompt_refinement"):
            raise HTTPException(409, "Другая задача ACHEWIW использует GPU; дождись окончания")
    try:
        prompt = (
            "Write a concise high-quality ENGLISH image generation prompt for SDXL. "
            "Return ONLY the visual prompt in plain text, no Markdown, no commentary. "
            "Keep the exact characters, animals, objects, locations, era and factual details "
            "provided by the author. Do not add extra characters, objects, text or fantasy "
            "features. Specify composition, lighting, medium and book illustration style only "
            "if the user's description implies them. Maximum 120 English words. "
            "Source description (Russian or English):\n" + request.description
        )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=8, read=120, write=20, pool=10),
            trust_env=False,
        ) as client:
            tags = await client.get(OLLAMA_BASE_URL + "/api/tags")
            tags.raise_for_status()
            names = [m.get("name") for m in tags.json().get("models", [])
                     if isinstance(m, dict)]
            if request.model not in names:
                raise HTTPException(422, "Выбранной текстовой модели нет в Ollama")
            response = await client.post(OLLAMA_BASE_URL + "/api/generate", json={
                "model": request.model, "prompt": prompt,
                "stream": False, "think": False, "keep_alive": 0,
                "options": {"temperature": 0.2, "num_predict": 320},
            })
            response.raise_for_status()
            prepared = response.json().get("response", "")
            if not isinstance(prepared, str) or not prepared.strip():
                raise HTTPException(502, "Ollama вернула пустой промпт")
            prepared = prepared.strip().strip("`").strip()
            if len(prepared) > 2500:
                raise HTTPException(502, "Промпт модели слишком длинный")
        return {
            "prepared_prompt": prepared, "model": request.model,
            "status": "author_review_required",
            "note": "Промпт не передан ComfyUI до решения автора. keep_alive=0 запросил выгрузку выбранной LLM.",
        }
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(502, "Не удалось подготовить описание через локальную Ollama") from exc
    finally:
        with db() as connection:
            release_gpu(connection, token)


@api.post("/projects/{project_id}/illustrations/generate", status_code=201)
async def generate_book_illustration(project_id: str, request: ImageGenerationRequest):
    """Sequential local image generation; never silently share loaded Ollama VRAM."""
    project = get_project(project_id)
    if request.scene_id not in {scene.id for scene in project.scenes}:
        raise HTTPException(422, "Укажи существующую главу")
    token = uuid.uuid4().hex
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if active_job(connection) or not reserve_gpu(connection, token, "comfyui_image"):
            raise HTTPException(409, "Другая задача ACHEWIW использует GPU; дождись её окончания")
    try:
        engine = await illustration_engine_status()
        if not engine.get("available"):
            raise HTTPException(503, engine.get("reason", "Графический движок недоступен"))
        # Fail CLOSED: no option to start diffusion while ANY Ollama model
        # remains in GPU, because 12 GB may not fit both. This is a read-only
        # query. Other host applications can still use GPU outside ACHEWIW.
        try:
            async with httpx.AsyncClient(timeout=3.5, trust_env=False) as client:
                loaded = await client.get(OLLAMA_BASE_URL + "/api/ps")
                loaded.raise_for_status()
                present = summarize_ollama_running(loaded.json())
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(503, "Не удалось проверить общую Ollama. Изображение не запускаем") from None
        if present:
            raise HTTPException(
                409, "Ollama ещё держит модель в GPU. Выгрузи нужную модель по отдельному подтверждению "
                     "и повтори генерацию; одновременную загрузку не разрешаем."
            )
        try:
            png, checkpoint = await generate_illustration_image(request.prompt)
            with db() as connection:
                result = save_illustration(DATA_DIR, connection,
                    project_id=project_id, scene_id=request.scene_id,
                    caption=request.caption, content=png,
                    created_at=utc_now(), placement=request.placement)
        except (ValueError, TimeoutError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, "ComfyUI не завершил генерацию") from exc
        released = "not_requested"
        if request.release_comfy_vram:
            from image_engine import free_cached_models
            released = await free_cached_models()
        return {
            "illustration": result, "checkpoint": checkpoint,
            "prompt_used": request.prompt, "comfy_vram": released,
            "note": "Картинка сохранена. Ollama не запускалась и не выгружалась в этом запросе.",
        }
    finally:
        with db() as connection:
            release_gpu(connection, token)


@api.get("/projects/{project_id}/continuity")
def continuity_report(project_id: str):
    p = get_project(project_id)
    return {"causality": story_preflight(p),
            "style": [{"scene_id": s.id, "title": s.title,
                       "report": editorial_lint(s.text)} for s in p.scenes if s.text.strip()]}


class AcceptanceRequest(BaseModel):
    confirm: Literal[True]


@api.post("/ai/jobs/{job_id}/accept")
def accept_written_proposal(job_id: str, request: AcceptanceRequest):
    """Atomically accept one AI-generated block or stylistic rewrite.

    The source text hash is captured BEFORE inference. If an editor changed
    the text meanwhile, acceptance is rejected rather than overwriting it.
    Each accepted revision remains stored in SQLite and is undoable.
    """
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        job = get_job(connection, job_id)
        if not job or job["status"] != "ready" or job["mode"] not in ("block", "polish", "scene"):
            raise HTTPException(422, "Можно принять только завершённую генерацию сцены, блока или редактуру")
        if job.get("accepted"):
            raise HTTPException(409, "Это предложение уже принималось")
        row = connection.execute("SELECT data FROM projects WHERE id=?", (job["project_id"],)).fetchone()
        if row is None:
            raise HTTPException(404, "Рукопись удалена")
        p = Project.model_validate_json(row["data"])
        scene = next((s for s in p.scenes if s.id == job["scene_id"]), None)
        if scene is None:
            raise HTTPException(409, "Сцена удалена или перемещена")
        if not job.get("base_text_sha256") or fingerprint(scene.text) != job["base_text_sha256"]:
            raise HTTPException(409, "Текст сцены изменился после генерации; открой предложение и проверь новую версию")
        original = scene.text
        proposed = job["result"].strip()
        if not proposed or len(proposed) > MAX_OUTPUT:
            raise HTTPException(422, "Невозможно принять пустой или слишком длинный черновик")
        selected = None
        if job["mode"] == "block":
            selected = next((b for b in scene.blocks if b.id == job["block_id"]), None)
            if selected is None or selected.accepted_job_id:
                raise HTTPException(409, "Блок удалён или уже был принят")
            scene.text = (scene.text.rstrip() + "\n\n" + proposed).strip()
            selected.accepted_job_id = job_id
            selected.accepted_at = utc_now()
            selected.evidence_stale = False
        else:
            scene.text = proposed
            # Rewriting the scene invalidates evidence anchored in older
            # block versions; keep history but require revalidation.
            if proposed != original:
                for earlier in scene.blocks:
                    if earlier.accepted_job_id:
                        earlier.evidence_stale = True
        if len(scene.text) > 150000:
            raise HTTPException(422, "Сцена превышает предельный размер")
        revision_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO author_text_revisions "
            "(id,project_id,scene_id,job_id,previous_text,accepted_text,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (revision_id, p.id, scene.id, job_id, original, scene.text, utc_now()),
        )
        p.updated_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        connection.execute(
            "UPDATE projects SET data=?, updated_at=? WHERE id=?",
            (p.model_dump_json(), p.updated_at, p.id),
        )
        result = {
            "status": "accepted_by_author", "revision_id": revision_id,
            "scene_id": scene.id, "scene_text": scene.text,
            "words": words(proposed), "mode": job["mode"],
        }
        if selected:
            result["target_words"] = selected.target_words
            result["within_15_percent"] = (abs(words(proposed) - selected.target_words) <=
                                           max(1, int(selected.target_words * 0.15)))
    return result


@api.get("/projects/{project_id}/scenes/{scene_id}/revisions")
def list_scene_revisions(project_id: str, scene_id: str):
    p = get_project(project_id)
    if not any(s.id == scene_id for s in p.scenes):
        raise HTTPException(404, "Сцена не найдена")
    with db() as connection:
        return authored_revisions(connection, project_id, scene_id)


class RestoreRevisionRequest(BaseModel):
    confirm: Literal[True]


@api.post("/projects/{project_id}/scenes/{scene_id}/revisions/{revision_id}/restore")
def restore_scene_revision(project_id: str, scene_id: str, revision_id: str,
                           request: RestoreRevisionRequest):
    """Undo last accepted text revision only if the current text still matches."""
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        record = revision_detail(connection, revision_id)
        if record is None or record["project_id"] != project_id or record["scene_id"] != scene_id:
            raise HTTPException(404, "Ревизия не найдена")
        row = connection.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Проект не найден")
        p = Project.model_validate_json(row["data"])
        scene = next((s for s in p.scenes if s.id == scene_id), None)
        if scene is None or scene.text != record["accepted_text"]:
            raise HTTPException(409, "После ревизии текст изменён; откат без потери правок невозможен")
        scene.text = record["previous_text"]
        for block in scene.blocks:
            if block.accepted_job_id == record["job_id"]:
                block.accepted_job_id = None
                block.accepted_at = None
                block.evidence_stale = False
        # Previously accepted facts need revalidation after any text restore.
        for block in scene.blocks:
            if block.accepted_job_id:
                block.evidence_stale = True
        # Retain the old revision as evidence, but a fresh acceptance must
        # create a new job. This undo itself is also recorded and reversible.
        undo_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO author_text_revisions "
            "(id,project_id,scene_id,job_id,previous_text,accepted_text,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (undo_id, project_id, scene_id, "undo:"+undo_id,
             record["accepted_text"], record["previous_text"], utc_now()),
        )
        p.updated_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        connection.execute("UPDATE projects SET data=?, updated_at=? WHERE id=?",
                           (p.model_dump_json(), p.updated_at, project_id))
    return {"status": "restored_by_author", "scene_id": scene_id,
            "scene_text": record["previous_text"], "undo_revision_id": undo_id}


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
        body = manuscript_epub(project, illustrations=illustrations_for_book(project_id))
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
        body = manuscript_docx(get_project(project_id), illustrations=illustrations_for_book(project_id))
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
        body = manuscript_pdf(get_project(project_id), illustrations=illustrations_for_book(project_id))
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
        body = manuscript_pdf(get_project(project_id), print_proof=True, illustrations=illustrations_for_book(project_id))
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
        if active_job(connection) or image_operation_active(connection):
            raise HTTPException(409, "В ACHEWIW занята GPU-задача; освобождение памяти запрещено")
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
                    percent, eta, estimated_tokens = estimate_progress(len(output), elapsed, payload["options"]["num_predict"])
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
    current_scene = next((s for s in project.scenes if s.id == request.scene_id), None)
    if request.mode in ("scene", "block", "polish") and not current_scene:
        raise HTTPException(422, "Выбери существующую сцену")
    selected_block = None
    if request.mode == "block" and current_scene:
        selected_block = next((b for b in current_scene.blocks if b.id == request.block_id), None)
        if not selected_block or selected_block.accepted_job_id:
            raise HTTPException(409, "Указанного непринятого блока нет в сцене")
    # Text-only Ollama: a bounded word target is more useful than a fake
    # guaranteed output length. Actual word count is shown after completion.
    token_budget = min(4200, max(1100, round(selected_block.target_words * 3.1))) if selected_block else LIMIT
    payload = {
        "model": request.model, "prompt": prompt,
        "stream": True, "think": False, "keep_alive": "5m",
        "options": {"temperature": 0.72 if request.mode in ("scene", "block") else 0.35,
                    "num_predict": token_budget},
    }
    if request.mode == "outline":
        payload["format"] = "json"
    job_id = uuid.uuid4().hex
    try:
        with db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if image_operation_active(connection):
                raise HTTPException(409, "Идёт подготовка иллюстрации или генерация ComfyUI. Дождись окончания.")
            record = create_job(connection, job_id, request.project_id,
                                request.mode, request.model, request.scene_id,
                                block_id=selected_block.id if selected_block else None,
                                base_text_sha256=fingerprint(current_scene.text) if current_scene else None,
                                token_budget=token_budget)
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
    if record is None or record["mode"] not in ("audit", "continuity") or record["status"] != "ready":
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
                               mode: Literal["outline", "scene", "audit", "block", "continuity", "polish"] | None = None,
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
