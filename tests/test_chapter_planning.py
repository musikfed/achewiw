"""Literary-length planning: suspense and author choice, never changing approved scenes."""
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from chapter_planning import (
    suggested_plan, validate_plan, init_plan_tables, store_plan, get_plan, prose_phase
)


def fixture_project():
    titles = ["Прибытие", "Первые шаги", "Погоня",
              "Раскрытие", "Кульминация", "Завершение"]
    return SimpleNamespace(
        id="book-test", title="Филиниусы", target_length="20 000 слов",
        duration_minutes=10, work_type="fiction",
        twist="Почему коты следят за Москвой",
        reveal="Раскрывать частями с третьей главы, полное раскрытие в кульминации",
        scenes=[SimpleNamespace(id=f"s{i}", title=title, text=f"Краткая заметка {i}")
                for i, title in enumerate(titles)]
    )


def test_twenty_thousand_words_are_not_forced_into_six_equal_chapters():
    project = fixture_project()
    before = [(s.id, s.title, s.text) for s in project.scenes]
    result = suggested_plan(project)
    lengths = [row["target_words"] for row in result["chapters"]]
    assert sum(lengths) == 20000
    assert len(set(lengths)) > 2
    assert result["chapters"][2]["stage"] == "conflict"
    assert result["chapters"][4]["stage"] == "climax"
    assert result["chapters"][5]["stage"] == "resolution"
    assert result["chapters"][0]["reveal_level"] == "none"
    assert result["chapters"][1]["reveal_level"] == "hint"
    assert result["chapters"][2]["reveal_level"] == "partial"
    assert result["chapters"][4]["reveal_level"] == "full"
    assert [(s.id, s.title, s.text) for s in project.scenes] == before


def test_author_can_reallocate_words_and_keep_exact_goal(tmp_path):
    p=fixture_project()
    proposed=suggested_plan(p)["chapters"]
    proposed[0]["target_words"]+=500
    proposed[3]["target_words"]-=500
    proposed[2]["tension"]=5
    proposed[2]["reveal_level"]="hint"
    proposed[2]["editorial_goal"]="Миирус должен обнаружить реальную подсказку"
    with sqlite3.connect(tmp_path/"plan.sqlite") as db:
        db.row_factory=sqlite3.Row
        init_plan_tables(db)
        saved=store_plan(db,p,proposed)
        assert saved["source"]=="author"
        assert saved["chapters"][2]["editorial_goal"].startswith("Миирус")
    with sqlite3.connect(tmp_path/"plan.sqlite") as db:
        db.row_factory=sqlite3.Row
        got=get_plan(db,p)
        assert got["chapters"][0]["target_words"]==proposed[0]["target_words"]
        assert got["chapters"][2]["reveal_level"]=="hint"
        p.target_length="22500 слов"
        stale=get_plan(db,p)
        assert stale["source"]=="suggested"
        assert stale["stale_saved_plan"] is True


def test_rejects_mismatched_cards_and_target():
    p=fixture_project()
    old=[(s.id,s.title,s.text) for s in p.scenes]
    proposed=suggested_plan(p)["chapters"]
    broken=[dict(row) for row in proposed]
    broken[0]["target_words"]+=5
    with pytest.raises(ValueError, match="Сумма"):
        validate_plan(p,broken)
    broken=[dict(row) for row in proposed]
    broken[0]["scene_id"]="forged"
    with pytest.raises(ValueError, match="ID"):
        validate_plan(p,broken)
    broken=[dict(row) for row in proposed]
    broken[0]["reveal_level"]="unknown"
    with pytest.raises(ValueError):
        validate_plan(p,broken)
    assert [(s.id,s.title,s.text) for s in p.scenes]==old


def test_stages_change_instruction_not_canon():
    assert prose_phase(0,3000).startswith("Вход")
    assert prose_phase(1400,3000).startswith("Углубление")
    assert prose_phase(2980,3000).startswith("Выход")


def test_http_plan_save_and_generation_freezes_author_instructions(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"plan-api.sqlite3")
    monkeypatch.setattr(main,"_start_book",lambda job_id:None)
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",12345)) as client:
        p=client.post("/api/projects",json={"title":"Филиниусы"}).json()
        p["target_length"]="20000 слов"
        p["twist"]="Кто наблюдает за москвичами"
        p["reveal"]="С третьей главы"
        p["scenes"]=[{"title":t,"text":"Старый краткий пересказ."}
                      for t in ("Прибытие","Первые шаги","Погоня",
                                "Раскрытие","Кульминация","Завершение")]
        p=client.put("/api/projects/"+p["id"],json=p).json()
        url=f"/api/projects/{p['id']}/book-plan"
        plan=client.get(url)
        assert plan.status_code==200,plan.text
        rows=plan.json()["chapters"]
        assert sum(s["target_words"] for s in rows)==20000
        rows[0]["target_words"]+=400
        rows[1]["target_words"]-=400
        rows[2]["reveal_level"]="hint"
        rows[2]["editorial_goal"]="Погоня не должна раскрывать главную тайну"
        changed=client.put(url,json={"chapters":rows})
        assert changed.status_code==200,changed.text
        assert client.get(url).json()["chapters"][0]["target_words"]==rows[0]["target_words"]
        job=client.post("/api/books/generate",json={"project_id":p["id"],
                                                   "model":"qwen3:8b"})
        assert job.status_code==202,job.text
        snap=job.json()
        assert snap["book"]["target_words"]==20000
        assert snap["book_chapters"][0]["target_words"]==rows[0]["target_words"]
        assert snap["book_chapters"][2]["reveal_level"]=="hint"
        assert snap["book_chapters"][2]["editorial_goal"].startswith("Погоня")
        assert client.get("/api/projects/"+p["id"]).json()["scenes"][0]["text"]=="Старый краткий пересказ."
        expected=snap["book_chapters"]
        # Raw draft and published manuscript remain distinct.
        draft=client.get("/api/books/jobs/"+snap["id"]+"/draft")
        assert draft.status_code==200,draft.text
        assert draft.json()["chapters"][0]["target_words"]==expected[0]["target_words"]
        assert draft.json()["chapters"][0]["text"]==""
        prompt=main._book_prompt(main.Project.model_validate(p),
                                 main.Scene.model_validate(p["scenes"][2]),2,
                                 expected[2]["target_words"],"","",700,expected[2])
        assert "Главную тайну" not in prompt  # no invented answer
        assert "без ответа" in prompt.lower()
        assert "Сохраняй цель сцены" in prompt
