# BDA2 cluster SQL domain

2026-11-14. Implements the cluster contract in docs/29 and section 7 of docs/28. Controlled acceptance passes; live Trino, HiveServer2 and Spark Thrift Server acceptance remains outstanding.

## Files and integration ownership

The cluster implementation is confined to:

- `data/sources/cluster_base.py`, `trino.py`, `hive.py`, `spark_sql.py`.
- `data/cluster_connections.py`, `data/cluster_query_store.py`, `api/cluster.py`.
- `requirements/cluster-trino.txt`, `requirements/cluster-hs2.txt`.
- `tests/test_cluster_sources.py`, `tests/test_cluster_hs2_transport.py` and this document.

The main integration registers `api.cluster.bp` with the existing Flask application and wires DataTools and the frontend to these interfaces. This domain does not edit the application factory, generic source classes, Agent validation, frontend, manifests or regression allowlists. There is one cluster source contract and one shared dataset publication pipeline.

## Source contract

The effective constructor keyword contract for `TrinoDataSource`, `HiveDataSource` and `SparkSQLDataSource` is:

```python
Source(config, *, credential_resolver=None, budget=None, query_store=None,
       connection_id="", transport_factory=None)

source.query_dialect  # "trino", "hive", or "spark"
source.validate_query(sql) -> str
source.get_capabilities() -> dict
source.submit_query(sql, *, idempotency_key="", budget=None) -> dict
source.query_status(query_id) -> dict
source.fetch_batches(query_id, *, budget=None, cancel=None) -> Iterator[pyarrow.RecordBatch]
source.cancel_query(query_id) -> dict
source.snapshot_result(query_id, *, catalog, idempotency_key="", cancel=None, progress=None) -> dict
source.close() -> None
```

`validate_query` returns the original SQL unchanged after strict target-dialect parsing and AST authorization, or raises `ClusterError(code, message, **details)`. It never returns an AST/list or silently rewrites SQL. Physical tables resolve through sqlglot scopes against the authorized catalog/schema/table list, including CTE bodies. Only enabled built-in functions are accepted. Writes, session commands, hints, dynamic/file/URL sources, table functions, qualified functions, recursive queries, multiple statements and parse failures are rejected. The remote account must independently have read-only permissions.

Existing DataSource methods remain available. Metadata lists the configured tables with unknown row counts; it does not count/download/cache every remote table. Schema reads use a qualified zero-row SELECT. Previews add a bounded server LIMIT. `execute_query` is a bounded legacy DataFrame facade; bulk consumers use batches or snapshots. Cluster sources have no writable analysis connection.

`get_capabilities` distinguishes protocol support, server budget requests, client result limits, native cancellation and snapshot availability. It does not claim that a target is connected or ready. `test_connection()` executes and fully consumes a real `SELECT 1 AS connection_test`; missing dependencies and protocol errors cannot return a successful readiness result.

## Stable HTTP API

All paths have the prefix `/api/datasource/cluster`. The main application must register the Blueprint; tests register it in a controlled Flask application.

| Method | Suffix | Request / response |
|---|---|---|
| GET | `/connections` | Optional session/workspace scope; returns owner's public `connections` |
| POST | `/connections` | Flat connection fields plus optional `secret`, `session_id`, `workspace_id`; returns `connection`, 201 |
| PATCH | `/connections/<id>` | Partial flat connection fields plus optional replacement `secret`; returns `connection` |
| DELETE | `/connections/<id>` | Optional JSON or query session/workspace scope; returns `deleted` |
| POST | `/connections/<id>/test` | `{session_id, workspace_id?}`; executes the protocol probe |
| POST | `/queries` | `{session_id, workspace_id?, connection_id, sql, idempotency_key, budget?}`; returns `query`, 202 |
| GET | `/queries/<id>` | Query parameters `session_id`, optional `workspace_id`; returns `query` |
| GET | `/queries/<id>/events` | Same scope plus `after_seq=0`, `limit=100`; returns bounded ordered append events |
| POST | `/queries/<id>/cancel` | `{session_id, workspace_id?}`; returns `query`, 202 while cancellation is pending |
| POST | `/queries/<id>/snapshot` | `{session_id, workspace_id?, idempotency_key?}`; returns `manifest` and `query` |
| GET | `/queries` | Same scope, optional `idempotency_key` and `limit`; recover a lost submission receipt without resubmitting |

