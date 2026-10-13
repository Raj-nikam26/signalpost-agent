#!/usr/bin/env python3
"""Drop-in entry point with the same flags as the Builderr reference runner.

python scripts/run_competition_batch.py --organisations batch.jsonl --output out/envelopes.jsonl --report out/run-report.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signalpost.__main__ import main  # noqa: E402

if __name__ == "__main__":
    # Flags the reference runner has and this agent does not need are accepted and ignored.
    compatibility = argparse.ArgumentParser(add_help=False)
    compatibility.add_argument("--checkpoint-every")
    compatibility.add_argument("--modules")
    _, remaining = compatibility.parse_known_args()
    raise SystemExit(main(["run", *remaining]))
