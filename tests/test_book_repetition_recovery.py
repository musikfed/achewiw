"""Qwen long-book continuation regression: repeat recovery and persistent resume."""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from book_generation_core import (check_new_prose, count_words, init_book_tables,
    create_book_chapters, chapters_for_job, save_chapter, trim_repeated_tail,
    continuation_repair_note, chunk_size)
from generation_core import update_job
import main


def test_trim_only_word_for_word_tail_echo_and_keep_new_prose():
    earlier = ("В темноте старого дома Кротомирус увидел открытую дверь "
               "и осторожно позвал Симсу. ")*5
    last = "В темноте старого дома Кротомирус увидел открытую дверь и осторожно позвал Симсу."
    new = ("В комнате раздался звон разбитой чашки, и Симса замерла. "
           "Затем они заметили металлический след у порога, "
           "и Кротомирус решил проверить, кто оставил его. ")*4
    fixed=trim_repeated_tail(earlier,last+"\n\n"+new)
    assert fixed.startswith("В комнате раздался звон")
    assert not fixed.startswith("В темноте старого дома")
    check_new_prose(earlier,fixed)
    assert trim_repeated_tail(earlier,new)==new.strip()
    with pytest.raises(ValueError, match="повтор"):
        check_new_prose(earlier," ".join([last]*6))


def test_small_chunks_and_distinct_recovery_strategies():
    assert chunk_size(1200) == 900
    assert chunk_size(117) == 180
    assert chunk_size(2500,1650) == 1500
    from book_generation_core import predict_output_tokens
    assert predict_output_tokens(900)>2600
    notes=[continuation_repair_note(i,"Первое событие. Затем открылась дверь.")
           for i in range(6)]
    assert notes[0]==""
    assert len(set(notes))==6
    assert all("РЕДАКТОРСКАЯ ПРАВКА" in x for x in notes[1:])



def test_prompt_never_cuts_the_last_saved_paragraphs_or_book_ending():
    scenes=[
        main.Scene(title="Первая глава",anchor="Начало",hook="Новая загадка"),
        main.Scene(title="Кульминация",anchor="Сложный выбор",hook="Обнаружение причины"),
        main.Scene(title="Развязка",anchor="Следствие выбора",hook="Тайна раскрывается"),
    ]
    p=main.Project(title="Филинохомусы",scenes=scenes,
                   premise="Нельзя менять причину появления сигнала. "*90,
                   anchors="Миирус всё ещё не знает источник сигнала. "*55,
                   sources="Важный авторский архив. "*150,
                   claims="Ни одного реткона. "*80)
    last="И в этот момент передатчик наконец подал третий сигнал."
    previous="Предыдущая глава завершилась открытием железной двери."
    prompt=main._book_prompt(p,scenes[0],0,2700,previous,
                             ("Первый сохранённый абзац. "*500)+"\\n\\n"+last,
                             900,{"stage_name":"opening","editorial_goal":"Установить загадку"},
                             "ПАМЯТЬ ОБ УЗЛАХ ГРАФА. "*700)
    assert len(prompt)<=22000
    assert "Кульминация" in prompt and "Развязка" in prompt
    assert "Миирус" in prompt
    assert previous in prompt
    assert last in prompt
    assert prompt.endswith("Продолжи текст этой же главы прямо с места остановки:")



def test_retry_epoch_migrates_existing_chapter_and_persists(tmp_path):
    dbpath=tmp_path/"book.sqlite3"
    with sqlite3.connect(dbpath) as db:
        db.row_factory=sqlite3.Row
        db.execute("""CREATE TABLE ai_book_chapters(
            job_id TEXT NOT NULL,scene_id TEXT NOT NULL,scene_index INTEGER NOT NULL,
            title TEXT NOT NULL,target_words INTEGER NOT NULL,original_sha256 TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',word_count INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,complete INTEGER NOT NULL DEFAULT 0,
            accepted_at TEXT,updated_at TEXT NOT NULL,
            PRIMARY KEY(job_id,scene_id))""")
        init_book_tables(db)
        assert "retry_epoch" in {x[1] for x in db.execute("PRAGMA table_info(ai_book_chapters)")}
        db.execute("INSERT INTO ai_book_chapters(job_id,scene_id,scene_index,title,target_words,original_sha256,updated_at) VALUES('a','b',0,'Книга',250,'h','now')")
        save_chapter(db,"a","b","Существующая первая часть книги не изменяется.",1)
    with sqlite3.connect(dbpath) as db:
        db.row_factory=sqlite3.Row
        c=chapters_for_job(db,"a")[0]
        assert c["retry_epoch"]==0
        assert c["text"].startswith("Существующая")