Responses contain `ok`; errors contain stable `code` and sanitized `error`, with `query_id` and `state` when submission has already been reserved. Public queries expose both `state` and the equivalent `status`. Responses set `Cache-Control: no-store`. Events are ordinary bounded JSON pages, retain the latest 1000 events/query, and report `first_seq`, `last_seq`, `next_seq` and `truncated`; no new SSE bus is introduced.

Connection example (the host is a placeholder):

```json
{
  "session_id": "session-id",
  "workspace_id": "workspace-id",
  "kind": "trino",
  "name": "Read-only warehouse",
  "host": "cluster.example",
  "port": 443,
  "tls": true,
  "auth": "basic",
  "username": "analytics_reader",
  "secret": "<entered once>",
  "catalog": "warehouse",
  "schema": "sales",
  "allowed_tables": ["warehouse.sales.orders"]
}
```

Hive/Spark use `kind: "hive"` / `"spark_sql"`, schema-qualified tables, an empty/omitted catalog, and `hs2_driver: "pyhive"` if specified. `auth` is a string (`none`, `basic`, `jwt` for Trino; `none`, `ldap` for HS2). Frontend integration must map its UI form fields to this flat schema; `type`, an auth object, broad `allowed_catalogs`/`allowed_schemas`, DSNs and arbitrary session properties are not accepted. Table permissions are explicit and fully qualified after normalization.

Submission example:

```json
{
  "session_id": "session-id",
  "workspace_id": "workspace-id",
  "connection_id": "server-issued-connection-id",
  "sql": "SELECT region, SUM(amount) AS total FROM sales.orders GROUP BY region",
  "idempotency_key": "one-user-operation",
  "budget": {"max_rows": 1000, "max_output_bytes": 4194304, "max_run_seconds": 60}
}
```

## Scope, credentials and TLS

Owner identity comes from the existing authenticated user in hosted mode; local mode uses the existing shared-local identity `local-default`. Session ownership is checked through `api.state.check_session_ownership`. Workspace identity is derived from that session; a conflicting caller workspace is rejected. Query reads, events, cancellation and publication require the original owner/session/workspace tuple. Snapshot catalogs must expose that exact tuple before any result is consumed.

The API stores secrets through the existing `infrastructure.credential_store.store_secret`, loads through `load_secret`, and persists only owner-bound opaque references in SQLite. There is no plaintext fallback. Public connections replace the reference with `credential_configured`; the HTTP caller cannot inject a reference. Changing host, authentication identity or approved endpoints requires a new secret. Old references are retained when updating/deleting a connection so frozen in-flight queries can still reconcile and cancel. Vault retirement requires a later policy that proves those references are unused.

TLS uses the system trust store and hostname verification. There is no caller-controlled `verify=False`, arbitrary CA path, client certificate or auth header. Authenticated connections require TLS; hosted connections require TLS even without authentication. Local unauthenticated plaintext is explicit. Trino supports Basic/JWT. HS2 LDAP uses SASL PLAIN inside verified TLS; `none` means the binary NOSASL transport, not a SASL-NONE/Kerberos negotiation. Unsupported authentication fails explicitly.

Connections and query facts use the existing content-data boundary. Hosted content-root lifetime follows the application's existing transient-content policy; this implementation does not make hosted user content durable on the account volume.

## Three protocols and resource limits

