"""JSON-only dataset snapshot metadata with bounded, streaming verification.

Arrow is intentionally optional at import time. Handles contain metadata and a
root path, never a connection, executable serialization, or an in-memory table.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime
from pathlib import Path


class ManifestError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def canonical_json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ManifestError("invalid_manifest", "Metadata must contain finite JSON values") from exc


def validate_id(value, name="id"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}", value) or value in (".", ".."):
        raise ManifestError("invalid_manifest", f"{name} must be a safe bounded identifier")
    return value


def validate_scope(value, name, *, allow_empty=False):
    if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value) or (not allow_empty and not value.strip()):
        raise ManifestError("invalid_manifest", f"Invalid {name}")
    return value


def _count(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= (1 << 63) - 1:
        raise ManifestError("invalid_manifest", f"{name} must be a nonnegative bounded integer")
    return value


def _json_types(value, depth=0):
    if depth > 40:
        raise ManifestError("invalid_manifest", "Metadata nesting is too deep")
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ManifestError("invalid_manifest", "JSON object keys must be strings")
        for item in value.values():
            _json_types(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _json_types(item, depth + 1)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ManifestError("invalid_manifest", "Metadata must contain JSON values only")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ManifestError("invalid_manifest", "Metadata contains a nonfinite number")


def validate_schema(schema):
    fields = schema.get("fields") if isinstance(schema, dict) else schema
    if not isinstance(fields, list) or len(fields) > 100000:
        raise ManifestError("invalid_manifest", "schema must contain Arrow field definitions")
    names = set()
    for field in fields:
        if not isinstance(field, dict):
            raise ManifestError("invalid_manifest", "Each schema field must be an object")
        name, data_type = field.get("name"), field.get("type")
        if not isinstance(name, str) or not name or len(name) > 1024 or "\0" in name or name.casefold() in names:
            raise ManifestError("invalid_manifest", "Schema contains invalid or duplicate field names")
        names.add(name.casefold())
        if "nullable" in field and not isinstance(field["nullable"], bool):
            raise ManifestError("invalid_manifest", "Field nullable must be boolean")
        # Arrow's str(type) includes nested fields, decimal precision, and timezone.
        # Validate its grammar envelope without importing Arrow at app startup.
        if isinstance(data_type, str):
            primitive = r"(?:null|bool|boolean|u?int(?:8|16|32|64)|halffloat|float|double|float(?:16|32|64)|string|large_string|string_view|binary|large_binary|binary_view|date32\[day\]|date64\[ms\]|date32|date64|utf8|large_utf8)"
            complex_type = r"(?:decimal(?:32|64|128|256)?\(\d+,\s*-?\d+\)|(?:timestamp|time32|time64|duration)\[(?:s|ms|us|ns)(?:,\s*tz=[^\]\x00-\x1f]+)?\]|(?:list|large_list|fixed_size_list|struct|map|dictionary|extension|fixed_size_binary|month_day_nano_interval|month_interval|day_time_interval).*)"
            if len(data_type) > 8192 or not re.fullmatch(f"(?:{primitive}|{complex_type})", data_type):
                raise ManifestError("invalid_manifest", f"Unsupported Arrow type definition for {name}")
        elif isinstance(data_type, dict):
            if not isinstance(data_type.get("name", data_type.get("type")), str):
                raise ManifestError("invalid_manifest", "Structured Arrow types require a type name")
        else:
            raise ManifestError("invalid_manifest", "Arrow field type must be a string or object")
    return schema


def relative_file_path(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or "\\" in value or ":" in value or "\0" in value:
        raise ManifestError("unsafe_snapshot_path", "Shard paths must be portable relative paths")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.endswith((" ", ".")) or any(ord(c) < 32 for c in p) for p in parts):
        raise ManifestError("unsafe_snapshot_path", "Invalid shard path component")
    if any(re.match(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", p, re.I) for p in parts):
        raise ManifestError("unsafe_snapshot_path", "Reserved filesystem name")
    if parts[0] in ("manifest.json", ".operation.json"):
        raise ManifestError("unsafe_snapshot_path", "Reserved snapshot metadata path")
    return value


def _ordinary(path, *, directory=False):
    try:
        info = path.lstat()
    except OSError as exc:
        raise ManifestError("snapshot_file_missing", "Snapshot file or directory is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ManifestError("unsafe_snapshot_path", "Links and reparse points are forbidden")
    if directory:
        valid = stat.S_ISDIR(info.st_mode)
    else:
        valid = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid:
        raise ManifestError("unsafe_snapshot_path", "Only ordinary directories and unlinked regular files are accepted")
    return info


def safe_snapshot_root(root):
    root = Path(os.path.abspath(root))
    for parent in reversed((root, *root.parents)):
        _ordinary(parent, directory=True)
    return root


def sha256_file(path, *, budget=None, cancel=None):
    import time
    started = time.monotonic()
    path = Path(path)
    before = _ordinary(path)
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or opened.st_nlink != 1:
            raise ManifestError("snapshot_changed", "Shard was replaced during verification")
        for block in iter(lambda: stream.read(min(1 << 20, budget.batch_bytes) if budget else 1 << 20), b""):
            from .dataset_snapshot import check_cancel
            check_cancel(cancel)
            if budget:
                budget.check_usage(run_seconds=time.monotonic() - started)
            digest.update(block)
        after = os.fstat(stream.fileno())
    current = _ordinary(path)
    signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if signature(before) != signature(after) or signature(before) != signature(current):
        raise ManifestError("snapshot_changed", "Shard changed during verification")
    return digest.hexdigest()


def manifest_digest(manifest):
    # Publication metadata is derived by the catalog and excluded from replay identity.
    value = {key: item for key, item in manifest.items() if key not in ("manifest_hash", "catalog_version", "published_at")}
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_manifest(manifest, *, root=None, verify_files=False, budget=None, cancel=None):
    import time
    started = time.monotonic()
    if budget is not None:
        from infrastructure.resource_budget import BudgetSpec
        budget = BudgetSpec.from_dict(budget)
    def check():
        from .dataset_snapshot import check_cancel
        check_cancel(cancel)
        if budget:
            budget.check_usage(run_seconds=time.monotonic() - started)
    check()
    if not isinstance(manifest, dict):
        raise ManifestError("invalid_manifest", "Manifest must be a dictionary")
    _json_types(manifest)
    if len(canonical_json(manifest).encode("utf-8")) > 16 << 20:
        raise ManifestError("invalid_manifest", "Manifest exceeds 16 MiB")
    value = copy.deepcopy(manifest)
    if type(value.get("version")) is not int or value["version"] != 1:
        raise ManifestError("invalid_manifest", "Only DatasetManifest version 1 is supported")
    for name in ("dataset_id", "snapshot_id"):
        validate_id(value.get(name), name)
    for name in ("owner_id", "session_id", "workspace_id"):
        validate_scope(value.get(name), name, allow_empty=name != "owner_id")
    if value.get("status") not in ("staging", "ready", "invalid", "retired"):
        raise ManifestError("invalid_manifest", "Invalid snapshot status")
    validate_schema(value.get("schema"))
    for name in ("source", "lineage", "schema_policy"):
        if not isinstance(value.get(name), dict):
            raise ManifestError("invalid_manifest", f"{name} must be an object")
    _count(value.get("row_count"), "row_count")
    try:
        created = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("Timezone required")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ManifestError("invalid_manifest", "created_at must be an ISO timestamp with timezone") from exc
    shards = value.get("files")
    if not isinstance(shards, list) or len(shards) > 100000 or (value["row_count"] and not shards):
        raise ManifestError("invalid_manifest", "Invalid snapshot file list")
    paths = set()
    total_rows = 0
    for shard in shards:
        if not isinstance(shard, dict):
            raise ManifestError("invalid_manifest", "Shard metadata must be an object")
        name = relative_file_path(shard.get("relative_path"))
        if name.casefold() in paths:
            raise ManifestError("invalid_manifest", "Duplicate shard path")
        paths.add(name.casefold())
        if shard.get("format") not in ("parquet", "arrow", "ipc", "csv", "json", "jsonl", "npy", "safetensors"):
            raise ManifestError("invalid_manifest", "Unsupported shard format")
        _count(shard.get("size_bytes"), "size_bytes")
        total_rows += _count(shard.get("row_count"), "shard row_count")
        if not isinstance(shard.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", shard["sha256"]):
            raise ManifestError("invalid_manifest", "Shard requires a lowercase SHA-256 digest")
    if total_rows != value["row_count"]:
        raise ManifestError("invalid_manifest", "Shard row counts do not match snapshot row_count")
    tables = value.get("tables")
    if tables is not None:
        if not isinstance(tables, list) or not tables or len(tables) > 256:
            raise ManifestError("invalid_manifest", "tables must be a nonempty bounded table mapping")
        table_names = set()
        for table in tables:
            if not isinstance(table, dict):
                raise ManifestError("invalid_manifest", "Table metadata must be an object")
            name = table.get('name')
            if not isinstance(name, str) or len(name) > 160 or not re.fullmatch(r'\w+', name):
                raise ManifestError('invalid_manifest', 'Table names must be normalized identifiers')
            if name.casefold() in table_names:
                raise ManifestError("invalid_manifest", "Duplicate table name")
            table_names.add(name.casefold())
            validate_schema(table.get("schema"))
            _count(table.get("row_count"), "table row_count")
            selected = [s for s in shards if s.get("table") == name]
            if sum(s["row_count"] for s in selected) != table["row_count"] or not selected:
                raise ManifestError("invalid_manifest", "Table shard mapping or row count is inconsistent")
        if any(s.get("table", "").casefold() not in table_names for s in shards):
            raise ManifestError("invalid_manifest", "Shard refers to an unknown table")
        if sum(t["row_count"] for t in tables) != value["row_count"] or value["schema"] != tables[0]["schema"]:
            raise ManifestError("invalid_manifest", "Top-level schema/row_count must match the table mapping")
    elif len({s.get("table", "data") for s in shards}) > 1:
        raise ManifestError("invalid_manifest", "Multiple tables require an explicit table mapping")
    if "manifest_hash" in value and value["manifest_hash"] != manifest_digest(value):
        raise ManifestError("manifest_hash_mismatch", "Manifest metadata hash does not match")
    if verify_files:
        if root is None:
            raise ManifestError("invalid_manifest", "A snapshot root is required for file verification")
        base = safe_snapshot_root(root)
        expected = {shard["relative_path"] for shard in shards}
        found = set()
        pending = [base]
        while pending:
            directory = pending.pop()
            _ordinary(directory, directory=True)
            for path in directory.iterdir():
                check()
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    _ordinary(path, directory=True)
                    pending.append(path)
                else:
                    _ordinary(path)
                    name = path.relative_to(base).as_posix()
                    if name != "manifest.json":
                        if name not in expected:
                            raise ManifestError('snapshot_file_set_mismatch', 'Snapshot contains an unlisted file')
                        found.add(name)
        if found != expected:
            raise ManifestError("snapshot_file_set_mismatch", "Snapshot contains missing or unlisted files")
        for shard in shards:
            check()
            path = base.joinpath(*shard["relative_path"].split("/"))
            if _ordinary(path).st_size != shard["size_bytes"]:
                raise ManifestError("snapshot_size_mismatch", "Shard size does not match manifest")
            if budget:
                from dataclasses import replace
                remaining = budget.max_run_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    check()
                hash_budget = replace(budget, max_run_seconds=max(0.001, remaining))
            else:
                hash_budget = None
            if sha256_file(path, budget=hash_budget, cancel=cancel) != shard["sha256"]:
                raise ManifestError("snapshot_hash_mismatch", "Shard SHA-256 does not match manifest")
            if shard['format'] == 'parquet':
                import pyarrow.parquet as pq
                from .import_schema import schema_fields
                with pq.ParquetFile(path, thrift_string_size_limit=16 << 20, thrift_container_size_limit=1000000) as parquet:
                    metadata = parquet.metadata
                    table = next((t for t in (tables or []) if t['name'] == shard.get('table')), None)
                    expected_schema = table['schema'] if table else value['schema']
                    if isinstance(expected_schema, dict):
                        expected_schema = expected_schema['fields']
                    if metadata.num_rows != shard['row_count'] or schema_fields(parquet.schema_arrow) != expected_schema:
                        raise ManifestError('invalid_manifest', 'Physical Parquet schema/rows differ from the manifest')
                    # Pruning metadata must be proved against every row group.
                    for column, partition_value in shard.get('partition', {}).items():
                        index = parquet.schema_arrow.get_field_index(column)
                        if index < 0:
                            raise ManifestError('invalid_manifest', 'Unknown partition column')
                        for group_index in range(metadata.num_row_groups):
                            group = metadata.row_group(group_index)
                            stats = group.column(index).statistics
                            if stats is None:
                                raise ManifestError('invalid_manifest', 'Partition pruning requires physical statistics')
                            if partition_value is None:
                                valid = stats.null_count == group.num_rows
                            else:
                                normalize = lambda v: v.isoformat() if hasattr(v, 'isoformat') else v
                                valid = stats.has_min_max and stats.null_count == 0 and normalize(stats.min) == partition_value and normalize(stats.max) == partition_value
                            if not valid:
                                raise ManifestError('invalid_manifest', 'Partition metadata differs from physical values')
    return value


class PinnedSnapshot:
    """Pin ordinary shard identities for an entire read.

    Windows denies concurrent writes/deletes. POSIX uses advisory read locks and
    identity checks; it cannot defeat a hostile same-user process ignoring locks.
    Published directories are never overwritten or removed by the catalog.
    """
    def __init__(self, handle, *, files=None, budget=None, cancel=None):
        self.handle, self.streams = handle, []
        self.files = handle.manifest['files'] if files is None else files
        self.budget, self.cancel = budget, cancel

    def __enter__(self):
        import time
        started = time.monotonic()
        try:
            base = safe_snapshot_root(self.handle.root)
            for entry in self.files:
                path = base / relative_file_path(entry['relative_path'])
                safe_snapshot_root(path.parent)
                before = _ordinary(path)
                if os.name == 'nt':
                    import ctypes
                    import msvcrt
                    from ctypes import wintypes
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    create = kernel.CreateFileW
                    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                    create.restype = wintypes.HANDLE
                    raw = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
                    if raw == ctypes.c_void_p(-1).value:
                        raise ManifestError('dataset_changed', 'Snapshot shard is being modified or unavailable')
                    fd = msvcrt.open_osfhandle(raw, os.O_RDONLY | os.O_BINARY)
                else:
                    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                stream = os.fdopen(fd, 'rb')
                self.streams.append((path, stream, before))
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(min(1 << 20, self.budget.batch_bytes) if self.budget else 1 << 20), b''):
                    from .dataset_snapshot import check_cancel
                    check_cancel(self.cancel)
                    if self.budget:
                        self.budget.check_usage(run_seconds=time.monotonic() - started)
                    digest.update(block)
                if digest.hexdigest() != entry['sha256'] or before.st_size != entry['size_bytes']:
                    raise ManifestError('dataset_changed', 'Snapshot shard content no longer matches its manifest')
            self.check()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def check(self):
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        for path, stream, before in self.streams:
            safe_snapshot_root(path.parent)
            if signature(before) != signature(os.fstat(stream.fileno())) or signature(before) != signature(_ordinary(path)):
                raise ManifestError('dataset_changed', 'Snapshot shard changed during a read')

    def __exit__(self, *args):
        for _, stream, _ in self.streams:
            stream.close()
        self.streams.clear()


class DatasetHandle:
    __slots__ = ("_manifest", "_root")

    def __init__(self, manifest, root):
        value = validate_manifest(manifest)
        value["manifest_hash"] = manifest_digest(value)
        object.__setattr__(self, "_manifest", canonical_json(value))
        object.__setattr__(self, "_root", safe_snapshot_root(root))

    def __setattr__(self, name, value):
        raise AttributeError("DatasetHandle is immutable")

    @property
    def manifest(self):
        return json.loads(self._manifest)

    @property
    def root(self):
        return self._root

    @property
    def dataset_id(self):
        return self.manifest["dataset_id"]

    @property
    def snapshot_id(self):
        return self.manifest["snapshot_id"]

    def to_dict(self):
        return {"manifest": self.manifest, "root": str(self.root)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"manifest", "root"}:
            raise ManifestError("invalid_manifest", "Handle must contain only manifest and root")
        return cls(value["manifest"], value["root"])
