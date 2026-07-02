"""Fail-closed packaging policy shared by staging and artifact audits."""

from __future__ import annotations

from pathlib import PurePosixPath


ALLOWED_ROOT_FILES = frozenset({"app.py", "LICENSE"})
ALLOWED_ROOT_DIRS = (
    "agent",
    "api",
    "commands",
    "data",
    "filehistory",
    "Function",
    "infrastructure",
    "Information",
    "LLM",
    "packaging",
    "skills",
    "static",
    "templates",
)

# These files are expected in a developer checkout and are silently omitted.
# Any other forbidden item found under an allowed root is a policy violation.
KNOWN_LOCAL_ONLY = frozenset({
    "llm/llm_config.json",
    "llm/mcp_config.json",
    "data/datasource_config.json",
})

IGNORED_CACHE_PARTS = frozenset({
    "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache",
})
FORBIDDEN_PARTS = frozenset({
    "mcp", "node_modules", "uploads", "outputs", ".uploads", ".zhixi",
    ".git", ".github", ".agents", ".claude", ".idea", ".vscode",
    ".venv", "venv", "build", "dist", "releases",
})
FORBIDDEN_NAMES = frozenset({
    ".env", ".env.local", ".deps_installed", ".ds_store", "thumbs.db",
    "workspace.json", "registry.json",
})
FORBIDDEN_SUFFIXES = frozenset({
    ".log", ".db", ".duckdb", ".sqlite", ".sqlite3",
    ".pem", ".key", ".p12", ".pfx", ".cer", ".crt", ".mobileprovision",
    ".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".docx", ".pdf",
})

# Reviewed public package assets required at runtime. These are not user
# documents or credentials; scope them to PyInstaller's dependency layout so
# similarly named files in the application staging tree remain forbidden.
PUBLIC_RUNTIME_ASSETS = (
    ("certifi", "cacert.pem"),
    ("docx", "templates", "default.docx"),
)


def normalized_relative(value: str) -> str:
    """Return a stable lower-case POSIX relative path or raise on traversal."""
    raw = str(value or "").replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise ValueError(f"invalid relative packaging path: {value!r}")
    return path.as_posix().lower()


def _is_public_frontend_dist(parts: tuple[str, ...]) -> bool:
    """Return True for the reviewed Vite runtime bundle under static/dist."""
    return any(
        parts[index] == "static" and parts[index + 1] == "dist"
        for index in range(max(0, len(parts) - 1))
    )


def classify_path(value: str) -> tuple[str, str]:
    """Return (allow|exclude|deny, reason) for a relative package path."""
    relative = normalized_relative(value)
    path = PurePosixPath(relative)
    parts = tuple(part.lower() for part in path.parts)
    public_frontend_dist = _is_public_frontend_dist(parts)
    if relative in KNOWN_LOCAL_ONLY:
        return "exclude", "local configuration"
    if any(part in IGNORED_CACHE_PARTS for part in parts):
        return "exclude", "generated cache"
    if any(
        part in FORBIDDEN_PARTS
        and not (part == "dist" and public_frontend_dist)
        for part in parts
    ):
        return "deny", "forbidden directory"
    name = path.name.lower()
    if name in FORBIDDEN_NAMES or name.startswith(".env.") or name.endswith(".local"):
        return "deny", "local state or secret filename"
    runtime_layout = bool(parts and parts[0] in {"_internal", "contents"}) or (
        len(parts) > 1 and parts[0].endswith(".app") and parts[1] == "contents"
    )
    public_runtime_asset = runtime_layout and any(
        len(parts) >= len(suffix) and parts[-len(suffix):] == suffix
        for suffix in PUBLIC_RUNTIME_ASSETS
    )
    if path.suffix.lower() in FORBIDDEN_SUFFIXES and not public_runtime_asset:
        return "deny", "runtime data, database, document, or credential extension"
    if name.endswith((".pyc", ".pyo")):
        return "exclude", "generated Python bytecode"
    return "allow", ""
