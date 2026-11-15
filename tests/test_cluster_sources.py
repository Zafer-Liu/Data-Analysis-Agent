"""Controlled Trino/HS2 tests; no server, sockets, credentials or live cluster."""
from __future__ import annotations

import base64
import datetime as dt
from decimal import Decimal
import importlib.metadata
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from data.cluster_connections import (ClusterConnectionStore, ClusterError, credential_label,
                                      normalize_config, public_connection)
from data.cluster_query_store import ClusterQueryStore, public_query
from data.sources.cluster_base import query_budget
from data.sources.hive import BoundedThriftTransport, HiveDataSource, PyHiveHS2Driver
from data.sources.spark_sql import SparkSQLDataSource
from data.sources.trino import BoundedTrinoHTTP, TrinoDataSource

NATIVE = "20261114_00001_controlled"
ORIGIN = "https://cluster.example:443"
NEXT = ORIGIN + "/v1/statement/executing/" + NATIVE + "/token/1"


def config(kind="trino", **changes):
    cfg = {"kind": kind, "host": "cluster.example", "schema": "sales", "allowed_tables": ["orders"],
           "tls": True, "batch_rows": 2}
    if kind == "trino":
        cfg["catalog"] = "warehouse"
    cfg.update(changes)
    return cfg


def page(rows=None, columns=None, *, next_uri="", state="FINISHED", **changes):
    value = {"id": NATIVE, "stats": {"state": state}, "data": rows or [],
             "columns": columns or [{"name": "x", "type": "bigint"}]}
    if next_uri:
        value["nextUri"] = next_uri
    return {**value, **changes}


class HTTP:
    def __init__(self, *steps):
        self.steps, self.calls = list(steps), []

    def request(self, method, uri, **kwargs):
        self.calls.append((method, uri, kwargs))
        if not self.steps:
            raise AssertionError("Unexpected controlled HTTP request")
        expected, result = self.steps.pop(0)
        if method != expected:
            raise AssertionError(f"Expected {expected}, got {method}")
        if isinstance(result, Exception):
            raise result
        if isinstance(result, tuple):
            return result
        return 200, {}, json.dumps(result).encode()


class HS2:
    """Controlled operation driver; state and rows change only as instructed."""
    def __init__(self, columns=None, pages=None, states=None, *, cancel_state="CANCELED_STATE"):
        self.column_values = columns or [{"name": "x", "type": "bigint"}]
        self.pages = list(pages if pages is not None else [[[1]], []])
        self.states = list(states or ["FINISHED_STATE"])
        self.current = self.states[0]
        self.cancel_state = cancel_state
        self.calls, self.closed = [], False
        self.handle = {"guid": base64.b64encode(bytes(range(16))).decode(),
                       "secret": base64.b64encode(b"private-operation-token").decode(),
                       "operation_type": 0, "has_result_set": True}

    def open_session(self):
        self.calls.append(("OpenSession",))

    def execute(self, sql, timeout_seconds):
        self.calls.append(("ExecuteStatement", sql, timeout_seconds))
        return self.handle

    def status(self, handle):
        self.calls.append(("GetOperationStatus", handle))
        if self.states:
            self.current = self.states.pop(0)
        if isinstance(self.current, Exception):
            raise self.current
        return {"state": self.current}

    def columns(self, handle):
        self.calls.append(("GetResultSetMetadata", handle))
        return self.column_values

    def fetch(self, handle, max_rows):
        self.calls.append(("FetchResults", handle, max_rows))
        value = self.pages.pop(0)
        if isinstance(value, Exception):
            raise value
        return value, False  # Verify an extra empty fetch, even when this hint is false.

    def cancel(self, handle):
        self.calls.append(("CancelOperation", handle))
        self.states = [self.cancel_state]

    def close_operation(self, handle):
        self.calls.append(("CloseOperation", handle))

    def close(self):
        self.closed = True


class Catalog:
    """Real shared catalog with controlled publication failure injection."""
    def __init__(self, root, *, owner_id="alice", session_id="s", workspace_id="w"):
        from data.dataset_catalog import DatasetCatalog
        self.catalog = DatasetCatalog(root, owner_id=owner_id, session_id=session_id, workspace_id=workspace_id)
        self.published = 0
        self.fail_publish = False

    def __getattr__(self, name):
        return getattr(self.catalog, name)

    def publish(self, manifest, *, idempotency_key, expected_version=None, **publication_options):
        if self.fail_publish:
            raise OSError("controlled disk failure")
        result = self.catalog.publish(manifest, idempotency_key=idempotency_key, expected_version=expected_version, **publication_options)
        self.published += 1
        return result


