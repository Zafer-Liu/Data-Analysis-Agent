"""Task confined regular files, streamed integrity checks and safe model headers."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
from pathlib import Path

from remote_runner.protocol import MAX_FRAME, RunnerError, canonical, relative_path, validate_tensor


def safe_path(root, relative, *, exists=False):
    relative = relative_path(relative)
    root = Path(root).absolute()
    # Check each existing component, including root; resolve() alone hides links.
    for item in [root, *root.parents]:
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise RunnerError("invalid_path", "Linked task root is forbidden")
    path = root
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise RunnerError("invalid_path", "Linked input/output path is forbidden")
    if exists:
        try:
            info = path.stat(follow_symlinks=False)
        except FileNotFoundError as exc:
            raise RunnerError("input_missing", "Expected file has not been uploaded", relative_path=relative) from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RunnerError("invalid_path", "Only singly-linked regular files are accepted")
    return path


def file_sha256(path, *, max_bytes=None, cancel=None):
    h, total = hashlib.sha256(), 0
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RunnerError("invalid_path", "Only singly-linked regular files are accepted")
        while block := stream.read(1024 * 1024):
            if cancel:
                cancel()
            total += len(block)
            if max_bytes is not None and total > max_bytes:
                raise RunnerError("budget_exceeded", "File exceeds byte budget")
            h.update(block)
    return h.hexdigest(), total


def verify_files(root, manifest, budget, *, cancel=None):
    declared = {f["relative_path"] for f in manifest["files"]}
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            safe_path(root, rel, exists=name in names)
            if name in names and rel not in declared:
                raise RunnerError("invalid_path", "Undeclared input file", relative_path=rel)
    for entry in manifest["files"]:
        path = safe_path(root, entry["relative_path"], exists=True)
        if cancel:
            cancel()
        sha, size = file_sha256(path, max_bytes=budget["max_input_bytes"], cancel=cancel)
        if size != entry["size_bytes"] or sha != entry["sha256"]:
            raise RunnerError("artifact_corrupt", "Input size or SHA-256 mismatch", relative_path=entry["relative_path"])
        if entry.get("format", path.suffix[1:]) == "json":
            read_json(path)


def read_json(path, limit=MAX_FRAME):
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise RunnerError("budget_exceeded", "JSON file exceeds limit")
    try:
        return json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RunnerError("invalid_json", "Malformed JSON file") from exc


def atomic_json(path, value):
    import tempfile
    import time
    path = Path(path)
    data = canonical(value)
    descriptor, temporary = tempfile.mkstemp(prefix=".baa-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers can briefly hold a non-delete-sharing handle. Retry
        # only that OS sharing failure, with a bounded total delay.
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 19:
                    raise
                time.sleep(.01)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_safetensors(path, max_bytes):
    size = Path(path).stat().st_size
    if size > max_bytes or size < 10:
        raise RunnerError("invalid_tensor", "Invalid safetensors size")
    with open(path, "rb") as stream:
        header_len = struct.unpack("<Q", stream.read(8))[0]
        if header_len > min(16 * 1024**2, size - 8):
            raise RunnerError("invalid_tensor", "Invalid safetensors header length")
        try:
            header = json.loads(stream.read(header_len))
        except (ValueError, UnicodeError) as exc:
            raise RunnerError("invalid_tensor", "Invalid safetensors header") from exc
    if not isinstance(header, dict) or len(header) > 4096:
        raise RunnerError("invalid_tensor", "Too many tensors")
    widths = {"F16": 2, "BF16": 2, "F32": 4, "F64": 8, "I8": 1, "U8": 1, "I16": 2, "I32": 4, "I64": 8, "BOOL": 1}
    ranges, total = [], 0
    for key, spec in header.items():
        if key == "__metadata__":
            continue
        if not isinstance(spec, dict):
            raise RunnerError("invalid_tensor", "Invalid tensor descriptor")
        count = validate_tensor(spec.get("shape"), spec.get("dtype"))
        total += count
        offsets = spec.get("data_offsets")
        if (not isinstance(offsets, list) or len(offsets) != 2 or any(type(x) is not int for x in offsets)
                or not 0 <= offsets[0] <= offsets[1] <= size - header_len - 8
                or offsets[1] - offsets[0] != count * widths[spec["dtype"]]):
            raise RunnerError("invalid_tensor", "Invalid tensor data offsets")
        ranges.append(tuple(offsets))
    if total > 16_777_216:
        raise RunnerError("invalid_tensor", "Model exceeds parameter limit")
    previous = 0
    for start, end in sorted(ranges):
        if start != previous:
            raise RunnerError("invalid_tensor", "Overlapping or sparse tensor data")
        previous = end
    if previous != size - header_len - 8:
        raise RunnerError("invalid_tensor", "Unreferenced tensor data")
    return {"tensor_count": len(ranges), "parameter_count": total}
