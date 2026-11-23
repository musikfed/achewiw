"""R3: DocWork persistence tests. Only tmp_path SQLite and synthetic DOCX."""
from __future__ import annotations
from io import BytesIO
from hashlib import sha256
from docx import Document
from fastapi.testclient import TestClient
import pytest
import main


def synthetic_docx():
    doc=Document()
    doc.add_heading("Глава 1. Проверка",1)
    doc.add_paragraph("Синтетический текст редактора с кириллицей.")
    out=BytesIO();doc.save(out)
    return out.getvalue()


@pytest.fixture
def studio(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"achewiw.sqlite3")
    with TestClient(main.app,base_url="http://127.0.0.1:7080",
                    client=("127.0.0.1",12345)) as client:
        yield client,tmp_path


def create(client):
    src=synthetic_docx()
    res=client.post("/api/docwork/drafts",files={"file":("test.docx",src)})
    assert res.status_code==201,res.text
    assert res.json()["preview"]["type"]=="docx"
    return res.json(),src


def key(draft):
    return {"X-ACHEWIW-Docwork-Token":draft["token"]}


def test_r3_f5_restores_source_settings_pending_text_and_library_unchanged(studio):
    c,tmp=studio
    draft,src=create(c);url="/api/docwork/drafts/"+draft["id"]
    state={
        "settings":{"preset":"technical","output_name":"Инженерная редакция"},
        "overrides":{"0":"heading1"},"replacements":{"1":"Исправленный текст"},
        "editor":{"selected":1,"text":"Непринятый текст","role":"body"},
        "ui":{"search":"текст","filter":"all","rendered":160}}
    saved=c.put(url,headers=key(draft),json={"revision":1,"state":state})
    assert saved.status_code==200,saved.text
    assert saved.json()["revision"]==2
    # Stateless after page reload: fetch metadata and original from server.
    meta=c.get(url,headers=key(draft)).json()
    assert meta["state"]==state and meta["revision"]==2
    assert meta["sha256"]==sha256(src).hexdigest()
    original=c.get(url+"/source",headers=key(draft))
    assert original.status_code==200 and original.content==src
    assert Document(BytesIO(original.content)).paragraphs[1].text.startswith("Синтетический")
    assert c.get("/api/projects").json()==[]


def test_r3_parallel_tab_conflict_fails_closed(studio):
    c,_=studio;draft,_=create(c);url="/api/docwork/drafts/"+draft["id"]
    first=c.put(url,headers=key(draft),json={
        "revision":1,"state":{"replacements":{"1":"Первая вкладка"}}})
    assert first.status_code==200
    stale=c.put(url,headers=key(draft),json={
        "revision":1,"state":{"replacements":{"1":"Вторая вкладка"}}})
    assert stale.status_code==409 and "Конфликт" in stale.json()["detail"]
    latest=c.get(url,headers=key(draft)).json()
    assert latest["revision"]==2
    assert latest["state"]["replacements"]["1"]=="Первая вкладка"


def test_r3_immutable_history_and_rollback_as_new_revision(studio):
    c,_=studio;draft,src=create(c);url="/api/docwork/drafts/"+draft["id"]
    for n in (1,2,3):
        result=c.put(url,headers=key(draft),json={
            "revision":n,"state":{"settings":{"preset":str(n)}}})
        assert result.status_code==200,result.text
    versions=c.get(url+"/history",headers=key(draft)).json()
    assert [r["revision"] for r in versions]==[4,3,2,1]
    previous=c.get(url+"/history/2",headers=key(draft)).json()
    assert previous["state"]["settings"]["preset"]=="1"
    restored=c.put(url,headers=key(draft),json={
        "revision":4,"state":previous["state"]})
    assert restored.status_code==200 and restored.json()["revision"]==5
    assert c.get(url+"/history/4",headers=key(draft)).json()["state"]["settings"]["preset"]=="3"
    assert c.get(url+"/source",headers=key(draft)).content==src


def test_r3_capability_token_and_origin_protection(studio):
    c,_=studio;draft,_=create(c);url="/api/docwork/drafts/"+draft["id"]
    for suffix in ("","/source","/history","/history/1"):
        assert c.get(url+suffix).status_code==404
    assert c.put(url,json={"revision":1,"state":{}}).status_code==404
    assert c.get(url,headers={"X-ACHEWIW-Docwork-Token":"A"*43}).status_code==404
    bad_headers={**key(draft),"Origin":"https://evil.example"}
    assert c.put(url,headers=bad_headers,json={"revision":1,"state":{}}).status_code==403


