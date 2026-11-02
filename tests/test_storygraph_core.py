"""Tests of isolated StoryGraph domain behavior (does not start the server)."""
import unittest

from storygraph_core import StoryGraph, StoryError


class StoryGraphTests(unittest.TestCase):
    def setUp(self):
        self.graph = StoryGraph(title="Театр")
        self.volume = self.graph.add_card("volume", "Том 1")
        self.chapter = self.graph.add_card("chapter", "Глава 1", self.volume.id)
        self.scene = self.graph.add_card("scene", "Фойе", self.chapter.id)
        self.paragraph = self.graph.add_card("paragraph", "Первый", self.scene.id)

    def test_hierarchy_and_tombstone(self):
        other = self.graph.add_card("paragraph", "Второй", self.scene.id)
        self.graph.move_card(other.id, self.scene.id, 0)
        self.assertEqual([c.id for c in self.graph.children(self.scene.id)],
                         [other.id, self.paragraph.id])
        with self.assertRaises(StoryError):
            self.graph.add_card("scene", "Bad", self.volume.id)
        self.graph.delete_card(self.scene.id)
        self.assertFalse(self.graph.is_visible(self.paragraph.id))
        self.graph.restore_card(self.scene.id)
        self.assertTrue(self.graph.is_visible(self.paragraph.id))

    def test_facts_not_modified_by_inversion_of_presentation(self):
        fact = self.graph.add_fact("Роман взял чертёж в 18:10")
        event = self.graph.add_event(10, "Роман забирает конверт", fact_ids=[fact.id])
        self.graph.attach_event(self.scene.id, event.id)
        later = self.graph.add_card("scene", "Находка", self.chapter.id)
        self.graph.disclose(fact.id, later.id, mode="explicit")
        frozen = self.graph.facts[fact.id].model_dump_json()
        self.graph.move_card(later.id, self.chapter.id, 0)
        self.assertEqual(self.graph.facts[fact.id].model_dump_json(), frozen)
        self.assertEqual(self.graph.timeline()[0].id, event.id)

    def test_ai_proposal_never_autowrites(self):
        human = self.graph.write_human(self.paragraph.id, "Авторский текст")
        before = self.graph.active_revisions[self.paragraph.id]
        idea = self.graph.propose_ai(self.paragraph.id, "Вариант ИИ", "qwen3:8b")
        self.assertEqual(self.graph.active_revisions[self.paragraph.id], before)
        self.graph.accept_ai(idea.id)
        self.assertEqual(self.graph.active_revisions[self.paragraph.id], idea.id)
        self.graph.restore_revision(self.paragraph.id, human.id)
        self.assertEqual(self.graph.active_revisions[self.paragraph.id], before)
        self.graph.remove_active_text(self.paragraph.id)
        self.assertNotIn(self.paragraph.id, self.graph.active_revisions)
        self.graph.restore_revision(self.paragraph.id, idea.id)
        self.assertEqual(self.graph.active_revisions[self.paragraph.id], idea.id)
        self.assertEqual(idea.origin, "ai")

    def test_locked_areas_and_explicit_fact_amendment(self):
        fact = self.graph.add_fact("Роман вошёл в театр")
        with self.assertRaises(StoryError):
            self.graph.amend_fact(fact.id, "Никто не вошёл")
        updated = self.graph.amend_fact(fact.id, "Роман вошёл в 18:00",
                                        author_confirmed=True)
        self.assertNotEqual(updated.id, fact.id)
        self.assertEqual(self.graph.facts[fact.id].statement,
                         "Роман вошёл в театр")
        self.paragraph.ai_policy = "off"
        with self.assertRaises(StoryError):
            self.graph.propose_ai(self.paragraph.id, "Text", "qwen3:8b")
        self.paragraph.ai_policy = "suggest"
        proposed = self.graph.propose_ai(self.paragraph.id, "Text", "qwen3:8b")
        self.paragraph.author_locked = True
        with self.assertRaises(StoryError):
            self.graph.accept_ai(proposed.id)

    def test_qa_and_cause_order(self):
        fact = self.graph.add_fact("Найден футляр")
        first = self.graph.add_event(1, "Пропажа", fact_ids=[fact.id])
        with self.assertRaises(StoryError):
            self.graph.add_event(0, "Невозможное следствие",
                                 cause_event_ids=[first.id])
        self.graph.disclose(fact.id, self.scene.id, mode="hint")
        self.scene.active_objects = ["1", "2", "3", "4", "5", "6"]
        issues = self.graph.qa()
        self.assertTrue(any("more than five" in item for item in issues))
        self.assertTrue(any("no explicit payoff" in item for item in issues))
        self.graph.disclose(fact.id, self.scene.id, mode="explicit")
        self.assertFalse(any("no explicit payoff" in item for item in self.graph.qa()))


if __name__ == "__main__":
    unittest.main()
