#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-flight validation of tool call arguments.

Runs BEFORE the tool dispatch, so dangerous inputs (write SQL,
malformed schemas) never reach the underlying executor.

Centralized here so:
  - The "what's blocked" policy lives in one place
  - Tests can exercise the rules without spinning up an LLM
  - Both BusinessAgent and any future bypass path (e.g. /sql fast path)
    can apply the same guards

SQL validation strategy (AST-level, not keyword matching)
---------------------------------------------------------
Previous approach: keyword blacklist (DROP/DELETE/…) + "must start with SELECT".

Problems with the old approach:
  1. False positives: SELECT update_time FROM … or WHERE note = 'please DELETE'
     were blocked even though they are read-only.
  2. Bypasses: DuckDB allows reading arbitrary files via read_csv('/etc/passwd'),
     ATTACH external databases, INSTALL/LOAD extensions, COPY … TO … — all of
     which can start with SELECT or contain no blocked keywords.

New approach (A4):
  - Use sqlglot to parse the SQL into an AST and inspect structure.
  - Accept only a single SELECT or WITH (CTE) statement.
  - Recursively scan for banned function calls and COPY nodes.
  - For file-read functions (read_csv / read_parquet / read_json / read_text /
    read_blob), do PATH WHITELIST validation: extract the literal path argument,
    resolve it against the workspace's allowed roots (workdir + uploads +
    Information + artifacts + cache), reject if not literal or out of whitelist.
  - Fall back to the old keyword heuristic if sqlglot is unavailable
    (so the app still works without the optional dependency).

DuckDB connection-level lockdown (second layer, in _utils.py, post-A4):
  SET enable_external_access = false  # 仍禁网络（HTTP/S3 扩展）
  # A4 起：不再 SET disabled_filesystems='LocalFileSystem' —— 改由本模块的
  # AST 路径白名单做精细控制，工作目录内文件可直读。
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from data.workspace import WorkspacePathAuthorization

log = logging.getLogger(__name__)

# Tool names whose JSON args carry a `sql` field that runs against the data source.
SQL_TOOLS = {"create_analysis_table", "query_data", "generate_chart", "run_analysis"}

# Functions that must NEVER appear in any SQL we execute, regardless of context.
# 这些是真正的"危险"函数：扩展管理、外部数据库、网络、写入文件、元命令。
# 注意：read_csv / read_parquet 等"文件读取"函数不再在此列表 —— 它们受
# _FILE_READ_FUNCTIONS 路径白名单控制（A4）。
_BANNED_FUNCTIONS = {
    # File system writes
    "copy_to",
    # Extension management (can load arbitrary native code)
    "install", "load",
    # External database attachment
    "attach", "detach",
    # Meta-commands
    "pragma",
    # Network access (httpfs extension)
    "http_get", "httpget", "http_post",
}

# 受路径白名单控制的文件读取函数。
# 这些函数的"第一个参数"必须是字面量字符串（或字面量列表），resolve 后必须在
# workspace 的 allowed_roots 内。非字面量参数（@var / 列引用 / 拼接）一律拒绝。
# sqlglot 对部分函数有专用节点（ReadCSV/ReadParquet/ReadJSON），其余解析为 Anonymous。
_FILE_READ_FUNCTIONS = {
    "read_csv", "read_csv_auto",
    "read_parquet", "read_parquet_auto",
    "read_json", "read_json_auto",
    "read_text", "read_text_auto",
    "read_blob", "read_blob_auto",
}

# sqlglot 的专用文件读取节点类型名（小写）。
# ReadCSV / ReadParquet / ReadJSON 由 sqlglot 专门识别；其余在 Anonymous 里。
_FILE_READ_NODE_TYPES = {"readcsv", "readparquet", "readjson"}

# 通配符字符（用于 read_csv('data/*.csv') 形式）
_GLOB_CHARS = ("*", "?", "[", "]")

# 禁止的网络协议前缀（任何路径参数带这些前缀一律拒绝，防止 SSRF）
_BLOCKED_URL_SCHEMES = (
    "http://", "https://", "ftp://", "ftps://",
    "s3://", "gs://", "gcs://", "azure://", "abfs://", "abfss://",
    "hdfs://", "webhdfs://", "sftp://", "scp://",
)


def _is_file_read_anonymous(node) -> bool:
    """Anonymous 节点是否是文件读取函数。"""
    if type(node).__name__ != "Anonymous":
        return False
    return (node.name or "").lower() in _FILE_READ_FUNCTIONS


