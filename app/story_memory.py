"""Project-scoped story memory: SQLite FTS5 retrieval plus a validated causal graph.

Designed for a single-user local Studio. There is no separate server, no automatic
AI promotion into canon, and no access between projects or alternative branches.
All facts/edges are author-approved, while unaccepted AI prose is indexed only
as draft evidence to avoid accidentally declaring inventions canonical.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

NODE_KINDS = {"character", "object", "place", "event", "fact", "mystery", "rule"}
EDGE_KINDS = {"causes", "requires", "before", "knows", "holds", "foreshadows", "reveals", "contradicts"}
SOURCE_KINDS = {"draft", "approved", "synopsis"}
STOPWORDS = {"это", "как", "для", "когда", "что", "его", "она", "все", "или", "были", "было",
             "свою", "этот", "также", "только", "после", "перед", "через", "глава"}


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_memory(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS story_branches (
        project_id TEXT NOT NULL, id TEXT NOT NULL, parent_id TEXT,
        fork_scene_id TEXT, name TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (project_id,id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS story_nodes (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, branch_id TEXT NOT NULL,
        kind TEXT NOT NULL, title TEXT NOT NULL, statement TEXT NOT NULL,
        scene_id TEXT, scene_index INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'approved', created_at TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS story_nodes_project "
               "ON story_nodes(project_id,branch_id,scene_index)")
    db.execute("""CREATE TABLE IF NOT EXISTS story_edges (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, branch_id TEXT NOT NULL,
        source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        UNIQUE(project_id,branch_id,source_id,target_id,relation))""")
    db.execute("CREATE INDEX IF NOT EXISTS story_edges_project "
               "ON story_edges(project_id,branch_id)")
    db.execute("""CREATE TABLE IF NOT EXISTS story_chunks (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, branch_id TEXT NOT NULL,
        job_id TEXT NOT NULL, source TEXT NOT NULL, scene_id TEXT NOT NULL,
        scene_index INTEGER NOT NULL, chunk_index INTEGER NOT NULL,
        content TEXT NOT NULL, digest TEXT NOT NULL,
        UNIQUE(project_id,branch_id,job_id,source,scene_id,chunk_index))""")
    db.execute("CREATE INDEX IF NOT EXISTS story_chunks_scope "
               "ON story_chunks(project_id,branch_id,scene_index,job_id,source)")
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS story_chunks_fts USING fts5("
                   "chunk_id UNINDEXED, content, tokenize='unicode61')")
    except sqlite3.OperationalError:
        # Some embedded SQLite distributions have no FTS5. LIKE retrieval
        # remains available; no destructive migration or external dependency.
        pass


def ensure_main(db: sqlite3.Connection, project_id: str) -> None:
    db.execute("INSERT OR IGNORE INTO story_branches "
               "(project_id,id,parent_id,fork_scene_id,name,created_at) "
               "VALUES (?,'main',NULL,NULL,'Основная история',?)",
               (project_id, stamp()))


def create_branch(db: sqlite3.Connection, project_id: str, name: str,
                  parent_id: str = "main", fork_scene_id: str | None = None) -> dict:
    ensure_main(db, project_id)
    if not 1 <= len(name.strip()) <= 120:
        raise ValueError("Название ветки: от 1 до 120 символов")
    parent = db.execute("SELECT 1 FROM story_branches WHERE project_id=? AND id=?",
                        (project_id, parent_id)).fetchone()
    if not parent:
        raise ValueError("Родительская сюжетная ветка не найдена")
    branch_id = uuid.uuid4().hex
    db.execute("INSERT INTO story_branches VALUES(?,?,?,?,?,?)",
               (project_id, branch_id, parent_id, fork_scene_id, name.strip(), stamp()))
    return {"id":branch_id, "name":name.strip(), "parent_id":parent_id,
            "fork_scene_id":fork_scene_id}


def branches(db: sqlite3.Connection, project_id: str) -> list[dict]:
    ensure_main(db, project_id)
    return [dict(x) for x in db.execute(
        "SELECT * FROM story_branches WHERE project_id=? ORDER BY created_at,id",
        (project_id,)).fetchall()]


def _branch_exists(db: sqlite3.Connection, project_id: str, branch: str) -> bool:
    ensure_main(db, project_id)
    return bool(db.execute("SELECT 1 FROM story_branches WHERE project_id=? AND id=?",
                           (project_id, branch)).fetchone())


def add_node(db: sqlite3.Connection, project_id: str, branch: str, kind: str,
             title: str, statement: str, scene_id: str | None,
             scene_index: int) -> dict:
    if not _branch_exists(db, project_id, branch):
        raise ValueError("Ветка истории не найдена")
    if kind not in NODE_KINDS or not 1 <= len(title.strip()) <= 160:
        raise ValueError("Недопустимый тип или название узла")
    if not 1 <= len(statement.strip()) <= 2000:
        raise ValueError("Нужен конкретный факт или формулировка события, до 2000 символов")
    if type(scene_index) is not int or not 0 <= scene_index <= 100:
        raise ValueError("Неверный порядковый номер главы")
    node = dict(id=uuid.uuid4().hex, project_id=project_id, branch_id=branch,
                kind=kind, title=title.strip(), statement=statement.strip(),
                scene_id=scene_id, scene_index=scene_index, status="approved",
                created_at=stamp())
    db.execute("""INSERT INTO story_nodes
        (id,project_id,branch_id,kind,title,statement,scene_id,scene_index,status,created_at)
        VALUES (:id,:project_id,:branch_id,:kind,:title,:statement,:scene_id,:scene_index,:status,:created_at)""",
        node)
    return node


def nodes(db: sqlite3.Connection, project_id: str, branch: str = "main") -> list[dict]:
    return [dict(x) for x in db.execute(
        "SELECT * FROM story_nodes WHERE project_id=? AND branch_id=? "
        "ORDER BY scene_index,created_at,id", (project_id, branch)).fetchall()]


def add_edge(db: sqlite3.Connection, project_id: str, branch: str,
             source_id: str, target_id: str, relation: str,
             note: str = "") -> dict:
    if relation not in EDGE_KINDS or source_id == target_id:
        raise ValueError("Недопустимая или циклическая связь с самим собой")
    if len(note) > 1000:
        raise ValueError("Пояснение связи не должно превышать 1000 символов")
    rows = db.execute("SELECT id,scene_index FROM story_nodes "
                      "WHERE project_id=? AND branch_id=? AND id IN (?,?)",
                      (project_id, branch, source_id, target_id)).fetchall()
    if len(rows) != 2:
        raise ValueError("Оба узла должны принадлежать этому проекту и одной ветке")
    indices = {row["id"]:row["scene_index"] for row in rows}
    if relation in ("causes", "before", "foreshadows") and indices[source_id] > indices[target_id]:
        raise ValueError("Причина или подсказка не может появиться позже следствия/раскрытия")
    if relation == "requires" and indices[target_id] > indices[source_id]:
        raise ValueError("Необходимый предмет или условие не может появиться позже зависимого события")
    # On causal edges detect any already-existing path back from target to source.
    if relation in ("causes", "requires", "before"):
        stack = [target_id]
        seen = set()
        while stack:
            current = stack.pop()
            if current == source_id:
                raise ValueError("Обнаружен цикл в причинно-временном графе")
            if current in seen:
                continue
            seen.add(current)
            stack.extend(row["target_id"] for row in db.execute(
                "SELECT target_id FROM story_edges WHERE project_id=? AND branch_id=? "
                "AND source_id=? AND relation IN ('causes','requires','before')",
                (project_id,branch,current)))
    entry = dict(id=uuid.uuid4().hex, project_id=project_id, branch_id=branch,
                 source_id=source_id, target_id=target_id, relation=relation,
                 note=note.strip(), created_at=stamp())
    db.execute("""INSERT INTO story_edges
        (id,project_id,branch_id,source_id,target_id,relation,note,created_at)
        VALUES (:id,:project_id,:branch_id,:source_id,:target_id,:relation,:note,:created_at)""",
        entry)
    return entry


def edges(db: sqlite3.Connection, project_id: str, branch: str = "main") -> list[dict]:
    return [dict(x) for x in db.execute(
        "SELECT * FROM story_edges WHERE project_id=? AND branch_id=? ORDER BY created_at,id",
        (project_id,branch)).fetchall()]


def erase_node(db: sqlite3.Connection, project_id: str, branch: str, node_id: str) -> bool:
    db.execute("DELETE FROM story_edges WHERE project_id=? AND branch_id=? "
               "AND (source_id=? OR target_id=?)", (project_id,branch,node_id,node_id))
    result = db.execute("DELETE FROM story_nodes WHERE project_id=? AND branch_id=? AND id=?",
                        (project_id,branch,node_id))
    return result.rowcount == 1


def audit_graph(db: sqlite3.Connection, project_id: str,
                branch: str = "main") -> dict:
    ns = nodes(db, project_id, branch)
    es = edges(db, project_id, branch)
    by_id = {x["id"]:x for x in ns}
    problems = []
    for edge in es:
        a,b = by_id.get(edge["source_id"]),by_id.get(edge["target_id"])
        if a is None or b is None:
            problems.append({"edge_id":edge["id"],"kind":"dangling","detail":"Связь с удалённым узлом"})
        elif edge["relation"] in ("causes","before","foreshadows") and a["scene_index"] > b["scene_index"]:
            problems.append({"edge_id":edge["id"],"kind":"chronology",
                             "detail":a["title"]+" → "+b["title"]})
    # An event or discovered fact reveals a mystery, not the reverse.
    answered = {e["target_id"] for e in es if e["relation"]=="reveals"
                and by_id.get(e["target_id"],{}).get("kind")=="mystery"}
    open_mysteries = [{"id":n["id"],"title":n["title"]} for n in ns
                      if n["kind"]=="mystery" and n["id"] not in answered]
    for revelation in (e for e in es if e["relation"]=="reveals"):
        reveal_event = by_id.get(revelation["source_id"])
        for hint in (e for e in es if e["relation"]=="foreshadows"
                     and e["target_id"]==revelation["target_id"]):
            setup = by_id.get(hint["source_id"])
            if setup and reveal_event and setup["scene_index"] > reveal_event["scene_index"]:
                problems.append({"edge_id":revelation["id"],"kind":"premature_reveal",
                                 "detail":"Подсказка появляется позже раскрытия загадки"})
    return {"nodes":len(ns),"edges":len(es),"issues":problems,
            "open_mysteries":open_mysteries,"branch_id":branch}


def _segments(value: str, max_chars: int = 1350) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n",value) if p.strip()]
    output, current = [], ""
    for paragraph in paras:
        # Protect the index from a single large unbroken paragraph.
        sections = [paragraph[i:i+max_chars] for i in range(0,len(paragraph),max_chars)]
        for section in sections:
            if current and len(current)+len(section)+2 > max_chars:
                output.append(current)
                current=""
            current = current+"\n\n"+section if current else section
    if current:
        output.append(current)
    return output


def _fts_available(db: sqlite3.Connection) -> bool:
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='story_chunks_fts'").fetchone())


def _delete_source(db: sqlite3.Connection, project_id: str, branch: str,
                   job_id: str, source: str, scene_id: str) -> None:
    ids = [row["id"] for row in db.execute(
        "SELECT id FROM story_chunks WHERE project_id=? AND branch_id=? AND "
        "job_id=? AND source=? AND scene_id=?",
        (project_id,branch,job_id,source,scene_id))]
    if ids and _fts_available(db):
        db.executemany("DELETE FROM story_chunks_fts WHERE chunk_id=?",
                       [(item,) for item in ids])
    db.execute("DELETE FROM story_chunks WHERE project_id=? AND branch_id=? "
               "AND job_id=? AND source=? AND scene_id=?",
               (project_id,branch,job_id,source,scene_id))


def index_chunk(db: sqlite3.Connection, project_id: str, branch: str, job_id: str,
                source: str, scene_id: str, scene_index: int,
                chunk_index: int, content: str) -> int:
    if source not in SOURCE_KINDS or not _branch_exists(db,project_id,branch):
        raise ValueError("Недопустимый источник памяти")
    content=content.strip()
    if not content:
        return 0
    uid=uuid.uuid4().hex
    digest=hashlib.sha256(content.encode()).hexdigest()
    row=db.execute("SELECT id,digest FROM story_chunks WHERE project_id=? "
        "AND branch_id=? AND job_id=? AND source=? AND scene_id=? AND chunk_index=?",
        (project_id,branch,job_id,source,scene_id,chunk_index)).fetchone()
    if row and row["digest"]==digest:
        return 0
    if row:
        if _fts_available(db):
            db.execute("DELETE FROM story_chunks_fts WHERE chunk_id=?",(row["id"],))
        db.execute("DELETE FROM story_chunks WHERE id=?",(row["id"],))
    db.execute("""INSERT INTO story_chunks
        (id,project_id,branch_id,job_id,source,scene_id,scene_index,chunk_index,content,digest)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (uid,project_id,branch,job_id,source,scene_id,scene_index,chunk_index,content,digest))
    if _fts_available(db):
        db.execute("INSERT INTO story_chunks_fts(chunk_id,content) VALUES(?,?)",
                   (uid,content))
    return 1


