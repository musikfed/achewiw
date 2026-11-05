"""Author-approved visual foreshadowing linking images to future chapter solutions.

Puzzles are publishing metadata, not generated lore. The image appears only in
its clue chapter; the answer is a private editorial note until the later text.
Graph gets explicit foreshadows/reveals edges without replacing other facts.
"""
from __future__ import annotations
import re
import sqlite3
import uuid
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def initialize(db: sqlite3.Connection):
    db.execute("""CREATE TABLE IF NOT EXISTS visual_riddles(
        id TEXT PRIMARY KEY,project_id TEXT NOT NULL,illustration_id TEXT NOT NULL UNIQUE,
        clue_scene_id TEXT NOT NULL,answer_scene_id TEXT NOT NULL,
        title TEXT NOT NULL,answer_note TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_visual_riddles_book ON visual_riddles(project_id)")

def list_puzzles(db,project_id):
    return [dict(x) for x in db.execute(
        "SELECT * FROM visual_riddles WHERE project_id=? ORDER BY created_at,id",
        (project_id,)).fetchall()]

def add(db,project,illustration_id,answer_scene_id,title,answer_note):
    if not re.fullmatch("[0-9a-f]{32}",illustration_id):
        raise ValueError("Выбери существующий рисунок из каталога")
    if not 1<=len(title.strip())<=160 or not 1<=len(answer_note.strip())<=1500:
        raise ValueError("Укажи название загадки и объяснение разгадки")
    img=db.execute("SELECT scene_id,placement,caption FROM illustrations WHERE id=? AND project_id=?",
                   (illustration_id,project.id)).fetchone()
    if not img or img["placement"]=="cover":
        raise ValueError("Иллюстрация загадки должна принадлежать главе, не обложке")
    chapter_ids=[c.id for c in project.scenes]
    if img["scene_id"] not in chapter_ids or answer_scene_id not in chapter_ids:
        raise ValueError("Одна из глав отсутствует в каркасе")
    index=chapter_ids.index(img["scene_id"])
    answer=chapter_ids.index(answer_scene_id)
    if answer<=index:
        raise ValueError("Разгадка должна быть в следующей или более поздней главе")
    if db.execute("SELECT 1 FROM visual_riddles WHERE illustration_id=?",
                  (illustration_id,)).fetchone():
        raise ValueError("На этот рисунок уже заведена загадка")
    puzzle_id=uuid.uuid4().hex
    db.execute("INSERT INTO visual_riddles VALUES(?,?,?,?,?,?,?,?)",
               (puzzle_id,project.id,illustration_id,img["scene_id"],answer_scene_id,
                title.strip(),answer_note.strip(),now()))
    # Plan chapters are non-canon nodes; author's explicit riddle is canon.
    mystery_id="visual-mystery:"+puzzle_id
    db.execute("""INSERT INTO story_nodes
        (id,project_id,branch_id,kind,title,statement,scene_id,scene_index,status,created_at)
        VALUES (?,?,'main','mystery',?,?,?,?, 'approved',?)""",
        (mystery_id,project.id,title.strip(),
         "Авторская визуальная загадка: подсказка в главе "+str(index+1)+
         ", разгадка запланирована в главе "+str(answer+1)+
         ". Секретный ответ хранится только в редакторских метаданных.",
         answer_scene_id,answer,now()))
    for label,src in (("foreshadows",img["scene_id"]),("reveals",answer_scene_id)):
        db.execute("""INSERT INTO story_edges
            (id,project_id,branch_id,source_id,target_id,relation,note,created_at)
            VALUES(?,?,'main',?,?,?,'Связь изображения и отложенной разгадки',?)""",
            ("visual:"+label+":"+puzzle_id,project.id,"outline:"+src,
             mystery_id,label,now()))
    return [x for x in list_puzzles(db,project.id) if x["id"]==puzzle_id][0]

def delete(db,project_id,puzzle_id):
    if not re.fullmatch("[0-9a-f]{32}",puzzle_id):
        raise ValueError("Неизвестная визуальная загадка")
    db.execute("DELETE FROM story_edges WHERE project_id=? AND id IN (?,?)",
               (project_id,"visual:foreshadows:"+puzzle_id,"visual:reveals:"+puzzle_id))
    db.execute("DELETE FROM story_nodes WHERE project_id=? AND id=?",
               (project_id,"visual-mystery:"+puzzle_id))
    return db.execute("DELETE FROM visual_riddles WHERE project_id=? AND id=?",
                      (project_id,puzzle_id)).rowcount==1
