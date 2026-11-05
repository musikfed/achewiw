"""Immutable draft snapshots and deterministic pre-publication QA for one-author ACHEWIW.

A snapshot is an author-reviewable, versioned COPY in work.sqlite3. Never
replace manuscript or approve AI claims in canon automatically.
"""
from __future__ import annotations
from datetime import datetime, timezone
from difflib import unified_diff
import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from editor_workspace import _open
from book_generation_core import count_words
from story_memory import audit_graph

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def initialize(data_dir: Path):
    with _open(data_dir / "work.sqlite3") as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS work_book_versions(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_kind TEXT NOT NULL,
            source_job_id TEXT NOT NULL DEFAULT '', digest TEXT NOT NULL,
            label TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(project_id,source_kind,source_job_id,digest)
        );
        CREATE INDEX IF NOT EXISTS idx_work_versions ON work_book_versions(project_id,created_at);
        CREATE TABLE IF NOT EXISTS work_version_reviews(
            version_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            report_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)

def snapshot(data_dir: Path, project, chapters: list[dict], *,
             source_kind: str, source_job_id: str = "", label: str = "") -> dict:
    if source_kind not in ("draft","manuscript"):
        raise ValueError("Неподдерживаемый источник редакции")
    if not chapters or not any(ch.get("text","").strip() for ch in chapters):
        raise ValueError("Нет написанного текста для сохранения редакции")
    if not 0 <= len(label) <= 160:
        raise ValueError("Название редакции слишком длинное")
    items=[]
    for i,ch in enumerate(chapters):
        body=str(ch.get("text") or "")
        if len(body)>150000:raise ValueError("Текст одной главы превышает лимит")
        items.append({
          "scene_id":str(ch["scene_id"]),"index":i+1,
          "title":str(ch.get("title") or ("Глава "+str(i+1)))[:200],
          "text":body,"words":count_words(body),
          "complete":bool(ch.get("complete",False))
        })
    payload={"title":project.title,"chapters":items,
             "target":project.target_length,"source_kind":source_kind,
             "job_id":source_job_id}
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    fingerprint=digest(encoded)
    initialize(data_dir)
    with _open(data_dir/"work.sqlite3") as db:
        row=db.execute("""SELECT id FROM work_book_versions WHERE project_id=? AND
             source_kind=? AND source_job_id=? AND digest=?""",
             (project.id,source_kind,source_job_id,fingerprint)).fetchone()
        if row:
            return {**get_version(data_dir,project.id,row["id"],include_text=False),"reused":True}
        identifier=uuid.uuid4().hex
        if not label.strip():
            count=db.execute("SELECT count(*) FROM work_book_versions WHERE project_id=?",
                             (project.id,)).fetchone()[0]+1
            label=f"Редакция {count} — "+("черновик ИИ" if source_kind=="draft" else "рукопись автора")
        db.execute("""INSERT INTO work_book_versions
          (id,project_id,source_kind,source_job_id,digest,label,payload_json,created_at)
          VALUES(?,?,?,?,?,?,?,?)""",
          (identifier,project.id,source_kind,source_job_id,fingerprint,label.strip(),encoded,stamp()))
    return {**get_version(data_dir,project.id,identifier,include_text=False),"reused":False}

def get_version(data_dir: Path, project_id: str, version_id: str, *, include_text: bool = True):
    if not re.fullmatch(r"[0-9a-f]{32}",version_id):
        raise ValueError("Неизвестная редакция")
    with _open(data_dir/"work.sqlite3") as db:
        row=db.execute("SELECT * FROM work_book_versions WHERE id=? AND project_id=?",
                       (version_id,project_id)).fetchone()
    if row is None:raise ValueError("Редакция этой книги не найдена")
    result={k:row[k] for k in ("id","project_id","source_kind","source_job_id","digest","label","created_at")}
    payload=json.loads(row["payload_json"])
    result["chapters"]=payload["chapters"] if include_text else [
      {k:ch[k] for k in ("scene_id","index","title","words","complete")}
      for ch in payload["chapters"]]
    result["total_words"]=sum(ch["words"] for ch in payload["chapters"])
    result["all_complete"]=all(ch["complete"] for ch in payload["chapters"])
    return result

