"""Trusted v1 streaming torch kernels shared by local analyzers and remote workers.

Imports torch lazily. Dataset inputs are DataFrames, DatasetHandles or batches;
only the trusted worker selects kernels. No Python objects are deserialized.
"""
from __future__ import annotations

import bisect
import contextlib
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from remote_runner.protocol import KERNEL_VERSION, RunnerError, validate_budget, validate_params
from remote_runner.safe_io import file_sha256, safe_path


@dataclass
class KernelResult:
    tables: tuple
    markdown: str
    model: object
    metadata: dict

    def legacy(self):
        return (*self.tables, self.markdown)


class BatchSource:
    """Replayable, bounded Arrow/Pandas batches with disk spooling for iterators."""
    def __init__(self, data, budget, batch_rows=256, scratch=None):
        self.budget, self.batch_rows = budget, batch_rows
        self._temp = tempfile.TemporaryDirectory(prefix="baa-kernel-", dir=scratch)
        self.root = Path(self._temp.name)
        self.files, self.frame, self.rows, self.scanned = [], None, 0, 0
        if isinstance(data, pd.DataFrame):
            self.frame = data
            self.rows = len(data)
        elif hasattr(data, "manifest") and hasattr(data, "root"):
            manifest = data.manifest
            for entry in manifest["files"]:
                if entry.get("format", "parquet") == "parquet":
                    path = safe_path(data.root, entry["relative_path"], exists=True)
                    if "sha256" in entry:
                        sha, size = file_sha256(path, max_bytes=budget["max_input_bytes"])
                        if sha != entry["sha256"] or size != entry["size_bytes"]:
                            raise RunnerError("dataset_changed", "Dataset snapshot hash changed")
                    self.files.append(path)
            self.rows = self._count_files()
            if "row_count" in manifest and self.rows != manifest["row_count"]:
                raise RunnerError("invalid_manifest", "Physical Parquet row count differs from the snapshot manifest")
        else:
            import pyarrow as pa
            import pyarrow.parquet as pq
            batches = data() if callable(data) else iter(data)
            total, schema, writer = 0, None, None
            path = self.root / "batches.parquet"
            try:
                for batch in batches:
                    table = pa.Table.from_pandas(batch, preserve_index=False) if isinstance(batch, pd.DataFrame) else pa.Table.from_batches([batch]) if isinstance(batch, pa.RecordBatch) else batch
                    if not isinstance(table, pa.Table) or table.nbytes > budget["batch_bytes"]:
                        raise RunnerError("budget_exceeded", "Input batch exceeds byte budget")
                    if schema is not None and not table.schema.equals(schema, check_metadata=False):
                        raise RunnerError("schema_drift", "Batch schema changed")
                    if writer is None:
                        schema = table.schema
                        writer = pq.ParquetWriter(path, schema)
                        self.files.append(path)
                    writer.write_table(table, row_group_size=batch_rows)
                    total += table.nbytes
                    self.rows += len(table)
                    if max(total, path.stat().st_size) > min(budget["temp_bytes"], budget["max_input_bytes"]) or self.rows > budget["max_rows"]:
                        raise RunnerError("budget_exceeded", "Batch spool exceeds budget")
            finally:
                if writer is not None:
                    writer.close()
        if self.frame is not None and int(self.frame.memory_usage(index=True, deep=True).sum()) > budget["max_input_bytes"]:
            raise RunnerError("budget_exceeded", "DataFrame exceeds input byte budget")
        if not 1 <= self.rows <= budget["max_rows"]:
            raise RunnerError("budget_exceeded", "Dataset row count is outside budget")
        if self.frame is not None:
            self.columns = list(self.frame.columns)
            self.numeric = {c: pd.api.types.is_numeric_dtype(self.frame[c]) for c in self.columns}
        else:
            import pyarrow as pa
            import pyarrow.parquet as pq
            if not self.files:
                raise RunnerError("invalid_manifest", "No Parquet inputs")
            schema = pq.ParquetFile(self.files[0]).schema_arrow
            for file in self.files[1:]:
                if not pq.ParquetFile(file).schema_arrow.equals(schema, check_metadata=False):
                    raise RunnerError("schema_drift", "Parquet schemas differ")
            self.columns = schema.names
            self.numeric = {f.name: pa.types.is_integer(f.type) or pa.types.is_floating(f.type) or pa.types.is_boolean(f.type) for f in schema}
        if len(set(self.columns)) != len(self.columns) or any(not isinstance(c, str) for c in self.columns):
            raise RunnerError("schema_drift", "Expected unique string column names")

    def _count_files(self):
        import pyarrow.parquet as pq
        total, rows = 0, 0
        for path in self.files:
            total += path.stat().st_size
            rows += pq.ParquetFile(path).metadata.num_rows
            if total > self.budget["max_input_bytes"] or rows > self.budget["max_rows"]:
                raise RunnerError("budget_exceeded", "Dataset exceeds byte/row budget")
        return rows

    def batches(self, columns=None):
        if self.frame is not None:
            for i in range(0, len(self.frame), self.batch_rows):
                batch = self.frame.iloc[i:i + self.batch_rows]
                batch = batch[columns] if columns else batch
                if int(batch.memory_usage(index=True, deep=True).sum()) > self.budget["batch_bytes"]:
                    raise RunnerError("budget_exceeded", "DataFrame batch exceeds byte budget")
                self.scanned += int(batch.memory_usage(index=True, deep=True).sum())
                if self.scanned > self.budget["max_scan_bytes"]:
                    raise RunnerError("budget_exceeded", "Training scan byte budget exceeded")
                yield batch.copy()
        else:
            import pyarrow.parquet as pq
            for path in self.files:
                for batch in pq.ParquetFile(path).iter_batches(batch_size=self.batch_rows, columns=columns, use_threads=False):
                    if batch.nbytes > self.budget["batch_bytes"]:
                        raise RunnerError("budget_exceeded", "Arrow batch exceeds byte budget")
                    self.scanned += batch.nbytes
                    if self.scanned > self.budget["max_scan_bytes"]:
                        raise RunnerError("budget_exceeded", "Training scan byte budget exceeded")
                    yield batch.to_pandas()

    def parquet_files(self):
        if self.frame is not None:
            import pyarrow as pa
            import pyarrow.parquet as pq
            path = self.root / "frame.parquet"
            with pq.ParquetWriter(path, pa.Table.from_pandas(self.frame.iloc[:0], preserve_index=False).schema) as writer:
                for batch in self.batches():
                    writer.write_table(pa.Table.from_pandas(batch, preserve_index=False))
            if path.stat().st_size > self.budget["temp_bytes"]:
                raise RunnerError("budget_exceeded", "Frame spool exceeds temporary byte budget")
            return [path]
        return self.files

    def close(self):
        mapped = getattr(self, "_memmap", None)
        if mapped is not None and mapped._mmap is not None:
            mapped._mmap.close()
        self._temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _torch_setup(params, device, budget):
    import torch
    from torch import nn
    if str(device).startswith("cuda") and not torch.cuda.is_available():
        raise RunnerError("cuda_unavailable", "Requested CUDA device is unavailable")
    torch.set_num_threads(budget["threads"])
    torch.manual_seed(params["seed"])
    if str(device).startswith("cuda"):
        torch.cuda.manual_seed_all(params["seed"])
    return torch, nn, torch.device(device)


