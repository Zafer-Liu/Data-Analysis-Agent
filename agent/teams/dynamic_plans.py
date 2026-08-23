"""Bounded dynamic team plans recorded around existing team_delegate execution."""
from __future__ import annotations
import json, os, re, threading, time, uuid
from datetime import datetime
from infrastructure.paths import data_path
from data.workspace import workspace_manager
from agent.tools.workspace.teams import WorkspaceTeamError
from agent.workflows.service import WorkflowService

_LOCK=threading.RLock()
TERMINAL={"completed","failed","canceled"}

class DynamicTeamPlanStore:
    def __init__(self, session_id, workspace_id=None):
        self.session_id=str(session_id or "")
        safe=re.sub(r"[^A-Za-z0-9_.-]+","_",self.session_id or "default")[:120]
        self.path=data_path("outputs","teams",safe,"dynamic_plans.json")
        self.workspace_id=str(workspace_id if workspace_id is not None else workspace_manager.workspace_id_for_session(self.session_id) or "")

    def _now(self): return datetime.now().isoformat(timespec="seconds")
    def _load(self):
        if not self.path.exists(): return {"workspace_id":self.workspace_id,"plans":[]}
        try: data=json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc: raise WorkspaceTeamError(f"dynamic plan store unreadable: {exc}") from exc
        if data.get("workspace_id") and self.workspace_id and data["workspace_id"]!=self.workspace_id:
            raise WorkspaceTeamError("dynamic plan workspace mismatch")
        data.setdefault("plans",[])
        return data
    def _save(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        # A fixed ``dynamic_plans.tmp`` races with other writers on Windows.
        # Keep the replacement atomic while giving transient file locks a short retry.
        tmp=self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
            for attempt in range(3):
                try:
                    os.replace(tmp,self.path)
                    return
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            if tmp.exists():
                tmp.unlink()
    @staticmethod
    def _normalize_usage(usage):
        usage=usage if isinstance(usage,dict) else {}
        def count(key):
            try: return max(0,int(usage.get(key) or 0))
            except (TypeError,ValueError): return 0
        return {
            "model":str(usage.get("model") or "")[:160],
            "provider":str(usage.get("provider") or "")[:120],
            "input_tokens":count("input_tokens"),
            "output_tokens":count("output_tokens"),
            "cached_input_tokens":count("cached_input_tokens"),
            "tool_calls":count("tool_calls"),
        }

    def _refresh_budget(self,plan):
        task_usages=[self._normalize_usage(item) for task in plan.get("tasks",[]) for item in (task.get("usage_history") or [task.get("usage")]) ]
        current_task_usages=[self._normalize_usage(task.get("usage")) for task in plan.get("tasks",[])]
        review_usages=[self._normalize_usage(item) for item in (plan.get("quality_review_usage_history") or [plan.get("quality_review_usage")]) ]
        all_usages=task_usages+review_usages
        measured=lambda item:any(item[key] for key in ("input_tokens","output_tokens","cached_input_tokens","tool_calls"))
        plan["budget"]={
            "input_tokens":sum(item["input_tokens"] for item in all_usages),
            "output_tokens":sum(item["output_tokens"] for item in all_usages),
            "cached_input_tokens":sum(item["cached_input_tokens"] for item in all_usages),
            "tool_calls":sum(item["tool_calls"] for item in all_usages),
            "measured_task_count":sum(1 for item in current_task_usages if measured(item)),
            "task_count":len(current_task_usages),
            "child_job_count":sum(len(task.get("job_ids") or ([task["job_id"]] if task.get("job_id") else [])) for task in plan.get("tasks",[])),
            "quality_review_measured":any(measured(item) for item in review_usages),
            "estimated_cost":None,
        }
        return plan["budget"]


    def create(self,team_name,goal,assignments,created_by="lead",status="running"):
        if status not in {"planned","running"}: raise WorkspaceTeamError("invalid dynamic plan status")
        if not 1<=len(assignments)<=8: raise WorkspaceTeamError("dynamic plan requires 1-8 tasks")
        now=self._now(); seen=set(); tasks=[]
        for i,item in enumerate(assignments,1):
            tid=re.sub(r"[^A-Za-z0-9_.-]+","_",str(item.get("task_id") or f"task_{i}"))[:64]
            if not tid or tid in seen: raise WorkspaceTeamError("duplicate dynamic task id")
            seen.add(tid)
            tasks.append({"id":tid,"member_name":str(item.get("member_name") or "")[:64],
              "title":str(item.get("description") or tid)[:240],"prompt":str(item.get("prompt") or "")[:12000],
              "depends_on":[str(x)[:64] for x in item.get("depends_on") or []][:8],
              "status":"pending","attempt":1,"job_id":"","result_summary":"","error":"","tool_count":0,"artifacts":[],"usage":self._normalize_usage({}),"usage_history":[],"job_ids":[],
              "started_at":"","finished_at":"","updated_at":now})
        if any(not t["member_name"] or not t["prompt"] or t["id"] in t["depends_on"] or any(x not in seen for x in t["depends_on"]) for t in tasks):
            raise WorkspaceTeamError("invalid dynamic task")
        dependencies={task["id"]:set(task["depends_on"]) for task in tasks}
        resolved=set()
        while dependencies:
            ready=[task_id for task_id, deps in dependencies.items() if deps.issubset(resolved)]
            if not ready:
                raise WorkspaceTeamError("dynamic task dependencies contain a cycle")
            for task_id in ready:
                resolved.add(task_id)
                dependencies.pop(task_id)
        plan={"id":"tp_"+uuid.uuid4().hex[:16],"workspace_id":self.workspace_id,"team_name":str(team_name)[:64],
          "goal":str(goal or "团队动态协作")[:4000],"status":status,"created_by":str(created_by)[:120],
          "tasks":tasks,"quality_review_usage":self._normalize_usage({}),"quality_review_usage_history":[],"created_at":now,"updated_at":now,"finished_at":"","workflow_draft_id":""}
        self._refresh_budget(plan)
        with _LOCK:
            data=self._load(); data["workspace_id"]=self.workspace_id; data["plans"].append(plan); data["plans"]=data["plans"][-100:]; self._save(data)
        return plan
    def start(self,pid):
        with _LOCK:
            data=self._load(); plan=next((p for p in data["plans"] if p["id"]==pid),None)
            if not plan: raise WorkspaceTeamError(f"dynamic plan not found: {pid}")
            if plan["status"]!="planned": raise WorkspaceTeamError("cannot start dynamic plan unless status is planned")
            plan["status"]="running"; plan["updated_at"]=self._now(); self._save(data); return plan

    def list(self,team_name=""):
        with _LOCK: plans=self._load()["plans"]
        if team_name: plans=[p for p in plans if p["team_name"]==team_name]
        return sorted(plans,key=lambda p:p["created_at"],reverse=True)
    def get(self,pid):
        with _LOCK: plan=next((p for p in self._load()["plans"] if p["id"]==pid),None)
        if not plan: raise WorkspaceTeamError(f"dynamic plan not found: {pid}")
        return plan
    def task(self,pid,tid,status,result="",error="",tool_events=None,job_id="",artifacts=None,usage=None):
        with _LOCK:
            data=self._load(); plan=next((p for p in data["plans"] if p["id"]==pid),None)
            if not plan: raise WorkspaceTeamError(f"dynamic plan not found: {pid}")
            task=next((t for t in plan["tasks"] if t["id"]==tid),None)
            if plan["status"]=="canceled": return task
            if not task: raise WorkspaceTeamError(f"dynamic task not found: {tid}")
            now=self._now(); task["status"]=status; task["updated_at"]=now
            if job_id:
                task["job_id"]=str(job_id)[:160]
                job_ids=task.setdefault("job_ids",[])
                if task["job_id"] not in job_ids: job_ids.append(task["job_id"])
            if artifacts is not None: task["artifacts"]=[dict(item) for item in artifacts if isinstance(item,dict)][:20]
            if usage is not None:
                task["usage"]=self._normalize_usage(usage)
                if status in TERMINAL:
                    task.setdefault("usage_history",[]).append({"attempt":int(task.get("attempt") or 1),**task["usage"]})
            if status=="running": task["started_at"]=task["started_at"] or now
            if status in TERMINAL: task["finished_at"]=now
            task["result_summary"]=str(result)[:500]; task["error"]=str(error)[:2000]
            if tool_events is not None:
                events=[event for event in (tool_events or []) if isinstance(event,dict)][:30]
                task["tool_count"]=len(events)
                task["tool_evidence"]=[{
                    "tool":str(event.get("tool") or "")[:120],
                    "status":str(event.get("status") or "ok")[:30],
                    "elapsed_seconds":event.get("elapsed_seconds"),
                    "created_at":str(event.get("created_at") or "")[:40],
                } for event in events if event.get("tool")]
            self._refresh_budget(plan)
            plan["updated_at"]=now; self._save(data); return task
    def record_quality_review_usage(self,pid,usage):
        with _LOCK:
            data=self._load(); plan=next((p for p in data["plans"] if p["id"]==pid),None)
            if not plan: raise WorkspaceTeamError(f"dynamic plan not found: {pid}")
            if plan["status"]=="canceled": return plan
            plan["quality_review_usage"]=self._normalize_usage(usage)
            plan.setdefault("quality_review_usage_history",[]).append(plan["quality_review_usage"])
            self._refresh_budget(plan); plan["updated_at"]=self._now(); self._save(data); return plan

    def prepare_retry(self,pid,task_ids):
        requested={str(item) for item in (task_ids or []) if str(item)}
        with _LOCK:
            data=self._load(); plan=next((p for p in data["plans"] if p["id"]==pid),None)
            if not plan: raise WorkspaceTeamError(f"dynamic plan not found: {pid}")
            if plan["status"] in {"completed","canceled"}:
                raise WorkspaceTeamError(f"cannot retry dynamic plan in {plan['status']}")
            targets=[task for task in plan["tasks"] if not requested or task["id"] in requested]
            if not targets or any(task["status"]!="failed" for task in targets):
                raise WorkspaceTeamError("only failed dynamic tasks can be retried")
            now=self._now()
            for task in targets:
                task["status"]="pending"; task["attempt"]=int(task.get("attempt") or 1)+1
                task["job_id"]=""; task["usage"]=self._normalize_usage({}); task["result_summary"]=""; task["error"]=""
                task["artifacts"]=[]; task["finished_at"]=""; task["updated_at"]=now
            plan["status"]="running"; plan["finished_at"]=""; plan["updated_at"]=now
            self._save(data)
            return plan, [task["id"] for task in targets]

    def prepare_review_retry(self,pid,task_ids):
        requested={str(item) for item in (task_ids or []) if str(item)}
        with _LOCK:
            data=self._load(); plan=next((p for p in data["plans"] if p["id"]==pid),None)
            if not plan: raise WorkspaceTeamError(f"dynamic plan not found: {pid}")
            if plan["status"]!="needs_review": raise WorkspaceTeamError("only review-blocked plans can be revised")
            tasks={task["id"]:task for task in plan["tasks"]}
            if not requested or not requested.issubset(tasks): raise WorkspaceTeamError("review retry requires existing task ids")
            selected=set(requested); changed=True
            while changed:
                changed=False
                for task in tasks.values():
                    if task["id"] not in selected and any(dep in selected for dep in task.get("depends_on") or []):
                        selected.add(task["id"]); changed=True
            now=self._now()
            for task_id in selected:
                task=tasks[task_id]; task["status"]="pending"; task["attempt"]=int(task.get("attempt") or 1)+1
                task["job_id"]=""; task["usage"]=self._normalize_usage({}); task["result_summary"]=""; task["error"]=""
                task["artifacts"]=[]; task["finished_at"]=""; task["updated_at"]=now
            plan["status"]="running"; plan["review_status"]="revising"; plan["finished_at"]=""; plan["updated_at"]=now
            self._save(data); return plan, [task["id"] for task in plan["tasks"] if task["id"] in selected]

    def finalize(self,pid,review_blocked=False,review_summary=""):
        with _LOCK:
            data=self._load(); plan=next((p for p in data["plans"] if p["id"]==pid),None)
            if not plan: raise WorkspaceTeamError(f"dynamic plan not found: {pid}")
            statuses=[t["status"] for t in plan["tasks"]]
            if plan["status"]!="canceled":
                plan["status"]=("needs_review" if review_blocked else ("completed" if statuses and all(x=="completed" for x in statuses) else "partial_failed"))
                plan["review_status"]="blocked" if review_blocked else "passed"
                plan["review_summary"]=str(review_summary or "")[:500]
            plan["finished_at"]=plan["updated_at"]=self._now(); self._save(data); return plan
    def control(self,pid,action):
        with _LOCK:
            data=self._load(); plan=next((p for p in data["plans"] if p["id"]==pid),None)
            if not plan: raise WorkspaceTeamError(f"dynamic plan not found: {pid}")
            if action=="cancel" and plan["status"] not in {"completed","partial_failed","canceled"}:
                plan["status"]="canceled"
                for t in plan["tasks"]:
                    if t["status"] not in TERMINAL: t["status"]="canceled"; t["finished_at"]=self._now()
            else: raise WorkspaceTeamError(f"cannot {action} plan in {plan['status']}")
            plan["updated_at"]=self._now(); self._save(data); return plan
    def create_workflow_draft(self,pid,created_by="teams_panel"):
        plan=self.get(pid)
        if plan["status"]!="completed": raise WorkspaceTeamError("only completed plans can become Workflow drafts")
        # Dynamic-team output can be promoted to a Workflow even when the
        # session has no user-mounted folder. The manager supplies the same
        # isolated Workflow runtime used by the Workflow API in that case.
        runtime=workspace_manager.workflow_runtime_for_session(self.session_id)
        suffix=uuid.uuid4().hex[:8]; profiles={}; nodes=[]; edges=[]; outputs={}
        with WorkflowService(runtime) as service:
            for t in plan["tasks"]:
                member=t["member_name"]
                if member not in profiles:
                    profiles[member]=service.create_agent_profile(key=f"dynamic_{suffix}_{len(profiles)+1}",name=member,
                      role="dynamic_team_member",instructions=f"执行有界动态团队任务。目标：{plan['goal']}",
                      allowed_tools=["get_schema","profile_data","query_data","query_knowledge","workspace_read_file","read_tool_result"],
                      model_policy="inherit",created_by=created_by)["id"]
                out=f"{t['id']}_result"; inputs=["goal"]+[f"{d}_result" for d in t["depends_on"]]
                nodes.append({"node_id":t["id"],"type":"agent","agent_profile_id":profiles[member],
                  "input_contract":inputs,"output_contract":[out],"task_prompt":t["prompt"]})
                outputs[out]={"type":"string"}
                for dep in t["depends_on"]: edges.append({"edge_id":f"{dep}-to-{t['id']}","from_node":dep,"to_node":t["id"],"type":"auto"})
            depended={d for t in plan["tasks"] for d in t["depends_on"]}
            terminal={f"{t['id']}_result":{"type":"string"} for t in plan["tasks"] if t["id"] not in depended} or outputs
            workflow=service.create_workflow(name=f"{plan['team_name']} · 动态协作草稿",
              description=f"由动态计划 {pid} 的成功路径人工保存。目标：{plan['goal']}",
              graph={"entry_node_ids":[t["id"] for t in plan["tasks"] if not t["depends_on"]],"nodes":nodes,"edges":edges,
                "limits":{"max_run_minutes":120,"max_total_node_runs":30}},
              input_schema={"type":"object","properties":{"goal":{"type":"string"}},"required":["goal"]},
              output_schema={"type":"object","properties":terminal,"required":list(terminal)},created_by=created_by)
        with _LOCK:
            data=self._load(); stored=next(p for p in data["plans"] if p["id"]==pid); stored["workflow_draft_id"]=workflow["id"]; self._save(data)
        return workflow
