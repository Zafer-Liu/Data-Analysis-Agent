"""Standard Trino statement HTTP protocol with explicit endpoint trust."""
from __future__ import annotations

import base64
import http.client
import json
import math
import re
import ssl
import time
from urllib.parse import unquote, urlsplit

from data.cluster_connections import ClusterError
from data.sources.cluster_base import ClusterDataSource


class _BoundedHTTPReader:
    """Count status, headers, chunk framing and body before HTTP decoding."""

    def __init__(self, stream, sock, maximum, deadline):
        self.stream, self.sock = stream, sock
        self.maximum, self.deadline, self.count = maximum, deadline, 0

    def read1(self, size=-1):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        self.sock.settimeout(remaining)
        capacity = self.maximum - self.count + 1
        value = self.stream.read1(min(capacity, size) if size >= 0 else capacity)
        self.count += len(value)
        if self.count > self.maximum:
            raise ClusterError("cluster_frame_too_large", "Trino response exceeds the whole-HTTP-page byte budget")
        return value

    def read(self, size=-1):
        size = min(size, self.maximum - self.count + 1) if size >= 0 else self.maximum - self.count + 1
        parts, left = [], size
        while left:
            value = self.read1(left)
            if not value:
                break
            parts.append(value)
            left -= len(value)
        return b"".join(parts)

    def readline(self, size=-1):
        # read1, unlike BufferedReader.readline/read, performs at most one raw
        # read. Recheck the absolute deadline even for a trickling header.
        size = min(size, self.maximum - self.count + 1) if size >= 0 else self.maximum - self.count + 1
        value = bytearray()
        while len(value) < size:
            part = self.read1(1)
            value.extend(part)
            if not part or part == b"\n":
                break
        return bytes(value)

    def flush(self):
        self.stream.flush()

    def close(self):
        self.stream.close()


class _BoundedHTTPResponse(http.client.HTTPResponse):
    def __init__(self, sock, *args, maximum, deadline, **kwargs):
        super().__init__(sock, *args, **kwargs)
        self.fp = _BoundedHTTPReader(self.fp, sock, maximum, deadline)


class BoundedTrinoHTTP:
    """No POST retries, redirects, environment proxies, decompression or spooling."""

    def request(self, method, url, *, headers, body=None, timeout=30, max_bytes=1024**2):
        parsed = urlsplit(url)
        connection = (http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout,
                                                context=ssl.create_default_context())
                      if parsed.scheme == "https" else
                      http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout))
        deadline = time.monotonic() + timeout
        connection.response_class = lambda sock, *args, **kwargs: _BoundedHTTPResponse(
            sock, *args, maximum=max_bytes, deadline=deadline, **kwargs)
        response = None
        try:
            connection.request(method, parsed.path + ("?" + parsed.query if parsed.query else ""), body=body, headers=headers)
            response = connection.getresponse()
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    if int(length) < 0 or int(length) > max_bytes:
                        raise ClusterError("cluster_frame_too_large", "Trino response exceeds the HTTP page budget")
                except ValueError:
                    raise ClusterError("cluster_protocol_error", "Invalid HTTP content length") from None
            parts, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, max_bytes - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ClusterError("cluster_frame_too_large", "Trino response exceeds the HTTP page budget")
                parts.append(chunk)
            return response.status, dict(response.getheaders()), b"".join(parts)
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                connection.close()


