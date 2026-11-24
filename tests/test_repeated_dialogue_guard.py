"""Narrow tests for inter-chapter dialogue loops; no LLM, GPU, or DB tests."""
import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/"app"/"prose_quality.py"
spec=importlib.util.spec_from_file_location("prose_quality",path)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


A="— Мы проверили центральный замок и нашли скрытый механизм под панелью, теперь необходимо проверить вторую дверь."
B="— Ты видел журнал событий после вчерашней аварии и заметил следы взлома на главном узле?"
C="— Теперь я открою чертёж и объясню, почему третья цепь остаётся изолированной и безопасной."

class RepeatDialogueTests(unittest.TestCase):
    def test_two_distinct_past_lines_rejected(self):
        finding=mod.repeated_dialogue_warning(A+"\n"+B+"\nНовый абзац",earlier_chapters="Другой эпизод\n"+A+"\n"+B)
        self.assertIsNotNone(finding)
        with self.assertRaises(mod.ProseDegeneration):
            mod.check_repeated_dialogue(A+"\n"+B,earlier_chapters=A+"\n"+B)

    def test_one_shortish_catchphrase_allowed(self):
        self.assertIsNone(mod.repeated_dialogue_warning(A+"\n"+C,earlier_chapters=A))

    def test_distinct_new_dialogue_allowed(self):
        self.assertIsNone(mod.repeated_dialogue_warning(C,earlier_chapters=A+"\n"+B))

    def test_same_chapter_detected(self):
        self.assertIsNotNone(mod.repeated_dialogue_warning(A+"\n"+B,same_chapter=A+"\n"+B))

    def test_three_times_in_new_generation_rejected(self):
        self.assertIsNotNone(mod.repeated_dialogue_warning(A+"\n"+A+"\n"+A))

    def test_different_dialogue_without_prefix_not_mistaken_for_line(self):
        self.assertIsNone(mod.repeated_dialogue_warning("Слова героя: "+A+"\n"+B,earlier_chapters=A))

if __name__=="__main__":
    unittest.main()
