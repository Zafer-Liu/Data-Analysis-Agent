#!/usr/bin/env python3
"""Fixed RPCv1 proxy and administrator-managed persistent service entry point."""
from __future__ import annotations

import argparse
import json
import sys

from remote_runner.protocol import MAX_FRAME, RunnerError, decode_frame, response
from remote_runner.supervisor import default_root, proxy_frame, serve


def rpc_stdio(root=None):
    # stdout contains bounded protocol frames only, even on malformed input.
    for _ in range(1000):
        raw = sys.stdin.buffer.readline(MAX_FRAME + 2)
        if not raw:
            return 0
        request_id = "invalid"
        try:
            frame = decode_frame(raw)
            request_id = frame["request_id"]
            out = proxy_frame(raw, root)
        except RunnerError as exc:
            out = response(request_id, error=exc)
        except (OSError, ValueError):
            out = response(request_id, error=RunnerError("supervisor_unavailable", "Persistent runner service is unavailable"))
        sys.stdout.buffer.write(out + b"\n")
        sys.stdout.buffer.flush()
        if len(raw) > MAX_FRAME:
            return 2
    return 0


def preflight(root=None):
    try:
        raw = json.dumps({"version": 1, "request_id": "preflight", "operation": "handshake", "payload": {}}).encode()
        payload = json.loads(proxy_frame(raw, root))
        result = payload.get("result", {})
        ready = payload.get("ok") and result.get("ready")
        print(json.dumps({**result, "status": "ready" if ready else "not_ready", "version": result.get("runner_version", "")}, ensure_ascii=False))
        return 0 if ready else 2
    except (OSError, RunnerError):
        print('{"status":"not_ready","message":"Persistent runner service is unavailable"}')
        return 2


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rpc-v1", action="store_true")
    mode.add_argument("--serve", action="store_true")
    mode.add_argument("--preflight", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", default=None, help="Administrator service root; never supplied by RPC tasks")
    parser.add_argument("--device", default="cuda:0", help="Service device; cpu is an explicit local test profile")
    args = parser.parse_args()
    if args.rpc_v1:
        return rpc_stdio(args.root)
    if args.preflight:
        return preflight(args.root)
    serve(args.root, device=args.device)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
