"""Offline regression tests. Run with: python -m unittest discover -s tests -v"""
from __future__ import annotations

import argparse
import copy
import json
import tempfile
import unittest
from pathlib import Path

from signalpost.batch import read_inputs, run_batch, validate
from signalpost.claims import Dossier
from signalpost.htmlx import parse_html
from signalpost.http import Budget, Fetcher, Response, SnapshotFetcher, check_url, registered_domain
from signalpost.pipeline import failed_envelope, research_company
from signalpost.refresh import apply_refresh, snapshot_hash
from signalpost.replay import run_replay
from signalpost.site import build_site
from signalpost.sources import brreg, website
from signalpost.sources.website import Candidate, Crawl, assess_identity, parse_feed, social_links
from signalpost.util import find_org_numbers, parse_date, valid_org_number

ORG = "923609016"
OTHER = "914778271"
API = "https://data.brreg.no/enhetsregisteret/api"
OFFLINE = {"website": False, "nav_jobs": False, "registry_updates": False, "account_history": False, "reviews": False, "search": False, "places": False}


def entity(**overrides):
    base = {
        "organisasjonsnummer": ORG, "navn": "Nordlys Bakeri AS", "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
        "antallAnsatte": 12, "harRegistrertAntallAnsatte": True, "hjemmeside": "www.nordlysbakeri.no", "konkurs": False, "underAvvikling": False,
        "naeringskode1": {"kode": "10.710", "beskrivelse": "Produksjon av brød"}, "stiftelsesdato": "2010-03-01",
        "forretningsadresse": {"adresse": ["Storgata 1"], "postnummer": "9008", "poststed": "TROMSØ", "kommune": "TROMSØ", "landkode": "NO", "land": "Norge"},
        "telefon": "77 60 00 00", "sisteInnsendteAarsregnskap": "2025", "erIKonsern": False, "registrertIMvaregisteret": True,
    }
    base.update(overrides)
    return base


