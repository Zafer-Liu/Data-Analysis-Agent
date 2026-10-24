# Frontend (智析 Agent / BAA)

> 前端源码目录与开发入口说明。本文是 `docs/05-architecture-improve.md`
> 中「前端路径说明」引用的权威文件：旧任务表里的 `static/js/modules/*.js`
> 与 `static/js/*` 是历史路径，**不代表当前开发入口**。新改动以本文与
> [`docs/03-conventions.md`](../docs/03-conventions.md) 为准。

本目录是「ES Module + Vue render-function island + Legacy IIFE 全局模块」
的混合架构，处于渐进式迁移的**收官**阶段：Vue 已接管核心渲染路径
（Chat 消息流、全局 Toast/Loading/Confirm、Settings/KB/MCP/Workspace 等模态框），
原生 JS 模块继续承载业务逻辑，但不再直接操作 DOM。

---

## 目录结构

```
frontend/
├── config/         # Vite 构建配置（显式以仓库根目录为 root）
├── entries/        # 构建入口（Vite input），按页面/功能聚合 side-effect 导入
│   ├── chat-app.js     # 聊天主应用入口（agent_chat.html 的唯一 <script type="module">）
│   ├── dashboard.js    # 仪表盘独立页入口
│   ├── legacy-core.js  # DOM/overlay/event 委托等基础 side-effect 模块聚合
│   ├── legacy-ui.js    # 旧 UI 模块聚合
│   ├── legacy-stream.js# SSE 流处理聚合
│   └── legacy-panels.js# 侧边面板聚合
├── core/           # 无业务依赖的基础设施 ES Module
│   ├── ui-registry.js  # Vue island 注册表（registerUiIsland/getUiIsland），globalThis.BAA.ui 门面
│   ├── event-bus.js    # 发布订阅事件总线
│   ├── runtime.js      # 运行态（state 单例 + to_dict）；window.BAA.state
│   ├── app-store.js    # 响应式 store（Proxy + eventBus）—— ⚠ 当前为死代码，见下文
│   ├── dom.js          # DOM 工具（esc、$、事件委托 helpers）
│   ├── overlay.js      # overlay 开关 + 栈管理（__baaOverlayStack）
│   ├── api-client.js   # BAA.api 统一 HTTP 客户端 —— ⚠ 当前零调用，见下文
│   ├── local-data-store.js / local-storage-setup.js  # 浏览器本地持久化（File System Access + IndexedDB 回退）
│   ├── markdown.js     # renderMd()（marked + DOMPurify）
│   ├── theme.js        # 主题切换（night/sun）
│   ├── webgpu-compute.js # WebGPU 能力探测
│   ├── page-runtime.js # 页面级运行时引导
│   └── legacy-bridge.js# 旧全局 ↔ ES Module 桥接
├── features/       # 业务逻辑 ES Module（不直接操作 DOM，委托 Vue island）
│   ├── ui/             # ── Vue island（render function，无 SFC）──
│   │   ├── chat-ui.js          # Chat 消息外壳/文本流/工具步骤（首屏 eager）
│   │   ├── global-ui.js        # Toast/Loading/Confirm（首屏 eager，注册 BAA.ui.*）
│   │   ├── settings-ui.js      # Settings 模态框（按需 chunk）
│   │   ├── knowledge-ui.js     # 知识库模态框（按需 chunk）
│   │   ├── mcp-ui.js           # MCP 模态框（按需 chunk）
│   │   ├── workspace-ui.js     # 工作目录模态框（按需 chunk）
│   │   ├── job-history-ui.js   # Job 历史模态框（按需 chunk）
│   │   ├── business-canvas-ui.js # 商业画布（draw.io）（按需 chunk）
│   │   └── drawio-postmessage.js # draw.io iframe postMessage 协议
│   ├── vue-app.js      # island 编排：mountVueApp() 首屏挂载 + ensureUiIsland() 按需动态 import
│   ├── chat-stream.js  # SSE 流逻辑（SSE_HANDLERS 注册表，27 个事件 handler）
│   ├── markdown-stream.js # 稳定 Markdown / 尾部切分（减少全量重绘）
│   ├── sse-retry.js    # SSE 自动重试（零事件网络失败，max 2 次，1s/3s）
│   ├── models.js / knowledge.js / mcp.js / workspace.js / skills.js / business-canvas.js / slash.js / sidebar.js
│   ├── teams.js        # 团队面板编排壳（F5 第二轮拆分后 335 行：local 状态 + renderPanel 组装 + 编排层）
│   └── teams/          # teams.js 拆出的域模块（shared-context 模式，同 legacy/settings/）
│       ├── shared.js   # 16 个纯函数（formatTime/statusLabel/workflowDagLevels/teamHasRunningMembers 等）
│       ├── api.js      # createTeamApi(ctx)：fetchJson + 11 个 fetch 函数
│       ├── team-actions.js      # createTeamActions(ctx)：清空消息/解散团队（双重确认）
│       ├── workflow-actions.js # createWorkflowActions(ctx) + WORKFLOW_TEMPLATES：47 个 workflow 业务函数
│       ├── team-views.js        # createTeamViews(ctx)：14 个 teams render 函数（含 renderHeader）
│       └── workflow-views.js    # createWorkflowViews(ctx)：22 个 workflow render 函数
└── legacy/         # 历史遗留 IIFE 全局模块（仍工作，但不再作为新代码范例）
    ├── state.js        # window.BAA.state 全局可变单例（194 处直接赋值的实际状态源）
    ├── app.js          # data-action / data-change 全局事件委托
    ├── i18n.js         # BAA.i18n 命名空间 + window.t 兼容别名
    ├── msg.js / sessions.js / autosave.js / datasource.js / preview.js / ...
    └── app_settings.js # ⚠ ~1800 行遗留巨石（F5 已拆出 GPU/Memory/Hooks/LLM → settings/，剩余 Embed/Bots/Storage/General 待拆）
    └── settings/ # app_settings 拆出的域模块（shared-context 模式）
        ├── shared.js # 共享 helper（toast/renderSwitch/_renderPanelHead/读写 localStorage）+ makeSettingsCtx
        ├── gpu.js # createGpuDomain(ctx)
        ├── memory.js # createMemoryDomain(ctx)
        ├── hooks.js # createHooksDomain(ctx) + DEFAULT_HOOKS_TEXT
        └── llm.js # createLlmDomain(ctx)：renderLlm + loadLlmData + 16 个 _llm* 辅助
```