def _check(cancel):
    if cancel and cancel():
        raise RunnerError("canceled", "Training canceled")


def _train_mask(offset, n, seed, fraction):
    # Stable across batch sizes; no full-dataset permutation or index array.
    return partition_rows(np.arange(offset, offset + n, dtype=np.uint64), seed, fraction)


def partition_rows(indices, seed, fraction):
    x = np.asarray(indices, dtype=np.uint64) + np.uint64(seed)
    with np.errstate(over="ignore"):
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xbf58476d1ce4e5b9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94d049bb133111eb)
        x ^= x >> np.uint64(31)
    return x % np.uint64(1_000_000) >= int(fraction * 1_000_000)


def _category(value):
    return None if pd.isna(value) else str(value)


def _target(frame, target, regression):
    if regression:
        values = pd.to_numeric(frame[target], errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan)
        return values, np.isfinite(values)
    values = [_category(x) for x in frame[target]]
    return values, np.array([x is not None for x in values])


class PredictionWriter:
    def __init__(self, root, max_bytes):
        self.root, self.limit, self.writer = root, max_bytes, None

    def write(self, frame):
        if self.root is None or frame.empty:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pa.Table.from_pandas(frame, preserve_index=False)
        path = Path(self.root) / "predictions.parquet"
        if self.writer is None:
            self.writer = pq.ParquetWriter(path, table.schema)
        self.writer.write_table(table)
        if path.stat().st_size > self.limit:
            raise RunnerError("budget_exceeded", "Predictions exceed output budget")

    def close(self):
        if self.writer:
            self.writer.close()


