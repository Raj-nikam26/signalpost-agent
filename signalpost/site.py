"""Static, dependency-free profile site: find, compare and verify companies on desktop and mobile.

Data is written as small script files, so the site opens straight from disk and on any static host.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TEMPLATE = Path(__file__).with_name("site_template.html")


def _dump(value: Any) -> str:
    # "</" is escaped so company-supplied text can never close the script element.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def index_row(envelope: dict[str, Any]) -> dict[str, Any]:
    identity = envelope.get("identity") or {}
    claims = [claim for claim in envelope.get("claims", []) if claim["availability"] == "available"]
    by_key = {claim["key"]: claim for claim in claims}
    periods = sorted({(claim["period"]["fiscal_year"], claim["period"]["accounts_type"]) for claim in claims if claim.get("period")},
                     key=lambda item: (item[0], item[1] == "SELSKAP"), reverse=True)
    year = kind = None
    if periods:
        year, kind = periods[0]

    def figure(field: str) -> Any:
        claim = by_key.get(f"accounts.{kind}.{year}.{field}")
        return claim["value"] if claim else None

    currency = next((claim.get("unit") for claim in claims if claim.get("period") and claim.get("unit")), None)
    row = {
        "org": envelope["organisation_number"], "state": envelope["status"], "name": identity.get("legal_name"), "brand": identity.get("public_brand"),
        "form": identity.get("legal_form"), "status": identity.get("status"), "municipality": identity.get("municipality"),
        "industry": identity.get("industry"), "industry_code": identity.get("industry_code"), "employees": identity.get("employees"),
        "website": identity.get("official_website"), "year": year, "revenue": figure("revenue"), "result": figure("annual_result"),
        "equity": figure("equity"), "currency": currency,
        "jobs": sum(1 for claim in claims if claim["field"] == "job_posting"),
        "profiles": sum(1 for claim in claims if claim["field"] in {"social_profile", "wikidata_item", "wikipedia_article"}),
        "claims": len(claims), "changes": len(envelope.get("changes") or []),
        "has": sorted({claim["family"] for claim in claims}),
        "rating": next((claim["value"]["rating"] for claim in claims if claim["field"] == "review_summary"), None),
    }
    return {key: value for key, value in row.items() if value is not None}


def build_site(envelopes: list[dict[str, Any]], output: Path, report: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    report = report or {}
    companies_dir = output / "data" / "companies"
    companies_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    changes = []
    for envelope in envelopes:
        org = str(envelope["organisation_number"])
        if not org.isalnum():
            continue
        slim = {key: value for key, value in envelope.items() if key != "profile"}
        (companies_dir / f"{org}.js").write_text(f"window.__sp&&window.__sp[{_dump(org)}]&&window.__sp[{_dump(org)}]({_dump(slim)});", encoding="utf-8")
        row = index_row(envelope)
        rows.append(row)
        for change in envelope.get("changes") or []:
            changes.append({"org": org, "name": row.get("name"), **{key: change.get(key) for key in ("change_type", "materiality", "summary", "detected_at", "source_url")}})
    order = {"high": 0, "medium": 1, "low": 2}
    changes.sort(key=lambda item: (order.get(item["materiality"], 3), str(item.get("name"))))
    operations = report.get("operations") or {}
    meta = {
        "completed_at": report.get("completed_at") or max((envelope["completed_at"] for envelope in envelopes), default=""),
        "run_id": report.get("run_id"), "claims": sum(row.get("claims", 0) for row in rows),
        "evidence": sum(len(envelope.get("evidence", [])) for envelope in envelopes), "cost": operations.get("third_party_cost_usd", 0),
        "stats": {
            "Companies": len(rows),
            "Resolved in the register": sum(1 for row in rows if row["state"] == "available"),
            "With filed accounts": sum(1 for row in rows if row.get("year")),
            "With a verified website": sum(1 for row in rows if row.get("website")),
            "With verified open positions": sum(1 for row in rows if row.get("jobs")),
            "Outbound requests": operations.get("requests", "n/a"),
            "Runtime (seconds)": report.get("runtime_seconds", "n/a"),
            "External API cost (USD)": operations.get("third_party_cost_usd", 0),
        },
    }
    (output / "data" / "index.js").write_text(f"window.SIGNALPOST_INDEX={_dump({'companies': rows, 'changes': changes[:500], 'meta': meta})};", encoding="utf-8")
    (output / "data" / "index.json").write_text(json.dumps({"companies": rows, "meta": meta}, ensure_ascii=False), encoding="utf-8")
    (output / "index.html").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return rows
