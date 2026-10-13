"""External sources keyed by organisation number: NAV's public job board and Wikidata.

Both prove identity with the organisation number itself, so a namesake can never be attributed to the company.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.parse
from typing import Any

from ..claims import OPEN_KNOWLEDGE, PUBLIC_JOB_BOARD, Dossier, span_from_json
from ..http import Fetcher
from ..util import all_tokens, fold, parse_date
from .website import full_core

NAV_SEARCH = "https://arbeidsplassen.nav.no/stillinger/api/search?q={query}&size=25"
NAV_AD = "https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql?format=json&query={query}"
_ORG_IN_AD = re.compile(r'orgnr\\?"\s*:\s*\\?"(\d{9})')


# -- NAV arbeidsplassen ---------------------------------------------------------------------------
class RateGate:
    """Spaces requests to one host and stops asking once the host keeps answering 429."""

    def __init__(self, interval: float = 7.0, give_up_after: int = 3) -> None:
        self.interval = interval
        self.give_up_after = give_up_after
        self.refusals = 0
        self.open = True
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delay = self.interval - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()

    def record(self, status: int) -> None:
        if status == 429:
            self.refusals += 1
            self.interval = min(self.interval * 1.5, 30.0)
            self.open = self.refusals < self.give_up_after
        elif status == 200:
            self.refusals = 0


def nav_not_checked(dossier: Dossier, reason: str) -> None:
    dossier.unavailable("hiring.nav_jobs", field="job_posting", family="hiring", availability="blocked", label="Open positions on NAV's job board",
                        explanation=reason)
    dossier.module("nav_jobs", "blocked", note=reason, transient=True)


def collect_nav_jobs(dossier: Dossier, fetcher: Fetcher, entity: dict[str, Any], subunits: list[dict[str, Any]], *, verify_limit: int = 3,
                     gate: RateGate | None = None) -> int:
    org = dossier.organisation_number
    name = str(entity.get("navn") or "")
    core = full_core(name)
    family = "hiring"
    if not core:
        dossier.module("nav_jobs", "not_applicable", note="no searchable legal name")
        return 0
    if gate and not gate.open:
        nav_not_checked(dossier, "NAV's job board kept answering HTTP 429 (rate limit) in this run, so it was not searched for this company.")
        return 0
    if gate:
        gate.wait()
    response = fetcher.get(NAV_SEARCH.format(query=urllib.parse.quote(name)), source_class=PUBLIC_JOB_BOARD, accept="application/json", timeout=15.0)
    if gate:
        gate.record(response.status)
    body = response.json() if response.status == 200 else None
    if response.blocked or not isinstance(body, dict):
        state = "blocked" if response.blocked or response.status in {401, 403, 429} else "failed"
        dossier.module("nav_jobs", state, note=response.blocked or response.error, response=response, transient=True)
        dossier.unavailable("hiring.nav_jobs", field="job_posting", family=family, availability=state, label="Open positions on NAV's job board",
                            explanation=f"NAV's public job board could not be searched in this run ({response.blocked or response.error}).")
        return 0
    own_orgs = {org} | {str(item.get("organisation_number")) for item in subunits if item.get("organisation_number")}
    own_names = {fold(name)} | {fold(item.get("name")) for item in subunits if item.get("name")}
    folded_core = " ".join(core)
    candidates = []
    for hit in (body.get("hits") or {}).get("hits") or []:
        source = hit.get("_source") or {}
        if source.get("status") not in (None, "ACTIVE"):
            continue
        employer = str((source.get("employer") or {}).get("name") or source.get("businessName") or "")
        employer_folded = fold(employer)
        if employer_folded in own_names or " ".join(all_tokens(employer)).startswith(folded_core) or " ".join(all_tokens(source.get("businessName"))) == " ".join(all_tokens(name)):
            candidates.append((source, employer))
    candidates.sort(key=lambda entry: (str(entry[0].get("published") or ""), str(entry[0].get("uuid") or "")), reverse=True)
    verified = 0
    for source, employer in candidates[:verify_limit]:
        uuid = str(source.get("uuid") or "")
        if not re.fullmatch(r"[0-9a-f-]{36}", uuid) or (gate and not gate.open):
            continue
        if gate:
            gate.wait()
        ad = fetcher.get(NAV_AD.format(uuid=uuid), source_class=PUBLIC_JOB_BOARD, timeout=15.0, max_bytes=600_000)
        if gate:
            gate.record(ad.status)
        if ad.status != 200:
            continue
        found = set(_ORG_IN_AD.findall(ad.text))
        if not found or not found <= own_orgs:
            continue  # the advert belongs to another legal entity, or does not state one: do not publish
        employer_org = sorted(found)[0]
        location = (source.get("locationList") or [{}])[0]
        value = {
            "title": " ".join(str(source.get("title") or "").replace(chr(92), "").split()), "url": ad.final_url, "employer_name": employer,
            "employer_organisation_number": employer_org, "published": parse_date(source.get("published")), "expires": parse_date(source.get("expires")),
            "location": location.get("city") or location.get("municipal"), "source": "NAV arbeidsplassen.no",
        }
        value = {key: item for key, item in value.items() if item}
        ev = dossier.add_evidence(ad, source_class=PUBLIC_JOB_BOARD, source_name="NAV arbeidsplassen.no",
                                  claim_span=f'{value.get("title")} | employer orgnr "{employer_org}"', extraction_method="ad_page_orgnr_match",
                                  effective_at=value.get("published"))
        relation = "the company itself" if employer_org == org else "a registered sub-unit of the company"
        dossier.add_claim(f"hiring.job.nav.{uuid}", field="job_posting", family=family, value=value, evidence_ids=[ev], confidence=0.98,
                          label="Open position", effective_date=value.get("published"),
                          explanation=f"The advert page states employer organisation number {employer_org}, which is {relation}.")
        verified += 1
    search_ev = dossier.add_evidence(response, source_class=PUBLIC_JOB_BOARD, source_name="NAV arbeidsplassen.no (search)",
                                     claim_span=f"search by legal name: {len(candidates)} advert(s) with a matching employer name",
                                     extraction_method="search_candidates_only")
    if verified:
        dossier.add_claim("hiring.nav_summary", field="open_positions_count", family=family, confidence=0.95, evidence_ids=[search_ev],
                          value={"verified_by_organisation_number": verified, "employer_name_matches": len(candidates)},
                          label="Open positions on NAV's job board",
                          explanation="Counts adverts whose page states this company's (or its sub-unit's) organisation number. Search results alone are never treated as evidence.")
    else:
        dossier.unavailable("hiring.nav_jobs", field="job_posting", family=family, availability="not_available", evidence_ids=[search_ev],
                            label="Open positions on NAV's job board",
                            explanation="NAV's public job board was searched by legal name; no active advert could be tied to this organisation number. "
                                        "This is not proof that the company is not hiring.")
    dossier.module("nav_jobs", "available" if verified else "not_available", response=response)
    return verified


# -- Wikidata -------------------------------------------------------------------------------------
def wikidata_lookup(fetcher: Fetcher, organisation_numbers: list[str], *, chunk: int = 60) -> dict[str, dict[str, Any]]:
    """One SPARQL request per chunk of the batch. Matches on property P2333 (Norwegian organisation number)."""
    results: dict[str, dict[str, Any]] = {}
    for start in range(0, len(organisation_numbers), chunk):
        orgs = organisation_numbers[start:start + chunk]
        values = " ".join(f'"{org}"' for org in orgs)
        query = ("SELECT ?org ?item ?itemLabel ?site ?nowiki ?enwiki WHERE { VALUES ?org { " + values + " } ?item wdt:P2333 ?org. "
                 "OPTIONAL { ?item wdt:P856 ?site } "
                 "OPTIONAL { ?nowiki schema:about ?item; schema:isPartOf <https://no.wikipedia.org/> } "
                 "OPTIONAL { ?enwiki schema:about ?item; schema:isPartOf <https://en.wikipedia.org/> } "
                 'SERVICE wikibase:label { bd:serviceParam wikibase:language "nb,en". } }')
        response = fetcher.get(WIKIDATA_SPARQL.format(query=urllib.parse.quote(query)), source_class=OPEN_KNOWLEDGE,
                               accept="application/sparql-results+json", attempts=2, timeout=30.0, max_bytes=4_000_000)
        body = response.json() if response.status == 200 else None
        if not isinstance(body, dict):
            for org in orgs:
                results[org] = {"error": response.blocked or response.error or f"HTTP {response.status}"}
            continue
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for row in (body.get("results") or {}).get("bindings") or []:
            org = (row.get("org") or {}).get("value")
            item = (row.get("item") or {}).get("value", "").rsplit("/", 1)[-1]
            if not org or not item.startswith("Q"):
                continue
            entry = grouped.setdefault(org, {}).setdefault(item, {"id": item, "sites": []})
            entry["label"] = (row.get("itemLabel") or {}).get("value")
            for key, target in (("nowiki", "wikipedia_no"), ("enwiki", "wikipedia_en")):
                if row.get(key):
                    entry[target] = row[key]["value"]
            site = (row.get("site") or {}).get("value")
            if site and site not in entry["sites"]:
                entry["sites"].append(site)
        for org in orgs:
            items = grouped.get(org) or {}
            if len(items) == 1:
                results[org] = {**next(iter(items.values())), "response": response}
            elif len(items) > 1:
                results[org] = {"ambiguous": sorted(items), "response": response}
            else:
                results[org] = {"response": response}
    return results


def publish_wikidata(dossier: Dossier, record: dict[str, Any] | None) -> str | None:
    """Publish the Wikidata/Wikipedia identity links. Returns a website candidate (never a published fact on its own)."""
    org = dossier.organisation_number
    family = "company_profiles"
    if record is None:
        return None
    response = record.get("response")
    if record.get("error") or response is None:
        dossier.module("wikidata", "failed", note=record.get("error"))
        return None
    if record.get("ambiguous"):
        dossier.module("wikidata", "ambiguous", note="several Wikidata items carry this organisation number: " + ", ".join(record["ambiguous"]), response=response)
        return None
    if not record.get("id"):
        dossier.module("wikidata", "not_available", note="no Wikidata item carries this organisation number", response=response)
        return None
    item = record["id"]
    ev = dossier.add_evidence(response, source_class=OPEN_KNOWLEDGE, source_name="Wikidata Query Service",
                              claim_span=span_from_json(f"wd:{item} wdt:P2333", org), extraction_method="sparql_exact_property_match",
                              source_url=f"https://www.wikidata.org/wiki/{item}")
    dossier.add_claim("profile.wikidata", field="wikidata_item", family=family, confidence=0.97, evidence_ids=[ev], label="Wikidata item",
                      value={"id": item, "url": f"https://www.wikidata.org/wiki/{item}", "label": record.get("label")},
                      explanation="Matched on Wikidata property P2333 (Norwegian organisation number), not on name.")
    for key, language in (("wikipedia_no", "Norwegian"), ("wikipedia_en", "English")):
        if record.get(key):
            dossier.add_claim(f"profile.{key}", field="wikipedia_article", family=family, confidence=0.95, evidence_ids=[ev],
                              value={"url": record[key], "language": language}, label=f"Wikipedia article ({language})",
                              explanation="Article linked from the Wikidata item that carries this organisation number.")
    dossier.module("wikidata", "available", response=response)
    sites = record.get("sites") or []
    return sites[0] if len(sites) == 1 else None
