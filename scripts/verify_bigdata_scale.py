"""Generate real CSV/Parquet files and measure isolated bounded import workers.

This opt-in benchmark writes multi-GiB synthetic data. It never connects to an
external system and never treats a logical uncompressed size as a physical file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def generate(directory, *, rows, payload_bytes):
    import pyarrow as pa
    import pyarrow.parquet as pq

    directory.mkdir(parents=True, exist_ok=True)
    csv_path, parquet_path = directory / "synthetic.csv", directory / "synthetic.parquet"
    schema = pa.schema([("row_id", pa.int64()), ("amount", pa.int64()), ("payload", pa.string())])
    payload = "0123456789abcdef" * (payload_bytes // 16) + "x" * (payload_bytes % 16)
    writer = pq.ParquetWriter(parquet_path, schema, compression="NONE", use_dictionary=False,
                             write_statistics=True, data_page_size=1024 * 1024)
    try:
        with csv_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write("row_id,amount,payload\n")
            for first in range(0, rows, 8192):
                end = min(rows, first + 8192)
                values = list(range(first, end))
                stream.writelines(f"{value},{value % 10000},{payload}\n" for value in values)
                writer.write_table(pa.Table.from_arrays([
                    pa.array(values, type=pa.int64()),
                    pa.array([value % 10000 for value in values], type=pa.int64()),
                    pa.array([payload] * len(values), type=pa.string()),
                ], schema=schema), row_group_size=8192)
    finally:
        writer.close()
    cycles, remainder = divmod(rows, 10000)
    return {"rows": rows, "amount_sum": cycles * 9999 * 10000 // 2 + remainder * (remainder - 1) // 2,
            "files": [{"path": str(path), "size_bytes": path.stat().st_size, "sha256": digest(path)}
                      for path in (csv_path, parquet_path)]}


def worker(input_path, output_dir, expected):
    sys.path.insert(0, str(ROOT))
    from data.dataset_catalog import DatasetCatalog
    from data.import_pipeline import create_import_plan, run_import
    from data.sources.dataset import DatasetDataSource
    from infrastructure.resource_budget import BudgetSpec

    scope = {"owner_id": "scale-fixture", "session_id": "scale-session", "workspace_id": ""}
    catalog = DatasetCatalog(output_dir / "catalog", **scope)
    budget = BudgetSpec.from_dict({"max_input_bytes": 8 * 1024 ** 3, "max_output_bytes": 8 * 1024 ** 3,
                                   "max_rows": 10_000_000, "max_run_seconds": 1200,
                                   "rss_bytes": 2 * 1024 ** 3, "duckdb_memory_bytes": 1024 ** 3,
                                   "batch_bytes": 16 * 1024 ** 2, "threads": 2, "temp_bytes": 8 * 1024 ** 3})
    source = None
    started = time.perf_counter()
    try:
        plan = create_import_plan(input_path, **scope, budget=budget)
        manifest = run_import(plan, catalog=catalog)
        handle = catalog.get(manifest["dataset_id"], snapshot_id=manifest["snapshot_id"], verify=True)
        source = DatasetDataSource(handle, budget=budget)
        table = source.list_tables()[0]
        quoted = '"' + table.replace('"', '""') + '"'
        frame, error = source.execute_query(f"SELECT COUNT(*) AS n, SUM(amount) AS amount_sum FROM {quoted}")
        if error:
            raise RuntimeError(error)
        actual = {"rows": int(frame.iloc[0]["n"]), "amount_sum": int(frame.iloc[0]["amount_sum"])}
        if actual != {key: expected[key] for key in actual}:
            raise AssertionError({"expected": expected, "actual": actual})
        result = {"ok": True, "input_size_bytes": input_path.stat().st_size,
                  "input_sha256": digest(input_path), "elapsed_ms": (time.perf_counter() - started) * 1000,
                  "dataset_id": manifest["dataset_id"], "snapshot_id": manifest["snapshot_id"],
                  "actual": actual, "shard_count": len(manifest["files"])}
        (output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    finally:
        if source is not None:
            source.close()
        closer = getattr(catalog, "close", None)
        if callable(closer):
            closer()


def directory_bytes(root):
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except FileNotFoundError:
            # Staging is atomically renamed/removed by the observed worker.
            continue
    return total


def benchmark(directory, *, trials, rows, payload_bytes, require_gib):
    import psutil

    if psutil.disk_usage(str(directory.parent)).free < max(12 * 1024 ** 3, 4 * rows * payload_bytes):
        raise ValueError("Insufficient disk budget for synthetic scale acceptance")
    input_record = directory / "inputs.json"
    if input_record.is_file():
        expected = json.loads(input_record.read_text(encoding="utf-8"))
        for item in expected["files"]:
            if Path(item["path"]).stat().st_size != item["size_bytes"] or digest(item["path"]) != item["sha256"]:
                raise ValueError("Previously generated scale input changed")
    else:
        expected = generate(directory / "inputs", rows=rows, payload_bytes=payload_bytes)
        input_record.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8", newline="\n")
    measurements = []
    external_input_bytes = sum(item["size_bytes"] for item in expected["files"]
                               if not Path(item["path"]).resolve().is_relative_to(directory.resolve()))
    for item in expected["files"]:
        if item["size_bytes"] < require_gib * 1024 ** 3:
            raise AssertionError("Physical input file is smaller than the required acceptance size")
        for trial in range(trials):
            output = directory / f"{Path(item['path']).suffix[1:]}-trial-{trial + 1}"
            output.mkdir()
            process_temp = output / "process-temp"
            process_temp.mkdir()
            environment = dict(os.environ, BAA_DATA_DIR=str(output / "runtime"), TEMP=str(process_temp), TMP=str(process_temp),
                               TMPDIR=str(process_temp), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
            command = [sys.executable, "-B", "-I", str(Path(__file__).resolve()), "--worker", item["path"],
                       "--output", str(output), "--expected", json.dumps(expected)]
            peak_rss, peak_tree_rss, peak_disk, peak_temp, peak_accounted_disk = 0, 0, 0, 0, 0
            started = time.monotonic()
            with (output / "worker.log").open("wb") as log:
                process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
                tracker = psutil.Process(process.pid)
                try:
                    while process.poll() is None:
                        try:
                            rss = tracker.memory_info().rss
                            tree_rss = rss + sum(child.memory_info().rss for child in tracker.children(recursive=True) if child.is_running())
                            peak_rss, peak_tree_rss = max(peak_rss, rss), max(peak_tree_rss, tree_rss)
                        except psutil.Error:
                            pass
                        peak_disk = max(peak_disk, directory_bytes(output))
                        temporary = directory_bytes(process_temp) + directory_bytes(output / "runtime" / "dataset-plans")
                        for catalog_part in (output / "catalog").glob("*"):
                            temporary += directory_bytes(catalog_part / "operations") + directory_bytes(catalog_part / "staging")
                        peak_temp = max(peak_temp, temporary)
                        peak_accounted_disk = max(peak_accounted_disk, external_input_bytes + directory_bytes(directory))
                        if peak_rss > 2 * 1024 ** 3 or peak_tree_rss > 3 * 1024 ** 3 or time.monotonic() - started > 1200:
                            raise RuntimeError("Scale worker exceeded measured memory/runtime budget")
                        time.sleep(0.2)
                    if process.returncode:
                        raise RuntimeError(f"Scale worker failed; inspect {output / 'worker.log'}")
                except BaseException:
                    for child in tracker.children(recursive=True):
                        try:
                            child.kill()
                        except psutil.Error:
                            pass
                    process.kill()
                    process.wait(timeout=30)
                    raise
            result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            result.update(format=Path(item["path"]).suffix[1:], trial=trial + 1,
                          peak_worker_rss_bytes=peak_rss, peak_process_tree_rss_bytes=peak_tree_rss,
                          peak_output_directory_bytes=max(peak_disk, directory_bytes(output)),
                          peak_temporary_bytes=peak_temp,
                          peak_accounted_disk_bytes=max(peak_accounted_disk, external_input_bytes + directory_bytes(directory)))
            measurements.append(result)
    report = {"version": 1, "kind": "local_physical_file_scale_acceptance", "expected": expected,
              "trials": measurements, "remote_gpu_tested": False, "live_clusters_tested": []}
    (directory / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"physical_inputs": [{"size_bytes": item["size_bytes"], "sha256": item["sha256"]} for item in expected["files"]],
                      "passed_trials": len(measurements), "peak_tree_rss_bytes": max(item["peak_process_tree_rss_bytes"] for item in measurements)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected")
    parser.add_argument("--generate-only", action="store_true", help="prepare physical inputs without running implementation workers")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--rows", type=int, default=2_200_000)
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument("--require-gib", type=float, default=2.0)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.output, json.loads(args.expected))
    elif args.generate_only:
        directory = args.output.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        if any(directory.iterdir()):
            raise ValueError("Scale input generation requires a new empty output directory")
        import psutil
        if psutil.disk_usage(str(directory)).free < 4 * args.rows * args.payload_bytes + 8 * 1024 ** 3:
            raise ValueError("Insufficient disk budget for physical scale inputs")
        result = generate(directory / "inputs", rows=args.rows, payload_bytes=args.payload_bytes)
        (directory / "inputs.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({"rows": result["rows"], "physical_bytes": [item["size_bytes"] for item in result["files"]]}))
    else:
        if not 1 <= args.trials <= 5 or args.rows < 1 or not 16 <= args.payload_bytes <= 65536:
            raise ValueError("Invalid scale parameters")
        benchmark(args.output.resolve(), trials=args.trials, rows=args.rows, payload_bytes=args.payload_bytes, require_gib=args.require_gib)


if __name__ == "__main__":
    main()
