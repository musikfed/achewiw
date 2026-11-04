"""Book-length generation planning and durable checkpoints (no Ollama needed)."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from book_generation_core import (
    target_words, distribute_words, count_words, chunk_size, max_attempts,
    init_book_tables, create_book_chapters, chapters_for_job, save_chapter,
    book_summary,
)


@pytest.mark.parametrize("raw,expected", [
    ("20000", 20000),
    ("20000 слов", 20000),
    ("20 000 слов", 20000),
    ("150 минут", 22500),
    ("150 min", 22500),
    ("150 минут / 20000 слов", 20000),
])
def test_book_target(raw, expected):
    assert target_words(raw) == expected


def test_speech_fallback_and_invalid_volume():
    assert target_words("", speech_minutes=150) == 22500
    for value in ("", "очень длинная", "100 слов", "100000 слов"):
        with pytest.raises(ValueError):
            target_words(value)


def test_allocate_20000_words_across_six_chapters():
    allocations = distribute_words(20000, [str(x) for x in range(6)])
    assert sum(allocations) == 20000
    assert max(allocations) - min(allocations) <= 1
    assert chunk_size(3333) == 800
    assert max_attempts(3333) >= 10


def test_large_chapter_requires_more_outline_cards():
    with pytest.raises(ValueError):
        distribute_words(20000, ["only"])


def test_book_checkpoint_and_summary_survive_connection_restart(tmp_path):
    path = tmp_path / "book.sqlite3"
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        init_book_tables(db)
        create_book_chapters(db, "job1", [
            {"id": "scene1", "title": "Начало", "target_words": 1500,
             "original_sha256": "hash1"},
            {"id": "scene2", "title": "Финал", "target_words": 1500,
             "original_sha256": "hash2"},
        ])
        save_chapter(db, "job1", "scene1", "Кот увидел корабль.", 1)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        chapters = chapters_for_job(db, "job1")
        assert chapters[0]["word_count"] == count_words("Кот увидел корабль.")
        assert chapters[1]["word_count"] == 0
        assert book_summary(db, "job1")["target_words"] == 3000
        assert book_summary(db, "job1")["shortfall"] > 0
        save_chapter(db, "job1", "scene1", "Кот увидел корабль. Он испугался.", 2)
        assert chapters_for_job(db, "job1")[0]["attempts"] == 2


def test_does_not_overwrite_accepted_chapter():
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        init_book_tables(db)
        create_book_chapters(db, "job", [{
            "id": "s", "title": "A", "target_words": 500, "original_sha256": "h"
        }])
        db.execute("UPDATE ai_book_chapters SET accepted_at='2026-11-05' WHERE job_id='job'")
        with pytest.raises(ValueError):
            save_chapter(db, "job", "s", "Попытка перезаписи", 1)


def test_book_api_does_not_publish_until_author_accepts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from generation_core import update_job

    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "book-api.sqlite3")
    monkeypatch.setattr(main, "_start_book", lambda job_id: None)
    with TestClient(main.app, base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1", 12345)) as client:
        project = client.post("/api/projects", json={"title": "Филиниусы"}).json()
        project["target_length"] = "500 слов"
        project["scenes"] = [
            {"title": "Прибытие", "text": "Авторская сцена."},
            {"title": "Развязка", "text": ""},
        ]
        project = client.put("/api/projects/" + project["id"], json=project).json()
        response = client.post("/api/books/generate", json={
            "project_id": project["id"], "model": "qwen3:8b"
        })
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["book"]["target_words"] == 500
        assert job["book"]["chapters"] == 2
        assert len(job["book_chapters"]) == 2
        assert client.get("/api/projects/" + project["id"]).json()["scenes"][0]["text"] == "Авторская сцена."
        assert client.post("/api/books/jobs/" + job["id"] + "/accept",
                           json={"confirm": True}).status_code == 409
        with main.db() as db:
            for chapter in chapters_for_job(db, job["id"]):
                text = "Кот увидел необычный свет. " * 65
                save_chapter(db, job["id"], chapter["scene_id"], text, 1, complete=True)
            update_job(db, job["id"], status="ready", stage="Готово",
                       output="Готово", percent=100)
        result = client.post("/api/books/jobs/" + job["id"] + "/accept",
                             json={"confirm": True})
        assert result.status_code == 200, result.text
        manuscript = client.get("/api/projects/" + project["id"]).json()
        assert count_words(manuscript["scenes"][0]["text"]) > 250
        assert client.get("/api/books/jobs/" + job["id"]).json()["book"]["chapters_accepted"] == 2
        assert client.post("/api/books/jobs/" + job["id"] + "/accept",
                           json={"confirm": True}).status_code == 409
        revisions = client.get(f"/api/projects/{project['id']}/scenes/{project['scenes'][0]['id']}/revisions")
        assert revisions.status_code == 200
        assert revisions.json()


def test_book_acceptance_rejects_stale_author_text(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from generation_core import update_job
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "stale.sqlite3")
    monkeypatch.setattr(main, "_start_book", lambda job_id: None)
    with TestClient(main.app, base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1", 12345)) as client:
        project = client.post("/api/projects", json={"title": "Книга"}).json()
        project["target_length"] = "500 слов"
        project["scenes"] = [{"title": "Первая"}, {"title": "Вторая"}]
        project = client.put("/api/projects/" + project["id"], json=project).json()
        job = client.post("/api/books/generate", json={
            "project_id": project["id"], "model": "qwen3:8b"
        }).json()
        with main.db() as db:
            for chapter in chapters_for_job(db, job["id"]):
                save_chapter(db, job["id"], chapter["scene_id"], "Длинный текст " * 250,
                             1, complete=True)
            update_job(db, job["id"], status="ready", stage="Готово", output="Готово")
        project["scenes"][0]["text"] = "Свежая правка автора"
        client.put("/api/projects/" + project["id"], json=project)
        assert client.post("/api/books/jobs/" + job["id"] + "/accept",
                           json={"confirm": True}).status_code == 409
        assert client.get("/api/projects/" + project["id"]).json()["scenes"][0]["text"] == "Свежая правка автора"


def test_live_book_draft_is_not_the_old_synopsis(tmp_path, monkeypatch):
    """The Book tab can read the full chapter drafts without publishing them."""
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "book-visible.sqlite3")
    monkeypatch.setattr(main, "_start_book", lambda job_id: None)
    with TestClient(main.app, base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1", 12345)) as client:
        project = client.post("/api/projects", json={"title": "Филиниусы"}).json()
        project["target_length"] = "20000"
        project["scenes"] = [
            {"title": "Прибытие", "text": "Краткий пересказ первой главы."},
            {"title": "Погоня", "text": "Краткий пересказ второй главы."},
        ]
        project = client.put("/api/projects/" + project["id"], json=project).json()
        job_resp = client.post("/api/books/generate", json={
            "project_id": project["id"], "model": "qwen3:8b"
        })
        assert job_resp.status_code == 202, job_resp.text
        job = job_resp.json()
        assert job["book"]["target_words"] == 20000
        first = job["book_chapters"][0]
        with main.db() as connection:
            draft_text = ("Кот увидел огни приближающегося корабля. " * 110).strip()
            save_chapter(connection, job["id"], first["scene_id"], draft_text, 1)
        preview = client.get("/api/books/jobs/" + job["id"] + "/draft")
        assert preview.status_code == 200, preview.text
        assert preview.headers["cache-control"] == "no-store"
        visible = preview.json()
        assert visible["book"]["generated_words"] == count_words(draft_text)
        assert visible["book"]["target_words"] == 20000
        assert visible["chapters"][0]["text"] == draft_text
        assert visible["chapters"][1]["text"] == ""
        plan = client.get(f"/api/projects/{project['id']}/book-plan").json()
        assert visible["chapters"][0]["target_words"] == plan["chapters"][0]["target_words"]
        assert sum(c["target_words"] for c in visible["chapters"]) == 20000
        # No automatic replacement of approved book or export.
        original = client.get(f"/api/projects/{project['id']}/manuscript").json()
        assert original["total_words"] < 30
        assert original["chapters"][0]["text"] == "Краткий пересказ первой главы."
        assert client.post("/api/books/jobs/" + job["id"] + "/accept",
                           json={"confirm": True}).status_code == 409
        missing = client.get("/api/books/jobs/" + "a" * 32 + "/draft")
        assert missing.status_code == 404
