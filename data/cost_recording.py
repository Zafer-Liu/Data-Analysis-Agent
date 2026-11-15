"""Bridge execution receipts to the numeric ledger without inferring missing usage."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import uuid


def record_model_calls(calls, *, session_id, workspace_id="", scope_type="session",
                       scope_id="", parent_scope_id="", store=None):
    from agent.workflows.pricing import estimate_model_cost
    from data.cost_event_store import get_cost_event_store, model_cost_event_id

    store = get_cost_event_store() if store is None else store
    recorded = []
    for receipt in calls or []:
        if not isinstance(receipt, Mapping):
            continue
        call_id = str(receipt.get("model_call_id") or receipt.get("call_id") or uuid.uuid4().hex)
        attempt = max(1, int(receipt.get("attempt") or 1))
        available = receipt.get("usage_available", receipt.get("input_tokens") is not None and receipt.get("output_tokens") is not None) is True
        input_tokens = receipt.get("input_tokens") if available else None
        output_tokens = receipt.get("output_tokens") if available else None
        provider, model = str(receipt.get("provider") or ""), str(receipt.get("model") or "")
        event_id = model_cost_event_id(call_id, attempt=attempt, workspace_id=workspace_id,
                                      session_id=session_id, scope_type=scope_type, scope_id=scope_id)
        store.record(
            event_id=event_id, model_call_id=call_id, attempt=attempt,
            occurred_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            scope_type=scope_type, scope_id=scope_id, parent_scope_id=parent_scope_id,
            session_id=session_id, workspace_id=workspace_id, provider=provider, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cached_input_tokens=receipt.get("cached_input_tokens") if available else None,
            cache_write_tokens=receipt.get("cache_write_tokens") if available else None,
            tool_call_count=max(0, int(receipt.get("tool_calls") or 0)), latency_ms=receipt.get("latency_ms"),
            status=str(receipt.get("status") or "succeeded"),
            usage_source=str(receipt.get("usage_source") or "provider_reported") if available else "unavailable",
            measurement_mode=str(receipt.get("measurement_mode") or "actual_model"),
            estimated_cost=estimate_model_cost(provider, model, input_tokens or 0, output_tokens or 0) if available else None,
        )
        recorded.append(event_id)
    return recorded