class ClusterTests(unittest.TestCase):
    def setUp(self):
        root = os.environ.get("BAA_DATA_DIR")
        self.temp = tempfile.TemporaryDirectory(prefix="controlled-cluster-", dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ClusterQueryStore(self.root / "state", owner_id="alice", session_id="s", workspace_id="w")

    def source(self, protocol, *, kind="trino", cfg=None, budget=None, resolver=None):
        cls = {"trino": TrinoDataSource, "hive": HiveDataSource, "spark_sql": SparkSQLDataSource}[kind]
        source = cls(cfg or config(kind), query_store=self.store, connection_id="connection1", budget=budget,
                     credential_resolver=resolver, transport_factory=lambda _: protocol)
        self.addCleanup(source.close)
        return source

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(ClusterError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_strict_target_dialects_return_original_sql(self):
        sql = "  WITH selected AS (SELECT x FROM orders) SELECT SUM(x) AS total FROM selected  "
        for kind in ("trino", "hive", "spark_sql"):
            with self.subTest(kind=kind):
                source = self.source(HTTP() if kind == "trino" else HS2(), kind=kind)
                self.assertEqual(source.validate_query(sql), sql)
                self.assertEqual(source.query_dialect, {"trino": "trino", "hive": "hive", "spark_sql": "spark"}[kind])
                self.assertTrue(source.get_capabilities()["batch_reader"])
                for invalid in ("SELECT * FROM other", "SELECT * FROM stolen.sales.orders",
                                "WITH hidden AS (SELECT * FROM stolen) SELECT * FROM orders",
                                "SET x = 1", "DELETE FROM orders", "SELECT 1; SELECT 2",
                                "SELECT read_csv('https://evil.example/data')", "SELECT * FROM read_parquet('s3://bucket/file')",
                                "SELECT system.run(x) FROM orders", "SELECT reflect('java.lang.Runtime', 'exec')",
                                "SELECT * FROM orders FOR UPDATE", "SELECT /*+ BROADCAST(orders) */ * FROM orders",
                                "SELECT * FROM orders WHERE (", "SELECT * FROM orders JOIN other ON 1=1"):
                    with self.subTest(sql=invalid), self.assertRaises(ClusterError):
                        source.validate_query(invalid)

    def test_function_allowlist_can_be_tightened(self):
        source = self.source(HTTP(), cfg=config(allowed_functions=["COUNT"]))
        self.assertEqual(source.validate_query("SELECT COUNT(*) FROM orders"), "SELECT COUNT(*) FROM orders")
        self.assert_code("cluster_function_denied", source.validate_query, "SELECT SUM(x) FROM orders")

    def test_trino_standard_pages_and_backpressure(self):
        http = HTTP(("POST", page([[1]], next_uri=NEXT, state="RUNNING")), ("GET", page([[2]])))
        source = self.source(http)
        query = source.submit_query("SELECT x FROM orders", idempotency_key="one")
        self.assertEqual(query["native_query_id"], NATIVE)
        batches = source.fetch_batches(query["query_id"])
        self.assertEqual(next(batches).to_pylist(), [{"x": 1}])
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(next(batches).to_pylist(), [{"x": 2}])
        with self.assertRaises(StopIteration):
            next(batches)
        status = source.query_status(query["query_id"])
        self.assertTrue(status["complete"])
        self.assertEqual(status["result_rows"], 2)
        self.assertEqual(http.calls[1][1], NEXT)
        self.assertIn("query_max_run_time=120s", http.calls[0][2]["headers"]["X-Trino-Session"])

    def test_trino_http_200_business_error(self):
        secret = "super-secret-dont-return"
        http = HTTP(("POST", page(error={"errorName": "ACCESS_DENIED", "message": secret})))
        source = self.source(http, cfg=config(auth="jwt", credential_ref="test-reference"), resolver=lambda _: secret)
        exc = self.assert_code("cluster_permission_denied", source.submit_query, "SELECT x FROM orders", idempotency_key="denied")
        record = self.store.get(exc.details["query_id"])
        self.assertEqual(record["remote_state"], "failed")
        self.assertEqual(record["native_query_id"], NATIVE)
        self.assertNotIn(secret, json.dumps(public_query(record)))
        self.assertNotIn(secret, json.dumps(self.store.events(record["query_id"])))
        self.assertEqual(http.calls[0][2]["headers"]["Authorization"], "Bearer " + secret)

    def test_untrusted_next_uri_never_receives_credentials(self):
        for uri in ("https://evil.example/v1/statement/executing/" + NATIVE + "/token/1",
                    ORIGIN + "/v1/statement/executing/another_query/token/1",
                    "http://cluster.example:443/v1/statement/" + NATIVE,
                    ORIGIN + "/v1/statement/" + NATIVE + "/%2e%2e/admin",
                    "https://user:pass@cluster.example/v1/statement/" + NATIVE,
                    ORIGIN + "/v1/statement/" + NATIVE + "?redirect=https://evil.example"):
            with self.subTest(uri=uri):
                http = HTTP(("POST", page([[1]], next_uri=uri, state="RUNNING")))
                source = self.source(http, cfg=config(auth="basic", credential_ref="opaque"), resolver=lambda _: "secret")
                exc = self.assert_code("cluster_untrusted_uri", source.submit_query, "SELECT x FROM orders")
                self.assertEqual(len(http.calls), 1)
                self.assertEqual(self.store.get(exc.details["query_id"])["state"], "remote_state_unknown")

    def test_redirect_is_rejected_without_follow(self):
        http = HTTP(("POST", (307, {"Location": "https://evil.example"}, b"")))
        source = self.source(http)
        self.assert_code("cluster_untrusted_uri", source.submit_query, "SELECT 1")
        self.assertEqual(len(http.calls), 1)

    def test_unknown_submission_replay_is_not_resubmitted(self):
        http = HTTP(("POST", TimeoutError("password may have reached remote")))
        source = self.source(http)
        exc = self.assert_code("remote_state_unknown", source.submit_query, "SELECT x FROM orders", idempotency_key="lost")
        same = source.submit_query("SELECT x FROM orders", idempotency_key="lost")
        self.assertEqual(same["query_id"], exc.details["query_id"])
        self.assertEqual(same["state"], "remote_state_unknown")
        self.assertEqual(len(http.calls), 1)
        self.assert_code("idempotency_conflict", source.submit_query, "SELECT 1", idempotency_key="lost")
        self.assertEqual(source.cancel_query(same["query_id"])["state"], "cancel_pending")

    def test_idempotency_survives_new_source_instance(self):
        http = HTTP(("POST", page([[1]])))
        first = self.source(http)
        query = first.submit_query("SELECT x FROM orders", idempotency_key="restart")
        second = self.source(HTTP())
        self.assertEqual(second.submit_query("SELECT x FROM orders", idempotency_key="restart")["query_id"], query["query_id"])
        self.assertEqual(len(http.calls), 1)

    def test_cancel_during_preflight_prevents_any_protocol_submission(self):
        for kind in ("trino", "hive", "spark_sql"):
            with self.subTest(kind=kind):
                protocol = HTTP() if kind == "trino" else HS2()
                source = self.source(protocol, kind=kind)
                key = "cancel-preflight-" + kind
                def preflight():
                    query_id = self.store.list(idempotency_key=key)[0]["query_id"]
                    source.cancel_query(query_id)
                with patch.object(source, "_preflight", side_effect=preflight):
                    exc = self.assert_code("query_cancelled", source.submit_query, "SELECT 1", idempotency_key=key)
                record = self.store.get(exc.details["query_id"])
                self.assertEqual(record["remote_state"], "not_submitted")
                self.assertEqual(record["state"], "cancelled")
                self.assertFalse(record["cancel_confirmed"])
                self.assertEqual(protocol.calls, [])

    def test_cancel_during_submission_is_forwarded_when_native_identity_arrives(self):
        for kind in ("trino", "hive", "spark_sql"):
            with self.subTest(kind=kind):
                key = "cancel-flight-" + kind
                if kind == "trino":
                    protocol = HTTP(("POST", page(next_uri=NEXT, state="RUNNING")), ("DELETE", (204, {}, b"")),
                                    ("GET", {"queryId": NATIVE, "state": "FAILED", "errorCode": {"name": "USER_CANCELED"}}))
                else:
                    protocol = HS2(states=["RUNNING_STATE"])
                source = self.source(protocol, kind=kind)
                def intent():
                    query_id = self.store.list(idempotency_key=key)[0]["query_id"]
                    source.cancel_query(query_id)
                if kind == "trino":
                    original = protocol.request
                    def request(method, uri, **kwargs):
                        if method == "POST":
                            intent()
                        return original(method, uri, **kwargs)
                    protocol.request = request
                else:
                    original = protocol.execute
                    def execute(sql, timeout_seconds):
                        intent()
                        return original(sql, timeout_seconds)
                    protocol.execute = execute
                query = source.submit_query("SELECT 1", idempotency_key=key)
                self.assertEqual(query["state"], "cancelled")
                self.assertTrue(query["cancel_confirmed"])
                names = [call[0] for call in protocol.calls]
                self.assertEqual(names.count("POST" if kind == "trino" else "ExecuteStatement"), 1)
                self.assertIn("DELETE" if kind == "trino" else "CancelOperation", names)

    def test_trino_cancel_requires_server_terminal_ack(self):
        http = HTTP(("POST", page(next_uri=NEXT, state="RUNNING")), ("DELETE", (204, {}, b"")),
                    ("GET", {"queryId": NATIVE, "state": "RUNNING"}),
                    ("GET", {"queryId": NATIVE, "state": "FAILED", "errorCode": {"name": "USER_CANCELED"}}))
        source = self.source(http)
        query = source.submit_query("SELECT x FROM orders")
        result = source.cancel_query(query["query_id"])
        self.assertEqual(result["state"], "cancel_pending")
        self.assertFalse(result["cancel_confirmed"])
        result = source.query_status(query["query_id"])
        self.assertEqual(result["state"], "cancelled")
        self.assertTrue(result["cancel_confirmed"])
        self.assertEqual(http.calls[1][1], NEXT)

    def test_trino_cancel_unknown_and_late_finish_are_distinct(self):
        for final in ((404, {}, b""), {"queryId": NATIVE, "state": "FINISHED"}):
            with self.subTest(final=final):
                http = HTTP(("POST", page(next_uri=NEXT, state="RUNNING")), ("DELETE", (204, {}, b"")), ("GET", final))
                source = self.source(http)
                query = source.submit_query("SELECT x FROM orders")
                result = source.cancel_query(query["query_id"])
                self.assertFalse(result["cancel_confirmed"])
                self.assertFalse(result["complete"])
                self.assertEqual(result["state"], "cancel_pending" if isinstance(final, tuple) else "failed")
                self.assertEqual(result["remote_state"], "running" if isinstance(final, tuple) else "succeeded")

    def test_trino_row_and_byte_truncation_never_complete(self):
        for budget, rows in (({"max_rows": 1}, [[1], [2]]), ({"max_output_bytes": 4, "batch_bytes": 4}, [[1], [2]])):
            with self.subTest(budget=budget):
                source = self.source(HTTP(("POST", page(rows))))
                query = source.submit_query("SELECT x FROM orders", budget=budget)
                self.assert_code("budget_exceeded", lambda: list(source.fetch_batches(query["query_id"])))
                record = self.store.get(query["query_id"])
                self.assertFalse(record["complete"])
                self.assertEqual(record["remote_state"], "succeeded")
                self.assertEqual(record["state"], "failed")

    def test_trino_whole_http_page_cap(self):
        source = self.source(HTTP(("POST", (200, {}, b" " * 1025))), cfg=config(max_response_bytes=1024))
        self.assert_code("cluster_frame_too_large", source.submit_query, "SELECT 1")

    def test_native_http_reader_caps_before_large_body_read(self):
        class Response:
            status = 200
            def close(self):
                pass
            def getheader(self, name):
                return "5000"
            def getheaders(self):
                return []
            def read1(self, size):
                raise AssertionError("Oversized advertised body must not be read")
        connection = types.SimpleNamespace(request=lambda *a, **k: None, getresponse=lambda: Response(), close=lambda: None)
        with patch("data.sources.trino.http.client.HTTPSConnection", return_value=connection):
            self.assert_code("cluster_frame_too_large", BoundedTrinoHTTP().request, "GET", ORIGIN + "/v1/query/" + NATIVE,
                             headers={}, max_bytes=1024)

    def test_trino_schema_drift_is_rejected(self):
        source = self.source(HTTP(("POST", page([[1]], next_uri=NEXT, state="RUNNING")),
                                  ("GET", page([["two"]], [{"name": "x", "type": "varchar"}]))))
        query = source.submit_query("SELECT x FROM orders")
        self.assert_code("schema_drift", lambda: list(source.fetch_batches(query["query_id"])))
        self.assertFalse(self.store.get(query["query_id"])["complete"])

    def test_abandoned_generator_cancels_and_cannot_replay(self):
        http = HTTP(("POST", page([[1]], next_uri=NEXT, state="RUNNING")), ("DELETE", (204, {}, b"")),
                    ("GET", {"queryId": NATIVE, "state": "FAILED", "errorCode": {"name": "USER_CANCELED"}}))
        source = self.source(http)
        query = source.submit_query("SELECT x FROM orders")
        stream = source.fetch_batches(query["query_id"])
        next(stream)
        stream.close()
        self.assertFalse(self.store.get(query["query_id"])["complete"])
        self.assertTrue(self.store.get(query["query_id"])["cancel_confirmed"])
        with self.assertRaises(ClusterError):
            list(source.fetch_batches(query["query_id"]))
        self.assertEqual([c[0] for c in http.calls], ["POST", "DELETE", "GET"])

    def test_decimal_and_timezone_are_preserved(self):
        columns = [{"name": "amount", "type": "decimal(38,9)"}, {"name": "instant", "type": "timestamp(6) with time zone"},
                   {"name": "wall", "type": "timestamp(6)"}]
        exact = "12345678901234567890123456789.123456789"
        source = self.source(HTTP(("POST", page([[exact, "2026-11-14 09:30:00.123456+08:00", "2026-11-14 09:30:00.123456"]], columns))))
        query = source.submit_query("SELECT 1")
        batch = list(source.fetch_batches(query["query_id"]))[0]
        self.assertEqual(batch.column(0)[0].as_py(), Decimal(exact))
        self.assertEqual(batch.column(1)[0].as_py(), dt.datetime(2026, 11, 14, 1, 30, 0, 123456, tzinfo=dt.timezone.utc))
        self.assertIsNone(batch.column(2)[0].as_py().tzinfo)
        self.assertEqual(batch.schema.field("amount").type, pa.decimal128(38, 9))

    def test_iana_timezone_dst_ambiguity_requires_explicit_offset(self):
        for text in ("2026-11-01 01:30:00 America/New_York", "2026-03-08 02:30:00 America/New_York",
                     "2026-11-14 09:00:00+08:00 America/New_York"):
            with self.subTest(text=text):
                source = self.source(HTTP(("POST", page([[text]], [{"name": "x", "type": "timestamp with time zone"}]))))
                query = source.submit_query("SELECT 1")
                self.assert_code("unsupported_result_type", lambda: list(source.fetch_batches(query["query_id"])))
        source = self.source(HTTP(("POST", page([["2026-11-01 01:30:00-05:00 America/New_York"]],
                                                 [{"name": "x", "type": "timestamp with time zone"}]))))
        query = source.submit_query("SELECT 1")
        value = list(source.fetch_batches(query["query_id"]))[0].column(0)[0].as_py()
        self.assertEqual(value, dt.datetime(2026, 11, 1, 6, 30, tzinfo=dt.timezone.utc))

    def test_lossy_decimal_and_timestamp_precision_rejected(self):
        for typename, value in (("decimal(38,9)", 1.1), ("timestamp(9)", "2026-11-14 01:00:00.123456789"),
                                ("timestamp with time zone", "2026-11-14 01:00:00")):
            with self.subTest(typename=typename):
                source = self.source(HTTP(("POST", page([[value]], [{"name": "x", "type": typename}]))))
                query = source.submit_query("SELECT 1")
                self.assert_code("unsupported_result_type", lambda: list(source.fetch_batches(query["query_id"])))

    def test_hive_real_operation_contract_and_terminal_empty_fetch(self):
        driver = HS2(pages=[[[1], [2]], [[3]], []], states=["RUNNING_STATE", "FINISHED_STATE"])
        source = self.source(driver, kind="hive")
        query = source.submit_query("SELECT x FROM orders")
        self.assertEqual(query["native_query_id"], "000102030405060708090a0b0c0d0e0f")
        batches = source.fetch_batches(query["query_id"])
        self.assertEqual(next(batches).num_rows, 2)
        self.assertEqual(sum(c[0] == "FetchResults" for c in driver.calls), 1)
        self.assertEqual(sum(b.num_rows for b in batches), 1)
        self.assertTrue(source.query_status(query["query_id"])["complete"])
        self.assertEqual(sum(c[0] == "FetchResults" for c in driver.calls), 3)
        self.assertIn(("CloseOperation", driver.handle), driver.calls)
        self.assertNotIn(driver.handle["secret"], json.dumps(public_query(self.store.get(query["query_id"]))))

    def test_spark_has_distinct_dialect_session_and_timestamp_semantics(self):
        columns = [{"name": "ltz", "type": "timestamp"}, {"name": "ntz", "type": "timestamp_ntz"},
                   {"name": "amount", "type": "decimal(20,4)"}]
        driver = HS2(columns, [[["2026-11-14 09:00:00", "2026-11-14 09:00:00", "1000000000000000.0001"]], []])
        source = self.source(driver, kind="spark_sql")
        query = source.submit_query("SELECT 1")
        batch = list(source.fetch_batches(query["query_id"]))[0]
        self.assertEqual(source.query_dialect, "spark")
        self.assertEqual(source._session_configuration()["spark.sql.session.timeZone"], "UTC")
        self.assertEqual(source._session_configuration()["spark.sql.ansi.enabled"], "true")
        self.assertEqual(batch.schema.field("ltz").type, pa.timestamp("us", "UTC"))
        self.assertEqual(batch.schema.field("ntz").type, pa.timestamp("us"))
        self.assertEqual(batch.column(2)[0].as_py(), Decimal("1000000000000000.0001"))
        self.assertFalse(source.get_capabilities()["spark_connect"])

    def test_hive_and_spark_cancel_native_operation_or_keep_unknown(self):
        for kind in ("hive", "spark_sql"):
            for state in ("CANCELED_STATE", "FINISHED_STATE", "CLOSED_STATE", OSError("secret")):
                with self.subTest(kind=kind, state=state):
                    driver = HS2(states=["RUNNING_STATE"], cancel_state=state)
                    source = self.source(driver, kind=kind)
                    query = source.submit_query("SELECT x FROM orders")
                    result = source.cancel_query(query["query_id"])
                    self.assertIn(("CancelOperation", driver.handle), driver.calls)
                    self.assertEqual(result["cancel_confirmed"], state == "CANCELED_STATE")
                    self.assertEqual(result["state"], "cancelled" if state == "CANCELED_STATE" else "failed" if state == "FINISHED_STATE" else "cancel_pending")
                    self.assertNotIn("secret", result["error"])

    def test_hs2_transport_failure_does_not_resubmit(self):
        for kind in ("hive", "spark_sql"):
            driver = HS2()
            driver.execute = lambda *a: (_ for _ in ()).throw(OSError("driver password=secret"))
            source = self.source(driver, kind=kind)
            key = "lost-" + kind
            self.assert_code("remote_state_unknown", source.submit_query, "SELECT 1", idempotency_key=key)
            self.assertEqual(source.submit_query("SELECT 1", idempotency_key=key)["state"], "remote_state_unknown")
            self.assertEqual(sum(c[0] == "OpenSession" for c in driver.calls), 1)

    def test_hs2_fetch_failure_does_not_publish(self):
        for kind in ("hive", "spark_sql"):
            source = self.source(HS2(pages=[OSError("token=secret")]), kind=kind)
            query = source.submit_query("SELECT 1")
            catalog = Catalog(self.root / kind)
            self.assert_code("cluster_result_error", source.snapshot_result, query["query_id"], catalog=catalog)
            self.assertFalse(self.store.get(query["query_id"])["complete"])
            self.assertEqual(catalog.published, 0)

    def test_hs2_whole_page_and_single_field_caps(self):
        for kind in ("hive", "spark_sql"):
            source = self.source(HS2(columns=[{"name": "x", "type": "string"}], pages=[[["x" * 2048]], []]),
                                 kind=kind, cfg=config(kind, max_response_bytes=1024))
            query = source.submit_query("SELECT 1")
            self.assert_code("cluster_frame_too_large", lambda: list(source.fetch_batches(query["query_id"])))
        class Raw:
            def __init__(self):
                self.reads = []
            def read(self, size):
                self.reads.append(size)
                return b"x" * size
        raw = Raw()
        bounded = BoundedThriftTransport(raw, 1024, 1)
        self.assert_code("cluster_frame_too_large", bounded.readAll, 2**31 - 1)
        self.assertEqual(raw.reads, [])
        self.assertEqual(len(bounded.readAll(900)), 900)
        self.assert_code("cluster_frame_too_large", bounded.readAll, 200)
        bounded.begin()
        self.assertEqual(len(bounded.readAll(1024)), 1024)

    def test_hs2_missing_pinned_driver_is_clear_and_does_not_claim_ready(self):
        for cls, kind in ((HiveDataSource, "hive"), (SparkSQLDataSource, "spark_sql")):
            with patch("data.sources.hive.importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
                source = cls(config(kind), query_store=self.store)
                exc = self.assert_code("dependency_unavailable", source.submit_query, "SELECT 1")
                self.assertIn("cluster-hs2.txt", str(exc))
                self.assertEqual(self.store.get(exc.details["query_id"])["state"], "failed")

    def test_unsupported_hard_hs2_budgets_are_rejected_before_transport(self):
        for kind in ("hive", "spark_sql"):
            for budget in ({"max_scan_bytes": 1000}, {"max_cpu_seconds": 1}, {"strict_server": True}):
                driver = HS2()
                source = self.source(driver, kind=kind)
                self.assert_code("unsupported_budget", source.submit_query, "SELECT 1", budget=budget)
                self.assertEqual(driver.calls, [])
        source = self.source(HS2(), kind="hive")
        query = source.submit_query("SELECT 1")
        self.assertEqual(query["budget_policy"]["unavailable"], ["max_run_seconds"])

    def test_trino_hard_scan_property_and_unsupported_property_error(self):
        source = self.source(HTTP(("POST", page(error={"errorName": "INVALID_SESSION_PROPERTY", "message": "private"}))))
        exc = self.assert_code("unsupported_budget", source.submit_query, "SELECT 1", budget={"max_scan_bytes": 500, "max_cpu_seconds": 2})
        self.assertEqual(self.store.get(exc.details["query_id"])["remote_state"], "failed")
        header = source.http.calls[0][2]["headers"]["X-Trino-Session"]
        self.assertIn("query_max_scan_physical_bytes=500B", header)
        self.assertIn("query_max_cpu_time=2s", header)

    def test_budget_is_finite_and_cannot_relax_source_or_fetch(self):
        for budget in ({"max_run_seconds": float("nan")}, {"max_rows": True}, {"max_scan_bytes": -1}, {"env": {}}):
            self.assert_code("invalid_budget", query_budget, budget)
        source = self.source(HTTP(("POST", page([[1]]))), budget={"max_rows": 2})
        self.assert_code("invalid_budget", source.submit_query, "SELECT 1", budget={"max_rows": 3})
        query = source.submit_query("SELECT 1", budget={"max_rows": 1})
        self.assert_code("invalid_budget", lambda: list(source.fetch_batches(query["query_id"], budget={"max_rows": 2})))

    def test_query_owner_session_workspace_and_connection_are_frozen(self):
        source = self.source(HTTP(("POST", page([[1]]))))
        query = source.submit_query("SELECT 1")
        for owner, session, workspace in (("bob", "s", "w"), ("alice", "other", "w"), ("alice", "s", "other")):
            store = ClusterQueryStore(self.root / "state", owner_id=owner, session_id=session, workspace_id=workspace)
            self.assert_code("cluster_not_found", store.get, query["query_id"])
            self.assert_code("cluster_not_found", store.events, query["query_id"])
        other = self.source(HTTP(), cfg=config(host="other.example"))
        self.assert_code("cluster_permission_denied", other.query_status, query["query_id"])

    def test_events_are_bounded_ordered_replayable_and_scoped(self):
        source = self.source(HTTP(("POST", page([[1]]))))
        query = source.submit_query("SELECT 1")
        for i in range(1005):
            self.store.update(query["query_id"], error_code=str(i))
        events = self.store.events(query["query_id"], limit=200)
        self.assertEqual(len(events), 200)
        self.assertGreater(events[0]["seq"], 1)
        after = events[-1]["seq"]
        next_events = self.store.events(query["query_id"], after_seq=after, limit=1)
        self.assertEqual(next_events[0]["seq"], after + 1)
        self.assertEqual(self.store.event_window(query["query_id"])["last_seq"] - events[0]["seq"] + 1, 1000)

    def test_connections_scope_vault_reference_and_tls_boundaries(self):
        store = ClusterConnectionStore(self.root / "connections", owner_id="alice")
        ref = credential_label("alice") + ":" + "0" * 32
        record = store.save(config(auth="basic", credential_ref=ref))
        self.assertNotIn("credential_ref", json.dumps(public_connection(record)))
        other = ClusterConnectionStore(self.root / "connections", owner_id="bob")
        self.assert_code("cluster_not_found", other.get, record["connection_id"])
        self.assert_code("cluster_permission_denied", other.save, config(auth="basic", credential_ref=ref))
        self.assert_code("credential_boundary_changed", store.save, {**record["config"], "host": "other.example", "approved_endpoints": []},
                         connection_id=record["connection_id"])
        self.assert_code("unsupported_auth", normalize_config, config(auth="basic", credential_ref=ref, tls=False))
        for bad in ({"dsn": "jdbc:any"}, {"host": "https://bad.example"}, {"session_properties": {"x": "y"}}, {"auth": "kerberos"}):
            with self.assertRaises(ClusterError):
                normalize_config(config(**bad))

    def test_metadata_uses_authorized_list_without_count_or_cache(self):
        source = self.source(HTTP())
        self.assertEqual(source.list_tables(), ["warehouse.sales.orders"])
        self.assertIsNone(source.get_preview()[0]["total_rows"])
        self.assertEqual(source.http.calls, [])
        self.assert_code("cluster_table_denied", source.get_table_schema, "not_allowed")
        self.assertIsNone(source.writable_conn())

    def test_snapshot_streams_through_shared_import_and_preserves_scope_types(self):
        columns = [{"name": "amount", "type": "decimal(20,4)"}, {"name": "instant", "type": "timestamp with time zone"}]
        http = HTTP(("POST", page([["100.0001", "2026-11-14 01:00:00+00:00"]], columns, next_uri=NEXT, state="RUNNING")),
                    ("GET", page([["200.0002", "2026-11-14 02:00:00+00:00"]], columns)))
        source = self.source(http)
        query = source.submit_query("SELECT 1")
        catalog = Catalog(self.root / "catalog")
        # Publishing must never collect all remote pages into a pa.Table.
        with patch("data.sources.cluster_base.ClusterDataSource.execute_query", side_effect=AssertionError("legacy materialization")):
            manifest = source.snapshot_result(query["query_id"], catalog=catalog)
        self.assertEqual(manifest["row_count"], 2)
        self.assertEqual((manifest["owner_id"], manifest["session_id"], manifest["workspace_id"]), ("alice", "s", "w"))
        self.assertEqual(manifest["source"]["kind"], "cluster")
        self.assertTrue(manifest["lineage"]["cluster"]["complete"])
        file = catalog.snapshot_dir(manifest["snapshot_id"]) / manifest["files"][0]["relative_path"]
        table = pq.read_table(file)
        self.assertEqual(table.schema.field("amount").type, pa.decimal128(20, 4))
        self.assertEqual(table.schema.field("instant").type, pa.timestamp("us", "UTC"))
        self.assertEqual(source.snapshot_result(query["query_id"], catalog=catalog), manifest)
        self.assertEqual(catalog.published, 1)
        self.assertEqual(len(http.calls), 2)

    def test_hive_and_spark_snapshots_use_common_catalog(self):
        for kind in ("hive", "spark_sql"):
            with self.subTest(kind=kind):
                driver = HS2(columns=[{"name": "amount", "type": "decimal(20,4)"}], pages=[[["123.0001"]], []])
                source = self.source(driver, kind=kind)
                query = source.submit_query("SELECT amount FROM orders")
                catalog = Catalog(self.root / kind)
                manifest = source.snapshot_result(query["query_id"], catalog=catalog)
                self.assertEqual(manifest["row_count"], 1)
                self.assertEqual(manifest["lineage"]["cluster"]["dialect"], "hive" if kind == "hive" else "spark")
                handle = catalog.get(manifest["dataset_id"], snapshot_id=manifest["snapshot_id"], verify=True)
                self.assertEqual(handle.manifest["owner_id"], "alice")
                self.assertEqual(catalog.published, 1)

    def test_later_page_truncation_cleans_open_parquet_writer(self):
        source = self.source(HTTP(("POST", page([[1]], next_uri=NEXT, state="RUNNING")), ("GET", page([[2], [3]]))))
        query = source.submit_query("SELECT 1", budget={"max_rows": 2})
        catalog = Catalog(self.root / "catalog")
        self.assert_code("budget_exceeded", source.snapshot_result, query["query_id"], catalog=catalog)
        self.assertEqual(catalog.published, 0)
        self.assertFalse(self.store.get(query["query_id"])["complete"])
        self.assertEqual(list(catalog.root.rglob("cluster-result.parquet")), [])

    def test_snapshot_claim_recovers_only_after_executor_exit(self):
        source = self.source(HTTP(("POST", page([[1]]))))
        query = source.submit_query("SELECT 1")
        self.store.update(query["query_id"], claim_snapshot=True)
        self.assert_code("resource_busy", self.store.update, query["query_id"], claim_snapshot=True)
        with patch("infrastructure.resource_budget.process_alive", return_value=False):
            self.store.update(query["query_id"], claim_snapshot=True)
        self.store.update(query["query_id"], snapshot_claimed=False)
        catalog = Catalog(self.root / "catalog")
        self.assertEqual(source.snapshot_result(query["query_id"], catalog=catalog)["row_count"], 1)

    def test_snapshot_wrong_scope_is_rejected_before_fetch(self):
        source = self.source(HTTP(("POST", page([[1]]))))
        query = source.submit_query("SELECT 1")
        for overrides in ({"owner_id": "bob"}, {"session_id": "other"}, {"workspace_id": "other"}):
            catalog = Catalog(self.root / "wrong", **overrides)
            self.assert_code("scope_mismatch", source.snapshot_result, query["query_id"], catalog=catalog)
        self.assertFalse(self.store.get(query["query_id"])["fetch_started"])

    def test_truncated_snapshot_never_publishes(self):
        source = self.source(HTTP(("POST", page([[1], [2]]))))
        query = source.submit_query("SELECT 1", budget={"max_rows": 1})
        catalog = Catalog(self.root / "catalog")
        self.assert_code("budget_exceeded", source.snapshot_result, query["query_id"], catalog=catalog)
        self.assertEqual(catalog.published, 0)
        self.assertFalse(self.store.get(query["query_id"])["complete"])
        self.assertEqual(list(catalog.root.rglob("cluster-result.parquet")), [])

    def test_snapshot_import_retry_uses_verified_spool_without_resubmitting(self):
        source = self.source(HTTP(("POST", page([[1]]))))
        query = source.submit_query("SELECT 1")
        catalog = Catalog(self.root / "catalog")
        catalog.fail_publish = True
        with self.assertRaises(OSError):
            source.snapshot_result(query["query_id"], catalog=catalog, idempotency_key="snap")
        self.assertTrue(self.store.get(query["query_id"])["complete"])
        catalog.fail_publish = False
        manifest = source.snapshot_result(query["query_id"], catalog=catalog, idempotency_key="snap")
        self.assertEqual(manifest["row_count"], 1)
        self.assertEqual(len(source.http.calls), 1)
        self.assert_code("idempotency_conflict", source.snapshot_result, query["query_id"], catalog=catalog, idempotency_key="different")

    def test_snapshot_cancellation_before_commit_vs_after_atomic_boundary(self):
        for percent, published in ((3, False), (100, True)):
            with self.subTest(percent=percent):
                source = self.source(HTTP(("POST", page([[1]]))))
                query = source.submit_query("SELECT 1")
                catalog = Catalog(self.root / str(percent))
                def progress(value, message):
                    if value == percent:
                        source.cancel_query(query["query_id"])
                if published:
                    result = source.snapshot_result(query["query_id"], catalog=catalog, progress=progress)
                    self.assertEqual(result["row_count"], 1)
                    self.assertEqual(source.cancel_query(query["query_id"])["cancel_outcome"], "result_committed")
                else:
                    self.assert_code("query_cancelled", source.snapshot_result, query["query_id"], catalog=catalog, progress=progress)
                self.assertEqual(catalog.published, int(published))


class ClusterAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cluster-api-", dir=os.environ.get("BAA_DATA_DIR"))
        self.addCleanup(self.temp.cleanup)
        from flask import Flask
        from api import cluster
        self.api = cluster
        self.app = Flask(__name__)
        self.app.register_blueprint(cluster.bp)
        self.app.config.update(TESTING=True, CLUSTER_STORE_ROOT=self.temp.name,
                               CLUSTER_ALLOWED_ORIGINS=["https://cluster.example:443", "https://other.example:443"])
        self.client = self.app.test_client()
        self.sessions = {"s": types.SimpleNamespace(owner_user_id="alice", workspace_id="w"),
                         "other": types.SimpleNamespace(owner_user_id="bob", workspace_id="w")}
        state = types.ModuleType("api.state")
        state.session_manager = types.SimpleNamespace(get_or_create=lambda sid: self.sessions[sid])
        state.check_session_ownership = lambda sid: (sid == "s", "alice")
        auth = types.ModuleType("api.auth")
        auth.current_user = lambda: {"id": "alice"}
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {"api.state": state, "api.auth": auth}).start()
        patch.object(cluster, "is_cloud_managed_runtime", return_value=True).start()
        self.vault = {}
        def save(secret, *, label):
            ref = label + ":" + "a" * 32
            self.vault[ref] = secret
            return ref
        patch("infrastructure.credential_store.store_secret", side_effect=save).start()
        self.app.config["CLUSTER_CREDENTIAL_RESOLVER"] = lambda ref: self.vault[ref]
        self.base = "/api/datasource/cluster"
        self.scope = {"session_id": "s", "workspace_id": "w"}

    def connection(self, **changes):
        body = {**config(), **self.scope, **changes}
        response = self.client.post(self.base + "/connections", json=body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["connection"]["connection_id"]

    def query(self, connection_id, **changes):
        response = self.client.post(self.base + "/queries", json={**self.scope, "connection_id": connection_id,
                                    "sql": "SELECT x FROM orders", "idempotency_key": "api-query", **changes})
        self.assertEqual(response.status_code, 202, response.get_json())
        return response.get_json()["query"]

    def test_exact_routes_crud_secret_reference_and_test_are_real(self):
        http = HTTP(("POST", page([[1]], [{"name": "connection_test", "type": "bigint"}])))
        self.app.config["CLUSTER_TRANSPORT_FACTORIES"] = {"trino": lambda _: http}
        connection = self.connection(auth="basic", secret="vault-only-secret")
        response = self.client.get(self.base + "/connections", query_string=self.scope)
        self.assertNotIn("vault-only-secret", response.get_data(as_text=True))
        self.assertNotIn("credential_ref", response.get_data(as_text=True))
        response = self.client.patch(self.base + "/connections/" + connection, json={"name": "renamed", **self.scope})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(self.base + "/connections/" + connection + "/test", json=self.scope)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["connected"])
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        response = self.client.delete(self.base + "/connections/" + connection, json=self.scope)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.vault)  # Frozen in-flight queries must retain reconciliation credentials.

    def test_query_routes_events_snapshot_and_fixed_scopes(self):
        http = HTTP(("POST", page([[1]])))
        self.app.config["CLUSTER_TRANSPORT_FACTORIES"] = {"trino": lambda _: http}
        connection = self.connection()
        query = self.query(connection)
        path = self.base + "/queries/" + query["query_id"]
        response = self.client.get(path, query_string=self.scope)
        self.assertEqual(response.status_code, 200)
        response = self.client.get(path + "/events", query_string={**self.scope, "after_seq": 0, "limit": 1})
        self.assertEqual(len(response.get_json()["events"]), 1)
        self.assertEqual(response.get_json()["semantics"], "append")
        for suffix in ("", "/events"):
            self.assertEqual(self.client.get(path + suffix, query_string={"session_id": "s", "workspace_id": "wrong"}).status_code, 403)
            self.assertEqual(self.client.get(path + suffix, query_string={"session_id": "other", "workspace_id": "w"}).status_code, 403)
        catalog = Catalog(Path(self.temp.name) / "catalog", owner_id="alice")
        self.app.config["CLUSTER_CATALOG_FACTORY"] = lambda **scope: catalog
        response = self.client.post(path + "/snapshot", json=self.scope)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["query"]["complete"])
        self.assertEqual(catalog.published, 1)
        response = self.client.post(path + "/cancel", json=self.scope)
        self.assertEqual(response.get_json()["query"]["cancel_outcome"], "result_committed")
        response = self.client.get(self.base + "/queries", query_string={**self.scope, "idempotency_key": "api-query"})
        self.assertEqual(response.get_json()["queries"][0]["query_id"], query["query_id"])

    def test_unknown_submission_replay_and_errors_do_not_expose_secret(self):
        http = HTTP(("POST", OSError("Authorization: secret")))
        self.app.config["CLUSTER_TRANSPORT_FACTORIES"] = {"trino": lambda _: http}
        connection = self.connection()
        body = {**self.scope, "connection_id": connection, "sql": "SELECT 1", "idempotency_key": "lost"}
        response = self.client.post(self.base + "/queries", json=body)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", response.get_data(as_text=True))
        query_id = response.get_json()["query_id"]
        response = self.client.post(self.base + "/queries", json=body)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["query"]["query_id"], query_id)
        self.assertEqual(len(http.calls), 1)

    def test_auth_ownership_workspace_and_reference_injection_are_rejected(self):
        connection = self.connection()
        self.assertEqual(self.client.post(self.base + "/connections", json={**config(), "credential_ref": "foreign"}).status_code, 403)
        self.assertEqual(self.client.patch(self.base + "/connections/" + connection,
                         json={**self.scope, "host": "other.example"}).status_code, 200)
        self.assertEqual(self.client.post(self.base + "/queries", json={**self.scope, "workspace_id": "wrong"}).status_code, 403)
        with patch.object(self.api, "_owner", side_effect=ClusterError("cluster_permission_denied", "Authentication is required")):
            self.assertEqual(self.client.get(self.base + "/connections").status_code, 403)
        self.assertEqual(self.client.post(self.base + "/connections", json={**config(), "tls": False}).status_code, 403)

    def test_missing_common_catalog_is_explicit(self):
        http = HTTP(("POST", page([[1]])))
        self.app.config["CLUSTER_TRANSPORT_FACTORIES"] = {"trino": lambda _: http}
        query = self.query(self.connection())
        with patch.dict(sys.modules, {"data.dataset_catalog": None}):
            response = self.client.post(self.base + "/queries/" + query["query_id"] + "/snapshot", json=self.scope)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "dependency_unavailable")
        self.assertFalse(ClusterQueryStore(self.temp.name, owner_id="alice", session_id="s", workspace_id="w").get(query["query_id"])["fetch_started"])


if __name__ == "__main__":
    unittest.main()
