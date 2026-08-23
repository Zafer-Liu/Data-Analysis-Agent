"""Blueprint: GPU 算力检测与总开关 — /api/gpu/*"""
import logging
import json
import urllib.request

from flask import Blueprint, request, jsonify

from infrastructure import credential_store, gpu_connections, gpu_detect, ssh_tunnel_manager

log = logging.getLogger(__name__)

bp = Blueprint("gpu", __name__)


def _models_at(base_url: str) -> list[str]:
    with urllib.request.urlopen(base_url.rstrip("/") + "/v1/models", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    return [item.get("id") for item in payload.get("data", []) if item.get("id")]


def _test_model_at(base_url: str, model: str) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply exactly: OK"}],
        "max_tokens": 5,
        "stream": False,
    }).encode("utf-8")
    request_obj = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer no-key"}, method="POST",
    )
    with urllib.request.urlopen(request_obj, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    choices = payload.get("choices") or []
    content = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
    return str(content).strip()[:200]


def _connection_url(connection_id: str, connection: dict) -> str | None:
    if connection.get("connection_type") == "direct":
        return connection["base_url"]
    return ssh_tunnel_manager.tunnel_manager.status(connection_id).get("local_url")


@bp.get("/api/gpu/status")
def gpu_status():
    """返回完整算力状态：NVIDIA 独显探测 + Ollama 在线状态 + 总开关。"""
    return jsonify(gpu_detect.detect_all())


@bp.post("/api/gpu/enabled")
def set_gpu_enabled():
    """设置「启用 GPU 算力」总开关。"""
    d = request.json or {}
    enabled = d.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"ok": False, "message": "enabled 必须是布尔值"}), 400
    gpu_detect.set_gpu_enabled(enabled)
    return jsonify({"ok": True, "enabled": enabled})


@bp.get("/api/gpu/connections")
def list_connections():
    """List saved connection definitions without any secrets."""
    return jsonify({"ok": True, "connections": gpu_connections.list_connections()})


@bp.post("/api/gpu/connections")
def create_connection():
    """Create a connection, delegating password storage to the OS vault."""
    payload = request.json or {}
    credential_ref = None
    try:
        if payload.get("connection_type", "ssh") == "ssh" and payload.get("auth_method", "agent") == "password":
            credential_ref = credential_store.store_secret(
                str(payload.get("password") or ""), label="remote-gpu-ssh",
            )
        connection = gpu_connections.create_connection(payload, credential_ref=credential_ref)
        return jsonify({"ok": True, "connection": connection}), 201
    except (ValueError, credential_store.CredentialStoreError) as exc:
        if credential_ref:
            try:
                credential_store.delete_secret(credential_ref)
            except credential_store.CredentialStoreError:
                log.warning("[gpu] failed to clean up rejected credential reference")
        return jsonify({"ok": False, "message": str(exc)}), 400


@bp.delete("/api/gpu/connections/<connection_id>")
def delete_connection(connection_id: str):
    connection = gpu_connections.delete_connection(connection_id)
    if connection is None:
        return jsonify({"ok": False, "message": "连接不存在"}), 404
    ssh_tunnel_manager.tunnel_manager.close(connection_id)
    reference = connection.get("credential_ref")
    if reference:
        try:
            credential_store.delete_secret(reference)
        except credential_store.CredentialStoreError:
            log.warning("[gpu] failed to delete credential reference for %s", connection_id)
    return jsonify({"ok": True})


@bp.post("/api/gpu/connections/<connection_id>/host-key")
def inspect_connection_host_key(connection_id: str):
    connection = gpu_connections.get_connection(connection_id)
    if connection is None:
        return jsonify({"ok": False, "message": "连接不存在"}), 404
    if connection.get("connection_type") == "direct":
        return jsonify({"ok": False, "message": "公网直连不使用 SSH 主机指纹"}), 409
    try:
        key = ssh_tunnel_manager.inspect_host_key(connection["host"], connection["port"])
        return jsonify({"ok": True, "host_key": key})
    except ssh_tunnel_manager.TunnelError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@bp.post("/api/gpu/connections/<connection_id>/trust-host-key")
def trust_connection_host_key(connection_id: str):
    connection = gpu_connections.get_connection(connection_id)
    if connection is None:
        return jsonify({"ok": False, "message": "连接不存在"}), 404
    if connection.get("connection_type") == "direct":
        return jsonify({"ok": False, "message": "公网直连不使用 SSH 主机指纹"}), 409
    payload = request.json or {}
    try:
        ssh_tunnel_manager.trust_host_key(
            connection["host"], connection["port"],
            str(payload.get("key_type") or ""), str(payload.get("key_base64") or ""),
        )
        return jsonify({"ok": True})
    except (ValueError, ssh_tunnel_manager.TunnelError) as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@bp.post("/api/gpu/connections/<connection_id>/connect")
def connect_connection(connection_id: str):
    connection = gpu_connections.get_connection(connection_id)
    if connection is None:
        return jsonify({"ok": False, "message": "连接不存在"}), 404
    try:
        if connection.get("connection_type") == "direct":
            _models_at(connection["base_url"])
            return jsonify({"ok": True, "connected": True, "local_url": connection["base_url"]})
        password = None
        if connection["auth_method"] == "password":
            password = credential_store.load_secret(connection["credential_ref"])
        tunnel = ssh_tunnel_manager.tunnel_manager.connect(
            connection_id, host=connection["host"], port=connection["port"],
            username=connection["username"], target_host=connection["target_host"],
            target_port=connection["target_port"], password=password,
            key_filename=connection.get("key_file"),
        )
        return jsonify({"ok": True, "connected": True, "local_url": tunnel.local_url})
    except (credential_store.CredentialStoreError, ssh_tunnel_manager.TunnelError, ValueError) as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    except Exception as exc:  # Direct endpoint health check failed.
        log.info("[gpu] connection failed for %s: %s", connection_id, exc.__class__.__name__)
        return jsonify({"ok": False, "message": "连接失败；请检查公网端点与 /v1/models 服务"}), 502


