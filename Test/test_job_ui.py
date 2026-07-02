#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static contracts for the B1.5 Vue job progress UI."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestJobUiContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stream_js = (ROOT / "frontend/features/chat-stream.js").read_text(encoding="utf-8")
        ui_dir = ROOT / "frontend" / "features" / "ui"
        cls.vue_js = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(ui_dir.glob("*-ui.js"))
        )
        cls.css = (ROOT / "static/css/parts/chat.css").read_text(encoding="utf-8")
        cls.slash_js = (ROOT / "frontend/features/slash.js").read_text(encoding="utf-8")
        cls.command_handlers_js = (
            ROOT / "frontend/legacy/command_handlers.js"
        ).read_text(encoding="utf-8")
        cls.markdown_js = (
            ROOT / "frontend/legacy/markdown.js"
        ).read_text(encoding="utf-8")
        cls.message_js = (
            ROOT / "frontend/legacy/msg.js"
        ).read_text(encoding="utf-8")
        cls.datasource_js = (
            ROOT / "frontend/legacy/datasource.js"
        ).read_text(encoding="utf-8")
        cls.sessions_js = (
            ROOT / "frontend/legacy/sessions.js"
        ).read_text(encoding="utf-8")
        cls.autosave_js = (
            ROOT / "frontend/legacy/autosave.js"
        ).read_text(encoding="utf-8")
        cls.overlay_js = (
            ROOT / "frontend/core/overlay.js"
        ).read_text(encoding="utf-8")
        cls.skills_js = (ROOT / "frontend/features/skills.js").read_text(encoding="utf-8")
        cls.models_js = (ROOT / "frontend/features/models.js").read_text(encoding="utf-8")
        cls.workspace_js = (ROOT / "frontend/features/workspace.js").read_text(encoding="utf-8")
        cls.preview_js = (ROOT / "frontend/legacy/preview.js").read_text(encoding="utf-8")
        cls.history_js = (ROOT / "frontend/legacy/job_history.js").read_text(encoding="utf-8")
        cls.checkpoint_js = (ROOT / "frontend/legacy/checkpoints.js").read_text(encoding="utf-8")
        cls.app_settings_js = (
            ROOT / "frontend/legacy/app_settings.js"
        ).read_text(encoding="utf-8")
        cls.update_js = (ROOT / "frontend/legacy/update.js").read_text(encoding="utf-8")
        cls.chat_app_entry = (
            ROOT / "frontend/entries/chat-app.js"
        ).read_text(encoding="utf-8")
        cls.modal_css = (ROOT / "static/css/parts/modals.css").read_text(encoding="utf-8")
        cls.template = (ROOT / "templates/agent_chat.html").read_text(encoding="utf-8")
        cls.app_js = (ROOT / "frontend/legacy/app.js").read_text(encoding="utf-8")
        cls.mcp_js = (ROOT / "frontend/features/mcp.js").read_text(encoding="utf-8")
        cls.knowledge_js = (ROOT / "frontend/features/knowledge.js").read_text(encoding="utf-8")
        cls.i18n_js = (ROOT / "frontend/legacy/i18n.js").read_text(encoding="utf-8")
        cls.temp_prompt_js = (ROOT / "frontend/legacy/temp_prompt_panel.js").read_text(encoding="utf-8")
        cls.legacy_panels_js = (ROOT / "frontend/entries/legacy-panels.js").read_text(encoding="utf-8")

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
        self.assertIn("async function cancelJob(jobId)", self.history_js)
        self.assertIn("/jobs/${encodeURIComponent(jobId)}/cancel", self.history_js)

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
            "filename='dist/chat-app.js'": "v='fe1-chat-app-4'",
        }
        for asset, version in asset_versions.items():
            line = next(line for line in self.template.splitlines() if asset in line)
            self.assertIn(version, line)
        self.assertNotIn("filename='dist/core.js'", self.template)
        self.assertNotIn("filename='dist/ui.js'", self.template)
        self.assertNotIn("filename='dist/stream.js'", self.template)

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

    def test_sc1_commands_use_typed_handler_registry_and_backend_route(self):
        self.assertIn("const handlers = new Map()", self.command_handlers_js)
        self.assertIn("async function execute(command", self.command_handlers_js)
        self.assertIn('commandHandlers.register("clear"', self.stream_js)
        self.assertIn('commandHandlers.register("status"', self.stream_js)
        self.assertNotIn('if (action === "clear")', self.stream_js)
        self.assertIn('["local", "local-ui", "backend"]', self.stream_js)
        self.assertIn("/commands/${encodeURIComponent(command.cmd)}/execute", self.stream_js)
        self.assertLess(
            self.chat_app_entry.index("../legacy/command_handlers.js"),
            self.chat_app_entry.index("./legacy-stream.js"),
        )

    def test_fe1_foundation_helpers_use_explicit_module_exports(self):
        self.assertIn("export function renderMd", self.markdown_js)
        self.assertIn("export function appendMsg", self.message_js)
        self.assertIn("export const commandHandlers", self.command_handlers_js)
        combined = "\n".join(
            (self.markdown_js, self.message_js, self.command_handlers_js, self.stream_js)
        )
        for legacy_global in (
            "window.renderMd",
            "window.BAA.markdown",
            "window.BAA.msg",
            "window.BAA.commandHandlers",
            "window.appendMsg",
            "window.sysMsg",
            "window.updateTokenBar",
        ):
            self.assertNotIn(legacy_global, combined)

    def test_fe1_session_and_datasource_modules_do_not_publish_globals(self):
        combined = "\n".join(
            (self.datasource_js, self.sessions_js, self.autosave_js)
        )
        self.assertIn("export {", self.datasource_js)
        self.assertIn("export {", self.sessions_js)
        self.assertIn("export {", self.autosave_js)
        self.assertIn('eventBus.emit("overlay:open"', self.overlay_js)
        self.assertIn('eventBus.on("overlay:open"', self.datasource_js)
        for legacy_global in (
            "window.BAA.datasource",
            "window.BAA.sessions",
            "window.BAA.autosave",
            "window.setSrc",
        ):
            self.assertNotIn(legacy_global, combined)
        self.assertNotIn("window.BAA.dom", combined)
        self.assertNotIn("window.BAA.overlay", combined)

    def test_sc2_slash_interception_completion_and_app_commands(self):
        self.assertIn("function parseSlashInput(value)", self.slash_js)
        self.assertIn('if (e.key === "Tab")', self.slash_js)
        self.assertIn("available.length === 1", self.slash_js)
        self.assertIn("parseSlashInput(text)", self.stream_js)
        self.assertIn("未知命令：/${parsed.name}", self.stream_js)
        self.assertIn('commandDef?.arguments === "required"', self.stream_js)
        self.assertIn('commandDef?.arguments === "none"', self.stream_js)
        self.assertNotIn('v === "/stop"', self.slash_js)
        for action in ("new", "stop", "data", "jobs", "teams"):
            self.assertIn(f'commandHandlers.register("{action}"', self.stream_js)
        self.assertIn("commandHandlers.register(\"data\", () => openSchemaView())", self.stream_js)
        self.assertIn("commandHandlers.register(\"jobs\", () => openJobHistory())", self.stream_js)
        self.assertIn("window.BAA.teams?.openPanel?.()", self.stream_js)

    def test_fe1_remaining_business_modules_use_explicit_exports(self):
        combined = "\n".join(
            (
                self.preview_js,
                self.history_js,
                self.checkpoint_js,
                self.app_settings_js,
                self.update_js,
            )
        )
        for module_source in (
            self.preview_js,
            self.history_js,
            self.checkpoint_js,
            self.app_settings_js,
        ):
            self.assertIn("export {", module_source)
        self.assertIn("export async function runUpdate", self.update_js)
        for legacy_global in (
            "window.BAA.preview",
            "window.BAA.jobHistory",
            "window.BAA.checkpoints",
            "window.BAA.appSettings",
            "window.BAA.update",
        ):
            self.assertNotIn(legacy_global, combined)

    def test_fe1_dead_chat_entry_and_monolith_are_removed(self):
        self.assertFalse((ROOT / "frontend/entries/chat.js").exists())
        self.assertFalse((ROOT / "static/js/agent_chat.js").exists())

    def test_sc3_dynamic_availability_and_diagnostics_contracts(self):
        self.assertIn("function getAvailability(command)", self.slash_js)
        self.assertIn('command.cmd === "stop" && !state.isStreaming', self.slash_js)
        self.assertIn("command.unavailable_reason", self.slash_js)
        self.assertIn("COMMAND_DIAGNOSTICS", self.slash_js)
        self.assertIn("slash-unavailable-reason", self.slash_js)
        self.assertIn("slash-diagnostics", self.slash_js)
        self.assertIn("window.BAA.slash.getAvailability(commandDef)", self.stream_js)
        self.assertIn("window.BAA.slash?.loadCommands?.()", self.models_js)

    def test_sc4_slash_rendering_and_metrics_are_content_safe(self):
        self.assertNotIn("div.innerHTML", self.slash_js)
        self.assertIn("icon.textContent = c.icon", self.slash_js)
        self.assertIn("description.textContent = _description(c)", self.slash_js)
        self.assertIn("reason.textContent = availability.reason", self.slash_js)
        self.assertIn("/command-metrics", self.stream_js)
        self.assertNotIn("arguments: text", self.stream_js)

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
        self.assertIn('registerUiIsland("jobHistory"', self.vue_js)
        self.assertIn("function applyEvent(ev)", self.vue_js)
        self.assertIn("job-history-modal", self.modal_css)

    def test_destructive_history_action_uses_app_confirm_dialog(self):
        self.assertIn("uiRegistry.confirm?.", self.history_js)
        self.assertNotIn("window.confirm", self.history_js)
        self.assertIn("function renderConfirm()", self.vue_js)
        self.assertIn('role: "alertdialog"', self.vue_js)
        self.assertIn("global-confirm-panel", self.modal_css)

    def test_checkpoint_ui_is_filehistory_timeline_with_three_modes(self):
        self.assertIn("code_and_conversation", self.checkpoint_js)
        self.assertIn("conversation_only", self.checkpoint_js)
        self.assertIn("code_only", self.checkpoint_js)
        self.assertIn("uiRegistry.confirm?.", self.checkpoint_js)
        self.assertNotIn("createCheckpoint", self.checkpoint_js)
        self.assertIn("v='fe1-chat-app-4'", self.template)

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
        app_line = next(
            line for line in self.template.splitlines()
            if "filename='dist/chat-app.js'" in line
        )
        self.assertIn("v='fe1-chat-app-4'", app_line)

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

    def test_fe1_step9_mcp_knowledge_no_global_object_assign(self):
        # Object.assign(globalThis, mcp, knowledge) must be gone — 30+ functions
        # were polluting the global scope. Each is now reached via BAA.mcp.* / BAA.knowledge.*.
        self.assertNotIn("Object.assign(globalThis, mcp, knowledge)", self.legacy_panels_js)
        self.assertNotIn("Object.assign(globalThis, mcp)", self.legacy_panels_js)
        self.assertNotIn("Object.assign(globalThis, knowledge)", self.legacy_panels_js)
        # app.js must use namespaced calls, not bare window.* globals
        for bare_global in (
            "window.openMcpSettings()",
            "window.loadMcpServers()",
            "window.toggleMcpAddForm()",
            "window.addMcpServer()",
            "window.switchMcpTab(",
            "window.scanLocalMcp()",
            "window.parseMcpConfig()",
            "window.updateMcpCmdPreview()",
            "window.kbOpenForm(",
            "window.kbRefresh(",
            "window.kbSwitchTab(",
            "window.kbLoadFiles()",
            "window.kbCancelImport()",
            "window.kbConfirmImport()",
            "window.kbSubmitForm()",
            "window.kbOnDrop",
            "window.kbOnFileSelect",
        ):
            self.assertNotIn(bare_global, self.app_js,
                             msg=f"app.js still uses bare global: {bare_global}")
        # chat-stream.js must reach mcp through BAA namespace
        self.assertNotIn("window.openMcpSettings?.()", self.stream_js)

    def test_fe1_step9_temp_prompt_uses_baa_namespace(self):
        # window.tp* globals must not be set from temp_prompt_panel.js
        for tp_global in (
            "window.tpSave",
            "window.tpToggle",
            "window.tpClear",
            "window.tpUpdateCount",
            "window.tpOpenWithText",
        ):
            self.assertNotIn(tp_global, self.temp_prompt_js,
                             msg=f"temp_prompt_panel.js still sets {tp_global}")
        # The namespace must be published instead
        self.assertIn("baa.tempPrompt", self.temp_prompt_js)
        # app.js and stream.js must call through the namespace
        self.assertNotIn("window.tpSave(", self.app_js)
        self.assertNotIn("window.tpToggle(", self.app_js)
        self.assertNotIn("window.tpClear(", self.app_js)
        self.assertNotIn("window.tpUpdateCount", self.app_js)
        self.assertNotIn("window.tpOpenWithText", self.stream_js)

    def test_fe1_step9_i18n_uses_baa_namespace(self):
        # window.t / getLang / setLang / applyI18n must NOT be the primary assignment;
        # functions must be defined first and then published on baa.i18n.
        self.assertIn("baa.i18n", self.i18n_js)
        self.assertIn("function t(", self.i18n_js)
        self.assertIn("function getLang(", self.i18n_js)
        self.assertIn("function setLang(", self.i18n_js)
        self.assertIn("function applyI18n(", self.i18n_js)
        # window.* aliases may still be present for legacy callers but must follow baa.i18n
        i18n_baa_pos = self.i18n_js.index("baa.i18n")
        for alias in ("window.t =", "window.getLang =", "window.setLang =", "window.applyI18n ="):
            if alias in self.i18n_js:
                alias_pos = self.i18n_js.index(alias)
                self.assertGreater(alias_pos, i18n_baa_pos,
                                   msg=f"{alias} must appear AFTER baa.i18n assignment")
        # app.js toggleLang must call through BAA.i18n
        self.assertNotIn("window.setLang(window.getLang", self.app_js)
        self.assertIn("BAA.i18n.setLang", self.app_js)

    def test_fe1_step10_eslint_covers_modularized_legacy_files(self):
        root = Path(__file__).resolve().parents[1]
        eslint_cfg = (root / "eslint.config.js").read_text(encoding="utf-8")
        # Every frontend module, including the compatibility layer, now uses
        # the same recommended no-undef and no-unused-vars protection.
        self.assertNotIn('"frontend/legacy/**"', eslint_cfg)
        self.assertNotIn("frontend/legacy/state.js", eslint_cfg)
        self.assertNotIn("frontend/legacy/app.js", eslint_cfg)
        self.assertNotIn('"no-unused-vars": "off"', eslint_cfg)
        self.assertNotIn('"no-undef": "off"', eslint_cfg)

    def test_chat_entry_is_esm_with_lazy_ui_chunks(self):
        root = Path(__file__).resolve().parents[1]
        config = (root / "vite.chat-app.config.js").read_text(encoding="utf-8")
        vue_entry = (root / "frontend/features/vue-app.js").read_text(encoding="utf-8")
        self.assertNotIn('formats: ["iife"]', config)
        self.assertIn('chunkFileNames: "chunks/[name]-[hash].js"', config)
        self.assertIn('await import("./ui/settings-ui.js")', vue_entry)
        self.assertIn("export async function ensureUiIsland", vue_entry)
        self.assertIn('type="module"', self.template)

        static_modules = root / "static/js/modules"
        self.assertEqual(
            {path.name for path in static_modules.glob("*.js")},
            {"desktop_lifecycle.js"},
        )
        source_policy = (root / "frontend/README.md").read_text(encoding="utf-8")
        self.assertIn(
            "All maintained chat-application JavaScript lives under `frontend/`",
            source_policy,
        )

    def test_fe1_step10_app_store_wraps_state_with_proxy(self):
        root = Path(__file__).resolve().parents[1]
        app_store_js = (root / "frontend" / "core" / "app-store.js").read_text(encoding="utf-8")
        state_js = (root / "frontend" / "legacy" / "state.js").read_text(encoding="utf-8")
        # state.js bootstraps the raw object onto BAA.state before app-store runs
        self.assertIn("window.BAA.state = state", state_js)
        # app-store.js wraps it with a Proxy (reactive store)
        self.assertIn("new Proxy(", app_store_js)
        # app-store.js fails fast if state bootstrap hasn't run
        self.assertIn("App Store must load after the legacy state bootstrap", app_store_js)
        # baa.state is replaced by the Proxy, not the raw object
        self.assertIn("baa.state = state", app_store_js)


if __name__ == "__main__":
    unittest.main()