def train_mlp(data, params, *, device="cpu", budget=None, progress=None, cancel=None, output_root=None, scratch=None):
    p, b = validate_params("Torch_MLP", params), validate_budget(budget)
    torch, nn, device = _torch_setup(p, device, b)
    progress = progress or (lambda *_: None)
    regression = p["mode"] in {"regression", "mlp_reg"}
    with BatchSource(data, b, p["batch_size"], scratch) as source:
        target = p["target_column"]
        if target not in source.columns or len(source.columns) < 2:
            raise RunnerError("invalid_parameter", "Target and at least one feature column are required")
        features = [c for c in source.columns if c != target]
        if len(features) > p["max_features"]:
            raise RunnerError("budget_exceeded", "Feature count exceeds model schema cap")
        numeric = [c for c in features if source.numeric[c]]
        categorical = [c for c in features if not source.numeric[c]]
        sums = np.zeros(len(numeric), np.float64)
        squares, counts = sums.copy(), sums.copy()
        categories, classes = {c: set() for c in categorical}, set()
        n_train, n_test, offset = 0, 0, 0
        for frame in source.batches():
            _check(cancel)
            y, valid = _target(frame, target, regression)
            mask = _train_mask(offset, len(frame), p["seed"], p["validation_split"]) & valid
            n_train += int(mask.sum())
            n_test += int(valid.sum() - mask.sum())
            offset += len(frame)
            if numeric:
                values = frame[numeric].to_numpy(dtype=np.float64, na_value=np.nan)[mask]
                good = np.isfinite(values)
                cleaned = np.where(good, values, 0)
                sums += cleaned.sum(axis=0)
                squares += (cleaned * cleaned).sum(axis=0)
                counts += good.sum(axis=0)
            for col in categorical:
                for value in frame.loc[mask, col]:
                    categories[col].add(_category(value))
                    if len(categories[col]) > p["max_categories"]:
                        raise RunnerError("cardinality_exceeded", f"Training feature {col} exceeds category cap")
            if not regression:
                classes.update(y[i] for i in np.flatnonzero(mask))
                if len(classes) > 256:
                    raise RunnerError("cardinality_exceeded", "Classification exceeds 256 classes")
        if n_train < 4 or n_test < 1 or (not regression and len(classes) < 2):
            raise RunnerError("insufficient_data", "Need train/validation rows and at least two training classes")
        if not np.all(np.isfinite(sums)) or not np.all(np.isfinite(squares)):
            raise RunnerError("nonfinite_training", "Training feature statistics overflowed")
        means = sums / np.maximum(counts, 1)
        deviations = np.sqrt(np.maximum(squares / np.maximum(counts, 1) - means * means, 0))
        deviations[deviations < 1e-8] = 1
        categories = {c: sorted(v, key=lambda x: (x is not None, x or "")) for c, v in categories.items()}
        labels = sorted(classes)
        label_map = {v: i for i, v in enumerate(labels)}
        width = len(numeric) + sum(len(v) + 1 for v in categories.values())
        if width > p["max_features"] or width * p["batch_size"] * 4 > b["batch_bytes"]:
            raise RunnerError("budget_exceeded", "Encoded feature tensor exceeds budget")
        if width < 1:
            raise RunnerError("insufficient_data", "No usable features")

        def transform(frame):
            blocks = []
            if numeric:
                array = frame[numeric].to_numpy(dtype=np.float64, na_value=np.nan)
                array = np.where(np.isfinite(array), array, means)
                blocks.append(((array - means) / deviations).astype(np.float32))
            for col, mapping in categories.items():
                lookup = {v: i for i, v in enumerate(mapping)}
                array = np.zeros((len(frame), len(mapping) + 1), np.float32)
                for i, value in enumerate(frame[col]):
                    array[i, lookup.get(_category(value), len(mapping))] = 1
                blocks.append(array)
            return np.concatenate(blocks, axis=1)

        output_dim = 1 if regression else len(labels)
        model = nn.Sequential(nn.Linear(width, p["hidden_size"]), nn.ReLU(), nn.Dropout(p["dropout"]), nn.Linear(p["hidden_size"], output_dim)).to(device)
        if sum(t.numel() for t in model.parameters()) > 16_777_216:
            raise RunnerError("invalid_tensor", "Model exceeds parameter limit")
        optimizer = torch.optim.Adam(model.parameters(), lr=p["learning_rate"])
        loss_fn = nn.MSELoss() if regression else nn.CrossEntropyLoss()
        losses = []
        progress(5, "准备分批 MLP 训练")
        for epoch in range(1, p["epochs"] + 1):
            model.train()
            offset, total, seen = 0, 0., 0
            for frame in source.batches():
                _check(cancel)
                y, valid = _target(frame, target, regression)
                mask = _train_mask(offset, len(frame), p["seed"], p["validation_split"]) & valid
                offset += len(frame)
                if not mask.any():
                    continue
                x = torch.from_numpy(transform(frame.loc[mask])).to(device)
                labels_batch = np.array(y)[mask] if regression else np.array([label_map[y[i]] for i in np.flatnonzero(mask)])
                y_tensor = torch.tensor(labels_batch, dtype=torch.float32 if regression else torch.long, device=device)
                optimizer.zero_grad(set_to_none=True)
                loss = loss_fn(model(x).squeeze(-1) if regression else model(x), y_tensor)
                if not torch.isfinite(loss):
                    raise RunnerError("nonfinite_training", "Training produced a nonfinite loss")
                loss.backward()
                optimizer.step()
                total += float(loss.detach().cpu()) * len(x)
                seen += len(x)
            losses.append({"epoch": epoch, "train_loss": total / seen})
            progress(5 + int(80 * epoch / p["epochs"]), f"MLP epoch {epoch}/{p['epochs']}")
        model.eval()
        offset, correct, unknown, n, abs_err, sq_err, sum_y, sum_y2 = 0, 0, 0, 0, 0., 0., 0., 0.
        details = []
        writer = PredictionWriter(output_root, b["max_output_bytes"])
        try:
            with torch.no_grad():
                for frame in source.batches():
                    _check(cancel)
                    y, valid = _target(frame, target, regression)
                    mask = ~_train_mask(offset, len(frame), p["seed"], p["validation_split"]) & valid
                    offset += len(frame)
                    if not mask.any():
                        continue
                    scores = model(torch.from_numpy(transform(frame.loc[mask])).to(device)).cpu().numpy()
                    if regression:
                        actual, predicted = np.asarray(y)[mask], scores.reshape(-1)
                        residual = predicted - actual
                        abs_err += float(np.abs(residual).sum())
                        sq_err += float((residual ** 2).sum())
                        sum_y += float(actual.sum())
                        sum_y2 += float((actual ** 2).sum())
                        detail = pd.DataFrame({"actual": actual, "predicted": predicted, "residual": residual})
                    else:
                        actual = np.array([label_map.get(y[i], -1) for i in np.flatnonzero(mask)])
                        predicted = scores.argmax(axis=1)
                        correct += int((actual == predicted).sum())
                        unknown += int((actual < 0).sum())
                        detail = pd.DataFrame({"actual": actual, "predicted": predicted, "correct": actual == predicted})
                    n += len(actual)
                    writer.write(detail)
                    remaining = 200 - sum(len(x) for x in details)
                    if remaining > 0:
                        details.append(detail.head(remaining))
        finally:
            writer.close()
        metrics = ([{"metric": "mae", "value": abs_err / n}, {"metric": "rmse", "value": math.sqrt(sq_err / n)},
                    {"metric": "r2", "value": 1 - sq_err / max(sum_y2 - sum_y**2 / n, 1e-12)}] if regression else
                   [{"metric": "accuracy", "value": correct / n}, {"metric": "n_classes", "value": len(labels)},
                    {"metric": "unknown_validation_labels", "value": unknown}])
        result = pd.DataFrame(metrics + [{"metric": "device", "value": str(device)}, {"metric": "n_train", "value": n_train}, {"metric": "n_test", "value": n_test}])
        metadata = {"kernel_version": KERNEL_VERSION, "family": "Torch_MLP", "params": p, "device": str(device),
                    "feature_order": features, "numeric_features": numeric, "means": means.tolist(), "stds": deviations.tolist(),
                    "categories": categories, "class_labels": labels, "missing_policy": "train_mean/explicit_category",
                    "unknown_category": "reserved_column", "split": "stable_row_hash_v1", "n_train": n_train, "n_test": n_test,
                    "encoded_features": width, "prediction_rows": n, "seed": p["seed"], "torch_version": torch.__version__}
        markdown = f"## PyTorch MLP {'回归' if regression else '分类'}\n\n- 设备：`{device}`\n- 训练样本：{n_train}；验证样本：{n_test}；特征：{width}；Epoch：{p['epochs']}"
        progress(100, "MLP 训练完成")
        return KernelResult((result, pd.DataFrame(losses), pd.concat(details, ignore_index=True)), markdown, model, metadata)


