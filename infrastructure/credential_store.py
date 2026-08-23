"""OS-backed secret storage for optional remote integrations.

Connection configuration may store only the opaque reference returned by this
module.  The secret itself is delegated to ``keyring`` (Windows Credential
Manager, macOS Keychain, or the platform Secret Service); there is deliberately
no plaintext-file fallback.
"""
from __future__ import annotations

import uuid


SERVICE_NAME = "BusinessAnalyticsAgent"


class CredentialStoreError(RuntimeError):
    """The host has no usable OS credential backend."""


def _keyring():
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise CredentialStoreError(
            "未安装系统凭据库支持；请安装 requirements-remote.txt，或使用 SSH agent/密钥认证"
        ) from exc
    return keyring, KeyringError


def store_secret(secret: str, *, label: str) -> str:
    """Persist a non-empty secret in the OS vault and return an opaque ID."""
    if not isinstance(secret, str) or not secret:
        raise ValueError("secret 必须是非空字符串")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("label 不能为空")
    keyring, keyring_error = _keyring()
    reference = f"{label.strip()}:{uuid.uuid4().hex}"
    try:
        keyring.set_password(SERVICE_NAME, reference, secret)
    except keyring_error as exc:
        raise CredentialStoreError(f"系统凭据库写入失败: {exc}") from exc
    return reference


def load_secret(reference: str) -> str:
    """Load a secret by opaque reference without logging its value."""
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("credential reference 不能为空")
    keyring, keyring_error = _keyring()
    try:
        secret = keyring.get_password(SERVICE_NAME, reference)
    except keyring_error as exc:
        raise CredentialStoreError(f"系统凭据库读取失败: {exc}") from exc
    if secret is None:
        raise CredentialStoreError("系统凭据库中不存在该凭据")
    return secret


def delete_secret(reference: str) -> None:
    """Remove a secret; a missing item is treated as already deleted."""
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("credential reference 不能为空")
    keyring, keyring_error = _keyring()
    try:
        keyring.delete_password(SERVICE_NAME, reference)
    except keyring_error as exc:
        # keyring backends differ on how a missing entry is represented.  It is
        # safe to make deletion idempotent for connection cleanup.
        if "not found" not in str(exc).lower():
            raise CredentialStoreError(f"系统凭据库删除失败: {exc}") from exc
