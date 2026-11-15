"""Exercise the real HS2 driver/RPC path with controlled generated-type peers."""
from __future__ import annotations

import base64
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from data.cluster_connections import ClusterError, normalize_config
from data.cluster_query_store import ClusterQueryStore, public_query
from data.sources.hive import (BoundedThriftTransport, HiveDataSource, PyHiveHS2Driver,
                               _SocketStream)
from data.sources.spark_sql import SparkSQLDataSource
from data.sources.trino import BoundedTrinoHTTP, _BoundedHTTPResponse


def config(kind="hive", **overrides):
    return normalize_config({"kind": kind, "host": "controlled.example", "schema": "sales",
                             "allowed_tables": ["orders"], **overrides})


class Raw:
    def __init__(self, cfg=None):
        self.opened, self.reads, self.writes = False, [], []

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False

    def isOpen(self):
        return self.opened

    def read(self, size):
        self.reads.append(size)
        return b"x" * size

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        pass

    def set_timeout(self, value):
        self.timeout = value


class Protocol:
    def __init__(self, trans, **limits):
        self.trans, self.limits = trans, limits


def constructor(**kwargs):
    return types.SimpleNamespace(**kwargs)


def wire_types():
    request_names = ("TOpenSessionReq", "TExecuteStatementReq", "TGetOperationStatusReq", "TGetResultSetMetadataReq",
                     "TFetchResultsReq", "TCancelOperationReq", "TCloseOperationReq", "THandleIdentifier", "TOperationHandle")
    result = types.SimpleNamespace(**{name: constructor for name in request_names})
    result.TStatusCode = types.SimpleNamespace(SUCCESS_STATUS=0, SUCCESS_WITH_INFO_STATUS=1, INVALID_HANDLE_STATUS=4)
    result.TProtocolVersion = types.SimpleNamespace(HIVE_CLI_SERVICE_PROTOCOL_V6=5)
    result.TOperationState = types.SimpleNamespace(_VALUES_TO_NAMES={2: "FINISHED_STATE", 3: "CANCELED_STATE"})
    result.TTypeId = types.SimpleNamespace(_VALUES_TO_NAMES={15: "DECIMAL_TYPE", 8: "TIMESTAMP_TYPE"})
    result.TFetchOrientation = types.SimpleNamespace(FETCH_NEXT=0)
    return result


class Peer:
    def __init__(self):
        self.calls, self.protocols, self.fetches = [], [], 0
        self.state, self.response_bytes, self.protocol_version = 2, 4, 5
        self.handle = constructor(operationId=constructor(guid=bytes(range(16)), secret=b"private-wire-token"),
                                  operationType=0, hasResultSet=True)

    def client(self, protocol):
        self.protocol = protocol
        self.protocols.append(protocol)
        return self

    def response(self, name, request, **fields):
        self.calls.append((name, request))
        self.protocol.trans.write(b"rpc")
        self.protocol.trans.flush()
        self.protocol.trans.readAll(self.response_bytes)
        return constructor(status=constructor(statusCode=0), **fields)

    def OpenSession(self, request):
        return self.response("OpenSession", request, sessionHandle=constructor(sessionId="session"), serverProtocolVersion=self.protocol_version)

    def ExecuteStatement(self, request):
        return self.response("ExecuteStatement", request, operationHandle=self.handle)

    def GetOperationStatus(self, request):
        return self.response("GetOperationStatus", request, operationState=self.state, sqlState="")

    def GetResultSetMetadata(self, request):
        decimal = constructor(type=15, typeQualifiers=constructor(qualifiers={"precision": constructor(i32Value=20),
                                                                             "scale": constructor(i32Value=4)}))
        timestamp = constructor(type=8, typeQualifiers=None)
        columns = [constructor(columnName=name, typeDesc=constructor(types=[constructor(primitiveEntry=primitive)]))
                   for name, primitive in (("amount", decimal), ("at", timestamp))]
        return self.response("GetResultSetMetadata", request, schema=constructor(columns=columns))

    def FetchResults(self, request):
        self.fetches += 1
        values = (["1234567890123456.0001", "0"] if self.fetches == 1 else [])
        timestamps = (["2026-11-14 09:00:00", "2026-11-14 10:00:00"] if self.fetches == 1 else [])
        columns = [constructor(stringVal=constructor(values=values, nulls=b"\x02")),
                   constructor(stringVal=constructor(values=timestamps, nulls=b"\x00"))]
        return self.response("FetchResults", request, results=constructor(columns=columns, rows=None, binaryColumns=None), hasMoreRows=False)

    def CancelOperation(self, request):
        self.state = 3
        return self.response("CancelOperation", request)

    def CloseOperation(self, request):
        return self.response("CloseOperation", request)


