"""Compact skill catalog with neural embedding retrieval.

Uses the same neural_embedder as KnowledgeBase for semantic matching,
combined with a lightweight lexical score and RRF fusion. Falls back to
hash-projection if neural/cloud models are unavailable.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from typing import Any, Mapping, Sequence

from infrastructure.paths import data_path

from Function.Knowledge.neural_embedder import (
    embed_batch as _embed_batch,
    embed_query as _embed_query,
    get_embedding_signature as _embedding_signature,
    cosine as _cosine,
    is_neural as _is_neural,
)

_SKILL_CACHE_PATH = data_path("outputs", "cache", "skill_embeddings.json")
_SKILL_CACHE_LOCK = threading.Lock()


def _lexical_tokens(text: str) -> list[str]:
    # Skill matching already uses embeddings as the primary signal. Keep the
    # lexical channel cheap so ordinary chat does not initialize jieba.
    tokens = re.findall(r"[a-z0-9]{2,}", (text or "").lower())
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", text or "")
    tokens += cjk_runs
    for run in cjk_runs:
        if len(run) > 1:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens
def _lexical_score(query: str, text: str) -> float:
    """Lightweight lexical overlap score."""
    q = (query or "").lower().strip()
    t = (text or "").lower()
    if not q or not t:
        return 0.0
    score = 0.0
    if q in t:
        score += 1.2
    q_tokens = set(_lexical_tokens(q))
    t_tokens = set(_lexical_tokens(t))
    if q_tokens:
        overlap = q_tokens & t_tokens
        score += 0.8 * len(overlap) / max(1, len(q_tokens))
    return round(score, 4)


def _name_bonus(name: str, query: str) -> float:
    name_lower = name.lower()
    query_lower = query.lower()
    score = 0.0
    if name_lower in query_lower:
        score += 2.0
    for part in re.split(r"[_\-\s]+", name_lower):
        if part and len(part) >= 3 and part in query_lower:
            score += 0.8
            break
    return score


def _load_embedding_cache(signature: str) -> dict[str, list[float]]:
    try:
        payload = json.loads(_SKILL_CACHE_PATH.read_text(encoding="utf-8"))
        if payload.get("signature") != signature:
            return {}
        entries = payload.get("entries")
        return entries if isinstance(entries, dict) else {}
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _save_embedding_cache(signature: str, entries: dict[str, list[float]]) -> None:
    _SKILL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = _SKILL_CACHE_PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"signature": signature, "entries": entries}, separators=(",", ":")),
        encoding="utf-8",
    )
    temp.replace(_SKILL_CACHE_PATH)


def build_skill_catalog(
    skills: Sequence[Mapping[str, Any]],
    *,
    stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Build a catalog while embedding only changed Skill descriptions."""
    entries = []
    for skill in skills:
        name = str(skill.get("name") or "")
        if not name:
            continue
        description = str(skill.get("description") or "")
        tags = [str(tag) for tag in (skill.get("tags") or []) if str(tag)]
        text = f"{name} {description} {' '.join(tags)}"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        entries.append({
            "name": name,
            "description": description[:200],
            "tags": tags,
            "text": text,
            "content_hash": content_hash,
        })

    signature = _embedding_signature()
    with _SKILL_CACHE_LOCK:
        cached = _load_embedding_cache(signature)
        stale = [entry for entry in entries if entry["content_hash"] not in cached]
        if stale:
            vectors = _embed_batch([entry["text"] for entry in stale])
            for entry, vector in zip(stale, vectors):
                cached[entry["content_hash"]] = vector
        current_cache = {
            entry["content_hash"]: cached[entry["content_hash"]]
            for entry in entries
            if entry["content_hash"] in cached
        }
        if stale or set(current_cache) != set(cached):
            _save_embedding_cache(signature, current_cache)

    if stats is not None:
        stats["rebuilt"] = len(stale)
        stats["total"] = len(entries)
    catalog = []
    for entry in entries:
        vector = current_cache.get(entry.pop("content_hash"))
        if vector:
            entry["embedding"] = vector
            catalog.append(entry)
    return sorted(catalog, key=lambda item: item["name"])


def rebuild_skill_embeddings(skills: Sequence[Mapping[str, Any]]) -> int:
    stats: dict[str, int] = {}
    build_skill_catalog(skills, stats=stats)
    return stats.get("rebuilt", 0)

