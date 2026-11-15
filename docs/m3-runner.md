# M3 fixed runner and shared kernels

Implemented against `29-training-bigdata-integration-contract.md`. The runner and client use the same RPC; the owned tests run a real local supervisor, socket RPC, fixed worker subprocess and real Torch CPU training. This is not SSH or CUDA acceptance.

## Installation (administrator action only)

Supported remote deployment profile: an administrator-owned Linux installation, one trusted SSH/SFTP account, one persistent supervisor root, and one configured CUDA device. Windows is covered by local CPU protocol/process tests, not a remote production GPU profile. The protocol is not a multi-tenant OS sandbox.

1. Place the reviewed `remote_runner/` tree and `Function/Analyze/Torch_MLP/{kernel.py,dataset.py}` plus `Function/Analyze/Torch_RNN/kernel.py` together under an administrator-controlled application prefix. Preserve the Python namespace directory layout. Input/upload directories must never be on Python's import path.
2. Create an administrator-managed Python environment. `remote_runner/requirements.txt` is an optional pinned environment, not an auto-installer. Select the appropriate Torch CPU/CUDA wheel index separately. The tested local environment used Python 3.13, Torch 2.9.1+cu126, Arrow 24.0.0, safetensors 0.8.0, psutil 7.2.2, NumPy 2.5.2, pandas 3.0.5 and DuckDB 1.5.6. Its tasks explicitly used CPU.
3. Install a trusted executable named `baa_remote_runner` on the SSH account's PATH. The checked-in `remote_runner/baa_remote_runner` resolves only its installed application tree. For a fixed environment/root, an administrator can wrap it as follows (example installation paths, not application defaults):

```sh
#!/bin/sh
export BAA_RUNNER_ROOT=/var/lib/baa-runner
exec /opt/baa/venv/bin/python -I /opt/baa/app/remote_runner/baa_remote_runner "$@"
```

4. Create a private root owned by that account. The supervisor owns a process lock, SQLite WAL facts, a persistent runner instance ID, a private authenticated loopback endpoint and generated task directories. On Linux the root/task directories use mode 0700 and endpoint metadata uses 0600. Configure filesystem permissions/quotas and GPU isolation at the OS level.
5. Run the persistent service independently of SSH. An example systemd unit is below. `KillMode=process` is intentional: replacing the supervisor preserves workers for birth-identity reconciliation. Stopping the service alone is not an acknowledgement that training stopped; use task cancellation and observe `exit_acknowledged`.

```ini
[Unit]
Description=BAA fixed training supervisor
After=network.target

[Service]
Type=simple
User=baa-training
WorkingDirectory=/opt/baa/app
Environment=BAA_RUNNER_ROOT=/var/lib/baa-runner
Environment=PATH=/opt/baa/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/opt/baa/venv/bin/python -m remote_runner.baa_remote_runner --serve --root /var/lib/baa-runner --device cuda:0
Restart=on-failure
RestartSec=2
KillMode=process
TimeoutStopSec=10
UMask=0077
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
```

No installation, unit activation, dependency download or live server connection was performed by this implementation session. Administrators must keep one canonical supervisor root per configured GPU/account, confirm `nvidia-smi` availability/UUID reporting, and configure OS isolation for shared hardware. CPU tests use `--device cpu` explicitly.

## Exact RPCv1

SSH command: `baa_remote_runner --rpc-v1`. Its stdin/stdout contain one UTF-8 JSON object per line, at most 1 MiB including framing. Stdout never contains training logs. The proxy only calls the persistent local service; disconnecting or killing the SSH proxy does not own a worker's lifetime. The legacy `--preflight --json` entry now reports service capabilities.

Envelope:

```json
{"version":1,"request_id":"opaque-id","operation":"handshake","payload":{}}
```

Success: `{"version":1,"request_id":"opaque-id","ok":true,"result":{...}}`.
Failure: the same identity with `ok:false`, stable `code`, `error` and optional `details`.

| Operation | Exact payload / result |
| --- | --- |
| `handshake` | `{}`; returns protocol/build/kernel version, persistent `runner_instance_id`, service generation, supported families, device, CUDA boolean, Torch/Python/GPU metadata, capability expiry, supervision and enforcement descriptions. A CPU service never reports CUDA execution. |
| `submit` / reserve | `phase:"reserve", idempotency_key, plan_hash, plan:{family,params,kernel_version,device}, budget, input_manifest, expires_at, grant_ref, retention_seconds` (retention default 86400). Returns task status and `upload_manifest:{root,files:[{relative_path,format,size_bytes,sha256,remote_path,...}]}`. Root and remote paths are absolute server allocations. |
| `submit` / commit | `phase:"commit", task_id, manifest_hash, grant_ref`. `manifest_hash` is SHA-256 of the exact canonical input manifest, identical to the client's calculation. Full input hashes/JSON and private regular-file paths must validate before queuing. |
| `status` | `{task_id}` or `{idempotency_key}`; returns task/instance/attempt IDs, phase status, expiry, input/plan hashes, upload allocation, native cursor, usage, worker birth identity, cancellation intent, artifacts and separate `exit_acknowledged`/`slot_owned` facts. |
| `events` | `{task_id,after_seq:0,limit:100}`; ascending `events` with `seq`, `type`, UTC time and bounded payload, plus `oldest_seq`, `last_seq`, `truncated`. Limit 1–100, each event <=4 KiB, retained count <=2000 and retained bytes <= `max_log_bytes`. |
| `cancel` | `{task_id,attempt_id?}`; persist intent before notification/signals. `reason:"deadline_expired"` records deadline failure rather than user cancellation. Duplicate cancellation does not start another attempt. |
| `artifact` | `{task_id,artifact_id?}`; returns the published bounded allocation (`root`, `files`) after rechecking hashes and safetensors headers. `artifact_id` selects a published relative filename. |

