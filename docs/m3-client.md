# M3 remote client, training API and Job recovery

The implementation uses the runner protocol documented in `m3-runner.md` and the shared contract in `29-training-bigdata-integration-contract.md`. The tests use the actual supervisor/RPC/worker with a controlled SFTP transport. They do not substitute a second submit API or claim live SSH/CUDA acceptance.

## Exact service signatures

```python
RemoteTrainingService(root=None, *, dataset_catalog=None,
                      connection_resolver=None, transport_factory=None)

service.plan(payload, *, owner_id, session_id, workspace_id='')
service.approve(plan_id, *, owner_id, session_id, workspace_id='', expected_plan_hash)
service.submit(plan_id, grant_id, *, idempotency_key,
               owner_id, session_id, workspace_id='', job_runner=None)
service.status(run_id, *, owner_id, session_id, workspace_id='')
service.cancel(run_id, *, owner_id, session_id, workspace_id='')
service.reconcile_pending(*, owner_id, session_id, workspace_id='', job_runner=None)
service.attach_job_runner(run_id, job_runner)
service.artifact(run_id, artifact_id, *, owner_id, session_id, workspace_id='')
service.close()
```

`artifact` returns a verified `(Path, file_metadata)` for a committed, scope-matching bundle only. `reconcile_pending` makes one reconciliation pass over the supplied scope; the host application owns its polling/startup scheduling. Closing a service closes local storage and removes its in-process cancellation callbacks. It neither cancels remote training nor declares an exit.

Default storage is `content_data_path('outputs','remote_training')`. With no injected catalog, the service lazily constructs exactly `DatasetCatalog(owner_id=..., session_id=..., workspace_id=...)` without a custom root. The API derives owner through existing session ownership, using `local-default` locally and the current session/workspace IDs, matching `api.datasets.catalog_for_session(sid)`. It does not import `api.datasets` or create an API import cycle. Catalog injection can be an object or a callable accepting those scope keywords. Connections resolve by configured ID; transport/catalog/resolver selection is never request JSON.

## Approval and routing

A plan accepts only `family`, builtin `params`, `budget`, `dataset_id`, optional `snapshot_id`, `connection_id` and `retention_seconds`. It obtains a complete ready DatasetHandle, verifies canonical file hashes, records all projected input columns (currently the entire selected snapshot), size/rows, target host/fingerprint/device, owner/session/workspace, runtime budget, retention and runner/kernel identity. Planning, approval and initial submission use fresh handshakes. A saved `runner_ready` flag is not submission authorization.

Plans expire after 15 minutes. Approval requires the exact displayed `plan_hash`, verifies data/target again and issues an opaque, durable server grant (at most ten minutes to consume). The grant fixes the remote UTC deadline. A changed active dataset invalidates a plan made without an explicit snapshot ID; an explicitly pinned immutable older snapshot remains valid if its bytes and metadata still match. Changed input bytes/schema, host/credentials, fingerprint, runner instance, device or parameters require a new plan/approval.

The API's dedicated approval action is the user-confirmation boundary. `approve:true`, `confirm:true`, a model parameter or a forged grant field cannot create approval. Model execution can only consume the scoped server grant. `submit` persists its run and idempotency binding before any possibly accepted remote request. Same key/same plan returns that run, including after a lost reply or grant expiry; same key/different plan conflicts. A consumed grant cannot launch another key.

`select_training_target(remote_runners=(), *, min_free_memory_mb=2048, execution_target='auto', approved_plan_id='', grant_id='')` supports `auto`, `cpu`, `local_cuda`, `remote` or `remote(connection_id)` (also `{kind,connection_id}`). `auto` considers local CUDA/CPU only. Explicit remote branches run before local Torch detection/import and require plan/grant IDs; remote failure never starts a local substitute. Analyzer `run(df, target_column, groupby_column=None, n_deciles=0, progress_callback=None, **kwargs)` preserves the legacy four-value result locally; remote execution returns its durable run record. `training_params` must match the approved normalized family parameters. MLP legacy `mlp_cls`/`mlp_reg` aliases normalize to classification/regression. RNN legacy monthly input and `lstm` selection remain supported.

## Transport and concrete reserve/commit compatibility

The minimum transport API is:

```python
transport.rpc(operation, payload) -> dict  # decoded result or stable coded exception
transport.upload(local_path, remote_path, *, sha256, cancel=None, progress=None)
transport.download(remote_path, local_path, *, sha256, size_bytes,
                   cancel=None, progress=None)
transport.close()
```