def versions(data_dir: Path, project_id: str):
    initialize(data_dir)
    with _open(data_dir/"work.sqlite3") as db:
        rows=db.execute("SELECT id FROM work_book_versions WHERE project_id=? ORDER BY created_at DESC,id DESC",
                        (project_id,)).fetchall()
    return [get_version(data_dir,project_id,r["id"],include_text=False) for r in rows]

def compare(data_dir: Path, project_id: str, left: str, right: str):
    a=get_version(data_dir,project_id,left)
    b=get_version(data_dir,project_id,right)
    l={x["scene_id"]:x for x in a["chapters"]}
    r={x["scene_id"]:x for x in b["chapters"]}
    rows=[]
    for scene_id in dict.fromkeys([*l,*r]):
        before=l.get(scene_id,{});after=r.get(scene_id,{})
        if before.get("text")==after.get("text"):continue
        lines=list(unified_diff(
            before.get("text","").splitlines(),after.get("text","").splitlines(),
            fromfile=a["label"],tofile=b["label"],lineterm="",n=2))
        rows.append({
            "scene_id":scene_id,"title":after.get("title") or before.get("title"),
            "words_before":before.get("words",0),
            "words_after":after.get("words",0),
            "diff_excerpt":"\n".join(lines)[:4500],
            "truncated":len("\n".join(lines))>4500
        })
    return {"left":a["id"],"right":b["id"],"changed_chapters":len(rows),"changes":rows}

def _duplicate_paragraphs(chapters):
    seen={};problems=[]
    for ch in chapters:
        for part in re.split(r"\n\s*\n",ch["text"]):
            words=re.findall(r"[^\W_]+",part.casefold(),re.UNICODE)
            if len(words)<25:continue
            norm=" ".join(words)
            if norm in seen:
                problems.append({"severity":"warning","kind":"repeated_paragraph",
                  "chapter":ch["index"],
                  "detail":f"Повтор длинного абзаца из главы {seen[norm]}"})
                if len(problems)>=12:return problems
            else:seen[norm]=ch["index"]
    return problems

def review(data_dir: Path, canon: sqlite3.Connection, project, version_id: str,
           artworks: list[dict], puzzles: list[dict] | None = None):
    version=get_version(data_dir,project.id,version_id)
    issues=[]
    def issue(level,kind,detail,chapter=None):
        issues.append({"severity":level,"kind":kind,"detail":detail,"chapter":chapter})
    for ch in version["chapters"]:
        if not ch["text"].strip():issue("warning","empty_chapter",f"Глава «{ch['title']}» ещё не написана",ch["index"])
        if not ch["complete"] and version["source_kind"]=="draft":
            issue("warning","incomplete_chapter",f"Черновик главы «{ch['title']}» ещё не закончен",ch["index"])
    issues.extend(_duplicate_paragraphs(version["chapters"]))
    if len(version["chapters"])!=len(project.scenes):
        issue("warning","outline_mismatch","Число глав не совпадает с каркасом")
    graph=audit_graph(canon,project.id)
    for g in graph.get("issues",[]):
        issue("warning","graph_"+g["kind"],g["detail"])
    for mystery in graph.get("open_mysteries",[]):
        issue("review","open_mystery","Не указано раскрытие загадки: "+mystery["title"])
    if not getattr(project,"author_name","").strip():issue("publication","missing_author","Не указан автор")
    if not getattr(project,"publication_year",None):issue("publication","missing_year","Не указан год выпуска")
    cover=any(x["placement"]=="cover" for x in artworks)
    if not cover:issue("publication","missing_cover","Нет обложки; PDF/EPUB допускают выпуск без неё")
    scene_ids={s.id for s in project.scenes}
    for art in artworks:
        if art["placement"]!="cover" and art["scene_id"] not in scene_ids:
            issue("warning","orphan_image","Рисунок ссылается на удалённую главу")
    for puzzle in puzzles or []:
        if puzzle["clue_scene_id"] not in scene_ids or puzzle["answer_scene_id"] not in scene_ids:
            issue("warning","broken_puzzle","Визуальная загадка ссылается на удалённую главу")
        elif not any(x["id"]==puzzle["illustration_id"] for x in artworks):
            issue("warning","missing_puzzle_image","Иллюстрация для визуальной загадки отсутствует")
    report={
        "version_id":version_id,"title":version["label"],
        "source_kind":version["source_kind"],
        "words":version["total_words"],
        "chapters":len(version["chapters"]),
        "graph_nodes":graph["nodes"],"graph_edges":graph["edges"],
        "issues":issues,
        "qa_result":"needs_review" if any(i["severity"] in ("warning","publication") for i in issues) else "no_structural_issues_found",
        "note":"Автоматическая структурная проверка не гарантирует литературной логики или фактической достоверности."
    }
    with _open(data_dir/"work.sqlite3") as work:
        work.execute("""INSERT INTO work_version_reviews
          (version_id,project_id,report_json,created_at) VALUES(?,?,?,?)
          ON CONFLICT(version_id) DO UPDATE SET
          report_json=excluded.report_json,created_at=excluded.created_at""",
          (version_id,project.id,json.dumps(report,ensure_ascii=False),stamp()))
    return report