def _rrf_fuse(
    vec_list: list[dict],
    lex_list: list[dict],
    name_list: list[dict],
    k: int = 60,
    limit: int = 5,
) -> list[dict]:
    """Weighted Reciprocal Rank Fusion.

    Vector channel gets 3x weight (neural embedding is the primary signal),
    name match 2x (exact name hit is strong), lexical 1x (supplementary).
    """
    weights = [3.0, 1.0, 2.0]  # vec, lex, name
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for ranked, w in zip((vec_list, lex_list, name_list), weights):
        for rank, item in enumerate(ranked):
            name = item.get("name", "")
            if not name:
                continue
            scores[name] = scores.get(name, 0.0) + w / (k + rank)
            items[name] = item
    fused = sorted(scores.items(), key=lambda x: -x[1])
    result = []
    for name, score in fused[:limit]:
        item = dict(items[name])
        item["rrf_score"] = round(score, 6)
        result.append(item)
    return result


def search_skill_catalog(
    catalog: Sequence[Mapping[str, Any]],
    query: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return at most *limit* skills ranked by neural embedding + lexical + RRF."""
    query_raw = str(query or "").strip()
    if not query_raw or not catalog:
        return []

    q_vec = _embed_query(query_raw)

    # Channel 1: vector similarity (neural embedding)
    vec_ranked = []
    for item in catalog:
        emb = item.get("embedding") or []
        score = _cosine(q_vec, emb) if emb else 0.0
        vec_ranked.append((score, dict(item)))
    vec_ranked.sort(key=lambda x: -x[0])
    vec_list = [r for _, r in vec_ranked[:limit * 3]]

    # Channel 2: lexical score
    lex_ranked = []
    for item in catalog:
        text = item.get("text") or f"{item.get('name', '')} {item.get('description', '')}"
        score = _lexical_score(query_raw, text)
        lex_ranked.append((score, dict(item)))
    lex_ranked.sort(key=lambda x: -x[0])
    lex_list = [r for _, r in lex_ranked[:limit * 3]]

    # Channel 3: name match
    name_ranked = []
    for item in catalog:
        score = _name_bonus(item.get("name", ""), query_raw)
        name_ranked.append((score, dict(item)))
    name_ranked.sort(key=lambda x: -x[0])
    name_list = [r for _, r in name_ranked[:limit * 3]]

    # Hybrid: RRF over ALL items (no truncation) + raw score bonuses.
    # RRF alone can bury a strong match if the item only appears in 1-2
    # channels.  Adding vector_score * 1.0 ensures high-confidence neural
    # matches surface; adding name_score * 0.5 ensures exact-name hits
    # (e.g. SWOT -> swot-analysis) are not buried by competitors that
    # rank higher in vector/lexical but lack the name signal.
    fused = _rrf_fuse(vec_list, lex_list, name_list, limit=len(catalog))

    # Build score lookups
    vec_lookup = {}
    for score, item in vec_ranked:
        vec_lookup[item.get("name", "")] = score
    name_lookup = {}
    for score, item in name_ranked:
        name_lookup[item.get("name", "")] = score

    # Add vector + name score bonuses to RRF score
    for r in fused:
        vs = vec_lookup.get(r.get("name", ""), 0.0)
        ns = name_lookup.get(r.get("name", ""), 0.0)
        r["vector_score"] = round(vs, 4)
        r["name_score"] = round(ns, 4)
        r["hybrid_score"] = round(r.get("rrf_score", 0) + vs * 1.0 + ns * 0.5, 6)

    fused.sort(key=lambda x: -x.get("hybrid_score", 0))

    # Threshold filter: noise (greetings, chitchat) typically scores
    # 0.2-0.35 on vector with zero lexical/name overlap.  Require
    # vector >= 0.46 OR lexical >= 0.35 OR name >= 0.80 to pass.
    # Filter BEFORE top-K so items that pass the threshold but rank
    # lower are not lost to truncation.
    passed = []
    for r in fused:
        vs = r.get("vector_score", 0.0)
        lex_score = _lexical_score(query_raw, r.get("text") or "")
        name_score = r.get("name_score", 0.0)
        if vs >= 0.46 or lex_score >= 0.35 or name_score >= 0.80:
            passed.append(r)
    return passed[:limit]