A transport must expose the actual authenticated `host_fingerprint` for approval binding. Production `SshTrainingTransport` opens a strict-known-host Paramiko connection with agent/key-file/OS-vault credential references, executes only `baa_remote_runner --rpc-v1`, bounds/validates response frames and request IDs, drains stderr without accumulating logs, and uses production `SftpTransfer`. The training purpose never needs `target_port`, a forwarded LLM port or an inference endpoint. Unknown/changed host keys are rejected; there is no plaintext password persistence/fallback.

The client persists and sends the exact runner reserve payload:

```python
{
    'phase': 'reserve', 'idempotency_key': run['remote_idempotency_key'],
    'plan_hash': plan['plan_hash'],
    'plan': {'family': plan['family'], 'params': plan['params'],
             'kernel_version': 'baa-torch-v1', 'device': plan['device']},
    'budget': plan['budget'], 'input_manifest': plan['manifest'],
    'expires_at': run['deadline_at'], 'grant_ref': run['grant_hash'],
    'retention_seconds': plan['retention_seconds'],
}
```

It looks up the original `task_id` or remote idempotency key before reserve/retry, validates task/instance/plan/input identity, verifies the allocated upload manifest against approved files, and uploads only those absolute allocated paths. Commit is exactly `{'phase':'commit','task_id':..., 'manifest_hash':plan['manifest_hash'], 'grant_ref':run['grant_hash']}`. Both sides compute SHA-256 from the same finite, sorted, compact UTF-8 JSON. A lost commit acknowledgement is resolved by status, not another task.

Transfers use <=1 MiB reads/writes, verified complete-block resume, full SHA-256, `.part` staging and atomic final rename. Existing length alone never authorizes resume. Paths must be canonical absolute allocations and match their relative entries, with checks against traversal, links/reparse points, hard-linked/special files, size/count limits and unallocated transfers. SFTP cannot implement a server-side `openat(O_NOFOLLOW)` race guarantee against a malicious server administrator; the trusted runner's private allocation and commit checks are the execution boundary. `/C:/...` allocation spelling is reserved for the Windows controlled test transport; real remote deployment uses POSIX paths.

Per-block progress/checkpoints and renewing run claims are durable. Reconnect recovers task identity/cursors/checkpoints and repeats integrity checks. Unknown task/instance or connectivity retains `unknown`/`recovery_pending` (or `canceling` with persisted intent); it never asserts stopped or releases the lease from a network error.

After a succeeded remote task proves exit, the client downloads the bounded artifact whitelist, checks full hashes, safetensors headers, model kernel/parameters/input/plan lineage and commits a local manifest before publishing success. Local delivery cancellation/failure is distinct from the retained real remote terminal state. Delivery publication and cancellation serialize in the execution store; a late cancel cannot remove a committed success. Full predictions remain Parquet; previews/metrics remain bounded. Managed content-root artifacts are indexed through existing `register_artifact`; explicitly injected roots retain their scoped execution manifest. Generic lifecycle indexing and execution SQLite are reconciled idempotently, not represented as a transaction across databases. The client does not import Torch or auto-load a model; `model_load_verified:false` is intentional until the safe loader is explicitly exercised.

## HTTP routes

`api.training.bp` is an independent Blueprint. Main must register it; this domain does not edit `api/__init__.py`.

| Method | Route | Request / result |
| --- | --- | --- |
| POST | `/api/session/<sid>/training/plans` | Plan fields above; 201 `{ok,plan,plan_id}`. |
| POST | `/api/session/<sid>/training/plans/<plan_id>/approve` | `{expected_plan_hash}` only; 201 `{ok,grant,grant_id}`. |
| POST | `/api/session/<sid>/training/runs` | `{plan_id,grant_id,idempotency_key}` only; 202 `{ok,run,run_id,job_id}`. |
| GET | `/api/session/<sid>/training/runs/<run_id>` | Fresh reconciliation; `{ok,run}`. |
| POST | `/api/session/<sid>/training/runs/<run_id>/cancel` | Empty request or `{}`; 202 with persisted cancellation/exit facts. |
| GET | `/api/session/<sid>/training/runs/<run_id>/artifacts/<artifact_id>` | A committed whitelist filename; verified attachment plus `X-Content-SHA256`. Scope is checked again; no arbitrary file path. |

Artifact-created and native training events use the existing Job event stream; no second SSE bus exists. The supplementary scoped artifact route is necessary because the existing generic export route does not resolve training bundle directories. Each result's `files` carries the actual download URLs. API responses omit transfer allocations, private roots, full input manifests and credential references. Hosted mode rejects remote training until tenant-specific SSH/configuration isolation is provided.

