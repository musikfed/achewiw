"""Author-approved block writer, continuity clues, LLM style and safe history.

No actual Ollama calls: emulate one durable ready job and prove API never
changes manuscript until explicit approval.
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"app"))
import main
from generation_core import create_job, update_job, get_job, init_jobs
from writing_blocks_core import preflight, editorial_lint, fingerprint, words


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"test.sqlite3")
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",12345)) as client:
        yield client


def make_story(client):
    result=client.post("/api/projects",json={"title":"Синяя звезда","work_type":"fiction"})
    assert result.status_code==201, result.text
    project=result.json()
    scene_id=uuid.uuid4().hex
    block_id=uuid.uuid4().hex
    project["scenes"]=[{
        "id":scene_id,"title":"Сцена 1","text":"Он открыл дверь.",
        "active_objects":["дверь"],"anchor":"Действие должно иметь причину.",
        "blocks":[{
            "id":block_id,"description":"Найти потерянный ключ и объяснить, откуда он появился.",
            "target_words":130,"requires":["дверь"],
            "introduces":["ключ"],"caused_by":[],
            "forbidden_inventions":["оружие","телепортация"],
            "style":"Сдержанная проза от третьего лица."
        }]
    }]
    saved=client.put("/api/projects/"+project["id"],json=project)
    assert saved.status_code==200, saved.text
    return saved.json(),scene_id,block_id


def insert_ready(project_id,scene_id,block_id,original,proposal,mode="block"):
    job_id=uuid.uuid4().hex
    with main.db() as db:
        create_job(db,job_id,project_id,mode,"qwen3:8b",scene_id,
                   block_id=block_id if mode=="block" else None,
                   base_text_sha256=fingerprint(original),token_budget=1300)
        update_job(db,job_id,status="ready",stage="Готово",output=proposal,percent=100)
        assert not get_job(db,job_id)["accepted"]
    return job_id


def test_block_prompt_contains_length_causality_and_forbidden_inventions(studio):
    p,sid,bid=make_story(studio)
    parsed=main.Project.model_validate(p)
    prompt=main.build_prompt(parsed,main.AIRequest(
        project_id=p["id"], mode="block",scene_id=sid,block_id=bid))
    for term in ("130 слов","потерянный ключ","телепортация",
                 "Сцена 1","причины","автор"):
        assert term in prompt


def test_block_requires_explicit_accept_and_reversible_history(studio):
    p,sid,bid=make_story(studio)
    original="Он открыл дверь."
    text="Ключ лежал у порога. Он проверил карманы и осторожно подобрал его."
    job=insert_ready(p["id"],sid,bid,original,text)
    url="/api/ai/jobs/"+job
    assert studio.get("/api/projects/"+p["id"]).json()["scenes"][0]["text"]==original
    bad=studio.post(url+"/accept",json={"confirm":False})
    assert bad.status_code==422
    accepted=studio.post(url+"/accept",json={"confirm":True})
    assert accepted.status_code==200,accepted.text
    assert accepted.json()["words"]>0
    now=studio.get("/api/projects/"+p["id"]).json()
    scene=now["scenes"][0]
    assert text in scene["text"]
    assert scene["blocks"][0]["accepted_job_id"]==job
    assert studio.post(url+"/accept",json={"confirm":True}).status_code==409
    revisions=studio.get(f"/api/projects/{p['id']}/scenes/{sid}/revisions").json()
    assert len(revisions)==1
    target=f"/api/projects/{p['id']}/scenes/{sid}/revisions/{revisions[0]['id']}/restore"
    result=studio.post(target,json={"confirm":True})
    assert result.status_code==200,result.text
    after=studio.get("/api/projects/"+p["id"]).json()["scenes"][0]
    assert after["text"]==original
    assert after["blocks"][0]["accepted_job_id"] is None
    assert studio.get(f"/api/projects/{p['id']}/scenes/{sid}/revisions").status_code==200



def test_real_api_queues_one_targeted_fragment_with_custom_token_budget(studio,monkeypatch):
    """HTTP POST /api/ai must send the authored fragment spec, not whole-book filler."""
    p,sid,bid=make_story(studio)
    observed=[]
    async def fake_stream(job_id,payload,mode):
        observed.append((job_id,payload,mode))
        with main.db() as conn:
            update_job(conn,job_id,status="ready",stage="Готово",
                       output="Из коридора донёсся металлический звон.",percent=100)
    monkeypatch.setattr(main,"execute_generation",fake_stream)
    response=studio.post("/api/ai",json={
        "project_id":p["id"],"mode":"block","model":"qwen3:8b",
        "scene_id":sid,"block_id":bid,"instructions":"Сдержанный стиль, не вводи новые предметы."
    })
    assert response.status_code==202,response.text
    job=response.json()
    assert job["scene_id"]==sid
    assert job["block_id"]==bid
    assert job["base_text_sha256"]==fingerprint("Он открыл дверь.")
    assert job["token_budget"]==1100
    import time
    for _ in range(80):
        if observed:break
        time.sleep(0.01)
    assert observed
    _,payload,mode=observed[0]
    assert mode=="block"
    assert "металличес" not in payload["prompt"]
    assert "телепортация" in payload["prompt"]
    assert "Сдержанный стиль" in payload["prompt"]
    assert payload["options"]["num_predict"]==1100
    # The model response must remain a proposal until author explicitly accepts.
    assert studio.get("/api/projects/"+p["id"]).json()["scenes"][0]["text"]=="Он открыл дверь."


def test_stale_generation_never_overwrites_manual_text(studio):
    p,sid,bid=make_story(studio)
    job=insert_ready(p["id"],sid,bid,"Он открыл дверь.","Генерируемый блок.")
    scene=p["scenes"][0]
    scene["text"]+=" Она закрылась."
    assert studio.put("/api/projects/"+p["id"],json=p).status_code==200
    result=studio.post("/api/ai/jobs/"+job+"/accept",json={"confirm":True})
    assert result.status_code==409,result.text
    after=studio.get("/api/projects/"+p["id"]).json()
    assert after["scenes"][0]["text"]==scene["text"]
    assert after["scenes"][0]["blocks"][0]["accepted_job_id"] is None


def test_stale_browser_tab_and_forged_accepted_block_rejected(studio):
    p,sid,bid=make_story(studio)
    copy=dict(p)
    p["premise"]="Новая редакция"
    assert studio.put("/api/projects/"+p["id"],json=p).status_code==200
    assert studio.put("/api/projects/"+p["id"],json=copy).status_code==409
    current=studio.get("/api/projects/"+p["id"]).json()
    current["scenes"][0]["blocks"][0]["accepted_job_id"]=uuid.uuid4().hex
    assert studio.put("/api/projects/"+p["id"],json=current).status_code==409


def test_polish_requires_approval_and_keeps_previous_text(studio):
    p,sid,bid=make_story(studio)
    prompt=main.build_prompt(main.Project.model_validate(p),main.AIRequest(
        project_id=p["id"],mode="polish",scene_id=sid))
    assert "Без новых" not in prompt or "сохрани" in prompt.lower()
    job=insert_ready(p["id"],sid,None,"Он открыл дверь.","Дверь медленно поддалась.",mode="polish")
    assert studio.get("/api/projects/"+p["id"]).json()["scenes"][0]["text"]=="Он открыл дверь."
    response=studio.post("/api/ai/jobs/"+job+"/accept",json={"confirm":True})
    assert response.status_code==200,response.text
    assert studio.get("/api/projects/"+p["id"]).json()["scenes"][0]["text"]=="Дверь медленно поддалась."



def test_scene_proposal_uses_same_safe_revision_mechanism(studio):
    p,sid,bid=make_story(studio)
    job=insert_ready(p["id"],sid,None,"Он открыл дверь.","Дверь открылась.",mode="scene")
    assert studio.get("/api/projects/"+p["id"]).json()["scenes"][0]["text"]=="Он открыл дверь."
    result=studio.post("/api/ai/jobs/"+job+"/accept",json={"confirm":True})
    assert result.status_code==200,result.text
    assert result.json()["mode"]=="scene"
    assert studio.get("/api/projects/"+p["id"]).json()["scenes"][0]["text"]=="Дверь открылась."
    revisions=studio.get(f"/api/projects/{p['id']}/scenes/{sid}/revisions").json()
    assert len(revisions)==1


def test_manual_rewrite_marks_old_accepted_evidence_unverified(studio):
    p,sid,bid=make_story(studio)
    text="Ключ лежал под дверью."
    job=insert_ready(p["id"],sid,bid,"Он открыл дверь.",text)
    assert studio.post("/api/ai/jobs/"+job+"/accept",json={"confirm":True}).status_code==200
    saved=studio.get("/api/projects/"+p["id"]).json()
    assert saved["scenes"][0]["blocks"][0]["evidence_stale"] is False
    saved["scenes"][0]["text"]="Автор переписал сцену без ключа."
    reply=studio.put("/api/projects/"+p["id"],json=saved)
    assert reply.status_code==200,reply.text
    assert reply.json()["scenes"][0]["blocks"][0]["evidence_stale"] is True
    report=studio.get("/api/projects/"+p["id"]+"/continuity")
    assert report.status_code==200
    assert any(row["code"]=="stale_evidence" for row in report.json()["causality"]["issues"])


def test_deterministic_causality_and_cliches_not_claimed_as_ai_detector(studio):
    p,sid,bid=make_story(studio)
    parsed=main.Project.model_validate(p)
    parsed.scenes[0].blocks[0].accepted_job_id="a"*32
    response=preflight(parsed)
    assert any(i["code"]=="unprepared_item" for i in response["issues"])
    # Literal matching detects phrases, never claims to identify machine authorship.
    report=editorial_lint("В этот момент всё изменилось.\n\nВ этот момент всё изменилось.")
    assert report["cliches"]
    assert report["repeated_paragraphs"]
    assert "не детектор" in report["disclaimer"]


def test_backward_compatible_sqlite_job_schema():
    conn=sqlite3.connect(":memory:")
    conn.row_factory=sqlite3.Row
    conn.execute("""CREATE TABLE ai_jobs (
      id TEXT PRIMARY KEY,project_id TEXT NOT NULL,mode TEXT NOT NULL,
      model TEXT NOT NULL,scene_id TEXT,status TEXT NOT NULL,stage TEXT NOT NULL,
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL,finished_at TEXT,
      output TEXT NOT NULL DEFAULT '',estimated_tokens INTEGER NOT NULL DEFAULT 0,
      token_budget INTEGER NOT NULL DEFAULT 2400,percent INTEGER,eta_seconds INTEGER,
      parse_error TEXT,suggested_scenes TEXT,error TEXT)""")
    conn.execute("INSERT INTO ai_jobs(id,project_id,mode,model,status,stage,created_at,updated_at) "
                 "VALUES('one','p','scene','qwen3:8b','ready','done','t','t')")
    init_jobs(conn)
    init_jobs(conn)
    assert conn.execute("SELECT id FROM ai_jobs WHERE id='one'").fetchone()["id"]=="one"
    assert {"block_id","base_text_sha256"} <= {
        r[1] for r in conn.execute("PRAGMA table_info(ai_jobs)")
    }
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='author_text_revisions'").fetchone()
    conn.close()
