"""Bounded summaries for immutable datasets; no full DataFrame materialization."""
from __future__ import annotations

import json


def _quote(value):
    return '"' + str(value).replace('"', '""') + '"'


def cleaning_query(source, table_name, operation, *, columns=None, fill_method="mean",
                   lower_pct=1.0, upper_pct=99.0, trim_column="", min_val=None, max_val=None):
    """Compile only registered transforms; aggregate fitting stays inside SQL."""
    from decimal import Decimal, InvalidOperation
    tables = source.list_tables()
    if table_name not in tables:
        raise ValueError("Select an existing dataset table")
    manifest_tables = source.handle.manifest.get("tables") or []
    table = next((item for item in manifest_tables if item.get("name") == table_name), {})
    schema = table.get("schema") or source.handle.manifest.get("schema") or []
    fields = schema.get("fields", []) if isinstance(schema, dict) else schema
    if not fields:
        raise ValueError("Dataset schema is unavailable")
    names = [item["name"] for item in fields]
    numeric = {item["name"] for item in fields if str(item.get("type", "")).lower().startswith(("int", "uint", "float", "double", "decimal"))}
    selected = list(columns or [name for name in names if name in numeric])
    if any(column not in numeric for column in selected) or len(selected) > 50 or len(selected) != len(set(selected)):
        raise ValueError("Select at most 50 distinct numeric columns")
    decimal_columns = {item["name"] for item in fields if str(item.get("type", "")).lower().startswith("decimal")}
    if decimal_columns.intersection(selected) and (operation == "winsorize" or operation == "fill_na" and fill_method != "zero"):
        raise ValueError("Exact DECIMAL aggregates need an explicit conversion/rounding plan; no float coercion is performed")
    base = _quote(table_name)
    if operation == "trimming":
        if trim_column not in numeric or min_val is None or max_val is None:
            raise ValueError("Trimming requires one numeric column and both bounds")
        try:
            low, high = Decimal(str(min_val)), Decimal(str(max_val))
        except InvalidOperation as exc:
            raise ValueError("Invalid trimming bounds") from exc
        if not low.is_finite() or not high.is_finite() or low > high:
            raise ValueError("Invalid trimming bounds")
        return f"SELECT * FROM {base} WHERE {_quote(trim_column)} >= {low} AND {_quote(trim_column)} <= {high}"
    if not selected:
        raise ValueError("No numeric columns were selected")
    if operation == "fill_na" and fill_method not in {"mean", "median", "zero"}:
        raise ValueError("Dataset fill supports mean, median or zero")
    if operation == "winsorize":
        low, high = Decimal(str(lower_pct)), Decimal(str(upper_pct))
        if not low.is_finite() or not high.is_finite() or not 0 <= low <= high <= 100:
            raise ValueError("Winsorization percentiles must be ordered within 0..100")
    elif operation != "fill_na":
        raise ValueError("Unsupported dataset cleaning operation")
    expressions = []
    for column in names:
        quoted = _quote(column)
        if column not in selected:
            expressions.append(quoted)
            continue
        if operation == "fill_na":
            aggregate = "0" if fill_method == "zero" else f"(SELECT {'AVG' if fill_method == 'mean' else 'MEDIAN'}({quoted}) FROM {base})"
            expression = f"COALESCE({quoted}, {aggregate})"
        else:
            minimum = f"(SELECT QUANTILE_CONT({quoted}, {low / 100}) FROM {base})"
            maximum = f"(SELECT QUANTILE_CONT({quoted}, {high / 100}) FROM {base})"
            expression = f"CASE WHEN {quoted} IS NULL THEN NULL WHEN {quoted} < {minimum} THEN {minimum} WHEN {quoted} > {maximum} THEN {maximum} ELSE {quoted} END"
        expressions.append(f"{expression} AS {quoted}")
    return "SELECT " + ", ".join(expressions) + " FROM " + base


def profile_dataset(source, table_name, columns=None, *, cancel=None):
    tables = source.list_tables()
    if table_name not in tables:
        raise ValueError("Select a table belonging to this dataset")
    metadata = source.get_table_schema(table_name)
    available = [str(item.get("name")) if isinstance(item, dict) else str(item)
                 for item in metadata.get("columns", [])]
    selected = list(columns or available)
    if not selected or len(selected) > 50 or any(not isinstance(column, str) or column not in available for column in selected):
        raise ValueError("Select at most 50 existing dataset columns")
    if len(selected) != len(set(selected)):
        raise ValueError("Profile columns must be unique")
    if cancel is not None and cancel():
        raise RuntimeError("query_canceled")
    aliases = ["COUNT(*) AS row_count"] + [f"COUNT({_quote(column)}) AS {_quote('present_' + str(index))}" for index, column in enumerate(selected)]
    sql = "SELECT " + ", ".join(aliases) + " FROM " + _quote(table_name)
    iterator = source.execute_query_batches(sql, cancel=cancel)
    records = []
    try:
        for batch in iterator:
            if len(records) + batch.num_rows > 1:
                raise ValueError("The dataset did not return a complete aggregate profile")
            records.extend(batch.to_pylist())
    finally:
        iterator.close()
    if len(records) != 1:
        raise ValueError("The dataset did not return a complete aggregate profile")
    count = int(records[0]["row_count"])
    fields = []
    for index, column in enumerate(selected):
        present = int(records[0]["present_" + str(index)])
        fields.append({"column": column, "row_count": count, "missing_count": count - present,
                       "present_count": present, "missing_rate": (count - present) / count if count else None})
    if cancel is not None and cancel():
        raise RuntimeError("query_canceled")
    handle = source.handle
    summary = {"complete": True, "sampling": False, "row_count": count, "columns": fields,
               "dataset_id": handle.dataset_id, "snapshot_id": handle.snapshot_id}
    return {"text": "### 数据集概况 / Dataset profile\n\n" + json.dumps(summary, ensure_ascii=False),
            "charts": [], "data": summary,
            "sources": [{"type": "dataset", "title": table_name,
                         "source": str(getattr(source, "name", "Dataset")),
                         "snapshot_id": handle.snapshot_id,
                         "snippet": "Full-snapshot aggregate; no sampling"}]}
