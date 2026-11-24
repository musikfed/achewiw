#!/usr/bin/env python3
"""Offline read-only ACHEWIW recovery snapshot. Never modifies running data.

Backs up entire achewiw.sqlite3 and work.sqlite3 with SQLite backup API.
WARNING: whole SQLite databases may contain OTHER projects and private drafts.
External CDR/PSD/DOCX/PDF files are NOT included: not a complete book snapshot.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import uuid

ID=re.compile(r"^[0-9a-f]{32}$")
DBS=("achewiw.sqlite3","work.sqlite3")

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):
            h.update(b)
    return h.hexdigest()

def rows(conn,sql,params=()):
    return [dict(x) for x in conn.execute(sql,params).fetchall()]

def copy_db(src,dest):
    if src.is_symlink() or not src.is_file():
        raise ValueError("Missing or unsafe database: "+str(src))
    with sqlite3.connect("file:"+str(src.resolve())+"?mode=ro",uri=True,timeout=30) as reader:
        with sqlite3.connect(dest) as dst:
            reader.backup(dst,pages=1024,sleep=0.1)
    os.chmod(dest,0o600)
    with sqlite3.connect(dest) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("SQLite backup integrity check failed")

def verify(root):
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        relative=Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe manifest path")
        p=root/relative
        if not p.is_file() or p.is_symlink() or p.stat().st_size!=entry["bytes"] or sha(p)!=entry["sha256"]:
            raise ValueError("Missing, altered or unsafe snapshot file: "+str(p))
        if entry["role"]=="sqlite":
            with sqlite3.connect(p) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise ValueError("Corrupt backed-up SQLite")
    return len(manifest["files"])

def make(data,output,project_id,job_id=None):
    if not ID.fullmatch(project_id) or (job_id and not ID.fullmatch(job_id)):
        raise ValueError("Project/job IDs must be 32 lowercase hex chars")
    data=data.resolve(strict=True)
    output=output.resolve(strict=True)
    if data==output or data in output.parents or output in data.parents:
        raise ValueError("Output must be outside the data tree")
    if not data.is_dir() or data.is_symlink():
        raise ValueError("Unsafe data directory")
    if any((data/name).is_symlink() or not (data/name).is_file() for name in DBS):
        raise ValueError("Required database missing/unsafe")
    root=output/("achewiw-p0-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8])
    root.mkdir(mode=0o700)
    (root/"databases").mkdir(mode=0o700)
    records=[]
    def record(p,role):
        records.append({"path":p.relative_to(root).as_posix(),"role":role,
                        "bytes":p.stat().st_size,"sha256":sha(p)})
    for name in DBS:
        dest=root/"databases"/name
        copy_db(data/name,dest)
        record(dest,"sqlite")
    with sqlite3.connect(root/"databases"/DBS[0]) as db:
        db.row_factory=sqlite3.Row
        selected=db.execute("SELECT data FROM projects WHERE id=?",(project_id,)).fetchone()
        if selected is None: raise ValueError("Project is not in database")
        project=json.loads(selected["data"])
        jobs=rows(db,"SELECT id,project_id,status,stage FROM ai_jobs WHERE project_id=?",(project_id,))
        if job_id and not any(j["id"]==job_id for j in jobs):
            raise ValueError("Job not owned by project")
        chapters=[]
        buffers=[]
        for j in jobs:
            if job_id and j["id"]!=job_id: continue
            jid=j["id"]
            chapters+=rows(db,"SELECT job_id,scene_id,scene_index,title,target_words,word_count,attempts,complete,accepted_at FROM ai_book_chapters WHERE job_id=? ORDER BY scene_index",(jid,))
            buffers+=rows(db,"SELECT job_id,scene_id,LENGTH(partial) AS partial_characters FROM ai_book_stream_buffers WHERE job_id=?",(jid,))
        artworks=rows(db,"SELECT id,scene_id,placement FROM illustrations WHERE project_id=?",(project_id,))
    if artworks:
        asset_dir=data/"illustrations"/project_id
        if asset_dir.is_symlink(): raise ValueError("Unsafe illustrations directory")
        (root/"illustrations").mkdir(mode=0o700)
        for artwork in artworks:
            if not ID.fullmatch(artwork["id"]): raise ValueError("Invalid artwork ID")
            src=asset_dir/(artwork["id"]+".png")
            if src.is_symlink() or not src.is_file(): raise ValueError("Missing/unsafe illustration")
            dst=root/"illustrations"/src.name
            shutil.copyfile(src,dst)
            os.chmod(dst,0o600)
            record(dst,"illustration_png")
    manifest={
      "format":"ACHEWIW-P0-RECOVERY-v1",
      "timestamp_utc":datetime.now(timezone.utc).isoformat(),
      "project_id":project_id,"job_id_filter":job_id,
      "summary":{"title":project.get("title"),"scenes":len(project.get("scenes",[])),
                 "jobs":jobs,"chapters":chapters,"stream_buffers":buffers,
                 "illustrations":artworks},
      "files":records,
      "privacy":"Contains ALL project and DOCWORK rows from BOTH SQLite databases. Keep locally with restricted access; never push archive to Git.",
      "consistency":"Individual SQLite backup API snapshots; not atomic across DB/files; check live writes before using for restore",
      "missing":["Acer CDR/PSD/SVG files","external DOCX/PDF/EPUB","other project files"],
      "complete_book_snapshot":False
    }
    dest=root/"manifest.json"
    dest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    os.chmod(dest,0o600)
    verify(root)
    return root,manifest

def main():
    parser=argparse.ArgumentParser(description="ACHEWIW P0 local read-only backup + SHA verification")
    sub=parser.add_subparsers(dest="cmd",required=True)
    create=sub.add_parser("create")
    create.add_argument("--data-dir",required=True,type=Path)
    create.add_argument("--output-root",required=True,type=Path)
    create.add_argument("--project-id",required=True)
    create.add_argument("--job-id")
    check=sub.add_parser("verify")
    check.add_argument("snapshot",type=Path)
    args=parser.parse_args()
    try:
        if args.cmd=="create":
            root,m=make(args.data_dir,args.output_root,args.project_id,args.job_id)
            print("Snapshot:",root,"Files:",len(m["files"]),
                  "Chapters:",len(m["summary"]["chapters"]))
            print("WARNING: not a complete book/artifact backup. Keep locally.")
        else:
            print("SHA and SQLite verification OK:",verify(args.snapshot),"files")
    except (OSError,ValueError,sqlite3.Error,KeyError) as exc:
        print("FAILED:",exc,file=sys.stderr)
        return 2
    return 0

if __name__=="__main__":
    sys.exit(main())
