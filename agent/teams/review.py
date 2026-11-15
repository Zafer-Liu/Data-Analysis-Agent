"""Structured quality decisions and bounded evidence references for handoffs."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping


class QualityReviewError(ValueError):
    pass


QUALITY_REVIEW_INSTRUCTION = (
    "Return raw JSON only with decision (pass|rework|escalate), issues (string array), "
    "evidence (string array of claim/artifact references), and missing_evidence (string array). "
    "Use pass only when the supplied evidence supports the deliverable and no blocking "
    "issue or missing evidence remains. Use rework for correctable issues and escalate "
    "when independent verification cannot be completed. Do not infer success from wording."
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QualityReviewError("quality review contains duplicate JSON keys: " + key)
        result[key] = value
    return result


def parse_quality_review(content: str | Mapping, *, require_missing_evidence: bool = False) -> dict:
    """Use the Workflow verifier convention; legacy missing_evidence defaults [].

    Invalid, contradictory or oversized outputs fail explicitly. No language or
    keyword in narrative text can override the structured decision.
    """
    if isinstance(content, Mapping):
        parsed = dict(content)
    else:
        raw = str(content or "").strip()
        if len(raw) > 65536:
            raise QualityReviewError("quality review exceeds 65536 characters")
        try:
            parsed = json.loads(raw, object_pairs_hook=_unique_object)
        except (TypeError, ValueError, RecursionError) as exc:
            raise QualityReviewError("quality review must return a JSON decision: " + str(exc)) from exc
    if (not isinstance(parsed, dict) or not isinstance(parsed.get("decision"), str)
            or parsed["decision"] not in {"pass", "rework", "escalate"}):
        raise QualityReviewError("quality review decision must be pass, rework, or escalate")
    if set(parsed) - {"decision", "issues", "evidence", "missing_evidence"}:
        raise QualityReviewError("quality review contains undeclared fields")
    normalized = {"decision": parsed["decision"]}
    for key in ("issues", "evidence", "missing_evidence"):
        optional = key == "missing_evidence" and not require_missing_evidence
        values = parsed.get(key, [] if optional else None)
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise QualityReviewError(f"quality review {key} must be a string array")
        if len(values) > 64 or any(len(value) > 2000 for value in values):
            raise QualityReviewError(f"quality review {key} exceeds its bounded contract")
        normalized[key] = list(values)
    if normalized["decision"] == "pass" and (normalized["issues"] or normalized["missing_evidence"]):
        raise QualityReviewError("quality review pass contradicts blocking issues or missing evidence")
    return normalized


def quality_review_blocks(content: str | Mapping, *, require_missing_evidence: bool = False) -> bool:
    return parse_quality_review(content, require_missing_evidence=require_missing_evidence)["decision"] != "pass"


_REF_FIELDS = (
    "claim_id", "artifact_id", "artifactId", "uri", "url", "type", "name",
    "sha256", "session_id", "workspace_id", "run_id", "job_id", "tool_call_id",
    "request_id", "attempt_id", "parent_attempt_id", "parent_task_id", "semantic_revision_id",
    "parent_claim_id", "child_claim_id",
)
_REF_CONTAINERS = {
    "artifacts", "artifact_refs", "evidence", "evidence_refs", "claims", "claim_ids",
    "evidence_claims", "child_claim_ids", "parent_claim_ids", "source_artifact_ids", "references",
    "tool_events", "data", "debug", "recovery", "provenance", "result", "content", "execution_context",
}
_ARTIFACT_URI = re.compile(r"artifact://[A-Za-z0-9_./-]+")


def collect_evidence_references(*materials, limit: int = 64) -> dict:
    """Retain only bounded identifiers/locators, never raw SQL/rows/tool arguments.

    Call with the full tool envelope before text-preview truncation. References
    beyond the bound are explicitly marked truncated instead of silently lost.
    """
    bound = max(1, min(int(limit), 128))
    result = {"claim_ids": [], "artifact_refs": [], "evidence_refs": [], "truncated": False}
    seen = {key: set() for key in ("claim_ids", "artifact_refs", "evidence_refs")}
    visited = 0

    def add(key, value):
        identity = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if identity in seen[key]:
            return
        if len(result[key]) >= bound:
            result["truncated"] = True
            return
        seen[key].add(identity)
        result[key].append(value)

    def walk(value, depth=0, kind=""):
        nonlocal visited
        visited += 1
        if depth > 7 or visited > 1500:
            result["truncated"] = True
            return
        if isinstance(value, Mapping):
            claim = value.get("claim_id")
            if isinstance(claim, str) and claim:
                add("claim_ids", claim[:256])
            is_ref = any(value.get(key) for key in (
                "artifact_id", "artifactId", "claim_id", "uri", "attempt_id", "parent_attempt_id",
                "parent_task_id", "parent_claim_id", "child_claim_id",
            ))
            if is_ref:
                reference = {
                    key: str(value[key])[:512]
                    for key in _REF_FIELDS
                    if value.get(key) is not None and isinstance(value[key], (str, int, float))
                }
                if reference:
                    add("artifact_refs" if any(key in reference for key in ("artifact_id", "artifactId", "uri")) else "evidence_refs", reference)
            if value.get("truncated") is True:
                result["truncated"] = True
            for key in sorted(_REF_CONTAINERS):
                if key in value:
                    walk(value[key], depth + 1, key)
        elif isinstance(value, (tuple, list)):
            for item in value[:256]:
                walk(item, depth + 1, kind)
            if len(value) > 256:
                result["truncated"] = True
        elif isinstance(value, str):
            if kind in {"claim_ids", "child_claim_ids", "parent_claim_ids", "claims"}:
                if value:
                    add("claim_ids", value[:256])
                    if kind in {"parent_claim_ids", "child_claim_ids"}:
                        add("evidence_refs", {"parent_claim_id" if kind == "parent_claim_ids" else "child_claim_id": value[:256]})
            elif kind == "source_artifact_ids":
                if value:
                    add("artifact_refs", {"artifact_id": value[:256]})
            elif kind in {"evidence_refs", "references", "evidence"}:
                if value:
                    add("evidence_refs", value[:512])
            # Tool results can be serialized envelopes. Read only bounded JSON,
            # and visit reference containers rather than arbitrary SQL/rows.
            if kind in {"result", "content"} and value.lstrip().startswith(("{", "[")) and len(value) <= 131072:
                try:
                    envelope = json.loads(value)
                except (TypeError, ValueError, RecursionError):
                    pass
                else:
                    walk(envelope, depth + 1)
            if len(value) > 32000:
                result["truncated"] = True
            for match in _ARTIFACT_URI.finditer(value[:32000]):
                add("artifact_refs", {"uri": match.group(0)[:512]})

    for material in materials:
        walk(material)
    return result
