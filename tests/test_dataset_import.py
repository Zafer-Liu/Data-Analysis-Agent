"""Actual small Arrow/DuckDB integration and supervised import fault tests."""
import datetime as dt
from decimal import Decimal
import gzip
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import duckdb
import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq

from data.dataset_catalog import DatasetCatalog
from data.dataset_snapshot import canonical_hash
from data.import_pipeline import create_import_plan, run_import
from data.import_schema import ImportFailure
from data.sources.dataset import DatasetDataSource, validate_snapshot_query
from infrastructure.resource_budget import BudgetSpec, BudgetExceeded, ResourceCoordinator


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bda-i-')
        self.root = Path(self.tmp.name)
        self.catalog = DatasetCatalog(self.root / 'catalog', owner_id='u', session_id='s')
        self.budget = BudgetSpec(max_input_bytes=8 << 20, max_output_bytes=8 << 20, temp_bytes=8 << 20,
                                 batch_bytes=65536, duckdb_memory_bytes=128 << 20, rss_bytes=768 << 20,
                                 max_run_seconds=30)
        self.sources = []

    def tearDown(self):
        for source in self.sources:
            source.close()
        self.tmp.cleanup()

    def csv(self, text, name='input.csv'):
        path = self.root / name
        path.write_text(text, encoding='utf-8', newline='\n')
        return path

    def plan(self, path, options=None, budget=None):
        return create_import_plan(path, owner_id='u', session_id='s', options=options, budget=budget or self.budget)

    def imported(self, path, options=None):
        plan = self.plan(path, options)
        manifest = run_import(plan, catalog=self.catalog)
        source = DatasetDataSource(self.catalog.get(manifest['dataset_id'], verify=True), budget=self.budget)
        self.sources.append(source)
        return source, manifest

    def test_csv_ids_nulls_decimal_and_timezone_are_exact(self):
        path = self.csv('id,amount,label,at,wide\n001,123456789012345678.90,NA,2026-11-14T12:34:56+08:00,9223372036854775807\n002,0.10,,2026-11-14T04:34:57Z,\n')
        policy = {'columns': {'amount': 'decimal128(20, 2)', 'at': 'timestamp[us, tz=UTC]'}}
        source, manifest = self.imported(path, {'schema_policy': policy})
        table = pa.Table.from_batches(list(source.execute_query_batches('SELECT * FROM input ORDER BY id')))
        self.assertEqual(['001', '002'], table['id'].to_pylist())
        self.assertEqual([Decimal('123456789012345678.90'), Decimal('0.10')], table['amount'].to_pylist())
        self.assertEqual(['NA', None], table['label'].to_pylist())
        self.assertEqual(dt.datetime(2026, 11, 14, 4, 34, 56, tzinfo=dt.timezone.utc), table['at'][0].as_py())
        preview = source.get_preview_table('input')
        self.assertEqual('123456789012345678.90', preview['rows'][0][1])
        self.assertEqual('9223372036854775807', preview['rows'][0][4])
        self.assertGreater(manifest['worker_usage']['rss_bytes'], 0)
        self.assertEqual('monitored', manifest['budget_enforcement']['rss'])

    def test_fixed_schema_drift_and_bad_row_count(self):
        path = self.csv('n,code\n1,001\nwrong,002\n3,003,extra\n4,004\n')
        options = {'sample_rows': 1, 'schema_policy': {'bad_rows': 'skip', 'max_bad_rows': 2}}
        source, manifest = self.imported(path, options)
        self.assertEqual(2, manifest['row_count'])
        self.assertEqual(2, manifest['import_metrics']['bad_rows'])
        frame, error = source.execute_query('SELECT SUM(n) AS n FROM input')
        self.assertFalse(error, error)
        self.assertEqual(5, int(frame.iloc[0, 0]))
        strict = self.plan(path, {'sample_rows': 1})
        with self.assertRaises(ImportFailure):
            run_import(strict, catalog=self.catalog)
        self.assertEqual(1, len(self.catalog.list()))

    def test_exact_decimal_scale_and_naive_timezone_rejections(self):
        for text, dtype in [('1.234', 'decimal128(5, 2)'), ('2026-01-01T00:00:00', 'timestamp[us, tz=UTC]'),
                            ('2026-01-01T00:00:00Z', 'timestamp[us]')]:
            path = self.csv('value\n' + text + '\n')
            plan = self.plan(path, {'schema_policy': {'columns': {'value': dtype}}})
            with self.assertRaises(ImportFailure):
                run_import(plan, catalog=self.catalog)
        self.assertEqual([], self.catalog.list())

    def test_long_quoted_newline_csv_is_not_cut_at_sampling_block(self):
        value = 'hello\n' + 'x' * 4000
        path = self.csv('id,text\n001,"' + value + '"\n')
        source, _ = self.imported(path, {'sample_bytes': 1024, 'infer_types': False})
        frame, error = source.execute_query('SELECT text FROM input')
        self.assertFalse(error, error)
        self.assertEqual(value, frame.iloc[0, 0])

    def test_csv_field_and_lexical_record_limits(self):
        with self.assertRaises(ImportFailure) as result:
            self.plan(self.csv('id,text\n001,' + 'x' * 100 + '\n'), {'max_cell_bytes': 32})
        self.assertEqual('cell_limit', result.exception.code)
        with self.assertRaises(ImportFailure) as result:
            self.plan(self.csv('a,b\n"' + ('x\n' * 100) + '",z\n'), {'max_cell_bytes': 64, 'max_row_bytes': 128})
        self.assertEqual('row_bytes_exceeded', result.exception.code)

    def test_compressed_csv_decompressed_limit(self):
        path = self.root / 'small.csv.gz'
        with gzip.open(path, 'wb') as stream:
            stream.write(b'n\n' + b'1\n' * 3000)
        with self.assertRaises(ImportFailure) as error:
            self.plan(path, {'max_decompressed_bytes': 1024})
        self.assertEqual('decompressed_limit', error.exception.code)

    def test_parquet_preserves_native_precision_nulls_and_nanoseconds(self):
        path = self.root / 'native.parquet'
        table = pa.table({'amount': pa.array([Decimal('123456789012345678901234.1234'), None], type=pa.decimal128(28, 4)),
                          'at': pa.array([1_700_000_000_000_000_123, None], type=pa.timestamp('ns', tz='UTC')),
                          'id': pa.array(['0001', '0002'])})
        pq.write_table(table, path, row_group_size=1)
        source, manifest = self.imported(path)
        actual = pa.Table.from_batches(list(source.execute_query_batches('SELECT amount,id FROM native ORDER BY id')))
        self.assertEqual(table['amount'].to_pylist(), actual['amount'].to_pylist())
        # DuckDB timestamps have microsecond execution precision. The immutable
        # canonical files must still retain native nanoseconds without a query.
        physical = pq.read_table(source.handle.root / manifest['files'][0]['relative_path'])
        self.assertEqual(table['at'], physical['at'])
        metadata = pq.read_metadata(source.handle.root / manifest['files'][0]['relative_path'])
        self.assertEqual(metadata.num_row_groups, len(manifest['files'][0]['row_groups']))
        self.assertEqual(table.num_rows, sum(g['row_count'] for g in manifest['files'][0]['row_groups']))
        with self.assertRaises(ImportFailure) as error:
            list(source.execute_query_batches('SELECT at FROM native'))
        self.assertEqual('unsupported_precision', error.exception.code)

    def test_partition_pruning_and_actual_duckdb_row_group_scan(self):
        path = self.root / 'partition.parquet'
        import hashlib
        table = pa.table({'region': ['a'] * 1000 + ['b'] * 1000, 'n': list(range(2000)),
                          'day': pa.array([dt.date(2026, 1, 1)] * 2000, type=pa.date32()),
                          'payload': [''.join(hashlib.sha256(f'{i}:{j}'.encode()).hexdigest() for j in range(8)) for i in range(2000)]})
        pq.write_table(table, path, row_group_size=100)
        source, manifest = self.imported(path, {'partition_by': ['region', 'day'], 'batch_rows': 100})
        batches = list(source.execute_query_batches("SELECT SUM(n) AS n FROM partition WHERE region='b'"))
        self.assertEqual(1499500, batches[0].column(0)[0].as_py())
        self.assertEqual(1, source.last_scan['pruned_files'])
        date_batches = list(source.execute_query_batches("SELECT COUNT(*) FROM partition WHERE day='2026-1-1'"))
        self.assertEqual(2000, date_batches[0].column(0)[0].as_py())
        baseline_source = DatasetDataSource(self.catalog.get(manifest['dataset_id']), budget=self.budget)
        self.sources.append(baseline_source)
        baseline = json.loads(baseline_source.explain_query('SELECT SUM(length(payload)) FROM partition', analyze=True))
        plan = json.loads(source.explain_query('SELECT SUM(length(payload)) FROM partition WHERE n>=1990', analyze=True))
        def nodes(value):
            if isinstance(value, dict):
                yield value
                for v in value.values():
                    yield from nodes(v)
            elif isinstance(value, list):
                for v in value:
                    yield from nodes(v)
        scans = [n for n in nodes(plan) if n.get('extra_info', {}).get('Function') == 'READ_PARQUET']
        self.assertTrue(scans, plan)
        self.assertGreater(baseline['total_bytes_read'], 0)
        self.assertGreater(plan['total_bytes_read'], 0)
        self.assertLess(plan['total_bytes_read'], baseline['total_bytes_read'])
        self.assertEqual(10, scans[0]['operator_cardinality'])

    def test_multiple_sheets_mapping_and_excel_date_epoch(self):
        path = self.root / 'book.xlsx'
        workbook = openpyxl.Workbook()
        workbook.epoch = openpyxl.utils.datetime.CALENDAR_MAC_1904
        first = workbook.active
        first.title = 'a-b'
        first.append(['id', 'when', 'amount'])
        first.append(['001', dt.datetime(2024, 1, 2), 45200.5])
        second = workbook.create_sheet('a b')
        second.append(['name', 'value'])
        second.append(['NA', 2])
        workbook.save(path)
        workbook.close()
        source, manifest = self.imported(path)
        self.assertEqual(['a_b', 'a_b_2'], source.list_tables())
        self.assertEqual({'a_b', 'a_b_2'}, {f['table'] for f in manifest['files']})
        table = pa.Table.from_batches(list(source.execute_query_batches('SELECT * FROM a_b')))
        self.assertEqual(dt.datetime(2024, 1, 2), table['when'][0].as_py())
        self.assertEqual(Decimal('45200.5'), table['amount'][0].as_py())
        self.assertEqual('NA', source.get_preview_table('a_b_2')['rows'][0][0])
        epoch1900 = self.root / 'epoch1900.xlsx'
        workbook = openpyxl.load_workbook(path)
        workbook.epoch = openpyxl.utils.datetime.CALENDAR_WINDOWS_1900
        workbook.save(epoch1900)
        workbook.close()
        other, _ = self.imported(epoch1900)
        actual = pa.Table.from_batches(list(other.execute_query_batches('SELECT "when" FROM a_b')))
        self.assertEqual(dt.datetime(2024, 1, 2), actual['when'][0].as_py())

    def test_xlsx_formula_decompression_cells_and_shared_string_limits(self):
        path = self.root / 'book.xlsx'
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(['n'])
        sheet.append(['=1+1'])
        workbook.save(path)
        with self.assertRaises(ImportFailure) as result:
            self.plan(path)
        self.assertEqual('xlsx_formula', result.exception.code)
        sheet['A2'] = 'value'
        workbook.save(path)
        workbook.close()
        with self.assertRaises(ImportFailure):
            self.plan(path, {'max_xml_bytes': 100})
        with self.assertRaises(ImportFailure):
            self.plan(path, {'max_cells': 1})
        import zipfile
        shared = self.root / 'shared.xlsx'
        with zipfile.ZipFile(path) as original, zipfile.ZipFile(shared, 'w') as modified:
            for entry in original.infolist():
                modified.writestr(entry, original.read(entry.filename))
            modified.writestr('xl/sharedStrings.xml', '<sst>' + 'x' * 1024 + '</sst>')
        with self.assertRaises(ImportFailure) as result:
            self.plan(shared, {'max_shared_strings_bytes': 128})
        self.assertEqual('xlsx_shared_strings_limit', result.exception.code)

    def test_source_changed_plan_tampering_and_query_file_access(self):
        path = self.csv('n\n1\n')
        plan = self.plan(path)
        path.write_text('n\n2\n', encoding='utf-8')
        with self.assertRaises(ImportFailure) as result:
            run_import(plan, catalog=self.catalog)
        self.assertEqual('dataset_changed', result.exception.code)
        changed = {**plan, 'options': {'infer_types': False}}
        with self.assertRaises(ImportFailure) as result:
            run_import(changed, catalog=self.catalog)
        self.assertEqual('plan_changed', result.exception.code)
        for sql in ("SELECT * FROM read_parquet('secret.parquet')", "SELECT read_blob('secret')", 'SET threads=99',
                    "SELECT * FROM 'secret.csv'", 'SELECT 1;SELECT 2', 'SELECT getenv(\'PATH\')',
                    'SELECT * FROM information_schema.tables'):
            with self.assertRaises(ImportFailure, msg=sql):
                validate_snapshot_query(sql, ['input'])

    def test_result_rows_bytes_cancel_and_legacy_bounds(self):
        source, _ = self.imported(self.csv('n\n1\n2\n3\n'))
        with self.assertRaises(BudgetExceeded):
            list(source.execute_query_batches('SELECT * FROM input', budget={**self.budget.to_dict(), 'max_rows': 2}))
        with self.assertRaises(ImportFailure):
            list(source.execute_query_batches('SELECT * FROM input', cancel=lambda: True))
        with self.assertRaises(BudgetExceeded):
            list(source.execute_query_batches('SELECT * FROM input', budget={**self.budget.to_dict(), 'max_output_bytes': 1}))
        frame, error = source.execute_query('SELECT * FROM input CROSS JOIN range(40000)')
        self.assertTrue(error)
        self.assertTrue(frame.empty)
        self.assertEqual(0, source.legacy_bounded_frame('SELECT * FROM input WHERE FALSE').shape[0])

    def test_idempotent_import_old_handle_and_derived_empty_result(self):
        path = self.csv('id,n\n001,1\n002,2\n')
        source, manifest = self.imported(path)
        replay = run_import(self.plan(path), catalog=self.catalog)
        self.assertEqual(manifest['snapshot_id'], replay['snapshot_id'])
        derived = source.derive_snapshot('SELECT id,n*2 AS doubled FROM input WHERE n>1', catalog=self.catalog, idempotency_key='derived')
        result = DatasetDataSource(self.catalog.get(derived['dataset_id']), budget=self.budget)
        self.sources.append(result)
        self.assertEqual([['002', 4]], result.get_preview_table('derived')['rows'])
        self.assertEqual(source.handle.snapshot_id, derived['lineage']['parent_snapshot_id'])
        empty = source.derive_snapshot('SELECT CAST(n AS DECIMAL(9,2)) AS amount FROM input WHERE FALSE', catalog=self.catalog, name='empty')
        self.assertEqual(0, empty['row_count'])
        self.assertEqual('decimal128(9, 2)', empty['schema'][0]['type'])
        self.assertEqual(2, source.get_preview_table('input')['total_rows'])

    def test_failed_import_and_cancel_preserve_active_version(self):
        path = self.csv('n\n1\n')
        source, manifest = self.imported(path)
        path.write_text('n\n2\n3\n', encoding='utf-8')
        plan = self.plan(path, {'dataset_id': manifest['dataset_id'], 'expected_version': 1})
        with self.assertRaises(ImportFailure):
            run_import(plan, catalog=self.catalog, cancel=lambda: True)
        limited = self.plan(path, {'dataset_id': manifest['dataset_id']}, budget={**self.budget.to_dict(), 'max_rows': 1})
        with self.assertRaises(ImportFailure):
            run_import(limited, catalog=self.catalog)
        self.assertEqual(manifest['snapshot_id'], self.catalog.get(manifest['dataset_id']).snapshot_id)
        self.assertEqual(0, ResourceCoordinator().snapshot()['slots'])

    def test_late_cancel_after_publication_preserves_committed_result(self):
        plan = self.plan(self.csv('n\n1\n'))
        stop = threading.Event()
        def progress(percent, message):
            if percent == 100:
                stop.set()
                raise RuntimeError('observer disappeared')
        result = run_import(plan, catalog=self.catalog, progress=progress, cancel=stop.is_set)
        self.assertTrue(stop.is_set())
        self.assertEqual(result['snapshot_id'], self.catalog.get(result['dataset_id']).snapshot_id)

    def test_workspace_stream_backpressure_fencing_and_cache_rebuild(self):
        from data.sources.workspace_persistent import WorkspacePersistentSource, _install_snapshot
        path = self.csv('n\n' + '1\n' * 10000)
        first, _ = self.imported(path)
        path.write_text('n\n' + '2\n' * 10000, encoding='utf-8')
        second, _ = self.imported(path)
        workspace = WorkspacePersistentSource(str(self.root / 'workspace.duckdb'))
        self.sources.append(workspace)
        _install_snapshot(workspace._owner, first.handle, file_key='input.csv', expected_snapshot=None)
        with self.assertRaises(ValueError):
            _install_snapshot(workspace._owner, second.handle, file_key='input.csv', expected_snapshot=None)
        stream = workspace.execute_query_batches('SELECT n FROM input', budget=self.budget)
        self.assertEqual(1, next(stream).column(0)[0].as_py())
        finished, failures = threading.Event(), []
        def replace():
            try:
                _install_snapshot(workspace._owner, second.handle, file_key='input.csv', expected_snapshot=first.handle.snapshot_id)
            except BaseException as exc:
                failures.append(exc)
            finally:
                finished.set()
        writer = threading.Thread(target=replace)
        writer.start()
        self.assertFalse(finished.wait(0.05))
        stream.close()
        self.assertTrue(finished.wait(10), 'producer failed to release owner after consumer closed')
        writer.join()
        self.assertFalse(failures, failures)
        actual = pa.Table.from_batches(list(workspace.execute_query_batches('SELECT SUM(n) AS n FROM input', budget=self.budget)))
        self.assertEqual(20000, actual['n'][0].as_py())
        preview = workspace.get_preview_table('input', max_rows=2)
        self.assertNotIn('error', preview, preview)
        self.assertEqual([['2'], ['2']], preview['rows'])
        self.assertEqual(10000, preview['total_rows'])
        class Runtime:
            records = {}
            def load_registry(self):
                return dict(self.records)
            def save_registry(self, value):
                self.records = value
        runtime = Runtime()
        workspace.reconcile_mounts(runtime)
        self.assertEqual(second.handle.snapshot_id, runtime.records['input.csv']['snapshot_id'])
        self.assertEqual(0, ResourceCoordinator().snapshot()['slots'])

    def test_snapshot_pins_reject_mutation_and_late_id_type_drift(self):
        source, manifest = self.imported(self.csv('n\n' + '1\n' * 3000))
        stream = source.execute_query_batches('SELECT n FROM input')
        next(stream)
        shard = source.handle.root / manifest['files'][0]['relative_path']
        if os.name == 'nt':
            with self.assertRaises(OSError):
                shard.write_bytes(b'changed')
        else:
            shard.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                next(stream)
        stream.close()
        plan = self.plan(self.csv('value\n1\n001\n'), {'sample_rows': 1})
        with self.assertRaises(ImportFailure) as error:
            run_import(plan, catalog=self.catalog)
        self.assertEqual('schema_drift', error.exception.code)

    def test_small_csv_excel_workspace_compatibility_and_exact_shadow(self):
        from data.sources.csv import CSVDataSource
        from data.sources.excel import ExcelDataSource
        from data.sources.workspace_persistent import WorkspacePersistentSource
        csv_source = CSVDataSource(str(self.csv('id,amount\n001,1.25\n002,2.50\n')), 'input.csv')
        self.sources.append(csv_source)
        frame, error = csv_source.execute_query('SELECT SUM(amount) FROM input')
        self.assertFalse(error, error)
        self.assertEqual(3.75, float(frame.iloc[0, 0]))
        path = self.root / 'book.xlsx'
        book = openpyxl.Workbook()
        book.active.append(['id', 'amount'])
        book.active.append(['001', 45200.5])
        book.save(path)
        book.close()
        excel_source = ExcelDataSource(str(path), 'book.xlsx')
        self.sources.append(excel_source)
        frame, error = excel_source.execute_query('SELECT amount FROM Sheet')
        self.assertFalse(error, error)
        self.assertEqual(45200.5, float(frame.iloc[0, 0]))
        db = self.root / 'workspace.duckdb'
        conn = duckdb.connect(str(db))
        conn.execute('CREATE TABLE t AS SELECT 123456789012345678.90::DECIMAL(20,2) AS amount')
        conn.close()
        workspace = WorkspacePersistentSource(str(db))
        self.sources.append(workspace)
        frame, error = workspace.execute_query('UPDATE t SET amount=amount+0.01')
        self.assertFalse(error, error)
        frame, error = workspace.execute_query('SELECT amount FROM t')
        self.assertEqual(Decimal('123456789012345678.91'), frame.iloc[0, 0])
        self.assertEqual((Decimal('123456789012345678.90'),), workspace._owner.execute('SELECT amount FROM t').fetchone())


if __name__ == '__main__':
    unittest.main()
