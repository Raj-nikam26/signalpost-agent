# Known limitations

## Coverage

- **Most small companies have no verifiable website.** In a random 100-company sample only about one in ten reaches a verified official website. The public universe is dominated by holding and property companies with no site. Without a search provider, a site is found only when the register lists it, Wikidata lists it, the email domain relates to the name, or `<legalname>.no` exists.
- **No web search.** A keyed search API would raise website discovery, but it would tie the run to a personal credential. It is the first thing to add if the organiser supplies a key.
- **Static HTML only.** Sites that render everything with JavaScript give too little text to verify and are reported as `ambiguous`.
- **Job postings are under-counted.** Postings are published from the verified site only when they are structured data (`JobPosting`) or links to a known applicant-tracking host. Free-text vacancy pages are recorded as a careers page without individual postings.
- **NAV's job board is rate-limited to a few requests a minute.** The agent searches it slowly, largest employers first, and stops after three consecutive HTTP 429 answers. Companies not reached are marked `blocked` with that reason. During development the board refused every request for more than 15 minutes after a burst of about 200 requests.
- **Dated news needs a feed, `<time>` elements or structured data.** Sites that show news without machine-readable dates yield no news items.
- **Annual accounts cover what the register's API returns**: normally the latest one to three fiscal years. Older years are listed with their official PDF link (small batches only), not parsed. Banks and insurers can return an error from the normalised endpoint and are then reported as `failed` for accounts.
- **Customer ratings cover trades only.** The rating comes from the Fagfolkguiden directory, which lists craft and trade businesses; about 3% of a random batch has one. It is a third-party display of an aggregate rating, and Builderr's reference kit marks the source as needing a rights review.
- **No ownership, sentiment or traffic.** No permitted, exact-company free source was found, so the profile states that these are unknown.

## Identity

- A registry-reported site that shows the full legal name is accepted without the organisation number. A group that shares one site across legal entities with near-identical names could pass this rule. Confidence for this path is 0.90 to 0.95, not 1.0.
- A site that prints a different organisation number is labelled related even when it is in fact the company's own site with a stale footer.
- Single-word legal names shorter than four letters are never matched by name alone.
- Wikidata is community-edited. Its website link is used only as a candidate and still has to pass the gate.

## Operations

- DNS is checked before each request, and the request then resolves again; a hostile DNS server could answer differently the second time. The agent only ever sends GET requests without credentials.
- `operations.requests` per company excludes the shared Wikidata batch query; the run report counts every request.
- The exact official time and request budget is not published. The defaults assume 45 minutes and 20 requests per company and are flags.
- The state directory grows by one compressed snapshot per run and is never pruned.

## Text

- The summary is English; register texts (industry names, statutory purpose) stay in Norwegian as filed.