@bp.post("/api/gpu/connections/<connection_id>/disconnect")
def disconnect_connection(connection_id: str):
    if gpu_connections.get_connection(connection_id) is None:
        return jsonify({"ok": False, "message": "连接不存在"}), 404
    ssh_tunnel_manager.tunnel_manager.close(connection_id)
    return jsonify({"ok": True, "connected": False})


@bp.get("/api/gpu/connections/<connection_id>/status")
def connection_status(connection_id: str):
    connection = gpu_connections.get_connection(connection_id)
    if connection is None:
        return jsonify({"ok": False, "message": "连接不存在"}), 404
    if connection.get("connection_type") == "direct":
        try:
            _models_at(connection["base_url"])
            return jsonify({"ok": True, "connected": True, "local_url": connection["base_url"]})
        except Exception:
            return jsonify({"ok": True, "connected": False})
    return jsonify({"ok": True, **ssh_tunnel_manager.tunnel_manager.status(connection_id)})


@bp.post("/api/gpu/connections/<connection_id>/training/preflight")
def training_preflight(connection_id: str):
    """Mark an SSH connection as a training target only after fixed-runner checks."""
    connection = gpu_connections.get_connection(connection_id)
    if connection is None:
        return jsonify({"ok": False, "message": "连接不存在"}), 404
    if connection.get("connection_type") != "ssh":
        return jsonify({"ok": False, "message": "远程训练器预检仅支持 SSH 连接"}), 409
    try:
        status = ssh_tunnel_manager.tunnel_manager.remote_training_preflight(connection_id)
        updated = gpu_connections.set_training_runner_status(connection_id, status)
        return jsonify({"ok": True, "training_runner": updated["training_runner"]})
    except ssh_tunnel_manager.TunnelError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 409


@bp.get("/api/gpu/connections/<connection_id>/models")
def connection_models(connection_id: str):
    """Discover OpenAI-compatible models through an active loopback tunnel."""
    connection = gpu_connections.get_connection(connection_id)
    if connection is None:
        return jsonify({"ok": False, "message": "连接不存在"}), 404
    status = ssh_tunnel_manager.tunnel_manager.status(connection_id)
    if connection.get("connection_type") == "direct":
        status = {"connected": True, "local_url": connection["base_url"]}
    if not status.get("connected"):
        return jsonify({"ok": False, "message": "连接尚未建立"}), 409
    try:
        models = _models_at(status["local_url"])
        return jsonify({"ok": True, "models": models})
    except Exception as exc:  # Endpoint implementations return varied network errors.
        log.info("[gpu] model discovery failed for %s: %s", connection_id, exc.__class__.__name__)
        return jsonify({"ok": False, "message": "远端模型列表不可用；请确认服务提供 OpenAI /v1/models"}), 502


@bp.post("/api/gpu/connections/<connection_id>/models/register")
def register_connection_model(connection_id: str):
    """Register one discovered model as a local OpenAI-compatible provider."""
    connection = gpu_connections.get_connection(connection_id)
    status = ssh_tunnel_manager.tunnel_manager.status(connection_id)
    if connection and connection.get("connection_type") == "direct":
        status = {"connected": True, "local_url": connection["base_url"]}
    if connection is None:
        return jsonify({"ok": False, "message": "连接不存在"}), 404
    if not status.get("connected"):
        return jsonify({"ok": False, "message": "连接尚未建立"}), 409
    model = str((request.json or {}).get("model") or "").strip()
    if not model:
        return jsonify({"ok": False, "message": "model 不能为空"}), 400
    from LLM.llm_config_manager import LOCAL_KEY_PLACEHOLDER, get_config_manager

    provider_name = f"{connection['name']} · {model}"
    ok, message = get_config_manager().add_custom_model(
        provider_name, status["local_url"] + "/v1", model, LOCAL_KEY_PLACEHOLDER,
        allow_anonymous=connection.get("connection_type") == "direct",
    )
    return jsonify({"ok": ok, "message": message}), (200 if ok else 409)


@bp.post("/api/gpu/connections/<connection_id>/models/test")
def test_connection_model(connection_id: str):
    """Execute a bounded real inference request for connection acceptance."""
    connection = gpu_connections.get_connection(connection_id)
    if connection is None:
        return jsonify({"ok": False, "message": "连接不存在"}), 404
    status = ssh_tunnel_manager.tunnel_manager.status(connection_id)
    if connection.get("connection_type") == "direct":
        status = {"connected": True, "local_url": connection["base_url"]}
    if not status.get("connected"):
        return jsonify({"ok": False, "message": "连接尚未建立"}), 409
    model = str((request.json or {}).get("model") or "").strip()
    if not model:
        return jsonify({"ok": False, "message": "model 不能为空"}), 400
    try:
        reply = _test_model_at(status["local_url"], model)
        return jsonify({"ok": True, "message": "模型推理连通性验证成功", "reply": reply})
    except Exception as exc:
        log.info("[gpu] model test failed for %s: %s", connection_id, exc.__class__.__name__)
        return jsonify({"ok": False, "message": "模型推理测试失败；请检查模型 ID、端点和服务日志"}), 502
