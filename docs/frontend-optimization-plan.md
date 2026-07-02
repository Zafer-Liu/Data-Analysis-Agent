# Data Analysis Agent 前端优化实施方案

> 文档状态：**FE0–FE1 完成，FE2 主体完成，FE3/FE4 核心项完成，FE5 及剩余低 ROI 项暂停（2026-07-01）**  
> 制定日期：2026-06-29  
> 适用范围：聊天工作台、Dashboard、图表详情及其共享前端基础设施  
> 实施策略：渐进式收口，保留 Vue 3 和 Flask，不进行框架重写

## 1. 目标

本方案用于把当前“功能完整但逐渐分叉”的前端，演进为可维护、可测试、可离线运行且具备统一视觉语言的前端体系。

目标包括：

1. 消除远程运行时依赖，保证桌面应用离线可用。
2. 加固 Dashboard、图表 iframe、跨域访问和 HTML 渲染边界。
3. 将脚本加载顺序和 `window.*` 隐式依赖迁移为显式模块依赖。
4. 统一聊天工作台、Dashboard、图表查看器的设计系统。
5. 完成手机、平板、桌面三类布局。
6. 建立可访问性、视觉回归、端到端测试和性能预算。
7. 在不影响现有业务功能的前提下逐阶段替换 Legacy 实现。

## 2. 非目标

本轮优化不包含：

- 将 Vue 改写为 React、Svelte 等其他框架。
- 重写 Flask API 或数据分析工具链。
- 改变模型、知识库、MCP、Workspace、Teams 的业务规则。
- 改变图表算法和分析口径。
- 一次性删除全部兼容接口。

## 3. 当前基线

### 3.1 代码规模

| 范围 | 文件数 | 代码行 | 体积 |
|---|---:|---:|---:|
| 自有 JavaScript | 30 | 约 14,771 | 约 582KB |
| CSS | 8 | 约 5,690 | 约 200KB |
| HTML 模板 | 3 | 约 1,611 | 约 78KB |

主要页面：

- `templates/agent_chat.html`
- `templates/dashboard.html`

主要高复杂度文件：

| 文件 | 规模 | 主要职责 |
|---|---:|---|
| `frontend/features/ui/*.js` | 7 个独立 island | 全局 UI、Chat、Jobs、Settings、Knowledge、MCP、Workspace |
| `frontend/features/chat-stream.js` | 约 1,600 行 | 会话、流式事件、工具事件、卡片、命令；构建为 `stream.js` |
| `static/css/parts/chat.css` | 约 1,740 行 | 消息、工具、欢迎页、Composer |
| `static/css/parts/modals.css` | 约 1,694 行 | 全部弹窗和功能面板 |
| `templates/agent_chat.html` | 约 1,166 行 | 主框架和约 20 个 Overlay |

### 3.2 运行时基线

- 初始聊天页包含约 31 个外部脚本标签；当前为 6 个，其中业务代码仅 1 个 Bundle。
- Vue 3 已切换为本地 vendored 资源。
- 页面通过 `window.BAA.*` 进行模块间通信。
- 约 42 处全局 `window.*` 导出。
- 约 126 处 `fetch()` 调用。
- 约 125 处 `innerHTML` 写入。
- 聊天模板包含约 93 处内联样式。
- 页面存在约 20 个 Overlay，但仅少量具备完整 Dialog 语义。

### 3.3 已完成的视觉基线

UI Phase 0 已完成：

- 聊天、工具流程和 Composer 使用统一内容轨道。
- 1280px 以下二级抽屉切换为浮层。
- 960px 以下侧栏切换为图标轨道。
- 720px 以下启用手机布局。
- 工具调用卡片、团队空状态和移动端弹窗完成第一轮收敛。

当前 `static/css/parts/workspace.css` 是视觉基线覆盖层，后续需要将有效规则合并回正式组件文件，不能长期依赖覆盖叠加。

## 4. 问题分级

### 4.1 P0：可靠性与安全边界

#### P0-1 Vue 远程运行时

现状：

- `templates/agent_chat.html` 已改为加载本地 Vue vendored 资源。
- 桌面应用离线、代理异常或 CDN 不可达时会进入部分 Legacy 回退。

风险：