def propose_graph(data_dir: Path, project, version_id: str):
    """Traceable candidates, NOT approved canonical claims or AI-invented causes."""
    version=get_version(data_dir,project.id,version_id)
    by_id={x.id:x for x in project.scenes}
    result=[]
    with _open(data_dir/"work.sqlite3") as db:
        for chapter in version["chapters"]:
            if not chapter["text"].strip():continue
            scene=by_id.get(chapter["scene_id"])
            if scene is None:continue
            evidence=chapter["text"][:500].strip()
            candidates=[{
              "kind":"event","title":"События главы: "+chapter["title"],
              "statement":"Фрагмент черновика для авторской проверки: "+evidence[:400],
              "scene_id":chapter["scene_id"],"scene_index":chapter["index"]-1
            }]
            for who in scene.active_objects[:8]:
                if not who.strip():continue
                candidates.append({
                  "kind":"character","title":who.strip()[:160],
                  "statement":"Автор указал в активных объектах главы «"+chapter["title"]+"». Требует проверки роли и состояния в тексте.",
                  "scene_id":chapter["scene_id"],"scene_index":chapter["index"]-1
                })
            for c in candidates:
                raw=json.dumps(c,ensure_ascii=False,sort_keys=True)
                stable=hashlib.sha256((version["digest"]+raw).encode()).hexdigest()[:32]
                db.execute("""INSERT OR IGNORE INTO work_proposals
                    (id,project_id,branch_id,source_revision,kind,payload_json,status,created_at)
                    VALUES(?,?,'main',?,'story_node',?,'proposed',?)""",
                    (stable,project.id,version["digest"],raw,stamp()))
                result.append({"id":stable,**c})
    return {"version_id":version_id,"suggestions":result,
            "note":"Кандидаты из конкретных фрагментов и авторских карточек, не установленные факты."}

def suggestions(data_dir: Path,project_id: str,version_digest: str):
    with _open(data_dir/"work.sqlite3") as db:
        rows=db.execute("""SELECT * FROM work_proposals WHERE project_id=? AND
          branch_id='main' AND source_revision=? AND kind='story_node'
          ORDER BY created_at,id""",(project_id,version_digest)).fetchall()
    return [{"id":r["id"],"status":r["status"],**json.loads(r["payload_json"])} for r in rows]

def mark_suggestion(data_dir: Path,project_id: str,source_revision: str,
                    suggestion_id: str,status: str):
    if status not in ("approved","rejected"):raise ValueError("Неверное решение")
    with _open(data_dir/"work.sqlite3") as db:
        row=db.execute("""SELECT * FROM work_proposals WHERE id=? AND project_id=?
           AND source_revision=? AND kind='story_node'""",
           (suggestion_id,project_id,source_revision)).fetchone()
        if not row:raise ValueError("Предложение отсутствует")
        if row["status"]!="proposed":raise ValueError("Предложение уже обработано")
        db.execute("UPDATE work_proposals SET status=? WHERE id=?",(status,suggestion_id))
        return json.loads(row["payload_json"])
