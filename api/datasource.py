"""Blueprint: data source management — upload Excel/CSV, connect SQL DB."""
import logging
import traceback
import uuid
import os
import re
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

from .state import session_manager, datasource_config_manager
from data.connector import ExcelDataSource, CSVDataSource, SQLDataSource, GoogleSheetsDataSource, HTTPAPIDataSource

log = logging.getLogger(__name__)

bp = Blueprint("datasource", __name__)

# 自动识别环境，Vercel 用 /tmp，本地用项目目录
if os.environ.get("VERCEL"):
    UPLOAD_DIR = Path("/tmp/uploads")
else:
    UPLOAD_DIR = Path(__file__).parent.parent / "uploads"

UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_EXTS = {".xlsx", ".xls", ".csv"}


def _allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTS


def _encode_db_password(conn_str: str) -> str:
    """对连接字符串中的密码部分做 URL 编码，处理 @ # 等特殊字符。"""
    # 匹配 scheme://user:password@host 格式，密码可能含多个 @
    # 贪婪匹配密码部分（.+），确保最后一个 @ 才是 host 分隔符
    m = re.match(r'^([a-zA-Z][a-zA-Z0-9+\-.]*://[^:@/]+):(.+)@([^@].*)', conn_str)
    if not m:
        return conn_str
    prefix, password, rest = m.group(1), m.group(2), m.group(3)
    # 若密码已含 % 编码则跳过，避免二次编码
    if re.search(r'%[0-9A-Fa-f]{2}', password):
        return conn_str
    encoded = quote(password, safe='-._~!*')
    return f"{prefix}:{encoded}@{rest}"


@bp.post("/api/session/<sid>/upload")
def upload_file(sid: str):
    if "file" not in request.files:
        return jsonify({"error": "未选择文件"}), 400
    f = request.files["file"]
    if not f.filename or not _allowed(f.filename):
        return jsonify({"error": "仅支持 .xlsx / .xls / .csv 文件"}), 400

    display_name = f.filename  # keep original (may contain CJK/unicode)
    ext = Path(f.filename).suffix.lower()
    safe_stem = secure_filename(f.filename)
    safe_name = (safe_stem if safe_stem else f"upload_{uuid.uuid4().hex[:8]}{ext}")
    save_path = UPLOAD_DIR / f"{sid[:8]}_{uuid.uuid4().hex[:6]}_{safe_name}"
    f.save(str(save_path))

    log.info("[upload] saved → %s  (display: %s)", save_path, display_name)

    try:
        log.info("[upload] building DataSource …")
        if ext == ".csv":
            source = CSVDataSource(str(save_path), display_name)
        else:
            source = ExcelDataSource(str(save_path), display_name)

        log.info("[upload] DataSource ready, fetching schema …")
        schema = source.get_schema()
        log.info("[upload] schema OK:\n%s", schema)

        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        return jsonify({"ok": True, "source_name": display_name,
                        "schema_preview": schema})
    except Exception as exc:
        log.error("[upload] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": f"文件解析失败: {exc}"}), 400


@bp.post("/api/session/<sid>/connect-db")
def connect_db(sid: str):
    d = request.json or {}
    conn_str     = (d.get("connection_string") or "").strip()
    display_name = (d.get("name") or "").strip()
    # Use saved config if field left blank
    if not conn_str:
        saved = datasource_config_manager.get("sql")
        conn_str = (saved or {}).get("connection_string", "")
    if not conn_str:
        return jsonify({"error": "连接字符串不能为空"}), 400
    conn_str = _encode_db_password(conn_str)
    try:
        source = SQLDataSource(conn_str, display_name)
        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        datasource_config_manager.save("sql", {
            "connection_string": conn_str, "name": display_name
        })
        return jsonify({"ok": True, "source_name": source.name,
                        "schema_preview": source.get_schema()})
    except Exception as exc:
        return jsonify({"error": f"数据库连接失败: {exc}"}), 400


@bp.get("/api/session/<sid>/preview")
def preview_data(sid: str):
    sess = session_manager.get(sid)
    if not sess or not sess.data_source:
        return jsonify({"error": "no data source"}), 404
    tables = sess.data_source.get_preview()
    return jsonify({"source_name": sess.data_source.name, "tables": tables})


@bp.delete("/api/session/<sid>/datasource")
def disconnect_source(sid: str):
    sess = session_manager.get_or_create(sid)
    sess.data_source = None
    return jsonify({"ok": True})


@bp.post("/api/session/<sid>/connect-gsheets")
def connect_gsheets(sid: str):
    import json as _json
    d = request.json or {}
    creds_raw = d.get("creds_json", "")
    spreadsheet = (d.get("spreadsheet") or "").strip()
    display_name = (d.get("name") or "").strip()

    # Use saved creds if field left blank
    if not creds_raw:
        saved = datasource_config_manager.get("gsheets")
        creds_raw = (saved or {}).get("creds_json", "")
    if not spreadsheet:
        saved = datasource_config_manager.get("gsheets")
        spreadsheet = (saved or {}).get("spreadsheet", "")

    if not creds_raw:
        return jsonify({"error": "服务账号 JSON 不能为空"}), 400
    if not spreadsheet:
        return jsonify({"error": "电子表格 URL 或 ID 不能为空"}), 400

    try:
        creds_dict = _json.loads(creds_raw) if isinstance(creds_raw, str) else creds_raw
    except Exception:
        return jsonify({"error": "服务账号 JSON 格式无效"}), 400

    try:
        source = GoogleSheetsDataSource(creds_dict, spreadsheet, display_name)
        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        datasource_config_manager.save("gsheets", {
            "creds_json": creds_raw if isinstance(creds_raw, str) else _json.dumps(creds_raw),
            "spreadsheet": spreadsheet, "name": display_name
        })
        return jsonify({"ok": True, "source_name": source.name,
                        "schema_preview": source.get_schema()})
    except Exception as exc:
        log.error("[connect-gsheets] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": f"Google Sheets 连接失败: {exc}"}), 400


@bp.post("/api/session/<sid>/connect-api")
def connect_api(sid: str):
    d = request.json or {}
    url = (d.get("url") or "").strip()
    auth_type = (d.get("auth_type") or "none").strip()
    auth_value = (d.get("auth_value") or "").strip()
    display_name = (d.get("name") or "").strip()

    # Fall back to saved config for blank fields
    saved = datasource_config_manager.get("api") or {}
    if not url:
        url = saved.get("url", "")
    if not url:
        return jsonify({"error": "API URL 不能为空"}), 400
    if not auth_type or auth_type == "none":
        auth_type = saved.get("auth_type", "none")
    if not auth_value:
        auth_value = saved.get("auth_value", "")
    if auth_type not in ("none", "bearer", "api_key"):
        return jsonify({"error": "认证方式无效，支持: none / bearer / api_key"}), 400

    try:
        source = HTTPAPIDataSource(url, auth_type, auth_value, display_name)
        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        datasource_config_manager.save("api", {
            "url": url, "auth_type": auth_type,
            "auth_value": auth_value, "name": display_name
        })
        return jsonify({"ok": True, "source_name": source.name,
                        "schema_preview": source.get_schema()})
    except Exception as exc:
        log.error("[connect-api] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": f"API 连接失败: {exc}"}), 400


@bp.get("/api/datasource-configs")
def list_datasource_configs():
    return jsonify(datasource_config_manager.list_public())


@bp.delete("/api/datasource-configs/<ds_type>")
def delete_datasource_config(ds_type: str):
    datasource_config_manager.delete(ds_type)
    return jsonify({"ok": True})