def sync_outline(db: sqlite3.Connection, project) -> None:
    """Mirror author-approved outline cards and their order into graph PLAN nodes.

    A plan card is not a verified world fact. Stable graph IDs follow the
    scene IDs; neither source cards nor author manuscript are modified.
    """
    ensure_main(db,project.id)
    expected=set()
    order=set()
    for i,scene in enumerate(project.scenes):
        uid="outline:"+scene.id
        expected.add(uid)
        synopsis=scene.text[:900] if len(re.findall(r"\w+",scene.text))<=180 else ""
        parts=[p for p in (getattr(scene,"anchor",""),getattr(scene,"hook",""),synopsis) if p.strip()]
        statement="План главы: "+(" | ".join(parts)[:1800] if parts else getattr(scene,"title",scene.id))
        db.execute("""INSERT INTO story_nodes
            (id,project_id,branch_id,kind,title,statement,scene_id,scene_index,status,created_at)
            VALUES(?,?,'main','event',?,?,?,?, 'outline',?)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                statement=excluded.statement,scene_index=excluded.scene_index,
                scene_id=excluded.scene_id""",
            (uid,project.id,getattr(scene,"title",scene.id),statement,scene.id,i,stamp()))
        if i:
            prev="outline:"+project.scenes[i-1].id
            eid="outline-order:"+scene.id
            order.add(eid)
            db.execute("""INSERT OR IGNORE INTO story_edges
                (id,project_id,branch_id,source_id,target_id,relation,note,created_at)
                VALUES(? ,?,'main',?,?,'before','Порядок утверждённого каркаса',?)""",
                (eid,project.id,prev,uid,stamp()))
    # Remove only machine-maintained outline items, never author-entered facts.
    for row in db.execute("SELECT id FROM story_nodes WHERE project_id=? AND "
                          "branch_id='main' AND status='outline'",(project.id,)).fetchall():
        if row["id"] not in expected:
            db.execute("DELETE FROM story_nodes WHERE id=?",(row["id"],))
    for row in db.execute("SELECT id FROM story_edges WHERE project_id=? AND "
                          "branch_id='main' AND id LIKE 'outline-order:%'",(project.id,)).fetchall():
        if row["id"] not in order:
            db.execute("DELETE FROM story_edges WHERE id=?",(row["id"],))


