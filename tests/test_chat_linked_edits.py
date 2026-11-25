"""Linked author chat → immutable editorial revision, only temporary SQLite."""
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from editorial_review import snapshot, get_version
import chat_conversations as chats
import chat_editorial_version_bridge as editions
import chat_linked_edits as linked

ORIGINAL="Глава 1. Магнитная головка\n\nЯ решил вам рассказать про HDD."
EDITED="Глава 1. Магнитная головка\n\nГоловка считывает магнитный сигнал."


class ChatLinkedEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.project=SimpleNamespace(id="fiction-test",title="Тестовая глава HDD",target_length="100")
        self.version=snapshot(self.root,self.project,[
            {"scene_id":"chapter-1","title":"HDD","text":ORIGINAL,"complete":True},
            {"scene_id":"chapter-2","title":"Финал","text":"Неизменная глава.","complete":True},
        ],source_kind="manuscript")
        editions.attach(self.root,project_id=self.project.id,version_id=self.version["id"])
        self.thread=chats.create_thread(self.root,project_id=self.project.id,workspace="editorial")
        self.msg=chats.submit(self.root,project_id=self.project.id,thread_id=self.thread["id"],
            message="Убери разговорный оборот",scope="chapter",
            source_ref="chapter-1",source_text=ORIGINAL)

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self,proposal=None,thread=None):
        return linked.prepare(self.root,project_id=self.project.id,
            thread_id=thread or self.thread["id"],
            chat_proposal_id=proposal or self.msg["proposal_id"],
            base_version_id=self.version["id"],
            scene_id="chapter-1",candidate_text=EDITED)

    def decide(self,approve=True,proposal=None,thread=None):
        return linked.decide(self.root,project_id=self.project.id,
            thread_id=thread or self.thread["id"],
            chat_proposal_id=proposal or self.msg["proposal_id"],approve=approve)

    def test_full_linked_cycle_and_immutable_original(self):
        result=self.prepare()
        self.assertIn("-Я решил вам рассказать",result["diff"])
        self.assertFalse(result["applied"])
        accepted=self.decide()
        self.assertEqual(accepted["status"],"accepted")
        self.assertNotEqual(accepted["new_immutable_version"],self.version["id"])
        self.assertEqual(editions.current(self.root,project_id=self.project.id)["chapters"][0]["text"],EDITED)
        self.assertEqual(editions.current(self.root,project_id=self.project.id)["chapters"][1]["text"],"Неизменная глава.")
        self.assertEqual(get_version(self.root,self.project.id,self.version["id"])["chapters"][0]["text"],ORIGINAL)
        saved=chats.conversation(self.root,project_id=self.project.id,thread_id=self.thread["id"])
        self.assertEqual(saved["proposals"][0]["status"],"accepted")
        self.assertEqual(saved["proposals"][0]["message_id"],self.msg["message_id"])

    def test_reject_is_atomic(self):
        self.prepare()
        self.assertFalse(self.decide(approve=False)["manuscript_changed"])
        self.assertEqual(editions.current(self.root,project_id=self.project.id)["version_id"],self.version["id"])
        with sqlite3.connect(self.root/"work.sqlite3") as db:
            self.assertEqual(db.execute("SELECT status FROM work_chat_book_edits").fetchone()[0],"rejected")
        self.assertEqual(chats.conversation(self.root,project_id=self.project.id,
            thread_id=self.thread["id"])["proposals"][0]["status"],"rejected")

    def test_reject_via_plan_only_api_is_blocked(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError,"joint decision"):
            chats.reject(self.root,project_id=self.project.id,
                thread_id=self.thread["id"],proposal_id=self.msg["proposal_id"])

    def test_wrong_thread_and_project_rejected(self):
        another=chats.create_thread(self.root,project_id=self.project.id,workspace="editorial")
        with self.assertRaises(linked.LinkedEditConflict):
            self.prepare(thread=another["id"])
        with self.assertRaises(ValueError):
            linked.decide(self.root,project_id="foreign-book",thread_id=self.thread["id"],
                          chat_proposal_id=self.msg["proposal_id"],approve=True)

    def test_mismatched_source_or_scope_rejected(self):
        with self.assertRaises(linked.LinkedEditConflict):
            linked.prepare(self.root,project_id=self.project.id,
                thread_id=self.thread["id"],chat_proposal_id=self.msg["proposal_id"],
                base_version_id=self.version["id"],scene_id="chapter-2",candidate_text="Different")
        another=chats.submit(self.root,project_id=self.project.id,thread_id=self.thread["id"],
            message="Исправь",scope="chapter",source_ref="chapter-1",source_text="wrong content")
        with self.assertRaisesRegex(linked.LinkedEditConflict,"fingerprint"):
            self.prepare(proposal=another["proposal_id"])

    def test_stale_proposal_and_no_double_accept(self):
        self.prepare()
        other=chats.submit(self.root,project_id=self.project.id,thread_id=self.thread["id"],
            message="Сделай другой вариант",scope="chapter",source_ref="chapter-1",source_text=ORIGINAL)
        self.prepare(proposal=other["proposal_id"])
        self.decide()
        with self.assertRaisesRegex(linked.LinkedEditConflict,"Stale edit"):
            self.decide(proposal=other["proposal_id"])
        with self.assertRaises(linked.LinkedEditConflict):
            self.decide()

    def test_migrate_legacy_status_without_losing_messages(self):
        with sqlite3.connect(self.root/"work.sqlite3") as db:
            # Simulate an installation with the original CHECK constraint.
            db.execute("ALTER TABLE work_chat_proposals RENAME TO old_chat_proposals")
            db.execute("""CREATE TABLE work_chat_proposals (
                id TEXT PRIMARY KEY,thread_id TEXT NOT NULL,message_id TEXT NOT NULL,
                source_ref TEXT,source_digest TEXT,scope TEXT,plan_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed'
                CHECK(status IN ('proposed','rejected')),
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
            db.execute("INSERT INTO work_chat_proposals SELECT * FROM old_chat_proposals")
            db.execute("DROP TABLE old_chat_proposals")
        chats.initialize(self.root)
        saved=chats.conversation(self.root,project_id=self.project.id,thread_id=self.thread["id"])
        self.assertEqual(len(saved["messages"]),1)
        self.assertEqual(len(saved["proposals"]),1)
        self.prepare()
        self.assertEqual(self.decide()["status"],"accepted")


if __name__=="__main__":
    unittest.main()
