"""Fiction style, short vague exchanges, repeat-safe continuation and RAG truth."""
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import main
from narrative_direction import fiction_warnings,reject_vacant_dialogue,scene_contract
from prose_quality import first_cycle,prompt_tail,streaming_loop
from editor_workspace import initialize,record_rag_runtime,read_rag_runtime


def test_real_user_style_no_progress_dialogue_flagged_not_auto_fixed():
    dialogue="""— Ты думаешь, что это возможно? — спросил Кротомирус.
— Я не знаю, — ответил Кальмас.
— Мы должны продолжать, потому что это наш путь.
— Я чувствую, что это наш путь.
— И это не конец, — добавил Симсу.
— Это только начало, — произнесла Типирия.
— Мы должны продолжать, — сказал Кальмас.
— Я чувствую, что мы уже в этом мире.
— Мы не знаем, как выйти отсюда.
— И что тогда? — спросил Симсу.
— Тогда мы должны продолжать.
— Это наш путь, — подтвердил Кротомирус.
"""
    filler=("Они оставались неподвижны на месте и не смогли объяснить ни одной причины своего решения. "
            "Всё выглядело неопределённым, хотя герои никак не проверяли найденную ими дверь. ")*3
    issues=fiction_warnings(dialogue+filler)
    assert any(x["kind"]=="empty_dialogue" for x in issues)
    with pytest.raises(ValueError,match="Пустой диалог"):
        reject_vacant_dialogue(dialogue+filler)


def test_good_purposeful_conversation_not_blocked():
    sample="""— Откуда идёт сигнал? — спросила Симса, показывая на металлическую трубу.
— Из подвала, — ответил Кротомирус. — Я проверил кабель и нашёл свежий разрыв.
— Значит, свет гаснет из-за проводки? — Кальмас поднял фонарь.
— Нет, — возразила Типирия. — Вот здесь на схеме обозначен отдельный генератор.
— Сначала проверим напряжение, а потом откроем дверь.
— Я возьму измеритель, — сказала Симса. — Остальные пусть наблюдают за лестницей.
— Дверь заперта. У меня сохранился ключ из мастерской.
— Подожди, — остановил его Кротомирус. — Прежде отключим питание.
"""
    filler="Симса положила инструмент на сухую доску и вывела друзей на освещённую лестницу. "*4
    reject_vacant_dialogue(sample+filler)


def test_scene_contract_explains_why_who_and_what_changes():
    project=main.Project(title="Филинохомусы",work_type="fiction",
        scenes=[main.Scene(title="Тайна",anchor="Коты пытаются найти источник радио",
                           active_objects=["Кротомирус","Симса"],
                           hook="Загадка раскрывается позже")])
    scene=project.scenes[0]
    contract=scene_contract(project,scene,0,{"stage_name":"opening"},0,2500)
    for phrase in ("Участники по карточке автора","Кротомирус","Симса",
                   "ДРАМАТУРГИЧЕСКИЙ КОНТРАКТ","препятствие","Диалог"):
        assert phrase in contract
    prompt=main._book_prompt(project,scene,0,2500,"","",900,{"stage_name":"opening"})
    assert "Участники по карточке автора" in prompt
    assert prompt.index("ДРАМАТУРГИЧЕСКИЙ КОНТРАКТ") < prompt.index("план_ВСЕХ_глав")
    assert "Сохраняй цель сцены" in prompt
    project.work_type="technical"
    technical=main._book_prompt(project,scene,0,2500,"","",900)
    assert "ДРАМАТУРГИЧЕСКИЙ КОНТРАКТ" not in technical


def test_looping_saved_draft_keeps_true_last_event_in_prompt():
    original=("Внизу, в тишине, что не была тишиной, ")*50
    last="В этот момент передатчик подал третий отчётливый сигнал."
    assert first_cycle(original) is not None
    assert streaming_loop(original)
    cleaned=prompt_tail("Они вошли в дом. "+original+last)
    assert last in cleaned
    assert cleaned.count("что не была тишиной")<50


def test_rag_runtime_reports_actual_hits_not_pretend_status(tmp_path,monkeypatch):
    initialize(tmp_path)
    assert read_rag_runtime(tmp_path,"book","job") is None
    record_rag_runtime(tmp_path,"book","job",2,{
        "vector_state":"fts5-only","semantic_hits":0,
        "fts_hits":5,"graph_facts":3,"graph_links":2,
        "used_passages":7
    })
    now=read_rag_runtime(tmp_path,"book","job")
    assert now["scene_index"]==2 and now["fts_hits"]==5
    assert now["graph_links"]==2 and now["semantic_hits"]==0
    assert read_rag_runtime(tmp_path,"other","job") is None

    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"book.sqlite3")
    monkeypatch.setattr(main,"_start_book",lambda job_id:None)
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",4441)) as client:
        p=client.post("/api/projects",json={"title":"Проверка RAG"}).json()
        idle=client.get("/api/projects/"+p["id"]+"/rag-diagnostics")
        assert idle.status_code==200,idle.text
        assert idle.json()["last_retrieval"] is None
        assert idle.json()["graph"]["approved"]==0
