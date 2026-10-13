"""Research one organisation number end to end and assemble its terminal envelope."""
from __future__ import annotations

import time
import traceback
from typing import Any

from . import SCHEMA_VERSION, __version__
from .claims import Dossier
from .footprint import build_footprint
from .http import Fetcher
from .refresh import apply_refresh
from .sources import brreg, external, website
from .synthesis import build_sections, synthesise
from .util import utc_now, valid_org_number

CORE_REQUESTS = 4  # entity, accounts, roles, sub-units: always attempted, never squeezed out by optional work


def collect_company(organisation_number: str, fetcher: Fetcher, *, wikidata: dict[str, Any] | None = None,
                    options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Gather evidence for one company. Never raises: a failure becomes an explicit state in the returned context."""
    options = options or {}
    context: dict[str, Any] = {
        "organisation_number": organisation_number, "dossier": Dossier(organisation_number), "entity": None, "financial_rows": [], "roles": [],
        "locations": [], "site": None, "status": "failed", "started_at": utc_now(), "runtime": 0.0, "requests": 0,
    }
    started = time.monotonic()
    requests_before = fetcher.thread_requests()
    dossier: Dossier = context["dossier"]
    try:
        if not valid_org_number(organisation_number):
            dossier.error("input", "invalid_organisation_number", "Not a valid 9-digit Norwegian organisation number (modulus-11 check failed).")
            dossier.module("registry", "not_applicable", note="invalid organisation number; nothing was looked up")
            context["status"] = "not_applicable"
        else:
            entity = context["entity"] = brreg.collect_entity(dossier, fetcher)
            if entity is None:
                context["status"] = dossier.modules.get("registry", {}).get("availability", "failed")
            else:
                context["status"] = "available"
                context["financial_rows"] = brreg.collect_accounts(dossier, fetcher, entity)
                context["roles"] = brreg.collect_roles(dossier, fetcher)
                context["locations"] = brreg.collect_subunits(dossier, fetcher)
                brreg.collect_group(dossier, fetcher, entity)
                if options.get("registry_updates", True):
                    brreg.collect_updates(dossier, fetcher)
                if options.get("account_history"):
                    brreg.collect_account_years(dossier, fetcher)
                wikidata_site = external.publish_wikidata(dossier, wikidata)
                if options.get("website", True) and not entity.get("slettedato"):
                    related = {str(item["organisation_number"]) for item in context["locations"] if item.get("organisation_number")}
                    context["site"] = website.collect_website(dossier, fetcher, entity, wikidata_site=wikidata_site, related_orgs=related,
                                                              allow_guess=options.get("guess_domains", True))
                if options.get("reviews", True) and not entity.get("slettedato"):
                    external.collect_reviews(dossier, fetcher, entity)
    except Exception as exc:  # noqa: BLE001 - one bad company must not take the batch down
        dossier.error("pipeline", "unhandled_exception", f"{type(exc).__name__}: {exc}"[:300])
        dossier.errors[-1]["trace"] = traceback.format_exc(limit=3)[-600:]
    context["runtime"] = time.monotonic() - started
    context["requests"] = fetcher.thread_requests() - requests_before
    return context


def collect_jobs(context: dict[str, Any], fetcher: Fetcher, gate: external.RateGate | None = None) -> None:
    """NAV job-board lookup for an already collected company. Runs separately because the board is tightly rate-limited."""
    entity = context["entity"]
    if entity is None or entity.get("slettedato"):
        return
    started = time.monotonic()
    requests_before = fetcher.thread_requests()
    try:
        external.collect_nav_jobs(context["dossier"], fetcher, entity, context["locations"], gate=gate)
    except Exception as exc:  # noqa: BLE001
        context["dossier"].error("nav_jobs", "unhandled_exception", f"{type(exc).__name__}: {exc}"[:300])
    context["runtime"] += time.monotonic() - started
    context["requests"] += fetcher.thread_requests() - requests_before


def finalise(context: dict[str, Any], *, run_id: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn a collected context into the terminal envelope: refresh diff, section states, synthesis."""
    organisation_number = context["organisation_number"]
    dossier: Dossier = context["dossier"]
    status = context["status"]
    completed_at = utc_now()
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "organisation_number": organisation_number,
        "status": status,
        "state": "complete" if status == "available" else {"not_available": "not_found", "not_applicable": "not_applicable",
                                                           "blocked": "blocked_policy"}.get(status, "source_error"),
        "run_id": run_id,
        "started_at": context["started_at"],
        "completed_at": completed_at,
        "run": {"run_id": run_id, "started_at": context["started_at"], "completed_at": completed_at,
                "terminal_status": "completed" if status != "failed" else "failed", "agent": f"signalpost-agent/{__version__}"},
        "identity": _identity_block(dossier),
        "claims": list(dossier.claims.values()),
        "evidence": list(dossier.evidence.values()),
        "changes": [],
        "errors": dossier.errors,
        "modules": dossier.modules,
    }
    apply_refresh(envelope, previous)
    envelope["sections"] = build_sections(envelope["claims"], envelope["modules"])
    envelope["summary"] = synthesise(envelope["claims"], envelope["sections"], envelope["changes"], envelope["refresh"])
    envelope["external_footprint"] = build_footprint(organisation_number, envelope["claims"], envelope["evidence"])
    envelope["operations"] = {"requests": context["requests"], "runtime_ms": int(context["runtime"] * 1000), "third_party_cost_usd": 0}
    envelope["profile"] = _reference_profile(organisation_number, context["entity"], dossier, context["financial_rows"], context["roles"],
                                             context["locations"], context["site"])
    return envelope


