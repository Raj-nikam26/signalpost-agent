from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

AVAILABILITY_STATES = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")

# Legal-form suffixes and filler words that carry no identity signal.
LEGAL_AND_GENERIC = {
    "as", "asa", "ans", "da", "enk", "iks", "sa", "ba", "sam", "sti", "stiftelsen", "stiftelse", "nuf", "ks",
    "ab", "aps", "oy", "gmbh", "bv", "limited", "ltd", "inc", "plc", "llc", "the", "og", "and", "i", "av",
    "for", "norge", "norway", "norsk", "holding", "invest", "group", "gruppen", "konsern",
}
# Tokens dropped only when something more distinctive remains.
SOFT_GENERIC = {"norge", "norway", "norsk", "holding", "invest", "group", "gruppen", "konsern"}
HARD_GENERIC = LEGAL_AND_GENERIC - SOFT_GENERIC

_ORG_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def short_hash(*parts: Any, length: int = 12) -> str:
    joined = "\x1f".join(str(part) for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:length]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def clean_org_number(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def valid_org_number(value: Any) -> bool:
    """Norwegian organisation numbers are 9 digits with a modulus-11 check digit."""
    org = clean_org_number(value)
    if len(org) != 9 or org[0] not in "89":
        return False
    total = sum(int(digit) * weight for digit, weight in zip(org[:8], _ORG_WEIGHTS))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    return check != 10 and check == int(org[8])


def fold(text: Any) -> str:
    """Lowercase ASCII fold that keeps Norwegian letters distinguishable (ø->o, å->a, æ->ae)."""
    value = str(text or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()


def all_tokens(text: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(text))


def name_tokens(name: Any) -> list[str]:
    """Distinctive tokens of a legal name: legal suffixes always dropped, soft generics only if others remain."""
    tokens = [token for token in all_tokens(name) if token not in HARD_GENERIC and len(token) > 1]
    distinctive = [token for token in tokens if token not in SOFT_GENERIC]
    return distinctive or tokens


def slug(text: Any) -> str:
    return "-".join(all_tokens(text))


def find_org_numbers(text: str) -> list[str]:
    """Checksum-valid organisation numbers written as 9 digits, optionally grouped 3-3-3 or with a NO/MVA wrapper."""
    found: list[str] = []
    for match in re.finditer(r"(?<![\d])(\d{3})[  .]?(\d{3})[  .]?(\d{3})(?![\d])", text or ""):
        candidate = "".join(match.groups())
        if valid_org_number(candidate) and candidate not in found:
            found.append(candidate)
    return found


def snippet_around(text: str, needle_start: int, needle_end: int, radius: int = 60) -> str:
    start = max(0, needle_start - radius)
    end = min(len(text), needle_end + radius)
    return " ".join(text[start:end].split())


def compact(value: Any) -> Any:
    """Drop None/empty members so absent data never masquerades as a value."""
    if isinstance(value, dict):
        cleaned = {key: compact(child) for key, child in value.items()}
        return {key: child for key, child in cleaned.items() if child not in (None, "", [], {})}
    if isinstance(value, list):
        return [compact(child) for child in value if child not in (None, "", [], {})]
    return value


def first(items: Iterable[Any], default: Any = None) -> Any:
    for item in items:
        return item
    return default


def parse_date(value: Any) -> str | None:
    """Normalise common date spellings to ISO YYYY-MM-DD; return None rather than guess."""
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return "-".join(match.groups())
    match = re.match(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if match:
        day, month, year = (int(part) for part in match.groups())
        if 1 <= day <= 31 and 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-{day:02d}"
    for pattern in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%d %b %Y"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
