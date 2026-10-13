"""Outbound HTTP with a request budget, per-host politeness, robots.txt and SSRF protection.

Standard library only, so the agent installs on a clean machine without a dependency step.
"""
from __future__ import annotations

import gzip
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .util import sha256_bytes, utc_now

USER_AGENT = os.environ.get(
    "SIGNALPOST_USER_AGENT",
    f"SignalpostAgent/{__version__} (+{os.environ.get('SIGNALPOST_CONTACT', 'https://builderr.ai/challenges/signalpost')})",
)
ROBOTS_AGENT = "SignalpostAgent"
MAX_REDIRECTS = 5
ALLOWED_PORTS = {None, 80, 443}


@dataclass
class Response:
    url: str
    status: int = 0
    final_url: str = ""
    raw: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0
    retrieved_at: str = ""
    error: str | None = None
    blocked: str | None = None  # robots / policy / budget reason when the request was not allowed
    redirect_chain: list[str] = field(default_factory=list)
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def content_sha256(self) -> str | None:
        return sha256_bytes(self.raw) if self.raw else None

    @property
    def text(self) -> str:
        if not self.raw:
            return ""
        charset = None
        match = re.search(r"charset=([\w-]+)", self.headers.get("content-type", ""), re.I)
        if match:
            charset = match.group(1)
        else:
            head = self.raw[:2048].decode("ascii", "ignore")
            match = re.search(r"charset=[\"']?([\w-]+)", head, re.I)
            if match:
                charset = match.group(1)
        for candidate in (charset, "utf-8", "cp1252"):
            if not candidate:
                continue
            try:
                return self.raw.decode(candidate)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.raw.decode("utf-8", "replace")

    def json(self) -> Any:
        try:
            return json.loads(self.raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


class Budget:
    """Global outbound-request budget shared by every worker thread."""

    def __init__(self, max_requests: int, deadline: float | None = None) -> None:
        self.max_requests = max_requests
        self.deadline = deadline
        self.used = 0
        self.reserved = 0  # requests held back for the mandatory registry calls of companies not yet started
        self.bytes = 0
        self.latencies_ms: list[int] = []
        self.by_class: dict[str, int] = {}
        self.dns_lookups = 0
        self._lock = threading.Lock()

    def reserve(self, amount: int) -> None:
        with self._lock:
            self.reserved = max(0, self.reserved + amount)

    def acquire(self, source_class: str, *, optional: bool) -> bool:
        with self._lock:
            ceiling = self.max_requests - (self.reserved if optional else 0)
            if self.used >= ceiling:
                return False
            if optional and self.deadline is not None and time.monotonic() >= self.deadline:
                return False
            self.used += 1
            self.by_class[source_class] = self.by_class.get(source_class, 0) + 1
            return True

    def record(self, response: Response) -> None:
        with self._lock:
            self.bytes += len(response.raw)
            if response.elapsed_ms:
                self.latencies_ms.append(response.elapsed_ms)

    def time_left(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - time.monotonic()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def registered_domain(host: str) -> str:
    """Cheap registrable-domain approximation that covers the suffixes Norwegian companies use."""
    host = host.lower().removeprefix("www.")
    parts = host.split(".")
    two_level = {"co.uk", "org.uk", "com.au", "co.nz", "priv.no", "kommune.no", "co.no"}
    if len(parts) >= 3 and ".".join(parts[-2:]) in two_level:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_public_host(host: str) -> tuple[bool, str]:
    """Reject anything that is not a publicly routable internet host (SSRF guard)."""
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localhost", ".lan")):
        return False, "non-public hostname"
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False, "dns resolution failed"
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False, "unparseable address"
        if not address.is_global or address.is_multicast:
            return False, f"non-public address {address}"
    return True, ""


def check_url(url: str) -> tuple[bool, str]:
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False, "malformed url"
    if parts.scheme not in {"http", "https"}:
        return False, "scheme not allowed"
    if parts.username or parts.password:
        return False, "credentials in url"
    if port not in ALLOWED_PORTS:
        return False, "port not allowed"
    return is_public_host(parts.hostname or "")


class Fetcher:
    """Thread-safe fetcher. Sub-classes may replace `_send` to replay saved snapshots."""

    def __init__(self, budget: Budget | None = None, *, timeout: float = 10.0, max_bytes: int = 1_500_000) -> None:
        self.budget = budget or Budget(10**9)
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._context = ssl.create_default_context()
        self._opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=self._context))
        self._host_locks: dict[str, threading.Semaphore] = {}
        self._host_limits = {"data.brreg.no": 12, "arbeidsplassen.nav.no": 4, "query.wikidata.org": 1}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()
        self._local = threading.local()

    def thread_requests(self) -> int:
        """Requests issued by the calling thread; one company is researched per thread, so this is its exact count."""
        return getattr(self._local, "count", 0)

    # -- politeness ---------------------------------------------------------------------------
    def _semaphore(self, host: str) -> threading.Semaphore:
        with self._lock:
            if host not in self._host_locks:
                self._host_locks[host] = threading.Semaphore(self._host_limits.get(host, 2))
            return self._host_locks[host]

    def robots_allows(self, url: str, source_class: str) -> bool | None:
        """True/False from robots.txt, or None when robots.txt itself could not be evaluated within budget."""
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            known = origin in self._robots
            parser = self._robots.get(origin)
        if not known:
            response = self.get(f"{origin}/robots.txt", source_class=source_class, optional=True, robots=False, max_bytes=200_000)
            parser = None
            if response.blocked:
                return None
            if response.status == 200 and "<html" not in response.text[:500].lower():
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(response.text.splitlines())
            elif response.status in {401, 403}:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(["User-agent: *", "Disallow: /"])
            with self._lock:
                self._robots[origin] = parser
        if parser is None:
            return True  # no robots.txt published: crawling is not restricted by it
        return parser.can_fetch(ROBOTS_AGENT, url)

    # -- transport ----------------------------------------------------------------------------
    def _send(self, url: str, accept: str, timeout: float, max_bytes: int) -> Response:
        started = time.monotonic()
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.6",
        })
        response = Response(url=url, final_url=url, retrieved_at=utc_now())
        try:
            with self._opener.open(request, timeout=timeout) as handle:
                response.status = handle.status
                response.headers = {key.lower(): value for key, value in handle.headers.items()}
                response.raw = self._read(handle, max_bytes)
        except urllib.error.HTTPError as exc:
            response.status = exc.code
            response.headers = {key.lower(): value for key, value in (exc.headers or {}).items()}
            try:
                response.raw = self._read(exc, min(max_bytes, 200_000))
            except Exception:  # noqa: BLE001 - an unreadable error body is not itself an error
                response.raw = b""
            response.error = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - every transport failure becomes an explicit state
            response.error = f"{type(exc).__name__}: {str(exc)[:120]}"
        response.elapsed_ms = int((time.monotonic() - started) * 1000)
        return response

    @staticmethod
    def _read(handle: Any, max_bytes: int) -> bytes:
        raw = handle.read(max_bytes + 1)[:max_bytes]
        encoding = (handle.headers.get("Content-Encoding") or "").lower()
        try:
            if encoding == "gzip":
                raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw, max_bytes * 8)
            elif encoding == "deflate":
                raw = zlib.decompressobj().decompress(raw, max_bytes * 8)
        except (zlib.error, gzip.BadGzipFile):
            pass
        return raw

    def get(
        self,
        url: str,
        *,
        source_class: str,
        optional: bool = True,
        accept: str = "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5",
        robots: bool = False,
        attempts: int = 1,
        timeout: float | None = None,
        max_bytes: int | None = None,
    ) -> Response:
        """Fetch one URL, following validated redirects. Never raises; failures are explicit states."""
        timeout = timeout or self.timeout
        max_bytes = max_bytes or self.max_bytes
        chain: list[str] = []
        current = url
        response = Response(url=url, final_url=url, retrieved_at=utc_now())
        for _hop in range(MAX_REDIRECTS + 1):
            allowed, reason = check_url(current)
            if not allowed:
                response.blocked = None
                response.error = f"unsafe or unresolvable url: {reason}"
                response.status = 0
                break
            if robots:
                verdict = self.robots_allows(current, source_class)
                if verdict is False:
                    response.blocked = "robots.txt disallows this path"
                    break
                if verdict is None:
                    response.blocked = "request budget exhausted"
                    break
            hop_response = None
            for attempt in range(attempts):
                if not self.budget.acquire(source_class, optional=optional):
                    response.blocked = "request budget exhausted"
                    break
                self._local.count = getattr(self._local, "count", 0) + 1
                with self._semaphore(host_of(current)):
                    hop_response = self._send(current, accept, timeout, max_bytes)
                hop_response.attempts = attempt + 1
                self.budget.record(hop_response)
                retryable = hop_response.status in {0, 429, 500, 502, 503, 504}
                if not retryable or attempt + 1 >= attempts:
                    break
                retry_after = hop_response.headers.get("retry-after", "")
                time.sleep(min(float(retry_after) if retry_after.isdigit() else 0.6 * (2 ** attempt), 8.0))
            if hop_response is None:
                break
            attempts_used = hop_response.attempts
            response = hop_response
            response.url = url
            response.final_url = current
            response.attempts = attempts_used
            if response.status in {301, 302, 303, 307, 308} and response.headers.get("location"):
                chain.append(current)
                current = urllib.parse.urljoin(current, response.headers["location"])
                continue
            break
        else:
            response.error = "too many redirects"
            response.status = 0
        response.redirect_chain = chain
        return response

    def resolves(self, host: str) -> bool:
        with self.budget._lock:  # noqa: SLF001 - simple shared counter
            self.budget.dns_lookups += 1
        return is_public_host(host)[0]


class SnapshotFetcher(Fetcher):
    """Replays saved responses so refresh behaviour can be tested without the network."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        super().__init__(Budget(10**9))
        self.snapshot = snapshot
        self.requests: list[str] = []

    def get(self, url: str, **_: Any) -> Response:  # type: ignore[override]
        self.requests.append(url)
        saved = (self.snapshot.get("responses") or {}).get(url)
        retrieved = self.snapshot.get("retrieved_at") or utc_now()
        if saved is None:
            return Response(url=url, final_url=url, status=404, error="HTTP 404", retrieved_at=retrieved)
        body = saved.get("body")
        raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return Response(url=url, final_url=url, status=int(saved.get("status", 200)), raw=raw,
                        headers={"content-type": saved.get("content_type", "application/json")}, retrieved_at=retrieved)

    def robots_allows(self, url: str, source_class: str) -> bool | None:  # type: ignore[override]
        return True

    def resolves(self, host: str) -> bool:  # type: ignore[override]
        return any(host in key for key in (self.snapshot.get("responses") or {}))
