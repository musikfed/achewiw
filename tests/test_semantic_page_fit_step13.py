"""Step 13: grounded paragraph, state isolation, fail-closed page-fit API."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import asyncio
import sqlite3
import sys
import tempfile
import unittest

import httpx
from fastapi import FastAPI,APIRouter,Depends,HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import semantic_page_fit as fit
import semantic_page_fit_api as api
from gpu_scheduling import init_gpu_leases,reserve_gpu

ORIGINAL="Головка считывает магнитные изменения поверхности. Контроллер управляет позиционированием."
ADDITION=("После позиционирования головки контроллер ожидает стабилизации сигнала, "
          "поэтому последовательность команд определяется не только механикой, "
          "но и порядком обращения к выбранному сектору накопителя.")

class FitServiceTests(unittest.TestCase):
    def setUp(self):
        self.packet={"evidence":[{"source":"approved","scene_id":"scene-1",
                         "text":"Контроллер управляет позиционированием магнитной головки."},
                         {"source":"draft","scene_id":"scene-2","text":"Непринятый черновик"}],
                     "facts":[{"id":"n1","status":"approved","statement":"Считывание требует позиционирования."},
                              {"id":"n2","status":"outline","statement":"План без доказательств"}]}
        self.evidence=fit.collect_evidence(self.packet,"scene-1")

    def test_excludes_draft_and_unapproved_outline(self):
        self.assertEqual(len(self.evidence),2)
        self.assertNotIn("Непринятый",str(self.evidence))
        self.assertNotIn("План без",str(self.evidence))

    def test_no_evidence_or_wrong_citation_is_rejected(self):
        with self.assertRaises(fit.FitProposalConflict):
            fit.validate_candidate(ORIGINAL,{"paragraph":ADDITION,
                                               "evidence_ids":["unknown"]},self.evidence)
        with self.assertRaises(fit.FitProposalConflict):
            asyncio.run(fit.propose_with_ollama(base_url="http://127.0.0.1:11434",
              model="qwen3:8b",original=ORIGINAL,instruction="",evidence=[]))

    def test_repeated_filler_rejected(self):
        duplicated=("Головка считывает магнитные изменения поверхности. "
                    "Контроллер управляет позиционированием. ")*6
        with self.assertRaises(fit.FitProposalConflict):
            fit.validate_candidate(duplicated,{"paragraph":duplicated,
                "evidence_ids":[self.evidence[0]["id"]]},self.evidence)

    def test_real_mock_transport_requires_installed_model(self):
        async def handler(request):
            if request.url.path=="/api/tags":
                return httpx.Response(200,json={"models":[{"name":"qwen3:8b"}]})
            return httpx.Response(200,json={"response":__import__("json").dumps({
                 "paragraph":ADDITION,"evidence_ids":["excerpt-1"]},ensure_ascii=False)})
        real=httpx.AsyncClient
        def factory(**kwargs):
            kwargs.pop("trust_env",None)
            return real(transport=httpx.MockTransport(handler),**kwargs)
        with patch.object(fit.httpx,"AsyncClient",side_effect=factory):
            result=asyncio.run(fit.propose_with_ollama(base_url="http://127.0.0.1:11434",
                 model="qwen3:8b",original=ORIGINAL,
                 instruction="Уточнить работу контроллера",evidence=self.evidence))
        self.assertEqual(result["evidence_ids"],["excerpt-1"])


class FitApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"canon.sqlite3"
        self.current=SimpleNamespace(
            id="test-hdd",scenes=[SimpleNamespace(id="scene-1",title="Головка HDD",text=ORIGINAL)])
        self.current.model_dump_json=lambda:'{"id":"test-hdd","scene":"'+self.current.scenes[0].text+'"}'
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE ai_jobs(id TEXT,status TEXT)")
            conn.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            conn.execute("INSERT INTO projects VALUES(?,?)",
                         ("test-hdd",self.current.model_dump_json()))
            init_gpu_leases(conn)
        @contextmanager
        def db():
            conn=sqlite3.connect(self.path)
            conn.row_factory=sqlite3.Row
            try:
                with conn:yield conn
            finally:conn.close()
        self.db=db
        def project(pid):
            if pid!="test-hdd":raise HTTPException(404,"No project")
            return self.current
        router=APIRouter(prefix="/api",dependencies=[Depends(lambda:None)])
        api.install(router,{"get_project":project,"db":db,"OLLAMA_BASE_URL":"http://127.0.0.1:11434",
                    "illustrations_for_book":lambda _:[]})
        app=FastAPI();app.include_router(router)
        self.client=TestClient(app)
        self.url="/api/projects/test-hdd/print-proof/grounded-fit"
        self.mockpdf=patch.object(api,"manuscript_pdf",return_value=b"%PDF-fixture")
        self.mockinspection=patch.object(api,"inspect_pdf",return_value={
            "page_count":2,"pages":[{"occupied_ratio":0.23,"wide_spaces_detected":False},
                                    {"occupied_ratio":0.25,"wide_spaces_detected":False}],
            "headings":[{"title":"Головка HDD","actual_pdf_page":1}]})
        self.mockrag=patch.object(api,"story_retrieve",return_value={
            "evidence":[{"source":"approved","scene_id":"scene-1",
                         "text":"Контроллер управляет позиционированием магнитной головки."}],
            "facts":[]})
        self.mockpdf.start();self.mockinspection.start();self.mockrag.start()

    def tearDown(self):
        patch.stopall();self.client.close();self.tmp.cleanup()

    def request(self):
        return self.client.post(self.url,json={"scene_id":"scene-1","page_number":2,
                              "model":"qwen3:8b","instruction":"Пояснить работу контроллера"})

    def test_proposal_remains_separate_and_can_be_rejected(self):
        async def model(**kwargs):
            return {"paragraph":ADDITION,"evidence_ids":["excerpt-1"]}
        with patch.object(api,"propose_with_ollama",new=model):
            result=self.request()
        self.assertEqual(result.status_code,201,result.text)
        value=result.json()
        self.assertFalse(value["approved"])
        self.assertFalse(value["manuscript_mutated"])
        self.assertEqual(self.current.scenes[0].text,ORIGINAL)
        recorded=self.client.get(self.url+"/"+value["id"])
        self.assertEqual(recorded.status_code,200)
        self.assertFalse(recorded.json()["stale"])
        rejected=self.client.post(self.url+"/"+value["id"]+"/reject")
        self.assertEqual(rejected.status_code,200)
        self.assertEqual(self.current.scenes[0].text,ORIGINAL)
        self.assertEqual(self.client.post(self.url+"/"+value["id"]+"/reject").status_code,409)

    def test_stale_proposal_flag(self):
        async def model(**kwargs):
            return {"paragraph":ADDITION,"evidence_ids":["excerpt-1"]}
        with patch.object(api,"propose_with_ollama",new=model):
            ident=self.request().json()["id"]
        self.current.scenes[0].text+=" Автор изменил главу."
        self.assertTrue(self.client.get(self.url+"/"+ident).json()["stale"])

    def test_immutable_storage_rechecks_canonical_version(self):
        async def model(**kwargs):
            # A concurrent writer updates the real SQLite project after the
            # initial API read and before the generated proposal is saved.
            with self.db() as conn:
                conn.execute("UPDATE projects SET data=? WHERE id=?",
                             ('{"id":"test-hdd","scene":"updated by other request"}',"test-hdd"))
            return {"paragraph":ADDITION,"evidence_ids":["excerpt-1"]}
        with patch.object(api,"propose_with_ollama",new=model):
            rejected=self.request()
        self.assertEqual(rejected.status_code,409,rejected.text)
        self.assertEqual(self.current.scenes[0].text,ORIGINAL)

    def test_busy_gpu_prevents_model_call(self):
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.assertTrue(reserve_gpu(conn,"busy","image"))
        async def model(**kwargs):
            raise AssertionError("Should never run with busy GPU")
        with patch.object(api,"propose_with_ollama",new=model):
            self.assertEqual(self.request().status_code,409)

    def test_intentional_chapter_break_and_foreign_project_blocked(self):
        with patch.object(api,"inspect_pdf",return_value={
            "page_count":3,
            "pages":[{"occupied_ratio":0.8,"wide_spaces_detected":False},
                     {"occupied_ratio":0.2,"wide_spaces_detected":False},
                     {"occupied_ratio":0.6,"wide_spaces_detected":False}],
            "headings":[{"title":"Головка HDD","actual_pdf_page":1},
                        {"title":"Другая глава","actual_pdf_page":3}]}):
            self.assertEqual(self.request().status_code,409)
        self.assertEqual(self.client.post(self.url.replace("test-hdd","other"),
           json={"scene_id":"scene-1","page_number":2,"model":"qwen3:8b"}).status_code,404)


if __name__=="__main__":
    unittest.main()
