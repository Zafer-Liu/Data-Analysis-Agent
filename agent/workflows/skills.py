"""Expose immutable published Workflow versions as tightly scoped Skills."""
from __future__ import annotations

import json
import re
from pathlib import Path

from agent.skills.models import SkillDef
from data.workflow_store import WorkflowStore


def workflow_skill_name(version_id: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", str(version_id or "").lower()).strip("-")
    return f"workflow-{token[-48:]}"


def build_workflow_skills(store: WorkflowStore) -> dict[str, SkillDef]:
    """Build one Skill per currently published immutable Workflow version."""
    result: dict[str, SkillDef] = {}
    for workflow in store.list_workflows():
        version_id = str(workflow.get("current_version_id") or "")
        if not version_id:
            continue
        version = store.get_version(version_id)
        if version is None:
            continue
        schema = version.get("input_schema") or {}
        properties = schema.get("properties") if isinstance(schema, dict) else {}
        required = schema.get("required") if isinstance(schema, dict) else []
        input_names = list(properties) if isinstance(properties, dict) else []
        description = (
            f"运行已发布 Workflow「{workflow['name']}」"
            f"v{version['version_number']}。{workflow.get('description') or ''}"
        ).strip()
        if input_names:
            description += f" 输入：{', '.join(input_names)}。"
        prompt = (
            f"启动不可变 Workflow 版本 `{version_id}`。\n\n"
            "只从用户请求提取输入 schema 声明的字段，然后调用 `workflow_start`，"
            "参数必须包含该 `workflow_version_id` 与 `inputs`。不要调用内部节点工具，"
            "不要改写流程图，不要绕过审批，也不要把 Run 已启动表述为已完成。\n\n"
            f"输入 schema：\n```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```\n\n"
            f"必填字段：{', '.join(str(item) for item in required) or '无'}。\n\n"
            "用户请求：$ARGUMENTS"
        )
        name = workflow_skill_name(version_id)
        result[name] = SkillDef(
            name=name,
            description=description[:240],
            prompt=prompt,
            path=Path("workflow-versions") / f"{version_id}.md",
            icon="🧩",
            allowed_tools=("workflow_start",),
            tags=("workflow",),
            source="workflow",
            display_name=f"{workflow['name']} · Workflow",
        )
    return result

def session_workflow_skills(session_id: str) -> dict[str, SkillDef]:
    """Resolve Workflow Skills for the workspace mounted by one session."""
    from agent.workflows.models import WorkflowContractError
    from agent.workflows.runtime import workflow_runtime_manager

    try:
        runtime = workflow_runtime_manager.get(str(session_id or ""))
    except WorkflowContractError:
        return {}
    return build_workflow_skills(runtime.workflow_store)


def get_session_workflow_skill(session_id: str, name: str) -> SkillDef | None:
    return session_workflow_skills(session_id).get(str(name or ""))
