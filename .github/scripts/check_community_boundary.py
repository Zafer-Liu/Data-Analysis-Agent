"""Keep credentials, deployment-instance metadata and runtime data out of Git.

Hosted application functionality is public as of the v1.4 feature sync. The
boundary applies to secrets and instance data rather than product features.
"""

from __future__ import annotations

import subprocess


FORBIDDEN_EXACT = {
    ".env",
    ".env.local",
    "CLOUDFLARE.md",
    "OPEN_SOURCE_BASE",
    "RAILWAY.md",
    "RAILWAY_VARIABLES.md",
    "secret_key",
    "auth.db",
    "LLM/llm_config.json",
    "LLM/mcp_config.json",
    "LLM/embedding_config.json",
    "data/datasource_config.json",
    "config/hooks.json",
    "config/hooks-history.json",
    "config/feishu-bot.json",
}
FORBIDDEN_PREFIXES = (
    ".mainpage-tmp/",
    ".pnpm-store/",
    "landing/",
    "poster/",
    "uploads/",
    "outputs/",
)
FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".sqlite-shm", ".sqlite-wal", ".sqlite-journal", ".db-shm", ".db-wal")


def tracked_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        text=True,
        encoding="utf-8",
    )
    return [path for path in output.split("\0") if path]


def main() -> int:
    violations = [
        path
        for path in tracked_files()
        if path in FORBIDDEN_EXACT
        or path.startswith(FORBIDDEN_PREFIXES)
        or path.endswith(FORBIDDEN_SUFFIXES)
    ]
    if not violations:
        print("Community repository boundary check passed.")
        return 0

    print("Credentials, instance metadata or runtime-only files would enter the public repository:")
    for path in violations:
        print(f"  - {path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
