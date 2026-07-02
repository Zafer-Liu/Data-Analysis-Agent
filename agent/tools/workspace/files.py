# -*- coding: utf-8 -*-
"""Workspace-scoped versions of MewCode's file and command tools."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
log = logging.getLogger(__name__)
import os
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from xml.etree import ElementTree
from pathlib import Path
from typing import Any

from data.workspace import workspace_manager
from data.system_workspace import MAX_INDEXED_FILES, MAX_LIST_CHARS, MAX_LIST_LIMIT, MAX_SEARCH_LIMIT

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_READ_BYTES = MAX_FILE_BYTES
MAX_SPREADSHEET_READ_BYTES = 256 * 1024 * 1024
MAX_READ_LINES = 400
MAX_READ_CHARS = 12_000
MAX_WRITE_BYTES = MAX_FILE_BYTES
MAX_DOCX_XML_BYTES = 64 * 1024 * 1024
MAX_RESULTS = 100
MAX_SEARCH_FILES = 200
MAX_SEARCH_FILE_CHARS = 200_000
MAX_COMMAND_OUTPUT = 20_000
TEXT_SUFFIXES = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".sql", ".py", ".js", ".css", ".html", ".xml", ".toml", ".ini",
}
SKIP_DIRS = {".git", ".zhixi", ".baa_cache", "node_modules", "__pycache__", ".venv"}
SPREADSHEET_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"}


class WorkspaceToolError(ValueError):
    pass


def _read_docx_text(path: Path) -> str:
    """Extract ordered paragraph/table-cell text from a bounded DOCX file."""
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                info = archive.getinfo("word/document.xml")
            except KeyError as exc:
                raise WorkspaceToolError("DOCX does not contain word/document.xml") from exc
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise WorkspaceToolError("DOCX expanded document XML exceeds 64 MiB safety limit")
            xml_data = archive.read(info)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise WorkspaceToolError(f"DOCX cannot be opened: {exc}") from exc

    try:
        root = ElementTree.fromstring(xml_data)
    except ElementTree.ParseError as exc:
        raise WorkspaceToolError(f"DOCX document XML is invalid: {exc}") from exc

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{namespace}tab":
                parts.append("\t")
            elif node.tag in {f"{namespace}br", f"{namespace}cr"}:
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            lines.extend(text.splitlines())
    return "\n".join(lines)


def _read_spreadsheet_preview(
    path: Path,
    *,
    offset: int,
    limit: int,
    sheet_name: str = "",
) -> dict:
    """Read a bounded worksheet window without treating Excel as UTF-8 text."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise WorkspaceToolError("spreadsheet preview requires pandas") from exc

    workbook = None
    errors: list[str] = []
    for engine in ("calamine", "openpyxl", None):
        try:
            kwargs = {"engine": engine} if engine else {}
            workbook = pd.ExcelFile(path, **kwargs)
            break
        except Exception as exc:
            errors.append(f"{engine or 'default'}: {exc}")
    if workbook is None:
        raise WorkspaceToolError(
            "spreadsheet cannot be opened: " + "; ".join(errors[-2:])
        )

    sheets = [str(name) for name in workbook.sheet_names]
    if not sheets:
        raise WorkspaceToolError("spreadsheet contains no worksheets")
    selected_sheet = str(sheet_name or "").strip() or sheets[0]
    if selected_sheet not in sheets:
        raise WorkspaceToolError(
            f"worksheet not found: {selected_sheet}; available: {', '.join(sheets[:20])}"
        )

    try:
        frame = workbook.parse(
            sheet_name=selected_sheet,
            header=None,
            skiprows=offset,
            nrows=limit,
        )
    except Exception as exc:
        raise WorkspaceToolError(
            f"worksheet cannot be read: {selected_sheet}: {exc}"
        ) from exc
    finally:
        try:
            workbook.close()
        except Exception:
            pass

    rendered_lines: list[str] = []
    rendered_chars = 0
    char_truncated = False
    for row_number, row in enumerate(frame.itertuples(index=False, name=None), offset + 1):
        cells: list[str] = []
        for value in row:
            try:
                empty = bool(pd.isna(value))
            except (TypeError, ValueError):
                empty = False
            text = "" if value is None or empty else str(value)
            cells.append(text.replace("\r", " ").replace("\n", " ")[:500])
        rendered = f"{row_number}: " + "\t".join(cells).rstrip()
        if rendered_lines and rendered_chars + len(rendered) + 1 > MAX_READ_CHARS:
            char_truncated = True
            break
        rendered_lines.append(rendered[:MAX_READ_CHARS])
        rendered_chars += len(rendered_lines[-1]) + 1

    consumed = len(rendered_lines)
    may_have_more = len(frame.index) >= limit
    return {
        "content_type": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if path.suffix.lower() != ".xls"
            else "application/vnd.ms-excel"
        ),
        "sheet_name": selected_sheet,
        "sheets": sheets[:50],
        "offset": offset,
        "total_lines": None,
        "content": "\n".join(rendered_lines),
        "next_offset": offset + consumed if may_have_more and consumed else None,
        "truncated": char_truncated or may_have_more,
        "character_limit_reached": char_truncated,
    }


