"""Authenticated A2A 1.0 JSON-RPC transport for session team members."""
from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone

from flask import jsonify, request, url_for

from agent.execution import ExecutionContext
from agent.teams.a2a import A2AError, message_text, public_task, validate_message
from agent.tools.workspace.teams import WorkspaceTeamError, WorkspaceTeamStore
from .auth import current_user, run_mode
from .state import config_manager, require_session_ownership, session_manager

log = logging.getLogger(__name__)
_ROOT = "/api/session/<sid>/teams/<team_name>/agents/<member_name>"
_STREAM_METHODS = frozenset({"SendStreamingMessage", "SubscribeToTask"})
_PUSH_METHODS = frozenset({
    "CreateTaskPushNotificationConfig", "GetTaskPushNotificationConfig",
    "ListTaskPushNotificationConfigs", "DeleteTaskPushNotificationConfig",
})


class _AccessError(A2AError):
    def __init__(self, message: str, status: int, code: str, **details) -> None:
        super().__init__(-32000, message)
        self.status = status
        self.data = {"code": code, **details}


def _response(payload, status=200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["A2A-Version"] = "1.0"
    return response, status


def _error(request_id, error: A2AError):
    payload = {"code": error.code, "message": str(error)}
    return _response(
        {"jsonrpc": "2.0", "id": request_id, "error": payload},
        getattr(error, "status", 200),
    )


def _reject_json(value):
    raise ValueError("Non-finite JSON numbers are not supported")


def _finite_float(value):
    number = float(value)
    return number if math.isfinite(number) else _reject_json(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not supported")
        result[key] = value
    return result


def _read_request() -> dict:
    if not request.is_json:
        raise A2AError(-32005, "Content-Type must be application/json")
    try:
        body = json.loads(
            request.get_data().decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_reject_json, parse_float=_finite_float,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise A2AError(-32700, "Parse error") from exc
    if not isinstance(body, dict):
        raise A2AError(-32600, "A single JSON-RPC request object is required; batches are not supported")
    request_id = body.get("id")
    valid_id = request_id is None or (
        isinstance(request_id, (str, int, float)) and not isinstance(request_id, bool)
    )
    if (body.get("jsonrpc") != "2.0" or "id" not in body or not valid_id
            or not isinstance(body.get("method"), str) or not body["method"]
            or set(body) - {"jsonrpc", "id", "method", "params"}):
        raise A2AError(-32600, "Invalid JSON-RPC 2.0 request; notifications are not supported")
    return body


def _history_length(params: dict):
    if "historyLength" not in params:
        return None
    length = params["historyLength"]
    if isinstance(length, bool) or not isinstance(length, int) or length < 0:
        raise A2AError(-32602, "historyLength must be a non-negative integer")
    return length


def _validate_params(method: str, params) -> tuple[dict, int | None]:
    if not isinstance(params, dict):
        raise A2AError(-32602, "params must be an object")
    allowed = {"message", "configuration", "metadata", "tenant"} if method == "SendMessage" else {"id", "metadata", "tenant"}
    if method == "GetTask":
        allowed.add("historyLength")
    if set(params) - allowed:
        raise A2AError(-32602, "Unknown parameters")
    if "metadata" in params and not isinstance(params["metadata"], dict):
        raise A2AError(-32602, "metadata must be an object")
    if "tenant" in params:
        if not isinstance(params["tenant"], str):
            raise A2AError(-32602, "tenant must be a string")
        if params["tenant"]:
            raise A2AError(-32004, "Tenant routing is not supported")
    if method != "SendMessage":
        task_id = params.get("id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
            raise A2AError(-32602, "id must be a non-empty task ID of at most 200 characters")
        return params, _history_length(params)
    message = validate_message(params.get("message"))
    configuration = params.get("configuration", {})
    if not isinstance(configuration, dict):
        raise A2AError(-32602, "configuration must be an object")
    if set(configuration) - {"acceptedOutputModes", "returnImmediately", "historyLength", "taskPushNotificationConfig"}:
        raise A2AError(-32602, "Unknown configuration options")
    if "taskPushNotificationConfig" in configuration:
        raise A2AError(-32003, "Push notifications are not supported")
    if "returnImmediately" in configuration:
        if not isinstance(configuration["returnImmediately"], bool):
            raise A2AError(-32602, "configuration.returnImmediately must be a boolean")
        if configuration["returnImmediately"]:
            raise A2AError(-32004, "Nonblocking SendMessage is not supported")
    if "acceptedOutputModes" in configuration:
        modes = configuration["acceptedOutputModes"]
        if not isinstance(modes, list) or not modes or any(not isinstance(mode, str) or not mode.strip() for mode in modes):
            raise A2AError(-32602, "acceptedOutputModes must be a non-empty array of media types")
        if not any(mode.split(";", 1)[0].strip().lower() in {"text/plain", "text/*", "*/*"} for mode in modes):
            raise A2AError(-32005, "Only text/plain output is supported")
    return {**params, "message": message}, _history_length(configuration)


def _session_context(sid: str):
    mode = run_mode()
    auth_user = current_user() if mode in {"cloud", "desktop"} else None
    if mode in {"cloud", "desktop"} and not auth_user:
        raise _AccessError("请先登录", 401, "needs_auth")
    sess = session_manager.get(sid)
    if sess is None:
        raise _AccessError("Session not found", 404, "session_not_found")
    owner = str(getattr(sess, "owner_user_id", "") or "")
    user_id = str(auth_user["id"]) if auth_user else owner or "local-default"
    if auth_user:
        if owner and owner != user_id:
            raise _AccessError("无权访问此会话", 403, "forbidden")
        if not owner:
            sess.owner_user_id = user_id
    return sess, user_id, mode


def _check_quota(user_id: str, mode: str, hosted_model):
    # Desktop hosted providers are authenticated and charged by the cloud gateway.
    if mode != "cloud" or not hosted_model:
        return
    from data.auth_store import check_quota, get_bonus_balance

    if hosted_model.get("requires_paid_balance") and get_bonus_balance(user_id) <= 0:
        raise _AccessError("该模型需要可用额度，请先获取或购买额度。", 402, "paid_quota_required")
    quota = check_quota(user_id)
    if not hosted_model.get("requires_paid_balance") and quota["exceeded"]:
        quota = {**quota, "model": {
            key: hosted_model.get(key, "")
            for key in ("id", "display_name", "public_model_name", "tier")
        }}
        raise _AccessError("今日赠送额度已用尽。", 403, "quota_exceeded", quota=quota)


def _build_agent(*args, **kwargs):
    # Resolve lazily, without registering another chat route or creating an app.
    from .chat import _build_agent as build
    return build(*args, **kwargs)


def _result_invalid_reason(content, tool_events):
    from agent.agent import _delegated_result_invalid_reason
    return _delegated_result_invalid_reason(content, tool_events)


class _RequestUsage:
    """Collect only this executor's calls, preserving the Session recorder."""

    def __init__(self, agent, *, sid, workspace_id, task_id, user_id, mode, hosted_model):
        self.agent = agent
        self.sid, self.workspace_id, self.task_id = sid, workspace_id, task_id
        self.user_id, self.mode, self.hosted_model = user_id, mode, hosted_model
        self.events = []
        self.model_calls = []
        self.model_call_id = ""
        self.original_recorder = agent._usage_recorder
        self.original_audit = getattr(agent, "_new_model_audit", None)
        agent._usage_recorder = self.record
        if self.original_audit is not None:
            agent._new_model_audit = self.audit

    def audit(self, purpose, billing_scope="user"):
        audit = self.original_audit(
            purpose, billing_scope=billing_scope,
        )
        self.model_call_id = str(getattr(audit, "request_id", "") or "")
        return audit

    def record(self, prompt_tokens, completion_tokens, **kwargs):
        breakdown = kwargs.get("breakdown")
        available = prompt_tokens is not None and completion_tokens is not None
        available = available and (not isinstance(breakdown, dict) or breakdown.get("usage_available", True) is not False)
        self.events.append({
            "usage_available": available,
            "prompt_tokens": max(0, int(prompt_tokens or 0)) if available else None,
            "completion_tokens": max(0, int(completion_tokens or 0)) if available else None,
            "cached_input_tokens": max(0, int(kwargs.get("cached_input_tokens"))) if available and kwargs.get("cached_input_tokens") is not None else None,
            "cache_write_tokens": max(0, int(kwargs.get("cache_write_tokens"))) if available and kwargs.get("cache_write_tokens") is not None else None,
            "breakdown": dict(breakdown) if isinstance(breakdown, dict) else {},
            "model_call_id": self.model_call_id,
            "event_id": uuid.uuid4().hex,
        })
        if self.original_recorder is not None:
            self.original_recorder(prompt_tokens, completion_tokens, **kwargs)

    def settle(self):
        self.agent._usage_recorder = self.original_recorder
        if self.original_audit is not None:
            self.agent._new_model_audit = self.original_audit
        billing_error = None
        if self.model_calls:
            from data.cost_recording import record_model_calls
            record_model_calls(self.model_calls, session_id=self.sid, workspace_id=self.workspace_id,
                               scope_type="team_task", scope_id=self.task_id)
        for event in self.events:
            tokens = {key: event[key] for key in (
                "prompt_tokens", "completion_tokens", "cached_input_tokens", "cache_write_tokens",
            )}
            breakdown = event["breakdown"]
            if self.mode == "cloud" and self.hosted_model and event["usage_available"]:
                from data.auth_store import record_usage_event
                hosted = self.hosted_model
                try:
                    record_usage_event(
                        self.user_id, **tokens,
                        model=str(breakdown.get("model") or self.agent.model or ""),
                        model_id=hosted["id"], tier=hosted["tier"],
                        quota_source=hosted["quota_source"], multiplier=hosted["quota_multiplier"],
                        input_multiplier=hosted["input_multiplier"],
                        output_multiplier=hosted["output_multiplier"],
                        cache_read_multiplier=hosted["cache_read_multiplier"],
                        cache_write_multiplier=hosted["cache_write_multiplier"],
                        session_id=self.sid, model_call_id=event["model_call_id"],
                    )
                except Exception as exc:
                    billing_error = exc
                    log.exception("[a2a] hosted usage settlement failed sid=%s task=%s", self.sid, self.task_id)
            try:
                if self.model_calls:
                    continue
                from data.cost_event_store import get_cost_event_store
                get_cost_event_store().record(
                    event_id=event["event_id"],
                    occurred_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    scope_type="team_task", scope_id=self.task_id,
                    workspace_id=self.workspace_id, session_id=self.sid,
                    provider=str(breakdown.get("provider") or getattr(self.agent, "_provider", "") or ""),
                    model=str(breakdown.get("model") or self.agent.model or ""),
                    input_tokens=event["prompt_tokens"], output_tokens=event["completion_tokens"],
                    cached_input_tokens=event["cached_input_tokens"],
                    cache_write_tokens=event["cache_write_tokens"], attribution=breakdown,
                    status=str(breakdown.get("status") or "succeeded"),
                    usage_source=str(breakdown.get("usage_source") or "provider_reported") if event["usage_available"] else "unavailable",
                    measurement_mode=str(breakdown.get("measurement_mode") or "actual_model"),
                    model_call_id=str(breakdown.get("model_call_id") or event["model_call_id"] or event["event_id"]),
                    attempt=max(1, int(breakdown.get("attempt") or 1)),
                )
            except Exception:
                log.exception("[a2a] cost event recording failed sid=%s task=%s", self.sid, self.task_id)
        if billing_error is not None:
            raise billing_error


def _send_message(store, sess, user_id, mode, team_name, member_name, params):
    task, created = store.submit_task(team_name, member_name, params["message"])
    if not created:
        return task
    task_id = task["id"]
    snapshot = usage = failure = turn = None
    result = {}
    context = ExecutionContext(request_id=task_id, timeout_seconds=300)
    member_context = None
    content, tool_events, invalid_reason = "", [], ""
    try:
        context.checkpoint()
        provider = sess.model_provider or config_manager.get_default_provider()
        hosted = config_manager.get_hosted_model(provider or "")
        _check_quota(user_id, mode, hosted)
        snapshot = sess.acquire_data_source_snapshot()
        context.add_exit_callback(snapshot.release)
        agent = _build_agent(
            sess, workspace_id=store.workspace_id, source_snapshot=snapshot, user_id=user_id,
        )
        agent._execution_context = context
        context.checkpoint()
        from agent.prompts import message_needs_knowledge
        agent._knowledge_allowed_this_turn = message_needs_knowledge(message_text(params["message"]))
        # Model settings can change concurrently; charge the provider actually built.
        actual_provider = getattr(agent, "_provider", provider) or provider
        if actual_provider != provider:
            hosted = config_manager.get_hosted_model(actual_provider or "")
            _check_quota(user_id, mode, hosted)
        usage = _RequestUsage(
            agent, sid=sess.session_id, workspace_id=store.workspace_id, task_id=task_id,
            user_id=user_id, mode=mode, hosted_model=hosted,
        )
        turn = store.begin_member_turn(
            team_name, member_name, task_id=task_id, execution_context=context, timeout_seconds=300,
        )
        member_context = turn["member"].get("_execution_context")
        inbox_context = ""
        if turn.get("inbox"):
            inbox_context = "\n\n[Unread team mailbox]\n" + "\n".join(
                f"- From {item.get('sender', '')}: {item.get('message', '')}"
                for item in turn["inbox"]
            )
        result = agent._run_delegated_llm(
            member=turn["member"], prompt=message_text(params["message"]),
            inbox_context=inbox_context, timeout_seconds=300, max_tokens=2000, max_tool_calls=30,
            **({"execution_context": member_context} if member_context is not None else {}),
        )
        if usage is not None:
            usage.model_calls = list((result.get("usage") or {}).get("model_calls") or [])
        context.checkpoint()
        if member_context is not None:
            member_context.checkpoint()
        content, tool_events = str(result.get("content") or ""), result.get("tool_events") or []
        invalid_reason = _result_invalid_reason(content, tool_events)
    except Exception as exc:
        failure = exc
        if usage is not None:
            usage.model_calls = list((getattr(exc, "delegated_usage", None) or {}).get("model_calls") or usage.model_calls)
        log.exception("[a2a] task failed sid=%s task=%s", sess.session_id, task_id)
    finally:
        try:
            if usage is not None:
                usage.settle()
        except Exception as exc:
            failure = failure or exc
    try:
        completion = store.complete_member_turn(
            team_name, member_name,
            (str(failure) if isinstance(failure, _AccessError) else "A2A task execution failed.")
            if failure else (invalid_reason or content),
            ok=failure is None and not invalid_reason, tool_events=tool_events, task_id=task_id,
            execution_context=member_context,
            attempt_id=str((turn or {}).get("member", {}).get("_execution_attempt_id") or ""),
            evidence=result,
        )
    finally:
        context.acknowledge_exit()
    if isinstance(failure, _AccessError):
        failure.data["taskId"] = task_id
        raise failure
    return completion["task"]


def register_routes(bp):
    @bp.get(_ROOT + "/.well-known/agent-card.json", endpoint="team_a2a_card")
    @require_session_ownership
    def agent_card(sid: str, team_name: str, member_name: str):
        try:
            _session_context(sid)
            store = WorkspaceTeamStore(sid)
            card = store.card(team_name, member_name, url=url_for(
                f"{request.blueprint}.team_a2a_rpc", sid=sid, team_name=team_name,
                member_name=member_name, _external=True,
            ))
            return _response(card)
        except _AccessError as exc:
            return _response({"error": str(exc), **exc.data}, exc.status)
        except WorkspaceTeamError as exc:
            return _response({"error": str(exc)}, 404 if "not found" in str(exc) else 400)
        except Exception:
            log.exception("[a2a] discovery failed sid=%s", sid)
            return _response({"error": "Agent discovery failed"}, 500)

    @bp.post(_ROOT + "/a2a", endpoint="team_a2a_rpc")
    @require_session_ownership
    def json_rpc(sid: str, team_name: str, member_name: str):
        request_id = None
        try:
            body = _read_request()
            request_id = body["id"]
            if request.headers.get("A2A-Version", "").strip() != "1.0":
                raise A2AError(-32009, "This endpoint requires the A2A-Version: 1.0 request header")
            method = body["method"]
            if method in _STREAM_METHODS:
                raise A2AError(-32004, "Streaming is not supported")
            if method in _PUSH_METHODS:
                raise A2AError(-32003, "Push notifications are not supported")
            if method not in {"SendMessage", "GetTask", "CancelTask"}:
                raise A2AError(-32601, "Method not found")
            params, history_length = _validate_params(method, body.get("params", {}))
            sess, user_id, mode = _session_context(sid)
            store = WorkspaceTeamStore(sid)
            store.member(team_name, member_name)
            if method == "SendMessage":
                task = _send_message(store, sess, user_id, mode, team_name, member_name, params)
                result = {"task": public_task(task, history_length)}
            elif method == "GetTask":
                result = store.get_task(
                    team_name, params["id"], member_name=member_name, history_length=history_length,
                )
            else:
                result = store.cancel_task(team_name, params["id"], member_name=member_name)
            return _response({"jsonrpc": "2.0", "id": request_id, "result": result})
        except A2AError as exc:
            return _error(request_id, exc)
        except WorkspaceTeamError as exc:
            if body["method"] in {"GetTask", "CancelTask"} and "not found" in str(exc):
                return _error(request_id, A2AError(-32001, "Task not found"))
            return _error(request_id, A2AError(-32602, str(exc)))
        except Exception:
            log.exception("[a2a] JSON-RPC request failed sid=%s", sid)
            return _error(request_id, A2AError(-32603, "Internal error"))
