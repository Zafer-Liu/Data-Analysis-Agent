"""MCP server configuration API — Flask Blueprint."""
import json
import logging
import os
import re
import threading
from pathlib import Path
from flask import Blueprint, request, jsonify
from infrastructure.paths import is_cloud_managed_runtime, resource_path
from .state import require_local_runtime_config

log = logging.getLogger(__name__)

bp = Blueprint("mcp", __name__)

# Shell metacharacters that must not appear in stdio args
_SHELL_META_RE = re.compile(r'[;&|`$<>()\n]|\|\||&&')

# Env keys that could hijack subprocess execution
_BLOCKED_ENV_KEYS = frozenset({
    "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH", "PYTHONPATH",
})

# Mirrored from mcp_manager — validated at API layer before reaching transport
from agent.mcp_manager import STDIO_ALLOWED_COMMANDS


def _validate_stdio(command: str, args: list, env: dict) -> tuple[bool, str]:
    # Strip directory and extension so both a bare "node" and a full
    # "C:\Program Files\nodejs\node.exe" validate against the whitelist.
    basename = os.path.splitext(os.path.basename(command or ""))[0]
    if basename not in STDIO_ALLOWED_COMMANDS:
        return False, f"命令 '{command}' 不在安全白名单中（允许: {', '.join(sorted(STDIO_ALLOWED_COMMANDS))}）"
    for arg in args:
        if _SHELL_META_RE.search(str(arg)):
            return False, f"参数包含不允许的 shell 特殊字符: {arg!r}"
    for key in env:
        if key in _BLOCKED_ENV_KEYS:
            return False, f"不允许覆盖环境变量: {key}"
    return True, ""


def _get_managers():
    from LLM.mcp_config_manager import get_mcp_config_manager
    from agent.mcp_manager import get_mcp_manager
    return get_mcp_config_manager(), get_mcp_manager()


# ── Built-in common MCP presets ─────────────────────────────────
# One-click fill templates for popular stdio MCP servers.
# All commands must stay within STDIO_ALLOWED_COMMANDS (npx/uvx/...).
# Placeholders in args/env are replaced by the user after fill.
_MCP_PRESETS = [
    {
        "id": "filesystem", "label": "文件系统 (Filesystem)",
        "description": "读写指定目录中的文件（npx）",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:\\your\\folder"],
        "env": {}, "note": "请把最后一个参数改为你允许访问的目录",
        "needs_input": True,
    },
    {
        "id": "fetch", "label": "网页抓取 (Fetch)",
        "description": "抓取网页并转为 Markdown（uvx）",
        "command": "uvx", "args": ["mcp-server-fetch"],
        "env": {}, "note": "需要已安装 uv",
        "needs_input": False,
    },
    {
        "id": "memory", "label": "记忆库 (Memory)",
        "description": "基于知识图谱的持久记忆（npx）",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"],
        "env": {}, "note": "",
        "needs_input": False,
    },
    {
        "id": "sequential-thinking", "label": "顺序思考 (Sequential Thinking)",
        "description": "结构化多步推理工具（npx）",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequentialthinking"],
        "env": {}, "note": "",
        "needs_input": False,
    },
    {
        "id": "sqlite", "label": "SQLite 数据库",
        "description": "查询本地 SQLite 数据库（uvx）",
        "command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "D:\\your\\db.sqlite"],
        "env": {}, "note": "请把 --db-path 改为你的数据库文件路径",
        "needs_input": True,
    },
    {
        "id": "git", "label": "Git 仓库",
        "description": "操作本地 Git 仓库（uvx）",
        "command": "uvx", "args": ["mcp-server-git", "--repository", "D:\\your\\repo"],
        "env": {}, "note": "请把 --repository 改为你的仓库路径",
        "needs_input": True,
    },
    {
        "id": "github", "label": "GitHub",
        "description": "仓库/Issue/PR/搜索（npx）",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "请填入你的GitHub令牌"},
        "note": "需要 GitHub Personal Access Token",
        "needs_input": True,
    },
    {
        "id": "brave-search", "label": "Brave 搜索",
        "description": "网页/本地/新闻搜索（npx）",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env": {"BRAVE_API_KEY": "请填入你的BraveAPI密钥"},
        "note": "需要 Brave Search API Key",
        "needs_input": True,
    },
    {
        "id": "playwright", "label": "Playwright 浏览器（微软官方）",
        "description": "自动化浏览器操作/截图/表单填写（npx）",
        "command": "npx", "args": ["@playwright/mcp@latest"],
        "env": {}, "note": "微软官方包 @playwright/mcp，首次运行会下载浏览器",
        "needs_input": False,
    },
    {
        "id": "puppeteer", "label": "Puppeteer 浏览器",
        "description": "浏览器自动化与网页截图（npx）",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "env": {}, "note": "首次运行会下载 Chromium",
        "needs_input": False,
    },
]