def sync_approved(db: sqlite3.Connection, project) -> int:
    """Re-index author-saved text, not drafts. Never modify the manuscript."""
    ensure_main(db,project.id)
    sync_outline(db,project)
    changed=0
    for i, scene in enumerate(project.scenes):
        source = "synopsis" if len(re.findall(r"\w+",scene.text)) <= 180 else "approved"
        records=_segments(scene.text)
        # Avoid rewriting a stable index on every page refresh.
        existing=db.execute(
            "SELECT source,digest FROM story_chunks WHERE project_id=? AND "
            "branch_id='main' AND job_id='author' AND scene_id=? ORDER BY chunk_index",
            (project.id,scene.id)).fetchall()
        if [row["source"] for row in existing] == [source]*len(records) and \
           [row["digest"] for row in existing] == [hashlib.sha256(x.encode()).hexdigest() for x in records]:
            continue
        for previous in ("approved","synopsis"):
            _delete_source(db,project.id,"main","author",previous,scene.id)
        for j,part in enumerate(records):
            changed+=index_chunk(db,project.id,"main","author",source,scene.id,i,j,part)
    # A deleted scene cannot pollute retrieval.
    ids={s.id for s in project.scenes}
    for row in db.execute("SELECT DISTINCT scene_id,source FROM story_chunks "
                          "WHERE project_id=? AND branch_id='main' AND job_id='author'",
                          (project.id,)).fetchall():
        if row["scene_id"] not in ids:
            _delete_source(db,project.id,"main","author",row["source"],row["scene_id"])
    return changed


