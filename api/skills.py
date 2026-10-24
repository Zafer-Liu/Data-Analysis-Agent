"""Public catalog for file-based analysis skills."""
import logging
import os
import shutil
from pathlib import Path

from flask import Blueprint, jsonify, request

from agent.skills import SkillLoader
from agent.skills.parser import SkillError, parse_skill_file
from agent.skills.models import SKILL_NAME_RE, normalize_skill_tags

log = logging.getLogger(__name__)

bp = Blueprint("skills", __name__)


def _user_skills_dir():
    """Return the user-level skills directory, creating it if needed."""
    d = Path(os.getenv("BAA_SKILLS_DIR", "~/.baa/skills")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_skill(name, sid=""):
    """Find a skill file path and its source by name."""
    loader = SkillLoader()
    if sid:
        from data.workspace import workspace_manager
        runtime = workspace_manager.get(sid)
        if runtime:
            loader.workspace_dir = runtime.workdir / ".baa" / "skills"
    loaded = loader.load_all()
    skill = loaded.get(name)
    if skill is None:
        return None
    return skill.path, skill.source


def _build_skill_md(name, description, icon, allowed_tools, tags, prompt):
    """Build SKILL.md file content from form fields."""
    tools_yaml = ""
    if allowed_tools:
        tools_str = ", ".join(str(t) for t in allowed_tools)
        tools_yaml = "\nallowedTools: [" + tools_str + "]"
    tags_yaml = ""
    if tags:
        tags_yaml = "\ntags: [" + ", ".join(tags) + "]"
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"icon: {icon}{tools_yaml}{tags_yaml}\n"
        "---\n"
        f"{prompt}\n"
    )


@bp.get("/api/skills")
def list_skills():
    """List all available skills (builtin + user + workspace)."""
    workspace_dir = None
    sid = (request.args.get("sid") or "").strip()
    mode = (request.args.get("mode") or "").strip().lower()
    if mode and mode not in {"business", "ml", "empirical"}:
        return jsonify({"ok": False, "error": "Unknown analysis mode."}), 400
    if sid:
        from data.workspace import workspace_manager
        runtime = workspace_manager.get(sid)
        if runtime:
            workspace_dir = runtime.workdir / ".baa" / "skills"
    loader = SkillLoader(workspace_dir=workspace_dir)
    loaded = loader.load_all()
    if sid:
        from agent.workflows.skills import session_workflow_skills
        for name, skill in session_workflow_skills(sid).items():
            loaded.setdefault(name, skill)
    skills = [
        skill.to_public_dict() for skill in loaded.values()
        if not mode or skill.supports_analysis_mode(mode)
    ]
    return jsonify({
        "skills": skills,
        "diagnostics": [item.to_dict() for item in loader.diagnostics()],
    })


@bp.get("/api/skills/<path:name>")
def get_skill_detail(name):
    """Return skill metadata plus raw markdown content."""
    sid = (request.args.get("sid") or "").strip()
    if sid:
        from agent.workflows.skills import get_session_workflow_skill
        workflow_skill = get_session_workflow_skill(sid, name)
        if workflow_skill is not None:
            return jsonify({
                "ok": True,
                "skill": {
                    "name": workflow_skill.name,
                    "display_name": workflow_skill.display_name or workflow_skill.name,
                    "description": workflow_skill.description,
                    "icon": workflow_skill.icon,
                    "source": workflow_skill.source,
                    "allowed_tools": list(workflow_skill.allowed_tools),
                    "tags": list(workflow_skill.tags),
                    "path": "",
                    "readonly": True,
                    "prompt": workflow_skill.prompt,
                    "raw": workflow_skill.prompt,
                },
            })
    resolved = _resolve_skill(name, sid)
    if resolved is None:
        return jsonify({"ok": False, "error": "Skill not found."}), 404
    path, source = resolved
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    try:
        skill = parse_skill_file(path, source=source)
    except SkillError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({
        "ok": True,
        "skill": {
            "name": skill.name,
            "description": skill.description,
            "icon": skill.icon,
            "source": skill.source,
            "allowed_tools": list(skill.allowed_tools),
            "tags": list(skill.tags),
            "path": str(path),
            "readonly": source == "builtin",
            "prompt": skill.prompt,
            "raw": raw,
        },
    })


