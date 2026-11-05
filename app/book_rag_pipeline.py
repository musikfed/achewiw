"""Local hybrid retrieval for a book job: canon graph + FTS5 + optional CPU embeddings.

No extra database server. Canon remains the sole truth. Work stores resumable
draft state; search is disposable. Ollama /api/embed is local and CPU-only,
and unloads ONLY the embedding model, never the author's writing model.
"""
from __future__ import annotations

import asyncio
import httpx
import json
import sqlite3
from pathlib import Path

from editor_workspace import (pending_embeddings, save_embeddings, semantic_snippets,
    search_snippets, read_story_state, vector_index_status)

EMBED_MODEL = "nomic-embed-text:latest"


async def hybrid_context(data_dir: Path, ollama_base_url: str, project_id: str,
                         job_id: str, scene_id: str, scene_index: int, query: str,
                         chapter_text: str, *, model: str = EMBED_MODEL) -> dict:
    """Return evidence bounded for the prompt; missing embeddings never block a book."""
    lexical=search_snippets(data_dir,project_id,job_id,scene_index,query,limit=6)
    semantic=[]
    vector_state="fts5-only"
    stored=0
    # No silent model pull. Ollama will return 404 when the embedding model
    # is absent; deterministic text RAG and graph still remain usable.
    try:
        backlog=pending_embeddings(data_dir,project_id,job_id,model,limit=12)
        inputs=[row["text"][:1500] for row in backlog]+[query[:1500]]
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=8,read=100,write=20,pool=10)) as client:
            response=await client.post(ollama_base_url+"/api/embed",json={
                "model":model,"input":inputs,"truncate":False,"keep_alive":0,
                "options":{"num_gpu":0,"num_ctx":2048}
            })
            response.raise_for_status()
            result=response.json()["embeddings"]
        if len(result)!=len(inputs):
            raise ValueError("Ollama embed: mismatch in vectors count")
        if backlog:
            stored=save_embeddings(data_dir,model,backlog,result[:-1])
        semantic=semantic_snippets(data_dir,project_id,job_id,scene_index,
                                   model,result[-1],limit=5)
        vector_state="active"
    except asyncio.CancelledError:
        raise
    except (httpx.HTTPError,ValueError,KeyError,TypeError,AttributeError,sqlite3.Error,OSError):
        vector_state="fts5-only"
    # Preserve source type and confidence. Never treat an unapproved draft as canon.
    sources={}
    for origin,rows in (("semantic",semantic),("fts5",lexical)):
        for row in rows:
            sid=row["source_id"]
            if sid not in sources:
                sources[sid]={
                    "source_id":sid,"kind":row["source_kind"],
                    "chapter":row["scene_index"]+1,
                    "source":origin,"draft":row["revision_id"]!="author",
                    "excerpt":row["excerpt"][:700],
                    "similarity":row.get("similarity")
                }
            if len(sources)>=9:break
    story=read_story_state(data_dir,project_id,job_id,scene_id,chapter_text)
    return {
        "vector_state":vector_state,"new_vectors":stored,
        "semantic_hits":len(semantic),"fts_hits":len(lexical),
        "evidence":list(sources.values())[:9],
        "work_state":{
            "current_goal":story["current_goal"],
            "last_written_paragraphs":story["ending"][-1300:]
        } if story else None,
    }


def compact_hybrid_context(result: dict, max_chars: int = 3400) -> str:
    """Prompt-safe context assembled from real indexed sources, no fake canon."""
    chunks=[{
        "chapter":p["chapter"],"type":("НЕПРИНЯТЫЙ ЧЕРНОВИК" if p["draft"] else
                                       "АВТОРСКИЙ ТЕКСТ/ПЛАН"),
        "retrieved_by":p["source"],"quote":p["excerpt"][:440]
    } for p in result.get("evidence",[])[:7]]
    package={"indexed_passages":chunks,"resume_state_from_work":result.get("work_state")}
    return json.dumps(package,ensure_ascii=False)[:max_chars]
