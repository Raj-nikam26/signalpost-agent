"""Decision-useful synthesis built only from published claims.

Every sentence cites the claims it rests on. No model writes prose here, so nothing can be invented:
if a claim is missing the sentence is not written and the gap is listed under `unknowns`.
"""
from __future__ import annotations

from typing import Any

from .sources.brreg import HEADLINE_ACCOUNT_FIELDS

SECTION_FAMILIES = [
    ("legal_identity", "Legal identity and public brand"),
    ("annual_accounts", "Latest annual accounts and available history"),
    ("leadership", "Leadership"),
    ("workplaces", "Registered workplaces"),
    ("group_structure", "Group structure"),
    ("official_website", "Verified official website"),
    ("company_profiles", "Company-owned profiles"),
    ("contact", "Contact details"),
    ("hiring", "Hiring"),
    ("public_activity", "Dated public activity"),
]


def money(value: Any, currency: str = "NOK") -> str:
    number = float(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    for limit, suffix in ((1e9, "billion"), (1e6, "million"), (1e3, "thousand")):
        if number >= limit:
            return f"{sign}{currency} {number / limit:.1f} {suffix}"
    return f"{sign}{currency} {number:,.0f}"


def build_sections(claims: list[dict[str, Any]], modules: dict[str, Any]) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    for family, title in SECTION_FAMILIES:
        members = [claim for claim in claims if claim["family"] == family]
        available = [claim for claim in members if claim["availability"] == "available"]
        if available:
            state, note = "available", None
        elif members:
            # A family can have several sources. If at least one was checked, the family is "checked, nothing found";
            # every member's explanation is kept so a blocked or failed source stays visible.
            states = {claim["availability"] for claim in members}
            state = next(candidate for candidate in ("ambiguous", "not_available", "blocked", "failed", "not_applicable") if candidate in states)
            note = " ".join(dict.fromkeys(claim["explanation"] for claim in members if claim.get("explanation")))
        elif family == "group_structure":
            state, note = "not_applicable", "The registry does not flag this entity as part of a group."
        elif family in {"company_profiles", "contact", "public_activity", "hiring"}:
            state, note = "not_available", "No verified company-owned or organisation-number-keyed source yielded this information."
        else:
            state, note = "not_available", "The source was not reached in this run."
        sections[family] = {key: value for key, value in {"title": title, "availability": state, "claims": len(available), "explanation": note}.items()
                            if value is not None}
    return sections


def _latest_accounts(claims: dict[str, dict[str, Any]]) -> tuple[str, str, dict[str, dict[str, Any]]] | None:
    periods = sorted({(claim["period"]["fiscal_year"], claim["period"]["accounts_type"]) for claim in claims.values()
                      if claim["family"] == "annual_accounts" and claim["availability"] == "available" and claim.get("period")},
                     key=lambda item: (item[0], item[1] == "SELSKAP"), reverse=True)
    if not periods:
        return None
    year, kind = periods[0]
    return year, kind, {field: claims[f"accounts.{kind}.{year}.{field}"] for field in HEADLINE_ACCOUNT_FIELDS + ("sales_revenue",)
                        if f"accounts.{kind}.{year}.{field}" in claims}


def synthesise(envelope_claims: list[dict[str, Any]], sections: dict[str, Any], changes: list[dict[str, Any]], refresh: dict[str, Any]) -> dict[str, Any]:
    claims = {claim["key"]: claim for claim in envelope_claims}
    sentences: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []

    def val(key: str) -> Any:
        claim = claims.get(key)
        return claim["value"] if claim and claim["availability"] == "available" else None

    def say(topic: str, text: str, keys: list[str], derived: bool = False) -> None:
        used = [claims[key] for key in keys if key in claims and claims[key]["availability"] == "available"]
        if not used:
            return
        sentences.append({"topic": topic, "text": text, "claim_ids": [claim["id"] for claim in used],
                          "evidence_ids": list(dict.fromkeys(e for claim in used for e in claim["evidence_ids"])),
                          **({"derived": True} if derived else {})})

    name = val("identity.legal_name")
    headline = None
    if name:
        form = val("identity.legal_form") or {}
        status = val("identity.status") or "registered"
        industry = val("identity.industry") or {}
        address = val("identity.business_address") or {}
        founded = val("identity.founded_date") or val("identity.registered_date")
        kind = form.get("english") or form.get("description") or "entity"
        text = f"{name} is an active {kind}" if status == "active" else f"{name} is {'an' if kind[0] in 'aeiou' else 'a'} {kind}"
        if form.get("code"):
            text += f" ({form['code']})"
        if address.get("municipality"):
            text += f" based in {str(address['municipality']).title()}"
        if founded:
            text += f", {'founded' if val('identity.founded_date') else 'registered'} in {str(founded)[:4]}"
        text += "."
        if status != "active":
            text += f" The register flags it as {status}."
        if industry.get("description"):
            text += f" Its registered industry is “{industry['description']}” (NACE {industry.get('code')})."
        headline = text
        say("what_it_is", text, ["identity.legal_name", "identity.legal_form", "identity.status", "identity.business_address",
                                 "identity.founded_date", "identity.registered_date", "identity.industry"])
        if status != "active":
            signals.append({"signal": "adverse_status", "severity": "high", "text": f"The register flags this entity as {status}.",
                            "claim_ids": [claims["identity.status"]["id"]]})
    brand = val("website.public_brand")
    if brand:
        say("what_it_is", f"It presents itself publicly as “{brand}”.", ["website.public_brand"])
    description = val("website.description")
    purpose = val("identity.purpose") or val("identity.activity")
    if description:
        say("what_it_does", f"In its own words: “{description[:280]}”", ["website.description"])
    if purpose:
        key = "identity.purpose" if val("identity.purpose") else "identity.activity"
        say("what_it_does", f"Registered purpose: {purpose[:300]}", [key])

    employees = val("identity.employees")
    if employees is not None:
        say("size", f"The register shows {employees:,} employee{'s' if employees != 1 else ''}.", ["identity.employees"])
    capital = val("identity.share_capital")
    if capital and capital.get("amount") is not None:
        say("size", f"Registered capital is {money(capital['amount'], capital.get('currency') or 'NOK')}.", ["identity.share_capital"])

    leaders = [claim for claim in envelope_claims if claim["family"] == "leadership" and claim["availability"] == "available"]
    for code, phrase in (("DAGL", "is led by"), ("INNH", "is owned and run by"), ("LEDE", "The board is chaired by")):
        holders = [claim for claim in leaders if claim["value"].get("role_code") == code]
        if holders:
            names = ", ".join(claim["value"]["name"] for claim in holders[:2])
            role = holders[0]["value"].get("role_english") or holders[0]["value"].get("role")
            text = f"{phrase} {names}." if code == "LEDE" else f"The company {phrase} {names} ({role})."
            say("leadership", text, [claim["key"] for claim in holders[:2]])
    board = [claim for claim in leaders if claim["value"].get("role_code") in {"LEDE", "NEST", "MEDL"}]
    if len(board) > 1:
        say("leadership", f"The register lists {len(board)} board members.", [claim["key"] for claim in board])
    auditor = [claim for claim in leaders if claim["value"].get("role_code") == "REVI"]
    if auditor:
        say("leadership", f"Its registered auditor is {auditor[0]['value']['name']}.", [auditor[0]["key"]])

    count = val("workplaces.count")
    if count:
        places = sorted({claim["value"].get("municipality") for claim in envelope_claims
                         if claim["family"] == "workplaces" and claim["availability"] == "available" and isinstance(claim["value"], dict)
                         and claim["value"].get("municipality")})
        text = f"It has {count} registered workplace{'s' if count != 1 else ''}"
        if places:
            text += " in " + ", ".join(place.title() for place in places[:5]) + (" and elsewhere" if len(places) > 5 else "")
        say("footprint", text + ".", ["workplaces.count"] + [claim["key"] for claim in envelope_claims if claim["family"] == "workplaces"][:6])
    parent = val("group.parent")
    if parent:
        say("group", f"Its direct parent is {parent.get('name')} (organisation number {parent.get('organisation_number')}"
                     + (f", ownership {parent['ownership']}" if parent.get("ownership") else "") + ").", ["group.parent"])
    subsidiaries = val("group.subsidiary_count")
    if subsidiaries:
        say("group", f"It has {subsidiaries['direct']} direct subsidiar{'ies' if subsidiaries['direct'] != 1 else 'y'}"
                     f" ({subsidiaries['all_levels']} group companies across all levels).", ["group.subsidiary_count"])

    latest = _latest_accounts(claims)
    if latest:
        year, kind, figures = latest
        scope = "consolidated group" if kind == "KONSERN" else "company"
        currency = next(iter(figures.values()))["unit"] if figures else "NOK"
        parts = []
        for field, label in (("revenue", "operating revenue"), ("operating_result", "an operating result"), ("annual_result", "an annual result")):
            if field in figures:
                parts.append(f"{label} of {money(figures[field]['value'], currency)}")
        if parts:
            say("financials", f"For fiscal year {year} the {scope} accounts report " + ", ".join(parts) + ".",
                [claim["key"] for field, claim in figures.items() if field in {"revenue", "operating_result", "annual_result"}])
        if "equity" in figures and "total_assets" in figures:
            equity, assets = figures["equity"]["value"], figures["total_assets"]["value"]
            text = f"Equity was {money(equity, currency)} against total assets of {money(assets, currency)}"
            if assets:
                text += f" (equity ratio {equity / assets:.0%}, derived from the two filed figures)"
            say("financials", text + ".", [figures["equity"]["key"], figures["total_assets"]["key"]], derived=True)
            if equity < 0:
                signals.append({"signal": "negative_equity", "severity": "high", "text": f"Filed equity for {year} is negative.",
                                "claim_ids": [figures["equity"]["id"]]})
        if "annual_result" in figures and figures["annual_result"]["value"] < 0:
            signals.append({"signal": "loss_making", "severity": "medium", "text": f"The {year} accounts show a loss.",
                            "claim_ids": [figures["annual_result"]["id"]]})
        previous_key = f"accounts.{kind}.{int(year) - 1}.revenue"
        if "revenue" in figures and val(previous_key) not in (None, 0) and claims[previous_key].get("unit") == currency:
            before, now = val(previous_key), figures["revenue"]["value"]
            say("financials", f"Operating revenue changed {(now - before) / abs(before):+.0%} from {int(year) - 1} to {year} "
                              f"({money(before, currency)} to {money(now, currency)}; derived from the two filed figures).",
                [previous_key, figures["revenue"]["key"]], derived=True)
        filing = val(f"accounts.{kind}.{year}.filing") or {}
        if filing.get("audit_opted_out") or filing.get("not_audited"):
            signals.append({"signal": "unaudited_accounts", "severity": "low", "text": f"The {year} accounts are not audited.",
                            "claim_ids": [claims[f'accounts.{kind}.{year}.filing']['id']]})

    site = val("website.official")
    if site:
        profiles = [claim for claim in envelope_claims if claim["field"] == "social_profile" and claim["availability"] == "available"]
        text = f"Its verified official website is {site}"
        if profiles:
            text += ", which links to its " + ", ".join(claim["value"]["platform"].capitalize() for claim in profiles[:5]) + " profile" + ("s" if len(profiles) > 1 else "")
        say("online_presence", text + ".", ["website.official"] + [claim["key"] for claim in profiles[:5]])
    related = val("website.related")
    if related:
        say("online_presence", f"The website it reported to the register ({related['url']}) identifies a different legal entity, so it is treated as a group or brand site.",
            ["website.related"])
    jobs = [claim for claim in envelope_claims if claim["field"] == "job_posting" and claim["availability"] == "available"]
    if jobs:
        titles = "; ".join(str(claim["value"].get("title"))[:70] for claim in jobs[:3])
        say("hiring", f"It appears to be hiring: {len(jobs)} open position{'s' if len(jobs) != 1 else ''} were verified, including {titles}.",
            [claim["key"] for claim in jobs[:6]])
        signals.append({"signal": "hiring", "severity": "info", "text": f"{len(jobs)} verified open position(s).", "claim_ids": [claim["id"] for claim in jobs[:6]]})
    news = sorted((claim for claim in envelope_claims if claim["field"] == "company_news_item" and claim["availability"] == "available"),
                  key=lambda claim: claim["value"].get("published", ""), reverse=True)
    if news:
        say("activity", f"Latest dated item on its own site ({news[0]['value']['published']}): “{news[0]['value']['title'][:140]}”.", [news[0]["key"]])
    updates = val("activity.registry_updates")
    if updates:
        say("activity", f"The official register recorded changes to the entity on {updates['update_days']} day(s) since {updates['since']}, most recently {updates['latest']}.",
            ["activity.registry_updates"])

    changed = []
    for change in changes[:12]:
        changed.append({"change_type": change["change_type"], "materiality": change["materiality"], "text": change["summary"], "change_id": change["id"]})
    if refresh.get("first_run"):
        change_note = "This is the first run for this company, so there is no earlier snapshot to compare against."
    elif not changes:
        change_note = f"No change since the previous run ({refresh.get('previous_completed_at')})."
    else:
        change_note = f"{len(changes)} change(s) since the previous run ({refresh.get('previous_completed_at')})."

    unknowns = [{"topic": section["title"], "availability": section["availability"], "explanation": section.get("explanation")}
                for section in sections.values() if section["availability"] != "available"]
    for key, topic in (("identity.employees", "Employee count"),):
        claim = claims.get(key)
        if claim and claim["availability"] != "available":
            unknowns.append({"topic": topic, "availability": claim["availability"], "explanation": claim.get("explanation")})
    unknowns.append({"topic": "Ownership, customer reviews, sentiment and web traffic", "availability": "not_available",
                     "explanation": "No permitted, exact-company source for these was used, so the profile makes no claim about them."})
    return {
        "headline": headline,
        "text": " ".join(sentence["text"] for sentence in sentences),
        "sentences": sentences,
        "signals": signals,
        "what_changed": {"note": change_note, "items": changed},
        "unknowns": unknowns,
        "method": "Deterministic template over published claims; every sentence lists the claim and evidence ids it rests on.",
    }
