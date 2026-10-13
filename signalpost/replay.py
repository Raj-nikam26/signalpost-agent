"""Deterministic refresh replay and reproducible batch selection (no network needed for the replay)."""
from __future__ import annotations

import gzip
import json
import random
from pathlib import Path
from typing import Any

from .http import SnapshotFetcher
from .pipeline import research_company

REPLAY_OPTIONS = {"website": False, "nav_jobs": False, "registry_updates": False, "account_history": False}


def run_replay(manifest_path: str, output_path: str) -> int:
    """Feed saved 'old' and 'new' source bytes through the production pipeline and score the detected changes."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    orgs = [str(profile["organisation_number"]) for profile in manifest["profiles"]]
    old_fetcher, new_fetcher = SnapshotFetcher(manifest["snapshots"]["old"]), SnapshotFetcher(manifest["snapshots"]["new"])
    events: list[dict[str, Any]] = []
    rerun_changes = 0
    duplicate_claims = 0
    for org in orgs:
        old = research_company(org, old_fetcher, run_id="replay-old", options=REPLAY_OPTIONS)
        new = research_company(org, new_fetcher, run_id="replay-new", previous=old, options=REPLAY_OPTIONS)
        again = research_company(org, SnapshotFetcher(manifest["snapshots"]["new"]), run_id="replay-new-again", previous=new, options=REPLAY_OPTIONS)
        events.extend(new["changes"])
        rerun_changes += len(again["changes"])
        keys = [claim["key"] for claim in again["claims"]]
        duplicate_claims += len(keys) - len(set(keys))
    observed = {(event["organisation_number"], event["group"]) for event in events}
    expected = {(str(item["organisation_number"]), item["field"]) for item in manifest.get("expected_changes", [])}
    true_positive, false_positive, false_negative = len(expected & observed), len(observed - expected), len(expected - observed)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    evidence_complete = all(event.get("source_url") and event.get("retrieved_at") and event.get("effective_at")
                            and event.get("old_content_sha256") and event.get("new_content_sha256") for event in events if event["kind"] == "changed")
    idempotent = rerun_changes == 0 and duplicate_claims == 0
    report = {
        "corpus": manifest.get("corpus", "snapshot replay"), "profiles": len(orgs), "modules": sorted(manifest.get("modules", [])),
        "old_requests": len(old_fetcher.requests), "new_requests": len(new_fetcher.requests),
        "expected_changes": len(expected), "observed_changes": len(observed), "claim_level_events": len(events),
        "true_positive": true_positive, "false_positive": false_positive, "false_negative": false_negative,
        "precision": precision, "recall": recall, "evidence_complete": evidence_complete, "idempotent_rerun": idempotent,
        "prior_evidence_preserved": all(event["old_evidence"] for event in events if event["kind"] != "added"),
        "qualification_passed": precision >= 0.95 and recall >= 0.95 and evidence_complete and idempotent,
        "events": events,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "events"}, ensure_ascii=False, indent=2))
    return 0 if report["qualification_passed"] else 1


def select_batch(universe_path: str, count: int, seed: int, output_path: str) -> int:
    """Reservoir-sample `count` organisation numbers from the public universe file with a fixed seed."""
    opener = gzip.open if universe_path.endswith(".gz") else open
    rng = random.Random(seed)
    chosen: list[str] = []
    seen = 0
    with opener(universe_path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            line = line.strip()
            if not line:
                continue
            org = str(json.loads(line).get("organisation_number") or "") if line.startswith("{") else line.split(",")[0]
            if not org:
                continue
            seen += 1
            if len(chosen) < count:
                chosen.append(org)
            else:
                slot = rng.randrange(seen)
                if slot < count:
                    chosen[slot] = org
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with Path(output_path).open("w", encoding="utf-8", newline="\n") as handle:
        for org in chosen:
            handle.write(json.dumps({"organisation_number": org}) + "\n")
    print(json.dumps({"universe_rows": seen, "selected": len(chosen), "seed": seed, "output": output_path}, indent=2))
    return 0
