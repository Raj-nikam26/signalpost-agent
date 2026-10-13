# Submission

## Checklist

| Required item | Where |
|---|---|
| Repository link | Fill in after pushing this repository |
| Exact commit hash | `git rev-parse HEAD` after the push |
| 100-company smoke-test result | [reports/smoke-100/run-report.json](reports/smoke-100/run-report.json), envelopes in `reports/smoke-100/envelopes.jsonl.gz` |
| One command to run | See below |
| Models / APIs / licences | See below |
| Expected cost per official run | $0 without keys. With the optional connectors switched on: capped at $8 per run by default |
| 1,000 company profiles | `reports/batch-1000/envelopes-1000.jsonl.gz`, report in [reports/batch-1000/run-report.json](reports/batch-1000/run-report.json) |
| Browsable dashboard | `docs/index.html` (the 100 smoke-test profiles: overview charts, sortable register, evidence drawer, compare; works from disk or GitHub Pages) |

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
| Fagfolkguiden.no | Public trade-directory pages, robots.txt allows `/bedrift/`; one aggregate rating per company |
| Dependencies | Python standard library only |

## Optional keyed connectors (off by default)

Two connectors are in the code at this commit and switch on only when the evaluator sets a key. They exist because website discovery is what limits recall, and the challenge page says a key can be supplied on request.

| Variable | Service | Adds | Declared cost |
|---|---|---|---|
| `SIGNALPOST_SEARCH_API_KEY` | Brave Search API | Candidate websites for companies with no verified site. Candidates only; the identity gate is unchanged | $0.005 per company searched |
| `SIGNALPOST_PLACES_API_KEY` | Google Places API (New) | Google rating, review count, Maps link for an identity-matched place | $0.035 per company |
| `SIGNALPOST_API_BUDGET_USD` | | Spend cap per run, default 8 | |

They are covered by tests against saved sample responses. **They have not been run against the live services**, because no key was available, so the first keyed run is also their first live test. All numbers below are without keys.

## Measured runs

Both batches were drawn from the public 411,160-company universe with a fixed seed (`python -m signalpost select`, seeds 20261013 and 424242). Run from a home connection in India, about 0.9 s per request to the Norwegian register.

### 100-company smoke test (`smoke-100-run1`)

| Measure | Value |
|---|---|
| Companies in / envelopes out | 100 / 100 |
| Terminal states | available 100 |
| Runtime | 318 s (210 s of it is the rate-limited filing-years endpoint, used only for small batches; about 70 s is the throttled job board) |
| Outbound requests | 869 (8.7 per company) |
| External API cost | $0 |
| Published facts | 8,037 (80 per company), 8,269 evidence records |
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
| Hiring | 2 | blocked 86, not_available 12 |
| Customer reviews | 3 | not_available 97 |
| Dated public activity | 100 | none |

### Refresh rerun on the same 100 (`smoke-100-run2`)

100 of 100 compared with the previous snapshot, 0 changes, 0 carried-forward claims, same fact count (8,037). Report: [reports/smoke-100/run-report-refresh.json](reports/smoke-100/run-report-refresh.json).

### 1,000-company run (`batch-1000-run1`)

| Measure | Value |
|---|---|
| Companies in / envelopes out | 1,000 / 1,000 |
| Terminal states | available 1,000 |
| Runtime | 628 s (10.5 minutes) |
| Outbound requests | 7,824 (7.8 per company) |
| External API cost | $0 |
| Published facts | 78,594 (79 per company), 80,759 evidence records |
| Website gate | verified 131, ambiguous 148, blocked 16, no usable candidate 705 |
| Validation | all checks passed |

| Section | Companies with facts | Other states |
|---|---|---|
| Legal identity | 1,000 | none |
| Annual accounts | 998 | failed 2 (register returned an error) |
| Leadership | 1,000 | none |
| Registered workplaces | 776 | not_available 224 |
| Group structure | 1,000 | none |
| Official website (verified or labelled group site) | 141 | not_available 705, ambiguous 138, blocked 16 |
| Company-owned profiles | 80 | not_available 920 |
| Contact details | 81 | not_available 919 |
| Hiring | 25 | blocked 869, not_available 106 |
| Customer reviews | 27 | not_available 973 |
| Dated public activity | 1,000 | none |

### Read these numbers with care

- **Hiring is mostly `blocked`.** NAV's job board answered a handful of searches in the smoke run and then HTTP 429; in the other runs it refused from the first request. The agent stopped asking after three refusals, as designed. In a separate 3-company run from the same machine the board did answer and two Equinor adverts were verified by organisation number, so the path works when the board is not rate-limiting.
- **"Dated public activity" is 100% because of the register's update feed**, which exists for every company. Dated news from company sites is far rarer.
- **Customer reviews cover trades only** (27 of 1,000). The source is a third-party directory that Builderr's reference kit marks "rights review required".
- **Wrong-company check**: every name-based website match in the 100-company sample and 45 in an earlier 1,000-company run were read by hand by the author; none looked wrong. There is no independent label set.
- All three runs were made with the committed code.

## Budget fit

| Limit | Assumed | Measured |
|---|---|---|
| Time | 45 minutes | 5.3 minutes for 100 companies, 10.5 minutes for 1,000 |
| Requests | 2,000 per 100 companies | 869 per 100 companies |
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
  NAV arbeidsplassen.no public job board (throttled), Fagfolkguiden.no public directory pages (customer rating).
Expected cost per official run: $0 external API cost without keys (capped at $8 if you switch on the optional connectors). Measured 869 requests and 5.3 minutes per 100 companies;
  7,824 requests and 10.5 minutes per 1,000 companies.
Contact for results: <your email>

Request: could you supply a search API key for official runs? The agent already contains a Brave Search connector
(and a Google Places connector) that switch on when SIGNALPOST_SEARCH_API_KEY / SIGNALPOST_PLACES_API_KEY are set.
Search results are used only as candidate websites; every site must still pass the organisation-number identity
gate before anything is published. Without a key the agent runs exactly as in the attached smoke test.
```
