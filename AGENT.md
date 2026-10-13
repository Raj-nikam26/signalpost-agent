# Research policy

## Order of work for one organisation number

1. **Validate the input.** A value that is not a checksum-valid 9-digit organisation number gets a `not_applicable` envelope; nothing is looked up.
2. **Anchor identity.** Fetch the entity from Enhetsregisteret by number. If the register answers 404 the envelope is `not_available`. If the reply carries a different number it is rejected and the envelope is `failed`.
3. **Collect official facts** by number: annual accounts, roles, sub-units, group structure, update feed.
4. **Resolve the website.** Build candidates, crawl the homepage and up to two contact/about pages, and run the identity gate ([IDENTITY_RESOLUTION.md](IDENTITY_RESOLUTION.md)).
5. **Only if the gate says `exact`:** fetch the careers page and the news page or feed, then publish description, brand, social profiles, contact mailbox, job postings and dated news.
6. **Search NAV's job board** by legal name. Search hits are candidates only. An advert is published when its own page states this company's organisation number or one of its registered sub-units.
7. **Diff against the previous snapshot**, set section states, and write the summary.

## Publication rules

- A claim with a value must have at least one evidence record. The code raises if it does not.
- A claim without a value has one of five states and an explanation: `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`.
- Financial figures are copied from the register's JSON. An absent line item produces no claim. A reported `0` stays `0`.
- Search results and Wikidata website links nominate candidates. They are never evidence for a fact.
- Derived figures (equity ratio, year-on-year revenue change) appear only in summary sentences, marked `derived`, citing both inputs.
- Personal data is limited to public role holders in their company role. Birth dates are discarded. Only generic mailboxes (`post@`, `info@` and similar) are published, never a named person's address.

## When the agent abstains

| Situation | Result |
|---|---|
| Candidate site shows neither the organisation number nor the full legal name | `official_website`: `ambiguous`; nothing from the site is published |
| Site prints a different organisation number | Labelled `related_group_website`; nothing on it is attributed to the company |
| Legal name contains `holding`, `invest`, `gruppen` and the site only shows the name without it | `ambiguous`: the site belongs to the operating company |
| Parked or for-sale domain | Rejected |
| Social link whose handle matches neither the legal name, the brand nor the domain | Withheld (typical for site-builder and agency links) |
| Job advert whose page states another organisation number, or none | Not published |
| Email domain unrelated to the legal name (accountant, employer, ISP) | Not used as a candidate |
| robots.txt disallows the path | `blocked`; the page is not fetched |

## No language model

Extraction, identity decisions and the summary are deterministic. The same source bytes always give the same profile, the declared cost is $0, and no credential is needed to reproduce a run.