def research_company(
    organisation_number: str,
    fetcher: Fetcher,
    *,
    run_id: str,
    previous: dict[str, Any] | None = None,
    wikidata: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Research one company end to end. Always returns a terminal envelope."""
    options = options or {}
    context = collect_company(organisation_number, fetcher, wikidata=wikidata, options=options)
    if options.get("nav_jobs", True):
        collect_jobs(context, fetcher)
    return finalise(context, run_id=run_id, previous=previous)


def failed_envelope(organisation_number: str, run_id: str, code: str, message: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Terminal envelope for a company the run could not start (time budget, crash). Still exactly one row per input."""
    now = utc_now()
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "organisation_number": organisation_number, "status": "failed", "state": "budget_exhausted" if code == "time_budget" else "source_error",
        "run_id": run_id, "started_at": now, "completed_at": now,
        "run": {"run_id": run_id, "started_at": now, "completed_at": now, "terminal_status": "failed", "agent": f"signalpost-agent/{__version__}"},
        "identity": {}, "claims": [], "evidence": [], "changes": [],
        "errors": [{"module": "batch", "code": code, "message": message, "at": now}],
        "modules": {"registry": {"state": "budget_exhausted" if code == "time_budget" else "source_error", "availability": "failed",
                                 "retry_count": 0, "final_timestamp": now, "note": message, "transient": True}},
    }
    apply_refresh(envelope, previous)
    envelope["sections"] = build_sections(envelope["claims"], envelope["modules"])
    for section in envelope["sections"].values():
        if section["availability"] != "available":
            section.update({"availability": "failed", "explanation": message})
    envelope["summary"] = synthesise(envelope["claims"], envelope["sections"], envelope["changes"], envelope["refresh"])
    envelope["external_footprint"] = build_footprint(organisation_number, envelope["claims"], envelope["evidence"])
    envelope["operations"] = {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0}
    envelope["profile"] = {"organisation_number": organisation_number, "evidence": {}}
    return envelope


def _identity_block(dossier: Dossier) -> dict[str, Any]:
    def value(key: str) -> Any:
        claim = dossier.claims.get(key)
        return claim["value"] if claim and claim["availability"] == "available" else None

    form = value("identity.legal_form") or {}
    address = value("identity.business_address") or {}
    industry = value("identity.industry") or {}
    block = {
        "legal_name": value("identity.legal_name"), "public_brand": value("website.public_brand"), "legal_form": form.get("code"),
        "status": value("identity.status"), "municipality": address.get("municipality"), "industry_code": industry.get("code"),
        "industry": industry.get("description"), "employees": value("identity.employees"), "official_website": value("website.official"),
    }
    return {key: item for key, item in block.items() if item is not None}