def _extract_path_literals(node) -> Tuple[List[str], bool]:
    """从文件读取函数节点提取字面量路径参数。

    返回 (paths, all_literal)：
      - paths: 字面量字符串路径列表（可能多个，如 read_csv(['a.csv','b.csv'])）
      - all_literal: True 表示所有路径参数都是字面量；False 表示存在非字面量参数
        （@var / 列引用 / 拼接表达式等），调用方应拒绝整条 SQL。
    """
    paths: List[str] = []
    all_literal = True

    # 找出"第一个参数"的位置：对 ReadCSV/ReadParquet，是 node.this；
    # 对 Anonymous，是 node.expressions[0]。
    # 但实际上 read_csv 的第一个参数既可以是字面量，也可以是 Array of literals。
    candidates = []

    # ReadCSV/ReadParquet/ReadJSON：this 字段是第一个参数
    if hasattr(node, "this") and node.this is not None:
        candidates.append(node.this)
    # Anonymous：expressions 列表是所有参数，第一项是路径
    if type(node).__name__ == "Anonymous":
        # node.expressions 是所有参数（路径 + 命名参数如 header=true）
        # 只取第一个作为路径参数
        if node.expressions:
            candidates = [node.expressions[0]]

    for cand in candidates:
        _extract_from_expr(cand, paths)

    # 检查路径参数是否都是字面量：如果 candidates 非空但 paths 空，说明非字面量
    if candidates and not paths:
        all_literal = False

    return paths, all_literal


def _extract_from_expr(expr, paths: List[str]) -> None:
    """从单个表达式节点提取字面量路径，追加到 paths。"""
    # 单个字符串字面量
    try:
        from sqlglot import expressions as exp
    except ImportError:
        return

    if isinstance(expr, exp.Literal) and expr.is_string:
        paths.append(expr.this)
        return

    # Array of literals: read_csv(['a.csv', 'b.csv'])
    if isinstance(expr, exp.Array):
        for item in expr.expressions or []:
            if isinstance(item, exp.Literal) and item.is_string:
                paths.append(item.this)
            else:
                # 列表里有非字面量
                pass
        return

    # 其他类型（Column / Parameter / Abs / Binary 操作等）→ 非字面量，不追加
    # 调用方通过 paths 为空判断


def _validate_file_read_paths(stmt, allowed_roots: List[Path]) -> Optional[str]:
    """扫描 SQL AST，对所有文件读取函数做路径白名单校验。

    返回错误字符串或 None。
    """
    try:
        from sqlglot import expressions as exp
    except ImportError:
        return None  # sqlglot 不可用，跳过路径白名单（heuristic fallback 会兜底）

    if not allowed_roots:
        # 无白名单根（未挂载 workspace 且无默认根）—— 一律拒绝文件读取
        # 实际上 agent.py 总会传 uploads/Information 作为默认根，这里防御性处理
        allowed_roots = []

    # 标准化白名单根（resolve）
    norm_roots: List[Path] = []
    for r in allowed_roots:
        try:
            norm_roots.append(r.expanduser().resolve())
        except (OSError, RuntimeError):
            continue

    # 遍历所有节点，找文件读取函数
    for node in stmt.walk():
        is_read_node = False
        paths: List[str] = []

        # 专用节点：ReadCSV / ReadParquet / ReadJSON
        node_type = type(node).__name__.lower()
        if node_type in _FILE_READ_NODE_TYPES:
            is_read_node = True
            paths, all_lit = _extract_path_literals(node)
            if not all_lit:
                return (f"禁止非字面量路径参数：{node_type}() 的路径参数必须是"
                        "字符串字面量，不接受变量/列引用/拼接表达式。")

        # Anonymous 节点：read_csv_auto / read_text / read_blob 等
        elif _is_file_read_anonymous(node):
            is_read_node = True
            paths, all_lit = _extract_path_literals(node)
            if not all_lit:
                return (f"禁止非字面量路径参数：{node.name}() 的路径参数必须是"
                        "字符串字面量，不接受变量/列引用/拼接表达式。")

        # DuckDB accepts SELECT * FROM 'file.csv' as a file scan. sqlglot
        # parses it as a quoted Table identifier rather than ReadCSV, so it
        # must pass through the same Workspace whitelist.
        elif isinstance(node, exp.Table):
            identifier = getattr(node, "this", None)
            quoted = bool(getattr(identifier, "args", {}).get("quoted"))
            name = str(getattr(node, "name", "") or "")
            if quoted and _looks_like_file_table(name):
                is_read_node = True
                paths = [name]

        if not is_read_node:
            continue

        # 校验每个路径
        for path_str in paths:
            err = _check_single_path(path_str, norm_roots)
            if err:
                return err

    return None