- 首屏能力不确定。
- Vue 版本和供应链不完全受本地发布包控制。
- 无法建立严格 CSP。

目标：

- Vue 固定版本存放于本地或进入构建产物。
- 生产运行时不依赖公网。

#### P0-2 图表 iframe 权限

现状：

- Dashboard iframe 使用 `allow-scripts allow-same-origin`。
- `/api/chart/<chart_id>` 返回可执行 HTML。
- 图表 HTML 与主应用同源。

风险：

- 图表页面与父页面边界偏宽。
- 无法清晰区分可信应用代码和生成型图表内容。

目标：

- 同源图表 iframe 保留 `allow-scripts allow-same-origin`（去掉 allow-same-origin 会把 iframe 降级为 opaque origin，Plotly 初始化失败）。
- 安全性由图表端点 CSP 兜底：`connect-src 'none'` 禁止外发数据，`frame-ancestors 'self'` 禁止被外部嵌入，`script-src 'self' 'unsafe-inline'` 禁止远程脚本。
- 禁止 `allow-top-navigation` 等逃逸 token，确保 iframe 无法影响父页面。
- 中期将图表迁移为结构化 Chart Spec。
- 长期由父页面共享 Plotly/ECharts Runtime。

#### P0-3 CORS 与安全响应头

现状：

- Flask 应用使用全局 `CORS(app)`。
- 未形成统一 CSP、frame、MIME、referrer 等响应头策略。

目标：

- CORS 限制到桌面应用实际 Origin。
- 写操作继续执行 Origin/Session 边界校验。
- 增加 CSP、`X-Content-Type-Options`、`Referrer-Policy` 等响应头。

#### P0-4 图表详情孤立页面

现状：

- `chart-detail.html` 已确认无注册路由，且已删除。
- 其职责如果未来要保留，应并入正式 Chart Viewer，而不是继续维护独立孤立页。

目标：

- 如需图表详情页，后续统一由正式 Chart Viewer 承接，并复用共享 Markdown、安全和设计系统。

#### P0-5 弹窗焦点边界

现状：

- Overlay 主要通过添加或移除 `.open` 控制。
- 缺少通用焦点陷阱、焦点恢复、背景 `inert`。
- 大部分 Overlay 没有完整 Dialog 语义。

目标：

- 建立统一 Dialog Manager。
- 所有 Modal 具备键盘和屏幕阅读器可访问性。

### 4.2 P1：架构与维护成本

#### P1-1 `vue_app.js` 巨石化

需要拆分为：

```text
frontend/
  core/
    api-client
    event-bus
    app-store
    modal-manager
  components/
    Button
    Dialog
    EmptyState
    DataTable
    ToolTimeline
    ChartFrame
  features/
    chat
    tools
    jobs
    models
    datasource
    workspace
    teams
    knowledge
    mcp
```

#### P1-2 模块通过加载顺序耦合

目标：

- 使用 ES Modules 和显式 import/export。
- 在迁移期保留 `window.BAA` 兼容适配器。
- 业务模块不得新增全局函数。

#### P1-3 HTTP 请求分散

建立统一 API Client：

```javascript
request(url, {
  method,
  body,
  signal,
  timeout,
  retry,
  errorMode
})
```

统一处理：

- JSON 和非 JSON 响应。
- HTTP 错误。
- 超时与取消。
- Session 失效。
- 用户可见错误。
- 请求日志和关联 ID。

#### P1-4 遗留文件

重点检查：

- [x] `static/js/agent_chat.js`（确认模板、构建入口和源码均无引用后删除）
- Vue Island 的 Legacy `innerHTML` 回退代码
- 已被 Vue 接管但仍保留的旧 DOM 渲染分支

删除前必须通过引用扫描、页面回归和打包审计。

### 4.3 P1：CSS 和设计系统

#### P1-5 多层覆盖

现状：

- `chat.css` 内存在新旧 Composer 规则。
- `workspace.css` 作为最终覆盖层。
- `modals.css` 同时承载十余类功能。

目标目录：

```text
styles/
  foundations/
    tokens.css
    reset.css
    typography.css
  layout/
    app-shell.css
    sidebar.css
    responsive.css
  components/
    buttons.css
    forms.css
    dialogs.css
    tables.css
    cards.css
  features/
    chat.css
    tools.css
    composer.css
    teams.css
    workspace.css
    knowledge.css
    dashboard.css
```

