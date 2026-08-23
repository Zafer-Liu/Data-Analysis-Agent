"""SQLite-coordinated global slots for memory-intensive local imports."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from infrastructure.paths import data_path


MAX_CONCURRENT_IMPORTS = max(1, int(os.environ.get("BAA_MAX_CONCURRENT_IMPORTS", "2")))
SLOT_LEASE_SECONDS = max(30, int(os.environ.get("BAA_IMPORT_SLOT_LEASE_SECONDS", "600")))
RETRY_SECONDS = max(0.1, float(os.environ.get("BAA_IMPORT_SLOT_RETRY_SECONDS", "1")))


class ImportSlot:
    def __init__(self, store: "ImportSlotStore", slot: int, owner_id: str):
        self._store, self.slot, self.owner_id = store, slot, owner_id
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        def renew() -> None:
            while not self._stop.wait(max(5, SLOT_LEASE_SECONDS / 3)):
                if not self._store.renew(self.slot, self.owner_id):
                    return

        self._thread = threading.Thread(target=renew, name="large-import-slot", daemon=True)
        self._thread.start()

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._store.release(self.slot, self.owner_id)


class ImportSlotStore:
    def __init__(self, path: Path | None = None, max_slots: int = MAX_CONCURRENT_IMPORTS):
        self.path = path or data_path("outputs", "jobs", "large_import_slots.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_slots = max(1, max_slots)
        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS large_import_slots ("
                "slot INTEGER PRIMARY KEY, owner_id TEXT NOT NULL, expires_at REAL NOT NULL)"
            )
            conn.commit()

    def _connection(self):
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return _ClosingConnection(conn)

    def acquire(self, owner_id: str) -> ImportSlot | None:
        now = time.time()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for slot in range(1, self.max_slots + 1):
                row = conn.execute(
                    "SELECT owner_id, expires_at FROM large_import_slots WHERE slot = ?", (slot,)
                ).fetchone()
                if row and row[1] > now and row[0] != owner_id:
                    continue
                conn.execute(
                    "INSERT INTO large_import_slots(slot, owner_id, expires_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(slot) DO UPDATE SET owner_id=excluded.owner_id, expires_at=excluded.expires_at",
                    (slot, owner_id, now + SLOT_LEASE_SECONDS),
                )
                conn.commit()
                result = ImportSlot(self, slot, owner_id)
                result.start()
                return result
            conn.rollback()
        return None

    def renew(self, slot: int, owner_id: str) -> bool:
        with self._connection() as conn:
            updated = conn.execute(
                "UPDATE large_import_slots SET expires_at = ? WHERE slot = ? AND owner_id = ?",
                (time.time() + SLOT_LEASE_SECONDS, slot, owner_id),
            ).rowcount
            conn.commit()
        return bool(updated)

    def release(self, slot: int, owner_id: str) -> bool:
        with self._connection() as conn:
            deleted = conn.execute(
                "DELETE FROM large_import_slots WHERE slot = ? AND owner_id = ?",
                (slot, owner_id),
            ).rowcount
            conn.commit()
        return bool(deleted)


class _ClosingConnection:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *args):
        self._conn.close()


@contextmanager
def acquire_large_import_slot(ctx) -> Iterator[ImportSlot]:
    store = ImportSlotStore()
    owner_id = str(getattr(ctx, "job_id", f"inline-{threading.get_ident()}"))
    waiting = False
    while True:
        ctx.check_canceled()
        slot = store.acquire(owner_id)
        if slot is not None:
            if hasattr(ctx, "record_event"):
                ctx.record_event("large_import_slot_acquired", slot=slot.slot)
            break
        if not waiting:
            ctx.set_progress(1, "等待大型文件导入槽位")
            if hasattr(ctx, "record_event"):
                ctx.record_event("large_import_slot_waiting")
            waiting = True
        time.sleep(RETRY_SECONDS)
    try:
        yield slot
    finally:
        slot.release()
        if hasattr(ctx, "record_event"):
            ctx.record_event("large_import_slot_released", slot=slot.slot)
