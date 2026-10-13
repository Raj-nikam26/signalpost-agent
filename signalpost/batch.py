"""Batch runner: N organisation numbers in, exactly N terminal envelopes out, inside a time and request budget."""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import re
import signal
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, __version__
from .http import Budget, Fetcher
from .pipeline import CORE_REQUESTS, collect_company, collect_jobs, failed_envelope, finalise
from .refresh import load_envelopes, save_state
from .sources.external import RateGate, nav_not_checked, wikidata_lookup
from .util import AVAILABILITY_STATES, clean_org_number, utc_now, valid_org_number

ORG_KEYS = ("organisation_number", "organization_number", "organisasjonsnummer", "orgnr", "org_number", "org_nr", "orgNumber", "org", "id")


def _open_text(path: Path) -> io.TextIOBase:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8-sig")  # type: ignore[return-value]
    return path.open("r", encoding="utf-8-sig")


def read_inputs(source: str) -> tuple[list[str], dict[str, Any]]:
    """Read organisation numbers from JSON, JSONL, CSV or plain text (optionally gzipped), or a comma-separated literal.

    Order is preserved. Duplicates collapse to one row. Malformed values are kept so they still receive an envelope.
    """
    values: list[Any] = []
    path = Path(source)
    if source == "-":
        text, kind = sys.stdin.read(), ".txt"
    elif path.exists():
        with _open_text(path) as handle:
            text = handle.read()
        kind = Path(path.stem).suffix if path.suffix == ".gz" else path.suffix
    else:
        text, kind = source.replace(",", "\n"), ".txt"
    stripped = text.lstrip()
    if kind == ".json" or (kind not in {".jsonl", ".csv"} and stripped[:1] in "[{" and "\n{" not in stripped):
        try:
            body = json.loads(text)
            if isinstance(body, dict):
                body = next((body[key] for key in ("organisation_numbers", "organisations", "companies", "inputs", "items", "data") if isinstance(body.get(key), list)), [body])
            values = list(body)
        except ValueError:
            values = []
    if not values:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line[:1] == "{":
                try:
                    values.append(json.loads(line))
                    continue
                except ValueError:
                    pass
            values.append(line)
    orgs: list[str] = []
    invalid: list[str] = []
    duplicates = 0
    for value in values:
        raw: Any = value
        if isinstance(value, dict):
            raw = next((value[key] for key in ORG_KEYS if value.get(key) not in (None, "")), "")
        text_value = str(raw).strip()
        match = re.search(r"(?<!\d)(\d{3})[ .]?(\d{3})[ .]?(\d{3})(?!\d)", text_value)
        org = "".join(match.groups()) if match else clean_org_number(text_value)
        if not org:
            if not re.search(r"\d", text_value):
                continue  # header row or blank
            org = text_value[:40]
        if org in orgs:
            duplicates += 1
            continue
        if not valid_org_number(org):
            invalid.append(org)
        orgs.append(org)
    return orgs, {"source": source if len(source) < 200 else source[:200], "received": len(values), "unique": len(orgs),
                  "duplicates_collapsed": duplicates, "invalid_organisation_numbers": invalid[:50]}


