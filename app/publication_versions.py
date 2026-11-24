"""Independent, immutable work-level editorial branches and publication editions.

Designed for books, comics and mixed projects. Never mutates projects, book
generation jobs or DOCWORK originals. A checkpoint carries the complete
canonical Project JSON from the author workspace, plus an optional bounded
manifest of supplementary artwork/print output references. External artifacts
are NOT preserved by a mere manifest; an edition is "metadata frozen" until
verified copies of those binaries are archived by a separate publishing step.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import sqlite3
import uuid

ID = re.compile(r"^[0-9a-f]{32}$")
MEDIA = {"book", "comic", "illustrated_book", "other"}
MAX_MANIFEST = 256 * 1024
MAX_SOURCE = 3 * 1024 * 1024
MAX_BRANCHES = 32
MAX_POINTS = 300


class EditionError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def digest(content):
    return sha256(content.encode("utf-8")).hexdigest()


def validate_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise EditionError(404, "Проект, ветка или редакция не найдены")
    return value


def validate_label(value, maximum=120):
    if not isinstance(value, str) or not (1 <= len(value.strip()) <= maximum):
        raise EditionError(422, "Название должно быть непустым и не длиннее " + str(maximum))
    return value.strip()


def validate_manifest(value):
    if value is None:
        return "{}"
    if not isinstance(value, dict):
        raise EditionError(422, "Манифест проекта должен быть объектом")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditionError(422, "Некорректный манифест") from exc
    if len(encoded.encode("utf-8")) > MAX_MANIFEST:
        raise EditionError(413, "Манифест проекта слишком велик")
    return encoded


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS publication_branches (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        medium TEXT NOT NULL,
        name TEXT NOT NULL,
        source_checkpoint_id TEXT,
        current_checkpoint_id TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(project_id, name)
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS ix_publication_branches_project
        ON publication_branches(project_id, created_at)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS publication_checkpoints (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        parent_checkpoint_id TEXT,
        label TEXT NOT NULL,
        project_json TEXT NOT NULL,
        project_sha256 TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS ix_publication_checkpoints_branch
        ON publication_checkpoints(branch_id, created_at)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS publication_editions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        checkpoint_id TEXT NOT NULL,
        label TEXT NOT NULL,
        medium TEXT NOT NULL,
        edition_number INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(project_id, edition_number)
    )""")


def canonical_project(value, project_id):
    if not isinstance(value, dict) or value.get("id") != project_id:
        raise EditionError(422, "Проект в редакции не соответствует выбранному произведению")
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise EditionError(422, "Некорректные данные рукописи") from exc
    if len(encoded.encode("utf-8")) > MAX_SOURCE:
        raise EditionError(413, "Слишком большой проект для контрольной точки")
    return encoded


def _project(conn, project_id):
    validate_id(project_id)
    row = conn.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise EditionError(404, "Проект не найден")
    data = row["data"]
    if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_SOURCE:
        raise EditionError(413, "Проект слишком велик для снимка редакции")
    try:
        value = json.loads(data)
    except ValueError as exc:
        raise EditionError(409, "Исходная структура проекта повреждена") from exc
    # Deterministic canonical form: source is copied; no mutation of projects.
    return canonical_project(value,project_id)


def _branch(conn, project_id, branch_id):
    validate_id(project_id)
    validate_id(branch_id)
    row = conn.execute(
        "SELECT * FROM publication_branches WHERE id=? AND project_id=?",
        (branch_id, project_id)
    ).fetchone()
    if row is None:
        raise EditionError(404, "Ветка этого проекта не найдена")
    return row


def _checkpoint(conn, project_id, checkpoint_id):
    validate_id(project_id)
    validate_id(checkpoint_id)
    row = conn.execute(
        "SELECT * FROM publication_checkpoints WHERE id=? AND project_id=?",
        (checkpoint_id, project_id)
    ).fetchone()
    if row is None:
        raise EditionError(404, "Редакция этого проекта не найдена")
    if digest(row["project_json"]) != row["project_sha256"] or (
        digest(row["manifest_json"]) != row["manifest_sha256"]
    ):
        raise EditionError(409, "Нарушена контрольная сумма редакции")
    return row


