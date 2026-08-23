"""Minimal, safe Feishu Bitable creation for the application bot identity."""
from __future__ import annotations

from collections.abc import Iterable
import json
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from data.feishu_bot_service import FeishuBotError, _configured_credentials, _tenant_access_token


_OPEN_API = "https://open.feishu.cn/open-apis"
_TIMEOUT = (3.05, 15)
_MAX_FIELDS = 50
_MAX_RECORDS = 500
_WRITE_BATCH_SIZE = 100
_MAX_READ_RECORDS = 500
_PAGE_SIZE = 100


class FeishuBitableError(RuntimeError):
    """A concise, user-facing Feishu Bitable operation error."""


def _clean_name(value: object, label: str, maximum: int = 100) -> str:
    if not isinstance(value, str):
        raise FeishuBitableError(f"{label}必须是文本")
    result = value.strip()
    if not result:
        raise FeishuBitableError(f"请填写{label}")
    if len(result) > maximum:
        raise FeishuBitableError(f"{label}不能超过 {maximum} 个字符")
    return result


def _credentials_and_token() -> str:
    try:
        app_id, app_secret = _configured_credentials()
        return _tenant_access_token(app_id, app_secret)
    except FeishuBotError as exc:
        raise FeishuBitableError(str(exc)) from exc


def _request(method: str, path: str, access_token: str, *, operation: str = "多维表格操作", **kwargs) -> dict:
    try:
        response = requests.request(
            method,
            f"{_OPEN_API}{path}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"},
            timeout=_TIMEOUT,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise FeishuBitableError("无法连接飞书开放平台，请检查网络") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise FeishuBitableError(f"{operation}返回 HTTP {response.status_code}") from exc
    if not response.ok or not isinstance(body, dict) or body.get("code") not in (None, 0):
        code = body.get("code") if isinstance(body, dict) else None
        msg = str(body.get("msg") or "") if isinstance(body, dict) else ""
        hint = (
            "请在飞书开放平台申请“查看、评论、编辑和管理多维表格”权限，并发布应用；"
            "若仍失败，也请确认应用可访问目标文件夹。"
        )
        suffix = f"（错误码 {code}）" if isinstance(code, int) else ""
        raise FeishuBitableError(f"飞书拒绝{operation}{suffix}：{msg or hint}")
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def _root_folder_token(access_token: str) -> str:
    # Root-folder metadata belongs to Drive Explorer v2.  The historical
    # drive/v1/files/root_folder/meta route returns 404 for current tenants.
    data = _request(
        "GET", "/drive/explorer/v2/root_folder/meta", access_token,
        operation="读取飞书默认保存文件夹",
    )
    token = str(data.get("token") or "").strip()
    if not token:
        raise FeishuBitableError(
            "无法读取飞书根文件夹。请申请云空间读取权限，或在创建请求中提供可写文件夹 Token。"
        )
    return token


