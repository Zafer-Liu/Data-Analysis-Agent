"""Bounded webpage reading tool for Agent-side configuration workflows."""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any


_MAX_READ_BYTES = 768_000


class _PageTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = True
        if lower in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth += 1
        if lower in {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = False
        if lower in {"script", "style", "noscript", "svg", "canvas"} and self._skip_depth:
            self._skip_depth -= 1
        if lower in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        text = " ".join(str(data or "").split())
        if not text:
            return
        if self._in_title:
            self.title = (self.title + " " + text).strip()
        if not self._skip_depth:
            self._chunks.append(text)
            self._chunks.append(" ")

    def text(self) -> str:
        lines: list[str] = []
        for raw in "".join(self._chunks).splitlines():
            line = " ".join(raw.split())
            if line:
                lines.append(line)
        return "\n".join(lines)


def browse_webpage(url: str, *, max_chars: int = 12000, timeout: int = 20) -> str:
    """Fetch an HTTP(S) page and return bounded readable text."""
    clean_url = _validate_url(url)
    max_chars = max(1000, min(int(max_chars or 12000), 30000))
    request = urllib.request.Request(
        clean_url,
        headers={
            "User-Agent": "BusinessAnalyticsAgent/1.0 (+hooks-auto-config)",
            "Accept": "text/html,application/json,text/plain,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=max(1, min(int(timeout or 20), 60))) as response:
        content_type = str(response.headers.get("Content-Type") or "")
        raw = response.read(_MAX_READ_BYTES + 1)
        if len(raw) > _MAX_READ_BYTES:
            raw = raw[:_MAX_READ_BYTES]
        charset = response.headers.get_content_charset() or "utf-8"
        text = raw.decode(charset, errors="replace")
        status = getattr(response, "status", 200)

    if "json" in content_type.lower():
        body = _format_json_text(text)
        title = "JSON response"
    elif "html" in content_type.lower() or "<html" in text[:500].lower():
        parser = _PageTextExtractor()
        parser.feed(text)
        title = parser.title or clean_url
        body = parser.text()
    else:
        title = clean_url
        body = text
    body = body.strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n...[truncated]"
    return "\n".join([
        f"URL: {clean_url}",
        f"HTTP status: {status}",
        f"Content-Type: {content_type or 'unknown'}",
        f"Title: {title}",
        "",
        body or "(no readable text extracted)",
    ])


def _format_json_text(text: str) -> str:
    try:
        parsed: Any = json.loads(text)
    except Exception:
        return text
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _validate_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("url is required")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("only absolute http/https URLs are supported")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL host is required")
    _reject_private_host(host)
    return urllib.parse.urlunparse(parsed)


def _reject_private_host(host: str) -> None:
    lowered = host.lower().strip(".")
    if lowered in {"localhost", "127.0.0.1", "::1"} or lowered.endswith(".localhost"):
        raise ValueError("local/private network URLs are not allowed")
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(host, None):
            addresses.add(item[4][0])
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve URL host: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("local/private network URLs are not allowed")
