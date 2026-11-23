#!/usr/bin/env python3
"""R1-R3 host smoke in the REAL ACHEWIW Studio container.

All documents and illustrations are synthetic. R2 uses only /tmp. R3
creates one synthetic draft through the public private-LAN API and explicitly
deletes only that identified test draft when finished. The report never prints
capability tokens, database contents, environment variables or manuscript text.
"""
from __future__ import annotations

from io import BytesIO
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zipfile import ZipFile
import json
import secrets
import sys

import fitz
from PIL import Image
from docx import Document

from manuscript_core import manuscript_epub
from publishing_exports import manuscript_pdf, manuscript_docx

BASE = "http://127.0.0.1:7080"
STATS = []


def check(name, predicate):
    if not predicate:
        raise AssertionError(name)
    STATS.append(name)
    print("PASS " + name, flush=True)


def call(method, path, data=None, content_type=None, token=None, confirm=False):
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    if token:
        headers["X-ACHEWIW-Docwork-Token"] = token
    if confirm:
        headers["X-ACHEWIW-Confirm-Close-Draft"] = "yes-delete-only-this-draft"
    req = Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as response:
            return response.status, response.read()
    except HTTPError as err:
        return err.code, err.read()


def form(fields, blob, name="synthetic.pdf"):
    boundary = "achewiw-r123-" + secrets.token_hex(12)
    out = BytesIO()
    for key, value in fields.items():
        out.write(("--" + boundary + "\r\n"
                   + 'Content-Disposition: form-data; name="' + key + '"\r\n\r\n'
                   + str(value) + "\r\n").encode())
    out.write(("--" + boundary + "\r\n"
               + 'Content-Disposition: form-data; name="file"; filename="' + name + '"\r\n'
               + "Content-Type: application/octet-stream\r\n\r\n").encode())
    out.write(blob)
    out.write(("\r\n--" + boundary + "--\r\n").encode())
    return out.getvalue(), "multipart/form-data; boundary=" + boundary


def fake_png(path=None):
    out = BytesIO()
    im = Image.new("RGB", (72, 54), "#de8844")
    if path is not None:
        im.save(path, "PNG")
    im.save(out, "PNG")
    return out.getvalue()


def synthetic_pdf():
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((40, 75), "LEFT ONE: engineering document.")
    page.insert_text((40, 130), "LEFT TWO: technical chapter.")
    page.insert_text((320, 75), "RIGHT ONE: page result.")
    page.insert_text((320, 130), "RIGHT TWO: summary.")
    page.insert_image(fitz.Rect(40, 200, 160, 290), stream=fake_png())
    page.insert_text((40, 320), "Figure 1 - Synthetic image caption.")
    data = pdf.tobytes()
    pdf.close()
    return data


def test_r1():
    src = synthetic_pdf()
    body, ctype = form({"target": "docx", "options": json.dumps({"preset": "preserve"})}, src)
    code, result = call("POST", "/api/docwork/export", body, ctype)
    check("R1 PDF-DOCX HTTP 200", code == 200)
    with ZipFile(BytesIO(result)) as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
    check("R1 PDF raster preserved", len(media) == 1)
    text = " ".join(p.text for p in Document(BytesIO(result)).paragraphs)
    check("R1 two-column reading order",
          text.index("LEFT ONE") < text.index("LEFT TWO")
          < text.index("RIGHT ONE") < text.index("RIGHT TWO"))


def test_r2():
    with TemporaryDirectory(prefix="achewiw-r2-test-") as folder:
        p = Path(folder) / "test.png"
        fake_png(p)
        scene = SimpleNamespace(id="a" * 32, title="Глава 1", text=
            "Синтетическая глава для контроля качества.\n\n"
            + "Лабораторный текст для проверки вёрстки. " * 40)
        book = SimpleNamespace(id="c" * 32, title="Проверка черновика",
                               work_type="technical", author_name="Тест",
                               scenes=[scene])
        art = {"id": "b" * 32, "scene_id": scene.id, "caption": "Рисунок 1 — Тест",
               "placement": "end", "path": p}
        docx = manuscript_docx(book, illustrations=[art])
        d = Document(BytesIO(docx))
        i = next(i for i,x in enumerate(d.paragraphs) if "w:drawing" in x._p.xml)
        check("R2 Word keeps picture with caption",
              d.paragraphs[i].paragraph_format.keep_with_next is True
              and d.paragraphs[i+1].text == art["caption"])
        pdf = manuscript_pdf(book, illustrations=[art])
        with fitz.open(stream=pdf, filetype="pdf") as document:
            image_pages = [i for i,page in enumerate(document) if page.get_images()]
            caption_pages = [i for i,page in enumerate(document)
                             if page.search_for("Рисунок 1")]
        check("R2 PDF figure and caption share page",
              len(image_pages)==1 and image_pages==caption_pages)
        # No authored documents in this test: remove ONLY the synthetic fixture.
        p.unlink()
        try:
            manuscript_epub(book, illustrations=[art])
        except ValueError:
            check("R2 missing artwork blocks EPUB", True)
        else:
            check("R2 missing artwork blocks EPUB", False)


def synthetic_docx():
    doc = Document()
    doc.add_heading("Synthetic chapter", level=1)
    doc.add_paragraph("Test paragraph for a durable R3 draft.")
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def test_r3():
    original = synthetic_docx()
    body, ctype = form({}, original, "r3-test.docx")
    code, response = call("POST", "/api/docwork/drafts", body, ctype)
    check("R3 create synthetic draft HTTP 201", code == 201)
    data = json.loads(response)
    draft_id, token = data["id"], data["token"]
    path = "/api/docwork/drafts/" + draft_id
    try:
        state = {"settings":{"preset":"technical"},
                 "replacements":{"1":"Synthetic editor change"},
                 "editor":{"selected":1,"text":"Not yet applied","role":"body"}}
        payload = json.dumps({"revision":1,"state":state}).encode()
        code, response = call("PUT", path, payload, "application/json", token)
        check("R3 persist revised editor state", code == 200)
        code, response = call("GET", path, token=token)
        current = json.loads(response) if code == 200 else {}
        check("R3 GET after F5 restores revision and state",
              code==200 and current.get("revision")==2 and current.get("state")==state)
        code, content = call("GET", path+"/source", token=token)
        check("R3 original checksum unchanged",
              code==200 and sha256(content).hexdigest()==sha256(original).hexdigest())
        code, _ = call("PUT", path, payload, "application/json", token)
        check("R3 stale second-tab revision rejected", code == 409)
        code, response = call("GET", path+"/history", token=token)
        versions = json.loads(response) if code == 200 else []
        check("R3 history retains original and new revision",
              code==200 and [v["revision"] for v in versions]==[2,1])
    finally:
        # User-authorized cleanup of the ONE disposable synthetic test draft.
        code, _ = call("DELETE", path, token=token, confirm=True)
        check("R3 explicitly remove own synthetic test draft", code == 204)


def main():
    try:
        status, response = call("GET", "/api/health")
        check("ACHEWIW /api/health HTTP 200", status==200)
        test_r1()
        test_r2()
        test_r3()
    except Exception as exc:
        # Class-only report avoids leaking HTTP payloads, paths or secret keys.
        print("FAIL " + type(exc).__name__, flush=True)
        return 1
    print("R1-R3 HOST SMOKE PASS; checks=" + str(len(STATS)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
