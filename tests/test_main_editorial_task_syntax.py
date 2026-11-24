"""Regression for missing concatenation in the editorial QA prompt.
Does not start ACHEWIW, Ollama, Docker, databases or server jobs.
"""
import ast
from pathlib import Path
import unittest


MAIN = Path(__file__).resolve().parent.parent / "app" / "main.py"


class EditorialPromptSyntaxRegression(unittest.TestCase):
    def test_no_literal_string_called_as_a_function(self):
        source = MAIN.read_text(encoding="utf-8")
        self.assertIn("Это редакторский ОТЧЁТ", source)
        tree = ast.parse(source, filename=str(MAIN))
        suspicious = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Constant)
            and isinstance(node.func.value, str)
        ]
        self.assertEqual(
            suspicious, [],
            "A string literal is invoked as a function. "
            "Check a missing '+' before a parenthesized expression at lines "
            + str(suspicious),
        )

    def test_editorial_instruction_branch_is_explicitly_concatenated(self):
        source = MAIN.read_text(encoding="utf-8")
        self.assertIn(
            '"Если нет достаточных данных — отметь неопределённость. "\n'
            '            + ("Проведи научно-техническую проверку:',
            source,
        )


if __name__ == "__main__":
    unittest.main()
