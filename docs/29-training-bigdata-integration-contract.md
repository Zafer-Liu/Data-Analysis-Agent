# M3/BDA 实施接口与写入归属

> 2026-11-14。用户已批准按 docs/28 实施，包括必要本地回归、构建与两类 2GiB 规模验收。未授权购买资源、调用付费模型、提交、推送或部署。
> 本文件是并行实现的最小兼容接口；实现可增加可选字段，不得在相邻域再创建同义框架。

## 工作区与验证

只在当前隔离 worktree 操作，不读写原仓库目录。保留已有未提交功能。使用 PowerShell、E 盘 Python/Node，UTF-8 无 BOM、LF。主线程正在准备 Arrow/Trino 依赖；其它 Agent 不安装环境。单域测试用独立临时 BAA_DATA_DIR，禁用真实外部服务、付费调用和模型下载。不要再创建子 Agent、Workflow、会话或 worktree。

各域只运行自己的针对性测试。完整 suite/build/browser、真实规模文件、白名单和集成由主线程执行。

## 独占文件归属

| 域 | 拥有的文件 |
|---|---|
| foundation | infrastructure/resource_budget.py、data/dataset_manifest.py、data/dataset_catalog.py、data/workspace_db_owner.py、对应独立测试、docs/m3-bda-foundation.md |
| import | data/import_pipeline.py、data/import_schema.py、data/dataset_snapshot.py、data/sources/dataset.py；sources/csv.py、excel.py、workspace_persistent.py；data/large_import_limiter.py；对应测试、docs/m3-bda-import.md |
| runner | remote_runner/ 全部、Function/Analyze/Torch_MLP/analyze.py、Torch_RNN/analyze.py 与其共享内核、对应测试、docs/m3-runner.md |
| remote-client | infrastructure/remote_training_client.py、remote_training_transfer.py、training_router.py、ssh_tunnel_manager.py、gpu_connections.py；data/remote_execution_store.py；api/training.py、api/gpu.py；data/jobs_store.py、agent/jobs.py；对应测试、docs/m3-client.md |
| cluster | data/sources/cluster_base.py、trino.py、hive.py、spark_sql.py；data/cluster_query_store.py、cluster_connections.py；api/cluster.py；requirements/cluster-trino.txt、cluster-hs2.txt；对应测试、docs/bda-cluster.md |
| frontend | frontend/features/compute-data.js、features/ui/compute-data-ui.js；frontend/legacy/settings/gpu.js；frontend/features/ui/job-history-ui.js；frontend/legacy/i18n.js；static/css/parts/compute-data.css；对应 Node 测试、docs/m3-bda-frontend.md |
| main | DataSource base/sql/merged_source/session、DataTools/agent/tools/schema/registry/validate、API datasource/workspace/datasets/注册、模板与前端 entry/island 注册、requirements/bigdata.txt、清单/白名单、集成测试与验收脚本 |

## 共同合同

### BudgetSpec / worker

infrastructure.resource_budget：

- `BudgetSpec.from_dict(value=None) -> BudgetSpec`；字段含 max_run_seconds、max_input_bytes、max_output_bytes、max_rows、max_files、max_log_bytes、duckdb_memory_bytes、rss_bytes、batch_bytes、queue_batches、threads、temp_bytes。可另加训练/集群上限，不接受任意 env/SQL SET。
- `BudgetSpec.to_dict() -> dict`、`check_usage(**measurements)`；`BudgetExceeded` 包含 code='budget_exceeded'、resource。
- `ResourceCoordinator(root=None).reserve(owner_id, execution_id, budget)` 返回可释放 lease；同执行幂等、全局槽位和空间预留，lease 保留至真实退出。
- 提供受监督 worker 的入口/用量快照，区分可用 OS 硬约束与监测式限额。不用 DuckDB memory_limit 代替 RSS 上限。

### DatasetManifest / catalog

data.dataset_manifest：`validate_manifest(manifest, *, root=None, verify_files=False) -> dict`；canonical manifest 为普通可序列化 dict。

字段：version=1、dataset_id、snapshot_id、owner_id、session_id、workspace_id、status、schema（Arrow 字段定义）、source、row_count、files（relative_path/format/size_bytes/sha256/row_count）、lineage、schema_policy、created_at。id/hash 由后端创建。相对路径以该 snapshot root 为准，禁止链接/越界/特殊文件。

`DatasetHandle(manifest, root)` 提供 manifest、root、dataset_id、snapshot_id，可传递普通 manifest 与 root 重新构造，不反序列化代码。

data.dataset_catalog：

- `DatasetCatalog(root=None, *, owner_id, session_id='', workspace_id='')`；默认 root 使用 content_data_path，不保存个人绝对安装路径。
- `.root`；`.staging_dir(operation_id)`、`.snapshot_dir(snapshot_id)`。
- `.begin_import(plan, *, idempotency_key='') -> operation dict`。
- `.publish(manifest, *, idempotency_key, expected_version=None) -> manifest dict`：核验完整不可变目录后原子发布，重复同摘要幂等，冲突拒绝。
- `.get(dataset_id, *, snapshot_id='', verify=False) -> DatasetHandle`、`.list() -> list[dict]`、`.versions(dataset_id) -> list[dict]`。
- `.recover()` 只处理自己的 staging/orphan/catalog 缓存；活动引用不能误删。数据 catalog 与版本指针在一个 owner DuckDB 中提交；全局状态靠幂等对账。

### 导入与数据源

data.import_pipeline：

