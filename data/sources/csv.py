"""CSV compatibility facade: bounded small tables, immutable large snapshots."""
from pathlib import Path

from ._utils import (_clean_identifier, _list_tables, _new_conn, _preview_table_dict,
                     _query, _register, _table_schema_str)
from .base import DataSource

SMALL_CSV_BYTES = 25_000_000


class CSVDataSource(DataSource):
    def __new__(cls, file_path: str, filename: str):
        if Path(file_path).stat().st_size > SMALL_CSV_BYTES:
            from data.import_pipeline import import_file_source
            return import_file_source(file_path, options={'table_name': _clean_identifier(Path(filename).stem)})
        return super().__new__(cls)

    def __init__(self, file_path: str, filename: str):
        from data.import_pipeline import _create_import_plan_local, _batches
        from infrastructure.resource_budget import BudgetSpec
        self.name, self.file_path = filename, file_path
        self._table = _clean_identifier(Path(filename).stem)
        self._conn = _new_conn()
        budget = BudgetSpec(max_input_bytes=SMALL_CSV_BYTES, max_output_bytes=128 << 20, max_rows=1_000_000)
        try:
            plan = _create_import_plan_local(file_path, owner_id='local-default', session_id='',
                                      options={'table_name': self._table}, budget=budget)
            def reject(code, message, **details):
                from data.import_schema import ImportFailure
                raise ImportFailure(code, message)
            schema = __import__('data.import_schema', fromlist=['to_arrow_schema']).to_arrow_schema(plan['tables'][0]['schema_policy'])
            import pyarrow as pa
            self._conn.register('_csv_batch', pa.Table.from_batches([], schema=schema))
            self._conn.execute(f'CREATE TABLE "{self._table}" AS SELECT * FROM _csv_batch')
            rows = size = 0
            for _, batch in _batches(plan, reject_row=reject, cancel=None):
                rows += batch.num_rows
                size += batch.nbytes
                budget.check_usage(rows=rows, output_bytes=size)
                self._conn.register('_csv_batch', batch)
                self._conn.execute(f'INSERT INTO "{self._table}" SELECT * FROM _csv_batch')
            self._conn.unregister('_csv_batch')
        except BaseException:
            self._conn.close()
            raise

    def get_schema(self):
        return '\n\n'.join(_table_schema_str(self._conn, t, self._conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in self.list_tables())

    def list_tables(self):
        return _list_tables(self._conn)

    def execute_query(self, sql):
        from .dataset import bounded_legacy_query
        return bounded_legacy_query(self._conn, sql)

    def create_analysis_table(self, sql, table_name='analysis_data', _df=None):
        table_name = _clean_identifier(table_name)
        try:
            if _df is not None:
                _register(self._conn, table_name, _df)
            else:
                self._conn.execute(f'CREATE OR REPLACE TABLE "{table_name}" AS ({sql})')
            rows = self._conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            return _table_schema_str(self._conn, table_name, rows)
        except Exception as exc:
            return f'Error building analysis table: {exc}'

    def get_preview(self):
        return [{'name': t, 'columns': [r[0] for r in self._conn.execute(f'DESCRIBE "{t}"').fetchall()],
                 'total_rows': self._conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]} for t in self.list_tables()]

    def get_preview_table(self, table_name, max_rows=100):
        if table_name not in self.list_tables():
            return {'name': table_name, 'columns': [], 'rows': [], 'total_rows': 0, 'error': 'unknown_table'}
        return _preview_table_dict(self._conn, table_name, table_name, max(0, min(int(max_rows), 1000)))

    def close(self):
        self._conn.close()