#### P1-6 内联样式与硬编码

目标：

- `agent_chat.html` 内联样式归零。
- 显示状态使用 `hidden`、状态类或 Vue 条件渲染。
- 颜色、间距、阴影、层级只使用 Token。
- 除第三方兼容规则外不使用 `!important`。

#### P1-7 图标体系

目标：

- 使用本地 SVG Sprite。
- 统一 16px、20px、24px 三档。
- Emoji 仅作为内容表达，不再作为基础操作图标。

### 4.4 P1：响应式与可访问性

#### P1-8 Dashboard 没有响应式断点

390px 实测：

- 顶部导航内容宽于视口。
- 保存布局操作被裁切。
- GridStack 没有手机单列策略。

目标：

- 768px 以下顶部导航拆分或使用更多菜单。
- 图表强制单列。
- 禁用触摸端拖拽缩放。
- 支持上下排序和全屏查看。

#### P1-9 语义与焦点

目标：

- 页面增加 `<main>`。
- 每个页面存在唯一 `<h1>`。
- Dialog 使用 `role="dialog"`、`aria-modal` 和标题关联。
- Toast、加载、流式状态使用适当的 `aria-live`。
- 图标按钮具备中文可访问名称。
- 消息操作在键盘聚焦时可见。

### 4.5 P2：性能与体验

#### P2-1 首屏加载过多

改为动态加载：

- 首屏：Chat、Composer、基础状态。
- 打开设置：Models。
- 打开团队：Teams。
- 打开知识库：Knowledge。
- 打开 MCP：MCP。
- Dashboard 页面：GridStack。

#### P2-2 图表 Runtime 重复解析

Plotly 本地包约 4.7MB，ECharts 约 1.1MB。Dashboard 每个 Widget 使用独立 iframe，多个 iframe 会重复初始化运行时。

演进路线：

```text
可执行 Chart HTML
→ Chart iframe 加固
→ ChartSpec JSON
→ 父页面共享 Renderer
```

#### P2-3 长会话 DOM 持续增长

目标：

- 超过 100–150 条消息后启用窗口化。
- 历史工具详情按需挂载。
- 离屏图表暂停或卸载。
- 流式文本更新按动画帧合并。

#### P2-4 国际化不完整

目标：

- 所有用户可见字符串进入 i18n。
- 禁止业务组件新增硬编码中文或英文。
- API 错误码和显示文本分离。

## 5. 实施路线

### 5.1 FE0：安全与可靠性基线

预计：2–3 个工作日。

#### 任务

- [x] Vue 3 改为本地固定版本。
- [x] 增加 CSP 和安全响应头。
- [x] CORS 限制到允许 Origin。
- [x] 图表 iframe sandbox 定型为 `allow-scripts allow-same-origin`（收紧为纯 allow-scripts 导致 Plotly 在 opaque origin 下初始化失败、聊天区图表空白，已回归修复；安全改由图表端点 CSP 兜底）。
- [x] 审计并处理 `chart-detail.html`（已确认无注册路由，已删除孤立模板）。
- [x] 建立 Dialog Manager。
- [x] 修复 Dashboard 390px 顶栏裁切。
- [x] 为上述边界增加自动化测试。

#### 验收标准

- [x] 页面运行不请求公网脚本。
- [x] 未授权 Origin 不获得跨域访问许可，且携带未知 Origin 的写请求返回 403。
- [x] 图表 iframe 无法读取或修改父页面。
- [x] 所有标准 Overlay Modal 支持 Escape、焦点陷阱和焦点恢复。
- [x] 390px 页面不存在不可访问的顶栏操作。
- [x] 浅色和暗色主题无回归。

#### 回滚策略

- 本地 Vue 保留与现有全局 `Vue` 相同接口。
- Dialog Manager 先适配现有 Overlay DOM，不立即重写弹窗内容。
- iframe 权限调整按图表类型灰度验证。

### 5.2 FE1：模块化与构建基础

预计：4–6 个工作日。

#### 任务