def _looks_like_file_table(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return (
        "/" in lowered
        or "\\" in lowered
        or lowered.endswith((
            ".csv", ".tsv", ".parquet", ".json", ".jsonl",
            ".xlsx", ".xls", ".txt",
        ))
    )


def _check_single_path(path_str: str, norm_roots: List[Path]) -> Optional[str]:
    """校验单个路径字符串是否在白名单根目录内。"""
    if not path_str:
        return "文件读取函数的路径参数为空。"

    # ── 网络 URL 一律拒绝（SSRF 防护）──────────────────────────────────────
    path_lower = path_str.lower().strip()
    for scheme in _BLOCKED_URL_SCHEMES:
        if path_lower.startswith(scheme):
            return (f"禁止网络路径：{path_str}（仅允许本地工作目录/上传目录内文件，"
                    "不接受 http/https/s3/gs/azure/hdfs 等 URL）。")

    # ── 路径解析 ────────────────────────────────────────────────────────────
    # 相对路径：相对第一个白名单根解析（通常是 workdir，未挂载时是 uploads）
    # 绝对路径：直接 resolve
    try:
        candidate = Path(path_str)
        if candidate.is_absolute():
            resolved = candidate.expanduser().resolve(strict=False)
        else:
            # 相对路径：依次尝试每个根，第一个存在的为准；都不存在则用第一个根解析
            resolved = None
            for root in norm_roots:
                trial = (root / path_str).expanduser().resolve(strict=False)
                if trial.exists():
                    resolved = trial
                    break
            if resolved is None:
                # 文件不存在，用第一个根解析做白名单校验
                if norm_roots:
                    resolved = (norm_roots[0] / path_str).expanduser().resolve(strict=False)
                else:
                    resolved = candidate.expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as e:
        return f"路径解析失败：{path_str}（{e}）"

    # 通配符路径：检查父目录（去掉通配符部分后）是否在白名单内
    # 例如 read_csv('data/*.csv') —— 检查 data/ 目录是否在白名单
    has_glob = any(c in path_str for c in _GLOB_CHARS)
    check_path = resolved
    if has_glob:
        # 取 resolved 的 parent 作为校验目标
        check_path = resolved.parent

    # 白名单校验：必须在某个根目录下
    in_whitelist = False
    for root in norm_roots:
        try:
            check_path.relative_to(root)
            in_whitelist = True
            break
        except ValueError:
            continue

    if not in_whitelist:
        return (f"路径不在允许的工作目录/上传目录内：{path_str} "
                f"(resolved: {check_path})。允许的根目录："
                + ", ".join(str(r) for r in norm_roots))

    return None


def _validate_sql_ast(sql: str, allowed_roots: Optional[List[Path]] = None) -> Optional[str]:
    """AST-level SQL validation using sqlglot.

    Returns an error string if the SQL is disallowed, else None.
    Falls back to a lightweight heuristic when sqlglot is not installed.

    Args:
        sql: SQL string to validate.
        allowed_roots: List of Path roots that file-read functions (read_csv etc.)
            are allowed to read from. If None or empty, file reads are rejected
            entirely (caller should pass uploads/Information as defaults).
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        # sqlglot not installed — fall back to conservative heuristic
        log.debug("[validate] sqlglot not available, using heuristic fallback")
        return _validate_sql_heuristic(sql)

    # ── Parse ──────────────────────────────────────────────────────────────────
    try:
        statements = sqlglot.parse(sql, dialect="duckdb", error_level=sqlglot.ErrorLevel.RAISE)
    except Exception as exc:
        # Parse error — could be valid DuckDB but not parseable by sqlglot.
        # Fall back to heuristic rather than blocking legitimate queries.
        log.debug("[validate] sqlglot parse error (%s), falling back to heuristic", exc)
        return _validate_sql_heuristic(sql)

    # ── Multi-statement check ──────────────────────────────────────────────────
    if len(statements) > 1:
        return "不允许多语句 SQL（检测到分号分隔的多条语句）。"
    if not statements:
        return "SQL 语句为空。"

    stmt = statements[0]

    # ── Top-level statement type ───────────────────────────────────────────────
    # sqlglot represents SELECT ... UNION SELECT ... as exp.Union. All read-only
    # query expressions inherit from exp.Query, while INSERT/UPDATE/etc. do not.
    if not isinstance(stmt, exp.Query):
        stmt_type = type(stmt).__name__
        return (
            f"只允许 SELECT / WITH / UNION 查询，检测到: {stmt_type}。"
            "请使用 SELECT 语句查询数据。"
        )

    # ── Banned function scan (recursive, 非 file-read 类) ─────────────────────
    for node in stmt.walk():
        # Anonymous function (e.g. read_csv_auto which sqlglot may not recognise)
        if isinstance(node, exp.Anonymous):
            fname = (node.name or "").lower()
            if fname in _BANNED_FUNCTIONS:
                return f"禁止使用函数 {fname}()：该函数可访问文件系统写入/网络/扩展。"

        # Named function nodes
        if isinstance(node, exp.Func):
            fname = type(node).__name__.lower()
            # Also check the sql_name if available
            sql_name = getattr(node, "sql_name", lambda: "")()
            for candidate in (fname, sql_name.lower()):
                if candidate in _BANNED_FUNCTIONS:
                    return f"禁止使用函数 {candidate}()：该函数可访问文件系统写入/网络/扩展。"

    # ── COPY statement scan ────────────────────────────────────────────────────
    for node in stmt.walk():
        if isinstance(node, exp.Copy):
            return "禁止 COPY 操作：不允许将数据写入文件。"

    # ── File-read path whitelist (A4) ──────────────────────────────────────────
    # 默认根目录：即使未挂载 workspace，也允许读 uploads/Information
    if allowed_roots is None:
        allowed_roots = _default_allowed_roots()
    err = _validate_file_read_paths(stmt, allowed_roots)
    if err:
        return err

    return None  # all good


def _default_allowed_roots() -> List[Path]:
    """默认白名单根目录（未挂载 workspace 时用）。

    包含 uploads/ 和 Information/，让 read_csv 能读已上传文件。
    不包含工作目录（未挂载时不存在）。
    """
    from infrastructure.paths import data_path, resource_path
    return [data_path("uploads"), resource_path("Information")]


def _validate_sql_heuristic(sql: str) -> Optional[str]:
    """Lightweight keyword-based fallback used when sqlglot is unavailable.

    Less precise than AST validation (can produce false positives on column
    names / string literals), but keeps security guarantees when the optional
    dependency is missing.

    注意：heuristic 模式下无法做路径白名单（拿不到 AST 节点），所以一律拒绝
    file-read 函数（保守策略，回到 A4 之前的行为）。
    """
    import re

    sql_stripped = sql.strip()
    sql_lower = sql_stripped.lower()

    if not sql_lower:
        return "SQL 语句为空。"

    # ── Multi-statement check (split on unquoted semicolons) ──────────────────
    sql_no_trail = sql_stripped.rstrip(";").strip()
    _no_quotes = re.sub(r"'[^']*'|\"[^\"]*\"", "", sql_no_trail)
    if ";" in _no_quotes:
        return "不允许多语句 SQL（检测到分号分隔的多条语句）。"

    if not sql_lower.startswith("select") and not sql_lower.startswith("with"):
        return f"只允许 SELECT/WITH 查询。检测到: {sql_lower[:60]}"

    # ── Write-operation keywords ───────────────────────────────────────────────
    _WRITE_TOKENS = [
        r"\bdrop\b", r"\bdelete\b", r"\btruncate\b",
        r"\binsert\b", r"\bupdate\b", r"\balter\b",
        r"\bcreate\s+table\b", r"\bcreate\s+index\b",
    ]
    for pattern in _WRITE_TOKENS:
        if re.search(pattern, sql_lower):
            token = re.sub(r"\\[bsS]|\(.*\)", "", pattern).strip().replace("\\", "")
            return f"禁止写操作关键字 {token}：只允许 SELECT 查询。"

    # ── Banned DuckDB extension/network keywords (heuristic 模式仍禁) ─────────
    _BANNED_TOKENS = [
        r"\binstall\b", r"\bload\b", r"\battach\b", r"\bdetach\b",
        r"\bcopy\b", r"\bpragma\b",
        r"\bhttp_get\b", r"\bhttp_post\b",
    ]
    for pattern in _BANNED_TOKENS:
        if re.search(pattern, sql_lower):
            token = pattern.replace(r"\b", "").strip()
            return f"禁止使用 {token}：该操作可访问文件系统写入/网络/扩展。"

    # ── File-read 函数：heuristic 模式下一律拒绝（无法做路径白名单）──────────
    _FILE_READ_TOKENS = [
        r"\bread_csv\b", r"\bread_csv_auto\b", r"\bread_json\b", r"\bread_json_auto\b",
        r"\bread_parquet\b", r"\bread_parquet_auto\b",
        r"\bread_text\b", r"\bread_text_auto\b",
        r"\bread_blob\b", r"\bread_blob_auto\b",
    ]
    for pattern in _FILE_READ_TOKENS:
        if re.search(pattern, sql_lower):
            token = pattern.replace(r"\b", "").strip()
            return (f"heuristic 模式下禁止 {token}（sqlglot 不可用时无法做路径白名单校验）。"
                    "请安装 sqlglot 或改用已注册表查询。")

    return None


def validate_tool_args(
    name: str,
    args: Dict[str, Any],
    allowed_roots: Optional[List[Path]] = None,
    workspace_authorization: Optional["WorkspacePathAuthorization"] = None,
) -> Optional[str]:
    """Return an error string if args are obviously invalid, else None.

    Policy:
      - SQL_TOOLS: `sql` is required; AST-level validation (SELECT/WITH only,
        no banned functions, no multi-statement, file-read path whitelist)
      - run_analysis: analysis_name + target_column required
      - propose_ppt_outline / generate_ppt: `slides` (if present) must be a list
      - propose_dashboard_outline / generate_dashboard: `widgets` (if present) must be a list

    Args:
        name: tool name.
        args: tool arguments dict.
        allowed_roots: legacy optional list of roots for callers without a
            Workspace identity.
        workspace_authorization: immutable, workspace_id-bound path capability.
            When present it is the only source of SQL file-read roots.
            If None, defaults to [uploads/, Information/]. Pass the workspace's
            allowed roots (workdir + uploads + Information + artifacts + cache)
            to enable read_csv('file.xlsx') from the mounted workdir.
    """
    if workspace_authorization is not None:
        allowed_roots = list(workspace_authorization.allowed_roots)
    if name in SQL_TOOLS:
        sql = (args.get("sql") or "").strip()
        if not sql:
            return f"'{name}' requires a non-empty 'sql' argument."
        err = _validate_sql_ast(sql, allowed_roots=allowed_roots)
        if err:
            return f"'{name}' SQL validation failed: {err}"

    if name == "run_analysis":
        if not args.get("analysis_name"):
            return "'run_analysis' requires 'analysis_name'."
        if not args.get("target_column"):
            return "'run_analysis' requires 'target_column'."

    if name == "ask_user":
        question = args.get("question")
        if not isinstance(question, str) or not question.strip():
            return "'ask_user' requires a non-empty string 'question'."
        options = args.get("options")
        if not isinstance(options, list):
            return "'ask_user': 'options' must be a list."
        if not 2 <= len(options) <= 6:
            return "'ask_user': 'options' must contain 2-6 items."
        if any(not isinstance(option, str) or not option.strip() for option in options):
            return "'ask_user': every option must be a non-empty string."

    if name in ("propose_ppt_outline", "generate_ppt"):
        slides = args.get("slides")
        if slides is not None and not isinstance(slides, list):
            return f"'{name}': 'slides' must be a list."

    if name in ("propose_dashboard_outline", "generate_dashboard"):
        widgets = args.get("widgets")
        if widgets is not None and not isinstance(widgets, list):
            return f"'{name}': 'widgets' must be a list."

    if name == "propose_report_outline":
        if not str(args.get("title") or "").strip():
            return "'propose_report_outline' requires a non-empty 'title'."
        sections = args.get("sections")
        if not isinstance(sections, list) or not sections:
            return (
                "'propose_report_outline' requires a non-empty 'sections' list."
            )
        if any(not isinstance(section, dict) for section in sections):
            return (
                "'propose_report_outline': every section must be an object."
            )

    return None


def normalize_ask_user_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize common provider variants to the ask_user string contract.

    Some OpenAI-compatible providers occasionally emit option objects even
    though the JSON schema declares an array of strings. Accept familiar
    display fields at this boundary, then let ``validate_tool_args`` reject
    anything still malformed.
    """
    normalized = dict(args or {})
    question = normalized.get("question")
    normalized["question"] = question.strip() if isinstance(question, str) else ""

    raw_options = normalized.get("options")
    options: List[str] = []
    if isinstance(raw_options, list):
        for raw_option in raw_options:
            text: Any = raw_option
            if isinstance(raw_option, dict):
                text = next(
                    (
                        raw_option.get(key)
                        for key in ("label", "text", "title", "name", "value")
                        if isinstance(raw_option.get(key), str)
                        and raw_option.get(key).strip()
                    ),
                    "",
                )
            if not isinstance(text, str):
                continue
            text = text.strip()
            if text and text not in options:
                options.append(text)
            if len(options) == 6:
                break
    normalized["options"] = options
    return normalized
