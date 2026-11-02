"""Tests for experimental release gate; no legal scanning is implemented."""
import unittest

from release_gate_core import (
    DiagramCard, Evidence, PublicationProfile, ReleaseError, ReleasePacket,
    ReviewCheck, TechnicalBook, TechnicalClaim,
)


class ReleaseGateTests(unittest.TestCase):
    def make_packet(self, kind="fiction", medium="digital"):
        profile = PublicationProfile.default(
            kind=kind, country_codes=["NL"], audience="adult", medium=medium
        )
        return ReleasePacket(
            edition_id="vol-1-v1", manuscript_hash="sha256:text-01",
            assets_hash="sha256:assets-01", profile=profile,
        )

    def sign_checks(self, packet):
        packet.checks = [ReviewCheck(kind=kind) for kind in packet.profile.required_checks]
        for check in packet.checks:
            check.approve(manuscript_hash=packet.manuscript_hash,
                          assets_hash=packet.assets_hash, reviewer="human editor",
                          evidence_refs=["review-report-1"], note="Reviewed scope")

    def test_release_blocked_by_default(self):
        packet = self.make_packet()
        self.assertEqual(packet.status(), "blocked")
        with self.assertRaises(ReleaseError):
            packet.finalize()

    def test_human_reviews_and_author_approval(self):
        packet = self.make_packet()
        self.sign_checks(packet)
        self.assertEqual(packet.status(), "blocked")
        packet.approve_author("author")
        self.assertEqual(packet.status(), "ready")
        self.assertEqual(packet.finalize()["status"], "approved_for_finalization")

    def test_snapshots_invalidate_approvals(self):
        packet = self.make_packet()
        self.sign_checks(packet)
        packet.approve_author("author")
        packet.change_snapshot(manuscript_hash="sha256:text-02")
        self.assertEqual(packet.status(), "blocked")
        self.assertTrue(any(check.status == "stale" for check in packet.checks))
        self.assertFalse(packet.author_approved)

    def test_tampered_check_not_accepted(self):
        packet = self.make_packet()
        self.sign_checks(packet)
        packet.checks[0].reviewer = None
        packet.approve_author("author")
        self.assertIn("human review evidence", "; ".join(packet.unresolved()))

    def test_technical_requires_more_checks(self):
        packet = self.make_packet(kind="technical", medium="print")
        self.assertIn("technical_verification", packet.profile.required_checks)
        self.assertIn("print_preflight", packet.profile.required_checks)
        with self.assertRaises(ReleaseError):
            packet.checks[0].approve(
                manuscript_hash="a", assets_hash="b", reviewer="",
                evidence_refs=[], note="")

    def test_diagram_claim_traceability(self):
        book = TechnicalBook(title="Docker")
        source = Evidence(kind="vendor_doc", title="Official docs", uri="https://example.test/docs")
        book.add_evidence(source)
        claim = TechnicalClaim(statement="This is a testable claim", evidence_ids=[source.id])
        book.add_claim(claim)
        diagram = DiagramCard(chapter_id="c1", title="Pipeline", format="mermaid",
                              source_text="flowchart LR\n A --> B", claim_ids=[claim.id])
        book.add_diagram(diagram)
        self.assertTrue(book.report())
        book.verify_claim(claim.id, reviewer="technical editor")
        diagram.alt_text = "A connects to B"
        self.assertEqual(book.report(), [])
        with self.assertRaises(ReleaseError):
            book.add_diagram(DiagramCard(chapter_id="c1", title="bad", format="dot",
                                         source_text="digraph G{}", claim_ids=["missing"]))


if __name__ == "__main__":
    unittest.main()
