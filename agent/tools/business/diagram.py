"""Agent tool implementations for draw.io diagram operations.

Model-visible tools: display_diagram, edit_diagram, and get_diagram.
Shape-library lookup is an internal validation helper used by diagram writes.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from data.business_canvas_store import get_business_canvas_store
from data.diagram_templates import DIAGRAM_TEMPLATES
from agent.tools.business.xml_utils import (
    validate_and_fix_xml,
    wrap_with_mxfile,
    apply_diagram_operations,
    is_mxcell_xml_complete,
)

log = logging.getLogger(__name__)

_SHAPE_LIBS_DIR = Path(__file__).resolve().parents[3] / "data" / "shape_libs"
_SHAPE_LIBRARY_REF_RE = re.compile(r"\bmxgraph\.([a-z0-9_-]+)(?:\.|;)", re.IGNORECASE)


def _load_shape_library_reference(library: str) -> dict[str, Any]:
    """Load one local draw.io shape catalog for internal diagram validation."""
    normalized = str(library or "").strip().lower()
    if not normalized or not re.fullmatch(r"[a-z0-9_-]+", normalized):
        return {"ok": False, "error": "Invalid shape library name"}

    lib_file = _SHAPE_LIBS_DIR / f"{normalized}.md"
    if not lib_file.is_file():
        available = sorted(path.stem for path in _SHAPE_LIBS_DIR.glob("*.md"))
        return {
            "ok": False,
            "library": normalized,
            "error": f"Shape library '{normalized}' is not installed",
            "available": available,
        }
    try:
        return {
            "ok": True,
            "library": normalized,
            "content": lib_file.read_text(encoding="utf-8"),
        }
    except OSError as exc:
        return {"ok": False, "library": normalized, "error": f"Failed to read shape library: {exc}"}


def _inspect_shape_libraries(xml: str) -> dict[str, Any]:
    """Resolve all mxgraph libraries referenced by XML without a model tool call."""
    libraries = sorted(set(_SHAPE_LIBRARY_REF_RE.findall(str(xml or ""))))
    resolved = [_load_shape_library_reference(library) for library in libraries]
    missing = [item for item in resolved if not item.get("ok")]
    return {
        "ok": not missing,
        "libraries": libraries,
        "missing": missing,
    }


# ── display_diagram ──────────────────────────────────────────


def _fill_template_content(template_id: str, content: dict[str, str]) -> str:
    """Load a template XML and fill content cells with provided text.

    Args:
        template_id: Key into DIAGRAM_TEMPLATES (e.g. "business_model_canvas").
        content: Dict mapping content_cells keys to text strings.

    Returns:
        Complete <mxfile> XML string with content filled in.
    """
    template = DIAGRAM_TEMPLATES.get(template_id)
    if not template:
        raise ValueError(f"Unknown template_id: {template_id}")
    cell_map = template.get("content_cells", {})

    # Parse with ElementTree instead of regex substitution so existing cell
    # values (placeholders) and attribute ordering are handled safely.
    root = ET.fromstring(template["xml"])
    id_to_cell = {cell.get("id"): cell for cell in root.iter("mxCell")}
    applied = 0
    for key, text in content.items():
        cell = id_to_cell.get(cell_map.get(key, ""))
        if cell is None:
            continue
        cell.set("value", str(text))
        applied += 1
    if not applied and content:
        raise ValueError(
            f"No content cells matched for template {template_id}; known keys: {sorted(cell_map)}"
        )
    # ET emits newlines inside attribute values as &#10; character references,
    # which XML parsers preserve (draw.io renders them as line breaks).
    return ET.tostring(root, encoding="unicode")


def handle_display_diagram(
    params: dict[str, Any],
    session_id: str | None = None,
    color_scheme: str = "mckinsey",
) -> dict[str, Any]:
    """Create or replace a diagram in the canvas project.

    Validates XML, auto-fixes if needed, stores in project, and returns
    project data. The frontend receives updates via SSE canvas_event.
    """
    xml = str(params.get("xml") or "")
    title = str(params.get("title") or "Diagram")
    template_id = str(params.get("template_id") or "")
    project_id = str(params.get("project_id") or "")
    content = params.get("content") or None

    # Content-fill mode: use template layout + fill content cells
    if content and isinstance(content, dict) and template_id:
        try:
            xml = _fill_template_content(template_id, content)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    elif content and not template_id:
        return {"ok": False, "error": "template_id is required when using content mode"}

    if not xml:
        return {"ok": False, "error": "either xml or (template_id + content) is required"}

    shape_libraries = _inspect_shape_libraries(xml)
    if not shape_libraries["ok"]:
        missing = ", ".join(item["library"] for item in shape_libraries["missing"])
        return {
            "ok": False,
            "error": f"Diagram XML references unavailable shape libraries: {missing}",
            "shape_libraries": shape_libraries,
        }

    # Wrap bare XML
    if "<mxfile>" not in xml:
        xml = wrap_with_mxfile(xml)

    # Validate and auto-fix
    result = validate_and_fix_xml(xml)
    if not result["valid"]:
        return {
            "ok": False,
            "error": f"Invalid diagram XML: {result['error']}",
            "fixes_applied": result.get("fixes", []),
        }

    xml = result["fixed"] or xml

    # Check completeness (truncation)
    if not is_mxcell_xml_complete(xml):
        return {
            "ok": False,
            "error": "Diagram XML appears truncated. Please provide complete XML.",
            "truncated": True,
        }

    # 主题换肤（槽位色 → 会话配色方案）+ 概念图质检；warnings 回传 LLM 同轮可修
    from agent.tools.business.diagram_theme import apply_diagram_theme, qc_diagram

    xml, _theme_warnings = apply_diagram_theme(xml, color_scheme)
    warnings = qc_diagram(xml)

    store = get_business_canvas_store()

    if project_id:
        # Update existing project
        project = store.get_project(project_id, session_id=session_id)
        if not project:
            return {"ok": False, "error": f"Canvas project {project_id} not found"}
        project = store.update_project_diagram_xml(
            project_id=project_id,
            diagram_xml=xml,
            actor_type="agent",
        )
        # Also switch to diagram mode
        project = store.update_project_rendering_mode(
            project_id=project_id,
            rendering_mode="diagram",
        )
        return {
            "ok": True,
            "project": project,
            "xml": xml,
            "fixes_applied": result.get("fixes", []),
            "shape_libraries": shape_libraries["libraries"],
            "warnings": warnings,
        }
    else:
        # Create new project with diagram template
        if not template_id:
            template_id = "business_model_canvas"  # default fallback
        try:
            project = store.create_project(
                session_id=session_id or "",
                template_id=template_id,
                title=title,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        # Update diagram XML
        project = store.update_project_diagram_xml(
            project_id=project["id"],
            diagram_xml=xml,
            actor_type="agent",
        )
        project = store.update_project_rendering_mode(
            project_id=project["id"],
            rendering_mode="diagram",
        )
        return {
            "ok": True,
            "project": project,
            "xml": xml,
            "fixes_applied": result.get("fixes", []),
            "shape_libraries": shape_libraries["libraries"],
            "warnings": warnings,
        }


# ── edit_diagram ──────────────────────────────────────────────


def handle_edit_diagram(
    params: dict[str, Any],
    session_id: str | None = None,
    color_scheme: str = "mckinsey",
) -> dict[str, Any]:
    """Edit specific cells in the current diagram by ID-based operations."""
    project_id = str(params.get("project_id") or "")
    operations = params.get("operations") or []

    if not project_id:
        return {"ok": False, "error": "project_id is required"}
    if not operations:
        return {"ok": False, "error": "operations array is required"}

    store = get_business_canvas_store()
    current_xml = store.get_project_diagram_xml(project_id)
    if not current_xml:
        return {"ok": False, "error": f"No diagram XML found for project {project_id}"}

    # Apply operations
    result = apply_diagram_operations(current_xml, operations)
    new_xml = result["result"]
    errors = result.get("errors", [])

    if errors and not new_xml:
        return {"ok": False, "error": "All operations failed", "operation_errors": errors}

    # Validate the result
    validation = validate_and_fix_xml(new_xml)
    if not validation["valid"]:
        return {"ok": False, "error": f"Result XML invalid after operations: {validation['error']}"}

    new_xml = validation["fixed"] or new_xml

    shape_libraries = _inspect_shape_libraries(new_xml)
    if not shape_libraries["ok"]:
        missing = ", ".join(item["library"] for item in shape_libraries["missing"])
        return {
            "ok": False,
            "error": f"Result XML references unavailable shape libraries: {missing}",
            "shape_libraries": shape_libraries,
        }

    # 编辑同样过主题换肤（新增槽位色统一）+ 质检
    from agent.tools.business.diagram_theme import apply_diagram_theme, qc_diagram

    new_xml, _theme_warnings = apply_diagram_theme(new_xml, color_scheme)
    warnings = qc_diagram(new_xml)

    # Store updated XML
    project = store.update_project_diagram_xml(
        project_id=project_id,
        diagram_xml=new_xml,
        actor_type="agent",
    )

    return {
        "ok": True,
        "project": project,
        "xml": new_xml,
        "operation_errors": errors,
        "fixes_applied": validation.get("fixes", []),
        "shape_libraries": shape_libraries["libraries"],
        "warnings": warnings,
    }


# ── get_diagram ────────────────────────────────────────────────


def handle_get_diagram(params: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    """Return the current diagram XML for a canvas project."""
    project_id = str(params.get("project_id") or "")

    if not project_id:
        return {"ok": False, "error": "project_id is required"}

    store = get_business_canvas_store()
    project = store.get_project(project_id, session_id=session_id)
    if not project:
        return {"ok": False, "error": f"Canvas project {project_id} not found"}

    xml = project.get("diagram_xml", "")
    return {"ok": True, "project_id": project_id, "xml": xml, "template_id": project.get("template_id", "")}
