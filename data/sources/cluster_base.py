"""Strict read-only cluster sources with bounded, single-consumer results."""
from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import math
import re
import time
from pathlib import Path

from data.cluster_connections import ClusterError, IDENTIFIER, config_fingerprint, normalize_config
from data.cluster_query_store import ClusterQueryStore, REMOTE_TERMINAL, public_query
from data.sources.base import DataSource

BUDGET_DEFAULTS = {"max_run_seconds": 120, "max_output_bytes": 8 * 1024 * 1024,
                   "max_rows": 10000, "batch_bytes": 256 * 1024, "max_scan_bytes": None,
                   "max_cpu_seconds": None, "strict_server": False}


def query_budget(value=None, defaults=None):
    if value is not None and hasattr(value, "to_dict"):
        value = {k: v for k, v in value.to_dict().items() if k in BUDGET_DEFAULTS}
    if value is not None and (not isinstance(value, dict) or set(value) - set(BUDGET_DEFAULTS)):
        raise ClusterError("invalid_budget", "Unsupported cluster budget field")
    result = {**BUDGET_DEFAULTS, **(defaults or {}), **(value or {})}
    for key, high in (("max_run_seconds", 3600), ("max_output_bytes", 1024 ** 3),
                      ("max_rows", 10000000), ("batch_bytes", 8 * 1024 * 1024),
                      ("max_scan_bytes", 1024 ** 5), ("max_cpu_seconds", 86400)):
        val = result[key]
        if val is None and key in {"max_scan_bytes", "max_cpu_seconds"}:
            continue
        seconds = key.endswith("seconds")
        if (isinstance(val, bool) or not isinstance(val, (int, float) if seconds else int)
                or not math.isfinite(val) or not (0.01 if seconds else 1) <= val <= high):
            raise ClusterError("invalid_budget", f"Invalid {key}")
    if not isinstance(result["strict_server"], bool):
        raise ClusterError("invalid_budget", "strict_server must be a boolean")
    result["batch_bytes"] = min(result["batch_bytes"], result["max_output_bytes"])
    return result


def validate_readonly_sql(sql, config, *, dialect=None):
    """Return the original SQL only after strict target-dialect AST authorization."""
    if not isinstance(sql, str) or not sql.strip() or len(sql.encode("utf-8")) > 65536:
        raise ClusterError("invalid_cluster_sql", "SQL must be a nonempty statement of at most 64 KiB")
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.scope import Scope, traverse_scope
        statements = sqlglot.parse(sql, read=dialect or config["kind"].replace("spark_sql", "spark"), error_level=sqlglot.ErrorLevel.RAISE)
        if len(statements) != 1 or not isinstance(statements[0], exp.Query):
            raise ClusterError("invalid_cluster_sql", "Only one read-only SELECT query is allowed")
        tree = statements[0]
        nodes = list(tree.walk())
        if any(comment.lstrip().startswith("+") for node in nodes for comment in (node.comments or [])):
            raise ClusterError("invalid_cluster_sql", "SQL optimizer hints are not allowed")
        if len(nodes) > 5000:
            raise ClusterError("invalid_cluster_sql", "Query AST exceeds the complexity limit")
        forbidden = {"Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command",
                     "Set", "Use", "Pragma", "Copy", "Export", "LoadData", "Transaction", "Commit",
                     "Rollback", "Into", "Lock", "Hint", "TableSample", "Placeholder", "Parameter",
                     "SessionParameter", "Var", "Pivot", "Unpivot", "Lateral", "UDTF", "Unnest",
                     "MatchRecognize", "Fetch", "WithTableHint", "HistoricalData", "QueryOption"}
        for node in nodes:
            if type(node).__name__ in forbidden:
                if type(node).__name__ == "Var" and isinstance(node.parent, (exp.DataType, exp.DataTypeParam)):
                    continue
                raise ClusterError("invalid_cluster_sql", f"Unsupported SQL construct: {type(node).__name__}")
            if isinstance(node, exp.Func):
                name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
                if name not in config["allowed_functions"] or isinstance(node.parent, exp.Dot):
                    raise ClusterError("cluster_function_denied", "Query uses an unapproved or qualified function")
        for scope in traverse_scope(tree):
            for _, (_, source) in scope.selected_sources.items():
                if isinstance(source, Scope):
                    continue
                if not isinstance(source, exp.Table) or not isinstance(source.this, exp.Identifier):
                    raise ClusterError("cluster_table_denied", "Table-valued functions and dynamic sources are not allowed")
                if source.args.get("pivots") or source.args.get("version") or source.args.get("when"):
                    raise ClusterError("cluster_table_denied", "Dynamic table versions are not supported")
                parts = [p.name for p in source.parts]
                if any(not IDENTIFIER.fullmatch(p) for p in parts):
                    raise ClusterError("cluster_table_denied", "Invalid table identifier")
                if len(parts) == 1:
                    parts = [config["schema"], *parts]
                if config["kind"] == "trino" and len(parts) == 2:
                    parts = [config["catalog"], *parts]
                if ".".join(parts).lower() not in config["allowed_tables"]:
                    raise ClusterError("cluster_table_denied", "Query references a table outside the authorized catalog/schema/table list")
        if any(w.args.get("recursive") for w in tree.find_all(exp.With)):
            raise ClusterError("invalid_cluster_sql", "Recursive queries are not supported")
    except ImportError as exc:
        raise ClusterError("dependency_unavailable", "sqlglot is required for strict cluster SQL validation") from exc
    except ClusterError:
        raise
    except Exception as exc:
        raise ClusterError("invalid_cluster_sql", "SQL could not be safely parsed in the target dialect") from exc
    return sql