- [x] 引入 Vite，仅作为构建工具。
- [x] 建立 Chat 和 Dashboard 两个入口。
- [x] 引入 ESLint、Prettier 和构建检查。
- [x] 建立 `api-client`。
- [x] 建立 App Store 和 Event Bus。
- [x] 拆分 `vue_app.js`。
- [x] 建立 `window.BAA` 兼容适配器。
- [x] 删除确认无引用的 Legacy 文件。

#### 验收标准

- [x] 聊天页首屏同步业务入口不超过 3 个（当前 1 个 ESM 入口；另有 5 个首次使用时加载的 UI chunk）。
- [x] 全局 `window.*` 业务接口不超过 5 个（`window.t/getLang/setLang/applyI18n` 保留为兼容别名，`BAA` 下业务接口已收敛）。
- [x] 所有新增请求通过统一 API Client。
- [x] Bundle 可离线构建。
- [x] Flask 只分发构建产物，不需要生产环境 Node.js。
- [x] 现有 Python 测试全部通过。

#### 已实施的兼容切片（2026-06-30）

> 本节记录 2026-06-30 的迁移过程快照，其中 IIFE、单 Bundle 和阶段性体积数据用于保留演进历史；当前架构以 2026-07-01 的“ES Module 与按需 Island”小节为准。


- Vite 固定依赖由 `pnpm-lock.yaml` 锁定，构建输出到 `static/dist/`。
- Chat 使用单一 IIFE 入口 `chat-app.js`，Dashboard 使用稳定 ES Module 入口
  `dashboard.js`；不再生成无人加载的 `chat.js`。
- 入口通过 `window.BAA.api` 暴露统一 API Client；旧脚本继续运行，后续按 Feature 逐步迁移。
- API Client 统一 JSON 编码、同源凭据、空响应处理和结构化 `ApiError`。
- Flask 模板只引用已生成的静态 Bundle，部署和桌面运行阶段不要求安装 Node.js。
- `dom`、`theme`、`overlay` 已迁入 `frontend/core/`，通过显式 import/export 构建为同步
  `static/dist/core.js`；同步加载用于保持尚未迁移模块的执行顺序。
- 三个旧 `static/js/modules/*.js` 文件已删除，聊天页脚本请求由 33 个降至 31 个。
- 浏览器回归已验证主题切换、弹窗打开/Escape 关闭及零控制台错误。
- `models`、`skills`、`slash` 已迁入 `frontend/features/`，通过
  `frontend/core/runtime.js` 显式共享状态和 DOM 接口。
- 对应三份旧脚本及模板引用已删除，聊天页脚本请求进一步由 31 个降至 28 个；
  `core.js` 缓存版本提升到 `fe1-core-2`。
- 浏览器回归已验证模型选择器、22 项 Skill 列表和 16 条斜杠命令均正常加载，
  且无控制台错误。
- `workspace`、`teams` 已迁入 `frontend/features/` 并合入 `core.js`；
  `knowledge`、`mcp` 使用延后加载的 `panels.js`，确保 Vue island 已初始化后再注入回调。
- 四份旧脚本和模板引用已删除，聊天页脚本请求由 28 个降至 25 个；
  `core.js` 缓存版本提升到 `fe1-core-3`，`panels.js` 使用 `fe1-panels-1`。
- 浏览器回归已验证工作目录最近记录、团队空状态、知识库指标列表和 MCP 服务器列表，
  且无控制台错误。
- `chat_stream` 已迁入 `frontend/features/chat-stream.js`，通过
  `frontend/core/page-runtime.js` 显式导入页面运行时依赖，并独立构建为
  `static/dist/stream.js`；原 `static/js/modules/chat_stream.js` 已删除。
- 模板保持原有同步加载位置并使用缓存版本 `fe1-stream-1`，避免改变尚未迁移脚本的
  启动时序；浏览器回归已验证空输入发送和 16 条斜杠命令展开，且无控制台错误。
- 原 `static/js/modules/vue_app.js` 已迁移并拆分为 7 个独立模块：
  `global-ui`、`job-history-ui`、`chat-ui`、`settings-ui`、`knowledge-ui`、
  `mcp-ui`、`workspace-ui`。它们由 `frontend/features/vue-app.js` 统一编排，
  构建为单一 `static/dist/ui.js`，不增加页面请求数。
