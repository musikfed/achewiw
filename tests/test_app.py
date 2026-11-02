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
