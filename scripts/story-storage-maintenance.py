#!/usr/bin/env python3
"""ACHEWIW only. Safe work/search SQLite preparation and incremental index sync.

preview: read-only; apply: Backup API snapshot before any migration; never
renames the legacy database, never deletes author data or other projects.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sqlite3
import sys
import os
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from editor_workspace import initialize,sync_search,status

def location() -> Path:
    data = ROOT/"data"
    if data.is_symlink() or not data.is_dir():
        raise RuntimeError("Нет безопасного каталога data")
    return data

def inspect(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("Главная SQLite отсутствует либо является ссылкой")
    with sqlite3.connect(f"file:{path}?mode=ro",uri=True,timeout=10) as conn:
        qc=conn.execute("PRAGMA quick_check").fetchone()[0]
        if qc!="ok":
            raise RuntimeError("SQLite quick_check не пройден: "+str(qc))
        tables={x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for required in ("projects","ai_jobs"):
            if required not in tables:
                raise RuntimeError("Не найдена таблица "+required)
        running=conn.execute(
            "SELECT count(*) FROM ai_jobs WHERE status IN ('queued','running')"
        ).fetchone()[0]
        projects=conn.execute("SELECT count(*) FROM projects").fetchone()[0]
        chunks=(conn.execute("SELECT count(*) FROM story_chunks").fetchone()[0]
                if "story_chunks" in tables else 0)
    return {"integrity":"ok","active_jobs":running,"projects":projects,
            "chunks":chunks,"size_bytes":path.stat().st_size}

def backup(data: Path, src: Path) -> Path:
    dest_dir=data/"backups"
    if dest_dir.is_symlink():
        raise RuntimeError("Каталог backups не должен быть ссылкой")
    dest_dir.mkdir(mode=0o700,exist_ok=True)
    name="before-story-storage-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+os.urandom(4).hex()+".sqlite3"
    target=dest_dir/name
    with sqlite3.connect(src,timeout=20) as source:
        with sqlite3.connect(target) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA quick_check").fetchone()[0]!="ok":
                raise RuntimeError("Не удалось проверить резервную копию")
    target.chmod(0o600)
    return target

def run(action: str) -> None:
    data=location()
    canon=data/"achewiw.sqlite3"
    state=inspect(canon)
    print("ACHEWIW storage:",state)
    print("files:",status(data))
    if action=="preview":
        print("PREVIEW: без изменений. apply создаст резервную копию и только производные базы.")
        return
    if state["active_jobs"]:
        raise RuntimeError("Запущены задания: сначала дождись их завершения")
    target=backup(data,canon)
    print("Резервная копия SQLite:",target)
    initialize(data)
    with sqlite3.connect(f"file:{canon}?mode=ro",uri=True,timeout=20) as origin:
        origin.row_factory=sqlite3.Row
        result=sync_search(data,origin)
    print("Инкрементальный индекс:",result)
    print("Остались без изменений: основная SQLite, рукописи, картинки, хостовая Ollama.")
    print("Готово. Дополнительные БД — рабочая область и восстанавливаемый индекс.")
    print("Старые ai_jobs/ai_book_chapters пока остаются в legacy БД до отдельной проверенной миграции.")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("preview","apply"))
    args=parser.parse_args()
    try:
        run(args.action)
    except (sqlite3.Error,OSError,RuntimeError,ValueError) as exc:
        parser.exit(1,"ОСТАНОВЛЕНО: "+str(exc)+"\n")
if __name__=="__main__":
    main()
