# Evaluation

The hidden daily batch is the exam. These are the local checks that gate every change, in the order they are applied.

## 1. Offline regression tests

```bash
python -m unittest discover -s tests
```

46 tests, no network, a few seconds. Each one pins a failure that would cost the run:

| Risk | Tests |
|---|---|
| Wrong-company publication | Site naming another entity is `related`; holding company does not inherit the operating company's site; guessed domain needs proof; parked domain rejected; nothing published from an unverified site; registry reply or accounts record for another number is dropped |
| Fabricated financial value | Absent line item produces no claim; reported zero stays zero; no accounts gives `not_available` with no numbers; unknown employee count is not zero |
| Missing envelopes | Unknown and invalid numbers get an envelope; a run with zero time and zero requests still returns one row per input, in order |
| False or lost changes | Same snapshot gives zero changes and no duplicate keys; changes are typed and keep earlier evidence; a new filing keeps older years; a failed source keeps the last value and reports no change; a total failure never erases the previous profile |
| Unsafe requests | Loopback, private, link-local, metadata, non-HTTP, credentialed and odd-port URLs are refused and never fetched |
| External sources | A rating is published only when the directory page states this organisation number; domain guesses are candidates only; the observation block mirrors published claims only |
| Keyed connectors | Off without a key; search results are candidates only and directories are dropped; the key travels in a header and never appears in output; a place is published only on an identity match; paid calls stop at the declared budget |
| Personal data | Birth dates never appear; only generic mailboxes are published |
| Input handling | JSON, JSONL, CSV, text and literal lists give the same batch; duplicates collapse |
| Site safety | Company text cannot break out of the page's script element |

## 2. Refresh replay

```bash
python -m signalpost replay --manifest tests/fixtures/refresh-snapshots.json --output out/refresh-demo.json
```

Saved old and new source responses go through the production pipeline. Result on the bundled fixture: 3 expected change groups, 3 found, precision 1.0, recall 1.0, evidence complete, third run change-free. Builderr's own fixture from the reference kit also passes (2 of 2).

## 3. Live smoke test

The submission's 100-company run and a 1,000-company run, both drawn from the public universe with a fixed seed. Numbers are in [SUBMISSION.md](SUBMISSION.md); the full reports are in `reports/`.

The run report's `validation.checks` must all be true: one envelope per input in order, only the six states, every published claim has a source and retrieval time, every financial figure appears in its evidence span and has a reporting period.

## 4. Manual audit of website decisions

After each smoke run every published website, and every fact taken from one, is listed with the gate's reason and read by hand. This audit drove three rule changes during development:

| Found in the first 100-company trial | Fix |
|---|---|
| Site-builder footer links (Wix's own Facebook, X and LinkedIn pages) published as the company's profiles | Social links now need a handle that matches the company, or a `sameAs` declaration; site-builder handles are refused outright |
| Share buttons (`linkedin.com/shareArticle`, `pinterest.com/pin`) published as profiles | Share, login and policy paths are excluded |
| An employee's university mail domain used as a website candidate | An email domain is a candidate only when it relates to the legal name |

After the fixes, the reviewer found no wrong-company publication among the verified sites in the 100-company sample. That is a hand check by the author of one sample, not an independent label set.

## What is not measured

- Recall against the organiser's pooled reference: that set is hidden.
- Exact-company precision on a large hand-labelled set: only the 100-company audit above exists.
- Behaviour under the organiser's real time and request budget: the exact limits are not published, so they are flags.

## Promotion rule used during development

A change was kept only if the offline tests stayed green, the replay stayed at precision and recall 1.0, the smoke run still validated, and the manual website audit showed no new wrong-company publication.
