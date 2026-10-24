"""SQLite-based user store, email verification codes, and daily token quota tracker.

Designed for cloud deployments (Railway).  All cloud-only features are gated
behind ``is_cloud_managed()`` in api.auth — the module is imported on every
startup (including local) but only *used* in cloud mode.
"""
from __future__ import annotations

import os
import math
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash, check_password_hash

from infrastructure.paths import account_data_path

# Use UTC+8 (Asia/Shanghai) for daily reset boundary
_CN_TZ = timezone(timedelta(hours=8))

# Account records are deliberately separated from content_data_path(): in a
# managed deployment this resolves to the Railway Volume and never to /tmp.
_DB_PATH = account_data_path("auth.db")
_LOCK = threading.Lock()

# Verification code lifetime
_CODE_TTL_MINUTES = 5
# Minimum seconds between code sends for the same email
_CODE_RESEND_SECONDS = 60
_DESKTOP_TOKEN_TTL_DAYS = max(1, int(os.environ.get("BAA_DESKTOP_TOKEN_TTL_DAYS", "30")))
REFERRAL_INVITER_BONUS = max(0, int(os.environ.get("BAA_REFERRAL_INVITER_BONUS", "10000000")))
REFERRAL_INVITEE_BONUS = max(0, int(os.environ.get("BAA_REFERRAL_INVITEE_BONUS", "4000000")))


