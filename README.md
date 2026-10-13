# Signalpost agent

Give it Norwegian organisation numbers. It returns one evidence-backed company profile per number, and tells you what changed since the last run.

- **Exact company, every time.** Official facts are fetched by organisation number. A website is published only after it is tied to the exact legal entity; parent, group and brand sites are labelled as related.
- **Every fact has a source.** Each claim carries the source URL, retrieval time, reporting period, the supporting text span and a SHA-256 of the fetched bytes.
- **Nothing invented.** No model writes facts or summaries. A missing value is reported as `not_available`, never as zero.
- **One result per input, always.** Unknown, invalid, blocked and failed inputs each get an explicit terminal envelope.
- **Zero install, zero cost.** Python standard library only. No API keys needed; external API cost per run is $0 unless the evaluator switches on an optional keyed connector.

## Run it

Requires Python 3.10 or newer. There is no install step and nothing to download.

```bash
python -m signalpost run --organisations batch.jsonl --output out/envelopes.jsonl --report out/run-report.json
```

`batch.jsonl` is the file of organisation numbers you are given. JSON, JSONL, CSV and plain text all work, gzipped or not; so does a literal list:

```bash
python -m signalpost run --organisations 923609016,914778271 --output out/envelopes.jsonl --report out/run-report.json
```

The command exits 0 when the run passes its own official-run checks (see `validation` in the report), 1 otherwise. It writes:

| File | Content |
|---|---|
| `out/envelopes.jsonl` | One terminal envelope per input, in input order |
| `out/run-report.json` | Runtime, request count, cost, coverage per field, validation checks |
| `out/site/index.html` | Browsable profiles: search, compare, verify (open the file directly) |
| `state/` | Latest profile per company plus an immutable snapshot of every run, used for the next diff |

The reference runner's flags are accepted too, so this is a drop-in replacement:

```bash
python scripts/run_competition_batch.py --organisations batch.jsonl --bulk brreg-enheter.csv \
  --profiles-output out/profiles.jsonl --output out/envelopes.jsonl --report out/run-report.json \
  --run-id official-001 --expected-count 100
```

`--bulk` is optional. When given, the frozen registry snapshot is used to cross-check each legal name against the live register.

### Budgets

| Flag | Default | Meaning |
|---|---|---|
| `--time-budget-min` | 40 | Wall-clock budget. Optional enrichment stops at 80% of it; every input still gets an envelope |
| `--max-requests` | 20 per company, minimum 2,000 | Hard cap on outbound requests |
| `--workers` | 16 | Companies researched in parallel |

The four official-register calls per company are reserved up front, so enrichment can never starve them. The measured numbers for this commit are in [SUBMISSION.md](SUBMISSION.md).

### Optional keyed connectors

Both are off unless the evaluator sets a key. Without keys the agent behaves exactly as measured in [SUBMISSION.md](SUBMISSION.md).

| Environment variable | Service | What it adds | Cost counted per call |
|---|---|---|---|
| `SIGNALPOST_SEARCH_API_KEY` | Brave Search API | Candidate websites for companies with no site from the register. Candidates only: each still has to pass the identity gate | $0.005 |
| `SIGNALPOST_PLACES_API_KEY` | Google Places API (New) | Google rating, review count and Maps link for a place matched by website, registry phone, or name plus registered address | $0.035 |
| `SIGNALPOST_API_BUDGET_USD` | | Cap on declared spend per run. Default 8; paid calls stop when it is reached | |

A key is sent only as a request header to the service's own host. It is never written to an envelope, report, log or URL.

### Refresh

Run the same command again. The agent diffs each company against `state/latest.jsonl` (or a file passed with `--previous`) and fills `changes` in each envelope. Re-running on unchanged sources produces no changes and no duplicate records. See [REFRESH.md](REFRESH.md).

### Browse the results

```bash
python -m signalpost serve --envelopes out/envelopes.jsonl
```

Open http://127.0.0.1:8787/. Routes: `/companies/<organisation_number>`, `/research?organisation_number=…` (research a company that is not stored yet) and `/refresh?organisation_number=…`. The same site is written to `out/site/` as static files that work on any static host and on a phone.

## Test it

```bash
python -m unittest discover -s tests
python -m signalpost replay --manifest tests/fixtures/refresh-snapshots.json --output out/refresh-demo.json
```

The tests run offline. The replay feeds Builderr's saved old/new source snapshots through the production pipeline and checks the detected changes.

To draw a reproducible batch from the public company universe:

```bash
python -m signalpost select --universe signalpost-company-universe-2025.jsonl.gz --count 100 --seed 20261013 --output data/smoke-100.jsonl
```

## What a profile contains

| Section | Source | Identity proof |
|---|---|---|
| Legal identity, status, industry, addresses, capital, employees | Enhetsregisteret | organisation number |
| Annual accounts, up to three fiscal years, company and group | Regnskapsregisteret | organisation number |
| Leadership, board, auditor | Enhetsregisteret roles | organisation number |
| Registered workplaces | Enhetsregisteret sub-units | organisation number |
| Group parent and subsidiaries | Enhetsregisteret group structure | organisation number |
| Verified official website, public brand, description | Company site | organisation number on the site, or registry-reported site plus full legal name |
| Company-owned social profiles, contact mailbox | Links on the verified site | verified site plus handle match |
| Open positions | Verified site (structured data) and NAV's job board | verified site; advert page states the organisation number |
| Customer rating | Fagfolkguiden trade-directory page | organisation number in the page's structured data |
| Dated activity | Company news feed, registry update feed | verified site; organisation number |
| Wikidata and Wikipedia links | Wikidata | property P2333 (organisation number) |

## Documentation

| File | Topic |
|---|---|
| [AGENT.md](AGENT.md) | Research policy and when the agent abstains |
| [IDENTITY_RESOLUTION.md](IDENTITY_RESOLUTION.md) | The gate that prevents wrong-company publication |
| [DATA_SCHEMA.md](DATA_SCHEMA.md) | Envelope, claim, evidence and change formats |
| [CRAWLERS.md](CRAWLERS.md) | Sources, rights, budgets, URL safety |
| [REFRESH.md](REFRESH.md) | Snapshots, diffs, idempotency |
| [EVAL.md](EVAL.md) | How the agent is measured locally |
| [LIMITATIONS.md](LIMITATIONS.md) | Known gaps |
| [SUBMISSION.md](SUBMISSION.md) | Submission checklist, measured run numbers, declared cost |
