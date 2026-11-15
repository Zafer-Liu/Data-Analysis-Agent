"""Legacy import-slot facade over the durable resource coordinator (no TTL)."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path

from infrastructure.resource_budget import BudgetSpec, BudgetExceeded, ResourceCoordinator, process_identity

MAX_CONCURRENT_IMPORTS = max(1, int(os.environ.get('BAA_MAX_CONCURRENT_IMPORTS', '2')))
RETRY_SECONDS = max(0.1, float(os.environ.get('BAA_IMPORT_SLOT_RETRY_SECONDS', '1')))


class ImportSlot:
    def __init__(self, store, slot, owner_id, lease):
        self._store, self.slot, self.owner_id, self.lease = store, slot, owner_id, lease

    def start(self):
        # Process birth identities replace lease heartbeats; elapsed time never
        # proves a worker has exited.
        return self

    def bind_process(self, process):
        return self.lease.bind_process(process)

    def release(self):
        self.lease.release()
        self._store._leases.pop((self.slot, self.owner_id), None)


class ImportSlotStore:
    def __init__(self, path=None, max_slots=MAX_CONCURRENT_IMPORTS):
        self.path = Path(path) if path is not None else None
        self.max_slots = max_slots
        root = self.path.with_name(self.path.name + '.resources') if self.path else None
        self.coordinator = ResourceCoordinator(root, max_slots=max_slots)
        self._leases = {}

    def acquire(self, owner_id):
        try:
            lease = self.coordinator.reserve('legacy-import', str(owner_id), BudgetSpec())
        except BudgetExceeded:
            return None
        if lease.snapshot()['controller'] != process_identity():
            return None
        slot = self.coordinator.snapshot()['executions'].index(str(owner_id)) + 1
        result = ImportSlot(self, slot, str(owner_id), lease)
        self._leases[(slot, str(owner_id))] = result
        return result

    def renew(self, slot, owner_id):
        result = self._leases.get((slot, owner_id))
        return bool(result and result.lease.snapshot())

    def release(self, slot, owner_id):
        result = self._leases.get((slot, owner_id))
        if result is None:
            return False
        result.release()
        return True


@contextmanager
def acquire_large_import_slot(ctx):
    store = ImportSlotStore()
    owner_id = str(getattr(ctx, 'job_id', f'inline-{os.getpid()}-{threading.get_ident()}'))
    waiting = False
    while True:
        ctx.check_canceled()
        slot = store.acquire(owner_id)
        if slot is not None:
            break
        if not waiting:
            ctx.set_progress(1, '等待大型文件导入槽位')
            waiting = True
        threading.Event().wait(RETRY_SECONDS)
    if hasattr(ctx, 'record_event'):
        ctx.record_event('large_import_slot_acquired', slot=slot.slot)
    try:
        yield slot
    finally:
        slot.release()
        if hasattr(ctx, 'record_event'):
            ctx.record_event('large_import_slot_released', slot=slot.slot)
