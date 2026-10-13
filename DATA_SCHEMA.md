# Data schema

One JSON object per input organisation number, one per line, in input order. The envelope is a superset of the starter kit's `OUTPUT_CONTRACT.md` (`organisation_number`, `run`, `claims`, `evidence`, `changes`, `errors`, `operations`) and of the reference runner's envelope (`run_id`, `state`, `started_at`, `completed_at`, `modules`, `profile`).

## Envelope

| Field | Meaning |
|---|---|
| `schema_version` | `signalpost-envelope/1.0` |
| `organisation_number` | The input, digits only |
| `status` | Terminal state of the company: one of the six states below |
| `state` | Same outcome in the reference runner's vocabulary (`complete`, `not_found`, `source_error`, …) |
| `run` | `run_id`, `started_at`, `completed_at`, `terminal_status`, `agent` |
| `identity` | Quick-read block: legal name, brand, form, status, municipality, industry, employees, verified website |
| `sections` | State, fact count and explanation for each of the ten sections |
| `summary` | Headline, cited sentences, signals, what changed, unknowns |
| `claims` | The facts |
| `evidence` | The proof behind the facts |
| `changes` | Differences from the previous snapshot |
| `change_history` | Changes from earlier runs (last 100) |
| `refresh` | `first_run`, `compared_with_run`, `previous_completed_at`, `changes`, `carried_forward`, `snapshot_sha256` |
| `modules` | Per-source state, retry count, final timestamp, note |
| `errors` | `module`, `code`, `message`, `source_url`, `at` |
| `operations` | `requests`, `runtime_ms`, `third_party_cost_usd` |
| `profile` | The same data in the reference runner's `profile.evidence.<module>` shape |

## States

| State | Meaning |
|---|---|
| `available` | Found, with evidence |
| `not_available` | The source was checked and has nothing |
| `blocked` | The source refused: robots.txt, HTTP 401/403/429, or a rate limit that left no time |
| `not_applicable` | The question does not apply (invalid input, entity not in a group) |
| `ambiguous` | A candidate exists but could not be tied to the exact company |
| `failed` | The source or the run broke |

## Claim

```json
{
  "id": "cl-7c1f0a2b9d3e",
  "key": "accounts.SELSKAP.2025.revenue",
  "field": "revenue",
  "family": "annual_accounts",
  "label": "Total operating revenue 2025",
  "value": 10023456,
  "unit": "NOK",
  "availability": "available",
  "confidence": 1.0,
  "period": {"from": "2025-01-01", "to": "2025-12-31", "fiscal_year": "2025", "accounts_type": "SELSKAP", "scope": "company accounts"},
  "effective_date": "2025-12-31",
  "evidence_ids": ["ev-3b9a61c20f44"],
  "first_observed_at": "2026-10-12T19:20:11Z",
  "last_observed_at": "2026-10-13T06:00:09Z"
}
```

- `key` is stable across runs and identifies the fact, for example `leadership.DAGL.kari-nordmann`, `workplace.973861883`, `profile.linkedin`, `hiring.job.nav.<uuid>`.
- `family` is one of `legal_identity`, `annual_accounts`, `leadership`, `workplaces`, `group_structure`, `official_website`, `company_profiles`, `contact`, `hiring`, `public_activity`.
- When `availability` is not `available`, `value` is `null`, `confidence` is absent and `explanation` says why.
- `carried_forward: true` marks a value kept from an earlier run, with `carried_reason`.
- `explanation` states how the fact was established or what it does not mean.

## Evidence

```json
{
  "id": "ev-3b9a61c20f44",
  "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/810034882",
  "source_class": "official_annual_accounts",
  "source_name": "Brønnøysund Regnskapsregisteret",
  "licence": "NLOD 2.0 (Norwegian Licence for Open Government Data)",
  "http_status": 200,
  "retrieved_at": "2026-10-13T06:00:04Z",
  "effective_at": "2025-12-31",
  "content_sha256": "9f2c…",
  "claim_span": "regnskap[id=5667199, SELSKAP, 2025-01-01..2025-12-31].resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter: 10023456.0",
  "extraction_method": "json_path"
}
```

`source_class` is one of `official_registry`, `official_annual_accounts`, `company_owned`, `public_job_board`, `open_knowledge_base`. `redirect_chain` and `requested_url` are present when the final URL differs from the requested one. Evidence and claim ids are hashes of their content, so a rerun produces the same ids.

## Change

```json
{
  "id": "ch-0d2e…",
  "change_type": "new_role",
  "kind": "added",
  "field": "role_holder",
  "key": "leadership.DAGL.ola-hansen",
  "group": "roles.roles",
  "materiality": "high",
  "summary": "New managing director / ceo: Ola Hansen",
  "old_value": null,
  "new_value": {"name": "Ola Hansen", "role_code": "DAGL"},
  "previous_observed_at": null,
  "detected_at": "2026-10-13T06:00:09Z",
  "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/923609016/roller",
  "retrieved_at": "2026-10-13T06:00:05Z",
  "effective_at": "2026-10-01",
  "old_content_sha256": null,
  "new_content_sha256": "41aa…",
  "old_evidence": [],
  "new_evidence_ids": ["ev-…"]
}
```

`kind` is `added`, `changed` or `removed`. Typed `change_type` values include `new_role`, `ended_role`, `new_location`, `closed_location`, `new_job`, `closed_job`, `new_filing`, `new_activity`, `changed_description`, `renamed`, `status_change`, `changed_employee_count`, `new_website`, `new_profile`, `new_subsidiary`; other fields use `changed_<field>`, `added_<field>`, `removed_<field>`. `old_evidence` holds the full earlier evidence records.

## Summary

`summary.sentences[]` each have `topic`, `text`, `claim_ids`, `evidence_ids`, and `derived: true` when the sentence computes something from two filed figures. `summary.signals[]` flags adverse status, negative equity, a loss, unaudited accounts and verified hiring. `summary.unknowns[]` lists every section that is not `available`, with its state and reason.

## Run report

`out/run-report.json`: counts per state, section availability, published claims per family and per field, website gate outcomes, refresh statistics, requests by source class, p50/p95 latency, runtime, cost, and `validation.checks`:

| Check | Meaning |
|---|---|
| `exact_expected_count`, `one_envelope_per_input_in_order`, `unique_organisation_numbers`, `zero_silent_drops` | One envelope per input |
| `all_states_terminal`, `all_claim_states_valid` | Only the six states are used |
| `every_published_claim_has_source_and_retrieval_time` | No unsupported claim |
| `every_financial_value_traceable_to_its_source_span` | Each published figure appears in its evidence span |
| `every_financial_claim_has_reporting_period` | Each figure has its fiscal period |
