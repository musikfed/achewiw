"""Protect public multilingual gallery from accidentally listing private projects.

This allow-list contains ONLY repositories verified to be publicly visible.
Never include private project names or paths in this public test file.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1] / "docs"
BASE = "https://musikfed.github.io/achewiw/works/"
LANGS = ["ru","en","zh","de","hi","fr","fi","ko","ja","it","be"]
PUBLIC = set(["musikfed/achewiw","musikfed/muzikfed","musikfed/YadroKreator","musikfed/Irila_baikal_v1.0","musikfed/wificam_T23N","musikfed/myipcam","musikfed/wifcam_helper","musikfed/micro","musikfed/Hombo3_3.0.0","musikfed/esp32s3devkitC-1N16R8","musikfed/diskshellhdd","musikfed/pitools","musikfed/registry-change-explorer","musikfed/pgsql","musikfed/postgresql1c","musikfed/analitikhhru","musikfed/smilegun","musikfed/smilegun_v2","musikfed/docker-compose-ui","musikfed/ai_video3060_12G"])


class PublicPortfolioSafety(unittest.TestCase):
    def test_public_allowlist_and_previews(self):
        catalog = json.loads((ROOT / "works/catalog.json").read_text(encoding="utf-8"))
        projects = catalog["project"]
        self.assertEqual(set(p["repository"] for p in projects), PUBLIC)
        self.assertEqual(len(projects), len(PUBLIC))
        for project in projects:
            self.assertNotEqual(project.get("visibility"), "private")
            self.assertEqual(set(project["descriptions"]), set(LANGS))
            self.assertTrue(project["showcase"].startswith("https://github.com/"))
            self.assertTrue(project["diagram"].startswith("/achewiw/works/previews/"))
            preview = ROOT / project["diagram"].removeprefix("/achewiw/")
            self.assertTrue(preview.is_file(), str(preview))
            ET.parse(preview)

    def test_eleven_searchable_language_pages(self):
        for lang in LANGS:
            page = ROOT / "works" / ("" if lang == "ru" else lang) / "index.html"
            text = page.read_text(encoding="utf-8")
            self.assertEqual(text.count('class="project"'), len(PUBLIC), lang)
            self.assertEqual(text.count('class="project-art"'), len(PUBLIC), lang)
            self.assertIn('<link rel="canonical" href="' + BASE +
                          ("" if lang == "ru" else lang + "/") + '">', text)
            for other in LANGS:
                self.assertIn('hreflang="' + ("zh-Hans" if other == "zh" else other) + '"', text)
            for contact in ("Feudorlab1", "+7 926 041 1145", "feudor.lab@yandex.ru",
                            "obzorniky@gmail.com", "peginelab.ru"):
                self.assertIn(contact, text)
            self.assertNotIn("raw.githubusercontent.com", text)

    def test_sitemap_and_landing(self):
        sitemap = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
        for lang in LANGS:
            self.assertIn(BASE + ("" if lang == "ru" else lang + "/"), sitemap)
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        for lang in ("ja", "it", "be"):
            self.assertIn("/achewiw/works/" + lang + "/", home)


if __name__ == "__main__":
    unittest.main()