- `ui.js` 使用缓存版本 `fe1-ui-1`，当前构建体积 73.17 KB（gzip 22.49 KB）；
  浏览器回归已验证各 island 渲染、任务历史弹窗和 16 条斜杠命令，且无控制台错误。
- 新增 `frontend/core/ui-registry.js`，将原先分散的 `window.BAA.vueChat`、
  `vueJobHistory`、`vueSettings`、`vueKb`、`vueMcp`、`vueWorkspace` 收敛到
  单一 `BAA.ui` 注册表；已迁移模块通过 `getUiIsland()` 显式访问。
- 浏览器回归已验证任务历史、37 条知识库指标、MCP 服务器、2 个最近工作目录和
  16 条斜杠命令均可用，且无控制台错误。
- 新增 `frontend/core/app-store.js` 与 `event-bus.js`：Store 以 Proxy 保持旧
  `state.xxx` 写法兼容，同时发布 `state:change` / `state:<key>`；Event Bus
  提供 `on`、`off`、`once`、`emit`、`clear`，并承载 UI island 生命周期事件。
- 独立行为测试覆盖订阅、取消订阅、批量 patch、快照和一次性事件；浏览器回归验证
  页面启动、UI island、斜杠命令和任务历史状态切换，且无控制台错误。
- 引入 ESLint 9、Prettier 3 和 Flat Config；ESLint 覆盖已模块化的
  `frontend/**/*.js`（暂排除 `frontend/legacy`）与 Vite 配置，Prettier 当前覆盖
  `frontend/core`、`frontend/entries` 和构建配置，避免一次性重排兼容模块。
- 新增 `pnpm quality` 统一门禁，顺序执行格式检查、ESLint、Vite 全量构建
  和 72 项 Python 前端契约测试；当前整套门禁已通过。
- 新增有序入口 `frontend/entries/chat-app.js`，按原模板顺序聚合 i18n、状态、
  Core、UI island、数据源、Stream、Panels 和主启动逻辑；模板只加载
  `static/dist/chat-app.js`，旧独立 Core/UI/Stream/Panels 构建配置已删除。
- 聊天页脚本标签由 25 个降至 6 个（主题引导、桌面生命周期、3 个本地 Vendor、
  1 个业务 Bundle），业务 Bundle 由 5 个收敛为 1 个；`chat-app.js` 为
  289.19 KB（gzip 87.47 KB）。
- `pnpm quality` 与 72 项契约测试已通过；本轮真实浏览器回归因本地 Browser 插件
  缺失 `scripts/browser-client.mjs` 暂未执行，待插件恢复后补测。
- 原由 `chat-app.js` 直接读取的 15 份 `static/js` 业务源码已迁入
  `frontend/legacy/`，聚合入口不再从静态发布目录反向导入源码；对应旧路径和无引用
  文件已删除，`static/js/modules/` 仅保留模板在 `<head defer>` 使用的桌面生命周期脚本。
- 迁移后 `chat-app.js` 体积保持 289.19 KB（gzip 87.47 KB），`pnpm quality`
  和 72 项契约测试继续通过；`frontend/legacy` 将按功能逐项改为显式模块接口。
- `markdown`、`command_handlers`、`msg` 三个基础兼容模块已改为 ES Module 显式导出，
  所有调用方改用 `import`；已删除 `window.renderMd`、`BAA.markdown`、
  `BAA.commandHandlers`、`BAA.msg` 以及顶层消息函数等隐式业务接口。
- 新增契约测试禁止上述全局接口回流；`chat-app.js` 降至 288.01 KB（gzip 87.21 KB），
  `pnpm quality` 和 73 项契约测试通过。
- `datasource`、`sessions`、`autosave` 已改为显式导出，主启动器、Chat Stream、
  Workspace 和 Checkpoint 均通过 `import` 调用；删除 `BAA.datasource`、
  `BAA.sessions`、`BAA.autosave` 与 `window.setSrc`。
- Core Overlay 不再反向读取数据源全局对象，改由 Event Bus 发布 `overlay:open`，
  数据源模块按需刷新连接配置；新增契约测试阻止四个旧全局接口回流。
- 本切片完成后 `chat-app.js` 降至 285.67 KB（gzip 86.47 KB），`pnpm quality`
  和 74 项契约测试通过。
