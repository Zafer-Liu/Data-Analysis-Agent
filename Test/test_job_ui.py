#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static contracts for the B1.5 Vue job progress UI."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestJobUiContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stream_js = (ROOT / "static/js/modules/chat_stream.js").read_text(encoding="utf-8")
        cls.vue_js = (ROOT / "static/js/modules/vue_app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "static/css/parts/chat.css").read_text(encoding="utf-8")
        cls.slash_js = (ROOT / "static/js/modules/slash.js").read_text(encoding="utf-8")
        cls.skills_js = (ROOT / "static/js/modules/skills.js").read_text(encoding="utf-8")
        cls.models_js = (ROOT / "static/js/modules/models.js").read_text(encoding="utf-8")
        cls.workspace_js = (ROOT / "static/js/modules/workspace.js").read_text(encoding="utf-8")
        cls.preview_js = (ROOT / "static/js/modules/preview.js").read_text(encoding="utf-8")
        cls.history_js = (ROOT / "static/js/modules/job_history.js").read_text(encoding="utf-8")
        cls.checkpoint_js = (ROOT / "static/js/modules/checkpoints.js").read_text(encoding="utf-8")
        cls.modal_css = (ROOT / "static/css/parts/modals.css").read_text(encoding="utf-8")
        cls.template = (ROOT / "templates/agent_chat.html").read_text(encoding="utf-8")

    def test_all_seven_job_events_have_named_handlers(self):
        expected = {
            "job_created": "_onJobCreated",
            "job_started": "_onJobStarted",
            "job_progress": "_onJobProgress",
            "artifact_created": "_onArtifactCreated",
            "job_done": "_onJobDone",
            "job_error": "_onJobError",
            "job_canceled": "_onJobCanceled",
        }
        for event_type, handler in expected.items():
            self.assertIn(f"{event_type}:".ljust(20) + handler, self.stream_js)

    def test_vue_island_owns_job_card_and_cancel_action(self):
        self.assertIn('h("div", { class: "job-list" })', self.vue_js)
        self.assertIn("function updateJob", self.vue_js)
        self.assertIn("jobs.map(_renderJobCard)", self.vue_js)
        self.assertIn('class: "job-cancel-btn"', self.vue_js)

    def test_conversation_history_renders_expandable_tool_steps_and_answer(self):
        self.assertIn('conversation_analysis', self.vue_js)
        self.assertIn('conversation_step_started', self.vue_js)
        self.assertIn('conversation_step_finished', self.vue_js)
        self.assertIn('job-history-expand', self.vue_js)
        self.assertIn('job-history-step-duration', self.vue_js)
        self.assertIn('job-history-answer', self.vue_js)
        self.assertIn("updateJob,", self.vue_js)

    def test_cancel_calls_b1_endpoint(self):
        self.assertIn("function _cancelJob(jobId)", self.stream_js)
        self.assertIn("/jobs/${encodeURIComponent(jobId)}/cancel", self.stream_js)

    def test_progress_card_has_accessible_progressbar_and_states(self):
        self.assertIn('role: "progressbar"', self.vue_js)
        self.assertIn('"aria-valuenow": String(progress)', self.vue_js)
        for state in ("running", "succeeded", "failed", "canceled"):
            self.assertIn(f".job-card-{state}", self.css)

    def test_messages_submitted_while_streaming_enter_fifo(self):
        self.assertIn("function _enqueueTurn", self.stream_js)
        self.assertIn("state.pendingMessages.push(item)", self.stream_js)
        self.assertIn("function _drainMessageQueue", self.stream_js)
        self.assertIn("state.pendingMessages.shift()", self.stream_js)

    def test_queued_turn_uses_vue_placeholder_and_can_be_canceled(self):
        self.assertIn("function setTurnQueueState", self.vue_js)
        self.assertIn("function _renderTurnQueueState", self.vue_js)
        self.assertIn('class: "turn-queue-cancel"', self.vue_js)
        self.assertIn("function _cancelQueued", self.stream_js)

    def test_jobtest_command_was_not_added(self):
        self.assertNotIn('{ cmd: "jobtest"', self.slash_js)
        self.assertNotIn('state.activeCommand === "jobtest"', self.stream_js)

    def test_changed_assets_have_composer_cache_buster(self):
        asset_versions = {
            "filename='js/i18n.js'": "v='c4-4-ui-1'",
            "filename='js/modules/vue_app.js'": "v='c4-4-ui-1'",
            "filename='js/modules/chat_stream.js'": "v='s4p1'",
        }
        for asset, version in asset_versions.items():
            line = next(line for line in self.template.splitlines() if asset in line)
            self.assertIn(version, line)
        history_line = next(
            line for line in self.template.splitlines()
            if "filename='js/modules/job_history.js'" in line
        )
        self.assertIn("v='ui-dialog-1'", history_line)

    def test_queued_message_has_send_now_edit_and_real_delete_actions(self):
        self.assertIn("function _sendQueuedNow", self.stream_js)
        self.assertIn("function _editQueued", self.stream_js)
        self.assertIn("await stopStreaming()", self.stream_js)
        self.assertIn("setMessageText", self.vue_js)
        self.assertIn("removeMessages", self.vue_js)
        self.assertIn("composer-queue-send-now", self.vue_js)
        self.assertIn("composer-queue-edit", self.vue_js)
        self.assertIn("composer-queue-delete", self.vue_js)
        self.assertNotIn('}, "⌫")', self.vue_js)

    def test_composer_matches_queue_editor_toolbar_structure(self):
        self.assertIn('id="composer-queue-root"', self.template)
        self.assertIn('class="composer-toolbar"', self.template)
        self.assertIn('class="composer-model"', self.template)
        self.assertIn('data-action="openSkillPicker"', self.template)
        self.assertIn("function renderComposerQueue", self.vue_js)
        self.assertIn("renderComposerQueue,", self.vue_js)
        self.assertNotIn('class="composer-tool composer-mode"', self.template)
        self.assertIn('id="workspace-permission-select"', self.template)
        self.assertIn('id="composer-expand-btn"', self.template)
        self.assertIn('id="model-sel-sidebar"', self.template)
        self.assertIn('class="composer-toolbar-actions"', self.template)
        actions = self.template.split('class="composer-toolbar-actions"', 1)[1].split("</div>", 3)
        action_markup = "</div>".join(actions)
        self.assertLess(action_markup.index('id="token-bar-wrap"'), action_markup.index('id="composer-expand-btn"'))
        self.assertLess(action_markup.index('id="composer-expand-btn"'), action_markup.index('id="send-btn"'))

    def test_skill_and_command_frontend_entries_are_separate(self):
        self.assertIn('fetch(`/api/commands${suffix}`)', self.slash_js)
        self.assertNotIn('/api/skills', self.slash_js)
        self.assertIn('fetch(`/api/skills${suffix}`)', self.skills_js)
        self.assertIn('id="skill-picker"', self.template)
        self.assertIn('id="skill-badge"', self.template)
        self.assertIn('id="cmd-badge"', self.template)
        self.assertIn('payload.skill = selectedSkill', self.stream_js)
        self.assertIn('internal_action: meta.confirmCmd', self.stream_js)
        self.assertIn('internal_action: meta.reviseCmd', self.stream_js)
        self.assertIn('item.payload.skill', self.stream_js)
        self.assertIn('job-activation-${activation.kind}', self.vue_js)

    def test_sidebar_and_composer_models_are_bidirectionally_synced(self):
        self.assertIn('$("model-sel-sidebar")', self.models_js)
        self.assertIn("function _syncModelSelectors", self.models_js)
        self.assertIn("_syncModelSelectors(v)", self.models_js)

    def test_unmounted_permission_opens_mount_flow_instead_of_disabling(self):
        self.assertNotIn('id="workspace-permission-select" disabled', self.template)
        self.assertIn('select.dataset.mounted !== "1"', self.workspace_js)
        self.assertIn("openModal(permission)", self.workspace_js)

    def test_preview_table_selection_is_gated_by_backend_sql_metadata(self):
        self.assertIn("state._previewData?.requires_table_selection", self.preview_js)
        self.assertIn("if (tb.selectable_for_analysis)", self.preview_js)
        self.assertNotIn("source_name.includes", self.preview_js)

    def test_b5_history_panel_is_vue_owned(self):
        self.assertIn('id="job-history-root"', self.template)
        self.assertIn("window.BAA.vueJobHistory", self.vue_js)
        self.assertIn("function applyEvent(ev)", self.vue_js)
        self.assertIn("job-history-modal", self.modal_css)

    def test_destructive_history_action_uses_app_confirm_dialog(self):
        self.assertIn("window.BAA.ui?.confirm", self.history_js)
        self.assertNotIn("window.confirm", self.history_js)
        self.assertIn("function renderConfirm()", self.vue_js)
        self.assertIn('role: "alertdialog"', self.vue_js)
        self.assertIn("global-confirm-panel", self.modal_css)

    def test_checkpoint_ui_is_filehistory_timeline_with_three_modes(self):
        self.assertIn("code_and_conversation", self.checkpoint_js)
        self.assertIn("conversation_only", self.checkpoint_js)
        self.assertIn("code_only", self.checkpoint_js)
        self.assertIn("window.BAA.ui?.confirm", self.checkpoint_js)
        self.assertNotIn("createCheckpoint", self.checkpoint_js)
        self.assertIn("v='filehistory-1'", self.template)

    def test_b5_replay_uses_durable_sequence_and_idempotency(self):
        self.assertIn("after_sequence=${lastSequence}", self.history_js)
        self.assertIn("seenSequences.has(sequence)", self.history_js)
        self.assertIn("data.replay_truncated", self.history_js)
        self.assertIn("sessionStorage.setItem(cursorKey(sid)", self.history_js)
        self.assertIn("applyLiveEvent", self.stream_js)

    def test_c4_workspace_switch_feedback_and_job_binding_are_visible(self):
        self.assertIn("continued_workspace?.active_job_count", self.workspace_js)
        self.assertIn("workspace.switched_jobs_continue", self.workspace_js)
        self.assertIn("workspace.unmounted_jobs_continue", self.workspace_js)
        self.assertIn('class: "job-history-workspace"', self.vue_js)
        self.assertIn("job.workspace_id", self.vue_js)
        workspace_line = next(
            line for line in self.template.splitlines()
            if "filename='js/modules/workspace.js'" in line
        )
        self.assertIn("v='c4-4-ui-1'", workspace_line)

    def test_c41_known_workspace_list_is_safe_and_vue_owned(self):
        self.assertIn("_fetchKnownWorkspaces", self.workspace_js)
        self.assertIn("selectKnownWorkspace", self.workspace_js)
        self.assertIn("function _renderKnownWorkspaces()", self.vue_js)
        self.assertIn('class: "ws-known-list"', self.vue_js)
        self.assertIn("!workspace.available || workspace.current", self.vue_js)
        self.assertNotIn("mountKnownWorkspace", self.workspace_js)
        self.assertIn(".ws-known-item", self.modal_css)

    def test_c42_known_workspace_switch_uses_preflight_and_app_confirm(self):
        self.assertIn("_previewSwitch", self.workspace_js)
        self.assertIn("expected_workspace_id", self.workspace_js)
        self.assertIn("activateKnownWorkspace", self.workspace_js)
        self.assertIn("window.BAA.ui?.confirm", self.workspace_js)
        self.assertNotIn("window.confirm", self.workspace_js)
        self.assertIn("continuing_job_count", self.workspace_js)
        self.assertIn("activateKnownWorkspace", self.vue_js)
        self.assertIn("workspace.known_switch", self.vue_js)

    def test_c43_workspace_rename_is_inline_and_does_not_use_system_dialogs(self):
        self.assertIn("_renameWorkspace", self.workspace_js)
        self.assertIn("method: \"PATCH\"", self.workspace_js)
        self.assertIn("renameKnownWorkspace", self.workspace_js)
        self.assertIn("_startWorkspaceRename", self.vue_js)
        self.assertIn("_saveWorkspaceRename", self.vue_js)
        self.assertIn('class: "ws-rename-input"', self.vue_js)
        self.assertIn("maxlength: 80", self.vue_js)
        self.assertNotIn("window.prompt", self.workspace_js)

    def test_c44_workspace_remove_is_preflighted_and_uses_app_confirmation(self):
        self.assertIn("_previewWorkspaceRemoval", self.workspace_js)
        self.assertIn('method: "DELETE"', self.workspace_js)
        self.assertIn("confirmed: true", self.workspace_js)
        self.assertIn("removeKnownWorkspace", self.workspace_js)
        self.assertIn("window.BAA.ui?.confirm", self.workspace_js)
        self.assertNotIn("window.confirm", self.workspace_js)
        self.assertIn("_removeKnownWorkspace", self.vue_js)
        self.assertIn("ws-known-remove", self.vue_js)
        self.assertIn(".ws-known-remove", self.modal_css)


if __name__ == "__main__":
    unittest.main()
