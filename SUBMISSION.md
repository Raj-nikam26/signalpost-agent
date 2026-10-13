# Submission

## Checklist

| Required item | Where |
|---|---|
| Repository link | Fill in after pushing this repository |
| Exact commit hash | `git rev-parse HEAD` after the push |
| 100-company smoke-test result | [reports/smoke-100/run-report.json](reports/smoke-100/run-report.json), envelopes in `reports/smoke-100/envelopes.jsonl.gz` |
| One command to run | See below |
| Models / APIs / licences | See below |
| Expected cost per official run | $0 |
| 1,000 company profiles | `reports/batch-1000/envelopes-1000.jsonl.gz`, report in [reports/batch-1000/run-report.json](reports/batch-1000/run-report.json) |
| Browsable sample | `docs/index.html` (the 100 smoke-test profiles; works from disk or GitHub Pages) |

## One command

Python 3.10+, no install step, no keys:

```bash
python -m signalpost run --organisations <batch file> --output out/envelopes.jsonl --report out/run-report.json
```

Reference-runner flags (`--bulk`, `--profiles-output`, `--run-id`, `--expected-count`, `--workers`, `--resume`) are accepted by the same command and by `scripts/run_competition_batch.py`.

## Models, APIs and licences

| Item | Detail |
|---|---|
| Language models | None. Extraction, identity decisions and summaries are deterministic |
| Paid APIs | None |
| Credentials | None |
| Brønnøysund Register Centre open APIs | Enhetsregisteret, roles, sub-units, group structure, update feed, Regnskapsregisteret. NLOD 2.0 |
| Wikidata Query Service | CC0 |
| Company websites | Public pages, robots.txt honoured, facts only |
| NAV arbeidsplassen.no | Public job board, robots.txt allows all paths, throttled to one request per 7 s |
| Dependencies | Python standard library only |

## Measured runs

Both batches were drawn from the public 411,160-company universe with a fixed seed (`python -m signalpost select`, seeds 20261013 and 424242). Run from a home connection in India, about 0.9 s per request to the Norwegian register.

### 100-company smoke test (`smoke-100-run1`)

| Measure | Value |
|---|---|
| Companies in / envelopes out | 100 / 100 |
| Terminal states | available 100 |
| Runtime | 244 s (210 s of it is the rate-limited filing-years endpoint, used only for small batches) |
| Outbound requests | 747 (7.5 per company) |
| External API cost | $0 |
| Published facts | 8,031 (80 per company), 8,166 evidence records |
| Website gate | verified 8, ambiguous 17, no usable candidate 75 |
| Validation | all checks passed |

| Section | Companies with facts | Other states |
|---|---|---|
| Legal identity | 100 | none |
| Annual accounts | 100 | none |
| Leadership | 100 | none |
| Registered workplaces | 61 | not_available 39 |
| Group structure | 100 | none |
| Official website (verified or labelled group site) | 10 | not_available 75, ambiguous 15 |
| Company-owned profiles | 5 | not_available 95 |
| Contact details | 7 | not_available 93 |
| Hiring | 2 | blocked 92, not_available 6 |
| Dated public activity | 100 | none |

### Refresh rerun on the same 100 (`smoke-100-run2`)

100 of 100 compared with the previous snapshot, 0 changes, 0 carried-forward claims, same fact count (8,031). Report: [reports/smoke-100/run-report-refresh.json](reports/smoke-100/run-report-refresh.json).

### 1,000-company run (`batch-1000-run1`)

| Measure | Value |
|---|---|
| Companies in / envelopes out | 1,000 / 1,000 |
| Terminal states | available 1,000 |
| Runtime | 642 s (10.7 minutes) |
| Outbound requests | 6,805 (6.8 per company) |
| External API cost | $0 |
| Published facts | 78,537 (79 per company), 79,725 evidence records |
| Website gate | verified 129, ambiguous 146, blocked 15, no usable candidate 710 |
| Validation | all checks passed |

| Section | Companies with facts | Other states |
|---|---|---|
| Legal identity | 1,000 | none |
| Annual accounts | 998 | failed 2 (register returned an error) |
| Leadership | 1,000 | none |
| Registered workplaces | 776 | not_available 224 |
| Group structure | 1,000 | none |
| Official website (verified or labelled group site) | 139 | not_available 710, ambiguous 136, blocked 15 |
| Company-owned profiles | 79 | not_available 921 |
| Contact details | 80 | not_available 920 |
| Hiring | 26 | blocked 871, not_available 103 |
| Dated public activity | 1,000 | none |

### Read these numbers with care

- **Hiring is mostly `blocked`.** NAV's job board answered HTTP 429 to all three requests in each of these runs, after an earlier development burst. The agent stopped asking, as designed. The 26 companies with hiring facts come from careers pages on verified sites. From a fresh IP address the board may answer; that was not observed in these runs.
- **"Dated public activity" is 100% because of the register's update feed**, which exists for every company. Dated news from company sites is far rarer.
- **Wrong-company check**: every name-based website match in the 100-company sample and the first 45 in the 1,000-company run were read by hand by the author; none looked wrong. There is no independent label set.
- The smoke run was made one small change before the final commit (clean handling of a termination signal). The refresh rerun and the 1,000-company run used the committed code.

## Budget fit

| Limit | Assumed | Measured |
|---|---|---|
| Time | 45 minutes | 4.1 minutes for 100 companies, 10.7 minutes for 1,000 |
| Requests | 2,000 per 100 companies | 747 per 100 companies |
| External API cost | $10 | $0 |

The exact official limits are not published on the challenge page; `--time-budget-min` and `--max-requests` set them.

## Email draft

To: submit@builderr.ai
Subject: Signalpost Round 1 submission

```
Agent name: Signalpost Evidence Agent
Repository URL: <your GitHub URL>
Exact commit hash: <output of git rev-parse HEAD>
100-company smoke-test result: <repo URL>/blob/<hash>/reports/smoke-100/run-report.json
  (envelopes: reports/smoke-100/envelopes.jsonl.gz; refresh rerun: reports/smoke-100/run-report-refresh.json;
   1,000 profiles: reports/batch-1000/envelopes-1000.jsonl.gz)
One-command run instruction:
  python -m signalpost run --organisations <batch file> --output out/envelopes.jsonl --report out/run-report.json
  (Python 3.10+, standard library only, no install step, no keys)
Models / APIs / licences: no language model, no paid API, no credentials.
  Brønnøysund open APIs (NLOD 2.0), Wikidata Query Service (CC0), public company websites (robots.txt honoured),
  NAV arbeidsplassen.no public job board (throttled).
Expected cost per official run: $0 external API cost. Measured 747 requests and 4.1 minutes per 100 companies;
  6,805 requests and 10.7 minutes per 1,000 companies.
Contact for results: <your email>
```