@bp.get("/api/mcp/presets")
def list_presets():
    return jsonify({"presets": [dict(p) for p in _MCP_PRESETS]})


@bp.get("/api/mcp/servers")
def list_servers():
    cfg_mgr, mcp_mgr = _get_managers()
    servers = cfg_mgr.list_servers()
    runtime = {s["server_id"]: s for s in mcp_mgr.get_all_status()}
    result = []
    for sid, cfg in servers.items():
        entry = dict(cfg)
        if is_cloud_managed_runtime():
            entry["env"] = {key: "" for key in (entry.get("env") or {})}
            entry["headers"] = {key: "" for key in (entry.get("headers") or {})}
        rt = runtime.get(sid, {})
        entry["status"] = rt.get("status", "disconnected")
        entry["last_error"] = rt.get("last_error", "")
        entry["tool_count"] = rt.get("tool_count", 0)
        result.append(entry)
    bundled = resource_path("MCP").is_dir()
    return jsonify({
        "servers": result,
        "bundled_resources_available": bundled,
        "bundled_message": "" if bundled else "内置 MCP 未随安装包提供",
    })


@bp.post("/api/mcp/servers")
@require_local_runtime_config
def add_server():
    data = request.get_json(force=True) or {}
    server_id = (data.get("server_id") or "").strip()
    label     = (data.get("label") or server_id).strip()
    transport = (data.get("transport") or "").strip()
    description = (data.get("description") or "").strip()

    if not server_id:
        return jsonify({"error": "server_id 不能为空"}), 400
    if transport not in ("stdio", "sse"):
        return jsonify({"error": "transport 必须是 'stdio' 或 'sse'"}), 400

    if transport == "stdio":
        command = (data.get("command") or "").strip()
        args    = data.get("args", [])
        env     = data.get("env", {})
        if not isinstance(args, list):
            return jsonify({"error": "args 必须是数组"}), 400
        if not isinstance(env, dict):
            return jsonify({"error": "env 必须是对象"}), 400
        ok, err = _validate_stdio(command, args, env)
        if not ok:
            return jsonify({"error": err}), 400
        url = ""
        headers = {}
    else:
        url = (data.get("url") or "").strip()
        headers = data.get("headers", {})
        if not url:
            return jsonify({"error": "SSE transport 需要 url"}), 400
        if not isinstance(headers, dict):
            return jsonify({"error": "headers 必须是对象"}), 400
        command, args, env = "", [], {}

    from LLM.mcp_config_manager import MCPServerConfig, get_mcp_config_manager
    from agent.mcp_manager import get_mcp_manager

    cfg = MCPServerConfig(
        server_id=server_id, label=label, transport=transport,
        description=description, enabled=True,
        command=command, args=args, env=env,
        url=url, headers=headers,
    )
    cfg_mgr = get_mcp_config_manager()
    ok, msg = cfg_mgr.add_server(cfg)
    if not ok:
        return jsonify({"error": msg}), 400

    mcp_mgr = get_mcp_manager()
    mcp_mgr.add_server(cfg)

    # Trigger lazy connect in background — non-blocking response
    def _connect_bg():
        mcp_mgr.connect_server(server_id)

    threading.Thread(target=_connect_bg, daemon=True, name=f"mcp-connect-{server_id}").start()

    return jsonify({"ok": True, "message": msg, "server_id": server_id}), 201


