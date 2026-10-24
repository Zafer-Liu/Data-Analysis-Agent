#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP (Model Context Protocol) runtime manager.

Transport layer:
  BaseTransport   — abstract base
  StdioTransport  — local subprocess via asyncio.create_subprocess_exec
  SSETransport    — remote HTTP via httpx.AsyncClient

Connection layer:
  MCPServerConnection — per-server state machine + tool cache

Manager layer:
  MCPManager      — daemon thread with asyncio event loop, sync→async bridge
"""
import asyncio
import json
import logging
import os
import shutil
import sys
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

STDIO_ALLOWED_COMMANDS = frozenset({
    "uvx", "uv", "npx", "npm", "node", "python", "python3", "deno"
})


def _resolve_command(command: str) -> str:
    """
    Resolve a whitelisted command name to an absolute executable path.

    Windows note: asyncio.create_subprocess_exec uses CreateProcess, which does
    NOT walk PATH the way a shell does and won't find .cmd/.bat shims (npx.cmd,
    npm.cmd). So a bare "node" / "npx" works in a terminal but fails here. We use
    shutil.which() (which honors PATH and PATHEXT) to find the real executable,
    so users can configure just "node" instead of the full install path.

    If the command is already an absolute/relative path, it's used as-is. If it
    can't be resolved, the original string is returned and the spawn surfaces the
    original error.
    """
    # Already a path (contains a separator) — trust it, don't re-resolve.
    if os.path.sep in command or (os.altsep and os.altsep in command):
        return command

    resolved = shutil.which(command)
    if resolved:
        return resolved

    # On Windows, also try resolving against the dir holding the python launcher
    # and common Node install dirs, in case PATH isn't inherited (frozen app).
    if sys.platform == "win32":
        candidates = []
        py_dir = os.path.dirname(sys.executable)
        candidates.append(py_dir)
        candidates.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "nodejs"))
        for base in candidates:
            for ext in (".cmd", ".bat", ".exe", ""):
                cand = os.path.join(base, command + ext)
                if os.path.isfile(cand):
                    return cand

    return command


def _build_stdio_argv(command: str, args: List[str]) -> List[str]:
    """
    Build the argv for create_subprocess_exec from a resolved command + args.

    On Windows, .cmd/.bat shims (npx.cmd, npm.cmd) are batch scripts, not real
    executables — CreateProcess refuses to launch them directly ("%1 is not a
    valid Win32 application"). They must be run through the command interpreter,
    so we prepend `cmd.exe /c`. Real .exe files (node.exe, uv.exe) are launched
    directly.
    """
    if sys.platform == "win32" and command.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("ComSpec", "cmd.exe")
        return [comspec, "/c", command, *args]
    return [command, *args]


STATUS_DISCONNECTED = "disconnected"
STATUS_CONNECTING   = "connecting"
STATUS_CONNECTED    = "connected"
STATUS_ERROR        = "error"


# ── Schema helpers ─────────────────────────────────────────────────────────────

def mcp_tool_to_openai_schema(server_id: str, mcp_tool: dict) -> dict:
    raw_name = mcp_tool.get("name", "unknown")
    prefixed_name = f"mcp__{server_id}__{raw_name}"
    description = mcp_tool.get("description", "")
    if description:
        description = f"[MCP:{server_id}] {description}"
    else:
        description = f"[MCP:{server_id}] {raw_name}"

    input_schema = mcp_tool.get("inputSchema", {})
    parameters = {
        "type": input_schema.get("type", "object"),
        "properties": input_schema.get("properties", {}),
    }
    if "required" in input_schema:
        parameters["required"] = input_schema["required"]

    return {
        "type": "function",
        "function": {
            "name": prefixed_name,
            "description": description,
            "parameters": parameters,
        },
    }


def validate_mcp_args(input_schema: dict, args: dict) -> Tuple[bool, str]:
    required = input_schema.get("required", [])
    for field in required:
        if field not in args:
            return False, f"缺少必填参数: {field}"

    properties = input_schema.get("properties", {})
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, val in args.items():
        prop = properties.get(key, {})
        expected_type = prop.get("type")
        if expected_type and expected_type in type_map:
            if not isinstance(val, type_map[expected_type]):
                return False, f"参数 '{key}' 类型错误: 期望 {expected_type}"
    return True, ""


def format_mcp_error(server_id: str, tool_name: str, reason: str) -> str:
    return (
        f"[MCP ERROR] server={server_id} tool={tool_name}\n"
        f"Error: {reason}\n"
        "The MCP tool call failed. Please inform the user and continue without this tool result."
    )


# ── Transport layer ────────────────────────────────────────────────────────────

class BaseTransport(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send_request(self, method: str, params: dict) -> Any: ...

    @abstractmethod
    async def close(self) -> None: ...


class StdioTransport(BaseTransport):
    def __init__(self, command: str, args: List[str], env: Dict[str, str]):
        # Validate against the basename (without extension) so both a bare
        # "node" and a full "C:\Program Files\nodejs\node.exe" pass the whitelist.
        basename = os.path.splitext(os.path.basename(command))[0]
        if basename not in STDIO_ALLOWED_COMMANDS:
            raise ValueError(f"命令 '{command}' 不在白名单中")
        # Resolve "node"/"npx"/... to an absolute executable path. On Windows
        # create_subprocess_exec won't find bare command names or .cmd shims.
        self._command = _resolve_command(command)
        self._args = args
        self._env = {**os.environ.copy(), **env}
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._req_id = 0
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        argv = _build_stdio_argv(self._command, self._args)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
        except (FileNotFoundError, NotADirectoryError) as e:
            raise ConnectionError(
                f"无法启动命令 '{self._command}': {e}. "
                f"请确认已安装对应运行时（如 Node.js / Python），"
                f"或在配置中填写可执行文件的完整路径。"
            )
        await self._initialize()

    async def _initialize(self) -> None:
        init_resp = await self.send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "business-analytics-agent", "version": "1.0"},
        }, timeout=120)
        log.debug("[stdio] initialize response: %s", init_resp)
        # send notifications/initialized (no response expected)
        notif = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }) + "\n"
        self._proc.stdin.write(notif.encode())
        await self._proc.stdin.drain()

    async def send_request(self, method: str, params: dict, *, timeout: float = 30) -> Any:
        async with self._lock:
            self._req_id += 1
            req = json.dumps({
                "jsonrpc": "2.0",
                "id": self._req_id,
                "method": method,
                "params": params,
            }) + "\n"
            self._proc.stdin.write(req.encode())
            await self._proc.stdin.drain()

            while True:
                line = await asyncio.wait_for(
                    self._proc.stdout.readline(), timeout=timeout
                )
                if not line:
                    raise ConnectionError("MCP stdio process closed stdout")
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                # skip notifications
                if "id" not in data:
                    continue
                if data.get("id") != self._req_id:
                    continue
                if "error" in data:
                    raise RuntimeError(data["error"].get("message", "MCP error"))
                return data.get("result")

    async def close(self) -> None:
        if self._proc:
            try:
                self._proc.stdin.close()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                self._proc.kill()
            self._proc = None


class SSETransport(BaseTransport):
    def __init__(self, url: str, headers: Dict[str, str]):
        self._base_url = url.rstrip("/")
        self._headers = headers
        self._client: Optional[Any] = None
        self._endpoint: Optional[str] = None
        self._req_id = 0
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        try:
            import httpx
        except ImportError:
            raise RuntimeError("httpx 未安装，SSE transport 需要: pip install httpx")

        self._client = httpx.AsyncClient(headers=self._headers, timeout=30)
        # Discover the POST endpoint from the SSE stream's first event
        endpoint = None
        try:
            async with self._client.stream("GET", self._base_url) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        try:
                            obj = json.loads(payload)
                            if isinstance(obj, dict) and "endpoint" in obj:
                                endpoint = obj["endpoint"]
                                break
                        except json.JSONDecodeError:
                            # some servers send plain URL string
                            if payload.startswith("http"):
                                endpoint = payload
                                break
                    if endpoint:
                        break
        except Exception as e:
            raise ConnectionError(f"SSE 连接失败: {e}")

        if not endpoint:
            # Fall back to base URL as POST endpoint
            endpoint = self._base_url
        self._endpoint = endpoint

        await self._initialize()

    async def _initialize(self) -> None:
        resp = await self.send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "business-analytics-agent", "version": "1.0"},
        })
        log.debug("[sse] initialize response: %s", resp)
        # send initialized notification
        await self._post_notification("notifications/initialized", {})

    async def _post_notification(self, method: str, params: dict) -> None:
        notif = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._client.post(self._endpoint, json=notif)

    async def send_request(self, method: str, params: dict) -> Any:
        import httpx
        async with self._lock:
            self._req_id += 1
            payload = {
                "jsonrpc": "2.0",
                "id": self._req_id,
                "method": method,
                "params": params,
            }
            try:
                resp = await self._client.post(self._endpoint, json=payload, timeout=60)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                raise RuntimeError(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
            if "error" in data:
                raise RuntimeError(data["error"].get("message", "MCP SSE error"))
            return data.get("result")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# ── Connection layer ───────────────────────────────────────────────────────────

class MCPServerConnection:
    # Max consecutive reconnect attempts before giving up until next explicit connect
    _MAX_RECONNECT_ATTEMPTS = 3
    _RECONNECT_BASE_WAIT = 1.0  # seconds, doubled each attempt

    def __init__(self, config):
        self._config = config
        self.status: str = STATUS_DISCONNECTED
        self.last_error: str = ""
        self._transport: Optional[BaseTransport] = None
        self._tools_cache: List[dict] = []
        self._openai_schemas: List[dict] = []
        self._reconnect_attempts: int = 0
        self._last_reconnect_at: float = 0.0

    @property
    def server_id(self) -> str:
        return self._config.server_id

    async def connect(self) -> bool:
        if self.status == STATUS_CONNECTED:
            return True
        self.status = STATUS_CONNECTING
        self.last_error = ""
        try:
            transport = self._build_transport()
            await transport.connect()
            self._transport = transport
            tools_result = await transport.send_request("tools/list", {})
            raw_tools = tools_result.get("tools", []) if isinstance(tools_result, dict) else []
            self._tools_cache = raw_tools
            self._openai_schemas = [
                mcp_tool_to_openai_schema(self.server_id, t) for t in raw_tools
            ]
            self.status = STATUS_CONNECTED
            self._reconnect_attempts = 0  # reset on successful connect
            log.info("[mcp] %s connected, %d tools", self.server_id, len(raw_tools))
            return True
        except Exception as e:
            self.status = STATUS_ERROR
            self.last_error = str(e)
            log.error("[mcp] %s connect failed: %s", self.server_id, e)
            if self._transport:
                try:
                    await self._transport.close()
                except Exception:
                    pass
                self._transport = None
            return False

    async def _reconnect(self) -> bool:
        """Attempt a single reconnect with exponential backoff tracking."""
        if self._reconnect_attempts >= self._MAX_RECONNECT_ATTEMPTS:
            log.warning(
                "[mcp] %s max reconnect attempts (%d) reached, giving up",
                self.server_id, self._MAX_RECONNECT_ATTEMPTS,
            )
            return False

        wait = self._RECONNECT_BASE_WAIT * (2 ** self._reconnect_attempts)
        self._reconnect_attempts += 1
        self._last_reconnect_at = time.monotonic()
        log.info(
            "[mcp] %s reconnecting (attempt %d/%d) after %.1fs...",
            self.server_id, self._reconnect_attempts,
            self._MAX_RECONNECT_ATTEMPTS, wait,
        )
        await asyncio.sleep(wait)

        # Tear down stale transport before reconnecting
        if self._transport:
            try:
                await self._transport.close()
            except Exception:
                pass
            self._transport = None
        self.status = STATUS_DISCONNECTED

        return await self.connect()

    def _build_transport(self) -> BaseTransport:
        cfg = self._config
        if cfg.transport == "stdio":
            return StdioTransport(cfg.command, cfg.args, cfg.env)
        elif cfg.transport == "sse":
            return SSETransport(cfg.url, cfg.headers)
        else:
            raise ValueError(f"未知 transport: {cfg.transport}")

    async def call_tool(self, tool_name: str, args: dict) -> str:
        # Auto-reconnect if not connected
        if self.status != STATUS_CONNECTED or not self._transport:
            log.info("[mcp] %s not connected (status=%s), attempting reconnect before call",
                     self.server_id, self.status)
            reconnected = await self._reconnect()
            if not reconnected:
                return format_mcp_error(self.server_id, tool_name,
                                        f"服务器未连接且重连失败: {self.last_error}")

        # find original tool schema for validation
        raw_schema = next(
            (t for t in self._tools_cache if t.get("name") == tool_name), {}
        )
        input_schema = raw_schema.get("inputSchema", {})
        valid, err_msg = validate_mcp_args(input_schema, args)
        if not valid:
            return format_mcp_error(self.server_id, tool_name, f"参数校验失败: {err_msg}")

        try:
            result = await self._transport.send_request("tools/call", {
                "name": tool_name,
                "arguments": args,
            })
            # MCP tools/call returns {content: [{type: "text", text: "..."}], isError: bool}
            if isinstance(result, dict):
                if result.get("isError"):
                    content = result.get("content", [])
                    err_text = " ".join(
                        c.get("text", "") for c in content if c.get("type") == "text"
                    )
                    return format_mcp_error(self.server_id, tool_name, err_text or "工具返回错误")
                content = result.get("content", [])
                parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                return "\n".join(parts) if parts else str(result)
            return str(result)
        except (ConnectionError, RuntimeError, OSError) as e:
            # Transport-level failure — mark disconnected and try once more
            log.warning("[mcp] %s transport error during call: %s — attempting reconnect",
                        self.server_id, e)
            self.status = STATUS_ERROR
            self.last_error = str(e)
            if self._transport:
                try:
                    await self._transport.close()
                except Exception:
                    pass
                self._transport = None

            reconnected = await self._reconnect()
            if not reconnected:
                return format_mcp_error(self.server_id, tool_name,
                                        f"调用时连接断开且重连失败: {self.last_error}")
            # Single retry after successful reconnect
            try:
                result = await self._transport.send_request("tools/call", {
                    "name": tool_name,
                    "arguments": args,
                })
                if isinstance(result, dict):
                    if result.get("isError"):
                        content = result.get("content", [])
                        err_text = " ".join(
                            c.get("text", "") for c in content if c.get("type") == "text"
                        )
                        return format_mcp_error(self.server_id, tool_name, err_text or "工具返回错误")
                    content = result.get("content", [])
                    parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                    return "\n".join(parts) if parts else str(result)
                return str(result)
            except Exception as retry_exc:
                return format_mcp_error(self.server_id, tool_name,
                                        f"重连后重试仍失败: {retry_exc}")
        except Exception as e:
            return format_mcp_error(self.server_id, tool_name, str(e))

    async def disconnect(self) -> None:
        if self._transport:
            try:
                await self._transport.close()
            except Exception:
                pass
            self._transport = None
        self.status = STATUS_DISCONNECTED
        self._tools_cache = []
        self._openai_schemas = []

    def get_openai_schemas(self) -> List[dict]:
        return self._openai_schemas if self.status == STATUS_CONNECTED else []

    def to_status_dict(self) -> dict:
        return {
            "server_id": self.server_id,
            "status": self.status,
            "last_error": self.last_error,
            "tool_count": len(self._tools_cache),
            "tools": [t.get("name") for t in self._tools_cache],
        }


# ── Manager layer ──────────────────────────────────────────────────────────────

class MCPManager:
    def __init__(self):
        self._connections: Dict[str, MCPServerConnection] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the async event loop daemon thread. Does NOT connect any servers."""
        if self._thread and self._thread.is_alive():
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="mcp-event-loop"
        )
        self._thread.start()
        log.info("[MCPManager] event loop thread started")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro, timeout: int = 60) -> Any:
        if not self._loop or not self._loop.is_running():
            raise RuntimeError("MCPManager event loop not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def load_from_config(self, mcp_config_manager) -> None:
        """Register all enabled servers from MCPConfigManager (no connections yet)."""
        for cfg in mcp_config_manager.get_enabled_servers():
            with self._lock:
                if cfg.server_id not in self._connections:
                    self._connections[cfg.server_id] = MCPServerConnection(cfg)

    def add_server(self, config) -> None:
        with self._lock:
            self._connections[config.server_id] = MCPServerConnection(config)

    def remove_server(self, server_id: str) -> None:
        with self._lock:
            conn = self._connections.pop(server_id, None)
        if conn and conn.status == STATUS_CONNECTED:
            try:
                self._submit(conn.disconnect(), timeout=10)
            except Exception:
               pass

    def connect_server(self, server_id: str) -> dict:
        """Lazily connect a single server. Exception-safe, returns status dict."""
        with self._lock:
            conn = self._connections.get(server_id)
        if not conn:
            return {"server_id": server_id, "status": STATUS_ERROR,
                    "last_error": "服务器未注册", "tool_count": 0}
        # Explicit connect resets the reconnect counter so auto-reconnect can retry
        conn._reconnect_attempts = 0
        try:
            self._submit(conn.connect(), timeout=180)
        except Exception as e:
            conn.status = STATUS_ERROR
            conn.last_error = str(e)
            log.error("[MCPManager] connect_server %s failed: %s", server_id, e)
        return conn.to_status_dict()
    def get_all_openai_schemas(self) -> List[dict]:
        with self._lock:
            conns = list(self._connections.values())
        schemas = []
        for conn in conns:
            schemas.extend(conn.get_openai_schemas())
        return schemas

    def get_server_status(self, server_id: str) -> Optional[dict]:
        with self._lock:
            conn = self._connections.get(server_id)
        return conn.to_status_dict() if conn else None

    def get_all_status(self) -> List[dict]:
        with self._lock:
            conns = list(self._connections.values())
        return [c.to_status_dict() for c in conns]

    def get_server_tools(self, server_id: str) -> List[dict]:
        with self._lock:
            conn = self._connections.get(server_id)
        if not conn or conn.status != STATUS_CONNECTED:
            return []
        return conn._tools_cache

    def call_tool(self, tool_name: str, args: dict) -> str:
        """
        tool_name format: mcp__<server_id>__<original_tool_name>
        Fully exception-safe, always returns str.
        """
        try:
            parts = tool_name.split("__", 2)
            if len(parts) != 3 or parts[0] != "mcp":
                return format_mcp_error("unknown", tool_name, f"无效的 MCP 工具名格式: {tool_name}")
            _, server_id, original_name = parts

            with self._lock:
                conn = self._connections.get(server_id)
            if not conn:
                return format_mcp_error(server_id, original_name, "服务器未注册")

            return self._submit(conn.call_tool(original_name, args), timeout=60)
        except Exception as e:
            log.error("[MCPManager] call_tool %s failed: %s", tool_name, e)
            return format_mcp_error("unknown", tool_name, str(e))


_mcp_manager: Optional[MCPManager] = None


def get_mcp_manager() -> MCPManager:
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager()
        _mcp_manager.start()
        # lazy load registered servers from config (no connections)
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from LLM.mcp_config_manager import get_mcp_config_manager
            _mcp_manager.load_from_config(get_mcp_config_manager())
        except Exception as e:
            log.warning("[MCPManager] 加载配置失败: %s", e)
    return _mcp_manager