class LazyWindows:
    """CPU memmap windows; each sample stays within one sequence and split."""
    def __init__(self, values, segments, window, split):
        self.values, self.window, self.ranges, self.ends = values, window, [], []
        total = 0
        for segment in segments:
            start, end = (segment["start"], segment["split"]) if split == "train" else (segment["split"], segment["end"])
            count = max(0, end - start - window)
            if count:
                self.ranges.append((start + window, segment))
                total += count
                self.ends.append(total)

    def __len__(self):
        return self.ends[-1] if self.ends else 0

    def __getitem__(self, index):
        bucket = bisect.bisect_right(self.ends, index)
        previous = self.ends[bucket - 1] if bucket else 0
        start, seg = self.ranges[bucket]
        at = start + index - previous
        x = ((self.values[at - self.window:at] - seg["mean"]) / seg["std"]).astype(np.float32).reshape(-1, 1)
        y = np.float32((self.values[at] - seg["mean"]) / seg["std"])
        return x, y


def build_rnn(nn, cell, hidden, dropout):
    class SequenceModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.rnn = (nn.GRU if cell == "gru" else nn.LSTM)(1, hidden, batch_first=True)
            self.dropout = nn.Dropout(dropout)
            self.head = nn.Linear(hidden, 1)

        def forward(self, x):
            output, _ = self.rnn(x)
            return self.head(self.dropout(output[:, -1])).squeeze(-1)
    return SequenceModel()


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _sorted_series(source, params, budget):
    import duckdb
    import pyarrow.parquet as pq
    p = params
    for col in (p["target_column"], p["time_column"], p["sequence_column"]):
        if col and col not in source.columns:
            raise RunnerError("invalid_parameter", f"Column {col} is absent")
    out = source.root / "ordered.parquet"
    sequence = "CAST(raw_sequence AS VARCHAR)" if p["sequence_column"] else "''"
    stamp = ("COALESCE(TRY_CAST(TRY_CAST(raw_time AS TIMESTAMPTZ) AS TIMESTAMP), "
             "CASE WHEN regexp_full_match(CAST(raw_time AS VARCHAR), '[0-9]{4}-[0-9]{2}') "
             "THEN try_strptime(CAST(raw_time AS VARCHAR), '%Y-%m') END)") if p["time_column"] else "ordinal"
    connection = duckdb.connect()
    try:
        connection.execute("SET threads=1")
        connection.execute("SET TimeZone='UTC'")
        connection.execute(f"SET memory_limit='{budget['duckdb_memory_bytes']}B'")
        connection.execute("SET temp_directory = ?", [str(source.root / "sort-temp")])
        connection.execute(f"SET max_temp_directory_size='{max(1, budget['temp_bytes'] // 1024**2)}MB'")
        input_files = source.parquet_files()
        source.scanned += sum(path.stat().st_size for path in input_files)
        if source.scanned > budget["max_scan_bytes"]:
            raise RunnerError("budget_exceeded", "Series input exceeds scan byte budget")
        connection.read_parquet([str(x) for x in input_files]).create_view("source")
        projection = [_quote(p["target_column"]) + " AS raw_target"]
        if p["time_column"]:
            projection.append(_quote(p["time_column"]) + " AS raw_time")
        if p["sequence_column"]:
            projection.append(_quote(p["sequence_column"]) + " AS raw_sequence")
        connection.execute("CREATE VIEW numbered AS SELECT " + ", ".join(projection) + ", row_number() OVER ()-1 AS ordinal FROM source")
        connection.execute(f"CREATE VIEW projected AS SELECT coalesce({sequence}, '') AS seq, {stamp} AS ds, TRY_CAST(raw_target AS DOUBLE) AS val, ordinal FROM numbered")
        if p["time_column"] and connection.execute("SELECT count(*) FROM projected WHERE ds IS NULL").fetchone()[0]:
            raise RunnerError("invalid_parameter", "Time column contains invalid timestamps")
        # First row wins duplicate timestamps deterministically within a sequence.
        sql = "SELECT seq, ds, val FROM projected WHERE isfinite(val) QUALIFY row_number() OVER (PARTITION BY seq, ds ORDER BY ordinal)=1 ORDER BY seq, ds"
        connection.execute("COPY (" + sql + ") TO ? (FORMAT PARQUET, ROW_GROUP_SIZE 2048)", [str(out)])
    finally:
        connection.close()
    count = pq.ParquetFile(out).metadata.num_rows
    from remote_runner.supervisor import _disk_usage
    if count * 8 + 256 + _disk_usage(source.root)[0] > budget["temp_bytes"]:
        raise RunnerError("budget_exceeded", "Sorted series exceeds temporary storage budget")
    if not count:
        raise RunnerError("insufficient_data", "No finite series values")
    values = np.lib.format.open_memmap(source.root / "values.npy", mode="w+", dtype=np.float64, shape=(count,))
    source._memmap = values
    segments, previous, offset = [], None, 0
    for batch in pq.ParquetFile(out).iter_batches(batch_size=source.batch_rows, use_threads=False):
        if batch.nbytes > budget["batch_bytes"]:
            raise RunnerError("budget_exceeded", "Series batch exceeds byte budget")
        frame = batch.to_pandas()
        values[offset:offset + len(frame)] = frame["val"].to_numpy()
        for i, key in enumerate(frame["seq"]):
            if previous != key:
                if segments:
                    segments[-1]["end"] = offset + i
                segments.append({"sequence": key, "start": offset + i})
                previous = key
                if len(segments) > p["max_sequences"]:
                    raise RunnerError("cardinality_exceeded", "Too many sequences")
        offset += len(frame)
    segments[-1]["end"] = count
    usable = []
    for seg in segments:
        n = seg["end"] - seg["start"]
        if n < p["window"] + 4:
            raise RunnerError("insufficient_data", "Each series needs at least window + 4 finite values")
        if n >= 2 * (p["window"] + 2):
            n_val = max(p["window"] + 1, int(n * p["validation_split"]))
        else:
            n_val = 0
        seg["split"] = seg["end"] - n_val
        # Bounded reductions, rather than a normalized whole-dataset copy.
        total, squares, count_train = 0., 0., 0
        for at in range(seg["start"], seg["split"], source.batch_rows):
            chunk = np.asarray(values[at:min(at + source.batch_rows, seg["split"])])
            total += float(chunk.sum())
            squares += float(np.dot(chunk, chunk))
            count_train += len(chunk)
        seg["mean"] = total / count_train
        seg["std"] = math.sqrt(max(0, squares / count_train - seg["mean"] ** 2)) or 1.
        if not math.isfinite(seg["mean"]) or not math.isfinite(seg["std"]):
            raise RunnerError("nonfinite_training", "Series training statistics overflowed")
        usable.append(seg)
    values.flush()
    values._mmap.close()
    source._memmap = np.load(source.root / "values.npy", mmap_mode="r", allow_pickle=False)
    return out, source._memmap, usable