@bp.post("/api/skills")
def create_skill():
    """Create a new user-level custom skill."""
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    description = str(body.get("description", "")).strip()
    icon = str(body.get("icon", "")).strip() or "custom"
    prompt = str(body.get("prompt", "")).strip()
    allowed_tools = body.get("allowed_tools", [])
    if not isinstance(allowed_tools, list):
        allowed_tools = []
    try:
        tags = normalize_skill_tags(body.get("tags", []))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    if not SKILL_NAME_RE.fullmatch(name):
        return jsonify({"ok": False, "error": "Name must start with lowercase letter, only lowercase/digits/hyphens (2-64 chars)."}), 400
    if not description:
        return jsonify({"ok": False, "error": "Description required."}), 400
    if not prompt:
        return jsonify({"ok": False, "error": "Prompt required."}), 400
    if len(description) > 240:
        return jsonify({"ok": False, "error": "Description too long (max 240)."}), 400
    if len(prompt) > 50000:
        return jsonify({"ok": False, "error": "Prompt too long (max 50000)."}), 400

    user_dir = _user_skills_dir()
    skill_dir = user_dir / name
    skill_file = skill_dir / "SKILL.md"
    if skill_file.exists():
        return jsonify({"ok": False, "error": "Skill already exists."}), 409

    skill_dir.mkdir(parents=True, exist_ok=True)
    content = _build_skill_md(name, description, icon, allowed_tools, tags, prompt)
    skill_file.write_text(content, encoding="utf-8")
    log.info("[skills] created user skill %r at %s", name, skill_file)
    return jsonify({"ok": True, "name": name}), 201


@bp.put("/api/skills/<path:name>")
def update_skill(name):
    """Update an existing user-level custom skill."""
    sid = (request.args.get("sid") or "").strip()
    resolved = _resolve_skill(name, sid)
    if resolved is None:
        return jsonify({"ok": False, "error": "Skill not found."}), 404
    path, source = resolved
    if source == "builtin":
        return jsonify({"ok": False, "error": "Cannot modify builtin skill."}), 403

    body = request.get_json(silent=True) or {}
    description = str(body.get("description", "")).strip()
    icon = str(body.get("icon", "")).strip() or "custom"
    prompt = str(body.get("prompt", "")).strip()
    allowed_tools = body.get("allowed_tools", [])
    if not isinstance(allowed_tools, list):
        allowed_tools = []
    try:
        tags = normalize_skill_tags(body.get("tags", []))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    new_name = str(body.get("name", name)).strip()

    if not SKILL_NAME_RE.fullmatch(new_name):
        return jsonify({"ok": False, "error": "Invalid name format."}), 400
    if not description:
        return jsonify({"ok": False, "error": "Description required."}), 400
    if not prompt:
        return jsonify({"ok": False, "error": "Prompt required."}), 400

    user_dir = _user_skills_dir()
    skill_dir = user_dir / new_name
    skill_file = skill_dir / "SKILL.md"

    if new_name != name:
        if skill_file.exists():
            return jsonify({"ok": False, "error": "Skill name already exists."}), 409
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(_build_skill_md(new_name, description, icon, allowed_tools, tags, prompt), encoding="utf-8")
        old_dir = user_dir / name
        if old_dir.exists():
            shutil.rmtree(old_dir, ignore_errors=True)
    else:
        skill_file.write_text(_build_skill_md(name, description, icon, allowed_tools, tags, prompt), encoding="utf-8")

    log.info("[skills] updated user skill %r", new_name)
    return jsonify({"ok": True, "name": new_name})


@bp.get("/api/skills/tools")
def list_available_tools():
    """Return all registered built-in tools for the skill editor multiselect."""
    from agent.tools.registry import BUILTIN_TOOL_REGISTRY
    tools = [
        {"name": spec.name, "cat": spec.category}
        for spec in BUILTIN_TOOL_REGISTRY.all()
    ]
    tools.sort(key=lambda t: t["name"])
    return jsonify({"ok": True, "tools": tools})



@bp.delete("/api/skills/<path:name>")
def delete_skill(name):
    """Delete a user-level custom skill."""
    sid = (request.args.get("sid") or "").strip()
    resolved = _resolve_skill(name, sid)
    if resolved is None:
        return jsonify({"ok": False, "error": "Skill not found."}), 404
    path, source = resolved
    if source == "builtin":
        return jsonify({"ok": False, "error": "Cannot delete builtin skill."}), 403

    skill_dir = path.parent
    if skill_dir.exists():
        shutil.rmtree(skill_dir, ignore_errors=True)
    log.info("[skills] deleted user skill %r", name)
    return jsonify({"ok": True})