class WorkspaceFileState:
    """Read-before-write state with optimistic mtime checking per session."""

    def __init__(self) -> None:
        self._entries: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(self, path: Path) -> None:
        with self._lock:
            self._entries[str(path)] = path.stat().st_mtime_ns

    def require_current(self, path: Path) -> None:
        with self._lock:
            prior = self._entries.get(str(path))
        if prior is None:
            raise WorkspaceToolError("existing file must be read before it can be changed")
        try:
            current = path.stat().st_mtime_ns
        except OSError as exc:
            log.debug("[files] stat failed for mtime check: %s", exc)
            raise WorkspaceToolError(f"cannot stat file: {exc}") from exc
        if current != prior:
            raise WorkspaceToolError("file changed after it was read; read it again before editing")

    def forget(self, path: Path) -> None:
        with self._lock:
            self._entries.pop(str(path), None)

    def move(self, source: Path, destination: Path) -> None:
        with self._lock:
            self._entries.pop(str(source), None)
            self._entries[str(destination)] = destination.stat().st_mtime_ns


_STATE_BY_AUTH_SCOPE: dict[tuple[str, str], WorkspaceFileState] = {}
_STATE_LOCK = threading.Lock()


def _state_for(workspace_id: str, session_id: str) -> WorkspaceFileState:
    # Include both identities: switching a session from A to B immediately
    # starts a fresh read-before-write cache, while two sessions sharing one
    # Workspace cannot authorize edits from each other's prior reads.
    key = (workspace_id or "system", session_id)
    with _STATE_LOCK:
        return _STATE_BY_AUTH_SCOPE.setdefault(key, WorkspaceFileState())