def read_bulk_names(path: str | None, wanted: set[str]) -> tuple[dict[str, str], dict[str, Any]]:
    """Optional frozen registry snapshot (Brreg CSV or JSONL, optionally gzipped): legal names for identity anchoring."""
    if not path or not Path(path).exists():
        return {}, {}
    names: dict[str, str] = {}
    digest = hashlib.sha256()
    source = Path(path)
    with source.open("rb") as raw:
        for block in iter(lambda: raw.read(1 << 20), b""):
            digest.update(block)
    with _open_text(source) as handle:
        first = handle.readline()
        handle.seek(0)
        if first.lstrip().startswith("{"):
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                org = clean_org_number(row.get("organisation_number") or row.get("organisasjonsnummer"))
                if org in wanted:
                    names[org] = str(row.get("name") or row.get("navn") or "")
        else:
            reader = csv.reader(handle, delimiter=";" if first.count(";") > first.count(",") else ",")
            header = [cell.strip().strip('"').lower() for cell in next(reader, [])]
            org_col = next((i for i, cell in enumerate(header) if cell in {"organisasjonsnummer", "organisation_number", "orgnr"}), 0)
            name_col = next((i for i, cell in enumerate(header) if cell in {"navn", "name"}), 1)
            for row in reader:
                if len(row) > max(org_col, name_col) and row[org_col] in wanted:
                    names[row[org_col]] = row[name_col]
                    if len(names) == len(wanted):
                        break
    return names, {"registry_snapshot_sha256": digest.hexdigest(), "requested": len(wanted), "selected": len(names)}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def validate(envelopes: list[dict[str, Any]], orgs: list[str]) -> dict[str, Any]:
    """The official-run checks that can be verified locally."""
    emitted = [envelope.get("organisation_number") for envelope in envelopes]
    problems: list[str] = []
    unsupported = untraceable = missing_dates = 0
    for envelope in envelopes:
        evidence = {item["id"]: item for item in envelope.get("evidence", [])}
        for claim in envelope.get("claims", []):
            if claim.get("availability") not in AVAILABILITY_STATES:
                problems.append(f"{envelope['organisation_number']}: bad availability on {claim.get('key')}")
            if claim.get("availability") != "available":
                if claim.get("value") is not None:
                    problems.append(f"{envelope['organisation_number']}: unavailable claim {claim.get('key')} carries a value")
                continue
            linked = [evidence[i] for i in claim.get("evidence_ids", []) if i in evidence]
            if not linked or not all(item.get("source_url") and item.get("retrieved_at") for item in linked):
                unsupported += 1
            if claim.get("family") == "annual_accounts" and claim.get("field") != "accounts_history_years":
                if not claim.get("period"):
                    missing_dates += 1
                if isinstance(claim.get("value"), (int, float)) and not any(
                        re.search(r"(?<![\d.])" + re.escape(str(claim["value"])) + r"(\.0+)?(?![\d])", item.get("claim_span", "")) for item in linked):
                    untraceable += 1
    checks = {
        "exact_expected_count": len(envelopes) == len(orgs),
        "one_envelope_per_input_in_order": emitted == orgs,
        "unique_organisation_numbers": len(emitted) == len(set(emitted)),
        "all_states_terminal": all(envelope.get("status") in AVAILABILITY_STATES for envelope in envelopes),
        "all_claim_states_valid": not problems,
        "every_published_claim_has_source_and_retrieval_time": unsupported == 0,
        "every_financial_value_traceable_to_its_source_span": untraceable == 0,
        "every_financial_claim_has_reporting_period": missing_dates == 0,
        "zero_silent_drops": set(emitted) == set(orgs),
    }
    return {"passed": all(checks.values()), "checks": checks, "problems": problems[:20],
            "unsupported_claims": unsupported, "untraceable_financial_values": untraceable}


