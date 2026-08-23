#!/usr/bin/env python3
"""M3 restricted remote-runner entry point.

The local app calls only its fixed ``--preflight --json`` mode over a
fingerprint-trusted SSH connection. Training task execution is added in a later
M3 step; this module deliberately does not accept arbitrary shell commands.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys


def preflight() -> int:
    try:
        import torch
    except ImportError:
        print(json.dumps({"status": "not_ready", "message": "PyTorch 未安装"}))
        return 2
    if not torch.cuda.is_available():
        print(json.dumps({"status": "not_ready", "message": "未检测到可用 CUDA"}))
        return 2
    print(json.dumps({
        "status": "ready", "version": "0.1", "python": platform.python_version(),
        "cuda": True, "gpu_name": torch.cuda.get_device_name(0),
    }, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.preflight and args.json:
        return preflight()
    parser.error("only --preflight --json is supported")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
