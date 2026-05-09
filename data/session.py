#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""In-memory session management for the business analyst agent."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional


@dataclass
class ChatSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    history: List[Dict[str, str]] = field(default_factory=list)
    data_source: Any = None          # DataSource instance
    model_provider: str = ""         # Selected LLM provider key
    # Token usage tracking
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    last_prompt_tokens: int = 0      # most recent call's prompt size (for context bar)
    # Cancellation flag — set by POST /api/session/<sid>/stop
    cancel_requested: bool = False
    # IDs of every chart generated in this session (appended by api/chat.py)
    chart_ids: List[str] = field(default_factory=list)
    # TTL tracking — updated on every access
    last_accessed: datetime = field(default_factory=datetime.now)

    def add_user(self, text: str):
        self.history.append({"role": "user", "content": text})

    def add_assistant(self, text: str):
        self.history.append({"role": "assistant", "content": text})

    def clear_history(self):
        self.history.clear()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_prompt_tokens = 0

    def record_usage(self, prompt_tokens: int, completion_tokens: int):
        self.total_input_tokens += prompt_tokens
        self.total_output_tokens += completion_tokens
        self.last_prompt_tokens = prompt_tokens


_SESSION_TTL = 7200      # seconds before an idle session is evicted
_CLEANUP_INTERVAL = 1800  # how often the daemon thread wakes to prune


class SessionManager:
    def __init__(self):
        self._store: Dict[str, ChatSession] = {}
        self._start_cleanup_daemon()

    def create(self) -> ChatSession:
        s = ChatSession()
        self._store[s.session_id] = s
        return s

    def get(self, sid: str) -> Optional[ChatSession]:
        s = self._store.get(sid)
        if s:
            s.last_accessed = datetime.now()
        return s

    def get_or_create(self, sid: str) -> ChatSession:
        if sid and sid in self._store:
            s = self._store[sid]
            s.last_accessed = datetime.now()
            return s
        s = ChatSession(session_id=sid) if sid else ChatSession()
        self._store[s.session_id] = s
        return s

    def remove(self, sid: str):
        self._store.pop(sid, None)

    def _cleanup_expired(self):
        cutoff = datetime.now()
        expired = [
            sid for sid, s in list(self._store.items())
            if (cutoff - s.last_accessed).total_seconds() > _SESSION_TTL
        ]
        for sid in expired:
            self._store.pop(sid, None)

    def _start_cleanup_daemon(self):
        import threading

        def _loop():
            import time
            while True:
                time.sleep(_CLEANUP_INTERVAL)
                try:
                    self._cleanup_expired()
                except Exception:
                    pass

        t = threading.Thread(target=_loop, daemon=True, name="session-cleanup")
        t.start()
