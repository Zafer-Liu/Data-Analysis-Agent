"""Session-scoped team definitions and mailbox."""
from __future__ import annotations

import json
import logging
log = logging.getLogger(__name__)
import re
import ast
import threading
import time
from datetime import datetime
import uuid

from data.workspace import workspace_manager
from infrastructure.paths import content_data_path
from agent.execution import (
    ExecutionCancelled, ExecutionContext, ExecutionStopped,
    execution_owner_alive, get_execution_context,
)
from agent.teams.review import (
    QUALITY_REVIEW_INSTRUCTION, QualityReviewError, collect_evidence_references,
    parse_quality_review,
)
from agent.teams.a2a import (
    A2AError, TERMINAL_STATES, agent_card, finish_task, new_task, public_task,
    set_task_state, task_state, text_message, validate_message,
)

_BAD_NAME_RE = re.compile(r"[\x00-\x1f\x7f/\\]")
_LOCK = threading.RLock()
LEAD_RECIPIENT = "leader"
LEGACY_LEAD_RECIPIENTS = {"leader", "lead"}
VALID_MEMBER_STATUSES = {"idle", "queued", "running", "completed", "failed", "needs_review"}
QUALITY_REVIEWER_NAME = "质量复核员"
QUALITY_REVIEWER_ROLE = "quality_reviewer"
QUALITY_REVIEWER_INSTRUCTIONS = (
    "固定质量复核员。只读复核团队输出中的数据口径、SQL、分子分母、样本范围、"
    "工具错误、图表失败和无证据业务归因。必须独立使用 get_schema/profile_data/query_data "
    "等只读工具复算关键指标，明确证据缺口。"
    + QUALITY_REVIEW_INSTRUCTION
)


class WorkspaceTeamError(ValueError):
    pass


