"""Official-website resolution and company-owned evidence.

A domain is only ever a candidate. Nothing found on it is published until the identity gate ties the site to the
exact legal entity: the organisation number on the site, or the full legal name on a domain the company itself
reported to the official register. Parent, group and brand sites are labelled as related, never collapsed.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from ..claims import COMPANY_OWNED, Dossier
from ..htmlx import Page, ld_types, parse_html
from ..http import Fetcher, Response, host_of, registered_domain
from ..util import HARD_GENERIC, SOFT_GENERIC, all_tokens, find_org_numbers, fold, parse_date, short_hash, utc_now

FREEMAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com", "live.no", "live.com", "msn.com", "yahoo.no", "yahoo.com",
    "icloud.com", "me.com", "mac.com", "online.no", "broadpark.no", "getmail.no", "frisurf.no", "start.no", "c2i.net", "lyse.net",
    "altibox.no", "epost.no", "mail.com", "protonmail.com", "proton.me", "tele2.no", "ebnett.no", "bluezone.no", "sensewave.com",
    "hesbynett.no", "runbox.no", "runbox.com", "telenor.no", "telia.no", "vikenfiber.no", "combitel.no", "loqal.no", "eidsiva.net",
    "nktv.no", "bbnett.no", "smartnet.no", "ntebb.no", "kraftlaget.no", "enivest.net", "tussa.com", "sfjbb.net", "signalbredband.no",
    "gmx.com", "gmx.net", "aol.com", "pm.me", "fastmail.com", "hey.com", "usa.net", "mimer.no", "powertech.no", "nextgentel.com",
}
SOCIAL_HOSTS = {
    "linkedin.com": "linkedin", "facebook.com": "facebook", "fb.com": "facebook", "instagram.com": "instagram", "twitter.com": "x",
    "x.com": "x", "youtube.com": "youtube", "youtu.be": "youtube", "tiktok.com": "tiktok", "vimeo.com": "vimeo", "github.com": "github",
    "snapchat.com": "snapchat", "pinterest.com": "pinterest",
}
NOT_A_COMPANY_SITE = set(SOCIAL_HOSTS) | {
    "proff.no", "1881.no", "gulesider.no", "finn.no", "brreg.no", "purehelp.no", "regnskapstall.no", "google.com", "wixsite.com",
    "business.site", "linktr.ee", "blogspot.com", "wordpress.com", "sites.google.com", "mittanbud.no", "anbudstorget.no",
}
ATS_HOSTS = (
    "teamtailor.com", "recman.no", "recman.page", "webcruiter.no", "webcruiter.com", "jobbnorge.no", "easycruit.com", "hr-manager.net",
    "varbi.com", "reachmee.com", "jobylon.com", "workable.com", "greenhouse.io", "lever.co", "smartrecruiters.com", "talentech.io",
    "cvpartner.com", "bamboohr.com", "personio.com", "personio.de", "workday.com", "myworkdayjobs.com", "ashbyhq.com", "jobs.finn.no",
)
PARKED_MARKERS = (
    "domain is for sale", "domain for sale", "domenet er til salgs", "hugedomains", "parked at", "parkert hos", "this domain is parked",
    "her flytter snart en ny gjest", "buy this domain", "kjop dette domenet", "sedo.com", "domeneshop.no/parkert", "nettsiden er under konstruksjon",
    "website coming soon", "default web site page", "apache2 ubuntu default page", "welcome to nginx", "index of /", "account suspended",
    "find the best information and most relevant links on all topics related to", "denne siden er parkert", "one.com - velkommen",
)
TEMPLATE_VENDORS = {"wix", "wixcom", "squarespace", "wordpress", "wordpressdotcom", "shopify", "webflow", "weebly", "godaddy", "onecom",
                    "mystore", "mystoreno", "24nettbutikk", "webnode", "jimdo", "domeneshop", "google", "youtube", "facebook", "instagram"}
NOT_A_PROFILE = {"sharer", "sharer.php", "share", "share.php", "sharearticle", "sharing", "pin", "intent", "dialog", "hashtag", "search", "login",
                 "signup", "policies", "legal", "help", "watch", "embed", "plugins", "tr", "privacy", "terms", "about", "business", "ads", "events",
                 "groups", "playlist", "results", "explore", "home", "feed", "maps", "l.php", "tos", "settings", "i", "share-offsite"}
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|pptx?|mp4|mp3|ics|css|js)(\?|$)", re.I)
CONTACT_RE = re.compile(r"kontakt|contact|om-oss|omoss|om_oss|om-|about|hvem-er-vi|selskapet|/om/?$|personvern|privacy", re.I)
CAREERS_RE = re.compile(r"karriere|career|jobb|/jobs|ledige|stilling|vacanc|join-us|work-with-us|bli-en-av-oss|rekrutter", re.I)
ABOUT_RE = re.compile(r"om-oss|omoss|om_oss|about|hvem-er-vi|selskapet|/om/?$|/om-", re.I)
NEWS_RE = re.compile(r"nyhet|aktuelt|/news|presse|/press|blogg|/blog|artikler|articles|siste-nytt|nytt", re.I)
GENERIC_MAILBOX = re.compile(r"^(post|info|kontakt|contact|firmapost|hei|hello|mail|salg|sales|kundeservice|support|booking|ordre|admin|office|kontor|resepsjon|epost)$", re.I)
JOB_WORDS = re.compile(r"soker|stilling|vikar|laerling|ingenior|tomrer|selger|konsulent|utvikler|radgiver|medarbeider|montor|elektriker|"
                       r"sjafor|leder|tekniker|mekaniker|operator|sykepleier|kokk|renholder|controller|engineer|developer|manager|"
                       r"assistent|fagarbeider|rorlegger|snekker|arkitekt|designer|analytiker|prosjekt|servitor|butikk|koordinator|okonom|"
                       r"regnskap|advokat|laerer|pedagog|trainee|sommerjobb|deltid|heltid", re.I)
JOB_TITLE_HINT = re.compile(r"/(jobs?|stilling(er)?|position|vacanc|ledig)[^/]*/[^/]*[\w-]{4,}", re.I)


@dataclass
class Candidate:
    url: str
    origin: str  # registry | wikidata | registry_email_domain | name_guess
    note: str = ""


@dataclass
class Crawl:
    candidate: Candidate
    home: Response | None = None
    home_page: Page | None = None
    pages: dict[str, tuple[Response, Page]] = field(default_factory=dict)  # role -> (response, parsed)
    site_domain: str = ""
    origin_url: str = ""
    blocked: str | None = None
    error: str | None = None
    links: list[tuple[str, str]] = field(default_factory=list)
    chosen: set[str] = field(default_factory=set)


def normalise_site(raw: Any) -> str | None:
    text = str(raw or "").strip().strip("/").replace(" ", "")
    if not text or "@" in text or "." not in text:
        return None
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    parts = urllib.parse.urlsplit(text)
    if not parts.hostname or "." not in parts.hostname:
        return None
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def social_platform(url: str) -> str | None:
    return SOCIAL_HOSTS.get(registered_domain(host_of(url)))


def full_core(name: Any) -> list[str]:
    """Legal-name tokens with only the legal-form suffix removed. 'holding', 'invest' etc. stay: they distinguish entities."""
    return [token for token in all_tokens(name) if token not in HARD_GENERIC and len(token) > 1]


def candidates_for(entity: dict[str, Any], wikidata_site: str | None, fetcher: Fetcher, *, allow_guess: bool) -> list[Candidate]:
    found: list[Candidate] = []
    seen: set[str] = set()

    def push(url: str | None, origin: str, note: str = "") -> None:
        if not url:
            return
        domain = registered_domain(host_of(url))
        if domain in seen or domain in NOT_A_COMPANY_SITE or any(domain.endswith("." + bad) for bad in NOT_A_COMPANY_SITE):
            return
        seen.add(domain)
        found.append(Candidate(url=url, origin=origin, note=note))

    push(normalise_site(entity.get("hjemmeside")), "registry")
    push(normalise_site(wikidata_site), "wikidata")
    email = str(entity.get("epostadresse") or "")
    if "@" in email:
        domain = email.rsplit("@", 1)[1].strip().lower()
        label = registered_domain(domain).split(".")[0].replace("-", "")
        distinctive = [token for token in full_core(entity.get("navn")) if token not in SOFT_GENERIC and len(token) >= 4]
        related = any(token in label for token in distinctive) or (len(label) >= 4 and label in "".join(full_core(entity.get("navn"))))
        # An unrelated mail domain is an accountant, employer or ISP, not the company's own site: it is not even a candidate.
        if domain and related and domain not in FREEMAIL and registered_domain(domain) not in FREEMAIL:
            push(normalise_site(domain), "registry_email_domain", f"domain of the email address reported to the registry ({email})")
    if allow_guess:
        guessed = 0
        for host in guess_hosts(entity.get("navn")):
            if registered_domain(host) in seen:
                continue
            if fetcher.resolves(host):
                push(f"https://{host}/", "name_guess", "domain derived from the legal name; published only with on-site proof")
                guessed += 1
                if guessed >= 2:
                    break
    return found


def guess_hosts(name: Any, limit: int = 8) -> list[str]:
    """Plausible domains for a legal name. They are only DNS-checked candidates; the identity gate decides everything."""
    raw = str(name or "").lower()
    spellings = []
    for mapping in ({"ø": "o", "å": "a", "æ": "ae"}, {"ø": "oe", "å": "aa", "æ": "ae"}):
        text = raw.translate(str.maketrans(mapping))
        tokens = [token for token in all_tokens(text) if token not in HARD_GENERIC and len(token) > 1]
        if tokens and tokens not in spellings:
            spellings.append(tokens)
    labels: list[str] = []
    for tokens in spellings:
        if not 1 <= len(tokens) <= 4:
            continue
        for label in ("".join(tokens), "-".join(tokens)):
            if 5 <= len(label.replace("-", "")) <= 30 and label not in labels:
                labels.append(label)
    # .no only: measured on 1,000 companies, .com and shortened-name guesses found namesakes, never the company.
    hosts = [f"{label}.no" for label in labels]
    return hosts[:limit]


# -- crawl ----------------------------------------------------------------------------------------
def _same_site_links(page: Page, base_url: str, site_domain: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in page.links:
        href = link.href
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")) or SKIP_EXT.search(href):
            continue
        absolute = urllib.parse.urldefrag(urllib.parse.urljoin(base_url, href))[0]
        if registered_domain(host_of(absolute)) != site_domain or absolute in seen:
            continue
        seen.add(absolute)
        links.append((absolute, link.text))
    return links


def _pick(links: list[tuple[str, str]], pattern: re.Pattern[str], exclude: set[str], count: int) -> list[str]:
    scored = []
    for url, text in links:
        path = urllib.parse.urlsplit(url).path
        if url in exclude or path in {"", "/"}:
            continue
        if pattern.search(path) or pattern.search(fold(text)):
            scored.append((path.count("/") + (0 if pattern.search(path) else 1), len(path), url))
    return [url for _depth, _length, url in sorted(scored)[:count]]


def crawl_site(candidate: Candidate, fetcher: Fetcher) -> Crawl:
    crawl = Crawl(candidate=candidate)
    response = fetcher.get(candidate.url, source_class=COMPANY_OWNED, robots=True, timeout=10.0)
    if response.status == 0 and not response.blocked and candidate.url.startswith("https://"):
        fallback = fetcher.get("http://" + candidate.url[len("https://"):], source_class=COMPANY_OWNED, robots=True, timeout=10.0)
        if fallback.status == 200:
            response = fallback
    crawl.home = response
    if response.blocked:
        crawl.blocked = response.blocked
        return crawl
    if response.status in {401, 403, 429, 451}:
        crawl.blocked = f"site refused the request (HTTP {response.status})"
        return crawl
    if response.status != 200 or "html" not in response.headers.get("content-type", "text/html").lower():
        crawl.error = response.error or f"HTTP {response.status}"
        return crawl
    crawl.home_page = parse_html(response.text)
    crawl.site_domain = registered_domain(host_of(response.final_url))
    parts = urllib.parse.urlsplit(response.final_url)
    crawl.origin_url = f"{parts.scheme}://{parts.netloc}/"
    if crawl.site_domain in NOT_A_COMPANY_SITE:
        crawl.error = f"redirects to {crawl.site_domain}, which is not a company-owned site"
        return crawl
    crawl.links = _same_site_links(crawl.home_page, response.final_url, crawl.site_domain)
    crawl.chosen = {response.final_url, crawl.origin_url}
    _fetch_roles(crawl, fetcher, [("contact", CONTACT_RE, 2)])
    return crawl


def _fetch_roles(crawl: Crawl, fetcher: Fetcher, roles: list[tuple[str, re.Pattern[str], int]], feeds: list[str] | None = None) -> None:
    plan: list[tuple[str, str]] = []
    for role, pattern, count in roles:
        for index, url in enumerate(_pick(crawl.links, pattern, crawl.chosen, count)):
            crawl.chosen.add(url)
            plan.append((role if index == 0 else f"{role}{index + 1}", url))
    if feeds:
        plan = [item for item in plan if item[0] != "news"] + [("feed", feeds[0])]
    for role, url in plan:
        sub = fetcher.get(url, source_class=COMPANY_OWNED, robots=True, timeout=10.0)
        if sub.status == 200 and registered_domain(host_of(sub.final_url)) == crawl.site_domain:
            crawl.pages[role] = (sub, parse_html(sub.text) if role != "feed" else Page())


def crawl_more(crawl: Crawl, fetcher: Fetcher) -> None:
    """Second phase, only for a site that already passed the identity gate: careers and news pages (or the declared feed)."""
    assert crawl.home is not None and crawl.home_page is not None
    base = crawl.home.final_url
    feeds = [urllib.parse.urljoin(base, feed) for feed in crawl.home_page.feeds
             if "comments" not in feed and registered_domain(host_of(urllib.parse.urljoin(base, feed))) == crawl.site_domain]
    _fetch_roles(crawl, fetcher, [("careers", CAREERS_RE, 1), ("news", NEWS_RE, 1)], feeds)


# -- identity gate --------------------------------------------------------------------------------
def _identity_parts(page: Page) -> list[str]:
    parts = [page.title, page.meta.get("og:site_name", ""), page.meta.get("og:title", ""), page.meta.get("application-name", ""),
             page.meta.get("author", ""), *page.headings[:3]]
    for node in page.ld_nodes():
        if ld_types(node) & {"Organization", "Corporation", "LocalBusiness", "WebSite", "NGO", "EducationalOrganization", "Store"}:
            for key in ("name", "legalName", "alternateName"):
                value = node.get(key)
                parts.extend(value if isinstance(value, list) else [value])
    return [str(part) for part in parts if part]


def _phrase_in(text_folded: str, tokens: list[str]) -> re.Match[str] | None:
    if not tokens:
        return None
    return re.search(r"(?<![a-z0-9])" + r"[\s\-&.,/]*".join(re.escape(token) for token in tokens) + r"(?![a-z0-9])", text_folded)


def assess_identity(entity: dict[str, Any], crawl: Crawl, related_orgs: set[str] | None = None) -> dict[str, Any]:
    """Decide whether the crawled site belongs to this exact legal entity. Deterministic; no model involved."""
    org = str(entity.get("organisasjonsnummer"))
    origin = crawl.candidate.origin
    trusted_origin = origin in {"registry", "wikidata"}
    home = crawl.home_page or Page()
    identity_pages: list[tuple[str, Response, Page]] = [("home", crawl.home, home)]  # type: ignore[list-item]
    identity_pages += [(role, resp, page) for role, (resp, page) in crawl.pages.items() if role.startswith("contact")]
    all_text = "\n".join(page.text for _role, _resp, page in identity_pages)
    folded = fold(all_text)
    result: dict[str, Any] = {"method": "deterministic_orgnr_name_gate_v1", "origin": origin, "reasons": [], "proof_span": None, "proof_url": None}

    def done(status: str, score: float, reason: str) -> dict[str, Any]:
        result["reasons"].append(reason)
        return {**result, "status": status, "score": score, "publishable": status == "exact"}

    if any(marker in folded for marker in PARKED_MARKERS) and len(home.text) < 2500:
        return done("rejected", 0.05, "page is a parked, for-sale or hosting placeholder")

    org_hits: dict[str, tuple[str, str]] = {}
    for _role, resp, page in identity_pages:
        for found in find_org_numbers(page.text):
            if found not in org_hits:
                match = re.search(r"[  .]?".join([found[0:3], found[3:6], found[6:9]]), page.text)
                span = " ".join(page.text[max(0, match.start() - 70): match.end() + 40].split()) if match else found
                org_hits[found] = (span, resp.final_url)
    others = set(org_hits) - {org} - (related_orgs or set())
    core = full_core(entity.get("navn"))
    distinctive = [token for token in core if token not in SOFT_GENERIC] or core
    part_tokens = [set(all_tokens(part)) for part in _identity_parts(home)]
    host_label = registered_domain(host_of(crawl.origin_url)).split(".")[0].replace("-", "")
    name_in_identity = bool(core) and any(set(core).issubset(tokens) for tokens in part_tokens)
    phrase = _phrase_in(folded, core)
    label_equals_name = len(host_label) >= 5 and host_label == "".join(core)
    result.update({"legal_name_tokens": core, "organisation_numbers_on_site": sorted(org_hits)[:8]})

    if org in org_hits:
        result["proof_span"], result["proof_url"] = org_hits[org]
        if trusted_origin or origin == "registry_email_domain" or len(others) <= 2 or name_in_identity or phrase:
            return done("exact", 1.0 if trusted_origin else 0.98, "the exact organisation number is printed on the company site")
        return done("ambiguous", 0.6, "the organisation number appears among many others on a site the company did not report; could be a directory")

    legal_phrase = _phrase_in(folded, all_tokens(entity.get("navn")))
    if others and not (legal_phrase and trusted_origin):
        result["related_organisation_numbers"] = sorted(others)[:5]
        return done("related", 0.4, "the site identifies a different legal entity (organisation number "
                    + ", ".join(sorted(others)[:3]) + "); likely a parent, group or brand site")

    if len(home.text) < 150 and not home.title:
        return done("ambiguous", 0.3, "the page has too little readable content to verify (possibly a JavaScript-only site)")

    name_proof = None
    if name_in_identity:
        name_proof = next(part for part in _identity_parts(home) if set(core).issubset(set(all_tokens(part))))
    elif phrase:
        start = max(0, phrase.start() - 50)
        name_proof = " ".join(folded[start: phrase.end() + 50].split())
    single_token_ok = len(core) > 1 or (len(core) == 1 and len(core[0]) >= 4 and len(home.text) >= 200)

    if trusted_origin:
        if (name_in_identity or phrase) and single_token_ok:
            result["proof_span"], result["proof_url"] = name_proof, crawl.home.final_url if crawl.home else None
            if others:
                result["related_organisation_numbers"] = sorted(others)[:5]
            return done("exact", 0.95 if name_in_identity else 0.92,
                        "the company reported this site to the official register and the full legal name appears on it")
        if label_equals_name:
            result["proof_span"], result["proof_url"] = f"domain {host_of(crawl.origin_url)} equals the legal name", crawl.origin_url
            return done("exact", 0.9, "the company reported this site to the official register and the domain equals the legal name")
        overlap = set(distinctive) & set().union(*part_tokens) if part_tokens else set()
        if overlap:
            return done("ambiguous", 0.6, "the registry-reported site shows only part of the legal name; it may be a brand or group site")
        return done("ambiguous", 0.35, "the registry-reported site shows neither the organisation number nor the legal name")

    # Untrusted origins (email domain, guessed domain) need the name plus an independent corroborator.
    corroborators = []
    digits = re.sub(r"\D", "", all_text)
    phone = re.sub(r"\D", "", str(entity.get("telefon") or entity.get("mobil") or ""))[-8:]
    if len(phone) == 8 and phone in digits:
        corroborators.append("registry phone number appears on the site")
    address = entity.get("forretningsadresse") or {}
    street = fold(" ".join(address.get("adresse") or []))
    street_tokens = [token for token in re.findall(r"[a-z]{4,}", street)]
    if address.get("postnummer") and str(address["postnummer"]) in all_text and street_tokens and street_tokens[0] in folded:
        corroborators.append("registered street and postal code appear on the site")
    email = str(entity.get("epostadresse") or "").lower()
    if email and email in all_text.lower():
        corroborators.append("registry email address appears on the site")
    if origin == "registry_email_domain":
        corroborators.append("the company's registry email address is on this domain")
    result["corroborators"] = corroborators
    if (name_in_identity or phrase or label_equals_name) and single_token_ok and corroborators:
        result["proof_span"] = name_proof or f"domain {host_of(crawl.origin_url)} equals the legal name"
        result["proof_url"] = crawl.home.final_url if crawl.home else None
        return done("exact", 0.93 if len(corroborators) > 1 else 0.9, "full legal name on the site, corroborated: " + "; ".join(corroborators))
    return done("ambiguous", 0.4, "candidate domain could not be tied to the exact legal entity; nothing from it is published")


# -- extraction -----------------------------------------------------------------------------------
def _handle(url: str) -> str:
    parts = [part for part in urllib.parse.urlsplit(url).path.split("/") if part]
    skip = {"company", "school", "showcase", "in", "channel", "c", "user", "pages", "people", "profile.php", "p"}
    parts = [part for part in parts if part.lower() not in skip]
    return urllib.parse.unquote(parts[0]).lstrip("@") if parts else ""


def _canonical_profile(url: str, platform: str) -> str:
    """Profile URL trimmed to the handle, so tab suffixes like /about or /timeline never create a 'different' profile."""
    parts = urllib.parse.urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    prefixed = {"company", "school", "showcase", "in", "channel", "c", "user", "pages", "people", "p"}
    keep = 2 if segments and segments[0].lower() in prefixed else 1
    if platform == "facebook" and segments and segments[0].lower() == "profile.php":
        return urllib.parse.urlunsplit(("https", parts.netloc.lower(), "/profile.php", parts.query, ""))
    return urllib.parse.urlunsplit(("https", parts.netloc.lower(), "/" + "/".join(segments[:keep]), "", ""))


def social_links(page: Page, base_url: str) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    urls = [urllib.parse.urljoin(base_url, link.href) for link in page.links if link.href.startswith(("http", "//"))]
    for node in page.ld_nodes():
        same = node.get("sameAs")
        urls.extend(str(item) for item in (same if isinstance(same, list) else [same]) if item)
    for url in urls:
        platform = social_platform(url)
        if not platform or platform in found:
            continue
        path = urllib.parse.urlsplit(url).path.lower()
        if path.strip("/").split("/")[0] in NOT_A_PROFILE:
            continue
        if "share" in urllib.parse.urlsplit(url).query.lower() or path.strip("/") == "":
            continue
        handle = _handle(url)
        if not handle or handle.lower() in {"home", "home.php", "feed", "explore"} or "".join(all_tokens(handle)) in TEMPLATE_VENDORS:
            continue
        clean = _canonical_profile(url, platform)
        found[platform] = {"platform": platform, "url": clean, "handle": handle}
    return list(found.values())


def _handle_matches(handle: str, aliases: list[str]) -> bool:
    compact_handle = "".join(all_tokens(handle))
    if len(compact_handle) < 3:
        return False
    return any(len(alias) >= 4 and (alias in compact_handle or (len(compact_handle) >= 5 and compact_handle in alias)) for alias in aliases)


def parse_feed(xml_text: str, limit: int = 6) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except (ET.ParseError, ValueError):
        return items
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in {"item", "entry"}:
            continue
        data: dict[str, str] = {}
        for child in node:
            name = child.tag.rsplit("}", 1)[-1].lower()
            if name == "title":
                data["title"] = " ".join((child.text or "").split())
            elif name == "link":
                data.setdefault("url", (child.get("href") or child.text or "").strip())
            elif name in {"pubdate", "published", "updated", "date"}:
                data.setdefault("published", parse_date(child.text) or "")
        if data.get("title") and data.get("url") and data.get("published"):
            items.append(data)
        if len(items) >= limit:
            break
    return items


def _ld_articles(page: Page) -> list[dict[str, str]]:
    items = []
    for node in page.ld_nodes():
        if ld_types(node) & {"NewsArticle", "BlogPosting", "Article", "PressRelease"} and node.get("headline") and node.get("datePublished"):
            url = node.get("url") or (node.get("mainEntityOfPage") if isinstance(node.get("mainEntityOfPage"), str) else "")
            published = parse_date(node.get("datePublished"))
            if url and published:
                items.append({"title": " ".join(str(node["headline"]).split()), "url": str(url), "published": published})
    return items


def _ld_jobs(page: Page) -> list[dict[str, Any]]:
    jobs = []
    for node in page.ld_nodes():
        if "JobPosting" in ld_types(node) and node.get("title"):
            location = node.get("jobLocation")
            location = location[0] if isinstance(location, list) and location else location
            address = (location or {}).get("address") if isinstance(location, dict) else None
            jobs.append({
                "title": " ".join(str(node["title"]).split()), "url": str(node.get("url") or ""),
                "date_posted": parse_date(node.get("datePosted")), "valid_through": parse_date(node.get("validThrough")),
                "location": (address or {}).get("addressLocality") if isinstance(address, dict) else None,
                "employment_type": node.get("employmentType") if isinstance(node.get("employmentType"), str) else None,
            })
    return jobs


def publish_site(dossier: Dossier, entity: dict[str, Any], crawl: Crawl, assessment: dict[str, Any]) -> dict[str, Any]:
    """Publish claims from a site that passed the identity gate. Returns a compact summary for the profile block."""
    home, page = crawl.home, crawl.home_page
    assert home is not None and page is not None
    today = utc_now()[:10]
    source = "Company website"

    def ev(response: Response, span: str, method: str, effective: str | None = None) -> str:
        return dossier.add_evidence(response, source_class=COMPANY_OWNED, source_name=source, claim_span=span[:300], extraction_method=method,
                                    effective_at=effective)

    proof_response = next((resp for resp, _page in crawl.pages.values() if resp.final_url == assessment.get("proof_url")), home)
    proof = ev(proof_response, str(assessment.get("proof_span") or crawl.origin_url), "identity_gate:" + assessment["method"])
    dossier.add_claim("website.official", field="official_website", family="official_website", value=crawl.origin_url, evidence_ids=[proof],
                      confidence=assessment["score"], label="Verified official website",
                      explanation="Identity gate: " + "; ".join(assessment["reasons"]) + f". Candidate source: {crawl.candidate.origin}.")
    if page.title:
        dossier.add_claim("website.title", field="website_title", family="official_website", value=page.title[:200],
                          evidence_ids=[ev(home, f"<title>{page.title[:200]}</title>", "html_title")], confidence=assessment["score"], label="Website title")
    description = page.meta.get("description") or page.meta.get("og:description")
    description_method, description_response = "html_meta", home
    if not description or len(description) < 25:
        # Fallback: a self-describing sentence from the company's own "about" page. Homepages are skipped on purpose:
        # their first paragraph is often a product, an offer or a banner, and it changes from day to day.
        name_token = next((token for token in full_core(entity.get("navn")) if token not in SOFT_GENERIC and len(token) >= 4), None)
        for role, (resp, sub) in crawl.pages.items():
            if not role.startswith("contact") or not ABOUT_RE.search(urllib.parse.urlsplit(resp.final_url).path):
                continue
            paragraph = next((line for line in sub.text.split("\n") if 80 <= len(line) <= 600 and line.count(" ") >= 10
                              and not re.search(r"cookie|informasjonskapsl|personvern|javascript|%|kr\b|,-", line, re.I)
                              and (re.search(r"\b(vi|we|vår|vårt|våre|our)\b", line, re.I) or (name_token and name_token in fold(line)))), None)
            if paragraph:
                description, description_method, description_response = paragraph, "about_page_self_description", resp
                break
    if description and len(description) >= 25:
        dossier.add_claim("website.description", field="company_description", family="official_website", value=description[:500],
                          evidence_ids=[ev(description_response, f'description: {description[:240]}', description_method)],
                          confidence=assessment["score"] if description_method == "html_meta" else min(assessment["score"], 0.85),
                          label="Company's own description", explanation="Company-reported promotional text from its own site; not an independent assessment.")
    brand = page.meta.get("og:site_name")
    if not brand:
        for node in page.ld_nodes():
            if ld_types(node) & {"Organization", "Corporation", "LocalBusiness"} and isinstance(node.get("name"), str):
                brand = node["name"]
                break
    legal = str(entity.get("navn") or "")
    same_as_legal = [token for token in all_tokens(brand) if token not in HARD_GENERIC] == [token for token in all_tokens(legal) if token not in HARD_GENERIC]
    if brand and 2 <= len(brand) <= 60 and not same_as_legal:
        dossier.add_claim("website.public_brand", field="public_brand", family="legal_identity", value=" ".join(brand.split()),
                          evidence_ids=[ev(home, f"site name: {brand}", "og_site_name_or_json_ld")], confidence=min(assessment["score"], 0.9),
                          label="Public brand name", explanation="Name the company uses on its verified site; the legal name is unchanged.")

    # contact details: generic mailboxes only, never a named person's address
    contact_pages = [(home, page)] + [(resp, sub) for role, (resp, sub) in crawl.pages.items() if role.startswith("contact")]
    email_done = phone_done = False
    for resp, sub in contact_pages:
        for link in sub.links:
            href = link.href.strip()
            if not email_done and href.lower().startswith("mailto:"):
                address = urllib.parse.unquote(href[7:].split("?")[0]).strip().lower()
                if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", address) and GENERIC_MAILBOX.match(address.split("@")[0]) \
                        and registered_domain(address.split("@")[1]) == crawl.site_domain:
                    dossier.add_claim("contact.email", field="contact_email", family="contact", value=address, confidence=assessment["score"],
                                      evidence_ids=[ev(resp, f"mailto:{address}", "html_mailto_link")], label="Contact email (company site)")
                    email_done = True
            if not phone_done and href.lower().startswith("tel:"):
                number = re.sub(r"[^\d+]", "", href[4:])
                if 8 <= len(re.sub(r"\D", "", number)) <= 12:
                    dossier.add_claim("contact.phone", field="contact_phone", family="contact", value=number, confidence=assessment["score"],
                                      evidence_ids=[ev(resp, f"tel:{number}", "html_tel_link")], label="Contact phone (company site)")
                    phone_done = True

    # company-owned social profiles
    aliases = ["".join(full_core(legal)), "".join(t for t in full_core(legal) if t not in SOFT_GENERIC), "".join(all_tokens(brand or "")),
               registered_domain(host_of(crawl.origin_url)).split(".")[0].replace("-", "")]
    aliases = [alias for alias in dict.fromkeys(aliases) if alias]
    per_page = [(resp, social_links(sub, resp.final_url)) for resp, sub in [(home, page)] + [(r, p) for r, p in crawl.pages.values()]]
    counts: dict[str, int] = {}
    for _resp, links in per_page:
        for link in links:
            counts[link["url"]] = counts.get(link["url"], 0) + 1
    ld_same = {_canonical_profile(str(item), social_platform(str(item)) or "") for node in page.ld_nodes() for item in (node.get("sameAs") if isinstance(node.get("sameAs"), list) else [node.get("sameAs")]) if item}
    profiles: list[dict[str, str]] = []
    quarantined = 0
    for link in per_page[0][1]:
        reasons = []
        if _handle_matches(link["handle"], aliases):
            reasons.append("handle matches the company or brand name")
        if link["url"] in ld_same:
            reasons.append("declared in the site's structured data (sameAs)")
        if not reasons:
            quarantined += 1
            continue
        dossier.add_claim(f"profile.{link['platform']}", field="social_profile", family="company_profiles", value=link,
                          evidence_ids=[ev(home, f"link on {host_of(home.final_url)}: {link['url']}", "html_outbound_link")],
                          confidence=round(min(assessment["score"], 0.97 if len(reasons) > 1 else 0.92), 2),
                          label=f"{link['platform'].capitalize()} profile",
                          explanation="Outbound link from the verified company site; " + "; ".join(reasons) + ". Only the profile URL is recorded; the platform itself is not crawled.")
        profiles.append(link)
    if not profiles:
        dossier.unavailable("profile.none", field="social_profile", family="company_profiles", availability="not_available", evidence_ids=[proof],
                            label="Company-owned social profiles",
                            explanation="The verified company site was checked and links to no social profile that could be tied to the company."
                            + (f" {quarantined} unrelated-looking link(s) were withheld." if quarantined else ""))

    # hiring
    job_claims = 0
    careers = crawl.pages.get("careers")
    job_sources = [(home, page)] + ([careers] if careers else [])
    seen_jobs: set[str] = set()
    for resp, sub in job_sources:
        for job in _ld_jobs(sub):
            url = urllib.parse.urljoin(resp.final_url, job["url"]) if job["url"] else resp.final_url
            if job.get("valid_through") and job["valid_through"] < today:
                continue
            key = short_hash(url, job["title"])
            if key in seen_jobs or job_claims >= 15:
                continue
            seen_jobs.add(key)
            dossier.add_claim(f"hiring.job.{key}", field="job_posting", family="hiring", confidence=min(assessment["score"], 0.95),
                              value={**{k: v for k, v in job.items() if v}, "url": url, "source": "company site (structured data)"},
                              evidence_ids=[ev(resp, f"JobPosting: {job['title']}", "json_ld_jobposting", job.get("date_posted"))],
                              label="Open position", effective_date=job.get("date_posted"))
            job_claims += 1
    if careers:
        resp, sub = careers
        dossier.add_claim("hiring.careers_page", field="careers_page", family="hiring", value=resp.final_url, confidence=assessment["score"],
                          evidence_ids=[ev(resp, f"careers page: {sub.title[:120] or resp.final_url}", "html_link_discovery")], label="Careers page")
        for link in sub.links:
            url = urllib.parse.urljoin(resp.final_url, link.href)
            host = host_of(url)
            if not any(host == ats or host.endswith("." + ats) for ats in ATS_HOSTS):
                continue
            if not JOB_TITLE_HINT.search(urllib.parse.urlsplit(url).path) or not (10 <= len(link.text) <= 140):
                continue
            key = short_hash(url)
            if key in seen_jobs or job_claims >= 15:
                continue
            seen_jobs.add(key)
            dossier.add_claim(f"hiring.job.{key}", field="job_posting", family="hiring", confidence=min(assessment["score"], 0.88),
                              value={"title": link.text, "url": url, "source": f"company careers page linking to {registered_domain(host)}"},
                              evidence_ids=[ev(resp, f"job link: {link.text} -> {url}", "html_ats_link")], label="Open position")
            job_claims += 1
    if careers and job_claims < 15:
        resp, sub = careers
        base_path = urllib.parse.urlsplit(resp.final_url).path.rstrip("/")
        for link in sub.links:
            url = urllib.parse.urldefrag(urllib.parse.urljoin(resp.final_url, link.href))[0]
            path = urllib.parse.urlsplit(url).path.rstrip("/")
            if registered_domain(host_of(url)) != crawl.site_domain or not path.startswith(base_path + "/") or SKIP_EXT.search(url):
                continue
            title = link.text.strip()
            if not (8 <= len(title) <= 120) or not JOB_WORDS.search(fold(title)):
                continue
            key = short_hash(url)
            if key in seen_jobs or job_claims >= 15:
                continue
            seen_jobs.add(key)
            dossier.add_claim(f"hiring.job.{key}", field="job_posting", family="hiring", confidence=min(assessment["score"], 0.8),
                              value={"title": title, "url": url, "source": "company careers page (vacancy link)"},
                              evidence_ids=[ev(resp, f"vacancy link under the careers page: {title} -> {url}", "html_careers_sublink")],
                              label="Open position",
                              explanation="Link below the company's own careers page whose text names a role. Listed by the company; closing date not stated.")
            job_claims += 1
    if not job_claims:
        checked = ev(careers[0] if careers else home, "no structured job posting on the checked page", "json_ld_jobposting")
        dossier.unavailable("hiring.site_jobs", field="job_posting", family="hiring", availability="not_available", evidence_ids=[checked],
                            label="Open positions on the company site",
                            explanation=("The careers page" if careers else "The homepage (no careers page was linked)")
                            + " was checked and exposes no machine-readable job posting. This is not proof that the company is not hiring.")

    # dated public activity from the company's own channels
    news_items: list[tuple[Response, dict[str, str], str]] = []
    if "feed" in crawl.pages:
        resp, _ = crawl.pages["feed"]
        news_items += [(resp, item, "rss_or_atom_feed") for item in parse_feed(resp.text)]
    if "news" in crawl.pages:
        resp, sub = crawl.pages["news"]
        news_items += [(resp, item, "json_ld_article") for item in _ld_articles(sub)]
        news_items += [(resp, {"title": item.title, "url": item.href, "published": parse_date(item.date) or ""}, "html_dated_item") for item in sub.items]
    news_items += [(home, item, "json_ld_article") for item in _ld_articles(page)]
    news_items += [(home, {"title": item.title, "url": item.href, "published": parse_date(item.date) or ""}, "html_dated_item") for item in page.items]
    published_news = 0
    seen_news: set[str] = set()
    for resp, item, method in sorted(news_items, key=lambda entry: entry[1].get("published") or "", reverse=True):
        url = urllib.parse.urljoin(resp.final_url, item["url"])
        date = item.get("published") or ""
        if not date or date > today or date < "2000-01-01" or url in seen_news or len(item["title"]) < 8:
            continue
        if registered_domain(host_of(url)) != crawl.site_domain:
            continue
        seen_news.add(url)
        dossier.add_claim(f"activity.news.{short_hash(url)}", field="company_news_item", family="public_activity", effective_date=date,
                          value={"title": item["title"][:200], "url": url, "published": date}, confidence=min(assessment["score"], 0.9),
                          evidence_ids=[ev(resp, f"{date}: {item['title'][:200]}", method, date)], label="Company news item",
                          explanation="Dated item published by the company on its own verified site.")
        published_news += 1
        if published_news >= 6:
            break
    if not published_news:
        dossier.unavailable("activity.news", field="company_news_item", family="public_activity", availability="not_available", evidence_ids=[proof],
                            label="Company news", explanation="The verified site was checked and exposes no dated news or blog item.")
    pages = [{"url": resp.final_url, "role": role, "title": sub.title[:300], "content_sha256": resp.content_sha256}
             for role, (resp, sub) in [("home", (home, page)), *crawl.pages.items()]]
    organisations = [{key: node.get(key) for key in ("@type", "name", "legalName", "url", "telephone", "email", "sameAs") if node.get(key)}
                     for node in page.ld_nodes() if ld_types(node) & {"Organization", "Corporation", "LocalBusiness"}][:5]
    return {"requested_url": crawl.candidate.url, "final_url": crawl.origin_url, "registered_domain": crawl.site_domain, "title": page.title,
            "description": description, "main_text_excerpt": page.text[:1500], "social_links": [{"platform": item["platform"], "url": item["url"]} for item in profiles],
            "structured_organisations": organisations, "pages": pages, "content_sha256": home.content_sha256,
            "extraction_state": "static_html" if len(page.text) >= 200 else "thin_or_javascript_shell",
            "identity_assessment": assessment, "pages_fetched": 1 + len(crawl.pages), "job_postings": job_claims, "news_items": published_news}


def collect_website(dossier: Dossier, fetcher: Fetcher, entity: dict[str, Any], *, wikidata_site: str | None = None,
                    related_orgs: set[str] | None = None, allow_guess: bool = True) -> dict[str, Any] | None:
    family = "official_website"
    candidates = candidates_for(entity, wikidata_site, fetcher, allow_guess=allow_guess)
    registry_site = entity.get("hjemmeside")
    if registry_site and social_platform(normalise_site(registry_site) or ""):
        # The entity reported a social page as its website: that is a company-declared profile, backed by the registry record.
        url = normalise_site(registry_site) or ""
        registry_evidence = dossier.claims.get("identity.registered_website", {}).get("evidence_ids", [])
        if registry_evidence and _handle(url):
            dossier.add_claim(f"profile.{social_platform(url)}", field="social_profile", family="company_profiles", confidence=0.9,
                              value={"platform": social_platform(url), "url": url.rstrip("/"), "handle": _handle(url)}, evidence_ids=registry_evidence,
                              label=f"{str(social_platform(url)).capitalize()} profile",
                              explanation="The company reported this profile as its website in the official register.")
    if not candidates:
        dossier.unavailable("website.official", field="official_website", family=family, availability="not_available", label="Verified official website",
                            explanation="No website is reported to the register and no candidate domain could be derived. "
                                        "This does not prove the company has no website.",
                            evidence_ids=dossier.claims.get("identity.legal_name", {}).get("evidence_ids", []))
        dossier.module("website", "not_available", note="no candidate website")
        return None
    outcome: dict[str, Any] | None = None
    last_state, last_note, transient = "not_available", "", False
    for candidate in candidates[:3]:
        crawl = crawl_site(candidate, fetcher)
        if crawl.blocked:
            last_state, last_note, transient = "blocked", f"{candidate.url}: {crawl.blocked}", True
            dossier.error("website", "blocked", crawl.blocked, candidate.url)
            continue
        if crawl.error or crawl.home_page is None:
            last_state, last_note, transient = "not_available", f"{candidate.url} did not return a readable page ({crawl.error})", True
            dossier.error("website", "unreachable", str(crawl.error), candidate.url)
            continue
        assessment = assess_identity(entity, crawl, related_orgs)
        if assessment["status"] == "exact":
            crawl_more(crawl, fetcher)
            outcome = publish_site(dossier, entity, crawl, assessment)
            outcome["candidate_origin"] = candidate.origin
            dossier.module("website", "available", response=crawl.home)
            return outcome
        reason = "; ".join(assessment["reasons"])
        if assessment["status"] == "related" and candidate.origin in {"registry", "wikidata"} and crawl.home is not None:
            ev = dossier.add_evidence(crawl.home, source_class=COMPANY_OWNED, source_name="Website reported to the registry",
                                      claim_span=f"organisation number(s) on site: {', '.join(assessment.get('related_organisation_numbers', []))}",
                                      extraction_method="identity_gate:" + assessment["method"])
            dossier.add_claim("website.related", field="related_group_website", family=family, confidence=0.85, evidence_ids=[ev],
                              value={"url": crawl.origin_url, "relation": "parent, group or brand site",
                                     "site_organisation_numbers": assessment.get("related_organisation_numbers", [])},
                              label="Related group or brand website",
                              explanation="The registry-reported site identifies a different legal entity. It is labelled as related and nothing on it is attributed to this company.")
        last_state, transient = "ambiguous" if assessment["status"] in {"ambiguous", "related"} else "not_available", False
        if assessment["status"] == "rejected":
            reason = "the only candidate domain is " + reason
        last_note = f"{candidate.url} ({candidate.origin}): {reason}"
    explanations = {
        "ambiguous": "A candidate site was found but could not be tied to this exact legal entity, so nothing from it is published. ",
        "blocked": "The candidate site refused automated access or disallows it in robots.txt. ",
        "not_available": "No candidate domain led to a usable company site. ",
    }
    dossier.unavailable("website.official", field="official_website", family=family, availability=last_state, label="Verified official website",
                        explanation=explanations[last_state] + last_note,
                        evidence_ids=dossier.claims.get("identity.legal_name", {}).get("evidence_ids", []))
    dossier.module("website", last_state, note=last_note, transient=transient)
    return None
