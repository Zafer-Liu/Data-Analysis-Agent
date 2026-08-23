# 本地 SQLite + DuckDB：多进程与大表导入演进计划

> 状态：**L1 进行中：大 CSV 后台批量导入与基础指标已落地**  
> 更新日期：2026-08-08  
> 目标：保持零部署、本地可用；提升大文件批量导入与多进程任务处理能力。

## 1. 架构边界

```text
API / 多个 Worker 进程
        │
        ├─ SQLite：Job 状态、事件 sequence、任务认领与恢复
        │
        └─ Workspace Writer：每个 Workspace 同时仅一个写入者
              └─ DuckDB：CSV / Excel / Parquet 导入、清洗和分析
```

- SQLite 和 DuckDB 都是嵌入式数据库；安装 Python 依赖后即可使用，不要求数据库服务。
- DuckDB 是分析数据的权威存储；不得让多个进程直接同时写同一个 `workspace.duckdb`。
- SQLite 继续保存任务协调事实。它不保存大表分析数据。

## 2. 目标与非目标

目标：大表以批量方式导入、充分使用本机多核；多个 Worker 能并发处理不同 Workspace；同一 Workspace 的写入有序、可恢复且不会损坏 DuckDB 文件。

非目标：引入 PostgreSQL、云服务、网络共享 DuckDB 文件或多个进程直写同一个 `.duckdb` 文件。

## 3. 分阶段实施

### L1：导入基线与批量路径

**当前进展（2026-08-08）**：Workspace 内超过 `BAA_CSV_JOB_THRESHOLD`
（默认 25 MB）的 CSV 已由后台 Job 使用 DuckDB `CREATE TABLE AS SELECT` 批量导入，
并在注册表记录 `source_bytes`、`row_count`、`table_count` 与 `elapsed_ms`。可用
`BAA_DUCKDB_THREADS` 调整导入线程数（默认 4），以及用
`BAA_DUCKDB_MEMORY_LIMIT` 设置 DuckDB 内存上限。

1. 为 CSV、Excel、Parquet 导入记录文件大小、行数、耗时、峰值内存、失败原因和输出表名。
2. CSV/Parquet 使用 DuckDB 的批量读取与 `CREATE TABLE AS SELECT` / `COPY`，禁止逐行 `INSERT`。
3. Excel 解析后以批量 Arrow/Pandas 数据帧或临时 Parquet 写入 DuckDB；不在 Python 循环中逐行落库。
4. 大型 CSV 导入后可选生成 Parquet 缓存，后续分析优先扫描 Parquet/DuckDB 表。
5. 为低内存设备提供线程数、内存上限、临时目录和失败提示配置。

**验收**：形成代表性 CSV、Excel、Parquet 基准样本及导入报告；导入路径无逐行 SQL 插入。

### L2：Workspace 单写者队列

**当前进展（2026-08-08）**：已引入 Workspace `.zhixi/workspace_write_leases.db`
中的 SQLite 原子租约。CSV/Excel 后台写入在打开 DuckDB 前获取租约、持有期间自动续租，
未取得租约时可取消地等待；获取、等待和释放事件写入 Job 事件流。
同步的小文件 CSV/Excel 注册及 `create_analysis_table` 也使用相同租约；同步调用最多等待
`BAA_WORKSPACE_WRITE_SYNC_WAIT_SECONDS`（默认 5 秒），超时会返回可重试错误。

1. 以现有 Workspace runtime lease 为基础，给每个写入 Job 分配 `workspace_id`、持有者、租约过期时间和重试次数。
2. Worker 先在 SQLite 中原子认领 Job，再获取 Workspace 写入 lease；获取失败则排队或回到可重试状态。
3. 同一 Workspace 仅一个 Writer 可以打开 DuckDB 的读写连接；只读查询使用只读连接或排队策略。
4. Writer 定期续租；进程崩溃后，过期租约由恢复任务释放并安全重投 Job。
5. 所有 lease 获取、续租、释放、超时和重试写入 Job 事件流，供 UI 显示和问题排查。

**验收**：多个 Worker 可并发导入不同 Workspace；同一 Workspace 的两项写入任务严格串行；崩溃后不遗留永久锁且数据文件可继续打开。

### L3：性能与可靠性回归

**当前进展（2026-08-08）**：已用 `C:\Users\Juxin\Desktop\外卖情况\大量测试数据.xlsx`
完成临时 Workspace 的真实导入基准。源文件为 29.24 MB、56 个工作表，端到端导入耗时
9.88 秒；最大两张表分别为 97,821 行和 87,099 行。启用全局限流后的复测为 9.77 秒，
生成 55 张非空数据表。原始文件未被修改。已增加失败 CSV
导入不发布注册表条目的回归测试，避免半成品被标记为可用。

**并发限流（2026-08-08）**：大 CSV 与 Excel 后台导入通过 SQLite 全局槽位限流，默认
最多两个 Job 同时解析/导入；可用 `BAA_MAX_CONCURRENT_IMPORTS` 调整。槽位会自动续租，
等待中的 Job 可取消，且等待/获取/释放均写入 Job 事件流。

1. 增加并发导入、取消、重试、进程中断和恢复集成测试。
2. 设置导入并发上限，避免多份大文件同时解压/解析耗尽内存或磁盘。
3. 定期检查 Workspace DuckDB 完整性，保留导入前源文件和操作日志；失败不把半成品标记为可用表。
4. 将性能基线展示在开发文档中，并以目标机器实测值而非固定“秒级”承诺验收。

## 4. 推荐执行顺序

先完成 L1 并得到真实的文件导入基线，再实施 L2 的进程级单写者队列。L2 完成后再以并发和崩溃恢复测试作为 L3 的发布门槛。
