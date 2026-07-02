"""Compact MCP catalog and deterministic lazy-discovery helpers."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence


_WORD_RE = re.compile(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", re.IGNORECASE)
_STOP_WORDS = {
    "the", "and", "for", "with", "from", "into", "this", "that", "tool",
    "use", "using", "get", "list", "create", "return", "returns",
}


def _function(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    value = schema.get("function")
    return value if isinstance(value, Mapping) else {}


def _schema_hash(schema: Mapping[str, Any]) -> str:
    payload = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _summary(description: str) -> str:
    text = " ".join(str(description or "").split())
    first = re.split(r"(?<=[.!?。！？])\s+", text, maxsplit=1)[0]
    return first[:240]


def _keywords(name: str, server: str, description: str) -> list[str]:
    original_name = name.split("__", 2)[-1]
    values = [server, original_name, *re.split(r"[_\-\s]+", original_name)]
    values.extend(_WORD_RE.findall(description))
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip().lower()
        if len(item) < 2 or item in _STOP_WORDS or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= 16:
            break
    return result


def build_mcp_catalog(
    schemas: Sequence[Mapping[str, Any]],
    *,
    keyword_overrides: Mapping[str, Iterable[str]] | None = None,
) -> list[dict[str, Any]]:
    """Build schema-free catalog entries from currently connected MCP tools."""
    overrides = keyword_overrides or {}
    catalog: list[dict[str, Any]] = []
    for schema in schemas:
        function = _function(schema)
        name = str(function.get("name") or "")
        if not name.startswith("mcp__"):
            continue
        parts = name.split("__", 2)
        server = parts[1] if len(parts) == 3 else ""
        description = str(function.get("description") or "")
        keywords = _keywords(name, server, description)
        for value in overrides.get(name, ()):
            item = str(value or "").strip().lower()
            if item and item not in keywords:
                keywords.append(item)
        catalog.append({
            "name": name,
            "server": server,
            "summary": _summary(description),
            "keywords": keywords[:24],
            "schema_hash": _schema_hash(schema),
        })
    return sorted(catalog, key=lambda item: item["name"])


def mcp_catalog_version(catalog: Sequence[Mapping[str, Any]]) -> str:
    compact = [
        (str(item.get("name") or ""), str(item.get("schema_hash") or ""))
        for item in catalog
    ]
    payload = json.dumps(sorted(compact), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def search_mcp_catalog(
    catalog: Sequence[Mapping[str, Any]],
    query: str,
    *,
    server: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return at most five catalog candidates without exposing parameter schemas."""
    query_raw = str(query or "").strip().lower()
    query_text = " ".join(re.split(r"[_\-\s]+", query_raw))
    query_tokens = {
        token.lower() for token in _WORD_RE.findall(query_text)
        if token.lower() not in _STOP_WORDS
    }
    server_filter = str(server or "").strip().lower()
    scored: list[tuple[int, str, Mapping[str, Any]]] = []
    for item in catalog:
        item_server = str(item.get("server") or "")
        if server_filter and item_server.lower() != server_filter:
            continue
        name = str(item.get("name") or "")
        normalized_name = " ".join(re.split(r"[_\-\s]+", name.lower()))
        summary = str(item.get("summary") or "").lower()
        keywords = {str(value).lower() for value in item.get("keywords") or []}
        score = 0
        if name.lower() in query_raw:
            score += 100
        original_name = name.split("__", 2)[-1].lower()
        if original_name and original_name in query_raw:
            score += 40
        if query_text and query_text in f"{normalized_name} {summary}":
            score += 20
        overlap = query_tokens.intersection(keywords)
        score += len(overlap) * 8
        for token in query_tokens:
            if len(token) >= 3 and token in normalized_name:
                score += 6
            elif len(token) >= 4 and token in summary:
                score += 2
        # One generic word from a long description is too weak for automatic
        # pre-discovery. Exact names and at least two strong keyword signals
        # still pass comfortably.
        if score >= 12:
            scored.append((score, name, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    bounded = max(1, min(5, int(limit or 5)))
    return [
        {
            "name": str(item.get("name") or ""),
            "server": str(item.get("server") or ""),
            "summary": str(item.get("summary") or ""),
            "schema_hash": str(item.get("schema_hash") or ""),
        }
        for _score, _name, item in scored[:bounded]
    ]


def select_mcp_schemas(
    schemas: Sequence[Mapping[str, Any]],
    names: Iterable[str],
    *,
    limit: int = 5,
) -> list[dict]:
    """Select connected full schemas in caller-provided recency order."""
    by_name = {
        str(_function(schema).get("name") or ""): dict(schema)
        for schema in schemas
    }
    ordered: list[str] = []
    for name in names:
        value = str(name or "")
        if value in by_name and value not in ordered:
            ordered.append(value)
    return [by_name[name] for name in ordered[-max(1, min(5, limit)):]]
