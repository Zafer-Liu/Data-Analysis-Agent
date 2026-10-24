"""Standalone TimesFM adapter. Weights must already exist; never downloads on startup."""
from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import threading
import time

MODEL = "google/timesfm-3.0-pytorch"
REVISION = "43046b85ec22d584a13f8098c2ed39c889e129c2"
state = {"ready": False, "status": "loading", "device": "", "gpu": None, "error": ""}
model = None
gate = threading.Lock()


def load_model(directory, device):
    global model
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        from timesfm3 import ModelConfig, TimesFM3Forecaster
        import torch
        torch.set_num_threads(4)
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; select CPU or install a compatible PyTorch runtime")
        model = TimesFM3Forecaster(ModelConfig(checkpoint_path=str(directory), device=device,
                                               per_core_batch_size=1, local_files_only=True))
        state.update(ready=True, status="ready", device=device,
                     gpu=torch.cuda.get_device_name(0) if device == "cuda" else None)
    except Exception as exc:
        state.update(ready=False, status="failed", error=str(exc)[:400])


def validate(payload):
    import numpy as np
    if not isinstance(payload, dict):
        raise ValueError("Input must be a JSON object")
    horizon = payload.get("horizon", 7)
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 256:
        raise ValueError("horizon must be within 1–256")
    contexts = np.asarray(payload.get("context"), dtype=np.float32)
    if contexts.ndim != 2 or not 1 <= contexts.shape[0] <= 8 or not 2 <= contexts.shape[1] <= 2048:
        raise ValueError("context must have shape [1–8 targets, 2–2048 historical points]")
    arrays = []
    for key, size in (("past_only_covariates", contexts.shape[1]),
                      ("past_future_covariates", contexts.shape[1] + horizon)):
        value = payload.get(key)
        if value is not None:
            value = np.asarray(value, dtype=np.float32)
            if value.ndim != 2 or not 1 <= value.shape[0] <= 31 or value.shape[1] != size:
                raise ValueError(f"{key} must be a nonempty matrix with {size} columns")
        arrays.append(value)
    if contexts.shape[0] + sum(a.shape[0] for a in arrays if a is not None) > 32:
        raise ValueError("Targets and covariates together must not exceed 32 variates")
    for a in [contexts, *arrays]:
        if a is not None and (not np.isfinite(a).all() or np.max(np.abs(a)) > 1e15):
            raise ValueError("All inputs must be finite and within +/-1e15")
    names = payload.get("target_names") or [f"target_{i+1}" for i in range(len(contexts))]
    if not isinstance(names, list) or len(names) != len(contexts) or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError("target_names must uniquely name every target")
    for key in ("make_positive", "use_symmetric_averaging"):
        if key in payload and not isinstance(payload[key], bool):
            raise ValueError(f"{key} must be boolean")
    return contexts, horizon, names, arrays


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return  # Do not log model inputs, paths, tokens or raw responses.

    def reply(self, code, value):
        raw = json.dumps(value, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        token = os.environ.get("TIMESFM_API_TOKEN", "")
        return not token or hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)

    def do_GET(self):
        admin_secret = os.environ.get("TIMESFM_INSTANCE_SECRET", "")
        admin = bool(admin_secret) and hmac.compare_digest(self.headers.get("X-Instance-Token", ""), admin_secret)
        if not self.authorized() and not admin:
            return self.reply(401, {"detail": "Authentication required"})
        if self.path != "/health":
            return self.reply(404, {"detail": "Unknown endpoint"})
        return self.reply(200, {"service": "timesfm3-service", "model": MODEL,
            "model_revision": REVISION, "api_version": 1, **state,
            "pid": os.getpid(), "instance_id": os.environ.get("TIMESFM_INSTANCE_ID", ""),
            "license": "non-commercial, non-production",
            "capabilities": {"max_targets": 8, "max_context": 2048, "max_horizon": 256,
                             "quantiles": True, "covariates": True, "batch": False}})

    def do_POST(self):
        if self.path == "/shutdown":
            secret = os.environ.get("TIMESFM_INSTANCE_SECRET", "")
            if not secret or not hmac.compare_digest(self.headers.get("X-Instance-Token", ""), secret):
                return self.reply(403, {"detail": "Managed instance token required"})
            state.update(ready=False, status="stopping")
            self.reply(200, {"status": "stopping"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if not self.authorized():
            return self.reply(401, {"detail": "Authentication required"})
        if self.path != "/forecast":
            return self.reply(404, {"detail": "Unknown endpoint"})
        if not state["ready"]:
            return self.reply(503, {"detail": "Model is not ready"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 4 * 1024 * 1024:
                return self.reply(413, {"detail": "Request must be within 4 MiB"})
            payload = json.loads(self.rfile.read(size))
            contexts, horizon, names, arrays = validate(payload)
        except (ValueError, TypeError, OverflowError):
            return self.reply(422, {"detail": "Invalid forecast matrix, names or options"})
        if not gate.acquire(blocking=False):
            return self.reply(409, {"detail": "Model is busy"})
        try:
            import numpy as np
            import torch
            started = time.perf_counter()
            with torch.inference_mode():
                output = model.predict(context=contexts, horizon=horizon,
                    past_only_covariates=arrays[0], past_future_covariates=arrays[1],
                    return_quantiles=True, make_positive=payload.get("make_positive", True),
                    use_symmetric_averaging=payload.get("use_symmetric_averaging", False))
            prediction, quantiles = np.atleast_2d(output.forecast), np.asarray(output.quantiles)
            if not np.isfinite(prediction).all() or not np.isfinite(quantiles).all():
                return self.reply(500, {"detail": "Non-finite model output"})
            self.reply(200, {"model": MODEL, "model_revision": REVISION, "target_names": names,
                "horizon": horizon, "forecast": prediction.tolist(), "quantiles": quantiles.tolist(),
                "quantile_levels": [i / 10 for i in range(1, 10)], "device": state["device"],
                "elapsed_seconds": round(time.perf_counter() - started, 5),
                "request_id": payload.get("request_id")})
        except Exception as exc:
            import torch
            oom = isinstance(exc, torch.cuda.OutOfMemoryError)
            if oom:
                torch.cuda.empty_cache()
            self.reply(503 if oom else 500, {"detail": "Insufficient GPU memory" if oom else "Model execution failed"})
        finally:
            gate.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", default=os.environ.get("TIMESFM_MODEL_DIR", "/model"))
    parser.add_argument("--host", default=os.environ.get("TIMESFM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TIMESFM_PORT", "8787")))
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default=os.environ.get("TIMESFM_DEVICE", "auto"))
    args = parser.parse_args()
    directory = Path(args.model_dir).resolve()
    if not (directory / "config.json").is_file() or not (directory / "model.safetensors").is_file():
        raise SystemExit("Model files are missing. Download explicitly or mount an existing model directory.")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = False  # A current forward pass finishes before process exit.
    threading.Thread(target=load_model, args=(directory, args.device), daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
