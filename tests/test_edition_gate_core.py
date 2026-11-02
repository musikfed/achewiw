"""Release gating: diagnostics do not masquerade as legal certification."""
import unittest

from edition_gate_core import (
    Asset, EditionCheckInput, Evidence, KnowledgeClaim, SignOff,
    SimilarityMatch, evaluate_edition,
)


class ReleaseGateTests(unittest.TestCase):
    def good_edition(self):
        return EditionCheckInput(
            kind="technical", edition_id="ed-1", source_hash="sha256-abc",
            target_markets=["NL"],
            approved_manuscript=True, epub_file_created=True,
            epubcheck_passed=True, navigation_checked=True,
            accessibility_checked=True,
            evidence=[Evidence(id="ref-1", claim_id="c1", title="Reference",
                               doi="10.1234/test", verified=True)],
            claims=[KnowledgeClaim(id="c1", text="Claim", evidence_ids=["ref-1"],
                                   verified_by_author=True)],
            assets=[Asset(id="fig-1", name="Flow diagram", kind="diagram",
                          rights="owned", alt_text="Sequence of operations")],
            signoffs=[SignOff(category=category, approved_source_hash="sha256-abc",
                              reviewer="editor")
                      for category in ("content", "rights", "jurisdiction", "proof")],
        )

    def test_accepted_edition_passes_deterministic_gate(self):
        report = evaluate_edition(self.good_edition())
        self.assertTrue(report.can_release)
        self.assertEqual(report.blocking_codes, [])

    def test_similarity_percentage_never_preapproves_usage(self):
        edition = self.good_edition()
        edition.matches.append(SimilarityMatch(
            id="copy", target_card_id="paragraph-1", source="Some source",
            similarity_percent=0.1,
        ))
        self.assertFalse(evaluate_edition(edition).can_release)
        edition.matches[0].review_approved = True
        edition.matches[0].review_explanation = "Permission verified by editor"
        self.assertTrue(evaluate_edition(edition).can_release)

    def test_signoffs_invalidate_when_manuscript_changes(self):
        edition = self.good_edition()
        edition.source_hash = "sha256-new"
        report = evaluate_edition(edition)
        self.assertFalse(report.can_release)
        self.assertIn("SIGNOFF_PROOF", report.blocking_codes)

    def test_unverified_source_is_review(self):
        edition = self.good_edition()
        edition.evidence[0].verified = False
        report = evaluate_edition(edition)
        self.assertIn("UNVERIFIED_REFERENCE", report.blocking_codes)

    def test_bad_epub_and_font_block_release(self):
        edition = self.good_edition()
        edition.epubcheck_passed = False
        edition.assets.append(Asset(
            id="font1", name="Font", kind="font", rights="owned",
            font_cyrillic_coverage=False))
        report = evaluate_edition(edition)
        self.assertIn("EPUBCHECK", report.blocking_codes)
        self.assertIn("FONT_CYRILLIC", report.blocking_codes)


if __name__ == "__main__":
    unittest.main()
