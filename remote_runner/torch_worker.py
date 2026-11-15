"""Fixed builtin worker. Administrative argv only; model inputs are bounded JSON/Parquet."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import threading
import time

from remote_runner.protocol import KERNEL_VERSION, MAX_FRAME, RunnerError, canonical, validate_budget, validate_params
from remote_runner.safe_io import atomic_json, file_sha256, read_json, safe_path, validate_safetensors, verify_files


def probe(device):
    import torch
    ready = device == "cpu" or torch.cuda.is_available()
    result = {"ready": ready, "python": platform.python_version(), "torch": str(torch.__version__),
              "cuda": device.startswith("cuda") and bool(torch.cuda.is_available()),
              "cuda_version": str(torch.version.cuda or ""), "gpu_name": "", "gpu_uuid": ""}
    if device.startswith("cuda") and ready:
        try:
            props = torch.cuda.get_device_properties(torch.device(device))
            gpu_uuid = str(getattr(props, "uuid", ""))
            if gpu_uuid and not gpu_uuid.startswith("GPU-"):
                gpu_uuid = "GPU-" + gpu_uuid
            result.update(gpu_name=props.name, gpu_uuid=gpu_uuid, total_memory=props.total_memory)
        except (RuntimeError, AssertionError):
            result["ready"] = False
    return result


def write_bundle(result, root, budget):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from safetensors.torch import save_file
    root = Path(root)
    metadata = result.metadata
    if metadata.get("kernel_version") != KERNEL_VERSION:
        raise RunnerError("runner_incompatible", "Unexpected kernel version")
    if len(canonical(metadata)) > MAX_FRAME:
        raise RunnerError("budget_exceeded", "Model metadata exceeds the 1 MiB JSON limit")
    state = {name: tensor.detach().cpu().contiguous() for name, tensor in result.model.state_dict().items()}
    save_file(state, str(root / "model.safetensors"), metadata={"kernel_version": KERNEL_VERSION})
    validate_safetensors(root / "model.safetensors", budget["max_output_bytes"])
    atomic_json(root / "model.json", metadata)
    for i, frame in enumerate(result.tables):
        frame = frame.copy()
        # Legacy metric tables mix text and numeric values in one column. Store a
        # lossless display string there; actual numeric predictions stay numeric.
        for name in frame.select_dtypes(include=["object"]).columns:
            values = frame[name].dropna()
            if any(not isinstance(v, str) for v in values):
                frame[name] = frame[name].map(lambda v: None if v is None else str(v))
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), root / f"table{i}.parquet")
    atomic_json(root / "metrics.json", {"kernel_version": KERNEL_VERSION, "markdown": result.markdown,
                                        "tables": [f"table{i}.parquet" for i in range(len(result.tables))],
                                        "preview_limit": 10000, "complete_predictions": "predictions.parquet"})
    entries, total = [], 0
    for path in sorted(root.iterdir()):
        safe_path(root, path.name, exists=True)
        sha, size = file_sha256(path, max_bytes=budget["max_output_bytes"])
        total += size
        entries.append({"relative_path": path.name, "format": path.suffix[1:], "size_bytes": size, "sha256": sha})
    if total > budget["max_output_bytes"] or len(entries) > min(8, budget["max_files"]):
        raise RunnerError("budget_exceeded", "Model bundle exceeds output byte/file budget")
    return {"version": 1, "kernel_version": KERNEL_VERSION, "files": entries}


def load_model_bundle(root, *, max_bytes=256 * 1024**2):
    """Load only versioned builtin architecture + validated safetensors, never pickle."""
    import torch
    from torch import nn
    from safetensors.torch import load_file
    root = Path(root)
    meta = read_json(safe_path(root, "model.json", exists=True))
    if meta.get("kernel_version") != KERNEL_VERSION:
        raise RunnerError("runner_incompatible", "Model kernel version is unsupported")
    p = validate_params(meta.get("family"), meta.get("params"))
    weight_path = safe_path(root, "model.safetensors", exists=True)
    validate_safetensors(weight_path, max_bytes)
    if meta["family"] == "Torch_MLP":
        from remote_runner.protocol import integer
        width = integer(meta.get("encoded_features"), "encoded_features", 1, 4096)
        output = 1 if p["mode"] in {"mlp_reg", "regression"} else len(meta.get("class_labels", []))
        integer(output, "output classes", 1, 256)
        model = nn.Sequential(nn.Linear(width, p["hidden_size"]), nn.ReLU(), nn.Dropout(p["dropout"]), nn.Linear(p["hidden_size"], output))
    else:
        from Function.Analyze.Torch_RNN.kernel import build_model
        model = build_model(nn, p["cell"], p["hidden_size"], p["dropout"])
    try:
        model.load_state_dict(load_file(str(weight_path), device="cpu"), strict=True)
    except (ValueError, RuntimeError) as exc:
        raise RunnerError("artifact_corrupt", "Weights do not match the builtin architecture") from exc
    return model.eval(), meta


def run(root, attempt):
    from remote_runner.supervisor import identity
    root = Path(root).absolute()
    spec = read_json(safe_path(root, "spec.json", exists=True))
    if spec.get("attempt_id") != attempt:
        raise RunnerError("attempt_conflict", "Worker attempt does not match persisted spec")
    budget = validate_budget(spec["budget"])
    atomic_json(root / "identity.json", {"attempt_id": attempt, "identity": identity()})
    stopped, outcome_lock = threading.Event(), threading.Lock()
    deadline = time.monotonic() + min(budget["max_run_seconds"], max(0, spec["expires_at"] - time.time()))
    def fail_exit(code, message):
        with outcome_lock:
            atomic_json(root / "outcome.json", {"attempt_id": attempt, "ok": False,
                                                  "error": {"code": code, "message": message}})
        os._exit(3)
    def watchdog():
        import psutil
        process = psutil.Process()
        from remote_runner.supervisor import _disk_usage
        while not stopped.wait(.2):
            try:
                if time.monotonic() >= deadline or time.time() >= spec["expires_at"]:
                    fail_exit("deadline_expired", "Worker durable deadline expired")
                if (root / "cancel.json").exists():
                    fail_exit("canceled", "Worker observed durable cancellation")
                if process.memory_info().rss > budget["rss_bytes"]:
                    fail_exit("budget_exceeded", "Worker RSS budget exceeded")
                cpu = process.cpu_times()
                if cpu.user + cpu.system > budget["max_cpu_seconds"]:
                    fail_exit("budget_exceeded", "Worker CPU budget exceeded")
                for folder, limit in (("scratch", budget["temp_bytes"]), ("output", budget["max_output_bytes"])):
                    size, count = _disk_usage(root / folder)
                    if size > limit:
                        fail_exit("budget_exceeded", f"Worker {folder} budget exceeded")
            except RunnerError as exc:
                fail_exit(exc.code, str(exc))
            except OSError:
                fail_exit("worker_io_failed", "Worker budget inspection failed")
    threading.Thread(target=watchdog, name="training-budget-watchdog", daemon=True).start()
    try:
        wait_until = time.monotonic() + 15
        while not (root / "start.json").exists():
            if time.monotonic() > wait_until:
                raise RunnerError("launch_unconfirmed", "Supervisor did not durably acknowledge worker ownership")
            stopped.wait(.05)
        if read_json(root / "start.json").get("attempt_id") != attempt:
            raise RunnerError("attempt_conflict", "Invalid worker launch gate")
        verify_files(root / "input", spec["input_manifest"], budget)
        # Import only these trusted, installed kernels. No module name comes from input.
        from Function.Analyze.Torch_MLP.kernel import train_mlp
        from Function.Analyze.Torch_RNN.kernel import train_rnn
        from types import SimpleNamespace
        plan = spec["plan"]
        params = validate_params(plan["family"], plan["params"])
        counter = 0
        def progress(pct, message):
            nonlocal counter
            counter += 1
            atomic_json(root / "progress.json", {"attempt_id": attempt, "counter": counter,
                                                   "progress": int(pct), "message": str(message)[:512]})
        source = SimpleNamespace(root=root / "input", manifest=spec["input_manifest"])
        kernel = train_mlp if plan["family"] == "Torch_MLP" else train_rnn
        result = kernel(source, params, device=plan["device"], budget=budget, progress=progress,
                        cancel=lambda: (root / "cancel.json").exists() or time.monotonic() >= deadline,
                        output_root=root / "output", scratch=root / "scratch")
        from remote_runner.protocol import digest
        result.metadata["lineage"] = {"input_manifest_hash": digest(spec["input_manifest"]),
                                      "dataset_id": spec["input_manifest"].get("dataset_id"),
                                      "snapshot_id": spec["input_manifest"].get("snapshot_id"),
                                      "plan_hash": spec["plan_hash"], "task_id": spec["task_id"],
                                      "runner_instance_id": spec["runner_instance_id"]}
        manifest = write_bundle(result, root / "output", budget)
        if (root / "cancel.json").exists() or time.monotonic() >= deadline:
            raise RunnerError("canceled", "Cancellation/deadline preceded output publication")
        with outcome_lock:
            atomic_json(root / "outcome.json", {"attempt_id": attempt, "ok": True, "artifacts": manifest})
    except Exception as exc:
        with outcome_lock:
            atomic_json(root / "outcome.json", {"attempt_id": attempt, "ok": False,
                                                  "error": {"code": getattr(exc, "code", "training_failed"),
                                                            "message": str(exc)[:1000]}})
        return 2
    finally:
        stopped.set()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--root")
    parser.add_argument("--attempt")
    args = parser.parse_args()
    if args.probe:
        try:
            print(canonical(probe(args.device)).decode("utf-8"))
            return 0
        except ImportError:
            print('{"ready":false,"cuda":false}')
            return 0
    if not args.root or not args.attempt:
        parser.error("worker requires an administrator-allocated root and attempt")
    return run(args.root, args.attempt)


if __name__ == "__main__":
    raise SystemExit(main())