def ensure_draft(db: sqlite3.Connection, project_id: str, job_id: str,
                 scene_id: str, scene_index: int, text: str) -> None:
    exists=db.execute("SELECT 1 FROM story_chunks WHERE project_id=? "
                      "AND job_id=? AND source='draft' AND scene_id=? LIMIT 1",
                      (project_id,job_id,scene_id)).fetchone()
    if not exists:
        for i, part in enumerate(_segments(text)):
            index_chunk(db,project_id,"main",job_id,"draft",
                        scene_id,scene_index,-10000+i,part)


def _terms(query: str) -> list[str]:
    items=re.findall(r"[^\W_]{3,}",query.casefold(),flags=re.UNICODE)
    return list(dict.fromkeys(word for word in items if word not in STOPWORDS))[:12]


def retrieve(db: sqlite3.Connection, project_id: str, job_id: str,
             scene_index: int, query: str, branch: str = "main",
             limit: int = 6) -> dict:
    """Read only related author evidence + same-job drafts through current chapter.

    Future manuscript text and parallel story branches are excluded. Retrieval
    does not imply a fact is true; draft evidence is explicitly marked draft.
    """
    limit=max(1,min(12,limit))
    terms=_terms(query)
    ranked=[]
    params=(project_id,branch,scene_index,job_id)
    base = ("SELECT c.* FROM story_chunks c WHERE c.project_id=? AND c.branch_id=? "
            "AND c.scene_index<=? AND (c.job_id='author' OR "
            "(c.job_id=? AND c.source='draft'))")
    if terms and _fts_available(db):
        q=" OR ".join('"' + w.replace('"','') + '"' for w in terms)
        try:
            ranked=[dict(row) for row in db.execute(
                base+" AND c.id IN (SELECT chunk_id FROM story_chunks_fts "
                "WHERE story_chunks_fts MATCH ? LIMIT 60) ORDER BY c.scene_index DESC LIMIT 80",
                params+(q,)).fetchall()]
        except sqlite3.OperationalError:
            ranked=[]
    if not ranked:
        ranked=[dict(row) for row in db.execute(
            base+" ORDER BY c.scene_index DESC,c.chunk_index DESC LIMIT 100",
            params).fetchall()]
    def score(row):
        line=row["content"].casefold()
        return (sum(1 for word in terms if word in line)*4
                + (2 if row["source"] in ("approved","synopsis") else 0)
                + min(scene_index,row["scene_index"])*0.01)
    ranked.sort(key=score,reverse=True)
    result=[]
    for row in ranked:
        if score(row) <= 0 or (not terms and row["source"]=="draft"):
            continue
        result.append({"scene_id":row["scene_id"],"scene_index":row["scene_index"],
                       "source":row["source"],"text":row["content"][:1000]})
        if len(result)>=limit:
            break
    ns=[n for n in nodes(db,project_id,branch) if n["scene_index"]<=scene_index]
    matching=sorted(ns,key=lambda n:sum(t in (n["title"]+" "+n["statement"]).casefold()
                            for t in terms),reverse=True)
    if terms:
        matching=[n for n in matching if any(t in (n["title"]+" "+n["statement"]).casefold()
                                               for t in terms)]
    matching=matching[:10]
    ids={n["id"] for n in matching}
    links=[{"relation":e["relation"],"source_id":e["source_id"],"target_id":e["target_id"]}
           for e in edges(db,project_id,branch)
           if e["source_id"] in ids and e["target_id"] in ids][:20]
    return {"evidence":result,"facts":matching,"links":links,"branch_id":branch}


