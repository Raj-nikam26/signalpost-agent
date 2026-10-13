"""Claim and evidence records. Every published value points at the bytes that support it."""
from __future__ import annotations

import json
from typing import Any

from .http import Response
from .util import AVAILABILITY_STATES, compact, short_hash, utc_now

# source_class vocabulary used across the envelope
OFFICIAL = "official_registry"
OFFICIAL_ACCOUNTS = "official_annual_accounts"
COMPANY_OWNED = "company_owned"
PUBLIC_JOB_BOARD = "public_job_board"
OPEN_KNOWLEDGE = "open_knowledge_base"

LICENCES = {
    OFFICIAL: "NLOD 2.0 (Norwegian Licence for Open Government Data)",
    OFFICIAL_ACCOUNTS: "NLOD 2.0 (Norwegian Licence for Open Government Data)",
    COMPANY_OWNED: "Public company-owned page; robots.txt honoured; facts only, no bulk republication",
    PUBLIC_JOB_BOARD: "NAV arbeidsplassen.no public job listing; robots.txt permits access",
    OPEN_KNOWLEDGE: "CC0 1.0 (Wikidata)",
}


def span_from_json(path: str, value: Any, limit: int = 240) -> str:
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    rendered = " ".join(str(rendered).split())
    if len(rendered) > limit:
        rendered = rendered[: limit - 1] + "…"
    return f"{path}: {rendered}"


class Dossier:
    """Collects evidence, claims and errors for one organisation number."""

    def __init__(self, organisation_number: str) -> None:
        self.organisation_number = organisation_number
        self.evidence: dict[str, dict[str, Any]] = {}
        self.claims: dict[str, dict[str, Any]] = {}
        self.errors: list[dict[str, Any]] = []
        self.modules: dict[str, dict[str, Any]] = {}
        self.requests = 0

    # -- evidence -----------------------------------------------------------------------------
    def add_evidence(
        self,
        response: Response | None,
        *,
        source_class: str,
        source_name: str,
        claim_span: str,
        extraction_method: str,
        source_url: str | None = None,
        effective_at: str | None = None,
        content_sha256: str | None = None,
        retrieved_at: str | None = None,
    ) -> str:
        url = source_url or (response.final_url or response.url if response else "")
        sha = content_sha256 or (response.content_sha256 if response else None)
        evidence_id = "ev-" + short_hash(url, sha, claim_span)
        if evidence_id not in self.evidence:
            self.evidence[evidence_id] = compact({
                "id": evidence_id,
                "source_url": url,
                "requested_url": response.url if response and response.url != url else None,
                "redirect_chain": response.redirect_chain if response else None,
                "source_class": source_class,
                "source_name": source_name,
                "licence": LICENCES.get(source_class),
                "http_status": response.status if response else None,
                "retrieved_at": retrieved_at or (response.retrieved_at if response else utc_now()),
                "effective_at": effective_at,
                "content_sha256": sha,
                "claim_span": claim_span,
                "extraction_method": extraction_method,
            })
        return evidence_id

    # -- claims -------------------------------------------------------------------------------
    def add_claim(
        self,
        key: str,
        *,
        field: str,
        family: str,
        value: Any,
        evidence_ids: list[str],
        availability: str = "available",
        confidence: float | None = None,
        label: str | None = None,
        period: dict[str, Any] | None = None,
        effective_date: str | None = None,
        explanation: str | None = None,
        unit: str | None = None,
    ) -> dict[str, Any] | None:
        if availability not in AVAILABILITY_STATES:
            raise ValueError(f"unknown availability state {availability!r}")
        if availability == "available":
            if value in (None, "", [], {}):
                return None  # absence is never published as a value
            if not evidence_ids:
                raise ValueError(f"claim {key} has a value but no evidence")
        else:
            value = None
        claim = {
            "id": "cl-" + short_hash(self.organisation_number, key),
            "key": key,
            "field": field,
            "family": family,
            "label": label or field.replace("_", " ").capitalize(),
            "value": value,
            "unit": unit,
            "availability": availability,
            "confidence": confidence if availability == "available" else None,
            "period": period,
            "effective_date": effective_date,
            "evidence_ids": list(dict.fromkeys(evidence_ids)),
            "explanation": explanation,
        }
        self.claims[key] = {name: item for name, item in claim.items() if item is not None or name == "value"}
        return self.claims[key]

    def unavailable(self, key: str, *, field: str, family: str, availability: str, explanation: str,
                    evidence_ids: list[str] | None = None, label: str | None = None) -> None:
        self.add_claim(key, field=field, family=family, value=None, evidence_ids=evidence_ids or [],
                       availability=availability, explanation=explanation, label=label)

    # -- bookkeeping --------------------------------------------------------------------------
    def error(self, module: str, code: str, message: str, url: str | None = None) -> None:
        self.errors.append(compact({"module": module, "code": code, "message": message, "source_url": url, "at": utc_now()}))

    def module(self, name: str, availability: str, *, note: str | None = None, response: Response | None = None,
               retries: int = 0, transient: bool = False) -> None:
        state = "blocked_robots" if availability == "blocked" and note and "robots" in note else STARTER_STATE[availability]
        record = compact({
            "state": state,
            "availability": availability,
            "final_timestamp": (response.retrieved_at if response else None) or utc_now(),
            "note": note,
            "transient": transient or None,
        })
        record["retry_count"] = retries or (max(0, response.attempts - 1) if response else 0)
        self.modules[name] = record

    def family_claims(self, family: str, *, available_only: bool = True) -> list[dict[str, Any]]:
        return [claim for claim in self.claims.values()
                if claim["family"] == family and (claim["availability"] == "available" or not available_only)]


# Mapping onto the module-state vocabulary used by the Builderr reference runner.
STARTER_STATE = {
    "available": "complete",
    "not_available": "not_found",
    "not_applicable": "not_applicable",
    "blocked": "blocked_policy",
    "ambiguous": "not_found",
    "failed": "source_error",
}
