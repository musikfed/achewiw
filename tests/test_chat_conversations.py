"""Isolated storage tests; only temporary SQLite, no server or LLM."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "app"))
import chat_conversations as chat


class ChatStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.thread = chat.create_thread(
            self.data_dir,project_id="book-1",workspace="editorial",mode="clarify")

    def tearDown(self):
        self.tmp.cleanup()

    def test_atomic_proposal_and_reload(self):
        result = chat.submit(self.data_dir,project_id="book-1",
            thread_id=self.thread["id"],message="Убери повторы и выровняй абзацы",
            scope="chapter",source_ref="scene-1",source_text="Исходная глава")
        self.assertFalse(result["applied"])
        saved = chat.conversation(self.data_dir,project_id="book-1",thread_id=self.thread["id"])
        self.assertEqual(len(saved["messages"]),1)
        self.assertEqual(len(saved["proposals"]),1)
        self.assertNotIn("Исходная глава",str(saved))
        self.assertEqual([a["kind"] for a in saved["proposals"][0]["plan"]["actions"]],
                         ["editorial_proposal","typesetting_proposal"])
        self.assertEqual(saved["proposals"][0]["status"],"proposed")

    def test_switch_mode_keeps_messages(self):
        chat.submit(self.data_dir,project_id="book-1",thread_id=self.thread["id"],
                    message="Поправь главу",scope="chapter")
        chat.switch_mode(self.data_dir,project_id="book-1",
                         thread_id=self.thread["id"],mode="autonomous")
        data = chat.conversation(self.data_dir,project_id="book-1",thread_id=self.thread["id"])
        self.assertEqual(data["thread"]["mode"],"autonomous")
        self.assertEqual(len(data["messages"]),1)
        next_plan = chat.submit(self.data_dir,project_id="book-1",
            thread_id=self.thread["id"],message="Исправь стилистику")
        self.assertIsNone(next_plan["question"])

    def test_reject_does_not_change_book(self):
        plan = chat.submit(self.data_dir,project_id="book-1",thread_id=self.thread["id"],
                           message="Перепиши",scope="chapter")
        changed = chat.reject(self.data_dir,project_id="book-1",thread_id=self.thread["id"],
                              proposal_id=plan["proposal_id"])
        self.assertFalse(changed["manuscript_changed"])
        self.assertEqual(chat.conversation(self.data_dir,project_id="book-1",
                         thread_id=self.thread["id"])["proposals"][0]["status"],"rejected")

    def test_source_conflict_is_reported_without_approval(self):
        plan = chat.submit(self.data_dir,project_id="book-1",thread_id=self.thread["id"],
                           message="Правка",scope="chapter",source_ref="s1",source_text="До")
        params = dict(data_dir=self.data_dir,project_id="book-1",
                      thread_id=self.thread["id"],proposal_id=plan["proposal_id"])
        valid = chat.proposal_preflight(**params,current_text="До")
        stale = chat.proposal_preflight(**params,current_text="После")
        self.assertTrue(valid["source_unchanged"])
        self.assertFalse(stale["source_unchanged"])
        self.assertFalse(valid["can_apply"])

    def test_other_book_cannot_access_thread(self):
        with self.assertRaises(ValueError):
            chat.conversation(self.data_dir,project_id="book-2",thread_id=self.thread["id"])
        with self.assertRaises(ValueError):
            chat.submit(self.data_dir,project_id="book-2",
                        thread_id=self.thread["id"],message="Нет")

    def test_legacy_work_tables_untouched(self):
        with sqlite3.connect(self.data_dir/"work.sqlite3") as db:
            db.execute("CREATE TABLE prior_work (id TEXT)")
            db.execute("INSERT INTO prior_work VALUES ('intact')")
        chat.initialize(self.data_dir)
        with sqlite3.connect(self.data_dir/"work.sqlite3") as db:
            self.assertEqual(db.execute("SELECT id FROM prior_work").fetchone()[0],"intact")

    def test_source_ref_is_mandatory_with_original_text(self):
        with self.assertRaises(ValueError):
            chat.submit(self.data_dir,project_id="book-1",
                        thread_id=self.thread["id"],message="test",source_text="text")


if __name__ == "__main__":
    unittest.main()
