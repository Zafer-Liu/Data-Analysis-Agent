"""Read-only, bounded DuckDB access to a shared immutable DatasetHandle."""
from __future__ import annotations

import datetime as dt
from contextlib import ExitStack, nullcontext
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import duckdb
import pandas as pd

from .base import DataSource
from data.dataset_snapshot import SnapshotWriter, canonical_hash, check_cancel
from data.import_schema import ImportFailure, arrow_type, schema_fields


def _budget(value):
    from infrastructure.resource_budget import BudgetSpec
    return value if isinstance(value, BudgetSpec) else BudgetSpec.from_dict(value)


def quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def snapshot_scan_sql(handle, table_name=None, *, files=None):
    """Trusted bridge: fixed, validated manifest paths for the merge layer."""
    from data.dataset_manifest import validate_manifest
    manifest = validate_manifest(handle.manifest, root=handle.root, verify_files=False)
    entries = files if files is not None else manifest["files"]
    if any(f not in manifest['files'] or f['format'] != 'parquet' for f in entries):
        raise ImportFailure('unsafe_snapshot_path', 'Scans must use declared immutable Parquet shards')
    default_table = (manifest.get('tables') or [{'name': manifest['files'][0].get('table', 'data') if manifest['files'] else 'data'}])[0]['name']
    if table_name is not None:
        entries = [f for f in entries if f.get('table', default_table) == table_name]
    paths = [str(Path(handle.root) / f['relative_path']) for f in entries]
    if not paths:
        raise ImportFailure("empty_scan", "No snapshot files selected")
    quoted = ", ".join("'" + p.replace("'", "''") + "'" for p in paths)
    return f"read_parquet([{quoted}], hive_partitioning=false, union_by_name=false)"


_SAFE_ANONYMOUS = {"regexp_matches", "regexp_replace", "regexp_extract", "regexp_extract_all", "regexp_full_match",
                   "date_diff", "date_part", "date_trunc", "strftime", "strptime", "try_strptime", "epoch", "epoch_ms",
                   "quantile_cont", "quantile_disc", "approx_quantile", "approx_count_distinct", "median", "mode",
                   "corr", "covar_pop", "covar_samp", "stddev", "stddev_pop", "stddev_samp", "variance", "var_pop", "var_samp",
                   "isfinite", "isinf", "isnan", "greatest", "least", "typeof", "round", "ceil", "ceiling", "floor",
                   "trim", "ltrim", "rtrim", "length", "char_length", "lower", "upper", "replace", "concat", "concat_ws",
                   "substring", "substr", "split_part", "string_split", "list_value", "list_contains", "array_length",
                   "count_if", "sum", "avg", "min", "max", "count", "first", "last", "arg_min", "arg_max"}


def validate_snapshot_query(sql, tables):
    """Fail closed on parse failures, writes, external scans and unknown UDFs."""
    import sqlglot
    from sqlglot import exp
    try:
        statements = [node for node in sqlglot.parse(sql, read="duckdb", error_level=sqlglot.ErrorLevel.RAISE) if node is not None]
    except Exception as exc:
        raise ImportFailure("unsafe_sql", "SQL could not be parsed as a read-only DuckDB query") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ImportFailure("read_only", "Snapshot queries must contain one SELECT/CTE/set query")
    tree = statements[0]
    banned = {"Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command", "Copy", "Attach", "Detach", "Pragma", "Set", "Into",
              "ReadCSV", "ReadParquet", "ReadJSON", "LoadData", "Install", "Export", "Import", "Execute", "Use", "Transaction", "Commit"}
    for node in tree.walk():
        if type(node).__name__ in banned:
            raise ImportFailure("unsafe_sql", f"Disallowed SQL node: {type(node).__name__}")
        if isinstance(node, exp.Anonymous) and node.name.casefold() not in _SAFE_ANONYMOUS:
            raise ImportFailure("unsafe_sql", f"Function is not in the snapshot read-only allowlist: {node.name}")
    known = {str(name).casefold() for name in tables}
    ctes = {cte.alias_or_name.casefold() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier) or table.this.args.get("is_string") or table.args.get("db") or table.args.get("catalog"):
            raise ImportFailure("unsafe_sql", "External/catalog/table-function scans are forbidden")
        if table.name.casefold() not in known | ctes:
            raise ImportFailure("unknown_table", f"Unknown snapshot table: {table.name}")
    # Built-in functions with specialized nodes can also perform external IO.
    for node in tree.find_all(exp.Func):
        label = str(node.sql_name()).casefold()
        if any(part in label for part in ("read_", "scan", "http", "sqlite", "postgres", "mysql", "glob", "getenv", "query", "secret", "extension")):
            raise ImportFailure("unsafe_sql", f"External function is forbidden: {label}")
    return tree


