"""Real immutable work_book_versions, synthetic text, no real project or host."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from editorial_review import snapshot, get_version
import chat_editorial_version_bridge as bridge

ORIGINAL = ("Глава 1. Головка HDD\n\n"
            "Магнитные пластины вращаются. Головка считывает изменения поля.\n\n"
            "Я решил вам рассказать о головке.")
REVISED = ("Глава 1. Головка HDD\n\n"
           "Магнитные пластины вращаются. Головка считывает изменения поля.\n\n"
           "Сигнал головки поступает в контроллер накопителя.")


class RealVersionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.project = SimpleNamespace(id="test-book",title="Проверка HDD",target_length="100")
        self.base = snapshot(self.root,self.project,[
            {"scene_id":"chapter-1","title":"Головка HDD","text":ORIGINAL,"complete":True},
            {"scene_id":"chapter-2","title":"Дальше","text":"Вторая глава неизменна.","complete":True},
        ],source_kind="manuscript")
        bridge.attach(self.root,project_id=self.project.id,version_id=self.base["id"])

    def tearDown(self):
        self.folder.cleanup()

    def propose(self, text=REVISED):
        return bridge.propose(self.root,project_id=self.project.id,
            base_version_id=self.base["id"],scene_id="chapter-1",candidate_text=text)

    def test_accept_and_restore_create_immutable_versions(self):
        p=self.propose()
        self.assertIn("-Я решил вам рассказать",p["diff"])
        self.assertEqual(bridge.current(self.root,project_id=self.project.id)["chapters"][0]["text"],ORIGINAL)
        result=bridge.decide(self.root,project_id=self.project.id,
                             proposal_id=p["proposal_id"],approve=True)
        self.assertEqual(result["status"],"accepted")
        self.assertEqual(bridge.current(self.root,project_id=self.project.id)["chapters"][0]["text"],REVISED)
        self.assertEqual(bridge.current(self.root,project_id=self.project.id)["chapters"][1]["text"],
                         "Вторая глава неизменна.")
        self.assertEqual(get_version(self.root,self.project.id,self.base["id"])["chapters"][0]["text"],ORIGINAL)
        restore=bridge.prepare_restore(self.root,project_id=self.project.id,
                       restore_version_id=self.base["id"],scene_id="chapter-1")
        self.assertFalse(restore["applied"])
        bridge.decide(self.root,project_id=self.project.id,
                      proposal_id=restore["proposal_id"],approve=True)
        self.assertEqual(bridge.current(self.root,project_id=self.project.id)["chapters"][0]["text"],ORIGINAL)

    def test_rejection_leaves_base(self):
        p=self.propose()
        result=bridge.decide(self.root,project_id=self.project.id,
                              proposal_id=p["proposal_id"],approve=False)
        self.assertFalse(result["changed"])
        self.assertEqual(bridge.current(self.root,project_id=self.project.id)["version_id"],self.base["id"])

    def test_stale_proposal_fails_closed(self):
        a=self.propose()
        b=self.propose("Другая правка")
        bridge.decide(self.root,project_id=self.project.id,proposal_id=a["proposal_id"],approve=True)
        with self.assertRaises(bridge.EditorialConflict):
            bridge.decide(self.root,project_id=self.project.id,
                          proposal_id=b["proposal_id"],approve=True)

    def test_project_isolation_and_no_auto_rebase(self):
        with self.assertRaises(ValueError):
            bridge.attach(self.root,project_id="another",version_id=self.base["id"])
        with self.assertRaises(ValueError):
            bridge.current(self.root,project_id="another")
        other = snapshot(self.root,self.project,[
            {"scene_id":"chapter-1","text":"Независимая редакция"}
        ],source_kind="manuscript")
        status=bridge.attach(self.root,project_id=self.project.id,version_id=other["id"])
        self.assertTrue(status["already_attached"])
        self.assertEqual(status["head_version_id"],self.base["id"])

    def test_approval_boolean_required(self):
        p=self.propose()
        with self.assertRaises(ValueError):
            bridge.decide(self.root,project_id=self.project.id,
                          proposal_id=p["proposal_id"],approve="yes")

    def test_empty_or_duplicate_candidate_rejected(self):
        for candidate in ("", ORIGINAL, " "):
            with self.subTest(candidate=candidate[:5]),self.assertRaises(ValueError):
                self.propose(candidate)


if __name__=="__main__":
    unittest.main()
