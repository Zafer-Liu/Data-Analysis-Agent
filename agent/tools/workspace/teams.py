"""Session-scoped team definitions and mailbox."""
from __future__ import annotations

import json
import logging
log = logging.getLogger(__name__)
import re
import ast
import threading
from datetime import datetime
import uuid

from data.workspace import workspace_manager
from infrastructure.paths import data_path

_BAD_NAME_RE = re.compile(r"[\x00-\x1f\x7f/\\]")
_LOCK = threading.RLock()
LEAD_RECIPIENT = "leader"
LEGACY_LEAD_RECIPIENTS = {"leader", "lead"}
VALID_MEMBER_STATUSES = {"idle", "queued", "running", "completed", "failed"}


class WorkspaceTeamError(ValueError):
    pass


class WorkspaceTeamStore:
    def __init__(self, session_id: str, *, workspace_id: str | None = None) -> None:
        safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id or "default"))[:120]
        self._path = data_path("outputs", "teams", safe_session, "agent_teams.json")
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
            normalized.append({
                "tool": str(item.get("tool") or item.get("name") or "")[:120],
                "args": item.get("args") if isinstance(item.get("args"), dict) else {},
                "result": str(item.get("result") or item.get("content") or "")[:2000],
                "status": str(item.get("status") or "ok")[:30],
                "elapsed_seconds": item.get("elapsed_seconds"),
                "created_at": str(item.get("created_at") or "")[:40],
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

    def create(self, name: str, description: str, members: list[dict]) -> dict:
        name = self._validate_name(name, "team")
        normalized = []
        seen = set()
        for member in self._coerce_members(members):
            member_name = self._validate_name(str(member.get("name", "")), "member")
            if member_name in seen:
                raise WorkspaceTeamError(f"duplicate member: {member_name}")
            seen.add(member_name)
            normalized.append({
                "name": member_name,
                "agent_id": str(member.get("agent_id") or member_name)[:100],
                "role": str(member.get("role", "analyst"))[:100],
                "instructions": str(member.get("instructions", ""))[:4000],
                "status": "idle",
                "last_message": "",
                "last_active_at": "",
            })
        if not normalized:
            raise WorkspaceTeamError("at least one member is required")
        with _LOCK:
            data = self._load()
            teams = data.setdefault("teams", {})
            now = self._now()
            if name in teams:
                existing = teams[name]
                teams[name] = {
                    "name": name,
                    "description": (description or existing.get("description", ""))[:1000],
                    "members": normalized,
                    "messages": [],
                    "created_at": existing.get("created_at", now),
                    "updated_at": now,
                    "reused": True,
                }
                self._save(data)
                return teams[name]
            teams[name] = {
                "name": name, "description": (description or "")[:1000],
                "members": normalized, "messages": [],
                "created_at": now,
                "updated_at": now,
                "reused": False,
            }
            self._save(data)
            return teams[name]

    def delete(self, name: str, *, require_inactive: bool = False) -> dict:
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
            if require_inactive:
                active = [
                    str(member.get("name") or "")
                    for member in team.get("members", [])
                    if member.get("status") in {"queued", "running"}
                ]
                if active:
                    raise WorkspaceTeamError(
                        "团队成员仍在执行或排队，暂不能解散团队："
                        + ", ".join(active)
                    )
            teams.pop(name, None)
            self._save(data)
        return {"deleted": name}

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
                team.setdefault("messages", []).append(item)
                created.append(item)
                if queue and target in members and item["sender"] != target:
                    members[target]["status"] = "queued"
                    members[target]["last_message"] = item["message"][:500]
                    members[target]["last_active_at"] = self._now()
            team["messages"] = team["messages"][-500:]
            team["updated_at"] = self._now()
            self._save(data)
            return {"sent": len(created), "messages": created, **(created[0] if len(created) == 1 else {})}

    def member(self, team_name: str, member_name: str) -> dict:
        team = self.get(team_name)
        member = next((item for item in team.get("members", []) if item.get("name") == member_name), None)
        if member is None:
            raise WorkspaceTeamError(f"member not found: {member_name}")
        return member

    def begin_member_turn(self, team_name: str, member_name: str) -> dict:
        team_name = self._validate_name(team_name, "team")
        member_name = self._validate_name(member_name, "member")
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(team_name)
            if team is None:
                raise WorkspaceTeamError(f"team not found: {team_name}")
            member = next((item for item in team.get("members", []) if item.get("name") == member_name), None)
            if member is None:
                raise WorkspaceTeamError(f"member not found: {member_name}")
            inbox = []
            for msg in team.get("messages", []):
                if msg.get("recipient") == member_name and not msg.get("read"):
                    msg["read"] = True
                    inbox.append(msg)
            member["status"] = "running"
            member["last_active_at"] = self._now()
            team["updated_at"] = self._now()
            self._save(data)
            return {"team": team_name, "member": dict(member), "inbox": inbox}

    def complete_member_turn(
        self,
        team_name: str,
        member_name: str,
        result: str,
        *,
        ok: bool = True,
        tool_events=None,
    ) -> dict:
        team_name = self._validate_name(team_name, "team")
        member_name = self._validate_name(member_name, "member")
        with _LOCK:
            data = self._load()
            team = data.get("teams", {}).get(team_name)
            if team is None:
                raise WorkspaceTeamError(f"team not found: {team_name}")
            member = next((item for item in team.get("members", []) if item.get("name") == member_name), None)
            if member is None:
                raise WorkspaceTeamError(f"member not found: {member_name}")
            member["status"] = "idle" if ok else "failed"
            member["last_message"] = (result or "")[:500]
            member["last_active_at"] = self._now()
            message = {
                "id": uuid.uuid4().hex[:12],
                "sender": member_name,
                "recipient": LEAD_RECIPIENT,
                "message": (result or "")[:20_000],
                "message_type": "result" if ok else "error",
                "read": False,
                "created_at": self._now(),
                "tool_events": self._normalize_tool_events(tool_events),
            }
            team.setdefault("messages", []).append(message)
            team["messages"] = team["messages"][-500:]
            team["updated_at"] = self._now()
            self._save(data)
            return {"team": team_name, "member": member_name, "status": member["status"], "message": message}

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
                    if self._age_seconds(member.get("last_active_at", "")) <= max_age_seconds:
                        continue
                    member["status"] = "failed"
                    member["last_message"] = "团队成员执行超时，已自动标记失败。"
                    member["last_active_at"] = self._now()
                    team.setdefault("messages", []).append({
                        "id": uuid.uuid4().hex[:12],
                        "sender": member.get("name", ""),
                        "recipient": LEAD_RECIPIENT,
                        "message": "团队成员执行超过 5 分钟仍未完成，已自动标记为失败。",
                        "message_type": "error",
                        "read": False,
                        "created_at": self._now(),
                    })
                    team["messages"] = team["messages"][-500:]
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
