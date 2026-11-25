"""Step 10: shared Studio outline/brief approval and safe chat LLM proposal."""
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import sqlite3
import sys
import tempfile
import unittest

from fastapi import FastAPI, APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from chat_api import install
import chat_plan_ollama
from chapter_planning import init_plan_tables, get_plan
from gpu_scheduling import init_gpu_leases


class Scene(BaseModel):
    id: str
    title: str
    anchor: str = ""
    hook: str = ""
    text: str = "Оригинальный текст главы."


class Project(BaseModel):
    id: str
    title: str = "Гантова зебра: тест"
    premise: str = "Устройство HDD"
    atmosphere: str = ""
    audience: str = ""
    twist: str = ""
    reveal: str = ""
    anchors: str = ""
    work_type: str = "technical"
    target_length: str = "600 слов"
    duration_minutes: int = 10
    scenes: list[Scene] = Field(default_factory=list)
    updated_at: str = ""


class OutlineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.dbpath=self.root/"achewiw.sqlite3"
        self.project=Project(id="test-hdd",scenes=[
            Scene(id="s1",title="Магнитная головка"),
            Scene(id="s2",title="Дорожки и сектора"),
        ])
        with closing(sqlite3.connect(self.dbpath)) as connection, connection:
            connection.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,data TEXT NOT NULL,updated_at TEXT NOT NULL)")
            connection.execute("INSERT INTO projects VALUES(?,?,?)",
                (self.project.id,self.project.model_dump_json(),""))
            connection.execute("CREATE TABLE ai_jobs(id TEXT, status TEXT)")
            init_plan_tables(connection)
            init_gpu_leases(connection)

        @contextmanager
        def db():
            conn=sqlite3.connect(self.dbpath,timeout=10)
            conn.row_factory=sqlite3.Row
            try:
                with conn:yield conn
            finally:conn.close()
        self.db=db
        def get_project(pid):
            if pid!=self.project.id:raise HTTPException(404,"Wrong book")
            with db() as conn:
                row=conn.execute("SELECT data FROM projects WHERE id=?",(pid,)).fetchone()
            return Project.model_validate_json(row["data"])
        app=FastAPI()
        api=APIRouter(prefix="/api",dependencies=[Depends(lambda:None)])
        install(api,{"DATA_DIR":self.root,"db":db,"Project":Project,
            "get_project":get_project,
            "_current_book_job_id":lambda conn,pid:getattr(self,"blocking_job",None),
            "OLLAMA_BASE_URL":"http://127.0.0.1:11434"})
        app.include_router(api)
        self.client=TestClient(app)
        self.url="/api/projects/test-hdd"
        self.tid=self.client.post(self.url+"/chats",
            json={"workspace":"generation","mode":"autonomous"}).json()["id"]
        sent=self.client.post(self.url+f"/chats/{self.tid}/messages",
            json={"message":"Сделай начало книги точнее, а вторую главу доступнее"})
        self.assertEqual(sent.status_code,201,sent.text)
        self.proposal_id=sent.json()["proposal_id"]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def candidate(self):
        base=self.client.get(self.url+f"/chats/{self.tid}/outline")
        self.assertEqual(base.status_code,200,base.text)
        obj=json.loads(json.dumps(base.json()["baseline"]["candidate"]))
        obj["brief"]["premise"]="Объяснить работу магнитной головки HDD без лишних вступлений."
        obj["chapters"][0]["title"]="Головка и магнитная запись"
        obj["chapters"][1]["editorial_goal"]="Показать геометрию дорожек и секторов на примере"
        return obj

    def prepare(self,candidate=None):
        fake=candidate if candidate is not None else self.candidate()
        async def llm(**kwargs):
            return {"candidate":fake,"model":"qwen3:8b",
                    "input_tokens":50,"output_tokens":75}
        with patch.object(chat_plan_ollama,"propose",new=llm):
            return self.client.post(self.url+f"/chats/{self.tid}/outline",json={
                "chat_proposal_id":self.proposal_id,"model":"qwen3:8b"})

    def test_preview_is_not_a_canonical_write_and_accept_updates_both_tabs(self):
        result=self.prepare()
        self.assertEqual(result.status_code,201,result.text)
        p=result.json()
        self.assertFalse(p["applied"])
        self.assertNotEqual(p["before"],p["after"])
        with self.db() as conn:
            current=Project.model_validate_json(conn.execute(
                "SELECT data FROM projects WHERE id=?",("test-hdd",)).fetchone()[0])
            self.assertEqual(current.premise,"Устройство HDD")
        accepted=self.client.post(self.url+f"/chats/{self.tid}/outline/{p['id']}/decision",
                                  json={"approve":True})
        self.assertEqual(accepted.status_code,200,accepted.text)
        self.assertEqual(accepted.json()["status"],"accepted")
        with self.db() as conn:
            current=Project.model_validate_json(conn.execute(
                "SELECT data FROM projects WHERE id=?",("test-hdd",)).fetchone()[0])
            saved=get_plan(conn,current)
        self.assertEqual(current.scenes[0].title,"Головка и магнитная запись")
        self.assertEqual(current.scenes[0].text,"Оригинальный текст главы.")
        self.assertEqual(current.premise,p["after"]["brief"]["premise"])
        self.assertEqual(saved["chapters"][1]["editorial_goal"],
                         "Показать геометрию дорожек и секторов на примере")
        self.assertEqual(saved["target_words"],600)

    def test_rejection_preserves_canon(self):
        result=self.prepare().json()
        response=self.client.post(self.url+f"/chats/{self.tid}/outline/{result['id']}/decision",
                                  json={"approve":False})
        self.assertEqual(response.status_code,200,response.text)
        with self.db() as conn:
            row=conn.execute("SELECT data FROM projects").fetchone()
        self.assertEqual(Project.model_validate_json(row["data"]).scenes[0].title,
                         "Магнитная головка")

    def test_stale_draft_is_rejected(self):
        result=self.prepare().json()
        with self.db() as conn:
            old=Project.model_validate_json(conn.execute(
                "SELECT data FROM projects").fetchone()["data"])
            old.atmosphere="Независимая правка другого редактора"
            conn.execute("UPDATE projects SET data=? WHERE id=?",
                         (old.model_dump_json(),old.id))
        rejected=self.client.post(self.url+f"/chats/{self.tid}/outline/{result['id']}/decision",
                                  json={"approve":True})
        self.assertEqual(rejected.status_code,409,rejected.text)

    def test_reject_changed_scene_ids_and_model_word_count(self):
        bad=self.candidate()
        bad["chapters"][0]["scene_id"]="s2"
        self.assertEqual(self.prepare(bad).status_code,422)
        bad=self.candidate()
        bad["chapters"][0]["target_words"]+=10
        self.assertEqual(self.prepare(bad).status_code,422)

    def test_refuses_pending_book_job(self):
        self.blocking_job="f"*32
        r=self.prepare()
        self.assertEqual(r.status_code,409,r.text)

    def test_wrong_workspace_and_cross_book_fail(self):
        editor=self.client.post(self.url+"/chats",
            json={"workspace":"editorial"}).json()["id"]
        bad=self.client.post(self.url+f"/chats/{editor}/outline",json={
            "chat_proposal_id":self.proposal_id,"model":"qwen3:8b"})
        self.assertEqual(bad.status_code,409,bad.text)
        self.assertEqual(self.client.get("/api/projects/other/chats/"+self.tid+"/outline").status_code,404)

    def test_reloaded_outline_and_duplicate_decision(self):
        result=self.prepare()
        self.assertEqual(result.status_code,201,result.text)
        ident=result.json()["id"]
        loaded=self.client.get(self.url+f"/chats/{self.tid}/outline")
        self.assertEqual(loaded.json()["latest"]["id"],ident)
        self.assertTrue(loaded.json()["latest"]["source_unchanged"])
        self.assertEqual(self.client.post(self.url+f"/chats/{self.tid}/outline/{ident}/decision",
                            json={"approve":True}).status_code,200)
        self.assertEqual(self.client.post(self.url+f"/chats/{self.tid}/outline/{ident}/decision",
                            json={"approve":True}).status_code,409)

    def test_no_unreviewed_execution_from_chat(self):
        script=(Path(__file__).resolve().parents[1]/"app/static/chat_studio.js").read_text()
        html=(Path(__file__).resolve().parents[1]/"app/static/index.html").read_text()
        self.assertIn("achewiw:chat-outline-approved",script)
        self.assertIn("data-chat-outline-approve",html)
        self.assertIn("data-chat-outline-reject",html)
        self.assertIn("window.confirm",script)


if __name__=="__main__":
    unittest.main()
