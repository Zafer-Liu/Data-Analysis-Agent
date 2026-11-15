"""Scoped immutable snapshots and atomic DuckDB operation/version publication.

Files are synced and renamed before the single catalog transaction. A crash in
that window leaves a recoverable orphan, never an active half snapshot. JSON is
only a disposable cache. Heavy workers never open this database.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .dataset_manifest import (DatasetHandle, ManifestError, canonical_json, manifest_digest,
                               safe_snapshot_root, validate_id, validate_manifest, validate_scope)
from .workspace_db_owner import WorkspaceDBOwner, WorkspaceOwnerError, _FileLock
from infrastructure.resource_budget import process_alive, process_identity


class CatalogError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sync_json(path, value):
    temporary = path.with_name('.tmp-' + uuid.uuid4().hex[:12])
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(canonical_json(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class DatasetCatalog:
    def __init__(self, root=None, *, owner_id, session_id='', workspace_id=''):
        from infrastructure.paths import content_data_path
        self.owner_id = validate_scope(owner_id, 'owner_id')
        self.session_id = validate_scope(session_id, 'session_id', allow_empty=True)
        self.workspace_id = validate_scope(workspace_id, 'workspace_id', allow_empty=True)
        self.scope = {k: getattr(self, k) for k in ('owner_id', 'session_id', 'workspace_id')}
        # Explicit roots are also scope-separated; a differently scoped facade
        # can neither inspect nor recover another owner's operations.
        scope_hash = hashlib.sha256(canonical_json(self.scope).encode('utf-8')).hexdigest()
        base = Path(root) if root is not None else content_data_path('datasets')
        base.mkdir(parents=True, exist_ok=True)
        safe_snapshot_root(base)
        self.root = base.absolute() / scope_hash[:32]
        self.root.mkdir(exist_ok=True)
        safe_snapshot_root(self.root)
        for name in ('staging', 'snapshots', 'operations'):
            (self.root / name).mkdir(exist_ok=True)
            safe_snapshot_root(self.root / name)
        self.path = self.root / 'catalog.duckdb'
        with WorkspaceDBOwner(self.path) as owner:
            owner.call(lambda conn: conn.execute('''
                CREATE TABLE IF NOT EXISTS dataset_versions (
                    snapshot_id VARCHAR PRIMARY KEY, dataset_id VARCHAR NOT NULL,
                    version BIGINT NOT NULL, digest VARCHAR NOT NULL, manifest VARCHAR NOT NULL,
                    UNIQUE(dataset_id, version));
                CREATE TABLE IF NOT EXISTS dataset_heads (
                    dataset_id VARCHAR PRIMARY KEY, snapshot_id VARCHAR NOT NULL, version BIGINT NOT NULL);
                CREATE TABLE IF NOT EXISTS import_operations (
                    operation_id VARCHAR PRIMARY KEY, idempotency_key VARCHAR UNIQUE NOT NULL,
                    plan_hash VARCHAR NOT NULL, dataset_id VARCHAR NOT NULL, snapshot_id VARCHAR NOT NULL,
                    status VARCHAR NOT NULL, fence VARCHAR NOT NULL, controller VARCHAR NOT NULL,
                    workers VARCHAR NOT NULL, error VARCHAR NOT NULL, created_at VARCHAR NOT NULL);
            ''') and None)

    def _call(self, callback, *, transaction=False):
        with WorkspaceDBOwner(self.path) as owner:
            return owner.call(callback, transaction=transaction)

    def _scope_check(self, value):
        if any(value.get(k) != v for k, v in self.scope.items()):
            raise CatalogError('scope_mismatch', 'Dataset metadata belongs to a different scope')

    def staging_dir(self, operation_id):
        return self.root / 'staging' / validate_id(operation_id, 'operation_id')

    def snapshot_dir(self, snapshot_id):
        return self.root / 'snapshots' / validate_id(snapshot_id, 'snapshot_id')

    @staticmethod
    def _operation_row(row):
        if row is None:
            raise KeyError('Import operation not found')
        result = dict(zip(('operation_id', 'idempotency_key', 'plan_hash', 'dataset_id', 'snapshot_id',
                           'status', 'fence', 'controller', 'workers', 'error', 'created_at'), row))
        for key in ('controller', 'workers'):
            result[key] = json.loads(result[key])
        return result

    def operation_status(self, operation_id):
        validate_id(operation_id, 'operation_id')
        return self._call(lambda c: self._operation_row(c.execute(
            'SELECT * FROM import_operations WHERE operation_id=?', [operation_id]).fetchone()))

    def begin_import(self, plan, *, idempotency_key=''):
        self._scope_check(plan)
        encoded = canonical_json({k: v for k, v in plan.items() if k != 'plan_hash'})
        digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        if plan.get('plan_hash', digest) != digest:
            raise CatalogError('plan_changed', 'Import plan hash does not match')
        key = idempotency_key or digest
        if not isinstance(key, str) or not key or len(key) > 512:
            raise ValueError('idempotency_key must be bounded text')
        def begin(conn):
            row = conn.execute('SELECT * FROM import_operations WHERE idempotency_key=?', [key]).fetchone()
            if row:
                operation = self._operation_row(row)
                if operation['plan_hash'] != digest:
                    raise CatalogError('idempotency_conflict', 'Key already identifies a different import plan')
                return operation
            dataset_id = (plan.get('options') or {}).get('dataset_id') or plan.get('dataset_id') or uuid.uuid4().hex
            validate_id(dataset_id, 'dataset_id')
            operation_id, snapshot_id = uuid.uuid4().hex, uuid.uuid4().hex
            conn.execute('INSERT INTO import_operations VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         [operation_id, key, digest, dataset_id, snapshot_id, 'staging', '', '{}', '[]', '', _now()])
            return self._operation_row(conn.execute('SELECT * FROM import_operations WHERE operation_id=?', [operation_id]).fetchone())
        return self._call(begin, transaction=True)

    @contextmanager
    def claim_operation(self, operation_id):
        """Real OS exclusion plus a durable fencing token, with no TTL takeover."""
        validate_id(operation_id, 'operation_id')
        try:
            lock = _FileLock(self.root / 'operations' / (operation_id + '.lock'))
        except WorkspaceOwnerError as exc:
            raise CatalogError('operation_busy', 'Another executor owns this operation') from exc
        fence = uuid.uuid4().hex
        try:
            def claim(conn):
                operation = self._operation_row(conn.execute('SELECT * FROM import_operations WHERE operation_id=?', [operation_id]).fetchone())
                if operation['status'] == 'published':
                    return operation
                if any(process_alive(p) for p in operation['workers']):
                    raise CatalogError('operation_busy', 'A previous materialization worker has not exited')
                conn.execute("UPDATE import_operations SET status='running',fence=?,controller=?,workers='[]',error='' WHERE operation_id=?",
                             [fence, canonical_json(process_identity()), operation_id])
                return self._operation_row(conn.execute('SELECT * FROM import_operations WHERE operation_id=?', [operation_id]).fetchone())
            operation = self._call(claim, transaction=True)
            try:
                yield operation
            except BaseException as exc:
                self._call(lambda c: c.execute("UPDATE import_operations SET status='failed',error=? WHERE operation_id=? AND fence=? AND status!='published'",
                                              [str(exc)[:1000], operation_id, fence]) and None, transaction=True)
                raise
        finally:
            lock.close()

    def observe_worker(self, operation_id, fence, identity):
        def observe(conn):
            op = self._operation_row(conn.execute('SELECT * FROM import_operations WHERE operation_id=?', [operation_id]).fetchone())
            if op['fence'] != fence or op['status'] != 'running':
                raise CatalogError('operation_fenced', 'Operation attempt has been replaced')
            workers = op['workers']
            if identity not in workers:
                workers.append(identity)
            conn.execute('UPDATE import_operations SET workers=? WHERE operation_id=?', [canonical_json(workers), operation_id])
        self._call(observe, transaction=True)

    def publish(self, manifest, *, idempotency_key, expected_version=None, budget=None, cancel=None):
        import time
        from infrastructure.resource_budget import BudgetSpec
        from .dataset_snapshot import check_cancel
        started = time.monotonic()
        spec = BudgetSpec.from_dict(budget) if budget is not None else None
        value = validate_manifest(manifest)
        self._scope_check(value)
        if value['status'] != 'ready':
            raise CatalogError('invalid_manifest', 'Only complete ready snapshots may be published')
        if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
            raise ValueError('expected_version must be a nonnegative integer')
        operation = self._call(lambda c: self._operation_row(c.execute(
            'SELECT * FROM import_operations WHERE idempotency_key=?', [idempotency_key]).fetchone()))
        if (value['dataset_id'], value['snapshot_id']) != (operation['dataset_id'], operation['snapshot_id']):
            raise CatalogError('idempotency_conflict', 'Manifest identity differs from its reserved operation')
        if operation['status'] == 'published':
            existing = self.get(value['dataset_id'], snapshot_id=value['snapshot_id']).manifest
            content = lambda v: manifest_digest({k: item for k, item in v.items() if k not in ('operation_id', 'operation_fence', 'manifest_hash')})
            if content(value) != content(existing):
                raise CatalogError('idempotency_conflict', 'Key already published different snapshot content')
            return existing
        # Contract callers may publish a staged operation without an explicit claim.
        if not value.get('operation_fence'):
            with self.claim_operation(operation['operation_id']) as claimed:
                return self.publish({**value, 'operation_id': claimed['operation_id'], 'operation_fence': claimed['fence']},
                                    idempotency_key=idempotency_key, expected_version=expected_version, budget=budget, cancel=cancel)
        if operation['status'] != 'running' or operation['fence'] != value['operation_fence']:
            raise CatalogError('operation_fenced', 'Stale operation publication rejected')
        stage, destination = self.staging_dir(operation['operation_id']), self.snapshot_dir(value['snapshot_id'])
        if destination.exists():
            raise CatalogError('snapshot_conflict', 'Uncommitted snapshot directory already exists; recover first')
        validate_manifest(value, root=stage, verify_files=True, budget=spec, cancel=cancel)
        check_cancel(cancel)
        value['manifest_hash'] = manifest_digest(value)
        _sync_json(stage / 'manifest.json', value)
        os.replace(stage, destination)
        # The directory rename precedes COMMIT. If commit fails, recovery owns
        # this known orphan; never remove a potentially committed directory here.
        def commit(conn):
            check_cancel(cancel)
            if spec:
                spec.check_usage(run_seconds=time.monotonic() - started)
            current = self._operation_row(conn.execute('SELECT * FROM import_operations WHERE operation_id=?', [operation['operation_id']]).fetchone())
            if current['status'] != 'running' or current['fence'] != value['operation_fence']:
                raise CatalogError('operation_fenced', 'Operation no longer owns publication')
            head = conn.execute('SELECT version FROM dataset_heads WHERE dataset_id=?', [value['dataset_id']]).fetchone()
            version = head[0] if head else 0
            if expected_version is not None and expected_version != version:
                raise CatalogError('version_conflict', 'Active dataset version changed since planning')
            published = {**value, 'catalog_version': version + 1, 'published_at': _now()}
            conn.execute('INSERT INTO dataset_versions VALUES (?,?,?,?,?)',
                         [value['snapshot_id'], value['dataset_id'], version + 1, value['manifest_hash'], canonical_json(published)])
            conn.execute('INSERT OR REPLACE INTO dataset_heads VALUES (?,?,?)', [value['dataset_id'], value['snapshot_id'], version + 1])
            conn.execute("UPDATE import_operations SET status='published' WHERE operation_id=? AND fence=?", [operation['operation_id'], current['fence']])
            return published
        result = self._call(commit, transaction=True)
        self._refresh_cache()
        return result

    def get(self, dataset_id, *, snapshot_id='', verify=False):
        validate_id(dataset_id, 'dataset_id')
        if snapshot_id:
            validate_id(snapshot_id, 'snapshot_id')
        def fetch(conn):
            if snapshot_id:
                row = conn.execute('SELECT manifest FROM dataset_versions WHERE dataset_id=? AND snapshot_id=?', [dataset_id, snapshot_id]).fetchone()
            else:
                row = conn.execute('SELECT v.manifest FROM dataset_versions v JOIN dataset_heads h ON h.snapshot_id=v.snapshot_id WHERE h.dataset_id=?', [dataset_id]).fetchone()
            if row is None:
                raise KeyError('Dataset snapshot not found')
            return json.loads(row[0])
        value = self._call(fetch)
        self._scope_check(value)
        root = self.snapshot_dir(value['snapshot_id'])
        validate_manifest(value, root=root, verify_files=verify)
        return DatasetHandle(value, root)

    def list(self):
        return self._call(lambda c: [json.loads(r[0]) for r in c.execute(
            'SELECT v.manifest FROM dataset_versions v JOIN dataset_heads h ON h.snapshot_id=v.snapshot_id ORDER BY h.dataset_id').fetchall()])

    def versions(self, dataset_id):
        validate_id(dataset_id, 'dataset_id')
        return self._call(lambda c: [json.loads(r[0]) for r in c.execute(
            'SELECT manifest FROM dataset_versions WHERE dataset_id=? ORDER BY version', [dataset_id]).fetchall()])

    def _refresh_cache(self):
        try:
            _sync_json(self.root / 'catalog.json', self.list())
        except (OSError, WorkspaceOwnerError):
            # The authoritative transaction has already committed.
            pass

    def recover(self):
        removed, active = [], []
        operations = self._call(lambda c: [self._operation_row(row) for row in c.execute('SELECT * FROM import_operations').fetchall()])
        for op in operations:
            if op['status'] == 'published':
                continue
            try:
                lock = _FileLock(self.root / 'operations' / (op['operation_id'] + '.lock'))
            except WorkspaceOwnerError:
                active.append(op['operation_id'])
                continue
            try:
                current = self.operation_status(op['operation_id'])
                if current['status'] == 'published':
                    continue
                if (current['status'] == 'running' and process_alive(current['controller'])) or any(process_alive(p) for p in current['workers']):
                    active.append(op['operation_id'])
                    continue
                for path in (self.staging_dir(op['operation_id']), self.snapshot_dir(op['snapshot_id']),
                             self.root / 'operations' / (op['operation_id'] + '.work')):
                    if path.exists():
                        safe_snapshot_root(path)
                        # Validate children without following a malicious link.
                        from infrastructure.resource_budget import directory_usage
                        directory_usage(path)
                        shutil.rmtree(path)
                self._call(lambda c: c.execute("UPDATE import_operations SET status='interrupted',error='Recovered uncommitted attempt' WHERE operation_id=? AND status!='published'",
                                              [op['operation_id']]) and None, transaction=True)
                removed.append(op['operation_id'])
            finally:
                lock.close()
        self._refresh_cache()
        return {'recovered_operations': removed, 'active_operations': active, 'cache_rebuilt': True}
