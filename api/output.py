"""Blueprint: file download endpoint for exported Excel / Word files.

A5 起：路由支持 ?sid=<session_id> 查询参数。
  - 带 sid：先查该 session 的 workspace artifacts_dir，找不到再查默认 outputs/exports
  - 不带 sid：只查默认 outputs/exports（向后兼容旧链接）
"""
import logging
import os
import re
from infrastructure.paths import content_data_path

from flask import Blueprint, send_file, abort, request, jsonify, Response
from .state import check_session_ownership, require_session_ownership

log = logging.getLogger(__name__)

bp = Blueprint("output", __name__)

_EXPORT_DIR = str(content_data_path("outputs", "exports"))


def _resolve_filepath(filename: str) -> str | None:
    """按优先级查找文件，返回存在的绝对路径或 None。

    查找顺序：
      1. session 的 workspace artifacts_dir（如有 sid 且已挂载）
      2. 默认 outputs/exports/
    """
    # 安全校验：禁止路径穿越
    if ".." in filename or re.search(r'[\\/\x00]', filename):
        return None

    # 1. Prefer the immutable Workspace identity embedded in new links. This
    # remains valid after the session switches to another Workspace.
    workspace_id = (request.args.get("workspace_id") or "").strip()
    if workspace_id:
        try:
            from data.workspace import workspace_manager
            root = workspace_manager.root_for_workspace(workspace_id)
            if root is not None:
                artifacts_dir = (root / "artifacts").resolve()
                resolved = (artifacts_dir / filename).resolve()
                resolved.relative_to(artifacts_dir)
                if resolved.is_file():
                    return str(resolved)
        except (OSError, ValueError):
            pass

    # 2. Legacy links: current session workspace artifacts_dir.
    sid = (request.args.get("sid") or "").strip()
    if sid:
        try:
            from data.workspace import workspace_manager
            runtime = workspace_manager.get(sid)
            if runtime is not None:
                artifacts_path = os.path.join(str(runtime.artifacts_dir), filename)
                # 二次校验：resolve 后必须在 artifacts_dir 内（防符号链接逃逸）
                try:
                    resolved = os.path.realpath(artifacts_path)
                    if resolved.startswith(str(runtime.artifacts_dir.resolve())):
                        if os.path.isfile(resolved):
                            return resolved
                except OSError as e:
                    log.debug("[output] resolve path failed for %s: %s", artifacts_path, e)
        except Exception as e:
            log.warning("[output] workspace lookup failed for sid=%s: %s", sid, e)

    # 3. 查默认 outputs/exports/
    default_path = os.path.join(_EXPORT_DIR, filename)
    if os.path.isfile(default_path):
        return default_path

    return None


@bp.get("/api/export/<path:filename>")
def download_export(filename: str):
    """Serve an exported file.

    查找顺序：session artifacts_dir（如有 ?sid=）→ outputs/exports/。
    Security: filename 经过路径穿越校验；artifacts_dir 路径 resolve 后必须在
    runtime.artifacts_dir 内。
    """
    sid = (request.args.get("sid") or "").strip()
    from infrastructure.paths import is_cloud_managed_runtime
    if is_cloud_managed_runtime():
        if not sid:
            abort(403)
        allowed, _ = check_session_ownership(sid)
        if not allowed:
            abort(403)

        # A caller may only resolve the current Workspace or one explicitly
        # referenced by an artifact created in its own live session.
        requested_workspace = (request.args.get("workspace_id") or "").strip()
        if requested_workspace:
            from api.state import session_manager
            from data.workspace import workspace_manager

            sess = session_manager.get(sid)
            allowed_workspaces = {
                str(workspace_manager.workspace_id_for_session(sid) or ""),
                *{
                    str(item.get("workspace_id") or "")
                    for item in getattr(sess, "recent_artifacts", []) if isinstance(item, dict)
                },
            }
            if requested_workspace not in allowed_workspaces:
                abort(403)

    filepath = _resolve_filepath(filename)
    if filepath is None:
        # File expired, was cleaned, or the workspace/session no longer
        # resolves the artifact. Return a structured error so the front
        # end can surface "该文件已过期，请重新生成" instead of a bare 404
        # page the user can't act on.
        return jsonify({
            "error": "file_expired",
            "message": "该导出文件已过期或被清理，请重新生成。",
        }), 404

    return send_file(filepath, as_attachment=True, download_name=filename)


@bp.get("/api/session/<sid>/tool-results/<artifact_id>")
@require_session_ownership
def read_tool_result(sid: str, artifact_id: str):
    """Read a B6 persisted result after validating it belongs to the session."""
    from api.state import session_manager
    from agent.tools.results import load_tool_result_artifact
    from data.workspace import workspace_manager

    sess = session_manager.get(sid)
    if sess is None:
        abort(404)
    allowed = any(
        item.get("artifact_id") == artifact_id
        for item in getattr(sess, "recent_artifacts", [])
    )
    if not allowed:
        abort(404)
    artifact = next(
        (item for item in getattr(sess, "recent_artifacts", [])
         if item.get("artifact_id") == artifact_id),
        {},
    )
    workspace_id = str(artifact.get("workspace_id") or "")
    runtime = workspace_manager.get_by_workspace(workspace_id) if workspace_id else workspace_manager.get(sid)
    workspace_root = workspace_manager.root_for_workspace(workspace_id) if workspace_id else None
    record = load_tool_result_artifact(
        artifact_id, runtime=runtime, workspace_root=workspace_root,
    )
    if record is None:
        abort(404)
    if request.args.get("format") == "json":
        return jsonify(record)
    return Response(
        str(record.get("data", "")),
        content_type=str(record.get("content_type") or "text/plain; charset=utf-8"),
        headers={
            "X-Artifact-Id": artifact_id,
            "X-Tool-Name": str(record.get("tool") or ""),
            "X-Content-SHA256": str(record.get("sha256") or ""),
        },
    )
