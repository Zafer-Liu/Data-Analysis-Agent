"""ImportPlan -> bounded Arrow batches -> immutable Parquet -> catalog.publish.

Only backend-authorized local paths belong here; HTTP authorization stays in API.
No large source is materialized as pandas or copied into an in-memory database.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import os
import shutil
import time
import zipfile
from pathlib import Path

from .dataset_snapshot import SnapshotWriter, canonical_hash, check_cancel, file_fingerprint, file_sha256
from .import_schema import ImportFailure, convert_batch, freeze_schema, normalized_names, to_arrow_schema


_ALLOWED_OPTIONS = {"format", "encoding", "delimiter", "schema_policy", "infer_types", "sample_rows", "sample_bytes", "batch_rows",
                    "max_cell_bytes", "max_row_bytes", "max_decompressed_bytes", "shard_bytes", "partition_by", "max_partitions",
                    "table_name", "dataset_id", "idempotency_key", "lineage", "max_columns", "max_xlsx_bytes", "max_xml_bytes",
                    "max_shared_strings_bytes", "max_sheets", "max_cells", "sheets", "header_row", "formulas", "expected_version"}


def _budget(value):
    from infrastructure.resource_budget import BudgetSpec
    return value if isinstance(value, BudgetSpec) else BudgetSpec.from_dict(value)


def _limits(options, budget):
    limits = {"batch_rows": 65536, "sample_rows": 1024, "sample_bytes": min(budget.batch_bytes, 256 * 1024),
              "max_cell_bytes": min(budget.batch_bytes, 1024 * 1024), "max_row_bytes": budget.batch_bytes,
              "max_decompressed_bytes": budget.max_input_bytes, "max_columns": 4096,
              "max_xlsx_bytes": min(budget.max_input_bytes, 50 * 1024**2), "max_xml_bytes": min(budget.max_input_bytes, 512 * 1024**2),
              "max_shared_strings_bytes": min(budget.batch_bytes, 8 * 1024**2), "max_sheets": 32, "max_cells": 5_000_000}
    for key in limits:
        limits[key] = int(options.get(key, limits[key]))
        if limits[key] <= 0:
            raise ImportFailure("invalid_options", f"{key} must be positive")
    if limits["sample_rows"] > 10000 or limits["sample_bytes"] > budget.batch_bytes or limits["max_row_bytes"] > budget.batch_bytes:
        raise ImportFailure("invalid_options", "Sampling and row limits must fit batch_bytes (sample_rows <= 10000)")
    if limits["max_cell_bytes"] > limits["max_row_bytes"] or limits["max_columns"] > 16384 or limits["max_sheets"] > 256:
        raise ImportFailure("invalid_options", "Cell, column or sheet limits are too large")
    return limits


def _format(path, options):
    value = str(options.get("format", "")).lower()
    if not value:
        lower = path.name.lower()
        value = "csv.gz" if lower.endswith(".csv.gz") else path.suffix.lower().lstrip(".")
    if value not in ("csv", "csv.gz", "parquet", "xlsx"):
        raise ImportFailure("unsupported_format", "Supported streaming formats: csv, csv.gz, parquet, xlsx; convert large XLS first")
    return value


class _BoundedInput(io.RawIOBase):
    """Keep decompressed bytes and reads bounded, including during schema sampling."""
    def __init__(self, source, maximum, *, cancel=None, digest=None):
        self.source, self.maximum, self.cancel, self.digest = source, maximum, cancel, digest
        self.bytes_read = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        check_cancel(self.cancel)
        data = self.source.read(min(len(buffer), max(1, self.maximum - self.bytes_read + 1)))
        self.bytes_read += len(data)
        if self.bytes_read > self.maximum:
            raise ImportFailure("decompressed_limit", "Input exceeds decompressed byte limit")
        if self.digest is not None:
            self.digest.update(data)
        buffer[:len(data)] = data
        return len(data)

    def close(self):
        if not self.closed:
            self.source.close()
        super().close()


def _csv_records(path, fmt, options, limits, *, cancel=None):
    """Bound lexical record bytes before creating Arrow arrays or Python fields.

    The standard CSV parser preserves text exactly. Arrow performs the frozen
    typed conversions; no native inference is allowed to erase leading zeros.
    """
    import csv
    delimiter = options.get('delimiter', ',')
    encoding = options.get('encoding', 'utf-8-sig')
    if not isinstance(delimiter, str) or len(delimiter) != 1 or ord(delimiter) > 127:
        raise ImportFailure('invalid_options', 'CSV delimiter must be one ASCII character')
    if encoding not in ('utf-8', 'utf-8-sig', 'latin-1', 'cp1252'):
        raise ImportFailure('invalid_options', 'Unsupported streaming text encoding')
    raw = gzip.open(path, 'rb') if fmt == 'csv.gz' else Path(path).open('rb')
    bounded = _BoundedInput(raw, limits['max_decompressed_bytes'], cancel=cancel)
    with io.TextIOWrapper(io.BufferedReader(bounded, buffer_size=65536), encoding=encoding, newline='') as text:
        class Lines:
            size = 0
            def __iter__(self):
                return self
            def __next__(self):
                check_cancel(cancel)
                line = text.readline(limits['max_row_bytes'] + 1)
                if not line:
                    raise StopIteration
                self.size += len(line.encode('utf-8'))
                if self.size > limits['max_row_bytes']:
                    raise ImportFailure('row_bytes_exceeded', 'CSV lexical record exceeds max_row_bytes')
                return line
        lines = Lines()
        # The parser's field limit is only a second guard. All parsing processes
        # are isolated, and the byte bound is checked before the parser sees text.
        csv.field_size_limit(max(csv.field_size_limit(), limits['max_cell_bytes']))
        reader = csv.reader(lines, delimiter=delimiter, strict=True)
        try:
            for row in reader:
                check_cancel(cancel)
                lines.size = 0
                if len(row) > limits['max_columns']:
                    raise ImportFailure('column_limit', 'CSV record has too many fields')
                if any(len(value.encode('utf-8')) > limits['max_cell_bytes'] for value in row):
                    raise ImportFailure('cell_limit', 'CSV field exceeds max_cell_bytes')
                yield row
        except csv.Error as exc:
            # An unterminated quoted record has no safe resynchronization point.
            raise ImportFailure('corrupt_row', str(exc)) from exc


def _xlsx_metadata(path, limits, *, cancel=None):
    if path.stat().st_size > limits["max_xlsx_bytes"]:
        raise ImportFailure("xlsx_compressed_limit", "Workbook exceeds compressed XLSX limit; convert to CSV/Parquet")
    total = 0
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 4096:
            raise ImportFailure("xlsx_member_limit", "Too many workbook archive entries")
        declared = sum(i.file_size for i in entries)
        if declared > limits["max_xml_bytes"]:
            raise ImportFailure("xlsx_decompressed_limit", "Workbook XML exceeds decompressed limit")
        for info in entries:
            if info.filename.endswith("sharedStrings.xml") and info.file_size > limits["max_shared_strings_bytes"]:
                raise ImportFailure("xlsx_shared_strings_limit", "Shared strings exceed the bounded workbook allowance")
            previous = b''
            with archive.open(info) as stream:
                while True:
                    check_cancel(cancel)
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    probe = (previous + chunk).upper()
                    if b'<!DOCTYPE' in probe or b'<!ENTITY' in probe:
                        raise ImportFailure('xlsx_xml', 'DTD and entity declarations are forbidden')
                    previous = probe[-16:]
                    if total > limits["max_xml_bytes"]:
                        raise ImportFailure("xlsx_decompressed_limit", "Actual decompressed workbook bytes exceed limit")
        # Reject malicious cell coordinates before openpyxl can allocate a row
        # using an attacker-controlled maximum column index.
        import re
        import xml.etree.ElementTree as ET
        cell_count = 0
        for info in entries:
            if not info.filename.startswith('xl/worksheets/') or not info.filename.endswith('.xml'):
                continue
            with archive.open(info) as stream:
                stack = []
                for event, node in ET.iterparse(stream, events=('start', 'end')):
                    check_cancel(cancel)
                    if event == 'start':
                        stack.append(node)
                        continue
                    tag = node.tag.rsplit('}', 1)[-1]
                    if tag == 'f':
                        raise ImportFailure('xlsx_formula', 'Formula cells require an exported value-only workbook')
                    if tag == 'c':
                        if node.get('t') == 'e':
                            raise ImportFailure('xlsx_cell_error', 'Workbook contains an error cell')
                        cell_count += 1
                        reference = re.fullmatch(r'([A-Z]{1,3})([0-9]{1,7})', node.get('r', ''))
                        if not reference:
                            raise ImportFailure('xlsx_coordinate', 'Invalid workbook cell coordinate')
                        column = 0
                        for character in reference[1]:
                            column = column * 26 + ord(character) - 64
                        if column > limits['max_columns'] or not 1 <= int(reference[2]) <= 1048576:
                            raise ImportFailure('column_limit', 'Workbook coordinates exceed supported dimensions')
                        if cell_count > limits['max_cells']:
                            raise ImportFailure('xlsx_cells_limit', 'Workbook exceeds max_cells')
                    if tag == 't' and len((node.text or '').encode('utf-8')) > limits['max_cell_bytes']:
                        raise ImportFailure('cell_limit', 'Workbook text exceeds max_cell_bytes')
                    node.clear()
                    stack.pop()
                    if stack:
                        stack[-1].remove(node)
    return total


def _xlsx_schemas(path, options, limits, *, cancel=None):
    import openpyxl
    import pyarrow as pa
    decompressed = _xlsx_metadata(path, limits, cancel=cancel)
    if options.get("formulas", "reject") != "reject":
        raise ImportFailure("unsupported_formula_policy", "Streaming XLSX currently requires formulas=reject; export cached values first")
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        selected = options.get("sheets", workbook.sheetnames)
        if not isinstance(selected, list) or not selected or len(workbook.sheetnames) > limits["max_sheets"] or len(selected) > limits["max_sheets"]:
            raise ImportFailure("xlsx_sheet_limit", "Workbook sheet count exceeds the frozen limit")
        if len(set(selected)) != len(selected):
            raise ImportFailure("invalid_options", "Sheet selection contains duplicates")
        names = normalized_names(selected)
        result = []
        for sheet_name, table_name in zip(selected, names):
            check_cancel(cancel)
            if sheet_name not in workbook.sheetnames:
                raise ImportFailure("invalid_options", f"Unknown sheet: {sheet_name}")
            sheet = workbook[sheet_name]
            sheet.reset_dimensions()
            rows = sheet.iter_rows(values_only=False)
            header_index = int(options.get("header_row", 0))
            if not 0 <= header_index < 1000:
                raise ImportFailure("invalid_options", "header_row must be between 0 and 999")
            header = None
            for _ in range(header_index + 1):
                header = next(rows, None)
            if not header:
                continue
            if len(header) > limits["max_columns"]:
                raise ImportFailure("column_limit", "Workbook has too many columns")
            original = [str(c.value) if c.value is not None else f"column_{i + 1}" for i, c in enumerate(header)]
            sample, sample_size = [], 0
            for row in rows:
                check_cancel(cancel)
                _check_xlsx_row(row, limits)
                values = [c.value for c in row]
                sample_size += sum(len(str(v).encode("utf-8")) for v in values if v is not None)
                if sample_size > limits["sample_bytes"]:
                    break
                sample.append(values)
                if len(sample) >= limits["sample_rows"]:
                    break
            requested = options.get("schema_policy", {})
            requested = requested.get("tables", {}).get(sheet_name, requested)
            # Cell types, not numeric value ranges, determine Excel dates.
            native = []
            samples = {name: [r[i] if i < len(r) else None for r in sample] for i, name in enumerate(original)}
            for raw in original:
                values = [v for v in samples[raw] if v is not None]
                if values and all(isinstance(v, dt.datetime) for v in values):
                    dtype = pa.timestamp("us")
                elif values and all(isinstance(v, dt.date) and not isinstance(v, dt.datetime) for v in values):
                    dtype = pa.date32()
                else:
                    dtype = None
                native.append(dtype)
            policy = freeze_schema(original, requested=requested, samples=samples, infer=bool(options.get("infer_types", True)))
            for index, dtype in enumerate(native):
                explicit = requested.get("columns", [])
                explicit_names = set(explicit) if isinstance(explicit, dict) else {c.get("original_name", c.get("name")) for c in explicit}
                if dtype is not None and original[index] not in explicit_names and policy["columns"][index]["name"] not in explicit_names:
                    from .import_schema import type_name
                    policy["columns"][index]["type"] = type_name(dtype)
                    policy['columns'][index]['time_unit'] = getattr(dtype, 'unit', None)
            result.append({"name": options.get("table_name", table_name) if len(selected) == 1 else table_name,
                           "source_sheet": sheet_name, "schema_policy": policy, "header_row": header_index})
        if not result:
            raise ImportFailure("invalid_input", "Workbook has no usable header")
        return result, decompressed
    finally:
        workbook.close()


def _check_xlsx_row(row, limits):
    if len(row) > limits["max_columns"]:
        raise ImportFailure("column_limit", "XLSX row exceeds column limit")
    row_bytes = 0
    for cell in row:
        if cell.data_type == "f":
            raise ImportFailure("xlsx_formula", "Formula cells require an explicitly exported value-only workbook")
        if cell.data_type == "e":
            raise ImportFailure("xlsx_cell_error", "Workbook contains an error cell")
        size = len(str(cell.value).encode("utf-8")) if cell.value is not None else 0
        if size > limits["max_cell_bytes"]:
            raise ImportFailure("cell_limit", "XLSX cell exceeds max_cell_bytes")
        row_bytes += size
    if row_bytes > limits["max_row_bytes"]:
        raise ImportFailure("row_bytes_exceeded", "XLSX row exceeds max_row_bytes")
    return row_bytes


def _create_import_plan_local(input_path, *, owner_id, session_id, workspace_id="", options=None, budget=None):
    import pyarrow as pa
    options = dict(options or {})
    unknown = set(options) - _ALLOWED_OPTIONS
    if unknown:
        raise ImportFailure("invalid_options", f"Unsupported import options: {sorted(unknown)}")
    limits_budget = _budget(budget)
    path = Path(input_path).absolute()
    before = file_fingerprint(path)
    limits_budget.check_usage(input_bytes=before["size_bytes"])
    fmt, limits = _format(path, options), _limits(options, limits_budget)
    table_name = normalized_names([options.get("table_name") or path.name.removesuffix(".gz").rsplit(".", 1)[0]])[0]
    if 'table_name' in options:
        options['table_name'] = table_name
    if fmt in ("csv", "csv.gz"):
        records = _csv_records(path, fmt, options, limits)
        try:
            names = next(records, None)
            if not names:
                raise ImportFailure('invalid_input', 'CSV requires a header')
            samples = {name: [] for name in names}
            sample_bytes = sample_rows = skipped = 0
            for row in records:
                if len(row) != len(names):
                    skipped += 1
                    requested = options.get('schema_policy', {})
                    if requested.get('bad_rows') != 'skip' or skipped > requested.get('max_bad_rows', 0):
                        raise ImportFailure('corrupt_row', 'CSV record width differs from header')
                    continue
                size = sum(len(v.encode('utf-8')) + 16 for v in row)
                if sample_bytes + size > limits['sample_bytes']:
                    break
                for name, value in zip(names, row):
                    samples[name].append(value)
                sample_bytes += size
                sample_rows += 1
                if sample_rows >= limits['sample_rows']:
                    break
        finally:
            records.close()
        policy = freeze_schema(names, requested=options.get("schema_policy"), samples=samples, infer=bool(options.get("infer_types", True)))
        tables = [{"name": table_name, "schema_policy": policy}]
        decompressed = None
    elif fmt == "parquet":
        import pyarrow.parquet as pq
        with pq.ParquetFile(path, thrift_string_size_limit=16 << 20, thrift_container_size_limit=1000000) as parquet:
            schema = parquet.schema_arrow
            if len(schema) > limits["max_columns"]:
                raise ImportFailure("column_limit", "Parquet has too many columns")
            limits_budget.check_usage(rows=parquet.metadata.num_rows)
            if sum(parquet.metadata.row_group(i).total_byte_size for i in range(parquet.metadata.num_row_groups)) > limits['max_decompressed_bytes']:
                raise ImportFailure('decompressed_limit', 'Parquet decoded row groups exceed max_decompressed_bytes')
            policy = freeze_schema(schema.names, requested=options.get("schema_policy"), native_schema=schema)
        tables = [{"name": table_name, "schema_policy": policy}]
        decompressed = None
    else:
        tables, decompressed = _xlsx_schemas(path, options, limits)
    if file_fingerprint(path) != before:
        raise ImportFailure("source_changed", "Input changed during schema planning")
    # The planned identity binds all bytes. Workers copy this authorized source
    # once into a private input file and verify it before parsing any rows.
    from .dataset_manifest import sha256_file, validate_scope
    fingerprint = {**before, 'sha256': sha256_file(path)}
    if file_fingerprint(path) != before:
        raise ImportFailure('dataset_changed', 'Input changed while hashing the plan')
    validate_scope(owner_id, 'owner_id')
    validate_scope(session_id, 'session_id', allow_empty=True)
    validate_scope(workspace_id, 'workspace_id', allow_empty=True)
    plan = {"version": 1, "format": fmt, "input_path": str(path), "owner_id": str(owner_id), "session_id": str(session_id),
            "workspace_id": str(workspace_id), "fingerprint": fingerprint,
            "source": {"kind": "local_file", "format": fmt, "name": path.name, "fingerprint": fingerprint, "materialized": False},
            "schema_policy": tables[0]["schema_policy"] if len(tables) == 1 else {"version": 1, "tables": {t["name"]: t["schema_policy"] for t in tables}},
            "tables": tables, "options": options, "limits": limits, "budget": limits_budget.to_dict(), "decompressed_bytes": decompressed}
    plan["plan_hash"] = canonical_hash(plan)
    return plan


def _check_batch(batch, limits):
    import pyarrow as pa
    import pyarrow.compute as pc
    if batch.num_columns > limits["max_columns"]:
        raise ImportFailure("column_limit", "Batch exceeds max_columns")
    row_sizes = None
    fixed_bytes = 0
    for column in batch.columns:
        if not (pa.types.is_string(column.type) or pa.types.is_large_string(column.type) or pa.types.is_binary(column.type) or pa.types.is_large_binary(column.type) or pa.types.is_null(column.type)):
            try:
                width = max(1, column.type.bit_width // 8)
            except (ValueError, AttributeError):
                raise ImportFailure('unsupported_type', 'Nested Parquet fields require an explicit flattening plan')
            if width > limits['max_cell_bytes']:
                raise ImportFailure('cell_limit', 'Fixed-width field exceeds max_cell_bytes')
            fixed_bytes += width
        if pa.types.is_string(column.type) or pa.types.is_large_string(column.type) or pa.types.is_binary(column.type) or pa.types.is_large_binary(column.type):
            lengths = pc.fill_null(pc.binary_length(column), 0)
            maximum = pc.max(lengths).as_py() or 0
            if maximum > limits["max_cell_bytes"]:
                raise ImportFailure("cell_limit", "Input cell exceeds max_cell_bytes")
            row_sizes = lengths if row_sizes is None else pc.add(row_sizes, lengths)
    maximum_row = fixed_bytes + ((pc.max(row_sizes).as_py() or 0) if row_sizes is not None else 0)
    if maximum_row > limits["max_row_bytes"]:
        raise ImportFailure("row_bytes_exceeded", "Input row exceeds max_row_bytes")
    return maximum_row


def _batches(plan, *, reject_row, cancel):
    import pyarrow as pa
    path, limits, options = Path(plan["input_path"]), plan["limits"], plan["options"]
    reject_original = reject_row
    def reject_row(code, message):
        return reject_original(code, message, policy=table['schema_policy'], table_name=table['name'])
    if plan["format"] in ("csv", "csv.gz"):
        table = plan["tables"][0]
        names = [c["original_name"] for c in table["schema_policy"]["columns"]]
        records = _csv_records(path, plan['format'], options, limits, cancel=cancel)
        columns, byte_count = [[] for _ in names], 0
        def converted():
            batch = pa.RecordBatch.from_arrays([pa.array(v, type=pa.string()) for v in columns], names=names)
            return convert_batch(batch, table['schema_policy'], reject_row=reject_row)
        try:
            if next(records, None) != names:
                raise ImportFailure('dataset_changed', 'CSV headers changed since planning')
            for row in records:
                if len(row) != len(names):
                    reject_row('corrupt_row', 'CSV record width differs from header')
                    continue
                size = sum(len(v.encode('utf-8')) + 16 for v in row)
                if columns[0] and (byte_count + size > plan['budget']['batch_bytes'] // 2 or len(columns[0]) >= limits['batch_rows']):
                    yield table, converted()
                    columns, byte_count = [[] for _ in names], 0
                for values, value in zip(columns, row):
                    values.append(value)
                byte_count += size
            if columns[0]:
                yield table, converted()
        finally:
            records.close()
    elif plan["format"] == "parquet":
        import pyarrow.parquet as pq
        table = plan["tables"][0]
        with pq.ParquetFile(path, thrift_string_size_limit=16 << 20, thrift_container_size_limit=1000000) as parquet:
            # Metadata gives a conservative first batch estimate; reject a
            # decoded batch over budget instead of pretending it never allocated.
            max_width = max((parquet.metadata.row_group(i).total_byte_size / max(1, parquet.metadata.row_group(i).num_rows) for i in range(parquet.metadata.num_row_groups)), default=64)
            batch_rows = max(1, min(limits["batch_rows"], int(plan["budget"]["batch_bytes"] / max(1, max_width) / 2)))
            decoded_bytes = 0
            for batch in parquet.iter_batches(batch_size=batch_rows, use_threads=False):
                check_cancel(cancel)
                decoded_bytes += batch.nbytes
                if decoded_bytes > limits['max_decompressed_bytes']:
                    raise ImportFailure('decompressed_limit', 'Actual Parquet batches exceed max_decompressed_bytes')
                _check_batch(batch, limits)
                if batch.nbytes > plan["budget"]["batch_bytes"]:
                    raise ImportFailure("batch_bytes_exceeded", "Decoded Parquet batch exceeds batch_bytes")
                yield table, convert_batch(batch, table["schema_policy"], reject_row=reject_row)
    else:
        import openpyxl
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
        cells = 0
        try:
            for table in plan["tables"]:
                policy = table["schema_policy"]
                schema = to_arrow_schema(policy)
                columns, byte_count = [[] for _ in schema], 0
                sheet = workbook[table['source_sheet']]
                sheet.reset_dimensions()
                for row in sheet.iter_rows(min_row=table['header_row'] + 2, values_only=False):
                    check_cancel(cancel)
                    size = _check_xlsx_row(row, limits)
                    cells += len(row)
                    if cells > limits["max_cells"]:
                        raise ImportFailure("xlsx_cells_limit", "Workbook exceeds max_cells")
                    if not any(c.value is not None for c in row):
                        continue
                    if len(row) > len(schema):
                        reject_row("corrupt_row", "Workbook row width differs from header")
                        continue
                    if columns[0] and (byte_count + size + len(row) * 16 > plan["budget"]["batch_bytes"] // 2 or len(columns[0]) >= limits["batch_rows"]):
                        yield table, _xlsx_batch(columns, policy, reject_row)
                        columns, byte_count = [[] for _ in schema], 0
                    for index, values in enumerate(columns):
                        values.append(row[index].value if index < len(row) else None)
                    byte_count += size + len(row) * 16
                if columns[0]:
                    yield table, _xlsx_batch(columns, policy, reject_row)
        finally:
            workbook.close()


def _xlsx_batch(columns, policy, reject_row):
    import pyarrow as pa
    from .import_schema import scalar_value
    schema = to_arrow_schema(policy)
    valid = [[] for _ in columns]
    for row in zip(*columns):
        try:
            values = [scalar_value(value, column) for value, column in zip(row, policy["columns"])]
            for value, field in zip(values, schema):
                pa.scalar(value, type=field.type)
        except (ValueError, TypeError, ArithmeticError, pa.ArrowException) as exc:
            reject_row("schema_drift", str(exc))
            continue
        for dest, value in zip(valid, values):
            dest.append(value)
    return pa.RecordBatch.from_arrays([pa.array(values, type=field.type) for values, field in zip(valid, schema)], schema=schema)


def _materialize_import(plan, stage, operation, *, progress=None, cancel=None):
    from .dataset_manifest import validate_manifest
    plan = dict(plan)
    if canonical_hash({k: v for k, v in plan.items() if k != "plan_hash"}) != plan.get("plan_hash"):
        raise ImportFailure("plan_changed", "Import plan hash no longer matches its frozen fields")
    budget = _budget(plan["budget"])
    check_cancel(cancel)
    operation_id = operation['operation_id']
    stage = Path(stage)
    writer = None
    committed = False
    started = time.monotonic()
    metrics = {"bad_rows": 0, 'bad_rows_by_table': {}, "bad_row_samples": [], "read_batches": 0, "peak_batch_bytes": 0,
               "queue_batches": 1, "reader_threads": 1}
    policies = [t["schema_policy"] for t in plan["tables"]]
    active_policy = policies[0]

    def reject_row(code, message, *, policy=None, table_name='data'):
        policy = policy or active_policy
        metrics["bad_rows"] += 1
        counts = metrics['bad_rows_by_table']
        counts[table_name] = counts.get(table_name, 0) + 1
        budget.check_usage(rows=metrics['bad_rows'] + (writer.row_count if writer else 0))
        if len(metrics["bad_row_samples"]) < 20:
            metrics["bad_row_samples"].append({"code": code, "message": str(message)[:240]})
        if policy.get("bad_rows") != "skip" or counts[table_name] > policy.get("max_bad_rows", 0):
            raise ImportFailure(code, str(message)[:240], bad_rows=metrics["bad_rows"])

    try:
        stage.mkdir(parents=True, exist_ok=False)
        expected = {k: v for k, v in plan['fingerprint'].items() if k != 'sha256'}
        path = Path(plan['input_path'])
        if file_fingerprint(path) != expected:
            raise ImportFailure('dataset_changed', 'Source metadata changed since planning')
        # A private, verified copy prevents a producer's mutable reference from
        # changing between sampling, batches or repeated parser passes.
        copied = stage.parent.parent / 'operations' / (operation_id + '.work') / ('input.' + plan['format'])
        digest, count = hashlib.sha256(), 0
        with path.open('rb') as source, copied.open('xb') as output:
            while True:
                check_cancel(cancel)
                budget.check_usage(run_seconds=time.monotonic() - started)
                block = source.read(min(1 << 20, budget.batch_bytes))
                if not block:
                    break
                count += len(block)
                budget.check_usage(input_bytes=count)
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        before_hash = digest.hexdigest()
        if before_hash != plan['fingerprint']['sha256'] or file_fingerprint(path) != expected:
            raise ImportFailure('dataset_changed', 'Source bytes changed since planning')
        if progress:
            progress(3, "已锁定 schema，正在分批物化不可变快照")
        writer = SnapshotWriter(stage, budget=budget, options=plan["options"], cancel=cancel)
        for table in plan["tables"]:
            writer.add_table(table["name"], to_arrow_schema(table["schema_policy"]), **({"source_sheet": table["source_sheet"]} if "source_sheet" in table else {}))
        for table, batch in _batches({**plan, 'input_path': str(copied)}, reject_row=reject_row, cancel=cancel):
            active_policy = table["schema_policy"]
            check_cancel(cancel)
            metrics["read_batches"] += 1
            metrics["peak_batch_bytes"] = max(metrics["peak_batch_bytes"], batch.nbytes)
            budget.check_usage(rows=writer.row_count + batch.num_rows + metrics['bad_rows'])
            writer.write(table["name"], batch)
            if progress:
                progress(min(90, 5 + metrics["read_batches"]), f"已写入 {writer.row_count:,} 行；坏行 {metrics['bad_rows']}")
        files = writer.finish()
        check_cancel(cancel)
        after_hash = file_sha256(copied, cancel=cancel)
        if before_hash != after_hash:
            raise ImportFailure('dataset_changed', 'Private input changed while materializing')
        tables = list(writer.tables.values())
        metrics.update({"elapsed_seconds": time.monotonic() - started, "input_bytes": expected["size_bytes"], "output_bytes": writer.total_bytes})
        manifest = {"version": 1, "dataset_id": operation["dataset_id"], "snapshot_id": operation["snapshot_id"],
                    "operation_id": operation_id, 'operation_fence': operation['fence'], "owner_id": plan["owner_id"], "session_id": plan["session_id"], "workspace_id": plan["workspace_id"],
                    "status": "ready", "schema": tables[0]["schema"], "tables": tables,
                    "source": {**plan["source"], "sha256": before_hash, "materialized": True},
                    "row_count": writer.row_count, "files": files, "lineage": plan["options"].get("lineage", {}),
                    "schema_policy": plan["schema_policy"], "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "import_metrics": metrics, 'import_limits': plan['limits']}
        manifest["content_hash"] = canonical_hash({"schema": manifest["schema"], "tables": tables,
                                                    "files": [{k: v for k, v in f.items() if k != "relative_path"} for f in files],
                                                    "source_sha256": before_hash, "schema_policy": manifest["schema_policy"]})
        validate_manifest(manifest, root=stage, verify_files=True)
        check_cancel(cancel)
        committed = True  # Completed materialization; publication remains in the supervisor.
        return manifest
    finally:
        if writer is not None:
            writer.close()
        if not committed and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _worker_main(request_path):
    """Fixed trusted local entry point. JSON input contains no executable code."""
    import json
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    result_path = Path(request['result_path'])
    try:
        if request.get('mode') == 'plan':
            plan = _create_import_plan_local(**request['arguments'])
            from .dataset_manifest import canonical_json
            if len(canonical_json(plan).encode('utf-8')) > 16 << 20:
                raise ImportFailure('invalid_plan', 'Import plan exceeds 16 MiB')
            from .dataset_catalog import _sync_json
            _sync_json(result_path, {'ok': True, 'plan': plan})
            return
        if request['plan'].get('format') == 'derived':
            from .sources.dataset import _materialize_derived
            manifest = _materialize_derived(request['plan'], request['stage'], request['operation'])
        else:
            manifest = _materialize_import(request['plan'], request['stage'], request['operation'])
        from .dataset_catalog import _sync_json
        _sync_json(result_path, {'ok': True, 'manifest': manifest})
    except BaseException as exc:
        from .dataset_catalog import _sync_json
        _sync_json(result_path, {'ok': False, 'code': getattr(exc, 'code', 'import_failed'),
                                 'error': str(exc)[:1000], 'details': getattr(exc, 'details', {})})
        raise SystemExit(1)


def _worker_command(request_path, code_root):
    """Fixed source/frozen entry; a frozen executable is not a Python CLI."""
    import sys
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--baa-dataset-worker', str(request_path)]
    return [sys.executable, '-I', '-B', '-c',
            'import sys;sys.path.insert(0,sys.argv[1]);from data.import_pipeline import _worker_main;_worker_main(sys.argv[2])',
            str(code_root), str(request_path)]


def create_import_plan(input_path, *, owner_id, session_id, workspace_id='', options=None, budget=None):
    """Plan schema/hashes in the same supervised worker used for imports.

    Planning can parse bounded XLSX XML/shared strings and whole-file hashes;
    those costs must also be subject to RSS and deadline monitoring.
    """
    import json
    import sys
    import uuid
    from infrastructure.paths import content_data_path
    from infrastructure.resource_budget import ResourceCoordinator, run_supervised_worker
    from .dataset_catalog import _sync_json
    from .dataset_manifest import canonical_json
    limits = _budget(budget)
    options = dict(options or {})
    if set(options) - _ALLOWED_OPTIONS:
        raise ImportFailure('invalid_options', f'Unsupported import options: {sorted(set(options) - _ALLOWED_OPTIONS)}')
    path = Path(input_path).absolute()
    fingerprint = file_fingerprint(path)
    limits.check_usage(input_bytes=fingerprint['size_bytes'])
    arguments = {'input_path': str(path), 'owner_id': owner_id, 'session_id': session_id,
                 'workspace_id': workspace_id, 'options': options, 'budget': limits.to_dict()}
    if len(canonical_json(arguments).encode('utf-8')) > 65536:
        raise ImportFailure('invalid_options', 'Planning arguments exceed 64 KiB')
    execution_id = 'plan-' + uuid.uuid4().hex
    work = content_data_path('dataset-plans', execution_id)
    work.mkdir(parents=True)
    request, result = work / 'request.json', work / 'result.json'
    _sync_json(request, {'mode': 'plan', 'arguments': arguments, 'result_path': str(result)})
    coordinator = ResourceCoordinator()
    coordinator.register_account_path(work.parent)
    coordinator.register_account_path(path)
    code_root = str(Path(__file__).resolve().parents[1])
    command = _worker_command(request, code_root)
    # Planning materializes only control metadata, not another source copy.
    reservation = {**limits.to_dict(), 'max_input_bytes': 1, 'temp_bytes': 1,
                   'max_output_bytes': min(limits.max_output_bytes, 16 << 20), 'max_files': max(2, limits.max_files)}
    exit_confirmed = False
    try:
        try:
            outcome = run_supervised_worker(command, coordinator=coordinator, owner_id=owner_id,
                execution_id=execution_id, budget=reservation, cwd=code_root, output_dir=work)
            exit_confirmed = outcome['exit_confirmed']
        except BaseException as exc:
            exit_confirmed = bool(getattr(exc, 'exit_confirmed', False))
            if result.exists() and getattr(exc, 'code', '') == 'worker_failed':
                response = json.loads(result.read_text(encoding='utf-8'))
                raise ImportFailure(response.get('code', 'plan_failed'), response.get('error', 'Planning worker failed'), **response.get('details', {})) from exc
            raise
        if result.stat().st_size > 16 << 20:
            raise ImportFailure('invalid_plan', 'Planning response exceeds 16 MiB')
        response = json.loads(result.read_text(encoding='utf-8'))
        return response['plan']
    finally:
        if exit_confirmed:
            shutil.rmtree(work, ignore_errors=True)


def run_import(plan, *, catalog, progress=None, cancel=None):
    """Supervise bounded materialization, then publish after confirmed real exit."""
    run_started = time.monotonic()
    import json
    import sys
    from infrastructure.resource_budget import ResourceCoordinator, run_supervised_worker, ResourceError
    from .dataset_catalog import _sync_json
    if canonical_hash({k: v for k, v in plan.items() if k != 'plan_hash'}) != plan.get('plan_hash'):
        raise ImportFailure('plan_changed', 'Import plan hash no longer matches its frozen fields')
    if any(getattr(catalog, k) != plan[k] for k in ('owner_id', 'session_id', 'workspace_id')):
        raise ImportFailure('scope_mismatch', 'Catalog scope differs from the import plan')
    check_cancel(cancel)
    key = str(plan['options'].get('idempotency_key') or plan['plan_hash'])
    operation = catalog.begin_import(plan, idempotency_key=key)
    with catalog.claim_operation(operation['operation_id']) as operation:
        if operation['status'] == 'published':
            return catalog.get(operation['dataset_id'], snapshot_id=operation['snapshot_id'], verify=True).manifest
        stage = catalog.staging_dir(operation['operation_id'])
        work = catalog.root / 'operations' / (operation['operation_id'] + '.work')
        # A failed attempt may be retried only after claim_operation has proved
        # every previously observed worker exited. Never resume CSV byte offsets.
        if catalog.snapshot_dir(operation['snapshot_id']).exists():
            raise ImportFailure('snapshot_conflict', 'Recover the uncommitted publication before retrying')
        for path in (stage, work):
            if path.exists():
                from infrastructure.resource_budget import directory_usage
                directory_usage(path)
                shutil.rmtree(path)
        work.mkdir()
        spill = work / 'spill'
        spill.mkdir()
        request_path, result_path = work / 'request.json', work / 'result.json'
        _sync_json(request_path, {'plan': plan, 'stage': str(stage), 'operation': operation, 'result_path': str(result_path)})
        coordinator = ResourceCoordinator()
        coordinator.register_account_path(catalog.root)
        if plan.get('input_path'):
            coordinator.register_account_path(plan['input_path'])
        if plan.get('handle'):
            coordinator.register_account_path(plan['handle']['root'])
        module_root = str(Path(__file__).resolve().parents[1])
        command = _worker_command(request_path, module_root)
        committed, exit_confirmed = False, False
        budget = _budget(plan['budget'])
        try:
            if progress:
                progress(3, '正在分批物化不可变快照')
            try:
                result = run_supervised_worker(command, owner_id=plan['owner_id'],
                    execution_id=operation['operation_id'] + '-' + operation['fence'], budget=budget,
                    coordinator=coordinator, cwd=module_root, temp_dir=spill, output_dir=stage, cancel=cancel,
                    on_spawn=lambda identity: catalog.observe_worker(operation['operation_id'], operation['fence'], identity))
                exit_confirmed = result['exit_confirmed']
            except BaseException as exc:
                exit_confirmed = bool(getattr(exc, 'exit_confirmed', False))
                if result_path.exists() and getattr(exc, 'code', '') == 'worker_failed':
                    error = json.loads(result_path.read_text(encoding='utf-8'))
                    raise ImportFailure(error.get('code', 'import_failed'), error.get('error', 'Worker failed'), **error.get('details', {})) from exc
                raise
            if result_path.stat().st_size > 16 << 20:
                raise ImportFailure('invalid_manifest', 'Worker metadata exceeds 16 MiB')
            response = json.loads(result_path.read_text(encoding='utf-8'))
            if not response.get('ok'):
                raise ImportFailure(response.get('code', 'import_failed'), response.get('error', 'Worker failed'))
            manifest = response['manifest']
            manifest['worker_usage'] = result['usage']
            manifest['budget_enforcement'] = result['limits']
            check_cancel(cancel)
            if progress:
                progress(95, '正在原子发布数据集版本')
            check_cancel(cancel)
            budget.check_usage(run_seconds=time.monotonic() - run_started)
            from dataclasses import replace
            publication_budget = replace(budget, max_run_seconds=max(0.001, budget.max_run_seconds - (time.monotonic() - run_started)))
            published = catalog.publish(manifest, idempotency_key=key, expected_version=plan['options'].get('expected_version'),
                                        budget=publication_budget, cancel=cancel)
            committed = True
            if progress:
                try:
                    progress(100, f"已发布 {published['row_count']:,} 行的不可变数据集")
                except Exception:
                    pass
            return published
        finally:
            if exit_confirmed:
                if not committed and stage.exists():
                    shutil.rmtree(stage)
                shutil.rmtree(work, ignore_errors=True)


def import_file_source(input_path, *, owner_id="local-default", session_id="", workspace_id="", options=None, budget=None, catalog=None, progress=None, cancel=None):
    """Bridge for existing trusted CSV/Excel/workspace entry points."""
    from .dataset_catalog import DatasetCatalog
    from .sources.dataset import DatasetDataSource
    catalog = catalog or DatasetCatalog(owner_id=owner_id, session_id=session_id, workspace_id=workspace_id)
    plan = create_import_plan(input_path, owner_id=owner_id, session_id=session_id, workspace_id=workspace_id, options=options, budget=budget)
    manifest = run_import(plan, catalog=catalog, progress=progress, cancel=cancel)
    return DatasetDataSource(catalog.get(manifest["dataset_id"], snapshot_id=manifest["snapshot_id"]), budget=budget, name=Path(input_path).name)