- `preview`、`job_history`、`checkpoints`、`app_settings`、`update` 已改为显式导出，
  调用方改用 `import`；删除对应五个 `BAA.*` 业务全局，确认弹窗、Workspace、
  Job History 和 Chat Stream 依赖均通过模块接口或 UI Registry 连接。
- 引用审计确认并删除 67 KB 的旧单体 `static/js/agent_chat.js`，同时删除只生成
  无人加载产物的 `frontend/entries/chat.js`，Vite 不再输出 `static/dist/chat.js`。
- 当前 `static/dist/` 只保留 Dashboard、Chat App 与 Manifest 三个必要文件；
  `chat-app.js` 降至 284.64 KB（gzip 85.96 KB），`pnpm quality` 和 76 项契约测试通过。
  后续继续收敛 i18n、临时提示词与主启动器兼容面，最后移除 `BAA.state`。
- `mcp` 和 `knowledge` 面板的 30+ 个函数通过 `Object.assign(globalThis, mcp, knowledge)`
  污染全局的接口已删除；`app.js` 和 `chat-stream.js` 改为通过 `BAA.mcp.*` /
  `BAA.knowledge.*` 调用，彻底消除对应的全局函数泄漏。
- `i18n.js` 的 `window.t/getLang/setLang/applyI18n` 改为先在 IIFE 内定义命名函数，
  再发布到 `BAA.i18n` 并保留 `window.*` 兼容别名；调用方不再直接写入全局。
- `temp_prompt_panel.js` 的 `window.tp*` 全局接口改为发布到 `BAA.tempPrompt`；
  `app.js` 和 `chat-stream.js` 改为通过命名空间调用。
- `eslint.config.js` 将 `frontend/legacy/**` 整体排除改为精细排除：13 个已完全
  模块化的 legacy 文件纳入 ESLint 检查（语法和结构规则），仅保留 `state.js`
  和 `app.js` 在排除列表（IIFE 引导和大型兼容层）。
- 新增 5 项契约测试固化上述全局接口收敛和 ESLint 覆盖范围，`pnpm quality` 通过
  81 项前端+契约测试，FE1 全部 10 个迁移步骤完成，进入 FE2 设计系统治理阶段。

#### ES Module 与按需 Island（2026-07-01）

- Chat 入口由单文件 IIFE 切换为原生 ES Module，入口保持
  `static/dist/chat-app.js`，动态模块输出到带内容哈希的 `static/dist/chunks/`。
- 首屏只同步挂载 Global 与 Chat 两个 UI surface；Settings、Knowledge、MCP、
  Workspace 和 Job History 在首次使用时通过 `ensureUiIsland()` 加载并挂载。
- 七个 UI 模块由 import 副作用 IIFE 改为显式 `mount*Ui()` 导出，首次交互会等待
  挂载完成后再同步业务数据，避免异步 chunk 引入初始化竞态。
- 主入口由 286.16 KB（gzip 86.36 KB）降至 245.14 KB（gzip 74.50 KB），五个
  非首屏 chunk 为 7.05–10.89 KB（gzip 1.99–3.97 KB）。
- `frontend/legacy/` 已恢复完整 `no-undef` 与 `no-unused-vars` 检查；`state.js` 和
  `app.js` 也不再排除。`frontend/README.md` 明确 `frontend/` 为唯一源码目录。

#### 迁移顺序

1. [x] `dom`、`theme`、`overlay`
2. [x] `models`、`skills`、`slash`
3. [x] `workspace`、`teams`、`knowledge`、`mcp`
4. [x] `chat_stream`
5. [x] `vue_app` 源码迁移与 island 拆分
6. [x] 删除 `window.BAA.vue*` 兼容门面
7. [x] `markdown`、消息、命令、数据源、会话与自动保存显式模块化
8. [x] Preview、Job History、Checkpoint、设置与更新显式模块化
9. [x] i18n、临时提示词与主启动器全局面收敛
10. [x] 移除 `BAA.state` 并将 Legacy 纳入完整静态检查

### 5.3 FE2：设计系统治理

预计：3–5 个工作日。

#### 任务

- [x] 建立正式 Design Token 层。
- [x] 合并 `workspace.css` 有效规则并删除覆盖层文件。
- [x] 删除旧 Composer 规则。
- [ ] 拆分 `chat.css` 和 `modals.css`。
- [x] 清理 93 处内联样式。
- [ ] 建立基础组件样式。
- [ ] 引入 SVG Icon Sprite。
- [ ] 统一 Chat、Dashboard、Chart Viewer。

