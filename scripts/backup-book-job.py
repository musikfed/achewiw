#!/usr/bin/env python3
"""Read-only, transaction-consistent ACHEWIW SQLite rescue bundle.

Does not modify source DB, projects, chapters, jobs, stream buffers, models,
network, server state or external files. Uses SQLite online backup() so WAL
commits are consistently included. ZIP holds a copy of the WHOLE database,
not just one job: needed to preserve coupled authoring/DOCWORK state.

Important: this is a DATABASE snapshot, not yet a full publication archive:
CDR/PSD/PDF/SVG and filesystem images are NOT captured here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile

TABLES = ("ai_jobs", "ai_book_chapters", "ai_book_stream_buffers", "projects")
REQUIRED = ("ai_jobs", "ai_book_chapters", "projects")


def hash_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def available(db: sqlite3.Connection) -> set[str]:
    return {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def check_report(db: sqlite3.Connection, job_id: str) -> dict:
    existing = available(db)
    missing = sorted(set(REQUIRED) - existing)
    if missing:
        raise ValueError("Некорректная БД: отсутствуют " + ", ".join(missing))
    row = db.execute(
        "SELECT id,project_id,mode,model,status,stage,created_at,updated_at,"
        "finished_at,error FROM ai_jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        raise ValueError("Задание не найдено (проверить --job-id)")
    job = dict(row)
    if job["mode"] != "book":
        raise ValueError("Задание существует, но его mode != book")
    project = db.execute("SELECT data FROM projects WHERE id=?",
                         (job["project_id"],)).fetchone()
    if project is None:
        raise ValueError("Не найден связанный проект")
    source = project[0].encode("utf-8")
    project_data = json.loads(project[0])
    chapters = [dict(x) for x in db.execute(
        "SELECT scene_id,scene_index,title,word_count,target_words,attempts,"
        "retry_epoch,complete,accepted_at,text,updated_at "
        "FROM ai_book_chapters WHERE job_id=? ORDER BY scene_index", (job_id,))]
    if not chapters:
        raise ValueError("У этого задания отсутствуют сохранённые главы")
    saved = []
    for chapter in chapters:
        text = chapter.pop("text")
        chapter["text_sha256"] = hash_bytes(text.encode("utf-8"))
        chapter["text_bytes"] = len(text.encode("utf-8"))
        saved.append(chapter)
    buffers = []
    if "ai_book_stream_buffers" in existing:
        for row in db.execute(
            "SELECT scene_id,base_digest,partial,retry,updated_at "
            "FROM ai_book_stream_buffers WHERE job_id=?", (job_id,)
        ):
            item = dict(row)
            partial = item.pop("partial")
            item["partial_sha256"] = hash_bytes(partial.encode("utf-8"))
            item["partial_bytes"] = len(partial.encode("utf-8"))
            buffers.append(item)
    target = sum(c["target_words"] for c in saved)
    actual = sum(c["word_count"] for c in saved)
    return {
        "project_id": job["project_id"],
        "project_title": project_data.get("title", ""),
        "project_sha256": hash_bytes(source),
        "job": job,
        "chapters": saved,
        "stream_buffers": buffers,
        "generated_words": actual,
        "target_words": target,
        "progress_percent": round(actual / target * 100, 2) if target else None,
        "chapter_count": len(saved),
        "complete_chapters": sum(bool(c["complete"]) for c in saved),
        "accepted_chapters": sum(bool(c["accepted_at"]) for c in saved),
        "source_scope": "complete SQLite online backup, selected job diagnostics",
        "external_files_archived": False,
        "publication_snapshot_complete": False,
        "limitations": [
            "В ZIP сохранена вся SQLite, включая другие проекты/черновики/ассеты",
            "CDR, PSD, SVG, PNG, PDF, EPUB вне SQLite не архивируются",
            "Это не гарантирует наличие всех исходников публикации вне базы",
            "Не выкладывать ZIP в GitHub и не пересылать посторонним",
        ],
    }


def create_archive(source_path: Path, target: Path, job_id: str) -> dict:
    if not source_path.is_file():
        raise FileNotFoundError("Исходная SQLite отсутствует: " + str(source_path))
    if target.exists():
        raise FileExistsError("Архив уже существует; перезапись запрещена")
    if source_path.resolve() == target.resolve():
        raise ValueError("Источник и результат совпадают")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.resolve() == source_path.parent.resolve():
        # Allowed but not recommended; won't overwrite the original.
        pass
    target_fd = None
    try:
        target_fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with tempfile.TemporaryDirectory(prefix="achewiw-rescue-") as work:
            copied = Path(work) / "achewiw-consistent.sqlite3"
            # SQLite read-only connection, including consistent live WAL backup.
            source_uri = source_path.resolve().as_uri() + "?mode=ro"
            src = sqlite3.connect(source_uri, uri=True, timeout=20)
            try:
                dst = sqlite3.connect(copied)
                try:
                    src.backup(dst, pages=1000, sleep=0.1)
                finally:
                    dst.close()
            finally:
                src.close()
            verify = sqlite3.connect("file:" + str(copied) + "?mode=ro", uri=True)
            verify.row_factory = sqlite3.Row
            try:
                integrity = verify.execute("PRAGMA quick_check").fetchone()[0]
                if integrity != "ok":
                    raise ValueError("SQLite quick_check не пройден: " + str(integrity))
                report = check_report(verify, job_id)
            finally:
                verify.close()
            db_hash = sha_file(copied)
            report["snapshot_time_utc"] = datetime.now(timezone.utc).isoformat()
            report["sqlite_sha256"] = db_hash
            report["sqlite_bytes"] = copied.stat().st_size
            payload = json.dumps(report, indent=2, ensure_ascii=False,
                                 sort_keys=True).encode("utf-8")
            with os.fdopen(target_fd, "wb") as out:
                target_fd = None
                with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED,
                                     compresslevel=6) as zipout:
                    zipout.write(copied, "database/achewiw-consistent.sqlite3")
                    zipout.writestr("report/job-rescue-report.json", payload)
                    zipout.writestr("README.txt",
                        "ACHEWIW rescue: COMPLETE SQLITE DATABASE snapshot only.\n"
                        "Contains private manuscript text and other DB projects.\n"
                        "Not a complete archive of external book/illustration files.\n"
                        "Keep secure offline; verify sqlite SHA-256 against report.\n"
                        "Never overwrite the working sqlite with this archive.\n")
        # Reopen created archive and compare stored report/DB integrity hash.
        with zipfile.ZipFile(target) as check:
            if check.testzip() is not None:
                raise ValueError("ZIP CRC verification failed")
            with check.open("database/achewiw-consistent.sqlite3") as stream:
                calculated = sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    calculated.update(chunk)
                if calculated.hexdigest() != db_hash:
                    raise ValueError("Snapshot SHA mismatch")
        return {
            "archive": str(target), "archive_sha256": sha_file(target),
            "size_bytes": target.stat().st_size,
            "job_id": job_id, "project_id": report["project_id"],
            "progress_percent": report["progress_percent"],
            "chapter_count": report["chapter_count"],
            "publication_snapshot_complete": False,
        }
    except Exception:
        if target_fd is not None:
            os.close(target_fd)
        # Only delete our own newly-created INVALID output (never source).
        if target.exists():
            target.unlink()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True,
                        help="Existing /data/achewiw.sqlite3 (read-only)")
    parser.add_argument("--job-id", required=True,
                        help="Exact existing book ai_jobs.id")
    parser.add_argument("--out", type=Path, required=True,
                        help="NEW output ZIP. Existing output is never overwritten.")
    args = parser.parse_args()
    try:
        result = create_archive(args.db, args.out, args.job_id)
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as error:
        parser.exit(2, "Снимок НЕ создан: " + str(error) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
