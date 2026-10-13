"""Single-pass HTML reader built on the standard library parser.

Collects what the agent needs from a company page: title, meta tags, links, JSON-LD, dated items and visible text.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

SKIP_TEXT = {"script", "style", "noscript", "svg", "template", "iframe", "select", "option"}
BLOCK_TAGS = {"article", "li"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@dataclass
class Link:
    href: str
    text: str = ""
    rel: str = ""
    region: str = ""  # header / nav / footer / main, when the page marks it


@dataclass
class DatedItem:
    title: str = ""
    href: str = ""
    date: str = ""


@dataclass
class Page:
    title: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    links: list[Link] = field(default_factory=list)
    feeds: list[str] = field(default_factory=list)
    canonical: str = ""
    json_ld: list[Any] = field(default_factory=list)
    items: list[DatedItem] = field(default_factory=list)
    text: str = ""
    lang: str = ""
    headings: list[str] = field(default_factory=list)

    def ld_nodes(self) -> list[dict[str, Any]]:
        """Flatten JSON-LD, including @graph containers, into a list of typed nodes."""
        nodes: list[dict[str, Any]] = []

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, dict):
                if "@type" in value:
                    nodes.append(value)
                for key in ("@graph", "mainEntity", "itemListElement", "item"):
                    if key in value:
                        visit(value[key])

        visit(self.json_ld)
        return nodes


def ld_types(node: dict[str, Any]) -> set[str]:
    kind = node.get("@type")
    return {str(item) for item in kind} if isinstance(kind, list) else {str(kind)}


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self._skip = 0
        self._stack: list[str] = []
        self._text: list[str] = []
        self._in_title = False
        self._title: list[str] = []
        self._ld: list[str] | None = None
        self._link: Link | None = None
        self._link_text: list[str] = []
        self._heading: list[str] | None = None
        self._regions: list[str] = []
        self._blocks: list[dict[str, Any]] = []
        self._time_open = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key: (value or "") for key, value in attrs}
        if tag == "html" and data.get("lang"):
            self.page.lang = data["lang"]
        if tag == "script" and "ld+json" in data.get("type", "").lower():
            self._ld = []
            return
        if tag in SKIP_TEXT:
            self._skip += 1
            return
        if tag == "title" and not self.page.title:
            self._in_title = True
        elif tag == "meta":
            name = (data.get("name") or data.get("property") or data.get("itemprop") or "").lower()
            if name and data.get("content") and name not in self.page.meta:
                self.page.meta[name] = " ".join(data["content"].split())
        elif tag == "link":
            rel = data.get("rel", "").lower()
            if "canonical" in rel and data.get("href"):
                self.page.canonical = data["href"]
            if "alternate" in rel and data.get("href") and re.search(r"rss|atom", data.get("type", ""), re.I):
                self.page.feeds.append(data["href"])
        elif tag == "a" and data.get("href"):
            self._link = Link(href=data["href"].strip(), rel=data.get("rel", ""), region=self._regions[-1] if self._regions else "")
            self._link_text = [data.get("title") or data.get("aria-label") or ""]
        elif tag in {"h1", "h2", "h3"}:
            self._heading = []
        elif tag == "time":
            self._time_open = True
            if self._blocks and data.get("datetime") and not self._blocks[-1]["date"]:
                self._blocks[-1]["date"] = data["datetime"]
        if tag in {"header", "nav", "footer", "main"}:
            self._regions.append(tag)
        if tag in BLOCK_TAGS:
            self._blocks.append({"tag": tag, "date": "", "title": "", "href": "", "text": []})
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "article", "footer", "td"}:
            self._text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._ld is not None:
            raw = "".join(self._ld).strip()
            self._ld = None
            try:
                self.page.json_ld.append(json.loads(raw, strict=False))
            except ValueError:
                pass
            return
        if tag in SKIP_TEXT:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title" and self._in_title:
            self._in_title = False
            self.page.title = " ".join("".join(self._title).split())
        elif tag == "a" and self._link is not None:
            self._link.text = " ".join(" ".join(self._link_text).split())[:200]
            self.page.links.append(self._link)
            if self._blocks and not self._blocks[-1]["href"] and len(self._link.text) >= 12:
                self._blocks[-1]["href"] = self._link.href
                self._blocks[-1]["title"] = self._blocks[-1]["title"] or self._link.text
            self._link = None
        elif tag in {"h1", "h2", "h3"} and self._heading is not None:
            heading = " ".join("".join(self._heading).split())
            if heading:
                self.page.headings.append(heading[:200])
                if self._blocks:
                    self._blocks[-1]["title"] = heading[:200]
            self._heading = None
        elif tag == "time":
            self._time_open = False
        if tag in {"header", "nav", "footer", "main"} and self._regions and self._regions[-1] == tag:
            self._regions.pop()
        if tag in BLOCK_TAGS and self._blocks:
            for index in range(len(self._blocks) - 1, -1, -1):
                if self._blocks[index]["tag"] == tag:
                    block = self._blocks.pop(index)
                    if not block["date"]:
                        match = re.search(r"\b(\d{1,2}[./]\d{1,2}[./]20\d{2}|20\d{2}-\d{2}-\d{2})\b", " ".join(block["text"]))
                        if match:
                            block["date"] = match.group(1)
                    if block["date"] and block["href"] and block["title"]:
                        self.page.items.append(DatedItem(title=block["title"], href=block["href"], date=block["date"]))
                    break

    def handle_data(self, data: str) -> None:
        if self._ld is not None:
            self._ld.append(data)
            return
        if self._in_title:
            self._title.append(data)
            return
        if self._skip:
            return
        self._text.append(data)
        if self._link is not None:
            self._link_text.append(data)
        if self._heading is not None:
            self._heading.append(data)
        if self._blocks and len(self._blocks[-1]["text"]) < 40:
            self._blocks[-1]["text"].append(data)


def parse_html(html: str) -> Page:
    reader = _Reader()
    try:
        reader.feed(html)
        reader.close()
    except Exception:  # noqa: BLE001 - malformed markup still yields whatever was read so far
        pass
    page = reader.page
    lines = [" ".join(line.split()) for line in "".join(reader._text).split("\n")]  # noqa: SLF001
    page.text = "\n".join(line for line in lines if line)
    return page
