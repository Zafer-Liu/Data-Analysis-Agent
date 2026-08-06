"""SQLite-based user store and daily token quota tracker for cloud deployments."""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash, check_password_hash

from infrastructure.paths import data_path

# Use UTC+8 (Asia/Shanghai) for daily reset boundary
_CN_TZ = timezone(timedelta(hours=8))

_DB_PATH = data_path("auth.db")
_LOCK = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema() -> None:
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id     TEXT NOT NULL,
                date        TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, date),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)
        conn.commit()


_ensure_schema()


def _today() -> str:
    return datetime.now(_CN_TZ).strftime("%Y-%m-%d")


def create_user(username: str, password: str) -> dict | None:
    """Register a new user. Returns user dict or None if username taken."""
    with _LOCK:
        conn = _get_conn()
        try:
            existing = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ).fetchone()
            if existing:
                return None
            uid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)",
                (uid, username, generate_password_hash(password)),
            )
            conn.commit()
            return {"id": uid, "username": username}
        finally:
            conn.close()


def verify_user(username: str, password: str) -> dict | None:
    """Authenticate. Returns user dict or None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return None
        return {"id": row["id"], "username": row["username"]}
    finally:
        conn.close()


def get_user_by_id(uid: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, username FROM users WHERE id = ?", (uid,)
        ).fetchone()
        if not row:
            return None
        return {"id": row["id"], "username": row["username"]}
    finally:
        conn.close()


def get_daily_usage(uid: str) -> int:
    """Return today's token usage for the user."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT tokens_used FROM daily_usage WHERE user_id = ? AND date = ?",
            (uid, _today()),
        ).fetchone()
        return row["tokens_used"] if row else 0
    finally:
        conn.close()


def add_usage(uid: str, tokens: int) -> int:
    """Atomically add tokens to today's usage. Returns new total."""
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO daily_usage (user_id, date, tokens_used)
                   VALUES (?, ?, ?)
                   ON CONFLICT (user_id, date)
                   DO UPDATE SET tokens_used = tokens_used + ?""",
                (uid, _today(), tokens, tokens),
            )
            conn.commit()
            return get_daily_usage(uid)
        finally:
            conn.close()


DAILY_TOKEN_LIMIT = int(os.environ.get("BAA_DAILY_TOKEN_LIMIT", "50000"))


def check_quota(uid: str) -> dict:
    """Return quota status dict."""
    used = get_daily_usage(uid)
    remaining = max(0, DAILY_TOKEN_LIMIT - used)
    return {
        "used": used,
        "limit": DAILY_TOKEN_LIMIT,
        "remaining": remaining,
        "exceeded": used >= DAILY_TOKEN_LIMIT,
    }
