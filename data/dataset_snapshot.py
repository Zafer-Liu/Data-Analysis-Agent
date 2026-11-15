"""Materialize immutable Parquet shards; the shared catalog owns publication."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from .import_schema import ImportFailure, schema_fields


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def file_sha256(path, *, cancel=None, chunk_bytes=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while True:
            check_cancel(cancel)
            chunk = source.read(chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def check_cancel(cancel):
    if cancel is not None and cancel():
        raise ImportFailure("canceled", "Operation canceled before publication")


def file_fingerprint(path):
    path = Path(path)
    from .dataset_manifest import _ordinary, safe_snapshot_root
    safe_snapshot_root(path.absolute().parent)
    stat = _ordinary(path.absolute())
    return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
            "device": stat.st_dev, "inode": stat.st_ino}


class SnapshotWriter:
    """One synchronous producer/consumer: no unbounded prefetch or batch queue.

    Every write is an exact Arrow batch, every shard is closed before hashing.
    Partition fanout, open writers, files and physical output have explicit caps.
    """
    def __init__(self, root, *, budget, options=None, cancel=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget, self.options, self.cancel = budget, dict(options or {}), cancel
        from .import_pipeline import _limits
        self.limits = _limits(self.options, budget)
        self.batch_bytes = int(budget.batch_bytes)
        self.target_bytes = min(int(self.options.get("shard_bytes", 128 * 1024**2)), int(budget.max_output_bytes))
        if self.target_bytes <= 0:
            raise ImportFailure("invalid_options", "shard_bytes must be positive")
        self.partition_by = list(self.options.get("partition_by", []))
        self.max_partitions = int(self.options.get("max_partitions", 64))
        if not 1 <= self.max_partitions <= 128 or len(self.partition_by) > 3:
            raise ImportFailure("partition_limit", "At most 128 partitions and 3 partition keys are supported")
        self.files, self.tables, self._writers, self._partition_keys = [], {}, {}, set()
        self.row_count, self.total_bytes, self._sequence = 0, 0, 0
        self.started = time.monotonic()

    def _check(self):
        check_cancel(self.cancel)
        self.budget.check_usage(run_seconds=time.monotonic() - self.started)
        self.budget.check_usage(output_bytes=self.total_bytes, rows=self.row_count, files=len(self.files) + len(self._writers))
        # RSS is monitored, not an OS allocation ceiling. Read-side block limits
        # and the coordinator remain necessary in addition to this check.
        try:
            import psutil
            self.budget.check_usage(rss_bytes=psutil.Process().memory_info().rss)
        except ImportError:
            pass

    def _new_writer(self, table, schema, partition):
        import pyarrow.parquet as pq
        self._sequence += 1
        relative = f"shards/{table}/"
        if partition:
            relative += "p-" + canonical_hash(partition)[:20] + "/"
        relative += f"part-{self._sequence:06d}.parquet"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(self.files) + len(self._writers) + 1 > self.budget.max_files:
            raise ImportFailure("file_limit", "Snapshot shard count exceeds max_files")
        writer = pq.ParquetWriter(str(path), schema, compression="zstd", use_dictionary=True, write_statistics=True)
        return {"writer": writer, "path": path, "relative_path": relative, "rows": 0, "arrow_bytes": 0,
                "table": table, "partition": partition}

    def add_table(self, name, schema, **metadata):
        if name in self.tables:
            raise ImportFailure("invalid_schema", f"Duplicate table: {name}")
        missing = set(self.partition_by) - set(schema.names)
        if missing:
            raise ImportFailure("partition_limit", f"Partition columns are missing: {sorted(missing)}")
        import pyarrow as pa
        from .import_schema import arrow_type, type_name
        for field in schema:
            arrow_type(type_name(field.type))  # Require a reversible JSON schema.
        for name_key in self.partition_by:
            dtype = schema.field(name_key).type
            if not (pa.types.is_string(dtype) or pa.types.is_large_string(dtype) or pa.types.is_integer(dtype) or pa.types.is_boolean(dtype) or pa.types.is_date(dtype)):
                raise ImportFailure("partition_limit", "Partition keys must be scalar string, integer, boolean or date")
        self.tables[name] = {"name": name, "schema": schema_fields(schema), "row_count": 0, 'max_row_bytes': 0, **metadata}

    def write(self, table, batch):
        self._check()
        from .import_pipeline import _check_batch
        row_bound = _check_batch(batch, self.limits)
        self.tables[table]['max_row_bytes'] = max(self.tables[table]['max_row_bytes'], row_bound)
        if schema_fields(batch.schema) != self.tables[table]['schema']:
            raise ImportFailure('schema_drift', 'Snapshot batches must retain the fixed table schema')
        if batch.nbytes > self.batch_bytes:
            if batch.num_rows <= 1:
                raise ImportFailure("batch_bytes_exceeded", "A single row exceeds batch_bytes")
            split = batch.num_rows // 2
            self.write(table, batch.slice(0, split))
            self.write(table, batch.slice(split))
            return
        if not batch.num_rows:
            return
        if not self.partition_by:
            self._write_partition(table, batch, {})
            return
        import pyarrow as pa
        values = [batch.column(batch.schema.get_field_index(k)).to_pylist() for k in self.partition_by]
        groups = {}
        for index, key in enumerate(zip(*values)):
            safe_key = tuple(v.isoformat() if hasattr(v, "isoformat") else v for v in key)
            groups.setdefault(safe_key, []).append(index)
            self._partition_keys.add((table, safe_key))
            if len(self._partition_keys) > self.max_partitions:
                raise ImportFailure("partition_limit", "Partition cardinality exceeded the frozen max_partitions")
        for key, indices in groups.items():
            part = dict(zip(self.partition_by, key))
            self._write_partition(table, batch.take(pa.array(indices, type=pa.int64())), part)

    def _write_partition(self, table, batch, partition):
        self._check()
        key = (table, canonical_hash(partition))
        current = self._writers.get(key)
        if current and current["arrow_bytes"] + batch.nbytes > self.target_bytes:
            self._finish(key)
            current = None
        if current is None:
            current = self._writers[key] = self._new_writer(table, batch.schema, partition)
        self.budget.check_usage(rows=self.row_count + batch.num_rows)
        current["writer"].write_batch(batch, row_group_size=batch.num_rows)
        current["rows"] += batch.num_rows
        current["arrow_bytes"] += batch.nbytes
        self.row_count += batch.num_rows
        self.tables[table]["row_count"] += batch.num_rows
        # Include open files: a large shard must not evade the disk ceiling until close.
        live_bytes = sum(w["path"].stat().st_size for w in self._writers.values())
        self.budget.check_usage(output_bytes=self.total_bytes + live_bytes, temp_bytes=self.total_bytes + live_bytes)

    def _finish(self, key):
        import pyarrow.parquet as pq
        current = self._writers.pop(key)
        current["writer"].close()
        path = current["path"]
        with path.open("rb+") as stream:
            os.fsync(stream.fileno())
        size = path.stat().st_size
        self.total_bytes += size
        metadata = pq.read_metadata(path)
        row_groups = []
        for i in range(metadata.num_row_groups):
            group = metadata.row_group(i)
            row_groups.append({"index": i, "row_count": group.num_rows, "uncompressed_bytes": group.total_byte_size})
        self.files.append({"relative_path": current["relative_path"], "format": "parquet", "size_bytes": size,
                           "sha256": file_sha256(path, cancel=self.cancel), "row_count": current["rows"],
                           "table": current["table"], "partition": current["partition"], "row_groups": row_groups})
        self._check()

    def finish(self):
        import pyarrow as pa
        from .import_schema import arrow_type
        # Empty tables still get a typed physical file, so DuckDB sees the schema.
        present = {entry["table"] for entry in self.files} | {w["table"] for w in self._writers.values()}
        for name, table in self.tables.items():
            if name not in present:
                schema = pa.schema([pa.field(f["name"], arrow_type(f["type"]), nullable=f.get("nullable", True)) for f in table["schema"]])
                key = (name, canonical_hash({}))
                self._writers[key] = self._new_writer(name, schema, {})
        for key in list(self._writers):
            self._finish(key)
        return sorted(self.files, key=lambda f: f["relative_path"])

    def close(self):
        for current in self._writers.values():
            current["writer"].close()
        self._writers.clear()
