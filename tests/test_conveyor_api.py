"""Conveyor API boundary: no models, GPU, network or new book generation."""
from __future__ import annotations
import uuid
from fastapi.testclient import TestClient
import main
import conveyor_core as cv


def test_conveyor_routes_and_author_gate_exist(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"conveyor-api.sqlite3")
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",12345)) as api:
        project=api.post("/api/projects",json={"title":"Глава для QA","work_type":"fiction"})
        assert project.status_code==201,project.text
        project_id=project.json()["id"]
        assert api.get(f"/api/projects/{project_id}/conveyor").json()==[]
        with main.db() as db:
            created=cv.create(db,job_id=uuid.uuid4().hex,project_id=project_id,
               book_job_id=uuid.uuid4().hex,writer_model="qwen3:8b",
               critic_model="hermes",image_mode="none",image_style="",
               allow_shared_unload=False)
        detail=api.get("/api/conveyor/jobs/"+created["id"])
        assert detail.status_code==200,detail.text
        assert detail.json()["state"]=="queued"
        assert detail.json()["steps"][0]["role"]=="writer"
        assert len(api.get(f"/api/projects/{project_id}/conveyor").json())==1
        premature=api.post("/api/conveyor/jobs/"+created["id"]+"/approve",
                           json={"confirm":True,"selected_edits":{}})
        assert premature.status_code==409
        assert api.get("/api/conveyor/jobs/"+created["id"]+
                       "/files/approved-a5.pdf").status_code==409
        cancelled=api.post("/api/conveyor/jobs/"+created["id"]+"/cancel")
        assert cancelled.status_code==200
        assert cancelled.json()["state"]=="cancelled"
