# Version Development Log

## v1.1.0 LTS
**July 2, 2026**

This release introduces the **Slash Command System, File Timeline Rewind, Hooks Automation, and AI Prompt Suggestions**, while significantly improving the chat interface and Dashboard experience. It is the next Long-Term Support (LTS) version following v1.0 LTS.

### 1. All-New Slash Command System
- Rebuilt slash commands with new system commands: `/new` (new session), `/stop` (stop response), `/data` (data preview), `/jobs` (task history), `/compact` (compress context), and more
- Supports alias search, Tab completion, and argument hints; use `/help <command>` for detailed descriptions
- Custom Prompt Commands: place a Markdown file in `.baa/commands/` inside your workspace to add custom commands

### 2. Hooks — User Automation
- New Hooks v1: trigger automated actions at key points in the AI session (session start, before/after each turn, before/after tool calls)
- Edit, validate, and test Hooks configuration directly from the app settings panel
- The Agent can read public documentation or API descriptions and automatically generate a Hooks configuration for you

### 3. AI Prompt Suggestion
- After each AI reply, the system automatically predicts and pre-fills a suggested next message into the input box
- Pre-fill only applies when the input box is empty; typing, sending, or switching sessions clears the suggestion
- Press Enter to send the suggestion directly, or edit it first

### 4. Workspace File Timeline Rewind
- A snapshot is automatically created before each message is sent; file versions are backed up before any write operation
- `/checkpoint` opens a timeline panel showing each snapshot's trigger message and number of modified files
- Three rewind modes: restore files + conversation, conversation only, or files only

### 5. Enhanced Workspace Management
- The workspace panel now shows a history list — switch back to previously used directories with one click
- Rename the display name of any workspace without affecting the actual file path
- "Remove from list" hides a workspace entry while keeping all local files intact
- New storage cleanup tool: automatically identifies and removes stale caches to free disk space


---

## v1.0 LTS
**June/20/2026**
- The project officially released its first stable version
- Supports basic data upload, viewing, analysis, and visualization
- Supports common data sources such as Excel, CSV, and databases
- Enables common data analysis tasks through conversational interaction
- Provides basic chart generation and result export capabilities

---

## v2.0
**May/11/2026**

This update focuses on **stronger analytical capabilities, more consistent presentation, and better export features**.

### 1. Logging System Upgrade
- Added a unified logging system
- Automatically records runtime activity, tool calls, model requests, and exceptions
- Logs rotate daily and are retained for 30 days to help with troubleshooting

### 2. Unified Color Scheme
- Fixed inconsistent color schemes between charts and PPTs
- When users switch brand colors, both charts and reports will update accordingly
- Default color scheme is McKinsey, with support for BCG and others

### 3. Agent Architecture Refactoring
- Split the original single-file Agent into multiple modules
- Improved code maintainability and extensibility
- Makes future feature integration easier

### 4. Internationalization Support
- Added Chinese/English language switching
- Frontend interface can be switched between Chinese and English with one click
- Page text, prompts, and command descriptions are fully adapted

### 5. Slash Command Optimization
- Replaced the general analysis command with more specific commands
- Such as `/decile`, `/tree`, and `/kmeans`
- Makes it easier for users to understand the purpose of each command

### 6. Enhanced Export Features
- Added Excel and Word report export
- Supports generating downloadable files directly from analysis results
- `/export` and `/report` commands enable quick export tasks

### 7. Enhanced Data Cleaning Features
- Added data profiling, missing value handling, winsorization, and trimming
- Supports more flexible data preprocessing workflows
- Cleaned data can be used directly for further analysis

### 8. Data Preview Upgrade
- Replaced the original data structure view with a table preview
- Provides an experience closer to Excel
- Allows quick browsing of tables, fields, and sample data

### 9. Token Usage Tracking
- Added context window and output token statistics
- Users can view token usage directly in the interface
- Helps control model cost and context length

### 10. Thinking Mode Display
- Supports displaying the model’s reasoning process
- Users can view the model’s chain of thought when needed
- Useful for debugging and understanding complex analysis tasks

