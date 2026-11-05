"""Sequential GPU use between ACHEWIW writing, prompt prep and ComfyUI.

All tests are isolated with a temporary SQLite database and fake Ollama.
No real GPU, internet, ComfyUI or external apps are touched.
"""
from io import BytesIO
from pathlib import Path
import sys
import sqlite3
import uuid

import pytest
from PIL import Image
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main
import image_engine
from gpu_scheduling import reserve_gpu, release_gpu, image_operation_active


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.sqlite3")
    with TestClient(main.app, base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1", 45321)) as client:
        p=client.post("/api/projects",json={"title":"Проверка GPU",
                                            "work_type":"fiction"}).json()
        sid="a"*32
        p["scenes"]=[{"id":sid,"title":"Сцена","text":"Текст автора."}]
        saved=client.put("/api/projects/"+p["id"],json=p)
        assert saved.status_code==200,saved.text
        yield client,p["id"],sid


class MockResponse:
    def __init__(self, data, status=200):
        self.data=data
        self.status=status
    def raise_for_status(self):
        if self.status>=400:
            raise RuntimeError("upstream status "+str(self.status))
    def json(self):
        return self.data


def fake_ollama(monkeypatch, loaded=None):
    calls=[]
    class FakeAsyncClient:
        def __init__(self,*args,**kwargs): pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        async def get(self,url,**kwargs):
            calls.append(("GET",url))
            if url.endswith("/api/tags"):
                return MockResponse({"models":[{"name":"qwen3:8b"}]})
            if url.endswith("/api/ps"):
                return MockResponse({"models":loaded if loaded is not None else []})
            raise AssertionError("unexpected GET "+url)
        async def post(self,url,**kwargs):
            calls.append(("POST",url,kwargs.get("json")))
            if url.endswith("/api/generate"):
                return MockResponse({"response":"A black cat on a 1960s Moscow street."})
            raise AssertionError("unexpected POST "+url)
    monkeypatch.setattr(main.httpx,"AsyncClient",FakeAsyncClient)
    return calls


def test_gpu_lease_is_atomic_and_expires():
    conn=sqlite3.connect(":memory:")
    from gpu_scheduling import init_gpu_leases
    init_gpu_leases(conn)
    assert reserve_gpu(conn,"a","image",at=10)
    assert image_operation_active(conn,at=11)
    assert not reserve_gpu(conn,"b","text",at=12)
    release_gpu(conn,"stranger")
    assert image_operation_active(conn,at=13)
    assert reserve_gpu(conn,"b","text",at=700)
    assert image_operation_active(conn,at=701)
    release_gpu(conn,"b")
    assert not image_operation_active(conn,at=701)


def test_active_image_blocks_text_model_unload_and_prompt(studio):
    client,pid,sid=studio
    with main.db() as conn:
        assert reserve_gpu(conn,"busy","image")
    requests=[
        client.post("/api/ai",json={"project_id":pid,"mode":"outline",
                                     "model":"qwen3:8b"}),
        client.post("/api/illustration-prompt/refine",json={
            "description":"Кот на улице","model":"qwen3:8b","confirm_shared_model":True
        }),
        client.post("/api/models/unload",json={
            "model":"qwen3:8b","confirm_shared_model":True
        }),
        client.post("/api/projects/"+pid+"/illustrations/generate",
                    json={"scene_id":sid,"prompt":"a cat"})
    ]
    assert all(r.status_code==409 for r in requests),[(r.status_code,r.text) for r in requests]
    with main.db() as conn:
        release_gpu(conn,"busy")


def test_prompt_refinement_requires_explicit_consent_and_not_an_image(studio,monkeypatch):
    client,pid,sid=studio
    assert client.post("/api/illustration-prompt/refine",json={
        "description":"Чёрный кот","model":"qwen3:8b","confirm_shared_model":False
    }).status_code==422
    calls=fake_ollama(monkeypatch)
    result=client.post("/api/illustration-prompt/refine",json={
        "description":"Миирус — кот в Москве 1964 года","model":"qwen3:8b",
        "confirm_shared_model":True
    })
    assert result.status_code==200,result.text
    assert "black cat" in result.json()["prepared_prompt"]
    assert result.json()["status"]=="author_review_required"
    gen=[x for x in calls if x[0]=="POST"]
    assert len(gen)==1
    assert gen[0][2]["keep_alive"]==0
    assert "Миирус" in gen[0][2]["prompt"]
    assert client.get("/api/projects/"+pid+"/illustrations").json()==[]
    with main.db() as conn:
        assert not image_operation_active(conn)


def test_no_images_while_any_ollama_model_loaded_even_if_override_sent(studio,monkeypatch):
    client,pid,sid=studio
    calls=fake_ollama(monkeypatch,loaded=[
        {"name":"qwen3:8b","size_vram":5200000000}
    ])
    async def engine(): return {"available":True,"model":"sd_xl_base"}
    async def forbidden(prompt): raise AssertionError("ComfyUI must not run")
    monkeypatch.setattr(main,"illustration_engine_status",engine)
    monkeypatch.setattr(main,"generate_illustration_image",forbidden)
    result=client.post("/api/projects/"+pid+"/illustrations/generate",json={
        "scene_id":sid,"prompt":"black cat","confirm_shared_gpu":True
    })
    assert result.status_code==409,result.text
    assert any(x[0]=="GET" and x[1].endswith("/api/ps") for x in calls)
    with main.db() as conn:
        assert not image_operation_active(conn)


def test_image_saves_only_after_empty_ollama_and_explicit_free(studio,monkeypatch):
    client,pid,sid=studio
    calls=fake_ollama(monkeypatch)
    async def engine(): return {"available":True,"model":"sd_xl_base"}
    picture=BytesIO()
    Image.new("RGB",(8,8),(12,34,56)).save(picture,format="PNG")
    async def generated(prompt):
        assert prompt=="A black cat on a 1960s Moscow street."
        with main.db() as conn:
            assert image_operation_active(conn)
        return picture.getvalue(),"sd_xl_base_1.0.safetensors"
    released=[]
    async def free():
        released.append(True)
        return "free_requested"
    monkeypatch.setattr(main,"illustration_engine_status",engine)
    monkeypatch.setattr(main,"generate_illustration_image",generated)
    monkeypatch.setattr(image_engine,"free_cached_models",free)
    result=client.post("/api/projects/"+pid+"/illustrations/generate",json={
        "scene_id":sid,"prompt":"A black cat on a 1960s Moscow street.",
        "release_comfy_vram":True
    })
    assert result.status_code==201,result.text
    assert result.json()["comfy_vram"]=="free_requested"
    assert released==[True]
    assert len(client.get("/api/projects/"+pid+"/illustrations").json())==1
    assert not any(x[0]=="POST" for x in calls),calls
    with main.db() as conn:
        assert not image_operation_active(conn)



def test_comfyui_free_only_when_shared_queue_is_empty(monkeypatch):
    import asyncio
    calls=[]
    class FakeComfy:
        def __init__(self,busy):self.busy=busy
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        async def get(self,url,**kwargs):
            calls.append(("GET",url))
            return MockResponse({"queue_running": [["other-task"]] if self.busy else [],
                                 "queue_pending": []})
        async def post(self,url,**kwargs):
            calls.append(("POST",url,kwargs["json"]))
            return MockResponse({})
    monkeypatch.setattr(image_engine.httpx,"AsyncClient",
                        lambda **kwargs: FakeComfy(True))
    assert asyncio.run(image_engine.free_cached_models())=="skipped_comfyui_queue_busy"
    assert all(x[0]=="GET" for x in calls)
    calls.clear()
    monkeypatch.setattr(image_engine.httpx,"AsyncClient",
                        lambda **kwargs: FakeComfy(False))
    assert asyncio.run(image_engine.free_cached_models())=="free_requested"
    assert calls[-1][1].endswith("/free")
    assert calls[-1][2]=={"unload_models":True,"free_memory":True}


def test_comfyui_control_api_is_opt_in_allowlisted_and_checks_gpu_lease(studio, monkeypatch):
    client, _project_id, _scene_id = studio
    missing = client.get("/api/illustration-engine/control")
    assert missing.status_code == 503

    # Browser cannot send shell commands, arbitrary actions or malformed IDs.
    assert client.post("/api/illustration-engine/control",
                       json={"action": "bash -c id"}).status_code == 422
    assert client.get("/api/illustration-engine/control/jobs/../x").status_code == 404

    token_path = main.DATA_DIR / "comfyui-control.token"
    token_path.write_text("A" * 56, encoding="utf-8")
    token_path.chmod(0o600)
    calls = []

    class FakeControlResponse:
        status_code = 200
        def json(self):
            return {"installed": True, "model_present": True, "api_ready": True}

    class FakeControlClient:
        def __init__(self, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def request(self, method, url, *, headers, json):
            calls.append((method, url, headers, json))
            response = FakeControlResponse()
            if method == "POST":
                response.status_code = 202
                response.json = lambda: {"job_id": "a" * 32, "action": "start"}
            return response

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeControlClient)
    status = client.get("/api/illustration-engine/control")
    assert status.status_code == 200, status.text
    assert status.json()["installed"] is True
    assert calls[-1][:2] == ("GET", "http://127.0.0.1:8190/status")
    assert calls[-1][2]["X-ACHEWIW-Control-Token"] == "A" * 56

    with main.db() as connection:
        assert reserve_gpu(connection, "busy", "text")
    blocked = client.post("/api/illustration-engine/control", json={"action": "stop"})
    assert blocked.status_code == 409, blocked.text
    assert not any(item[0] == "POST" for item in calls)
    with main.db() as connection:
        release_gpu(connection, "busy")

    denied = client.post("/api/illustration-engine/control",
                         headers={"Origin": "http://evil.example"},
                         json={"action": "start"})
    assert denied.status_code == 403
    started = client.post("/api/illustration-engine/control", json={"action": "start"})
    assert started.status_code == 202, started.text
    assert calls[-1][:2] == ("POST", "http://127.0.0.1:8190/control")
    assert calls[-1][3] == {"action": "start"}


def test_browser_persistence_and_cache_scoping_markers():
    app=(Path(__file__).resolve().parents[1]/"app/static/app.js").read_text()
    illustrations=(Path(__file__).resolve().parents[1]/"app/static/illustrations.js").read_text()
    html=(Path(__file__).resolve().parents[1]/"app/static/index.html").read_text()
    control=(Path(__file__).resolve().parents[1]/"scripts/achewiw-control.sh").read_text()
    updater=(Path(__file__).resolve().parents[1]/"scripts/update-studio-clean.sh").read_text()
    for marker in ("manuscriptDraft","activeProjectId","writingScene","lastScrollAt",
                   "selectedModel","uiKey('tab')","achewiw:tab-shown",
                   "queueUnsavedProjectSave","draftSaveTimer"):
        assert marker in app,marker
    for marker in ("illustrationPreparedPrompt","illustrationRefine",
                   "illustrationReleaseComfy"):
        assert marker in illustrations and marker in html,marker
    for marker in ("saveDraft","restoreDraft","achewiw:tab-shown",
                    "resumeControlJob","savedControlJob","watchControlJob",
                    "const prompt=prepared||plain",
                    "release_comfy_vram:el('illustrationReleaseComfy').checked"):
        assert marker in illustrations,marker
    assert "illustrationEnginePrepare" in illustrations and "illustrationEnginePrepare" in html
    assert "20261105-narrative-v12" in html
    assert "rebuild-fresh" in control
    assert "compose build --no-cache studio" in control
    assert "comfyui-control-agent.sh reload" in updater
    # A Git fast-forward may replace the updater itself. The running Bash process
    # must re-exec the new script before validating the newly fetched assets.
    assert 'updater_before="$(git hash-object scripts/update-studio-clean.sh)"' in updater
    assert 'exec bash "$ROOT/scripts/update-studio-clean.sh" apply' in updater
    assert 'class AssetParser(HTMLParser):' in updater
    assert "grep -Fq '20261105-narrative-v12'" not in updater
    assert "docker system prune" not in updater
    assert "rm -rf" not in updater
    assert "data/achewiw.sqlite3" in updater
