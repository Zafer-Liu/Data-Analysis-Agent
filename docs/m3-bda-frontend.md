# M3 / BDA 前端交付

2026-11-14。实现位于隔离 worktree，延续既有 compute-data controller；未修改模板、入口、vue-app、构建清单或后端文件。界面复用 Vue `h` island、公共 API client、request owner、overlay 焦点管理与设计令牌。

## 文件

- `frontend/features/compute-data.js`：作用域、表单状态、计划、批准、提交、对账、状态、取消和快照动作。
- `frontend/features/ui/compute-data-ui.js`：数据集 / 训练 / 集群 SQL 三个视图，真实表单与渐进展开的资源选项。
- `frontend/legacy/settings/gpu.js`：分别显示推理与训练能力，支持仅训练 SSH 用途、主机指纹复核、独立训练预检与训练面板链接。
- `frontend/features/ui/job-history-ui.js`：保留远端业务身份、取消请求、确认退出和 recovery pending，链接到训练详情。
- `frontend/legacy/i18n.js`：新增功能与状态的中英文词条。
- `static/css/parts/compute-data.css`：既有主题令牌、单一 modal 容器、窄屏重排、焦点、高对比模式与长文本处理。
- `tests/compute-data.test.mjs`、`tests/compute-data-ui-20261114.test.mjs`：独立 Node 回归。

## 导出与 island

`frontend/features/compute-data.js` 的主入口：

```js
await mountComputeDataUi(options = {});
await openComputeDataPanel(tab = "datasets", options = {});
```

`tab` 为 `datasets`、`training` 或 `cluster`。打开选项支持 `connectionId`、`runId`、`queryId`。`mountComputeDataUi` facade 返回 Promise；`frontend/features/ui/compute-data-ui.js` 的同名函数同步挂载并返回 island。

注册名严格为 `computeData`，即 `BAA.ui.computeData`。island 提供 `open(tab, options)`、`close()`、`reset()`、`isOpen()`、`destroy()` 和 `controller`。挂载幂等，不建立第二个 controller。

controller factory：

```js
createComputeDataController({ client, getContext, onChange, idFactory });
```

可注入的 `getContext()` 返回 `{ sessionId, workspaceId }`。默认使用当前应用 SID，并先通过既有 `GET /api/session/<sid>/workspace` 核实工作区；缺少或迟到的元数据不会允许提交。会话、工作区状态变化及 `knowledge:scope-changed` 会清空当前数据和未消费批准，并重新加载。

其余具名导出为 `defaultComputeContext`、`computeText`、`computeExpiry`、`trainingConnectionReadiness`、`buildComputeBudget`、`buildClusterBudget`、`canSnapshotClusterQuery`、`trainingTransferReview`、`executionStateLabel`、`safeComputeDownload`。

controller 动作包括：

- 数据集：`chooseFile`、`updateDatasetForm`、`planDataset`、`importDataset`、`refreshImport`、`cancelImport`、`newDatasetImport`、`refreshDatasets`、`selectDataset`、`activateDataset`。
- 训练：`updateTrainingForm`、`loadTrainingSnapshots`、`refreshTrainingConnections`、`planTraining`、`setTransferReviewed`、`approveTraining`、`submitTraining`、`refreshTrainingRun`、`refreshTrainingEvents`、`cancelTraining`。
- 集群：`updateClusterForm`、`editClusterConnection`、`requestDeleteConnection`、`refreshClusterConnections`、`saveClusterConnection`、`deleteClusterConnection`、`testClusterConnection`、`updateQueryForm`、`submitClusterQuery`、`refreshClusterQuery`、`refreshClusterEvents`、`cancelClusterQuery`、`snapshotClusterQuery`。
- 通用：`open`、`close`、`scopeChanged`、`syncScope`、`setLookup`、`cancelReads`。

## 实际 API 合同

以下根据本 worktree 的 `api/datasets.py`、`api/training.py`、`api/cluster.py` 与模型参数校验实现适配，未使用预案中不存在的 uploads / training events 路由。

### 数据集

基路径 `/api/session/<sid>/datasets`。

