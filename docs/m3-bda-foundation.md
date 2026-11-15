# M3/BDA 数据基础实现

2026-11-14。实现接口依据 `29-training-bigdata-integration-contract.md`；仅描述 foundation/import 自有代码和本地小样本验证。真实 2GiB、完整回归、API/UI 集成由主线程验收。

## 数据合同与发布

`DatasetManifest` 是普通 JSON dict。`validate_manifest` 返回独立副本，校验必需身份、作用域、Arrow schema、行数、文件映射和元数据摘要。`DatasetHandle` 内部保存 canonical JSON，属性不可重赋值，`.manifest` 每次返回独立 dict；`.to_dict()` / `.from_dict(value)` 只传递 manifest/root，不反序列化程序。

- `manifest_hash` 为 canonical SHA-256，排除 catalog_version/published_at 等派生发布元数据；文件逐个记录 SHA-256、真实字节、行数。
- 文件路径必须相对于 snapshot root；拒绝越界、链接/reparse point、硬链接、特殊文件及未列出的文件。核验 Parquet 物理 schema/行数，并用每个 row group 的真实统计验证分区标签。
- 多表采用 `tables: [{name, schema, row_count, max_row_bytes, ...}]`，每个 `files[]` 必须有一致的 `table`。顶层 schema 等于第一张表，顶层 row_count 等于所有表之和。
- Windows 活跃读取持有拒绝 write/delete sharing 的文件句柄；POSIX 使用共享 advisory lock 和文件身份/mtime 检查。非合作的同用户进程不属于 OS 隔离保证。

Catalog root 按 owner/session/workspace 的哈希分区；传入 root 作为内容根，`.root` 才是该 scope 的实际目录。缺省根来自 `content_data_path('datasets')`，源码不写入个人安装路径。

同一个 DuckDB 事务提交 import operation、dataset version 和 active pointer。完整 staging 文件先核验、同步、原子 rename，再提交目录指针；不是“文件系统和 DuckDB 的跨介质原子事务”。rename 后、COMMIT 前崩溃产生已知 orphan，原 active pointer 不变。JSON `catalog.json` 只是可重建缓存，缓存写失败不撤销已提交版本。

`claim_operation` 同时持有 OS 锁和持久 fencing token；监督器记录 worker 的 PID/birth identity。活跃 worker 未确认退出时不能重试、清理或偷取操作。恢复只清理本 scope 已知未提交 operation 的 staging/orphan/control work；不删除已发布快照，也不扫描删除未知目录。已发布版本目前保留，不实现后台 GC；旧 handle 始终指向原 snapshot。

## 实际接口

以下省略实例方法的 self，签名中的默认值与源码一致。

```python
BudgetSpec.from_dict(value=None)
BudgetSpec.to_dict()
BudgetSpec.check_usage(**measurements)
ResourceCoordinator(root=None, *, max_slots=None, max_owner_slots=None,
                    max_rss_bytes=None, max_disk_bytes=None,
                    disk_safety_bytes=None, account_paths=None)
coordinator.reserve(owner_id, execution_id, budget)
coordinator.register_account_path(path)
coordinator.recover()
coordinator.snapshot()
coordinator.run_worker(argv, *, owner_id, execution_id, budget=None, **kwargs)
lease.bind_process(process)
lease.observe_processes(identities)
lease.release()
lease.snapshot()
run_supervised_worker(argv, *, budget=None, coordinator=None, owner_id,
                      execution_id, cwd=None, temp_dir=None, output_dir=None,
                      env=None, cancel=None, progress=None,
                      poll_interval=0.05, on_spawn=None)

validate_manifest(manifest, *, root=None, verify_files=False,
                  budget=None, cancel=None)
DatasetHandle(manifest, root)
DatasetHandle.from_dict(value)
handle.to_dict()
DatasetCatalog(root=None, *, owner_id, session_id='', workspace_id='')
catalog.staging_dir(operation_id)
catalog.snapshot_dir(snapshot_id)
catalog.begin_import(plan, *, idempotency_key='')
catalog.claim_operation(operation_id)
catalog.observe_worker(operation_id, fence, identity)
catalog.operation_status(operation_id)
catalog.publish(manifest, *, idempotency_key, expected_version=None,
                budget=None, cancel=None)
catalog.get(dataset_id, *, snapshot_id='', verify=False)
catalog.list()
catalog.versions(dataset_id)
catalog.recover()

WorkspaceDBOwner(path, *, max_pending=32, enqueue_timeout=5.0, budget=None)
owner.submit(callback, *, transaction=False)
owner.call(callback, *, transaction=False)
owner.transaction(callback)
owner.execute(sql, parameters=None, *, max_rows=10000, max_bytes=67108864)
owner.interrupt(future=None)
owner.snapshot()
owner.close()
```

