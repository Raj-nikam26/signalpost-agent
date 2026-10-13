"""Command line: python -m signalpost run --organisations batch.jsonl --output out/envelopes.jsonl"""
from __future__ import annotations

import argparse
import json
import sys


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--organisations", "--input", "-i", required=True,
                        help="Batch of organisation numbers: JSON, JSONL, CSV or text file (optionally .gz), '-' for stdin, or a comma-separated list")
    parser.add_argument("--output", "-o", default="out/envelopes.jsonl", help="Terminal envelopes, one JSON object per input, in input order")
    parser.add_argument("--report", default="out/run-report.json", help="Machine-readable run report")
    parser.add_argument("--profiles-output", help="Optional: profiles in the Builderr reference-runner shape")
    parser.add_argument("--run-id", help="Run identifier (default: UTC timestamp)")
    parser.add_argument("--expected-count", type=int, help="Optional sanity check on the batch size")
    parser.add_argument("--bulk", help="Optional frozen registry snapshot (Brreg CSV/JSONL, .gz ok) used to cross-check legal names")
    parser.add_argument("--previous", help="Envelopes from an earlier run to diff against (default: the state directory)")
    parser.add_argument("--state-dir", default="state", help="Where the latest profile per company and immutable run snapshots are kept")
    parser.add_argument("--no-state", action="store_true", help="Do not read or write the state directory")
    parser.add_argument("--resume", action="store_true", help="Reuse finished envelopes from --output that carry the same --run-id")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--time-budget-min", type=float, default=40.0, help="Wall-clock budget; the run always finishes with one envelope per input")
    parser.add_argument("--max-requests", type=int, help="Hard cap on outbound requests (default: 20 per company, minimum 2000)")
    parser.add_argument("--requests-per-company", type=int, default=20)
    parser.add_argument("--history", choices=["auto", "on", "off"], default="auto", help="List every filed accounts year (rate-limited endpoint)")
    parser.add_argument("--nav-interval", type=float, default=7.0, help="Seconds between requests to NAV's rate-limited job board")
    parser.add_argument("--nav-share", type=float, default=0.25, help="Largest share of the time budget the job-board pass may use")
    parser.add_argument("--no-website", action="store_true")
    parser.add_argument("--no-jobs", action="store_true")
    parser.add_argument("--no-guess", action="store_true", help="Never derive a candidate domain from the legal name")
    parser.add_argument("--no-updates", action="store_true")
    parser.add_argument("--no-reviews", action="store_true", help="Skip the trade-directory rating lookup")
    parser.add_argument("--no-wikidata", action="store_true")
    parser.add_argument("--site-output", default="out/site", help="Build the browsable profile site here ('' to skip)")
    parser.add_argument("--quiet", action="store_true")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="signalpost", description="Norwegian company research agent with claim-level evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_run_arguments(commands.add_parser("run", help="Research a batch and write one terminal envelope per organisation number"))
    site = commands.add_parser("site", help="Build the static profile site from an envelopes file")
    site.add_argument("--envelopes", default="out/envelopes.jsonl")
    site.add_argument("--report", default="out/run-report.json")
    site.add_argument("--output", default="out/site")
    serve = commands.add_parser("serve", help="Serve the profile site with live /research and /refresh routes")
    serve.add_argument("--envelopes", default="out/envelopes.jsonl")
    serve.add_argument("--state-dir", default="state")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    replay = commands.add_parser("replay", help="Replay saved old/new source snapshots through the refresh pipeline")
    replay.add_argument("--manifest", required=True)
    replay.add_argument("--output", required=True)
    select = commands.add_parser("select", help="Draw a reproducible random batch from the public company universe")
    select.add_argument("--universe", required=True)
    select.add_argument("--count", type=int, default=100)
    select.add_argument("--seed", type=int, default=20261013)
    select.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    if args.command == "run":
        from .batch import run_batch
        report = run_batch(args)
        summary = {key: report[key] for key in ("run_id", "emitted_envelopes", "runtime_seconds", "status_counts")}
        summary.update({"requests": report["operations"]["requests"], "claims": report["coverage"]["published_claims_total"],
                        "changes": report["refresh"]["changes_total"], "validation_passed": report["validation"]["passed"],
                        "output": args.output, "report": args.report})
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        if report["interrupted"]:
            import os
            os._exit(1)  # outputs are written; do not wait for worker threads still blocked on the network
        return 0 if report["validation"]["passed"] else 1
    if args.command == "site":
        from pathlib import Path

        from .refresh import load_envelopes
        from .site import build_site
        report_path = Path(args.report)
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        index = build_site(list(load_envelopes(args.envelopes).values()), Path(args.output), report)
        print(json.dumps({"site": args.output, "companies": len(index)}, indent=2))
        return 0
    if args.command == "serve":
        from .server import serve as run_server
        run_server(args)
        return 0
    if args.command == "replay":
        from .replay import run_replay
        return run_replay(args.manifest, args.output)
    if args.command == "select":
        from .replay import select_batch
        return select_batch(args.universe, args.count, args.seed, args.output)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