#### 验收标准

- [ ] HTML 内联样式归零。
- [ ] 非第三方兼容规则不使用 `!important`。
- [ ] 同一组件不存在多份互相覆盖的定义。
- [ ] 五档视口视觉回归通过。
- [ ] 明暗主题使用同一组件规则。
- [ ] 关键文本符合 WCAG AA 对比度。

### 5.4 FE3：聊天与工具体验

预计：4–6 个工作日。

#### 任务

- [x] 工具流程统一为 Tool Timeline。
- [x] 失败工具自动展开。
- [ ] 数据依据、知识引用、工具审计合并为 Evidence Panel。
- [ ] 长报告增加目录和回到顶部。
- [ ] 表格增加吸顶表头、全屏和导出。
- [ ] 图表增加标题、来源、生成时间和操作栏。
- [ ] Ask User 使用确定性选项 Schema。
- [ ] Composer 手机端增加更多菜单。
- [x] 统一 Queue、Stop、Retry 和 Error 状态。

#### 验收标准

- [ ] 20 步工具流程可以快速定位失败项。
- [ ] 展开内容显示完整信息，不与折叠摘要重复。
- [ ] Ask User 对象不会渲染为 `[object Object]`。
- [ ] 宽表不会撑破消息区域。
- [ ] 移动端核心能力无需横向滚动才能访问。

### 5.5 FE4：Dashboard 与图表体系

预计：4–6 个工作日。

#### 任务

- [x] Dashboard 顶栏响应式重构（新增 768px 断点）。
- [x] 小屏单列布局（动态 resize 切换）。
- [x] 小屏关闭拖拽和缩放。
- [ ] Widget 状态组件化。
- [ ] Session ID 文本输入改为会话选择器。
- [x] 刷新请求支持取消和超时（AbortController + 30s/60s）。
- [ ] Chart Viewer 替代孤立详情页。
- [ ] 定义 ChartSpec。
- [ ] 试点父页面共享 Plotly/ECharts Runtime。

#### 验收标准

- [ ] 390px、768px、1024px、1440px 均可完整操作。
- [ ] Dashboard 10 图表下保持可交互。
- [ ] 图表具备加载、空、错误、成功和刷新状态。
- [ ] Session 过期时提供明确恢复路径。
- [ ] 图表全屏支持 Escape 和焦点恢复。

### 5.6 FE5：测试、性能和可访问性门禁

预计：3–5 个工作日。

#### 任务

- [ ] Vitest 覆盖 Store、API Client 和状态转换。
- [ ] Playwright 覆盖核心用户流程。
- [ ] axe 自动检查。
- [ ] 五档视口视觉回归。
- [ ] Bundle 分析和性能预算。
- [ ] 长会话压力测试。
- [ ] 多图表 Dashboard 压力测试。
- [ ] 建立发布前端门禁。

#### 验收标准

- [ ] axe 无 Critical/Serious 问题。
- [ ] 首屏自有压缩 JS 目标不超过 250KB。
- [ ] 非首屏功能按需加载。
- [ ] 100 条消息下输入和滚动无明显卡顿。
- [ ] 前端错误可被统一记录和定位。
- [ ] CI 能阻止构建、类型、可访问性和视觉回归问题。

## 6. 依赖关系

```text
FE0 安全可靠性
  └─ FE1 模块与构建基础
       ├─ FE2 设计系统
       │    ├─ FE3 聊天体验
       │    └─ FE4 Dashboard/图表
       └─ FE5 测试与质量门禁
```

FE0 必须先完成。FE3 和 FE4 可以在 FE2 基础组件稳定后并行，但不应在 FE1 之前继续扩大 Legacy 全局接口。

## 7. 当前目录与构建产物

```text
frontend/
  entries/
    chat-app.js
    dashboard.js
  core/
    api-client.js
    event-bus.js
    app-store.js
    ui-registry.js
    overlay.js
  features/
    vue-app.js
    chat-stream.js
    models.js
    knowledge.js
    mcp.js
    workspace.js
    ui/
      global-ui.js
      chat-ui.js
      job-history-ui.js
      settings-ui.js
      knowledge-ui.js
      mcp-ui.js
      workspace-ui.js
  legacy/
    app.js
    state.js
    datasource.js
    sessions.js
```

