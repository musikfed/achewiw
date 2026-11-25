"""Short-fragment local Ollama candidate: isolated HTTP mocks and real work SQLite."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import chat_ollama_editor as engine
from chat_api import install
from editorial_review import snapshot
from gpu_scheduling import init_gpu_leases, reserve_gpu

BEFORE = ("Глава 1. Магнитная запись\n\n"
          "Я решил вам рассказать, что головка читает магнитные сигналы.")
AFTER = ("Глава 1. Магнитная запись\n\n"
         "Магнитная головка считывает изменения намагниченности поверхности.")


class OllamaCandidateTests(unittest.TestCase):
    def test_bounded_prompt_and_model_metadata(self):
        requests=[]
        async def handler(request):
            requests.append(request)
            if request.url.path=="/api/tags":
                return httpx.Response(200,json={"models":[{"name":"qwen3:8b"}]})
            body=json.loads(request.content)
            self.assertFalse(body["stream"])
            self.assertFalse(body["think"])
            self.assertLessEqual(body["options"]["num_predict"],1800)
            self.assertIn("Я решил",body["prompt"])
            return httpx.Response(200,json={"response":AFTER,
                                            "prompt_eval_count":102,
                                            "eval_count":80})

        real=httpx.AsyncClient
        def fake(**kwargs):
            kwargs.pop("trust_env",None)
            return real(transport=httpx.MockTransport(handler),**kwargs)
        with patch.object(engine.httpx,"AsyncClient",side_effect=fake):
            result=asyncio.run(engine.generate_candidate(
                base_url="http://127.0.0.1:11434",model="qwen3:8b",
                original=BEFORE,instruction="Убери вводную фразу"))
        self.assertEqual(result["candidate_text"],AFTER)
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["output_tokens"],80)
        self.assertEqual(len(requests),2)

    def test_missing_model_fails_closed(self):
        real=httpx.AsyncClient
        def fake(**kwargs):
            kwargs.pop("trust_env",None)
            return real(transport=httpx.MockTransport(
                lambda req:httpx.Response(200,json={"models":[]})),**kwargs)
        with patch.object(engine.httpx,"AsyncClient",side_effect=fake):
            with self.assertRaises(engine.ModelEditorialError):
                asyncio.run(engine.generate_candidate(
                    base_url="http://127.0.0.1:11434",model="missing",
                    original=BEFORE,instruction="Исправь"))

    def test_oversize_text_never_calls_model(self):
        with self.assertRaises(engine.ModelEditorialError):
            asyncio.run(engine.generate_candidate(
                base_url="http://127.0.0.1:11434",model="qwen3:8b",
                original="А"*4501,instruction="Редактируй"))


class OllamaApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        project=SimpleNamespace(id="test-project",title="HDD",target_length="60")
        self.version=snapshot(self.root,project,[
            {"scene_id":"chapter-1","text":BEFORE,"title":"Глава","complete":True}
        ],source_kind="manuscript")
        dbpath=self.root/"achewiw.sqlite3"
        with sqlite3.connect(dbpath) as conn:
            conn.execute("CREATE TABLE ai_jobs(id TEXT,status TEXT)")
            init_gpu_leases(conn)
        @contextmanager
        def db():
            conn=sqlite3.connect(dbpath)
            conn.row_factory=sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()
        self.db=db
        def project_exists(pid):
            if pid!="test-project":
                raise HTTPException(404,"Missing project")
            return project
        app=FastAPI()
        api=APIRouter(prefix="/api",dependencies=[Depends(lambda:None)])
        install(api,{"DATA_DIR":self.root,"get_project":project_exists,
                     "db":db,"OLLAMA_BASE_URL":"http://127.0.0.1:11434"})
        app.include_router(api)
        self.client=TestClient(app)
        self.prefix="/api/projects/test-project"
        self.client.post(self.prefix+"/chat-edition",json={"version_id":self.version["id"]})
        thread=self.client.post(self.prefix+"/chats",json={"workspace":"editorial"})
        self.thread_id=thread.json()["id"]
        message=self.client.post(self.prefix+f"/chats/{self.thread_id}/messages",
            json={"message":"Убери вводную фразу","scope":"chapter",
                  "scene_id":"chapter-1","base_version_id":self.version["id"]})
        self.assertEqual(message.status_code,201,message.text)
        self.proposal_id=message.json()["proposal_id"]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def request(self):
        return self.client.post(
            self.prefix+f"/chats/{self.thread_id}/ollama-candidate",
            json={"chat_proposal_id":self.proposal_id,
                  "base_version_id":self.version["id"],
                  "scene_id":"chapter-1","model":"qwen3:8b"})

    def test_model_result_becomes_unaccepted_diff(self):
        async def model(**_):
            return {"candidate_text":AFTER,"model":"qwen3:8b",
                    "engine":"local_ollama","input_tokens":10,"output_tokens":20}
        with patch.object(engine,"generate_candidate",new=model):
            response=self.request()
        self.assertEqual(response.status_code,201,response.text)
        self.assertIn("-Я решил",response.json()["diff"])
        self.assertTrue(response.json()["approval_required"])
        self.assertEqual(self.client.get(self.prefix+"/chat-edition").json()
                         ["chapters"][0]["text"],BEFORE)
        with self.db() as conn:
            self.assertIsNone(conn.execute(
                "SELECT token FROM achewiw_gpu_lease").fetchone())

    def test_busy_gpu_prevents_model_invocation(self):
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.assertTrue(reserve_gpu(conn,"another-task","image"))
        async def forbidden(**_):
            raise AssertionError("Model must not run with busy GPU")
        with patch.object(engine,"generate_candidate",new=forbidden):
            response=self.request()
        self.assertEqual(response.status_code,409,response.text)

    def test_upstream_failure_does_not_leave_lease_or_proposal(self):
        async def broken(**_):
            raise engine.ModelEditorialError("Upstream response invalid")
        with patch.object(engine,"generate_candidate",new=broken):
            response=self.request()
        self.assertEqual(response.status_code,502,response.text)
        with self.db() as conn:
            self.assertIsNone(conn.execute(
                "SELECT token FROM achewiw_gpu_lease").fetchone())
        saved=self.client.get(self.prefix+f"/chats/{self.thread_id}").json()
        self.assertEqual(saved["proposals"][0]["status"],"proposed")

if __name__=="__main__":
    unittest.main()