class TrinoDataSource(ClusterDataSource):
    kind, query_dialect = "trino", "trino"
    protocol = "trino-statement-http"
    hard_limits = frozenset({"max_run_seconds", "max_scan_bytes", "max_cpu_seconds"})

    def __init__(self, config, **kwargs):
        super().__init__(config, **kwargs)
        host = f"[{self.config['host']}]" if ":" in self.config["host"] else self.config["host"]
        self.origin = f"{'https' if self.config['tls'] else 'http'}://{host}:{self.config['port']}"
        self.http = self.transport_factory(self.config) if self.transport_factory else BoundedTrinoHTTP()

    def get_capabilities(self):
        return {**super().get_capabilities(), "statistics": ["elapsed_seconds", "queued_seconds", "scan_bytes", "cpu_seconds"],
                "server_budget_support": "Trino session properties; an unsupported property rejects the query",
                "resource_group_selection": "client tag; administrator selection rules required",
                "spooling": False}

    def _validate_uri(self, uri, *, native="", status=False):
        if not isinstance(uri, str) or len(uri) > 4096:
            raise ClusterError("cluster_untrusted_uri", "Invalid Trino response URI")
        try:
            parsed = urlsplit(uri)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            raise ClusterError("cluster_untrusted_uri", "Invalid Trino response URI") from None
        h = f"[{parsed.hostname}]" if parsed.hostname and ":" in parsed.hostname else (parsed.hostname or "").lower()
        origin = f"{parsed.scheme}://{h}:{port}"
        decoded = unquote(parsed.path)
        if (origin not in self.config["approved_endpoints"] or parsed.username or parsed.password
                or parsed.query or parsed.fragment or "\\" in decoded
                or any(p in {".", ".."} for p in decoded.split("/"))
                or not re.fullmatch(r"/v1/(?:statement|query)/[A-Za-z0-9_/.-]+", decoded)
                or "%" in parsed.path or any(ord(c) < 33 or ord(c) == 127 for c in uri)
                or (native and native not in decoded.split("/"))):
            raise ClusterError("cluster_untrusted_uri", "Trino returned an endpoint outside the approved cluster/query boundary")
        if status and decoded != "/v1/query/" + native:
            raise ClusterError("cluster_untrusted_uri", "Invalid Trino status URI")
        return uri

    def _headers(self, limits=None):
        cfg = self.config
        headers = {"X-Trino-User": cfg["username"], "X-Trino-Source": "baa-cluster-readonly",
                   "X-Trino-Catalog": cfg["catalog"], "X-Trino-Schema": cfg["schema"],
                   "Content-Type": "text/plain; charset=utf-8", "Accept": "application/json",
                   "Accept-Encoding": "identity"}
        if cfg["auth"] == "basic":
            token = base64.b64encode(f"{cfg['username']}:{self._credential()}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        elif cfg["auth"] == "jwt":
            headers["Authorization"] = f"Bearer {self._credential()}"
        if limits is not None:
            properties = [f"query_max_run_time={limits['max_run_seconds']}s"]
            if limits["max_scan_bytes"] is not None:
                properties.append(f"query_max_scan_physical_bytes={limits['max_scan_bytes']}B")
            if limits["max_cpu_seconds"] is not None:
                properties.append(f"query_max_cpu_time={limits['max_cpu_seconds']}s")
            headers["X-Trino-Session"] = ",".join(properties)
        if cfg["resource_group"]:
            headers["X-Trino-Client-Tags"] = cfg["resource_group"]
        return headers

    def _request(self, method, uri, *, record=None, sql=None, initial=False):
        if not initial:
            self._validate_uri(uri, native=record["native_query_id"] if record else "")
        limits = record["budget"] if record else self.budget
        try:
            status, headers, raw = self.http.request(method, uri, headers=self._headers(limits if sql is not None else None),
                                                   body=sql.encode("utf-8") if sql is not None else None,
                                                   timeout=min(self.config["timeout_seconds"], limits["max_run_seconds"]),
                                                   max_bytes=self.config["max_response_bytes"])
        except ClusterError:
            raise
        except Exception:
            raise ClusterError("remote_state_unknown", "Trino request outcome is unknown; submission will not be repeated") from None
        if not isinstance(raw, bytes) or len(raw) > self.config["max_response_bytes"]:
            raise ClusterError("cluster_frame_too_large", "Trino response exceeds the HTTP page budget")
        if 300 <= status < 400:
            raise ClusterError("cluster_untrusted_uri", "Trino redirects are disabled")
        if status in {401, 403}:
            raise ClusterError("cluster_permission_denied", "Cluster authentication or authorization was denied")
        if not 200 <= status < 300:
            raise ClusterError("remote_state_unknown" if status in {404, 408, 502, 503, 504} else "cluster_http_error", f"Trino returned HTTP {status}")
        if method == "DELETE" and not raw:
            return {}
        if any(k.lower() == "content-encoding" and v.lower() not in {"identity", ""} for k, v in headers.items()):
            raise ClusterError("cluster_protocol_error", "Compressed Trino responses are not enabled")
        try:
            data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError, RecursionError):
            raise ClusterError("cluster_protocol_error", "Invalid Trino JSON response") from None

    @staticmethod
    def _statistics(data):
        stats = data.get("stats") or data.get("queryStats") or {}
        result = {"server_stats_source": "trino", "scan_bytes": None, "cpu_seconds": None,
                  "queued_seconds": None, "elapsed_seconds": None}
        for native, field, scale in (("physicalInputBytes", "scan_bytes", 1), ("cpuTimeMillis", "cpu_seconds", .001),
                                     ("queuedTimeMillis", "queued_seconds", .001), ("elapsedTimeMillis", "elapsed_seconds", .001)):
            val = stats.get(native)
            if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val) and val >= 0:
                result[field] = val * scale
        return result

    def _apply(self, query_id, data, *, page=False):
        record = self.store.get(query_id)
        native = data.get("id") or data.get("queryId") or record["native_query_id"]
        if not isinstance(native, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", native):
            raise ClusterError("cluster_protocol_error", "Missing or invalid Trino query ID")
        if record["native_query_id"] and native != record["native_query_id"]:
            raise ClusterError("cluster_protocol_error", "Trino changed the query identity")
        self.store.update(query_id, native_query_id=native)
        stats = data.get("stats") or {}
        state = str(data.get("state") or stats.get("state") or "RUNNING").upper()
        error = data.get("error") or (data.get("errorCode") if state == "FAILED" else None) or {}
        if not isinstance(error, dict):
            raise ClusterError("cluster_protocol_error", "Invalid Trino error response")
        if error or state in {"FAILED", "CANCELED", "CANCELLED"}:
            code = error.get("errorName") or error.get("name") or state
            cancelled = code in {"USER_CANCELED", "USER_CANCELLED", "CANCELED", "CANCELLED"}
            is_budget = code in {"EXCEEDED_TIME_LIMIT", "EXCEEDED_SCAN_LIMIT", "EXCEEDED_CPU_LIMIT", "EXCEEDED_GLOBAL_MEMORY_LIMIT"}
            denied = code in {"PERMISSION_DENIED", "ACCESS_DENIED"}
            unavailable = code in {"INVALID_SESSION_PROPERTY", "INVALID_SESSION_PROPERTY_VALUE", "NOT_SUPPORTED"}
            stable = "query_cancelled" if cancelled else "unsupported_budget" if unavailable else "budget_exceeded" if is_budget else "cluster_permission_denied" if denied else "cluster_query_failed"
            message = "Trino cancelled the query" if cancelled else "Trino rejected or failed the query"
            self.store.update(query_id, state="cancelled" if cancelled else "failed", remote_state="cancelled" if cancelled else "failed",
                              cancel_confirmed=cancelled, complete=False, statistics=self._statistics(data), error_code=stable, error=message)
            raise ClusterError(stable, message)
        remote = "succeeded" if state == "FINISHED" else "running"
        if record["remote_state"] == "succeeded":
            remote = "succeeded"
        changes = {"statistics": self._statistics(data), "remote_state": remote}
        if record["state"] not in {"fetching", "cancel_pending", "cancelled", "failed", "succeeded"}:
            changes["state"] = "running"
        if page:
            next_uri = data.get("nextUri", "")
            if next_uri:
                self._validate_uri(next_uri, native=native)
                if not urlsplit(next_uri).path.startswith("/v1/statement/"):
                    raise ClusterError("cluster_untrusted_uri", "Invalid Trino statement continuation")
            rows = data.get("data", [])
            columns = data.get("columns") or record.get("columns", [])
            if not isinstance(rows, list) or not isinstance(columns, list) or len(columns) > 1024:
                raise ClusterError("cluster_protocol_error", "Unsupported Trino data encoding; spooling is disabled")
            normalized = []
            for c in columns:
                if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not isinstance(c.get("type"), str):
                    raise ClusterError("cluster_protocol_error", "Invalid Trino column metadata")
                normalized.append({"name": c["name"], "type": c["type"]})
            if any(not isinstance(r, list) or len(r) != len(normalized) for r in rows):
                raise ClusterError("cluster_protocol_error", "Invalid Trino row width")
            changes.update(columns=normalized, driver_state={"next_uri": next_uri, "pending_rows": rows,
                           "status_uri": f"{self.origin}/v1/query/{native}", "page_final": not bool(next_uri)})
            if not next_uri:
                changes["remote_state"] = "succeeded"
        if record.get("cancel_requested") and changes["remote_state"] == "succeeded":
            changes.update(state="failed", complete=False, error_code=record["error_code"] or "result_discarded",
                           error=record["error"] or "Server finished before cancellation was confirmed; the result was discarded")
        self.store.update(query_id, **changes)

    def _submit(self, record):
        claimed = self.store.update(record["query_id"], claim_submit=True)
        if claimed["submission_phase"] != "sending":
            raise ClusterError("query_cancelled", "Query was cancelled before transport submission")
        try:
            data = self._request("POST", f"{self.origin}/v1/statement", record=record, sql=record["sql"], initial=True)
        except ClusterError as exc:
            if exc.code == "cluster_permission_denied":
                self.store.update(record["query_id"], state="failed", remote_state="failed", error_code=exc.code, error=str(exc))
            raise
        self._apply(record["query_id"], data, page=True)

    def _poll(self, record):
        uri = record["driver_state"].get("status_uri") or f"{self.origin}/v1/query/{record['native_query_id']}"
        self._validate_uri(uri, native=record["native_query_id"], status=True)
        self._apply(record["query_id"], self._request("GET", uri, record=record))

    def _cancel(self, record):
        uri = record["driver_state"].get("next_uri") or f"{self.origin}/v1/query/{record['native_query_id']}"
        self._request("DELETE", uri, record=record)
        self._poll(self.store.get(record["query_id"]))

    def _pages(self, record, *, cancel=None):
        while True:
            self._check_active(record, cancel=cancel)
            current = self.store.get(record["query_id"])
            state = current["driver_state"]
            rows = state.get("pending_rows", [])
            self.store.update(record["query_id"], driver_state={**state, "pending_rows": []})
            yield rows, current.get("columns", [])
            self._check_active(record, cancel=cancel)
            uri = state.get("next_uri")
            if not uri:
                return
            data = self._request("GET", uri, record=record)
            self._apply(record["query_id"], data, page=True)

    def close(self):
        if hasattr(self.http, "close"):
            self.http.close()
        super().close()
