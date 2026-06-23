# Test Suite

Run all tests:

```bash
python -m unittest discover -s Test -p "test_*.py" -v
```

Or one file:

```bash
python -m unittest Test.test_validate -v
```

## Files

| File | What it covers |
|---|---|
| `test_validate.py`       | `agent/validate.py` — SQL write-keyword guard + tool-arg validation. The single line of defense against destructive LLM SQL. |
| `test_retry.py`          | `agent/retry.py` — transient-error classifier + exponential backoff driver (time.sleep mocked). |
| `test_chart_selector.py` | `LLM/chart_selector.py` — registry integrity (41 charts, unique ids, required fields) + scoring sanity for time/distribution/pie keywords. |
| `test_sources.py`        | `data/sources/` — utility helpers (clean_identifier / dedup / header detection) + `CSVDataSource` round-trip (load → query → analysis table). |
| `test_preview_policy.py` | 数据预览选表边界：仅远程 SQL 表可选；文件预览不要求二次选表，聊天上下文拒绝伪造的本地表选择。 |
| `test_api_smoke.py`      | Flask app boot + every public endpoint returns 200 + every `data-action` in HTML maps to an `ACTIONS` entry + no inline `onclick=` regressions. |
| `test_temp_prompt.py`    | Temporary Prompt reasoning-tag sanitization and system-prompt section construction. |
| `test_reasoning.py`      | Streaming `<think>` parser plus assistant reasoning persistence. |
| `test_chart_list_mappings.py` | Line/grouped/stacked chart list mappings, SQL `series` alias recovery, and numeric `y` color regression. |
| `test_knowledge_rag.py` | KnowledgeBase local RAG indexing, document chunk retrieval/deletion, Chinese retrieval, structured vector fallback, weak-match score filtering, and UI-safe citation refs. |
| `test_tool_contract.py` | Tool Schema/ToolSpec registry 一致性、动态暴露、Job eligibility、结果 envelope/error taxonomy、数据依据和保守并发策略。 |
| `test_skills.py` | `SKILL.md` frontmatter 校验、目录扫描、参数替换、非法名称拒绝及 `/api/skills` 安全目录响应。 |
| `test_skill_loader.py` | Skill 三层来源、热加载回退、资源索引、禁止脚本执行及 `allowedTools` 权限收窄。 |
| `test_command_loader.py` | Command Markdown、alias/命名空间、来源覆盖、受保护内置命令、类型化 Dispatcher 及 `/api/commands`。 |
| `test_activation_runtime.py` | Skill/Command/internal action API 互斥解析、工具 guard、父 Job 激活记录与 Session 保存审计。 |
| `test_workspace_tools.py` | Workspace 路径越界/敏感目录拒绝、glob/grep/read/write/edit、先读后写竞态保护、无 Shell 操作、任务板和团队邮箱。 |
| `test_filehistory.py` | FileHistory 持久化快照、连续文件版本回退、新建文件撤销，以及文件+对话/仅对话/仅文件三种模式。 |
| `test_checkpoint_api.py` | `/checkpoint` 快照列表、回退 Job、确认要求与只读/可读编辑权限边界。 |
| `test_packaging_security.py` | 跨平台安装包 P0：资源白名单 staging、MCP/运行数据排除、敏感路径与密钥扫描、ZIP 路径穿越阻断。 |
| `test_installer_security.py` | Windows 安装包 P4：Inno 仅消费 onedir、无 Python/venv/运行目录遗留，构建脚本具备双审计、冻结自检与发布哈希。 |
| `test_macos_packaging.py` | macOS 安装包 P5：`.app`/`.dmg` 构建脚本、原生 Runner 限制、架构命名、审计门禁和未签名测试包标记。 |
| `test_release_workflow.py` | 跨平台安装包 P6：GitHub Actions release workflow 的测试/构建/发布门禁、artifact 边界和未签名发布说明。 |
| `test_runtime_paths.py` | 跨平台安装包 P1：源码路径兼容、Windows/macOS 用户数据目录、`BAA_DATA_DIR` 隔离及禁止自动迁移。 |
| `test_desktop_runtime.py` | 跨平台安装包 P2：健康探针、桌面启动器、冻结入口及 MCP 资源缺失兼容。 |
| `test_desktop_lifecycle.py` | 桌面页面租约：最后页面关闭、心跳超时、多标签页和跨站请求阻断。 |
| `test_frozen_smoke.py` | 跨平台安装包 P3：冻结构建内的动态分析、图表、PPT、Excel/SQL 驱动和科学计算依赖自检。 |
| `test_smoke_all.py`      | (legacy) Generates all 41 chart types end-to-end. Slow — keep for nightly. |
| `diagnose.py`            | (legacy) Manual diagnostic script, not a unittest. Useful for "does this machine have all deps installed". |

## Conventions

- stdlib `unittest` only — **no pytest, no fixtures package**.
- Each test file is runnable directly (`python Test/test_xxx.py`).
- External services (LLM API, Google Sheets, SQL DBs) are **never** hit. Use
  fakes / temp files / DuckDB in-memory only.
- New tests for new behaviour go in the matching file. New file = new module's
  worth of behaviour.