def _partition_constraints(tree, tables):
    """Conservative top-level conjunctions only; OR/joins/subqueries keep all files."""
    from sqlglot import exp
    if not isinstance(tree, exp.Select) or tree.args.get("joins") or tree.args.get("with_") or len(list(tree.find_all(exp.Select))) != 1:
        return {}
    source = tree.args.get("from_")
    if not source or not isinstance(source.this, exp.Table) or source.this.name not in tables:
        return {}
    where = tree.args.get("where")
    if where is None:
        return {}
    predicates = []
    def collect(node):
        if isinstance(node, exp.And):
            collect(node.this)
            collect(node.expression)
        else:
            predicates.append(node)
    collect(where.this)
    result = {}
    for pred in predicates:
        if isinstance(pred, exp.EQ):
            col, value = pred.this, pred.expression
            if isinstance(value, exp.Column) and isinstance(col, exp.Literal):
                col, value = value, col
            if isinstance(col, exp.Column) and isinstance(value, exp.Literal):
                # Preserve literal type; DuckDB implicit casts must not cause an
                # optimizer helper to prune a partition that SQL would include.
                result[col.name] = (value.this, bool(value.is_string))
        elif isinstance(pred, exp.Is) and isinstance(pred.this, exp.Column) and isinstance(pred.expression, exp.Null):
            result[pred.this.name] = (None, True)
    return {source.this.name: result}


