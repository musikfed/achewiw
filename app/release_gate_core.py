"""ACHEWIW experimental publication gate and technical-book data models.

No legal judgments, plagiarism scoring, LLM calls, network access, or publishing
actions are implemented here. This is an explicit, auditable release decision
model that can be connected to a future UI and verification workers.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReleaseError(ValueError):
    """Release cannot proceed."""


PublicationType = Literal["fiction", "technical", "hybrid"]
CheckKind = Literal[
    "canon_consistency", "citations_rights", "similarity_triage",
    "jurisdiction_review", "safety_accuracy", "technical_verification",
    "accessibility", "print_preflight",
]
CheckStatus = Literal["pending", "needs_review", "passed", "failed", "stale"]
GateStatus = Literal["blocked", "ready"]


class PublicationProfile(BaseModel):
    name: str
    kind: PublicationType
    country_codes: list[str] = Field(min_length=1)
    audience: str = Field(min_length=1)
    medium: Literal["print", "digital", "both"] = "both"
    policy_version: str = Field(min_length=1)
    required_checks: list[CheckKind]

    @classmethod
    def default(cls, kind: PublicationType,
                country_codes: list[str],
                audience: str = "general",
                medium: Literal["print", "digital", "both"] = "both") -> "PublicationProfile":
        checks: list[CheckKind] = [
            "canon_consistency", "citations_rights", "similarity_triage",
            "jurisdiction_review", "safety_accuracy",
        ]
        if kind in ("technical", "hybrid"):
            checks.append("technical_verification")
        checks.append("accessibility")
        if medium != "digital":
            checks.append("print_preflight")
        return cls(
            name=kind, kind=kind, country_codes=country_codes,
            audience=audience, medium=medium,
            policy_version="rfc-0004-draft",
            required_checks=checks,
        )


class ReviewCheck(BaseModel):
    id: str = Field(default_factory=new_id)
    kind: CheckKind
    status: CheckStatus = "pending"
    manuscript_hash: str = ""
    assets_hash: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    reviewer: str | None = None
    reviewed_at: str | None = None
    note: str = ""

    def approve(self, *, manuscript_hash: str, assets_hash: str,
                reviewer: str, evidence_refs: list[str], note: str) -> None:
        if not reviewer.strip() or not note.strip() or not evidence_refs:
            raise ReleaseError("Approval needs identified reviewer, evidence and note")
        self.manuscript_hash = manuscript_hash
        self.assets_hash = assets_hash
        self.status = "passed"
        self.evidence_refs = evidence_refs
        self.reviewer = reviewer
        self.reviewed_at = utcnow()
        self.note = note


class ReleasePacket(BaseModel):
    edition_id: str = Field(min_length=1)
    manuscript_hash: str = Field(min_length=1)
    assets_hash: str = Field(min_length=1)
    profile: PublicationProfile
    checks: list[ReviewCheck] = Field(default_factory=list)
    author_approved: bool = False
    author_name: str | None = None
    author_approval_at: str | None = None

    def index(self) -> dict[CheckKind, ReviewCheck]:
        by_kind: dict[CheckKind, ReviewCheck] = {}
        for check in self.checks:
            if check.kind in by_kind:
                raise ReleaseError(f"Duplicate check: {check.kind}")
            by_kind[check.kind] = check
        return by_kind

    def unresolved(self) -> list[str]:
        checks = self.index()
        problems: list[str] = []
        for kind in self.profile.required_checks:
            check = checks.get(kind)
            if check is None:
                problems.append(f"{kind}: missing review")
            elif check.status != "passed":
                problems.append(f"{kind}: {check.status}")
            elif check.manuscript_hash != self.manuscript_hash or check.assets_hash != self.assets_hash:
                problems.append(f"{kind}: stale snapshot")
            elif not check.reviewer or not check.reviewed_at or not check.evidence_refs or not check.note:
                problems.append(f"{kind}: no human review evidence")
        if not self.profile.country_codes:
            problems.append("publication jurisdictions not specified")
        if not self.author_approved or not self.author_name or not self.author_approval_at:
            problems.append("author approval is missing")
        return problems

    def approve_author(self, author_name: str) -> None:
        if not author_name.strip():
            raise ReleaseError("Name must be provided for release sign-off")
        self.author_name = author_name
        self.author_approval_at = utcnow()
        self.author_approved = True

    def change_snapshot(self, *, manuscript_hash: str | None = None,
                        assets_hash: str | None = None) -> None:
        changed = False
        if manuscript_hash is not None and manuscript_hash != self.manuscript_hash:
            self.manuscript_hash = manuscript_hash
            changed = True
        if assets_hash is not None and assets_hash != self.assets_hash:
            self.assets_hash = assets_hash
            changed = True
        if changed:
            self.author_approved = False
            self.author_name = None
            self.author_approval_at = None
            for check in self.checks:
                if check.status == "passed":
                    check.status = "stale"

    def status(self) -> GateStatus:
        return "blocked" if self.unresolved() else "ready"

    def finalize(self) -> dict:
        problems = self.unresolved()
        if problems:
            raise ReleaseError("Final release blocked: " + "; ".join(problems))
        return {
            "edition_id": self.edition_id,
            "manuscript_hash": self.manuscript_hash,
            "assets_hash": self.assets_hash,
            "profile": self.profile.model_dump(),
            "approved_at": utcnow(),
            "author": self.author_name,
            "checks": [check.model_dump() for check in self.checks],
            "status": "approved_for_finalization",
            "warning": "This record is an approval audit, not a legal certificate.",
        }


class Evidence(BaseModel):
    id: str = Field(default_factory=new_id)
    kind: Literal["standard", "vendor_doc", "research", "test", "experiment", "other"]
    title: str = Field(min_length=1)
    uri: str = ""
    version: str = ""
    retrieved_at: str = Field(default_factory=utcnow)
    license: str = ""
    notes: str = ""


class TechnicalClaim(BaseModel):
    id: str = Field(default_factory=new_id)
    statement: str = Field(min_length=1)
    valid_for: str = ""
    status: Literal["proposed", "verified", "disputed", "outdated"] = "proposed"
    evidence_ids: list[str] = Field(default_factory=list)
    reviewed_by: str | None = None


class DiagramCard(BaseModel):
    id: str = Field(default_factory=new_id)
    chapter_id: str
    title: str = Field(min_length=1)
    format: Literal["mermaid", "dot", "plantuml", "svg", "tikz"]
    source_text: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    caption: str = ""
    alt_text: str = ""
    accepted: bool = False
    render_sha256: str | None = None


class TechnicalBook(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    claims: dict[str, TechnicalClaim] = Field(default_factory=dict)
    diagrams: dict[str, DiagramCard] = Field(default_factory=dict)

    def add_evidence(self, evidence: Evidence) -> None:
        self.evidence[evidence.id] = evidence

    def add_claim(self, claim: TechnicalClaim) -> None:
        if any(e not in self.evidence for e in claim.evidence_ids):
            raise ReleaseError("Claim must reference existing evidence")
        self.claims[claim.id] = claim

    def verify_claim(self, claim_id: str, reviewer: str) -> None:
        claim = self.claims[claim_id]
        if not reviewer.strip() or not claim.evidence_ids:
            raise ReleaseError("Verified claims need reviewer and evidence")
        if any(e not in self.evidence for e in claim.evidence_ids):
            raise ReleaseError("Missing evidence")
        claim.status = "verified"
        claim.reviewed_by = reviewer

    def add_diagram(self, diagram: DiagramCard) -> None:
        if any(c not in self.claims for c in diagram.claim_ids):
            raise ReleaseError("Diagram references unknown claims")
        self.diagrams[diagram.id] = diagram

    def report(self) -> list[str]:
        issues: list[str] = []
        for claim in self.claims.values():
            if claim.status != "verified":
                issues.append(f"Claim {claim.id}: {claim.status}")
            if any(e not in self.evidence for e in claim.evidence_ids):
                issues.append(f"Claim {claim.id}: missing source")
        for diagram in self.diagrams.values():
            if not diagram.alt_text:
                issues.append(f"Diagram {diagram.id}: missing accessible description")
            if any(c not in self.claims or self.claims[c].status != "verified"
                   for c in diagram.claim_ids):
                issues.append(f"Diagram {diagram.id}: unverified claims")
        return issues
