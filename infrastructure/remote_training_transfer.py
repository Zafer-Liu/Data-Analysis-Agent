"""Bounded, verified SFTP transfers into runner-reserved task directories."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat

from data.remote_execution_store import RemoteExecutionError

BLOCK_BYTES = 1024 * 1024
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def relative_path(value):
    text = str(value or "")
    path = PurePosixPath(text)
    if (not text or len(text) > 1024 or "\\" in text or ":" in text or "\x00" in text
            or path.is_absolute() or any(p in {"", ".", ".."} for p in text.split("/"))):
        raise RemoteExecutionError("unsafe_path", "Expected a canonical relative file path")
    return path


def local_file(root, name, *, must_exist=True):
    root = Path(root).absolute()
    path = root.joinpath(*relative_path(name).parts)
    # Check every component before resolve; resolve alone hides a symlink escape.
    for part in [*reversed(root.parents), root, *list(path.parents)[::-1], path]:
        if not part.exists() and not part.is_symlink():
            continue
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RemoteExecutionError("unsafe_path", "Links and reparse points are not allowed")
    if not path.resolve().is_relative_to(root.resolve()):
        raise RemoteExecutionError("unsafe_path", "Local file escapes the snapshot root")
    if must_exist or path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RemoteExecutionError("unsafe_file", "Expected a regular file with one link")
    return path


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(BLOCK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_cancel(cancel):
    if cancel and cancel():
        raise RemoteExecutionError("cancel_pending", "Transfer canceled; remote exit still requires reconciliation")


def _remote_absolute(value):
    text = str(value or "")
    path = PurePosixPath(text)
    if not path.is_absolute() or len(text) > 2048 or "\\" in text or "\x00" in text or any(p == ".." for p in path.parts):
        raise RemoteExecutionError("unsafe_path", "Expected a runner-allocated absolute POSIX path")
    if str(path) != text or str(path) == "/":
        raise RemoteExecutionError("unsafe_path", "Non-canonical remote path")
    return path


def _missing(exc):
    return getattr(exc, "errno", None) == 2


class SftpTransfer:
    """An allocation is mandatory; no mkdir or arbitrary path operation is exposed.

    Each complete .part block is compared with the source before resuming. The
    final file is read back for a full SHA-256 before atomic rename. SFTP cannot
    enforce openat(O_NOFOLLOW) against a malicious server administrator; the
    trusted runner owns and isolates the allocated directory.
    """

    def __init__(self, sftp, *, max_bytes, max_files=1000, checkpoint=None):
        self.sftp = sftp
        self.max_bytes = int(max_bytes)
        self.max_files = int(max_files)
        self.checkpoint = checkpoint or (lambda **_kw: None)
        self.uploads = {}
        self.downloads = {}
        self.upload_root = None
        self.download_root = None

    def authorize_uploads(self, root, files):
        self.upload_root, self.uploads = self._allocation(root, files)

    def authorize_downloads(self, root, files):
        self.download_root, self.downloads = self._allocation(root, files)

    def _allocation(self, root, files):
        root = _remote_absolute(root)
        if not isinstance(files, list) or not 0 <= len(files) <= self.max_files:
            raise RemoteExecutionError("budget_exceeded", "Remote file count exceeds budget")
        result = {}
        total = 0
        for item in files:
            name = relative_path(item["relative_path"])
            path = _remote_absolute(item.get("remote_path") or str(root / name))
            if path != root / name or not path.is_relative_to(root) or path == root or path in result:
                raise RemoteExecutionError("unsafe_path", "Remote file is outside its reserved allocation")
            size = item.get("size_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0 or not _SHA256.fullmatch(str(item.get("sha256", ""))):
                raise RemoteExecutionError("invalid_manifest", "Invalid bounded file metadata")
            total += size
            result[path] = dict(item)
        if total > self.max_bytes:
            raise RemoteExecutionError("budget_exceeded", "Transfer exceeds approved byte budget")
        return root, result

    def _guard(self, path, root, *, missing=False):
        path = _remote_absolute(path)
        if root is None or not path.is_relative_to(root):
            raise RemoteExecutionError("unsafe_path", "Unallocated remote path")
        # Inspect ancestors above the allocation as well; the root cannot itself
        # hide behind a symlink. Never create missing remote directories.
        for part in [*reversed(path.parents), path]:
            if str(part) == "/":
                continue
            try:
                info = self.sftp.lstat(str(part))
            except OSError as exc:
                if missing and part == path and _missing(exc):
                    return None
                raise
            mode = info.st_mode
            if stat.S_ISLNK(mode) or (part != path and not stat.S_ISDIR(mode)):
                raise RemoteExecutionError("unsafe_path", "Remote link or non-directory ancestor")
            if part == path:
                if not stat.S_ISREG(mode) or getattr(info, "st_nlink", 1) != 1:
                    raise RemoteExecutionError("unsafe_file", "Remote file is not a private regular file")
                return info
        raise RemoteExecutionError("unsafe_path", "Invalid remote file")

    def _digest(self, path, *, size, cancel=None):
        digest = hashlib.sha256()
        seen = 0
        with self.sftp.open(str(path), "rb") as stream:
            while True:
                _check_cancel(cancel)
                block = stream.read(min(BLOCK_BYTES, size - seen + 1))
                if not block:
                    break
                seen += len(block)
                if seen > size:
                    raise RemoteExecutionError("size_mismatch", "Remote file grew beyond declared size")
                digest.update(block)
        if seen != size:
            raise RemoteExecutionError("size_mismatch", "Remote file size does not match manifest")
        return digest.hexdigest()

    def upload(self, local_path, remote_path, *, sha256, cancel=None, progress=None):
        path = _remote_absolute(remote_path)
        item = self.uploads.get(path)
        if item is None or item["sha256"] != sha256:
            raise RemoteExecutionError("unallocated_transfer", "Upload was not allocated by reserve")
        source = Path(local_path)
        local_file(source.parent, source.name)
        size = item["size_bytes"]
        if source.stat().st_size != size or file_sha256(source) != sha256:
            raise RemoteExecutionError("snapshot_changed", "Local input no longer matches approved snapshot")
        existing = self._guard(path, self.upload_root, missing=True)
        if existing:
            if existing.st_size == size and self._digest(path, size=size, cancel=cancel) == sha256:
                return {"size_bytes": size, "sha256": sha256, "complete": True}
            raise RemoteExecutionError("hash_mismatch", "Existing final upload differs from manifest")
        part = PurePosixPath(str(path) + ".part")
        partial = self._guard(part, self.upload_root, missing=True)
        verified = 0
        if partial and partial.st_size <= size:
            with self.sftp.open(str(part), "rb") as remote, source.open("rb") as local:
                while verified + BLOCK_BYTES <= partial.st_size:
                    _check_cancel(cancel)
                    left, right = remote.read(BLOCK_BYTES), local.read(BLOCK_BYTES)
                    if len(left) != BLOCK_BYTES or hashlib.sha256(left).digest() != hashlib.sha256(right).digest():
                        break
                    verified += BLOCK_BYTES
        with source.open("rb") as local, self.sftp.open(str(part), "r+b" if partial else "wb") as remote:
            remote.truncate(verified)
            remote.seek(verified)
            local.seek(verified)
            offset = verified
            while offset < size:
                _check_cancel(cancel)
                block = local.read(min(BLOCK_BYTES, size - offset))
                if not block:
                    raise RemoteExecutionError("snapshot_changed", "Input truncated during upload")
                remote.write(block)
                remote.flush()
                offset += len(block)
                self.checkpoint(direction="upload", remote_path=str(path), offset=offset,
                                block_sha256=hashlib.sha256(block).hexdigest(), sha256=sha256, size_bytes=size)
                if progress:
                    progress(offset, size)
        self._guard(part, self.upload_root)
        if self._digest(part, size=size, cancel=cancel) != sha256:
            raise RemoteExecutionError("hash_mismatch", "Uploaded SHA-256 differs from approved input")
        _check_cancel(cancel)
        self.sftp.rename(str(part), str(path))
        return {"size_bytes": size, "sha256": sha256, "complete": True}

    def download(self, remote_path, local_path, *, sha256, size_bytes, cancel=None, progress=None):
        path = _remote_absolute(remote_path)
        item = self.downloads.get(path)
        if item is None or item["sha256"] != sha256 or item["size_bytes"] != size_bytes:
            raise RemoteExecutionError("unallocated_transfer", "Download is not in the published artifact manifest")
        info = self._guard(path, self.download_root)
        if info.st_size != size_bytes:
            raise RemoteExecutionError("size_mismatch", "Published artifact changed size")
        target = Path(local_path)
        local_file(target.parent, target.name, must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.stat().st_size == size_bytes and file_sha256(target) == sha256:
                return {"size_bytes": size_bytes, "sha256": sha256, "complete": True}
            raise RemoteExecutionError("hash_mismatch", "Existing artifact has different content")
        part = target.with_name(target.name + ".part")
        local_file(part.parent, part.name, must_exist=False)
        offset = 0
        if part.exists():
            local_file(part.parent, part.name)
            with part.open("rb") as local, self.sftp.open(str(path), "rb") as remote:
                while offset + BLOCK_BYTES <= min(part.stat().st_size, size_bytes):
                    _check_cancel(cancel)
                    left, right = local.read(BLOCK_BYTES), remote.read(BLOCK_BYTES)
                    if len(right) != BLOCK_BYTES or hashlib.sha256(left).digest() != hashlib.sha256(right).digest():
                        break
                    offset += BLOCK_BYTES
        digest = hashlib.sha256()
        with part.open("r+b" if part.exists() else "w+b") as local, self.sftp.open(str(path), "rb") as remote:
            for _ in range(offset // BLOCK_BYTES):
                digest.update(local.read(BLOCK_BYTES))
            local.truncate(offset)
            remote.seek(offset)
            while offset < size_bytes:
                _check_cancel(cancel)
                block = remote.read(min(BLOCK_BYTES, size_bytes - offset))
                if not block:
                    raise RemoteExecutionError("size_mismatch", "Artifact truncated during download")
                local.write(block)
                local.flush()
                digest.update(block)
                offset += len(block)
                self.checkpoint(direction="download", remote_path=str(path), offset=offset,
                                block_sha256=hashlib.sha256(block).hexdigest(), sha256=sha256, size_bytes=size_bytes)
                if progress:
                    progress(offset, size_bytes)
            if remote.read(1):
                raise RemoteExecutionError("size_mismatch", "Artifact exceeds declared bytes")
            os.fsync(local.fileno())
        if digest.hexdigest() != sha256:
            raise RemoteExecutionError("hash_mismatch", "Downloaded artifact hash mismatch")
        _check_cancel(cancel)
        part.replace(target)
        return {"size_bytes": size_bytes, "sha256": sha256, "complete": True}
