#!/usr/bin/env python3
"""Host-only comparative QA: ordinary book vs autonomous chat + real Ollama.

Run in an ALREADY verified Studio container, as a manually invoked test.
Uses the public FastAPI HTTP endpoints and actual Ollama; QA-only projects.
Does not touch existing manuscripts, delete any data, manage services or deploy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE="http://127.0.0.1:7080"
BRIEF=(
    "Лёгкая познавательная мини-книга «Дежурство маленького контроллера». "
    "Три главы о вымышленном контроллере по имени Искра, который объясняет "
    "время ожидания, движение головки и устройство секторов HDD. "
    "Без настоящих исторических дат, вымышленных цитат и названий компаний. "
    "Объяснения должны быть технически понятными, но это НЕ «Гантова зебра», "
    "не её главы и не пересказ книги автора. Сохранять последовательность: "
    "поиск дорожки → ожидание сектора → передача данных."
)
CHAPTERS=[
 ("Дорожка и первый поиск","Контроллер Искра наблюдает перемещение головки к дорожке",
  "От позиционирования перейти к ожиданию сектора","Показать отличие механического перемещения от чтения"),
 ("Сектор проходит мимо","Искра объясняет вращение диска и ожидание нужного сектора",
  "Подвести к переносу информации","Пояснить задержку вращения без вымышленных чисел"),
 ("Данные отправляются в путь","Контроллер отделяет передачу данных от поиска",
  "Сформулировать вывод о последовательности этапов","Сравнить три этапа и завершить мини-книгу")
]

def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def words(text):
    return len(re.findall(r"[^\W_]+",text,re.UNICODE))

def check(condition,label):
    if not condition:
        raise RuntimeError("QA failed: "+label)

class Tester:
    def __init__(self,base,model,report):
        self.base=base.rstrip("/")
        self.model=model
        self.report=Path(report)
        self.items={"qa_label":"two_mode_real_ollama","created_at":datetime.now(timezone.utc).isoformat(),
                    "host":self.base,"model":model,"scope":"QA-only distinct projects",
                    "stage":"init","events":[],"projects":{},"errors":[]}
        self.client=httpx.Client(base_url=self.base,timeout=httpx.Timeout(
            connect=5,read=180,write=20,pool=5),trust_env=False)

    def event(self,name,**details):
        record={"step":name,"at":datetime.now(timezone.utc).isoformat(),**details}
        self.items["events"].append(record)
        self.items["stage"]=name
        self.flush()
        print("QA",name,json.dumps(details,ensure_ascii=False)[:250],flush=True)

    def flush(self):
        self.report.parent.mkdir(parents=True,exist_ok=True)
        self.report.write_text(json.dumps(self.items,ensure_ascii=False,indent=2),encoding="utf-8")

    def req(self,method,path,payload=None,timeout=None):
        resp=self.client.request(method,"/api"+path,json=payload,timeout=timeout)
        if resp.status_code>=400:
            try:reason=str(resp.json().get("detail",""))[:300]
            except (ValueError,AttributeError):reason=resp.text[:120]
            raise RuntimeError(f"{method} {path} -> HTTP {resp.status_code}: {reason}")
        if resp.headers.get("content-type","").startswith("application/pdf"):
            return resp.content
        if "officedocument" in resp.headers.get("content-type",""):
            return resp.content
        return resp.json()

    def new(self,kind):
        title=("QA 11–15 · "+("Обычный режим" if kind=="ordinary" else "Чат")+
               " · "+uuid.uuid4().hex[:6])
        p=self.req("POST","/projects",{"title":title,"work_type":"technical"})
        p.update({"genre":"Популярная техническая миниатюра",
             "premise":BRIEF,"audience":"Начинающий читатель",
             "anchors":"Искра — вымышленный контроллер; факты проверять отдельно",
             "target_length":"600 слов","atmosphere":"Познавательная, лёгкая",
             "claims":"Различать позиционирование, вращательное ожидание и передачу"})
        if kind=="ordinary":
            p["scenes"]=[{"id":uuid.uuid4().hex,"title":a,"anchor":b,"hook":c,
                          "reader_info":d} for a,b,c,d in CHAPTERS]
        p=self.req("PUT","/projects/"+p["id"],p)
        self.items["projects"][kind]={"id":p["id"],"title":p["title"]}
        self.event(kind+"_created",project_id=p["id"],chapter_count=len(p["scenes"]))
        return p

    def normal_outline(self,p):
        proposal=self.req("GET",f"/projects/{p['id']}/book-plan?suggested=true")
        result=self.req("PUT",f"/projects/{p['id']}/book-plan",
                        {"chapters":proposal["chapters"]})
        self.event("ordinary_outline",chapters=len(proposal["chapters"]),
                   target=result.get("target_words"))
        return p

    def chat_outline(self,p):
        t=self.req("POST",f"/projects/{p['id']}/chats",{"workspace":"generation","mode":"autonomous"})
        msg=self.req("POST",f"/projects/{p['id']}/chats/{t['id']}/messages",{
            "message":BRIEF+" Сначала составь последовательный план на 3 главы.","scope":"book"})
        start=self.req("POST",f"/projects/{p['id']}/chats/{t['id']}/new-outline",
                        {"chat_proposal_id":msg["proposal_id"],"count":3,"total_words":600})
        result=self.req("POST",
            f"/projects/{p['id']}/chats/{t['id']}/new-outline/{start['id']}/batch",
            {"model":self.model},timeout=180)
        check(result["status"]=="ready" and result["created"]==3,"chat outline complete")
        decision=self.req("POST",
            f"/projects/{p['id']}/chats/{t['id']}/new-outline/{start['id']}/decision",
            {"approve":True})
        check(decision["status"]=="accepted","chat outline approved")
        self.event("chat_outline",chapters=result["chapters"],
                   model=result.get("model"),tokens=result.get("output_tokens"))
        return t

    def job(self,kind,p,thread=None):
        if kind=="ordinary":
            job=self.req("POST","/books/generate",
                         {"project_id":p["id"],"model":self.model})
        else:
            job_response=self.req("POST",
                f"/projects/{p['id']}/chats/{thread['id']}/book-control",
                {"action":"start","confirm":True,"model":self.model})
            job=job_response["job"]
            check(job_response["independent_generator"] is False,
                  "chat did not create a second generator")
        self.event(kind+"_started",job_id=job["id"])
        deadline=time.monotonic()+1800
        prev=None
        while time.monotonic()<deadline:
            state=self.req("GET",f"/books/jobs/{job['id']}")
            status=state.get("status")
            stage=state.get("stage")
            position=(status,stage,state.get("stream_words"))
            if position!=prev:
                print("QA STATUS",kind,position,flush=True)
                prev=position
            if status in ("ready","error","interrupted","cancelled"):
                break
            time.sleep(5)
        else:
            self.event(kind+"_timeout",job_id=job["id"],last_status=prev)
            raise RuntimeError(kind+" generation exceeded time budget; job remains in SQLite")
        self.event(kind+"_finished",job_id=job["id"],status=status,stage=stage,
                   actual_words=state.get("book",{}).get("total_words"))
        check(status=="ready",f"{kind} generation status={status}")
        draft=self.req("GET",f"/books/jobs/{job['id']}/draft")
        check(len(draft["chapters"])==3 and all(c["text"].strip() for c in draft["chapters"]),
              f"{kind} three nonempty draft chapters")
        accepted=self.req("POST",f"/books/jobs/{job['id']}/accept",{"confirm":True})
        check(accepted["status"]=="accepted_by_author",kind+" QA manuscript accepted")
        current=self.req("GET",f"/projects/{p['id']}")
        check(all(s["text"].strip() for s in current["scenes"]),kind+" canon written")
        self.items["projects"][kind].update({
            "job_id":job["id"],
            "chapter_titles":[s["title"] for s in current["scenes"]],
            "chapter_words":[words(s["text"]) for s in current["scenes"]],
            "chapter_digests":[sha(s["text"]) for s in current["scenes"]]})
        self.event(kind+"_accepted",total_words=sum(words(s["text"]) for s in current["scenes"]))
        return current

    def editorial_chat(self,project):
        pid=project["id"]
        original=project["scenes"][0]["text"]
        version=self.req("POST",f"/projects/{pid}/editorial/versions",
            {"source":"manuscript","label":"QA исходная редакция"})
        version_id=version["id"]
        attach=self.req("POST",f"/projects/{pid}/chat-edition",{"version_id":version_id})
        base=attach["head_version_id"]
        thread=self.req("POST",f"/projects/{pid}/chats",{
            "workspace":"editorial","mode":"autonomous"})
        msg=self.req("POST",f"/projects/{pid}/chats/{thread['id']}/messages",{
            "message":"Отредактируй главу для ясности: убери повторы, сделай переходы точнее, "
                     "не вводи новых исторических дат и чисел. Верни полный вариант.",
            "scope":"chapter","scene_id":project["scenes"][0]["id"],
            "base_version_id":base})
        draft=self.req("POST",f"/projects/{pid}/chats/{thread['id']}/ollama-candidate",{
            "chat_proposal_id":msg["proposal_id"],"base_version_id":base,
            "scene_id":project["scenes"][0]["id"],"model":self.model},timeout=180)
        check(draft["approval_required"],"chat edit requires approval")
        decision=self.req("POST",f"/projects/{pid}/chats/{thread['id']}/candidates/{msg['proposal_id']}/decision",
            {"approve":True})
        current=self.req("GET",f"/projects/{pid}/chat-edition")
        manuscript=self.req("GET",f"/projects/{pid}")
        check(sha(manuscript["scenes"][0]["text"])==sha(original),
              "chat editing preserves source manuscript canon")
        check(current["version_id"]!=base,"new immutable chat edition")
        self.event("editorial_chat_accepted",
                   old_edition=base,new_edition=current["version_id"],
                   candidate_tokens=draft.get("output_tokens"),
                   canonical_untouched=True)
        return current

    def export(self,kind,p):
        pid=p["id"]
        pdf=self.req("GET",f"/projects/{pid}/manuscript.pdf")
        docx=self.req("GET",f"/projects/{pid}/manuscript.docx")
        check(pdf[:4]==b"%PDF" and docx[:2]==b"PK",kind+" export MIME")
        from print_proof_preflight import inspect_pdf
        result=inspect_pdf(pdf,chapter_titles=[s["title"] for s in p["scenes"]])
        match=sum(x["match"] is True for x in result["headings"])
        self.items["projects"][kind]["export"]={
            "pdf_pages":result["page_count"],"matching_toc_chapters":match,
            "chapter_count":len(p["scenes"]), "pdf_bytes":len(pdf),"docx_bytes":len(docx)}
        self.event(kind+"_export",pages=result["page_count"],
                   toc_matched=match,out_of=len(p["scenes"]))
        check(match==len(p["scenes"]),kind+" actual PDF TOC")
        reloaded=self.req("GET",f"/projects/{pid}")
        check([s["text"] for s in reloaded["scenes"]]==[s["text"] for s in p["scenes"]],
              kind+" F5-style state reload")

    def run(self):
        healthy=self.req("GET","/health")
        check(healthy["status"]=="ok","server health")
        self.event("health_ok")
        normal=self.new("ordinary")
        self.normal_outline(normal)
        chat=self.new("chat")
        thread=self.chat_outline(chat)
        normal=self.job("ordinary",normal)
        chat=self.job("chat",chat,thread)
        self.export("ordinary",normal)
        self.export("chat",chat)
        self.editorial_chat(chat)
        self.items["result"]="PASS"
        self.event("completed",result="PASS",created_projects=2)
        print("QA COMPLETE:",str(self.report))
        return 0


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--base",default=BASE)
    parser.add_argument("--model",default="qwen3:4b-instruct")
    parser.add_argument("--report",default="/data/qa-reports/dual-mode-real-ollama.json")
    args=parser.parse_args()
    test=Tester(args.base,args.model,args.report)
    try:
        return test.run()
    except Exception as exc:
        test.items["result"]="FAIL"
        test.items["errors"].append({"stage":test.items["stage"],
                                       "error":str(exc),"type":type(exc).__name__})
        test.flush()
        print("QA FAILED",type(exc).__name__,str(exc),flush=True)
        return 1
    finally:
        test.client.close()


if __name__=="__main__":
    raise SystemExit(main())