def _make_checkpoint(conn, project_id, branch_id, parent_id, label, data, manifest):
    checkpoint_id = uuid.uuid4().hex
    now = stamp()
    conn.execute("""INSERT INTO publication_checkpoints
        (id,project_id,branch_id,parent_checkpoint_id,label,project_json,
         project_sha256,manifest_json,manifest_sha256,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (checkpoint_id,project_id,branch_id,parent_id,label,data,
         digest(data),manifest,digest(manifest),now))
    conn.execute(
        "UPDATE publication_branches SET current_checkpoint_id=? WHERE id=?",
        (checkpoint_id, branch_id)
    )
    return {"id":checkpoint_id, "branch_id":branch_id, "project_id":project_id,
            "parent_checkpoint_id":parent_id, "label":label,
            "project_sha256":digest(data), "manifest_sha256":digest(manifest),
            "created_at":now}


def create_branch(conn, project_id, name, medium="book", from_checkpoint=None):
    name = validate_label(name)
    if medium not in MEDIA:
        raise EditionError(422, "Неизвестный тип произведения")
    data = None
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT COUNT(*) FROM publication_branches WHERE project_id=?",
            (validate_id(project_id),)
        ).fetchone()[0] >= MAX_BRANCHES:
            raise EditionError(409, "Лимит веток этого проекта")
        if from_checkpoint is not None:
            prev = _checkpoint(conn, project_id, from_checkpoint)
            data, manifest = prev["project_json"], prev["manifest_json"]
        else:
            data, manifest = _project(conn, project_id), "{}"
        branch_id = uuid.uuid4().hex
        now=stamp()
        try:
            conn.execute("""INSERT INTO publication_branches
                (id,project_id,medium,name,source_checkpoint_id,
                 current_checkpoint_id,created_at) VALUES(?,?,?,?,?,?,?)""",
                (branch_id,project_id,medium,name,from_checkpoint,None,now))
        except sqlite3.IntegrityError as exc:
            raise EditionError(409, "Ветка с таким названием уже существует") from exc
        point = _make_checkpoint(conn,project_id,branch_id,from_checkpoint,
                                 "Начальная редакция",data,manifest)
    return {"id":branch_id,"project_id":project_id,"medium":medium,
            "name":name,"source_checkpoint_id":from_checkpoint,
            "head":point,"created_at":now}


def checkpoint(conn, project_id, branch_id, label, expected_head, manifest=None, project_state=None):
    label = validate_label(label)
    validate_id(expected_head)
    manifest_data=validate_manifest(manifest)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        branch=_branch(conn,project_id,branch_id)
        if branch["current_checkpoint_id"] != expected_head:
            raise EditionError(409, "Ветка изменилась в другой вкладке. Обнови историю.")
        count=conn.execute(
            "SELECT COUNT(*) FROM publication_checkpoints WHERE branch_id=?",
            (branch_id,)
        ).fetchone()[0]
        if count>=MAX_POINTS:
            raise EditionError(409, "Лимит сохранённых редакций в ветке")
        _project(conn,project_id)  # Scope still belongs to an existing project.
        data = (canonical_project(project_state,project_id)
                if project_state is not None else _checkpoint(conn,project_id,expected_head)["project_json"])
        return _make_checkpoint(conn,project_id,branch_id,expected_head,
                                label,data,manifest_data)


def list_branches(conn, project_id):
    _project(conn,project_id)
    return [dict(x) for x in conn.execute(
        "SELECT * FROM publication_branches WHERE project_id=? "
        "ORDER BY created_at ASC",(project_id,)
    ).fetchall()]


def list_checkpoints(conn, project_id, branch_id):
    _branch(conn,project_id,branch_id)
    return [dict(x) for x in conn.execute(
        "SELECT id, branch_id, parent_checkpoint_id, label, "
        "project_sha256,manifest_sha256,created_at "
        "FROM publication_checkpoints WHERE project_id=? AND branch_id=? "
        "ORDER BY created_at DESC",(project_id,branch_id)
    ).fetchall()]


def read_checkpoint(conn, project_id, checkpoint_id):
    row=_checkpoint(conn,project_id,checkpoint_id)
    return {"id":row["id"],"project_id":row["project_id"],
            "branch_id":row["branch_id"],"label":row["label"],
            "parent_checkpoint_id":row["parent_checkpoint_id"],
            "project_sha256":row["project_sha256"],
            "manifest_sha256":row["manifest_sha256"],
            "project":json.loads(row["project_json"]),
            "manifest":json.loads(row["manifest_json"]),
            "created_at":row["created_at"]}


def seal_edition(conn, project_id, checkpoint_id, label, medium="book"):
    label=validate_label(label)
    if medium not in MEDIA:
        raise EditionError(422, "Неизвестный тип произведения")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        point=_checkpoint(conn,project_id,checkpoint_id)
        number=conn.execute("SELECT COALESCE(MAX(edition_number),0)+1 "
            "FROM publication_editions WHERE project_id=?",(project_id,)
        ).fetchone()[0]
        entry={"id":uuid.uuid4().hex,"project_id":project_id,
               "checkpoint_id":point["id"],"label":label,"medium":medium,
               "edition_number":number,"created_at":stamp()}
        conn.execute("""INSERT INTO publication_editions
            (id,project_id,checkpoint_id,label,medium,edition_number,created_at)
            VALUES (:id,:project_id,:checkpoint_id,:label,:medium,
                    :edition_number,:created_at)""",entry)
        return entry


def list_editions(conn, project_id):
    _project(conn,project_id)
    return [dict(row) for row in conn.execute(
        "SELECT * FROM publication_editions WHERE project_id=? "
        "ORDER BY edition_number DESC",(project_id,)
    ).fetchall()]