Canonical hashes use UTF-8 JSON with sorted keys, compact separators, `ensure_ascii=False`, `allow_nan=False`. Same reserve key and identical payload return the original task; changed content returns `idempotency_conflict`. Repeated commit never creates another worker. Unknown task/instance state cannot authorize replacement training.

## Supervision and resource semantics

States include `reserved`, `verifying`, `queued`, `starting`, `running`, `canceling`, `unknown`, and the terminal `succeeded`/`failed`/`canceled` labels. Input verification hashes blocks outside the state lock so status, cancellation and deadlines remain live. A service restart returns interrupted verification to `reserved`; it does not repeat a committed training attempt.

A launch is persisted before spawning a fixed `shell=False` argv. The worker independently records PID birth and attempt token, waits for a persisted start gate, and has its own deadline/cancel/RSS/CPU/disk watchdog before importing Torch. Supervision survives SSH loss and adopts a living registered worker after a service restart. UTC deadlines persist; live checks use monotonic time and restart reconstruction never grants more than the original runtime. No epoch continuation is promised after worker death.

A slot remains held until the leader and observed descendants are proven exited. Linux boot ID and PID birth guard process ownership. A reused PID cannot authorize signaling a different process; inaccessible processes or missing launch proof never count as exit. Cancellation allows a short cooperative interval, then TERM/KILL only against birth-verified owned processes. A canceled/expired attempt cannot publish a late success. A supervisor crash does not disable the worker's budget watchdog.

The service reserves aggregate input/scratch/output disk, admits one attempt per configured device, and checks external GPU occupancy by UUID before launch and while running. It never kills external GPU users. RSS, CPU, disk, and wall-time enforcement is **monitored and may overshoot**; it is not cgroup/Job Object hard isolation. GPU allocator estimates are not hardware isolation. Uninspectable GPU occupancy prevents admission.

Retention is fixed by the approved payload (60–604800 seconds). After the approved deadline plus retention and proven task exit, service maintenance removes only that task's input/scratch/output allocation; status/idempotency facts remain. Native event storage is separately bounded. At 4096 retained task records or 64 active reservations the service refuses new reservations until administrator archival/admission becomes available.

## Kernels and safe results

Both local analyzer `run(...)` functions and the fixed worker use `baa-torch-v1` in `Torch_MLP/kernel.py`; `Torch_RNN/kernel.py` exports that same RNN implementation. Both accept small DataFrames, canonical DatasetHandles or replayable/spooled Arrow batches. The compatibility dataset adapter uses the same verified batch source and split.

MLP classification/regression fits category maps, missing-value means and scaling on deterministic training rows only; unknown categories have a reserved column and cardinality/encoded-feature caps. RNN sorts each sequence externally, uses train-only normalization and lazy numeric memmap windows with disjoint train/validation contexts. GPU tensors contain minibatches only; no full-dataset device tensors or window matrix are constructed. RNN retains legacy `YYYY-MM` input support and uses a fixed UTC SQL timezone. Input bytes/rows, batch/queue size, threads, time, CPU, scratch/output size and model dimensions are bounded; RNN window reads use a conservative logical read reservation rather than pretending to measure physical disk billing.

Inputs are canonical Parquet and bounded JSON. Derived numeric NPY is read with `allow_pickle=False`. Outputs are `predictions.parquet`, three bounded legacy preview tables, `metrics.json`, `model.json`, and `model.safetensors`. Model metadata records feature order, splits, seed, actual device/version and input/plan/task lineage. `load_model_bundle(root, *, max_bytes=...)` reconstructs only a builtin architecture and loads validated safetensors with a strict state dictionary; no whole-object Torch load, pickle, joblib, client script, dynamic model import or task environment is supported.

## Evidence and remaining acceptance

The owned regression command is `python -m unittest discover -s tests -p test_remote_m3_owned.py -v`, with an isolated temporary `BAA_DATA_DIR`. It exercises a real local supervisor and fixed CPU worker, controlled filesystem SFTP with production block/full-hash logic, all four model variants, output reload/integrity, dropped reserve/commit replies, same-key conflicts, queue/running cancellation, kill/restart/deadlines, late output, scope/plan invalidation, API/service integration, old JobsStore migration and native event deduplication. The final owned suite contains 17 tests, executed in short CPU batches, and six separate legacy Torch RNN tests pass, including monthly inputs. The real foundation DatasetCatalog also publishes a tiny snapshot consumed by the default client catalog constructor through approval, RPC training and verified artifact lineage.

Genuine remote SSH/SFTP/OS vault interoperability, Linux systemd/service/cgroup behavior, CUDA training and blocked GPU cancellation, driver/GPU UUID/VRAM reporting, external GPU contention, reboot/physical disk/RAM pressure, and target hardware acceptance remain unavailable without a supplied target. Full quality, physical 2 GiB and browser acceptance belong to the main integration thread.
