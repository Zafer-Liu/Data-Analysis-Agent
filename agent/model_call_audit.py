"""Content-free audit trail for real upstream model calls.

The quota ledger intentionally answers "what did we charge the user?".  This
module answers the separate operational question "what did we ask a provider
to run?" without storing prompts, responses, API keys, or provider URLs.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class ModelCallAudit:
    """Best-effort lifecycle recorder that never breaks an LLM request."""

    def __init__(
        self,
        *,
        user_id: str,
        session_id: str,
        source: str,
        purpose: str,
        billing_scope: str,
        provider: str,
        model: str,
    ) -> None:
        self.request_id = ""
        self._finished = False
        try:
            from infrastructure.paths import is_cloud_managed_runtime

            if not is_cloud_managed_runtime():
                return
            from data.auth_store import begin_model_call

            self.request_id = begin_model_call(
                user_id=user_id,
                session_id=session_id,
                source=source,
                purpose=purpose,
                billing_scope=billing_scope,
                provider=provider,
                model=model,
            )
        except Exception:
            log.exception("[model-audit] failed to begin purpose=%s", purpose)

    def attempt(self, *_args: Any) -> None:
        if _args and _args[0] != "start":
            return
        if not self.request_id or self._finished:
            return
        try:
            from data.auth_store import increment_model_call_attempt

            increment_model_call_attempt(self.request_id)
        except Exception:
            log.exception("[model-audit] failed to increment request=%s", self.request_id)

    def succeed(self, response_or_usage: Any) -> None:
        usage = getattr(response_or_usage, "usage", None) or response_or_usage
        self._finish("succeeded", usage=usage)

    def missing_usage(self) -> None:
        self._finish("missing_usage")

    def fail(self, error: BaseException | str) -> None:
        error_name = type(error).__name__ if isinstance(error, BaseException) else str(error)
        self._finish("failed", error_code=error_name)

    def cancel(self) -> None:
        self._finish("canceled")

    def _finish(
        self,
        status: str,
        *,
        usage: Any = None,
        error_code: str = "",
    ) -> None:
        if not self.request_id or self._finished:
            return
        self._finished = True
        try:
            from data.auth_store import settle_model_call

            settle_model_call(
                self.request_id,
                status=status,
                usage=usage,
                error_code=error_code,
            )
        except Exception:
            log.exception("[model-audit] failed to settle request=%s", self.request_id)


def audit_model_call(**kwargs: str) -> ModelCallAudit:
    """Create a lifecycle recorder with a concise call-site API."""
    return ModelCallAudit(**kwargs)
