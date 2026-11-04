"""Offline structural checks for the editorial, multi-display ACHEWIW UI.

Run without browser/server: python3 -m unittest discover -s tests
        -p 'test_editorial_layout.py'
"""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/style.css").read_text(encoding="utf-8")


class Structure(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.mismatch = []
        self.classes = set()
        self.ids = set()
        self.stylesheet_urls = []
        self.script_urls = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("class"):
            self.classes.update(attributes["class"].split())
        if attributes.get("id"):
            self.ids.add(attributes["id"])
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.stylesheet_urls.append(attributes.get("href", ""))
        if tag == "script" and attributes.get("src"):
            self.script_urls.append(attributes["src"])
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.mismatch.append((self.stack[-1] if self.stack else None, tag))
        else:
            self.stack.pop()


class EditorialLayoutTests(unittest.TestCase):
    def test_html_tree_is_balanced(self):
        parser = Structure()
        parser.feed(HTML)
        parser.close()
        self.assertEqual(parser.mismatch, [])
        self.assertEqual(parser.stack, [])
        self.assertTrue({"workspace", "page-shell", "work-navigator",
                         "hero-copy", "monitor-grid", "export-grid"} <= parser.classes)
        self.assertTrue({"editor", "bookPreview", "monitorCpu", "storyText",
                         "bookProduction", "bookDraftPreview", "bookGenerate", "newProject", "monitorToggle", "monitorGrid", "writingBlockList", "runContinuity", "polishScene"} <= parser.ids)

    def test_all_assets_are_busted_to_one_visual_release(self):
        parser = Structure()
        parser.feed(HTML)
        urls = parser.stylesheet_urls + parser.script_urls
        self.assertEqual(len(urls), 5)
        self.assertTrue(all("v=20261105-longbook-v11" in u for u in urls))
        self.assertTrue(all(u.startswith("/static/") for u in urls))

    def test_typography_and_responsive_layout_rules(self):
        self.assertIn('grid-template-columns:var(--nav-width) minmax(0,1fr)', CSS)
        self.assertIn("font-family:var(--book)", CSS)
        self.assertIn(".work-navigator button.nav-all", CSS)
        self.assertIn("repeat(auto-fit,minmax(min(100%,225px),1fr))", CSS)
        self.assertIn(":focus-visible", CSS)
        self.assertIn("prefers-reduced-motion:reduce", CSS)
        for viewport in (2200, 1300, 1050, 720, 430):
            self.assertRegex(CSS, rf"@media\s*\((?:min|max)-width:{viewport}px\)")
        self.assertIn(".actions >", CSS)
        self.assertIn("min-height:44px", CSS)

    def test_monitor_can_fold_and_uses_compact_card_spacing(self):
        self.assertIn('class="monitor-controls"', HTML)
        self.assertIn('aria-expanded="true"', HTML)
        self.assertIn("monitor-collapsed .monitor-grid", CSS)
        self.assertIn(".monitor-item{", CSS)

    def test_css_braces_are_balanced(self):
        cleaned = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
        self.assertEqual(cleaned.count("{"), cleaned.count("}"))


if __name__ == "__main__":
    unittest.main()
