"""Strict-host-key, loopback-only SSH forwarding for remote GPU inference.

This module owns long-lived transports for the process.  HTTP handlers must
use its returned local endpoint and never retain Paramiko objects themselves.
It deliberately provides forwarding only; remote training command execution is
implemented by a separate, restricted M3 runner.
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import logging
import select
import socket
import socketserver
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from infrastructure.paths import runtime_config_path


log = logging.getLogger(__name__)
KNOWN_HOSTS_FILE = runtime_config_path("ssh_known_hosts", "config/ssh_known_hosts")
_REMOTE_PREFLIGHT_COMMAND = "python3 -m baa_remote_runner --preflight --json"


class TunnelError(RuntimeError):
    pass


class UnknownHostKeyError(TunnelError):
    """A host must be explicitly trusted before credentials are sent."""


def _paramiko():
    try:
        import paramiko
    except ImportError as exc:
        raise TunnelError("未安装远程连接依赖；请安装 requirements-remote.txt") from exc
    return paramiko


def _host_key_name(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def fingerprint(key: Any) -> str:
    """Return the OpenSSH-style SHA256 fingerprint for display/confirmation."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def inspect_host_key(host: str, port: int = 22, timeout: float = 8.0) -> dict[str, str]:
    """Fetch a key for a UI confirmation step; this does not trust it."""
    paramiko = _paramiko()
    transport = paramiko.Transport((host, port))
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        return {"type": key.get_name(), "fingerprint": fingerprint(key), "base64": key.get_base64()}
    finally:
        transport.close()


def trust_host_key(host: str, port: int, key_type: str, key_base64: str) -> None:
    """Persist a user-confirmed server key in the application known_hosts file."""
    if not host or not key_type or not key_base64:
        raise ValueError("host、key_type 和 key_base64 均不能为空")
    paramiko = _paramiko()
    try:
        key = paramiko.PKey.from_type_string(key_type, base64.b64decode(key_base64))
    except Exception as exc:
        raise TunnelError("无效的 SSH 主机公钥") from exc
    KNOWN_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    hosts = paramiko.HostKeys()
    if KNOWN_HOSTS_FILE.exists():
        hosts.load(str(KNOWN_HOSTS_FILE))
    hosts.add(_host_key_name(host, port), key_type, key)
    hosts.save(str(KNOWN_HOSTS_FILE))


class _ForwardServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _ForwardHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        tunnel: "SshTunnel" = self.server.tunnel  # type: ignore[attr-defined]
        transport = tunnel.client.get_transport()
        if not transport or not transport.is_active():
            return
        channel = transport.open_channel(
            "direct-tcpip", (tunnel.target_host, tunnel.target_port), self.request.getpeername()
        )
        if channel is None:
            return
        try:
            while True:
                readable, _, _ = select.select([self.request, channel], [], [], 1.0)
                if self.request in readable:
                    data = self.request.recv(32768)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(32768)
                    if not data:
                        break
                    self.request.sendall(data)
        finally:
            channel.close()


@dataclass
class SshTunnel:
    connection_id: str
    client: Any
    server: _ForwardServer
    target_host: str
    target_port: int

    @property
    def local_port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def healthy(self) -> bool:
        transport = self.client.get_transport()
        return bool(transport and transport.is_active())

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.client.close()


class SshTunnelManager:
    """Owns active tunnels and serializes their lifecycle operations."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tunnels: dict[str, SshTunnel] = {}

    def connect(
        self,
        connection_id: str,
        *,
        host: str,
        port: int = 22,
        username: str,
        target_host: str = "127.0.0.1",
        target_port: int,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        timeout: float = 10.0,
    ) -> SshTunnel:
        """Open a trusted SSH transport and a loopback listener.

        Paramiko's RejectPolicy ensures unknown or changed keys fail before any
        password is sent.  Call ``inspect_host_key`` then ``trust_host_key``
        only after explicit user confirmation.
        """
        if not connection_id or not host or not username or not target_port:
            raise ValueError("connection_id、host、username 和 target_port 为必填项")
        if password is not None and key_filename is not None:
            raise ValueError("密码认证与私钥文件认证只能二选一")
        paramiko = _paramiko()
        with self._lock:
            self.close(connection_id)
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            if KNOWN_HOSTS_FILE.exists():
                client.load_host_keys(str(KNOWN_HOSTS_FILE))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            try:
                client.connect(
                    hostname=host, port=port, username=username, password=password,
                    key_filename=key_filename, timeout=timeout, banner_timeout=timeout,
                    auth_timeout=timeout, look_for_keys=password is None,
                    allow_agent=password is None,
                )
            except paramiko.SSHException as exc:
                client.close()
                if "not found in known_hosts" in str(exc).lower():
                    raise UnknownHostKeyError("SSH 主机尚未确认；请先核对并信任主机指纹") from exc
                raise TunnelError(f"SSH 连接失败: {exc}") from exc
            except OSError as exc:
                client.close()
                raise TunnelError(f"SSH 网络连接失败: {exc}") from exc
            server = _ForwardServer(("127.0.0.1", 0), _ForwardHandler)
            tunnel = SshTunnel(connection_id, client, server, target_host, target_port)
            server.tunnel = tunnel  # type: ignore[attr-defined]
            threading.Thread(target=server.serve_forever, name=f"ssh-tunnel-{connection_id}", daemon=True).start()
            self._tunnels[connection_id] = tunnel
            return tunnel

    def get(self, connection_id: str) -> Optional[SshTunnel]:
        with self._lock:
            return self._tunnels.get(connection_id)

    def status(self, connection_id: str) -> dict[str, Any]:
        tunnel = self.get(connection_id)
        if not tunnel:
            return {"connected": False}
        return {"connected": tunnel.healthy(), "local_url": tunnel.local_url}

    def remote_training_preflight(self, connection_id: str) -> dict[str, Any]:
        """Run the single, fixed M3 runner health command over an active tunnel.

        There are no user-controlled command fragments here. A host must first
        be fingerprint-trusted and connected through this manager.
        """
        tunnel = self.get(connection_id)
        if not tunnel or not tunnel.healthy():
            raise TunnelError("SSH 连接尚未建立")
        try:
            stdin, stdout, stderr = tunnel.client.exec_command(_REMOTE_PREFLIGHT_COMMAND, timeout=15)
            del stdin
            output = stdout.read(65536).decode("utf-8", "replace")
            error = stderr.read(4096).decode("utf-8", "replace")
            exit_code = stdout.channel.recv_exit_status()
        except Exception as exc:
            raise TunnelError(f"远程训练器预检失败: {exc.__class__.__name__}") from exc
        if exit_code != 0:
            raise TunnelError("远程训练器不可用；请在服务器部署 baa_remote_runner")
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise TunnelError("远程训练器返回格式无效") from exc
        if not isinstance(payload, dict) or payload.get("status") != "ready":
            raise TunnelError(str(payload.get("message") or error or "远程训练器未就绪"))
        return {
            "runner_ready": True,
            "runner_version": str(payload.get("version") or "unknown"),
            "python": str(payload.get("python") or "unknown"),
            "cuda": bool(payload.get("cuda")),
            "gpu_name": str(payload.get("gpu_name") or ""),
        }

    def close(self, connection_id: str) -> None:
        with self._lock:
            tunnel = self._tunnels.pop(connection_id, None)
        if tunnel:
            tunnel.close()

    def close_all(self) -> None:
        with self._lock:
            identifiers = list(self._tunnels)
        for connection_id in identifiers:
            self.close(connection_id)


tunnel_manager = SshTunnelManager()
atexit.register(tunnel_manager.close_all)