| Adapter | Actual path | Distinct semantics |
|---|---|---|
| Trino | Standard `/v1/statement` POST, returned `nextUri` GET, native query status, DELETE cancellation | Catalog/schema headers, HTTP-200 business errors, query identity binding, session budget properties |
| Hive | Pinned PyHive-generated TCLIService V6 client over bounded binary Thrift | OpenSession, async ExecuteStatement, OperationHandle, metadata, FetchResults, CancelOperation, terminal status |
| Spark SQL | Spark Thrift Server using the same bounded HS2 transport | Spark AST dialect, fixed UTC/ANSI session requests, LTZ/NTZ handling, administrator-configured resource pools |

Trino HTTP counts the plaintext HTTP status, headers, chunk framing/trailers and body before JSON decoding. Responses, redirects and next URIs cannot silently bypass the configured page cap. There are no automatic redirects, environment proxies, POST retries, decompression or spooling. Returned URIs must belong to explicitly approved origins, use the same TLS policy and native query identity, and have approved protocol paths without credentials, query strings or encoded traversal. Untrusted endpoints are rejected before resolving/forwarding authorization for a follow-up request. Trino resource-group configuration supplies a client tag; actual group selection needs administrator rules.

HS2 bounds each complete RPC before `readAll` can allocate an advertised field or frame. SASL is guarded both below its wire framing and above decoding, and binary protocol strings/containers have explicit limits. The driver negotiates V6, serializes the operation handle privately, fetches bounded pages and verifies an empty terminal page instead of trusting historically inconsistent `hasMoreRows` hints. It does not claim that DB-API `fetchmany` alone limits network frames. Saved handles can be queried over a fresh transport without executing the SQL again; a server rejecting that recovery produces unknown state.

Default source ceilings are 120 seconds, 10,000 rows, 8 MiB output and 256 KiB Arrow batches. Default HTTP/Thrift page cap is 1 MiB (configurable up to 8 MiB); result batching has no page prefetch. Whole decoded pages, individual rows, Arrow batches, cumulative rows/output and physical Parquet staging are checked. A narrow client result limit does not imply a narrow server scan or a cost ceiling. Protocol limits do not constitute a hard RSS bound for the controller.

Cluster budget fields are `max_run_seconds`, `max_output_bytes`, `max_rows`, `batch_bytes`, `max_scan_bytes`, `max_cpu_seconds`, `strict_server`. Unknown fields are rejected. Requests can tighten the configured source budget; fetch cannot relax a submitted result/time budget. The trusted application can configure `CLUSTER_QUERY_BUDGET` at startup to change source defaults/ceilings. HTTP request bodies cannot change that configuration. Generic frontend import/training budget fields must be mapped to cluster fields.

Trino sends fixed `query_max_run_time`, optional `query_max_scan_physical_bytes` and `query_max_cpu_time` session properties. Unsupported server properties fail with `unsupported_budget`. Hive/Spark request `ExecuteStatement.queryTimeout`, but do not claim a hard server timeout, scan or CPU ceiling. Specifying an unsupported hard scan/CPU limit, or `strict_server: true` with an unavailable hard time limit, rejects the query before transport submission. Budget policy returns the enforceable/unavailable distinction explicitly. Unreported scan/CPU/queue statistics remain unavailable; result rows are never reported as scanned rows.

## Cancellation, unknown outcomes and snapshots

Reservations and idempotency keys are durable and scope-bound. A key binds SQL, frozen connection configuration and budget. Same key/same input returns the existing record; different input conflicts. A transport failure after sending enters `remote_state_unknown`, retains any native identity, and never blindly resubmits. Preflight failure is known `not_submitted`. Cancellation before submission prevents sending; intent received while submission is in flight is forwarded as soon as the native identity arrives.

`cancel_requested`, `cancel_confirmed`, local `state`, `remote_state` and `complete` are separate facts. DELETE/CancelOperation acceptance or closing a socket cannot prove termination. Until an actual native terminal state is observed, cancellation remains pending. A server finishing before cancel confirmation is recorded as succeeded remotely with discarded/incomplete delivery; it is not relabeled as a confirmed cancellation. Unknown/closed/invalid HS2 handles preserve uncertainty.