def train_rnn(data, params, *, device="cpu", budget=None, progress=None, cancel=None, output_root=None, scratch=None):
    p, b = validate_params("Torch_RNN", params), validate_budget(budget)
    if p["batch_size"] * p["window"] * 4 > b["batch_bytes"]:
        raise RunnerError("budget_exceeded", "RNN batch tensor exceeds byte budget")
    torch, nn, device = _torch_setup(p, device, b)
    progress = progress or (lambda *_: None)
    with BatchSource(data, b, p["batch_size"], scratch) as source:
        ordered, values, segments = _sorted_series(source, p, b)
        try:
            train = LazyWindows(values, segments, p["window"], "train")
            validation = LazyWindows(values, segments, p["window"], "validation")
            logical_reads = (len(train) + len(validation)) * (p["epochs"] + 1) * (p["window"] + 1) * 8
            logical_reads += len(segments) * p["mc_samples"] * p["steps"] * p["window"] * 8
            # Reserve a conservative logical-read bound for repeated CPU memmap
            # windows; this is not a measurement of physical disk or cloud billing.
            if source.scanned + logical_reads > b["max_scan_bytes"]:
                raise RunnerError("budget_exceeded", "Planned series/window reads exceed scan budget")
            # num_workers=0: no imported worker code and no hidden prefetched GPU tensors.
            generator = torch.Generator().manual_seed(p["seed"])
            loader = torch.utils.data.DataLoader(train, batch_size=p["batch_size"], shuffle=False, generator=generator, num_workers=0)
            val_loader = torch.utils.data.DataLoader(validation, batch_size=p["batch_size"], num_workers=0)
            model = build_rnn(nn, p["cell"], p["hidden_size"], p["dropout"]).to(device)
            optimizer = torch.optim.Adam(model.parameters(), lr=p["learning_rate"])
            loss_fn, losses = nn.MSELoss(), []
            for epoch in range(1, p["epochs"] + 1):
                model.train()
                total, seen = 0., 0
                for x, y in loader:
                    _check(cancel)
                    optimizer.zero_grad(set_to_none=True)
                    loss = loss_fn(model(x.to(device)), y.to(device))
                    if not torch.isfinite(loss):
                        raise RunnerError("nonfinite_training", "Training produced a nonfinite loss")
                    loss.backward()
                    optimizer.step()
                    total += float(loss.detach().cpu()) * len(x)
                    seen += len(x)
                model.eval()
                val_total, val_seen = 0., 0
                with torch.no_grad():
                    for x, y in val_loader:
                        _check(cancel)
                        val_total += float(loss_fn(model(x.to(device)), y.to(device)).cpu()) * len(x)
                        val_seen += len(x)
                losses.append({"epoch": epoch, "train_loss": total / seen, "val_loss": val_total / val_seen if val_seen else None})
                progress(10 + int(70 * epoch / p["epochs"]), f"{p['cell'].upper()} epoch {epoch}/{p['epochs']}")
            import pyarrow.parquet as pq
            writer, rows = PredictionWriter(output_root, b["max_output_bytes"]), []
            absolute, squared, evaluation_n, offset = 0., 0., 0, 0
            try:
                # Stream sorted timestamps and infer only windows within a split.
                seg_index = 0
                with torch.no_grad():
                    for batch in pq.ParquetFile(ordered).iter_batches(batch_size=p["batch_size"], use_threads=False):
                        _check(cancel)
                        frame, pending = batch.to_pandas(), []
                        for local, record in enumerate(frame.itertuples(index=False)):
                            at = offset + local
                            while at >= segments[seg_index]["end"]:
                                seg_index += 1
                            seg = segments[seg_index]
                            boundary = seg["start"] if at < seg["split"] else seg["split"]
                            if at - boundary < p["window"]:
                                continue
                            pending.append((at, record, seg))
                        if pending:
                            x = np.stack([((values[at-p['window']:at]-seg['mean'])/seg['std']).astype(np.float32)[:, None] for at, _, seg in pending])
                            prediction = model(torch.from_numpy(x).to(device)).cpu().numpy()
                            result = []
                            for predicted, (at, record, seg) in zip(prediction, pending):
                                predicted = float(predicted * seg["std"] + seg["mean"])
                                actual = float(values[at])
                                split = "train" if at < seg["split"] else "validation"
                                if split == "validation":
                                    absolute += abs(actual - predicted)
                                    squared += (actual - predicted) ** 2
                                    evaluation_n += 1
                                result.append({"ds": str(record.ds), "y_actual": actual, "y_pred": predicted, "lower_ci": float("nan"), "upper_ci": float("nan"), "segment": "historical", "sequence": seg["sequence"], "split": split})
                            result = pd.DataFrame(result)
                            writer.write(result)
                            remaining = 200 - sum(len(x) for x in rows)
                            if remaining > 0:
                                rows.append(result.head(remaining))
                        offset += len(frame)
                # Forecast each sequence; MC outputs are small and never training inputs.
                forecasts = []
                for seg in segments:
                    _check(cancel)
                    last_window = ((values[seg["end"]-p["window"]:seg["end"]] - seg["mean"]) / seg["std"]).astype(np.float32)
                    all_forecasts = []
                    model.train()
                    with torch.no_grad():
                        for _ in range(p["mc_samples"]):
                            window, prediction = last_window.copy(), []
                            for _ in range(p["steps"]):
                                _check(cancel)
                                predicted = float(model(torch.from_numpy(window[None, :, None]).to(device)).cpu()[0])
                                prediction.append(predicted)
                                window = np.concatenate((window[1:], [predicted])).astype(np.float32)
                            all_forecasts.append(prediction)
                    model.eval()
                    array = np.array(all_forecasts) * seg["std"] + seg["mean"]
                    # Timestamp lookup is bounded to the last two rows of a sequence.
                    last_rows = []
                    cursor = 0
                    for batch in pq.ParquetFile(ordered).iter_batches(batch_size=p["batch_size"], columns=["ds"], use_threads=False):
                        if cursor + len(batch) > seg["end"] - 2 and cursor < seg["end"]:
                            stamps = batch.column(0).to_pylist()
                            last_rows.extend(stamps[max(0, seg["end"] - 2 - cursor):min(len(stamps), seg["end"] - cursor)])
                        cursor += len(batch)
                        if cursor >= seg["end"]:
                            break
                    for step in range(p["steps"]):
                        if p["time_column"]:
                            delta = last_rows[-1] - last_rows[-2]
                            stamp = str(last_rows[-1] + (step + 1) * delta)
                        else:
                            stamp = str(seg["end"] - seg["start"] + step)
                        forecasts.append({"ds": stamp, "y_actual": float("nan"), "y_pred": float(array[:, step].mean()), "lower_ci": float(np.percentile(array[:, step], 2.5)), "upper_ci": float(np.percentile(array[:, step], 97.5)), "segment": "forecast", "sequence": seg["sequence"], "split": "forecast"})
                    if len(forecasts) > 10000:
                        raise RunnerError("budget_exceeded", "Forecast table exceeds bounded result rows")
                forecast = pd.DataFrame(forecasts)
                writer.write(forecast)
            finally:
                writer.close()
            result = pd.concat([*rows, forecast], ignore_index=True)
            metrics = pd.DataFrame([
                {"metric": "模型类型", "value": f"{p['cell'].upper()}（隐层 {p['hidden_size']} 维，PyTorch {torch.__version__}）"},
                {"metric": "训练设备", "value": str(device)}, {"metric": "窗口大小（lookback）", "value": p["window"]},
                {"metric": "训练轮数（epochs）", "value": p["epochs"]}, {"metric": "训练样本数", "value": source.rows},
                {"metric": "最终训练 MSE Loss", "value": losses[-1]["train_loss"]},
                {"metric": "最终验证 MSE Loss", "value": losses[-1]["val_loss"] if len(validation) else "N/A"},
                {"metric": "MAE（验证集）", "value": absolute / evaluation_n if evaluation_n else "N/A"},
                {"metric": "RMSE（验证集）", "value": math.sqrt(squared / evaluation_n) if evaluation_n else "N/A"},
            ])
            metadata = {"kernel_version": KERNEL_VERSION, "family": "Torch_RNN", "params": p, "device": str(device),
                        "segments": segments, "split": "per_sequence_chronological_no_cross_boundary", "missing_policy": "drop_nonfinite_target",
                        "train_windows": len(train), "validation_windows": len(validation), "seed": p["seed"], "torch_version": torch.__version__,
                        "cache": {"format": "npy", "dtype": "float64", "shape": [len(values)], "allow_pickle": False}}
            progress(100, "RNN 训练完成")
            return KernelResult((result, pd.DataFrame(losses), metrics), f"## {p['cell'].upper()} 时间序列预测（PyTorch）\n\n- 设备：`{device}`\n- 窗口：{p['window']}；预测步数：{p['steps']}；训练窗口：{len(train)}；验证窗口：{len(validation)}", model, metadata)
        finally:
            # Windows cannot remove an open memmap; release it before temp cleanup.
            if hasattr(values, "_mmap") and values._mmap is not None:
                values._mmap.close()