@bp.put("/api/mcp/servers/<server_id>")
@require_local_runtime_config
def update_server(server_id: str):
    data = request.get_json(force=True) or {}
    cfg_mgr, mcp_mgr = _get_managers()
    existing = cfg_mgr.get_server(server_id)
    if not existing:
        return jsonify({"error": "服务器不存在"}), 404

    transport = (data.get("transport") or existing.transport).strip()
    label       = (data.get("label") or existing.label).strip()
    description = data.get("description", existing.description)

    updates = {"label": label, "description": description, "transport": transport}

    if transport == "stdio":
        command = (data.get("command") or existing.command).strip()
        args    = data.get("args", existing.args)
        env     = data.get("env", existing.env)
        if not isinstance(args, list):
            return jsonify({"error": "args 必须是数组"}), 400
        if not isinstance(env, dict):
            return jsonify({"error": "env 必须是对象"}), 400
        ok, err = _validate_stdio(command, args, env)
        if not ok:
            return jsonify({"error": err}), 400
        updates.update(command=command, args=args, env=env, url="", headers={})
    else:
        url     = (data.get("url") or existing.url).strip()
        headers = data.get("headers", existing.headers)
        if not url:
            return jsonify({"error": "SSE transport 需要 url"}), 400
        if not isinstance(headers, dict):
            return jsonify({"error": "headers 必须是对象"}), 400
        updates.update(url=url, headers=headers, command="", args=[], env={})

    ok, msg = cfg_mgr.update_server(server_id, **updates)
    if not ok:
        return jsonify({"error": msg}), 400

    # Re-register updated config and reconnect
    mcp_mgr.remove_server(server_id)
    mcp_mgr.add_server(cfg_mgr.get_server(server_id))
    def _bg():
        mcp_mgr.connect_server(server_id)
    threading.Thread(target=_bg, daemon=True, name=f"mcp-reconnect-{server_id}").start()

    return jsonify({"ok": True, "message": msg})


@bp.delete("/api/mcp/servers/<server_id>")
@require_local_runtime_config
def remove_server(server_id: str):
    cfg_mgr, mcp_mgr = _get_managers()
    mcp_mgr.remove_server(server_id)
    ok, msg = cfg_mgr.remove_server(server_id)
    if not ok:
        return jsonify({"error": msg}), 404
    return jsonify({"ok": True, "message": msg})


@bp.post("/api/mcp/servers/<server_id>/enable")
@require_local_runtime_config
def enable_server(server_id: str):
    cfg_mgr, _ = _get_managers()
    ok, msg = cfg_mgr.set_enabled(server_id, True)
    if not ok:
        return jsonify({"error": msg}), 404
    return jsonify({"ok": True, "message": msg})


@bp.post("/api/mcp/servers/<server_id>/disable")
@require_local_runtime_config
def disable_server(server_id: str):
    cfg_mgr, _ = _get_managers()
    ok, msg = cfg_mgr.set_enabled(server_id, False)
    if not ok:
        return jsonify({"error": msg}), 404
    return jsonify({"ok": True, "message": msg})


@bp.post("/api/mcp/servers/<server_id>/connect")
@require_local_runtime_config
def connect_server(server_id: str):
    _, mcp_mgr = _get_managers()

    def _bg():
        mcp_mgr.connect_server(server_id)

    threading.Thread(target=_bg, daemon=True, name=f"mcp-connect-{server_id}").start()
    return jsonify({"ok": True, "message": f"正在连接 {server_id}…"})


@bp.get("/api/mcp/servers/<server_id>/tools")
def server_tools(server_id: str):
    _, mcp_mgr = _get_managers()
    status = mcp_mgr.get_server_status(server_id)
    if not status:
        return jsonify({"error": "服务器不存在"}), 404
    tools = mcp_mgr.get_server_tools(server_id)
    return jsonify({"server_id": server_id, "tools": tools})


