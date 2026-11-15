"""Run the shared regression manifest, failing if required material is absent."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import unittest


class RegressionManifestError(ValueError):
    pass


def load_manifest(root: Path) -> dict[str, list[str]]:
    path = root / "tests" / "required-regressions.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegressionManifestError(f"Cannot read required regression manifest: {exc}") from exc
    if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] != 1:
        raise RegressionManifestError("Required regression manifest must use version 1.")
    if set(payload) != {"version", "python", "frontend", "fixtures"}:
        raise RegressionManifestError("Required regression manifest has unexpected or missing fields.")
    root = root.resolve()
    result = {}
    seen = set()
    for group in ("python", "frontend", "fixtures"):
        entries = payload[group]
        if not isinstance(entries, list) or (group != "fixtures" and not entries):
            raise RegressionManifestError(f"Required regression group must be a list and cannot be empty: {group}")
        result[group] = []
        for entry in entries:
            if not isinstance(entry, str) or not entry or "\\" in entry:
                raise RegressionManifestError(f"Invalid required regression path: {entry!r}")
            relative = PurePosixPath(entry)
            if not relative.parts or relative.as_posix() != entry or relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "tests":
                raise RegressionManifestError(f"Required regression path must stay inside tests/: {entry}")
            if entry in seen:
                raise RegressionManifestError(f"Duplicate required regression path: {entry}")
            seen.add(entry)
            if group == "python" and (relative.parent != PurePosixPath("tests") or not relative.name.startswith("test_") or relative.suffix != ".py"):
                raise RegressionManifestError(f"Python regression must be a tests/test_*.py file: {entry}")
            if group == "frontend" and not relative.name.endswith(".test.mjs"):
                raise RegressionManifestError(f"Frontend regression must be a .test.mjs file: {entry}")
            target = (root / entry).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                raise RegressionManifestError(f"Required regression file is missing or outside the checkout: {entry}")
            result[group].append(entry)
    return result


def run_python(root: Path, paths: list[str]) -> int:
    sys.path.insert(0, str(root))
    suite = unittest.TestSuite()
    for relative in paths:
        tests = unittest.defaultTestLoader.discover(
            str(root / "tests"), pattern=Path(relative).name,
            top_level_dir=str(root / "tests"),
        )
        if not tests.countTestCases():
            raise RegressionManifestError(f"Required regression contains no tests: {relative}")
        suite.addTests(tests)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def run_frontend(root: Path, paths: list[str]) -> int:
    node = shutil.which("node")
    if node is None:
        raise RegressionManifestError("Node.js is required for the frontend regression suite.")
    return subprocess.run(
        [node, "--test", "--test-concurrency=1", *paths], cwd=root, check=False,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "python", "frontend"), default="all")
    parser.add_argument("--check-only", action="store_true", help="check required files without executing tests")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        manifest = load_manifest(root)
        print("Required regression files are present.", flush=True)
        if args.check_only:
            return 0
        if args.suite in {"all", "python"}:
            result = run_python(root, manifest["python"])
            if result:
                return result
        if args.suite in {"all", "frontend"}:
            return run_frontend(root, manifest["frontend"])
        return 0
    except RegressionManifestError as exc:
        print(f"Required regression check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
