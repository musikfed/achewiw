"""SQLite story memory: causality, source provenance, project/job/branch isolation."""
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from story_memory import (init_memory,ensure_main,add_node,add_edge,nodes,edges,
    audit_graph,create_branch,index_chunk,sync_approved,ensure_draft,retrieve,
    context_text,erase_project)


def connection():
    db=sqlite3.connect(":memory:")
    db.row_factory=sqlite3.Row
    init_memory(db)
    return db


def book(project_id="book1"):
    return SimpleNamespace(id=project_id,scenes=[
      SimpleNamespace(id="s0",text="Кот Миирус увидел шифрованный радиосигнал под фонарём."),
      SimpleNamespace(id="s1",text="В Москве Миирус нашёл ключ. Ключ открыл старый сейф."),
      SimpleNamespace(id="s2",text="Тайна сейфа раскрывается в кульминации."),
    ])


def test_approved_and_draft_memory_survives_reopen_without_promoting(tmp_path):
    path=tmp_path/"story.sqlite3"
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        init_memory(db)
        sync_approved(db,book())
        index_chunk(db,"book1","main","job1","draft","s0",0,1,
                    "Миирус увидел скрытую антенну рядом с домом.")
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        init_memory(db)
        result=retrieve(db,"book1","job1",1,"антенну шифрованный ключ")
        sources={x["source"] for x in result["evidence"]}
        assert "draft" in sources and "synopsis" in sources
        other=retrieve(db,"book1","another-job",1,"антенну")
        assert all(c["source"]!="draft" for c in other["evidence"])
        assert "НЕПРИНЯТЫЙ ЧЕРНОВИК" in context_text(result)
        assert "АВТОРСКИЙ СИНОПСИС" in context_text(result)


def test_same_project_cannot_see_future_events_or_other_project():
    with connection() as db:
        sync_approved(db,book())
        sync_approved(db,book("book2"))
        index_chunk(db,"book1","main","job1","draft","s2",2,3,
                    "Миирус разгадал истинную тайну сейфа позже.")
        now=retrieve(db,"book1","job1",0,"сейфа")
        assert not any(x["scene_index"]>0 for x in now["evidence"])
        index_chunk(db,"book2","main","job2","draft","s0",0,1,
                    "Другой автор раскрыл космическую радиостанцию.")
        assert not any("радиостанцию" in x["text"] for x in
                       retrieve(db,"book1","job1",2,"радиостанцию")["evidence"])


def test_causal_graph_prevents_backwards_causality_and_cycles():
    with connection() as db:
        a=add_node(db,"book","main","event","Посадка",
                   "Корабль приземлился до исчезновения", "s0",0)
        b=add_node(db,"book","main","event","Исчезновение",
                   "Люди исчезли после посадки", "s1",1)
        c=add_node(db,"book","main","mystery","Кто похищает людей",
                   "Причина исчезновения пока неизвестна", "s1",1)
        add_edge(db,"book","main",a["id"],b["id"],"causes")
        with pytest.raises(ValueError,match="цикл"):
            add_edge(db,"book","main",b["id"],a["id"],"requires")
        with pytest.raises(ValueError,match="позже"):
            add_edge(db,"book","main",b["id"],a["id"],"foreshadows")
        before=audit_graph(db,"book")
        assert len(before["open_mysteries"])==1
        add_edge(db,"book","main",c["id"],b["id"],"reveals")
        after=audit_graph(db,"book")
        assert not after["open_mysteries"]
        assert after["edges"]==2


def test_branch_and_foreign_edges_are_isolated():
    with connection() as db:
        first=add_node(db,"one","main","character","Миирус","Чёрный кот","s0",0)
        second=add_node(db,"two","main","character","Кто-то","Из другой книги","s0",0)
        with pytest.raises(ValueError,match="проекту"):
            add_edge(db,"one","main",first["id"],second["id"],"knows")
        alternate=create_branch(db,"one","Кот не возвращается","main","s1")
        other=add_node(db,"one",alternate["id"],"event","Побег",
                       "Миирус не возвращается домой","s1",1)
        assert nodes(db,"one","main")==[first]
        assert nodes(db,"one",alternate["id"])==[other]
        assert nodes(db,"two","main")==[second]
        assert not retrieve(db,"one","job",2,"Миирус",branch=alternate["id"])["facts"]


def test_reindex_author_source_and_remove_project_memory():
    with connection() as db:
        p=book()
        sync_approved(db,p)
        original=db.execute("SELECT count(*) FROM story_chunks WHERE project_id='book1'").fetchone()[0]
        assert original==3
        sync_approved(db,p)
        assert db.execute("SELECT count(*) FROM story_chunks WHERE project_id='book1'").fetchone()[0]==3
        p.scenes[0].text="Миирус нашёл голубую антенну у дома."
        sync_approved(db,p)
        assert db.execute("SELECT count(*) FROM story_chunks WHERE project_id='book1'").fetchone()[0]==3
        result=retrieve(db,"book1","job",0,"голубую антенну")
        assert any("голубую" in c["text"] for c in result["evidence"])
        erase_project(db,"book1")
        assert not db.execute("SELECT id FROM story_chunks WHERE project_id='book1'").fetchone()
        assert not db.execute("SELECT id FROM story_branches WHERE project_id='book1'").fetchone()


def test_existing_book_draft_is_indexed_without_modifying_it():
    with connection() as db:
        text="Миирус открыл старый сейф.\n\nЗа дверью находилась карта."
        ensure_draft(db,"book1","jobA","s0",0,text)
        ensure_draft(db,"book1","jobA","s0",0,text)
        assert db.execute("SELECT count(*) FROM story_chunks WHERE job_id='jobA'").fetchone()[0]==1
        assert "сейф" in context_text(retrieve(db,"book1","jobA",0,"сейф"))


def test_api_memory_never_auto_accepts_drafts(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"memory.sqlite3")
    monkeypatch.setattr(main,"_start_book",lambda job_id:None)
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",12345)) as api:
        p=api.post("/api/projects",json={"title":"Филиниусы"}).json()
        p["target_length"]="20000 слов"
        p["scenes"]=[{"title":"Прибытие","text":"Синопсис о посадке."},
                     {"title":"Погоня","text":"Синопсис погони."}]
        p=api.put(f"/api/projects/{p['id']}",json=p).json()
        prefix=f"/api/projects/{p['id']}/story-memory"
        initial=api.get(prefix)
        assert initial.status_code==200,initial.text
        assert initial.json()["nodes"]==[]
        one=api.post(prefix+"/nodes",json={"kind":"event","title":"Посадка",
            "statement":"Посадка произошла до погони","scene_id":p["scenes"][0]["id"]})
        assert one.status_code==201,one.text
        two=api.post(prefix+"/nodes",json={"kind":"event","title":"Погоня",
            "statement":"Погоня вызвана посадкой","scene_id":p["scenes"][1]["id"]})
        assert two.status_code==201,two.text
        link=api.post(prefix+"/edges",json={"source_id":one.json()["id"],
            "target_id":two.json()["id"],"relation":"causes"})
        assert link.status_code==201,link.text
        assert api.get(prefix+"/audit").json()["edges"]==1
        job=api.post("/api/books/generate",json={"project_id":p["id"],
                                                  "model":"qwen3:8b"})
        assert job.status_code==202,job.text
        unchanged=api.get(f"/api/projects/{p['id']}").json()
        assert unchanged["scenes"][0]["text"]=="Синопсис о посадке."
        no_change=api.post(prefix+"/nodes",json={"kind":"fact","title":"Новый",
                       "statement":"Пока генерируется нельзя менять граф"})
        assert no_change.status_code==409
