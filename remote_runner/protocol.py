"""Versioned, bounded JSON RPC and builtin-only task validation (no torch import)."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import PurePosixPath

VERSION = 1
BUILD_VERSION = "1.0.0"
KERNEL_VERSION = "baa-torch-v1"
MAX_FRAME = 1024 * 1024
MAX_TENSOR_ELEMENTS = 16_777_216
OPERATIONS = {"handshake", "submit", "status", "events", "cancel", "artifact"}
TERMINAL = {"succeeded", "failed", "canceled"}


class RunnerError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise RunnerError("invalid_request", "Expected finite JSON values") from exc


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def decode_frame(raw):
    if len(raw) > MAX_FRAME:
        raise RunnerError("frame_too_large", "RPC frame exceeds 1 MiB")
    try:
        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RunnerError("invalid_json", "Invalid UTF-8 JSON frame") from exc
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != VERSION:
        raise RunnerError("runner_incompatible", "Expected RPC version 1")
    token(value.get("request_id"), "request_id")
    if value.get("operation") not in OPERATIONS or not isinstance(value.get("payload", {}), dict):
        raise RunnerError("invalid_request", "Unknown operation or invalid payload")
    return value


def response(request_id, result=None, error=None):
    value = {"version": VERSION, "request_id": request_id, "ok": error is None}
    if error is None:
        value["result"] = result
    else:
        value.update(code=error.code, error=str(error), details=error.details)
    raw = canonical(value)
    if len(raw) > MAX_FRAME:
        return canonical({"version": VERSION, "request_id": request_id, "ok": False,
                          "code": "frame_too_large", "error": "Response exceeds frame limit"})
    return raw


def token(value, name, limit=256):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise RunnerError("invalid_request", f"Invalid {name}")
    return value


def relative_path(value):
    token(value, "relative_path", 512)
    path = PurePosixPath(value)
    if (path.is_absolute() or "\\" in value or ":" in value or "\x00" in value
            or any(p in {".", "..", ""} for p in value.split("/"))
            or any(p.endswith((" ", ".")) for p in path.parts)
            or any(p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))} for p in path.parts)):
        raise RunnerError("invalid_path", "Expected a bounded, canonical relative path")
    return path.as_posix()


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise RunnerError("invalid_parameter", f"{name} must be an integer in [{low}, {high}]")
    return value


def number(value, name, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise RunnerError("invalid_parameter", f"{name} outside supported range")
    return value


BUDGET_DEFAULTS = {
    "max_run_seconds": 3600, "max_input_bytes": 2 * 1024**3, "max_output_bytes": 256 * 1024**2,
    "max_rows": 10_000_000, "max_files": 64, "max_log_bytes": 1024**2,
    "duckdb_memory_bytes": 256 * 1024**2, "rss_bytes": 2 * 1024**3, "batch_bytes": 8 * 1024**2,
    "queue_batches": 2, "threads": 2, "temp_bytes": 2 * 1024**3,
    "max_cpu_seconds": 3600, "max_scan_bytes": 1024**4,
}


def validate_budget(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(BUDGET_DEFAULTS):
        raise RunnerError("unsupported_budget", "Unknown budget fields")
    budget = dict(BUDGET_DEFAULTS, **value)
    for key, val in budget.items():
        if key.endswith("seconds"):
            number(val, key, .1, 604800)
        else:
            integer(val, key, 1, {"threads": 32, "queue_batches": 8, "max_files": 1024,
                                "max_rows": 1_000_000_000}.get(key, 1024**4))
    if budget["max_log_bytes"] < 4096:
        raise RunnerError("unsupported_budget", "Native event retention requires at least 4096 bytes")
    if max(budget["batch_bytes"] * budget["queue_batches"], budget["duckdb_memory_bytes"]) > budget["rss_bytes"]:
        raise RunnerError("unsupported_budget", "Batch queues and DuckDB allocator must fit within RSS budget")
    return budget


def validate_params(family, value):
    if family not in {"Torch_MLP", "Torch_RNN"}:
        raise RunnerError("unsupported_task", "Only builtin Torch_MLP and Torch_RNN kernels are supported")
    if not isinstance(value, dict):
        raise RunnerError("invalid_parameter", "params must be an object")
    common = {"target_column", "epochs", "seed", "batch_size", "hidden_size", "learning_rate", "validation_split", "dropout"}
    allowed = common | ({"mode", "max_categories", "max_features"} if family == "Torch_MLP" else
                        {"time_column", "sequence_column", "cell", "window", "steps", "mc_samples", "max_sequences"})
    if set(value) - allowed:
        raise RunnerError("invalid_parameter", "Unsupported model parameters", fields=sorted(set(value) - allowed))
    p = {"epochs": 60 if family == "Torch_MLP" else 120, "seed": 42, "batch_size": 256 if family == "Torch_MLP" else 16,
         "hidden_size": 32, "learning_rate": .001, "validation_split": .2 if family == "Torch_MLP" else .15, "dropout": .1, **value}
    token(p.get("target_column"), "target_column", 256)
    for key, bounds in {"epochs": (1, 500), "seed": (0, 2**31 - 1), "batch_size": (1, 1024), "hidden_size": (1, 256)}.items():
        integer(p[key], key, *bounds)
    number(p["learning_rate"], "learning_rate", .000001, .1)
    number(p["validation_split"], "validation_split", .05, .4)
    number(p["dropout"], "dropout", 0, .8)
    if family == "Torch_MLP":
        p = {"mode": "classification", "max_categories": 64, "max_features": 4096, **p}
        if p["mode"] not in {"classification", "regression", "mlp_cls", "mlp_reg"}:
            raise RunnerError("invalid_parameter", "Unsupported MLP mode")
        p["mode"] = {"mlp_cls": "classification", "mlp_reg": "regression"}.get(p["mode"], p["mode"])
        integer(p["max_categories"], "max_categories", 2, 256)
        integer(p["max_features"], "max_features", 1, 4096)
    else:
        p = {"time_column": "", "sequence_column": "", "cell": "gru", "window": 10, "steps": 12,
             "mc_samples": 30, "max_sequences": 1024, **p}
        if p["cell"] not in {"gru", "lstm"}:
            raise RunnerError("invalid_parameter", "Unsupported RNN cell")
        for key, bounds in {"window": (1, 512), "steps": (1, 1024), "mc_samples": (1, 100), "max_sequences": (1, 10000)}.items():
            integer(p[key], key, *bounds)
        for key in ("time_column", "sequence_column"):
            if p[key]:
                token(p[key], key, 256)
    return p


def validate_manifest(value, budget):
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise RunnerError("invalid_manifest", "Expected a version 1 input manifest")
    files = value.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= budget["max_files"]:
        raise RunnerError("invalid_manifest", "Invalid input file count")
    integer(value.get("row_count", 0), "row_count", 0, budget["max_rows"])
    seen, total = set(), 0
    for entry in files:
        if not isinstance(entry, dict):
            raise RunnerError("invalid_manifest", "Invalid file entry")
        path = relative_path(entry.get("relative_path"))
        if path.casefold() in seen:
            raise RunnerError("invalid_manifest", "Duplicate input path")
        if any(path.casefold().startswith(existing + "/") or existing.startswith(path.casefold() + "/") for existing in seen):
            raise RunnerError("invalid_manifest", "Input files cannot also be path ancestors")
        seen.add(path.casefold())
        fmt = entry.get("format", path.rsplit(".", 1)[-1])
        if not isinstance(fmt, str):
            raise RunnerError("invalid_manifest", "File format must be a canonical string")
        if fmt not in {"parquet", "json"} or not path.lower().endswith("." + fmt):
            raise RunnerError("invalid_manifest", "Only canonical Parquet/JSON inputs are accepted")
        integer(entry.get("size_bytes"), "size_bytes", 1, budget["max_input_bytes"])
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            raise RunnerError("invalid_manifest", "Expected lowercase SHA-256")
        total += entry["size_bytes"]
        if fmt == "json" and entry["size_bytes"] > MAX_FRAME:
            raise RunnerError("invalid_manifest", "JSON schema exceeds frame limit")
        if "shape" in entry:
            validate_tensor(entry["shape"], entry.get("dtype", ""))
    if total > budget["max_input_bytes"] or value.get("row_count", 0) > budget["max_rows"]:
        raise RunnerError("budget_exceeded", "Input manifest exceeds budget")
    if not any(f.get("format", f["relative_path"].rsplit(".", 1)[-1]).lower() == "parquet" for f in files):
        raise RunnerError("invalid_manifest", "At least one Parquet file is required")
    canonical(value)
    return value


def validate_tensor(shape, dtype):
    if not isinstance(shape, list) or len(shape) > 8 or dtype not in {
        "float16", "float32", "float64", "int8", "uint8", "int16", "int32", "int64", "bool",
        "F16", "BF16", "F32", "F64", "I8", "U8", "I16", "I32", "I64", "BOOL"}:
        raise RunnerError("invalid_tensor", "Unsupported tensor shape or dtype")
    count = 1
    for dim in shape:
        integer(dim, "tensor dimension", 0, MAX_TENSOR_ELEMENTS)
        count *= dim
        if count > MAX_TENSOR_ELEMENTS:
            raise RunnerError("invalid_tensor", "Tensor exceeds element limit")
    return count