def accounts(revenue=1000, year="2025", result=50, **extra):
    record = {"id": 7, "regnskapstype": "SELSKAP", "valuta": "NOK", "virksomhet": {"organisasjonsnummer": ORG},
              "regnskapsperiode": {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"},
              "resultatregnskapResultat": {"aarsresultat": result, "driftsresultat": {"driftsresultat": 80, "driftsinntekter": {"sumDriftsinntekter": revenue}}},
              "eiendeler": {"sumEiendeler": 900}, "egenkapitalGjeld": {"egenkapital": {"sumEgenkapital": 300}, "gjeldOversikt": {"sumGjeld": 600}}}
    record.update(extra)
    return [record]


def roles(*people):
    return {"rollegrupper": [{"type": {"kode": code}, "sistEndret": "2024-01-01",
                              "roller": [{"type": {"kode": code, "beskrivelse": code}, "person": {"fodselsdato": "1970-01-01", "navn": {"fornavn": first, "etternavn": last}}}]}
                             for code, first, last in people]}


def snapshot(*, ent=None, acc=None, rol=None, sub=None, at="2026-08-01T00:00:00Z"):
    responses = {f"{API}/enheter/{ORG}": {"status": 200, "body": ent or entity()},
                 f"{API}/enheter/{ORG}/roller": {"status": 200, "body": rol or roles(("DAGL", "Kari", "Nordmann"))},
                 f"{API}/underenheter?overordnetEnhet={ORG}&size=100": {"status": 200, "body": sub or {"_embedded": {"underenheter": []}, "page": {"totalElements": 0}}}}
    if acc is not None:
        responses[f"https://data.brreg.no/regnskapsregisteret/regnskap/{ORG}"] = {"status": 200, "body": acc}
    return {"retrieved_at": at, "responses": responses}


def run(snap, previous=None, run_id="t"):
    return research_company(ORG, SnapshotFetcher(snap), run_id=run_id, previous=previous, options=OFFLINE)


def claims_of(envelope):
    return {claim["key"]: claim for claim in envelope["claims"]}


def page_response(url, html):
    return Response(url=url, final_url=url, status=200, raw=html.encode("utf-8"), headers={"content-type": "text/html"}, retrieved_at="2026-08-01T00:00:00Z")


def crawl_of(html, origin="registry", url="https://www.nordlysbakeri.no/"):
    response = page_response(url, html)
    return Crawl(candidate=Candidate(url=url, origin=origin), home=response, home_page=parse_html(html),
                 site_domain=registered_domain(website.host_of(url)), origin_url=url)


class OrganisationNumbers(unittest.TestCase):
    def test_checksum(self):
        self.assertTrue(valid_org_number("923609016"))
        self.assertTrue(valid_org_number("923 609 016"))
        self.assertFalse(valid_org_number("923609017"))
        self.assertFalse(valid_org_number("123456789"))
        self.assertFalse(valid_org_number("12345"))

    def test_found_only_when_checksum_valid(self):
        text = "Org.nr 923 609 016 MVA. Phone 22 33 44 55. Random 123456789. NO914778271MVA"
        self.assertEqual(find_org_numbers(text), ["923609016", "914778271"])

    def test_dates(self):
        self.assertEqual(parse_date("12.03.2026"), "2026-03-12")
        self.assertEqual(parse_date("2026-03-12T10:00:00+02:00"), "2026-03-12")
        self.assertEqual(parse_date("Tue, 03 Mar 2026 09:00:00 +0100"), "2026-03-03")
        self.assertIsNone(parse_date("soon"))


class Inputs(unittest.TestCase):
    def write(self, name, text):
        path = Path(self.tmp.name) / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_formats_agree(self):
        expected = [ORG, OTHER]
        self.assertEqual(read_inputs(self.write("a.txt", f"{ORG}\n{OTHER}\n"))[0], expected)
        self.assertEqual(read_inputs(self.write("a.jsonl", f'{{"organisation_number":"{ORG}"}}\n{{"orgnr":"{OTHER}"}}\n'))[0], expected)
        self.assertEqual(read_inputs(self.write("a.json", json.dumps({"organisation_numbers": expected})))[0], expected)
        self.assertEqual(read_inputs(self.write("b.json", json.dumps([{"organisation_number": int(ORG)}, OTHER])))[0], expected)
        self.assertEqual(read_inputs(self.write("a.csv", f"organisation_number,name\n{ORG},A\n923 609 016,dup\n{OTHER},B\n"))[0], expected)
        self.assertEqual(read_inputs(f"{ORG},{OTHER}")[0], expected)

    def test_duplicates_collapse_and_invalid_rows_survive(self):
        orgs, info = read_inputs(self.write("c.txt", f"{ORG}\n{ORG}\n123\n"))
        self.assertEqual(orgs, [ORG, "123"])
        self.assertEqual(info["duplicates_collapsed"], 1)
        self.assertEqual(info["invalid_organisation_numbers"], ["123"])


class OfficialFacts(unittest.TestCase):
    def test_every_claim_has_evidence_and_no_birth_dates(self):
        envelope = run(snapshot(acc=accounts()))
        self.assertEqual(envelope["status"], "available")
        evidence = {item["id"] for item in envelope["evidence"]}
        for claim in envelope["claims"]:
            if claim["availability"] == "available":
                self.assertTrue(claim["evidence_ids"] and set(claim["evidence_ids"]) <= evidence, claim["key"])
        self.assertNotIn("1970-01-01", json.dumps(envelope))
        self.assertTrue(validate([envelope], [ORG])["passed"])

    def test_missing_financial_line_is_never_zero(self):
        record = accounts()
        del record[0]["eiendeler"]
        envelope = run(snapshot(acc=record))
        keys = claims_of(envelope)
        self.assertNotIn("accounts.SELSKAP.2025.total_assets", keys)
        self.assertEqual(keys["accounts.SELSKAP.2025.revenue"]["value"], 1000)
        self.assertEqual(keys["accounts.SELSKAP.2025.revenue"]["period"]["to"], "2025-12-31")
        self.assertNotIn("total_assets", [claim["field"] for claim in envelope["claims"] if claim["value"] == 0])

    def test_reported_zero_is_kept_as_zero(self):
        envelope = run(snapshot(acc=accounts(revenue=0)))
        self.assertEqual(claims_of(envelope)["accounts.SELSKAP.2025.revenue"]["value"], 0)

    def test_no_accounts_is_not_available_with_no_numbers(self):
        envelope = run(snapshot())
        self.assertEqual(envelope["sections"]["annual_accounts"]["availability"], "not_available")
        self.assertFalse([c for c in envelope["claims"] if c["family"] == "annual_accounts" and c["availability"] == "available"])

    def test_accounts_of_another_company_are_dropped(self):
        record = accounts()
        record[0]["virksomhet"]["organisasjonsnummer"] = OTHER
        envelope = run(snapshot(acc=record))
        self.assertFalse([c for c in envelope["claims"] if c["family"] == "annual_accounts" and c["availability"] == "available"])

    def test_unknown_employee_count_is_not_zero(self):
        envelope = run(snapshot(ent=entity(harRegistrertAntallAnsatte=False, antallAnsatte=None)))
        claim = claims_of(envelope)["identity.employees"]
        self.assertEqual((claim["availability"], claim["value"]), ("not_available", None))

    def test_registry_reply_for_another_company_is_rejected(self):
        envelope = run(snapshot(ent=entity(organisasjonsnummer=OTHER)))
        self.assertEqual(envelope["status"], "failed")
        self.assertEqual(envelope["claims"], [])

    def test_unknown_and_invalid_numbers_still_get_an_envelope(self):
        missing = research_company(ORG, SnapshotFetcher({"responses": {}}), run_id="t", options=OFFLINE)
        self.assertEqual(missing["status"], "not_available")
        invalid = research_company("123", SnapshotFetcher({"responses": {}}), run_id="t", options=OFFLINE)
        self.assertEqual(invalid["status"], "not_applicable")
        self.assertTrue(validate([missing, invalid], [ORG, "123"])["passed"])

    def test_summary_sentences_cite_claims(self):
        envelope = run(snapshot(acc=accounts()))
        self.assertIn("Nordlys Bakeri AS", envelope["summary"]["headline"])
        ids = {claim["id"] for claim in envelope["claims"]}
        for sentence in envelope["summary"]["sentences"]:
            self.assertTrue(sentence["claim_ids"] and set(sentence["claim_ids"]) <= ids)
            self.assertTrue(sentence["evidence_ids"])
        self.assertTrue(envelope["summary"]["unknowns"])


class IdentityGate(unittest.TestCase):
    def test_org_number_on_site_is_exact(self):
        result = assess_identity(entity(), crawl_of("<title>Bakeri</title><p>Velkommen til oss.</p><footer>Org.nr 923 609 016 MVA</footer>"))
        self.assertEqual((result["status"], result["score"]), ("exact", 1.0))
        self.assertIn("923 609 016", result["proof_span"])

    def test_registry_site_with_full_legal_name_is_exact(self):
        html = "<title>Nordlys Bakeri AS - ferskt brød</title>" + "<p>Vi baker brød hver dag i Tromsø og leverer til hele byen.</p>" * 5
        self.assertEqual(assess_identity(entity(), crawl_of(html))["status"], "exact")

    def test_site_naming_another_entity_is_related_not_exact(self):
        html = "<title>Nordlys Gruppen</title><p>Nordlys Bakeri og flere merkevarer.</p><footer>Org.nr 914 778 271</footer>"
        result = assess_identity(entity(), crawl_of(html))
        self.assertEqual(result["status"], "related")
        self.assertFalse(result["publishable"])

    def test_holding_company_does_not_inherit_the_operating_company_site(self):
        html = "<title>Nordlys Bakeri</title>" + "<p>Vi baker brød hver dag i Tromsø og leverer til hele byen.</p>" * 5
        result = assess_identity(entity(navn="Nordlys Bakeri Holding AS"), crawl_of(html))
        self.assertNotEqual(result["status"], "exact")

    def test_guessed_domain_with_name_only_is_not_published(self):
        html = "<title>Nordlys Bakeri AS</title>" + "<p>Vi baker brød hver dag og leverer til hele byen.</p>" * 5
        self.assertEqual(assess_identity(entity(hjemmeside=None), crawl_of(html, origin="name_guess"))["status"], "ambiguous")

    def test_guessed_domain_with_name_and_registry_phone_is_exact(self):
        html = "<title>Nordlys Bakeri AS</title>" + "<p>Vi baker brød hver dag.</p>" * 5 + "<p>Ring oss: 77 60 00 00</p>"
        self.assertEqual(assess_identity(entity(hjemmeside=None), crawl_of(html, origin="name_guess"))["status"], "exact")

    def test_parked_domain_is_rejected(self):
        self.assertEqual(assess_identity(entity(), crawl_of("<title>nordlysbakeri.no</title><p>This domain is for sale.</p>"))["status"], "rejected")

    def test_unrelated_registry_site_is_ambiguous(self):
        html = "<title>Regnskapsbyrået Tall og Tull</title>" + "<p>Vi fører regnskap for små bedrifter i nord.</p>" * 5
        self.assertEqual(assess_identity(entity(), crawl_of(html, url="https://www.tallogtull.no/"))["status"], "ambiguous")

    def test_registry_site_on_a_domain_equal_to_the_legal_name_is_exact(self):
        html = "<title>Velkommen</title>" + "<p>Ferske bakervarer hver dag, bestill kaker og brød hos oss.</p>" * 5
        self.assertEqual(assess_identity(entity(), crawl_of(html))["status"], "exact")

    def test_nothing_is_published_from_an_unverified_site(self):
        dossier = Dossier(ORG)
        html = "<title>Regnskapsbyrået</title>" + "<p>Vi fører regnskap for små bedrifter.</p>" * 5 + '<a href="https://facebook.com/regnskapsbyraaet">fb</a>'

        class OneSite(Fetcher):
            def get(self, url, **_):
                return page_response(url, html)

            def robots_allows(self, url, source_class):
                return True

        outcome = website.collect_website(dossier, OneSite(), entity(hjemmeside="www.tallogtull.no"), allow_guess=False)
        self.assertIsNone(outcome)
        self.assertEqual(dossier.claims["website.official"]["availability"], "ambiguous")
        self.assertFalse([c for c in dossier.claims.values() if c["availability"] == "available"])

    def test_verified_site_publishes_profiles_and_news_with_evidence(self):
        dossier = Dossier(ORG)
        home = ('<html><head><title>Nordlys Bakeri AS</title><meta name="description" content="Ferskt brød fra Tromsø hver eneste dag siden 2010.">'
                '<link rel="alternate" type="application/rss+xml" href="/feed.xml"></head><body><p>Org.nr 923609016</p>'
                '<a href="https://www.facebook.com/nordlysbakeri/about">Facebook</a><a href="https://www.facebook.com/sharer/sharer.php?u=x">Del</a>'
                '<a href="https://www.instagram.com/webbyraaet_design">design</a><a href="mailto:post@nordlysbakeri.no">e-post</a>'
                '<a href="mailto:kari.nordmann@nordlysbakeri.no">Kari</a></body></html>')
        feed = ('<rss><channel><item><title>Ny butikk i Storgata</title><link>https://www.nordlysbakeri.no/nytt/butikk</link>'
                '<pubDate>Tue, 03 Mar 2026 09:00:00 +0100</pubDate></item></channel></rss>')

        class Site(Fetcher):
            def get(self, url, **_):
                return page_response(url, feed if url.endswith("feed.xml") else home)

            def robots_allows(self, url, source_class):
                return True

        outcome = website.collect_website(dossier, Site(), entity(), allow_guess=False)
        self.assertEqual(outcome["identity_assessment"]["status"], "exact")
        self.assertEqual(dossier.claims["profile.facebook"]["value"]["url"], "https://www.facebook.com/nordlysbakeri")
        self.assertNotIn("profile.instagram", dossier.claims)  # agency credit link: handle unrelated to the company
        self.assertEqual(dossier.claims["contact.email"]["value"], "post@nordlysbakeri.no")  # never a named person's mailbox
        news = [c for c in dossier.claims.values() if c["field"] == "company_news_item"]
        self.assertEqual(news[0]["value"]["published"], "2026-03-03")
        for claim in dossier.claims.values():
            if claim["availability"] == "available":
                self.assertTrue(all(dossier.evidence[e]["content_sha256"] for e in claim["evidence_ids"]))

    def test_social_link_filtering(self):
        page = parse_html('<a href="https://twitter.com/intent/tweet?text=x">t</a><a href="https://www.linkedin.com/company/nordlys-bakeri/">li</a>'
                          '<a href="https://www.youtube.com/watch?v=abc">video</a>')
        self.assertEqual([link["platform"] for link in social_links(page, "https://x.no/")], ["linkedin"])

    def test_feed_parser_needs_dates(self):
        self.assertEqual(parse_feed("<rss><channel><item><title>Uten dato</title><link>https://x.no/a</link></item></channel></rss>"), [])


class ExternalSources(unittest.TestCase):
    PAGE = ('<html><head><script type="application/ld+json">{"@type":"LocalBusiness","name":"NORDLYS BAKERI AS",'
            '"identifier":{"@type":"PropertyValue","name":"Organisasjonsnummer","value":"%s"},'
            '"aggregateRating":{"@type":"AggregateRating","ratingValue":4.6,"bestRating":5,"ratingCount":31}}</script></head><body>x</body></html>')

    def fetcher(self, html, status=200):
        class Directory(Fetcher):
            def get(self, url, **_):
                response = page_response(url, html)
                response.status = status
                return response

            def robots_allows(self, url, source_class):
                return True
        return Directory()

    def test_rating_needs_the_organisation_number_in_structured_data(self):
        from signalpost.sources.external import collect_reviews
        good = Dossier(ORG)
        self.assertTrue(collect_reviews(good, self.fetcher(self.PAGE % ORG), entity()))
        self.assertEqual(good.claims["reviews.rating"]["value"]["rating"], 4.6)
        self.assertEqual(good.claims["reviews.rating"]["value"]["review_count"], 31)
        other = Dossier(ORG)
        self.assertFalse(collect_reviews(other, self.fetcher(self.PAGE % OTHER), entity()))
        self.assertEqual(other.claims["reviews.rating"]["availability"], "ambiguous")
        missing = Dossier(ORG)
        self.assertFalse(collect_reviews(missing, self.fetcher("", status=404), entity()))
        self.assertEqual(missing.claims["reviews.rating"]["availability"], "not_available")

    def test_domain_guesses_are_candidates_only(self):
        from signalpost.sources.website import guess_hosts
        hosts = guess_hosts("RØRLEGGER JOHANNESEN AS")
        self.assertIn("rorleggerjohannesen.no", hosts)
        self.assertIn("roerlegger-johannesen.no", hosts)
        self.assertEqual(guess_hosts("AS"), [])

    def test_footprint_mirrors_published_claims_only(self):
        envelope = run(snapshot(acc=accounts()))
        observations = envelope["external_footprint"]["observations"]
        self.assertEqual([item["signal_type"] for item in observations], ["workforce_snapshot"])
        self.assertTrue(all(item["exact_entity"] and len(item["content_sha256"]) == 64 for item in observations))
        unknown = run(snapshot(ent=entity(harRegistrertAntallAnsatte=False, antallAnsatte=None)))
        self.assertEqual(unknown["external_footprint"]["observations"], [])


class KeyedConnectors(unittest.TestCase):
    """Run against saved sample responses; no key and no network are needed."""

    SEARCH = {"web": {"results": [
        {"url": "https://www.proff.no/selskap/nordlys-bakeri-as/1", "title": "t"}, {"url": "https://www.facebook.com/nordlysbakeri", "title": "t"},
        {"url": "https://www.unrelated-news.no/artikkel", "title": "t"}, {"url": "https://www.nordlysbakeri.no/om-oss", "title": "t"}]}}
    PLACE = {"id": "ChIJabc", "displayName": {"text": "Nordlys Bakeri"}, "formattedAddress": "Storgata 1, 9008 Tromsø, Norge",
             "postalAddress": {"postalCode": "9008"}, "nationalPhoneNumber": "77 60 00 00", "websiteUri": "https://www.nordlysbakeri.no/",
             "rating": 4.4, "userRatingCount": 128, "googleMapsUri": "https://maps.google.com/?cid=1"}

    def setUp(self):
        import os
        from signalpost.sources import keyed
        self.keyed = keyed
        self.saved = {name: os.environ.get(name) for name in ("SIGNALPOST_SEARCH_API_KEY", "SIGNALPOST_PLACES_API_KEY", "BRAVE_SEARCH_API_KEY", "GOOGLE_PLACES_API_KEY")}
        for name in self.saved:
            os.environ.pop(name, None)
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None) for k, v in self.saved.items()])

    def api(self, body, seen):
        class Api(Fetcher):
            def get(self, url, **kwargs):
                seen.append((url, kwargs.get("headers") or {}, kwargs.get("data")))
                return Response(url=url, final_url=url, status=200, raw=json.dumps(body).encode("utf-8"), retrieved_at="2026-08-01T00:00:00Z")
        return Api(Budget(100))

    def test_connectors_are_off_without_a_key(self):
        seen = []
        self.assertEqual(self.keyed.search_candidates(self.api(self.SEARCH, seen), entity()), ([], None))
        self.assertFalse(self.keyed.collect_places(Dossier(ORG), self.api({"places": [self.PLACE]}, seen), entity(), None))
        self.assertEqual(seen, [])
        self.assertEqual(self.keyed.enabled(), {"search": False, "places": False})

    def test_search_returns_candidates_only_and_drops_directories(self):
        import os
        os.environ["SIGNALPOST_SEARCH_API_KEY"] = "test-key-not-real"
        seen = []
        fetcher = self.api(self.SEARCH, seen)
        urls, note = self.keyed.search_candidates(fetcher, entity())
        self.assertEqual(urls[0], "https://www.nordlysbakeri.no/")
        self.assertFalse([url for url in urls if "proff" in url or "facebook" in url])
        self.assertIsNone(note)
        self.assertEqual(seen[0][1]["X-Subscription-Token"], "test-key-not-real")
        self.assertNotIn("test-key-not-real", seen[0][0])  # the key travels in a header, never in the URL
        self.assertAlmostEqual(fetcher.budget.cost_usd, self.keyed.SEARCH_COST_USD)

    def test_search_candidate_still_needs_on_site_proof(self):
        html = "<title>Nordlys Bakeri AS</title>" + "<p>Vi baker brød hver dag og leverer til hele byen.</p>" * 5
        self.assertEqual(assess_identity(entity(hjemmeside=None), crawl_of(html, origin="search"))["status"], "ambiguous")
        proven = html + "<footer>Org.nr 923 609 016</footer>"
        self.assertEqual(assess_identity(entity(hjemmeside=None), crawl_of(proven, origin="search"))["status"], "exact")

    def test_place_is_published_only_on_an_identity_match(self):
        import os
        os.environ["SIGNALPOST_PLACES_API_KEY"] = "test-key-not-real"
        seen = []
        dossier = Dossier(ORG)
        self.assertTrue(self.keyed.collect_places(dossier, self.api({"places": [self.PLACE]}, seen), entity(), "nordlysbakeri.no"))
        self.assertEqual(dossier.claims["reviews.google"]["value"]["review_count"], 128)
        self.assertNotIn("test-key-not-real", json.dumps([dossier.claims, dossier.evidence, dossier.modules]))
        namesake = dict(self.PLACE, formattedAddress="Havnegata 9, 5003 Bergen, Norge", postalAddress={"postalCode": "5003"},
                        nationalPhoneNumber="55 11 22 33", websiteUri="https://nordlys-bakeri-bergen.no/")
        other = Dossier(ORG)
        self.assertFalse(self.keyed.collect_places(other, self.api({"places": [namesake]}, seen), entity(), "nordlysbakeri.no"))
        self.assertEqual(other.claims["reviews.google"]["availability"], "ambiguous")
        self.assertNotIn("profile.google_maps", other.claims)

    def test_paid_calls_stop_at_the_declared_budget(self):
        budget = Budget(100)
        budget.max_cost_usd = 0.01
        self.assertTrue(budget.spend("brave_search", 0.005))
        self.assertTrue(budget.spend("brave_search", 0.005))
        self.assertFalse(budget.spend("google_places", 0.035))
        self.assertAlmostEqual(budget.cost_usd, 0.01)


