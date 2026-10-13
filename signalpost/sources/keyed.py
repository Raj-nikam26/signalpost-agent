"""Optional keyed connectors. They are off unless the evaluator supplies a key in the environment.

    SIGNALPOST_SEARCH_API_KEY   Brave Search API: finds candidate websites. Results are candidates only and are not stored.
    SIGNALPOST_PLACES_API_KEY   Google Places API (New): rating, review count and Maps link for an exactly matched place.

A key is read from the environment, sent only as a request header to the API's own host, and never written to an
envelope, a report, a log or a URL. Spend is counted per call and capped by SIGNALPOST_API_BUDGET_USD (default 8).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from typing import Any

from ..claims import Dossier
from ..http import Fetcher, host_of, registered_domain
from ..util import all_tokens, fold
from .website import NOT_A_COMPANY_SITE, full_core

SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search?q={query}&country=no&search_lang=nb&count=10&result_filter=web"
PLACES_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELDS = "places.id,places.displayName,places.formattedAddress,places.postalAddress,places.nationalPhoneNumber,places.websiteUri,places.rating,places.userRatingCount,places.googleMapsUri,places.businessStatus"
SEARCH_COST_USD = float(os.environ.get("SIGNALPOST_SEARCH_COST_USD", "0.005"))
PLACES_COST_USD = float(os.environ.get("SIGNALPOST_PLACES_COST_USD", "0.035"))
PLACES = "licensed_places_api"
NEVER_A_COMPANY_SITE = NOT_A_COMPANY_SITE | {
    "wikipedia.org", "brreg.no", "nav.no", "regjeringen.no", "ssb.no", "altinn.no", "skatteetaten.no", "bizweb.no", "allabolag.se",
    "enento.no", "forvalt.no", "firmabasen.no", "companybooknorway.com", "opencorporates.com", "dnb.com", "kompass.com", "yelp.com",
    "tripadvisor.com", "tripadvisor.no", "trustpilot.com", "maps.google.com", "apple.com", "play.google.com", "youtube.com", "vg.no",
    "dn.no", "e24.no", "nrk.no", "aftenposten.no", "europages.com", "cylex.no", "infobel.com", "norgesfirma.no", "bedrift.no",
    "firmaoversikt.no", "proff.se", "proff.dk", "largestcompanies.no", "kapital.no", "finansavisen.no", "doffin.no", "fagfolkguiden.no",
}
_search_lock = threading.Lock()
_search_last = 0.0


def search_key() -> str | None:
    return os.environ.get("SIGNALPOST_SEARCH_API_KEY") or os.environ.get("BRAVE_SEARCH_API_KEY") or None


def places_key() -> str | None:
    return os.environ.get("SIGNALPOST_PLACES_API_KEY") or os.environ.get("GOOGLE_PLACES_API_KEY") or None


def enabled() -> dict[str, bool]:
    return {"search": bool(search_key()), "places": bool(places_key())}


# -- search: candidate websites only ---------------------------------------------------------------
def search_candidates(fetcher: Fetcher, entity: dict[str, Any], *, limit: int = 3) -> tuple[list[str], str | None]:
    """Return up to `limit` candidate site URLs for a company, plus a note when the search could not run.

    Nothing from the result page (title, snippet, rank) is kept or published. Each candidate is fetched afresh and must
    pass the same identity gate as a guessed domain: the organisation number on the site, or the full legal name plus a
    registry corroborator.
    """
    global _search_last
    key = search_key()
    if not key:
        return [], None
    name = str(entity.get("navn") or "")
    if not full_core(name):
        return [], None
    if not fetcher.budget.spend("brave_search", SEARCH_COST_USD):
        return [], "external API budget reached before this company was searched"
    place = str((entity.get("forretningsadresse") or {}).get("poststed") or "")
    query = urllib.parse.quote(f'"{name}" {place}'.strip())
    with _search_lock:  # the entry plans allow about one query a second
        delay = 1.05 - (time.monotonic() - _search_last)
        if delay > 0:
            time.sleep(delay)
        _search_last = time.monotonic()
    response = fetcher.get(SEARCH_ENDPOINT.format(query=query), source_class="search_api", accept="application/json", timeout=15.0,
                           headers={"X-Subscription-Token": key}, attempts=2)
    body = response.json() if response.status == 200 else None
    if not isinstance(body, dict):
        return [], f"search API answered {response.blocked or response.error or response.status}"
    name_tokens = [token for token in full_core(name) if len(token) >= 3]
    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for rank, item in enumerate(((body.get("web") or {}).get("results") or [])):
        url = str(item.get("url") or "")
        domain = registered_domain(host_of(url))
        if not domain or domain in seen or domain in NEVER_A_COMPANY_SITE or any(domain.endswith("." + bad) for bad in NEVER_A_COMPANY_SITE):
            continue
        seen.add(domain)
        label = domain.split(".")[0].replace("-", "")
        overlap = sum(1 for token in name_tokens if token in label)
        scored.append((-overlap, rank, f"https://{host_of(url)}/"))
    return [url for _overlap, _rank, url in sorted(scored)[:limit]], None


# -- Google Places: rating for an exactly matched place ------------------------------------------
def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def match_place(entity: dict[str, Any], place: dict[str, Any], site_domain: str | None) -> list[str]:
    """Independent reasons why a returned place is this exact company. Fewer than one strong reason means no publication."""
    reasons: list[str] = []
    website = registered_domain(host_of(str(place.get("websiteUri") or "")))
    if site_domain and website and website == site_domain:
        reasons.append(f"the place lists the company's verified website ({site_domain})")
    phone = _digits(entity.get("telefon") or entity.get("mobil"))[-8:]
    if len(phone) == 8 and _digits(place.get("nationalPhoneNumber"))[-8:] == phone:
        reasons.append("the place's phone number equals the number in the official register")
    address = entity.get("forretningsadresse") or {}
    place_address = place.get("postalAddress") or {}
    formatted = fold(place.get("formattedAddress"))
    street = fold(" ".join(address.get("adresse") or []))
    street_word = next(iter(re.findall(r"[a-z]{4,}", street)), "")
    house = next(iter(re.findall(r"\d+", street)), "")
    same_postcode = address.get("postnummer") and (str(place_address.get("postalCode") or "") == str(address["postnummer"])
                                                   or str(address["postnummer"]) in formatted)
    display = (place.get("displayName") or {}).get("text") if isinstance(place.get("displayName"), dict) else place.get("displayName")
    core = full_core(entity.get("navn"))
    same_name = bool(core) and [t for t in all_tokens(display) if t not in {"as", "asa"}] == core
    if same_name and same_postcode and street_word and street_word in formatted and (not house or re.search(rf"\b{house}\b", formatted)):
        reasons.append("the place has the same name and the registered street address and postal code")
    return reasons


def collect_places(dossier: Dossier, fetcher: Fetcher, entity: dict[str, Any], site_domain: str | None) -> bool:
    key = places_key()
    if not key:
        return False
    org = dossier.organisation_number
    name = str(entity.get("navn") or "")
    address = entity.get("forretningsadresse") or {}
    label = "Google rating"
    if not fetcher.budget.spend("google_places", PLACES_COST_USD):
        dossier.unavailable("reviews.google", field="review_summary", family="reviews", availability="blocked", label=label,
                            explanation="The declared external-API budget for this run was used up before this company's Google Places lookup.")
        dossier.module("places", "blocked", note="external API budget reached", transient=True)
        return False
    query = " ".join(part for part in [name, " ".join(address.get("adresse") or []), str(address.get("poststed") or "")] if part)
    payload = json.dumps({"textQuery": query, "regionCode": "NO", "languageCode": "no", "maxResultCount": 5}).encode("utf-8")
    response = fetcher.get(PLACES_ENDPOINT, source_class=PLACES, accept="application/json", timeout=15.0, data=payload, attempts=2,
                           headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": PLACES_FIELDS, "Content-Type": "application/json"})
    body = response.json() if response.status == 200 else None
    if not isinstance(body, dict):
        state = "blocked" if response.blocked or response.status in {401, 403, 429} else "failed"
        note = response.blocked or response.error or f"HTTP {response.status}"
        dossier.module("places", state, note=note, response=response, transient=True)
        dossier.unavailable("reviews.google", field="review_summary", family="reviews", availability=state, label=label,
                            explanation=f"Google Places could not be queried in this run ({note}).")
        return False
    matches = [(place, match_place(entity, place, site_domain)) for place in body.get("places") or []]
    matches = [(place, reasons) for place, reasons in matches if reasons]
    if len(matches) != 1:
        state = "ambiguous" if len(matches) > 1 or body.get("places") else "not_available"
        reason = ("several places matched this company's identity" if len(matches) > 1 else
                  "places were returned, but none could be tied to this exact company" if body.get("places") else "no place was returned")
        ev = dossier.add_evidence(response, source_class=PLACES, source_name="Google Places API", claim_span=f"text search: {reason}",
                                  extraction_method="places_text_search_identity_match", source_url=PLACES_ENDPOINT)
        dossier.unavailable("reviews.google", field="review_summary", family="reviews", availability=state, evidence_ids=[ev], label=label,
                            explanation=f"Google Places was searched by name and registered address; {reason}, so no rating is published.")
        dossier.module("places", state, note=reason, response=response)
        return False
    place, reasons = matches[0]
    place_url = f"https://places.googleapis.com/v1/places/{place.get('id')}"
    display = (place.get("displayName") or {}).get("text") if isinstance(place.get("displayName"), dict) else place.get("displayName")
    ev = dossier.add_evidence(response, source_class=PLACES, source_name="Google Places API", source_url=place_url,
                              claim_span=f'{display} | {place.get("formattedAddress")} | rating {place.get("rating")} from {place.get("userRatingCount")} ratings',
                              extraction_method="places_text_search_identity_match")
    dossier.add_claim("profile.google_maps", field="google_maps_place", family="company_profiles", evidence_ids=[ev], confidence=0.93,
                      value={"name": display, "address": place.get("formattedAddress"), "url": place.get("googleMapsUri"), "place_id": place.get("id")},
                      label="Google Maps place", explanation="Matched to this company because " + "; ".join(reasons) + ".")
    rating, count = place.get("rating"), place.get("userRatingCount")
    if isinstance(rating, (int, float)) and isinstance(count, int) and count > 0:
        dossier.add_claim("reviews.google", field="review_summary", family="reviews", evidence_ids=[ev], confidence=0.93, label=label,
                          value={"rating": float(rating), "scale": 5.0, "review_count": count, "shown_by": "Google Maps", "listing_url": place.get("googleMapsUri")},
                          explanation="Aggregate rating from the Google Places API for the place matched to this company (" + "; ".join(reasons)
                                      + "). No review text is collected.")
    else:
        dossier.unavailable("reviews.google", field="review_summary", family="reviews", availability="not_available", evidence_ids=[ev], label=label,
                            explanation="The company's Google Maps place was found and has no rating yet.")
    dossier.module("places", "available", response=response)
    return True
