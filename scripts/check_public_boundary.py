#!/usr/bin/env python3
"""Reject private project material in the index or outgoing Git commits."""
from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import re
import subprocess
import sys

PRIVATE_ROOTS = frozenset({"docs", "tests", "test"})
PRIVATE_SCRIPTS = frozenset({
    "scripts/generate_tool_inventory.py", "scripts/run_required_regressions.py",
    "scripts/run_optimization_baseline.py", "scripts/verify_bigdata_scale.py",
})


def git(*args):
    result = subprocess.run(["git", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError("Git publication check failed; no push is allowed until the repository can be inspected.")
    return result.stdout


def private_paths(raw):
    violations = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        name = item.decode("utf-8", "surrogateescape")
        normalized = name.replace("\\", "/").casefold()
        parts = PurePosixPath(normalized).parts
        if parts and (parts[0] in PRIVATE_ROOTS or normalized in PRIVATE_SCRIPTS):
            violations.append(name)
    return sorted(set(violations))


def check_tree(revision):
    return private_paths(git("ls-tree", "-r", "--name-only", "-z", revision))


def outgoing(lines):
    seen = set()
    violations = []
    for line in lines:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 4:
            raise RuntimeError("Malformed pre-push update; refusing publication.")
        local_ref, local_oid, remote_ref, remote_oid = parts
        if not all(re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", oid) for oid in (local_oid, remote_oid)):
            raise RuntimeError("Invalid object identity; refusing publication.")
        if not local_oid.strip("0"):
            continue
        # Always inspect the destination tree, even when its commit already
        # exists on another remote branch. Tags are resolved to commits too.
        local_commit = git("rev-parse", local_oid + "^{commit}").strip().decode("ascii")
        revisions = [local_commit]
        if remote_oid.strip("0"):
            remote_commit = git("rev-parse", remote_oid + "^{commit}").strip().decode("ascii")
            revisions.extend(git("rev-list", local_commit, "^" + remote_commit).decode("ascii").splitlines())
        else:
            revisions.extend(git("rev-list", local_commit, "--not", "--remotes").decode("ascii").splitlines())
        for revision in revisions:
            if revision in seen:
                continue
            seen.add(revision)
            for name in check_tree(revision):
                violations.append((remote_ref, revision[:12], name))
    return violations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true", help="inspect pre-push stdin and every newly published commit")
    parser.add_argument("remote", nargs="?")
    parser.add_argument("url", nargs="?")
    options = parser.parse_args()
    try:
        if options.push:
            failures = outgoing(sys.stdin)
            for ref, revision, name in failures:
                print(f"Private material blocked: {ref} {revision} {name}", file=sys.stderr)
        else:
            failures = private_paths(git("ls-files", "-z", "--cached"))
            for name in failures:
                print(f"Private material is tracked: {name}", file=sys.stderr)
        if failures:
            print("Keep private documents, local tests and acceptance scripts outside Git tracking.", file=sys.stderr)
            return 1
        print("Public repository boundary passed.")
        return 0
    except (OSError, RuntimeError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
