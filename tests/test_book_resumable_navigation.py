"""Book navigation, idempotent start and mid-stream power-loss regression tests."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import main
from generation_core import mark_interrupted,update_job
from book_generation_core import (
    chapters_for_job,book_summary,save_chapter,save_stream_buffer,
    read_stream_buffer,clear_stream_buffer,recover_completed_sentences,check_new_prose)


def _api(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"achewiw.sqlite3")
    monkeypatch.setattr(main,"_start_book",lambda job_id:None)
    return TestClient(main.app,base_url="http://192.168.0.100:7080",
                      client=("127.0.0.1",4599))


def _new_book(client):
    project=client.post("/api/projects",json={"title":"Филинохомусы"}).json()
    project["target_length"]="500 слов"
    project["scenes"]=[
        {"title":"Первая встреча","text":"Коты услышали сигнал."},
        {"title":"Развязка","text":"Симса выясняет источник."}]
    project=client.put("/api/projects/"+project["id"],json=project).json()
    result=client.post("/api/books/generate",json={"project_id":project["id"],"model":"qwen3:8b"})
    assert result.status_code==202,result.text
    return project,result.json()


def test_get_current_and_duplicate_start_are_server_idempotent(tmp_path,monkeypatch):
    with _api(tmp_path,monkeypatch) as client:
        p,job=_new_book(client)
        url="/api/projects/"+p["id"]+"/books/current"
        got=client.get(url)
        assert got.status_code==200
        assert got.json()["job"]["id"]==job["id"]
        again=client.post("/api/books/generate",json={"project_id":p["id"],"model":"qwen3:8b"})
        assert again.status_code==202,again.text
        assert again.json()["id"]==job["id"] and again.json()["reused"]
        with main.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ai_jobs WHERE project_id=? AND mode='book'",(p["id"],)).fetchone()[0]==1
            first=chapters_for_job(conn,job["id"])[0]
            original="Один кот подошёл к окну и услышал тихий шорох. "*22
            save_chapter(conn,job["id"],first["scene_id"],original,1)
            update_job(conn,job["id"],status="error",stage="Ollama connection",
                       error="Power outage")
        assert client.get(url).json()["job"]["book"]["generated_words"]>0
        again=client.post("/api/books/generate",json={"project_id":p["id"],"model":"qwen3:8b"})
        assert again.json()["id"]==job["id"] and again.json()["reused"]
        with main.db() as conn:
            assert chapters_for_job(conn,job["id"])[0]["text"]==original
        resumed=client.post("/api/books/jobs/"+job["id"]+"/resume")
        assert resumed.status_code==202,resumed.text
        assert resumed.json()["status"]=="queued"
        with main.db() as conn:
            mark_interrupted(conn)  # simulates the next container startup
        assert client.get(url).json()["job"]["status"]=="interrupted"
        # Even if the user has edited the draft outline, RETURN never
        # validates a fresh book or overwrites this existing job.
        p["scenes"]=[]
        client.put("/api/projects/"+p["id"],json=p)
        still=client.post("/api/books/generate",json={"project_id":p["id"],"model":"qwen3:8b"})
        assert still.status_code==202 and still.json()["id"]==job["id"]
        with main.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ai_jobs WHERE mode='book'").fetchone()[0]==1


def test_stream_buffer_preserves_complete_sentences_without_duplication(tmp_path,monkeypatch):
    with _api(tmp_path,monkeypatch) as client:
        p,job=_new_book(client)
        with main.db() as conn:
            chapter=chapters_for_job(conn,job["id"])[0]
            base="Первый кот увидел на улице незнакомого человека. "*15
            save_chapter(conn,job["id"],chapter["scene_id"],base,1)
            partial=("Симса медленно открыла дверь и увидела новый след возле окна. "
                     "Кротомирус спустился вниз и начал изучать странный сигнал. ")*5
            partial+="После этого внезапно"  # must not be committed
            save_stream_buffer(conn,job["id"],chapter["scene_id"],base,partial,0)
            snap=read_stream_buffer(conn,job["id"],chapter["scene_id"],base)
            assert snap and snap["partial"]==partial
            cut=recover_completed_sentences(snap["partial"])
            assert not cut.endswith("внезапно")
            assert "сигнал" in cut and len(cut)<len(partial)
            check_new_prose(base,cut)
            new=(base.rstrip()+"\n\n"+cut).strip()
            save_chapter(conn,job["id"],chapter["scene_id"],new,2)
            clear_stream_buffer(conn,job["id"],chapter["scene_id"])
        with main.db() as conn:
            saved=chapters_for_job(conn,job["id"])[0]
            assert saved["text"]==new
            assert read_stream_buffer(conn,job["id"],chapter["scene_id"],saved["text"]) is None
            save_stream_buffer(conn,job["id"],chapter["scene_id"],saved["text"],partial,1)
            # The chapter is now different: stale buffer cannot be merged twice
            assert read_stream_buffer(conn,job["id"],chapter["scene_id"],base) is None


def test_recovery_after_power_loss_during_ollama_stream(tmp_path,monkeypatch):
    with _api(tmp_path,monkeypatch) as client:
        p,job=_new_book(client)
        with main.db() as conn:
            chapter=chapters_for_job(conn,job["id"])[0]
            base=("Кротомирус увидел окно и остановился. "
                  "Симса подошла к старой двери. ")*15
            save_chapter(conn,job["id"],chapter["scene_id"],base,1)
            partial=("Они заметили новый след на ступеньке. "
                     "Симса задала вопрос о скрытом передатчике. "
                     "Кротомирус осмотрел антенну и нашёл повреждение. ")*18
            save_stream_buffer(conn,job["id"],chapter["scene_id"],base,partial,0)
            update_job(conn,job["id"],status="interrupted",stage="power loss")
        resumed=client.post("/api/books/jobs/"+job["id"]+"/resume")
        assert resumed.status_code==202,resumed.text
        calls=[]
        text=("На следующей улице персонажи увидели светящееся окно. "
              "Затем они проверили металлическую дверь и обнаружили письмо. "
              "Симса раскрыла письмо и прочла предупреждение. ")*30
        class Response:
            def raise_for_status(self):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def aiter_lines(self):
                yield json.dumps({"response":text,"done":True})
        class Client:
            def __init__(self,*args,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def stream(self,method,url,json):
                calls.append(json["prompt"])
                return Response()
        monkeypatch.setattr(main.httpx,"AsyncClient",Client)
        asyncio.run(main.execute_book(job["id"]))
        with main.db() as conn:
            first=chapters_for_job(conn,job["id"])[0]
            assert first["text"].startswith(base.rstrip())
            assert "Симса задала вопрос о скрытом передатчике." in first["text"]
            assert first["text"].count("Симса задала вопрос о скрытом передатчике.")==18
            assert read_stream_buffer(conn,job["id"],first["scene_id"],first["text"]) is None
            assert book_summary(conn,job["id"])["generated_words"]>250
        assert calls  # second chapter still required Ollama


def test_two_tabs_can_read_same_job_without_starting_anything(tmp_path,monkeypatch):
    with _api(tmp_path,monkeypatch) as first:
        p,job=_new_book(first)
        url="/api/projects/"+p["id"]+"/books/current"
        for _ in range(4):
            assert first.get(url).json()["job"]["id"]==job["id"]
        with main.db() as conn:
            assert conn.execute("SELECT count(*) FROM ai_jobs WHERE project_id=?",(p["id"],)).fetchone()[0]==1
