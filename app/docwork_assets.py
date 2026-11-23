"""R3.6: immutable replacement artwork scoped to an authorized DocWork draft.

A reference in the editorial state links an image index to an immutable PNG
asset; no asset ever rewrites the original DOCX. Old assets remain available for
Undo/R3 revision restore and are removed ONLY with explicit draft deletion.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import sqlite3
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from docwork_sessions import SessionError, _require

MAX_UPLOAD = 10 * 1024 * 1024
MAX_PNG = 12 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_ASSETS = 120
MAX_STORED = 120 * 1024 * 1024


def initialize(connection: sqlite3.Connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS docwork_image_assets (
        id TEXT PRIMARY KEY,
        draft_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        content BLOB NOT NULL,
        sha256 TEXT NOT NULL,
        width_px INTEGER NOT NULL,
        height_px INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(draft_id) REFERENCES docwork_drafts(id)
    )""")
    connection.execute("""CREATE INDEX IF NOT EXISTS idx_docwork_image_assets_draft
        ON docwork_image_assets(draft_id)""")


def normalize_image(upload: bytes):
    if not isinstance(upload, bytes) or not upload or len(upload) > MAX_UPLOAD:
        raise SessionError(413, "Иллюстрация должна быть меньше 10 МиБ")
    try:
        with Image.open(BytesIO(upload)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise SessionError(422, "Для замены принимаются PNG, JPEG и WebP")
            w,h=source.size
            if w < 8 or h < 8 or w*h > MAX_PIXELS:
                raise SessionError(422, "Иллюстрация должна содержать 8–16 млн пикселей или меньше")
            source.load()
            # Respect EXIF orientation and strip metadata. Keep transparency.
            fixed = ImageOps.exif_transpose(source)
            fixed = fixed.convert("RGBA" if ("A" in fixed.getbands() or
                                   "transparency" in fixed.info) else "RGB")
            w,h=fixed.size
            out=BytesIO()
            fixed.save(out,"PNG",optimize=True)
            png=out.getvalue()
    except SessionError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError) as exc:
        raise SessionError(422, "Изображение не удалось безопасно открыть") from exc
    if len(png)>MAX_PNG:
        raise SessionError(413, "Полученный PNG превышает 12 МиБ")
    return png,w,h


def add(connection, draft_id: str, token: str, filename: str, data: bytes):
    _require(connection,draft_id,token)
    if not isinstance(filename,str) or not filename or len(filename)>180:
        raise SessionError(422, "Некорректное имя иллюстрации")
    filename=filename.replace("\\","/").rsplit("/",1)[-1]
    png,w,h=normalize_image(data)
    digest=sha256(png).hexdigest()
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        _require(connection,draft_id,token)
        found=connection.execute(
            "SELECT id, width_px, height_px FROM docwork_image_assets "
            "WHERE draft_id=? AND sha256=?",(draft_id,digest)
        ).fetchone()
        if found:
            return {"id":found["id"],"width_px":found["width_px"],
                    "height_px":found["height_px"],"sha256":digest,
                    "size_bytes":len(png)}
        count,size=connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)),0) "
            "FROM docwork_image_assets WHERE draft_id=?",(draft_id,)
        ).fetchone()
        if count>=MAX_ASSETS or size+len(png)>MAX_STORED:
            raise SessionError(409,
                "Лимит рисунков черновика (120 или 120 МиБ). "
                "Сохраните экспорт и создайте новый черновик")
        asset_id=uuid.uuid4().hex
        connection.execute(
            "INSERT INTO docwork_image_assets "
            "(id,draft_id,filename,content,sha256,width_px,height_px,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (asset_id,draft_id,filename,sqlite3.Binary(png),digest,w,h,
             datetime.now(timezone.utc).isoformat())
        )
    return {"id":asset_id,"width_px":w,"height_px":h,
            "sha256":digest,"size_bytes":len(png)}


def get(connection, draft_id: str, token: str, asset_id: str):
    _require(connection,draft_id,token)
    if not isinstance(asset_id,str) or len(asset_id)!=32 or any(
        c not in "0123456789abcdef" for c in asset_id
    ):
        raise SessionError(404,"Иллюстрация не найдена")
    row=connection.execute(
        "SELECT content,sha256 FROM docwork_image_assets "
        "WHERE draft_id=? AND id=?",(draft_id,asset_id)
    ).fetchone()
    if not row:
        raise SessionError(404,"Иллюстрация не найдена")
    data=bytes(row["content"])
    if sha256(data).hexdigest()!=row["sha256"]:
        raise SessionError(409,"Контрольная сумма рисунка не совпала")
    return data


def replacements(connection, draft_id: str, token: str, choices: dict):
    _require(connection,draft_id,token)
    if not isinstance(choices,dict) or len(choices)>100:
        raise SessionError(422,"Неверная карта замены рисунков")
    result={}
    for index, asset_id in choices.items():
        if not isinstance(index,str) or not index.isdecimal() or int(index)>100:
            raise SessionError(422,"Недопустимый индекс изображения")
        if not isinstance(asset_id,str):
            raise SessionError(422,"Недопустимый ID нового рисунка")
        result[index]=get(connection,draft_id,token,asset_id)
    return result


def remove_for_draft(connection, draft_id: str):
    connection.execute("DELETE FROM docwork_image_assets WHERE draft_id=?",(draft_id,))