Result streams have one durable consumer claim. Abandonment, schema drift, row/byte limits and fetch failures retain `complete: false`, request cancellation when needed and cannot publish a partial dataset. A new source instance does not replay/resubmit an already-consumed result.

`snapshot_result` streams RecordBatches directly into a bounded Parquet staging file under the scoped catalog. It never collects all remote pages into a `pa.Table`. The complete, hash-verified staging file goes through the common `create_import_plan` / `run_import` / `DatasetCatalog.publish` path. Source/lineage record the query/native identity, SQL hash, dialect and extraction completeness. The shared import worker has an explicit 512 MiB RSS budget, one queue batch and one thread; actual enforcement/usage comes from the shared supervisor. Import policy remains owned by the import/foundation domains.

A failed local import may retry its verified spool and shared import key without rerunning remote SQL. Snapshot claims can be recovered only after the recorded executor has exited. Cancellation before catalog commit prevents publication; cancellation after the shared atomic commit returns `result_committed` and preserves the published version. A local immutable extraction is reproducible; it does not imply time travel or repeatable re-execution on the remote source.

DECIMAL through precision 38 is converted from exact decimal text/values, with no float round-trip. Timezone-free and timezone-aware timestamps remain distinct. Ambiguous/nonexistent IANA-zone wall times require an explicit offset, and contradictory offsets/zones fail. Timestamp precision beyond microseconds and unsupported complex types fail explicitly. Spark requests UTC and ANSI semantics in OpenSession; standard Spark timestamp is LTZ, and NTZ requires explicit NTZ result metadata. The chosen Spark server must be checked for session-setting acceptance and its NTZ wire representation before live acceptance.

## Optional dependencies and acceptance

`requirements/cluster-trino.txt` pins Trino 0.340.0, Arrow 24.0.0 and sqlglot 30.21.0. The in-tree statement HTTP transport enforces whole-page limits; optional imports do not affect application boot. `requirements/cluster-hs2.txt` selects only `PyHive[hive_pure_sasl]==0.7.0`, `thrift==0.22.0`, `thrift-sasl==0.4.3`, `pure-sasl==0.6.2`, Arrow 24.0.0 and sqlglot 30.21.0. There is no impyla fallback or second SQL framework. An absent/wrong PyHive version raises `dependency_unavailable` with the optional requirements filename. Kerberos, HTTP Thrift, Spark Connect, client certificates and arbitrary session options are outside this support profile.

Executed from the approved worktree with the E-drive interpreter and a fresh data root:

```powershell
$clusterTestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('baa-cluster-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $clusterTestRoot | Out-Null
$env:BAA_DATA_DIR = $clusterTestRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
& 'E:/Tool/Python313/python.exe' -m unittest discover -s tests -p 'test_cluster*.py' -q
```

Latest result: **57 tests passed in 18.011 seconds**, exit 0. This includes controlled Trino HTTP, real HS2 driver/RPC code with controlled generated-type peers, Hive/Spark differences, HTTP-200 errors, malicious URIs, auth-secret redaction, whole HTTP/Thrift caps, submit/cancel races, lost receipts, replay prevention, exact decimals/timezones, owner/session/workspace denial, and all three protocols publishing through the real shared import/catalog path. The shared import file changed during earlier verification; the main integration must rerun the full suite after adjacent-domain changes settle.

Arrow 24.0.0 and Trino 0.340.0 were available locally. The optional HS2 driver was absent; its missing-dependency path and controlled driver path were tested. The pinned PyHive source definitions were inspected without installing or executing the downloaded package. No live cluster, actual LDAP/TLS handshake, OS-vault round trip, real server resource-group enforcement, scan/CPU ceiling, Spark NTZ representation or live cancellation was accepted by these tests. Those remain separate external CL-01 through CL-04 acceptance tasks with chosen server versions and read-only accounts. No server/build/install/full suite or Git publication was performed by this domain.
