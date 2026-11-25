"""Chat controls delegate exclusively to canonical book workflow (mock runner)."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from chat_api import install
import chat_book_control as control


class CanonicalBookHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.events=[]
        self.job=None
        self.pid="test-HDD-book"
        project=SimpleNamespace(id=self.pid)
        def get_project(pid):
            if pid!=self.pid:raise HTTPException(404,"Unknown project")
            return project

        @contextmanager
        def db():
            yield object()
        def current(_db,pid):
            if pid!=self.pid:return None
            return self.job["id"] if self.job else None
        def record(_db,id):
            if self.job is None or id!=self.job["id"]:raise HTTPException(404,"Missing")
            return dict(self.job)
        async def start(req):
            self.events.append(("start",req.project_id,req.model))
            if self.job is None:
                self.job={"id":"a"*32,"project_id":req.project_id,"status":"running",
                          "stage":"Глава 1","book":{"generated_words":13,"target_words":100,
                          "percent_words":13,"chapters_complete":0,"chapters":1}}
            return {**self.job,"reused":len(self.events)>1}
        def pause(job_id):
            self.events.append(("pause",job_id))
            self.job["status"]="interrupted"
            return {"status":"pause_requested"}
        async def resume(job_id):
            self.events.append(("resume",job_id))
            self.job["status"]="running"
            return dict(self.job)
        app=FastAPI()
        api=APIRouter(prefix="/api",dependencies=[Depends(lambda:None)])
        install(api,{"DATA_DIR":self.root,"get_project":get_project,
            "db":db,"_current_book_job_id":current,"_book_record":record,
            "start_full_book":start,"pause_full_book":pause,"resume_full_book":resume,
            "BookRequest":lambda **kw:SimpleNamespace(**kw),"DEFAULT_MODEL":"qwen3:8b"})
        app.include_router(api)
        self.client=TestClient(app)
        self.url="/api/projects/"+self.pid
        r=self.client.post(self.url+"/chats",json={"workspace":"generation"})
        self.assertEqual(r.status_code,201,r.text)
        self.thread=r.json()["id"]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def control(self,action,thread=None,confirm=True):
        return self.client.post(self.url+"/chats/"+(thread or self.thread)+"/book-control",
            json={"action":action,"confirm":confirm,"model":"qwen3:8b"})

    def send(self):
        resp=self.client.post(self.url+"/chats/"+self.thread+"/messages",
            json={"message":"Создай техническую книгу HDD, используй сохранённый план"})
        self.assertEqual(resp.status_code,201,resp.text)

    def test_explicit_start_pause_resume_uses_single_job(self):
        self.send()
        self.assertIsNone(self.client.get(self.url+"/chats/"+self.thread+"/book-job")
                          .json()["job"])
        started=self.control("start")
        self.assertEqual(started.status_code,202,started.text)
        self.assertEqual(started.json()["job"]["id"],"a"*32)
        self.assertFalse(started.json()["independent_generator"])
        self.assertEqual(self.control("pause").status_code,202)
        self.assertEqual(self.control("resume").status_code,202)
        self.assertEqual([e[0] for e in self.events],["start","pause","resume"])
        summary=self.client.get(self.url+"/chats/"+self.thread+"/book-job")
        self.assertEqual(summary.status_code,200,summary.text)
        self.assertEqual([x["action"] for x in summary.json()["events"]],
                         ["start","pause","resume"])
        self.assertEqual(summary.json()["source"],"canonical_ai_jobs")
        self.assertEqual(summary.json()["job"]["id"],"a"*32)

    def test_start_requires_saved_author_instruction_and_confirmation(self):
        self.assertEqual(self.control("start").status_code,409)
        self.send()
        self.assertEqual(self.control("start",confirm=False).status_code,422)
        self.assertEqual(self.events,[])

    def test_wrong_project_and_editorial_thread_blocked(self):
        editor=self.client.post(self.url+"/chats",
            json={"workspace":"editorial"}).json()["id"]
        self.send()
        self.assertEqual(self.control("start",thread=editor).status_code,409)
        self.assertEqual(self.client.post("/api/projects/other/chats/"+self.thread+
           "/book-control",json={"action":"start","confirm":True}).status_code,404)
        self.assertEqual(self.events,[])

    def test_cannot_pause_other_or_missing_job(self):
        self.send()
        self.assertEqual(self.control("pause").status_code,409)
        self.assertEqual(self.control("resume").status_code,409)
        self.assertEqual(self.events,[])

    def test_duplicate_start_reuses_existing_id(self):
        self.send()
        first=self.control("start")
        second=self.control("start")
        self.assertEqual(first.status_code,202)
        self.assertEqual(second.status_code,202)
        self.assertEqual(first.json()["job"]["id"],second.json()["job"]["id"])
        self.assertEqual(self.client.get(self.url+"/chats/"+self.thread+
                              "/book-job").json()["job"]["id"],"a"*32)

    def test_empty_generation_chat_control_not_allowed(self):
        editor=self.client.post(self.url+"/chats",
            json={"workspace":"editorial"}).json()["id"]
        self.assertEqual(self.client.get(self.url+"/chats/"+editor+
                                          "/book-job").status_code,409)


class UIIntegrationChecks(unittest.TestCase):
    def test_chat_generation_buttons_are_additive(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"app/static/index.html").read_text()
        js=(root/"app/static/chat_studio.js").read_text()
        for attr in ("data-chat-book-start","data-chat-book-pause","data-chat-book-resume",
                     "data-chat-book-open","data-chat-book-status","data-chat-book-progress"):
            self.assertIn(attr,html)
        for name in ("book-control","book-job","canonical","window.confirm"):
            self.assertIn(name,js)
        self.assertIn('data-tab="book"',html)
        self.assertIn('data-tab="chat-generation"',html)


if __name__=="__main__":
    unittest.main()