构建产物输出到：

```text
static/dist/
  chat-app.js
  dashboard.js
  chunks/*-[hash].js
  .vite/manifest.json
```

聊天模板只直接引用固定的 `chat-app.js` ESM 入口；浏览器根据入口中的动态 import 按需请求 hash chunk。`static/dist/` 为生成目录，不得手工编辑。

## 8. 质量指标

| 指标 | 当前基线 | 最终目标 |
|---|---:|---:|
| 聊天页业务加载 | 约 31 个脚本标签 | 1 个首屏 ESM 入口 + 5 个按需 UI chunk |
| 全局 `window.*` 导出 | 约 42 | ≤ 5 |
| HTML 内联样式 | 约 93 | 0 |
| 远程运行时脚本 | 1 | 0 |
| 统一 API Client 覆盖 | 低 | 100% 新代码，逐步覆盖旧代码 |
| 完整 Dialog 语义 | 少量 | 100% |
| Dashboard 响应式断点 | 0 | 至少 3 档 |
| 前端自动化测试 | 以静态契约为主 | Unit + E2E + axe + Visual |
| 首屏自有压缩 JS | 待测量 | ≤ 250KB |

## 9. 风险管理

### 9.1 模块化迁移破坏加载顺序

措施：

- 每次只迁移一个 Feature。
- 保留 `window.BAA` 兼容层。
- 为每个迁移模块增加契约测试。

### 9.2 iframe 权限收紧导致旧图表失效

已发生（2026-06-29）：FE0 第一批把图表 iframe sandbox 从 `allow-scripts allow-same-origin` 收紧为纯 `allow-scripts`，浏览器把同源 iframe 降级为 opaque origin，Plotly 初始化时读取 computed style / storage 抛 SecurityError，导致聊天区图表空白（全屏新标签页无 sandbox 不受影响）。

修复：

- 恢复 `allow-scripts allow-same-origin`：同源 iframe 本就同源，加 allow-same-origin 只是"不降级 origin"，不赋予跨域能力。
- 安全改由图表端点 CSP 兜底：`connect-src 'none'` + `frame-ancestors 'self'` + `script-src 'self' 'unsafe-inline'`。
- 禁止 `allow-top-navigation`（真正能让 iframe 影响父窗口的危险 token）。
- 新增回归测试 `test_chart_endpoint_iframe_security_contract` 固化安全契约。

教训：

- 同源 iframe 不要去掉 allow-same-origin，opaque origin 会破坏大量依赖同源 API 的库（Plotly、ECharts、d3 等）。
- iframe 隔离的正确抓手是 CSP（限制能加载什么、能连什么）+ 禁止逃逸 token，而非降级 origin。
- 建立 41 类图表 smoke matrix，先验证消息图表，再验证 Dashboard。

### 9.3 CSS 合并造成视觉回归

措施：

- 建立五档视口截图基线。
- 每次只移动一个组件的规则。
- 旧规则在新规则验收后再删除。

### 9.4 Vite 增加构建复杂度

措施：

- Node.js 仅用于开发和发布构建。
- 安装包只包含 `static/dist`。
- 保留可重复构建和产物审计脚本。

## 10. 执行约束

1. 不在同一提交中同时重构业务逻辑和视觉。
2. 每个阶段必须有独立测试和回滚点。
3. 不新增未经 Token 管理的颜色和尺寸。
4. 不新增 `window.*` 业务全局。
5. 不新增直接 `fetch()`，统一走 API Client。
6. 不新增无 Dialog 语义的 Overlay。
7. 不新增远程 CDN 运行时依赖。
8. 不以扩大 `!important` 或覆盖层作为长期修复。

## 11. 推荐下一步

立即开始 FE0，建议按以下顺序：

1. FE0 自动化门禁。
2. Dashboard 手机顶栏和小屏交互复核。
3. 图表/消息界面剩余视觉收口。
4. FE1 构建与模块化拆分预研。

FE0 完成后再进入 Vite 和 ES Modules 迁移，避免在当前全局脚本体系上继续增加功能。
