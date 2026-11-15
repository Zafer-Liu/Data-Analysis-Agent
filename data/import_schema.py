"""Persisted, loss-conscious ImportSchema v1 policies (no pandas inference)."""
from __future__ import annotations

import datetime as dt
import decimal
import re
from zoneinfo import ZoneInfo


class ImportFailure(ValueError):
    def __init__(self, code: str, message: str, **details):
        self.code, self.details = code, details
        super().__init__(f"{code}: {message}")


def arrow_type(value):
    import pyarrow as pa
    if isinstance(value, pa.DataType):
        return value
    text = str(value).strip()
    match = re.fullmatch(r"decimal(?:32|64|128|256)?\((\d+),\s*(-?\d+)\)", text)
    if match:
        precision, scale = map(int, match.groups())
        return pa.decimal128(precision, scale) if precision <= 38 else pa.decimal256(precision, scale)
    match = re.fullmatch(r"timestamp\[(s|ms|us|ns)(?:,\s*tz=(.+))?\]", text)
    if match:
        return pa.timestamp(match[1], tz=match[2])
    match = re.fullmatch(r"(time32|time64|duration)\[(s|ms|us|ns)\]", text)
    if match:
        return getattr(pa, match[1])(match[2])
    match = re.fullmatch(r'fixed_size_binary\[(\d+)\]', text)
    if match:
        return pa.binary(int(match[1]))
    aliases = {"str": "string", "text": "string", "integer": "int64", "double": "float64", "boolean": "bool", "date": "date32"}
    try:
        return pa.type_for_alias(aliases.get(text.lower(), text))
    except (ValueError, KeyError) as exc:
        raise ImportFailure("unsupported_type", f"Unsupported scalar Arrow type: {text}") from exc


def type_name(value) -> str:
    import pyarrow as pa
    value = arrow_type(value)
    if pa.types.is_date32(value):
        return "date32"
    if pa.types.is_date64(value):
        return "date64"
    return str(value)


def normalized_names(names):
    used, result = set(), []
    for raw in names:
        name = re.sub(r"[^\w]+", "_", str(raw).strip()).strip("_") or "col"
        if name[0].isdigit() or re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])', name, re.I):
            name = "_" + name
        if len(name) > 140:
            import hashlib
            name = name[:120] + '_' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:16]
        base, suffix = name, 2
        while name.casefold() in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name.casefold())
        result.append(name)
    return result


def schema_fields(schema) -> list[dict]:
    """Use the shared manifest's plain Arrow field definitions."""
    return [{"name": field.name, "type": type_name(field.type), "nullable": field.nullable} for field in schema]


def to_arrow_schema(policy):
    import pyarrow as pa
    return pa.schema([pa.field(c["name"], arrow_type(c["type"]), nullable=c.get("nullable", True)) for c in policy["columns"]])


def _infer(values, name, nulls):
    """Only unambiguous scalar numerics; IDs, dates and NA categories stay text."""
    if re.search(r"(?:^|_)(?:id|code|zip|postal|phone|account)(?:$|_)", name, re.I):
        return "string"
    values = [str(v) for v in values if v is not None and str(v) not in nulls]
    if not values:
        return "string"
    if any(re.fullmatch(r"[+-]?0\d+", v) for v in values):
        return "string"
    if all(re.fullmatch(r"[+-]?\d+", v) for v in values):
        if all(-(2**63) <= int(v) < 2**63 for v in values):
            return "int64"
        return "string"
    if all(re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)", v) for v in values):
        parsed = [decimal.Decimal(v) for v in values]
        scale = max(max(0, -v.as_tuple().exponent) for v in parsed)
        whole = max(max(0, v.adjusted() + 1) for v in parsed)
        precision = max(1, whole + scale)
        if precision <= 38:
            return f"decimal128({precision}, {scale})"
    return "string"


