from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from .config import SourceConfig
from .profiles import get_profile


class RequestPacer:
    """Space SEC requests across all fetch and document-reading threads."""
    def __init__(self, interval_seconds: float = 0.25):
        self.interval_seconds = interval_seconds
        self._next_request = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delay = self._next_request - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._next_request = time.monotonic() + self.interval_seconds


SEC_REQUEST_PACER = RequestPacer()


def _pace_sec_request(url: str) -> None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host == "sec.gov" or host.endswith(".sec.gov"):
        SEC_REQUEST_PACER.wait()


@dataclass(frozen=True)
class FetchResult:
    status: int | None
    text: str
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


class LinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self._href: str | None = None
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr = dict(attrs)
        href = attr.get("href")
        if href:
            self._href = urllib.parse.urljoin(self.base_url, href)
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            title = " ".join("".join(self._text).split())
            if title:
                self.links.append((title, self._href))
            self._href = None
            self._text = []


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)


def fetch_url(source: SourceConfig, user_agent: str, etag: str | None = None, last_modified: str | None = None) -> FetchResult:
    headers = {"User-Agent": user_agent}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    req = urllib.request.Request(source.url, headers=headers)
    _pace_sec_request(source.url)
    try:
        with urllib.request.urlopen(req, timeout=source.timeout_seconds) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return FetchResult(
                status=resp.status,
                text=raw.decode(charset, errors="replace"),
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FetchResult(status=304, text="", etag=etag, last_modified=last_modified, not_modified=True)
        raise


def fetch_text_url(url: str, user_agent: str, timeout_seconds: int = 12, max_bytes: int = 750_000) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    _pace_sec_request(url)
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        raw = resp.read(max_bytes)
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def html_to_text(raw: str) -> str:
    parser = TextExtractor()
    parser.feed(raw)
    text = " ".join(parser.parts)
    if not text:
        text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def sec_filing_text(url: str, user_agent: str, timeout_seconds: int = 12) -> tuple[str, dict[str, Any]]:
    primary_html = fetch_text_url(url, user_agent=user_agent, timeout_seconds=timeout_seconds)
    sections = [("primary", url, html_to_text(primary_html))]
    parser = LinkParser(url)
    parser.feed(primary_html)

    exhibit_links: list[tuple[str, str]] = []
    for title, href in parser.links:
        label = f"{title} {href}".lower()
        if not href.lower().endswith((".htm", ".html", ".xml", ".txt")):
            continue
        if href == url:
            continue
        if any(marker in label for marker in ("ex-99", "ex99", "exhibit 99", "ex-10", "ex10", "exhibit 10")):
            exhibit_links.append((title, href))

    seen: set[str] = set()
    for title, href in exhibit_links[:5]:
        if href in seen:
            continue
        seen.add(href)
        try:
            exhibit_html = fetch_text_url(href, user_agent=user_agent, timeout_seconds=timeout_seconds)
            sections.append((title or "exhibit", href, html_to_text(exhibit_html)))
        except Exception:
            continue

    combined_parts = []
    fetched = []
    for label, href, text in sections:
        if not text:
            continue
        fetched.append({"label": label, "url": href, "chars": len(text)})
        combined_parts.append(f"--- {label} ({href}) ---\n{text}")
    return "\n\n".join(combined_parts), {"sections": fetched}


def item_hash(source_id: str, title: str, url: str | None) -> str:
    base = f"{source_id}|{url or ''}|{title}".encode("utf-8", errors="ignore")
    return hashlib.sha256(base).hexdigest()


def _xml_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(node):
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names and child.text:
            return html.unescape(child.text.strip())
    return ""


def _atom_link(node: ET.Element) -> str:
    for child in list(node):
        if child.tag.rsplit("}", 1)[-1].lower() != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href
        if child.text:
            return child.text.strip()
    return ""


def parse_rss(source: SourceConfig, text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(text)
    nodes = root.findall(".//item")
    if not nodes:
        nodes = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1].lower() == "entry"]

    items = []
    for node in nodes[:80]:
        title = _xml_text(node, ("title",)) or "(untitled)"
        link = _xml_text(node, ("link",)) or _atom_link(node)
        summary = _xml_text(node, ("description", "summary", "content", "encoded"))
        published = _xml_text(node, ("pubdate", "published", "updated"))
        items.append(
            {
                "source_id": source.id,
                "ticker": source.ticker,
                "title": title,
                "url": link,
                "published_at": published,
                "summary": re.sub(r"<[^>]+>", " ", summary).strip(),
                "raw_text": summary,
                "metadata": {"source_type": source.type},
                "item_hash": item_hash(source.id, title, link),
            }
        )
    return items


def parse_html_watch(source: SourceConfig, text: str, profile: str = "general") -> list[dict[str, Any]]:
    rules = get_profile(profile)
    parser = LinkParser(source.url)
    parser.feed(text)
    items = []
    seen: set[str] = set()
    for title, url in parser.links:
        if source.link_url_contains and source.link_url_contains not in url:
            continue
        lowered = title.lower()
        if not source.link_url_contains and not any(k in lowered for k in rules.html_terms):
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append(
            {
                "source_id": source.id,
                "ticker": source.ticker,
                "title": html.unescape(title),
                "url": url,
                "published_at": None,
                "summary": "",
                "raw_text": "",
                "metadata": {"source_type": source.type},
                "item_hash": item_hash(source.id, title, url),
            }
        )
    return items


def parse_sec_submissions(source: SourceConfig, text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    cik = str(data.get("cik", "")).lstrip("0") or "0"
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    items = []
    for idx, form in enumerate(forms[:80]):
        accession = accessions[idx]
        accession_path = accession.replace("-", "")
        doc = docs[idx]
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_path}/{doc}"
        description = descriptions[idx] if idx < len(descriptions) else ""
        title = f"{source.ticker or data.get('name', 'Company')} filed {form}: {description or doc}"
        items.append(
            {
                "source_id": source.id,
                "ticker": source.ticker,
                "title": title,
                "url": url,
                "published_at": dates[idx] if idx < len(dates) else None,
                "summary": description,
                "raw_text": json.dumps({"form": form, "accession": accession, "document": doc, "description": description}),
                "metadata": {"source_type": source.type, "form": form, "accession": accession},
                "item_hash": item_hash(source.id, title, url),
            }
        )
    return items


def parse_source(source: SourceConfig, text: str, profile: str = "general") -> list[dict[str, Any]]:
    if source.type in {"rss", "atom"}:
        return parse_rss(source, text)
    if source.type == "html_watch":
        return parse_html_watch(source, text, profile=profile)
    if source.type == "sec_submissions":
        return parse_sec_submissions(source, text)
    raise ValueError(f"unsupported source type: {source.type}")