# ---------------------------------------------------------------------------
# LLM-assisted MCP config parser
# ---------------------------------------------------------------------------

_PARSE_SYSTEM = """You are a configuration parser for MCP (Model Context Protocol) servers.
The user will paste arbitrary text: an npm/npx/uvx install command, a JSON config snippet,
a README excerpt, a docker-compose fragment, or any description of an MCP server.

Extract the MCP server configuration and return ONLY a JSON object — no markdown, no explanation.

Schema:
{
  "transport": "stdio" | "sse",
  "label": "<human-friendly name, infer from package name if not given>",
  "description": "<one-line description of what the server does, in Chinese>",
  "command": "<executable: npx | uvx | node | python | python3 | uv | deno — stdio only>",
  "args": ["<arg1>", "<arg2>", ...],
  "env": {"KEY": "<VALUE or PLACEHOLDER like YOUR_API_KEY_HERE>"},
  "url": "<SSE endpoint URL — sse only, empty string otherwise>",
  "headers": {"KEY": "<VALUE or PLACEHOLDER>"}
}

Rules:
- transport is "stdio" when the text shows a local command; "sse" when it shows an HTTP URL endpoint
- For stdio: command must be one of: npx, uvx, node, python, python3, uv, deno
- If an env var value is unknown/secret, use a clear placeholder like "YOUR_XXX_API_KEY"
- args must NOT include shell metacharacters: ; & | ` $ < > ( ) newline
- If you cannot determine a required field, use an empty string
- Always respond with valid JSON only, starting with { and ending with }"""

_PARSE_USER_TMPL = "Parse this MCP server configuration:\n\n{text}"

_ALLOWED_COMMANDS = frozenset({"npx", "npm", "uvx", "node", "python", "python3", "uv", "deno"})
_SHELL_META = re.compile(r'[;&|`$<>()\n]|\|\||&&')


def _sanitize_parsed(data: dict) -> tuple[dict, list[str]]:
    """Validate and sanitize fields returned by LLM. Returns (clean_data, warnings)."""
    warnings = []

    transport = data.get("transport", "stdio")
    if transport not in ("stdio", "sse"):
        transport = "stdio"
        warnings.append("transport 字段无效，已重置为 stdio")

    label = str(data.get("label") or "").strip()[:80]
    description = str(data.get("description") or "").strip()[:200]

    if transport == "stdio":
        command = str(data.get("command") or "").strip()
        basename = os.path.splitext(os.path.basename(command))[0]
        if basename not in _ALLOWED_COMMANDS:
            warnings.append(f"命令 '{command}' 不在安全白名单，已清空 — 请手动填写")
            command = ""

        raw_args = data.get("args") or []
        clean_args = []
        for a in raw_args:
            s = str(a)
            if _SHELL_META.search(s):
                warnings.append(f"参数 '{s[:40]}' 含危险字符，已过滤")
            else:
                clean_args.append(s)

        raw_env = data.get("env") or {}
        clean_env = {}
        for k, v in raw_env.items():
            if _SHELL_META.search(str(k)) or _SHELL_META.search(str(v)):
                warnings.append(f"环境变量 '{k}' 含危险字符，已过滤")
            else:
                clean_env[str(k)[:128]] = str(v)[:512]

        return {
            "transport": "stdio",
            "label": label,
            "description": description,
            "command": command,
            "args": clean_args,
            "env": clean_env,
            "url": "",
            "headers": {},
        }, warnings

    else:  # sse
        url = str(data.get("url") or "").strip()[:1024]
        raw_headers = data.get("headers") or {}
        clean_headers = {}
        for k, v in raw_headers.items():
            clean_headers[str(k)[:128]] = str(v)[:512]

        return {
            "transport": "sse",
            "label": label,
            "description": description,
            "command": "",
            "args": [],
            "env": {},
            "url": url,
            "headers": clean_headers,
        }, warnings