### 11. Frontend Experience Improvements
- Further modularized the page structure for easier maintenance
- Separated styles and scripts into independent files
- Improved interface stability and responsiveness

---
## v3.0
May 18, 2026
This upgrade focuses on external ecosystem integration and enhanced business knowledge capabilities, significantly expanding the Agent’s data acquisition abilities and adaptability to different fields.

### 1. MCP Tool Invocation Capability
- Added support for the MCP (Model Context Protocol); Agents can now dynamically invoke external MCP tools.
- Supports the use of calculators, code executors, third-party API wrappers, and other tools to expand analytical capabilities.
- Through a standardized protocol, any tool that complies with MCP specifications can be integrated.
- The entire process of tool invocation is automatically logged for easy debugging and auditing.

### 2. Business Knowledge Base Integration
- Added a business database feature that allows the import of internal company materials, product documentation, industry reports, etc.
- Materials are automatically processed in a vectorized manner to create a searchable knowledge base.
- When performing analyses, Agents automatically retrieve relevant information, enhancing their understanding and insight into specific business scenarios.- Supports multiple formats: Word, Excel, etc., to meet the needs of importing common documents.

### 3. Expansion of data sources: Google Sheets and custom APIs
- Integration with Google Sheets API: Allows direct access to data in Google Sheets as an analysis source.
- Custom database API interface: Provides a generic API adapter.
- All external data sources can be utilized along with a full set of functions such as data cleaning, previewing, and analysis.

---

## v4.0
May 29, 2026

This upgrade focuses on comprehensive front-end experience refactoring, chart stability enhancement, and engineering quality reinforcement.

### 1. User Interface Optimization
- Sidebar refactored into a three-part information architecture: Status Area, Operations Area, History Area
- Added Model Connection Test indicator (automatically tests upon model selection, also manually triggerable in settings)
- Agent output bubbles changed to a "report style": left brand-colored vertical line + shaded background, visually unified with chart frames
- Supports one-click dark mode switching
- Split `agent_chat.css` into five sub-modules: `tokens`, `base`, `chat`, `modals`, `kb`
- Added an operation guide, including explanations for common issues

### 2. Enhanced Stability
- Localized chart dependencies to eliminate reliance on external CDNs
- Fixed the issue where loading historical conversations forced overriding the currently selected model: now restores from history only when the user hasn't selected a model yet

### 3. Batch Data Processing Capability Improvement
- Changed the conversion logic after original data source connection, switching from SQL Slight to duckdb, enabling second-level processing for tens of thousands of rows of data

### 4. New Time Series Analysis Module
- Supports Prophet, SARIMA, ARIMA, VAR, and GRU models

