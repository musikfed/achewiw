"""Offline tests for additive main + derived search + durable workspace SQLite."""
import sqlite3
import sys
from pathlib import Path
import importlib.util

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from editor_workspace import initialize,sync_search,checkpoint_work,status,search_snippets


def test_non_destructive_databases_and_incremental_fts(tmp_path):
    main=tmp_path/"achewiw.sqlite3"
    with sqlite3.connect(main) as db:
        db.execute("""CREATE TABLE story_chunks
          (id TEXT PRIMARY KEY, project_id TEXT, branch_id TEXT, scene_index INTEGER, job_id TEXT,
           source TEXT, content TEXT, digest TEXT)""")
        db.execute("INSERT INTO story_chunks VALUES(?,?,?,?,?,?,?,?)",
                   ("id1","book1","main",0,"author","approved",
                    "Миирус нашёл ключ под креслом.","hash-one"))
    paths=initialize(tmp_path)
    assert set(paths)=={"canon","search","work"}
    assert status(tmp_path)["canon"]["exists"]
    with sqlite3.connect(main) as db:
        db.row_factory=sqlite3.Row
        first=sync_search(tmp_path,db)
        second=sync_search(tmp_path,db)
        assert first["indexed"]==1
        assert second["indexed"]==0 and second["unchanged"]==1
        db.execute("UPDATE story_chunks SET content=?,digest=? WHERE id='id1'",
                   ("Миирус потерял ключ рядом с домом.","hash-two"))
        third=sync_search(tmp_path,db)
        assert third["indexed"]==1
        assert third["total"]==1
    with sqlite3.connect(tmp_path/"search.sqlite3") as search:
        assert search.execute("SELECT count(*) FROM search_fts").fetchone()[0]==1
        assert "потерял" in search.execute("SELECT text FROM search_fts").fetchone()[0]
        assert search.execute("SELECT digest FROM search_manifest").fetchone()[0]=="hash-two"
    matches=search_snippets(tmp_path,"book1","job1",0,"ключ Миирус")
    assert matches and matches[0]["source_kind"]=="approved"
    assert search_snippets(tmp_path,"other-project","job1",0,"ключ")==[]
    assert search_snippets(tmp_path,"book1","job1",-1,"ключ")==[]
    checkpoint_work(tmp_path,"book1","job1","chapter1","Или это был сон?",5)
    with sqlite3.connect(tmp_path/"work.sqlite3") as work:
        assert work.execute("SELECT words FROM work_checkpoints").fetchone()[0]==5
    with sqlite3.connect(main) as db:
        assert db.execute("SELECT count(*) FROM story_chunks").fetchone()[0]==1


def test_removed_source_cleans_only_derived_index(tmp_path):
    initialize(tmp_path)
    with sqlite3.connect(":memory:") as db:
        db.row_factory=sqlite3.Row
        db.execute("""CREATE TABLE story_chunks
          (id TEXT PRIMARY KEY, project_id TEXT, branch_id TEXT, job_id TEXT,
           source TEXT, content TEXT, digest TEXT)""")
        db.execute("INSERT INTO story_chunks VALUES('x','p','main',0,'job','draft','hello','h')")
        assert sync_search(tmp_path,db)["total"]==1
        db.execute("DELETE FROM story_chunks")
        out=sync_search(tmp_path,db)
        assert out["removed"]==1 and out["total"]==0
    assert status(tmp_path)["work"]["exists"]


def test_storage_maintenance_read_only_preview_and_safe_backup(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[1]/"scripts/story-storage-maintenance.py"
    spec=importlib.util.spec_from_file_location("achewiw_storage_cli",path)
    tool=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.setattr(tool,"ROOT",tmp_path)
    data=tmp_path/"data";data.mkdir()
    main=data/"achewiw.sqlite3"
    with sqlite3.connect(main) as db:
        db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE ai_jobs(id TEXT PRIMARY KEY,status TEXT)")
        db.execute("INSERT INTO projects VALUES('keep')")
        db.execute("CREATE TABLE story_chunks(id TEXT PRIMARY KEY,project_id TEXT,branch_id TEXT,job_id TEXT,source TEXT,content TEXT,digest TEXT)")
        db.execute("INSERT INTO story_chunks VALUES('a','keep','main',0,'author','synopsis','Кот ждёт','h')")
    tool.run("preview")
    assert not (data/"search.sqlite3").exists()
    tool.run("apply")
    assert (data/"search.sqlite3").is_file()
    assert (data/"work.sqlite3").is_file()
    backups=list((data/"backups").glob("before-story-storage-*.sqlite3"))
    assert len(backups)==1
    tool.run("apply")
    assert len(list((data/"backups").glob("before-story-storage-*.sqlite3")))==1
    with sqlite3.connect(main) as db:
        assert db.execute("SELECT count(*) FROM projects").fetchone()[0]==1