def _get_conn() -> sqlite3.Connection:
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema() -> None:
    with _get_conn() as conn:
        # --- migration: preserve the legacy username-based account tables ---
        legacy_users_table = ""
        legacy_usage_table = ""
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if cols and "email" not in cols:
            suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            legacy_users_table = f"users_legacy_username_{suffix}"
            usage_cols = [
                r[1] for r in conn.execute("PRAGMA table_info(daily_usage)").fetchall()
            ]
            if usage_cols:
                legacy_usage_table = f"daily_usage_legacy_username_{suffix}"
                conn.execute(f'ALTER TABLE daily_usage RENAME TO "{legacy_usage_table}"')
            conn.execute(f'ALTER TABLE users RENAME TO "{legacy_users_table}"')
        # --- tables ---
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                auth_version  INTEGER NOT NULL DEFAULT 1,
                sponsor_prompt_last_shown_at TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS email_codes (
                email      TEXT NOT NULL,
                code       TEXT NOT NULL,
                purpose    TEXT NOT NULL DEFAULT 'register',
                expires_at TEXT NOT NULL,
                used       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (email, code)
            );
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id     TEXT NOT NULL,
                date        TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, date),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS usage_events (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           TEXT NOT NULL,
                session_id        TEXT NOT NULL DEFAULT '',
                model             TEXT NOT NULL DEFAULT '',
                prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens      INTEGER NOT NULL DEFAULT 0,
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_usage_events_user_created
                ON usage_events(user_id, created_at);
            CREATE TABLE IF NOT EXISTS model_call_events (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id        TEXT UNIQUE NOT NULL,
                user_id           TEXT NOT NULL DEFAULT '',
                session_id        TEXT NOT NULL DEFAULT '',
                source            TEXT NOT NULL DEFAULT 'web',
                purpose           TEXT NOT NULL DEFAULT 'chat',
                billing_scope     TEXT NOT NULL DEFAULT 'user',
                provider          TEXT NOT NULL DEFAULT '',
                model             TEXT NOT NULL DEFAULT '',
                status            TEXT NOT NULL DEFAULT 'started',
                attempt_count     INTEGER NOT NULL DEFAULT 0,
                prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                raw_tokens        INTEGER NOT NULL DEFAULT 0,
                error_code        TEXT NOT NULL DEFAULT '',
                started_at        TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_model_calls_started
                ON model_call_events(started_at, status);
            CREATE INDEX IF NOT EXISTS idx_model_calls_user_started
                ON model_call_events(user_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_model_calls_purpose_started
                ON model_call_events(purpose, started_at);
            CREATE TABLE IF NOT EXISTS auth_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT,
                email      TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL,
                success    INTEGER NOT NULL,
                ip_address TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                detail     TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_auth_events_user_created
                ON auth_events(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_auth_events_email_created
                ON auth_events(email, created_at);
            CREATE TABLE IF NOT EXISTS admin_audit_events (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_user_id  TEXT NOT NULL,
                admin_email    TEXT NOT NULL DEFAULT '',
                action         TEXT NOT NULL,
                target_user_id TEXT NOT NULL DEFAULT '',
                detail         TEXT NOT NULL DEFAULT '',
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (admin_user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_admin_audit_created
                ON admin_audit_events(created_at);
            CREATE TABLE IF NOT EXISTS referrals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id   TEXT NOT NULL,
                invitee_id    TEXT UNIQUE,
                invitee_email TEXT NOT NULL DEFAULT '',
                referral_code TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                registered_at TEXT,
                FOREIGN KEY (referrer_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_referrals_referrer
                ON referrals(referrer_id);
            CREATE INDEX IF NOT EXISTS idx_referrals_code
                ON referrals(referral_code);
            CREATE TABLE IF NOT EXISTS quota_bonus_balances (
                user_id          TEXT PRIMARY KEY,
                tokens_available INTEGER NOT NULL DEFAULT 0,
                updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS quota_bonus_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key    TEXT UNIQUE NOT NULL,
                user_id      TEXT NOT NULL,
                amount       INTEGER NOT NULL,
                source       TEXT NOT NULL DEFAULT '',
                reference_id TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_quota_bonus_events_user_created
                ON quota_bonus_events(user_id, created_at);
            CREATE TABLE IF NOT EXISTS desktop_tokens (
                user_id    TEXT PRIMARY KEY,
                email      TEXT NOT NULL DEFAULT '',
                token      TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT
            );
        """)
        if legacy_users_table:
            # Email-shaped legacy usernames remain usable without resetting the
            # password.  Non-email usernames stay in the preserved legacy table
            # for explicit administrator-assisted migration.
            conn.execute(
                f'INSERT OR IGNORE INTO users (id, email, password_hash, created_at) '
                f'SELECT id, lower(username), password_hash, created_at '
                f"FROM \"{legacy_users_table}\" WHERE instr(username, '@') > 1"
            )
            if legacy_usage_table:
                conn.execute(
                    f'INSERT OR IGNORE INTO daily_usage (user_id, date, tokens_used) '
                    f'SELECT old.user_id, old.date, old.tokens_used '
                    f'FROM "{legacy_usage_table}" old '
                    f'JOIN users current ON current.id = old.user_id'
                )
        user_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "sponsor_prompt_last_shown_at" not in user_cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN sponsor_prompt_last_shown_at TEXT"
            )
        if "auth_version" not in user_cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 1"
            )
        email_code_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(email_codes)").fetchall()
        }
        if "purpose" not in email_code_cols:
            conn.execute(
                "ALTER TABLE email_codes ADD COLUMN purpose TEXT NOT NULL DEFAULT 'register'"
            )
        usage_event_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(usage_events)").fetchall()
        }
        usage_event_migrations = {
            "model_call_id": "TEXT NOT NULL DEFAULT ''",
            "model_id": "TEXT NOT NULL DEFAULT ''",
            "tier": "TEXT NOT NULL DEFAULT ''",
            "quota_source": "TEXT NOT NULL DEFAULT ''",
            "raw_tokens": "INTEGER NOT NULL DEFAULT 0",
            "multiplier": "REAL NOT NULL DEFAULT 1",
            "uncached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
            "input_multiplier": "REAL NOT NULL DEFAULT 1",
            "output_multiplier": "REAL NOT NULL DEFAULT 1",
            "cache_read_multiplier": "REAL NOT NULL DEFAULT 1",
            "cache_write_multiplier": "REAL NOT NULL DEFAULT 1",
            "charged_units": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, declaration in usage_event_migrations.items():
            if column not in usage_event_cols:
                conn.execute(
                    f"ALTER TABLE usage_events ADD COLUMN {column} {declaration}"
                )
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_users_created_at
                ON users(created_at);
            CREATE INDEX IF NOT EXISTS idx_usage_events_created_user
                ON usage_events(created_at, user_id);
            CREATE INDEX IF NOT EXISTS idx_usage_events_model_call
                ON usage_events(model_call_id);
            CREATE INDEX IF NOT EXISTS idx_auth_events_created_success_user
                ON auth_events(created_at, success, user_id);
        """)
        # Complete rewards for referrals created by versions that only marked
        # registrations.  Unique event keys make this migration idempotent.
        legacy_referrals = conn.execute(
            """SELECT id, referrer_id, invitee_id FROM referrals
               WHERE invitee_id IS NOT NULL AND status IN ('registered', 'rewarded')"""
        ).fetchall()
        for referral in legacy_referrals:
            for role, user_id, amount in (
                ("inviter", referral["referrer_id"], REFERRAL_INVITER_BONUS),
                ("invitee", referral["invitee_id"], REFERRAL_INVITEE_BONUS),
            ):
                event_key = f"referral:{referral['id']}:{role}"
                inserted = conn.execute(
                    """INSERT OR IGNORE INTO quota_bonus_events
                       (event_key, user_id, amount, source, reference_id)
                       VALUES (?, ?, ?, 'referral', ?)""",
                    (event_key, user_id, amount, str(referral["id"])),
                )
                if inserted.rowcount and amount:
                    conn.execute(
                        """INSERT INTO quota_bonus_balances (user_id, tokens_available)
                           VALUES (?, ?)
                           ON CONFLICT(user_id) DO UPDATE SET
                               tokens_available = tokens_available + excluded.tokens_available,
                               updated_at = datetime('now')""",
                        (user_id, amount),
                    )
            conn.execute(
                "UPDATE referrals SET status = 'rewarded' WHERE id = ?",
                (referral["id"],),
            )
        conn.commit()
    # sqlite3.Connection's context manager commits/rolls back but does not
    # close the handle. Close explicitly so Windows can release the DB file.
    conn.close()


_ensure_schema()


def _today() -> str:
    return datetime.now(_CN_TZ).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
#  User CRUD
# ---------------------------------------------------------------------------

def create_user(email: str, password: str, referral_code: str = "") -> dict | None:
    """Register a new user. Returns user dict or None if email taken.

    A valid ``referral_code`` awards a persistent bonus to both accounts in the
    same transaction.  Invalid codes never block registration.
    """
    with _LOCK:
        conn = _get_conn()
        try:
            existing = conn.execute(
                "SELECT 1 FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing:
                return None
            uid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                (uid, email, generate_password_hash(password)),
            )
            normalized_code = str(referral_code or "").strip().upper()
            if normalized_code:
                owner = conn.execute(
                    """SELECT referrer_id FROM referrals
                       WHERE referral_code = ? ORDER BY id LIMIT 1""",
                    (normalized_code,),
                ).fetchone()
                if owner and owner["referrer_id"] != uid:
                    referral = conn.execute(
                        """INSERT INTO referrals
                           (referrer_id, invitee_id, invitee_email, referral_code,
                            status, registered_at)
                           VALUES (?, ?, ?, ?, 'rewarded', datetime('now'))""",
                        (owner["referrer_id"], uid, email, normalized_code),
                    )
                    referral_id = int(referral.lastrowid)
                    for role, user_id, amount in (
                        ("inviter", owner["referrer_id"], REFERRAL_INVITER_BONUS),
                        ("invitee", uid, REFERRAL_INVITEE_BONUS),
                    ):
                        conn.execute(
                            """INSERT INTO quota_bonus_events
                               (event_key, user_id, amount, source, reference_id)
                               VALUES (?, ?, ?, 'referral', ?)""",
                            (f"referral:{referral_id}:{role}", user_id, amount, str(referral_id)),
                        )
                        if amount:
                            conn.execute(
                                """INSERT INTO quota_bonus_balances
                                   (user_id, tokens_available) VALUES (?, ?)
                                   ON CONFLICT(user_id) DO UPDATE SET
                                       tokens_available = tokens_available + excluded.tokens_available,
                                       updated_at = datetime('now')""",
                                (user_id, amount),
                            )
            conn.commit()
            return {"id": uid, "email": email, "auth_version": 1}
        finally:
            conn.close()


def verify_user(email: str, password: str) -> dict | None:
    """Authenticate. Returns user dict or None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, email, password_hash, auth_version FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return None
        return {
            "id": row["id"],
            "email": row["email"],
            "auth_version": int(row["auth_version"] or 1),
        }
    finally:
            conn.close()


def get_user_by_id(uid: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, email, auth_version FROM users WHERE id = ?", (uid,)
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "email": row["email"],
            "auth_version": int(row["auth_version"] or 1),
        }
    finally:
            conn.close()


def get_user_by_email(email: str) -> dict | None:
    """Return the account identity for a normalized email address."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, email, auth_version FROM users WHERE email = ?",
            (str(email).strip().lower(),),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "email": row["email"],
            "auth_version": int(row["auth_version"] or 1),
        }
    finally:
        conn.close()


def reset_user_password(uid: str, password: str) -> bool:
    """Replace a password and invalidate every existing login for the account."""
    with _LOCK:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                """UPDATE users
                   SET password_hash = ?, auth_version = auth_version + 1
                   WHERE id = ?""",
                (generate_password_hash(password), uid),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return False
            conn.execute("DELETE FROM desktop_tokens WHERE user_id = ?", (uid,))
            conn.commit()
            return True
        finally:
            conn.close()


# ---------------------------------------------------------------------------
#  Email verification codes
# ---------------------------------------------------------------------------

def generate_code() -> str:
    """Return a 6-digit verification code."""
    return f"{secrets.randbelow(1000000):06d}"


def store_email_code(email: str, code: str, purpose: str = "register") -> None:
    """Store a verification code with expiry. Deletes previous unused codes."""
    purpose = str(purpose or "register")[:40]
    now = datetime.now(_CN_TZ)
    expires = now + timedelta(minutes=_CODE_TTL_MINUTES)
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                "DELETE FROM email_codes WHERE used = 1 OR expires_at <= ?",
                (now.strftime("%Y-%m-%d %H:%M:%S"),),
            )
            conn.execute(
                "DELETE FROM email_codes WHERE email = ? AND purpose = ? AND used = 0",
                (email, purpose),
            )
            conn.execute(
                """INSERT OR REPLACE INTO email_codes
                   (email, code, purpose, expires_at, used)
                   VALUES (?, ?, ?, ?, 0)""",
                (email, code, purpose, expires.strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        finally:
            conn.close()


def can_resend(email: str, purpose: str = "register") -> bool:
    """Return True if enough time has passed since the last code was sent."""
    conn = _get_conn()
    try:
        row = conn.execute(
            """SELECT created_at FROM email_codes
               WHERE email = ? AND purpose = ?
               ORDER BY created_at DESC LIMIT 1""",
            (email, str(purpose or "register")[:40]),
        ).fetchone()
        if not row:
            return True
        last = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        return (datetime.now(timezone.utc) - last).total_seconds() >= _CODE_RESEND_SECONDS
    finally:
            conn.close()


def consume_email_code(email: str, code: str, purpose: str = "register") -> bool:
    """Verify and consume a code. Returns True if valid, False otherwise."""
    with _LOCK:
        conn = _get_conn()
        try:
            now = datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
            row = conn.execute(
                """SELECT 1 FROM email_codes
                   WHERE email = ? AND code = ? AND purpose = ? AND used = 0
                     AND expires_at > ?""",
                (email, code, str(purpose or "register")[:40], now),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                """UPDATE email_codes SET used = 1
                   WHERE email = ? AND code = ? AND purpose = ?""",
                (email, code, str(purpose or "register")[:40]),
            )
            conn.commit()
            return True
        finally:
            conn.close()


# ---------------------------------------------------------------------------
#  Daily token quota
# ---------------------------------------------------------------------------

def get_daily_usage(uid: str) -> int:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT tokens_used FROM daily_usage WHERE user_id = ? AND date = ?",
            (uid, _today()),
        ).fetchone()
        return row["tokens_used"] if row else 0
    finally:
            conn.close()


def get_bonus_balance(uid: str) -> int:
    """Return persistent promotional/purchased quota still available."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT tokens_available FROM quota_bonus_balances WHERE user_id = ?",
            (uid,),
        ).fetchone()
        return max(0, int(row["tokens_available"] if row else 0))
    finally:
        conn.close()


def get_bonus_summary(uid: str) -> dict:
    """Return the persistent balance together with its tracked grant history.

    Older/manual databases may contain a balance without a matching event. In
    that case the visible balance is treated as the granted total so the UI
    never reports a negative or fabricated amount consumed.
    """
    conn = _get_conn()
    try:
        balance_row = conn.execute(
            "SELECT tokens_available FROM quota_bonus_balances WHERE user_id = ?",
            (uid,),
        ).fetchone()
        grant_row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0)
                      AS total_granted
               FROM quota_bonus_events WHERE user_id = ?""",
            (uid,),
        ).fetchone()
        balance = max(0, int(balance_row["tokens_available"] if balance_row else 0))
        tracked_total = max(0, int(grant_row["total_granted"] if grant_row else 0))
        total_granted = max(balance, tracked_total)
        return {
            "balance": balance,
            "total_granted": total_granted,
            "used": max(0, total_granted - balance),
        }
    finally:
        conn.close()


def _consume_bonus_after_daily(conn: sqlite3.Connection, uid: str, before: int, after: int) -> None:
    """Charge only the portion that exceeds today's free allowance."""
    charge = max(0, after - DAILY_TOKEN_LIMIT) - max(0, before - DAILY_TOKEN_LIMIT)
    if charge <= 0:
        return
    conn.execute(
        """UPDATE quota_bonus_balances
           SET tokens_available = MAX(0, tokens_available - ?),
               updated_at = datetime('now')
           WHERE user_id = ?""",
        (charge, uid),
    )


def add_usage(uid: str, tokens: int) -> int:
    with _LOCK:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT tokens_used FROM daily_usage WHERE user_id = ? AND date = ?",
                (uid, _today()),
            ).fetchone()
            before = int(row["tokens_used"] if row else 0)
            increment = max(0, int(tokens or 0))
            conn.execute(
                """INSERT INTO daily_usage (user_id, date, tokens_used)
                   VALUES (?, ?, ?)
                   ON CONFLICT (user_id, date)
                   DO UPDATE SET tokens_used = tokens_used + ?""",
                (uid, _today(), increment, increment),
            )
            after = before + increment
            _consume_bonus_after_daily(conn, uid, before, after)
            conn.commit()
            return after
        finally:
            conn.close()


def record_usage_event(
    uid: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    model: str = "",
    model_id: str = "",
    tier: str = "",
    quota_source: str = "",
    multiplier: float = 1.0,
    input_multiplier: float | None = None,
    output_multiplier: float | None = None,
    cache_read_multiplier: float | None = None,
    cache_write_multiplier: float | None = None,
    session_id: str = "",
    model_call_id: str = "",
) -> int:
    """Atomically charge the matching balance and append a content-free row.

    Free hosted models consume the daily allowance first and then the existing
    persistent balance. Paid hosted models use ``persistent_balance`` and must
    never consume the daily gift.
    """
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    raw_total = prompt + completion

    def _weight(value, fallback: float) -> float:
        if value is None:
            return fallback
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return fallback
        return parsed if math.isfinite(parsed) and parsed >= 0 else fallback

    effective_multiplier = _weight(multiplier, 1.0)
    input_weight = _weight(input_multiplier, effective_multiplier)
    output_weight = _weight(output_multiplier, effective_multiplier)
    cache_read_weight = _weight(cache_read_multiplier, input_weight)
    cache_write_weight = _weight(cache_write_multiplier, input_weight)

    # OpenAI-compatible usage reports cache tokens as subsets of prompt_tokens.
    # Clamp them to the reported prompt total so cache discounts never double
    # count or create negative uncached input when a provider reports oddly.
    cached_input = min(prompt, max(0, int(cached_input_tokens or 0)))
    cache_write = min(
        prompt - cached_input,
        max(0, int(cache_write_tokens or 0)),
    )
    uncached_input = prompt - cached_input - cache_write
    charged_units = int(math.ceil(
        uncached_input * input_weight
        + completion * output_weight
        + cached_input * cache_read_weight
        + cache_write * cache_write_weight
    ))
    if raw_total <= 0:
        return get_daily_usage(uid)
    with _LOCK:
        conn = _get_conn()
        try:
            current = conn.execute(
                "SELECT tokens_used FROM daily_usage WHERE user_id = ? AND date = ?",
                (uid, _today()),
            ).fetchone()
            before = int(current["tokens_used"] if current else 0)
            uses_persistent_balance = quota_source == "persistent_balance"
            if uses_persistent_balance:
                conn.execute(
                    """UPDATE quota_bonus_balances
                       SET tokens_available = MAX(0, tokens_available - ?),
                           updated_at = datetime('now')
                       WHERE user_id = ?""",
                    (charged_units, uid),
                )
            else:
                conn.execute(
                    """INSERT INTO daily_usage (user_id, date, tokens_used)
                       VALUES (?, ?, ?)
                       ON CONFLICT (user_id, date)
                       DO UPDATE SET tokens_used = tokens_used + ?""",
                    (uid, _today(), charged_units, charged_units),
                )
            conn.execute(
                """INSERT INTO usage_events
                   (user_id, session_id, model_call_id, model, model_id, tier, quota_source,
                    prompt_tokens, completion_tokens, total_tokens, raw_tokens,
                    multiplier, uncached_input_tokens, cached_input_tokens,
                    cache_write_tokens, input_multiplier, output_multiplier,
                    cache_read_multiplier, cache_write_multiplier, charged_units)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uid,
                    str(session_id)[:100],
                    str(model_call_id)[:64],
                    str(model)[:200],
                    str(model_id)[:100],
                    str(tier)[:30],
                    str(quota_source)[:30],
                    prompt,
                    completion,
                    raw_total,
                    raw_total,
                    effective_multiplier,
                    uncached_input,
                    cached_input,
                    cache_write,
                    input_weight,
                    output_weight,
                    cache_read_weight,
                    cache_write_weight,
                    charged_units,
                ),
            )
            if not uses_persistent_balance:
                _consume_bonus_after_daily(conn, uid, before, before + charged_units)
            conn.commit()
            row = conn.execute(
                "SELECT tokens_used FROM daily_usage WHERE user_id = ? AND date = ?",
                (uid, _today()),
            ).fetchone()
            return int(row["tokens_used"] if row else 0)
        finally:
            conn.close()


def begin_model_call(
    *,
    user_id: str = "",
    session_id: str = "",
    source: str = "web",
    purpose: str = "chat",
    billing_scope: str = "user",
    provider: str = "",
    model: str = "",
) -> str:
    """Append a content-free upstream call start and return its request id."""
    request_id = uuid.uuid4().hex
    safe_source = source if source in {"web", "desktop", "api", "system"} else "system"
    safe_scope = billing_scope if billing_scope in {"user", "platform"} else "platform"
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO model_call_events
                   (request_id, user_id, session_id, source, purpose,
                    billing_scope, provider, model)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id,
                    str(user_id)[:100],
                    str(session_id)[:100],
                    safe_source,
                    str(purpose or "unknown")[:60],
                    safe_scope,
                    str(provider)[:100],
                    str(model)[:200],
                ),
            )
            conn.commit()
            return request_id
        finally:
            conn.close()


def increment_model_call_attempt(request_id: str) -> None:
    """Count one actual SDK invocation for a logical model call."""
    if not request_id:
        return
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """UPDATE model_call_events
                      SET attempt_count = attempt_count + 1
                    WHERE request_id = ? AND status = 'started'""",
                (str(request_id)[:64],),
            )
            conn.commit()
        finally:
            conn.close()


def _model_usage_int(usage, *names: str) -> int:
    for name in names:
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        if value is not None:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                continue
    return 0


def settle_model_call(
    request_id: str,
    *,
    status: str,
    usage=None,
    error_code: str = "",
) -> None:
    """Finalize an upstream call without changing any user quota balance."""
    if not request_id:
        return
    prompt = _model_usage_int(usage, "prompt_tokens", "input_tokens")
    completion = _model_usage_int(usage, "completion_tokens", "output_tokens")
    if isinstance(usage, dict):
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    else:
        details = (
            getattr(usage, "prompt_tokens_details", None)
            or getattr(usage, "input_tokens_details", None)
            or {}
        )
    cached = _model_usage_int(
        details,
        "cached_tokens",
        "cache_read_tokens",
    ) or _model_usage_int(usage, "cached_input_tokens", "cache_read_input_tokens")
    cache_write = _model_usage_int(
        details,
        "cache_write_tokens",
        "cache_creation_tokens",
    ) or _model_usage_int(
        usage,
        "cache_write_tokens",
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
    )
    safe_status = status if status in {
        "succeeded", "failed", "missing_usage", "canceled",
    } else "failed"
    if safe_status == "succeeded" and prompt + completion <= 0:
        safe_status = "missing_usage"
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """UPDATE model_call_events
                      SET status = ?, prompt_tokens = ?, completion_tokens = ?,
                          cached_input_tokens = ?, cache_write_tokens = ?,
                          raw_tokens = ?, error_code = ?,
                          completed_at = datetime('now')
                    WHERE request_id = ? AND status = 'started'""",
                (
                    safe_status,
                    prompt,
                    completion,
                    cached,
                    cache_write,
                    prompt + completion,
                    str(error_code)[:100],
                    str(request_id)[:64],
                ),
            )
            conn.commit()
        finally:
            conn.close()


def record_auth_event(
    *,
    event_type: str,
    success: bool,
    user_id: str = "",
    email: str = "",
    ip_address: str = "",
    user_agent: str = "",
    detail: str = "",
) -> None:
    """Append a structured authentication audit event without credentials."""
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO auth_events
                   (user_id, email, event_type, success, ip_address, user_agent, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(user_id)[:100] or None,
                    str(email).strip().lower()[:320],
                    str(event_type)[:50],
                    1 if success else 0,
                    str(ip_address)[:100],
                    str(user_agent)[:500],
                    str(detail)[:500],
                ),
            )
            conn.commit()
        finally:
            conn.close()


DAILY_TOKEN_LIMIT = int(os.environ.get("BAA_DAILY_TOKEN_LIMIT", "50000"))


def check_quota(uid: str) -> dict:
    used = get_daily_usage(uid)
    bonus_summary = get_bonus_summary(uid)
    bonus_remaining = bonus_summary["balance"]
    bonus_used_today = max(0, used - DAILY_TOKEN_LIMIT)
    limit = DAILY_TOKEN_LIMIT + bonus_used_today + bonus_remaining
    remaining = max(0, DAILY_TOKEN_LIMIT - used) + bonus_remaining
    now = datetime.now(_CN_TZ)
    next_reset = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return {
        "used": used,
        "limit": limit,
        "daily_limit": DAILY_TOKEN_LIMIT,
        "bonus_remaining": bonus_remaining,
        # Public semantic alias used by the account UI. The current persistent
        # pool includes referral/admin grants and is the balance that authorizes
        # standard and advanced hosted models.
        "paid_model_balance": bonus_remaining,
        "paid_model_total_granted": bonus_summary["total_granted"],
        "paid_model_used": bonus_summary["used"],
        "remaining": remaining,
        "exceeded": remaining <= 0,
        "reset_at": next_reset.isoformat(),
    }


# ---------------------------------------------------------------------------
#  Sponsor reminder cadence
# ---------------------------------------------------------------------------

SPONSOR_PROMPT_INTERVAL_DAYS = 15


def _utc_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_sponsor_prompt_status(uid: str, *, now: datetime | None = None) -> dict:
    """Return whether the account is due for its 15-day sponsor reminder."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT sponsor_prompt_last_shown_at FROM users WHERE id = ?", (uid,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {
            "should_show": False,
            "last_shown_at": None,
            "next_show_at": None,
            "interval_days": SPONSOR_PROMPT_INTERVAL_DAYS,
        }

    raw = row["sponsor_prompt_last_shown_at"]
    if not raw:
        return {
            "should_show": True,
            "last_shown_at": None,
            "next_show_at": None,
            "interval_days": SPONSOR_PROMPT_INTERVAL_DAYS,
        }

    try:
        last_shown = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        last_shown = None

    if last_shown is None:
        should_show = True
        next_show = None
    else:
        next_show = last_shown + timedelta(days=SPONSOR_PROMPT_INTERVAL_DAYS)
        should_show = current >= next_show

    return {
        "should_show": should_show,
        "last_shown_at": last_shown.isoformat() if last_shown else None,
        "next_show_at": next_show.isoformat() if next_show else None,
        "interval_days": SPONSOR_PROMPT_INTERVAL_DAYS,
    }


def mark_sponsor_prompt_shown(uid: str, *, now: datetime | None = None) -> dict:
    """Record an automatic sponsor prompt after it is actually displayed."""
    shown_at = _utc_timestamp(now)
    with _LOCK:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                "UPDATE users SET sponsor_prompt_last_shown_at = ? WHERE id = ?",
                (shown_at, uid),
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "ok": cursor.rowcount > 0,
        "shown_at": datetime.strptime(shown_at, "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=timezone.utc)
        .isoformat(),
        "interval_days": SPONSOR_PROMPT_INTERVAL_DAYS,
    }


# ---------------------------------------------------------------------------
#  Referral tracking
# ---------------------------------------------------------------------------

def _derive_referral_code(uid: str) -> str:
    """Stable short referral code derived from the user id (first 8 hex of a hash)."""
    import hashlib
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:8].upper()


def get_or_create_referral_code(uid: str) -> str:
    """Return the user's referral code, creating a referral row if needed.

    The code is deterministic (derived from the user id) so it is stable across
    calls and survives redeploys; the row exists mainly to track invitees.
    """
    code = _derive_referral_code(uid)
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM referrals WHERE referrer_id = ? AND referral_code = ?",
            (uid, code),
        ).fetchone()
        if not row:
            conn.execute(
                """INSERT OR IGNORE INTO referrals (referrer_id, referral_code, status)
                   VALUES (?, ?, 'pending')""",
                (uid, code),
            )
            conn.commit()
        return code
    finally:
        conn.close()


def get_referral_stats(uid: str) -> dict:
    """Aggregate referral counts for a user."""
    code = get_or_create_referral_code(uid)
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT status FROM referrals
               WHERE referrer_id = ? AND invitee_id IS NOT NULL""", (uid,)
        ).fetchall()
        total = len(rows)
        registered = sum(1 for r in rows if r["status"] in {"registered", "rewarded"})
        rewarded = sum(1 for r in rows if r["status"] == "rewarded")
        return {
            "referral_code": code,
            "total": total,
            "registered": registered,
            "rewarded": rewarded,
            "inviter_bonus": REFERRAL_INVITER_BONUS,
            "invitee_bonus": REFERRAL_INVITEE_BONUS,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  Desktop auth tokens (local persistence for the desktop client proxy)
# ---------------------------------------------------------------------------

def save_desktop_token(user_id: str, email: str, token: str) -> None:
    """Persist the cloud-issued auth token so the desktop client survives restarts."""
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=_DESKTOP_TOKEN_TTL_DAYS)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO desktop_tokens (user_id, email, token, expires_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                      email = excluded.email,
                      token = excluded.token,
                      created_at = datetime('now'),
                      expires_at = excluded.expires_at""",
                (user_id, email, token, expires_at),
            )
            conn.commit()
        finally:
            conn.close()


def get_desktop_token() -> dict | None:
    """Return the persisted desktop auth token row, or None if logged out."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT user_id, email, token FROM desktop_tokens "
            "WHERE expires_at IS NOT NULL AND expires_at > datetime('now') LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return {"user_id": row["user_id"], "email": row["email"], "token": row["token"]}
    finally:
        conn.close()


def clear_desktop_token() -> None:
    """Remove the persisted desktop auth token (logout)."""
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM desktop_tokens")
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
#  Read-only administrator reporting
# ---------------------------------------------------------------------------

def _reporting_window(days: int) -> tuple[int, str, str]:
    bounded_days = max(1, min(90, int(days or 30)))
    today = datetime.now(_CN_TZ).date()
    start = today - timedelta(days=bounded_days - 1)
    return bounded_days, start.isoformat(), today.isoformat()


_USAGE_SOURCE_SQL = """CASE
    WHEN session_id = 'desktop-llm-gateway'
      OR session_id LIKE 'desktop-llm-gateway:%' THEN 'desktop'
    WHEN session_id LIKE 'api:%' THEN 'api'
    ELSE 'web'
END"""


def get_admin_overview() -> dict:
    """Return content-free account and quota totals for the Shanghai day."""
    today = _today()
    conn = _get_conn()
    try:
        row = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM users) AS total_users,
                 (SELECT COUNT(*) FROM users
                    WHERE date(created_at, '+8 hours') = ?) AS registrations_today,
                 (SELECT COUNT(DISTINCT user_id) FROM (
                    SELECT user_id FROM usage_events
                     WHERE date(created_at, '+8 hours') = ?
                    UNION
                    SELECT user_id FROM auth_events
                     WHERE success = 1 AND event_type IN ('login', 'register')
                       AND user_id IS NOT NULL
                       AND date(created_at, '+8 hours') = ?
                 )) AS active_users_today,
                 (SELECT COUNT(DISTINCT user_id) FROM auth_events
                   WHERE success = 1 AND event_type IN ('login', 'register')
                     AND user_id IS NOT NULL
                     AND date(created_at, '+8 hours') = ?) AS login_users_today,
                 (SELECT COUNT(*) FROM usage_events
                   WHERE date(created_at, '+8 hours') = ?) AS calls_today,
                 (SELECT COUNT(*) FROM usage_events
                   WHERE (session_id = 'desktop-llm-gateway'
                          OR session_id LIKE 'desktop-llm-gateway:%')
                     AND date(created_at, '+8 hours') = ?) AS desktop_calls_today,
                 (SELECT COUNT(*) FROM usage_events
                   WHERE session_id LIKE 'api:%'
                     AND date(created_at, '+8 hours') = ?) AS api_calls_today,
                 (SELECT COUNT(*) FROM usage_events
                   WHERE session_id != 'desktop-llm-gateway'
                     AND session_id NOT LIKE 'desktop-llm-gateway:%'
                     AND session_id NOT LIKE 'api:%'
                     AND date(created_at, '+8 hours') = ?) AS web_calls_today,
                 (SELECT COALESCE(SUM(raw_tokens), 0) FROM usage_events
                   WHERE date(created_at, '+8 hours') = ?) AS raw_tokens_today,
                 (SELECT COALESCE(SUM(charged_units), 0) FROM usage_events
                   WHERE date(created_at, '+8 hours') = ?) AS charged_units_today,
                 (SELECT COALESCE(SUM(tokens_used), 0) FROM daily_usage
                   WHERE date = ?) AS free_units_today,
                 (SELECT COALESCE(SUM(charged_units), 0) FROM usage_events
                   WHERE quota_source = 'persistent_balance'
                     AND date(created_at, '+8 hours') = ?) AS persistent_units_today,
                 (SELECT COUNT(*) FROM auth_events
                   WHERE event_type = 'login' AND success = 0
                     AND date(created_at, '+8 hours') = ?) AS failed_logins_today""",
            (today,) * 13,
        ).fetchone()
        result = {key: int(row[key] or 0) for key in row.keys()}
        calls = conn.execute(
            """SELECT COUNT(*) AS logical_model_calls_today,
                      COALESCE(SUM(attempt_count), 0) AS upstream_calls_today,
                      COALESCE(SUM(raw_tokens), 0) AS observed_tokens_today,
                      COALESCE(SUM(CASE WHEN billing_scope = 'user' THEN 1 ELSE 0 END), 0)
                          AS user_model_calls_today,
                      COALESCE(SUM(CASE WHEN billing_scope = 'platform' THEN 1 ELSE 0 END), 0)
                          AS platform_model_calls_today,
                      COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0)
                          AS failed_model_calls_today,
                      COALESCE(SUM(CASE WHEN status = 'missing_usage' THEN 1 ELSE 0 END), 0)
                          AS missing_usage_calls_today,
                      COALESCE(SUM(CASE WHEN status = 'started' THEN 1 ELSE 0 END), 0)
                          AS unsettled_model_calls_today
                 FROM model_call_events
                WHERE date(started_at, '+8 hours') = ?""",
            (today,),
        ).fetchone()
        result.update({key: int(calls[key] or 0) for key in calls.keys()})
        result["date"] = today
        result["timezone"] = "Asia/Shanghai"
        return result
    finally:
        conn.close()


def get_admin_trends(days: int = 30) -> dict:
    """Return bounded daily registration, activity, and quota series."""
    bounded_days, start, today = _reporting_window(days)
    conn = _get_conn()
    try:
        registrations = conn.execute(
            """SELECT date(created_at, '+8 hours') AS day, COUNT(*) AS value
                 FROM users
                WHERE date(created_at, '+8 hours') BETWEEN ? AND ?
                GROUP BY day""",
            (start, today),
        ).fetchall()
        usage = conn.execute(
            """SELECT date(created_at, '+8 hours') AS day,
                      COUNT(*) AS calls,
                      SUM(CASE WHEN session_id = 'desktop-llm-gateway'
                                OR session_id LIKE 'desktop-llm-gateway:%'
                               THEN 1 ELSE 0 END) AS desktop_calls,
                      SUM(CASE WHEN session_id LIKE 'api:%'
                               THEN 1 ELSE 0 END) AS api_calls,
                      SUM(CASE WHEN session_id != 'desktop-llm-gateway'
                                AND session_id NOT LIKE 'desktop-llm-gateway:%'
                                AND session_id NOT LIKE 'api:%'
                               THEN 1 ELSE 0 END) AS web_calls,
                      COUNT(DISTINCT user_id) AS model_users,
                      COALESCE(SUM(raw_tokens), 0) AS raw_tokens,
                      COALESCE(SUM(charged_units), 0) AS charged_units
                 FROM usage_events
                WHERE date(created_at, '+8 hours') BETWEEN ? AND ?
                GROUP BY day""",
            (start, today),
        ).fetchall()
        active = conn.execute(
            """SELECT day, COUNT(DISTINCT user_id) AS value FROM (
                   SELECT date(created_at, '+8 hours') AS day, user_id
                     FROM usage_events
                    WHERE date(created_at, '+8 hours') BETWEEN ? AND ?
                   UNION
                   SELECT date(created_at, '+8 hours') AS day, user_id
                     FROM auth_events
                    WHERE success = 1 AND event_type IN ('login', 'register')
                      AND user_id IS NOT NULL
                      AND date(created_at, '+8 hours') BETWEEN ? AND ?
                 ) GROUP BY day""",
            (start, today, start, today),
        ).fetchall()
        login_active = conn.execute(
            """SELECT date(created_at, '+8 hours') AS day,
                      COUNT(DISTINCT user_id) AS value
                 FROM auth_events
                WHERE success = 1 AND event_type IN ('login', 'register')
                  AND user_id IS NOT NULL
                  AND date(created_at, '+8 hours') BETWEEN ? AND ?
                GROUP BY day""",
            (start, today),
        ).fetchall()
        models = conn.execute(
            """SELECT COALESCE(NULLIF(model, ''), NULLIF(model_id, ''), '未知模型') AS model,
                      tier, quota_source, COUNT(*) AS calls,
                      COALESCE(SUM(raw_tokens), 0) AS raw_tokens,
                      COALESCE(SUM(charged_units), 0) AS charged_units
                 FROM usage_events
                WHERE date(created_at, '+8 hours') BETWEEN ? AND ?
                GROUP BY model, tier, quota_source
                ORDER BY charged_units DESC, raw_tokens DESC
                LIMIT 20""",
            (start, today),
        ).fetchall()
        sources = conn.execute(
            f"""SELECT {_USAGE_SOURCE_SQL} AS source,
                       COUNT(*) AS calls,
                       COALESCE(SUM(raw_tokens), 0) AS raw_tokens,
                       COALESCE(SUM(charged_units), 0) AS charged_units
                  FROM usage_events
                 WHERE date(created_at, '+8 hours') BETWEEN ? AND ?
                 GROUP BY source
                 ORDER BY charged_units DESC, raw_tokens DESC""",
            (start, today),
        ).fetchall()
        model_operations = conn.execute(
            """SELECT purpose, billing_scope, status,
                      COUNT(*) AS logical_calls,
                      COALESCE(SUM(attempt_count), 0) AS upstream_calls,
                      COALESCE(SUM(raw_tokens), 0) AS raw_tokens
                 FROM model_call_events
                WHERE date(started_at, '+8 hours') BETWEEN ? AND ?
                GROUP BY purpose, billing_scope, status
                ORDER BY raw_tokens DESC, upstream_calls DESC, purpose ASC""",
            (start, today),
        ).fetchall()
    finally:
        conn.close()

    registration_map = {row["day"]: int(row["value"] or 0) for row in registrations}
    usage_map = {row["day"]: row for row in usage}
    active_map = {row["day"]: int(row["value"] or 0) for row in active}
    login_map = {row["day"]: int(row["value"] or 0) for row in login_active}
    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    series = []
    for offset in range(bounded_days):
        day = (start_date + timedelta(days=offset)).isoformat()
        usage_row = usage_map.get(day)
        series.append({
            "date": day,
            "registrations": registration_map.get(day, 0),
            "active_users": active_map.get(day, 0),
            "login_users": login_map.get(day, 0),
            "model_users": int(usage_row["model_users"] or 0) if usage_row else 0,
            "calls": int(usage_row["calls"] or 0) if usage_row else 0,
            "desktop_calls": int(usage_row["desktop_calls"] or 0) if usage_row else 0,
            "web_calls": int(usage_row["web_calls"] or 0) if usage_row else 0,
            "api_calls": int(usage_row["api_calls"] or 0) if usage_row else 0,
            "raw_tokens": int(usage_row["raw_tokens"] or 0) if usage_row else 0,
            "charged_units": int(usage_row["charged_units"] or 0) if usage_row else 0,
        })
    return {
        "days": bounded_days,
        "start_date": start,
        "end_date": today,
        "series": series,
        "models": [
            {
                "model": row["model"],
                "tier": row["tier"],
                "quota_source": row["quota_source"],
                "calls": int(row["calls"] or 0),
                "raw_tokens": int(row["raw_tokens"] or 0),
                "charged_units": int(row["charged_units"] or 0),
            }
            for row in models
        ],
        "sources": [
            {
                "source": row["source"],
                "calls": int(row["calls"] or 0),
                "raw_tokens": int(row["raw_tokens"] or 0),
                "charged_units": int(row["charged_units"] or 0),
            }
            for row in sources
        ],
        "model_operations": [
            {
                "purpose": row["purpose"],
                "billing_scope": row["billing_scope"],
                "status": row["status"],
                "logical_calls": int(row["logical_calls"] or 0),
                "upstream_calls": int(row["upstream_calls"] or 0),
                "raw_tokens": int(row["raw_tokens"] or 0),
            }
            for row in model_operations
        ],
    }


def list_admin_users(
    *, search: str = "", page: int = 1, page_size: int = 25, sort: str = "created_desc"
) -> dict:
    """Return a safe, paginated account summary for administrator use."""
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 25)))
    today = _today()
    _, start_30d, _ = _reporting_window(30)
    normalized_search = str(search or "").strip().lower()
    where_sql = ""
    args: list[object] = [today, start_30d, today, today]
    if normalized_search:
        escaped = normalized_search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where_sql = "WHERE lower(u.email) LIKE ? ESCAPE '\\'"
        args.append(f"{escaped}%")
    order_by = {
        "created_asc": "u.created_at ASC",
        "email_asc": "u.email COLLATE NOCASE ASC",
        "usage_desc": "raw_tokens_30d DESC, u.created_at DESC",
        "active_desc": "last_active_at DESC, u.created_at DESC",
        "created_desc": "u.created_at DESC",
    }.get(sort, "u.created_at DESC")
    conn = _get_conn()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS value FROM users u {where_sql}",
            tuple(args[4:]),
        ).fetchone()["value"]
        rows = conn.execute(
            f"""WITH auth_rollup AS (
                    SELECT user_id,
                           MAX(CASE WHEN success = 1 AND event_type IN ('login', 'register')
                                    THEN created_at END) AS last_login_at
                      FROM auth_events GROUP BY user_id
                 ), usage_rollup AS (
                    SELECT user_id, MAX(created_at) AS last_usage_at,
                           SUM(CASE WHEN date(created_at, '+8 hours') = ?
                                    THEN raw_tokens ELSE 0 END) AS raw_tokens_today,
                           SUM(CASE WHEN date(created_at, '+8 hours') >= ?
                                    THEN raw_tokens ELSE 0 END) AS raw_tokens_30d,
                           SUM(CASE WHEN date(created_at, '+8 hours') = ?
                                    THEN charged_units ELSE 0 END) AS charged_units_today
                      FROM usage_events GROUP BY user_id
                 ), daily AS (
                    SELECT user_id, tokens_used FROM daily_usage WHERE date = ?
                 )
                 SELECT u.id, u.email, u.created_at,
                        a.last_login_at, x.last_usage_at,
                        CASE
                          WHEN a.last_login_at IS NULL THEN x.last_usage_at
                          WHEN x.last_usage_at IS NULL THEN a.last_login_at
                          WHEN a.last_login_at >= x.last_usage_at THEN a.last_login_at
                          ELSE x.last_usage_at
                        END AS last_active_at,
                        COALESCE(x.raw_tokens_today, 0) AS raw_tokens_today,
                        COALESCE(x.raw_tokens_30d, 0) AS raw_tokens_30d,
                        COALESCE(x.charged_units_today, 0) AS charged_units_today,
                        COALESCE(d.tokens_used, 0) AS free_units_today,
                        COALESCE(b.tokens_available, 0) AS bonus_balance
                   FROM users u
                   LEFT JOIN auth_rollup a ON a.user_id = u.id
                   LEFT JOIN usage_rollup x ON x.user_id = u.id
                   LEFT JOIN daily d ON d.user_id = u.id
                   LEFT JOIN quota_bonus_balances b ON b.user_id = u.id
                   {where_sql}
                  ORDER BY {order_by}
                  LIMIT ? OFFSET ?""",
            tuple(args + [page_size, (page - 1) * page_size]),
        ).fetchall()
    finally:
        conn.close()
    return {
        "items": [dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": int(total or 0),
        "pages": max(1, math.ceil(int(total or 0) / page_size)),
    }


def get_admin_user_detail(uid: str) -> dict | None:
    """Return one account summary plus recent content-free ledger events."""
    today = _today()
    _, start_30d, _ = _reporting_window(30)
    conn = _get_conn()
    try:
        base = conn.execute(
            """SELECT u.id, u.email, u.created_at,
                      (SELECT MAX(created_at) FROM auth_events
                        WHERE user_id = u.id AND success = 1
                          AND event_type IN ('login', 'register')) AS last_login_at,
                      (SELECT MAX(created_at) FROM usage_events
                        WHERE user_id = u.id) AS last_usage_at,
                      (SELECT COALESCE(SUM(raw_tokens), 0) FROM usage_events
                        WHERE user_id = u.id
                          AND date(created_at, '+8 hours') = ?) AS raw_tokens_today,
                      (SELECT COALESCE(SUM(raw_tokens), 0) FROM usage_events
                        WHERE user_id = u.id
                          AND date(created_at, '+8 hours') >= ?) AS raw_tokens_30d,
                      (SELECT COALESCE(SUM(charged_units), 0) FROM usage_events
                        WHERE user_id = u.id
                          AND date(created_at, '+8 hours') = ?) AS charged_units_today,
                      COALESCE((SELECT tokens_used FROM daily_usage
                        WHERE user_id = u.id AND date = ?), 0) AS free_units_today,
                      COALESCE((SELECT tokens_available FROM quota_bonus_balances
                        WHERE user_id = u.id), 0) AS bonus_balance
                 FROM users u WHERE u.id = ?""",
            (today, start_30d, today, today, uid),
        ).fetchone()
        if not base:
            return None
        user = dict(base)
        login_at = user.get("last_login_at")
        usage_at = user.get("last_usage_at")
        user["last_active_at"] = max(
            (value for value in (login_at, usage_at) if value), default=None
        )
        usage = conn.execute(
            f"""SELECT model, tier, quota_source, raw_tokens, charged_units,
                       {_USAGE_SOURCE_SQL} AS source, created_at
                 FROM usage_events WHERE user_id = ?
                ORDER BY created_at DESC LIMIT 20""",
            (uid,),
        ).fetchall()
        model_calls = conn.execute(
            """SELECT source, purpose, billing_scope, provider, model, status,
                      attempt_count, raw_tokens, error_code, started_at, completed_at
                 FROM model_call_events WHERE user_id = ?
                ORDER BY started_at DESC LIMIT 30""",
            (uid,),
        ).fetchall()
        auth = conn.execute(
            """SELECT event_type, success, created_at
                 FROM auth_events WHERE user_id = ?
                ORDER BY created_at DESC LIMIT 20""",
            (uid,),
        ).fetchall()
    finally:
        conn.close()
    user["recent_usage"] = [dict(row) for row in usage]
    user["recent_model_calls"] = [dict(row) for row in model_calls]
    user["recent_auth"] = [dict(row) for row in auth]
    return user


def record_admin_audit_event(
    *, admin_user_id: str, admin_email: str, action: str,
    target_user_id: str = "", detail: str = "",
) -> None:
    """Append an administrator audit event without request content or secrets."""
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO admin_audit_events
                   (admin_user_id, admin_email, action, target_user_id, detail)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    str(admin_user_id)[:100],
                    str(admin_email).strip().lower()[:320],
                    str(action)[:80],
                    str(target_user_id)[:100],
                    str(detail)[:500],
                ),
            )
            conn.commit()
        finally:
            conn.close()


def list_admin_audit_events(page: int = 1, page_size: int = 50) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 50)))
    conn = _get_conn()
    try:
        total = int(conn.execute(
            "SELECT COUNT(*) AS value FROM admin_audit_events"
        ).fetchone()["value"] or 0)
        rows = conn.execute(
            """SELECT admin_email, action, target_user_id, detail, created_at
                 FROM admin_audit_events
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
            (page_size, (page - 1) * page_size),
        ).fetchall()
    finally:
        conn.close()
    return {
        "items": [dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, math.ceil(total / page_size)),
    }
