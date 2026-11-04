"""Book-length generation planning and durable checkpoints (no Ollama needed)."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from book_generation_core import (
    target_words, distribute_words, count_words, chunk_size, max_attempts,
    init_book_tables, create_book_chapters, chapters_for_job, save_chapter,
    book_summary,
)


@pytest.mark.parametrize("raw,expected", [
    ("20000", 20000),
    ("20000 слов", 20000),
    ("20 000 слов", 20000),
    ("150 минут", 22500),
    ("150 min", 22500),
    ("150 минут / 20000 слов", 20000),
])
def test_book_target(raw, expected):
    assert target_words(raw) == expected


def test_speech_fallback_and_invalid_volume():
    assert target_words("", speech_minutes=150) == 22500
    for value in ("", "очень длинная", "100 слов", "100000 слов"):
        with pytest.raises(ValueError):
            target_words(value)


def test_allocate_20000_words_across_six_chapters():
    allocations = distribute_words(20000, [str(x) for x in range(6)])
    assert sum(allocations) == 20000
    assert max(allocations) - min(allocations) <= 1
    assert chunk_size(3333) == 800
    assert max_attempts(3333) >= 10


def test_large_chapter_requires_more_outline_cards():
    with pytest.raises(ValueError):
        distribute_words(20000, ["only"])


def test_book_checkpoint_and_summary_survive_connection_restart(tmp_path):
    path = tmp_path / "book.sqlite3"
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        init_book_tables(db)
        create_book_chapters(db, "job1", [
            {"id": "scene1", "title": "Начало", "target_words": 1500,
             "original_sha256": "hash1"},
            {"id": "scene2", "title": "Финал", "target_words": 1500,
             "original_sha256": "hash2"},
        ])
        save_chapter(db, "job1", "scene1", "Кот увидел корабль.", 1)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        chapters = chapters_for_job(db, "job1")
        assert chapters[0]["word_count"] == count_words("Кот увидел корабль.")
        assert chapters[1]["word_count"] == 0
        assert book_summary(db, "job1")["target_words"] == 3000
        assert book_summary(db, "job1")["shortfall"] > 0
        save_chapter(db, "job1", "scene1", "Кот увидел корабль. Он испугался.", 2)
        assert chapters_for_job(db, "job1")[0]["attempts"] == 2


def test_does_not_overwrite_accepted_chapter():
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        init_book_tables(db)
        create_book_chapters(db, "job", [{
            "id": "s", "title": "A", "target_words": 500, "original_sha256": "h"
        }])
        db.execute("UPDATE ai_book_chapters SET accepted_at='2026-11-05' WHERE job_id='job'")
        with pytest.raises(ValueError):
            save_chapter(db, "job", "s", "Попытка перезаписи", 1)
