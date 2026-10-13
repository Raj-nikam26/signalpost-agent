"""Small local server: the profile site plus live routes.

GET  /                                   the site
GET  /companies/<organisation_number>    profile page (redirects into the site)
GET  /api/companies/<organisation_number> stored envelope as JSON
GET  /research?organisation_number=...   research a company now (also accepts POST with a JSON body)
GET  /refresh?organisation_number=...    re-run a stored company and report what changed
"""
from __future__ import annotations

import json
import tempfile
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .http import Budget, Fetcher
from .pipeline import research_company
from .refresh import load_envelopes, save_state
from .site import build_site
from .sources.external import wikidata_lookup
from .util import clean_org_number, valid_org_number


def serve(args: Any) -> None:
    envelopes = load_envelopes(args.envelopes)
    state_latest = load_envelopes(Path(args.state_dir) / "latest.jsonl")
    for org, envelope in state_latest.items():
        envelopes.setdefault(org, envelope)
    site_dir = Path(tempfile.mkdtemp(prefix="signalpost-site-"))
    lock = threading.Lock()
    build_site(list(envelopes.values()), site_dir)

    def research(org: str) -> dict[str, Any]:
        fetcher = Fetcher(Budget(40))
        run_id = datetime.now(timezone.utc).strftime("live-%Y%m%dT%H%M%SZ")
        wikidata = wikidata_lookup(fetcher, [org]).get(org)
        envelope = research_company(org, fetcher, run_id=run_id, previous=envelopes.get(org), wikidata=wikidata, options={"account_history": True})
        with lock:
            if envelope["status"] != "failed" or org not in envelopes:
                envelopes[org] = envelope
            save_state(args.state_dir, run_id, [envelope])
            build_site(list(envelopes.values()), site_dir)
        return envelope

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *handler_args: Any, **handler_kwargs: Any) -> None:
            super().__init__(*handler_args, directory=str(site_dir), **handler_kwargs)

        def log_message(self, *_: Any) -> None:
            return

        def _json(self, status: int, body: Any) -> None:
            raw = json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _live(self, org_raw: str, as_json: bool) -> None:
            org = clean_org_number(org_raw)
            if not valid_org_number(org):
                self._json(400, {"error": "organisation_number must be a valid 9-digit Norwegian organisation number", "status": "not_applicable"})
                return
            envelope = research(org)
            if as_json:
                self._json(200, {key: value for key, value in envelope.items() if key != "profile"})
            else:
                self.send_response(303)
                self.send_header("Location", f"/#/companies/{org}")
                self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            parts = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parts.query)
            path = parts.path.rstrip("/")
            wants_json = "application/json" in self.headers.get("Accept", "") or query.get("format") == ["json"]
            if path.startswith("/companies/"):
                self.send_response(302)
                self.send_header("Location", "/#/companies/" + clean_org_number(path.rsplit("/", 1)[-1]))
                self.end_headers()
            elif path.startswith("/api/companies/"):
                org = clean_org_number(path.rsplit("/", 1)[-1])
                envelope = envelopes.get(org)
                self._json(200 if envelope else 404, {key: value for key, value in envelope.items() if key != "profile"} if envelope else {"error": "no stored profile", "organisation_number": org})
            elif path in {"/research", "/refresh"}:
                org = (query.get("organisation_number") or query.get("org") or [""])[0]
                if not org:
                    self._json(400, {"error": "pass ?organisation_number=<9 digits>"})
                else:
                    self._live(org, wants_json)
            else:
                super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            if urllib.parse.urlsplit(self.path).path.rstrip("/") not in {"/research", "/refresh"}:
                self._json(404, {"error": "unknown route"})
                return
            length = min(int(self.headers.get("Content-Length") or 0), 10_000)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            self._live(str(body.get("organisation_number") or ""), True)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Signalpost is serving {len(envelopes)} profiles at http://{args.host}:{args.port}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
