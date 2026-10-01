"""Content sources (Phase 10): text from documents, subtitles and web pages, fetched safely.

A *source* is the structured value source nodes produce and text nodes read
(``SOURCE`` port type)::

    {"source_type": "text|url|document|video|audio", "title": str, "text": str,
     "language": str | None, "asset_id": str | None, "source_url": str | None,
     "segments": [{"start": s, "end": s, "text": str}] | None, "metadata": {...}}

No local path is ever part of it.

Web pages are fetched with ``fetch_page``:

* HTTPS only, on port 443, with no user info.
* Every address the host resolves to must be public. Loopback, private, link-local
  (including cloud metadata services), multicast, reserved and unspecified
  addresses are refused, for IPv4 and IPv6.
* The connection goes to the checked IP, with the TLS certificate verified for the
  host name, so the name cannot be re-resolved to another address (DNS rebinding).
* Redirects are followed by hand (at most 3), each one checked again.
* Responses are HTML or plain text of at most 2 MB, read under a timeout.
* No JavaScript runs, no browser is used, and paywalls or bot checks are not bypassed.
"""
from dataclasses import dataclass
from html.parser import HTMLParser
import ipaddress
import re
import socket
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import httpx

MAX_SOURCE_CHARS = 60_000
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
USER_AGENT = "ReelForgeStudio/1.0 (+source import; no JavaScript)"
DOCUMENT_TYPES = {"text/plain": "document", "text/markdown": "document", "application/x-subrip": "subtitles",
                  "text/vtt": "subtitles"}
PAGE_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
# Named hosts that must never be fetched, whatever they resolve to.
_BLOCKED_NAMES = {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal", "instance-data"}
_METADATA_IPS = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254"),
                 ipaddress.ip_address("100.100.100.200")}
_TIME = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")


class SourceError(ValueError):
    """A source that cannot be used, with a stable ``code`` (blocked_url, too_large, not_text…)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def make_source(source_type: str, *, title: str = "", text: str = "", language: str | None = None,
                asset_id: str | None = None, source_url: str | None = None, segments: list | None = None,
                metadata: dict | None = None) -> dict[str, Any]:
    text = (text or "").strip()
    return {"source_type": source_type, "title": " ".join((title or "").split())[:300],
            "text": text[:MAX_SOURCE_CHARS], "truncated": len(text) > MAX_SOURCE_CHARS, "language": language,
            "asset_id": asset_id, "source_url": source_url, "segments": segments, "metadata": metadata or {}}


# Documents and subtitles ---------------------------------------------------------

def decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise SourceError("not_text", "The file is not a text document")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceError("not_text", "Text documents must be UTF-8") from exc


def _seconds(match: re.Match) -> float:
    hours, minutes, seconds, fraction = match.groups()
    return round(int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction.ljust(3, "0")) / 1000, 3)


def parse_subtitles(text: str) -> list[dict[str, Any]]:
    """Cues of an SRT or WebVTT file as ``{start, end, text}`` segments, in order."""
    segments = []
    for block in re.split(r"\r?\n\s*\r?\n", text.replace("﻿", "")):
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        timing = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        start, _, end = lines[timing].partition("-->")
        start_match, end_match = _TIME.search(start), _TIME.search(end)
        if not start_match or not end_match:
            continue
        cue = " ".join(re.sub(r"<[^>]+>", "", line) for line in lines[timing + 1:]).strip()
        if cue:
            segments.append({"start": _seconds(start_match), "end": _seconds(end_match), "text": cue})
    return segments


def document_source(data: bytes, content_type: str, *, title: str, asset_id: str) -> dict[str, Any]:
    if len(data) > MAX_DOCUMENT_BYTES:
        raise SourceError("too_large", "The document is larger than 2 MB")
    text = decode_text(data)
    if DOCUMENT_TYPES.get(content_type) == "subtitles":
        segments = parse_subtitles(text)
        if not segments:
            raise SourceError("not_text", "No subtitle cues were found")
        return make_source("document", title=title, text=" ".join(segment["text"] for segment in segments),
                           asset_id=asset_id, segments=segments, metadata={"format": content_type})
    return make_source("document", title=title, text=text, asset_id=asset_id, metadata={"format": content_type})


# Web pages ---------------------------------------------------------------------

class _PageText(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe", "template",
            "button", "select", "canvas", "object"}
    BLOCK = {"p", "div", "section", "article", "main", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
             "blockquote", "pre", "tr", "table", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.main = 0
        self.in_title = False
        self.title = ""
        self.og_title = ""
        self.lang = None
        self.body: list[str] = []
        self.article: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "html" and attributes.get("lang"):
            self.lang = attributes["lang"][:20]
        if tag == "meta" and attributes.get("property") == "og:title" and attributes.get("content"):
            self.og_title = attributes["content"]
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ("article", "main"):
            self.main += 1
        if tag == "title":
            self.in_title = True
        if tag in self.BLOCK:
            self._add("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in ("article", "main") and self.main:
            self.main -= 1
        if tag == "title":
            self.in_title = False
        if tag in self.BLOCK:
            self._add("\n")

    def _add(self, text):
        if self.skip:
            return
        self.body.append(text)
        if self.main:
            self.article.append(text)

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self._add(data)


def _paragraphs(chunks: list[str]) -> str:
    lines = (" ".join(line.split()) for line in "".join(chunks).split("\n"))
    return "\n\n".join(line for line in lines if len(line) > 1)


def extract_page(html: str) -> tuple[str, str, str | None]:
    """(title, main text, language) of an HTML page; the article or main element is preferred."""
    parser = _PageText()
    parser.feed(html)
    parser.close()
    article = _paragraphs(parser.article)
    text = article if len(article) >= 200 else _paragraphs(parser.body)
    title = " ".join((parser.og_title or parser.title).split())
    return title, text, parser.lang


def _blocked(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (address in _METADATA_IPS or not address.is_global or address.is_private or address.is_loopback
            or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified)


def check_url(url: str) -> tuple[str, str]:
    """(host, path with query) of a URL that may be fetched, else ``SourceError('blocked_url')``."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise SourceError("invalid_url", "The URL is not valid") from exc
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password or port not in (None, 443):
        raise SourceError("blocked_url", "Only public https:// URLs on the standard port can be imported")
    if host in _BLOCKED_NAMES or host.endswith((".localhost", ".local", ".internal")) or len(url) > 2048:
        raise SourceError("blocked_url", "This host cannot be imported")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and _blocked(literal):
        raise SourceError("blocked_url", "Private and local addresses cannot be imported")
    path = parts.path or "/"
    return host, path + (f"?{parts.query}" if parts.query else "")