def run_batch(args: Any) -> dict[str, Any]:
    started_wall = utc_now()
    started = time.monotonic()
    orgs, input_info = read_inputs(args.organisations)
    if not orgs:
        raise SystemExit(f"No organisation numbers found in {args.organisations!r}")
    count = len(orgs)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("signalpost-%Y%m%dT%H%M%SZ")
    time_budget = float(args.time_budget_min) * 60
    max_requests = int(args.max_requests or os.environ.get("SIGNALPOST_MAX_REQUESTS") or max(2000, int(args.requests_per_company) * count))
    soft_deadline = started + time_budget * 0.80   # optional enrichment stops being started here
    hard_deadline = started + time_budget * 0.95   # companies not started by now get an explicit `failed` envelope
    budget = Budget(max_requests, deadline=soft_deadline)
    budget.reserve(CORE_REQUESTS * count)
    fetcher = Fetcher(budget)

    output = Path(args.output)
    state_dir = None if args.no_state else Path(args.state_dir)
    previous = load_envelopes(args.previous) if args.previous else (load_envelopes(state_dir / "latest.jsonl") if state_dir else {})
    resumed: dict[str, dict[str, Any]] = {}
    if args.resume and output.exists():
        resumed = {org: row for org, row in load_envelopes(output).items() if row.get("run_id") == run_id and row.get("status") != "failed" and org in set(orgs)}
    bulk_names, bulk_info = read_bulk_names(args.bulk, set(orgs))

    history_mode = args.history
    account_history = history_mode == "on" or (history_mode == "auto" and count * 2.2 <= time_budget * 0.25)
    options = {"website": not args.no_website, "nav_jobs": not args.no_jobs, "guess_domains": not args.no_guess,
               "registry_updates": not args.no_updates, "account_history": account_history}
    valid = [org for org in orgs if valid_org_number(org) and org not in resumed]
    wikidata = wikidata_lookup(fetcher, valid) if valid and not args.no_wikidata else {}

    results: dict[str, dict[str, Any]] = dict(resumed)
    log = (lambda message: print(message, file=sys.stderr, flush=True)) if not args.quiet else (lambda message: None)
    log(f"[signalpost] run {run_id}: {count} companies, budget {max_requests} requests / {args.time_budget_min} min, {args.workers} workers")

    contexts: dict[str, dict[str, Any]] = {}

    def work(org: str) -> dict[str, Any] | None:
        budget.reserve(-CORE_REQUESTS)
        if time.monotonic() >= hard_deadline:
            return None
        return collect_company(org, fetcher, wikidata=wikidata.get(org), options=options)

    def stop(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt  # a termination signal ends the run cleanly: every input still gets its envelope

    try:
        signal.signal(signal.SIGTERM, stop)
    except (ValueError, AttributeError, OSError):
        pass  # not the main thread, or the platform has no SIGTERM

    pending = [org for org in orgs if org not in results]
    pool = ThreadPoolExecutor(max_workers=max(1, int(args.workers)))
    futures = {pool.submit(work, org): org for org in pending}
    interrupted = False
    try:
        for index, future in enumerate(as_completed(futures), 1):
            org = futures[future]
            try:
                context = future.result()
            except Exception as exc:  # noqa: BLE001 - belt and braces: collect_company already never raises
                results[org] = failed_envelope(org, run_id, "unhandled_exception", f"{type(exc).__name__}: {exc}"[:300], previous.get(org))
                continue
            if context is None:
                results[org] = failed_envelope(org, run_id, "time_budget", "The run's time budget was exhausted before this company could be researched.", previous.get(org))
            else:
                contexts[org] = context
            if index % 25 == 0 or index == len(pending):
                log(f"[signalpost] {index}/{len(pending)} collected, {budget.used} requests, {int(time.monotonic() - started)}s elapsed")
    except KeyboardInterrupt:
        interrupted = True
        for future in futures:
            future.cancel()
    finally:
        pool.shutdown(wait=not interrupted, cancel_futures=True)

    # NAV's job board tolerates only a few requests a minute, so it is searched in one throttled pass, largest employers first.
    if options["nav_jobs"] and contexts and not interrupted:
        gate = RateGate(interval=float(args.nav_interval))
        nav_deadline = min(soft_deadline, time.monotonic() + time_budget * float(args.nav_share))
        queue = sorted((context for context in contexts.values() if context["entity"] is not None and not context["entity"].get("slettedato")),
                       key=lambda context: -(context["entity"].get("antallAnsatte") or 0))
        searched = 0
        try:
            for context in queue:
                if time.monotonic() + gate.interval >= nav_deadline or not gate.open:
                    reason = ("NAV's job board kept answering HTTP 429 (rate limit) in this run" if not gate.open else
                              "NAV's job board allows only a few requests a minute and the time reserved for it ran out before this company's turn")
                    nav_not_checked(context["dossier"], reason + ", so it was not searched for this company. Larger employers are searched first.")
                    continue
                collect_jobs(context, fetcher, gate)
                searched += 1
        except KeyboardInterrupt:
            interrupted = True
        log(f"[signalpost] job board: {searched}/{len(queue)} companies searched, {budget.used} requests, {int(time.monotonic() - started)}s elapsed")

    for org, context in contexts.items():
        try:
            results[org] = finalise(context, run_id=run_id, previous=previous.get(org))
        except Exception as exc:  # noqa: BLE001
            results[org] = failed_envelope(org, run_id, "unhandled_exception", f"{type(exc).__name__}: {exc}"[:300], previous.get(org))
    for org in orgs:  # nothing is ever silently dropped
        if org not in results:
            results[org] = failed_envelope(org, run_id, "interrupted", "The run was interrupted before this company finished.", previous.get(org))

    envelopes = [results[org] for org in orgs]
    for envelope in envelopes:
        name = bulk_names.get(envelope["organisation_number"])
        live = (envelope.get("identity") or {}).get("legal_name")
        if name and live:
            envelope["identity_anchor"] = {"snapshot_name": name, "live_name": live, "matches": name.strip().casefold() == live.strip().casefold()}
    completed_wall = utc_now()
    validation = validate(envelopes, orgs)
    write_jsonl(output, envelopes)
    if args.profiles_output:
        write_jsonl(Path(args.profiles_output), [envelope["profile"] for envelope in envelopes])
    if state_dir:
        save_state(state_dir, run_id, envelopes)

    latencies = sorted(budget.latencies_ms)
    runtimes = sorted(envelope["operations"]["runtime_ms"] for envelope in envelopes)
    families = Counter(claim["family"] for envelope in envelopes for claim in envelope["claims"] if claim["availability"] == "available")
    fields = Counter(claim["field"] for envelope in envelopes for claim in envelope["claims"] if claim["availability"] == "available")
    companies_with = Counter(family for envelope in envelopes for family in {claim["family"] for claim in envelope["claims"] if claim["availability"] == "available"})
    section_states = {name: dict(Counter(envelope["sections"][name]["availability"] for envelope in envelopes if name in envelope.get("sections", {})))
                      for name in (envelopes[0].get("sections") or {})}
    gate = Counter((envelope["modules"].get("website") or {}).get("availability", "not_run") for envelope in envelopes)
    change_types = Counter(change["change_type"] for envelope in envelopes for change in envelope["changes"])
    elapsed = time.monotonic() - started
    report = {
        "agent": f"signalpost-agent/{__version__}", "schema_version": SCHEMA_VERSION, "run_id": run_id,
        "started_at": started_wall, "completed_at": completed_wall, "runtime_seconds": round(elapsed, 1),
        "expected_count": args.expected_count if args.expected_count is not None else count,
        "input": input_info, "emitted_envelopes": len(envelopes),
        "resumed_profiles": len(resumed), "profiles_fetched_this_run": len(pending),
        "status_counts": dict(Counter(envelope["status"] for envelope in envelopes)),
        "section_availability": section_states,
        "coverage": {
            "companies_with_family": dict(sorted(companies_with.items())),
            "published_claims_by_family": dict(sorted(families.items())),
            "published_claims_by_field": dict(sorted(fields.items())),
            "published_claims_total": sum(families.values()),
            "claims_per_company_mean": round(sum(families.values()) / count, 1),
            "evidence_records_total": sum(len(envelope["evidence"]) for envelope in envelopes),
        },
        "website_identity_gate": dict(gate),
        "refresh": {
            "compared_with_previous": sum(1 for envelope in envelopes if not envelope["refresh"]["first_run"]),
            "first_run": sum(1 for envelope in envelopes if envelope["refresh"]["first_run"]),
            "changes_total": sum(len(envelope["changes"]) for envelope in envelopes),
            "companies_with_changes": sum(1 for envelope in envelopes if envelope["changes"]),
            "change_types": dict(change_types),
            "carried_forward_claims": sum(envelope["refresh"]["carried_forward"] for envelope in envelopes),
            "state_dir": str(state_dir) if state_dir else None, "previous_source": args.previous or (str(state_dir / "latest.jsonl") if state_dir and previous else None),
        },
        "operations": {
            "requests": budget.used, "request_budget": max_requests, "requests_per_company_mean": round(budget.used / count, 2),
            "requests_by_source_class": dict(sorted(budget.by_class.items())), "dns_lookups": budget.dns_lookups, "bytes": budget.bytes,
            "p50_ms": latencies[len(latencies) // 2] if latencies else None,
            "p95_ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None,
            "company_runtime_p50_ms": runtimes[len(runtimes) // 2], "company_runtime_p95_ms": runtimes[min(len(runtimes) - 1, int(len(runtimes) * 0.95))],
            "time_budget_minutes": float(args.time_budget_min), "within_time_budget": elapsed <= time_budget,
            "within_request_budget": budget.used <= max_requests, "third_party_cost_usd": 0.0, "models": [], "workers": int(args.workers),
        },
        "modules_enabled": options, "registry": bulk_info or {"identity_anchor": "live Enhetsregisteret API, exact organisation-number lookup"},
        "errors": dict(Counter(f"{error['module']}:{error['code']}" for envelope in envelopes for error in envelope["errors"])),
        "interrupted": interrupted,
        "expected_count_matches": args.expected_count is None or args.expected_count == count,
        "validation": validation,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.site_output:
        from .site import build_site
        build_site(envelopes, Path(args.site_output), report)
    return report