@bp.post("/api/mcp/parse")
@require_local_runtime_config
def parse_mcp_config():
    """Use the configured LLM to parse free-form MCP server descriptions."""
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text 不能为空"}), 400
    if len(text) > 8000:
        return jsonify({"error": "输入过长，请粘贴核心配置部分（< 8000 字符）"}), 400

    try:
        from LLM.llm_config_manager import get_llm_client_with_fallback
        client, provider, cfg = get_llm_client_with_fallback()
    except ValueError as e:
        return jsonify({"error": f"LLM 未配置，无法使用智能解析：{e}"}), 503

    try:
        resp = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": _PARSE_SYSTEM},
                {"role": "user",   "content": _PARSE_USER_TMPL.format(text=text)},
            ],
            max_tokens=512,
            temperature=0,
        )
        raw = resp.choices[0].message.content or ""
        # Strip markdown code fences if model wrapped the JSON
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw.strip())
        parsed = __import__("json").loads(raw)
    except __import__("json").JSONDecodeError as e:
        return jsonify({"error": f"LLM 返回格式无法解析，请重试或手动填写：{e}"}), 422
    except Exception as e:
        return jsonify({"error": f"LLM 调用失败：{e}"}), 502

    clean, warnings = _sanitize_parsed(parsed)
    return jsonify({"ok": True, "config": clean, "warnings": warnings})


# ---------------------------------------------------------------------------
# Local MCP package directory scanner
# ---------------------------------------------------------------------------

_MCP_SDK_MARKERS = {
    "@modelcontextprotocol/sdk",
    "mcp",                        # Python MCP SDK
    "fastmcp",
}

_SCAN_SYSTEM = """You are helping configure an MCP (Model Context Protocol) server.
Given a package's README excerpt and package.json summary, extract:
1. A short one-line description of what this MCP server does (in Chinese, ≤60 chars)
2. Any environment variables the server needs (name and purpose)
3. Any required command-line arguments beyond the entry file (e.g. directory paths)

Reply ONLY with a JSON object, no markdown:
{
  "description": "<Chinese description>",
  "env": {"VAR_NAME": "<purpose or example value>"},
  "extra_args": ["<arg1>", "<arg2>"]
}
If nothing is needed for env or extra_args, use empty object/array."""


def _safe_path(raw: str) -> tuple[Path | None, str]:
    """Resolve and validate the user-supplied path. Returns (path, error)."""
    try:
        p = Path(raw.strip()).resolve()
    except Exception as e:
        log.debug("[mcp] _safe_path resolve failed: %s", e)
        return None, "路径格式无效"
    # Block path traversal — resolved path must be absolute and not empty
    if not p.is_absolute():
        return None, "必须是绝对路径"
    # Must exist and be a directory
    if not p.exists():
        return None, f"路径不存在: {p}"
    if not p.is_dir():
        return None, "路径必须指向一个目录"
    return p, ""


def _find_package_json(base: Path) -> Path | None:
    """
    Look for the MCP package's own package.json.
    User may give the wrapper dir (containing node_modules) OR the package dir itself.
    Strategy: prefer the direct package.json if it looks like an MCP pkg,
    then walk one level into node_modules.
    """
    # Direct hit — only accept if it actually looks like an MCP package
    direct = base / "package.json"
    if direct.exists():
        try:
            pkg = json.loads(direct.read_text(encoding="utf-8"))
            if _check_is_mcp(pkg):
                return direct
        except Exception as e:
            log.debug("[mcp] failed to parse direct package.json: %s", e)
    # One level inside node_modules (e.g. user gave the node_modules parent)
    nm = base / "node_modules"
    if nm.is_dir():
        for child in nm.iterdir():
            if child.name.startswith("."):
                continue
            pkg = child / "package.json"
            if pkg.exists():
                try:
                    data = json.loads(pkg.read_text(encoding="utf-8"))
                    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    if any(m in deps for m in _MCP_SDK_MARKERS):
                        return pkg
                except Exception as e:
                    log.debug("[mcp] failed to parse node_modules package.json: %s", e)
            # scoped packages: @scope/name
            if child.name.startswith("@"):
                for scoped in child.iterdir():
                    spkg = scoped / "package.json"
                    if spkg.exists():
                        try:
                            data = json.loads(spkg.read_text(encoding="utf-8"))
                            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                            if any(m in deps for m in _MCP_SDK_MARKERS):
                                return spkg
                        except Exception as e:
                            log.debug("[mcp] failed to parse scoped package.json: %s", e)
    return None


