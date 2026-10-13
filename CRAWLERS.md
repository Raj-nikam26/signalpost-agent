# Sources, rights and budgets

## Sources

| Source | Endpoint | Used for | Licence / access basis | Requests |
|---|---|---|---|---|
| Enhetsregisteret entity | `data.brreg.no/enhetsregisteret/api/enheter/{org}` | Identity, status, industry, addresses, capital, employees, reported website | NLOD 2.0, official open API | 1 per company |
| Regnskapsregisteret | `data.brreg.no/regnskapsregisteret/regnskap/{org}` | Annual accounts, up to three fiscal years | NLOD 2.0 | 1 per company |
| Enhetsregisteret roles | `…/enheter/{org}/roller` | Leadership, board, auditor | NLOD 2.0 | 1 per company |
| Enhetsregisteret sub-units | `…/underenheter?overordnetEnhet={org}` | Registered workplaces | NLOD 2.0 | 1 per company |
| Enhetsregisteret group structure | `…/konsernstruktur/{org}` | Parent and subsidiaries | NLOD 2.0 | 1, only when the entity is flagged as part of a group |
| Enhetsregisteret update feed | `…/oppdateringer/enheter?organisasjonsnummer={org}` | Dates of registry changes in the last 12 months | NLOD 2.0 | 1 per company |
| Annual-account copies | `…/regnskap/aarsregnskap/kopi/{org}/aar` | Every filed year with the official PDF link | NLOD 2.0; about 30 requests a minute | 1 per company, only when the batch is small enough (`--history`) |
| Wikidata Query Service | `query.wikidata.org/sparql` | Wikidata and Wikipedia links, website candidate | CC0 | 1 per 60 companies |
| Company website | Homepage, up to two contact/about pages, careers page, news page or feed; up to three candidate sites | Verified website, brand, description, social profile links, mailbox, jobs, dated news | Public company-owned pages; robots.txt honoured; facts only | 0 to 7 per company |
| Fagfolkguiden.no | `fagfolkguiden.no/bedrift/{name}-{org}` | Aggregate customer rating and review count shown on the company's directory page | Public page; robots.txt allows `/bedrift/`; one aggregate figure, no review text. Builderr's reference kit marks this source "rights review required", and the observation block carries that flag | 1 per company |
| NAV arbeidsplassen.no | Public search and advert pages | Open positions | Public government job board; robots.txt allows all paths; heavily rate-limited | 0 to 4 per company, throttled |

No source above needs an API key, an account or a paid plan.

### Optional keyed sources (off by default)

| Source | Endpoint | Used for | Access basis | Requests |
|---|---|---|---|---|
| Brave Search API | `api.search.brave.com/res/v1/web/search` | Candidate websites only, for companies whose site could not be verified from the register | Official API with the evaluator's key. Titles, snippets and ranks are not kept or published; only independently fetched pages become evidence | 1 per company without a verified site |
| Google Places API (New) | `places.googleapis.com/v1/places:searchText` | Rating, review count, Maps link | Official API with the evaluator's key; Google Maps Platform terms | 1 per company |

Directories, social networks, news sites and registers are removed from search results before any page is fetched. A search candidate gets no trust from the search: it needs the organisation number on the site, or the full legal name plus a registry corroborator, exactly like a guessed domain.

## Sources deliberately not used

| Source | Reason |
|---|---|
| LinkedIn, Facebook, Instagram, X, YouTube pages | Their terms restrict automated collection. Only the profile URL linked from the verified company site is recorded; the platform is never requested |
| `w2.brreg.no/kunngjoring` (official announcements) | Its robots.txt disallows `/kunngjoring/` |
| Proff, Purehelp, 1881 and similar directories | Commercial terms; they also repackage the same register data |
| Glassdoor, Indeed, Google Maps reviews | No permitted free access path |
| Web search engines | Result-page scraping is not permitted, and a keyed search API would tie the run to a personal credential |
| Sentiment models | No evaluated, exact-company Norwegian corpus; company-owned text is promotional by nature |

## Fallback order for the website

1. Website reported to the register
2. Website on the Wikidata item that carries the organisation number
3. Domain of the reported email address, if it relates to the legal name
4. `<legalname>.no` (also with `oe`/`aa` spellings and a hyphenated form), after a DNS check. `.com` and shortened names were tried on 1,000 companies and dropped: they found namesakes, never the company

Static HTML only. A page with too little readable text is reported as `ambiguous`; no browser is launched.

## Budgets and politeness

| Control | Value |
|---|---|
| Global request cap | `--max-requests`, default 20 per company (minimum 2,000) |
| Reserved for the register | 4 requests per company, held back until that company starts |
| Time budget | `--time-budget-min`, default 40. Optional work stops at 80%; companies not started by 95% get a `failed` envelope |
| Concurrency | 16 companies in parallel; at most 12 concurrent requests to `data.brreg.no`, 2 to any other host |
| Retries | Register: up to 3 attempts with backoff on 429/5xx/timeouts, honouring `Retry-After`. Websites: 1 attempt |
| Timeouts | 10 s for websites, 20 s for the register |
| Response size | 1.5 MB cap per page |
| NAV job board | One request every 7 s, largest employers first, at most 25% of the time budget, stops after three consecutive HTTP 429 |
| User agent | `SignalpostAgent/<version> (+contact URL)`; set `SIGNALPOST_CONTACT` to change the contact |

## Outbound URL safety

Every URL, including every redirect hop, passes `check_url` in [signalpost/http.py](signalpost/http.py) before a request is made:

- scheme must be `http` or `https`; ports other than 80 and 443 are refused
- URLs with embedded credentials are refused
- the host must resolve only to public addresses: loopback, private, link-local, multicast and cloud-metadata ranges are refused
- at most five redirects
- robots.txt is fetched once per site and obeyed for company sites; a 401/403 on robots.txt is treated as "disallow all"

Fetched HTML is parsed as data only. Nothing from a page is executed, and no instruction found in page text is followed. The profile site escapes all source text and only renders `http`/`https` links.

## Secrets

The agent needs none. Two optional keys (`SIGNALPOST_SEARCH_API_KEY`, `SIGNALPOST_PLACES_API_KEY`) are read from the environment, sent only as request headers to their own API host, never forwarded on a redirect, and never written to any output. Three further optional environment variables exist: `SIGNALPOST_CONTACT` and `SIGNALPOST_USER_AGENT` (user-agent text), and `SIGNALPOST_MAX_REQUESTS` (request cap). Nothing is written outside the output, report, state and site paths given on the command line.