def freeze_schema(names, *, requested=None, samples=None, native_schema=None, infer=True):
    """Freeze names, null/date/identifier/decimal/timezone and corrupt-row policy."""
    requested = dict(requested or {})
    original = [str(n) for n in names]
    if not original or len(set(original)) != len(original):
        raise ImportFailure("invalid_schema", "Source column names must be present and unique")
    normalized = normalized_names(original)
    supplied = requested.get("columns", [])
    if isinstance(supplied, dict):
        supplied = [{"original_name": k, **(v if isinstance(v, dict) else {"type": v})} for k, v in supplied.items()]
    if not isinstance(supplied, list) or any(not isinstance(c, dict) for c in supplied):
        raise ImportFailure("invalid_schema", "columns must be a list or mapping of field policies")
    explicit = {str(c.get("original_name", c.get("name", ""))): c for c in supplied}
    if len(explicit) != len(supplied):
        raise ImportFailure("invalid_schema", "Duplicate explicit column policies")
    nulls = requested.get("null_values", [""])
    if not isinstance(nulls, list) or any(not isinstance(v, str) for v in nulls) or len(nulls) > 128:
        raise ImportFailure("invalid_schema", "null_values must be a bounded list of text tokens")
    bad = requested.get("bad_rows", "error")
    if bad not in ("error", "skip"):
        raise ImportFailure("invalid_schema", "bad_rows must be error or skip")
    max_bad = int(requested.get("max_bad_rows", 0))
    if max_bad < 0 or max_bad > 1_000_000 or (bad == "skip" and max_bad == 0):
        raise ImportFailure("invalid_schema", "Skipping requires an explicit positive max_bad_rows <= 1000000")
    columns = []
    for index, (raw, name) in enumerate(zip(original, normalized)):
        # Raw names take precedence globally: a normalized spelling may also
        # be a different source column's original name.
        spec = dict(explicit.get(raw, explicit.get(name, {}) if name not in original else {}))
        col_nulls = spec.get("null_values", nulls)
        if not isinstance(col_nulls, list) or len(col_nulls) > 128 or any(not isinstance(v, str) for v in col_nulls):
            raise ImportFailure("invalid_schema", "Column null_values must contain bounded text tokens")
        if 'nullable' in spec and not isinstance(spec['nullable'], bool):
            raise ImportFailure("invalid_schema", "nullable must be boolean")
        native_type = native_schema.field(index).type if native_schema is not None else None
        dtype = spec.get("type") or (type_name(native_type) if native_type is not None else _infer((samples or {}).get(raw, []), name, col_nulls) if infer else "string")
        if raw in requested.get("id_columns", []) or name in requested.get("id_columns", []):
            dtype = "string"
        dtype = type_name(dtype)
        field = {"original_name": raw, "name": name, "type": dtype, "nullable": spec.get('nullable', native_schema.field(index).nullable if native_schema is not None else True),
                 "null_values": col_nulls, "date_format": spec.get("date_format", "iso8601"),
                 "source_timezone": spec.get("source_timezone", requested.get("source_timezone")),
                 "time_unit": getattr(arrow_type(dtype), 'unit', None), "decimal_policy": "exact", "conversion": "strict"}
        if field["source_timezone"]:
            ZoneInfo(field["source_timezone"])
        columns.append(field)
    unknown = set(explicit) - set(original) - set(normalized)
    if unknown:
        raise ImportFailure("invalid_schema", f"Unknown column policy: {sorted(unknown)}")
    return {"version": 1, "columns": columns, "null_values": nulls, "bad_rows": bad, "max_bad_rows": max_bad,
            "id_policy": "preserve_leading_zero", "decimal_policy": "exact", "timezone_policy": "explicit_aware_or_naive", "type_drift": "error"}


