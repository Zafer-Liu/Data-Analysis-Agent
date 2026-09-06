"""Fail CI when hosted-edition or runtime-only files are tracked publicly."""

from __future__ import annotations

import subprocess


FORBIDDEN_EXACT = {
    ".env",
    ".env.local",
    "CLOUDFLARE.md",
    "OPEN_SOURCE_BASE",
    "RAILWAY.md",
    "secret_key",
}
FORBIDDEN_PREFIXES = (
    ".mainpage-tmp/",
    ".pnpm-store/",
    "enterprise/",
    "landing/",
    "poster/",
)
FORBIDDEN_SUFFIXES = (".sqlite-shm", ".sqlite-wal")


def tracked_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"],
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

    print("Hosted-edition or runtime-only files are tracked publicly:")
    for path in violations:
        print(f"  - {path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