def _infer_node_entry(pkg_dir: Path, pkg: dict) -> tuple[str, list[str], int]:
    """
    Return (entry_file_abs, extra_args, confidence 0-100) for a Node.js package.
    Confidence reflects how certain we are this is the right entry point.
    """
    # Priority 1: bin field — most explicit
    bin_field = pkg.get("bin")
    if isinstance(bin_field, str):
        entry = (pkg_dir / bin_field).resolve()
        if entry.exists():
            return str(entry), [], 95
    elif isinstance(bin_field, dict):
        # Prefer bin entry whose key contains "mcp"
        candidates = sorted(
            bin_field.items(),
            key=lambda kv: (0 if "mcp" in kv[0].lower() else 1, kv[0])
        )
        for _key, rel in candidates:
            entry = (pkg_dir / rel).resolve()
            if entry.exists():
                return str(entry), [], 90

    # Priority 2: scripts.start — "node dist/index.js" style
    start_script = pkg.get("scripts", {}).get("start", "")
    m = re.search(r'node\s+([\w./\\-]+\.(?:js|mjs|cjs))', start_script)
    if m:
        entry = (pkg_dir / m.group(1)).resolve()
        if entry.exists():
            return str(entry), [], 80

    # Priority 3: main field
    main = pkg.get("main", "")
    if main:
        entry = (pkg_dir / main).resolve()
        if entry.exists():
            return str(entry), [], 70

    # Priority 4: common fallback paths
    for fallback in ("dist/index.js", "index.js", "build/index.js", "out/index.js"):
        entry = (pkg_dir / fallback).resolve()
        if entry.exists():
            return str(entry), [], 50

    return "", [], 0


def _check_is_mcp(pkg: dict) -> bool:
    """Return True if this package looks like an MCP server."""
    all_deps = {
        **pkg.get("dependencies", {}),
        **pkg.get("devDependencies", {}),
        **pkg.get("peerDependencies", {}),
    }
    if any(m in all_deps for m in _MCP_SDK_MARKERS):
        return True
    # Also check name/description for "mcp" keyword
    name = pkg.get("name", "").lower()
    desc = pkg.get("description", "").lower()
    return "mcp" in name or "mcp" in desc


def _llm_enrich(pkg: dict, readme: str) -> tuple[str, dict, list, list]:
    """
    Call LLM to extract description (Chinese), env vars, and extra args.
    Returns (description, env, extra_args, warnings).
    Falls back gracefully if LLM is not configured.
    """
    warnings = []
    try:
        from LLM.llm_config_manager import get_llm_client_with_fallback
        client, _provider, cfg = get_llm_client_with_fallback()
    except ValueError:
        return pkg.get("description", ""), {}, [], []

    summary = {
        "name": pkg.get("name", ""),
        "description": pkg.get("description", ""),
        "scripts": pkg.get("scripts", {}),
    }
    user_content = (
        f"package.json summary:\n{json.dumps(summary, ensure_ascii=False)}\n\n"
        f"README (first 600 chars):\n{readme[:600]}"
    )
    try:
        resp = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": _SCAN_SYSTEM},
                {"role": "user",   "content": user_content},
            ],
            max_tokens=256,
            temperature=0,
        )
        raw = resp.choices[0].message.content or ""
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw.strip())
        if not raw:
            return pkg.get("description", ""), {}, [], []
        result = json.loads(raw)
        desc      = str(result.get("description") or pkg.get("description", ""))[:200]
        env_raw   = result.get("env") or {}
        extra_raw = result.get("extra_args") or []
        # Sanitize env
        env = {}
        for k, v in env_raw.items():
            if not _SHELL_META_RE.search(str(k)) and not _SHELL_META_RE.search(str(v)):
                env[str(k)[:128]] = str(v)[:256]
        # Sanitize extra_args
        extra = []
        for a in extra_raw:
            s = str(a)
            if _SHELL_META_RE.search(s):
                warnings.append(f"LLM 建议的参数 '{s[:40]}' 含危险字符，已过滤")
            else:
                extra.append(s)
        return desc, env, extra, warnings
    except Exception as e:
        log.warning("[mcp] LLM enrichment failed: %s", e)
        return pkg.get("description", ""), {}, [], [f"LLM 补全失败（已跳过）：{e}"]


