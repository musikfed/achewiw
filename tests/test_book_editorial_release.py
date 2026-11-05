"""Partial AI PDF, immutable editorial snapshots, version comparison, safe graph choices."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
import sqlite3
import sys

import pytest
from PIL import Image
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import main
from book_generation_core import chapters_for_job,save_chapter
from generation_core import update_job
from editorial_review import get_version


def api(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"achewiw.sqlite3")
    monkeypatch.setattr(main,"_start_book",lambda job_id:None)
    return TestClient(main.app,base_url="http://192.168.0.100:7080",
                      client=("127.0.0.1",4488))


def new_project(client):
    p=client.post("/api/projects",json={"title":"Филиниусы"}).json()
    p["author_name"]="Кот"
    p["publication_year"]=2026
    p["target_length"]="900 слов"
    p["scenes"]=[
        {"title":"Приветствие из будущего","text":"План: тайное письмо и ночь в Москве.",
         "active_objects":["Миирус","Симса"]},
        {"title":"Кульминация","text":"План: источник сигнала открывается.",
         "active_objects":["Миирус"]},
        {"title":"Финал","text":"План: возвращение героев в дом.",
         "active_objects":["Симса"]}
    ]
    return client.put("/api/projects/"+p["id"],json=p).json()


def book(client,p):
    response=client.post("/api/books/generate",json={
        "project_id":p["id"],"model":"qwen3:8b"})
    assert response.status_code==202,response.text
    return response.json()["id"]


def write(tmp_path,job_id,text,words_target=None):
    with main.db() as con:
        chapter=chapters_for_job(con,job_id)[0]
        save_chapter(con,job_id,chapter["scene_id"],text,1,complete=False)
        return chapter["scene_id"]


def test_partial_pdf_is_readable_no_old_synopsis_and_never_mutates_canon(tmp_path,monkeypatch):
    with api(tmp_path,monkeypatch) as c:
        p=new_project(c);job=book(c,p)
        empty=c.get("/api/books/jobs/"+job+"/draft.pdf")
        assert empty.status_code==409
        text=("Кротомирус увидел на пороге старинный передатчик. "
              "Симса услышала тихий сигнал и подошла к окну. ")*14
        write(tmp_path,job,text)
        pdf=c.get("/api/books/jobs/"+job+"/draft.pdf")
        assert pdf.status_code==200,pdf.text[:250] if pdf.status_code!=200 else "ok"
        assert pdf.headers["content-type"].startswith("application/pdf")
        assert pdf.headers["x-achewiw-export"]=="ai-draft-not-publication"
        assert pdf.headers["cache-control"]=="no-store"
        assert pdf.headers["content-disposition"].startswith("inline;")
        assert pdf.content.startswith(b"%PDF")
        assert len(pdf.content)>6500
        assert c.get("/api/projects/"+p["id"]).json()["scenes"][0]["text"]==p["scenes"][0]["text"]
        with main.db() as con:
            assert chapters_for_job(con,job)[0]["text"]==text
        assert c.get("/api/projects/"+p["id"]+"/manuscript.pdf").status_code==200


def test_versions_are_immutable_diffable_and_project_scoped(tmp_path,monkeypatch):
    with api(tmp_path,monkeypatch) as c:
        p=new_project(c);j=book(c,p)
        first_text="Симса спрятала письмо под старой лампой. "*20
        write(tmp_path,j,first_text)
        url="/api/projects/"+p["id"]+"/editorial/versions"
        first=c.post(url,json={"source":"draft","label":"Вариант 1"})
        assert first.status_code==201,first.text
        one=first.json()
        assert one["total_words"]>0 and not one["reused"]
        dupe=c.post(url,json={"source":"draft","label":"Новая этикетка"})
        assert dupe.json()["id"]==one["id"] and dupe.json()["reused"]
        second_text="Кротомирус забрал письмо из тайника. "*30
        write(tmp_path,j,first_text+"\n\n"+second_text)
        second=c.post(url,json={"source":"draft","label":"Вариант 2"})
        assert second.status_code==201,second.text
        two=second.json()
        assert two["id"]!=one["id"]
        older=c.get(url+"/"+one["id"]).json()
        assert older["chapters"][0]["text"]==first_text
        assert second_text not in older["chapters"][0]["text"]
        changes=c.get("/api/projects/"+p["id"]+"/editorial/compare",
                      params={"left":one["id"],"right":two["id"]})
        assert changes.status_code==200
        assert changes.json()["changed_chapters"]==1
        assert "Кротомирус" in changes.json()["changes"][0]["diff_excerpt"]
        report=c.post(url+"/"+two["id"]+"/review")
        assert report.status_code==200,report.text
        data=report.json()
        assert data["graph_nodes"]>=3 and data["chapters"]==3
        assert data["qa_result"]=="needs_review"
        proposed=c.post(url+"/"+two["id"]+"/graph-proposals")
        assert proposed.status_code==200,proposed.text
        items=proposed.json()["suggestions"]
        assert items and any(x["kind"]=="character" for x in items)
        candidate=items[0]
        choice=c.post(url+"/"+two["id"]+"/graph-proposals/"+candidate["id"],
                      json={"decision":"approved"})
        assert choice.status_code==200,choice.text
        memory=c.get("/api/projects/"+p["id"]+"/story-memory").json()
        assert candidate["id"] in {x["id"] for x in memory["nodes"]}
        repeat=c.post(url+"/"+two["id"]+"/graph-proposals/"+candidate["id"],
                      json={"decision":"approved"})
        assert repeat.status_code==409
        other=c.post("/api/projects",json={"title":"Чужая книга"}).json()
        assert c.get("/api/projects/"+other["id"]+"/editorial/versions/"+one["id"]).status_code==404
        with main.db() as db:
            assert db.execute("SELECT count(*) FROM ai_book_chapters WHERE job_id=?",(j,)).fetchone()[0]==3


def test_visual_riddle_has_later_answer_and_never_leaks_secret_into_graph(tmp_path,monkeypatch):
    with api(tmp_path,monkeypatch) as c:
        p=new_project(c)
        buf=BytesIO();Image.new("RGB",(128,128),(142,60,40)).save(buf,format="PNG")
        upload=c.post("/api/projects/"+p["id"]+"/illustrations",
                      data={"scene_id":p["scenes"][0]["id"],"caption":"Зашифрованный знак",
                            "placement":"end"},
                      files={"file":("clue.png",buf.getvalue(),"image/png")})
        assert upload.status_code==201,upload.text
        asset=upload.json()
        url="/api/projects/"+p["id"]+"/visual-puzzles"
        secret="Ответ: символ означает потерянную звезду."
        created=c.post(url,json={
          "illustration_id":asset["id"],"answer_scene_id":p["scenes"][2]["id"],
          "title":"Знак на передатчике","answer_note":secret})
        assert created.status_code==201,created.text
        clue=created.json()
        assert clue["answer_note"]==secret
        graph=c.get("/api/projects/"+p["id"]+"/story-memory").json()
        assert graph["audit"]["nodes"]>=4
        assert not any(secret in x["statement"] for x in graph["nodes"])
        assert any(e["relation"]=="foreshadows" for e in graph["edges"])
        invalid=c.post(url,json={
          "illustration_id":asset["id"],"answer_scene_id":p["scenes"][0]["id"],
          "title":"Слишком рано","answer_note":secret})
        assert invalid.status_code==422
        deleted=c.delete(url+"/"+clue["id"])
        assert deleted.status_code==204
        assert c.get(url).json()==[]
        assert c.get("/api/projects/"+p["id"]+"/illustrations")
