"""Refresh as a diff: stable claim keys, typed changes, preserved prior evidence, idempotent reruns.

Comparing a snapshot with itself yields no change. A source that failed keeps the last supported value
(flagged as carried forward) instead of erasing it or reporting a false removal.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from .util import canonical_json, short_hash

# Families where a claim that disappears from a healthy source is a real-world change.
REMOVAL_IS_CHANGE = {"legal_identity", "leadership", "workplaces", "group_structure", "official_website", "company_profiles", "contact"}
# Families whose older items simply age out of the fetched window; they are kept as history, not reported as removed.
KEEP_AS_HISTORY = {"annual_accounts", "public_activity"}
MODULE_OF_FAMILY = {
    "legal_identity": "registry", "annual_accounts": "financials", "leadership": "roles", "workplaces": "locations",
    "group_structure": "group", "official_website": "website", "company_profiles": "website", "contact": "website",
    "hiring": "website", "public_activity": "website",
}
HIGH = {"legal_name", "company_status", "legal_form", "accounts_filing", "official_website"}
HIGH_ROLES = {"DAGL", "LEDE", "INNH"}
LOW = {"company_news_item", "registry_updates", "website_title", "job_posting", "open_positions_count", "careers_page"}
TYPED = {
    ("role_holder", "added"): "new_role", ("role_holder", "removed"): "ended_role",
    ("registered_workplace", "added"): "new_location", ("registered_workplace", "removed"): "closed_location",
    ("job_posting", "added"): "new_job", ("job_posting", "removed"): "closed_job",
    ("accounts_filing", "added"): "new_filing", ("company_news_item", "added"): "new_activity",
    ("company_description", "changed"): "changed_description", ("subsidiary", "added"): "new_subsidiary",
    ("subsidiary", "removed"): "removed_subsidiary", ("social_profile", "added"): "new_profile",
    ("official_website", "added"): "new_website", ("legal_name", "changed"): "renamed",
    ("company_status", "changed"): "status_change", ("employees_registered", "changed"): "changed_employee_count",
}
# Coarse group names used by the Builderr reference refresh fixture.
GROUPS = {"annual_accounts": "financials.records", "leadership": "roles.roles", "workplaces": "locations.locations",
          "company_profiles": "website.social_links", "group_structure": "group.structure", "hiring": "hiring.jobs",
          "public_activity": "activity.items", "contact": "website.contact"}
REGISTRY_GROUP = {"employees_registered": "registry.employees", "legal_name": "registry.name", "legal_form": "registry.legal_form",
                  "registered_website": "registry.website", "latest_accounts_year": "registry.latest_submitted_accounts",
                  "business_address": "registry.municipality", "website_title": "website.title", "company_description": "website.description"}


def _group(claim: dict[str, Any]) -> str:
    return REGISTRY_GROUP.get(claim["field"]) or GROUPS.get(claim["family"]) or f"registry.{claim['field']}"


def _materiality(claim: dict[str, Any], kind: str) -> str:
    field = claim["field"]
    if field in HIGH or (field == "role_holder" and isinstance(claim.get("value"), dict) and claim["value"].get("role_code") in HIGH_ROLES):
        return "high"
    if claim["family"] == "annual_accounts":
        return "high" if kind != "added" or field == "accounts_filing" else "medium"
    return "low" if field in LOW else "medium"


def _describe(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "title", "formatted", "url", "description", "code"):
            if value.get(key):
                return str(value[key])[:120]
        return canonical_json(value)[:120]
    return str(value)[:120]


def _module_healthy(envelope: dict[str, Any], family: str) -> bool:
    module = (envelope.get("modules") or {}).get(MODULE_OF_FAMILY.get(family, ""), {})
    return module.get("availability") in {"available", "not_available", "not_applicable", "ambiguous"} and not module.get("transient")


def apply_refresh(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Annotate `current` in place with observation times, changes and carried-forward claims."""
    now = current["run"]["completed_at"]
    if not previous or previous.get("organisation_number") != current["organisation_number"]:
        for claim in current["claims"]:
            claim["first_observed_at"] = claim["last_observed_at"] = now
        current["changes"] = []
        current["change_history"] = []
        current["refresh"] = {"first_run": True, "compared_with_run": None, "previous_completed_at": None, "changes": 0, "carried_forward": 0,
                              "snapshot_sha256": snapshot_hash(current)}
        return current

    old_claims = {claim["key"]: claim for claim in previous.get("claims", [])}
    old_evidence = {item["id"]: item for item in previous.get("evidence", [])}
    new_claims = {claim["key"]: claim for claim in current["claims"]}
    evidence_ids = {item["id"] for item in current["evidence"]}
    changes: list[dict[str, Any]] = []
    carried = 0

    def record(kind: str, claim: dict[str, Any], old: dict[str, Any] | None, new: dict[str, Any] | None) -> None:
        new_ev = [item for item in current["evidence"] if new and item["id"] in new.get("evidence_ids", [])]
        old_ev = [old_evidence[i] for i in (old or {}).get("evidence_ids", []) if i in old_evidence]
        reference = (new_ev or old_ev or [{}])[0]
        label = claim.get("label") or claim["field"]
        if kind == "changed":
            summary = f"{label}: {_describe(old['value'])} → {_describe(new['value'])}"
        elif kind == "added":
            summary = f"New {label.lower()}: {_describe(new['value'])}"
        else:
            summary = f"No longer listed, {label.lower()}: {_describe(old['value'])}"
        changes.append({
            "id": "ch-" + short_hash(current["organisation_number"], claim["key"], kind, canonical_json((old or {}).get("value")), canonical_json((new or {}).get("value"))),
            "organisation_number": current["organisation_number"],
            "change_type": TYPED.get((claim["field"], kind), f"{kind}_{claim['field']}"),
            "kind": kind, "field": claim["field"], "key": claim["key"], "family": claim["family"], "group": _group(claim),
            "materiality": _materiality(claim, kind), "summary": summary,
            "old_value": (old or {}).get("value"), "new_value": (new or {}).get("value"),
            "previous_observed_at": (old or {}).get("last_observed_at"), "detected_at": now,
            "source_url": reference.get("source_url"), "retrieved_at": reference.get("retrieved_at"),
            "effective_at": (new or old or {}).get("effective_date") or reference.get("effective_at") or reference.get("retrieved_at"),
            "source_class": reference.get("source_class"),
            "old_content_sha256": (old_ev[0].get("content_sha256") if old_ev else None),
            "new_content_sha256": (new_ev[0].get("content_sha256") if new_ev else None),
            "old_evidence": old_ev, "new_evidence_ids": [item["id"] for item in new_ev],
        })

    for key, claim in new_claims.items():
        old = old_claims.get(key)
        old_available = bool(old) and old["availability"] == "available"
        if claim["availability"] == "available":
            claim["last_observed_at"] = now
            if old_available:
                claim["first_observed_at"] = old.get("first_observed_at") or previous["run"]["completed_at"]
                if canonical_json(old["value"]) != canonical_json(claim["value"]):
                    record("changed", claim, old, claim)
                    claim["previous_value"] = old["value"]
            else:
                claim["first_observed_at"] = now
                if claim["family"] != "public_activity" or claim["field"] == "company_news_item":
                    record("added", claim, None, claim)
        else:
            claim["first_observed_at"] = (old or {}).get("first_observed_at") or now
            claim["last_observed_at"] = now

    for key, old in old_claims.items():
        if old["availability"] != "available":
            continue
        new = new_claims.get(key)
        if new and new["availability"] == "available":
            continue
        family = old["family"]
        healthy = _module_healthy(current, family)
        if key.startswith("hiring.job.nav."):
            healthy = (current.get("modules") or {}).get("nav_jobs", {}).get("availability") in {"available", "not_available"}
        if healthy and family not in KEEP_AS_HISTORY:
            if not old.get("carried_forward") and (family in REMOVAL_IS_CHANGE or old["field"] == "job_posting"):
                record("removed", old, old, None)
            continue
        kept = dict(old)
        kept["carried_forward"] = True
        kept["carried_reason"] = "retained as history" if healthy else "source unavailable in this run; last supported value retained"
        kept.setdefault("last_observed_at", previous["run"]["completed_at"])
        if key in new_claims:  # replace the unavailable placeholder with the last supported value, keeping the failure visible
            kept["current_availability"] = new_claims[key]["availability"]
            current["claims"] = [claim for claim in current["claims"] if claim["key"] != key]
        current["claims"].append(kept)
        for evidence_id in kept.get("evidence_ids", []):
            if evidence_id in old_evidence and evidence_id not in evidence_ids:
                current["evidence"].append({**old_evidence[evidence_id], "from_previous_run": True})
                evidence_ids.add(evidence_id)
        carried += 1

    current["changes"] = sorted(changes, key=lambda item: ({"high": 0, "medium": 1, "low": 2}[item["materiality"]], item["key"]))
    history = list(previous.get("change_history") or []) + [
        {key: change[key] for key in ("id", "change_type", "key", "summary", "materiality", "detected_at", "old_value", "new_value", "source_url")}
        for change in previous.get("changes") or []]
    current["change_history"] = history[-100:]
    current["refresh"] = {
        "first_run": False, "compared_with_run": previous["run"]["run_id"], "previous_completed_at": previous["run"]["completed_at"],
        "changes": len(changes), "carried_forward": carried, "snapshot_sha256": snapshot_hash(current),
        "previous_snapshot_sha256": (previous.get("refresh") or {}).get("snapshot_sha256"),
    }
    return current


