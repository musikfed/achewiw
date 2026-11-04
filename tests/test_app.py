import asyncio
import time
import base64
import os

os.environ["ACHEWIW_PASSWORD"] = "test-password"
from fastapi.testclient import TestClient
import main


def test_project_and_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.sqlite3")
    auth = base64.b64encode(b"author:test-password").decode()
    headers = {"Authorization": "Basic " + auth}
    with TestClient(main.app) as client:
        assert client.get("/api/projects").status_code == 401
        created = client.post("/api/projects", json={"title": "История"}, headers=headers)
        assert created.status_code == 201
        project = created.json()
        project["scenes"] = [
            {"title": "Начало", "active_objects": ["1", "2", "3", "4", "5", "6"],
             "text": "Один два три."}
        ]
        updated = client.put(
            f"/api/projects/{project['id']}", json=project, headers=headers
        )
        assert updated.status_code == 200
        stats = client.get(
            f"/api/projects/{project['id']}/stats", headers=headers
        ).json()
        assert stats["total_words"] == 3
        assert stats["scenes"][0]["overloaded"] is True
        exported = client.get(
            f"/api/projects/{project['id']}/export", headers=headers
        )
        assert "Один два три" in exported.text


def test_outline_parser():
    result = main.parse_outline('{"scenes":[{"title":"Первая","active_objects":["кот"]}]}')
    assert len(result) == 1
    assert result[0].active_objects == ["кот"]


def test_generation_job_is_persistent_and_restorable_after_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "achewiw.sqlite3")
    headers={"Authorization": "Basic " + base64.b64encode(b"author:test-password").decode()}

    async def fake_generate(job_id, payload, mode):
        await asyncio.sleep(0.08)
        with main.db() as connection:
            main.update_job(connection, job_id, stage="Генерация текста",
                            output="Черновик части 1", percent=18, eta_seconds=55)
        await asyncio.sleep(0.15)
        with main.db() as connection:
            main.update_job(connection, job_id, status="ready",
                            stage="Готово", output="Черновик готов",
                            percent=100, eta_seconds=0)
    monkeypatch.setattr(main, "execute_generation", fake_generate)
    with TestClient(main.app) as client:
        project=client.post("/api/projects",json={"title":"Продолжение"},headers=headers).json()
        started=client.post("/api/ai",json={"project_id":project["id"],"mode":"outline",
                             "model":"qwen3:8b"},headers=headers)
        assert started.status_code==202, started.text
        jid=started.json()["id"]
        assert client.post("/api/ai",json={"project_id":project["id"],"mode":"audit",
                                           "model":"qwen3:8b"},headers=headers).status_code==409
        seen_partial=False
        for _ in range(45):
            job=client.get(f"/api/ai/jobs/{jid}",headers=headers).json()
            if job["result"]=="Черновик части 1":
                seen_partial=True
            if job["status"]=="ready":break
            time.sleep(0.02)
        assert seen_partial
        assert job["status"]=="ready"
        assert job["result"]=="Черновик готов"
        # A brand-new GET, equivalent to refreshing a browser, sees DB state.
        history=client.get(f"/api/projects/{project['id']}/ai/jobs",headers=headers)
        assert history.status_code==200
        assert history.json()[0]["id"]==jid
        assert history.json()[0]["result"]=="Черновик готов"
        assert client.get(f"/api/ai/jobs/{jid}").status_code==401
        html=client.get("/",headers=headers).text
        assert 'id="aiProgress"' in html
        assert 'id="unloadModel"' in html


def test_generation_restart_retains_partial_and_blocks_project_deletion(tmp_path,monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "achewiw.sqlite3")
    headers={"Authorization":"Basic "+base64.b64encode(b"author:test-password").decode()}
    with TestClient(main.app) as client:
        p=client.post("/api/projects",json={"title":"Тест"},headers=headers).json()
        with main.db() as connection:
            main.create_job(connection,"c"*32,p["id"],"outline","qwen3:8b",None)
            main.update_job(connection,"c"*32,output="Сохранённая половина",percent=23)
        assert client.delete(f"/api/projects/{p['id']}",headers=headers).status_code==409
    with TestClient(main.app) as client:
        j=client.get("/api/ai/jobs/"+"c"*32,headers=headers).json()
        assert j["status"]=="interrupted"
        assert j["result"]=="Сохранённая половина"
        assert client.delete(f"/api/projects/{p['id']}",headers=headers).status_code==204


def test_unload_selected_ollama_model_only_with_informed_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"achewiw.sqlite3")
    headers={"Authorization":"Basic "+base64.b64encode(b"author:test-password").decode()}
    commands=[]
    class FakeResponse:
        def raise_for_status(self):pass
        def json(self):return {"models":[{"name":"qwen3:8b"},{"name":"other:7b"}]}
    class Client:
        def __init__(self,*args,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url):return FakeResponse()
        async def post(self,url,json):
            commands.append((url,json))
            return FakeResponse()
    monkeypatch.setattr(main.httpx,"AsyncClient",Client)
    with TestClient(main.app) as client:
        assert client.post("/api/models/unload",json={"model":"qwen3:8b"},headers=headers).status_code==422
        assert commands==[]
        r=client.post("/api/models/unload",
          json={"model":"qwen3:8b","confirm_shared_model":True},headers=headers)
        assert r.status_code==200, r.text
        assert r.json()["status"]=="unload_requested"
        assert len(commands)==1
        assert commands[0][1]=={"model":"qwen3:8b","keep_alive":0,"stream":False}
        assert client.post("/api/models/unload",json={"model":"absent",
          "confirm_shared_model":True},headers=headers).json()["status"]=="already_unloaded"
        assert len(commands)==1