| 方法 / 后缀 | 请求与行为 |
|---|---|
| POST `/plans` | 文件模式直接发送原生 `File` 的 `FormData`，字段为 `file`、`workspace_id`、JSON 字符串 `options` / `budget`。不使用 FileReader、全文件 text/arrayBuffer 或浏览器哈希。 |
| POST `/plans` | 工作区 / 已有源模式发送 JSON `{workspace_file, options, budget, workspace_id}` 或 `{source_id, options, budget, workspace_id}`。 |
| POST `/imports` | `{plan_id, idempotency_key, workspace_id}`；计划与导入分开。回执未知时只复用原计划和原幂等键核对。 |
| GET `/imports/<operation_id>` | 查询导入状态。取消使用回执中的 `job_id` 调用既有 POST `/api/session/<sid>/jobs/<job_id>/cancel`。 |
| GET 空后缀 | 数据集列表。 |
| GET `/<dataset_id>/versions` | 快照版本列表。 |
| GET `/<dataset_id>/preview` | `snapshot_id`、`limit=50`，不下载完整数据。 |
| POST `/<dataset_id>/activate` | `{snapshot_id, workspace_id}`。 |

导入选项使用实际字段 `infer_types` 与 `schema_policy.{null_values,id_columns,source_timezone,bad_rows}`。默认坏行失败；不将 NA 无条件作为空值，不宣称 XLS 大文件支持。

### 训练

基路径 `/api/session/<sid>/training`。当前严格 API 从 SID 派生 owner / workspace，POST 不发送 `workspace_id` 或 `execution_target`。

| 方法 / 后缀 | 请求与行为 |
|---|---|
| POST `/plans` | `{family, params, budget, dataset_id, snapshot_id, connection_id, retention_seconds}`。 |
| POST `/plans/<plan_id>/approve` | 只发送 `{expected_plan_hash}`，必须先展示完整外传复核并由用户勾选，再点击批准。 |
| POST `/runs` | 只发送 `{plan_id, grant_id, idempotency_key}`。 |
| GET `/runs/<run_id>` | 业务运行状态与交付结果。 |
| POST `/runs/<run_id>/cancel` | 空 JSON 对象。请求取消与确认退出分开。 |
| GET `/api/session/<sid>/jobs/events` | 使用运行记录中的 `job_id`，按 `after_sequence` / `limit=100` 回放；不建立第二条训练事件总线。 |

MLP 参数使用 `mode`、`target_column`、`epochs`、`batch_size`、`hidden_size`、`seed`。MLP 内核使用快照内全部非目标列，界面明确说明，不提供无效的特征投影控件。RNN 使用 `cell=gru|lstm`、`window`、`steps`、可选 `time_column` / `sequence_column` 以及共享参数。限制与当前内置参数 schema 一致。

外传复核读取服务器计划的指纹、快照、完整列范围、输入字节、预算时长和 `retention_seconds`。任何必要信息缺失均不可批准。参数变化或作用域重置使旧批准失效；授权过期显示恢复动作。仅有旧 `runner_ready`、推理 connected 或缺少有效期的记录不会变成训练就绪。

当前训练 API 没有按幂等键 GET 的列表路由。回执未知时保留原始已批准的三个提交字段，核对动作将其原样重放到 `/runs`；服务端在检查 grant 有效期之前先查原幂等键，避免启动第二份任务。不会生成新键、新计划或自动批准。产物仅使用服务器返回的同源 `/api/` 下载地址。

### 集群

基路径 `/api/datasource/cluster`。GET / DELETE 的查询串包含 `session_id` / `workspace_id`；POST / PATCH 在 JSON 中发送作用域。

| 方法 / 后缀 | 请求与行为 |
|---|---|
| GET / POST `/connections` | 列表 / 创建。创建使用平铺的 `name,kind,host,port,tls,username,auth,catalog,schema,allowed_tables,resource_group`。 |
| PATCH / DELETE `/connections/<connection_id>` | 修改 / 删除。删除采用面板内复核。 |
| POST `/connections/<connection_id>/test` | 返回真实连接结果与能力；缺失的能力显示不可用。 |
| POST `/queries` | `{session_id,workspace_id,connection_id,sql,idempotency_key,budget}`。 |
| GET `/queries` | 回执未知时按原 `idempotency_key` 查询。 |
| GET `/queries/<query_id>` | 状态、原生身份、完整性与实际统计。 |
| GET `/queries/<query_id>/events` | `after_seq`、`limit=100`；去重并披露截断。 |
| POST `/queries/<query_id>/cancel` | 保持 cancel pending，直到服务端确认。 |
| POST `/queries/<query_id>/snapshot` | `{session_id,workspace_id,idempotency_key}`，使用固定 `snapshot-<query_id>`，不额外放宽预算。 |

