"""Project-scoped artwork catalogue. Real uploaded PNG assets, never external URLs.

Images are saved under data/illustrations/<project-id>/<asset-id>.png and can
be placed before a matching authored chapter in EPUB, DOCX and A5 PDF.
"""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import re
import sqlite3
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from book_layout_core import COVER_SCENE_ID

ID = re.compile(r"^[0-9a-f]{32}$")
MAX_UPLOAD = 12 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 24_000_000


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS illustrations(
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            scene_id TEXT NOT NULL,
            caption TEXT NOT NULL,
            created_at TEXT NOT NULL,
            placement TEXT NOT NULL DEFAULT "start",
            paragraph_index INTEGER NOT NULL DEFAULT 0
        )"""
    )
    columns = {r[1] for r in conn.execute("PRAGMA table_info(illustrations)")}
    if "placement" not in columns:
        conn.execute("ALTER TABLE illustrations ADD COLUMN placement TEXT NOT NULL DEFAULT 'start'")
    if "paragraph_index" not in columns:
        conn.execute("ALTER TABLE illustrations ADD COLUMN paragraph_index INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_illustrations_project ON illustrations(project_id)")


def checked_path(data_dir: Path, project_id: str, asset_id: str) -> Path:
    if not ID.fullmatch(project_id) or not ID.fullmatch(asset_id):
        raise ValueError("Некорректный ID иллюстрации")
    base = data_dir / "illustrations"
    folder = base / project_id
    for part in (base, folder):
        if part.is_symlink():
            raise ValueError("Символические ссылки в каталоге изображений запрещены")
    return folder / (asset_id + ".png")


def normalize_png(data: bytes) -> bytes:
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("Файл должен быть изображением до 12 МиБ")
    try:
        with Image.open(BytesIO(data)) as img:
            if img.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Допускаются PNG, JPEG и WebP")
            if img.width * img.height > 24_000_000:
                raise ValueError("Слишком высокое разрешение")
            clean = ImageOps.exif_transpose(img)
            clean.load()
            if clean.mode not in {"RGB", "RGBA"}:
                clean = clean.convert("RGBA" if "A" in clean.getbands() else "RGB")
            output = BytesIO()
            clean.save(output, format="PNG", optimize=True)
            if output.tell() > 50 * 1024 * 1024:
                raise ValueError("Изображение слишком велико после преобразования")
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Не удалось безопасно прочитать изображение") from error


def save(data_dir: Path, conn: sqlite3.Connection, *,
         project_id: str, scene_id: str, caption: str,
         content: bytes, created_at: str, placement: str = "start",
         paragraph_index: int = 0) -> dict:
    if not ID.fullmatch(project_id) or not ID.fullmatch(scene_id):
        raise ValueError("Неверный проект или раздел")
    if placement not in {"start", "end", "paragraph", "cover"}:
        raise ValueError("Некорректное расположение иллюстрации")
    if type(paragraph_index) is not int or paragraph_index < 0 or paragraph_index > 5000:
        raise ValueError("Номер абзаца должен быть от 0 до 5000")
    if placement == "paragraph" and paragraph_index < 1:
        raise ValueError("Для точной вставки укажи номер абзаца, начиная с 1")
    if (placement == "cover") != (scene_id == COVER_SCENE_ID):
        raise ValueError("Обложка должна иметь собственный раздел, отдельно от глав")
    if placement == "cover" and conn.execute(
        "SELECT 1 FROM illustrations WHERE project_id=? AND placement='cover'",
        (project_id,)).fetchone():
        raise ValueError("Обложка уже есть — сначала удали старую обложку")
    if len(caption) > 1000:
        raise ValueError("Подпись слишком длинная")
    count = conn.execute(
        "SELECT COUNT(*) FROM illustrations WHERE project_id=?", (project_id,)
    ).fetchone()[0]
    if count >= 40:
        raise ValueError("В одном издании допускается до 40 иллюстраций")
    blob = normalize_png(content)
    asset_id = uuid.uuid4().hex
    path = checked_path(data_dir, project_id, asset_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as dst:
            dst.write(blob)
        conn.execute(
            "INSERT INTO illustrations (id,project_id,scene_id,caption,created_at,placement,paragraph_index) VALUES (?,?,?,?,?,?,?)",
            (asset_id, project_id, scene_id, caption.strip(), created_at, placement, paragraph_index),
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"id": asset_id, "project_id": project_id, "scene_id": scene_id,
            "caption": caption.strip(), "created_at": created_at, "placement": placement,
            "paragraph_index": paragraph_index}


def listing(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    if not ID.fullmatch(project_id):
        raise ValueError("Некорректный ID проекта")
    records = conn.execute(
        "SELECT id,scene_id,caption,created_at,placement,paragraph_index FROM illustrations WHERE project_id=? ORDER BY created_at,id",
        (project_id,),
    ).fetchall()
    return [dict(id=row[0], scene_id=row[1], caption=row[2],
                 created_at=row[3], placement=row[4],paragraph_index=row[5]) for row in records]


def asset_for_export(data_dir: Path, conn: sqlite3.Connection, project_id: str) -> list[dict]:
    result = []
    for record in listing(conn, project_id):
        path = checked_path(data_dir, project_id, record["id"])
        if path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_size > 50 * 1024 * 1024:
            continue
        result.append({**record, "path": path})
    return result
