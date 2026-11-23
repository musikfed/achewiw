#!/usr/bin/env python3
"""ACHEWIW 00-Д: read-only document publication acceptance gate.

Checks an evidence register. It NEVER reads manuscripts, opens live SQLite,
modifies repositories, starts services, installs dependencies or grants release.
Use --expect-blocked for CI while open defects exist; --release is fail-closed.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "quality" / "document-release-00d.json"
VALID_STATUSES = {"pass", "known_fail", "not_tested", "not_implemented"}
VALID_PRIORITIES = {"P0", "P1", "P2"}


def check_register(register: dict) -> dict:
    """Validate the register and compute readiness WITHOUT treating CI as release."""
    if not isinstance(register, dict) or register.get("schema_version") != 1:
        raise ValueError("Unexpected acceptance register schema")
    checks = register.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("Acceptance criteria missing")

    ids = set()
    for record in checks:
        if not isinstance(record, dict):
            raise ValueError("Invalid acceptance criterion")
        ident = record.get("id")
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError("Duplicate or missing criterion id")
        ids.add(ident)
        if record.get("priority") not in VALID_PRIORITIES:
            raise ValueError(f"{ident}: invalid priority")
        if record.get("status") not in VALID_STATUSES:
            raise ValueError(f"{ident}: invalid status")
        if not isinstance(record.get("criterion"), str) or not record["criterion"].strip():
            raise ValueError(f"{ident}: missing acceptance criterion")
        evidence = record.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(ref, str) and ref.strip() for ref in evidence
        ):
            raise ValueError(f"{ident}: missing evidence or source reference")

    ci = register.get("baseline_ci")
    if not isinstance(ci, dict) or not isinstance(ci.get("url"), str) or not ci["url"].startswith(
        "https://github.com/musikfed/achewiw/actions/runs/"
    ):
        raise ValueError("Baseline GitHub Actions evidence URL missing")
    if not all(isinstance(ci.get(key), int) and ci[key] >= 0 for key in
               ("passed", "skipped", "xfailed")):
        raise ValueError("Invalid baseline CI counts")

    failures = [c for c in checks
                if c["priority"] in {"P0", "P1"} and c["status"] != "pass"]
    approval = register.get("manual_signoff")
    if not isinstance(approval, dict):
        raise ValueError("Manual approval record missing")
    approved = (
        approval.get("approved") is True
        and isinstance(approval.get("reviewer"), str)
        and bool(approval["reviewer"].strip())
        and isinstance(approval.get("evidence_ref"), str)
        and bool(approval["evidence_ref"].strip())
    )
    # A human review is required in addition to synthetic CI.
    reasons = [f"{c['id']} {c['priority']}: {c['status']}" for c in failures]
    if not approved:
        reasons.append("MANUAL: no verified editorial signoff recorded")
    return {
        "stage": "00-Д",
        "result": "BLOCKED" if reasons else "READY_FOR_HUMAN_REVIEW",
        "checks_total": len(checks),
        "statuses": dict(Counter(c["status"] for c in checks)),
        "blockers": reasons,
        "note": "Readiness register, not proof of normative ГОСТ compliance or a publication authorization.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--expect-blocked", action="store_true",
                      help="CI check: current unresolved defects must block release")
    mode.add_argument("--release", action="store_true",
                      help="Fail closed if any criterion or manual signoff is missing")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = check_register(json.loads(args.manifest.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        print(f"INVALID ACCEPTANCE REGISTER: {exc}")
        return 4

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ACHEWIW {report['stage']} · {report['result']}")
        print(f"Criteria: {report['checks_total']}; statuses: {report['statuses']}")
        for item in report["blockers"]:
            print("  BLOCK:", item)
        print(report["note"])

    if args.expect_blocked:
        return 0 if report["result"] == "BLOCKED" else 3
    if args.release:
        return 0 if report["result"] == "READY_FOR_HUMAN_REVIEW" else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
