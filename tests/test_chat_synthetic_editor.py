"""Auto-generated minimal chapter: no author interaction or LLM needed."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import chat_synthetic_editor as edit

ORIGINAL=("Глава 1. Зачем диску головка?\n\n"
          "Жёсткий диск хранит данные на вращающихся магнитных пластинах. "
          "Головка читает изменения намагниченности поверхности.\n\n"
          "В общем, я решил вам рассказать, что головка нужна для чтения данных.")
REVISED=("Глава 1. Зачем диску головка?\n\n"
         "Жёсткий диск хранит данные на вращающихся магнитных пластинах. "
         "Головка читает изменения намагниченности поверхности.\n\n"
         "Головка преобразует магнитный сигнал в данные для контроллера.")


class SyntheticChapterReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)
        self.book=edit.create(self.path,"synthetic-book",ORIGINAL)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_cycle_and_reversible_acceptance(self):
        p=edit.propose(self.path,"synthetic-book",self.book["chapter_id"],REVISED)
        self.assertIn("-В общем, я решил вам рассказать",p["diff"])
        self.assertIn("+Головка преобразует",p["diff"])
        self.assertEqual(edit.read(self.path,"synthetic-book",self.book["chapter_id"])["text"],ORIGINAL)
        accepted=edit.decide(self.path,"synthetic-book",self.book["chapter_id"],
                             p["proposal_id"],approve=True)
        self.assertTrue(accepted["changed"])
        self.assertEqual(edit.read(self.path,"synthetic-book",self.book["chapter_id"])["text"],REVISED)
        rolled=edit.restore(self.path,"synthetic-book",self.book["chapter_id"],self.book["revision_id"])
        self.assertNotEqual(rolled["head_id"],self.book["revision_id"])
        self.assertEqual(edit.read(self.path,"synthetic-book",self.book["chapter_id"])["text"],ORIGINAL)

    def test_rejection_preserves_original(self):
        p=edit.propose(self.path,"synthetic-book",self.book["chapter_id"],REVISED)
        result=edit.decide(self.path,"synthetic-book",self.book["chapter_id"],
                           p["proposal_id"],approve=False)
        self.assertFalse(result["changed"])
        self.assertEqual(edit.read(self.path,"synthetic-book",self.book["chapter_id"])["text"],ORIGINAL)

    def test_stale_proposal_rejected(self):
        a=edit.propose(self.path,"synthetic-book",self.book["chapter_id"],REVISED)
        b=edit.propose(self.path,"synthetic-book",self.book["chapter_id"],"Другая редакция")
        edit.decide(self.path,"synthetic-book",self.book["chapter_id"],a["proposal_id"],approve=True)
        with self.assertRaisesRegex(ValueError,"Stale proposal"):
            edit.decide(self.path,"synthetic-book",self.book["chapter_id"],b["proposal_id"],approve=True)

    def test_project_isolation(self):
        with self.assertRaises(ValueError):
            edit.read(self.path,"other-project",self.book["chapter_id"])

    def test_explicit_approval_required(self):
        p=edit.propose(self.path,"synthetic-book",self.book["chapter_id"],REVISED)
        with self.assertRaises(ValueError):
            edit.decide(self.path,"synthetic-book",self.book["chapter_id"],
                        p["proposal_id"],approve="yes")


if __name__=="__main__":
    unittest.main()
