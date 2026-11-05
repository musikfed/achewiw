"""Live production conveyor for ACHEWIW; configured by main.py.

A role never auto-accepts rewritten fiction. Images, reader proofs and finalized
editions are separate immutable artifacts. All GPU calls are serial within the
ACHEWIW lease, with heartbeat and fail-closed checks of shared Ollama.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import conveyor_core as cv
from prose_quality import quality_findings
from narrative_direction import fiction_warnings
from reader_engagement import lexical_findings as attention_findings, review_contract as attention_review_contract
from gpu_scheduling import (image_operation_active, reserve_gpu, release_gpu,
                            refresh_gpu)
from image_engine import generate as generate_picture, free_cached_models, status as image_status
from illustrations_core import save as save_picture
from book_layout_core import COVER_SCENE_ID
from editorial_review import snapshot as edition_snapshot, review as edition_review
from publishing_exports import manuscript_pdf, manuscript_docx, manuscript_fb2
from manuscript_core import manuscript_epub

class ConveyorRequest(BaseModel):
    writer_model: str = Field(default="qwen3:8b",min_length=1,max_length=200)
    critic_model: str = Field(default="qwen3:4b-instruct",min_length=1,max_length=200)
    image_mode: Literal["none","cover","cover_chapters"] = "cover"
    image_style: str = Field(default="book illustration, expressive ink and watercolor",max_length=400)
    allow_shared_unload: bool = False

class AuthorDecision(BaseModel):
    confirm: Literal[True]
    # True = explicitly accept proposed edited chapter; False = original saved draft.
    selected_edits: dict[str,bool] = Field(default_factory=dict)

class ResumeRequest(BaseModel):
    confirm_author_approval: bool = False


def review_windows(source: str, limit: int=6500) -> list[str]:
    """Cover the entire chapter, preserving byte-for-byte order.

    Find a paragraph/sentence/word boundary when possible; a pathological
    unbroken paragraph is still inspected rather than silently skipped.
    """
    if limit<1000:
        raise ValueError("Review windows must be large enough for literary context")
    result=[]
    start=0
    while start<len(source):
        end=min(len(source),start+limit)
        if end<len(source):
            candidate=source.rfind("\n\n",start+limit//2,end)
            if candidate!=-1:
                end=candidate+2
            else:
                candidate=max(source.rfind(". ",start+limit//2,end),
                              source.rfind("! ",start+limit//2,end),
                              source.rfind("? ",start+limit//2,end))
                if candidate!=-1:
                    end=candidate+2
                else:
                    candidate=source.rfind(" ",start+limit//2,end)
                    if candidate!=-1:
                        end=candidate+1
        if end<=start:
            end=min(len(source),start+limit)
        result.append(source[start:end])
        start=end
    return result

def install(api: APIRouter, env: dict) -> None:
    """Install API routes after main's functions exist, before include_router."""
    conn=env["db"]
    background=env["_background_tasks"]
    data_dir=env["DATA_DIR"]
    ollama=env["OLLAMA_BASE_URL"]

    def book_rows(job: dict) -> list[dict]:
        with conn() as db:
            return env["chapters_for_job"](db,job["book_job_id"])

    def project(job: dict):
        return env["get_project"](job["project_id"])

    async def ps() -> list[str]:
        async with httpx.AsyncClient(timeout=10,trust_env=False) as client:
            r=await client.get(ollama+"/api/ps")
            r.raise_for_status()
            return [x["name"] for x in env["summarize_ollama_running"](r.json())]

    async def unload_our_model(name: str, allow: bool):
        loaded=await ps()
        if name not in loaded:
            return
        if not allow:
            raise ValueError("Нужно разрешение автора на выгрузку модели "+name)
        # Do not unload anything except the writer/critic specified by this job.
        async with httpx.AsyncClient(timeout=40,trust_env=False) as client:
            r=await client.post(ollama+"/api/generate",
                json={"model":name,"stream":False,"keep_alive":0})
            r.raise_for_status()
        for attempt in range(12):
            if name not in await ps():
                return
            await asyncio.sleep(1)
        raise ValueError("Ollama не освободила модель "+name)

    async def check_gpu_models(selected: str, job: dict):
        loaded=await ps()
        authorized={job["writer_model"],job["critic_model"]}
        foreign=[name for name in loaded if name not in authorized and name!=selected]
        if foreign:
            raise ValueError("GPU занята другой Ollama-моделью: "+", ".join(foreign[:3]))
        for name in loaded:
            if name!=selected:
                await unload_our_model(name,job["allow_shared_unload"])

    @asynccontextmanager
    async def gpu_session(job_id: str, reason: str):
        token=uuid.uuid4().hex
        with conn() as db:
            db.execute("BEGIN IMMEDIATE")
            if env["active_job"](db) or not reserve_gpu(db,token,"conveyor:"+reason):
                raise ValueError("GPU занята другим заданием ACHEWIW")
        stopping=asyncio.Event()
        lost=asyncio.Event()
        async def renew():
            while not stopping.is_set():
                try:
                    await asyncio.wait_for(stopping.wait(),timeout=90)
                    return
                except asyncio.TimeoutError:
                    try:
                        with conn() as db:
                            if not refresh_gpu(db,token):
                                lost.set()
                                return
                    except sqlite3.Error:
                        lost.set()
                        return
        task=asyncio.create_task(renew())
        try:
            yield lost
            if lost.is_set():
                raise ValueError("Потеряна аренда GPU; результат требует перепроверки")
        finally:
            stopping.set()
            await task
            with conn() as db:
                release_gpu(db,token)

    async def call_model(job: dict, name: str, prompt: str,
                         *, num_predict: int=1200, temperature: float=0.15) -> str:
        if len(prompt)>16000:
            raise ValueError("Слишком длинный запрос критика")
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10,read=360,
                                        write=30,pool=10),trust_env=False) as client:
            r=await client.post(ollama+"/api/generate",json={
                "model":name,"prompt":prompt,"stream":False,"think":False,
                "keep_alive":"5m",
                "options":{"temperature":temperature,"num_ctx":8192,
                           "num_predict":num_predict,"repeat_penalty":1.1}})
            r.raise_for_status()
            data=r.json()
        output=data.get("response","")
        if not isinstance(output,str) or not output.strip():
            raise ValueError("Модель вернула пустой отчёт")
        return output.strip()

    def store(job_id: str, stage: str, asset_key: str, source: str, body):
        with conn() as db:
            return cv.put_artifact(db,job_id,stage,asset_key,source,body)

    def get(job_id: str, stage: str, key: str) -> dict:
        with conn() as db:
            return cv.artifact(db,job_id,stage,key)

    def state(job_id: str) -> dict:
        with conn() as db:
            return cv.status(db,job_id)

    def digest_chapters(chapters: list[dict]) -> str:
        return cv.fingerprint(json.dumps([(r["scene_id"],r["text"]) for r in chapters],
                                         ensure_ascii=False))

    async def book_stage(job: dict) -> str:
        # Main's durable book writer is already running (or is resumable).
        while True:
            with conn() as db:
                state_book=env["_book_record"](db,job["book_job_id"])
            if state_book["status"]=="ready":
                await unload_our_model(job["writer_model"],job["allow_shared_unload"])
                chapters=book_rows(job)
                if not chapters or not all(x["complete"] for x in chapters):
                    raise ValueError("Генерация не завершила все главы")
                return digest_chapters(chapters)
            if state_book["status"] in ("error","interrupted","cancelled"):
                raise ValueError("Книга прервана. Сначала возобнови её сохранённое задание: "+
                                 job["book_job_id"])
            if state_book["status"] not in ("queued","running"):
                raise ValueError("Недопустимый статус книги: "+state_book["status"])
            if state(job["id"])["cancel_requested"]:
                raise ValueError("Конвейер остановлен автором")
            await asyncio.sleep(3)

    async def mechanical(job: dict) -> str:
        chapters=book_rows(job)
        if not chapters or not all(c["complete"] for c in chapters):
            raise ValueError("Нельзя проверять незавершённую книгу")
        p=project(job)
        issues=[]
        seen=set()
        all_previous=""
        for ch in chapters:
            seq=ch["scene_index"]+1
            for warning in quality_findings(ch["text"],previous_chapters=all_previous,limit=10):
                issues.append({"chapter":seq,**warning})
            if p.work_type=="fiction":
                issues.extend({"chapter":seq,**w} for w in fiction_warnings(ch["text"]))
                issues.extend({"chapter":seq,**w} for w in attention_findings(ch["text"]))
            for paragraph in re.split(r"\n\s*\n",ch["text"]):
                norm=" ".join(paragraph.casefold().split())
                if len(norm)>65 and norm in seen:
                    issues.append({"chapter":seq,"kind":"duplicate_paragraph",
                                   "detail":"Повторён абзац: "+paragraph[:140]})
                seen.add(norm)
            all_previous+="\n\n"+ch["text"]
        report={"source_sha":digest_chapters(chapters),"issues":issues,
                "chapters":len(chapters),"total_words":sum(c["word_count"] for c in chapters),
                "finding_count":len(issues),
                "note":"Это автоматический фильтр, не литературное заключение."}
        return store(job["id"],"mechanical_qa","report",
                     report["source_sha"],report)

    async def literary(job: dict) -> str:
        chapters=book_rows(job)
        d=digest_chapters(chapters)
        last=""
        async with gpu_session(job["id"],"literary_qa") as lease:
            await check_gpu_models(job["critic_model"],job)
            p=project(job)
            with conn() as db:
                puzzle_cards=env["visual_puzzle_list"](db,p.id)
            for ch in chapters:
                if state(job["id"])["cancel_requested"]:
                    raise ValueError("Автор отменил конвейер")
                key=str(ch["scene_index"]+1)
                try:
                    old=get(job["id"],"literary_qa",key)
                    if old["source_sha"]==cv.fingerprint(ch["text"]):
                        last+=old["content_sha"]
                        continue
                except ValueError:
                    pass
                previous=chapters[ch["scene_index"]-1]["text"][-700:] if ch["scene_index"] else ""
                scheduled=[card for card in puzzle_cards if card["answer_scene_id"]==ch["scene_id"]]
                reveal_checks="; ".join(
                    "В ЭТОЙ главе должна быть проверяемая разгадка: "+
                    card["answer_note"][:500] for card in scheduled)[:1100]
                parts=review_windows(ch["text"])
                reports=[]
                for part_index,part in enumerate(parts,1):
                    part_key=key+"."+str(part_index)
                    part_sha=cv.fingerprint(part)
                    try:
                        prior=get(job["id"],"literary_qa",part_key)
                        if prior["source_sha"]==part_sha:
                            reports.append(json.loads(prior["body"])["report"])
                            continue
                    except (ValueError,KeyError,json.JSONDecodeError):
                        pass
                    prompt=(
                        "Ты независимый литературный редактор. НЕ пиши новую прозу. "
                        "Проверяй только процитированное: какие события происходят, их причина "
                        "и следствие, цель персонажа, живость действий, длинные смысловые повторы "
                        "и пустой диалог. Не считай отсутствие событий нормальной прозой. "
                        "Не придумывай факты, а приводи точные короткие цитаты и рекомендации. "
                        "Если задана визуальная разгадка, проверь её фактическое раскрытие действием. "
                        "Ответ на русском, только отчёт. "
                        "ЧАСТЬ "+str(part_index)+" ИЗ "+str(len(parts))+
                        " ГЛАВЫ "+key+"\n"
                        "ЗАДАНИЕ: "+p.premise[:900]+
                        attention_review_contract(p,p.scenes[ch["scene_index"]],{
                            "stage_name":ch.get("stage_name","development"),
                            "reveal_level":ch.get("reveal_level","none")})+
                        "\nПЛАН РАЗГАДОК В ЭТОЙ ГЛАВЕ: "+reveal_checks+
                        "\nПРЕДЫДУЩАЯ ГЛАВА (КОНЕЦ): "+previous+
                        "\nФРАГМЕНТ ГЛАВЫ:\n"+part)
                    report=await call_model(job,job["critic_model"],prompt,num_predict=1100)
                    store(job["id"],"literary_qa",part_key,part_sha,{"report":report})
                    reports.append(report)
                combined="\n\n".join(
                    "Фрагмент "+str(i+1)+"/"+str(len(reports))+":\n"+rep
                    for i,rep in enumerate(reports))
                last+=store(job["id"],"literary_qa",key,
                            cv.fingerprint(ch["text"]),{"report":combined,
                                                       "windows":len(parts)})
            if lease.is_set():
                raise ValueError("GPU lease lost during literary QA")
        return cv.fingerprint(last)

    async def editorial(job: dict) -> str:
        chapters=book_rows(job)
        summary=""
        async with gpu_session(job["id"],"editorial") as lease:
            await check_gpu_models(job["critic_model"],job)
            for ch in chapters:
                if state(job["id"])["cancel_requested"]:
                    raise ValueError("Автор отменил конвейер")
                key=str(ch["scene_index"]+1)
                old_source=cv.fingerprint(ch["text"])
                try:
                    old=get(job["id"],"editorial_proposals",key)
                    if old["source_sha"]==old_source:
                        summary+=old["content_sha"]
                        continue
                except ValueError:
                    pass
                candidate=ch["text"]
                applied=[]
                parts=review_windows(ch["text"])
                for part_index,part in enumerate(parts,1):
                    part_key=key+"."+str(part_index)
                    critique=json.loads(
                        get(job["id"],"literary_qa",part_key)["body"])["report"]
                    prompt=(
                        "Ты редактор книги. Исправления ТОЛЬКО в виде JSON объекта с полем edits "
                        "(не более 5 объектов). У каждого объекта original и replacement и reason. "
                        "original — точная непрерывная цитата ИМЕННО из приведённого фрагмента, "
                        "replacement — литературная замена, не меняющая утверждённые факты. "
                        "Предлагай только доказуемые исправления, без новых персонажей и событий. "
                        "Если нет точных правок, верни {\"edits\":[]}. "
                        "Не пиши объяснений вне JSON. Редактируй полноценную прозу, не сценарий. "
                        "Отдельно устраняй смысловые повторы и пустой диалог. "
                        "\nЧАСТЬ "+str(part_index)+" ИЗ "+str(len(parts))+
                        "\nОТЧЁТ КРИТИКА:\n"+critique[:3900]+
                        "\nТЕКСТ ФРАГМЕНТА:\n"+part)
                    raw=await call_model(job,job["critic_model"],prompt,
                                         num_predict=1700,temperature=0.08)
                    # Never trust model-proposed patches blindly; they must
                    # match a unique original span and not worsen loop QA.
                    try:
                        cleaned=re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$","",
                                       raw.strip(),flags=re.IGNORECASE)
                        raw_obj=json.loads(cleaned)
                        items=raw_obj.get("edits",[])
                        if not isinstance(items,list):
                            raise ValueError("Invalid edits array")
                    except (ValueError,TypeError,AttributeError) as exc:
                        raise ValueError("Редактор вернул некорректный JSON: часть "+
                                         part_key+"; глава не принималась") from exc
                    for item in items[:5]:
                        if not isinstance(item,dict):
                            continue
                        original=item.get("original","")
                        replacement=item.get("replacement","")
                        if (not isinstance(original,str) or not isinstance(replacement,str)
                            or len(original)<35 or len(original)>1600
                            or not replacement.strip() or len(replacement)>2500
                            or candidate.count(original)!=1 or original==replacement):
                            continue
                        test=candidate.replace(original,replacement,1)
                        if len(quality_findings(test,limit=10))>len(
                            quality_findings(candidate,limit=10)):
                            continue
                        candidate=test
                        applied.append({"original":original,"replacement":replacement,
                                        "reason":str(item.get("reason",""))[:500]})
                body={"original_sha":old_source,
                      "candidate_sha":cv.fingerprint(candidate),
                      "candidate_text":candidate,
                      "edits":applied,
                      "requires_author_approval":True,
                      "note":"Предложение не меняет рукопись и исходный черновик."}
                summary+=store(job["id"],"editorial_proposals",key,old_source,body)
            # Ollama is shared with other projects: unload only with author
            # permission. The next image role must fail closed if not allowed.
            if job["allow_shared_unload"]:
                await unload_our_model(job["critic_model"],True)
            if lease.is_set():
                raise ValueError("GPU lease lost during editorial QA")
        return cv.fingerprint(summary)

    async def images(job: dict) -> str:
        if job["image_mode"]=="none":
            return store(job["id"],"illustration","manifest","none",
                         {"assets":[],"mode":"none"})
        if not (await image_status()).get("available"):
            raise ValueError("ComfyUI/SDXL не готов: открой вкладку Иллюстрации и установи модель")
        p=project(job)
        chapters=book_rows(job)
        targets=[(COVER_SCENE_ID,"cover",p.title+"; "+p.premise[:750])]
        if job["image_mode"]=="cover_chapters":
            for scene in p.scenes[:min(39,len(p.scenes))]:
                text=(scene.anchor or scene.hook or scene.text[:450]).strip()
                if text:
                    targets.append((scene.id,"start",scene.title+"; "+text[:600]))
        created=[]
        async with gpu_session(job["id"],"illustration") as lease:
            await check_gpu_models("",job)
            if await ps():
                raise ValueError("Ollama по-прежнему держит VRAM; изображение не запускаем")
            for scene_id,placement,description in targets:
                if state(job["id"])["cancel_requested"]:
                    raise ValueError("Конвейер остановлен автором")
                with conn() as db:
                    exists=db.execute("""SELECT id FROM illustrations WHERE project_id=?
                                         AND scene_id=? AND placement=?
                                         ORDER BY created_at DESC LIMIT 1""",
                                      (p.id,scene_id,placement)).fetchone()
                if exists:
                    created.append({"scene_id":scene_id,"asset_id":exists["id"],"reused":True})
                    continue
                art_prompt=(
                    "One finished BOOK ILLUSTRATION. "+job["image_style"][:350]+
                    ". Composition with coherent environment and material details. "
                    "No letters, no text, no captions, no watermark. "
                    "Exact story subject: "+description[:850])
                png,checkpoint=await generate_picture(art_prompt[:2400])
                with conn() as db:
                    art=save_picture(data_dir,db,project_id=p.id,scene_id=scene_id,
                                     caption=description[:220],content=png,
                                     created_at=cv.now(),placement=placement)
                created.append({"scene_id":scene_id,"asset_id":art["id"],
                                "checkpoint":checkpoint,"prompt_sha":cv.fingerprint(art_prompt)})
            if job["allow_shared_unload"]:
                free=await free_cached_models()
            else:
                free="not_requested"
            if lease.is_set():
                raise ValueError("GPU lease lost during ComfyUI task")
        return store(job["id"],"illustration","manifest",
                     cv.fingerprint(job["image_mode"]+job["image_style"]),
                     {"assets":created,"comfy_free":free})

    def file_atomically(job_id: str, filename: str, content: bytes) -> dict:
        if not cv.ID.fullmatch(job_id) or "/" in filename or "\\" in filename:
            raise ValueError("Invalid path")
        folder=data_dir/"production"/job_id
        if folder.is_symlink():
            raise ValueError("Unsafe artifact folder")
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/filename
        digest=hashlib.sha256(content).hexdigest()
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError("Existing proof has different content; do not overwrite")
        else:
            temporary=folder/("."+filename+"."+uuid.uuid4().hex+".tmp")
            try:
                with temporary.open("xb") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary,path)
            finally:
                temporary.unlink(missing_ok=True)
        return {"file":filename,"sha256":digest,"bytes":len(content)}

    async def proof(job: dict) -> str:
        p=project(job).model_copy(deep=True)
        written={ch["scene_id"]:ch["text"] for ch in book_rows(job)}
        for scene in p.scenes:
            scene.text=written[scene.id]
        with conn() as db:
            arts=env["export_illustrations"](data_dir,db,p.id)
        pdf=manuscript_pdf(p,illustrations=arts,
                           draft_notice="ЧЕРНОВИК ИИ — ПРЕДПРОСМОТР — НЕ ДЛЯ ПУБЛИКАЦИИ")
        epub=manuscript_epub(p,illustrations=arts)
        files=[file_atomically(job["id"],"proof-draft-a5.pdf",pdf),
               file_atomically(job["id"],"proof-draft.epub",epub)]
        return store(job["id"],"proof","manifest",digest_chapters(book_rows(job)),
                     {"files":files,"pdfx_certified":False,
                      "note":"Не принят автором. PDF A5 — проба, не PDF/X."})

    async def archive(job: dict) -> str:
        p=project(job)
        rows=book_rows(job)
        if not all(x["accepted_at"] for x in rows):
            raise ValueError("Нельзя архивировать не принятую автором книгу")
        decision=json.loads(get(job["id"],"author_review","decisions")["body"])
        expected=decision.get("accepted_chapter_sha",{})
        for scene in p.scenes:
            if expected.get(scene.id)!=cv.fingerprint(scene.text):
                raise ValueError("Автор изменил главу после приёмки; выпуск отменён до нового утверждения")
        with conn() as db:
            arts=env["export_illustrations"](data_dir,db,p.id)
        files=[]
        for filename,content in (
            ("approved-a5.pdf",manuscript_pdf(p,illustrations=arts)),
            ("approved.epub",manuscript_epub(p,illustrations=arts)),
            ("approved.docx",manuscript_docx(p,illustrations=arts)),
            ("approved.fb2",manuscript_fb2(p,illustrations=arts)),
        ):
            files.append(file_atomically(job["id"],filename,content))
        # An opt-in local corpus package of accepted material. Do not train
        # the writer automatically; the rights/provenance and quality of this
        # small sample must be reviewed before any future fine-tune.
        examples=[]
        for i,scene in enumerate(p.scenes,1):
            examples.append(json.dumps({
                "project_id":p.id,"chapter":i,"scene_id":scene.id,
                "title":scene.title,"genre":p.genre,
                "accepted_text":scene.text,
                "source_sha256":cv.fingerprint(scene.text),
                "status":"author_approved","usage":"local_RAG_reference",
                "training_opt_in":False
            },ensure_ascii=False,sort_keys=True))
        corpus=("\n".join(examples)+"\n").encode("utf-8")
        files.append(file_atomically(job["id"],"approved-corpus.jsonl",corpus))
        snapshot=edition_snapshot(data_dir,p,[
            {"scene_id":s.id,"title":s.title,"text":s.text,"complete":True}
            for s in p.scenes],source_kind="manuscript",label="Одобренный том · конвейер")
        with conn() as db:
            env["sync_approved"](db,p)
        result={"files":files,"edition_id":snapshot["id"],
                "canon_sha":cv.fingerprint(p.model_dump_json()),
                "approved_words":sum(env["book_words"](s.text) for s in p.scenes),
                "corpus":"Approved texts indexed in RAG, no model fine-tuning",
                "temp_policy":"No foreign Ollama/ComfyUI cache or user files touched",
                "pdfx_certified":False}
        return store(job["id"],"archive","manifest",result["canon_sha"],result)

    async def run(job_id: str):
        try:
            while True:
                job=state(job_id)
                if job["state"] in ("cancelled","ready","waiting_author","error","interrupted"):
                    return
                step=cv.STAGES[job["current_step"]]
                if step=="author_review":
                    with conn() as db:
                        cv.pause(db,job_id,"Прочти черновой PDF, QA и редакторские предложения; выбери версии глав.")
                    return
                rows=book_rows(job)
                input_sha=digest_chapters(rows)
                with conn() as db:
                    # Do not restart a running stage after partial HTTP requests.
                    cv.stage_start(db,job_id,step,input_sha)
                result={"book":book_stage,"mechanical_qa":mechanical,
                        "literary_qa":literary,"editorial_proposals":editorial,
                        "illustration":images,"proof":proof,"archive":archive}[step]
                digest=await result(job)
                with conn() as db:
                    cv.stage_done(db,job_id,step,digest)
        except asyncio.CancelledError:
            with conn() as db:
                cv.fail(db,job_id,"Прервано при остановке сервера; результаты этапов сохранены.")
            raise
        except Exception as exc:
            with conn() as db:
                cv.fail(db,job_id,str(exc)[:1800])

    def launch(job_id: str):
        task=asyncio.create_task(run(job_id))
        background.add(task)
        task.add_done_callback(background.discard)

    @api.post("/projects/{project_id}/conveyor",status_code=202)
    async def start_conveyor(project_id: str, request: ConveyorRequest):
        # Automated role swaps affect the shared Ollama daemon. Require
        # explicit opt-in before even creating the writer job.
        if not request.allow_shared_unload:
            raise HTTPException(422,
                "Для автоматического конвейера разреши выгрузку выбранных моделей Ollama. "
                "Чужие модели не выгружаются.")
        env["get_project"](project_id)
        with conn() as db:
            existing=db.execute("""SELECT id FROM production_jobs WHERE project_id=?
               AND state IN ('queued','running','waiting_author','interrupted','error')
               ORDER BY created_at DESC LIMIT 1""",(project_id,)).fetchone()
            if existing:
                return {**cv.status(db,existing["id"]),"reused":True}
            if image_operation_active(db):
                raise HTTPException(409,"Другая задача заняла GPU")
        # This returns the existing durable book job if it is unfinished.
        try:
            book=await env["start_full_book"](env["BookRequest"](
                project_id=project_id,model=request.writer_model))
            actual=book["model"]
            if actual!=request.writer_model:
                raise HTTPException(409,"Существующий черновик написан другой моделью: "+actual)
            with conn() as db:
                db.execute("BEGIN IMMEDIATE")
                created=cv.create(db,job_id=uuid.uuid4().hex,project_id=project_id,
                    book_job_id=book["id"],writer_model=actual,
                    critic_model=request.critic_model,image_mode=request.image_mode,
                    image_style=request.image_style,
                    allow_shared_unload=request.allow_shared_unload)
            launch(created["id"])
            return {**created,"reused":False}
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc

    @api.get("/projects/{project_id}/conveyor")
    def list_conveyor(project_id: str):
        env["get_project"](project_id)
        with conn() as db:
            rows=db.execute("""SELECT id FROM production_jobs WHERE project_id=?
                               ORDER BY created_at DESC LIMIT 20""",(project_id,)).fetchall()
            return [cv.status(db,x["id"]) for x in rows]

    @api.get("/conveyor/jobs/{job_id}")
    def conveyor_status(job_id: str):
        try:return state(job_id)
        except ValueError as exc:raise HTTPException(404,str(exc)) from exc

    @api.get("/conveyor/jobs/{job_id}/artifacts/{stage}/{key}")
    def artifact_detail(job_id: str,stage: str,key: str):
        try:
            if stage not in cv.STAGES:
                raise ValueError("Unknown stage")
            item=get(job_id,stage,key)
            return {"stage":stage,"key":key,"source_sha":item["source_sha"],
                    "content_sha":item["content_sha"],
                    "body":json.loads(item["body"])}
        except (ValueError,json.JSONDecodeError) as exc:
            raise HTTPException(404,str(exc)) from exc

    @api.get("/conveyor/jobs/{job_id}/files/{filename}")
    def download_conveyor_file(job_id: str,filename: str):
        if not cv.ID.fullmatch(job_id) or filename not in (
            "proof-draft-a5.pdf","proof-draft.epub",
            "approved-a5.pdf","approved.epub","approved.docx","approved.fb2",
            "approved-corpus.jsonl"):
            raise HTTPException(404,"Недопустимый файл")
        job=state(job_id)
        if filename.startswith("approved") and job["state"]!="ready":
            raise HTTPException(409,"Книга ещё не утверждена")
        path=data_dir/"production"/job_id/filename
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404,"Файл ещё не создан")
        return FileResponse(path,filename=filename,
            headers={"Cache-Control":"no-store"})

    @api.post("/conveyor/jobs/{job_id}/resume",status_code=202)
    async def resume_conveyor(job_id: str,request: ResumeRequest):
        job=state(job_id)
        if job["state"] in ("queued","running"):
            return job
        if job["state"]=="waiting_author" and cv.STAGES[job["current_step"]]=="author_review":
            raise HTTPException(409,"Используй отдельное подтверждение редакции")
        if cv.STAGES[job["current_step"]]=="book":
            with conn() as db:
                book=env["_book_record"](db,job["book_job_id"])
            if book["status"] in ("error","interrupted"):
                await env["resume_full_book"](job["book_job_id"])
        try:
            with conn() as db:
                resumed=cv.resume(db,job_id)
            launch(job_id)
            return resumed
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc

    @api.post("/conveyor/jobs/{job_id}/cancel")
    def cancel_conveyor(job_id: str):
        with conn() as db:
            try:return cv.cancel(db,job_id)
            except ValueError as exc:raise HTTPException(404,str(exc)) from exc

    @api.post("/conveyor/jobs/{job_id}/approve",status_code=202)
    def approve_conveyor(job_id: str,request: AuthorDecision):
        """Author selects exact chapter versions; the sole canonical write."""
        with conn() as db:
            db.execute("BEGIN IMMEDIATE")
            job=cv.status(db,job_id)
            if job["state"]!="waiting_author" or cv.STAGES[job["current_step"]]!="author_review":
                raise HTTPException(409,"Конвейер не ожидает решения автора")
            written=env["chapters_for_job"](db,job["book_job_id"])
            original={c["scene_id"]:c for c in written}
            if set(request.selected_edits)-set(original):
                raise HTTPException(422,"Неизвестная глава в решениях автора")
            row=db.execute("SELECT data FROM projects WHERE id=?", (job["project_id"],)).fetchone()
            p=env["Project"].model_validate_json(row["data"])
            if [s.id for s in p.scenes]!=[c["scene_id"] for c in written]:
                raise HTTPException(409,"Каркас изменился")
            if not all(c["complete"] for c in written):
                raise HTTPException(409,"Книга не завершена")
            if any(c["accepted_at"] for c in written):
                raise HTTPException(409,"Черновик ранее уже был принят")
            for s,c in zip(p.scenes,written):
                if env["fingerprint"](s.text)!=c["original_sha256"]:
                    raise HTTPException(409,"Рукопись изменена; автоматическое принятие запрещено")
            approved={}
            for index,(s,c) in enumerate(zip(p.scenes,written),1):
                target=c["text"]
                if request.selected_edits.get(s.id,False):
                    item=cv.artifact(db,job_id,"editorial_proposals",str(index))
                    proposal=json.loads(item["body"])
                    if proposal["original_sha"]!=cv.fingerprint(target):
                        raise HTTPException(409,"Предложение устарело: "+s.title)
                    target=proposal["candidate_text"]
                    if cv.fingerprint(target)!=proposal["candidate_sha"]:
                        raise HTTPException(409,"Некорректная контрольная сумма редакции")
                approved[s.id]=target
            cv.resume(db,job_id,author_approved=True)
            cv.stage_start(db,job_id,"author_review",digest_chapters(written))
            for s,c in zip(p.scenes,written):
                old=s.text
                s.text=approved[s.id]
                revision=uuid.uuid4().hex
                db.execute("""INSERT INTO author_text_revisions
                    (id,project_id,scene_id,job_id,previous_text,accepted_text,created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (revision,p.id,s.id,"conveyor:"+job_id,old,s.text,cv.now()))
                db.execute("""UPDATE ai_book_chapters SET accepted_at=?
                    WHERE job_id=? AND scene_id=? AND accepted_at IS NULL""",
                    (cv.now(),job["book_job_id"],s.id))
                for block in s.blocks:
                    if block.accepted_job_id and old!=s.text:
                        block.evidence_stale=True
            p.updated_at=cv.now()
            db.execute("UPDATE projects SET data=?,updated_at=? WHERE id=?",
                       (p.model_dump_json(),p.updated_at,p.id))
            digest=cv.put_artifact(db,job_id,"author_review","decisions",
                digest_chapters(written),
                {"selected_edits":request.selected_edits,
                 "accepted_chapter_sha":{sid:cv.fingerprint(t) for sid,t in approved.items()},
                 "approved_at":cv.now()})
            cv.stage_done(db,job_id,"author_review",digest)
        launch(job_id)
        return state(job_id)