`static/js/` 下另有 `dashboard.js`、`login.js`、`modules/account_menu.js` 等
**独立页面的经典浏览器脚本**（IIFE，非 ES Module，不经 Vite 构建，由各页面
`<script>` 直接引用）。它们与聊天主应用隔离，各有独立 CSP。

---

## 构建管线

```
vite build --config frontend/config/vite.chat-app.config.js
  ├── input: frontend/entries/chat-app.js
  ├── base: /static/dist/        ← 必须与 HTML 服务路径一致，否则 chunk preload 失效
  ├── manualChunks: ui-registry + event-bus 强制进共享 chunk
  └── output: static/dist/
        ├── chat-app.js          ← 入口产物（agent_chat.html 引用，带 cache 版本号）
        └── chunks/<name>-<hash>.js  ← 7 个按需 island chunk
            ↓
node scripts/build-chat.mjs      ← 3 阶段：clean stale chunks → vite build → verify bundle
  └── verify-chat-bundle.mjs 校验：体积、anti-double-load 守卫、base 路径、chunk 无反向依赖
```

- **入口页** `templates/agent_chat.html` 以唯一 `<script type="module" src="/static/dist/chat-app.js?v=...">` 加载产物；CSP 为 `script-src 'self'`。
- **anti-double-load 守卫**：`__baaAppLoaded` / `__baaAppDelegationRegistered` / `__baaOverlayStack` / `__baaOverlayDocumentListenersRegistered`，由 `verify-chat-bundle.mjs` 校验存在。
- **入口顺序敏感**：`entries/chat-app.js` 的 side-effect 导入顺序对应旧模板的 `<script>` 顺序，改动需谨慎。

### 质量门禁

```bash
npm run quality   # format:check → lint → build:check → test:frontend → python 工具清单 → python unittest
```

- `test:frontend` 用 Node 内置 test runner 跑 `tests/*.test.mjs`，`--test-concurrency=1`（共享 `globalThis.BAA` 态需串行）。
- 契约测试覆盖：SSE 重试、Markdown 流切分、chat bundle 完整性、ui-registry 注册语义。
- 修改前端后必须跑全量门禁；**不要用 `pnpm`**（本机网络沙箱拦截，统一用 `npm`/`npx`）。

---

## Vue island 模式

所有动态 UI 必须走 Vue island。Island = `Vue.h()` + `Vue.render()` 的 render function
（不引入 SFC/Vite 构建链），通过 `registerUiIsland(name, api)` 注册 facade，
业务模块用 `getUiIsland(name)` 取接口，不直接操作 DOM。

