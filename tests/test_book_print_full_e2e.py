"""Full synthetic book journey, steps 11–14: actual PDF and DOCX, fake Ollama.

Everything lives in TemporaryDirectory. No external HTTP, home GPU, app service
or author files. PDF pagination/TOC are measured from actual ReportLab output.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))

import chat_outline_builder as outline
import page_fit_editorial_api as accept_api
import page_fit_editorial_acceptance as accept_core
import semantic_page_fit as fit
from chapter_planning import init_plan_tables, get_plan
from publishing_exports import manuscript_pdf,manuscript_docx
from print_proof_preflight import inspect_pdf
from page_fit_advisor import PageEvidence,recommend


class Scene(BaseModel):
    id:str
    title:str
    text:str=""
    anchor:str=""
    hook:str=""
    blocks:list=Field(default_factory=list)


class Project(BaseModel):
    id:str
    title:str="Испытательный курс HDD"
    author_name:str="Тестовый автор"
    publisher_name:str=""
    publication_year:int=2026
    edition_label:str="e2e"
    work_type:str="technical"
    target_length:str=""
    duration_minutes:int=10
    twist:str=""
    reveal:str=""
    updated_at:str=""
    scenes:list[Scene]=Field(default_factory=list)


CHAPTERS=[
    {"title":"Магнитная запись",
     "anchor":"Дорожка, поверхность, магнитная головка",
     "hook":"От физики чтения к механике позиционирования",
     "editorial_goal":"Последовательно определить основные понятия"},
    {"title":"Позиционирование головки",
     "anchor":"Перемещение и ожидание сектора",
     "hook":"От механики к организации данных",
     "editorial_goal":"Объяснить роль управляющей электроники"},
    {"title":"Дорожки и сектора",
     "anchor":"Разметка поверхности и адресация",
     "hook":"Переход к надёжности хранения",
     "editorial_goal":"Сопоставить физическую и логическую адресацию"},
]
ADDITION=("После перемещения головки электроника диска ждёт пригодного для "
          "чтения положения относительно дорожки. Этот этап необходимо "
          "рассматривать отдельно от последующей передачи данных, поскольку "
          "механические задержки и последовательность команд влияют на "
          "восприятие производительности накопителя.")
SOURCE=("В HDD магнитная головка выполняет чтение данных с дорожки. "
        "Позиционирование и передача данных — разные этапы работы накопителя.")


def chapter_text(i):
    # Intentionally longer than a page: real A5 render usually spans
    # three or more pages per chapter; never inject fake page metrics.
    patterns=[
      "Плотность магнитной записи зависит от свойств поверхности и способа считывания. "
      "Важна последовательность физических процессов, а не только число записанных битов.",
      "Перемещение головки предшествует поиску сектора и чтению данных. "
      "При изучении задержек следует различать позиционирование и передачу информации.",
      "Дорожки и сектора используются для организации информации на поверхности. "
      "Физическая геометрия и способы адресации связаны, но не тождественны."
    ]
    return "\n\n".join((patterns[i]+" "+patterns[i])*7 for _ in range(1))


class FullJourney(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"achewiw.sqlite3"
        self.original=Project(id="synthetic-hdd")
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("""CREATE TABLE projects(
                id TEXT PRIMARY KEY,data TEXT NOT NULL,updated_at TEXT NOT NULL)""")
            conn.execute("INSERT INTO projects VALUES(?,?,?)",
                         (self.original.id,self.original.model_dump_json(),""))
            conn.execute("CREATE TABLE ai_jobs(id TEXT,status TEXT)")
            init_plan_tables(conn)

        @contextmanager
        def db():
            conn=sqlite3.connect(self.path,timeout=10)
            conn.row_factory=sqlite3.Row
            try:
                with conn:yield conn
            finally:conn.close()
        self.db=db

        def get_project(pid):
            if pid!=self.original.id:raise HTTPException(404,"Unknown synthetic project")
            with db() as conn:
                raw=conn.execute("SELECT data FROM projects WHERE id=?",(pid,)).fetchone()
            return Project.model_validate_json(raw["data"])

        self.get_project=get_project
        api=APIRouter(prefix="/api",dependencies=[Depends(lambda:None)])
        accept_api.install(api,{"db":db,"get_project":get_project,
                                "Project":Project,"illustrations_for_book":lambda pid:[]})
        app=FastAPI();app.include_router(api)
        self.client=TestClient(app)
        self.prefix="/api/projects/synthetic-hdd/print-proof/fit-reviews"

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def make_book(self):
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            draft=outline.begin(conn,project_id=self.original.id,
                thread_id="synthetic-thread",message_id="synthetic-author",
                total_words=2100,count=3,Project=Project)
            self.assertEqual(draft["status"],"draft")
            next_=outline.append(conn,project_id=self.original.id,
                thread_id="synthetic-thread",build_id=draft["id"],
                items=CHAPTERS,Project=Project)
            self.assertEqual(next_["status"],"ready")
            approved=outline.decide(conn,project_id=self.original.id,
                thread_id="synthetic-thread",build_id=draft["id"],
                approve=True,Project=Project,Scene=Scene,
                has_pending_book=lambda connection,pid:None)
            self.assertEqual(approved["count"],3)
            p=self.get_project(self.original.id) if False else Project.model_validate_json(
                conn.execute("SELECT data FROM projects").fetchone()["data"])
            self.assertEqual(len(p.scenes),3)
            self.assertEqual(get_plan(conn,p)["target_words"],2100)
            for i,scene in enumerate(p.scenes):
                scene.text=chapter_text(i)
            conn.execute("UPDATE projects SET data=? WHERE id=?",
                         (p.model_dump_json(),p.id))
        return self.get_project(self.original.id)

    def fake_llm(self,original):
        evidence=fit.collect_evidence({"evidence":[
            {"source":"approved","scene_id":"chapter-2","text":SOURCE},
            {"source":"draft","scene_id":"chapter-3","text":"Непринятый текст"}],
            "facts":[]},original.scenes[1].id)
        self.assertEqual(len(evidence),1)
        async def transport(req):
            if req.url.path=="/api/tags":
                return httpx.Response(200,json={"models":[{"name":"qwen3:8b"}]})
            return httpx.Response(200,json={"response":json.dumps({
                "paragraph":ADDITION,"evidence_ids":["excerpt-1"]},ensure_ascii=False)})
        real=httpx.AsyncClient
        def fake_client(**kwargs):
            kwargs.pop("trust_env",None)
            return real(transport=httpx.MockTransport(transport),**kwargs)
        with patch.object(fit.httpx,"AsyncClient",side_effect=fake_client):
            proposed=asyncio.run(fit.propose_with_ollama(
                base_url="http://127.0.0.1:11434",model="qwen3:8b",
                original=original.scenes[1].text,instruction="Уточни процесс",
                evidence=evidence))
        self.assertEqual(proposed["evidence_ids"],["excerpt-1"])
        with self.db() as conn:
            saved=fit.save(conn,project_id=original.id,
                scene_id=original.scenes[1].id,
                source_digest=fit.digest(original.scenes[1].text),
                project_digest=fit.digest(original.model_dump_json()),
                paragraph=proposed["paragraph"],evidence=evidence,
                evidence_ids=proposed["evidence_ids"],model="qwen3:8b",
                instruction="Уточни процесс")
        return saved


    def test_docx_page_starts_are_bound_to_headings(self):
        from docx import Document

        project=self.make_book()
        document=Document(BytesIO(manuscript_docx(project)))
        starts=[p.text for p in document.paragraphs if p.paragraph_format.page_break_before]
        self.assertEqual(starts,["Содержание",*(s.title for s in project.scenes),"Выходные сведения"])
        self.assertFalse(document.element.xpath(".//w:br[@w:type='page']"))

    def test_book_real_pdf_toc_llm_approve_and_undo(self):
        p=self.make_book()
        source_text=[s.text for s in p.scenes]
        pdf_before=manuscript_pdf(p,print_proof=True)
        before=inspect_pdf(pdf_before,chapter_titles=[s.title for s in p.scenes])
        self.assertGreaterEqual(before["page_count"],8)
        self.assertLessEqual(before["page_count"],12)
        self.assertEqual(len(before["headings"]),3)
        # The page references must be real, not fabricated by mocked preflight.
        self.assertTrue(all(h["actual_pdf_page"] is not None for h in before["headings"]))
        if not all(h["toc_page"] is not None for h in before["headings"]):
            import fitz
            with fitz.open(stream=pdf_before,filetype="pdf") as pdf:
                print("DEBUG TOC page 2 text:",repr(pdf[1].get_text("text",sort=True)[:1600]))
                print("DEBUG TOC page 2 words:",repr(pdf[1].get_text("words")[:40]))
        self.assertTrue(all(h["toc_page"] is not None for h in before["headings"]),
                        before["headings"])
        self.assertTrue(all(h["match"] is True for h in before["headings"]),
                        before["headings"])
        self.assertEqual(before["page_size_pt"],[419.53,595.28])

        docx=manuscript_docx(p)
        with ZipFile(BytesIO(docx)) as z:
            xml=z.read("word/document.xml").decode()
            settings=z.read("word/settings.xml").decode()
        self.assertIn(' TOC \\o "1-2" \\h \\z \\u ',xml)
        self.assertIn("updateFields",settings)

        suggestion=self.fake_llm(p)
        self.assertEqual(self.get_project(p.id).scenes[1].text,source_text[1])
        prepared=self.client.post(self.prefix,json={"suggestion_id":suggestion["id"]})
        self.assertEqual(prepared.status_code,201,prepared.text)
        review=prepared.json()
        self.assertEqual(review["before_pages"],before["page_count"])
        self.assertEqual(self.get_project(p.id).scenes[1].text,source_text[1])
        reject_unapproved=self.client.post(
           self.prefix+"/"+review["review_id"]+"/accept",json={
             "confirm_sources":False,"confirm_pagination":True,
             "expected_preview_digest":review["preview_digest"]})
        self.assertEqual(reject_unapproved.status_code,409)
        accepted=self.client.post(self.prefix+"/"+review["review_id"]+"/accept",json={
             "confirm_sources":True,"confirm_pagination":True,
             "expected_preview_digest":review["preview_digest"]})
        self.assertEqual(accepted.status_code,200,accepted.text)
        a=accepted.json()
        self.assertTrue(a["docx_toc_update_required"])
        self.assertFalse(a["release_ready"])
        changed=self.get_project(p.id)
        self.assertEqual(changed.scenes[0].text,source_text[0])
        self.assertEqual(changed.scenes[2].text,source_text[2])
        self.assertTrue(changed.scenes[1].text.endswith(ADDITION))
        after=inspect_pdf(manuscript_pdf(changed,print_proof=True),
                    chapter_titles=[s.title for s in changed.scenes])
        self.assertEqual(a["final_pdf_preflight"]["pages"],after["page_count"])
        self.assertNotEqual(before["pdf_sha256"],after["pdf_sha256"])
        self.assertTrue(all(h["match"] is True for h in after["headings"]),
                        after["headings"])
        self.assertEqual(self.client.post(self.prefix+"/"+review["review_id"]+"/undo").status_code,200)
        restored=self.get_project(p.id)
        self.assertEqual([s.text for s in restored.scenes],source_text)
        final=inspect_pdf(manuscript_pdf(restored,print_proof=True),
                    chapter_titles=[s.title for s in restored.scenes])
        self.assertEqual(final["page_count"],before["page_count"])
        self.assertTrue(all(h["match"] is True for h in final["headings"]))
        with self.db() as db:
            self.assertEqual(db.execute("SELECT status FROM ai_page_fit_reviews").fetchone()[0],"undone")
        print("E2E REAL PAGE METRICS:",json.dumps({
            "before_pages":before["page_count"],"after_pages":after["page_count"],
            "restored_pages":final["page_count"],
            "before_toc":before["headings"],"after_toc":after["headings"],
            "accepted":True,"undone":True
        },ensure_ascii=False))
        artifact_dir=os.environ.get("ACHEWIW_E2E_ARTIFACT_DIR")
        if artifact_dir:
            output=Path(artifact_dir)
            output.mkdir(parents=True,exist_ok=True)
            (output/"synthetic-hdd-before.pdf").write_bytes(pdf_before)
            (output/"synthetic-hdd-after.pdf").write_bytes(
                manuscript_pdf(changed,print_proof=True))
            (output/"synthetic-hdd-restored.pdf").write_bytes(
                manuscript_pdf(restored,print_proof=True))
            (output/"synthetic-hdd-before.docx").write_bytes(docx)
            report={"book":"synthetic HDD only",
                    "real_pdf_pages":{"before":before["page_count"],
                                      "after":after["page_count"],
                                      "restored":final["page_count"]},
                    "real_toc_before":before["headings"],
                    "real_toc_after":after["headings"],
                    "author_approval_simulated":True,
                    "ollama_http_transport":"mock, no local model used",
                    "canonical_accept_and_undo_passed":True,
                    "server_7080_or_user_manuscript_modified":False}
            (output/"e2e-report.json").write_text(
                json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")

    def test_bad_toc_and_layout_are_not_padded_with_filler(self):
        self.assertEqual(recommend(PageEvidence(223,0.12,next_page_starts_part=True))["action"],
                         "preserve_break")
        self.assertEqual(recommend(PageEvidence(221,0.32,justified_large_word_spaces=True))["action"],
                         "fix_layout")
        self.assertEqual(recommend(PageEvidence(150,0.37))["action"],
                         "offer_semantic_expansion")


if __name__=="__main__":
    unittest.main()