@bp.post("/api/mcp/scan-local")
@require_local_runtime_config
def scan_local():
    """Scan a local MCP package directory and infer its stdio configuration."""
    data = request.get_json(force=True) or {}
    raw_path = (data.get("path") or "").strip()
    if not raw_path:
        return jsonify({"error": "path 不能为空"}), 400

    pkg_dir, err = _safe_path(raw_path)
    if err:
        return jsonify({"error": err}), 400

    # Locate package.json
    pkg_json_path = _find_package_json(pkg_dir)
    if not pkg_json_path:
        return jsonify({"error": "未找到 package.json，请确认路径指向 MCP 包目录"}), 422

    try:
        pkg = json.loads(pkg_json_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("[mcp] package.json parse failed: %s", e)
        return jsonify({"error": f"package.json 解析失败：{e}"}), 422

    actual_pkg_dir = pkg_json_path.parent

    # Confirm it's an MCP package
    if not _check_is_mcp(pkg):
        return jsonify({
            "error": "该目录不像是 MCP 服务器包（未发现 @modelcontextprotocol/sdk 或相关依赖）",
            "hint": "如果确认这是 MCP 包，请使用下方手动配置或智能填充",
        }), 422

    # Infer entry point
    entry_abs, base_extra_args, confidence = _infer_node_entry(actual_pkg_dir, pkg)
    warnings = []

    if not entry_abs:
        warnings.append("未找到入口文件（dist/ 目录可能未构建），command 已留空，请手动填写")
        confidence = 0

    # Read README for LLM enrichment
    readme = ""
    for readme_name in ("README.md", "readme.md", "README.txt", "README"):
        readme_path = actual_pkg_dir / readme_name
        if readme_path.exists():
            try:
                readme = readme_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                log.debug("[mcp] failed to read README %s: %s", readme_path, e)
            break

    # LLM enrichment (non-blocking on failure)
    description, env, llm_extra_args, llm_warnings = _llm_enrich(pkg, readme)
    warnings.extend(llm_warnings)

    # Merge extra_args: base (from package.json inference) + LLM suggestions
    all_extra_args = base_extra_args + llm_extra_args

    # Build label and server_id from package name
    name = pkg.get("name", actual_pkg_dir.name)
    bare = name.split("/")[-1]  # strip npm scope (@scope/pkg-name → pkg-name)
    label = bare.replace("-mcp", "").replace("mcp-", "").replace("-", " ").title()
    # server_id: only alphanum + underscore, max 40 chars
    server_id = re.sub(r"[^a-zA-Z0-9_]", "_", bare)[:40].strip("_")

    config = {
        "transport": "stdio",
        "label":       label,
        "server_id":   server_id,
        "description": description,
        "command":     "node" if entry_abs else "",
        "args":        ([entry_abs] + all_extra_args) if entry_abs else [],
        "env":         env,
        "url":         "",
        "headers":     {},
    }

    if confidence < 70:
        warnings.append(f"入口文件置信度较低（{confidence}%），请在命令预览中确认路径是否正确")

    return jsonify({
        "ok":         True,
        "config":     config,
        "confidence": confidence,
        "pkg_name":   name,
        "warnings":   warnings,
    })