def resolve_public(host: str, resolver: Callable = socket.getaddrinfo) -> str:
    """One public IP for ``host``; every address it resolves to must be public."""
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise SourceError("unreachable", "The host name could not be resolved") from exc
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0].split("%")[0]))
        except (ValueError, IndexError, TypeError):
            continue
    if not addresses or any(_blocked(address) for address in addresses):
        raise SourceError("blocked_url", "The host resolves to a private or local address")
    return str(addresses[0])


@dataclass(frozen=True)
class Page:
    url: str
    content_type: str
    text: str


def fetch_page(url: str, *, client: httpx.Client, resolver: Callable = socket.getaddrinfo) -> Page:
    """Download one public web page safely (see the module docstring)."""
    for _ in range(MAX_REDIRECTS + 1):
        host, path = check_url(url)
        address = resolve_public(host, resolver)
        target = f"https://[{address}]{path}" if ":" in address else f"https://{address}{path}"
        try:
            with client.stream("GET", target, headers={"Host": host, "User-Agent": USER_AGENT,
                                                       "Accept": "text/html, text/plain;q=0.8"},
                               extensions={"sni_hostname": host}, follow_redirects=False, timeout=TIMEOUT) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise SourceError("fetch_failed", "The page redirected without a location")
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise SourceError("fetch_failed", f"The page answered HTTP {response.status_code}")
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if content_type not in PAGE_TYPES:
                    raise SourceError("unsupported_type", "Only HTML and plain-text pages can be imported")
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > MAX_PAGE_BYTES:
                    raise SourceError("too_large", "The page is larger than 2 MB")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_PAGE_BYTES:
                        raise SourceError("too_large", "The page is larger than 2 MB")
                charset = response.charset_encoding or "utf-8"
        except httpx.TimeoutException as exc:
            raise SourceError("timeout", "The page did not answer in time") from exc
        except httpx.RequestError as exc:
            raise SourceError("unreachable", "The page could not be reached") from exc
        try:
            text = bytes(body).decode(charset, errors="replace")
        except LookupError:
            text = bytes(body).decode("utf-8", errors="replace")
        return Page(url, content_type, text)
    raise SourceError("fetch_failed", "The page redirected too many times")


def page_source(page: Page) -> dict[str, Any]:
    if page.content_type == "text/plain":
        title, text, language = "", page.text, None
    else:
        title, text, language = extract_page(page.text)
    if len(text.strip()) < 50:
        raise SourceError("no_text", "No readable text was found on the page (it may need JavaScript or a login)")
    return make_source("url", title=title or urlsplit(page.url).hostname or "", text=text, language=language,
                       source_url=page.url)
