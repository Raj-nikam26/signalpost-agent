"""External-footprint observations in the shape used by the Builderr reference kit.

The same facts as `claims`, re-expressed as one observation per external signal (platform + signal type) so that
tooling written against the reference kit's observation schema can read this agent's output without a mapping step.
"""
from __future__ import annotations

from typing import Any

PLATFORMS = {"linkedin", "facebook", "instagram", "x", "youtube", "tiktok"}
SOURCE_CLASS = {"official_registry": "official_registry", "official_annual_accounts": "official_registry", "company_owned": "company_site",
                "public_job_board": "job_board", "open_knowledge_base": "open_knowledge_base", "public_directory": "customer_review"}


def build_footprint(organisation_number: str, claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item["id"]: item for item in evidence}
    observations: list[dict[str, Any]] = []

    def add(claim: dict[str, Any], platform: str, signal_type: str, *, mode: str, rights: str = "approved", proof: str,
            metrics: dict[str, Any] | None = None, url: str | None = None) -> None:
        source = next((by_id[i] for i in claim.get("evidence_ids", []) if i in by_id), None)
        if source is None or not source.get("content_sha256"):
            return
        observations.append({key: value for key, value in {
            "id": f"obs-{claim['id'][3:]}", "organisation_number": organisation_number, "platform": platform, "signal_type": signal_type,
            "source_url": source["source_url"], "target_url": url, "retrieved_at": source["retrieved_at"],
            "published_at": claim.get("effective_date"), "content_sha256": source["content_sha256"], "exact_entity": True,
            "identity_proof": [{"type": proof, "claim_id": claim["id"], "confidence": claim.get("confidence")}],
            "acquisition_mode": mode, "rights_status": rights, "source_class": SOURCE_CLASS.get(source.get("source_class"), source.get("source_class")),
            "evidence_span": source.get("claim_span"), "metrics": metrics, "text": claim.get("label"), "claim_key": claim["key"],
        }.items() if value is not None})

    for claim in claims:
        if claim["availability"] != "available" or claim.get("carried_forward"):
            continue
        field, value = claim["field"], claim["value"]
        if field == "official_website":
            add(claim, "company_site", "company_profile", mode="permitted_public_page", proof="website_identity_gate", url=value)
        elif field == "social_profile" and value.get("platform") in PLATFORMS:
            add(claim, value["platform"], "profile_handle", mode="permitted_public_page", proof="outbound_link_on_verified_company_site", url=value["url"])
        elif field == "job_posting":
            nav = claim["key"].startswith("hiring.job.nav.")
            add(claim, "job_board" if nav else "company_site", "job_posting", mode="permitted_public_page",
                proof="organisation_number_on_advert_page" if nav else "verified_company_site", url=value.get("url"))
        elif field == "company_news_item":
            add(claim, "company_site", "public_post", mode="permitted_public_page", proof="verified_company_site", url=value.get("url"))
        elif field == "review_summary":
            add(claim, "company_directory", "review_summary", mode="permitted_public_page", rights="review_required",
                proof="organisation_number_in_directory_structured_data", url=value.get("listing_url"),
                metrics={"rating": value["rating"], "review_count": value["review_count"], "scale": value["scale"]})
        elif field == "employees_registered":
            add(claim, "brreg", "workforce_snapshot", mode="official_api", proof="organisation_number_lookup", metrics={"registered_employees": value})
        elif field == "wikidata_item":
            add(claim, "wikidata", "company_profile", mode="official_api", proof="wikidata_P2333_organisation_number", url=value.get("url"))
        elif field == "wikipedia_article":
            add(claim, "wikipedia", "company_profile", mode="official_api", proof="wikidata_P2333_organisation_number", url=value.get("url"))
    platforms = sorted({item["platform"] for item in observations})
    return {
        "status": "available" if observations else "not_available",
        "observations": observations,
        "platforms": platforms,
        "external_platforms": [name for name in platforms if name != "brreg"],
        "active_job_count": sum(1 for item in observations if item["signal_type"] == "job_posting"),
        "public_item_count": sum(1 for item in observations if item["signal_type"] == "public_post"),
        "review_signal_count": sum(1 for item in observations if item["signal_type"] == "review_summary"),
        "sentiment": {"status": "abstain", "reason": "No evaluated exact-company sentiment source is used; company-owned text is promotional."},
    }
