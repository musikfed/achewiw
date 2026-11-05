"""ACHEWIW multi-genre local studio tests (fake network/SQLite; no Ollama call)."""
from __future__ import annotations
import os
import base64
from pathlib import Path

os.environ["ACHEWIW_DATA_DIR"] = "/tmp/achewiw-test"
from fastapi.testclient import TestClient
import main


def test_project_and_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.sqlite3")
    # No password on trusted private network. No BasicAuth dialog.
    with TestClient(main.app, base_url="http://192.168.0.100:7080", client=("127.0.0.1", 12345)) as client:
        landing = client.get("/")
        assert landing.status_code == 200
        assert landing.headers["x-achewiw-app"] == "studio"
        assert landing.headers["x-achewiw-auth"] == "private-vpn-no-password"
        assert "www-authenticate" not in landing.headers
        assert client.get("/api/projects").status_code == 200
        created = client.post("/api/projects", json={
            "title": "История", "work_type": "fiction"
        })
        assert created.status_code == 201
        project = created.json()
        assert project["work_type"] == "fiction"
        project["scenes"] = [
            {"title": "Начало", "active_objects": ["1", "2", "3", "4", "5", "6"],
             "text": "Один два три."}
        ]
        updated = client.put(f"/api/projects/{project['id']}", json=project)
        assert updated.status_code == 200
        stats = client.get(f"/api/projects/{project['id']}/stats").json()
        assert stats["total_words"] == 3
        assert stats["scenes"][0]["overloaded"] is True
        export = client.get(f"/api/projects/{project['id']}/manuscript.epub")
        assert export.status_code == 200
        assert export.content[:2] == b"PK"
        pdf = client.get(f"/api/projects/{project['id']}/manuscript.pdf")
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")
        docx = client.get(f"/api/projects/{project['id']}/manuscript.docx")
        assert docx.status_code == 200
        assert docx.content.startswith(b"PK")
        fb2 = client.get(f"/api/projects/{project['id']}/manuscript.fb2")
        assert fb2.status_code == 200
        assert b"<FictionBook" in fb2.content
        print_pdf = client.get(f"/api/projects/{project['id']}/manuscript.print.pdf")
        assert print_pdf.status_code == 200
        assert print_pdf.headers["x-print-status"] == "proof-not-pdfx"


def test_other_work_types_have_relevant_prompts(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "work.sqlite3")
    with TestClient(main.app, base_url="http://192.168.0.100:7080", client=("127.0.0.1", 12345)) as client:
        for work_type, expected in [
            ("technical", "технической книги"),
            ("nonfiction", "проверяемые"),
            ("speech", "таймингом"),
        ]:
            resp = client.post("/api/projects", json={
                "title": work_type, "work_type": work_type
            })
            assert resp.status_code == 201, resp.text
            project = main.Project.model_validate(resp.json())
            assert project.work_type == work_type
            prompt = main.build_prompt(project, main.AIRequest(
                project_id=project.id, mode="outline"))
            assert expected in prompt


def test_public_source_denied_and_cross_site_post_denied(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "sec.sqlite3")
    with TestClient(main.app, base_url="http://192.168.0.100:7080", client=("203.0.113.9", 23456)) as outside:
        assert outside.get("/api/projects").status_code == 403
        assert outside.get("/").status_code == 403
    with TestClient(main.app, base_url="http://192.168.0.100:7080", client=("192.168.0.42", 23456)) as trusted:
        assert trusted.post("/api/projects", json={"title": "X"},
                            headers={"Origin": "https://evil.example"}).status_code == 403
        assert trusted.post("/api/projects", json={"title": "X"}).status_code == 201
        assert trusted.get("/api/projects", headers={"Host": "malicious.test"}).status_code == 403


def test_outline_parser():
    result = main.parse_outline('{"scenes":[{"title":"Первая","active_objects":["кот"]}]}')
    assert len(result) == 1
    assert result[0].active_objects == ["кот"]
