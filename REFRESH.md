# Refresh

A refresh is the same command run again. Each company is compared with its previous envelope, taken from `state/latest.jsonl` or from the file passed with `--previous`.

```bash
python -m signalpost run --organisations batch.jsonl --output out/envelopes.jsonl --report out/run-report.json
```

## How the diff works

Every fact has a stable `key`, for example `identity.employees`, `accounts.SELSKAP.2025.revenue`, `leadership.DAGL.kari-nordmann`, `workplace.973861883`, `hiring.job.nav.<uuid>`.

| Previous run | This run | Result |
|---|---|---|
| value A | value A | No change. `first_observed_at` is kept, `last_observed_at` moves forward |
| value A | value B | `changed`, with both values and both evidence records |
| absent | value | `added` |
| value | absent, source answered normally | `removed` for identity, leadership, workplaces, group, website, profiles, contact and job postings |
| value | absent, source failed, was blocked or unreachable | No change. The value is kept with `carried_forward: true` and the failure stays in `modules` and `errors` |
| older accounts year or news item | no longer inside the fetched window | Kept as history with `carried_forward: true`; not reported as removed |

Values are compared as canonical JSON, so key order and retrieval time never create a change.

## Guarantees

- **Idempotent.** Running twice on the same source data gives zero changes, no duplicate claim keys, and the same `refresh.snapshot_sha256`.
- **Earlier evidence is preserved.** Each change carries `old_evidence` (the full earlier records) and `old_content_sha256`. Every run is also stored unmodified in `state/snapshots/<run_id>.jsonl.gz`.
- **A failed run never overwrites a good profile.** A `failed` envelope is not written over the last good one in `state/latest.jsonl`, and it carries the previous claims forward.
- **History accumulates.** `change_history` keeps the changes of earlier runs (last 100).

## Change types and materiality

| Materiality | Examples |
|---|---|
| high | `renamed`, `status_change`, new or ended managing director, chair or proprietor, `new_filing`, any changed financial figure, `new_website` |
| medium | address, employee count, board members, workplaces, subsidiaries, social profiles |
| low | `new_job`, `closed_job`, `new_activity`, website title |

## Volatility

| Source | How often it really changes |
|---|---|
| Annual accounts | Once a year per company |
| Roles, addresses, capital | A few times a year |
| Employee count | Monthly |
| Job postings, company news | Weekly |

Every source is cheap enough to re-check on each daily run, so the agent does not skip sources by age.

## Replay test

```bash
python -m signalpost replay --manifest tests/fixtures/refresh-snapshots.json --output out/refresh-demo.json
```

Saved "old" and "new" source responses go through the production pipeline. The report gives precision and recall against the expected changes, whether each change has complete evidence, and whether a third run on the "new" data is free of changes. The manifest format is the one used by Builderr's reference kit, so their fixture runs unmodified.
