# Identity resolution

The organisation number is the only key. A domain, brand or handle is a candidate until evidence ties it to the exact legal entity. The gate is `assess_identity` in [signalpost/sources/website.py](signalpost/sources/website.py).

## Candidates

| Origin | Where it comes from | Trust |
|---|---|---|
| `registry` | `hjemmeside` the company reported to Enhetsregisteret | Reported by the company itself |
| `wikidata` | Official-website property of the Wikidata item whose P2333 equals the organisation number | Keyed by number, community-edited |
| `registry_email_domain` | Domain of the email the company reported, only if the domain relates to the legal name and is not a mail provider | Weak |
| `name_guess` | `<legalname>.no`, only if it resolves in DNS and no other candidate exists | None on its own |

At most two candidates are crawled per company.

## Decision table

The gate reads the homepage and up to two contact/about pages.

| Evidence on the site | `registry` / `wikidata` | `registry_email_domain` | `name_guess` |
|---|---|---|---|
| This company's organisation number | **exact** (1.0) | **exact** (0.98) | **exact** (0.98), unless the site lists many other numbers and not the name |
| A different organisation number, and not the full legal name with its legal form | **related** | **related** | **related** |
| Full legal name in title, site name, heading or structured data | **exact** (0.95) | exact only with a corroborator | exact only with a corroborator |
| Full legal name as a phrase in the page text | **exact** (0.92) | exact only with a corroborator | exact only with a corroborator |
| Domain equals the legal name | **exact** (0.90) | exact (0.90) | ambiguous |
| Part of the name only | ambiguous | ambiguous | ambiguous |
| Parked, for-sale or hosting placeholder | rejected | rejected | rejected |
| Too little readable text | ambiguous | ambiguous | ambiguous |

Corroborators for untrusted origins: the registry phone number on the site, the registered street and postal code on the site, the registry email address on the site, or the registry email being on that domain.

"Full legal name" keeps words such as `holding`, `invest` and `gruppen`. `Nordlys Bakeri Holding AS` does not match a site titled "Nordlys Bakeri", because that site belongs to the operating company.

Organisation numbers are recognised only when the modulus-11 check digit is valid, so phone numbers and prices are not mistaken for them.

## What each outcome publishes

| Outcome | Published |
|---|---|
| `exact` | `official_website` with the proof span, then everything extracted from the site |
| `related` (registry or Wikidata origin) | `related_group_website`, labelled, with the organisation numbers the site shows. Nothing else |
| `ambiguous`, `rejected` | `official_website` with state `ambiguous` or `not_available` and the reason. Nothing else |

The website the company reported to the register is always published separately as `registered_website`, sourced to the register and labelled as reported, not verified.

## Second-level checks

- **Social profiles**: the link must be on the verified homepage, must not be a share or login link, and its handle must match the legal name, the brand or the domain, or be declared in the site's `sameAs` structured data. Handles of site builders (Wix, Squarespace and similar) are never published.
- **Job adverts on NAV**: the advert's own page must state an organisation number, and every number it states must be this company or one of its registered sub-units.
- **Annual accounts**: a record whose `virksomhet.organisasjonsnummer` differs from the requested number is dropped.
- **Wikidata**: if two items carry the same organisation number the match is `ambiguous` and nothing is published.

## Tests

`tests/test_agent.py`, class `IdentityGate`, covers: number on site, registry site with full name, site naming another entity, holding company against the operating company's site, guessed domain with name only, guessed domain with name and registry phone, parked domain, unrelated registry site, and that nothing is published from an unverified site.
