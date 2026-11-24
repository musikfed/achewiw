#!/usr/bin/env python3
"""A1.4: audit an ACHEWIW rescue ZIP in isolation; read-only, no services used.

Verifies member safety/duplicates, archive CRC, SHA256, SQLite integrity, job
chapters/buffers and any selected external asset manifest. Does not declare an
archive of the whole publication complete without an independent dependency
inventory. Never extracts manuscripts or media into the live installation.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile

CHUNK = 1024 * 1024
SQLITE_MEMBER = "database/achewiw-consistent.sqlite3"
REPORT_MEMBER = "report/job-rescue-report.json"
EXTERNAL_MEMBER = "report/external-manifest.json"


def hash_reader(reader, target=None):
    h=sha256()
    length=0
    for part in iter(lambda:reader.read(CHUNK), b""):
        length+=len(part)
        h.update(part)
        if target is not None:
            target.write(part)
    return h.hexdigest(), length


def safe_names(archive):
    seen=set()
    for member in archive.infolist():
        name=member.filename
        parts=name.split("/")
        if (name.startswith("/") or "\\" in name or
            any(p in ("", ".", "..") for p in parts) or
            name in seen or member.is_dir()):
            raise ValueError("Некорректный/повторяющийся путь в ZIP: "+name)
        seen.add(name)
    return seen


def check_archive(path:Path)->dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    with zipfile.ZipFile(path) as bundle:
        names=safe_names(bundle)
        if not {SQLITE_MEMBER,REPORT_MEMBER}.issubset(names):
            raise ValueError("Нет контрольной копии SQLite или отчёта")
        if bundle.testzip() is not None:
            raise ValueError("ZIP CRC проверка не пройдена")
        report=json.loads(bundle.read(REPORT_MEMBER))
        with tempfile.TemporaryDirectory(prefix="achewiw-rescue-verify-") as temp:
            destination=Path(temp)/"verified.sqlite3"
            with bundle.open(SQLITE_MEMBER) as stream, destination.open("wb") as out:
                digest, size=hash_reader(stream,out)
            if digest!=report.get("sqlite_sha256") or size!=report.get("sqlite_bytes"):
                raise ValueError("SQLite SHA256/размер не совпадает с отчётом")
            db=sqlite3.connect(destination.as_uri()+"?mode=ro",uri=True)
            db.row_factory=sqlite3.Row
            try:
                if db.execute("PRAGMA quick_check").fetchone()[0]!="ok":
                    raise ValueError("SQLite quick_check failed")
                job=report.get("job",{})
                job_id=job.get("id")
                source=db.execute(
                    "SELECT project_id,mode FROM ai_jobs WHERE id=?",(job_id,)
                ).fetchone()
                if (not source or source["mode"]!="book"
                    or source["project_id"]!=report.get("project_id")):
                    raise ValueError("Нарушена привязка книги/job/project")
                project=db.execute("SELECT data FROM projects WHERE id=?",
                                   (source["project_id"],)).fetchone()
                if (project is None or
                    sha256(project["data"].encode("utf-8")).hexdigest()!=report.get("project_sha256")):
                    raise ValueError("SHA проекта не совпадает")
                rows=db.execute(
                    "SELECT scene_id,scene_index,text,word_count,target_words,"
                    "attempts,complete,accepted_at FROM ai_book_chapters "
                    "WHERE job_id=? ORDER BY scene_index",(job_id,)
                ).fetchall()
                listed=report.get("chapters",[])
                if len(rows)!=len(listed) or not rows:
                    raise ValueError("Количество глав отличается от отчёта")
                for row,item in zip(rows,listed):
                    if (row["scene_id"]!=item["scene_id"] or
                        row["scene_index"]!=item["scene_index"] or
                        any(row[key]!=item[key] for key in
                            ("word_count","target_words","attempts","complete","accepted_at")) or
                        sha256(row["text"].encode("utf-8")).hexdigest()!=item["text_sha256"]):
                        raise ValueError("Несовпадение сохранённой главы")
                existing={r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                partial=db.execute(
                    "SELECT scene_id,base_digest,partial,retry FROM ai_book_stream_buffers "
                    "WHERE job_id=?",(job_id,)
                ).fetchall() if "ai_book_stream_buffers" in existing else []
                reported=report.get("stream_buffers",[])
                if len(partial)!=len(reported):
                    raise ValueError("Количество stream buffers отличается от отчёта")
                by_scene={x["scene_id"]:x for x in reported}
                for row in partial:
                    expected=by_scene.get(row["scene_id"])
                    if (not expected or row["base_digest"]!=expected["base_digest"]
                        or row["retry"]!=expected["retry"] or
                        sha256(row["partial"].encode("utf-8")).hexdigest()!=expected["partial_sha256"]):
                        raise ValueError("Несовпадение буфера потока")
                word_total=sum(x["word_count"] for x in rows)
            finally:
                db.close()
        external_count=0
        if EXTERNAL_MEMBER in names:
            manifest=json.loads(bundle.read(EXTERNAL_MEMBER))
            items=manifest.get("files",[])
            external_names={n for n in names if n.startswith("external/")}
            if len(items)!=len(external_names):
                raise ValueError("Количество файлов во внешнем манифесте не совпадает")
            used=set()
            for item in items:
                name=item["archive_path"]
                if (name not in external_names or name in used):
                    raise ValueError("Внешний файл отсутствует/повторяется")
                used.add(name)
                with bundle.open(name) as reader:
                    digest,length=hash_reader(reader)
                if digest!=item["sha256"] or length!=item["size_bytes"]:
                    raise ValueError("Не совпадает хеш внешнего файла: "+name)
            external_count=len(items)
        elif any(name.startswith("external/") for name in names):
            raise ValueError("Внешние файлы без манифеста")
        return {
            "verified":True,
            "job_id":job_id,
            "project_id":source["project_id"],
            "saved_chapters":len(rows),
            "generated_words":word_total,
            "stream_buffers":len(partial),
            "external_files_verified":external_count,
            "sqlite_sha256":report["sqlite_sha256"],
            "publication_snapshot_complete":False,
            "next_step":"Сверить инвентаризацию зависимостей и внешние файлы на Huananzhi/Acer",
        }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=Path,required=True)
    args=p.parse_args()
    try:
        print(json.dumps(check_archive(args.archive),ensure_ascii=False,indent=2))
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,zipfile.BadZipFile) as exc:
        p.exit(2,"Проверка НЕ пройдена: "+str(exc)+"\n")


if __name__=="__main__":
    main()