认证 secret 仅发送至连接 API 的服务器凭据库存储边界；发送后即清空。浏览器不提交任意 credential_ref，列表和编辑状态不回显密钥。编辑不变的连接可留空以保留已有凭据。

集群预算只发送当前适配器接受的 `max_run_seconds,max_rows,max_output_bytes,batch_bytes,max_scan_bytes,max_cpu_seconds,strict_server`。扫描 / CPU 限制可选；不支持的硬约束由服务端拒绝。结果行上限不冒称扫描或费用上限。

快照动作会在服务器按已提交预算获取、校验和发布完整结果，因此允许对可获取的运行中查询发起此动作。尚未获取的结果不称完整；失败、取消、未知或已经中断的部分结果不发布。只有成功快照回执才显示已保存。

## main 注册与剩余接线

以下保持单处注册；本域没有修改这些文件。

1. 模板结构必须保留一个外层 modal，island 只渲染内部 shell：

   ```html
   <div class="overlay" id="ov-compute-data">
     <div class="modal compute-data-modal"><div id="compute-data-root"></div></div>
   </div>
   ```

2. `frontend/features/vue-app.js` 的 loader 键为 `computeData`，懒加载 `./ui/compute-data-ui.js` 并调用 `mountComputeDataUi()`。`frontend/entries/legacy-panels.js` 导入 `../features/compute-data.js` 为 `BAA.computeData`。当前 worktree 已观察到这两处接线与模板结构。
3. launcher 使用 `BAA.computeData.openComputeDataPanel('datasets')`，训练 / 集群入口分别传 `training` / `cluster`。当前 `frontend/legacy/app.js` 已有 `openComputeData` action。若仍需支持通用 `openOverlay('ov-compute-data')`，在 `frontend/entries/legacy-core.js` 的 `overlayIslands` 增加 `"ov-compute-data": "computeData"`；不要建立另一套 mount。
4. 主样式在 tokens / modals 后导入 `parts/compute-data.css`。当前 `static/css/agent_chat.css` 已观察到此导入；不要手改构建产物或 manifest。
5. 激活成功时 island 发布 `compute:dataset-activated`，载荷含 `source_id`、`dataset_id`、`snapshot_id`。main 可监听后，通过公共 client 与 scope guard 刷新 GET `/api/session/<sid>/sources`，交给已有 `frontend/legacy/datasource.js` 的 `onSourcesUpdated`，使侧栏立即更新。
6. `api/jobs.py` 的任务 DTO 需保留 `execution_backend`、`external_id`、`recovery_phase`、`recovery_metadata.run_id` / `exit_acknowledged`；事件保持相同元数据。前端已有消费与链接逻辑，不能将 native task ID 当作 training run ID。此前读到的 Job DTO 尚未序列化这些扩展字段，此后端改动归 main / remote-client 协调。
7. main 按既有门禁将两个 Node 文件加入回归清单 / 白名单。当前 `.gitignore` 的 `/tests/*` 与 `/docs/*` 会忽略两个测试及本文档，需由 main 增加精确例外。本域不修改 required-regressions、.gitignore、入口或构建清单。

## 已执行验证与限制

通过 PowerShell，工作目录为当前隔离 worktree，运行：

```powershell
& 'E:/Tool/node/node.exe' --test tests/compute-data.test.mjs tests/compute-data-ui-20261114.test.mjs
```

结果：**39 tests，39 pass，0 fail，0 skipped**。覆盖真实 controller / Vue 表单事件请求、原生 File / FormData、严格训练 / 集群请求形状、迟到响应和错误、工作区解析与切换、reset 后写锁、重复 approve / submit / cancel、回执未知的幂等核对、过期批准、权限提示、事件游标、快照完整性、训练专用连接 / 主机指纹复核、Job 链接、中英文与关联标签。响应式 CSS 检查是静态断言，不代替浏览器验收。

执行了本域文件的 `git diff --check`，9 个域文件均通过严格 UTF-8 无 BOM、LF、末尾换行与尾随空白检查。未安装依赖、启动 server、构建、运行完整 suite 或后端测试；未使用真实 SSH / CUDA / 集群或实体 2GiB 文件。真实环境、浏览器与构建验收仍由 main 完成。未 stage / commit / push / deploy。
