#!/usr/bin/env python3
"""Read-only ACHEWIW real-container smoke checks.

Inside actual running Studio:
  docker exec -i -w /app <verified-studio-id> python - < scripts/host-smoke-11-15.py

Uses an in-memory synthetic three-chapter book. Never inserts into the real
database, calls an LLM, touches server jobs or writes user files.
"""
from __future__ import annotations

import os
import sys
import json
import tempfile
from dataclasses import dataclass,field
from collections import Counter
from urllib.request import urlopen
from pathlib import Path

FAILS=[]
WARN=[]
PASSES=[]


def check(name,condition,detail=""):
    target=PASSES if condition else FAILS
    target.append(name)
    print(("PASS " if condition else "FAIL ")+name+((": "+str(detail)[:180]) if detail else ""))
    return condition


def warning(name,detail=""):
    WARN.append(name)
    print("WARN "+name+((": "+str(detail)[:180]) if detail else ""))


def main():
    print("ACHEWIW real-container smoke: Python",sys.version.split()[0])
    root=Path("/app")
    if not check("current app code in /app",root.is_dir()):
        return 1
    sys.path.insert(0,str(root))
    try:
        from publishing_exports import manuscript_pdf,manuscript_docx
        from print_proof_preflight import inspect_pdf
        from print_release_gate import inspect_docx_toc,release_preflight
        from page_fit_advisor import PageEvidence,recommend
    except Exception as exc:
        check("step12-15 modules load",False,type(exc).__name__+": "+str(exc))
        return 1
    check("step12-15 modules load",True)
    try:
        from main import app,api
        # FastAPI >=0.165 uses lazy _IncludedRouter; app.routes is NOT flat.
        # OpenAPI is the effective public endpoint registry.
        paths=set(app.openapi()["paths"])
        protected={getattr(route,"path","") for route in api.routes}
        print("PUBLIC protected print routes:",sorted(path for path in paths if "print-proof" in path))
        for key in (
            "/api/health",
            "/api/projects/{project_id}/print-proof/preflight",
            "/api/projects/{project_id}/print-proof/preview-fit",
            "/api/projects/{project_id}/print-proof/grounded-fit",
            "/api/projects/{project_id}/print-proof/fit-reviews",
        ):
            check("route "+key,key in paths and (key=="/api/health" or key in protected))
    except Exception as exc:
        check("real app route registry",False,type(exc).__name__+": "+str(exc))
    @dataclass
    class Scene:
        id:str
        title:str
        text:str
    @dataclass
    class Project:
        id:str="host-smoke-only"
        title:str="Синтетическая техническая книга"
        scenes:list[Scene]=field(default_factory=list)
        author_name:str="Тестовый автор"
        publication_year:int=2026
        publisher_name:str=""
        edition_label:str="qa"
        work_type:str="technical"
    seed=[
        ("Первые магнитные диски","Магнитная поверхность сохраняет изменения на дорожке. "),
        ("Управление головкой","Позиционирование головки влияет на доступ к выбранной дорожке. "),
        ("Сектора и адресация","Сектора и дорожки организуют расположение данных на носителе. "),
    ]
    p=Project(scenes=[Scene("test-"+str(i+1),title,
         "\n\n".join((sentence*10).strip() for _ in range(3))) for i,(title,sentence) in enumerate(seed)])
    try:
        pdf=manuscript_pdf(p,print_proof=True)
        docx=manuscript_docx(p)
        titles=[scene.title for scene in p.scenes]
        proof=inspect_pdf(pdf,chapter_titles=titles)
        check("real ReportLab PDF generated",pdf.startswith(b"%PDF") and proof["page_count"]>=5,proof["page_count"])
        check("real DOCX generated",docx.startswith(b"PK"))
        check("three PDF TOC references agree with printed chapters",
              len(proof["headings"])==3 and all(row["match"] is True for row in proof["headings"]),
              [(h["toc_page"],h["actual_pdf_page"]) for h in proof["headings"]])
        toc=inspect_docx_toc(docx)
        check("DOCX has dynamic TOC and update-on-open",toc["toc_field"] and toc["update_on_open"])
        blocked=release_preflight(docx=docx,final_pdf=pdf,
                 chapter_titles=titles,office_refreshed=False)
        check("publication gate blocks before office refresh",
              not blocked["publication_allowed"] and any(
                 item["code"]=="OFFICE_REFRESH_NOT_VERIFIED" for item in blocked["blocking"]))
        check("intentional part break cannot be filled",
              recommend(PageEvidence(5,0.22,next_page_starts_part=True))["action"]=="preserve_break")
        check("bad word spacing is fixed typographically",
              recommend(PageEvidence(5,0.22,justified_large_word_spaces=True))["action"]=="fix_layout")
    except Exception as exc:
        check("synthetic real PDF/DOCX generation",False,type(exc).__name__+": "+str(exc))
    try:
        with urlopen("http://127.0.0.1:7080/api/health",timeout=4) as res:
            data=json.load(res)
        check("real Studio HTTP /api/health",data.get("status")=="ok")
    except Exception as exc:
        warning("Studio loopback health from container",type(exc).__name__)
    try:
        from main import OLLAMA_BASE_URL
        from urllib.parse import urlparse
        parsed=urlparse(OLLAMA_BASE_URL)
        if parsed.scheme in ("http","https") and parsed.hostname in (
            "127.0.0.1","localhost","192.168.0.100"):
            with urlopen(OLLAMA_BASE_URL.rstrip("/")+"/api/tags",timeout=4) as res:
                models=json.load(res).get("models",[])
            check("configured local Ollama responds",isinstance(models,list),
                  "installed_models="+str(len(models)))
        else:
            warning("Ollama endpoint not probed; nonlocal or custom address")
    except Exception as exc:
        warning("configured Ollama availability",type(exc).__name__)
    print("=== HOST SMOKE SUMMARY ===")
    print("PASS",len(PASSES),"WARN",len(WARN),"FAIL",len(FAILS))
    print("All synthetic; no user book or project database modified.")
    return 1 if FAILS else 0


if __name__=="__main__":
    raise SystemExit(main())
