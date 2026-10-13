#!/usr/bin/env python3
"""Print a run report as a short Markdown summary: python scripts/report_summary.py out/run-report.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    count = report["emitted_envelopes"]
    operations, coverage, refresh = report["operations"], report["coverage"], report["refresh"]
    print(f"### Run `{report['run_id']}`\n")
    print("| Measure | Value |\n|---|---|")
    rows = [
        ("Companies in / envelopes out", f"{report['input']['unique']} / {count}"),
        ("Terminal states", ", ".join(f"{state} {number}" for state, number in report["status_counts"].items())),
        ("Runtime", f"{report['runtime_seconds']} s"),
        ("Outbound requests", f"{operations['requests']} ({operations['requests_per_company_mean']} per company; cap {operations['request_budget']})"),
        ("Requests by source", ", ".join(f"{name} {number}" for name, number in operations["requests_by_source_class"].items())),
        ("Request latency p50 / p95", f"{operations['p50_ms']} ms / {operations['p95_ms']} ms"),
        ("External API cost", f"${operations['third_party_cost_usd']}"),
        ("Published facts", f"{coverage['published_claims_total']} ({coverage['claims_per_company_mean']} per company)"),
        ("Evidence records", str(coverage["evidence_records_total"])),
        ("Website gate", ", ".join(f"{state} {number}" for state, number in report["website_identity_gate"].items())),
        ("Changes detected", f"{refresh['changes_total']} in {refresh['companies_with_changes']} companies ({refresh['compared_with_previous']} compared with a previous snapshot)"),
        ("Validation", "passed" if report["validation"]["passed"] else "FAILED: " + ", ".join(k for k, v in report["validation"]["checks"].items() if not v)),
    ]
    for label, value in rows:
        print(f"| {label} | {value} |")
    print("\n| Section | Companies with facts | Other states |\n|---|---|---|")
    for name, states in report["section_availability"].items():
        others = ", ".join(f"{state} {number}" for state, number in states.items() if state != "available") or "none"
        print(f"| {name} | {states.get('available', 0)} of {count} | {others} |")
    if report["errors"]:
        print("\nErrors recorded: " + ", ".join(f"{name} {number}" for name, number in report["errors"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