class WorkspaceTeamStore:
    def __init__(self, session_id: str, *, workspace_id: str | None = None) -> None:
        safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id or "default"))[:120]
        self._path = content_data_path("outputs", "teams", safe_session, "agent_teams.json")
        self.scope = "session"
        fixed_id = (
            str(workspace_manager.workspace_id_for_session(session_id) or "")
            if workspace_id is None else str(workspace_id or "")
        )
        self.workspace_id = fixed_id

    def _load(self) -> dict:
        if not self._path.exists():
            return {"teams": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.debug("[teams] team store load failed: %s", exc)
            raise WorkspaceTeamError(f"team store is unreadable: {exc}") from exc
        return data if isinstance(data, dict) else {"teams": {}}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self._path)

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _age_seconds(value: str) -> float:
        if not value:
            return 0.0
        try:
            return (datetime.now() - datetime.fromisoformat(value)).total_seconds()
        except ValueError:
            return 0.0

    @staticmethod
    def _validate_name(name: str, label: str) -> str:
        name = re.sub(r"\s+", " ", str(name or "")).strip()
        if not name or len(name) > 64 or name == "*" or _BAD_NAME_RE.search(name):
            raise WorkspaceTeamError(f"invalid {label} name")
        return name

    @staticmethod
    def _normalize_leader_alias(name: str) -> str:
        value = re.sub(r"\s+", " ", str(name or "")).strip()
        return LEAD_RECIPIENT if value.lower() == "lead" else value

    @staticmethod
    def _normalize_tool_events(events) -> list[dict]:
        if not isinstance(events, list):
            return []
        normalized = []
        for item in events[:30]:
            if not isinstance(item, dict):
                continue
            # Mailbox previews are bounded. Full output lives behind the
            # artifact/claim references collected before preview truncation.
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            args_text = json.dumps(args, ensure_ascii=False, default=str)
            normalized.append({
                "tool": str(item.get("tool") or item.get("name") or "")[:120],
                "args": args if len(args_text) <= 2000 else {"preview": args_text[:1800], "truncated": True},
                "result": str(item.get("result") or item.get("content") or "")[:2000],
                "status": str(item.get("status") or "ok")[:30],
                "elapsed_seconds": item.get("elapsed_seconds"),
                "created_at": str(item.get("created_at") or "")[:40],
                "evidence": collect_evidence_references(item),
            })
        return [item for item in normalized if item["tool"]]

    @staticmethod
    def _coerce_members(members) -> list[dict]:
        if isinstance(members, str):
            raw = members.strip()
            if not raw:
                return []
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(raw)
                except (ValueError, SyntaxError) as exc:
                    raise WorkspaceTeamError("members must be an array") from exc
            members = parsed
        if isinstance(members, dict):
            members = members.get("members") or [members]
        if not isinstance(members, list):
            raise WorkspaceTeamError("members must be an array")
        normalized = []
        for item in members:
            if isinstance(item, dict):
                normalized.append(item)
            else:
                normalized.append({"name": str(item)})
        return normalized

    @staticmethod
    def quality_reviewer_member() -> dict:
        return {
            "name": QUALITY_REVIEWER_NAME,
            "agent_id": QUALITY_REVIEWER_NAME,
            "role": QUALITY_REVIEWER_ROLE,
            "instructions": QUALITY_REVIEWER_INSTRUCTIONS,
            "status": "idle",
            "last_message": "",
            "last_active_at": "",
        }

    @staticmethod
    def _is_quality_reviewer(member: dict) -> bool:
        return (
            str(member.get("name") or "") == QUALITY_REVIEWER_NAME
            or str(member.get("role") or "") == QUALITY_REVIEWER_ROLE
        )

    @classmethod
    def _append_quality_reviewer_if_missing(cls, members: list[dict]) -> tuple[list[dict], bool]:
        if any(cls._is_quality_reviewer(member) for member in members):
            return members, False
        return [*members, cls.quality_reviewer_member()], True

    @classmethod
    def _normalize_member(cls, member: dict) -> dict:
        from agent.teams.profiles import normalize_team_profile

        member_name = cls._validate_name(str(member.get("name", "")), "member")
        if member_name.lower() in LEGACY_LEAD_RECIPIENTS:
            raise WorkspaceTeamError("leader/lead are reserved for the main agent")
        return {
            "name": member_name,
            "agent_id": str(member.get("agent_id") or "agent_" + uuid.uuid4().hex)[:100],
            "role": str(member.get("role") or "analyst")[:100],
            "instructions": str(member.get("instructions") or "")[:4000],
            "profile": normalize_team_profile(member.get("profile")),
            "status": "idle", "last_message": "", "last_active_at": "",
        }

    def create(self, name: str, description: str, members: list[dict]) -> dict:
        name = self._validate_name(name, "team")
        normalized = [self._normalize_member(member) for member in self._coerce_members(members)]
        names = [member["name"] for member in normalized]
        if not normalized or len(normalized) > 16:
            raise WorkspaceTeamError("a team requires 1-16 members")
        if len(set(names)) != len(names):
            raise WorkspaceTeamError("duplicate team member")
        with _LOCK:
            data = self._load()
            teams = data.setdefault("teams", {})
            now = self._now()
            existing = teams.get(name)
            if existing:
                # Reusing a team must never erase its mailbox, evidence or tasks.
                current = {member["name"]: member for member in existing.get("members", [])}
                for member in normalized:
                    previous = current.get(member["name"])
                    if previous:
                        if previous.get("status") in {"queued", "running"}:
                            raise WorkspaceTeamError(f"member is busy: {member['name']}")
                        for key in ("role", "instructions", "profile"):
                            previous[key] = member[key]
                    else:
                        current[member["name"]] = member
                if len(current) > 17:
                    raise WorkspaceTeamError("team member limit exceeded")
                normalized = list(current.values())
            normalized, reviewer_added = self._append_quality_reviewer_if_missing(normalized)
            team = existing or {
                "name": name, "members": [], "messages": [],
                "context_id": uuid.uuid4().hex, "a2a_tasks": {}, "created_at": now,
            }
            team.update({
                "description": (description or team.get("description", ""))[:1000],
                "members": normalized, "updated_at": now, "reused": bool(existing),
                "quality_reviewer": {"name": QUALITY_REVIEWER_NAME, "auto_added": reviewer_added},
            })
            self._ensure_protocol_state(team)
            teams[name] = team
            self._save(data)
            return team

    @staticmethod
    def _ensure_protocol_state(team: dict) -> None:
        # Additive migration: legacy teams/mailbox records remain readable.
        team.setdefault("context_id", uuid.uuid4().hex)
        team.setdefault("a2a_tasks", {})

    def create_agent(
        self, name: str, *, team_name: str = "default", role: str = "analyst",
        instructions: str = "", profile: str = "inherit",
    ) -> dict:
        name = self._validate_name(name, "member")
        team_name = self._validate_name(team_name, "team")
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(team_name)
            if team:
                member = next((m for m in team.get("members", []) if m["name"] == name), None)
                if member:
                    return {"team": team_name, "member": dict(member), "reused": True}
            team = self.create(team_name, "", [{
                "name": name, "role": role, "instructions": instructions, "profile": profile,
            }])
            member = next(m for m in team["members"] if m["name"] == name)
            return {"team": team_name, "member": dict(member), "reused": False}

    def card(self, team_name: str, member_name: str, *, url: str) -> dict:
        return agent_card(self.member(team_name, member_name), url=url)

    @staticmethod
    def _find_task(team: dict, task_id: str, member_name: str = "") -> dict:
        task = team.get("a2a_tasks", {}).get(task_id)
        if task is None or (member_name and task.get("metadata", {}).get("member_name") != member_name):
            raise A2AError(-32001, "Task not found")
        return task

    def get_task(
        self, team_name: str, task_id: str, *, member_name: str = "",
        history_length: int | None = None,
    ) -> dict:
        self.fail_stale_members(team_name)
        with _LOCK:
            return public_task(self._find_task(self.get(team_name), task_id, member_name), history_length)

    def cancel_task(self, team_name: str, task_id: str, *, member_name: str = "") -> dict:
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(self._validate_name(team_name, "team"))
            if team is None:
                raise WorkspaceTeamError(f"team not found: {team_name}")
            task = self._find_task(team, task_id, member_name)
            if task_state(task) == "canceled":
                return public_task(task)
            if task_state(task) in TERMINAL_STATES:
                raise A2AError(-32002, "Task is not cancelable")
            set_task_state(task, "canceled", text="Task canceled by the caller.")
            execution = task.setdefault("metadata", {}).setdefault("execution", {})
            execution.update(cancel_requested=True, cancel_reason="Task canceled by the caller.")
            context = get_execution_context(execution.get("attempt_id", ""))
            if context is not None:
                context.cancel("Task canceled by the caller.")
            # Running members stay occupied until their executor acknowledges
            # cancellation; never-started tasks can release the member now.
            for member in team.get("members", []):
                if member.get("active_task_id") == task_id and member.get("status") == "queued":
                    member["status"] = "idle"
                    member.pop("active_task_id", None)
            team["updated_at"] = self._now()
            self._save(data)
            return public_task(task)

    def submit_task(self, team_name: str, member_name: str, message: dict) -> tuple[dict, bool]:
        incoming = validate_message(message)
        team_name = self._validate_name(team_name, "team")
        member_name = self._validate_name(member_name, "member")
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(self._validate_name(team_name, "team"))
            if team is None:
                raise WorkspaceTeamError(f"team not found: {team_name}")
            member = next((m for m in team.get("members", []) if m["name"] == member_name), None)
            if member is None:
                raise WorkspaceTeamError(f"member not found: {member_name}")
            self._ensure_protocol_state(team)
            for task in team["a2a_tasks"].values():
                for prior in task.get("history", []):
                    if prior.get("messageId") != incoming["messageId"]:
                        continue
                    if (task["metadata"]["member_name"] != member_name
                            or prior.get("parts") != incoming["parts"]
                            or (incoming.get("contextId") and incoming["contextId"] != task["contextId"])
                            or (incoming.get("taskId") and incoming["taskId"] != task["id"])):
                        raise A2AError(-32602, "messageId has already been used for a different request")
                    return public_task(task), False
            if incoming.get("taskId"):
                task = self._find_task(team, incoming["taskId"], member_name)
                if incoming.get("contextId") and incoming["contextId"] != task["contextId"]:
                    raise A2AError(-32602, "contextId does not match the task")
                if task_state(task) in TERMINAL_STATES:
                    raise A2AError(-32004, "Terminal tasks cannot be restarted; omit taskId for a new task")
                raise A2AError(-32004, "This bounded agent cannot accept updates to an active task")
            context_id = incoming.get("contextId") or team["context_id"]
            if (member.get("status") == "running"
                    or (member.get("status") == "queued" and member.get("active_task_id"))):
                raise A2AError(-32004, "Agent already has queued or running work")
            task = new_task(incoming, context_id=context_id, member_name=member_name)
            team["a2a_tasks"][task["id"]] = task
            from agent.teams.a2a import message_text
            team.setdefault("messages", []).append({
                "id": incoming["messageId"], "sender": LEAD_RECIPIENT, "recipient": member_name,
                "message": message_text(incoming), "message_type": "assignment", "read": True,
                "created_at": self._now(), "a2a": task["history"][0],
                "task_id": task["id"], "context_id": context_id,
            })
            team["messages"] = team["messages"][-500:]
            self._prune_tasks(team)
            member["status"] = "queued"
            member["active_task_id"] = task["id"]
            member["last_active_at"] = self._now()
            team["updated_at"] = self._now()
            self._save(data)
            return public_task(task), True

    @staticmethod
    def _prune_tasks(team: dict) -> None:
        tasks = team.get("a2a_tasks", {})
        active_ids = {member.get("active_task_id") for member in team.get("members", [])}
        terminal = [key for key, task in tasks.items() if task_state(task) in TERMINAL_STATES and key not in active_ids]
        for key in terminal[:max(0, len(tasks) - 200)]:
            tasks.pop(key, None)

    def ensure_quality_reviewer(self, name: str) -> dict:
        """Ensure the fixed quality reviewer exists for an existing team."""
        name = self._validate_name(name, "team")
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(name)
            if team is None:
                raise WorkspaceTeamError(f"team not found: {name}")
            members = list(team.get("members", []))
            updated, added = self._append_quality_reviewer_if_missing(members)
            if added:
                team["members"] = updated
                team["quality_reviewer"] = {"name": QUALITY_REVIEWER_NAME, "auto_added": True}
                team["updated_at"] = self._now()
                self._save(data)
            return next(
                member for member in team.get("members", [])
                if self._is_quality_reviewer(member)
            )

    def delete(
        self,
        name: str,
        *,
        require_inactive: bool = False,
        force: bool = False,
    ) -> dict:
        name = self._validate_name(name, "team")
        if require_inactive:
            self.fail_stale_members(name)
            self.release_stale_queued_members(name)
        with _LOCK:
            data = self._load()
            teams = data.setdefault("teams", {})
            team = teams.get(name)
            if team is None:
                raise WorkspaceTeamError(f"team not found: {name}")
            active = [
                str(member.get("name") or "")
                for member in team.get("members", [])
                if member.get("status") in {"queued", "running"}
            ]
            if require_inactive and active:
                raise WorkspaceTeamError(
                    "团队成员仍在执行或排队，暂不能解散团队："
                    + ", ".join(active)
                )
            messages = team.get("messages", [])
            if (messages or team.get("a2a_tasks")) and not force:
                raise WorkspaceTeamError(
                    "团队已有委派结果、错误或质量复核记录，默认保留以便复盘；"
                    "请先使用 team_status 查看证据。只有用户明确确认删除时，"
                    "才可再次调用 team_delete 并传 force=true。"
                )
            teams.pop(name, None)
            self._save(data)
        return {"deleted": name, "force": bool(force)}

    def clear_messages(self, name: str) -> dict:
        """Clear one team's communication history without deleting the team."""
        name = self._validate_name(name, "team")
        self.fail_stale_members(name)
        self.release_stale_queued_members(name)
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(name)
            if team is None:
                raise WorkspaceTeamError(f"team not found: {name}")
            active = [
                str(member.get("name") or "")
                for member in team.get("members", [])
                if member.get("status") in {"queued", "running"}
            ]
            if active:
                raise WorkspaceTeamError(
                    "团队成员仍在执行或排队，暂不能清空沟通记录："
                    + ", ".join(active)
                )
            cleared = len(team.get("messages", []))
            team["messages"] = []
            for member in team.get("members", []):
                member["last_message"] = ""
            team["updated_at"] = self._now()
            self._save(data)
        return {"team": name, "cleared_messages": cleared}

    def get(self, name: str) -> dict:
        name = self._validate_name(name, "team")
        with _LOCK:
            team = self._load().get("teams", {}).get(name)
        if team is None:
            raise WorkspaceTeamError(f"team not found: {name}")
        return team

    def list(self) -> list[dict]:
        self.fail_stale_members()
        self.release_stale_queued_members()
        with _LOCK:
            teams = list(self._load().get("teams", {}).values())
        result = []
        for team in teams:
            members = team.get("members", [])
            result.append({
                "name": team.get("name", ""),
                "description": team.get("description", ""),
                "member_count": len(members),
                "members": [
                    {
                        "name": member.get("name", ""),
                        "role": member.get("role", ""),
                        "status": member.get("status", "idle"),
                        "last_active_at": member.get("last_active_at", ""),
                    }
                    for member in members
                ],
                "message_count": len(team.get("messages", [])),
                "created_at": team.get("created_at", ""),
                "updated_at": team.get("updated_at", ""),
            })
        return result

    def status(self, name: str, *, mark_lead_read: bool = False) -> dict:
        self.fail_stale_members(name)
        self.release_stale_queued_members(name)
        if mark_lead_read:
            self.mark_lead_read(name)
        team = self.get(name)
        messages = team.get("messages", [])
        members = []
        for member in team.get("members", []):
            member_name = member.get("name", "")
            unread = [
                msg for msg in messages
                if msg.get("recipient") == member_name and not msg.get("read")
            ]
            members.append({
                "name": member_name,
                "agent_id": member.get("agent_id", member_name),
                "role": member.get("role", ""),
                "instructions": member.get("instructions", ""),
                "profile": member.get("profile") or {},
                "active_task_id": member.get("active_task_id", ""),
                "execution": member.get("execution") or {},
                "evidence": member.get("evidence") or {},
                "quality_review": member.get("quality_review"),
                "status": member.get("status", "idle"),
                "last_message": member.get("last_message", ""),
                "last_active_at": member.get("last_active_at", ""),
                "unread_messages": len(unread),
            })
        lead_unread = [
            msg for msg in messages
            if msg.get("recipient") in LEGACY_LEAD_RECIPIENTS and not msg.get("read")
        ]
        return {
            "name": team.get("name", ""),
            "description": team.get("description", ""),
            "members": members,
            "lead_unread_messages": len(lead_unread),
            "recent_messages": messages[-200:],
            "quality_review": team.get("quality_review"),
            "protocol": "a2a/1.0",
            "context_id": team.get("context_id", ""),
            "recent_tasks": [public_task(task, 0) for task in list(team.get("a2a_tasks", {}).values())[-30:]],
            "created_at": team.get("created_at", ""),
            "updated_at": team.get("updated_at", ""),
        }

    def send_message(
        self,
        team_name: str,
        recipient: str,
        message: str,
        sender: str = LEAD_RECIPIENT,
        *,
        read: bool = False,
        queue: bool = True,
        message_type: str = "text",
    ) -> dict:
        team_name = self._validate_name(team_name, "team")
        recipient = self._normalize_leader_alias(recipient)
        if recipient != "*":
            recipient = self._validate_name(recipient, "recipient")
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(team_name)
            if team is None:
                raise WorkspaceTeamError(f"team not found: {team_name}")
            members = {member["name"]: member for member in team.get("members", [])}
            if recipient not in {*members, LEAD_RECIPIENT, "*"}:
                raise WorkspaceTeamError(f"member not found: {recipient}")
            recipients = list(members) if recipient == "*" else [recipient]
            created = []
            for target in recipients:
                item = {
                    "id": uuid.uuid4().hex[:12],
                    "sender": self._validate_name(self._normalize_leader_alias(sender), "sender"),
                    "recipient": target,
                    "message": (message or "")[:10_000],
                    "message_type": str(message_type or "text")[:40],
                    "read": bool(read),
                    "created_at": self._now(),
                }
                self._ensure_protocol_state(team)
                item["a2a"] = text_message(
                    item["message"], role="user" if item["sender"] == LEAD_RECIPIENT else "agent",
                    context_id=team["context_id"],
                    metadata={"sender": item["sender"], "recipient": target, "message_type": item["message_type"]},
                )
                team.setdefault("messages", []).append(item)
                created.append(item)
                if queue and target in members and item["sender"] != target:
                    if members[target].get("status") != "running":
                        members[target]["status"] = "queued"
                    members[target]["last_message"] = item["message"][:500]
                    members[target]["last_active_at"] = self._now()
            team["messages"] = team["messages"][-500:]
            team["updated_at"] = self._now()
            self._save(data)
            return {"sent": len(created), "messages": created, **(created[0] if len(created) == 1 else {})}

    def member(self, team_name: str, member_name: str) -> dict:
        member_name = self._validate_name(member_name, "member")
        team = self.get(team_name)
        member = next((item for item in team.get("members", []) if item.get("name") == member_name), None)
        if member is None:
            raise WorkspaceTeamError(f"member not found: {member_name}")
        return member

    def _task_execution_canceled(self, team_name: str, task_id: str) -> bool:
        with _LOCK:
            task = self._load().get("teams", {}).get(team_name, {}).get("a2a_tasks", {}).get(task_id)
            return not task or task_state(task) != "working" or bool(
                task.get("metadata", {}).get("execution", {}).get("cancel_requested")
            )

    def _acknowledge_member_exit(self, team_name: str, member_name: str, task_id: str, attempt_id: str) -> None:
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(team_name)
            if team is None:
                return
            task = team.get("a2a_tasks", {}).get(task_id)
            if task is None:
                return
            execution = task.get("metadata", {}).get("execution", {})
            if execution.get("attempt_id") != attempt_id:
                return
            execution.update(exit_acknowledged=True, exited_at=self._now(), awaiting_exit=False)
            if task_state(task) == "working":
                set_task_state(task, "failed", text="Executor exited without completing its task.")
            member = next((m for m in team.get("members", []) if m.get("name") == member_name), None)
            if (member is not None and member.get("active_task_id") == task_id
                    and member.get("execution", {}).get("attempt_id") == attempt_id):
                member["execution"] = dict(execution)
                member.pop("active_task_id", None)
                member["status"] = (
                    "needs_review" if task_state(task) == "input-required" else
                    "idle" if task_state(task) in {"completed", "canceled"} else "failed"
                )
                member["last_active_at"] = self._now()
            team["updated_at"] = self._now()
            self._save(data)

    def begin_member_turn(
        self, team_name: str, member_name: str, *, task_id: str = "",
        execution_context: ExecutionContext | None = None, timeout_seconds: float | None = 300,
    ) -> dict:
        team_name = self._validate_name(team_name, "team")
        member_name = self._validate_name(member_name, "member")
        self.fail_stale_members(team_name)
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(team_name)
            if team is None:
                raise WorkspaceTeamError(f"team not found: {team_name}")
            member = next((item for item in team.get("members", []) if item.get("name") == member_name), None)
            if member is None:
                raise WorkspaceTeamError(f"member not found: {member_name}")
            self._ensure_protocol_state(team)
            prior_execution = member.get("execution") or {}
            if (member.get("status") == "running" or (
                prior_execution.get("attempt_id") and execution_owner_alive(prior_execution) is not False
            )):
                raise WorkspaceTeamError(f"member is already running: {member_name}")
            active_id = member.get("active_task_id", "")
            if active_id and active_id != task_id:
                active = team["a2a_tasks"].get(active_id)
                if active and task_state(active) not in TERMINAL_STATES:
                    raise WorkspaceTeamError(f"member already has an active task: {member_name}")
            inbox = []
            for msg in team.get("messages", []):
                if msg.get("recipient") == member_name and not msg.get("read"):
                    msg["read"] = True
                    inbox.append(msg)
            if task_id:
                task = self._find_task(team, task_id, member_name)
            else:
                assignment = next((
                    msg for msg in reversed(team.get("messages", []))
                    if msg.get("recipient") == member_name
                    and msg.get("message_type") in {"assignment", "quality_review_assignment"}
                    and not (msg.get("a2a") or {}).get("taskId")
                ), None)
                incoming = (assignment or {}).get("a2a") or text_message(
                    "\n".join(msg.get("message", "") for msg in inbox) or "Delegated task",
                    context_id=team["context_id"],
                )
                task = new_task(incoming, context_id=team["context_id"], member_name=member_name)
                team["a2a_tasks"][task["id"]] = task
                if assignment is not None:
                    assignment["a2a"] = task["history"][0]
                self._prune_tasks(team)
            if task_state(task) in TERMINAL_STATES:
                raise WorkspaceTeamError("terminal task cannot start a new execution attempt")
            current_task_id = task["id"]
            check = lambda: self._task_execution_canceled(team_name, current_task_id)
            context = (
                execution_context.child(timeout_seconds=timeout_seconds, cancellation_check=check)
                if execution_context is not None else
                ExecutionContext(timeout_seconds=timeout_seconds, cancellation_check=check)
            )
            execution = {**context.to_dict(), "awaiting_exit": False}
            task.setdefault("metadata", {})["execution"] = dict(execution)
            set_task_state(task, "working")
            member["execution"] = dict(execution)
            member["status"] = "running"
            member["active_task_id"] = current_task_id
            member["last_active_at"] = self._now()
            team["updated_at"] = self._now()
            try:
                self._save(data)
            except Exception:
                context.acknowledge_exit()
                raise
            context.add_exit_callback(lambda: self._acknowledge_member_exit(
                team_name, member_name, current_task_id, context.attempt_id,
            ))
            execution_member = dict(member)
            execution_member.update({
                "_a2a_team_name": team_name, "_a2a_task_id": current_task_id,
                "_execution_context": context, "_execution_attempt_id": context.attempt_id,
                "_structured_output_only": self._is_quality_reviewer(member),
            })
            return {"team": team_name, "member": execution_member, "inbox": inbox,
                    "task": public_task(task), "_execution_context": context,
                    "_execution_attempt_id": context.attempt_id}

    def complete_member_turn(
        self, team_name: str, member_name: str, result: str, *, ok: bool = True,
        tool_events=None, task_id: str | None = None,
        execution_context: ExecutionContext | None = None, attempt_id: str = "",
        evidence=None, quality_review=None,
    ) -> dict:
        """Publish the owning attempt, then acknowledge its actual body exit.

        Old callers may omit context/attempt; resolve them from the selected
        task, never from a different active member turn. Child exit callbacks
        retain the member lease when a blocking child is still returning.
        """
        result = str(result or "")
        if not result.strip():
            result, ok = "Agent produced no final output.", False
        team_name = self._validate_name(team_name, "team")
        member_name = self._validate_name(member_name, "member")
        context = None
        try:
            with _LOCK:
                data = self._load()
                team = data.get("teams", {}).get(team_name)
                if team is None:
                    raise WorkspaceTeamError(f"team not found: {team_name}")
                member = next((item for item in team.get("members", []) if item.get("name") == member_name), None)
                if member is None:
                    raise WorkspaceTeamError(f"member not found: {member_name}")
                self._ensure_protocol_state(team)
                selected_id = member.get("active_task_id", "") if task_id is None else task_id
                task = self._find_task(team, selected_id, member_name) if selected_id else None
                execution = (task or {}).get("metadata", {}).get("execution", {})
                expected = str(execution.get("attempt_id") or "")
                supplied = attempt_id or (execution_context.attempt_id if execution_context is not None else "")
                if supplied and (supplied != expected or (
                    execution_context is not None and execution_context.attempt_id != expected
                )):
                    raise WorkspaceTeamError("execution attempt does not own this task")
                context = execution_context or get_execution_context(expected)
                if expected and context is None and not execution.get("exit_acknowledged"):
                    raise WorkspaceTeamError("execution attempt is no longer owned by this process")
                if context is not None:
                    try:
                        context.checkpoint()
                    except ExecutionStopped as exc:
                        if task is not None and task_state(task) not in TERMINAL_STATES:
                            set_task_state(task, "canceled" if isinstance(exc, ExecutionCancelled) else "failed", text=str(exc))
                        execution.update(cancel_requested=True, cancel_reason=str(exc)[:500], awaiting_exit=True)
                if task is not None and (task_state(task) in TERMINAL_STATES or execution.get("exit_acknowledged")):
                    team["updated_at"] = self._now()
                    self._save(data)
                    return {"team": team_name, "member": member_name, "status": task_state(task),
                            "ignored": True, "task": public_task(task)}
                # Dependency failures can be recorded without claiming another
                # active member's task. Only its owning completion changes it.
                owns_member = not member.get("active_task_id") or member.get("active_task_id") == selected_id
                if task is None:
                    task = new_task(text_message("Undispatched delegated task"), context_id=team["context_id"], member_name=member_name)
                    team["a2a_tasks"][task["id"]] = task
                    self._prune_tasks(team)
                refs = collect_evidence_references(evidence, tool_events or [], result)
                metadata = task.setdefault("metadata", {})
                metadata["evidence"] = refs
                review = None
                if self._is_quality_reviewer(member) or quality_review is not None:
                    try:
                        review = parse_quality_review(
                            quality_review if quality_review is not None else result,
                            require_missing_evidence=True,
                        )
                    except QualityReviewError as exc:
                        review = {"decision": "escalate", "issues": [str(exc)], "evidence": [],
                                  "missing_evidence": ["valid structured quality review"]}
                    if not ok and review["decision"] == "pass":
                        review = {**review, "decision": "escalate", "issues": ["Reviewer execution failed."]}
                    metadata["quality_review"] = review
                    team["quality_review"] = {"member": member_name, "task_id": task["id"], **review}
                blocked = review is not None and review["decision"] != "pass"
                if blocked:
                    set_task_state(task, "input-required", text=json.dumps(review, ensure_ascii=False))
                    a2a_message = task["status"]["message"]
                else:
                    a2a_message = finish_task(task, result, ok=ok)
                completion_status = "needs_review" if blocked else "idle" if ok else "failed"
                if expected:
                    execution["awaiting_exit"] = True
                if owns_member:
                    member["last_message"] = result[:500]
                    member["last_active_at"] = self._now()
                    member["evidence"] = refs
                    if review is not None:
                        member["quality_review"] = review
                    if expected:
                        member["execution"] = dict(execution)
                        member["status"] = "running"
                    else:
                        member.pop("active_task_id", None)
                        member["status"] = completion_status
                message = {
                    "id": uuid.uuid4().hex[:12], "sender": member_name, "recipient": LEAD_RECIPIENT,
                    "message": result[:20_000], "message_type": "quality_review" if review is not None else "result" if ok else "error",
                    "read": False, "created_at": self._now(),
                    "tool_events": self._normalize_tool_events(tool_events), "evidence": refs,
                    "a2a": a2a_message, "task_id": task["id"], "context_id": task["contextId"],
                }
                if review is not None:
                    message["quality_review"] = review
                team.setdefault("messages", []).append(message)
                team["messages"] = team["messages"][-500:]
                team["updated_at"] = self._now()
                self._save(data)
                return {"team": team_name, "member": member_name, "status": completion_status,
                        "message": message, "task": public_task(task), "evidence": refs,
                        "quality_review": review}
        finally:
            if context is not None:
                context.acknowledge_exit()

    def mark_lead_read(self, team_name: str) -> int:
        team_name = self._validate_name(team_name, "team")
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(team_name)
            if team is None:
                raise WorkspaceTeamError(f"team not found: {team_name}")
            changed = 0
            for msg in team.get("messages", []):
                if msg.get("recipient") in LEGACY_LEAD_RECIPIENTS and not msg.get("read"):
                    msg["read"] = True
                    changed += 1
            if changed:
                team["updated_at"] = self._now()
                self._save(data)
            return changed

    def fail_stale_members(self, team_name: str | None = None, *, max_age_seconds: int = 300) -> int:
        """Request timeout cancellation; release only proven exited owners.

        Unknown legacy/foreign ownership stays occupied. Wall-clock age alone
        cannot prove that an uninterruptible executor stopped using its source.
        """
        with _LOCK:
            data = self._load()
            teams = data.get("teams", {})
            selected = [self._validate_name(team_name, "team")] if team_name else list(teams)
            changed = 0
            for name in selected:
                team = teams.get(name)
                if not team:
                    continue
                for member in team.get("members", []):
                    if member.get("status") != "running":
                        continue
                    task = team.get("a2a_tasks", {}).get(member.get("active_task_id", ""))
                    execution = (task or {}).get("metadata", {}).get("execution") or member.get("execution") or {}
                    context = get_execution_context(execution.get("attempt_id", ""))
                    alive = execution_owner_alive(execution)
                    expired = self._age_seconds(member.get("last_active_at", "")) > max_age_seconds
                    expired = expired or bool(context is not None and context.deadline is not None and context.deadline <= time.monotonic())
                    canceled = bool(execution.get("cancel_requested")) or bool(context is not None and context.cancellation_requested)
                    if alive is not False and not expired and not canceled:
                        continue
                    reason = "Executor owner exited before task completion." if alive is False else (
                        "Task cancellation requested; awaiting executor exit." if canceled else
                        "Delegated task timed out; awaiting executor exit."
                    )
                    if alive is not False and execution.get("awaiting_exit"):
                        continue
                    if context is not None:
                        context.cancel(reason)
                    execution.update(cancel_requested=True, cancel_reason=reason, awaiting_exit=alive is not False)
                    if task is not None:
                        if task_state(task) in {"working", "submitted"}:
                            set_task_state(task, "canceled" if canceled else "failed", text=reason)
                        task.setdefault("metadata", {})["execution"] = dict(execution)
                    if alive is False:
                        execution.update(exit_acknowledged=True, exited_at=self._now(), awaiting_exit=False)
                        if task is not None:
                            task["metadata"]["execution"] = dict(execution)
                        member.pop("active_task_id", None)
                        member["status"] = (
                            "needs_review" if task and task_state(task) == "input-required" else
                            "idle" if task and task_state(task) in {"completed", "canceled"} else "failed"
                        )
                    member["execution"] = dict(execution)
                    member["last_message"] = reason
                    team["updated_at"] = self._now()
                    changed += 1
            if changed:
                self._save(data)
            return changed

    def release_stale_queued_members(self, team_name: str | None = None, *, max_age_seconds: int = 300) -> int:
        with _LOCK:
            data = self._load()
            teams = data.get("teams", {})
            selected = [self._validate_name(team_name, "team")] if team_name else list(teams)
            changed = 0
            for name in selected:
                team = teams.get(name)
                if not team:
                    continue
                for member in team.get("members", []):
                    if member.get("status") != "queued":
                        continue
                    if self._age_seconds(member.get("last_active_at", "")) <= max_age_seconds:
                        continue
                    member_name = member.get("name", "")
                    task = team.get("a2a_tasks", {}).get(member.get("active_task_id", ""))
                    if task and task_state(task) not in TERMINAL_STATES:
                        set_task_state(task, "failed", text="Queued task expired before execution.")
                    member.pop("active_task_id", None)
                    for msg in team.get("messages", []):
                        if msg.get("recipient") == member_name and not msg.get("read"):
                            msg["read"] = True
                    member["status"] = "idle"
                    member["last_message"] = ""
                    member["last_active_at"] = self._now()
                    team["updated_at"] = self._now()
                    changed += 1
            if changed:
                self._save(data)
            return changed