def context_text(memory: dict, max_chars: int = 6000) -> str:
    facts=[{"id":n["id"],"type":n["kind"],"fact":n["statement"],
            "scene":n["scene_index"]+1} for n in memory["facts"]
           if n["status"]=="approved"]
    plan_cards=[{"id":n["id"],"scene":n["scene_index"]+1,
                 "outline":n["statement"]} for n in memory["facts"]
                if n["status"]=="outline"]
    snippets=[{"source":("НЕПРИНЯТЫЙ ЧЕРНОВИК" if e["source"]=="draft" else
                         "АВТОРСКИЙ СИНОПСИС" if e["source"]=="synopsis" else "АВТОРСКАЯ РУКОПИСЬ"),
               "chapter":e["scene_index"]+1,"excerpt":e["text"][:850]}
              for e in memory["evidence"]]
    return json.dumps({"author_approved_facts":facts,"outline_cards_not_world_facts":plan_cards,
                       "causal_links":memory["links"],
                       "retrieved_passages":snippets},ensure_ascii=False)[:max_chars]


def erase_project(db: sqlite3.Connection, project_id: str) -> None:
    rows=[(x["project_id"],x["branch_id"],x["job_id"],x["source"],x["scene_id"])
          for x in db.execute("SELECT DISTINCT project_id,branch_id,job_id,source,scene_id "
                              "FROM story_chunks WHERE project_id=?",(project_id,))]
    for row in rows:
        _delete_source(db,*row)
    for table in ("story_nodes","story_edges","story_branches"):
        db.execute(f"DELETE FROM {table} WHERE project_id=?",(project_id,))
