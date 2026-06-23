#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for B1 JobRunner state, durable events and API replay.

Covers:
  - JobsStore CRUD + state transitions + terminal-state protection
  - JobRunner empty job execution + progress reporting + completion
  - JobRunner cancellation (before start + cooperative during run)
  - JobRunner error capture
  - API endpoints (list / get / cancel / test)
"""
import sys
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.jobs_store import (
    JobsStore,
    STATUS_CREATED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_FAILED,
    STATUS_CANCELING,
    STATUS_CANCELED,
    _TERMINAL,
)
from agent.jobs import JobRunner, JobContext, JobCanceled, empty_job
from agent.events import (
    ArtifactCreatedEvent,
    JobCanceledEvent,
    JobCreatedEvent,
    JobDoneEvent,
    JobErrorEvent,
    JobProgressEvent,
    JobStartedEvent,
    serialize_event,
)
from api import create_app
from api.state import session_manager
from agent.agent import BusinessAgent
from data.workspace import workspace_manager
from data.workspace_metadata import WorkspaceMetadataStore


# ── JobsStore ──────────────────────────────────────────────────────────────

class TestJobsStore(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = JobsStore(Path(self._tmp.name) / "jobs.db")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_create_returns_created_job(self):
        job = self.store.create(
            "sess-1", "test_empty", workspace_id="workspace-stable-id"
        )
        self.assertEqual(job["status"], STATUS_CREATED)
        self.assertEqual(job["progress"], 0)
        self.assertEqual(job["type"], "test_empty")
        self.assertEqual(job["session_id"], "sess-1")
        self.assertEqual(job["workspace_id"], "workspace-stable-id")
        self.assertTrue(job["id"])
        self.assertTrue(job["created_at"])

    def test_full_lifecycle_created_queued_started_done(self):
        job = self.store.create("s1", "t")
        jid = job["id"]
        self.store.mark_queued(jid)
        self.store.mark_started(jid)
        self.store.set_progress(jid, 50)
        self.store.mark_done(jid, {"ok": True})
        final = self.store.get(jid)
        self.assertEqual(final["status"], STATUS_SUCCEEDED)
        self.assertEqual(final["progress"], 100)
        self.assertEqual(final["result"], {"ok": True})
        self.assertTrue(final["started_at"])
        self.assertTrue(final["finished_at"])

    def test_error_lifecycle(self):
        job = self.store.create("s1", "t")
        self.store.mark_queued(job["id"])
        self.store.mark_started(jid := job["id"])
        self.store.mark_error(jid, "boom")
        final = self.store.get(jid)
        self.assertEqual(final["status"], STATUS_FAILED)
        self.assertEqual(final["error"], "boom")

    def test_clear_terminal_preserves_active_jobs_and_monotonic_sequence(self):
        done = self.store.create("cleanup-session", "done")
        self.store.mark_queued(done["id"])
        self.store.mark_started(done["id"])
        self.store.mark_succeeded(done["id"], {"ok": True})
        active = self.store.create("cleanup-session", "active")
        before_sequence = self.store.last_sequence("cleanup-session")

        deleted = self.store.clear_terminal("cleanup-session")

        self.assertEqual(deleted, 1)
        self.assertIsNone(self.store.get(done["id"]))
        self.assertIsNotNone(self.store.get(active["id"]))
        self.assertEqual(self.store.last_sequence("cleanup-session"), before_sequence)

    def test_canceled_lifecycle(self):
        job = self.store.create("s1", "t")
        self.store.mark_canceled(jid := job["id"])
        final = self.store.get(jid)
        self.assertEqual(final["status"], STATUS_CANCELED)

    def test_terminal_state_rejects_nonterminal_transition(self):
        """终态 job 不能再变回 started/progress 等非终态。"""
        job = self.store.create("s1", "t")
        jid = job["id"]
        self.store.mark_queued(jid)
        self.store.mark_started(jid)
        self.store.mark_done(jid, "ok")
        # 尝试改回 started — 应被拒绝
        self.store.mark_started(jid)
        self.store.set_progress(jid, 30)
        final = self.store.get(jid)
        self.assertEqual(final["status"], STATUS_SUCCEEDED)
        self.assertEqual(final["progress"], 100)  # 没被改成 30

    def test_set_progress_clamps_0_100(self):
        job = self.store.create("s1", "t")
        jid = job["id"]
        self.store.mark_queued(jid)
        self.store.mark_started(jid)
        self.store.set_progress(jid, 150)
        self.assertEqual(self.store.get(jid)["progress"], 100)
        self.store.set_progress(jid, -10)
        self.assertEqual(self.store.get(jid)["progress"], 0)

    def test_progress_is_field_not_status(self):
        job = self.store.create("s1", "t")
        jid = job["id"]
        self.store.mark_queued(jid)
        self.store.mark_started(jid)
        self.store.set_progress(jid, 42, "working")
        current = self.store.get(jid)
        self.assertEqual(current["status"], STATUS_RUNNING)
        self.assertEqual(current["progress"], 42)
        self.assertEqual(current["message"], "working")

    def test_events_are_persisted_with_strict_session_sequence(self):
        first = self.store.create("s1", "a")
        second = self.store.create("s1", "b")
        self.store.mark_queued(first["id"])
        self.store.mark_started(first["id"])
        self.store.set_progress(first["id"], 50, "half")
        self.store.mark_succeeded(first["id"], {"ok": True})
        events = self.store.list_events("s1")
        self.assertEqual(
            [e["sequence"] for e in events],
            list(range(1, len(events) + 1)),
        )
        self.assertEqual(events[0]["type"], "job_created")
        self.assertEqual(events[1]["job_id"], second["id"])
        self.assertEqual(events[-1]["type"], "job_done")

    def test_event_replay_after_sequence_is_exclusive(self):
        job = self.store.create("s1", "t")
        self.store.mark_queued(job["id"])
        self.store.mark_started(job["id"])
        events = self.store.list_events("s1", after_sequence=1)
        self.assertEqual([e["sequence"] for e in events], [2])
        self.assertEqual(events[0]["type"], "job_started")

    def test_three_events_replay_without_gap_after_reopen(self):
        path = Path(self._tmp.name) / "replay.db"
        first = JobsStore(path)
        job = first.create("replay-session", "analysis")
        first.mark_queued(job["id"])
        first.mark_started(job["id"])
        cursor = first.last_sequence("replay-session")
        first.set_progress(job["id"], 30, "one")
        first.set_progress(job["id"], 60, "two")
        first.mark_succeeded(job["id"], {"ok": True})
        first.close()

        reopened = JobsStore(path)
        try:
            replay = reopened.list_events("replay-session", after_sequence=cursor)
            self.assertEqual(
                [event["type"] for event in replay],
                ["job_progress", "job_progress", "job_done"],
            )
            sequences = [event["sequence"] for event in replay]
            self.assertEqual(sequences, list(range(cursor + 1, cursor + 4)))
        finally:
            reopened.close()

    def test_list_by_session(self):
        self.store.create("s1", "t")
        self.store.create("s1", "t")
        self.store.create("s2", "t")
        self.assertEqual(len(self.store.list_by_session("s1")), 2)
        self.assertEqual(len(self.store.list_by_session("s2")), 1)

    def test_list_active_excludes_terminal(self):
        j1 = self.store.create("s1", "t")
        j2 = self.store.create("s1", "t")
        self.store.mark_queued(j1["id"])
        self.store.mark_started(j1["id"])
        self.store.mark_done(j1["id"], "ok")
        # j2 仍 started
        self.store.mark_queued(j2["id"])
        self.store.mark_started(j2["id"])
        active = self.store.list_active("s1")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], j2["id"])

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.store.get("nonexistent"))

    def test_legacy_status_and_columns_migrate_on_open(self):
        legacy_path = Path(self._tmp.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
            "type TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER DEFAULT 0, "
            "result TEXT, error TEXT, created_at TEXT NOT NULL, started_at TEXT, "
            "finished_at TEXT)"
        )
        connection.execute(
            "INSERT INTO jobs(id, session_id, type, status, created_at) "
            "VALUES ('legacy', 's1', 't', 'progress', '2026-01-01T00:00:00')"
        )
        connection.commit()
        connection.close()
        migrated = JobsStore(legacy_path)
        try:
            job = migrated.get("legacy")
            self.assertEqual(job["status"], STATUS_FAILED)
            self.assertIn("restarted", job["error"])
            self.assertIn("message", job)
            self.assertIn("updated_at", job)
            self.assertEqual(job["workspace_id"], "")
        finally:
            migrated.close()

    def test_reopen_marks_interrupted_job_failed_and_appends_event(self):
        path = Path(self._tmp.name) / "restart.db"
        first = JobsStore(path)
        job = first.create("restart-session", "long")
        first.mark_queued(job["id"])
        first.mark_started(job["id"])
        before = first.last_sequence("restart-session")
        first.close()

        reopened = JobsStore(path)
        try:
            recovered = reopened.get(job["id"])
            self.assertEqual(recovered["status"], STATUS_FAILED)
            self.assertIn("restarted", recovered["error"])
            replay = reopened.list_events("restart-session", after_sequence=before)
            self.assertEqual([event["type"] for event in replay], ["job_error"])
        finally:
            reopened.close()

    def test_event_cleanup_keeps_sequence_monotonic_and_reports_oldest(self):
        for _ in range(120):
            self.store.create("cleanup-session", "event")
        latest = self.store.last_sequence("cleanup-session")
        deleted = self.store.cleanup_events(retention_days=30, max_events_per_session=100)
        self.assertEqual(deleted, 20)
        self.assertEqual(self.store.oldest_sequence("cleanup-session"), 21)
        new_job = self.store.create("cleanup-session", "event")
        self.assertEqual(self.store.last_sequence("cleanup-session"), latest + 1)
        self.assertEqual(
            self.store.list_events("cleanup-session", job_id=new_job["id"])[0]["sequence"],
            latest + 1,
        )


# ── JobRunner ──────────────────────────────────────────────────────────────

class TestJobRunnerExecution(unittest.TestCase):
    """JobRunner 执行/进度/完成/错误/取消。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = JobsStore(Path(self._tmp.name) / "jobs.db")
        self.runner = JobRunner("sess-test", self.store, max_workers=2)

    def tearDown(self):
        self.runner.shutdown(wait=True)
        self.store.close()
        self._tmp.cleanup()

    def test_empty_job_completes_done(self):
        jid = self.runner.create(empty_job, "test_empty")
        self._wait_terminal(jid, timeout=5)
        job = self.runner.get_status(jid)
        self.assertEqual(job["status"], STATUS_SUCCEEDED)
        self.assertEqual(job["progress"], 100)
        self.assertIsInstance(job["result"], dict)
        self.assertIn("ticks", job["result"])

    def test_progress_reported(self):
        """empty_job 会分步上报进度，过程中应能看到 progress 状态。"""
        jid = self.runner.create(
            lambda ctx: empty_job(ctx, duration=0.5), "test_empty"
        )
        # 给一点时间让它进入 progress 状态
        time.sleep(0.15)
        mid = self.runner.get_status(jid)
        # 可能已经 done（快机器），但进度应该 >0
        if mid["status"] != STATUS_SUCCEEDED:
            self.assertEqual(mid["status"], STATUS_RUNNING)
            self.assertGreater(mid["progress"], 0)
        self._wait_terminal(jid, timeout=5)
        self.assertEqual(self.runner.get_status(jid)["status"], STATUS_SUCCEEDED)

    def test_job_error_captured(self):
        def failing(ctx):
            raise ValueError("intentional failure")
        jid = self.runner.create(failing, "test_fail")
        self._wait_terminal(jid, timeout=5)
        job = self.runner.get_status(jid)
        self.assertEqual(job["status"], STATUS_FAILED)
        self.assertIn("intentional failure", job["error"])

    def test_cancel_cooperative(self):
        """job 函数主动检查 ctx.check_canceled() 并退出 → 标记 canceled。"""
        def long_job(ctx):
            for i in range(100):
                ctx.check_canceled()
                ctx.set_progress(i)
                time.sleep(0.02)
            return "finished"
        jid = self.runner.create(long_job, "test_long")
        time.sleep(0.05)  # 让它先跑起来
        accepted = self.runner.cancel(jid)
        self.assertTrue(accepted)
        self._wait_terminal(jid, timeout=5)
        job = self.runner.get_status(jid)
        self.assertEqual(job["status"], STATUS_CANCELED)

    def test_cancel_terminal_returns_false(self):
        jid = self.runner.create(empty_job, "test_empty")
        self._wait_terminal(jid, timeout=5)
        # 已 done，cancel 应返回 False
        accepted = self.runner.cancel(jid)
        self.assertFalse(accepted)

    def test_iter_events_yields_created_through_terminal(self):
        jid = self.runner.create(
            lambda ctx: empty_job(ctx, duration=0.1), "test_events"
        )
        events = list(self.runner.iter_events(jid, timeout=5))
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "job_created")
        self.assertIn("job_started", types)
        self.assertIn("job_progress", types)
        self.assertEqual(types[-1], "job_done")
        sequences = [event["sequence"] for event in events]
        self.assertEqual(sequences, sorted(set(sequences)))

    def test_runner_cannot_read_another_session_job(self):
        other = JobRunner("other-session", self.store, max_workers=1)
        try:
            jid = other.create(empty_job, "other")
            self.assertIsNone(self.runner.get_status(jid))
            self.assertFalse(self.runner.cancel(jid))
        finally:
            other.shutdown(wait=True)

    def test_artifact_event_is_persisted(self):
        jid = self.runner.create(
            lambda ctx: (ctx.artifact_created({"filename": "report.pptx"}) or "ok"),
            "artifact_job",
        )
        self._wait_terminal(jid)
        events = self.runner.list_events(job_id=jid)
        artifact = next(event for event in events if event["type"] == "artifact_created")
        self.assertEqual(artifact["artifact"]["filename"], "report.pptx")

    def test_agent_bridge_yields_events_and_returns_terminal_job(self):
        agent = object.__new__(BusinessAgent)
        agent._job_runner = self.runner
        agent._active_job_id = ""
        bridge = agent._run_as_job(
            lambda ctx: empty_job(ctx, duration=0.1),
            job_type="bridge_test",
            label="Bridge test",
        )
        events = []
        while True:
            try:
                events.append(next(bridge))
            except StopIteration as stop:
                terminal = stop.value
                break
        self.assertEqual(events[0]["type"], "job_created")
        self.assertEqual(events[-1]["type"], "job_done")
        self.assertEqual(terminal["status"], STATUS_SUCCEEDED)

    def test_closing_agent_bridge_requests_cancellation(self):
        agent = object.__new__(BusinessAgent)
        agent._job_runner = self.runner
        agent._active_job_id = ""
        bridge = agent._run_as_job(
            lambda ctx: empty_job(ctx, duration=1.0),
            job_type="bridge_cancel",
        )
        created = next(bridge)
        jid = created["job_id"]
        bridge.close()
        self._wait_terminal(jid)
        self.assertEqual(self.runner.get_status(jid)["status"], STATUS_CANCELED)

    def test_list_jobs_scoped_to_session(self):
        self.runner.create(empty_job, "t1")
        self.runner.create(empty_job, "t2")
        time.sleep(0.5)
        jobs = self.runner.list_jobs()
        self.assertEqual(len(jobs), 2)
        active = self.runner.list_jobs(active_only=True)
        # 都已完成
        self.assertEqual(len(active), 0)

    def _wait_terminal(self, jid: str, timeout: float = 5.0) -> None:
        """轮询直到 job 进入终态或超时。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.runner.get_status(jid)
            if job and job["status"] in _TERMINAL:
                return
            time.sleep(0.02)
        job = self.runner.get_status(jid)
        self.fail(f"job {jid} did not reach terminal state within {timeout}s "
                  f"(last status: {job['status'] if job else 'missing'})")


# ── C3 workspace leases ────────────────────────────────────────────────────

class TestWorkspaceJobLeases(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        self.original_metadata_store = workspace_manager.metadata_store
        workspace_manager.metadata_store = WorkspaceMetadataStore(
            self.root / "global-index.json"
        )
        self.sid = f"lease-{time.time_ns()}"
        ok, message, self.runtime = workspace_manager.mount(
            self.sid, str(self.workdir), "read_write"
        )
        self.assertTrue(ok, message)
        self.store = JobsStore(self.root / "jobs.db")
        self.runner = JobRunner(self.sid, self.store, max_workers=1)

    def tearDown(self):
        self.runner.shutdown(wait=True)
        workspace_manager.unmount(self.sid)
        workspace_manager.metadata_store = self.original_metadata_store
        self.store.close()
        self.tmp.cleanup()

    def _wait_terminal(self, jid, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.runner.get_status(jid)
            if job and job["status"] in _TERMINAL:
                return job
            time.sleep(0.02)
        self.fail(f"job {jid} did not finish")

    def test_job_freezes_workspace_and_keeps_runtime_after_session_unmount(self):
        started = threading.Event()
        finish = threading.Event()

        def worker(ctx):
            self.assertEqual(ctx.workspace_id, self.runtime.workspace_id)
            self.assertIs(ctx.runtime, self.runtime)
            started.set()
            finish.wait(3)
            return {"workspace_id": ctx.workspace_id}

        jid = self.runner.create(worker, "workspace_job")
        self.assertTrue(started.wait(2))
        job = self.runner.get_status(jid)
        self.assertEqual(job["workspace_id"], self.runtime.workspace_id)
        self.assertEqual(self.runtime.job_ref_count, 1)

        workspace_manager.unmount(self.sid)
        self.assertEqual(self.runtime.session_ref_count, 0)
        self.assertIs(
            workspace_manager.get_by_workspace(self.runtime.workspace_id), self.runtime
        )
        finish.set()
        self.assertEqual(self._wait_terminal(jid)["status"], STATUS_SUCCEEDED)
        self.assertIsNone(workspace_manager.get_by_workspace(self.runtime.workspace_id))

    def test_child_job_inherits_parent_workspace_after_session_unbind(self):
        parent_id = self.runner.begin_tracked("conversation_analysis", "parent")
        workspace_manager.unmount(self.sid)
        with self.runner.conversation_scope(parent_id):
            child_id = self.runner.create(lambda ctx: ctx.workspace_id, "child")
        child = self._wait_terminal(child_id)
        self.assertEqual(child["workspace_id"], self.runtime.workspace_id)
        self.assertEqual(child["result"], self.runtime.workspace_id)
        self.assertEqual(self.runtime.job_ref_count, 1)
        self.runner.succeed_tracked(parent_id, {"ok": True})
        self.assertIsNone(workspace_manager.get_by_workspace(self.runtime.workspace_id))


# ── JobContext ─────────────────────────────────────────────────────────────

class TestJobContext(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = JobsStore(Path(self._tmp.name) / "jobs.db")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_check_canceled_raises(self):
        job = self.store.create("s1", "t")
        ctx = JobContext(job["id"], self.store, lambda jid: True)
        with self.assertRaises(JobCanceled):
            ctx.check_canceled()

    def test_check_canceled_passes_when_not_canceled(self):
        job = self.store.create("s1", "t")
        ctx = JobContext(job["id"], self.store, lambda jid: False)
        ctx.check_canceled()  # should not raise

    def test_set_progress_updates_store(self):
        job = self.store.create("s1", "t")
        self.store.mark_queued(job["id"])
        self.store.mark_started(job["id"])
        ctx = JobContext(job["id"], self.store, lambda jid: False)
        ctx.set_progress(42)
        self.assertEqual(self.store.get(job["id"])["progress"], 42)


class TestTypedJobEvents(unittest.TestCase):

    def test_all_protocol_events_serialize_with_stable_type(self):
        events = [
            JobCreatedEvent(job_id="j", job_type="test"),
            JobStartedEvent(job_id="j"),
            JobProgressEvent(job_id="j", job_type="test", progress=25),
            ArtifactCreatedEvent(job_id="j", artifact={"filename": "x"}),
            JobDoneEvent(job_id="j", result={"ok": True}),
            JobErrorEvent(job_id="j", error="boom"),
            JobCanceledEvent(job_id="j"),
        ]
        self.assertEqual(
            [serialize_event(event)["type"] for event in events],
            [
                "job_created", "job_started", "job_progress",
                "artifact_created", "job_done", "job_error", "job_canceled",
            ],
        )


# ── API endpoints ──────────────────────────────────────────────────────────

class TestJobsAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = cls.app.test_client()

    @staticmethod
    def _create_job(sid: str, duration: float = 0.1):
        return session_manager.get_or_create(sid).job_runner.create(
            lambda ctx: empty_job(ctx, duration), "test_empty"
        )

    def test_query_real_runner_job(self):
        jid = self._create_job("test-sid")

        # 轮询直到 done
        for _ in range(50):
            r2 = self.client.get(f"/api/session/test-sid/jobs/{jid}")
            self.assertEqual(r2.status_code, 200)
            job = r2.get_json()["job"]
            if job["status"] == STATUS_SUCCEEDED:
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], STATUS_SUCCEEDED)

    def test_events_endpoint_replays_after_sequence(self):
        sid = "events-sid"
        jid = self._create_job(sid)
        for _ in range(100):
            job = self.client.get(f"/api/session/{sid}/jobs/{jid}").get_json()["job"]
            if job["status"] in _TERMINAL:
                break
            time.sleep(0.02)
        first = self.client.get(f"/api/session/{sid}/jobs/events?limit=2")
        self.assertEqual(first.status_code, 200)
        first_data = first.get_json()
        self.assertEqual(len(first_data["events"]), 2)
        second = self.client.get(
            f"/api/session/{sid}/jobs/events?after_sequence={first_data['next_sequence']}"
        )
        second_data = second.get_json()
        self.assertTrue(second_data["events"])
        self.assertGreater(
            second_data["events"][0]["sequence"], first_data["next_sequence"]
        )
        self.assertIn("oldest_sequence", second_data)
        self.assertFalse(second_data["replay_truncated"])

    def test_get_missing_job_404(self):
        r = self.client.get("/api/session/test-sid/jobs/nonexistent")
        self.assertEqual(r.status_code, 404)

    def test_list_jobs_returns_array(self):
        r = self.client.get("/api/session/test-sid/jobs")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("jobs", data)
        self.assertIsInstance(data["jobs"], list)

    def test_clear_completed_history_keeps_running_jobs(self):
        sid = f"clear-jobs-{time.time_ns()}"
        runner = session_manager.get_or_create(sid).job_runner
        finished = runner.create(lambda ctx: {"ok": True}, "finished")
        for _ in range(100):
            if runner.get_status(finished)["status"] in _TERMINAL:
                break
            time.sleep(0.01)
        running = runner.create(lambda ctx: time.sleep(0.3), "running")

        response = self.client.delete(f"/api/session/{sid}/jobs")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.get_json()["deleted"], 1)
        self.assertIsNone(runner.get_status(finished))
        self.assertIsNotNone(runner.get_status(running))

    def test_cancel_terminal_returns_409(self):
        # 先创建一个会很快完成的 job
        jid = self._create_job("test-sid2", duration=0.05)
        # 等它完成
        for _ in range(50):
            r2 = self.client.get(f"/api/session/test-sid2/jobs/{jid}")
            if r2.get_json()["job"]["status"] in _TERMINAL:
                break
            time.sleep(0.02)
        # 取消已完成的 → 409
        r3 = self.client.post(f"/api/session/test-sid2/jobs/{jid}/cancel")
        self.assertEqual(r3.status_code, 409)

    def test_cancel_missing_404(self):
        r = self.client.post("/api/session/test-sid/jobs/nonexistent/cancel")
        self.assertEqual(r.status_code, 404)

    def test_legacy_jobtest_endpoint_is_removed(self):
        r = self.client.post("/api/session/test-sid/jobs/test", json={"duration": 0.1})
        self.assertEqual(r.status_code, 405)


if __name__ == "__main__":
    unittest.main(verbosity=2)
