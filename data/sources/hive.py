"""HiveServer2 operations using the pinned optional PyHive Thrift definitions.

The DB-API fetchmany buffer is deliberately bypassed: every complete Thrift RPC
response, including metadata and SASL frames, passes through a byte/deadline cap.
"""
from __future__ import annotations

import base64
import datetime as dt
import importlib.metadata
import math
import re
import socket
import ssl
import threading
import time
import uuid

from data.cluster_connections import ClusterError
from data.sources.cluster_base import ClusterDataSource

PYHIVE_VERSION = "0.7.0"


class BoundedThriftTransport:
    """Bound a whole RPC, before readAll can allocate an advertised field/frame."""

    def __init__(self, transport, maximum, timeout):
        self.transport, self.maximum, self.timeout = transport, maximum, timeout
        self.begin()

    def begin(self):
        self.read_bytes = self.write_bytes = 0
        self.deadline = time.monotonic() + self.timeout

    def _remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ClusterError("remote_state_unknown", "HS2 RPC deadline expired")
        if hasattr(self.transport, "set_timeout"):
            self.transport.set_timeout(remaining)

    def read(self, size):
        self._remaining()
        if not isinstance(size, int) or size < 0 or size > self.maximum - self.read_bytes:
            raise ClusterError("cluster_frame_too_large", "HS2 response exceeds the whole-RPC byte budget")
        value = self.transport.read(size)
        if not isinstance(value, bytes) or len(value) > size:
            raise ClusterError("cluster_protocol_error", "Invalid HS2 transport read")
        self.read_bytes += len(value)
        return value

    def readAll(self, size):
        if not isinstance(size, int) or size < 0 or size > self.maximum - self.read_bytes:
            raise ClusterError("cluster_frame_too_large", "HS2 field or SASL frame exceeds the remaining RPC budget")
        chunks, remaining = [], size
        while remaining:
            block = self.read(remaining)
            if not block:
                raise EOFError("HS2 transport ended before the complete response")
            chunks.append(block)
            remaining -= len(block)
        return b"".join(chunks)

    def write(self, value):
        self._remaining()
        self.write_bytes += len(value)
        if self.write_bytes > max(self.maximum, 65536 + 32768):
            raise ClusterError("cluster_frame_too_large", "HS2 request exceeds the request byte budget")
        self.transport.write(value)

    def flush(self):
        self.transport.flush()

    def open(self):
        self.transport.open()

    def close(self):
        self.transport.close()

    def isOpen(self):
        return self.transport.isOpen()


class _SocketStream:
    def __init__(self, config):
        self.config, self.handle = config, None
        self.timeout = config["timeout_seconds"]

    def open(self):
        if self.handle is not None:
            return
        handle = socket.create_connection((self.config["host"], self.config["port"]), timeout=self.timeout)
        try:
            if self.config["tls"]:
                handle = ssl.create_default_context().wrap_socket(handle, server_hostname=self.config["host"])
            self.handle = handle
        except BaseException:
            handle.close()
            raise

    def set_timeout(self, timeout):
        self.timeout = timeout
        if self.handle is not None:
            self.handle.settimeout(timeout)

    def isOpen(self):
        return self.handle is not None

    def read(self, size):
        return self.handle.recv(size)

    def write(self, value):
        self.handle.sendall(value)

    def flush(self):
        pass

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def _handle_dict(handle):
    try:
        guid, secret = handle.operationId.guid, handle.operationId.secret
        if len(guid) != 16 or not isinstance(secret, bytes) or not 1 <= len(secret) <= 256:
            raise ValueError()
        return {"guid": base64.b64encode(guid).decode(), "secret": base64.b64encode(secret).decode(),
                "operation_type": int(handle.operationType), "has_result_set": bool(handle.hasResultSet)}
    except (AttributeError, TypeError, ValueError):
        raise ClusterError("cluster_protocol_error", "HS2 returned an invalid operation handle") from None


