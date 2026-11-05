"""Real three-SQLite RAG: scoped vectors, draft provenance, digest safety and work state."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from editor_workspace import (initialize,sync_search,pending_embeddings,
    save_embeddings,semantic_snippets,checkpoint_story_state,read_story_state,
    vector_index_status)
from book_rag_pipeline import hybrid_context, compact_hybrid_context


def _prepare(tmp_path):
    initialize(tmp_path)
    main=tmp_path/"achewiw.sqlite3"
    with sqlite3.connect(main) as db:
        db.execute("""CREATE TABLE story_chunks
        (id TEXT PRIMARY KEY,project_id TEXT,branch_id TEXT,scene_index INTEGER,
         job_id TEXT,source TEXT,content TEXT,digest TEXT)""")
        for row in [
            ("a","book","main",0,"author","synopsis","Миирус спрятал ключ в старом радио.","a"),
            ("b","book","main",0,"current","draft","Симса нашла на полу следы и услышала сигнал.","b"),
            ("c","book","main",0,"foreign","draft","Совсем другой черновик о летающей машине.","c"),
            ("d","other","main",0,"author","approved","Письмо из чужой книги.","d"),
            ("e","book","main",3,"author","approved","Будущее раскрытие тайны в четвёртой главе.","e"),
            ("f","book","what-if",0,"author","approved","Другая ветка: Миирус исчез навсегда.","f")
        ]:
            db.execute("INSERT INTO story_chunks VALUES(?,?,?,?,?,?,?,?)",row)
        db.row_factory=sqlite3.Row
        sync_search(tmp_path,db)
    return main


def test_hybrid_vectors_scope_and_digest_refresh(tmp_path):
    main=_prepare(tmp_path)
    todo=pending_embeddings(tmp_path,"book","current","nomic-embed-text:latest")
    assert {row["source_id"] for row in todo}=={"a","b","e"}  # never index another branch
    vecs=[[1.,0.,0.,0.,0.,0.,0.,0.] for _ in todo]
    assert save_embeddings(tmp_path,"nomic-embed-text:latest",todo,vecs)==len(todo)
    results=semantic_snippets(tmp_path,"book","current",0,"nomic-embed-text:latest",
                              [1,0,0,0,0,0,0,0],limit=10)
    assert {item["source_id"] for item in results}=={"a","b"}
    assert all(item["scene_index"]==0 for item in results)
    assert semantic_snippets(tmp_path,"other","current",0,"nomic-embed-text:latest",
                             [1,0,0,0,0,0,0,0])==[]
    report=vector_index_status(tmp_path,"book")
    assert report["stored_vectors"]>0
    with sqlite3.connect(main) as db:
        db.row_factory=sqlite3.Row
        db.execute("UPDATE story_chunks SET content='Изменённый ключ',digest='a-v2' WHERE id='a'")
        sync_search(tmp_path,db,project_id="book")
    assert "a" in {x["source_id"] for x in
                   pending_embeddings(tmp_path,"book","current","nomic-embed-text:latest")}
    fresh=semantic_snippets(tmp_path,"book","current",0,"nomic-embed-text:latest",
                            [1,0,0,0,0,0,0,0])
    assert "a" not in {x["source_id"] for x in fresh}


def test_checkpoint_is_source_verified_not_future_canon(tmp_path):
    initialize(tmp_path)
    manuscript="Кот услышал сигнал.\n\nСимса открыла дверь.\n\nОни увидели антенну."
    checkpoint_story_state(tmp_path,"book","job","s1",0,manuscript,"Найти передатчик")
    state=read_story_state(tmp_path,"book","job","s1",manuscript)
    assert state and "Они увидели антенну" in state["ending"]
    assert state["current_goal"]=="Найти передатчик"
    assert read_story_state(tmp_path,"book","job","s1",manuscript+" Новый текст.") is None
    assert read_story_state(tmp_path,"book","another-job","s1",manuscript) is None


def test_local_cpu_embedding_request_actually_writes_via_ollama(tmp_path,monkeypatch):
    _prepare(tmp_path)
    text="Симса нашла на полу следы и услышала сигнал."
    checkpoint_story_state(tmp_path,"book","current","s0",0,text,"Услышать источник сигнала")
    requests=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):
            n=len(requests[-1]["input"])
            return {"embeddings":[[1.,0.,0.,0.,0.,0.,0.,0.] for _ in range(n)]}
    class Client:
        def __init__(self,*a,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,json):
            requests.append(json)
            assert url=="http://127.0.0.1:11434/api/embed"
            return Response()
    import book_rag_pipeline
    monkeypatch.setattr(book_rag_pipeline.httpx,"AsyncClient",Client)
    result=asyncio.run(hybrid_context(tmp_path,"http://127.0.0.1:11434",
                                      "book","current","s0",0,
                                      "сигнал Миирус",text))
    assert requests
    assert requests[0]["model"]=="nomic-embed-text:latest"
    assert requests[0]["options"]["num_gpu"]==0
    assert requests[0]["keep_alive"]==0
    assert result["vector_state"]=="active"
    assert result["new_vectors"]>0
    assert result["semantic_hits"]>0
    assert result["work_state"]["current_goal"]=="Услышать источник сигнала"
    prompt=compact_hybrid_context(result)
    assert "НЕПРИНЯТЫЙ ЧЕРНОВИК" in prompt
    assert "Найти" not in prompt  # No model-invented facts.


def test_missing_embed_endpoint_falls_back_to_search_not_error(tmp_path,monkeypatch):
    _prepare(tmp_path)
    import book_rag_pipeline
    class Client:
        def __init__(self,*a,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*a,**kw):
            raise book_rag_pipeline.httpx.ConnectError("Ollama down")
    monkeypatch.setattr(book_rag_pipeline.httpx,"AsyncClient",Client)
    result=asyncio.run(hybrid_context(tmp_path,"http://127.0.0.1:11434",
                                      "book","current","s0",0,"сигнал",""))
    assert result["vector_state"]=="fts5-only"
    assert result["fts_hits"]>0
