"""Validate host two-mode test before it ever runs against a real host.

These checks do not generate a book and do not claim real Ollama evidence.
"""
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts/host-dual-mode-book-qa.py"

class HostDualModeHarness(unittest.TestCase):
    def test_script_parses(self):
        ast.parse(SCRIPT.read_text(encoding="utf-8"))
    def test_actual_cyrillic_five_word_repeats(self):
        script=ast.parse(SCRIPT.read_text(encoding="utf-8"))
        function=next(node for node in script.body
                      if isinstance(node,ast.FunctionDef) and node.name=="prose_metrics")
        namespace={"re":__import__("re")}
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(SCRIPT),"exec"),namespace)
        result=namespace["prose_metrics"]("Мира нашла запись в журнале. Мира нашла запись в журнале.")
        self.assertEqual(result["words"],10)
        self.assertEqual(result["unique_word_ratio"],0.5)
        self.assertEqual(result["duplicate_5gram_ratio"],0.167)

    def test_distinct_ordinary_and_chat_paths(self):
        text=SCRIPT.read_text(encoding="utf-8")
        for marker in (
          '"/books/generate"',
          '"/projects"',
          '/chats"',
          '/chat-edition"',
          '/ollama-candidate"',
          '/book-control"',
          '/new-outline"',
          '/manuscript.pdf"',
          '/manuscript.docx"',
          'stage',
          'original',
          'sha(',
          '"ordinary"',
          '"chat"',
        ):
            self.assertIn(marker,text)
    def test_only_qa_projects_and_no_destructive_operations(self):
        text=SCRIPT.read_text(encoding="utf-8")
        self.assertIn('QA 11–15',text)
        self.assertIn('qa-reports',text)
        for forbidden in ('docker stop','docker rm','git reset --hard',
                          'DELETE FROM projects','DROP TABLE',
                          'shutil.rmtree','/api/pull'):
            self.assertNotIn(forbidden,text)
if __name__=="__main__":
    unittest.main()
