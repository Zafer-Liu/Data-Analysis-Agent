# Product

## Register

product

## Platform

web (Flask + vanilla JS, desktop-first with responsive adaptations)

## Product Name

智析 Agent (Business Analytics Agent) — 自然语言交互式经营数据分析软件 V1.0

## Summary

一个面向商业分析场景的 AI Agent 工作台。连接数据源后，用户使用自然语言提问，系统自动完成数据结构识别、SQL 生成与执行、图表生成和业务洞察分析。通过 SSE 流式输出实时展示分析过程。支持本地部署（Windows / macOS / Linux）和云端部署（Railway / Vercel）。

## Users

面向需要在本地或受控工作区完成数据查询、业务分析、复核和报告交付的业务分析人员、数据分析师与团队负责人。用户通常处于连续工作流中，需要快速查看数据、调用分析能力、跟踪团队任务并审计结果来源。

## Product Purpose

提供一个可调用数据工具、Skills、知识库和多 Agent Workflow 的分析工作台。成功意味着用户能从对话或场景启动真实分析任务，清楚看到执行状态和待办，并将最终结论追溯到数据、SQL、节点、审批和 Artifact。

## Key Features

### Core Analysis
- **自然语言数据分析**：输入自然语言问题，自动生成 SQL、执行查询、推荐图表并输出业务洞察
- **多数据源支持**：文件（Excel / CSV）、数据库（SQLite / MySQL / PostgreSQL / SQL Server）
- **智能图表系统**：6 大类 43 种图表自动推荐（对比、时间趋势、分布、地理、关系、占比）
- **SSE 流式输出**：分析过程实时可见，分阶段展示进度

### Advanced Capabilities
- **多模型兼容**：DeepSeek / OpenAI / AtlasCloud / 任意 OpenAI SDK Compatible API
- **深度分析**：异常值处理、十分位分组、K-Means 聚类、决策树建模
- **报告生成**：Excel 表格、Word 文档、PPT 演示文稿导出
- **MCP 拓展**：连接本地或远程 MCP 服务器，扩展 Agent 工具能力
- **知识库**：上传业务知识文档，让 Agent 理解业务上下文

### Workflow & Collaboration
- **AI 团队协作**：复杂任务拆分给多个专长 Agent 协同完成
- **业务画布**：梳理业务关系，复用分析方法
- **Skills 系统**：可复用的分析技能模块
- **工作流引擎**：WF0 规范的确定性工作流执行，支持审批、暂停、恢复
- **Hook 系统**：事件驱动的自动化钩子，支持条件触发和一次性执行

### Workspace & Session
- **工作区管理**：多工作区隔离，每个工作区独立的数据源、会话和文件
- **会话系统**：持久化会话，支持历史回溯和上下文延续
- **跨会话记忆**：持久化用户偏好和分析上下文

## Architecture

```
app.py                          — 应用入口，Flask factory
├── agent/                      — Agent 核心引擎
│   ├── agent.py                — 主 Agent 循环（SSE 流式、工具调用、上下文压缩）
│   ├── prompts.py              — 系统提示词构建
│   ├── tools/                  — 工具系统（数据查询、分析、导出、工作区）
│   ├── workflows/              — WF0 工作流引擎（DAG 执行、审批、状态机）
│   ├── teams/                  — 多 Agent 团队协作
│   ├── hooks/                  — 事件钩子系统
│   ├── memory.py               — 跨会话记忆
│   ├── compaction.py           — 上下文窗口管理与压缩
│   └── skills.py               — Skills 加载与执行
├── api/                        — Flask Blueprint 路由层
│   ├── chat.py                 — 聊天 SSE 接口
│   ├── datasource.py           — 数据源管理
│   ├── workspace.py            — 工作区 API
│   ├── jobs.py                 — 后台任务管理
│   ├── workflows.py / workflow_runs.py — 工作流 API
│   ├── teams.py                — 团队协作 API
│   ├── hooks.py                — Hook 管理
│   ├── dashboard.py            — 仪表盘
│   ├── knowledge.py            — 知识库
│   ├── skills.py               — Skills API
│   └── ...
├── data/                       — 数据存储层
│   ├── session.py              — 会话持久化
│   ├── workspace.py            — 工作区存储
│   ├── memory_store.py         — 记忆存储
│   └── ...
├── Function/                   — 分析功能模块
│   ├── Analyze/                — 统计分析（聚类、决策树、异常值）
│   ├── Charts_generation/      — 图表生成引擎
│   ├── Clean/                  — 数据清洗
│   ├── Knowledge/              — 知识库 RAG
│   └── Output/                 — 报告导出
├── LLM/                        — LLM 配置与图表选择器
├── frontend/                   — 前端 ES 模块（Vite 构建）
│   ├── features/               — 功能模块（chat-stream, models, teams, UI）
│   └── legacy/                 — 遗留模块
├── static/                     — 静态资源
│   ├── css/parts/tokens.css    — 设计令牌（浅色 / 深色主题）
│   ├── css/parts/chat.css      — 聊天界面
│   ├── css/parts/modals.css    — 模态框
│   └── dist/                   — Vite 构建产物
└── templates/
    └── agent_chat.html         — 主聊天页面
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, Flask, Waitress/Gunicorn |
| Frontend | Vanilla JS (ES modules), Vite, CSS Custom Properties |
| Data | Pandas, NumPy, DuckDB, SQLAlchemy, SciPy |
| Visualization | Plotly, Matplotlib, PyEcharts |
| LLM | OpenAI SDK (DeepSeek / OpenAI / AtlasCloud / compatible) |
| RAG | Jieba 分词, ONNX Runtime (BGE-small-zh 嵌入) |
| Export | python-docx, python-pptx, openpyxl |
| Database Drivers | PyMySQL, psycopg2, pyodbc |
| Package Manager | pip (Python), pnpm (Node) |
| Deployment | Railway, Vercel, Docker, Windows installer (PyInstaller) |

## Surface Inventory

| Surface | Route | Description |
|---------|-------|-------------|
| 聊天主界面 | `/` | 对话式分析主工作区：消息列表 + 输入框 + 侧边栏 |
| 侧边栏 | inline | 会话、模型、数据源、MCP、工作区状态与入口 |
| 数据预览模态框 | modal | 多 Sheet 数据预览，含侧边栏和主内容区 |
| 设置面板 | overlay | LLM 配置、API Key、Base URL、Model 选择 |
| 仪表盘 | `/dashboard` | 可视化看板（独立页面，遗留实现） |
| 业务画布 | overlay | 业务关系梳理与 Skills 管理 |
| 工作流详情 | overlay | 工作流 DAG 可视化、节点状态、审批操作 |
| 任务历史 | drawer | 后台任务运行记录与状态 |
| MCP 配置 | overlay | MCP 服务器连接管理 |
| 知识库管理 | overlay | 业务知识文档上传与管理 |

## Design Tokens

### Light theme
- **Primary**: `#002FA7` (Klein Blue) — primary actions, selection, links
- **Background**: `#f4f6fa` — app shell
- **Surface**: `#ffffff` — cards, modals, bubbles
- **Surface layers**: `#f8fafc` (surface-2), `#f1f5f9` (surface-3)
- **Text**: `#1e293b` (body), `#475569` (soft), `#64748b` (mute), `#94a3b8` (dim)
- **Sidebar**: `#1c1f33` background, `#e2e8f0` foreground — dark sidebar on light app
- **Code blocks**: `#0f172a` background
- **User bubble**: `#2e6df0` — deeper blue for AA contrast on white text
- **Semantic**: success (`#22c55e`), warning (`#f59e0b`), danger (`#ef4444`), accent (`#7c3aed`)

