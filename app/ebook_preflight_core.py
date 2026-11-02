"""EPUB-specific deterministic preflight, separate from legal ReleaseGate.

This module DOES NOT build or parse EPUBs, run EPUBCheck, or approve publication.
Inputs must come from a trusted EPUB build / scan worker. The publisher's
existing ReleaseGate must still approve the final edition snapshot.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EpubResource(BaseModel):
    id: str
    kind: Literal["cover", "image", "diagram", "font", "css", "other"]
    path: str
    meaningful: bool = True
    alt_text: str = ""
    cyrillic_supported: bool | None = None


class EpubScan(BaseModel):
    edition_id: str
    manuscript_hash: str = ""
    epub_created: bool = False
    epubcheck_passed: bool = False
    epubcheck_version: str = ""
    toc_present: bool = False
    metadata_complete: bool = False
    spine_valid: bool = False
    internal_broken_links: list[str] = Field(default_factory=list)
    resources: list[EpubResource] = Field(default_factory=list)
    reading_proof_completed: bool = False


class EpubFinding(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    message: str
    item_id: str | None = None


class EpubScanReport(BaseModel):
    edition_id: str
    findings: list[EpubFinding]
    ready_for_editor_review: bool
    fully_proofed: bool


def evaluate_epub_scan(scan: EpubScan) -> EpubScanReport:
    findings = []

    def warn(code: str, message: str, item_id: str | None = None):
        findings.append(EpubFinding(
            code=code, severity="warning", message=message, item_id=item_id))

    def error(code: str, message: str, item_id: str | None = None):
        findings.append(EpubFinding(
            code=code, severity="error", message=message, item_id=item_id))

    if not scan.manuscript_hash:
        error("SOURCE_HASH", "No immutable manuscript snapshot")
    if not scan.epub_created:
        error("EPUB_MISSING", "No EPUB artifact was created")
    if not scan.epubcheck_passed:
        error("EPUBCHECK", "EPUBCheck result has not passed")
    if scan.epubcheck_passed and not scan.epubcheck_version:
        error("EPUBCHECK_VERSION", "Validation tool version not recorded")
    if not scan.toc_present:
        error("NAVIGATION", "No valid table of contents")
    if not scan.metadata_complete:
        error("METADATA", "Required publication metadata incomplete")
    if not scan.spine_valid:
        error("READING_ORDER", "Reading order invalid or absent")
    for link in scan.internal_broken_links:
        error("BROKEN_LINK", f"Broken internal link: {link}")
    for r in scan.resources:
        if r.kind in ("image", "diagram") and r.meaningful and not r.alt_text:
            warn("ALT_TEXT", "Meaningful visual needs alternative text", r.id)
        if r.kind == "font" and r.cyrillic_supported is False:
            error("FONT_CYRILLIC", "Font lacks verified Cyrillic glyph coverage", r.id)
    if not scan.reading_proof_completed:
        warn("READER_PROOF", "Author/editor must proof the actual exported EPUB")
    clean = not any(f.severity == "error" for f in findings)
    proofed = clean and scan.reading_proof_completed and not findings
    return EpubScanReport(
        edition_id=scan.edition_id, findings=findings,
        ready_for_editor_review=clean, fully_proofed=proofed)
