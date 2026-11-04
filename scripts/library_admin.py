#!/usr/bin/env python3
"""ACHEWIW-only SQLite library cleanup. Never deletes a file or directory.

Usage:
    python3 scripts/library_admin.py --list
    python3 scripts/library_admin.py --purge-project HEX_ID --confirm DELETE_PROJECT
    python3 scripts/library_admin.py --reset-all --confirm DELETE_ALL_ACHEWIW_LIBRARY

The database is copied via sqlite3.backup() before a change; it remains available
under /1tbdisk/achewiw/data/backups/. Other projects/host services are untouched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

PROJECT = Path("/1tbdisk/achewiw")
DATABASE = PROJECT / "data" / "achewiw.sqlite3"


def check_scope(database: Path) -> None:
    if database != DATABASE or database.is_symlink() or not database.is_file():
        raise RuntimeError("Только существующий SQLite ACHEWIW: " + str(DATABASE))
    if (database.parent.is_symlink() or
            database.parent.resolve() != (PROJECT / "data")):
        raise RuntimeError("Недопустимая ссылка/путь к БД ACHEWIW")


def assert_stopped() -> None:
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "label=com.docker.compose.project=achewiw",
             "--format", "{{.ID}}"], capture_output=True, check=True, text=True, timeout=8,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise RuntimeError("Нет доступа проверить Docker. Операция отменена.") from None
    if result.stdout.strip():
        raise RuntimeError(
            "ACHEWIW запущен. Сначала останови только свой проект: "
            "bash scripts/achewiw-control.sh stop"
        )


def tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def counts(conn: sqlite3.Connection) -> dict:
    available = tables(conn)
    return {table: (conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    if table in available else 0)
            for table in ("projects", "ai_jobs", "ai_job_reviews")}


def archive(conn: sqlite3.Connection, database: Path) -> Path:
    backup_dir = database.parent / "backups"
    if backup_dir.is_symlink():
        raise RuntimeError("Нельзя писать архив через символическую ссылку")
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    # Refuse to rename an existing archive or overwrite a backup.
    target = backup_dir / (
        "achewiw-before-reset-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + "-" + uuid.uuid4().hex[:8] + ".sqlite3"
    )
    if target.exists() or target.is_symlink():
        raise RuntimeError("Архив уже существует")
    # Create the snapshot under a temporary name; SQLite backup provides a
    # consistent image even for a WAL database.
    with sqlite3.connect(target) as archive_conn:
        conn.backup(archive_conn)
    target.chmod(0o600)
    return target


def purge(conn: sqlite3.Connection, *, project_id: str | None) -> None:
    present = tables(conn)
    if project_id:
        if "projects" not in present or conn.execute(
            "SELECT id FROM projects WHERE id=?", (project_id,)
        ).fetchone() is None:
            raise RuntimeError("Проект не найден в SQLite")
    if project_id is None:
        if "ai_job_reviews" in present:
            conn.execute("DELETE FROM ai_job_reviews")
        if "ai_jobs" in present:
            conn.execute("DELETE FROM ai_jobs")
        if "projects" in present:
            conn.execute("DELETE FROM projects")
    else:
        if "ai_job_reviews" in present and "ai_jobs" in present:
            conn.execute("DELETE FROM ai_job_reviews WHERE job_id IN "
                         "(SELECT id FROM ai_jobs WHERE project_id=?)", (project_id,))
        if "ai_jobs" in present:
            conn.execute("DELETE FROM ai_jobs WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true")
    group.add_argument("--purge-project", metavar="HEX_ID")
    group.add_argument("--reset-all", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    check_scope(DATABASE)
    with sqlite3.connect(DATABASE, timeout=10) as connection:
        before = counts(connection)
        print("ACHEWIW SQLite:", before, flush=True)
        if args.list:
            for item in connection.execute("SELECT id, updated_at FROM projects"):
                print("Проект:", item[0], "· изменён", item[1])
            return
        if args.reset_all:
            expected = "DELETE_ALL_ACHEWIW_LIBRARY"
            project_id = None
        else:
            expected = "DELETE_PROJECT"
            project_id = args.purge_project
            if not project_id or len(project_id) != 32 or any(
                c not in "0123456789abcdef" for c in project_id.lower()
            ):
                raise RuntimeError("ID проекта должен быть 32-символьным HEX")
        if args.confirm != expected:
            raise RuntimeError("Требуется --confirm " + expected)
        assert_stopped()
        # Validate the target before backup and avoid surprise no-op deletion.
        if project_id and not connection.execute(
            "SELECT 1 FROM projects WHERE id=?", (project_id,)
        ).fetchone():
            raise RuntimeError("Такого ID проекта нет — ничего не удалено")
        backup = archive(connection, DATABASE)
        print("ПОЛНАЯ резервная копия БД:", backup, flush=True)
        with connection:
            purge(connection, project_id=project_id)
        print("Готово. После:", counts(connection))
        print("Архив сохранён; другие каталоги и файлы не удалялись.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, sqlite3.Error) as err:
        print("ОСТАНОВЛЕНО:", err, file=sys.stderr)
        sys.exit(1)