### Dark theme
- **Primary**: `#4a7ad8` — desaturated for dark background readability
- **Background**: `#0b0f1c` — deep navy-black
- **Surface**: `#161a2c` → `#1e2238` → `#2a2f47`
- **Text**: `#e6e8f0` (body), stepping down through `#c2c8d6`, `#97a0b3`, `#6b7388`
- **Sidebar**: `#0d1124` — slightly darker than main bg

### Typography
- System font stack (`system-ui, -apple-system, Segoe UI, Roboto, ...`)
- Single sans family across all surfaces — no display/body pairing
- Monospace for code, SQL, and data
- Fixed rem scale (no fluid typography outside hero areas)

### Spacing & Layout
- Chat layout: dark sidebar (left) + light content area (right)
- 24px content padding, 14px gap between messages
- Modals: resizable, min 520px width, max 94vw
- Responsive: structural breakpoints, not fluid type

### Component Vocabulary
- **Buttons**: solid primary, ghost secondary, icon buttons for compact actions
- **Status dots**: colored indicators (green=connected, gray=disconnected, animated=loading)
- **Chevrons**: `›` for expandable rows, `⌄` for dropdowns
- **Sidebar**: collapsible dark panel with status rows, drawers for detail views
- **Modals**: overlay with backdrop blur, resizable, header + body structure
- **Messages**: user = compact blue bubble (right), assistant = left-rule report style (left)
- **Scrollbar**: slim custom (4px, primary gradient)

### Motion
- Message entrance: 350ms slide-up with scale (cubic-bezier .2, .8, .2, 1)
- Avatar hover: 200ms scale transform
- Modals: fade + scale entrance
- No orchestrated page-load sequences; motion conveys state, not decoration

## Brand Personality

专业、克制、可信。表达直接、信息密度适中，强调事实、状态和可操作性，让界面在长时间分析工作中保持安静而可靠。

## Anti-references

不做营销落地页式构图，不用装饰性卡片、夸张大标题或无意义动效。普通用户不应先理解复杂 DAG 才能运行任务；自动化也不能隐藏失败、审批、数据来源或人工修改记录。

## Design Principles

1. 任务优先：首屏直接进入分析、运行和审批，不用功能宣传占据工作空间。
2. 状态可读：运行中、等待、失败和完成必须有稳定、一致、可操作的表达。
3. 渐进披露：默认展示用户当前需要的信息，图结构、JSON 和血缘细节按需展开。
4. 证据可追溯：结论、Job、Artifact、SQL、数据快照和审批记录之间保持可导航关联。
5. 熟悉胜过新奇：复用现有控件、颜色和交互词汇，让用户把注意力留给分析任务。

## Accessibility & Inclusion

以键盘可达、清晰焦点、可读对比度和语义化状态为基础；支持深浅主题、减少动态效果偏好和窄屏结构重排。按钮、长名称、错误信息和结构化结果在桌面与移动视口均不得重叠或溢出。