def test_r3_invalid_state_and_bounded_disk_growth(studio):
    c,_=studio;draft,_=create(c);url="/api/docwork/drafts/"+draft["id"]
    for state in ([],{"unknown":{}},{"settings":[]},
                  {"replacements":{"0":"x"*125000}}):
        res=c.put(url,headers=key(draft),json={"revision":1,"state":state})
        assert res.status_code in (413,422),res.text
    assert c.put(url,headers=key(draft),
                 json={"revision":True,"state":{}}).status_code==422
    assert c.get(url,headers=key(draft)).json()["revision"]==1
    for i in range(4):
        create(c)
    sixth=c.post("/api/docwork/drafts",files={"file":("sixth.docx",synthetic_docx())})
    assert sixth.status_code==409 and "Лимит" in sixth.json()["detail"]


def test_r3_explicit_close_only_removes_selected_draft(studio):
    c,_=studio
    p=c.post("/api/projects",json={"title":"Рабочая книга — не удалять"}).json()
    a,_=create(c);b,_=create(c)
    url="/api/docwork/drafts/"+a["id"]
    assert c.delete(url,headers=key(a)).status_code==422
    assert c.get(url,headers=key(a)).status_code==200
    hdr={**key(a),"X-ACHEWIW-Confirm-Close-Draft":"yes-delete-only-this-draft"}
    assert c.delete(url,headers=hdr).status_code==204
    assert c.get(url,headers=key(a)).status_code==404
    assert c.get("/api/docwork/drafts/"+b["id"],headers=key(b)).status_code==200
    assert c.get("/api/projects/"+p["id"]).json()["title"]=="Рабочая книга — не удалять"


def test_r3_detects_corrupt_source_instead_of_silent_recovery(studio):
    c,_=studio;draft,_=create(c)
    with main.db() as conn:
        conn.execute("UPDATE docwork_drafts SET source=? WHERE id=?",(
            b"invalid",draft["id"]))
    resp=c.get("/api/docwork/drafts/"+draft["id"]+"/source",headers=key(draft))
    assert resp.status_code==409 and "Контрольная сумма" in resp.json()["detail"]


def test_r3_browser_has_state_controls_and_client_conflict_handler(studio):
    c,_=studio
    html=c.get("/docwork/").text
    js=c.get("/static/docwork.js").text
    for ident in ("draftStatus","draftHistory","saveDraft","reloadDraft",
                  "restoreDraftVersion","exportDraftState","closeDraft"):
        assert 'id="'+ident+'"' in html
    for marker in ("sessionStorage","restoreDraft()","pagehide",
                   "response.status===409","markDraftDirty"):
        assert marker in js


def test_r3_history_limit_is_fail_closed_not_silent_pruning(studio, monkeypatch):
    import docwork_sessions
    c,_=studio
    draft,original=create(c)
    monkeypatch.setattr(docwork_sessions,"MAX_REVISIONS",2)
    url="/api/docwork/drafts/"+draft["id"]
    assert c.put(url,headers=key(draft),
                 json={"revision":1,"state":{"settings":{"preset":"technical"}}}).status_code==200
    stop=c.put(url,headers=key(draft),
               json={"revision":2,"state":{"settings":{"preset":"modern"}}})
    assert stop.status_code==409 and "500 редакций" in stop.json()["detail"]
    assert [x["revision"] for x in c.get(url+"/history",headers=key(draft)).json()]==[2,1]
    assert c.get(url+"/source",headers=key(draft)).content==original


def test_r3_nan_or_unknown_state_is_refused_without_server_error(studio):
    c,_=studio
    draft,_=create(c)
    url="/api/docwork/drafts/"+draft["id"]
    resp=c.put(url,headers={**key(draft),"Content-Type":"application/json"},
        content='{"revision":1,"state":{"settings":{"unsupported":NaN}}}')
    assert resp.status_code==422
    assert c.get(url,headers=key(draft)).json()["revision"]==1


def test_r3_click_passes_bool_not_mouse_event_to_inspection(studio):
    c,_=studio
    js=c.get("/static/docwork.js").text
    assert "$('inspect').addEventListener('click', () => { void inspect(false); });" in js
    assert "const reusing=restoring||!!draft;" in js
    assert "reusing?'/api/docwork/inspect':'/api/docwork/drafts'" in js