`owner.execute` 返回 detached `QueryResult(columns, types, rows)`，支持 fetchall/fetchone/description/df；callback 不能返回 live connection/relation/reader。SQL/callback 是受信后端接口，不是用户代码 RPC。一个进程内多个 facade 共享队列；其它进程得到 `workspace_owner_busy`，不得绕过打开活跃 DuckDB 文件。重计算 worker 只读取不可变文件，使用独立执行库。

## 预算与退出

BudgetSpec 字段：max_run_seconds、max_input_bytes、max_output_bytes、max_rows、max_files、max_log_bytes、duckdb_memory_bytes、rss_bytes、batch_bytes、queue_batches、threads、temp_bytes、max_scan_bytes、max_cpu_seconds。未知字段、布尔数值、非有限值、不一致的内存/队列预算均拒绝。`BudgetExceeded` 有 code=`budget_exceeded`、resource、observed、limit。

协调器使用同一个 SQLite admission ledger，策略由首次创建持久化，后续不能静默扩大。计算 owner 当前 RSS、并发 reservation RSS、现有内容/旧快照/staging/log、新输入副本/输出/spill/log 预留和安全余量。可注册后端实际内容路径，重叠根不重复计数；跨卷采用已注册卷的最小可用空间，保守扣除预留。

同执行同预算 reserve 幂等，参数变化冲突。启动另有 OS launch lock，禁止同 execution 并发启动。lease 无 TTL；活跃 controller/worker 的 PID 和 creation time 未证明退出就保留。监督器监测树 RSS、CPU、墙钟、spill、输出、文件和日志；失败只终止自己的进程树，确认子孙退出后才释放槽。legacy large_import_limiter 已改为此协调器的兼容 facade。

RSS/CPU/磁盘/日志/期限是采样监测，可超调。DuckDB allocator/temp 配置不是进程硬 RSS 限额。Windows Job Object 成功时提供 kill-on-close 的进程树生命周期约束，不宣称 Job memory hard cap；Linux 未接入可委派 cgroup。本地直接查询使用 interrupt，不能以杀死应用进程实现 RSS 硬隔离。

## 验证与主线程集成

在 PowerShell 中，每轮设置新的临时 `BAA_DATA_DIR`、`PYTHONIOENCODING=utf-8`、`PYTHONDONTWRITEBYTECODE=1`，用 `E:/Tool/Python313/python.exe` 运行：

```powershell
& 'E:/Tool/Python313/python.exe' -m unittest discover -s tests -p test_dataset_foundation.py -v
```

最终 17/17 通过，7.070 秒。包括真实子进程失败/超时/RSS/CPU/log/disk/cancel、子孙退出、controller 崩溃但 worker 仍活跃、跨进程数据库排他、事务回滚、幂等、版本冲突、缓存失败、scope 和危险文件拒绝。Arrow 24.0.0、DuckDB 1.5.6、pandas 3.0.5、psutil 7.2.2；仅本机 Windows 验证。

主线程仍需：

- 让 workspace_storage/其它原有查询与写入入口经相同 owner 路由；不能让重计算进程直接打开 live `.duckdb`。`WorkspacePersistentSource._conn` 现在是有界 detached adapter。
- 将 startup recovery 接入 catalog/coordinator；不要用通用 Job 恢复逻辑删除活跃 operation。
- API 捕获并映射 BudgetExceeded/ResourceError/WorkspaceOwnerError 等 RuntimeError 的稳定 code；当前部分路由只捕获 ValueError 等。
- 修复主线程 API 测试 `test_ownership_rejection_precedes_uploaded_file_access`：一次误用较宽测试 glob 时该用例期望 403、实际 200。未编辑 API 或其测试，其余三个被带入的 API 用例通过。
- 主线程维护 required-regressions、依赖/打包与完整质量清单。本域未修改这些文件，未提交/推送/部署、未安装依赖、未启动服务器或远程服务。