def test_book_generation_recovers_from_four_repeated_qwen_responses(tmp_path,monkeypatch):
    """The fifth, distinct continuation must advance; old words and canon survive."""
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"book-recovery.sqlite3")
    monkeypatch.setattr(main,"_start_book",lambda job_id:None)
    old=("Кротомирус сидел на бронзовой лампе возле окна и смотрел на улицу. "
         "Симса держала старую карту в лапах и ждала условленного сигнала. ")*7
    # Natural, varied continuations: a regression test must not teach the
    # newly strengthened validator that 8 identical sentences are good prose.
    accepted_1=" ".join([
       "Симса остановилась у запертой двери, потому что услышала три ровных удара за стеной.",
       "Кротомирус сравнил отметки на деревянной раме с узором на старом плане дома.",
       "У карты оказался оторван правый угол, но на бумаге сохранился номер лестничной клетки.",
       "Кальмас предложил проверить подвал, где прошлой ночью сам заметил слабый зелёный свет.",
       "Типирия осмотрела следы пыли и выяснила, что тяжёлую крышку сдвигали недавно.",
       "Симса вынула карандаш и записала время, чтобы позже сопоставить его с шумом трамвая.",
       "За дверью щёлкнула защёлка, заставив всех отступить на несколько шагов от порога.",
       "Миирус увидел тонкий кабель, проложенный по кирпичной кладке к соседнему подъезду.",
       "Кротомирус осторожно поднял металлическую скобу, обнаружив под ней вдавленный знак.",
       "Запах свежей краски подсказал героям, что отметку кто-то намеренно скрывал от жильцов.",
       "На лестнице послышались шаги дворника, и участники решили на время разделиться.",
       "Кальмас остался наблюдать за входом, пока Симса проверяла найденный провод у окна.",
       "Когда свет погас, они успели запомнить цвет изоляции и направление последнего изгиба.",
       "Утром компания договорилась проверить мастерскую и выяснить, кому принадлежит старый прибор."
    ])
    accepted_2=" ".join([
       "На рассвете у лесной тропы коты обнаружили свежие следы ботинок в размокшей глине.",
       "Небольшой грузовик свернул к заброшенной станции и остановился за деревьями.",
       "Симса заметила в кузове ту самую металлическую коробку, которую они видели ночью.",
       "Кротомирус попросил остальных не приближаться, пока не выяснится число незнакомых людей.",
       "Через минуту из кабины вышел человек в сером плаще и снял с борта тяжёлую сумку.",
       "Кальмас обнаружил под ногами кабель, соединявший будку сторожа с пустым сараем.",
       "Типирия проследила за линией проводов и нашла аккуратно замаскированную антенну.",
       "На одном из креплений оставалась старая заводская маркировка с номером выпуска.",
       "Симса переписала цифры, чтобы позднее сверить их с документами в городской библиотеке.",
       "Из сарая раздался негромкий сигнал, похожий на тот, что они слышали у окна.",
       "Кротомирус решился открыть боковую дверцу и обнаружил внутри неисправный приёмник.",
       "Рядом лежали выцветшие записи с датами, не совпадавшими с показаниями часов.",
       "Кальмас поднял лист бумаги и увидел рисунок той же геометрической фигуры.",
       "Когда незнакомец вернулся, коты уже успели спрятать блокнот и закрыть корпус прибора.",
       "Человек осмотрел запор, нахмурился и отправился проверить провода за стеной.",
       "Типирия отвлекла его внезапным звуком в другой части здания, дав друзьям уйти.",
       "На обратной дороге Симса объяснила, почему находка меняет их первоначальное предположение.",
       "Вечером они составили перечень вещей, которые ещё требовали проверки перед следующим походом."
    ])
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",12000)) as api:
        project=api.post("/api/projects",json={"title":"Филинохомусы"}).json()
        project["target_length"]="500 слов"
        project["scenes"]=[
            {"title":"Приветствие из будущего","text":"Коты слышат неизвестный сигнал."},
            {"title":"Развязка","text":"Симса выясняет источник сигнала."},
        ]
        project=api.put("/api/projects/"+project["id"],json=project).json()
        job=api.post("/api/books/generate",json={
            "project_id":project["id"],"model":"qwen3:8b"}).json()
        job_id=job["id"]
        with main.db() as conn:
            chapter=chapters_for_job(conn,job_id)[0]
            save_chapter(conn,job_id,chapter["scene_id"],old,1)
            update_job(conn,job_id,status="error",stage="repetition",error="repeat")
        before=api.get("/api/books/jobs/"+job_id+"/draft").json()
        assert before["chapters"][0]["text"]==old
        resumed=api.post("/api/books/jobs/"+job_id+"/resume")
        assert resumed.status_code==202,resumed.text
        with main.db() as conn:
            state=chapters_for_job(conn,job_id)[0]
            assert state["retry_epoch"]==1
            assert state["text"]==old

        calls=[]
        class MockResponse:
            def __init__(self,text):self.text=text
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def raise_for_status(self):pass
            async def aiter_lines(self):
                yield json.dumps({"response":self.text,"done":True},ensure_ascii=False)

        class MockClient:
            def __init__(self,*a,**k):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            def stream(self,method,url,json):
                calls.append(copy.deepcopy(json))
                return MockResponse(old if len(calls)<=4 else
                                    accepted_1 if len(calls)==5 else accepted_2)

        monkeypatch.setattr(main.httpx,"AsyncClient",MockClient)
        asyncio.run(main.execute_book(job_id))
        with main.db() as conn:
            state=chapters_for_job(conn,job_id)[0]
            report=main._book_record(conn,job_id)
        assert len(calls)>=6
        assert len(set(c["options"]["seed"] for c in calls[:5]))==5
        assert "РЕДАКТОРСКАЯ ПРАВКА" in calls[1]["prompt"]
        assert state["text"].startswith(old.rstrip())
        assert "За дверью щёлкнула защёлка" in state["text"]
        assert state["word_count"]>=250
        assert report["status"]=="ready"
        versions=api.get("/api/projects/"+project["id"]+"/editorial/versions").json()
        assert len(versions)==1
        assert versions[0]["source_kind"]=="draft"
        assert versions[0]["all_complete"]
        proposals=api.get("/api/projects/"+project["id"]+"/editorial/versions/"+
                          versions[0]["id"]+"/graph-proposals").json()
        assert proposals["suggestions"]
        with main.db() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM story_nodes WHERE project_id=? AND status='approved'",
                (project["id"],)).fetchone()[0]==0  # never auto-approve AI events
        assert api.get("/api/projects/"+project["id"]).json()["scenes"][0]["text"]=="Коты слышат неизвестный сигнал."
