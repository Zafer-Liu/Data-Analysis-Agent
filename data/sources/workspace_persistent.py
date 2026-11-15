"""Workspace facade: one live DuckDB owner, immutable imports, bounded shadows.

Import workers only touch private files. The owner installs views in a single
transaction. The legacy registry is a cache; mount metadata in DuckDB is the
source for reconciliation after a registry write failure.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import duckdb
import pandas as pd

from .base import DataSource
from ._utils import _clean_identifier, _list_tables, _table_schema_str, _preview_table_dict, _query, is_write_sql
from .dataset import quote_identifier, snapshot_scan_sql, validate_snapshot_query, validate_snapshot_precision, query_batch_rows
from data.dataset_manifest import DatasetHandle, validate_manifest
from data.import_schema import ImportFailure
from data.workspace_db_owner import WorkspaceDBOwner

CSV_JOB_THRESHOLD_BYTES = int(os.environ.get('BAA_CSV_JOB_THRESHOLD', '25000000'))


def csv_requires_job(path: str) -> bool:
    try:
        return Path(path).stat().st_size > CSV_JOB_THRESHOLD_BYTES
    except OSError:
        return False


class _OwnerConnection:
    """Detached, bounded compatibility results; never expose a live cursor."""
    def __init__(self, owner):
        self.owner = owner

    def execute(self, sql, parameters=None):
        return self.owner.execute(sql, parameters, max_rows=100000, max_bytes=64 << 20)

    def close(self):
        self.owner.close()


def _catalog(runtime):
    from data.dataset_catalog import DatasetCatalog
    # Catalog roots use the existing user content boundary. Workspace IDs are
    # scope values, never interpolated into paths.
    return DatasetCatalog(owner_id=str(getattr(runtime, 'owner_user_id', '') or 'local-default'),
                          session_id='', workspace_id=str(runtime.workspace_id))


def _install_snapshot(owner, handle, *, old_tables=(), file_key='', file_hash='', cancel=None, expected_snapshot=Ellipsis):
    from data.dataset_snapshot import check_cancel
    manifest = validate_manifest(handle.manifest, root=handle.root, verify_files=True)
    tables = [t['name'] for t in manifest['tables']]
    def install(conn):
        check_cancel(cancel)
        conn.execute('CREATE TABLE IF NOT EXISTS _baa_dataset_mounts (file_key VARCHAR PRIMARY KEY, payload VARCHAR NOT NULL)')
        previous = conn.execute('SELECT payload FROM _baa_dataset_mounts WHERE file_key=?', [file_key]).fetchone()
        previous = json.loads(previous[0]) if previous else None
        current_snapshot = previous.get('snapshot_id') if previous else None
        if expected_snapshot is not Ellipsis and current_snapshot != expected_snapshot:
            raise ValueError('workspace_version_conflict: file mount changed while importing')
        replaceable = previous['tables'] if previous else list(old_tables)
        # A distinct source may already own a name. Reject it before dropping
        # anything; replacement is allowed only for this file's old mapping.
        for table in tables:
            existing = conn.execute('SELECT table_type FROM information_schema.tables WHERE table_schema=\'main\' AND table_name=?', [table]).fetchone()
            if existing and table not in replaceable:
                raise ValueError(f'workspace_table_conflict: {table}')
        for table in set(replaceable) | set(tables):
            kind = conn.execute('SELECT table_type FROM information_schema.tables WHERE table_schema=\'main\' AND table_name=?', [table]).fetchone()
            if kind:
                command = 'DROP VIEW' if kind[0] == 'VIEW' else 'DROP TABLE'
                conn.execute(f'{command} {quote_identifier(table)}')
        for table in tables:
            conn.execute(f'CREATE VIEW {quote_identifier(table)} AS SELECT * FROM {snapshot_scan_sql(handle, table)}')
        record = {'file_key': file_key, 'sha256': file_hash or manifest['source'].get('sha256', ''), 'tables': tables,
                  'source_type': manifest['source'].get('format', 'dataset'), 'dataset_handle': handle.to_dict(),
                  'dataset_id': handle.dataset_id, 'snapshot_id': handle.snapshot_id}
        conn.execute('INSERT OR REPLACE INTO _baa_dataset_mounts VALUES (?,?)', [file_key, json.dumps(record, ensure_ascii=False)])
        check_cancel(cancel)
        return record
    return owner.transaction(install)


def _workspace_import(ctx, runtime, file_path, table_name, file_key, file_hash, old_tables):
    from data.import_pipeline import import_file_source
    started = time.monotonic()
    ctx.check_canceled()
    options = {'table_name': _clean_identifier(table_name)}
    catalog = _catalog(runtime)
    with runtime.db_lock, WorkspaceDBOwner(runtime.db_path) as owner:
        def prior(conn):
            exists = conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name='_baa_dataset_mounts'").fetchone()
            row = conn.execute('SELECT payload FROM _baa_dataset_mounts WHERE file_key=?', [file_key]).fetchone() if exists else None
            return json.loads(row[0]).get('snapshot_id') if row else None
        expected_snapshot = owner.call(prior)
    source = import_file_source(file_path, owner_id=catalog.owner_id, session_id=catalog.session_id,
                               workspace_id=catalog.workspace_id, options=options, catalog=catalog,
                               progress=ctx.set_progress, cancel=ctx.is_canceled)
    try:
        if file_hash and source.manifest['source']['sha256'] != file_hash:
            raise ValueError('dataset_changed: workspace file changed after selection')
        # The heavy process has really exited before opening this owner facade.
        with runtime.db_lock, WorkspaceDBOwner(runtime.db_path) as owner:
            record = _install_snapshot(owner, source.handle, old_tables=old_tables or [], file_key=file_key,
                                       file_hash=file_hash, cancel=ctx.is_canceled, expected_snapshot=expected_snapshot)
            registry = runtime.load_registry()
            registry[file_key] = {**record, 'file_path': str(file_path)}
            registry_warning = ''
            try:
                runtime.save_registry(registry)
            except OSError as exc:
                # Data committed. Reattaching the source reconstructs this cache.
                registry_warning = str(exc)[:240]
        metrics = {'source_bytes': Path(file_path).stat().st_size, 'row_count': source.manifest['row_count'],
                   'table_count': len(record['tables']), 'elapsed_ms': round((time.monotonic() - started) * 1000)}
        try:
            ctx.set_progress(100, f"已导入 {metrics['row_count']:,} 行")
        except Exception:
            pass
        return {'workspace_id': runtime.workspace_id, 'source_name': file_key, 'tables': record['tables'],
                'metrics': metrics, 'dataset_handle': source.handle.to_dict(), 'registry_warning': registry_warning,
                'result_committed': True}
    finally:
        source.close()


def parse_workspace_csv_job(ctx, runtime, file_path: str, table_name: str, file_key: str, file_hash: str, old_tables=None):
    return _workspace_import(ctx, runtime, file_path, table_name, file_key, file_hash, old_tables)


def parse_workspace_excel_job(ctx, runtime, file_path: str, base_table_name: str, file_key: str, file_hash: str, old_tables=None):
    if Path(file_path).suffix.lower() != '.xlsx':
        raise ValueError('Large XLS requires conversion to XLSX/CSV/Parquet')
    return _workspace_import(ctx, runtime, file_path, base_table_name, file_key, file_hash, old_tables)


class WorkspacePersistentSource(DataSource):
    def __init__(self, db_path: str, display_name='工作目录', db_lock=None):
        self.name, self.file_path = display_name, db_path
        self._db_path = Path(db_path)
        self._db_lock = db_lock or threading.RLock()
        self._shadow_lock = threading.RLock()
        self._shadow_conn = None
        self._owner = WorkspaceDBOwner(db_path)
        self._conn = _OwnerConnection(self._owner)
        self._closed = False
        self.reconcile_mounts()

    def reconcile_mounts(self, runtime=None):
        def load(conn):
            exists = conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name='_baa_dataset_mounts'").fetchone()
            return [json.loads(r[0]) for r in conn.execute('SELECT payload FROM _baa_dataset_mounts').fetchall()] if exists else []
        self._mounts = self._owner.call(load)
        if runtime is not None:
            registry = runtime.load_registry()
            for record in self._mounts:
                # Keep the original registry key inside authoritative metadata.
                key = record.get('file_key')
                if key:
                    registry[key] = record
            runtime.save_registry(registry)
        return list(self._mounts)

    def _active_conn(self):
        return self._shadow_conn if self._shadow_conn is not None else self._conn

    def writable_conn(self):
        """Only a small, exact Arrow copy is permitted; large views need a plan."""
        with self._shadow_lock:
            if self._shadow_conn is not None:
                return self._shadow_conn
            def detach_tables(conn, pins):
                from data.dataset_manifest import PinnedSnapshot
                exists = conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name='_baa_dataset_mounts'").fetchone()
                records = [json.loads(r[0]) for r in conn.execute('SELECT payload FROM _baa_dataset_mounts').fetchall()] if exists else []
                for record in records:
                    handle = DatasetHandle.from_dict(record['dataset_handle'])
                    for table in handle.manifest['tables']:
                        tree = validate_snapshot_query(f'SELECT * FROM {quote_identifier(table["name"])}', [table['name']])
                        validate_snapshot_precision(tree, [table])
                    pins.enter_context(PinnedSnapshot(handle))
                names = [r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND NOT starts_with(table_name, '_baa_') ORDER BY table_name").fetchall()]
                tables, size, rows = [], 0, 0
                for name in names:
                    reader = conn.execute(f'SELECT * FROM {quote_identifier(name)}').to_arrow_reader(batch_size=1024)
                    batches = []
                    try:
                        schema = reader.schema
                        for batch in reader:
                            rows += batch.num_rows
                            size += batch.nbytes
                            if rows > 100000 or size > 32 << 20:
                                raise ValueError('immutable_dataset: large workspace writes require a derived snapshot plan')
                            batches.append(batch)
                    finally:
                        reader.close()
                    import pyarrow as pa
                    tables.append((name, pa.Table.from_batches(batches, schema=schema)))
                return tables
            def detach(conn):
                from contextlib import ExitStack
                with ExitStack() as pins:
                    return detach_tables(conn, pins)
            tables = self._owner.call(detach, transaction=True)
            shadow = duckdb.connect(':memory:')
            try:
                for name, table in tables:
                    shadow.register('_typed_copy', table)
                    shadow.execute(f'CREATE TABLE {quote_identifier(name)} AS SELECT * FROM _typed_copy')
                    shadow.unregister('_typed_copy')
            except BaseException:
                shadow.close()
                raise
            self._shadow_conn = shadow
            return shadow

    def _invalidate_shadow(self):
        with self._shadow_lock:
            if self._shadow_conn is not None:
                self._shadow_conn.close()
                self._shadow_conn = None

    def _register_snapshot(self, file_path, table_name):
        from data.import_pipeline import import_file_source
        source = import_file_source(file_path, options={'table_name': _clean_identifier(table_name)})
        try:
            key = str(Path(file_path).name)
            prior = self._owner.call(lambda c: c.execute("SELECT 1 FROM information_schema.tables WHERE table_name='_baa_dataset_mounts'").fetchone())
            old_tables = []
            if prior:
                row = self._owner.execute('SELECT payload FROM _baa_dataset_mounts WHERE file_key=?', [key]).fetchone()
                if row:
                    old_tables = json.loads(row[0])['tables']
            record = _install_snapshot(self._owner, source.handle, old_tables=old_tables, file_key=key)
            self._invalidate_shadow()
            self.reconcile_mounts()
            return record['tables']
        finally:
            source.close()

    def _register_csv(self, file_path: str, table_name: str) -> bool:
        self._register_snapshot(file_path, table_name)
        return True

    def _register_excel(self, file_path: str, base_table_name: str):
        if Path(file_path).suffix.lower() == '.xlsx':
            return self._register_snapshot(file_path, base_table_name)
        # Historical XLS stays explicitly small and installs exact Arrow types.
        if Path(file_path).stat().st_size > 5_000_000:
            raise ValueError('Large XLS requires conversion to XLSX/CSV/Parquet')
        from .excel import ExcelDataSource
        source = ExcelDataSource(file_path, Path(file_path).name)
        try:
            tables = []
            for name in source.list_tables():
                target = _clean_identifier(base_table_name) if len(source.list_tables()) == 1 else name
                table = source._conn.execute(f'SELECT * FROM {quote_identifier(name)}').to_arrow_table()
                if table.nbytes > 32 << 20 or table.num_rows > 100000:
                    raise ValueError('Small XLS exceeds decoded compatibility limit')
                def install(conn):
                    conn.register('_xls_batch', table)
                    try:
                        conn.execute(f'CREATE OR REPLACE TABLE {quote_identifier(target)} AS SELECT * FROM _xls_batch')
                    finally:
                        conn.unregister('_xls_batch')
                self._owner.transaction(install)
                tables.append(target)
            self._invalidate_shadow()
            return tables
        finally:
            source.close()

    def list_tables(self):
        with self._shadow_lock:
            if self._shadow_conn is not None:
                return _list_tables(self._shadow_conn)
            return [r[0] for r in self._owner.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND NOT starts_with(table_name, '_baa_') ORDER BY table_name").fetchall()]

    def get_schema(self):
        with self._shadow_lock:
            conn = self._active_conn()
            return '\n\n'.join(_table_schema_str(conn, t, conn.execute(f'SELECT COUNT(*) FROM {quote_identifier(t)}').fetchone()[0]) for t in self.list_tables())

    def execute_query(self, sql):
        try:
            with self._shadow_lock:
                conn = self.writable_conn() if is_write_sql(sql) else self._active_conn()
                if conn is self._conn:
                    import pyarrow as pa
                    from infrastructure.resource_budget import BudgetSpec
                    limits = BudgetSpec(max_rows=100000, max_output_bytes=64 << 20)
                    batches = list(self.execute_query_batches(sql, budget=limits))
                    return pa.Table.from_batches(batches, schema=self._result_schema).to_pandas(types_mapper=pd.ArrowDtype), ''
                import pyarrow as pa
                reader = conn.execute(sql).to_arrow_reader(batch_size=1024)
                batches, rows, size = [], 0, 0
                try:
                    schema = reader.schema
                    for batch in reader:
                        rows += batch.num_rows
                        size += batch.nbytes
                        if rows > 100000 or size > 64 << 20:
                            raise ValueError('Workspace result exceeds bounded legacy limit')
                        batches.append(batch)
                finally:
                    reader.close()
                return pa.Table.from_batches(batches, schema=schema).to_pandas(types_mapper=pd.ArrowDtype), ''
        except Exception as exc:
            return pd.DataFrame(), str(exc)

    def execute_query_batches(self, sql, *, budget=None, cancel=None):
        """Owner-thread producer with bounded backpressure and fixed mount pins."""
        import queue
        import uuid
        from contextlib import ExitStack
        from data.dataset_snapshot import check_cancel
        from data.dataset_manifest import PinnedSnapshot
        from infrastructure.resource_budget import BudgetSpec, BudgetExceeded, ResourceCoordinator
        limits = BudgetSpec.from_dict(budget)
        tree = validate_snapshot_query(sql, self.list_tables())
        with self._shadow_lock:
            if self._shadow_conn is not None:
                # Shadow creation is restricted to small inputs; result bounds
                # still apply when a SELECT expands it with joins.
                reader = self._shadow_conn.execute(sql).to_arrow_reader(batch_size=1024)
                rows = size = 0
                try:
                    self._result_schema = reader.schema
                    for batch in reader:
                        check_cancel(cancel)
                        rows += batch.num_rows
                        size += batch.nbytes
                        limits.check_usage(rows=rows, output_bytes=size)
                        if batch.nbytes > limits.batch_bytes:
                            raise BudgetExceeded('batch_bytes', batch.nbytes, limits.batch_bytes)
                        yield batch
                finally:
                    reader.close()
                return
            reservation = limits.to_dict()
            reservation.update(max_input_bytes=1, max_output_bytes=1)
            coordinator = ResourceCoordinator()
            coordinator.register_account_path(self._db_path)
            coordinator.register_account_path(self._db_path.with_name(self._db_path.name + '.spill'))
            with coordinator.reserve('workspace', 'query-' + uuid.uuid4().hex, reservation):
                pending = queue.Queue(maxsize=limits.queue_batches)
                stop, errors = threading.Event(), []
                started = time.monotonic()
                def check():
                    if errors:
                        raise errors[0]
                    if stop.is_set():
                        raise ImportFailure('canceled', 'Workspace stream closed')
                    check_cancel(cancel)
                    limits.check_usage(run_seconds=time.monotonic() - started)
                def send(kind, value):
                    while True:
                        check()
                        try:
                            pending.put((kind, value), timeout=0.05)
                            return
                        except queue.Full:
                            pass
                def produce(conn):
                    check()
                    with ExitStack() as pins:
                        exists = conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name='_baa_dataset_mounts'").fetchone()
                        records = [json.loads(r[0]) for r in conn.execute('SELECT payload FROM _baa_dataset_mounts').fetchall()] if exists else []
                        pinned, table_metadata = [], []
                        input_bytes = 0
                        for record in records:
                            handle = DatasetHandle.from_dict(record['dataset_handle'])
                            validate_snapshot_precision(tree, handle.manifest['tables'])
                            table_metadata.extend(handle.manifest['tables'])
                            input_bytes += sum(f['size_bytes'] for f in handle.manifest['files'])
                            limits.check_usage(input_bytes=input_bytes)
                            pinned.append(pins.enter_context(PinnedSnapshot(handle, budget=limits, cancel=lambda: stop.is_set() or bool(cancel and cancel()))))
                        self._last_read_tables = table_metadata
                        previous = conn.execute("SELECT current_setting('memory_limit'),current_setting('threads'),current_setting('max_temp_directory_size')").fetchone()
                        reader = None
                        try:
                            conn.execute('SET memory_limit=?', [f'{min(limits.duckdb_memory_bytes, 1 << 30)}B'])
                            conn.execute('SET threads=?', [min(limits.threads, 4)])
                            conn.execute('SET max_temp_directory_size=?', [f'{min(limits.temp_bytes, 8 << 30)}B'])
                            cursor = conn.execute(sql)
                            reader = cursor.to_arrow_reader(batch_size=query_batch_rows(cursor, tree, table_metadata, limits, ceiling=1024))
                            send('schema', reader.schema)
                            rows = size = 0
                            def pieces(batch):
                                if batch.nbytes <= limits.batch_bytes:
                                    yield batch
                                elif batch.num_rows > 1:
                                    middle = batch.num_rows // 2
                                    yield from pieces(batch.slice(0, middle))
                                    yield from pieces(batch.slice(middle))
                                else:
                                    raise BudgetExceeded('batch_bytes', batch.nbytes, limits.batch_bytes)
                            for batch in reader:
                                for piece in pieces(batch):
                                    check()
                                    for pin in pinned:
                                        pin.check()
                                    rows += piece.num_rows
                                    size += piece.nbytes
                                    limits.check_usage(rows=rows, output_bytes=size)
                                    send('batch', piece)
                        finally:
                            if reader is not None:
                                reader.close()
                            for setting, value in zip(('memory_limit', 'threads', 'max_temp_directory_size'), previous):
                                conn.execute(f'SET {setting}=?', [value])
                future = self._owner.submit(produce)
                def watch():
                    import psutil
                    while not stop.wait(0.05):
                        try:
                            check()
                            limits.check_usage(rss_bytes=psutil.Process().memory_info().rss)
                            from infrastructure.resource_budget import directory_usage
                            limits.check_usage(temp_bytes=directory_usage(self._db_path.with_name(self._db_path.name + '.spill'))['bytes'])
                        except BaseException as exc:
                            errors.append(exc)
                            future.cancel()
                            self._owner.interrupt(future)
                            return
                watcher = threading.Thread(target=watch, name='workspace-query-budget', daemon=True)
                watcher.start()
                try:
                    while True:
                        check()
                        try:
                            kind, value = pending.get(timeout=0.05)
                        except queue.Empty:
                            if future.done():
                                future.result()
                                break
                            continue
                        if kind == 'schema':
                            self._result_schema = value
                        else:
                            yield value
                    if errors:
                        raise errors[0]
                finally:
                    stop.set()
                    future.cancel()
                    self._owner.interrupt(future)
                    watcher.join(timeout=1)
                    # The producer observes stop while waiting on a full queue;
                    # retain the lease until it actually leaves the connection.
                    try:
                        future.result()
                    except BaseException:
                        pass

    def get_preview(self):
        with self._shadow_lock:
            return [self.get_preview_table(t, max_rows=0) for t in self.list_tables()]

    def get_preview_table(self, table_name, max_rows=100):
        if table_name not in self.list_tables():
            return {'name': table_name, 'columns': [], 'rows': [], 'total_rows': 0, 'error': 'unknown_table'}
        with self._shadow_lock:
            if self._shadow_conn is not None:
                return _preview_table_dict(self._shadow_conn, table_name, table_name, max(0, min(int(max_rows), 1000)))
            frame, error = self.execute_query(f'SELECT * FROM {quote_identifier(table_name)} LIMIT {max(0, min(int(max_rows), 1000))}')
            metadata = next((t for t in getattr(self, '_last_read_tables', []) if t['name'] == table_name), None)
            total = metadata['row_count'] if metadata else self._owner.execute(f'SELECT COUNT(*) FROM {quote_identifier(table_name)}').fetchone()[0]
            rows = [[None if v is None or v is pd.NA or v is pd.NaT else str(v) for v in row]
                    for row in frame.itertuples(index=False, name=None)]
            result = {'name': table_name, 'columns': list(frame.columns), 'rows': rows, 'total_rows': total}
            if error:
                result['error'] = error
            return result

    def create_analysis_table(self, sql, table_name='analysis_data', _df=None):
        try:
            with self._shadow_lock:
                conn = self.writable_conn()
                if _df is not None:
                    import pyarrow as pa
                    table = pa.Table.from_pandas(_df, preserve_index=False)
                    if table.nbytes > 32 << 20 or table.num_rows > 100000:
                        raise ValueError('Analysis result exceeds small workspace limit')
                    conn.register('_analysis_batch', table)
                    try:
                        conn.execute(f'CREATE OR REPLACE TABLE {quote_identifier(table_name)} AS SELECT * FROM _analysis_batch')
                    finally:
                        conn.unregister('_analysis_batch')
                else:
                    conn.execute(f'CREATE OR REPLACE TABLE {quote_identifier(table_name)} AS ({sql})')
                return _table_schema_str(conn, table_name, conn.execute(f'SELECT COUNT(*) FROM {quote_identifier(table_name)}').fetchone()[0])
        except Exception as exc:
            return f'创建表失败：{exc}'

    def get_capabilities(self):
        self.reconcile_mounts()
        return {'query_dialect': 'duckdb', 'batch_reader': True, 'execute_query_batches': True, 'immutable': False, 'large': bool(self._mounts),
                'bounded_legacy_result': True, 'requires_table_selection': bool(self._mounts),
                'database_access': 'single_owner_thread', 'large_writes': 'derived_snapshot_required'}

    def close(self):
        if not self._closed:
            self._invalidate_shadow()
            self._owner.close()
            self._closed = True