```markdown
---

## v5.0
**June 4, 2026**

This is a major release, covering four key areas: **multi-source support, intelligent interaction, stability fixes, and security hardening**.

### 1. Multi-Source Support
- You can now connect to multiple data sources at the same time — no longer limited to just one
- A new data source list has been added to the sidebar, with an independent toggle for each source, so you can freely choose which sources are active and included in analysis
- In multi-source scenarios, the AI can automatically determine which source each query should pull from, and supports joint analysis across sources

### 2. AI Proactive Questions
- When a request is ambiguous (e.g., it's unclear which dimension to analyze by or which metric to use), the AI will proactively pause and ask a question
- Option cards appear in the interface — just click an option to continue, or choose "Other" to enter a custom answer
- The AI won't interrupt arbitrarily — it only asks when different choices would lead to substantially different results

### 3. Automatic Conversation Saving
- Each conversation is automatically saved after the AI finishes responding, so it won't be lost when you refresh or reopen the page
- In the saved conversation list, auto-saved and manually saved records now share a unified style, distinguished by a small label — there's no longer a visual "hierarchy" between them
- Fixed an issue where auto-save would incorrectly create a new record when you continued chatting after loading a saved conversation

### 4. Improved MCP Tool Integration Experience
- Connected MCP tools can now be expanded to view detailed descriptions, so you can understand each tool's purpose and parameters
- Added "Smart Fill": paste in a tool's install command or configuration, and the form is automatically parsed and filled — no need to enter each field manually
- Added "Local Scan": enter the path to a local toolkit, and configuration details are automatically detected

### 5. Data Preview Upgrade
- The data preview dialog now uses a split-panel layout: the left side lists all tables (with row counts), and the right side shows their contents
- With multiple sources, the left side groups tables by source, showing how many tables each source contains
- The divider can be dragged to adjust the panel widths

### 6. Improved SQL Database Connectivity

- After connecting to an external database, tables the AI has queried are automatically cached locally, greatly speeding up repeated queries without having to reconnect to the remote server each time
- Fixed inaccurate table count detection (previously, large databases might only have some tables recognized; now all tables and views are retrieved)
- Fixed incorrect table-name quoting formats across database dialects (MySQL, PostgreSQL, etc.)
- Large tables (over 500,000 rows) are no longer fully pulled to the local machine; queries are executed directly on the remote server to avoid out-of-memory errors

### 7. Preventing AI from Fabricating Data

- Fixed an issue where, across multiple conversation turns, the AI would "remember" numbers from earlier replies and reuse them in the next turn instead of re-querying
- The AI can now see the complete history of queries and won't treat textual descriptions as known facts

### 8. Knowledge Base Trigger Fix

- Fixed an issue where the business knowledge base often wasn't triggered during conversations — the AI used to skip checking the knowledge base for various reasons
- Now, for any data analysis request, the AI consults the metric definitions and business rules in the knowledge base first, before starting the analysis

### 9. Other Experience Fixes

- While the AI is responding, scrolling up to review earlier content no longer forces you back to the bottom; the view only auto-scrolls to the latest message when you send a new one
- Fixed an issue where Google Sheets data sources would sometimes only recognize half of the worksheets
- Fixed an issue where the grouping field in grouped bar charts would sometimes have no effect, causing the chart to fall back to a regular bar chart
- Fixed an issue where, after loading a saved conversation, the sidebar data source status wouldn't refresh and would show stale or blank states
- Enhanced security: all SQL queries run by the AI undergo stricter syntax-level checks, and filesystem and network access have been disabled at the database connection layer

```
## v5.1
**June 10, 2026**

This update focuses on multi-data-source federated analysis and data connectivity stability improvements.

### 1. Enhanced Cross-Data-Source Federated Analysis
- Added a multi-data-source virtual merge layer, supporting direct cross-source `JOIN` and `UNION` operations on tables from different data sources.
- When multiple Excel or CSV files contain tables with the same name, the system now automatically prefixes table names with their data source identifier, enabling the AI to accurately distinguish and query data from the specified file.
- Optimized multi-data-source query routing. Both single-source and cross-source queries now automatically select the correct execution method.
- Data source schema information now includes sample data, helping the AI differentiate between tables with identical structures but different content.

### 2. Excel Data Connectivity Fixes
- Fixed an issue where Excel columns containing "space-separated number strings" (e.g., bank account numbers, ID numbers, codes) would fail to upload.
- Standard text columns are now consistently recognized as strings, preventing DuckDB from incorrectly inferring them as numeric and triggering type conversion errors.

### 3. Optimized Data Source Disconnection Prompts
- Fixed a problem where, after a service restart, the frontend would still show data sources as connected, but queries would not respond.
- When a data source connection within a session becomes invalid, the system now clearly prompts the user to reconnect, instead of returning blank replies.
- Added dual frontend-backend checks to prevent the AI from continuing with invalid execution when data structures are missing.

### 4. Improved Model Testing Experience
- Model settings now support "test before save".
- After users enter a new API Key, Base URL, or model name, they can directly test the current inputs.
- If input fields are empty, the system will automatically use the saved configuration, maintaining compatibility with the original testing method.

---

## v5.2
**June 16, 2026**

This update focuses on **business knowledge base RAG enhancement, traceable citation sources, and Chinese retrieval optimization**.