class WorkspaceToolService:
    def __init__(self, session_id: str, *, workspace_id: str | None = None) -> None:
        self.session_id = session_id
        self.workspace_id = (
            str(workspace_manager.workspace_id_for_session(session_id) or "")
            if workspace_id is None else str(workspace_id or "")
        )

    def _file_state(self) -> WorkspaceFileState:
        return _state_for(self.workspace_id, self.session_id)

    def _runtime(self):
        runtime = workspace_manager.get_by_workspace(self.workspace_id) if self.workspace_id else None
        if runtime is None:
            raise WorkspaceToolError("no workspace is mounted for this session")
        return runtime

    def _path(self, value: str, *, write: bool = False) -> Path:
        return self._location(value, write=write)[0]

    def _location(self, value: str, *, write: bool = False) -> tuple[Path, str, Path]:
        system = workspace_manager.system_workspace
        virtual = system.parse_virtual_path(value)
        if virtual is not None:
            root_name, relative = virtual
            try:
                path = system.resolve(root_name, relative, write=write)
            except ValueError as exc:
                log.debug("[files] virtual path resolve failed: %s", exc)
                raise WorkspaceToolError(str(exc)) from exc
            return path, root_name, system.policy(root_name).path.resolve()
        try:
            runtime = self._runtime()
            normalized = str(value or "").strip().replace("\\", "/")
            lowered = normalized.lower()
            if lowered == "workspace://user" or lowered == "user":
                normalized = "."
            elif lowered.startswith("workspace://user/"):
                normalized = normalized[len("workspace://user/"):]
            elif lowered.startswith("user/"):
                # ``_display_path`` returns user/<relative>; accept that value
                # directly so list/search results round-trip into other tools.
                normalized = normalized[len("user/"):]
            return runtime.resolve_tool_path(normalized, write=write), "user", runtime.workdir
        except (ValueError, PermissionError) as exc:
            log.debug("[files] runtime path resolve failed: %s", exc)
            raise WorkspaceToolError(str(exc)) from exc

    def _display_path(self, path: Path) -> str:
        system = workspace_manager.system_workspace
        for name, policy in system.roots.items():
            try:
                return system.virtual_name(name, path)
            except ValueError:
                log.debug("[files] display path root mismatch: %s", name)
                continue
        runtime = workspace_manager.get_by_workspace(self.workspace_id) if self.workspace_id else None
        if runtime is not None:
            try:
                return f"user/{path.resolve().relative_to(runtime.workdir.resolve()).as_posix()}"
            except ValueError:
                log.debug("[files] display path relative_to failed")
                pass
        raise WorkspaceToolError("path is outside the workspace")

    def _track_before_mutation(self, *paths: Path) -> None:
        """Record pre-mutation user-workspace versions for the active turn."""
        from filehistory import FileHistoryError, for_session
        history = for_session(self.session_id, self.workspace_id)
        if history is None:
            return
        try:
            for path in paths:
                history.track_before_write(path)
        except FileHistoryError as exc:
            raise WorkspaceToolError(str(exc)) from exc

    @staticmethod
    def _skip(path: Path) -> bool:
        return any(part.lower() in SKIP_DIRS for part in path.parts)

    def glob(
        self, pattern: str, path: str = "", max_results: int = 20, cursor: int = 0,
    ) -> dict:
        # Omitted path means the mounted user workspace when available. System
        # roots remain explicitly addressable as uploads, outputs, or mcp.
        if not str(path or "").strip():
            path = "." if self.workspace_id else "uploads"
        system = workspace_manager.system_workspace
        virtual = system.parse_virtual_path(path)
        if virtual is not None:
            root_name, relative = virtual
            return system.list_files(
                root_name, relative, pattern,
                limit=min(int(max_results), MAX_LIST_LIMIT), cursor=cursor,
            )
        base, _alias, root = self._location(path)
        if not base.is_dir():
            raise WorkspaceToolError("search path is not a directory")
        max_results = max(1, min(int(max_results), MAX_LIST_LIMIT))
        cursor = max(0, int(cursor))
        results = []
        scanned = 0
        for item in base.glob(pattern or "**/*"):
            scanned += 1
            if scanned > MAX_INDEXED_FILES:
                break
            if not item.is_file() or self._skip(item):
                continue
            try:
                safe = item.resolve()
                safe.relative_to(root.resolve())
                stat = safe.stat()
            except (ValueError, OSError):
                log.debug("[files] glob entry skipped: %s", item)
                continue
            results.append({
                "path": self._display_path(safe),
                "size": stat.st_size,
                "modified_ns": stat.st_mtime_ns,
            })
        results.sort(key=lambda item: item["modified_ns"], reverse=True)
        page = []
        rendered_chars = 0
        for row in results[cursor:cursor + max_results]:
            row_chars = len(row["path"]) + 80
            if page and rendered_chars + row_chars > MAX_LIST_CHARS:
                break
            page.append(row)
            rendered_chars += row_chars
        next_cursor = cursor + len(page) if cursor + len(page) < len(results) else None
        return {
            "matches": page, "count": len(page), "total": len(results),
            "cursor": cursor, "next_cursor": next_cursor,
            "truncated": next_cursor is not None,
        }

    def grep(self, pattern: str, path: str = ".", include: str = "*", max_results: int = 20) -> dict:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            log.debug("[files] invalid regex pattern: %s", exc)
            raise WorkspaceToolError(f"invalid regex: {exc}") from exc
        base, alias, root = self._location(path)
        if not base.is_dir():
            raise WorkspaceToolError("search path is not a directory")
        max_results = max(1, min(int(max_results), MAX_SEARCH_LIMIT))
        results = []
        system = workspace_manager.system_workspace
        virtual = system.parse_virtual_path(path)
        if virtual is not None:
            root_name, _relative = virtual
            candidates = (
                system.resolve(root_name, rel)
                for rel in system.entries(root_name)
            )
        else:
            candidates = base.rglob("*")
        searched_files = 0
        output_chars = 0
        for item in candidates:
            if len(results) >= max_results:
                break
            if not item.is_file() or self._skip(item) or not fnmatch.fnmatch(item.name, include or "*"):
                continue
            if item.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                item.resolve().relative_to(base.resolve())
            except ValueError:
                log.debug("[files] grep candidate outside base: %s", item)
                continue
            searched_files += 1
            if searched_files > MAX_SEARCH_FILES:
                break
            try:
                safe = item.resolve()
                safe.relative_to(root.resolve())
                if safe.stat().st_size > MAX_READ_BYTES:
                    continue
                text = safe.read_text(encoding="utf-8", errors="replace")[:MAX_SEARCH_FILE_CHARS]
                lines = text.splitlines()
            except (ValueError, OSError):
                log.debug("[files] grep file read skipped: %s", item)
                continue
            for number, line in enumerate(lines, 1):
                if regex.search(line):
                    snippet = line[:500]
                    display_path = self._display_path(safe)
                    row_chars = len(display_path) + len(snippet) + 40
                    if results and output_chars + row_chars > MAX_READ_CHARS:
                        break
                    results.append({
                        "path": display_path,
                        "line": number,
                        "text": snippet,
                    })
                    output_chars += row_chars
                    if len(results) >= max_results:
                        break
        return {
            "matches": results,
            "count": len(results),
            "searched_files": min(searched_files, MAX_SEARCH_FILES),
            "truncated": len(results) >= max_results or searched_files > MAX_SEARCH_FILES,
        }

    def read_file(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 200,
        sheet_name: str = "",
    ) -> dict:
        path = self._path(file_path)
        if not path.is_file():
            raise WorkspaceToolError("file not found")
        size = path.stat().st_size
        suffix = path.suffix.lower()
        size_limit = (
            MAX_SPREADSHEET_READ_BYTES
            if suffix in SPREADSHEET_SUFFIXES else MAX_READ_BYTES
        )
        if size > size_limit:
            raise WorkspaceToolError(f"file exceeds {size_limit} byte read limit")
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), MAX_READ_LINES))
        if suffix in SPREADSHEET_SUFFIXES:
            result = _read_spreadsheet_preview(
                path,
                offset=offset,
                limit=limit,
                sheet_name=sheet_name,
            )
            self._file_state().record(path)
            return {"path": self._display_path(path), **result}
        if suffix == ".docx":
            text = _read_docx_text(path)
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                log.debug("[files] file not UTF-8: %s", file_path)
                raise WorkspaceToolError("file is not UTF-8 text or a supported DOCX document") from exc
            content_type = "text/plain; charset=utf-8"
        lines = text.splitlines()
        self._file_state().record(path)
        selected = []
        chars = 0
        char_truncated = False
        for number, line in enumerate(lines[offset:offset + limit], offset + 1):
            rendered = f"{number}: {line}"
            if selected and chars + len(rendered) + 1 > MAX_READ_CHARS:
                char_truncated = True
                break
            clipped = rendered[:MAX_READ_CHARS]
            char_truncated = char_truncated or len(clipped) < len(rendered)
            selected.append(clipped)
            chars += len(selected[-1]) + 1
        consumed = len(selected)
        return {
            "path": self._display_path(path),
            "content_type": content_type,
            "offset": offset,
            "total_lines": len(lines),
            "content": "\n".join(selected),
            "next_offset": offset + consumed if offset + consumed < len(lines) else None,
            "truncated": char_truncated or offset + consumed < len(lines),
            "character_limit_reached": char_truncated,
        }

    def write_file(self, file_path: str, content: str) -> dict:
        path = self._path(file_path, write=True)
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise WorkspaceToolError(f"content exceeds {MAX_WRITE_BYTES} byte write limit")
        if path.exists():
            if not path.is_file():
                raise WorkspaceToolError("target is not a file")
            self._file_state().require_current(path)
        self._track_before_mutation(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._file_state().record(path)
        virtual = workspace_manager.system_workspace.parse_virtual_path(file_path)
        if virtual is not None:
            workspace_manager.system_workspace.invalidate(virtual[0])
        return {"path": self._display_path(path), "bytes": len(encoded)}

    def edit_file(self, file_path: str, old_string: str, new_string: str) -> dict:
        path = self._path(file_path, write=True)
        if not path.is_file():
            raise WorkspaceToolError("file not found")
        self._file_state().require_current(path)
        text = path.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count != 1:
            raise WorkspaceToolError(f"old_string must occur exactly once; found {count}")
        updated = text.replace(old_string, new_string, 1)
        if len(updated.encode("utf-8")) > MAX_WRITE_BYTES:
            raise WorkspaceToolError("edited content exceeds write limit")
        self._track_before_mutation(path)
        path.write_text(updated, encoding="utf-8")
        self._file_state().record(path)
        virtual = workspace_manager.system_workspace.parse_virtual_path(file_path)
        if virtual is not None:
            workspace_manager.system_workspace.invalidate(virtual[0])
        return {"path": self._display_path(path), "replacements": 1}

    def delete_file(self, file_path: str, *, confirm: bool = False) -> dict:
        """Delete one workspace file without exposing recursive filesystem access."""
        if not confirm:
            raise WorkspaceToolError("file deletion requires confirm=true")
        path = self._path(file_path, write=True)
        if not path.exists():
            raise WorkspaceToolError("file not found")
        if not path.is_file():
            raise WorkspaceToolError("only files can be deleted; directory deletion is not supported")
        display_path = self._display_path(path)
        size = path.stat().st_size
        self._track_before_mutation(path)
        path.unlink()
        self._file_state().forget(path)
        virtual = workspace_manager.system_workspace.parse_virtual_path(file_path)
        if virtual is not None:
            workspace_manager.system_workspace.invalidate(virtual[0])
        return {"path": display_path, "deleted": True, "bytes": size}

    def move_file(
        self, source_path: str, destination_path: str, *, confirm_overwrite: bool = False,
    ) -> dict:
        """Move or rename one file inside writable workspace roots."""
        source = self._path(source_path, write=True)
        destination = self._path(destination_path, write=True)
        if not source.exists():
            raise WorkspaceToolError("source file not found")
        if not source.is_file():
            raise WorkspaceToolError("only files can be moved; directory moves are not supported")
        if source == destination:
            raise WorkspaceToolError("source and destination must be different")
        destination_existed = destination.exists()
        if destination_existed:
            if not destination.is_file():
                raise WorkspaceToolError("destination is not a file")
            if not confirm_overwrite:
                raise WorkspaceToolError("destination exists; overwrite requires confirm_overwrite=true")
        self._track_before_mutation(source, destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_display = self._display_path(source)
        shutil.move(str(source), str(destination))
        self._file_state().move(source, destination)
        system = workspace_manager.system_workspace
        for raw_path in (source_path, destination_path):
            virtual = system.parse_virtual_path(raw_path)
            if virtual is not None:
                system.invalidate(virtual[0])
        return {
            "source_path": source_display,
            "path": self._display_path(destination),
            "moved": True,
            "overwritten": destination_existed,
        }

    def command(self, operation: str, path: str = ".", pattern: str = "", timeout: int = 30) -> dict:
        """Run a fixed, shell-free operation. No user-provided executable exists."""
        target = self._path(path)
        timeout = max(1, min(int(timeout), 120))
        if operation == "checksum":
            if not target.is_file():
                raise WorkspaceToolError("checksum target must be a file")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            return {"operation": operation, "sha256": digest, "path": self._display_path(target)}
        if operation == "json_validate":
            if not target.is_file():
                raise WorkspaceToolError("JSON target must be a file")
            json.loads(target.read_text(encoding="utf-8"))
            return {"operation": operation, "valid": True, "path": self._display_path(target)}

        runtime = self._runtime()

        git_base = [
            "git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=NUL",
            "-c", "diff.external=", "-C", str(runtime.workdir),
        ]
        commands = {
            "git_status": [*git_base, "status", "--short"],
            "git_diff": [*git_base, "diff", "--no-ext-diff", "--", str(target)],
            "git_log": [*git_base, "log", "-n", "20", "--oneline"],
            "python_compile": [sys.executable, "-m", "compileall", "-q", str(target)],
        }
        argv = commands.get(operation)
        if argv is None:
            raise WorkspaceToolError("unsupported operation")
        command_env = os.environ.copy()
        command_env["GIT_CONFIG_NOSYSTEM"] = "1"
        command_env["GIT_CONFIG_GLOBAL"] = "NUL" if os.name == "nt" else "/dev/null"
        command_env["PYTHONPYCACHEPREFIX"] = str(runtime.cache_dir / "pycache")
        try:
            completed = subprocess.run(
                argv, cwd=str(runtime.workdir), capture_output=True, text=True,
                timeout=timeout, shell=False, check=False, env=command_env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("[files] command execution failed: %s", exc)
            raise WorkspaceToolError(f"operation failed: {exc}") from exc
        output = (completed.stdout or "") + (completed.stderr or "")
        return {
            "operation": operation,
            "exit_code": completed.returncode,
            "output": output[:MAX_COMMAND_OUTPUT],
            "truncated": len(output) > MAX_COMMAND_OUTPUT,
        }

def structured_output(output: Any, required_fields: list[str] | None = None) -> dict:
    if not isinstance(output, (dict, list, str)):
        raise WorkspaceToolError("output must be an object, array, or string")
    missing = []
    if required_fields:
        if not isinstance(output, dict):
            raise WorkspaceToolError("required_fields can only validate object output")
        missing = [field for field in required_fields if field not in output]
    if missing:
        raise WorkspaceToolError("missing required fields: " + ", ".join(missing))
    return {"output": output}