def scalar_value(value, column):
    import pyarrow as pa
    dtype = arrow_type(column["type"])
    if value is None or (isinstance(value, str) and value in column["null_values"]):
        if not column.get("nullable", True):
            raise ValueError("null in non-nullable column")
        return None
    if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        return value.isoformat() if isinstance(value, (dt.date, dt.datetime, dt.time)) else str(value)
    if pa.types.is_decimal(dtype):
        number = decimal.Decimal(str(value))
        quantum = decimal.Decimal(1).scaleb(-dtype.scale)
        with decimal.localcontext() as ctx:
            ctx.prec = max(80, dtype.precision * 2)
            if not number.is_finite() or number.quantize(quantum) != number:
                raise ValueError("decimal scale would lose digits")
        return number
    if pa.types.is_integer(dtype):
        if isinstance(value, bool) or (isinstance(value, str) and re.fullmatch(r'[+-]?0\d+', value)):
            raise ValueError('integer conversion would erase an identifier or boolean type')
        if isinstance(value, (float, decimal.Decimal)) and value != int(value):
            raise ValueError("fractional integer")
        return int(value)
    if pa.types.is_floating(dtype):
        import math
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite number")
        return number
    if pa.types.is_boolean(dtype):
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "false"):
            return str(value).lower() == "true"
        raise ValueError("expected true/false")
    if pa.types.is_timestamp(dtype) or pa.types.is_date(dtype):
        if isinstance(value, dt.datetime):
            parsed = value
        elif isinstance(value, dt.date):
            parsed = dt.datetime.combine(value, dt.time())
        else:
            fmt = column.get("date_format", "iso8601")
            parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")) if fmt == "iso8601" else dt.datetime.strptime(str(value), fmt)
        if pa.types.is_date(dtype):
            if parsed.time() != dt.time() or parsed.tzinfo is not None:
                raise ValueError("date conversion would discard a time or offset")
            return parsed.date()
        if isinstance(value, str) and re.search(r'\.\d{6}[1-9]|\.\d{7,}', value):
            raise ValueError("submicrosecond text timestamps require native Parquet timestamps")
        divisor = {'s': 1000000, 'ms': 1000, 'us': 1, 'ns': 1}[dtype.unit]
        if parsed.microsecond % divisor:
            raise ValueError("timestamp unit would lose fractional seconds")
        source_tz = column.get("source_timezone")
        if parsed.tzinfo is None and source_tz:
            zone = ZoneInfo(source_tz)
            a, b = parsed.replace(tzinfo=zone, fold=0), parsed.replace(tzinfo=zone, fold=1)
            if a.utcoffset() != b.utcoffset():
                raise ValueError("ambiguous/nonexistent local time requires an explicit offset")
            parsed = a
        if dtype.tz:
            if parsed.tzinfo is None:
                raise ValueError("timezone required for UTC instant")
            return parsed.astimezone(ZoneInfo(dtype.tz))
        if parsed.tzinfo is not None:
            raise ValueError("offset supplied for timezone-naive column")
        return parsed
    return value


def convert_batch(batch, policy, *, reject_row):
    """Vectorized strict cast, with counted row isolation only after a cast fails."""
    import pyarrow as pa
    import pyarrow.compute as pc
    schema = to_arrow_schema(policy)
    arrays = []
    try:
        for index, column in enumerate(policy["columns"]):
            source, target = batch.column(index), schema.field(index).type
            if pa.types.is_string(source.type) or pa.types.is_large_string(source.type):
                if column["null_values"]:
                    source = pc.if_else(pc.is_in(source, value_set=pa.array(column["null_values"], type=source.type)), None, source)
            if not column.get("nullable", True) and source.null_count:
                raise ValueError("null in non-nullable column")
            if pa.types.is_integer(target) and pa.types.is_boolean(source.type):
                raise ValueError('boolean to integer conversion requires an explicit transformation')
            if pa.types.is_integer(target) and (pa.types.is_string(source.type) or pa.types.is_large_string(source.type)):
                if pc.any(pc.match_substring_regex(source, r'^[+-]?0[0-9]+$')).as_py():
                    raise ValueError('integer conversion would erase a leading-zero identifier')
            if source.type == target:
                arrays.append(source)
            elif pa.types.is_timestamp(target) or pa.types.is_date(target):
                # A timezone-naive string cast to timestamp(tz) can silently assume UTC.
                values = [scalar_value(v, column) for v in source.to_pylist()]
                arrays.append(pa.array(values, type=target))
            elif pa.types.is_decimal(target) and pa.types.is_floating(source.type):
                arrays.append(pa.array([scalar_value(v, column) for v in source.to_pylist()], type=target))
            else:
                arrays.append(pc.cast(source, target, safe=True))
        return pa.RecordBatch.from_arrays(arrays, schema=schema)
    except (ValueError, TypeError, decimal.InvalidOperation, pa.ArrowException):
        rows = []
        for row in zip(*(batch.column(i).to_pylist() for i in range(batch.num_columns))):
            try:
                values = [scalar_value(v, c) for v, c in zip(row, policy["columns"])]
                # Validate exact overflow/precision before retaining this row.
                for value, field in zip(values, schema):
                    pa.scalar(value, type=field.type)
                rows.append(values)
            except (ValueError, TypeError, OverflowError, decimal.InvalidOperation, pa.ArrowException) as exc:
                reject_row("schema_drift", str(exc))
        return pa.RecordBatch.from_arrays([pa.array([row[i] for row in rows], type=f.type) for i, f in enumerate(schema)], schema=schema)