- `create_import_plan(input_path, *, owner_id, session_id, workspace_id='', options=None, budget=None) -> dict`；只能接后端已授权解析的路径。
- `run_import(plan, *, catalog, progress=None, cancel=None) -> manifest`；progress(percent,message)、cancel() 为真或抛取消异常时停止。接入 manifest/catalog 接口，所有字节上限明确。
- plan 包含 format、input_path、指纹、source、schema_policy、options、budget、plan_hash。大文件不整表 pandas fallback。

data.sources.dataset：`DatasetDataSource(handle, *, budget=None, name='')` 保持既有 DataSource 方法；`execute_query_batches(sql, *, budget=None, cancel=None)` 返回 Arrow RecordBatch 迭代器；legacy execute_query 返回有界 DataFrame，超限明确失败，不悄悄采样。

### 远端 RPC

固定入口 `baa_remote_runner --rpc-v1`；JSON 一行一帧，version=1、request_id、operation、payload。单帧默认<=1MiB，禁止输出无界日志。响应含 ok/result 或稳定 code/error，保留 request_id。

operations：handshake、submit、status、events、cancel、artifact。submit payload phase=reserve|commit。reserve 输入规范化 plan/budget/input manifest/idempotency_key，返回 task_id、runner_instance_id、upload manifest（受任务限制的相对位置）；commit 验证输入后入队。status 按 task_id/idempotency_key 查询；events 用 after_seq/limit；artifact 返回已发布的受限文件清单。

runner 作为持久监督服务的入口，SSH 断开不结束训练；CLI 支持服务管理/本地测试入口，但模型文本不能改变命令或启动模块。

允许的训练 family：Torch_MLP、Torch_RNN；参数仅内置 schema。输入为 canonical Parquet/JSON，缓存 NPY 必须 allow_pickle=False；权重 safetensors，禁止 pickle/joblib/任意脚本。取消先持久化，实际退出确认后释放 GPU 槽。

### 远程客户端与 API

`RemoteTrainingService(root=None, *, dataset_catalog=None, connection_resolver=None, transport_factory=None)`：

- `.plan(payload, *, owner_id, session_id, workspace_id='') -> plan dict`。
- `.approve(plan_id, *, owner_id, session_id, workspace_id='', expected_plan_hash) -> grant dict`。
- `.submit(plan_id, grant_id, *, idempotency_key, owner_id, session_id, workspace_id='', job_runner=None) -> run dict`。
- `.status(run_id, *, owner_id, session_id, workspace_id='')`、`.cancel(...)`、`.reconcile_pending(...)`。

transport 最小接口：`.rpc(operation,payload)->dict`；`.upload(local_path, remote_path, *, sha256, cancel=None, progress=None)`；`.download(remote_path,local_path,*,sha256,size_bytes,cancel=None,progress=None)`；`.close()`。客户端和 runner 按相同协议完成，不把 mocked transport 通过当真实 SSH/CUDA 验收。

api.training 独立 Blueprint `bp`，前缀 /api/session/<sid>/training，plan/approve/run/status/cancel。主线程负责在 api/__init__.py 注册。owner 来自现有会话权限，模型参数不能批准 grant。hosted 没有租户级隔离时拒绝。

Job 持久字段增加 execution_backend（缺省 local）、external_id、recovery_phase/recovery_metadata。远程任务启动恢复不能被统一标失败；本地 Job 兼容旧行为。remote-client 域负责恢复与事件幂等，main 不并发改 jobs_store/jobs.py。

### 集群

`ClusterDataSource` 基类与 `TrinoDataSource(config, *, credential_resolver=None, budget=None)`、`HiveDataSource`、`SparkSQLDataSource`，保持 DataSource 方法，并提供：

- `.submit_query(sql, *, idempotency_key='', budget=None) -> query dict`、`.query_status(query_id)`、`.fetch_batches(query_id, *, budget=None, cancel=None)`、`.cancel_query(query_id)`、`.close()`。
- 目标方言严格只读验证实现于独占 cluster_base，不并发改 agent/validate.py；main 将按 source capability 调用这个验证入口。
- 连接配置按 owner 存储，只保存凭据引用/白名单端点，不回传密钥。api.cluster `bp` 处理连接 CRUD、test、submit/status/cancel；实际路由在 docs/bda-cluster.md 列出，main 注册与 UI 适配。
- native query/operation ID、unknown/cancel_pending、结果 complete 与可强制预算明确。fetchmany 不冒称网络帧有界；不支持硬预算时拒绝或明确受控降级，不静默放开。

### 前端 facade

新增 compute-data 模块与岛，沿用公共 API client/request owner，不新增全局状态框架。`mountComputeDataUi()`、`openComputeDataPanel(tab='datasets')` 及导出的动作由 main 在 UI registry/entry/模板接入。

面板覆盖数据集计划/导入/版本、训练计划/用户批准/提交/状态/取消、集群连接/查询/预算/取消。使用上述 API；不要求用户理解协议内部字段，不用 alert/confirm/prompt。外传确认显示目标、范围、输入规模、时长与保留期。认证方式/secret 输入只用于存储引用，不回显到列表。

## 集成交付

每域完成报告列出实际修改文件、API 示例、已执行测试命令/结果、需要主线程集成点和真实环境限制；将针对性测试加入建议清单，但由 main 修改 required-regressions.json/.gitignore。不要用跳过断言、返回假 ready 或恒定成功来取得测试通过。实际代码可用后再写域文档，不先编造完成记录。
