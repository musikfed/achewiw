"""ACHEWIW experimental edition validation / publication gate.

This module is intentionally detached from the MVP API. It cannot prove
copyright clearance or legal compliance; those require documented human review.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PublicationKind = Literal["fiction", "technical", "scientific_popular", "textbook",
                          "manual", "reference", "performance", "periodical"]
RightsState = Literal["owned", "licensed", "public_domain", "unknown"]
CheckState = Literal["passed", "warning", "needs_review", "failed"]


class CheckResult(BaseModel):
    code: str
    status: CheckState
    description: str
    item_id: str | None = None


class Evidence(BaseModel):
    id: str
    claim_id: str
    title: str
    doi: str | None = None
    url: str | None = None
    locator: str = ""
    verified: bool = False
    # This is a reference record, not a verdict that a scientific claim is true.


class KnowledgeClaim(BaseModel):
    id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    verified_by_author: bool = False


class Asset(BaseModel):
    id: str
    name: str
    kind: Literal["illustration", "font", "diagram", "table", "code", "other"]
    rights: RightsState = "unknown"
    rights_evidence: str = ""
    font_cyrillic_coverage: bool | None = None
    alt_text: str = ""


class SimilarityMatch(BaseModel):
    id: str
    target_card_id: str
    source: str
    # Diagnostic number, never a legal fair-use percentage.
    similarity_percent: float = Field(ge=0, le=100)
    review_approved: bool = False
    review_explanation: str = ""


class SignOff(BaseModel):
    category: Literal["content", "rights", "jurisdiction", "proof"]
    approved_source_hash: str
    reviewer: str
    rationale: str = ""


class EditionCheckInput(BaseModel):
    kind: PublicationKind
    edition_id: str
    source_hash: str
    target_markets: list[str] = Field(default_factory=list)
    claims: list[KnowledgeClaim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    matches: list[SimilarityMatch] = Field(default_factory=list)
    approved_manuscript: bool = False
    epub_file_created: bool = False
    epubcheck_passed: bool = False
    navigation_checked: bool = False
    accessibility_checked: bool = False
    signoffs: list[SignOff] = Field(default_factory=list)


class GateReport(BaseModel):
    edition_id: str
    checks: list[CheckResult]
    can_release: bool
    blocking_codes: list[str]


def evaluate_edition(data: EditionCheckInput) -> GateReport:
    """Deterministic guardrail, NOT a scientific/legal/copyright evaluator."""
    checks: list[CheckResult] = []

    def add(code: str, status: CheckState, message: str, item: str | None = None):
        checks.append(CheckResult(code=code, status=status,
                                  description=message, item_id=item))

    if not data.source_hash:
        add("SOURCE_SNAPSHOT", "failed", "Нет зафиксированного снимка рукописи")
    if not data.approved_manuscript:
        add("MANUSCRIPT_APPROVAL", "failed", "Рукопись ещё не принята автором")

    evidence_by_id = {e.id: e for e in data.evidence}
    for claim in data.claims:
        if not claim.evidence_ids:
            add("UNSUPPORTED_CLAIM", "needs_review",
                "Утверждение без источников; проверить и обосновать", claim.id)
        if any(id not in evidence_by_id for id in claim.evidence_ids):
            add("BROKEN_REFERENCE", "failed", "Нет записи о цитируемом источнике", claim.id)
        for evidence_id in claim.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence and not evidence.verified:
                add("UNVERIFIED_REFERENCE", "needs_review",
                    "Библиография/место цитирования требуют проверки", evidence.id)
        if not claim.verified_by_author:
            add("CLAIM_REVIEW", "needs_review", "Автор не подтвердил утверждение", claim.id)

    for asset in data.assets:
        if asset.rights == "unknown":
            add("ASSET_RIGHTS", "needs_review",
                "Нет подтверждения прав на изображение/шрифт/код", asset.id)
        if asset.rights in ("licensed", "public_domain") and not asset.rights_evidence:
            add("RIGHTS_EVIDENCE", "needs_review",
                "Требуется документ о лицензии или статусе общественного достояния",
                asset.id)
        if asset.kind in ("illustration", "diagram") and not asset.alt_text:
            add("ALT_TEXT", "warning", "Нужна текстовая альтернатива", asset.id)
        if asset.kind == "font" and asset.font_cyrillic_coverage is False:
            add("FONT_CYRILLIC", "failed", "Шрифт не поддерживает кириллицу", asset.id)

    for match in data.matches:
        if not (match.review_approved and match.review_explanation.strip()):
            add("SIMILARITY_REVIEW", "needs_review",
                f"Совпадение {match.similarity_percent:.1f}%: требуется анализ заимствования",
                match.id)

    if not data.target_markets:
        add("TARGET_JURISDICTION", "needs_review",
            "Не указана территория распространения для проверки требований")

    if not data.epub_file_created:
        add("EPUB_MISSING", "failed", "EPUB ещё не создан")
    if not data.epubcheck_passed:
        add("EPUBCHECK", "failed", "EPUBCheck не подтвердил правильность EPUB")
    if not data.navigation_checked:
        add("EPUB_NAVIGATION", "failed", "Не проверены оглавление и внутренние ссылки")
    if not data.accessibility_checked:
        add("ACCESSIBILITY", "needs_review", "Нужен просмотр доступности")

    categories = {s.category for s in data.signoffs
                  if s.approved_source_hash == data.source_hash
                  and s.reviewer.strip()}
    for category in ("content", "rights", "jurisdiction", "proof"):
        if category not in categories:
            add("SIGNOFF_" + category.upper(), "needs_review",
                f"Нет действующего утверждения автора/редактора: {category}")

    blockers = [c.code for c in checks
                if c.status in ("failed", "needs_review")]
    return GateReport(edition_id=data.edition_id, checks=checks,
                      can_release=not blockers, blocking_codes=blockers)