class Refresh(unittest.TestCase):
    def test_same_snapshot_is_idempotent(self):
        first = run(snapshot(acc=accounts()))
        second = run(snapshot(acc=accounts(), at="2026-08-02T00:00:00Z"), previous=first, run_id="t2")
        self.assertEqual(second["changes"], [])
        self.assertEqual(snapshot_hash(first), snapshot_hash(second))
        keys = [claim["key"] for claim in second["claims"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(claims_of(second)["identity.legal_name"]["first_observed_at"], first["run"]["completed_at"])

    def test_changes_are_typed_and_keep_earlier_evidence(self):
        first = run(snapshot(acc=accounts(), rol=roles(("DAGL", "Kari", "Nordmann"))))
        second = run(snapshot(ent=entity(antallAnsatte=15), acc=accounts(revenue=1500), rol=roles(("DAGL", "Ola", "Hansen")), at="2026-08-02T00:00:00Z"),
                     previous=first, run_id="t2")
        types = {change["change_type"] for change in second["changes"]}
        self.assertLessEqual({"changed_employee_count", "new_role", "ended_role", "changed_revenue"}, types)
        changed = next(change for change in second["changes"] if change["change_type"] == "changed_employee_count")
        self.assertEqual((changed["old_value"], changed["new_value"]), (12, 15))
        self.assertTrue(changed["old_evidence"] and changed["old_content_sha256"] and changed["new_content_sha256"])
        self.assertEqual(next(c for c in second["changes"] if c["change_type"] == "new_role")["materiality"], "high")
        third = run(snapshot(ent=entity(antallAnsatte=15), acc=accounts(revenue=1500), rol=roles(("DAGL", "Ola", "Hansen")), at="2026-08-03T00:00:00Z"),
                    previous=second, run_id="t3")
        self.assertEqual(third["changes"], [])
        self.assertEqual(len(third["change_history"]), len(second["changes"]))

    def test_new_filing_keeps_older_years_as_history(self):
        first = run(snapshot(acc=accounts(year="2024")))
        second = run(snapshot(acc=accounts(year="2025", revenue=2000), at="2026-08-02T00:00:00Z"), previous=first, run_id="t2")
        keys = claims_of(second)
        self.assertIn("new_filing", {change["change_type"] for change in second["changes"]})
        self.assertTrue(keys["accounts.SELSKAP.2024.revenue"]["carried_forward"])
        self.assertNotIn("removed", {change["kind"] for change in second["changes"]})

    def test_failed_source_keeps_last_value_and_reports_no_false_change(self):
        first = run(snapshot(acc=accounts()))
        broken = snapshot(acc=accounts(), at="2026-08-02T00:00:00Z")
        broken["responses"][f"{API}/enheter/{ORG}/roller"] = {"status": 500, "body": {}}
        second = run(broken, previous=first, run_id="t2")
        self.assertEqual(second["changes"], [])
        kept = claims_of(second)["leadership.DAGL.kari-nordmann"]
        self.assertTrue(kept["carried_forward"])
        self.assertEqual(second["modules"]["roles"]["availability"], "failed")

    def test_total_failure_never_erases_the_previous_profile(self):
        first = run(snapshot(acc=accounts()))
        failed = failed_envelope(ORG, "t2", "time_budget", "out of time", first)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["changes"], [])
        self.assertTrue(claims_of(failed)["identity.legal_name"]["carried_forward"])

    def test_builderr_refresh_fixture(self):
        manifest = {"modules": ["registry_live", "financials"], "profiles": [{"organisation_number": ORG}],
                    "expected_changes": [{"organisation_number": ORG, "field": "registry.employees"}, {"organisation_number": ORG, "field": "financials.records"}],
                    "snapshots": {"old": snapshot(acc=accounts()), "new": snapshot(ent=entity(antallAnsatte=13), acc=accounts(revenue=1200), at="2026-08-02T00:00:00Z")}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(run_replay(str(path), str(Path(tmp) / "out.json")), 0)
            report = json.loads((Path(tmp) / "out.json").read_text(encoding="utf-8"))
        self.assertTrue(report["qualification_passed"] and report["idempotent_rerun"] and report["prior_evidence_preserved"])


class SafetyAndBudget(unittest.TestCase):
    def test_unsafe_urls_are_refused(self):
        for url in ("http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/", "file:///etc/passwd",
                    "ftp://example.com/", "http://user:pw@example.com/", "http://example.com:8080/", "http://[::1]/"):
            self.assertFalse(check_url(url)[0], url)

    def test_unsafe_url_is_never_fetched(self):
        fetcher = Fetcher(Budget(10))
        response = fetcher.get("http://127.0.0.1:80/", source_class="company_owned")
        self.assertEqual((response.status, fetcher.budget.used), (0, 0))

    def test_budget_keeps_room_for_mandatory_calls(self):
        budget = Budget(5)
        budget.reserve(4)
        self.assertTrue(budget.acquire("x", optional=True))
        self.assertFalse(budget.acquire("x", optional=True))
        self.assertTrue(all(budget.acquire("x", optional=False) for _ in range(4)))
        self.assertFalse(budget.acquire("x", optional=False))

    def test_one_envelope_per_input_even_when_everything_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                organisations=f"{ORG},{OTHER},123", output=str(Path(tmp) / "e.jsonl"), report=str(Path(tmp) / "r.json"), profiles_output=None,
                run_id="dead", expected_count=3, bulk=None, previous=None, state_dir=str(Path(tmp) / "state"), no_state=False, resume=False, workers=2,
                time_budget_min=0.0, max_requests=0, requests_per_company=20, history="off", no_website=True, no_jobs=True, no_guess=True,
                no_updates=True, no_wikidata=True, nav_interval=7.0, nav_share=0.35, site_output=str(Path(tmp) / "site"), quiet=True)
            report = run_batch(args)
            rows = [json.loads(line) for line in (Path(tmp) / "e.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["organisation_number"] for row in rows], [ORG, OTHER, "123"])
            self.assertTrue(report["validation"]["checks"]["zero_silent_drops"])
            self.assertEqual(report["operations"]["requests"], 0)
            self.assertTrue((Path(tmp) / "site" / "index.html").exists())


class Site(unittest.TestCase):
    def test_site_escapes_script_breakout(self):
        envelope = run(snapshot(ent=entity(navn="Evil </script><script>alert(1)</script> AS"), acc=accounts()))
        with tempfile.TemporaryDirectory() as tmp:
            rows = build_site([copy.deepcopy(envelope)], Path(tmp))
            self.assertEqual(rows[0]["revenue"], 1000)
            data = (Path(tmp) / "data" / "companies" / f"{ORG}.js").read_text(encoding="utf-8")
            self.assertNotIn("</script>", data)
            self.assertNotIn("</script>", (Path(tmp) / "data" / "index.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