```js
// 业务模块（features/xxx.js）标准数据流
import { ensureUiIsland, getUiIsland } from "../core/ui-registry.js";

const island = await ensureUiIsland("settings");   // 非首屏 island 首次调用须 await
getUiIsland("settings").setProviders(data);        // → island 更新 reactive state → Vue 重渲染
```

- **首屏 eager**（`vue-app.js: mountVueApp()`）：`global-ui`（Toast/Loading/Confirm）、`chat-ui`。
- **按需 chunk**（`vue-app.js: islandLoaders` 动态 `import()`）：settings / knowledge / mcp / workspace / jobHistory / businessCanvas。首次业务调用必须 `await ensureUiIsland(name)`。
- **callbacks 注入**：island 不感知业务格式，通过 `callbacks` 参数（`onConfirm`/`onCancel`/`onSubmit`）回流业务逻辑（参考 outline/ask_user 卡片）。
- **挂载点清空**：island `renderAll()` 开头 `root.innerHTML = ""` 清空静态 HTML，防双表单 bug。
- **Markdown 安全渲染**：`bubble.innerHTML = renderMd(text)` 走 marked + DOMPurify，是唯一允许的 innerHTML 赋值。
- `window.BAA.ui` 即 `uiRegistry`：`toast()` / `showLoading()` / `hideLoading()` / `updateLoading()` / `confirm({title,message,confirmText,cancelText,danger})`。

---

## 新增前端功能检查清单

1. HTML 只放骨架 + Vue island 挂载点（`<div id="xxx-vue-root"></div>`），逻辑进 `features/` 或 `legacy/`，样式进 `static/css/parts/`。
2. **动态 UI 走 Vue**：在 `features/ui/` 新增/扩展 island，reactive state + render function。禁止 `document.createElement` / `innerHTML` / `outerHTML` 拼 DOM（例外见下）。
3. 业务逻辑进现有 ES Module，通过 `window.BAA.<module>` 暴露接口；island 通过 callbacks 注入对接。
4. `frontend/legacy/app.js` 注册 `data-action` / `data-change` 事件委托。**禁止内联 `onclick`/`onchange`/`oninput`**。
5. 新文案进 `frontend/legacy/i18n.js`（`BAA.i18n` + `window.t` 别名），同步中英文。
6. **禁止浏览器系统弹窗**：通知用 `window.BAA.ui.toast()`，确认用 `await window.BAA.ui.confirm({...})`，文本输入新增/复用应用内 Vue 表单。
7. **不新增裸 `fetch()`**（见下「已知技术债」）；显隐用 `classList.toggle('hidden')` 而非 `style.display`；颜色/间距/阴影只用 `tokens.css` 的 CSS 变量。
8. 验证浅色/深色、窄窗口、长文本、恢复会话场景；跑 `npm run quality`。

> 完整约束见 [`docs/03-conventions.md`](../docs/03-conventions.md) 的「添加前端功能」「Vue 渐进式接入路线」「禁止事项」三节。

---

## 已知技术债（新代码不得模仿）

下列为审计记录的现状，**不是新代码范例**。新增同类功能必须走 Vue island。

- **`legacy/state.js` 是实际状态源**：`window.BAA.state` 全局可变单例，全前端 194 处直接 `state.X = ...` 赋值。`core/app-store.js` 的响应式系统（Proxy + eventBus）是**死代码**（0 外部订阅），不建议全量激活，新代码停止继续直接写 `state.X`。
- **`core/api-client.js`（BAA.api）零调用**：全前端约 190 处裸 `fetch()`，违反 `03-conventions.md` FE1。401 重定向靠 `legacy/app.js` monkey-patch `window.fetch` 实现。建议 new-code-only 强制走 `BAA.api`，不迁移存量。
- **`legacy/app_settings.js` 为遗留巨石**。F5 已完成：按 shared-context 模式（`createXxxDomain(ctx)`）八域全拆至 `legacy/settings/{shared,gpu,memory,hooks,llm,embed,feishu,storage,general}.js`，主文件降至 247 行纯编排层；`features/teams.js` 六模块拆入 `features/teams/`（主文件 335 行）。新代码不得再把域逻辑塞回编排层。
- ~~**`openOverlay` 三层 monkey-patch 链**~~ 已收敛（F3 中期）：`window.openOverlay` 仅由 `legacy-core.js` 定义一次，`core/overlay.js` 开启后 emit `overlay:open` 事件；消费者（knowledge / temp_prompt_panel / datasource）一律 `eventBus.on("overlay:open", ...)` 订阅，禁止再覆盖全局函数。
- 保留旧 DOM 的区域（import 导入区、MCP smart-fill、会话侧边栏）功能稳定、改动频率低，**不作为新代码范例**。