### 1. Complete RAG Retrieval Pipeline
- Added document chunking and local vector indexing on top of the existing business knowledge base
- Imported Word and Excel knowledge files can be split into retrievable fragments
- After the user confirms knowledge ingestion, the system synchronously builds a RAG index
- Even documents that do not yield structured metrics or rules can serve as full-text knowledge sources for retrieval

### 2. Knowledge Base Citation Display
- `query_knowledge` tool calls now display matched citation sources
- Supports showing metric definitions, business rules, background knowledge, and document fragment sources
- The frontend adds an expandable "Citations" section under the "Query Knowledge" step
- Each citation shows type, title, source, content fragment, and relevance score for user verification

### 3. Chinese Retrieval Optimization
- Optimized Chinese business phrase retrieval, supporting continuous Chinese words, short words, and 2/3/4-character phrase matching
- Retrieval ranking combines keyword matching, local vector similarity, and Chinese phrase similarity
- More stable hits for Chinese business terms like "cost", "base fee", "reward", "premium", "retention", "churn"
- Business rules are now retrieved by relevance, preventing unrelated rules from being mistaken as hits

### 4. Traceable Agent Answers
- When the Agent relies on knowledge base results, it lists short "Citations" at the end of the answer where possible
- Knowledge base retrieval results are passed to the Agent as structured context, reducing the risk of fabricating business definitions
- The original mandatory knowledge base pre-check is preserved: data analysis requests query the knowledge base first before running analysis

### 5. Tool Calling Architecture Optimization
- Tool execution results upgraded from plain text to a structured envelope, uniformly containing `ok`, `error`, `data`, `summary`, `sources`, `artifacts`, `debug` fields
- Tool results passed to the model remain in a compatible text format while carrying the JSON structure, facilitating frontend display, log auditing, and automated testing
- Added tool error classification: parameter errors, SQL validation errors, missing field, missing table, SQL syntax errors, data source disconnection, permission errors, empty results, and MCP errors
- Added tool schema version registration for tracking parameter contract changes

### 6. Dynamic Tool Exposure and Conservative Parallelism
- Dynamically trims the tool list based on the current command and data source state, reducing the number of tools exposed to the model per turn
- Generation tools (PPT, Dashboard, Report, Export) are no longer exposed in normal conversations; they are only opened when the corresponding slash command is triggered
- Query, chart, analysis, and cleaning tools are hidden when no data source is connected, reducing mis-invocations
- Conservative parallel execution is applied to `query_knowledge`, `get_table_detail`, `select_chart`, and MCP tools to improve multi-tool batch call efficiency
- `query_data` is not parallelized for now to avoid thread-safety risks from shared database connections

### 7. Tool Audit and Data Provenance Display
- Frontend tool steps now show tool audit information: duration, parallel status, source count, artifact count, and error type
- SQL queries, analysis table creation, analysis runs, and chart generation return "data provenance"
- Data provenance includes data source, table name, SQL summary, and returned row count, making it easy to verify where analysis results come from
- "Citations" and "Data Provenance" are unified in blue style and collapsed by default; click to expand for details
- Fixed residual inline event handlers in the MCP settings modal, unified to frontend event delegation, improving test stability

---

## v5.3
**June 17, 2026**

This update focuses on **front-end architecture modernization**, progressively migrating scattered DOM handlers into Vue islands under the "Vue Progressive Integration" plan to improve state management clarity and interaction experience.

### 1. Unified Vue Entry
- Merged the original `vue_chat.js` and `vue_ui.js` into `vue_app.js` as the single Vue progressive entry point
- Exposes `window.BAA.ui` (toast/loading facade), `window.BAA.vueChat` (message shell facade), and `window.BAA.vueSettings` (settings modal facade) for backward compatibility
- New Vue capabilities are unified into `vue_app.js`; no more scattered island files