def _normalize_fields(fields: Iterable[object]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in fields:
        name = _clean_name(raw, "字段名", 100)
        if name in seen:
            raise FeishuBitableError(f"字段名重复：{name}")
        seen.add(name)
        normalized.append({"field_name": name, "type": 1})
    if not normalized:
        raise FeishuBitableError("至少需要一个字段")
    if len(normalized) > _MAX_FIELDS:
        raise FeishuBitableError(f"一次最多创建 {_MAX_FIELDS} 个字段")
    return normalized


def _normalize_records(records: Iterable[object], field_names: set[str]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for raw in records:
        if not isinstance(raw, dict):
            raise FeishuBitableError("每条记录必须是字段和值组成的对象")
        fields: dict[str, object] = {}
        for key, value in raw.items():
            name = str(key or "").strip()
            if name not in field_names:
                raise FeishuBitableError(f"记录包含未定义字段：{name or '（空）'}")
            if value is None:
                continue
            fields[name] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if fields:
            normalized.append({"fields": fields})
    if len(normalized) > _MAX_RECORDS:
        raise FeishuBitableError(f"一次最多写入 {_MAX_RECORDS} 条记录")
    return normalized


def _token(value: object, label: str, maximum: int = 300) -> str:
    result = str(value or "").strip()
    if not result:
        raise FeishuBitableError(f"请提供{label}")
    if len(result) > maximum or any(char.isspace() for char in result):
        raise FeishuBitableError(f"{label}格式不正确")
    return result


def parse_bitable_reference(bitable: object, table_id: object = "") -> tuple[str, str]:
    """Accept a Bitable URL or app token and return app/table tokens.

    A table token may be supplied separately when the Bitable URL points at the
    app homepage.  This parser deliberately accepts Feishu and Lark Suite
    domains only; it never fetches the supplied URL.
    """
    raw = _token(bitable, "多维表格链接或 app_token")
    supplied_table = str(table_id or "").strip()
    app_token = raw
    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
        if not (host == "feishu.cn" or host.endswith(".feishu.cn") or host == "larksuite.com" or host.endswith(".larksuite.com")):
            raise FeishuBitableError("只支持飞书多维表格链接")
        parts = [part for part in parsed.path.split("/") if part]
        try:
            app_token = parts[parts.index("base") + 1]
        except (ValueError, IndexError) as exc:
            raise FeishuBitableError("无法从链接中识别多维表格 app_token") from exc
        supplied_table = supplied_table or (parse_qs(parsed.query).get("table") or [""])[0]
    return _token(app_token, "app_token"), (str(supplied_table or "").strip())


def _bitable_url(app_token: str, table_id: str = "", app_url: str = "") -> str:
    parsed = urlparse(str(app_url or f"https://feishu.cn/base/{app_token}"))
    query = parse_qs(parsed.query)
    if table_id:
        query["table"] = [table_id]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _cell_value(value: object) -> object:
    """Make Bitable's rich cell payload safe and useful in a DataFrame."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return ", ".join(str(_cell_value(item)) for item in value if item is not None)
    if isinstance(value, dict):
        for key in ("text", "name", "url", "link"):
            scalar = value.get(key)
            if isinstance(scalar, (str, int, float, bool)):
                return scalar
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _write_records(records: Iterable[object]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for raw in records:
        if not isinstance(raw, dict) or not raw:
            raise FeishuBitableError("每条记录必须是非空字段对象")
        fields = {str(key).strip(): value for key, value in raw.items() if str(key).strip()}
        if not fields:
            raise FeishuBitableError("每条记录至少需要一个有效字段")
        normalized.append({"fields": fields})
    if not normalized:
        raise FeishuBitableError("至少需要一条记录")
    if len(normalized) > _MAX_RECORDS:
        raise FeishuBitableError(f"一次最多写入 {_MAX_RECORDS} 条记录")
    return normalized


def list_bitable_tables(*, bitable: object) -> dict[str, object]:
    """List tables visible to the configured application bot in one Bitable."""
    app_token, _ = parse_bitable_reference(bitable)
    access_token = _credentials_and_token()
    tables: list[dict[str, str]] = []
    page_token = ""
    while len(tables) < _MAX_READ_RECORDS:
        params: dict[str, object] = {"page_size": _PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        data = _request(
            "GET", f"/bitable/v1/apps/{app_token}/tables", access_token,
            operation="读取飞书多维表格数据表", params=params,
        )
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            table_id = str(item.get("table_id") or "").strip()
            if not table_id:
                continue
            tables.append({
                "table_id": table_id,
                "name": str(item.get("name") or "未命名数据表"),
                "url": _bitable_url(app_token, table_id),
            })
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token") or "").strip()
        if not page_token:
            break
    return {"app_token": app_token, "tables": tables}


def read_bitable_records(
    *, bitable: object, table_id: object = "", max_records: int = _MAX_READ_RECORDS,
) -> dict[str, object]:
    """Read a bounded set of records, retaining record IDs for explicit edits."""
    app_token, resolved_table_id = parse_bitable_reference(bitable, table_id)
    if not resolved_table_id:
        raise FeishuBitableError("请提供 table_id，或使用包含 ?table=... 的多维表格链接")
    try:
        limit = int(max_records)
    except (TypeError, ValueError) as exc:
        raise FeishuBitableError("max_records 必须是数字") from exc
    limit = max(1, min(limit, _MAX_READ_RECORDS))
    access_token = _credentials_and_token()
    rows: list[dict[str, object]] = []
    page_token = ""
    while len(rows) < limit:
        params: dict[str, object] = {"page_size": min(_PAGE_SIZE, limit - len(rows))}
        if page_token:
            params["page_token"] = page_token
        data = _request(
            "GET", f"/bitable/v1/apps/{app_token}/tables/{resolved_table_id}/records", access_token,
            operation="读取飞书多维表格记录", params=params,
        )
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            row = {str(key): _cell_value(value) for key, value in fields.items()}
            record_id = str(item.get("record_id") or "").strip()
            if record_id:
                row["_feishu_record_id"] = record_id
            rows.append(row)
            if len(rows) >= limit:
                break
        if len(rows) >= limit or not data.get("has_more"):
            break
        page_token = str(data.get("page_token") or "").strip()
        if not page_token:
            break
    return {
        "app_token": app_token,
        "table_id": resolved_table_id,
        "url": _bitable_url(app_token, resolved_table_id),
        "records": rows,
        "record_count": len(rows),
        "limited": bool(data.get("has_more")) if "data" in locals() else False,
    }


def append_bitable_records(*, bitable: object, table_id: object, records: Iterable[object]) -> dict[str, object]:
    """Append explicit records to an existing Bitable table."""
    app_token, resolved_table_id = parse_bitable_reference(bitable, table_id)
    if not resolved_table_id:
        raise FeishuBitableError("请提供 table_id")
    normalized = _write_records(records)
    access_token = _credentials_and_token()
    data = _request(
        "POST", f"/bitable/v1/apps/{app_token}/tables/{resolved_table_id}/records/batch_create", access_token,
        operation="写入飞书多维表格记录", json={"records": normalized},
    )
    created = data.get("records") if isinstance(data.get("records"), list) else []
    return {
        "app_token": app_token, "table_id": resolved_table_id,
        "url": _bitable_url(app_token, resolved_table_id),
        "record_count": len(normalized),
        "record_ids": [str(item.get("record_id")) for item in created if isinstance(item, dict) and item.get("record_id")],
    }


def update_bitable_record(
    *, bitable: object, table_id: object, record_id: object, fields: object,
) -> dict[str, object]:
    """Update one existing record. The caller must supply an ID returned by a read."""
    app_token, resolved_table_id = parse_bitable_reference(bitable, table_id)
    if not resolved_table_id:
        raise FeishuBitableError("请提供 table_id")
    resolved_record_id = _token(record_id, "record_id")
    if not isinstance(fields, dict) or not fields:
        raise FeishuBitableError("fields 必须是非空字段对象")
    clean_fields = {str(key).strip(): value for key, value in fields.items() if str(key).strip()}
    if not clean_fields:
        raise FeishuBitableError("fields 至少需要一个有效字段")
    access_token = _credentials_and_token()
    _request(
        "PUT", f"/bitable/v1/apps/{app_token}/tables/{resolved_table_id}/records/{resolved_record_id}", access_token,
        operation="更新飞书多维表格记录", json={"fields": clean_fields},
    )
    return {
        "app_token": app_token, "table_id": resolved_table_id, "record_id": resolved_record_id,
        "url": _bitable_url(app_token, resolved_table_id), "updated_fields": sorted(clean_fields),
    }


def create_bitable(
    *, name: str, table_name: str, fields: Iterable[object], records: Iterable[object] = (),
    folder_token: str = "",
) -> dict[str, object]:
    """Create a Base app, one named text table, and optional first records."""
    base_name = _clean_name(name, "多维表格名称")
    data_table_name = _clean_name(table_name, "数据表名称")
    normalized_fields = _normalize_fields(fields)
    normalized_records = _normalize_records(records, {str(field["field_name"]) for field in normalized_fields})
    access_token = _credentials_and_token()
    target_folder = str(folder_token or "").strip() or _root_folder_token(access_token)
    app_data = _request(
        "POST", "/bitable/v1/apps", access_token,
        operation="创建飞书多维表格",
        json={"name": base_name, "folder_token": target_folder},
    )
    app = app_data.get("app") if isinstance(app_data.get("app"), dict) else {}
    app_token = str(app.get("app_token") or "").strip()
    if not app_token:
        raise FeishuBitableError("飞书已返回成功但未提供多维表格标识，请稍后在云空间确认")
    table_data = _request(
        "POST", f"/bitable/v1/apps/{app_token}/tables", access_token,
        operation="创建飞书数据表",
        json={"table": {"name": data_table_name, "fields": normalized_fields}},
    )
    # The current REST API returns ``data.table_id``.  Some SDK generations
    # serialize the same response as ``data.table.table_id``; support both so
    # a successful table creation is never misreported as incomplete.
    table = table_data.get("table") if isinstance(table_data.get("table"), dict) else {}
    table_id = str(table_data.get("table_id") or table.get("table_id") or "").strip()
    if not table_id:
        raise FeishuBitableError("多维表格已创建，但未返回数据表标识；请在飞书中检查表结构")
    if normalized_records:
        for start in range(0, len(normalized_records), _WRITE_BATCH_SIZE):
            _request(
                "POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create", access_token,
                operation="写入飞书多维表格记录",
                json={"records": normalized_records[start:start + _WRITE_BATCH_SIZE]},
            )
    return {
        "app_token": app_token, "table_id": table_id, "name": base_name,
        "table_name": data_table_name, "record_count": len(normalized_records),
        "url": _bitable_url(app_token, table_id, str(app.get("url") or "")),
    }
