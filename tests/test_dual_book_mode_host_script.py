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
    def test_distinct_ordinary_and_chat_paths(self):
        text=SCRIPT.read_text(encoding="utf-8")
        for marker in (
          '"/books/generate"',
          '"/projects"',
          '"/chats"',
          '"/chat-edition"',
          '"/ollama-candidate"',
          '"/book-control"',
          '"/new-outline"',
          '"/manuscript.pdf"',
          '"/manuscript.docx"',
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