class HS2WireTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cluster-wire-", dir=os.environ.get("BAA_DATA_DIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        self.peer = Peer()
        fake_service = types.ModuleType("TCLIService")
        fake_service.TCLIService = types.SimpleNamespace(Client=self.peer.client)
        fake_service.ttypes = wire_types()
        fake_protocol = types.ModuleType("thrift.protocol.TBinaryProtocol")
        fake_protocol.TBinaryProtocol = Protocol
        patch.dict(sys.modules, {"TCLIService": fake_service, "thrift.protocol.TBinaryProtocol": fake_protocol}).start()
        patch.object(PyHiveHS2Driver, "check_dependencies").start()
        self.raws = []
        def raw_factory(cfg):
            value = Raw(cfg)
            self.raws.append(value)
            return value
        patch("data.sources.hive._SocketStream", side_effect=raw_factory).start()

    def driver(self, cfg):
        return PyHiveHS2Driver(cfg, credential_resolver=lambda: "controlled-secret",
                              session_configuration={"use:database": "sales", "spark.sql.session.timeZone": "UTC"})

    def test_driver_constructs_real_operations_metadata_fetch_cancel(self):
        for kind, cls in (("hive", HiveDataSource), ("spark_sql", SparkSQLDataSource)):
            with self.subTest(kind=kind):
                self.peer.fetches, self.peer.state = 0, 2
                cfg = config(kind)
                driver = self.driver(cfg)
                self.addCleanup(driver.close)
                store = ClusterQueryStore(self.root / kind, owner_id="alice", session_id="s", workspace_id="w")
                source = cls(cfg, query_store=store, transport_factory=lambda _: driver)
                query = source.submit_query("SELECT amount FROM orders")
                batches = list(source.fetch_batches(query["query_id"]))
                self.assertEqual(batches[0].column(0)[0].as_py(), Decimal("1234567890123456.0001"))
                self.assertIsNone(batches[0].column(0)[1].as_py())
                self.assertEqual(bool(batches[0].schema.field("at").type.tz), kind == "spark_sql")
                self.assertTrue(source.query_status(query["query_id"])["complete"])
                request = next(request for name, request in reversed(self.peer.calls) if name == "ExecuteStatement")
                self.assertTrue(request.runAsync)
                self.assertEqual(request.queryTimeout, 120)
                self.assertEqual(request.confOverlay, {})
                self.assertEqual(request.statement, "SELECT amount FROM orders")
                self.assertEqual(self.peer.fetches, 2)
                self.assertEqual(self.peer.protocols[-1].limits["string_length_limit"], cfg["max_response_bytes"])
                private_handle = store.get(query["query_id"])["driver_state"]["operation_handle"]
                driver.cancel(private_handle)
                self.assertEqual(driver.status(private_handle)["state"], "CANCELED_STATE")
                cancel = next(request for name, request in reversed(self.peer.calls) if name == "CancelOperation")
                self.assertEqual(cancel.operationHandle.operationId.secret, b"private-wire-token")
                self.assertNotIn(private_handle["secret"], json.dumps(public_query(store.get(query["query_id"]))))

    def test_whole_rpc_budget_resets_and_blocks_large_frame_before_read(self):
        cfg = config(max_response_bytes=1024)
        driver = self.driver(cfg)
        self.addCleanup(driver.close)
        driver.open_session()
        handle = driver.execute("SELECT 1", 1)
        driver.status(handle)
        self.assertEqual(self.raws[-1].reads, [4, 4, 4])
        self.peer.response_bytes = 1025
        with self.assertRaises(ClusterError) as caught:
            driver.status(handle)
        self.assertEqual(caught.exception.code, "cluster_frame_too_large")
        self.assertEqual(self.raws[-1].reads, [4, 4, 4])
        self.assertFalse(self.raws[-1].opened)
        self.assertIsNone(driver.client)

    def test_protocol_negotiation_must_be_v6(self):
        self.peer.protocol_version = 9
        driver = self.driver(config())
        self.addCleanup(driver.close)
        with self.assertRaises(ClusterError) as caught:
            driver.open_session()
        self.assertEqual(caught.exception.code, "unsupported_capability")
        self.assertIsNone(driver.session)
        self.assertEqual([name for name, _ in self.peer.calls], ["OpenSession"])

    def test_reconnect_status_uses_saved_handle_without_new_session_or_execute(self):
        driver = self.driver(config())
        self.addCleanup(driver.close)
        driver.open_session()
        handle = driver.execute("SELECT 1", 1)
        driver.close()
        before = len(self.peer.calls)
        self.assertEqual(driver.status(handle)["state"], "FINISHED_STATE")
        self.assertEqual([name for name, _ in self.peer.calls[before:]], ["GetOperationStatus"])
        self.assertEqual(len(self.raws), 2)

    def test_ldap_uses_pinned_pure_sasl_inside_two_byte_guards(self):
        compat = types.ModuleType("pyhive.sasl_compat")
        credentials = []
        compat.PureSASLClient = lambda host, **kwargs: credentials.append((host, kwargs)) or object()
        sasl_module = types.ModuleType("thrift_sasl")
        class SASL:
            def __init__(self, factory, mechanism, transport):
                self.factory, self.mechanism, self.transport = factory, mechanism, transport
            def open(self):
                self.transport.open()
                self.factory()
            def close(self):
                self.transport.close()
            def isOpen(self):
                return self.transport.isOpen()
            def read(self, size):
                return self.transport.read(size)
            def write(self, value):
                self.transport.write(value)
            def flush(self):
                self.transport.flush()
        sasl_module.TSaslClientTransport = SASL
        with patch.dict(sys.modules, {"pyhive.sasl_compat": compat, "thrift_sasl": sasl_module}):
            driver = self.driver(config(auth="ldap", credential_ref="controlled-reference"))
            self.addCleanup(driver.close)
            driver.open_session()
            self.assertEqual(len(driver.guards), 2)
            self.assertEqual(credentials[0][1]["mechanism"], "PLAIN")
            self.assertEqual(credentials[0][1]["password"], "controlled-secret")
            self.assertTrue(self.raws[-1].opened)

    def test_native_socket_requires_default_verified_tls_and_hostname(self):
        # Use the actual socket wrapper, isolated from the patched driver peer.
        socket_stream_type = _SocketStream
        raw = types.SimpleNamespace(close=lambda: None)
        wrapped = types.SimpleNamespace(close=lambda: None)
        context = types.SimpleNamespace(wrap_socket=lambda sock, **kwargs: wrapped)
        with patch("data.sources.hive.socket.create_connection", return_value=raw), \
             patch("data.sources.hive.ssl.create_default_context", return_value=context) as create_context, \
             patch.object(context, "wrap_socket", wraps=context.wrap_socket) as wrap:
            stream = socket_stream_type(config())
            stream.open()
            self.assertEqual(create_context.call_count, 1)
            self.assertEqual(wrap.call_args.kwargs, {"server_hostname": "controlled.example"})
            self.assertIs(stream.handle, wrapped)
            stream.close()

    def test_http_wire_cap_includes_status_headers_and_body(self):
        wire = b'HTTP/1.1 200 OK\r\nContent-Length: 8\r\nX-Test: header\r\n\r\n{"id":1}'
        def response(maximum):
            sock = types.SimpleNamespace(makefile=lambda *a: io.BytesIO(wire), settimeout=lambda value: None)
            import time
            return _BoundedHTTPResponse(sock, maximum=maximum, deadline=time.monotonic() + 5)
        result = response(len(wire))
        result.begin()
        reader = result.fp
        self.assertEqual(result.read1(1024), b'{"id":1}')
        self.assertEqual(reader.count, len(wire))
        result.close()
        with self.assertRaises(ClusterError) as caught:
            result = response(len(wire) - 1)
            try:
                result.begin()
                result.read1(1024)
            finally:
                result.close()
        self.assertEqual(caught.exception.code, "cluster_frame_too_large")
        with self.assertRaises(ClusterError):
            result = response(10)
            try:
                result.begin()
            finally:
                result.close()

    def test_chunked_wire_cap_includes_chunk_headers_and_trailers(self):
        wire = b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8\r\n{"id":1}\r\n0\r\nX-Trailer: value\r\n\r\n'
        import time
        sock = types.SimpleNamespace(makefile=lambda *a: io.BytesIO(wire), settimeout=lambda value: None)
        result = _BoundedHTTPResponse(sock, maximum=len(wire), deadline=time.monotonic() + 5)
        result.begin()
        reader = result.fp
        self.assertEqual(result.read1(1024), b'{"id":1}')
        self.assertEqual(result.read1(1024), b'')
        self.assertEqual(reader.count, len(wire))
        result.close()

    def test_chunked_http_page_cap_is_cumulative(self):
        sizes = []
        class Response:
            status = 200
            def close(self):
                pass
            def getheader(self, name):
                return None
            def read1(self, size):
                sizes.append(size)
                return b"x" * size
        closed = []
        connection = types.SimpleNamespace(sock=None, request=lambda *a, **k: None,
                                          getresponse=lambda: Response(), close=lambda: closed.append(True))
        with patch("data.sources.trino.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaises(ClusterError) as caught:
                BoundedTrinoHTTP().request("GET", "https://controlled.example/v1/query/id", headers={}, max_bytes=1024)
        self.assertEqual(caught.exception.code, "cluster_frame_too_large")
        self.assertEqual(sizes, [1025])
        self.assertTrue(closed)

    def test_transport_deadline_and_partial_reads_are_bounded(self):
        raw = Raw()
        bounded = BoundedThriftTransport(raw, 1024, 1)
        with patch("data.sources.hive.time.monotonic", return_value=bounded.deadline + 1):
            with self.assertRaises(ClusterError) as caught:
                bounded.readAll(1)
        self.assertEqual(caught.exception.code, "remote_state_unknown")
        self.assertEqual(raw.reads, [])
        raw.read = lambda size: b"x" * min(size, 7)
        bounded.begin()
        self.assertEqual(len(bounded.readAll(20)), 20)
        self.assertEqual(bounded.read_bytes, 20)


if __name__ == "__main__":
    unittest.main()
