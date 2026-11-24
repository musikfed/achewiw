#!/usr/bin/env python3
"""R3.8 repeatable local Vision acceptance; no manuscript or project access.

Default: create deterministic synthetic DOCX/PNG/ground-truth JSON, inspect installed
vision models without running one. Real GPU inference requires BOTH --model and
--confirm-gpu. Uses temporary capability draft; always attempts its explicit deletion.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import secrets
import sys
import time
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import HTTPError, URLError

from PIL import Image, ImageDraw, ImageFont
from docx import Document

BASE = "http://127.0.0.1:7080"
HTTP = build_opener(ProxyHandler({}))
WIDTH, HEIGHT = 800, 480
TRUTH = [
    {"type":"rectangle","x":60,"y":150,"w":205,"h":150},
    {"type":"rectangle","x":525,"y":150,"w":205,"h":150},
    {"type":"arrow","x":275,"y":225,"w":240,"h":0},
    {"type":"text","text":"PUMP","x":115,"y":200,"w":95,"h":35},
    {"type":"text","text":"VALVE","x":575,"y":200,"w":110,"h":35},
]


def call(method, path, payload=None, token=None, ctype=None, close=False, timeout=20):
    headers={"Host":"127.0.0.1:7080"}
    if token:
        headers["X-ACHEWIW-Docwork-Token"]=token
    if ctype:
        headers["Content-Type"]=ctype
    if close:
        headers["X-ACHEWIW-Confirm-Close-Draft"]="yes-delete-only-this-draft"
    request=Request(BASE+path, data=payload, method=method, headers=headers)
    try:
        with HTTP.open(request, timeout=timeout) as reply:
            return reply.status, reply.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def fixture():
    img=Image.new("RGB",(WIDTH,HEIGHT),(255,255,255))
    draw=ImageDraw.Draw(img)
    draw.rectangle((60,150,265,300),outline=(10,50,120),width=5)
    draw.rectangle((525,150,730,300),outline=(10,50,120),width=5)
    draw.line((275,225,515,225),fill=(10,50,120),width=6)
    draw.polygon([(515,225),(495,213),(495,237)],fill=(10,50,120))
    font=None
    for name in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"):
        try:
            font=ImageFont.truetype(name,34)
            break
        except OSError:
            continue
    if font is None:
        font=ImageFont.load_default()
    draw.text((115,200),"PUMP",font=font,fill=(10,40,90))
    draw.text((575,200),"VALVE",font=font,fill=(10,40,90))
    png=BytesIO()
    img.save(png,format="PNG")
    doc=Document()
    doc.add_heading("ACHEWIW Vision R3.8: synthetic test",0)
    doc.add_paragraph("This is synthetic test data, not a manuscript.")
    png.seek(0)
    doc.add_picture(png)
    doc.add_paragraph("Expected nodes: PUMP, VALVE; one right-pointing arrow.")
    stream=BytesIO()
    doc.save(stream)
    return png.getvalue(),stream.getvalue()


def multipart(docx):
    boundary="vision-bench-"+secrets.token_hex(14)
    value=(("--"+boundary+"\r\nContent-Disposition: form-data; name=\"file\"; "
            "filename=\"synthetic-vision-r38.docx\"\r\n"
            "Content-Type: application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document\r\n\r\n").encode()
           +docx+("\r\n--"+boundary+"--\r\n").encode())
    return value,"multipart/form-data; boundary="+boundary


def iou(a,b):
    ax,ay,aw,ah=(a[k] for k in ("x","y","w","h"))
    bx,by,bw,bh=(b[k] for k in ("x","y","w","h"))
    area=max(0,min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))
    union=aw*ah+bw*bh-area
    return round(area/union,3) if union>0 else 0.0


def edge_distance(a,b):
    """Sum of start/end endpoint errors, in pixels (direction matters)."""
    import math
    start=math.hypot(a["x"]-b["x"],a["y"]-b["y"])
    end=math.hypot(a["x"]+a["w"]-b["x"]-b["w"],
                   a["y"]+a["h"]-b["y"]-b["h"])
    return round((start+end)/2,2)


def evaluate(proposals):
    matches=[]
    remaining=set(range(len(proposals)))
    for expected in TRUTH:
        candidate_ids=[i for i in remaining if proposals[i].get("type")==expected["type"]]
        if expected["type"]=="text":
            normalized=lambda x:"".join(c for c in str(x).upper() if c.isalnum())
            best=max(candidate_ids,key=lambda i:(
                normalized(proposals[i].get("text"))==normalized(expected["text"]),
                iou(expected,proposals[i])),default=None)
            correct=best is not None and normalized(proposals[best].get("text"))==normalized(expected["text"])
            metric={"kind":"text","expected":expected["text"],
                    "recognized":proposals[best].get("text","") if best is not None else None,
                    "exact":correct}
        elif expected["type"] in ("arrow","line"):
            best=min(candidate_ids,key=lambda i:edge_distance(expected,proposals[i]),
                     default=None)
            metric={"kind":expected["type"],"endpoint_error_px":
                    edge_distance(expected,proposals[best]) if best is not None else None}
            correct=best is not None and metric["endpoint_error_px"]<=25
        else:
            best=max(candidate_ids,key=lambda i:iou(expected,proposals[i]),default=None)
            metric={"kind":expected["type"],"best_iou":
                    iou(expected,proposals[best]) if best is not None else 0}
            correct=best is not None and metric["best_iou"]>=0.5
        if best is not None and correct:
            remaining.remove(best)
        metric["matched"]=bool(correct)
        matches.append(metric)
    return {"expected":len(TRUTH),"proposed":len(proposals),
            "matched":sum(item["matched"] for item in matches),
            "unmatched_proposals_lower_bound":len(remaining),
            "details":matches,
            "note":"Simple approximate fixture matching, not a production OCR or geometry benchmark."}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",default="/tmp/achewiw-vision-bench-r38")
    p.add_argument("--fixture-only",action="store_true",help="Generate fixture without contacting Studio")
    p.add_argument("--model",help="Explicit local Ollama vision model name")
    p.add_argument("--confirm-gpu",action="store_true",
                   help="I approve one synthetic image inference on the shared GPU")
    args=p.parse_args()
    if bool(args.model)!=bool(args.confirm_gpu):
        p.error("A real inference requires BOTH --model and --confirm-gpu")
    output=Path(args.output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    png,docx=fixture()
    (output/"synthetic-scheme.png").write_bytes(png)
    (output/"synthetic-scheme.docx").write_bytes(docx)
    (output/"ground-truth.json").write_text(json.dumps(
        {"canvas":{"width":WIDTH,"height":HEIGHT},"objects":TRUTH},
        ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Fixture:",output/"synthetic-scheme.docx",flush=True)
    if args.fixture_only:
        print("FIXTURE PASS; no API or GPU use.")
        return 0
    code,_=call("GET","/api/health")
    if code!=200:
        raise RuntimeError("Studio health is not HTTP 200")
    body,ctype=multipart(docx)
    code,data=call("POST","/api/docwork/drafts",body,ctype=ctype,timeout=35)
    if code!=201:
        raise RuntimeError("Could not create synthetic DOCWORK draft (HTTP "+str(code)+")")
    created=json.loads(data)
    draft_id,token=created["id"],created["token"]
    base="/api/docwork/drafts/"+draft_id
    try:
        code,png_original=call("GET",base+"/images/0/original",token=token,timeout=40)
        if code!=200:
            raise RuntimeError("Original picture endpoint: HTTP "+str(code))
        image=Image.open(BytesIO(png_original))
        dimensions=image.size
        code,data=call("GET",base+"/vision/preflight",token=token)
        if code!=200:
            raise RuntimeError("Vision preflight: HTTP "+str(code))
        ready=json.loads(data)
        print("Installed candidates:",", ".join(ready.get("models",[])) or "none")
        print("Preflight:",ready.get("reason"))
        if not args.model:
            print("READ-ONLY PASS; no GPU inference performed.")
            return 0
        if args.model not in ready.get("models",[]):
            raise RuntimeError("Requested model is not present in preflight models")
        payload=json.dumps({"image_index":0,"model":args.model,
            "width":dimensions[0],"height":dimensions[1],
            "base_asset":"original","consent":True}).encode()
        started=time.monotonic()
        code,data=call("POST",base+"/vision/analyze",payload,token=token,
                       ctype="application/json",timeout=145)
        elapsed=round(time.monotonic()-started,2)
        if code!=200:
            # Report only allowlisted server error categories; no tokens or model text.
            try:
                error=json.loads(data)
                detail=error.get("detail","")
                if not isinstance(detail,str):
                    detail="Other server validation error"
                if detail not in (
                    "Модель не вернула проверяемую структуру схемы",
                    "Модель не установлена в локальной Ollama",
                    "Выбери установленную vision-модель",
                    "Нельзя прочитать исходный рисунок",
                    "Распознавание поддерживает только рисунки DOCX",
                    "Локальная Ollama Vision не ответила корректно",
                    "Размер рисунка изменился — обнови страницу",
                    "Рисунок слишком большой для безопасного запуска Vision",
                ):
                    detail="Other server validation error"
            except (ValueError, TypeError, AttributeError):
                detail="Server returned a non-JSON error"
            raise RuntimeError("Vision analysis rejected (HTTP "+str(code)+
                               ", "+detail+", duration="+str(elapsed)+"s). "+
                               "No model output was accepted.")
        result=json.loads(data)
        if result.get("status")!="unverified" or result.get("autoApply") is not False:
            raise RuntimeError("Fail-closed: unverified result contract not respected")
        proposals=result.get("proposals",[])
        if not isinstance(proposals,list):
            raise RuntimeError("Invalid proposals")
        report={"model":args.model,"elapsed_seconds":elapsed,
                "image_sha256":sha256(png).hexdigest(),
                "original_docx_sha256":sha256(docx).hexdigest(),
                "evaluation":evaluate(proposals)}
        (output/"model-proposals.json").write_text(
            json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        (output/"evaluation.json").write_text(
            json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("Vision benchmark:",json.dumps(report["evaluation"],ensure_ascii=False))
        print("Saved:",output/"evaluation.json")
        return 0
    finally:
        try:
            code,_=call("DELETE",base,token=token,close=True,timeout=20)
            print("Synthetic draft closed:",code==204,flush=True)
            if code!=204:
                print("WARNING: synthetic draft could not be deleted",file=sys.stderr)
        except (OSError,URLError):
            print("WARNING: synthetic draft cleanup failed",file=sys.stderr)


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (OSError,URLError,RuntimeError,KeyError,ValueError) as exc:
        print("R3.8 benchmark failed:",type(exc).__name__,str(exc),file=sys.stderr)
        raise SystemExit(1)