### 2. Message Shell and Basic Text Stream (Stages 1-3)
- Message shell rendered by Vue: `.msg.user` / `.msg.assistant` / `.sys-msg`; `msg.js` delegates to Vue first, falls back to legacy DOM when unavailable
- Global Toast and Loading consolidated into a unified toast stack and overlay
- Chat streaming text managed by Vue state: `appendTextDelta` / `setMarkdown` / `addReasoning` / `setError` / `markStopped`

### 3. Tool Steps and "Thinking Next Step" Wait State (Stage 4)
- Tool steps (`tool_start` / `tool_end` / `knowledge_refs` / `data_refs` / `tool_audit`) managed by Vue state, supporting `.running` state
- After `tool_start`, yields one browser paint to avoid the following `tool_end/text` in the same SSE batch directly overwriting the running state
- Tool steps retain a minimum visible duration (~650ms) so fast SQL/knowledge queries don't look like they have no animation
- Added `showToolActivity()` / `hideToolActivity()`: shows a "Thinking next step..." wait state when a tool batch ends but the next real output hasn't arrived, preventing users from mistaking it for a hang
- Citations, data provenance, and tool audit info are unified in blue style and collapsed by default

### 4. ChartFrame Chart Container (Stage 5A)
- Added `addChartRef()`, rendering `.chart-list` / `.chart-frame` / iframe via Vue
- Preserves IntersectionObserver lazy loading, iframe load height sync, Plotly resize, and open-in-new-tab capabilities
- Charts render independently within the message body without repainting `.msg-bubble`, avoiding disruption to streaming text or tool steps
- `chart_ref` events delegate to Vue first; falls back to legacy `_buildChartFrame()` when Vue is unavailable or the target is not a Vue message

### 5. outline / ask_user Interactive Cards (Stage 5B)
- Added `card-list` container (after `msg-bubble`), rendering `.ppt-outline-card` / `.ask-user-card` via Vue, preserving original classes and DOM structure with zero CSS changes
- All four outline variants (PPT / Excel / Report / Dashboard) and ask_user (single-select / multi-select / other input) migrated to Vue state management
- Callback injection pattern: `sendConfirmStream` is a closure-local function in `chat_stream.js`; `onConfirm` / `onRevise` / `onCancel` / `onSubmit` are injected via the `callbacks` parameter of `addOutlineCard(target, data, callbacks)` / `addAskUserCard(target, ev, callbacks)`, preserving encapsulation
- The `CURRENT_SLIDES_JSON` / `CURRENT_REPORT_JSON` / `CURRENT_DASHBOARD_JSON` command concatenation logic for outline revise stays in the `onRevise` callback closure in `chat_stream.js`; Vue only collects `editText` and passes it back, unaware of command formats
- Legacy DOM implementations preserved as `_legacyOutlineBody()` / `_legacyAskUserBody()`, falling back when Vue is unavailable or returns false

### 6. Model Settings Modal Vue-ification (Stage 7A)
- Three mount points of the ov-settings modal (`#builtin-providers` / `#custom-list` / `#add-custom-form`) taken over by Vue, sharing a single reactive state rendered separately
- Provider card fields (apiKey / baseUrl / model / ctx / output / think / budget) centrally managed by Vue state, replacing the scattered 7 ID suffix groups (`pk-` / `pu-` / `pm-` / `pctx-` / `pout-` / `pthink-` / `pbudget-`)
- Added `busy` field (`null` / `"save"` / `"test"` / `"clear"`) to disable buttons, eliminating duplicate clicks during test/save/clear
- thinking checkbox reactively drives budget row visibility, replacing manual DOM toggling
- Custom model add/edit form managed by Vue form API (`openForm(key, cfg)` / `getFormValues` / `toggleForm` / `closeForm`); edit vs add mode distinguished by `editingKey`
- `setProviders` detects hasKey transitioning true→false (just cleared) and automatically resets fields to defaults; after successful save, `clearProviderApiKey` clears only the apiKey input, preserving other user inputs
- Legacy innerHTML implementations preserved as `_legacyRenderBuiltin` / `_legacyRenderCustom` / `_legacyEditCustom` / `_legacyAddCustom` / `_legacyToggleAddCustom`, falling back when Vue is unavailable

