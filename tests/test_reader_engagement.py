"""Contract tests: genre beats, reveal guards, author data, and API.

No Ollama calls and no mass-produced prose; original book text is untouched.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from chapter_planning import suggested_plan
from reader_engagement import (map_chapters, fragment_contract, review_contract,
                               lexical_findings, choose_mode)


def project(kind="fiction",genre="детектив",extra=False):
    titles=["Находка","Вторая улика","Проверка версии","Разгадка","Последствия"]
    scenes=[]
    for i,title in enumerate(titles):
        scenes.append(NS(id="s"+str(i),title=title,text="",
                         hook="Кто оставил след?" if i==0 else "",
                         anchor="След найден возле маяка" if i==0 else "",
                         active_objects=[]))
    return NS(id="example",work_type=kind,genre=genre,
              title="Маяк",target_length="12000 слов",duration_minutes=20,
              twist="Кто выключил маяк?",reveal="Раскрыть в четвёртой главе",
              scenes=scenes)


def test_plan_is_read_only_and_has_nonuniform_beat_roles():
    p=project()
    original=[(s.id,s.title,s.anchor,s.hook) for s in p.scenes]
    view=map_chapters(p,suggested_plan(p))
    assert view["enabled"] is True
    assert view["mode"]=="detective"
    assert view["technique"].startswith("улика")
    assert len(view["chapters"])==5
    assert view["chapters"][0]["reader_question"]=="Кто оставил след?"
    assert all(x["beat"] for x in view["chapters"])
    assert [(s.id,s.title,s.anchor,s.hook) for s in p.scenes]==original


def test_no_automatic_mystery_for_technical_books():
    p=project("technical")
    assert map_chapters(p,suggested_plan(p))["enabled"] is False
    assert fragment_contract(p,p.scenes[0],0,{},0,2000)==""


def test_revelation_rights_follow_saved_chapter_plan():
    p=project()
    plan=suggested_plan(p)
    plan["chapters"][0]["reveal_level"]="hint"
    plan["chapters"][2]["reveal_level"]="full"
    data=map_chapters(p,plan)
    assert data["chapters"][0]["release_answer"] is False
    assert data["chapters"][2]["release_answer"] is True
    hint=fragment_contract(p,p.scenes[0],0,
                            {"stage_name":"opening","reveal_level":"hint"},250,2000)
    assert "не объясняй" in hint.lower()
    assert "не придумывай" in hint.lower()
    full=fragment_contract(p,p.scenes[3],3,
                            {"stage_name":"reveal","reveal_level":"full"},1300,2500)
    assert "допустима" in full.lower()
    assert "разгадка" in full.lower()


def test_mystery_mode_is_not_forced_on_every_genre():
    assert choose_mode("любовный роман")=="literary"
    assert choose_mode("сериал, фантастика")=="serial"
    assert choose_mode("детектив")=="detective"


def test_suspense_has_evidence_based_critic_and_weak_lexical_signals():
    p=project()
    critic=review_contract(p,p.scenes[0],{"stage_name":"opening","reveal_level":"none"})
    assert "не найдено" in critic
    assert "не сочиняй" in critic
    repeated=("И он спросил? "*85)+"Это только начало."
    result=lexical_findings(repeated)
    assert {x["kind"] for x in result}=={"question_spam","generic_cliffhanger"}
    assert lexical_findings("Он открыл дверь.")==[]


def test_reject_outline_mismatch_before_generating():
    p=project()
    rows=suggested_plan(p)
    rows["chapters"][2]["scene_id"]="wrong"
    with pytest.raises(ValueError,match="каркас"):
        map_chapters(p,rows)


def test_ollama_fiction_prompt_contains_fair_play_contract():
    import main
    p=main.Project.model_validate({
        "title":"Маяк","work_type":"fiction","genre":"детектив",
        "target_length":"12000 слов","twist":"Кто выключил маяк?",
        "reveal":"Открыть ближе к концу",
        "scenes":[{"title":"Находка","hook":"Кто оставил след?",
                   "anchor":"След возле маяка"}]})
    scene=p.scenes[0]
    prompt=main._book_prompt(p,scene,0,1800,"","",450,
                              {"stage_name":"opening","reveal_level":"hint"})
    assert "ВОРОНКА ВНИМАНИЯ" in prompt
    assert "Ложный след" in prompt
    assert "Без ответа" in prompt or "не объясняй" in prompt
    assert "Продолжи текст этой же главы" in prompt


def test_api_map_reports_valid_author_plan(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"engagement.sqlite3")
    with TestClient(main.app,base_url="http://192.168.0.100:7080",
                    client=("127.0.0.1",12345)) as api:
        posted=api.post("/api/projects",json={"title":"Читатель","work_type":"fiction",
                  "target_length":"12000 слов","genre":"детектив"})
        assert posted.status_code==201,posted.text
        p=posted.json()
        p["target_length"]="12000 слов"
        p["scenes"]=[{"title":t,"anchor":"Зацепка "+str(i),"hook":"Вопрос "+str(i)}
                     for i,t in enumerate(("Находка","Поиск","Проверка","Ответ","Финал"))]
        saved=api.put("/api/projects/"+p["id"],json=p)
        assert saved.status_code==200,saved.text
        response=api.get("/api/projects/"+p["id"]+"/reader-engagement")
        assert response.status_code==200,response.text
        assert len(response.json()["chapters"])==5
        assert response.json()["chapters"][0]["has_authored_hook"] is True
        persisted=api.get("/api/projects/"+p["id"]).json()
        assert persisted["scenes"][0]["anchor"]=="Зацепка 0"
