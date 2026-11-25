"""Focused routing tests: no model, database, files or GPU required."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "app" / "chat_command_planner.py"
spec = importlib.util.spec_from_file_location("chat_command_planner", path)
mod = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

class CommandPlannerTests(unittest.TestCase):
    def test_generation_uses_existing_stages(self):
        result = mod.plan_command("Напиши книгу: план глав, картинки, EPUB",
                                  workspace="generation", mode="autonomous")
        self.assertEqual(result["status"], "proposal_only")
        self.assertFalse(result["applied"])
        self.assertEqual([x["kind"] for x in result["actions"]],
                         ["generation_brief", "outline_proposal", "draft_proposal",
                          "illustration_proposal", "export_proposal"])
        self.assertTrue(all(x["requires_approval"] for x in result["actions"]))

    def test_editorial_multi_step_is_split(self):
        result = mod.plan_command(
            "Убери повторы, выровняй абзацы, скругли картинки и проверь библиографию",
            workspace="editorial", mode="autonomous", scope="chapter", project_id="p1")
        self.assertEqual([x["kind"] for x in result["actions"]],
                         ["editorial_proposal", "typesetting_proposal",
                          "illustration_proposal", "source_review"])
        self.assertIsNone(result["question"])
        self.assertEqual(result["warnings"], [])

    def test_clarify_only_missing_scope(self):
        result = mod.plan_command("Исправь текст", workspace="editorial",
                                  mode="clarify", project_id="p")
        self.assertIn("Что редактируем", result["question"])
        self.assertFalse(result["applied"])

    def test_autonomous_does_not_guess_target(self):
        result = mod.plan_command("Исправь текст", workspace="editorial",
                                  mode="autonomous", project_id="p")
        self.assertIsNone(result["question"])
        self.assertTrue(result["warnings"])

    def test_invalid_input_is_rejected(self):
        for message in ("", " ", "x" * 10001):
            with self.subTest(message=len(message)), self.assertRaises(ValueError):
                mod.plan_command(message, workspace="generation")
        with self.assertRaises(ValueError):
            mod.plan_command("hi", workspace="other")

if __name__ == "__main__":
    unittest.main()