### 7. Knowledge Base Modal Vue-ification (Stage 7B)
- Five mount points of the ov-knowledge / ov-kb-form modals (`#kb-tabs` / `#kb-panel-metrics` / `#kb-panel-rules` / `#kb-panel-notes` / `#kb-form-body`) taken over by Vue, sharing a single reactive state rendered separately
- 4-tab reactive switching (including import tab), 3 card types (metrics/rules/notes) rendered via v-for, centralized state `{ id, fields..., enabled }`
- Toggle enable/disable uses optimistic update (`vueKb.updateItem` flips enabled, rollback + toast on API failure), eliminating full-list reload
- Delete uses optimistic removal (`vueKb.removeItem`, rollback via `loadByTab` on API failure)
- Add/edit form uses v-model two-way binding; `openForm({ type, mode, editId, rec })` accepts full rec for prefill, replacing 11 input IDs and 3 sets of `style.display` toggling; form title managed by Vue island
- **Import zone not migrated**: the 4-state state machine (idle/parsing/preview/done) + null marker is bug-prone, and the import flow is stable with low change frequency; kept as legacy DOM, with visibility managed by `renderAll()` at the end
- `_origOpenOverlay` hook preserved as fallback, internally delegating to `vueKb.onOpen()` to trigger current tab load
- Legacy innerHTML implementations preserved as `_legacy*`, falling back when Vue is unavailable

### 8. MCP Tool Server Modal Vue-ification (Stage 7C)
- Two mount points of the ov-mcp modal (`#mcp-server-list` / `#mcp-form-fields`) taken over by Vue, sharing a single reactive state rendered separately
- Server cards v-for rendered, state centralized `{ server_id, label, transport, status, enabled, tool_count, toolsOpen, tools, toolsLoading, busy }`, eliminating 5 inline `onclick`/`onchange` handlers
- Tool expansion merged into server state (`toolsOpen` / `tools` / `toolsLoading`), Vue path deprecates `_mcpToolsCache` (preserved as fallback); `setServers` preserves existing server tool expansion state
- toggle enabled uses optimistic update (`vueMcp.updateServer` flips enabled, API failure rolls back + toast), replacing `setTimeout(loadMcpServers, 300)` full list reload
- remove uses optimistic deletion (`vueMcp.removeServer`, API failure `loadMcpServers` rolls back)
- transport stdio/sse reactive switching + command preview computed, replacing `style.display` + manual flexDirection/gap + `updateMcpCmdPreview` manual input monitoring
- Add/edit form v-model two-way binding, `openForm({ mode, editId, server })` accepts complete server prefill, replacing 8 input IDs and 3 global variables (`_mcpFormOpen` / `_mcpEditId` / `_mcpActiveTab`)
- **Smart-fill area not migrated**: 4-state state machine + IO intensive + warnings/confidence/LLM hint edge cases numerous, and feature is stable with low change frequency, keeping legacy DOM
- **Smart-fill bridge**: `_applyMcpConfig` rewritten to call `vueMcp.setField` field-by-field writing to Vue state (transport linkage + command preview auto-triggered by `_renderForm` computed); `_clearMcpForm` rewritten to `vueMcp.resetForm()` + `_clearSmartFillDom()` clearing smart-fill legacy DOM
- `onMcpTransportChange` / `updateMcpCmdPreview` return directly on Vue path (transport managed by Vue @change, command preview by computed auto-rendering)
- Legacy innerHTML implementations preserved as `_legacy*`, falling back when Vue is unavailable