class PyHiveHS2Driver:
    """One supported driver selection, with no optional import during app boot."""

    @staticmethod
    def check_dependencies(config):
        try:
            if importlib.metadata.version("PyHive") != PYHIVE_VERSION:
                raise ImportError()
            from TCLIService import TCLIService, ttypes  # noqa: F401
            from thrift.protocol.TBinaryProtocol import TBinaryProtocol  # noqa: F401
            if config["auth"] == "ldap":
                from pyhive.sasl_compat import PureSASLClient  # noqa: F401
                import thrift_sasl  # noqa: F401
        except (ImportError, importlib.metadata.PackageNotFoundError):
            raise ClusterError("dependency_unavailable", "HS2 requires the pinned optional requirements/cluster-hs2.txt (PyHive 0.7.0; LDAP also needs SASL)") from None

    def __init__(self, config, *, credential_resolver, session_configuration):
        self.check_dependencies(config)
        from TCLIService import TCLIService, ttypes
        from thrift.protocol.TBinaryProtocol import TBinaryProtocol
        self.config, self.types, self.service = config, ttypes, TCLIService
        self.credential_resolver = credential_resolver
        self.session_configuration = session_configuration
        self.session = self.client = self.transport = None
        self.guards = []
        self.protocol_type = TBinaryProtocol
        self.lock = threading.RLock()

    def _connect(self):
        if self.client is not None:
            return
        maximum, timeout = self.config["max_response_bytes"], self.config["timeout_seconds"]
        raw = BoundedThriftTransport(_SocketStream(self.config), maximum, timeout)
        transport = raw
        guards = [raw]
        if self.config["auth"] == "ldap":
            from pyhive.sasl_compat import PureSASLClient
            from thrift_sasl import TSaslClientTransport
            # Use the pinned driver's existing pure-SASL compatibility layer.
            # Both SASL wire frames and decoded Thrift responses are capped.
            def sasl_factory():
                return PureSASLClient(self.config["host"], service="hive", mechanism="PLAIN",
                                      username=self.config["username"], password=self.credential_resolver())
            transport = BoundedThriftTransport(TSaslClientTransport(sasl_factory, "PLAIN", raw), maximum, timeout)
            guards.append(transport)
        try:
            transport.open()
            protocol = self.protocol_type(transport, string_length_limit=maximum, container_length_limit=maximum // 2)
            self.client = self.service.Client(protocol)
            self.transport, self.guards = transport, guards
        except BaseException:
            raw.close()
            raise

    def _check(self, response):
        status = response.status
        code = status.statusCode
        success = {self.types.TStatusCode.SUCCESS_STATUS, self.types.TStatusCode.SUCCESS_WITH_INFO_STATUS}
        if code in success:
            return response
        if code == self.types.TStatusCode.INVALID_HANDLE_STATUS:
            raise ClusterError("remote_state_unknown", "HS2 no longer recognizes the saved operation handle")
        state = str(getattr(status, "sqlState", "") or "")
        denied = state.startswith("28") or state == "42501"
        raise ClusterError("cluster_permission_denied" if denied else "cluster_query_failed",
                           "HS2 denied the operation" if denied else "HS2 rejected the operation")

    def _rpc(self, method, request):
        with self.lock:
            self._connect()
            for guard in self.guards:
                guard.begin()
            try:
                return self._check(getattr(self.client, method)(request))
            except BaseException:
                # A partial response cannot be reused as the next RPC stream.
                self.transport.close()
                self.client = self.transport = None
                raise

    def open_session(self):
        if self.session is not None:
            return
        req = self.types.TOpenSessionReq(client_protocol=self.types.TProtocolVersion.HIVE_CLI_SERVICE_PROTOCOL_V6,
                                         username=self.config["username"], configuration=self.session_configuration)
        result = self._rpc("OpenSession", req)
        if result.sessionHandle is None:
            raise ClusterError("cluster_protocol_error", "HS2 did not return a session handle")
        if result.serverProtocolVersion != self.types.TProtocolVersion.HIVE_CLI_SERVICE_PROTOCOL_V6:
            raise ClusterError("unsupported_capability", "HS2 did not negotiate the supported V6 result encoding")
        self.session = result.sessionHandle

    def execute(self, sql, timeout_seconds):
        # queryTimeout is a server request, not a claim that every HS2 service
        # enforces it. Hard-server budgets remain unavailable for Hive/Spark.
        req = self.types.TExecuteStatementReq(sessionHandle=self.session, statement=sql,
                                              confOverlay={}, runAsync=True, queryTimeout=math.ceil(timeout_seconds))
        result = self._rpc("ExecuteStatement", req)
        return _handle_dict(result.operationHandle)

    def _handle(self, value):
        try:
            operation = self.types.THandleIdentifier(guid=base64.b64decode(value["guid"], validate=True),
                                                     secret=base64.b64decode(value["secret"], validate=True))
            return self.types.TOperationHandle(operationId=operation, operationType=value["operation_type"],
                                               hasResultSet=value["has_result_set"])
        except (KeyError, ValueError, TypeError):
            raise ClusterError("cluster_protocol_error", "Invalid persisted HS2 operation handle") from None

    def status(self, handle):
        result = self._rpc("GetOperationStatus", self.types.TGetOperationStatusReq(operationHandle=self._handle(handle)))
        state = self.types.TOperationState._VALUES_TO_NAMES.get(result.operationState, "UNKNOWN_STATE")
        return {"state": state, "sql_state": getattr(result, "sqlState", "") or ""}

    def columns(self, handle):
        result = self._rpc("GetResultSetMetadata", self.types.TGetResultSetMetadataReq(operationHandle=self._handle(handle)))
        columns = []
        for column in result.schema.columns:
            entries = column.typeDesc.types
            if len(entries) != 1 or entries[0].primitiveEntry is None:
                raise ClusterError("unsupported_result_type", "Complex HS2 types require an explicit projection")
            primitive = entries[0].primitiveEntry
            name = self.types.TTypeId._VALUES_TO_NAMES.get(primitive.type, "UNKNOWN_TYPE").removesuffix("_TYPE").lower()
            qualifiers = getattr(getattr(primitive, "typeQualifiers", None), "qualifiers", None) or {}
            if name == "decimal":
                try:
                    name = f"decimal({qualifiers['precision'].i32Value},{qualifiers['scale'].i32Value})"
                except (KeyError, AttributeError):
                    raise ClusterError("unsupported_result_type", "HS2 DECIMAL metadata omitted precision or scale") from None
            elif name in {"varchar", "char"}:
                name = "string"
            elif name == "timestamplocaltz":
                name = "timestamp_ltz"
            qualifier = qualifiers.get("typeName")
            if qualifier is not None and getattr(qualifier, "stringValue", "") == "timestamp_ntz":
                name = "timestamp_ntz"
            columns.append({"name": column.columnName, "type": name})
        return columns

    @staticmethod
    def _rows(rowset):
        if getattr(rowset, "binaryColumns", None):
            raise ClusterError("unsupported_result_type", "HS2 binary column encoding is not enabled")
        fields = ("boolVal", "byteVal", "i16Val", "i32Val", "i64Val", "doubleVal", "stringVal", "binaryVal")
        if getattr(rowset, "columns", None):
            arrays = []
            for column in rowset.columns:
                values = [getattr(column, f, None) for f in fields if getattr(column, f, None) is not None]
                if len(values) != 1:
                    raise ClusterError("cluster_protocol_error", "Invalid HS2 column union")
                value = values[0]
                nulls = value.nulls or b""
                arrays.append([None if i // 8 < len(nulls) and (nulls[i // 8] >> (i % 8)) & 1 else v
                               for i, v in enumerate(value.values)])
            if len({len(a) for a in arrays}) != 1:
                raise ClusterError("cluster_protocol_error", "HS2 columns have different lengths")
            return [list(r) for r in zip(*arrays)]
        rows = []
        for row in getattr(rowset, "rows", None) or []:
            values = []
            for column in row.colVals:
                union = [getattr(column, f, None) for f in fields if getattr(column, f, None) is not None]
                if len(union) != 1:
                    raise ClusterError("cluster_protocol_error", "Invalid HS2 row union")
                values.append(union[0].value)
            rows.append(values)
        return rows

    def fetch(self, handle, max_rows):
        req = self.types.TFetchResultsReq(operationHandle=self._handle(handle),
                                          orientation=self.types.TFetchOrientation.FETCH_NEXT, maxRows=max_rows)
        result = self._rpc("FetchResults", req)
        return self._rows(result.results), bool(result.hasMoreRows)

    def cancel(self, handle):
        self._rpc("CancelOperation", self.types.TCancelOperationReq(operationHandle=self._handle(handle)))

    def close_operation(self, handle):
        self._rpc("CloseOperation", self.types.TCloseOperationReq(operationHandle=self._handle(handle)))

    def close(self):
        # Closing a socket/session is never used to assert operation termination.
        if self.transport is not None:
            self.transport.close()
            self.transport = self.client = None


class HiveDataSource(ClusterDataSource):
    kind, query_dialect = "hive", "hive"
    protocol = "hiveserver2-thrift-v6"

    def __init__(self, config, **kwargs):
        super().__init__(config, **kwargs)
        self._transport = None

    def get_capabilities(self):
        return {**super().get_capabilities(), "driver": "PyHive==" + PYHIVE_VERSION,
                "transport": "binary-thrift/TLS; LDAP uses SASL PLAIN inside TLS",
                "server_timeout": "requested via ExecuteStatement.queryTimeout; enforcement unverified",
                "timestamp_semantics": "timestamp is timezone-free; local-time-zone values require UTC session configuration"}

    def _session_configuration(self):
        return {"use:database": self.config["schema"], "hive.local.time.zone": "UTC"}

    def _preflight(self):
        super()._preflight()
        if self.transport_factory is None:
            PyHiveHS2Driver.check_dependencies(self.config)

    def _driver(self):
        if self._transport is None:
            self._transport = (self.transport_factory(self.config) if self.transport_factory else
                               PyHiveHS2Driver(self.config, credential_resolver=self._credential,
                                               session_configuration=self._session_configuration()))
        return self._transport

    def _submit(self, record):
        driver = self._driver()
        driver.open_session()
        claimed = self.store.update(record["query_id"], claim_submit=True)
        if claimed["submission_phase"] != "sending":
            raise ClusterError("query_cancelled", "Query was cancelled before transport submission")
        try:
            handle = driver.execute(record["sql"], record["budget"]["max_run_seconds"])
        except ClusterError as exc:
            if exc.code in {"cluster_permission_denied", "cluster_query_failed"}:
                self.store.update(record["query_id"], state="failed", remote_state="failed", error_code=exc.code, error=str(exc))
            raise
        try:
            native = uuid.UUID(bytes=base64.b64decode(handle["guid"], validate=True)).hex
            if not handle["secret"] or not handle["has_result_set"]:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ClusterError("cluster_protocol_error", "HS2 returned no usable result operation") from None
        self.store.update(record["query_id"], state="running", remote_state="running", native_query_id=native,
                          driver_state={"operation_handle": handle})
        self._poll(self.store.get(record["query_id"]))

    def _apply_status(self, record, status):
        state = status.get("state")
        if state == "FINISHED_STATE":
            remote = "succeeded"
        elif state == "CANCELED_STATE":
            remote = "cancelled"
        elif state in {"ERROR_STATE", "TIMEDOUT_STATE"}:
            remote = "failed"
        elif state in {"INITIALIZED_STATE", "PENDING_STATE", "RUNNING_STATE"}:
            remote = "running"
        else:
            raise ClusterError("remote_state_unknown", "HS2 has not proved a terminal operation state")
        changes = {"remote_state": remote}
        if remote == "cancelled":
            changes.update(state="cancelled", complete=False, cancel_confirmed=True,
                           error_code=record["error_code"] or "query_cancelled", error=record["error"] or "HS2 cancelled the operation")
        elif remote == "failed":
            denied = str(status.get("sql_state", "")).startswith("28") or status.get("sql_state") == "42501"
            code = "budget_exceeded" if state == "TIMEDOUT_STATE" else "cluster_permission_denied" if denied else "cluster_query_failed"
            changes.update(state="failed", complete=False, error_code=code, error="HS2 failed the operation")
        elif remote == "succeeded" and record.get("cancel_requested"):
            changes.update(state="failed", complete=False, error_code=record["error_code"] or "result_discarded",
                           error=record["error"] or "Server finished before cancellation was confirmed; the result was discarded")
        elif record["state"] not in {"fetching", "cancel_pending", "failed", "succeeded"}:
            changes["state"] = "running"
        self.store.update(record["query_id"], **changes)

    def _poll(self, record):
        handle = record["driver_state"].get("operation_handle")
        if not handle:
            raise ClusterError("remote_state_unknown", "No HS2 operation handle is available")
        self._apply_status(record, self._driver().status(handle))

    def _cancel(self, record):
        self._driver().cancel(record["driver_state"]["operation_handle"])
        self._poll(self.store.get(record["query_id"]))

    def _columns(self, columns):
        return columns

    def _pages(self, record, *, cancel=None):
        while True:
            self._check_active(record, cancel=cancel)
            current = self.store.get(record["query_id"])
            if current["remote_state"] == "succeeded":
                break
            if current["remote_state"] in {"failed", "cancelled"}:
                raise ClusterError(current["error_code"] or "cluster_query_failed", "HS2 did not complete the result")
            self._poll(current)
            if self.store.get(record["query_id"])["remote_state"] == "running":
                threading.Event().wait(0.05)
        handle = current["driver_state"]["operation_handle"]
        columns = self._columns(self._driver().columns(handle))
        self._arrow_schema(columns)
        while True:
            self._check_active(record, cancel=cancel)
            rows, more = self._driver().fetch(handle, self.config["batch_rows"])
            # A controlled/custom driver must obey the same page budget. The
            # native path enforces it before decoding, including metadata.
            import json
            encoded = json.dumps(rows, default=str, ensure_ascii=False).encode("utf-8")
            if len(encoded) > self.config["max_response_bytes"] or len(rows) > self.config["batch_rows"]:
                raise ClusterError("cluster_frame_too_large", "HS2 fetch page exceeded its byte or row budget")
            yield rows, columns
            if not rows:
                if more:
                    raise ClusterError("cluster_protocol_error", "Finished HS2 operation returned an empty nonterminal page")
                break
            # Do not rely on historically inconsistent hasMoreRows flags: ask
            # for the terminal empty page, without retaining previous pages.
        try:
            self._driver().close_operation(handle)
        except Exception:
            self.store.update(record["query_id"], operation_cleanup_pending=True)

    def _convert(self, value, arrow_type):
        # HS2 local-time-zone text is interpreted only in the fixed UTC session.
        if getattr(arrow_type, "tz", None) and value is not None:
            if isinstance(value, str):
                if not re.search(r"\.\d{7,}", value):
                    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
                    value = parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed
            elif isinstance(value, dt.datetime) and value.tzinfo is None:
                value = value.replace(tzinfo=dt.timezone.utc)
        return super()._convert(value, arrow_type)

    def close(self):
        if self._transport is not None:
            self._transport.close()
        super().close()
