"""Focused local regressions for budgets, real exits and catalog publication."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import pyarrow as pa

from infrastructure.resource_budget import (BudgetSpec, BudgetExceeded, ResourceCoordinator,
    ResourceError, process_identity, run_supervised_worker)
from data.dataset_catalog import DatasetCatalog, CatalogError
from data.dataset_manifest import DatasetHandle, ManifestError, validate_manifest, manifest_digest
from data.dataset_snapshot import SnapshotWriter, canonical_hash
from data.workspace_db_owner import WorkspaceDBOwner, WorkspaceOwnerError

ROOT = Path(__file__).resolve().parents[1]


def child(code, *args):
    return [sys.executable, '-I', '-B', '-c', 'import sys;sys.path.insert(0,sys.argv[1]);' + code, str(ROOT), *map(str, args)]


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bda-f-')
        self.root = Path(self.tmp.name)
        self.spec = BudgetSpec(max_input_bytes=1 << 20, max_output_bytes=1 << 20, temp_bytes=1 << 20,
                               duckdb_memory_bytes=32 << 20, rss_bytes=2 << 30, batch_bytes=1 << 20,
                               max_run_seconds=10)

    def tearDown(self):
        self.tmp.cleanup()

    def catalog(self, **kwargs):
        return DatasetCatalog(self.root / 'c', owner_id='owner', session_id='session', **kwargs)

    def staged(self, catalog, *, dataset_id='', value=1, key='one'):
        plan = {**catalog.scope, 'options': {'dataset_id': dataset_id} if dataset_id else {}, 'value': value}
        plan['plan_hash'] = canonical_hash(plan)
        operation = catalog.begin_import(plan, idempotency_key=key)
        writer = SnapshotWriter(catalog.staging_dir(operation['operation_id']), budget=self.spec)
        batch = pa.record_batch([pa.array([value], type=pa.int64())], names=['n'])
        writer.add_table('data', batch.schema)
        writer.write('data', batch)
        files = writer.finish()
        writer.close()
        manifest = {'version': 1, **catalog.scope, 'dataset_id': operation['dataset_id'],
                    'snapshot_id': operation['snapshot_id'], 'operation_id': operation['operation_id'],
                    'status': 'ready', 'schema': writer.tables['data']['schema'], 'tables': list(writer.tables.values()),
                    'source': {}, 'row_count': 1, 'files': files, 'lineage': {}, 'schema_policy': {},
                    'created_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        return operation, manifest

    def test_budget_validates_and_checks(self):
        self.assertEqual(self.spec, BudgetSpec.from_dict(self.spec.to_dict()))
        for value in ({'threads': True}, {'rss_bytes': float('nan')}, {'shell': 'x'}, {'max_rows': 0}, {'queue_batches': 10000}):
            with self.assertRaises(ValueError):
                BudgetSpec.from_dict(value)
        with self.assertRaises(BudgetExceeded) as result:
            self.spec.check_usage(rows=self.spec.max_rows + 1)
        self.assertEqual(('budget_exceeded', 'rows'), (result.exception.code, result.exception.resource))

    def test_reservation_idempotency_slots_and_real_exit(self):
        coordinator = ResourceCoordinator(self.root / 'r', max_slots=1, disk_safety_bytes=0)
        lease = coordinator.reserve('owner', 'e', self.spec)
        self.assertEqual(lease.token, coordinator.reserve('owner', 'e', self.spec).token)
        with self.assertRaises(BudgetExceeded):
            coordinator.reserve('owner', 'other', self.spec)
        process = subprocess.Popen(child('import time;time.sleep(30)'))
        try:
            lease.bind_process(process)
            with self.assertRaises(ResourceError) as result:
                lease.release()
            self.assertEqual('lease_process_alive', result.exception.code)
            self.assertEqual(1, coordinator.snapshot()['slots'])
        finally:
            process.kill()
            process.wait(timeout=10)
        self.assertTrue(lease.release())
        self.assertEqual(0, coordinator.snapshot()['slots'])

    def test_disk_reserves_include_existing_content(self):
        existing = self.root / 'existing'
        existing.mkdir()
        (existing / 'a').write_bytes(b'x' * 1024)
        coordinator = ResourceCoordinator(self.root / 'r', max_disk_bytes=3 * (1 << 20),
                                          disk_safety_bytes=0, account_paths=[existing])
        with self.assertRaises(BudgetExceeded) as result:
            coordinator.reserve('o', 'e', self.spec)
        self.assertEqual('global_disk_bytes', result.exception.resource)

    def test_dead_controller_recovery(self):
        ledger = self.root / 'r'
        code = "from infrastructure.resource_budget import ResourceCoordinator;ResourceCoordinator(sys.argv[2],disk_safety_bytes=0).reserve('o','dead',None)"
        result = subprocess.run(child(code, ledger), capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        coordinator = ResourceCoordinator(ledger, disk_safety_bytes=0)
        self.assertEqual(['dead'], coordinator.recover())

    def test_worker_success_failure_and_deadline(self):
        coordinator = ResourceCoordinator(self.root / 'r', disk_safety_bytes=0)
        result = run_supervised_worker(child("print('ok')"), coordinator=coordinator, owner_id='o', execution_id='success', budget=self.spec)
        self.assertTrue(result['exit_confirmed'])
        self.assertEqual('monitored', result['limits']['rss'])
        self.assertGreater(result['usage']['log_bytes'], 0)
        with self.assertRaises(ResourceError) as failed:
            run_supervised_worker(child('raise SystemExit(7)'), coordinator=coordinator, owner_id='o', execution_id='fail', budget=self.spec)
        self.assertEqual('worker_failed', failed.exception.code)
        limits = {**self.spec.to_dict(), 'max_run_seconds': 0.15}
        with self.assertRaises(BudgetExceeded) as exceeded:
            run_supervised_worker(child('import time;time.sleep(30)'), coordinator=coordinator, owner_id='o', execution_id='deadline', budget=limits)
        self.assertEqual('run_seconds', exceeded.exception.resource)
        self.assertEqual(0, coordinator.snapshot()['slots'])

    def test_worker_rss_cpu_and_log_monitoring(self):
        coordinator = ResourceCoordinator(self.root / 'r', disk_safety_bytes=0)
        cases = [
            ('rss', 'import time;x=bytearray(32<<20);time.sleep(30)',
             {'rss_bytes': 24 << 20, 'duckdb_memory_bytes': 8 << 20}, 'rss_bytes'),
            ('cpu', '\nwhile True: pass', {'max_cpu_seconds': 0.1}, 'cpu_seconds'),
            ('log', "import time;print('x'*4096,flush=True);time.sleep(30)", {'max_log_bytes': 1024}, 'log_bytes')]
        for name, code, changes, resource in cases:
            with self.subTest(resource=resource), self.assertRaises(BudgetExceeded) as result:
                run_supervised_worker(child(code), coordinator=coordinator, owner_id='o', execution_id=name,
                                      budget={**self.spec.to_dict(), **changes})
            self.assertEqual(resource, result.exception.resource)
            self.assertTrue(result.exception.exit_confirmed)
        self.assertEqual(0, coordinator.snapshot()['slots'])

    def test_worker_cancel_and_output_ceiling(self):
        coordinator = ResourceCoordinator(self.root / 'r', disk_safety_bytes=0)
        cancel = threading.Event()
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        try:
            with self.assertRaises(ResourceError) as result:
                run_supervised_worker(child('import time;time.sleep(30)'), coordinator=coordinator, owner_id='o', execution_id='cancel', budget=self.spec, cancel=cancel.is_set)
            self.assertEqual('worker_cancelled', result.exception.code)
        finally:
            timer.cancel()
        output = self.root / 'output'
        output.mkdir()
        code = "from pathlib import Path;import time;Path(sys.argv[2]).write_bytes(b'x'*2048);time.sleep(30)"
        with self.assertRaises(BudgetExceeded) as result:
            run_supervised_worker(child(code, output / 'a'), coordinator=coordinator, owner_id='o', execution_id='disk',
                                  budget={**self.spec.to_dict(), 'max_output_bytes': 1024}, output_dir=output)
        self.assertEqual('output_bytes', result.exception.resource)
        self.assertEqual(0, coordinator.snapshot()['slots'])

    def test_worker_descendant_exit_is_confirmed_before_release(self):
        coordinator = ResourceCoordinator(self.root / 'r', disk_safety_bytes=0)
        identity_file = self.root / 'child.json'
        code = ("import subprocess,time,json;from pathlib import Path;from infrastructure.resource_budget import process_identity;"
                "p=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(30)']);"
                "Path(sys.argv[2]).write_text(json.dumps(process_identity(p.pid)),encoding='utf-8');time.sleep(0.25)")
        with self.assertRaises(ResourceError) as result:
            run_supervised_worker(child(code, identity_file), coordinator=coordinator, owner_id='o', execution_id='descendant', budget=self.spec)
        self.assertEqual('worker_descendants_alive', result.exception.code)
        self.assertTrue(result.exception.exit_confirmed)
        from infrastructure.resource_budget import process_alive
        self.assertFalse(process_alive(json.loads(identity_file.read_text(encoding='utf-8'))))
        self.assertEqual(0, coordinator.snapshot()['slots'])

    def test_workspace_owner_queue_and_other_process_exclusion(self):
        path = self.root / 'owner.duckdb'
        with WorkspaceDBOwner(path) as owner, WorkspaceDBOwner(path) as shared:
            owner.execute('CREATE TABLE t(n INTEGER)')
            futures = [shared.submit(lambda c, i=i: c.execute('INSERT INTO t VALUES (?)', [i]) and None, transaction=True) for i in range(10)]
            for future in futures:
                future.result(timeout=10)
            self.assertEqual((10,), owner.execute('SELECT COUNT(*) FROM t').fetchone())
            code = "from data.workspace_db_owner import WorkspaceDBOwner,WorkspaceOwnerError\ntry:\n WorkspaceDBOwner(sys.argv[2])\nexcept WorkspaceOwnerError as e:\n print(e.code)\nelse:\n raise SystemExit(5)"
            result = subprocess.run(child(code, path), capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('workspace_owner_busy', result.stdout)
        with WorkspaceDBOwner(path) as reopened:
            self.assertEqual((45,), reopened.execute('SELECT SUM(n) FROM t').fetchone())

    def test_owner_rollback_and_detached_limit(self):
        with WorkspaceDBOwner(self.root / 'db.duckdb') as owner:
            owner.execute('CREATE TABLE t(n INTEGER)')
            def bad(conn):
                conn.execute('INSERT INTO t VALUES (1)')
                raise ValueError('stop')
            with self.assertRaises(ValueError):
                owner.transaction(bad)
            self.assertEqual((0,), owner.execute('SELECT COUNT(*) FROM t').fetchone())
            with self.assertRaises(WorkspaceOwnerError):
                owner.execute('SELECT * FROM range(100)', max_rows=5)

    def test_manifest_hash_handle_and_table_mapping(self):
        catalog = self.catalog()
        op, manifest = self.staged(catalog)
        root = catalog.staging_dir(op['operation_id'])
        validate_manifest(manifest, root=root, verify_files=True)
        handle = DatasetHandle(manifest, root)
        self.assertEqual(manifest_digest(manifest), handle.manifest['manifest_hash'])
        detached = handle.manifest
        detached['row_count'] = 99
        self.assertEqual(1, handle.manifest['row_count'])
        with self.assertRaises(AttributeError):
            handle._root = self.root
        wrong = copy.deepcopy(manifest)
        wrong['tables'][0]['row_count'] = 3
        with self.assertRaises(ManifestError):
            validate_manifest(wrong)

    def test_manifest_rejects_traversal_extra_files_hardlinks_and_changes(self):
        catalog = self.catalog()
        op, manifest = self.staged(catalog)
        root = catalog.staging_dir(op['operation_id'])
        for path in ('../x', '/x', 'a\\x', 'C:/x', 'NUL/x'):
            bad = copy.deepcopy(manifest)
            bad['files'][0]['relative_path'] = path
            with self.assertRaises(ManifestError):
                validate_manifest(bad)
        extra = root / 'extra'
        extra.write_bytes(b'x')
        with self.assertRaises(ManifestError):
            validate_manifest(manifest, root=root, verify_files=True)
        extra.unlink()
        shard = root / manifest['files'][0]['relative_path']
        link = self.root / 'linked'
        os.link(shard, link)
        with self.assertRaises(ManifestError):
            validate_manifest(manifest, root=root, verify_files=True)
        link.unlink()
        shard.write_bytes(b'changed')
        with self.assertRaises(ManifestError):
            validate_manifest(manifest, root=root, verify_files=True)

    def test_publish_version_pointer_replay_and_scope(self):
        catalog = self.catalog()
        op, manifest = self.staged(catalog)
        first = catalog.publish(manifest, idempotency_key='one', expected_version=0)
        self.assertEqual(1, first['catalog_version'])
        self.assertEqual(first, catalog.publish(first, idempotency_key='one'))
        self.assertEqual(first, catalog.publish(manifest, idempotency_key='one'))
        _, second = self.staged(catalog, dataset_id=first['dataset_id'], value=2, key='two')
        second = catalog.publish(second, idempotency_key='two', expected_version=1)
        self.assertEqual(2, len(catalog.versions(first['dataset_id'])))
        self.assertEqual(second['snapshot_id'], catalog.get(first['dataset_id']).snapshot_id)
        self.assertEqual(first['snapshot_id'], catalog.get(first['dataset_id'], snapshot_id=first['snapshot_id']).snapshot_id)
        foreign = DatasetCatalog(self.root / 'c', owner_id='other', session_id='session')
        self.assertEqual([], foreign.list())
        with self.assertRaises(KeyError):
            foreign.get(first['dataset_id'])

    def test_fencing_and_version_conflict_recovery_keep_old_snapshot(self):
        catalog = self.catalog()
        _, manifest = self.staged(catalog)
        first = catalog.publish(manifest, idempotency_key='one')
        op, second = self.staged(catalog, dataset_id=first['dataset_id'], value=2, key='two')
        second['operation_fence'] = 'stale'
        with self.assertRaises(CatalogError) as error:
            catalog.publish(second, idempotency_key='two')
        self.assertEqual('operation_fenced', error.exception.code)
        second.pop('operation_fence')
        with self.assertRaises(CatalogError) as error:
            catalog.publish(second, idempotency_key='two', expected_version=0)
        self.assertEqual('version_conflict', error.exception.code)
        catalog.recover()
        self.assertFalse(catalog.snapshot_dir(second['snapshot_id']).exists())
        self.assertEqual(first['snapshot_id'], catalog.get(first['dataset_id'], verify=True).snapshot_id)

    def test_active_operation_is_not_recovered_or_reclaimed(self):
        catalog = self.catalog()
        op, manifest = self.staged(catalog)
        with catalog.claim_operation(op['operation_id']):
            with self.assertRaises(CatalogError):
                with catalog.claim_operation(op['operation_id']):
                    self.fail('duplicate claim')
            self.assertIn(op['operation_id'], catalog.recover()['active_operations'])
            self.assertTrue(catalog.staging_dir(op['operation_id']).exists())

    def test_catalog_recovers_crashed_staging_but_retains_live_worker(self):
        code = ("from data.dataset_catalog import DatasetCatalog;from infrastructure.resource_budget import process_identity;"
                "import os,subprocess;from pathlib import Path;"
                "c=DatasetCatalog(sys.argv[2],owner_id='owner',session_id='session');"
                "op=c.begin_import({**c.scope,'value':1},idempotency_key='crash');"
                "claim=c.claim_operation(op['operation_id']);op=claim.__enter__();"
                "c.staging_dir(op['operation_id']).mkdir();"
                "p=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                "c.observe_worker(op['operation_id'],op['fence'],process_identity(p.pid));"
                "os._exit(9)")
        result = subprocess.run(child(code, self.root / 'c'), capture_output=True, text=True, timeout=10)
        self.assertEqual(9, result.returncode, result.stderr)
        catalog = self.catalog()
        operation = catalog.begin_import({**catalog.scope, 'value': 1}, idempotency_key='crash')
        import psutil
        worker = psutil.Process(operation['workers'][0]['pid'])
        try:
            self.assertIn(operation['operation_id'], catalog.recover()['active_operations'])
            self.assertTrue(catalog.staging_dir(operation['operation_id']).exists())
        finally:
            worker.kill()
            worker.wait(timeout=10)
        self.assertIn(operation['operation_id'], catalog.recover()['recovered_operations'])
        self.assertFalse(catalog.staging_dir(operation['operation_id']).exists())

    def test_cache_failure_does_not_undo_catalog_commit(self):
        import data.dataset_catalog as module
        catalog = self.catalog()
        _, manifest = self.staged(catalog)
        real = module._sync_json
        def fail_cache(path, value):
            if path.name == 'catalog.json':
                raise OSError('simulated registry full')
            return real(path, value)
        with patch.object(module, '_sync_json', side_effect=fail_cache):
            result = catalog.publish(manifest, idempotency_key='one')
        self.assertEqual(result['snapshot_id'], catalog.get(result['dataset_id']).snapshot_id)
        catalog.recover()
        self.assertEqual(1, len(json.loads((catalog.root / 'catalog.json').read_text(encoding='utf-8'))))


if __name__ == '__main__':
    unittest.main()