### 9. Tech Debt Cleanup: Remove `_legacy` Fallback Functions (Conservative Strategy)
- **models.js** (603→374 lines, -229 lines): Removed 5 `_legacy` functions (`_legacyRenderBuiltin` / `_legacyRenderCustom` / `_legacyEditCustom` / `_legacyAddCustom` / `_legacyToggleAddCustom`); `saveBuiltin` / `clearBuiltin` / `testModel` / `_setProviderRowState` simplified to Vue-only paths; all legacy DOM ID read/write removed (`pk-` / `pm-` / `pmsg-` etc.)
- **knowledge_panel.js** (747→449 lines, -298 lines): Removed 8 `_legacy` functions + 4 card rendering helpers + 2 form helpers (`kbFormClear` / `kbFormFill`); removed `_kb.editType` / `_kb.editId` global variables
- **mcp_settings.js** (857→456 lines, -401 lines): Removed 12 `_legacy` functions; removed `_mcpFormOpen` / `_mcpEditId` / `_mcpToolsCache` global variables; retained `_mcpActiveTab` (still used by smart-fill `switchMcpTab`)
- **Conservatively retained**: Chat island outline/ask_user fallback (`_legacyOutlineBody` / `_legacyAskUserBody`) — core streaming safety net; `_origOpenOverlay` hook retained; `_kb.tab` / `_kb.previewRecs` / `_kb.sourceFile` retained (still used by import zone)
- Three files total **928 lines** of `_legacy` code removed; 0→0 `_legacy` references remaining (except comments)
- Syntax check passed; all 21 API smoke tests pass

### 10. Stage 6 Evaluation Conclusion
- After evaluation, the conversation sidebar (saved-list + load/rename/delete buttons + autosave-status row) has no significant pain points: low state complexity, low update frequency, small list size, no known bugs
- Migration benefits do not outweigh the dual-track maintenance cost; decided to **skip Stage 6** and proceed directly to Stage 7 (settings-type modals)
- Stage 7 split into 7A (ov-settings, completed) / 7B (ov-knowledge, completed) / 7C (ov-mcp, completed), each executed independently; Vue progressive integration **concluded**

### 11. Documentation and Testing
- `docs/conventions.md`: added Vue progressive integration stage plan, per-stage acceptance status, and constraints (5B callback injection, 7A three-mount-point shared state, 7B five-mount-point + import-not-migrated + optimistic update, 7C two-mount-point + smart-fill-not-migrated + bridge, tech debt cleanup conservative strategy, etc.)
- `docs/architecture.md`: added Vue island architecture description, key constraints 13/14/15/16, responsibility boundary table (including knowledge_panel.js / mcp_settings.js); updated post-cleanup to remove _legacy fallback references
- `docs/step7_plan.md`: Stage 7 split planning and 7A/7B/7C implementation records, Vue progressive integration conclusion reference
- `docs/changelog.md`: independent entries per sub-step
- `Notes for development.md`: added "Current Progress Overview" table (including tech debt cleanup); main index now includes `step7_plan.md`
- `Test/test_api_smoke.py`: all 21 tests pass, including `vue_app.js` static resource check and `test_no_inline_handlers_remain` inline handler check

### 12. Vue Conclusion Constraint: New Features Must Not Use createElement
- After Vue progressive integration concluded, all core rendering paths are managed by Vue islands (Chat message flow + Toast/Loading + Settings/KB/MCP modals)
- **Hard constraint**: New frontend features **MUST NOT** use `document.createElement`, `innerHTML`, or `outerHTML` to build DOM; must instead add or extend a Vue island in `vue_app.js` using reactive state + render function
- Only exception: purely static content (no interaction, no dynamic state) may be written directly in HTML templates
- Existing `innerHTML` in old code is historical legacy and must NOT be used as a pattern for new code
- When adding a new Vue island, `renderAll()` must clear root static HTML at the beginning (prevent double-form bug)
- `docs/conventions.md` updates: overall principles changed from "Vue only as fallback island" to "Vue is core rendering path"; added Principle 9 + Prohibition 1 + 3 new checklist items; "Adding Frontend Features" flow rewritten with Vue-first + island checklist; data flow changed from "transitional" to "current stable state"; "not yet migrated" section notes old code is not a pattern reference
- `docs/architecture.md` updates: responsibility boundary table "future migration direction" column changed to "notes"; added key constraints 17/18 (createElement prohibition + renderAll root clearing)
- `Notes for development.md` updates: progress overview title changed to "concluded"; added "standards" row; document maintenance rules updated

---

