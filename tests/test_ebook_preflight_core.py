import unittest

from ebook_preflight_core import EpubResource, EpubScan, evaluate_epub_scan


class EpubPreflightTests(unittest.TestCase):
    def base(self):
        return EpubScan(
            edition_id="book-1",
            manuscript_hash="hash001",
            epub_created=True, epubcheck_passed=True,
            epubcheck_version="5.4.0",
            toc_present=True, metadata_complete=True, spine_valid=True,
            reading_proof_completed=True,
            resources=[EpubResource(id="d1", kind="diagram", path="images/diagram.svg",
                                    alt_text="Directed workflow from input to output")],
        )

    def test_clean_scan(self):
        report = evaluate_epub_scan(self.base())
        self.assertTrue(report.fully_proofed)
        self.assertTrue(report.ready_for_editor_review)

    def test_broken_links_prevent_proof(self):
        scan = self.base()
        scan.internal_broken_links.append("chapter2.xhtml#nonexistent")
        report = evaluate_epub_scan(scan)
        self.assertFalse(report.ready_for_editor_review)
        self.assertIn("BROKEN_LINK", [f.code for f in report.findings])

    def test_font_cyrillic_is_not_optional(self):
        scan = self.base()
        scan.resources.append(EpubResource(id="f", kind="font",
                                           path="fonts/latin.woff2",
                                           cyrillic_supported=False))
        self.assertFalse(evaluate_epub_scan(scan).ready_for_editor_review)

    def test_missing_alt_text_warns_and_blocks_fully_proofed(self):
        scan = self.base()
        scan.resources[0].alt_text = ""
        result = evaluate_epub_scan(scan)
        self.assertTrue(result.ready_for_editor_review)
        self.assertFalse(result.fully_proofed)
        self.assertIn("ALT_TEXT", [f.code for f in result.findings])

    def test_unproofed_is_not_final(self):
        scan = self.base()
        scan.reading_proof_completed = False
        result = evaluate_epub_scan(scan)
        self.assertTrue(result.ready_for_editor_review)
        self.assertFalse(result.fully_proofed)


if __name__ == "__main__":
    unittest.main()
