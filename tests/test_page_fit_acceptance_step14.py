"""Step 14: measured before/after print proof + atomic author acceptance + CAS undo."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import sqlite3
import sys
import tempfile
import unittest

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel,Field

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import page_fit_editorial_api as routes
import page_fit_editorial_acceptance as review
from semantic_page_fit import digest,save
from generation_core import active_job

ORIGINAL=("Магнитная головка располагается над дорожкой. "
          "Контроллер передаёт команды перемещения.")
PARAGRAPH=("После позиционирования контроллер проверяет готовность механизма. "
           "Именно согласование этапов чтения и перемещения помогает объяснить "
           "разницу между временем поиска и временем передачи информации на диск.")


class Scene(BaseModel):
    id:str
    title:str
    text:str
    anchor:str=""
    hook:str=""


class Project(BaseModel):
    id:str
    title:str="Гантова зебра — проверка"
    target_length:str="1000 слов"
    work_type:str="technical"
    edition_label:str="r04"
    updated_at:str=""
    scenes:list[Scene]=Field(default_factory=list)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"canon.sqlite3"
        self.p=Project(id="test-hdd",scenes=[Scene(
            id="s1",title="Устройство HDD",text=ORIGINAL)])
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,data TEXT,updated_at TEXT)")
            db.execute("INSERT INTO projects VALUES(?,?,?)",(
                self.p.id,self.p.model_dump_json(),""))
            db.execute("CREATE TABLE ai_jobs(id TEXT,status TEXT)")
        @contextmanager
        def database():
            conn=sqlite3.connect(self.path,timeout=6)
            conn.row_factory=sqlite3.Row
            try:
                with conn:yield conn
            finally:
                conn.close()
        self.database=database
        def project(pid):
            if pid!="test-hdd":raise HTTPException(404,"Not found")
            with database() as db:
                raw=db.execute("SELECT data FROM projects WHERE id=?",(pid,)).fetchone()["data"]
            return Project.model_validate_json(raw)
        self.project=project
        with database() as db:
            self.suggestion=save(db,project_id=self.p.id,scene_id="s1",
                source_digest=digest(ORIGINAL),project_digest=digest(self.p.model_dump_json()),
                paragraph=PARAGRAPH,evidence=[{"id":"excerpt-1","excerpt":"Авторская рукопись",
                  "kind":"author_manuscript"}],evidence_ids=["excerpt-1"],
                instruction="Уточнить переход",model="qwen3:8b")["id"]
        router=APIRouter(prefix="/api",dependencies=[Depends(lambda:None)])
        routes.install(router,{"get_project":project,"db":database,"Project":Project,
                               "illustrations_for_book":lambda pid:[]})
        app=FastAPI()
        app.include_router(router)
        self.client=TestClient(app)
        self.prefix="/api/projects/test-hdd/print-proof/fit-reviews"
        self.mockpdf=patch.object(routes,"manuscript_pdf",side_effect=lambda p,**kw:
            b"%PDF-new" if PARAGRAPH in p.scenes[0].text else b"%PDF-old")
        self.mockinspect=patch.object(routes,"inspect_pdf",side_effect=lambda pdf,**kw:{
            "page_count":4 if pdf==b"%PDF-new" else 3,
            "pdf_sha256":digest(pdf.decode()),
            "ready":True,"requires_editor_review":False,"findings":[],
            "headings":[{"title":"Устройство HDD","actual_pdf_page":2 if pdf==b"%PDF-new" else 1}]})
        self.mockpdf.start();self.mockinspect.start()

    def tearDown(self):
        self.mockpdf.stop();self.mockinspect.stop()
        self.client.close()
        self.tmp.cleanup()

    def prepare(self):
        return self.client.post(self.prefix,json={"suggestion_id":self.suggestion})

    def decision(self,identifier,preview_digest,yes=True):
        return self.client.post(self.prefix+"/"+identifier+"/accept",json={
            "confirm_sources":yes,"confirm_pagination":True,
            "expected_preview_digest":preview_digest})

    def text(self):
        return self.project("test-hdd").scenes[0].text

    def test_review_is_read_only_until_explicit_accept_then_safe_undo(self):
        prep=self.prepare()
        self.assertEqual(prep.status_code,201,prep.text)
        record=prep.json()
        self.assertEqual(record["page_delta"],1)
        self.assertEqual(self.text(),ORIGINAL)
        self.assertIn(PARAGRAPH,record["candidate_text"])
        self.assertEqual(record["evidence"][0]["id"],"excerpt-1")
        missing=self.decision(record["review_id"],record["preview_digest"],False)
        self.assertEqual(missing.status_code,409,missing.text)
        self.assertEqual(self.text(),ORIGINAL)
        accepted=self.decision(record["review_id"],record["preview_digest"])
        self.assertEqual(accepted.status_code,200,accepted.text)
        self.assertEqual(accepted.json()["status"],"accepted")
        self.assertEqual(accepted.json()["final_pdf_preflight"]["pages"],4)
        self.assertTrue(accepted.json()["docx_toc_update_required"])
        self.assertTrue(accepted.json()["pdf_proof_ready"])
        self.assertFalse(accepted.json()["release_ready"])
        self.assertIn(PARAGRAPH,self.text())
        with self.database() as db:
            self.assertEqual(db.execute("SELECT status FROM ai_page_fit_reviews").fetchone()[0],
                             "accepted")
        undone=self.client.post(self.prefix+"/"+record["review_id"]+"/undo")
        self.assertEqual(undone.status_code,200,undone.text)
        self.assertEqual(self.text(),ORIGINAL)
        self.assertEqual(self.client.post(self.prefix+"/"+record["review_id"]+"/undo").status_code,409)

    def test_invalid_preview_digest_never_changes_chapter(self):
        data=self.prepare().json()
        result=self.decision(data["review_id"],"0"*64)
        self.assertEqual(result.status_code,409)
        self.assertEqual(self.text(),ORIGINAL)

    def test_concurrent_edit_refuses_approval(self):
        data=self.prepare().json()
        with self.database() as db:
            changed=self.project("test-hdd")
            changed.scenes[0].text+=" Новая правка."
            db.execute("UPDATE projects SET data=? WHERE id=?",(
                changed.model_dump_json(),changed.id))
        refused=self.decision(data["review_id"],data["preview_digest"])
        self.assertEqual(refused.status_code,409,refused.text)
        self.assertIn("Новая правка",self.text())

    def test_undo_cannot_overwrite_later_author_edits(self):
        data=self.prepare().json()
        self.assertEqual(self.decision(data["review_id"],data["preview_digest"]).status_code,200)
        with self.database() as db:
            current=self.project("test-hdd")
            current.scenes[0].text+=" Другая правка."
            db.execute("UPDATE projects SET data=? WHERE id=?",(
                current.model_dump_json(),current.id))
        refused=self.client.post(self.prefix+"/"+data["review_id"]+"/undo")
        self.assertEqual(refused.status_code,409)
        self.assertIn("Другая правка",self.text())

    def test_duplicate_prepare_and_accept_blocked(self):
        data=self.prepare().json()
        self.assertEqual(self.prepare().status_code,409)
        self.assertEqual(self.decision(data["review_id"],data["preview_digest"]).status_code,200)
        self.assertEqual(self.decision(data["review_id"],data["preview_digest"]).status_code,409)

    def test_review_is_project_scoped(self):
        self.assertEqual(self.client.post(
            "/api/projects/other/print-proof/fit-reviews",
            json={"suggestion_id":self.suggestion}).status_code,404)
        self.assertEqual(self.client.get(self.prefix+"/"+"a"*32).status_code,404)

    def test_active_book_job_blocks_accept(self):
        data=self.prepare().json()
        with self.database() as db:
            db.execute("INSERT INTO ai_jobs VALUES('j1','running')")
        self.assertEqual(self.decision(data["review_id"],data["preview_digest"]).status_code,409)
        self.assertEqual(self.text(),ORIGINAL)

    def test_ui_keeps_original_typography_and_confirmations(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"app/static/index.html").read_text()
        js=(root/"app/static/chat_studio.js").read_text()
        for key in ("data-fit-review-sources","data-fit-review-pages",
                    "data-fit-review-accept","data-fit-review-undo"):
            self.assertIn(key,html)
        self.assertIn("expected_preview_digest",js)
        self.assertIn("window.confirm",js)
        self.assertIn('data-tab="book"',html)


if __name__=="__main__":
    unittest.main()