def query_batch_rows(cursor, tree, tables, limits, *, ceiling=4096):
    """Use fixed result widths or proved import row bounds, never a mean width."""
    from sqlglot import exp
    types = [str(column[1]).upper() for column in (cursor.description or [])]
    widths = {'BOOLEAN': 1, 'TINYINT': 1, 'UTINYINT': 1, 'SMALLINT': 2, 'USMALLINT': 2,
              'INTEGER': 4, 'UINTEGER': 4, 'FLOAT': 4, 'DOUBLE': 8, 'BIGINT': 8, 'UBIGINT': 8,
              'HUGEINT': 16, 'UHUGEINT': 16, 'DATE': 4, 'TIME': 8, 'TIMESTAMP': 8,
              'TIMESTAMP_NS': 8, 'TIMESTAMP_MS': 8, 'TIMESTAMP_S': 8, 'TIMESTAMP WITH TIME ZONE': 8}
    if types and all(dtype in widths or dtype.startswith('DECIMAL(') for dtype in types):
        row_bound = sum(widths.get(dtype, 16) + 1 for dtype in types)
    else:
        row_bound = limits.batch_bytes
        if isinstance(tree, exp.Select):
            projections = [node.this if isinstance(node, exp.Alias) else node for node in tree.expressions]
            referenced = [node.name.casefold() for node in tree.find_all(exp.Table)]
            mapping = {table['name'].casefold(): table for table in tables}
            if referenced and all(isinstance(p, (exp.Column, exp.Star)) for p in projections) and all(
                    name in mapping and mapping[name].get('max_row_bytes', 0) > 0 for name in referenced):
                row_bound = sum(mapping[name]['max_row_bytes'] + len(DatasetDataSource._fields(mapping[name])) * 8
                                for name in referenced) * max(1, len(projections))
    return max(1, min(ceiling, limits.max_rows + 1, limits.batch_bytes // max(1, row_bound)))


def bounded_legacy_query(conn, sql, *, budget=None, cancel=None):
    """Small mutable-source compatibility, with exact Arrow types and hard result caps."""
    import pyarrow as pa
    limits = _budget(budget or {'max_rows': 100000, 'max_output_bytes': 64 << 20,
                                'max_run_seconds': 60, 'duckdb_memory_bytes': 128 << 20,
                                'batch_bytes': 1 << 20, 'temp_bytes': 64 << 20})
    stop, errors = threading.Event(), []
    started = time.monotonic()
    def watch():
        import psutil
        while not stop.wait(0.05):
            try:
                check_cancel(cancel)
                limits.check_usage(run_seconds=time.monotonic() - started, rss_bytes=psutil.Process().memory_info().rss)
            except BaseException as exc:
                errors.append(exc)
                conn.interrupt()
                return
    watcher = threading.Thread(target=watch, name='small-source-budget', daemon=True)
    reader = None
    watcher.start()
    try:
        check_cancel(cancel)
        conn.execute(f"SET memory_limit='{limits.duckdb_memory_bytes}B'")
        conn.execute(f"SET max_temp_directory_size='{limits.temp_bytes}B'")
        cursor = conn.execute(sql)
        reader = cursor.to_arrow_reader(batch_size=query_batch_rows(cursor, None, [], limits, ceiling=1024))
        schema, batches, rows, size = reader.schema, [], 0, 0
        for batch in reader:
            check_cancel(cancel)
            if errors:
                raise errors[0]
            rows += batch.num_rows
            size += batch.nbytes
            limits.check_usage(rows=rows, output_bytes=size, run_seconds=time.monotonic() - started)
            batches.append(batch)
        if errors:
            raise errors[0]
        return pa.Table.from_batches(batches, schema=schema).to_pandas(types_mapper=pd.ArrowDtype), ''
    except Exception as exc:
        if errors:
            exc = errors[0]
        return pd.DataFrame(), f"{getattr(exc, 'code', 'query_failed')}: {exc}"
    finally:
        stop.set()
        watcher.join(timeout=1)
        if reader is not None:
            reader.close()


def validate_snapshot_precision(tree, tables):
    """Fail explicitly where DuckDB would narrow a native Arrow value."""
    from sqlglot import exp
    import re
    referenced = {t.name.casefold() for t in tree.find_all(exp.Table)}
    def unsupported(dtype):
        text = str(dtype)
        decimal = re.fullmatch(r'decimal(?:128|256)\((\d+),\s*(-?\d+)\)', text)
        return bool((decimal and (int(decimal[1]) > 38 or int(decimal[2]) < 0)) or
                    re.fullmatch(r'timestamp\[ns,\s*tz=.+\]|time64\[ns\]|duration\[.+\]', text))
    sensitive = set()
    for table in tables:
        if table['name'].casefold() not in referenced:
            continue
        fields = table['schema'].get('fields', []) if isinstance(table['schema'], dict) else table['schema']
        sensitive.update(f['name'].casefold() for f in fields if unsupported(f['type']))
    if sensitive:
        selected = {c.name.casefold() for c in tree.find_all(exp.Column)}
        stars = any(not isinstance(node.parent, exp.Count) for node in tree.find_all(exp.Star))
        if selected & sensitive or stars:
            raise ImportFailure('unsupported_precision', 'DuckDB cannot represent these native Arrow fields exactly. Canonical Parquet files remain exact; select supported fields or prepare an explicit conversion plan.')


class DatasetDataSource(DataSource):
    def __init__(self, handle, *, budget=None, name=""):
        from data.dataset_manifest import DatasetHandle, validate_manifest
        self.handle = handle if isinstance(handle, DatasetHandle) else DatasetHandle.from_dict(handle)
        self._manifest_json = json.dumps(validate_manifest(self.handle.manifest, root=self.handle.root, verify_files=True), ensure_ascii=False)
        self.budget = _budget(budget)
        self.name = name or self.manifest["source"].get("name") or self.manifest["dataset_id"]
        self.file_path = str(self.handle.root)
        default_table = self.manifest['files'][0].get('table', 'data') if self.manifest['files'] else 'data'
        self._tables = self.manifest.get("tables") or [{"name": default_table, "schema": self.manifest["schema"], "row_count": self.manifest["row_count"]}]
        if isinstance(self._tables, dict):
            self._tables = [{"name": name_key, **value} for name_key, value in self._tables.items()]
        self._lock, self._closed = threading.RLock(), False
        self._temp = tempfile.TemporaryDirectory(prefix="baa-dataset-query-")
        self._conn = duckdb.connect(":memory:")
        self._conn.execute(f"SET threads={self.budget.threads}")
        self._conn.execute(f"SET memory_limit='{self.budget.duckdb_memory_bytes}B'")
        self._conn.execute("SET temp_directory = ?", [self._temp.name])
        self._conn.execute(f"SET max_temp_directory_size='{self.budget.temp_bytes}B'")
        self._conn.execute("SET autoinstall_known_extensions=false")
        self._conn.execute("SET autoload_known_extensions=false")
        self.last_scan = {}
        self._last_error_code = ""
        self._install_views(self.manifest["files"])

    @property
    def manifest(self):
        return json.loads(self._manifest_json)

    @property
    def capabilities(self):
        return {"dialect": "duckdb", 'query_dialect': 'duckdb', 'batch_reader': True, 'requires_table_selection': True,
                "immutable": True, "large": True, "execute_query_batches": True,
                "native_cancel": True, "read_only": True, "snapshot": True, "projection_pushdown": True,
                "partition_pruning": True, "row_group_pruning": True, "bounded_legacy_result": True,
                "budget_enforcement": {"rows": "hard_result", "bytes": "checked_per_arrow_batch", "rss": "monitored", "run_seconds": "interrupt"}}

    def get_capabilities(self):
        return self.capabilities

    def list_tables(self):
        return [table["name"] for table in self._tables]

    def get_schema(self):
        return "\n\n".join(f"Table: {t['name']} ({t['row_count']} rows)\n" + "\n".join(f"  {f['name']}: {f['type']}" for f in self._fields(t)) for t in self._tables)

    @staticmethod
    def _fields(table):
        schema = table["schema"]
        return schema.get("fields", []) if isinstance(schema, dict) else schema

    def get_preview(self):
        return [{"name": t["name"], "columns": [f["name"] for f in self._fields(t)], "total_rows": t["row_count"],
                 "dataset_id": self.manifest["dataset_id"], "snapshot_id": self.manifest["snapshot_id"]} for t in self._tables]

    def get_table_schema(self, table_name):
        for table in self._tables:
            if table["name"] == table_name:
                import copy
                fields = copy.deepcopy(self._fields(table))
                return {"name": table_name, "columns": [f["name"] for f in fields], "fields": fields,
                        "schema_available": True, "total_rows": table["row_count"]}
        return {"name": table_name, "columns": [], "schema_available": False}

    def _install_views(self, files):
        for table in self._tables:
            selected = [f for f in files if f.get("table", table["name"]) == table["name"]]
            if selected:
                query = "SELECT * FROM " + snapshot_scan_sql(self.handle, table["name"], files=selected)
            else:
                import pyarrow as pa
                schema = pa.schema([pa.field(f["name"], arrow_type(f["type"])) for f in self._fields(table)])
                empty = pa.Table.from_batches([], schema=schema)
                temp = "_empty_" + table["name"]
                self._conn.register(temp, empty)
                query = "SELECT * FROM " + quote_identifier(temp)
            self._conn.execute(f"CREATE OR REPLACE VIEW {quote_identifier(table['name'])} AS {query}")

    def _select_files(self, tree):
        constraints = _partition_constraints(tree, self.list_tables())
        result = []
        for entry in self.manifest["files"]:
            table_name = entry.get('table', self._tables[0]['name'])
            tests = constraints.get(table_name, {})
            fields = next(self._fields(t) for t in self._tables if t['name'] == table_name)
            field_types = {f['name']: f['type'] for f in fields}
            partition = entry.get("partition", {})
            keep = True
            for col, (value, string_literal) in tests.items():
                if col not in partition:
                    continue
                actual = partition[col]
                if value is None:
                    keep = actual is None
                elif isinstance(actual, str) and string_literal and field_types.get(col) in ('string', 'large_string', 'string_view'):
                    keep = actual == value
                elif isinstance(actual, int) and not isinstance(actual, bool) and not string_literal:
                    import re
                    # Floating/exponent literals can coerce BIGINTs to DOUBLE;
                    # only exact integer literals are safe for manual pruning.
                    if re.fullmatch(r'[+]?[0-9]+', str(value)) and int(value) <= (1 << 63) - 1:
                        keep = actual == int(value)
                # Ambiguous implicit casts/date/bool comparisons are delegated
                # to DuckDB; never remove files on an approximate comparison.
                if not keep:
                    break
            if keep:
                result.append(entry)
        self.last_scan = {"total_files": len(self.manifest["files"]), "selected_files": len(result),
                          "pruned_files": len(self.manifest["files"]) - len(result),
                          "selected_file_bytes": sum(f["size_bytes"] for f in result), "snapshot_id": self.manifest["snapshot_id"]}
        return result

    def _check_precision(self, tree):
        validate_snapshot_precision(tree, self._tables)

    def execute_query_batches(self, sql, *, budget=None, cancel=None):
        yield from self._execute_batches(sql, budget=budget, cancel=cancel)

    def _execute_batches(self, sql, *, budget=None, cancel=None, explain=''):
        limits = _budget(budget) if budget is not None else self.budget
        # Call-specific limits may tighten, never expand a source's grant.
        values = limits.to_dict()
        for key, value in self.budget.to_dict().items():
            if key in values and isinstance(value, (int, float)) and isinstance(values[key], (int, float)):
                values[key] = min(value, values[key])
        limits = _budget(values)
        tree = validate_snapshot_query(sql, self.list_tables())
        self._check_precision(tree)
        from infrastructure.resource_budget import ResourceCoordinator
        import uuid
        reservation = limits.to_dict()
        reservation.update(max_input_bytes=1, max_output_bytes=1)
        if getattr(self, '_supervised', False):
            lease = nullcontext()
        else:
            coordinator = ResourceCoordinator()
            coordinator.register_account_path(self.handle.root)
            lease = coordinator.reserve(self.manifest['owner_id'], 'query-' + uuid.uuid4().hex, reservation)
        with lease, self._lock, ExitStack() as reads:
            if self._closed:
                raise ImportFailure("source_closed", "Dataset source has been closed")
            started = time.monotonic()
            check_cancel(cancel)
            files = self._select_files(tree)
            limits.check_usage(input_bytes=sum(f['size_bytes'] for f in files))
            from data.dataset_manifest import PinnedSnapshot
            pinned = reads.enter_context(PinnedSnapshot(self.handle, files=files, budget=limits, cancel=cancel))
            self._install_views(files)
            stop, interrupted = threading.Event(), []
            def watch():
                while not stop.wait(0.05):
                    try:
                        check_cancel(cancel)
                        limits.check_usage(run_seconds=time.monotonic() - started)
                        import psutil
                        limits.check_usage(rss_bytes=psutil.Process().memory_info().rss)
                        from infrastructure.resource_budget import directory_usage
                        limits.check_usage(temp_bytes=directory_usage(self._temp.name)['bytes'])
                    except BaseException as exc:
                        interrupted.append(exc)
                        self._conn.interrupt()
                        return
            watcher = threading.Thread(target=watch, name="dataset-query-cancel", daemon=True)
            watcher.start()
            reader = None
            try:
                self._conn.execute(f"SET threads={limits.threads}")
                self._conn.execute(f"SET memory_limit='{limits.duckdb_memory_bytes}B'")
                self._conn.execute(f"SET max_temp_directory_size='{limits.temp_bytes}B'")
                # DuckDB exposes a row-count fetch size. Fixed result widths or
                # observed immutable row bounds choose it; unproved variable
                # expressions use one row and retain monitored RSS enforcement.
                prefix = 'EXPLAIN (ANALYZE, FORMAT JSON) ' if explain == 'analyze' else 'EXPLAIN ' if explain == 'plan' else ''
                cursor = self._conn.execute(prefix + sql)
                batch_rows = query_batch_rows(cursor, tree, self._tables, limits)
                reader = cursor.to_arrow_reader(batch_size=batch_rows)
                self._result_schema = reader.schema
                rows, output_bytes = 0, 0
                for batch in reader:
                    if interrupted:
                        raise interrupted[0]
                    check_cancel(cancel)
                    limits.check_usage(run_seconds=time.monotonic() - started)
                    def bounded(batch):
                        if batch.nbytes <= limits.batch_bytes:
                            yield batch
                        elif batch.num_rows > 1:
                            middle = batch.num_rows // 2
                            yield from bounded(batch.slice(0, middle))
                            yield from bounded(batch.slice(middle))
                        else:
                            from infrastructure.resource_budget import BudgetExceeded
                            raise BudgetExceeded('batch_bytes', batch.nbytes, limits.batch_bytes)
                    for piece in bounded(batch):
                        pinned.check()
                        check_cancel(cancel)
                        rows += piece.num_rows
                        output_bytes += piece.nbytes
                        limits.check_usage(rows=rows, output_bytes=output_bytes)
                        yield piece
                if interrupted:
                    raise interrupted[0]
                self.last_scan.update({"result_rows": rows, "result_bytes": output_bytes, "complete": True})
            except BaseException:
                self.last_scan["complete"] = False
                if interrupted:
                    raise interrupted[0]
                raise
            finally:
                stop.set()
                watcher.join(timeout=1)
                if reader is not None:
                    reader.close()

    def legacy_bounded_frame(self, sql, *, budget=None, cancel=None):
        import pyarrow as pa
        values = (_budget(budget) if budget is not None else self.budget).to_dict()
        values['max_rows'] = min(values['max_rows'], 100000)
        values['max_output_bytes'] = min(values['max_output_bytes'], 64 << 20)
        with self._lock:
            batches = list(self.execute_query_batches(sql, budget=values, cancel=cancel))
            table = pa.Table.from_batches(batches, schema=self._result_schema)
            frame = table.to_pandas(types_mapper=pd.ArrowDtype)
            _budget(values).check_usage(output_bytes=int(frame.memory_usage(index=True, deep=True).sum()))
            return frame

    def execute_query(self, sql):
        try:
            return self.legacy_bounded_frame(sql), ''
        except Exception as exc:
            code = getattr(exc, "code", "query_failed")
            self._last_error_code = code
            return pd.DataFrame(), str(exc) if str(exc).startswith(f"{code}:") else f"{code}: {exc}"

    def get_preview_table(self, table_name, max_rows=100):
        schema = self.get_table_schema(table_name)
        if not schema["schema_available"]:
            return {"name": table_name, "columns": [], "rows": [], "total_rows": 0, "error": "unknown_table"}
        count = max(0, min(int(max_rows), 1000, self.budget.max_rows))
        frame, error = self.execute_query(f"SELECT * FROM {quote_identifier(table_name)} LIMIT {count}")
        if error:
            return {"name": table_name, "columns": schema["columns"], "rows": [], "total_rows": schema["total_rows"], "error": error}
        # Only this explicitly bounded preview is converted to JSON-compatible rows.
        import decimal
        def json_value(value):
            if value is None or value is pd.NA or value is pd.NaT:
                return None
            if isinstance(value, decimal.Decimal):
                return str(value)
            if isinstance(value, (dt.datetime, dt.date, dt.time)):
                return value.isoformat()
            if isinstance(value, bytes):
                return value.hex()
            if isinstance(value, int) and not isinstance(value, bool) and abs(value) > (1 << 53) - 1:
                return str(value)
            if isinstance(value, float):
                import math
                if not math.isfinite(value):
                    return 'NaN' if math.isnan(value) else 'Infinity' if value > 0 else '-Infinity'
            return value
        rows = [[json_value(value) for value in row] for row in frame.itertuples(index=False, name=None)]
        return {"name": table_name, "columns": list(frame.columns), "rows": rows, "total_rows": schema["total_rows"]}

    def writable_conn(self):
        raise ImportFailure("immutable_dataset", "Use derive_snapshot with an explicit SELECT and budget; whole-database memory shadows are disabled")

    def create_analysis_table(self, sql, table_name="analysis_data", _df=None):
        return "immutable_dataset: create a budgeted derived snapshot with derive_snapshot(sql, catalog=...)"

    def derive_snapshot(self, sql, *, catalog, budget=None, idempotency_key="", name="derived", progress=None, cancel=None):
        """A planned SELECT creates a new typed disk snapshot; source stays intact."""
        from data.dataset_manifest import validate_manifest
        validate_snapshot_query(sql, self.list_tables())
        limits = _budget(budget) if budget is not None else self.budget
        scope = {k: self.manifest[k] for k in ("owner_id", "session_id", "workspace_id")}
        if any(str(getattr(catalog, k, scope[k])) != str(scope[k]) for k in scope):
            raise ImportFailure("scope_mismatch", "Derived snapshot catalog must match the source scope")
        from data.import_schema import normalized_names
        name = normalized_names([name])[0]
        plan = {"version": 1, **scope, "source": {"kind": "derived_snapshot", "snapshot_id": self.manifest["snapshot_id"]},
                "sql": sql, "budget": limits.to_dict(), "name": name}
        plan["plan_hash"] = canonical_hash(plan)
        plan['budget'] = {k: min(v, self.budget.to_dict()[k]) for k, v in plan['budget'].items()}
        plan.update({'format': 'derived', 'handle': self.handle.to_dict(), 'options': {'idempotency_key': idempotency_key}})
        plan['plan_hash'] = canonical_hash({k: v for k, v in plan.items() if k != 'plan_hash'})
        from data.import_pipeline import run_import
        return run_import(plan, catalog=catalog, progress=progress, cancel=cancel)

    def explain_query(self, sql, *, analyze=False, budget=None, cancel=None):
        batches = list(self._execute_batches(sql, budget=budget, cancel=cancel, explain='analyze' if analyze else 'plan'))
        value = batches[0].column(1)[0].as_py()
        if analyze:
            profile = json.loads(value)
            self.last_scan.update({'profile_source': 'duckdb_explain_analyze',
                                   'actual_total_bytes_read': profile.get('total_bytes_read'),
                                   'engine_reported_rows_scanned': profile.get('cumulative_rows_scanned')})
        return value

    def interrupt(self):
        if not self._closed:
            self._conn.interrupt()

    def close(self):
        if not self._closed:
            self.interrupt()
            with self._lock:
                self._conn.close()
                self._closed = True
                self._temp.cleanup()


def _materialize_derived(plan, stage, operation):
    """Worker-side SELECT materialization using only a fixed input snapshot."""
    from data.dataset_manifest import validate_manifest
    source = DatasetDataSource(plan['handle'], budget=plan['budget'])
    source._supervised = True  # Parent's reservation spans this worker's real exit.
    writer = SnapshotWriter(stage, budget=_budget(plan['budget']))
    name = plan['name']
    try:
        for batch in source.execute_query_batches(plan['sql']):
            if not writer.tables:
                writer.add_table(name, batch.schema)
            writer.write(name, batch)
        if not writer.tables:
            writer.add_table(name, source._result_schema)
        files = writer.finish()
        manifest = {'version': 1, **{k: plan[k] for k in ('owner_id', 'session_id', 'workspace_id')},
                    'dataset_id': operation['dataset_id'], 'snapshot_id': operation['snapshot_id'],
                    'operation_id': operation['operation_id'], 'operation_fence': operation['fence'],
                    'status': 'ready', 'schema': writer.tables[name]['schema'], 'tables': list(writer.tables.values()),
                    'source': plan['source'], 'row_count': writer.row_count, 'files': files,
                    'lineage': {'parent_dataset_id': source.handle.dataset_id, 'parent_snapshot_id': source.handle.snapshot_id,
                                'sql_hash': canonical_hash(plan['sql'])},
                    'schema_policy': {'version': 1, 'conversion': 'exact_arrow'},
                    'created_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        return validate_manifest(manifest, root=stage, verify_files=True)
    finally:
        writer.close()
        source.close()
