"""R3 isolated Document Workshop draft persistence.

The original upload is stored ONCE in a dedicated SQLite table. Updates append
small immutable editorial-state revisions, never rewrite the upload, project
manuscripts or other ACHEWIW tables.

Each draft is protected by an unguessable capability token supplied in a
request header, in addition to the app's existing private LAN/VPN gate.
There is no background cleanup, TTL, silent deletion, or backup creation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import secrets
import sqlite3
import uuid

MAX_SESSIONS = 5
MAX_STORED_SOURCES = 50 * 1024 * 1024
MAX_STATE_BYTES = 96 * 1024
MAX_REVISIONS = 500
SID = re.compile(r"^[0-9a-f]{32}$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


class SessionError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def initialize(conn: sqlite3.Connection):
    # Only additive, independent tables. No changes to user projects or jobs.
    conn.execute("""CREATE TABLE IF NOT EXISTS docwork_drafts (
        id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
        filename TEXT NOT NULL, source BLOB NOT NULL, source_sha256 TEXT NOT NULL,
        state_json TEXT NOT NULL, revision INTEGER NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS docwork_draft_revisions (
        draft_id TEXT NOT NULL, revision INTEGER NOT NULL,
        state_json TEXT NOT NULL, saved_at TEXT NOT NULL,
        PRIMARY KEY(draft_id, revision),
        FOREIGN KEY(draft_id) REFERENCES docwork_drafts(id)
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_docwork_draft_saved "
        "ON docwork_drafts(updated_at)"
    )


def _validate_state(state: object) -> str:
    if not isinstance(state, dict):
        raise SessionError(422, "Состояние редактора должно быть объектом")
    if not set(state).issubset({"settings", "overrides", "replacements", "images", "moves", "editor", "ui"}):
        raise SessionError(422, "Неизвестный параметр сохранения редактора")
    for field in ("settings", "overrides", "replacements", "images", "moves", "editor", "ui"):
        if field in state and not isinstance(state[field], dict):
            raise SessionError(422, "Некорректный раздел состояния: " + field)
    try:
        raw = json.dumps(state, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SessionError(422, "Некорректные значения в черновике") from exc
    if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
        raise SessionError(413, "Черновик слишком велик: сократите объём правок")
    return raw


def _require(conn, session_id: str, token: str):
    if not SID.fullmatch(session_id) or not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise SessionError(404, "Черновик не найден")
    row = conn.execute(
        "SELECT * FROM docwork_drafts WHERE id=?", (session_id,)
    ).fetchone()
    if row is None or not secrets.compare_digest(
        row["token_hash"], sha256(token.encode("utf-8")).hexdigest()
    ):
        raise SessionError(404, "Черновик не найден")
    return row


def _metadata(row) -> dict:
    return {
        "id": row["id"], "filename": row["filename"],
        "sha256": row["source_sha256"], "source_bytes": len(row["source"]),
        "revision": row["revision"], "state": json.loads(row["state_json"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def create(conn, *, filename: str, source: bytes, state: dict) -> dict:
    raw = _validate_state(state)
    if not isinstance(source, bytes) or not source:
        raise SessionError(422, "Не загружен исходный файл")
    if len(filename) > 250 or not filename or "/" in filename or "\\" in filename:
        raise SessionError(422, "Недопустимое имя файла")
    if len(source) > 20 * 1024 * 1024:
        raise SessionError(413, "Файл превышает 20 МиБ")
    now = utc_now()
    sid, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        n, size = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(source)), 0) FROM docwork_drafts"
        ).fetchone()
        if n >= MAX_SESSIONS or size + len(source) > MAX_STORED_SOURCES:
            raise SessionError(
                409, "Лимит черновиков (5 штук или 50 МиБ исходников). "
                     "Сохраните файлы и закройте ненужный черновик вручную."
            )
        conn.execute(
            "INSERT INTO docwork_drafts VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, sha256(token.encode("utf-8")).hexdigest(), filename, source,
             sha256(source).hexdigest(), raw, 1, now, now),
        )
        conn.execute(
            "INSERT INTO docwork_draft_revisions VALUES(?,?,?,?)",
            (sid, 1, raw, now),
        )
    return {"id": sid, "token": token, "revision": 1,
            "filename": filename, "sha256": sha256(source).hexdigest()}


def read(conn, session_id: str, token: str):
    return _metadata(_require(conn, session_id, token))


def original(conn, session_id: str, token: str):
    row = _require(conn, session_id, token)
    data = bytes(row["source"])
    if sha256(data).hexdigest() != row["source_sha256"]:
        raise SessionError(409, "Контрольная сумма исходника не совпала; восстановление отменено")
    return data, row["filename"]


def save(conn, session_id: str, token: str, revision: int, state: dict):
    raw = _validate_state(state)
    if type(revision) is not int or revision < 1:
        raise SessionError(422, "Неверный номер редакции")
    now = utc_now()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _require(conn, session_id, token)
        if row["revision"] != revision:
            raise SessionError(409, "Конфликт редакций: сервер уже содержит более новую версию "
                                   + str(row["revision"]))
        if revision >= MAX_REVISIONS:
            raise SessionError(409, "Достигнут предел 500 редакций. "
                                   "Создайте новый черновик, оригинал сохранён.")
        next_rev = revision + 1
        conn.execute(
            "UPDATE docwork_drafts SET state_json=?, revision=?, updated_at=? "
            "WHERE id=? AND revision=?",
            (raw, next_rev, now, session_id, revision),
        )
        conn.execute(
            "INSERT INTO docwork_draft_revisions VALUES(?,?,?,?)",
            (session_id, next_rev, raw, now),
        )
    return {"id": session_id, "revision": next_rev, "updated_at": now}


def history(conn, session_id: str, token: str):
    _require(conn, session_id, token)
    rows = conn.execute(
        "SELECT revision, saved_at FROM docwork_draft_revisions "
        "WHERE draft_id=? ORDER BY revision DESC LIMIT 50", (session_id,)
    )
    return [{"revision": r["revision"], "saved_at": r["saved_at"]} for r in rows]


def revision_state(conn, session_id: str, token: str, revision: int):
    _require(conn, session_id, token)
    if type(revision) is not int or revision < 1:
        raise SessionError(422, "Неверная редакция")
    r = conn.execute(
        "SELECT state_json, saved_at FROM docwork_draft_revisions "
        "WHERE draft_id=? AND revision=?", (session_id, revision)
    ).fetchone()
    if not r:
        raise SessionError(404, "Редакция не найдена")
    return {"revision": revision, "saved_at": r["saved_at"],
            "state": json.loads(r["state_json"])}


def remove(conn, session_id: str, token: str, confirmed: bool):
    if confirmed is not True:
        raise SessionError(422, "Удаление черновика требует явного подтверждения")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        _require(conn, session_id, token)
        conn.execute("DELETE FROM docwork_draft_revisions WHERE draft_id=?", (session_id,))
        conn.execute("DELETE FROM docwork_drafts WHERE id=?", (session_id,))
