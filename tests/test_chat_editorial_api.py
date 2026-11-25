"""In-process API acceptance on synthetic HDD edition; no port, GPU or Ollama."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from chat_api import install
from editorial_review import snapshot, get_version

ORIGINAL="Глава 1. Магнитная запись\n\nЯ решил рассказать о принципе работы HDD."
CANDIDATE="Глава 1. Магнитная запись\n\nМагнитная головка считывает сигнал с поверхности диска."


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.project=SimpleNamespace(id="test-project",title="Проверка HDD",target_length="50")
        self.edition=snapshot(self.root,self.project,[
            {"scene_id":"chapter-1","text":ORIGINAL,"title":"Магнитная запись","complete":True},
            {"scene_id":"chapter-2","text":"Вторая глава не меняется.","complete":True},
        ],source_kind="manuscript")

        def get_project(project_id):
            if project_id!="test-project":
                raise HTTPException(404,"Not found")
            return self.project

        def guard():
            return None

        app=FastAPI()
        api=APIRouter(prefix="/api",dependencies=[Depends(guard)])
        install(api,{"DATA_DIR":self.root,"get_project":get_project})
        app.include_router(api)
        self.client=TestClient(app)
        self.url="/api/projects/test-project"

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def setup_thread(self,workspace="editorial"):
        r=self.client.post(self.url+"/chats",json={"workspace":workspace})
        self.assertEqual(r.status_code,201,r.text)
        return r.json()["id"]

    def attach(self):
        r=self.client.post(self.url+"/chat-edition",
            json={"version_id":self.edition["id"]})
        self.assertEqual(r.status_code,201,r.text)

    def send(self,thread_id):
        return self.client.post(self.url+f"/chats/{thread_id}/messages",
            json={"message":"Убери вводную фразу","scope":"chapter",
                  "scene_id":"chapter-1","base_version_id":self.edition["id"]})

    def test_unattached_project_returns_not_found_before_first_edit(self):
        result=self.client.get(self.url+"/chat-edition")
        self.assertEqual(result.status_code,404,result.text)
        self.attach()
        self.assertEqual(self.client.get(self.url+"/chat-edition").json()["version_id"],
                         self.edition["id"])

    def test_full_api_proposal_decision_and_read(self):
        self.attach()
        tid=self.setup_thread()
        msg=self.send(tid)
        self.assertEqual(msg.status_code,201,msg.text)
        self.assertEqual(msg.json()["source_digest"],__import__("hashlib").sha256(
            ORIGINAL.encode("utf8")).hexdigest())
        proposal_id=msg.json()["proposal_id"]
        cand=self.client.post(self.url+f"/chats/{tid}/candidates",json={
            "chat_proposal_id":proposal_id,"base_version_id":self.edition["id"],
            "scene_id":"chapter-1","candidate_text":CANDIDATE})
        self.assertEqual(cand.status_code,201,cand.text)
        self.assertIn("-Я решил",cand.json()["diff"])
        self.assertEqual(self.client.get(self.url+"/chat-edition").json()["version_id"],
                         self.edition["id"])
        decision=self.client.post(self.url+f"/chats/{tid}/candidates/{proposal_id}/decision",
            json={"approve":True})
        self.assertEqual(decision.status_code,200,decision.text)
        new_version=decision.json()["new_immutable_version"]
        self.assertNotEqual(new_version,self.edition["id"])
        head=self.client.get(self.url+"/chat-edition").json()
        self.assertEqual(head["chapters"][0]["text"],CANDIDATE)
        self.assertEqual(head["chapters"][1]["text"],"Вторая глава не меняется.")
        self.assertEqual(get_version(self.root,self.project.id,self.edition["id"])
                         ["chapters"][0]["text"],ORIGINAL)
        thread=self.client.get(self.url+f"/chats/{tid}").json()
        self.assertEqual(thread["proposals"][0]["status"],"accepted")

    def test_draft_only_api_never_automatically_applies(self):
        self.attach()
        tid=self.setup_thread("generation")
        msg=self.client.post(self.url+f"/chats/{tid}/messages",
            json={"message":"Напиши главу HDD"})
        self.assertEqual(msg.status_code,201,msg.text)
        self.assertFalse(msg.json()["applied"])
        self.assertEqual(self.client.get(self.url+"/chat-edition").json()
                         ["chapters"][0]["text"],ORIGINAL)

    def test_invalid_or_foreign_project_rejected(self):
        self.assertEqual(self.client.post("/api/projects/other/chats",
            json={"workspace":"editorial"}).status_code,404)
        tid=self.setup_thread()
        self.assertEqual(self.client.get("/api/projects/other/chats/"+tid).status_code,404)
        self.assertEqual(self.client.post(self.url+f"/chats/{tid}/messages",
            json={"message":"change","scope":"chapter",
                  "scene_id":"chapter-1"}).status_code,422)

    def test_stale_request_rejected_and_mode_keeps_history(self):
        self.attach()
        tid=self.setup_thread()
        self.assertEqual(self.send(tid).status_code,201)
        res=self.client.put(self.url+f"/chats/{tid}/mode",json={"mode":"autonomous"})
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(len(self.client.get(self.url+f"/chats/{tid}")
                             .json()["messages"]),1)
        other=self.client.post(self.url+"/chat-edition",
            json={"version_id":self.edition["id"]})
        self.assertEqual(other.status_code,201)

    def test_candidate_must_be_bound_to_exact_chapter(self):
        self.attach()
        tid=self.setup_thread()
        msg=self.send(tid).json()
        bad=self.client.post(self.url+f"/chats/{tid}/candidates",
            json={"chat_proposal_id":msg["proposal_id"],
                  "base_version_id":self.edition["id"],
                  "scene_id":"chapter-2","candidate_text":"Попытка заменить чужую главу"})
        self.assertEqual(bad.status_code,409,bad.text)
        self.assertEqual(self.client.get(self.url+"/chat-edition").json()
                         ["chapters"][1]["text"],"Вторая глава не меняется.")


if __name__=="__main__":
    unittest.main()
