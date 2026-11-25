"""Step 11 in-process API: 15-chapter book from blank project, page-fit triage."""
from contextlib import contextmanager
from pathlib import Path
from pydantic import BaseModel, Field
from unittest.mock import patch
import sqlite3
import sys
import tempfile
import unittest

from fastapi import FastAPI,APIRouter,Depends,HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import chat_outline_batch_ollama as batch
from chat_api import install
from chapter_planning import init_plan_tables, get_plan
from gpu_scheduling import init_gpu_leases
from page_fit_advisor import PageEvidence,recommend

class Scene(BaseModel):
    id: str
    title: str
    anchor: str=""
    hook: str=""
    text: str=""
    blocks: list = Field(default_factory=list)

class Project(BaseModel):
    id: str
    title: str="Synthetic HDD guide"
    work_type: str="technical"
    target_length: str=""
    duration_minutes: int=10
    reveal: str=""
    twist: str=""
    scenes: list[Scene]=Field(default_factory=list)
    updated_at: str=""

class OutlineFromZeroTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.path=self.root/"achewiw.sqlite3"
        project=Project(id="hdd-blank")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,data TEXT NOT NULL,updated_at TEXT NOT NULL)")
            db.execute("INSERT INTO projects VALUES(?,?,?)",(project.id,project.model_dump_json(),""))
            db.execute("CREATE TABLE ai_jobs(id TEXT,status TEXT)")
            init_plan_tables(db)
            init_gpu_leases(db)
        @contextmanager
        def db():
            connection=sqlite3.connect(self.path,timeout=10)
            connection.row_factory=sqlite3.Row
            try:
                with connection:yield connection
            finally:connection.close()
        self.db=db
        def get_project(pid):
            if pid!="hdd-blank":raise HTTPException(404,"No such book")
            with db() as c:
                row=c.execute("SELECT data FROM projects WHERE id=?",(pid,)).fetchone()
            return Project.model_validate_json(row["data"])
        api=APIRouter(prefix="/api",dependencies=[Depends(lambda:None)])
        install(api,{"DATA_DIR":self.root,"db":db,"get_project":get_project,
                     "Project":Project,"Scene":Scene,
                     "OLLAMA_BASE_URL":"http://127.0.0.1:11434",
                     "_current_book_job_id":lambda _db,pid:getattr(self,"busy_job",None)})
        app=FastAPI()
        app.include_router(api)
        self.client=TestClient(app)
        self.prefix="/api/projects/hdd-blank"
        self.tid=self.client.post(self.prefix+"/chats",json={"workspace":"generation"}).json()["id"]
        msg=self.client.post(self.prefix+f"/chats/{self.tid}/messages",
                             json={"message":"Напиши техническую книгу об истории HDD по этапам"})
        self.assertEqual(msg.status_code,201,msg.text)
        self.proposal_id=msg.json()["proposal_id"]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def start(self,count=15,total_words=6000):
        return self.client.post(self.prefix+f"/chats/{self.tid}/new-outline",
            json={"chat_proposal_id":self.proposal_id,
                  "count":count,"total_words":total_words})

    def batch(self,bid):
        async def fake(**kwargs):
            offset=kwargs["offset"]
            return {"chapters":[{
                "title":f"Глава {offset+i+1}: HDD",
                "anchor":"Причины появления технологии",
                "hook":"Связь со следующей главой",
                "editorial_goal":"Объяснить новый механизм без повторов",
            } for i in range(kwargs["count"])],
                "input_tokens":55,"output_tokens":80,"model":"qwen3:8b"}
        with patch.object(batch,"generate_batch",new=fake):
            return self.client.post(self.prefix+f"/chats/{self.tid}/new-outline/{bid}/batch",
                                    json={"model":"qwen3:8b"})

    def decide(self,bid,approve=True):
        return self.client.post(self.prefix+f"/chats/{self.tid}/new-outline/{bid}/decision",
                                json={"approve":approve})

    def test_fifteen_chapters_two_batches_then_atomic_canonical_accept(self):
        started=self.start()
        self.assertEqual(started.status_code,201,started.text)
        ident=started.json()["id"]
        first=self.batch(ident)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()["created"],12)
        with self.db() as db:
            before=Project.model_validate_json(db.execute("SELECT data FROM projects").fetchone()[0])
            self.assertEqual(before.scenes,[])
        self.assertEqual(self.decide(ident).status_code,409)
        recovered=self.client.get(self.prefix+f"/chats/{self.tid}/new-outline/{ident}")
        self.assertEqual(recovered.json()["created"],12)
        second=self.batch(ident)
        self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(second.json()["created"],15)
        accepted=self.decide(ident)
        self.assertEqual(accepted.status_code,200,accepted.text)
        with self.db() as db:
            book=Project.model_validate_json(db.execute("SELECT data FROM projects").fetchone()[0])
            plan=get_plan(db,book)
        self.assertEqual(len(book.scenes),15)
        self.assertEqual(len({s.id for s in book.scenes}),15)
        self.assertEqual(sum(p["target_words"] for p in plan["chapters"]),6000)
        self.assertEqual(plan["source"],"author")
        self.assertTrue(all(not s.text for s in book.scenes))
        self.assertEqual(self.decide(ident).status_code,409)

    def test_refuse_duplicate_builder_and_reject_without_change(self):
        started=self.start()
        self.assertEqual(started.status_code,201)
        self.assertEqual(self.start().status_code,409)
        self.assertEqual(self.decide(started.json()["id"],False).status_code,200)
        with self.db() as db:
            self.assertEqual(Project.model_validate_json(
                db.execute("SELECT data FROM projects").fetchone()[0]).scenes,[])
        self.assertEqual(self.start().status_code,201)

    def test_stale_source_fails_and_other_project_isolated(self):
        ident=self.start().json()["id"]
        with self.db() as db:
            book=Project.model_validate_json(db.execute("SELECT data FROM projects").fetchone()[0])
            book.title="Changed in other tab"
            db.execute("UPDATE projects SET data=? WHERE id=?",
                       (book.model_dump_json(),book.id))
        self.assertEqual(self.batch(ident).status_code,409)
        self.assertEqual(self.client.get("/api/projects/other/chats/"+self.tid+
                                         "/new-outline/"+ident).status_code,404)

    def test_running_job_or_invalid_count_rejected(self):
        self.busy_job="f"*32
        self.assertEqual(self.start().status_code,409)
        self.busy_job=None
        self.assertEqual(self.start(count=50,total_words=500).status_code,422)

    def test_no_model_call_from_plain_chat_message(self):
        with patch.object(batch,"generate_batch",side_effect=AssertionError("Unexpected model")):
            response=self.client.get(self.prefix+f"/chats/{self.tid}")
            self.assertEqual(response.status_code,200)


class PageFitTests(unittest.TestCase):
    def test_break_before_new_part_is_intentional(self):
        result=recommend(PageEvidence(223,0.18,next_page_starts_part=True))
        self.assertEqual(result["action"],"preserve_break")
        self.assertEqual(result["max_added_words"],0)

    def test_justified_gaps_are_typography_not_missing_prose(self):
        result=recommend(PageEvidence(221,0.90,justified_large_word_spaces=True))
        self.assertEqual(result["action"],"fix_layout")
        self.assertEqual(result["max_added_words"],0)

    def test_genuine_short_page_can_offer_limited_meaningful_prose(self):
        result=recommend(PageEvidence(121,0.42))
        self.assertEqual(result["action"],"offer_semantic_expansion")
        self.assertLessEqual(result["max_added_words"],180)
        self.assertTrue(result["requires_approval"])
        self.assertTrue(result["must_repaginate"])

    def test_unmeasured_text_cannot_guess_pagination(self):
        result=recommend(PageEvidence(121,0.25,measured=False))
        self.assertEqual(result["action"],"measure_first")


if __name__=="__main__":
    unittest.main()
