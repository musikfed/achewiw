"""R3.6: independent replacement image assets + real DOCX export.

Only synthetic manuscript and pictures; no real author files, cloud, GPU or LLM.
"""
from __future__ import annotations

from io import BytesIO
from hashlib import sha256
from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest
import main


def png(color, shape=(300,160)):
    im=Image.new("RGB",shape,color)
    draw=ImageDraw.Draw(im)
    draw.rectangle((30,20,100,70),outline="black",width=3)
    out=BytesIO();im.save(out,"PNG")
    return out.getvalue()


def sample_document():
    im=png((220,140,70))
    doc=Document()
    doc.add_paragraph("Глава 1")
    doc.add_paragraph().add_run().add_picture(BytesIO(im))
    doc.add_paragraph("Рисунок 1 — подпись",style="Caption")
    doc.add_paragraph().add_run().add_picture(BytesIO(im))
    doc.add_paragraph("Рисунок 2 — исходная копия",style="Caption")
    out=BytesIO();doc.save(out)
    return out.getvalue()


def picture_at(blob,index):
    document=Document(BytesIO(blob))
    shape=document.inline_shapes[index]
    rel=shape._inline.xpath(".//a:blip")[0].get(qn("r:embed"))
    return document.part.related_parts[rel].blob


def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"test.sqlite3")


def test_image_asset_upload_export_undo_restore_and_document_integrity(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    raw=sample_document()
    original=sha256(raw).hexdigest()
    replacement=png((40,110,200),(400,300))
    with TestClient(main.app,base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1",12345)) as c:
        created=c.post("/api/docwork/drafts",
            files={"file":("test.docx",raw)})
        assert created.status_code==201,created.text
        draft=created.json()
        sid,token=draft["id"],draft["token"]
        base="/api/docwork/drafts/"+sid
        headers={"X-ACHEWIW-Docwork-Token":token}
        upload=c.post(base+"/assets",
            files={"file":("new-diagram.png",replacement)},headers=headers)
        assert upload.status_code==201,upload.text
        asset=upload.json()
        aid=asset["id"]
        assert len(aid)==32
        assert c.get(base+"/assets/"+aid).status_code==404
        preview=c.get(base+"/assets/"+aid,headers=headers)
        assert preview.status_code==200 and preview.content.startswith(b"\x89PNG")
        bad=c.post(base+"/assets",
            files={"file":("trick.svg",b"<svg><script>alert(1)</script></svg>")},
            headers=headers)
        assert bad.status_code==422
        state={"artwork":{"0":aid},"settings":{"preset":"preserve"}}
        saving=c.put(base,headers=headers,json={"revision":1,"state":state})
        assert saving.status_code==200,saving.text
        saved=c.get(base,headers=headers)
        assert saved.json()["state"]["artwork"]=={"0":aid}
        options={
            "preset":"preserve",
            "image_assets":{"0":aid},
            "image_edits":{"0":{"width_percent":65,"align":"center","corner_percent":8}}
        }
        form={"target":"docx","options":__import__("json").dumps(options),
              "draft_id":sid}
        missing_token=c.post("/api/docwork/export",
            files={"file":("test.docx",raw)},data=form)
        assert missing_token.status_code==404
        bad_source=c.post("/api/docwork/export",
            files={"file":("test.docx",raw+b"X")},data=form,headers=headers)
        assert bad_source.status_code==409
        result=c.post("/api/docwork/export",files={"file":("test.docx",raw)},
                      data=form,headers=headers)
        assert result.status_code==200,result.text
        out=Document(BytesIO(result.content))
        assert len(out.inline_shapes)==2
        assert out.paragraphs[2].text=="Рисунок 1 — подпись"
        with Image.open(BytesIO(picture_at(result.content,0))) as im:
            assert im.size==(400,300) and im.getpixel((0,0))[3]==0
            assert im.getpixel((200,150))[:3]==(40,110,200)
        assert picture_at(result.content,1)==picture_at(raw,1)
        assert sha256(c.get(base+"/source",headers=headers).content).hexdigest()==original
        assert c.put(base,headers=headers,json={"revision":2,
                     "state":{"artwork":{}}}).status_code==200
        assert c.get(base,headers=headers).json()["state"]["artwork"]=={}
        earlier=c.get(base+"/history/2",headers=headers)
        assert earlier.status_code==200 and earlier.json()["state"]["artwork"]=={"0":aid}
        assert c.get(base+"/assets/"+aid,headers=headers).status_code==200
        close={**headers,"X-ACHEWIW-Confirm-Close-Draft":"yes-delete-only-this-draft"}
        assert c.delete(base,headers=close).status_code==204
        assert c.get(base+"/assets/"+aid,headers=headers).status_code==404


def test_sibling_drafts_cannot_read_other_image_assets(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    raw=sample_document()
    with TestClient(main.app,base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1",12345)) as c:
        docs=[c.post("/api/docwork/drafts",
                     files={"file":(f"test{i}.docx",raw)}).json() for i in range(2)]
        h0={"X-ACHEWIW-Docwork-Token":docs[0]["token"]}
        h1={"X-ACHEWIW-Docwork-Token":docs[1]["token"]}
        r=c.post("/api/docwork/drafts/"+docs[0]["id"]+"/assets",
                 headers=h0,files={"file":("alt.png",png((25,125,200)))})
        aid=r.json()["id"]
        other=c.get("/api/docwork/drafts/"+docs[1]["id"]+"/assets/"+aid,
                    headers=h1)
        assert other.status_code==404
        for doc,h in zip(docs,[h0,h1]):
            assert c.delete("/api/docwork/drafts/"+doc["id"],headers={
                **h,"X-ACHEWIW-Confirm-Close-Draft":"yes-delete-only-this-draft"
            }).status_code==204


def test_replacement_table_picture_does_not_change_table_layout():
    from document_workshop import transform_docx
    doc=Document()
    cell=doc.add_table(rows=1,cols=1).cell(0,0)
    cell.paragraphs[0].add_run().add_picture(BytesIO(png((210,180,80))))
    out=BytesIO();doc.save(out)
    raw=out.getvalue()
    result=transform_docx(raw,{"_replacement_blobs":{"0":png((35,80,135),(450,100))}})
    new=Document(BytesIO(result))
    assert len(new.tables)==1 and len(new.inline_shapes)==1
    assert new.inline_shapes[0].width>new.inline_shapes[0].height
    assert picture_at(result,0)!=picture_at(raw,0)


def test_editor_has_workbench_tab_with_real_image_upload():
    html=(main.STATIC_DIR/"docwork.html").read_text(encoding="utf-8")
    main_js=(main.STATIC_DIR/"docwork.js").read_text(encoding="utf-8")
    wb_js=(main.STATIC_DIR/"docwork_workbench.js").read_text(encoding="utf-8")
    for marker in ['id="imageWorkbench"','id="wbExternal"','id="wbCanvas"',
                   'id="wbApply"','id="wbRestore"','id="openImageWorkbench"',
                   'id="wbOriginalPreview"','id="wbCurrentPreview"']:
        assert marker in html
    assert 'window.ACHEWIWWorkbench?.connect' in main_js
    assert 'image_assets:{...imageAssets}' in main_js
    assert 'artwork:{...imageAssets}' in main_js
    assert 'function authenticatedExportHeaders()' in main_js
    assert "new FormData()" in wb_js and "/assets" in wb_js
    assert "host.applyAsset(index,asset.id)" in wb_js
    assert "Не" in wb_js and "canvas" in wb_js
