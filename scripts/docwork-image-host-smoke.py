#!/usr/bin/env python3
"""DocWork R3.2 integrated editing smoke test against a running ACHEWIW Studio.

Only synthetic DOCX bytes and a test draft with an unguessable ID are used.
On successful test draft creation it explicitly deletes ONLY that test draft.
No private manuscripts or real author-project endpoints are accessed.
"""
from __future__ import annotations

from io import BytesIO
from hashlib import sha256
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
from zipfile import ZipFile
import json
import secrets
import sys

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from PIL import Image

BASE = "http://127.0.0.1:7080"
HTTP = build_opener(ProxyHandler({}))
count = 0


def ok(name, condition):
    global count
    if not condition:
        raise AssertionError(name)
    count += 1
    print("PASS " + name, flush=True)


def request(method, path, payload=None, ctype=None, token=None, close=False):
    headers = {"Host": "127.0.0.1:7080"}
    if ctype:
        headers["Content-Type"] = ctype
    if token:
        headers["X-ACHEWIW-Docwork-Token"] = token
    if close:
        headers["X-ACHEWIW-Confirm-Close-Draft"] = "yes-delete-only-this-draft"
    r = Request(BASE + path, data=payload, method=method, headers=headers)
    try:
        with HTTP.open(r, timeout=45) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def multipart(file_bytes, name="images.docx", fields=None):
    boundary="achewiw-art-" + secrets.token_hex(10)
    chunks=[]
    for key, value in (fields or {}).items():
        chunks += [
            ("--"+boundary+"\r\nContent-Disposition: form-data; name=\""+key+
             "\"\r\n\r\n"+str(value)+"\r\n").encode()
        ]
    chunks += [
        ("--"+boundary+"\r\nContent-Disposition: form-data; name=\"file\"; filename=\""+
         name+"\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode(),
        file_bytes,
        ("\r\n--"+boundary+"--\r\n").encode()
    ]
    return b"".join(chunks), "multipart/form-data; boundary="+boundary


def test_docx():
    doc=Document()
    doc.add_heading("Тестовая глава",1)
    image=Image.new("RGB",(320,180),(207,138,51))
    out=BytesIO();image.save(out,"PNG");out.seek(0)
    doc.add_paragraph().add_run().add_picture(out)
    doc.add_paragraph("Рисунок 1 — Проверка скругления",style="Caption")
    doc.add_paragraph("Текст после иллюстрации для проверки переноса.")
    out=BytesIO();doc.save(out)
    return out.getvalue()


def main():
    original=test_docx()
    code,resp=request("GET","/api/health")
    ok("Studio health HTTP 200",code==200)
    body,mime=multipart(original)
    code,resp=request("POST","/api/docwork/drafts",body,mime)
    ok("R3.1 synthetic draft created",code==201)
    draft=json.loads(resp)
    sid,token=draft["id"],draft["token"]
    base="/api/docwork/drafts/"+sid
    try:
        inventory=draft["preview"].get("images",[])
        ok("R3.1 editable picture inventoried",
           len(inventory)==1 and inventory[0]["editable"] is True)
        code,thumb=request("GET",base+"/images/0",token=token)
        ok("R3.1 private thumbnail PNG",code==200 and thumb.startswith(b"\x89PNG"))
        code,_=request("GET",base+"/images/0")
        ok("R3.1 thumbnail requires token",code==404)
        styles={"0":{"width_percent":65,"align":"center","corner_percent":16}}
        positions={"0":{"before_paragraph":0,"order":1}}
        edits={"3":"Отредактированный текст после иллюстрации."}
        state={"images":styles,"moves":positions,"replacements":edits,
               "settings":{"preset":"book"}}
        code,resp=request("PUT",base,json.dumps({"revision":1,"state":state}).encode(),
                          "application/json",token)
        ok("R3.1 image styling saved",code==200)
        code,resp=request("GET",base,token=token)
        ok("R3.1 image style survives reload",
           code==200 and json.loads(resp).get("state",{}).get("images")==styles)
        ok("R3.2 picture position survives reload",
           code==200 and json.loads(resp).get("state",{}).get("moves")==positions)

        body,mime=multipart(original,fields={
            "options":json.dumps({"preset":"book","image_edits":styles,
                                  "image_moves":positions,"replacements":edits}),
            "target":"docx"
        })
        code,resp=request("POST","/api/docwork/export",body,mime)
        ok("R3.1 styled DOCX exported",code==200 and resp.startswith(b"PK"))
        revised=Document(BytesIO(resp))
        ok("R3.2 picture actually moved before chapter heading",
           "w:drawing" in revised.paragraphs[0]._p.xml
           and revised.paragraphs[2].text=="Тестовая глава")
        ok("R3.2 caption moved together with picture",
           revised.paragraphs[1].text.startswith("Рисунок 1"))
        ok("R3.2 text edited on document canvas is exported",
           revised.paragraphs[3].text=="Отредактированный текст после иллюстрации.")
        shape=revised.inline_shapes[0]
        rid=shape._inline.xpath(".//a:blip")[0].get(qn("r:embed"))
        blob=revised.part.related_parts[rid].blob
        with Image.open(BytesIO(blob)) as im:
            ok("R3.1 actual rounded transparent PNG",
               im.mode=="RGBA" and im.getpixel((0,0))[3]==0
               and im.getpixel((160,90))[3]==255)
        section=revised.sections[0]
        text_width=section.page_width-section.left_margin-section.right_margin
        ok("R3.1 relative width and center alignment",
           abs(shape.width/text_width-0.65)<0.02
           and revised.paragraphs[0].alignment==WD_ALIGN_PARAGRAPH.CENTER)
        code,source=request("GET",base+"/source",token=token)
        ok("R3.1 source still byte-identical",
           code==200 and sha256(source).digest()==sha256(original).digest())
    finally:
        code,_=request("DELETE",base,token=token,close=True)
        ok("R3.1 disposable draft explicitly closed",code==204)
    print("DOCWORK IMAGE HOST SMOKE PASS; checks="+str(count),flush=True)
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never print response bodies, token, user paths or private data.
        print("DOCWORK IMAGE HOST SMOKE FAIL: "+type(exc).__name__,flush=True)
        raise SystemExit(1)
