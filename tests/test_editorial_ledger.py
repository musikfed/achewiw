"""ADR-0003: new editorial storage is additive, isolated, and not auto-approved."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from editorial_ledger import init_editorial_schema, editorial_schema_status, EDITORIAL_TABLES


def test_additive_migration_does_not_modify_preexisting_books(tmp_path):
    path=tmp_path/"old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("INSERT INTO projects VALUES('filinius','{\"text\":\"1266 words saved\"}')")
        db.execute("CREATE TABLE ai_book_chapters(job_id TEXT, text TEXT)")
        db.execute("INSERT INTO ai_book_chapters VALUES('job-old','Миирус вошёл во двор')")
        init_editorial_schema(db)
        init_editorial_schema(db)
        legacy=db.execute("SELECT text FROM ai_book_chapters").fetchone()[0]
        assert legacy=="Миирус вошёл во двор"
        assert db.execute("SELECT count(*) FROM projects").fetchone()[0]==1
        assert len(set(EDITORIAL_TABLES))==len(EDITORIAL_TABLES)
        for table in EDITORIAL_TABLES:
            assert db.execute("SELECT name FROM sqlite_master WHERE name=?",(table,)).fetchone()
        data=editorial_schema_status(db,"filinius")
        assert data["stage"]=="schema_only"
        assert all(x==0 for x in data["counts"].values())
        assert not data["auto_extraction_ready"]
        assert not data["causal_qa_ready"]
        assert data["author_approval_required"]


def test_candidate_approval_status_defaults_and_rejects_invalid_values():
    with sqlite3.connect(":memory:") as db:
        init_editorial_schema(db)
        db.execute("""INSERT INTO story_editorial_proposals
            (project_id,branch_id,proposal_id,proposal_kind,payload_json,
             source_text_sha256,model_id,canon_revision)
            VALUES('a','main','p1','fact','{}','sha','qwen','v1')""")
        assert db.execute("SELECT status FROM story_editorial_proposals").fetchone()[0]=="pending"
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("""INSERT INTO story_knowledge_ledger
                (project_id,branch_id,id,fact_node_id,knower_node_id,
                 discourse_seq,knowledge_level,canon_revision)
                VALUES('a','main','k','fact','reader',1,'magic','v1')""")
        db.execute("""INSERT INTO story_knowledge_ledger
            (project_id,branch_id,id,fact_node_id,knower_node_id,
             discourse_seq,knowledge_level,canon_revision)
            VALUES('a','main','k','fact','reader',1,'believes_false','v1')""")
        assert db.execute("SELECT knowledge_level FROM story_knowledge_ledger").fetchone()[0]=="believes_false"


def test_editorial_scope_counts_not_cross_project_or_branch():
    with sqlite3.connect(":memory:") as db:
        init_editorial_schema(db)
        rows=[
            ("book1","main","t1","Секрет котов","v1"),
            ("book1","alternate","t2","Другой вариант","v1"),
            ("book2","main","t3","Чужая история","v1"),
        ]
        db.executemany("""INSERT INTO story_plot_threads
          (project_id,branch_id,thread_id,title,canon_revision)
          VALUES(?,?,?,?,?)""",rows)
        main=editorial_schema_status(db,"book1","main")
        other=editorial_schema_status(db,"book1","alternate")
        assert main["counts"]["story_plot_threads"]==1
        assert other["counts"]["story_plot_threads"]==1
        assert db.execute("SELECT count(*) FROM story_plot_threads").fetchone()[0]==3


def test_real_api_exposes_schema_stage_without_changing_manuscript(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"app.sqlite3")
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",12345)) as api:
        p=api.post("/api/projects",json={"title":"Филиниусы"}).json()
        p["scenes"]=[{"title":"Прибытие","text":"Миирус приземлился во дворе."}]
        p=api.put("/api/projects/"+p["id"],json=p).json()
        status=api.get("/api/projects/"+p["id"]+"/editorial-storage")
        assert status.status_code==200,status.text
        assert status.json()["stage"]=="schema_only"
        assert status.json()["counts"]["story_entity_states"]==0
        assert api.get("/api/projects/"+p["id"]+"/editorial-storage?branch=other").status_code==404
        current=api.get("/api/projects/"+p["id"]).json()
        assert current["scenes"][0]["text"]=="Миирус приземлился во дворе."
