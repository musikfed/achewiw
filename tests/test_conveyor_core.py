"""SQLite conveyor invariants: real on-disk persistence, no model mocks needed."""
from __future__ import annotations
import json
import sqlite3
import uuid

import pytest

import conveyor_core as cv
from gpu_scheduling import init_gpu_leases, reserve_gpu, refresh_gpu, release_gpu

def connected(path):
    db=sqlite3.connect(path)
    db.row_factory=sqlite3.Row
    cv.initialize(db)
    init_gpu_leases(db)
    return db

def create(db, project=None):
    return cv.create(db,job_id=uuid.uuid4().hex,project_id=project or uuid.uuid4().hex,
           book_job_id=uuid.uuid4().hex,writer_model="qwen3:8b",
           critic_model="hermes",image_mode="none",image_style="",
           allow_shared_unload=True)

def test_durable_resume_and_immutable_artifact(tmp_path):
    path=tmp_path/"job.sqlite"
    with connected(path) as db:
        job=create(db)
        cv.stage_start(db,job["id"],"book","input")
        digest=cv.put_artifact(db,job["id"],"book","version","input",{"words":99})
        assert cv.put_artifact(db,job["id"],"book","version","input",{"words":99})==digest
        with pytest.raises(ValueError,match="Immutable"):
            cv.put_artifact(db,job["id"],"book","version","input",{"words":42})
        cv.stage_done(db,job["id"],"book",digest)
        cv.stage_start(db,job["id"],"mechanical_qa","input2")
    with connected(path) as db:
        job=cv.status(db,job["id"])
        assert job["state"]=="interrupted"
        assert job["current_step"]==1
        assert job["steps"][0]["status"]=="done"
        cv.resume(db,job["id"])
        cv.stage_start(db,job["id"],"mechanical_qa","input2")
        assert cv.artifact(db,job["id"],"book","version")["content_sha"]==digest

def test_author_gate_prevents_early_accept(tmp_path):
    with connected(tmp_path/"book.sqlite") as db:
        job=create(db)
        with pytest.raises(ValueError,match="Out-of-order"):
            cv.stage_start(db,job["id"],"archive","invalid")
        for stage in cv.STAGES[:6]:
            cv.stage_start(db,job["id"],stage,"input")
            cv.stage_done(db,job["id"],stage,"output")
        cv.pause(db,job["id"],"Review required")
        assert cv.status(db,job["id"])["current_step"]==6
        assert cv.status(db,job["id"])["state"]=="waiting_author"
        with pytest.raises(ValueError,match="approval"):
            cv.resume(db,job["id"])
        cv.resume(db,job["id"],author_approved=True)
        cv.stage_start(db,job["id"],"author_review","input")
        cv.stage_done(db,job["id"],"author_review","approved")
        cv.stage_start(db,job["id"],"archive","approved")
        cv.stage_done(db,job["id"],"archive","result")
        assert cv.status(db,job["id"])["state"]=="ready"
        assert cv.status(db,job["id"])["percent"]==100

def test_single_active_project_and_cancel(tmp_path):
    with connected(tmp_path/"book.sqlite") as db:
        project=uuid.uuid4().hex
        one=create(db,project)
        with pytest.raises(ValueError,match="unfinished"):
            create(db,project)
        cv.cancel(db,one["id"])
        two=create(db,project)
        assert two["id"]!=one["id"]
        with pytest.raises(ValueError):
            cv.stage_start(db,one["id"],"book","blocked")

def test_gpu_refresh_does_not_steal_or_revive(tmp_path):
    with connected(tmp_path/"gpu.sqlite") as db:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        assert reserve_gpu(db,"owner","writer",at=100.0)
        assert refresh_gpu(db,"owner",at=200.0)
        assert not refresh_gpu(db,"foreigner",at=200.0)
        assert not reserve_gpu(db,"other","artist",at=201.0)
        assert not refresh_gpu(db,"owner",at=801.0)
        assert reserve_gpu(db,"other","artist",at=801.0)
        release_gpu(db,"owner")
        assert not reserve_gpu(db,"owner","writer",at=802.0)

def test_core_code_is_safe_to_import():
    import conveyor_api
    assert conveyor_api.ConveyorRequest(image_mode="none").image_mode=="none"


def test_single_word_ai_spam_rejected_but_literary_emphasis_allowed():
    from prose_quality import first_cycle, check_quality, ProseDegeneration
    assert first_cycle("шёпотом " * 45)["kind"] == "word_loop"
    with pytest.raises(ProseDegeneration):
        check_quality("шёпотом " * 45)
    assert first_cycle(("Да. " * 6) + ("Миирус вышел на лестницу и заметил на стене след. Он решил рассказать об этом сестре. Сестра проверила окно и обнаружила записку.")) is None


def test_literary_review_covers_long_chapter_without_losing_chars():
    from conveyor_api import review_windows
    body = ("Миирус заметил след на камне и повернулся к спутнику. " * 1600)
    windows=review_windows(body)
    assert len(windows)>7
    assert "".join(windows)==body
    assert all(0<len(window)<=6500 for window in windows)
    assert review_windows("")==[]