def _record(dossier: Dossier, module: str, source_class: str, value: Any, keys: list[str]) -> dict[str, Any]:
    """Module record in the shape the Builderr reference runner emits, so existing evaluator tooling can read it."""
    state = dossier.modules.get(module, {})
    evidence = next((dossier.evidence[e] for key in keys for e in dossier.claims.get(key, {}).get("evidence_ids", []) if e in dossier.evidence), {})
    status = {"available": "available", "not_available": "not_found", "not_applicable": "not_applicable", "blocked": "blocked",
              "ambiguous": "not_found", "failed": "source_error"}.get(state.get("availability", "failed"), "source_error")
    return {"field": module, "status": status, "source_type": source_class, "source_class": source_class,
            "source_url": evidence.get("source_url"), "retrieved_at": evidence.get("retrieved_at") or state.get("final_timestamp"),
            "content_sha256": evidence.get("content_sha256"), "value": value if status == "available" else None, "note": state.get("note"),
            "retry_count": state.get("retry_count", 0)}


def _reference_profile(org: str, entity: dict[str, Any] | None, dossier: Dossier, financial_rows: list[dict[str, Any]],
                       roles: list[dict[str, Any]], locations: list[dict[str, Any]], site: dict[str, Any] | None) -> dict[str, Any]:
    if entity is None:
        return {"organisation_number": org, "evidence": {name: _record(dossier, name, "official_registry", None, []) for name in dossier.modules}}
    address = entity.get("forretningsadresse") or {}
    industry = entity.get("naeringskode1") or {}
    live = {
        "organisation_number": entity.get("organisasjonsnummer"), "name": entity.get("navn"), "legal_form": (entity.get("organisasjonsform") or {}).get("kode"),
        "employees": entity.get("antallAnsatte") if entity.get("harRegistrertAntallAnsatte") else None, "bankrupt": entity.get("konkurs"),
        "liquidating": entity.get("underAvvikling"), "website": entity.get("hjemmeside"), "industry": industry or None,
        "business_address": entity.get("forretningsadresse"), "postal_address": entity.get("postadresse"),
        "latest_submitted_accounts": entity.get("sisteInnsendteAarsregnskap"),
    }
    records = [{"record_id": row["record_id"], "account_type": row["account_type"], "period": row["period"], "currency": row["currency"],
                "revenue": row.get("revenue"), "operating_result": row.get("operating_result"), "profit_before_tax": row.get("profit_before_tax"),
                "annual_result": row.get("annual_result"), "assets": row.get("total_assets"), "equity": row.get("equity"), "debt": row.get("total_debt")}
               for row in financial_rows]
    evidence = {
        "registry_live": _record(dossier, "registry", "official_registry_live", live, ["identity.legal_name"]),
        "financials": _record(dossier, "financials", "official_annual_accounts", {"records": records}, [key for key in dossier.claims if key.startswith("accounts.")][:1] or ["accounts.latest"]),
        "roles": _record(dossier, "roles", "official_roles", {"roles": [{"name": r["name"], "role_code": r["role_code"], "role": r.get("role"), "inactive": False} for r in roles]},
                         [key for key in dossier.claims if key.startswith("leadership.")][:1]),
        "locations": _record(dossier, "locations", "official_subunits", {"locations": locations}, ["workplaces.count", "workplaces.registered"]),
        "website": _record(dossier, "website", "company_owned", site, ["website.official"]),
    }
    evidence["registry"] = {**evidence["registry_live"], "field": "registry"}
    if "group" in dossier.modules:
        evidence["group"] = _record(dossier, "group", "official_group_structure",
                                    {key: claim["value"] for key, claim in dossier.claims.items() if key.startswith("group.") and claim["availability"] == "available"},
                                    ["group.parent", "group.subsidiary_count", "group.in_group"])
    return {
        "organisation_number": org, "name": entity.get("navn"), "legal_form": live["legal_form"], "employees": live["employees"],
        "bankrupt": entity.get("konkurs"), "liquidating": entity.get("underAvvikling"), "municipality": address.get("kommune"),
        "municipality_number": address.get("kommunenummer"), "industry_code": industry.get("kode"), "industry_label": industry.get("beskrivelse"),
        "website": entity.get("hjemmeside") or "", "latest_submitted_accounts": entity.get("sisteInnsendteAarsregnskap"), "evidence": evidence,
    }