Existing GPU routes retain connection CRUD/fingerprint trust. `POST /api/gpu/connections/<id>/training/preflight` and training-purpose `.../connect` perform the fresh fixed RPC handshake without an inference tunnel. `.../status` exposes training readiness separately from inference connectivity. Creating a connection with `purpose:'training'` accepts SSH host/port/username/auth settings without `target_port`; `purpose:'both'` retains inference forwarding fields. Trusting a host key records its fingerprint and invalidates old readiness.

Tests inject a **real service** at `app.extensions['remote_training_service']`, with a controlled transport factory. Request fields cannot inject transports or claim approval.

## Durable Jobs and main integration hooks

`JobsStore` adds/migrates `execution_backend` (default `local`), `external_id`, `recovery_phase`, `recovery_metadata` **before** interrupted-job recovery. Local jobs retain existing restart behavior. Nonlocal jobs enter `recovery_pending`; they are not blanket failed/canceled. Native event cursor advancement and Job SSE insertion commit in one SQLite transaction, so replay duplicates do not duplicate events.

Native progress updates the existing `job_progress` event and Job row atomically with its native cursor; training completion stays below full Job completion until delivery commits. Concurrent `begin_remote` calls share one managed Job. A proven committed remote delivery resolves a late Job cancellation as succeeded without changing local Job cancellation rules. Clearing terminal Job history also clears its native cursors, and reconciliation does not recreate that cleared history.

`JobRunner.begin_remote`, `checkpoint_remote`, `append_remote_event`, `finish_remote`, and its bound cancellation callback connect managed Jobs to the real service. The Job exists before the first reserve. Cancellation persists intent before network access, works through the injected service while attached, and can reconstruct the default service from durable metadata after restart. Unknown state retains the local workspace lease and durable bounded remote handoff. `finish_remote` needs proven exit; success additionally follows local artifact publication. Restart reconciliation reattaches/reacquires Job leases where a workspace runtime is available.

Main integration responsibilities (outside this domain's ownership):

- Register `api.training.bp`, wire the existing frontend to these exact payloads/results and expose approve only as the explicit user action.
- Invoke `reconcile_pending(**scope, job_runner=session.job_runner)` when restoring a session/workspace and periodically while managed Jobs remain active, before discarding workspace recovery/lease metadata. `status`/`cancel` also reconcile without a background app server.
- Pass scoped approved plan/grant IDs through the validated analysis/tool facade. Preserve the distinction between a queued remote run record and an immediate local four-table result.
- Include run/snapshot/plan/model manifests and existing artifact-created events in the main evidence facade, and add owned tests to the central regression manifest/allowlists. No allowlist, tool/schema, dataset foundation, frontend, packaging or central API registration files were edited here.

## Validation and acceptance limits

Run owned tests with E-drive Python and an isolated temporary `BAA_DATA_DIR`:

```powershell
$env:BAA_DATA_DIR = Join-Path ([IO.Path]::GetTempPath()) ('baa-m3-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $env:BAA_DATA_DIR | Out-Null
& $Python -m unittest discover -s tests -p test_remote_m3_owned.py -v
& $Python -m unittest discover -s tests -p test_torch_rnn.py -v
```

Final local results: all 17 owned tests passed in three short batches (10 tests / 30.599 s, 4 tests / 52.462 s, 3 tests / 40.988 s; each exit 0). A concurrent invocation of the last batch hit the command tool timeout; its result was discarded and the successful three-test batch was rerun serially. Six legacy RNN tests also passed (7.901 s). UTF-8/LF, Python syntax and owned diff-hygiene checks passed.

The tests cover actual CPU training/subprocesses, all fixed model families, safe weight reload, SFTP block/full-hash resume and corruption, lost reserve/commit replies, same-key conflicts, queue/running cancel, unexpected worker death, supervisor restart/deadline watchdog, late outcome suppression, input/host/scope/active-version invalidation, default catalog scope keywords, a real foundation DatasetCatalog publish-to-training path, real Flask-service integration, Job cancellation while disconnected, legacy schema migration, event dedup/truncation and unproven ownership retaining its slot. Commit hashing is specifically tested for live status/cancel while verification is in progress. Legacy RNN tests include monthly input compatibility.

Still unavailable: a supplied real SSH/SFTP target and OS credential/key trust exchange, Linux service lifecycle/reboot/permission testing, CUDA/driver/GPU UUID/VRAM behavior, blocked CUDA cancellation and real shared GPU contention, and physical target hardware/resource acceptance. Full quality, real 2 GiB files and browser integration remain the main thread's acceptance work. No dependencies were installed, paid/live remote services called, commits made, or deployment performed.
