"""Protected built-in slash-command catalog.

Business analysis workflows live under ``skills/``.  This catalog contains
only MewCode-style application control commands.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .models import CommandDef, CommandType
from .parser import CommandError, parse_command_file
from infrastructure.paths import resource_path

PROJECT_COMMANDS_DIR = resource_path("commands")

_FALLBACK = (
    ("help", "查看智析 Agent 可用命令及使用方法", "❓", "tools"),
    ("clear", "清除当前对话内容，保留数据源、模型和工作目录连接", "🧹", "session"),
    ("compact", "立即压缩当前对话上下文，保留关键结论和最近内容", "🗜️", "session"),
    ("instruction", "设置仅对当前分析对话生效的临时指令", "📝", "session"),
    ("sessions", "刷新已保存对话，或新建分析对话", "💬", "session"),
    ("mcp", "打开 MCP 连接与工具管理", "🔌", "tools"),
    ("knowledge", "打开业务知识库，管理指标口径、规则和参考资料", "🧠", "tools"),
    ("workspace", "连接工作目录并设置只读或可编辑权限", "📁", "tools"),
    ("checkpoint", "查看对话快照并回退文件、对话或两者", "⏪", "tools"),
    ("status", "查看当前模型、数据源和 Token 上下文状态", "📡", "tools"),
    ("skills", "查看、选择或刷新数据分析 Skill", "🧩", "tools"),
    ("new", "新建一个干净的分析会话", "✨", "session"),
    ("stop", "停止当前正在生成的回复", "⏹️", "session"),
    ("data", "打开当前数据源和数据表预览", "🗂️", "tools"),
    ("jobs", "打开任务历史和运行状态", "🕘", "tools"),
    ("teams", "打开分析团队和沟通记录", "👥", "tools"),
)

_FALLBACK_ACTIONS = {
    "instruction": "plan",
    "sessions": "session", "knowledge": "memory", "workspace": "permission",
    "checkpoint": "rewind", "skills": "skill",
}
_FALLBACK_ALIASES = {
    "help": ("h", "?"), "status": ("s",), "compact": ("c",),
    "instruction": ("i",), "sessions": ("session",), "knowledge": ("kb",),
    "workspace": ("ws",), "checkpoint": ("cp",), "skills": ("sk",),
    "new": ("n",),
}
_FALLBACK_LOCAL = frozenset({"help", "status"})
_FALLBACK_OPTIONAL_ARGS = frozenset({
    "help", "compact", "instruction", "sessions", "skills",
})


@lru_cache(maxsize=1)
def builtin_commands() -> tuple[CommandDef, ...]:
    if PROJECT_COMMANDS_DIR.is_dir():
        loaded: list[CommandDef] = []
        for path in sorted(PROJECT_COMMANDS_DIR.rglob("*.md")):
            try:
                command = parse_command_file(
                    PROJECT_COMMANDS_DIR, path,
                    source="builtin", allow_trusted_types=True,
                )
            except CommandError:
                continue
            loaded.append(command)
        if loaded:
            return tuple(loaded)

    # Packaging fallback for installations missing the command content folder.
    commands: list[CommandDef] = []
    for name, description, icon, category in _FALLBACK:
        if name == "compact":
            command_type = CommandType.BACKEND
            handler_key = "server:compact"
        elif name in _FALLBACK_LOCAL:
            command_type = CommandType.LOCAL
            handler_key = f"client:{_FALLBACK_ACTIONS.get(name, name)}"
        else:
            command_type = CommandType.LOCAL_UI
            handler_key = f"client:{_FALLBACK_ACTIONS.get(name, name)}"
        commands.append(CommandDef(
            name=name, description=description, type=command_type,
            icon=icon, category=category,
            aliases=_FALLBACK_ALIASES.get(name, ()),
            arguments=("optional" if name in _FALLBACK_OPTIONAL_ARGS else "none"),
            handler_key=handler_key,
            protected=True,
            uses_model=(name == "compact"),
        ))
    return tuple(commands)