class ClusterDataSource(DataSource):
    query_dialect = ""
    kind = ""
    hard_limits = frozenset()
    protocol = ""

    def __init__(self, config, *, credential_resolver=None, budget=None, query_store=None,
                 connection_id="", transport_factory=None):
        self.config = normalize_config(config)
        if self.config["kind"] != self.kind:
            raise ClusterError("invalid_cluster_config", "Source kind and adapter do not match")
        self.name = self.config["name"]
        self.budget = query_budget(budget)
        self.store = query_store or ClusterQueryStore()
        self.connection_id = connection_id
        self.credential_resolver = credential_resolver
        self.transport_factory = transport_factory
        self._closed = False

    def validate_query(self, sql):
        guard = getattr(self, "_configuration_guard", None)
        if callable(guard) and not guard():
            raise ClusterError("cluster_permission_denied", "Cluster configuration changed; reconnect before starting a new query")
        return validate_readonly_sql(sql, self.config, dialect=self.query_dialect)

    def get_schema(self):
        lines = ["Read-only cluster catalog. Quote qualified table parts separately, not as one dotted identifier."]
        for table in self.list_tables():
            lines.append(f"Table: {table}\n  Columns: call get_table_detail; row count is unknown.")
        return "\n\n".join(lines)

    def get_table_detail(self, table_name):
        metadata = self.get_table_schema(table_name)
        return "Table: " + table_name + "\n" + "\n".join(
            "  " + (column["name"] + " " + str(column.get("type", "")) if isinstance(column, dict) else str(column))
            for column in metadata.get("columns", [])
        )

    def get_capabilities(self):
        return {"kind": self.kind, "dialect": self.query_dialect, "query_dialect": self.query_dialect,
                "protocol": self.protocol, "read_only": True, "immutable": False, "large": True,
                "native_query": True, "cluster": True,
                "batch_reader": True, "requires_table_selection": False,
                "submit": True, "status": True, "fetch_batches": True, "native_cancel": True,
                "snapshot_result": True, "transactions": False, "source_snapshot": False,
                "hard_server_budgets": sorted(self.hard_limits),
                "client_limits": ["max_rows", "max_output_bytes", "batch_bytes", "max_run_seconds"],
                "network_page_bytes": self.config["max_response_bytes"], "prefetch_pages": 0,
                "credential_auth": ["none", "basic", "jwt"] if self.kind == "trino" else ["none", "ldap"],
                "scan_limit_is_result_limit": False, "statistics": "unavailable"}

    @property
    def capabilities(self):
        return self.get_capabilities()

    def _credential(self):
        ref = self.config["credential_ref"]
        if not ref:
            return ""
        try:
            if self.credential_resolver is None:
                from infrastructure.credential_store import load_secret
                result = load_secret(ref)
            else:
                result = self.credential_resolver(ref)
            if not isinstance(result, str) or not result or len(result) > 16384 or any(c in result for c in "\r\n\0"):
                raise ValueError()
            return result
        except Exception as exc:
            raise ClusterError("credential_unavailable", "The configured OS credential is unavailable") from exc

    def _record(self, query_id):
        record = self.store.get(query_id)
        if (config_fingerprint(record["config"]) != config_fingerprint(self.config)
                or record["connection_id"] != self.connection_id):
            raise ClusterError("cluster_permission_denied", "Query is bound to a different connection configuration")
        return record

    def _budget_policy(self, budget):
        requested = {"max_run_seconds"}
        requested.update(k for k in ("max_scan_bytes", "max_cpu_seconds") if budget[k] is not None)
        unavailable = sorted(requested - self.hard_limits)
        if (budget["strict_server"] and unavailable) or any(k in unavailable for k in ("max_scan_bytes", "max_cpu_seconds")):
            raise ClusterError("unsupported_budget", "The service cannot enforce the requested hard budget", unavailable=unavailable)
        return {"server_enforced": sorted(requested & self.hard_limits), "unavailable": unavailable,
                "client_enforced": ["max_rows", "max_output_bytes", "batch_bytes", "max_run_seconds"],
                "reason": "Client timeout and result limits do not bound remote scan or cost" if unavailable else ""}

    def _preflight(self):
        self._credential()
        try:
            import pyarrow  # noqa: F401
        except ImportError as exc:
            raise ClusterError("dependency_unavailable", "pyarrow is required to consume cluster results") from exc

    def submit_query(self, sql, *, idempotency_key="", budget=None):
        if self._closed:
            raise ClusterError("source_closed", "Cluster source is closed")
        sql = self.validate_query(sql)
        limits = query_budget(budget, self.budget)
        for key in ("max_rows", "max_output_bytes", "batch_bytes", "max_run_seconds", "max_scan_bytes", "max_cpu_seconds"):
            if self.budget[key] is not None and (limits[key] is None or limits[key] > self.budget[key]):
                raise ClusterError("invalid_budget", "A query cannot relax the source's configured budget")
        if self.budget["strict_server"] and not limits["strict_server"]:
            raise ClusterError("unsupported_budget", "A query cannot disable the configured hard-server policy")
        policy = self._budget_policy(limits)
        record, created = self.store.reserve(sql, self.config, limits, idempotency_key=idempotency_key, connection_id=self.connection_id)
        if not created:
            return public_query(record)
        query_id = record["query_id"]
        self.store.update(query_id, budget_policy=policy, submission_phase="preflight")
        try:
            self._preflight()
            latest = self.store.get(query_id)
            if latest.get("cancel_requested"):
                self.store.update(query_id, claim_submit=True)
                raise ClusterError("query_cancelled", "Query was cancelled before transport submission")
            self._submit(latest)
            latest = self.store.get(query_id)
            if latest.get("cancel_requested") and latest["remote_state"] not in REMOTE_TERMINAL:
                self.cancel_query(query_id)
        except Exception as exc:
            latest = self.store.get(query_id)
            code = exc.code if isinstance(exc, ClusterError) else "cluster_transport_error" if latest.get("submission_phase") == "preflight" else "remote_state_unknown"
            if latest["remote_state"] in REMOTE_TERMINAL:
                pass  # Preserve an explicit HTTP-200/HS2 server failure or cancellation.
            elif latest.get("submission_phase") == "preflight":
                self.store.update(query_id, state="failed", remote_state="not_submitted", error_code=code,
                                  error=str(exc) if isinstance(exc, ClusterError) else "Cluster preparation failed")
            else:
                self.store.update(query_id, state="remote_state_unknown", complete=False, error_code=code,
                                  error=str(exc) if isinstance(exc, ClusterError) else "Submission outcome is unknown; the query was not resubmitted")
            latest = self.store.get(query_id)
            raise ClusterError(latest["error_code"], latest["error"], query_id=query_id,
                               state=latest["state"]) from None
        return public_query(self.store.get(query_id))

    def query_status(self, query_id):
        record = self._record(query_id)
        if record["remote_state"] not in REMOTE_TERMINAL and record["native_query_id"]:
            try:
                self._poll(record)
            except Exception as exc:
                current = self.store.get(query_id)
                if current["remote_state"] not in REMOTE_TERMINAL:
                    self.store.update(query_id, state="cancel_pending" if current.get("cancel_requested") else "remote_state_unknown",
                                      error_code=getattr(exc, "code", "remote_state_unknown"),
                                      error=str(exc) if isinstance(exc, ClusterError) else "Server state could not be verified")
        elif record["state"] == "submitting":
            self.store.update(query_id, state="remote_state_unknown", error_code="remote_state_unknown",
                              error="Submission has no confirmed native identity; it must not be resubmitted")
        return public_query(self.store.get(query_id))

    def cancel_query(self, query_id):
        record = self._record(query_id)
        if record.get("snapshot"):
            return {**public_query(record), "cancel_outcome": "result_committed"}
        if record.get("snapshot_claimed") and record["complete"]:
            return public_query(self.store.update(query_id, state="cancel_pending", cancel_requested=True, complete=False,
                                                  error_code="query_cancelled", error="Snapshot delivery cancellation was requested"))
        if record["complete"]:
            return {**public_query(record), "cancel_outcome": "result_complete"}
        if record["remote_state"] in REMOTE_TERMINAL:
            if record["remote_state"] == "succeeded":
                record = self.store.update(query_id, state="failed", cancel_requested=True, complete=False,
                                           error_code=record["error_code"] or "result_discarded",
                                           error=record["error"] or "Server already finished; the unconsumed result was discarded")
            return public_query(record)
        self.store.update(query_id, state="cancel_pending", cancel_requested=True, complete=False, cancel_confirmed=False)
        try:
            if not record["native_query_id"]:
                raise ClusterError("cancel_pending", "No native identity is available to verify termination")
            self._cancel(self.store.get(query_id))
        except Exception:
            current = self.store.get(query_id)
            if current["remote_state"] not in REMOTE_TERMINAL:
                self.store.update(query_id, state="cancel_pending", error_code=current["error_code"] or "cancel_pending",
                                  error=current["error"] or "Server termination has not been confirmed")
        return public_query(self.store.get(query_id))

    def _check_active(self, record, *, cancel=None):
        current = self.store.get(record["query_id"])
        if (cancel is not None and cancel()) or current.get("cancel_requested"):
            raise ClusterError("query_cancelled", "Result consumption was cancelled")
        if time.time() - record["created_at"] > record["budget"]["max_run_seconds"]:
            raise ClusterError("budget_exceeded", "Query duration exceeded the client time limit", resource="max_run_seconds")

    def fetch_batches(self, query_id, *, budget=None, cancel=None):
        import pyarrow as pa
        record = self._record(query_id)
        if record["state"] in {"failed", "cancelled", "cancel_pending", "remote_state_unknown", "submitting"}:
            raise ClusterError(record.get("error_code") or "query_not_fetchable", "Query results are unavailable in the current state")
        limits = query_budget(budget, record["budget"])
        for key in ("max_rows", "max_output_bytes", "max_run_seconds", "batch_bytes"):
            if limits[key] > record["budget"][key]:
                raise ClusterError("invalid_budget", "Fetch cannot relax the submitted budget")
        record = self.store.update(query_id, claim_fetch=True, state="fetching")
        record["budget"] = limits
        rows_total = bytes_total = 0
        completed = False
        fixed_schema = None
        try:
            for rows, columns in self._pages(record, cancel=cancel):
                self._check_active(record, cancel=cancel)
                if not columns:
                    if rows:
                        raise ClusterError("cluster_protocol_error", "The server returned rows without a schema")
                    continue
                schema = self._arrow_schema(columns)
                if fixed_schema is not None and schema != fixed_schema:
                    raise ClusterError("schema_drift", "The server changed the result schema between pages")
                fixed_schema = schema
                pending, pending_bytes = [], 0
                for row in rows:
                    self._check_active(record, cancel=cancel)
                    if not isinstance(row, (list, tuple)) or len(row) != len(columns):
                        raise ClusterError("cluster_protocol_error", "Result row does not match its schema")
                    converted = [self._convert(v, field.type) for v, field in zip(row, schema)]
                    size = len(json.dumps(row, ensure_ascii=False, default=str, allow_nan=False).encode("utf-8"))
                    if size > limits["batch_bytes"]:
                        raise ClusterError("budget_exceeded", "A result row exceeds the batch byte budget", resource="batch_bytes")
                    if rows_total + len(pending) + 1 > limits["max_rows"] or bytes_total + pending_bytes + size > limits["max_output_bytes"]:
                        raise ClusterError("budget_exceeded", "Result exceeds its row or byte budget", resource="result")
                    if pending and (pending_bytes + size > limits["batch_bytes"] or len(pending) >= self.config["batch_rows"]):
                        batch = self._make_batch(pending, schema, limits)
                        rows_total += len(pending)
                        bytes_total += max(pending_bytes, batch.nbytes)
                        if bytes_total > limits["max_output_bytes"]:
                            raise ClusterError("budget_exceeded", "Arrow output exceeds its byte budget", resource="max_output_bytes")
                        self.store.update(query_id, result_rows=rows_total, result_bytes=bytes_total)
                        yield batch
                        pending, pending_bytes = [], 0
                    pending.append(converted)
                    pending_bytes += size
                if pending:
                    batch = self._make_batch(pending, schema, limits)
                    rows_total += len(pending)
                    bytes_total += max(pending_bytes, batch.nbytes)
                    if bytes_total > limits["max_output_bytes"]:
                        raise ClusterError("budget_exceeded", "Arrow output exceeds its byte budget", resource="max_output_bytes")
                    self.store.update(query_id, result_rows=rows_total, result_bytes=bytes_total)
                    yield batch
            self._check_active(record, cancel=cancel)
            latest = self.store.get(query_id)
            if latest["remote_state"] != "succeeded":
                raise ClusterError(latest.get("error_code") or "remote_state_unknown", "Server has not confirmed a complete successful result")
            if fixed_schema is None:
                raise ClusterError("unsupported_result_schema", "The server returned no result schema")
            if rows_total == 0:
                yield pa.RecordBatch.from_arrays([pa.array([], type=f.type) for f in fixed_schema], schema=fixed_schema)
            self.store.update(query_id, state="succeeded", complete=True, result_rows=rows_total, result_bytes=bytes_total, error_code="", error="")
            completed = True
        except GeneratorExit:
            self.store.update(query_id, complete=False, error_code="result_abandoned", error="The consumer stopped before completing the result")
            raise
        except ClusterError as exc:
            self.store.update(query_id, complete=False, error_code=exc.code, error=str(exc))
            raise
        except Exception:
            self.store.update(query_id, complete=False, error_code="cluster_result_error", error="Cluster result could not be decoded without loss")
            raise ClusterError("cluster_result_error", "Cluster result could not be decoded without loss") from None
        finally:
            if not completed:
                self.cancel_query(query_id)

    @staticmethod
    def _make_batch(rows, schema, limits):
        import pyarrow as pa
        arrays = [pa.array([row[i] for row in rows], type=field.type, safe=True) for i, field in enumerate(schema)]
        batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
        if batch.nbytes > limits["batch_bytes"]:
            raise ClusterError("budget_exceeded", "Decoded batch exceeds the byte budget", resource="batch_bytes")
        return batch

    def _arrow_schema(self, columns):
        import pyarrow as pa
        names = [c["name"] for c in columns]
        if (not columns or len(names) > 1024 or len(set(names)) != len(names)
                or any(not isinstance(n, str) or not n or len(n) > 1024 or "\0" in n for n in names)):
            raise ClusterError("unsupported_result_schema", "Results require distinct, bounded column names and at most 1024 columns")
        return pa.schema([pa.field(c["name"], self._arrow_type(c["type"]), nullable=True) for c in columns])

    def _arrow_type(self, typename):
        import pyarrow as pa
        name = str(typename).lower().strip()
        simple = {"boolean": pa.bool_(), "bool": pa.bool_(), "tinyint": pa.int8(), "smallint": pa.int16(),
                  "int": pa.int32(), "integer": pa.int32(), "bigint": pa.int64(), "real": pa.float32(),
                  "float": pa.float32(), "double": pa.float64(), "double precision": pa.float64(),
                  "string": pa.string(), "varchar": pa.string(), "char": pa.string(), "binary": pa.binary(),
                  "varbinary": pa.binary(), "date": pa.date32(), "null": pa.null(), "void": pa.null()}
        if name in simple:
            return simple[name]
        match = re.fullmatch(r"decimal\((\d+),\s*(\d+)\)", name)
        if match:
            p, s = map(int, match.groups())
            if 1 <= p <= 38 and 0 <= s <= p:
                return pa.decimal128(p, s)
        if re.fullmatch(r"(?:varchar|char)\(\d+\)", name):
            return pa.string()
        match = re.fullmatch(r"timestamp(?:\((\d+)\))?( with time zone|_ltz|_ntz)?", name)
        if match and (match[1] is None or int(match[1]) <= 6):
            return pa.timestamp("us", tz="UTC" if match[2] in {" with time zone", "_ltz"} else None)
        raise ClusterError("unsupported_result_type", "Unsupported cluster result type or precision")

    @staticmethod
    def _convert(value, arrow_type):
        if value is None:
            return None
        import pyarrow.types as pt
        if pt.is_decimal(arrow_type):
            if isinstance(value, (float, bool)):
                raise ClusterError("unsupported_result_type", "DECIMAL must arrive as an exact decimal value")
            return decimal.Decimal(str(value))
        if pt.is_timestamp(arrow_type):
            if isinstance(value, str):
                if re.search(r"\.\d{7,}", value):
                    raise ClusterError("unsupported_result_type", "Sub-microsecond timestamps require an explicit lossless representation")
                parts = value.rsplit(" ", 1)
                if arrow_type.tz and len(parts) == 2 and ("/" in parts[1] or parts[1] == "UTC"):
                    from zoneinfo import ZoneInfo
                    parsed, zone = dt.datetime.fromisoformat(parts[0]), ZoneInfo(parts[1])
                    if parsed.tzinfo is not None:
                        value = parsed.astimezone(zone)
                        if value.replace(tzinfo=None) != parsed.replace(tzinfo=None):
                            raise ClusterError("unsupported_result_type", "Timestamp offset and named timezone disagree")
                    else:
                        value = parsed.replace(tzinfo=zone, fold=0)
                        if value.utcoffset() != parsed.replace(tzinfo=zone, fold=1).utcoffset():
                            raise ClusterError("unsupported_result_type", "Ambiguous or nonexistent timezone-local timestamp needs an explicit offset")
                else:
                    value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if not isinstance(value, dt.datetime) or bool(value.tzinfo and value.utcoffset() is not None) != bool(arrow_type.tz):
                raise ClusterError("unsupported_result_type", "Timestamp timezone semantics do not match the result schema")
            return value
        if pt.is_date(arrow_type) and isinstance(value, str):
            return dt.date.fromisoformat(value)
        if pt.is_binary(arrow_type) and isinstance(value, str):
            import base64
            return base64.b64decode(value, validate=True)
        if pt.is_floating(arrow_type) and isinstance(value, str):
            return float(value)
        return value

    def snapshot_result(self, query_id, *, catalog, idempotency_key="", cancel=None, progress=None):
        """Stream to bounded Parquet, then publish via the common import pipeline."""
        import pyarrow.parquet as pq
        from data.dataset_snapshot import canonical_hash, file_sha256
        from data.dataset_manifest import safe_snapshot_root
        record = self._record(query_id)
        scope = dict(zip(("owner_id", "session_id", "workspace_id"), self.store.scope))
        if any(getattr(catalog, k, None) != v for k, v in scope.items()):
            raise ClusterError("scope_mismatch", "Snapshot catalog must have the query's fixed owner, session and workspace")
        key = idempotency_key or "cluster-" + query_id
        if not isinstance(key, str) or not key or len(key) > 200:
            raise ClusterError("invalid_idempotency_key", "Invalid snapshot idempotency key")
        if record.get("snapshot_idempotency_key", key) != key:
            raise ClusterError("idempotency_conflict", "The query already has a different snapshot key")
        if record.get("snapshot"):
            return record["snapshot"]
        try:
            from data.import_pipeline import create_import_plan, run_import
        except ImportError:
            raise ClusterError("dependency_unavailable", "The shared dataset import pipeline is not available") from None
        self.store.update(query_id, claim_snapshot=True, snapshot_idempotency_key=key)
        path = None
        stream = None
        writer = None
        try:
            staging = Path(catalog.staging_dir("cluster-" + query_id))
            staging.mkdir(parents=True, exist_ok=True)
            safe_snapshot_root(staging)
            if not staging.resolve().is_relative_to(Path(catalog.root).resolve()):
                raise ClusterError("scope_mismatch", "Cluster staging must be inside the scoped catalog")
            path = staging / "cluster-result.parquet"
            record = self.store.get(query_id)
            if record["complete"]:
                expected = record["driver_state"].get("spool_sha256")
                if not expected or not path.is_file() or file_sha256(path) != expected:
                    raise ClusterError("result_already_consumed", "The complete result has no reusable verified staging file")
            else:
                path.unlink(missing_ok=True)
                stream = self.fetch_batches(query_id, cancel=cancel)
                with path.open("xb") as output:
                    try:
                        for batch in stream:
                            if writer is None:
                                writer = pq.ParquetWriter(output, batch.schema, compression="zstd")
                            writer.write_batch(batch)
                            if output.tell() > record["budget"]["max_output_bytes"]:
                                raise ClusterError("budget_exceeded", "Parquet staging exceeds the output budget")
                        if writer is None:
                            raise ClusterError("unsupported_result_schema", "The server returned no schema")
                    except BaseException:
                        if writer is not None:
                            try:
                                writer.close()
                            except Exception:
                                pass
                            writer = None
                        raise
                    else:
                        writer.close()
                        writer = None
                if path.stat().st_size > record["budget"]["max_output_bytes"]:
                    raise ClusterError("budget_exceeded", "Parquet staging exceeds the output budget")
                record = self.store.get(query_id)
                state = {**record["driver_state"], "spool_sha256": file_sha256(path)}
                self.store.update(query_id, driver_state=state)
            record = self.store.get(query_id)
            if not record["complete"] or record.get("cancel_requested"):
                raise ClusterError("incomplete_result", "Incomplete results cannot be published")
            if cancel is not None and cancel():
                raise ClusterError("query_cancelled", "Snapshot publication was cancelled")
            size = path.stat().st_size
            batch_bytes = record["budget"]["batch_bytes"]
            memory = max(512 * 1024 * 1024, batch_bytes * 4)
            lineage = {"cluster": {"connection_id": self.connection_id, "query_id": query_id,
                                  "native_query_id": record["native_query_id"], "dialect": self.query_dialect,
                                  "sql_sha256": hashlib.sha256(record["sql"].encode()).hexdigest(), "complete": True}}
            plan = create_import_plan(path, **scope,
                                      budget={"max_input_bytes": max(size, 1), "max_output_bytes": record["budget"]["max_output_bytes"],
                                              "max_rows": record["budget"]["max_rows"], "max_run_seconds": record["budget"]["max_run_seconds"],
                                              "batch_bytes": batch_bytes, "rss_bytes": memory, "duckdb_memory_bytes": memory,
                                              "queue_batches": 1, "threads": 1, "temp_bytes": record["budget"]["max_output_bytes"]},
                                      options={"idempotency_key": "cluster-" + query_id, "lineage": lineage, "table_name": "cluster_result"})
            plan["source"] = {**plan["source"], "kind": "cluster", **lineage["cluster"]}
            plan["plan_hash"] = canonical_hash({k: v for k, v in plan.items() if k != "plan_hash"})
            def import_cancel():
                self._check_active(record, cancel=cancel)
                return False

            manifest = run_import(plan, catalog=catalog, progress=progress, cancel=import_cancel)
            if any(manifest.get(k) != v for k, v in scope.items()):
                raise ClusterError("scope_mismatch", "Published manifest does not match the query scope")
            # The common catalog commit is the boundary: a later cancellation
            # cannot remove or misreport a published immutable version.
            self.store.update(query_id, snapshot=manifest, state="succeeded", complete=True,
                              cancel_outcome="result_committed", error_code="", error="")
            path.unlink(missing_ok=True)
            return manifest
        finally:
            if writer is not None:
                writer.close()
            if stream is not None:
                stream.close()
            self.store.update(query_id, snapshot_claimed=False)
            latest = self.store.get(query_id)
            if path is not None and (not latest["complete"] or not latest["driver_state"].get("spool_sha256")):
                path.unlink(missing_ok=True)

    def list_tables(self):
        return list(self.config["allowed_tables"])

    def get_preview(self):
        return [{"name": t, "columns": [], "total_rows": None, "schema_available": False,
                 "row_count_available": False} for t in self.list_tables()]

    def _table_sql(self, table_name):
        if not isinstance(table_name, str) or table_name.lower() not in self.config["allowed_tables"]:
            raise ClusterError("cluster_table_denied", "Table is not authorized")
        quote = '"' if self.kind == "trino" else "`"
        return ".".join(quote + p + quote for p in table_name.split("."))

    def get_table_schema(self, table_name):
        query = self.submit_query("SELECT * FROM " + self._table_sql(table_name) + " WHERE 1 = 0")
        columns = []
        for batch in self.fetch_batches(query["query_id"]):
            columns = [{"name": f.name, "type": str(f.type)} for f in batch.schema]
        return {"name": table_name, "columns": columns, "total_rows": None, "schema_available": bool(columns)}

    def get_preview_table(self, table_name, max_rows=100):
        rows = min(max(int(max_rows), 1), 200)
        query = self.submit_query("SELECT * FROM " + self._table_sql(table_name) + f" LIMIT {rows}", budget={"max_rows": rows})
        values, columns = [], []
        for batch in self.fetch_batches(query["query_id"]):
            columns = batch.schema.names
            values.extend(batch.to_pylist())
        return {"name": table_name, "columns": columns, "rows": values, "total_rows": None}

    def execute_query(self, sql):
        import pandas as pd
        try:
            import pyarrow as pa
            query = self.submit_query(sql)
            batches = list(self.fetch_batches(query["query_id"]))
            return pa.Table.from_batches(batches).to_pandas(), ""
        except ClusterError as exc:
            return pd.DataFrame(), f"{exc.code}: {exc}"

    def execute_query_batches(self, sql, *, budget=None, cancel=None):
        query = self.submit_query(sql, budget=budget)
        yield from self.fetch_batches(query["query_id"], cancel=cancel)

    def writable_conn(self):
        return None

    def create_analysis_table(self, *args, **kwargs):
        return "cluster_read_only: Materialize a complete bounded result into a local dataset first"

    def test_connection(self):
        query = self.submit_query("SELECT 1 AS connection_test", budget={"max_rows": 1})
        for _ in self.fetch_batches(query["query_id"]):
            pass
        return {"connected": True, "query": self.query_status(query["query_id"]), "capabilities": self.get_capabilities()}

    def close(self):
        # Closing a local transport is not proof that a remote query has stopped.
        self._closed = True
