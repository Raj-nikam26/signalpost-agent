"""Brønnøysund Register Centre: the identity anchor and every official fact.

All endpoints are keyed by organisation number, so nothing here can be attributed to the wrong company.
Published under NLOD 2.0. Personal data is limited to public role holders; birth dates are discarded.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from ..claims import OFFICIAL, OFFICIAL_ACCOUNTS, Dossier, span_from_json
from ..http import Fetcher, Response
from ..util import slug

API = "https://data.brreg.no/enhetsregisteret/api"
ENTITY = API + "/enheter/{org}"
ROLES = API + "/enheter/{org}/roller"
SUBUNITS = API + "/underenheter?overordnetEnhet={org}&size=100"
GROUP = API + "/konsernstruktur/{org}"
UPDATES = API + "/oppdateringer/enheter?organisasjonsnummer={org}&dato={since}&size=50"
ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap/{org}"
ACCOUNT_YEARS = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/aar"
ACCOUNT_COPY = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/{year}"
JSON = "application/json"

LEGAL_FORMS_EN = {
    "AS": "private limited company", "ASA": "public limited company", "ENK": "sole proprietorship",
    "NUF": "Norwegian branch of a foreign company", "ANS": "general partnership", "DA": "partnership with shared liability",
    "SA": "cooperative", "STI": "foundation", "BRL": "housing cooperative", "BBL": "housing association",
    "FLI": "association or club", "ESEK": "jointly owned property unit", "IKS": "inter-municipal company",
    "KS": "limited partnership", "SF": "state enterprise", "SPA": "savings bank", "KF": "municipal enterprise",
    "SAM": "co-ownership", "PK": "pension fund", "GFS": "mutual insurance company", "VPFO": "securities fund",
    "SE": "European company", "ANNA": "other legal entity", "PRE": "sole proprietorship with shipping", "SÆR": "special-law entity",
    "BA": "limited-liability company (other)", "KIRK": "church council",
}
ROLES_EN = {
    "DAGL": "Managing director / CEO", "LEDE": "Chair of the board", "NEST": "Deputy chair", "MEDL": "Board member",
    "VARA": "Deputy board member", "OBS": "Observer", "REVI": "Auditor", "REGN": "Accountant", "INNH": "Proprietor",
    "KONT": "Contact person", "FFØR": "Business manager", "DTSO": "Partner with joint liability", "DTPR": "Partner with pro-rata liability",
    "KOMP": "General partner", "BOBE": "Estate administrator", "REPR": "Norwegian representative of a foreign entity",
    "BEST": "Managing shipowner", "KENK": "Sole owner", "KDEB": "Bankruptcy debtor", "KIRK": "Church council member",
    "ETDL": "Subdivision of", "HFOR": "Principal", "HLSE": "Health-region owner", "SAM": "Co-owner", "EIKM": "Owner municipality",
}
LEADERSHIP_ORDER = ["DAGL", "INNH", "LEDE", "NEST", "MEDL", "FFØR", "KONT", "DTSO", "DTPR", "KOMP", "REPR", "BEST", "VARA", "OBS", "REVI", "REGN"]

# (claim field, label, JSON path inside one annual-account record)
ACCOUNT_FIELDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("revenue", "Total operating revenue", ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter")),
    ("sales_revenue", "Sales revenue", ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "salgsinntekter")),
    ("operating_costs", "Total operating costs", ("resultatregnskapResultat", "driftsresultat", "driftskostnad", "sumDriftskostnad")),
    ("operating_result", "Operating result", ("resultatregnskapResultat", "driftsresultat", "driftsresultat")),
    ("net_financial_items", "Net financial items", ("resultatregnskapResultat", "finansresultat", "nettoFinans")),
    ("profit_before_tax", "Ordinary result before tax", ("resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad")),
    ("tax_expense", "Tax on ordinary result", ("resultatregnskapResultat", "ordinaertResultatSkattekostnad")),
    ("annual_result", "Annual result", ("resultatregnskapResultat", "aarsresultat")),
    ("total_result", "Total result", ("resultatregnskapResultat", "totalresultat")),
    ("total_assets", "Total assets", ("eiendeler", "sumEiendeler")),
    ("fixed_assets", "Fixed assets", ("eiendeler", "anleggsmidler", "sumAnleggsmidler")),
    ("current_assets", "Current assets", ("eiendeler", "omloepsmidler", "sumOmloepsmidler")),
    ("cash", "Bank deposits and cash", ("eiendeler", "sumBankinnskuddOgKontanter")),
    ("receivables", "Receivables", ("eiendeler", "sumFordringer")),
    ("inventory", "Inventory", ("eiendeler", "sumVarer")),
    ("investments", "Investments", ("eiendeler", "sumInvesteringer")),
    ("equity", "Total equity", ("egenkapitalGjeld", "egenkapital", "sumEgenkapital")),
    ("paid_in_equity", "Paid-in equity", ("egenkapitalGjeld", "egenkapital", "innskuttEgenkapital", "sumInnskuttEgenkaptial")),
    ("retained_earnings", "Retained earnings", ("egenkapitalGjeld", "egenkapital", "opptjentEgenkapital", "sumOpptjentEgenkapital")),
    ("total_debt", "Total liabilities", ("egenkapitalGjeld", "gjeldOversikt", "sumGjeld")),
    ("short_term_debt", "Short-term liabilities", ("egenkapitalGjeld", "gjeldOversikt", "kortsiktigGjeld", "sumKortsiktigGjeld")),
    ("long_term_debt", "Long-term liabilities", ("egenkapitalGjeld", "gjeldOversikt", "langsiktigGjeld", "sumLangsiktigGjeld")),
]
HEADLINE_ACCOUNT_FIELDS = ("revenue", "operating_result", "profit_before_tax", "annual_result", "total_assets", "equity", "total_debt")

_history_lock = threading.Lock()
_history_last = 0.0


def _get(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if float(value).is_integer() else value


def format_address(address: Any) -> str | None:
    if not isinstance(address, dict):
        return None
    lines = [line for line in address.get("adresse") or [] if line]
    place = " ".join(part for part in [address.get("postnummer"), address.get("poststed")] if part)
    country = address.get("land") if address.get("landkode") not in (None, "NO") else None
    return ", ".join(part for part in [*lines, place, country] if part) or None


def status_of(entity: dict[str, Any]) -> str:
    if entity.get("slettedato"):
        return "deleted from the register"
    if entity.get("konkurs"):
        return "bankrupt"
    if entity.get("underTvangsavviklingEllerTvangsopplosning"):
        return "under forced liquidation or dissolution"
    if entity.get("underAvvikling"):
        return "under liquidation"
    return "active"


def _fetch(fetcher: Fetcher, url: str, *, optional: bool = False) -> Response:
    return fetcher.get(url, source_class=OFFICIAL, optional=optional, accept=JSON, attempts=3, timeout=20.0, max_bytes=6_000_000)


# -- entity ---------------------------------------------------------------------------------------
def collect_entity(dossier: Dossier, fetcher: Fetcher) -> dict[str, Any] | None:
    org = dossier.organisation_number
    response = _fetch(fetcher, ENTITY.format(org=org))
    entity = response.json() if response.status in {200, 410} else None
    family = "legal_identity"
    if response.blocked:
        dossier.module("registry", "blocked", note=response.blocked, response=response)
        dossier.error("registry", "budget", response.blocked, response.url)
        return None
    if response.status == 404:
        dossier.module("registry", "not_available", note="Enhetsregisteret has no entity with this organisation number", response=response)
        ev = dossier.add_evidence(response, source_class=OFFICIAL, source_name="Brønnøysund Enhetsregisteret",
                                  claim_span="HTTP 404: no such organisation number", extraction_method="http_status")
        dossier.unavailable("identity.legal_name", field="legal_name", family=family, availability="not_available",
                            explanation="The Central Coordinating Register for Legal Entities returned HTTP 404 for this organisation number.",
                            evidence_ids=[ev])
        return None
    if not isinstance(entity, dict) or str(entity.get("organisasjonsnummer")) != org:
        state = "blocked" if response.status in {401, 403, 429} else "failed"
        dossier.module("registry", state, note=response.error or "unexpected registry response", response=response)
        dossier.error("registry", "source_error", response.error or f"HTTP {response.status}", response.url)
        return None

    def ev(path: str, value: Any, effective: str | None = None) -> list[str]:
        return [dossier.add_evidence(response, source_class=OFFICIAL, source_name="Brønnøysund Enhetsregisteret",
                                     claim_span=span_from_json(path, value), extraction_method="json_path", effective_at=effective)]

    def claim(key: str, field: str, value: Any, path: str, raw: Any = None, *, label: str | None = None,
              effective: str | None = None, explanation: str | None = None, unit: str | None = None) -> None:
        if value in (None, "", [], {}):
            return
        dossier.add_claim(f"identity.{key}", field=field, family=family, value=value, evidence_ids=ev(path, value if raw is None else raw, effective),
                          confidence=1.0, label=label, effective_date=effective, explanation=explanation, unit=unit)

    form = entity.get("organisasjonsform") or {}
    claim("organisation_number", "organisation_number", org, "organisasjonsnummer")
    claim("legal_name", "legal_name", entity.get("navn"), "navn")
    claim("legal_form", "legal_form", {"code": form.get("kode"), "description": form.get("beskrivelse"),
                                         "english": LEGAL_FORMS_EN.get(form.get("kode"))} if form.get("kode") else None,
          "organisasjonsform", {"kode": form.get("kode"), "beskrivelse": form.get("beskrivelse")})
    status = status_of(entity)
    flags = {key: entity.get(key) for key in ("konkurs", "underAvvikling", "underTvangsavviklingEllerTvangsopplosning", "slettedato") if key in entity}
    dossier.add_claim("identity.status", field="company_status", family=family, value=status, evidence_ids=ev("status flags", flags),
                      confidence=1.0, label="Registry status", effective_date=entity.get("konkursdato") or entity.get("slettedato"),
                      explanation="Derived only from the registry's bankruptcy, liquidation, forced-dissolution and deletion flags.")
    claim("founded_date", "founded_date", entity.get("stiftelsesdato"), "stiftelsesdato", effective=entity.get("stiftelsesdato"))
    claim("registered_date", "registered_date", entity.get("registreringsdatoEnhetsregisteret"), "registreringsdatoEnhetsregisteret",
          effective=entity.get("registreringsdatoEnhetsregisteret"), label="Registered in Enhetsregisteret")
    for index in ("1", "2", "3"):
        code = entity.get(f"naeringskode{index}")
        if isinstance(code, dict) and code.get("kode"):
            name = "industry" if index == "1" else f"industry_secondary_{int(index) - 1}"
            claim(name, "industry" if index == "1" else "industry_secondary",
                  {"code": code.get("kode"), "description": code.get("beskrivelse")}, f"naeringskode{index}",
                  label="Industry (NACE)" if index == "1" else "Additional industry (NACE)")
    for key, field, label in (("forretningsadresse", "business_address", "Business address"), ("postadresse", "postal_address", "Postal address")):
        address = entity.get(key)
        if isinstance(address, dict):
            claim(field, field, {"formatted": format_address(address), "postal_code": address.get("postnummer"),
                                 "city": address.get("poststed"), "municipality": address.get("kommune"),
                                 "municipality_number": address.get("kommunenummer"), "country": address.get("land")}, key, address, label=label)
    employees = _number(entity.get("antallAnsatte"))
    employees_date = entity.get("registreringsdatoAntallAnsatteEnhetsregisteret")
    if entity.get("harRegistrertAntallAnsatte") is not False and employees is not None:
        claim("employees", "employees_registered", employees, "antallAnsatte", effective=employees_date, label="Registered employees",
              explanation="Employee count as registered with NAV's Aa-register and mirrored in Enhetsregisteret; not full-time equivalents.", unit="employees")
    else:
        dossier.unavailable("identity.employees", field="employees_registered", family=family, availability="not_available", label="Registered employees",
                            explanation="The registry reports no registered employee count for this entity. This is not the same as zero employees.",
                            evidence_ids=ev("harRegistrertAntallAnsatte", entity.get("harRegistrertAntallAnsatte")))
    purpose = " ".join(entity.get("vedtektsfestetFormaal") or [])
    activity = " ".join(entity.get("aktivitet") or [])
    claim("purpose", "statutory_purpose", purpose, "vedtektsfestetFormaal", effective=entity.get("vedtektsdato"), label="Statutory purpose")
    if activity and activity != purpose:
        claim("activity", "registered_activity", activity, "aktivitet", label="Registered activity")
    capital = entity.get("kapital")
    if isinstance(capital, dict) and capital.get("belop") is not None:
        claim("share_capital", "share_capital", {"amount": _number(capital.get("belop")), "currency": capital.get("valuta"),
                                                "shares": _number(capital.get("antallAksjer")), "type": capital.get("type")},
              "kapital", capital, effective=capital.get("innfortDato"), label="Registered capital")
    sector = entity.get("institusjonellSektorkode")
    if isinstance(sector, dict):
        claim("sector", "institutional_sector", {"code": sector.get("kode"), "description": sector.get("beskrivelse")}, "institusjonellSektorkode")
    registers = {name: bool(entity.get(key)) for name, key in (
        ("register_of_business_enterprises", "registrertIForetaksregisteret"), ("vat_register", "registrertIMvaregisteret"),
        ("voluntary_organisations_register", "registrertIFrivillighetsregisteret"), ("foundations_register", "registrertIStiftelsesregisteret"),
        ("political_parties_register", "registrertIPartiregisteret")) if key in entity}
    claim("registers", "register_memberships", registers, "registrertI*", registers, label="Register memberships")
    if "registrertIMvaregisteret" in entity:
        dossier.add_claim("identity.vat_registered", field="vat_registered", family=family, value="yes" if entity["registrertIMvaregisteret"] else "no",
                          evidence_ids=ev("registrertIMvaregisteret", entity["registrertIMvaregisteret"]), confidence=1.0, label="VAT registered",
                          effective_date=entity.get("registreringsdatoMerverdiavgiftsregisteret"))
    names = [{"name": item.get("navn"), "from": str(item.get("fraDato") or "")[:10] or None, "to": str(item.get("tilDato") or "")[:10] or None}
             for item in entity.get("historiskeNavn") or [] if item.get("navn")]
    claim("previous_names", "previous_names", names, "historiskeNavn", entity.get("historiskeNavn"), label="Previous registered names")
    claim("registered_website", "registered_website", entity.get("hjemmeside"), "hjemmeside", label="Website reported to the registry",
          explanation="Self-reported by the entity to Enhetsregisteret. Reported, not independently verified; see official_website for the verified site.")
    claim("registered_email", "registered_email", entity.get("epostadresse"), "epostadresse", label="Email reported to the registry")
    claim("registered_phone", "registered_phone", entity.get("telefon") or entity.get("mobil"), "telefon", label="Phone reported to the registry")
    claim("latest_accounts_year", "latest_accounts_year", entity.get("sisteInnsendteAarsregnskap"), "sisteInnsendteAarsregnskap",
          label="Latest submitted annual accounts (year)")
    if "erIKonsern" in entity:
        dossier.add_claim("group.in_group", field="part_of_group", family="group_structure", value="yes" if entity["erIKonsern"] else "no",
                          evidence_ids=ev("erIKonsern", entity["erIKonsern"]), confidence=1.0, label="Part of a registered group")
    parent = entity.get("overordnetEnhet")
    if parent:
        dossier.add_claim("group.parent_unit", field="parent_unit", family="group_structure", value={"organisation_number": parent},
                          evidence_ids=ev("overordnetEnhet", parent), confidence=1.0, label="Superior unit")
    dossier.module("registry", "available", response=response)
    return entity


# -- annual accounts ------------------------------------------------------------------------------
def collect_accounts(dossier: Dossier, fetcher: Fetcher, entity: dict[str, Any], *, max_years: int = 3) -> list[dict[str, Any]]:
    org = dossier.organisation_number
    response = _fetch(fetcher, ACCOUNTS.format(org=org))
    family = "annual_accounts"
    body = response.json() if response.status == 200 else None
    if response.blocked:
        dossier.module("financials", "blocked", note=response.blocked, response=response)
        return []
    if response.status == 404 or (response.status == 200 and not body):
        ev = dossier.add_evidence(response, source_class=OFFICIAL_ACCOUNTS, source_name="Brønnøysund Regnskapsregisteret",
                                  claim_span=f"HTTP {response.status}: no annual accounts returned", extraction_method="http_status")
        dossier.unavailable("accounts.latest", field="annual_accounts", family=family, availability="not_available", evidence_ids=[ev],
                            label="Annual accounts",
                            explanation="Regnskapsregisteret returned no annual accounts for this entity. Missing accounts are not interpreted as zero values.")
        dossier.module("financials", "not_available", note="no annual accounts returned", response=response)
        return []
    if not isinstance(body, list):
        dossier.module("financials", "blocked" if response.status in {401, 403, 429} else "failed", note=response.error, response=response)
        dossier.error("financials", "source_error", response.error or f"HTTP {response.status}", response.url)
        dossier.unavailable("accounts.latest", field="annual_accounts", family=family, availability="failed", label="Annual accounts",
                            explanation=f"Regnskapsregisteret could not be read ({response.error or response.status}). No financial value is published.")
        return []
    records = [item for item in body if isinstance(item, dict) and str(_get(item, "virksomhet", "organisasjonsnummer") or org) == org
               and _get(item, "regnskapsperiode", "tilDato")]
    records.sort(key=lambda item: (item["regnskapsperiode"]["tilDato"], item.get("regnskapstype") == "SELSKAP"), reverse=True)
    years_kept: list[str] = []
    normalised: list[dict[str, Any]] = []
    for record in records:
        period = record["regnskapsperiode"]
        year = str(period["tilDato"])[:4]
        if year not in years_kept:
            if len(years_kept) >= max_years:
                continue
            years_kept.append(year)
        kind = str(record.get("regnskapstype") or "SELSKAP")
        scope = "consolidated group accounts" if kind == "KONSERN" else "company accounts"
        currency = record.get("valuta") or "NOK"
        period_value = {"from": period.get("fraDato"), "to": period.get("tilDato"), "fiscal_year": year, "accounts_type": kind, "scope": scope}
        prefix = f"accounts.{kind}.{year}"
        row: dict[str, Any] = {"record_id": record.get("id"), "account_type": kind, "period": period, "currency": currency, "fiscal_year": year}
        for field, label, path in ACCOUNT_FIELDS:
            value = _number(_get(record, *path))
            row[field] = value
            if value is None:
                continue  # an absent line item is never published, least of all as zero
            ev = dossier.add_evidence(response, source_class=OFFICIAL_ACCOUNTS, source_name="Brønnøysund Regnskapsregisteret",
                                      claim_span=span_from_json(f"regnskap[id={record.get('id')}, {kind}, {period.get('fraDato')}..{period.get('tilDato')}].{'.'.join(path)}", _get(record, *path)),
                                      extraction_method="json_path", effective_at=period.get("tilDato"))
            dossier.add_claim(f"{prefix}.{field}", field=field, family=family, value=value, evidence_ids=[ev], confidence=1.0,
                              label=f"{label} {year}" + (" (group)" if kind == "KONSERN" else ""), period=period_value,
                              effective_date=period.get("tilDato"), unit=currency)
        meta = {"currency": currency, "accounting_rules": _get(record, "regnkapsprinsipper", "regnskapsregler"),
                "small_enterprise_rules": _get(record, "regnkapsprinsipper", "smaaForetak"),
                "not_audited": _get(record, "revisjon", "ikkeRevidertAarsregnskap"), "audit_opted_out": _get(record, "revisjon", "fravalgRevisjon"),
                "liquidation_accounts": record.get("avviklingsregnskap"), "journal_number": record.get("journalnr")}
        meta = {key: value for key, value in meta.items() if value is not None}
        ev = dossier.add_evidence(response, source_class=OFFICIAL_ACCOUNTS, source_name="Brønnøysund Regnskapsregisteret",
                                  claim_span=span_from_json(f"regnskap[id={record.get('id')}] metadata", meta), extraction_method="json_path",
                                  effective_at=period.get("tilDato"))
        dossier.add_claim(f"{prefix}.filing", field="accounts_filing", family=family, value={**meta, "fiscal_year": year, "accounts_type": kind},
                          evidence_ids=[ev], confidence=1.0, label=f"Annual accounts filing {year}" + (" (group)" if kind == "KONSERN" else ""),
                          period=period_value, effective_date=period.get("tilDato"))
        normalised.append(row)
    dossier.module("financials", "available" if normalised else "not_available", response=response)
    return normalised


def collect_account_years(dossier: Dossier, fetcher: Fetcher) -> None:
    """Optional: list every filed year with the official PDF copy. The endpoint allows roughly 30 requests a minute."""
    global _history_last
    org = dossier.organisation_number
    with _history_lock:
        delay = 2.1 - (time.monotonic() - _history_last)
        if delay > 0:
            time.sleep(delay)
        _history_last = time.monotonic()
    response = fetcher.get(ACCOUNT_YEARS.format(org=org), source_class=OFFICIAL_ACCOUNTS, optional=True, accept=JSON, attempts=1, timeout=20.0)
    body = response.json() if response.status == 200 else None
    if response.blocked:
        dossier.module("financial_history", "blocked", note=response.blocked, response=response)
        return
    if not isinstance(body, list) or not body:
        dossier.module("financial_history", "not_available" if response.status in {200, 404} else "failed", note=response.error, response=response)
        return
    years = sorted({str(year) for year in body if str(year).isdigit()})
    ev = dossier.add_evidence(response, source_class=OFFICIAL_ACCOUNTS, source_name="Brønnøysund Regnskapsregisteret (annual-account copies)",
                              claim_span=span_from_json("available filing years", years), extraction_method="json_array")
    dossier.add_claim("accounts.history.years", field="accounts_history_years", family="annual_accounts", evidence_ids=[ev], confidence=1.0,
                      label="Years with a filed annual-accounts copy",
                      value={"years": years, "copies": [{"year": year, "url": ACCOUNT_COPY.format(org=org, year=year)} for year in reversed(years[-5:])]})
    dossier.module("financial_history", "available", response=response)


# -- roles ----------------------------------------------------------------------------------------
def collect_roles(dossier: Dossier, fetcher: Fetcher) -> list[dict[str, Any]]:
    org = dossier.organisation_number
    response = _fetch(fetcher, ROLES.format(org=org))
    body = response.json() if response.status == 200 else None
    family = "leadership"
    if response.blocked or not isinstance(body, dict):
        state = "blocked" if response.blocked or response.status in {401, 403, 429} else "not_available" if response.status == 404 else "failed"
        dossier.module("roles", state, note=response.blocked or response.error, response=response)
        if state == "failed":
            dossier.error("roles", "source_error", response.error or f"HTTP {response.status}", response.url)
        dossier.unavailable("leadership.roles", field="leadership", family=family, availability=state, label="Leadership and board",
                            explanation="The official roles endpoint returned no usable role list for this run.")
        return []
    roles: list[dict[str, Any]] = []
    for group in body.get("rollegrupper") or []:
        group_code = _get(group, "type", "kode")
        for item in group.get("roller") or []:
            person = item.get("person") or {}
            holder_entity = item.get("enhet") or {}
            if item.get("avregistrert") or person.get("erDoed"):
                continue
            name_parts = person.get("navn") or {}
            name = " ".join(part for part in [name_parts.get("fornavn"), name_parts.get("mellomnavn"), name_parts.get("etternavn")] if part)
            name = name or " ".join(holder_entity.get("navn") or []) if isinstance(holder_entity.get("navn"), list) else name or holder_entity.get("navn")
            code = _get(item, "type", "kode")
            if not name or not code:
                continue
            value = {
                "name": name, "role": _get(item, "type", "beskrivelse"), "role_english": ROLES_EN.get(code), "role_code": code,
                "holder_type": "person" if person else "organisation",
                "holder_organisation_number": holder_entity.get("organisasjonsnummer"),
                "elected_by": _get(item, "valgtAv", "beskrivelse"), "role_group_last_changed": group.get("sistEndret"),
            }
            value = {key: entry for key, entry in value.items() if entry is not None}
            span_value = {"type": code, "navn": name, "sistEndret": group.get("sistEndret")}
            ev = dossier.add_evidence(response, source_class=OFFICIAL, source_name="Brønnøysund Enhetsregisteret (roles)",
                                      claim_span=span_from_json(f"rollegrupper[{group_code}].roller", span_value), extraction_method="json_path",
                                      effective_at=group.get("sistEndret"))
            dossier.add_claim(f"leadership.{code}.{slug(name)}", field="role_holder", family=family, value=value, evidence_ids=[ev], confidence=1.0,
                              label=ROLES_EN.get(code) or value.get("role") or code, effective_date=group.get("sistEndret"),
                              explanation="Public role holder from the official register. Birth dates are discarded.")
            roles.append(value)
    if not roles:
        ev = dossier.add_evidence(response, source_class=OFFICIAL, source_name="Brønnøysund Enhetsregisteret (roles)",
                                  claim_span="rollegrupper: no active role holders", extraction_method="json_path")
        dossier.unavailable("leadership.roles", field="leadership", family=family, availability="not_available", evidence_ids=[ev],
                            label="Leadership and board", explanation="The official register lists no active role holders for this entity.")
    roles.sort(key=lambda item: LEADERSHIP_ORDER.index(item["role_code"]) if item["role_code"] in LEADERSHIP_ORDER else 99)
    dossier.module("roles", "available" if roles else "not_available", response=response)
    return roles


# -- registered workplaces ------------------------------------------------------------------------
def collect_subunits(dossier: Dossier, fetcher: Fetcher, *, limit: int = 60) -> list[dict[str, Any]]:
    org = dossier.organisation_number
    response = _fetch(fetcher, SUBUNITS.format(org=org))
    body = response.json() if response.status == 200 else None
    family = "workplaces"
    if response.blocked or not isinstance(body, dict):
        state = "blocked" if response.blocked or response.status in {401, 403, 429} else "failed"
        dossier.module("locations", state, note=response.blocked or response.error, response=response)
        dossier.unavailable("workplaces.registered", field="registered_workplaces", family=family, availability=state, label="Registered workplaces",
                            explanation="The official sub-unit endpoint could not be read in this run.")
        return []
    rows = sorted(_get(body, "_embedded", "underenheter") or [], key=lambda item: str(item.get("organisasjonsnummer")))
    total = _get(body, "page", "totalElements") or len(rows)
    locations: list[dict[str, Any]] = []
    for item in rows[:limit]:
        address = item.get("beliggenhetsadresse") or item.get("postadresse") or {}
        industry = item.get("naeringskode1") or {}
        value = {
            "organisation_number": item.get("organisasjonsnummer"), "name": item.get("navn"), "address": format_address(address),
            "municipality": address.get("kommune"), "industry_code": industry.get("kode"), "industry": industry.get("beskrivelse"),
            "employees": _number(item.get("antallAnsatte")) if item.get("harRegistrertAntallAnsatte") else None,
            "start_date": item.get("oppstartsdato"),
        }
        value = {key: entry for key, entry in value.items() if entry is not None}
        if not value.get("organisation_number"):
            continue
        ev = dossier.add_evidence(response, source_class=OFFICIAL, source_name="Brønnøysund Enhetsregisteret (sub-units)",
                                  claim_span=span_from_json(f"underenheter[{value['organisation_number']}]", {"navn": item.get("navn"), "adresse": format_address(address)}),
                                  extraction_method="json_path", effective_at=item.get("oppstartsdato"))
        dossier.add_claim(f"workplace.{value['organisation_number']}", field="registered_workplace", family=family, value=value, evidence_ids=[ev],
                          confidence=1.0, label="Registered workplace", effective_date=item.get("oppstartsdato"))
        locations.append(value)
    ev = dossier.add_evidence(response, source_class=OFFICIAL, source_name="Brønnøysund Enhetsregisteret (sub-units)",
                              claim_span=span_from_json("page.totalElements", total), extraction_method="json_path")
    if locations:
        dossier.add_claim("workplaces.count", field="registered_workplace_count", family=family, value=int(total), evidence_ids=[ev], confidence=1.0,
                          label="Number of registered workplaces", unit="sub-units")
    else:
        dossier.unavailable("workplaces.registered", field="registered_workplaces", family=family, availability="not_available", evidence_ids=[ev],
                            label="Registered workplaces",
                            explanation="No registered sub-units were returned. This does not prove the company has no physical presence.")
    dossier.module("locations", "available" if locations else "not_available", response=response)
    return locations


# -- group structure ------------------------------------------------------------------------------
def collect_group(dossier: Dossier, fetcher: Fetcher, entity: dict[str, Any], *, limit: int = 40) -> None:
    org = dossier.organisation_number
    family = "group_structure"
    if not entity.get("erIKonsern"):
        dossier.module("group", "not_applicable", note="registry does not flag this entity as part of a group")
        return
    response = _fetch(fetcher, GROUP.format(org=org), optional=True)
    body = response.json() if response.status == 200 else None
    if response.blocked or not isinstance(body, dict):
        state = "blocked" if response.blocked or response.status in {401, 403, 429} else "not_available" if response.status == 404 else "failed"
        dossier.module("group", state, note=response.blocked or response.error, response=response)
        return

    def walk(node: dict[str, Any], trail: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        if str(node.get("organisasjonsnummer")) == org:
            return node, trail
        for child in node.get("children") or []:
            found = walk(child, [*trail, node])
            if found:
                return found
        return None

    def count(node: dict[str, Any]) -> int:
        return sum(1 + count(child) for child in node.get("children") or [])

    located = walk(body, [])
    if not located:
        dossier.module("group", "not_available", note="entity not present in the returned group tree", response=response)
        return
    node, trail = located

    def ev(span_path: str, value: Any, effective: str | None = None) -> list[str]:
        return [dossier.add_evidence(response, source_class=OFFICIAL, source_name="Brønnøysund Enhetsregisteret (group structure)",
                                     claim_span=span_from_json(span_path, value), extraction_method="json_tree", effective_at=effective)]

    if trail:
        root, parent = trail[0], trail[-1]
        dossier.add_claim("group.parent", field="group_parent", family=family, confidence=1.0, label="Direct parent company",
                          value={"organisation_number": parent.get("organisasjonsnummer"), "name": parent.get("navn"), "ownership": node.get("grunnlag"),
                                 "relation": _get(node, "knytningsform", "beskrivelse")},
                          evidence_ids=ev("parent", {"parentOrganisasjonsnummer": parent.get("organisasjonsnummer"), "parentNavn": parent.get("navn"), "grunnlag": node.get("grunnlag")}, node.get("dato")),
                          effective_date=node.get("dato"))
        if root is not parent:
            dossier.add_claim("group.ultimate_parent", field="group_ultimate_parent", family=family, confidence=1.0, label="Ultimate registered parent",
                              value={"organisation_number": root.get("organisasjonsnummer"), "name": root.get("navn")},
                              evidence_ids=ev("root", {"organisasjonsnummer": root.get("organisasjonsnummer"), "navn": root.get("navn")}))
    children = sorted(node.get("children") or [], key=lambda item: str(item.get("organisasjonsnummer")))
    for child in children[:limit]:
        value = {"organisation_number": child.get("organisasjonsnummer"), "name": child.get("navn"), "ownership": child.get("grunnlag"),
                 "relation": _get(child, "knytningsform", "beskrivelse"), "legal_form": _get(child, "organisasjonsform", "kode")}
        value = {key: entry for key, entry in value.items() if entry is not None}
        if value.get("organisation_number"):
            dossier.add_claim(f"group.subsidiary.{value['organisation_number']}", field="subsidiary", family=family, value=value, confidence=1.0,
                              label="Direct subsidiary", evidence_ids=ev(f"children[{value['organisation_number']}]", value, child.get("dato")),
                              effective_date=child.get("dato"))
    if children:
        dossier.add_claim("group.subsidiary_count", field="subsidiary_count", family=family, confidence=1.0, label="Group companies below this entity",
                          value={"direct": len(children), "all_levels": count(node)},
                          evidence_ids=ev("children", {"direct": len(children), "all_levels": count(node)}))
    dossier.module("group", "available", response=response)


# -- dated registry activity ----------------------------------------------------------------------
def collect_updates(dossier: Dossier, fetcher: Fetcher, *, days: int = 365) -> None:
    org = dossier.organisation_number
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    response = fetcher.get(UPDATES.format(org=org, since=since), source_class=OFFICIAL, optional=True, accept=JSON, attempts=2, timeout=20.0)
    body = response.json() if response.status == 200 else None
    if response.blocked or not isinstance(body, dict):
        dossier.module("registry_updates", "blocked" if response.blocked else "failed", note=response.blocked or response.error, response=response)
        return
    events = _get(body, "_embedded", "oppdaterteEnheter") or []
    dates = sorted({str(item.get("dato"))[:10] for item in events if item.get("dato")}, reverse=True)
    if not dates:
        ev = dossier.add_evidence(response, source_class=OFFICIAL, source_name="Brønnøysund Enhetsregisteret (update feed)",
                                  claim_span=f"no registry updates since {since[:10]}", extraction_method="json_path")
        dossier.unavailable("activity.registry_updates", field="registry_updates", family="public_activity", availability="not_available",
                            evidence_ids=[ev], label="Registry updates (12 months)",
                            explanation=f"The official update feed lists no changes to this entity since {since[:10]}.")
        dossier.module("registry_updates", "not_available", response=response)
        return
    ev = dossier.add_evidence(response, source_class=OFFICIAL, source_name="Brønnøysund Enhetsregisteret (update feed)",
                              claim_span=span_from_json("oppdaterteEnheter[].dato", dates[:8]), extraction_method="json_path", effective_at=dates[0])
    dossier.add_claim("activity.registry_updates", field="registry_updates", family="public_activity", evidence_ids=[ev], confidence=1.0,
                      label="Registry updates (12 months)", effective_date=dates[0],
                      value={"since": since[:10], "update_days": len(dates), "latest": dates[0], "recent_dates": dates[:8]},
                      explanation="Dates on which Enhetsregisteret recorded a change to this entity. The feed gives dates, not the changed fields.")
    dossier.module("registry_updates", "available", response=response)