def snapshot_hash(envelope: dict[str, Any]) -> str:
    """Hash of the supported facts only, so two runs over identical source data hash identically."""
    facts = sorted((claim["key"], claim["availability"], canonical_json(claim.get("value"))) for claim in envelope.get("claims", [])
                   if not claim.get("carried_forward"))
    return short_hash(canonical_json(facts), length=40)


# -- state store ----------------------------------------------------------------------------------
def load_envelopes(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if not path or not Path(path).exists():
        return {}
    opener = gzip.open if str(path).endswith(".gz") else open
    rows: dict[str, dict[str, Any]] = {}
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("organisation_number") and isinstance(row.get("claims"), list):
                rows[str(row["organisation_number"])] = row
    return rows


def save_state(state_dir: str | Path, run_id: str, envelopes: list[dict[str, Any]]) -> None:
    """Keep the latest envelope per company plus an immutable, compressed snapshot of every run."""
    directory = Path(state_dir)
    (directory / "snapshots").mkdir(parents=True, exist_ok=True)
    latest_path = directory / "latest.jsonl"
    latest = load_envelopes(latest_path)
    for envelope in envelopes:
        if envelope.get("status") == "failed" and envelope["organisation_number"] in latest:
            continue  # a failed run never overwrites the last good profile
        latest[envelope["organisation_number"]] = envelope
    temporary = latest_path.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for envelope in latest.values():
            handle.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(latest_path)
    snapshot = directory / "snapshots" / f"{run_id}.jsonl.gz"
    if not snapshot.exists():
        with gzip.open(snapshot, "wt", encoding="utf-8") as handle:
            for envelope in envelopes:
                handle.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
