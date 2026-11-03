# Hand-check of company-site facts

Requested in the organiser's review: check a small set by hand, outside the agent, and report how often each source type is right.

**Sample.** 40 companies drawn at random (seed 7) from the 131 companies with a verified website in the 1,000-company run (`data/audit-40.txt`). Each published fact was read against the page it cites. The checker is the author, so this is a hand review, not an independent label set.

**Rule used.** A fact is wrong if it belongs to another legal entity, or if it is not the kind of fact it claims to be (a project page published as news, a slogan published as a brand name, a booking page published as a careers page).

## Before the fixes (commit 7965165)

| Source type | Published | Wrong | Precision | What was wrong |
|---|---:|---:|---:|---|
| Verified website | 40 | 0 | 100% | |
| Social profile | 48 | 0 | 100% | One Facebook page is named after the owner as well as the company; counted correct |
| Company news | 19 | 7 | 63% | 4 project pages titled with street addresses, 2 course attachment pages, 1 template page ("front about 2") |
| Careers page | 8 | 3 | 63% | "stilling" matched inside "forestilling", "bestilling" and "avbestillingsvilkår" |
| Public brand | 9 | 3 | 67% | Slogans taken from the site name ("Dine verdiar er vårt fag") |
| Company description | 31 | 2 | 94% | A keyword list and a shop banner ("Gratis frakt på ordre over kr 1.000,-") |

## After the fixes (this commit)

| Source type | Published | Wrong | Precision |
|---|---:|---:|---:|
| Verified website | 40 | 0 | 100% |
| Social profile | 48 | 0 | 100% |
| Company news | 9 | 0 | 100% |
| Careers page | 5 | 0 | 100% |
| Public brand | 6 | 0 | 100% |
| Company description | 29 | 0 | 100% |

Job postings: none of the 40 sites published a posting in this sample, so job precision is not measured here. The review's job problems (a group careers page, and pages saying there are no openings) are covered by regression tests instead (`tests/test_agent.py`, class `ReviewFixes`).

## What changed in the agent

| Problem from the review | Fix |
|---|---|
| News items were ordinary pages (sale, project) | Only feed entries and pages marked up as articles count, and the headline must read as a headline: no street addresses, product or course codes, project, shop or attachment paths, and nothing older than three years (this also drops two correct but old posts in the sample) |
| Job and social links from a parent group's page | A site or careers page that names other legal entities (other organisation numbers, or several other "… AS" companies) is treated as shared: no jobs or news are taken from it, and a social profile counts only if its handle carries this company's own legal name |
| Pages saying there are no openings | A careers page that says "ingen ledige stillinger" (or similar) publishes no job and no careers page; the hiring state explains why |
| A product page picked as the careers page (found in this audit) | Shop, product and category paths are never chosen as careers, contact or news pages |
