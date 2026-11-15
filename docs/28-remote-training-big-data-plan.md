# 远程 GPU 训练与大数据专项完整实施规划

> 日期：2026-11-14。
> 状态：工程实现已开展；本地验收与真实环境待验项目以 [实施验收记录](m3-bda-validation.md) 为准。原规划估时和环境前置保留作设计依据。
> 用户目标：完成远程 GPU 训练完整通道和大数据专项能力，先完成完整规划。
> 依据：当前隔离工作树的源码、现有 G/M/BDA 路线图与已交付执行/证据机制；不以旧文档勾选状态替代源码。

## 1. 完成目标与范围

完成后必须具备三条可验收用户路径：

1. 用户选择已有 SSH 服务器和允许外传的数据快照，执行 MLP 或 GRU/LSTM；应用可查看进度、取消、断线重连、重启对账，并下载校验后的结果和模型产物。
2. 用户导入至少 2GiB 实体 CSV 或 Parquet，在明确的内存/临时盘预算内查询、画像、聚合、画图、导出；取消或崩溃不会替换原有可用数据集或留下可见半成品。
3. 用户连接 Trino、HiveServer2 或 Spark Thrift Server，使用授权表进行服务端查询，只将有界结果落到本地快照；能追踪远端查询 ID、状态、取消及可获得的资源用量。

本期覆盖 M3、BDA1、BDA2。BDA3 完成交付级技术决策：本期采用 DuckDB/Arrow 与集群 SQL，不引入 cuDF/RAPIDS。未来若有实测 GPU 分析需求，另行评估。

不纳入本期：自动购买云 GPU、支付与供应商账单、DDP/多机训练、Ray/Dask 编排、AutoML、Kaggle、任意用户脚本/Notebook 执行、Spark Connect、任意模型插件。训练权重的保存及回读是本期内容；训练进程死亡后的通用 epoch 续训不作为首版承诺。

## 2. 规划假设与部署边界

- 本地控制端支持项目已有 Windows/macOS/Linux 运行方式；远端首个支持档为用户已拥有的 Linux + NVIDIA CUDA + SSH/SFTP。
- 首批训练仅支持内置、版本化的 Torch_MLP 分类/回归与 Torch_RNN 的 GRU/LSTM。
- 首个远端执行档为单机、每 GPU 一个受控任务槽；一个训练任务一个计算进程。多账户的 GPU 隔离需要操作系统/集群管理员配置，不能由本项目的显存估计替代。
- 首个集群 SQL 档分别覆盖 Trino、HiveServer2、Spark Thrift Server；不能把普通 SQLAlchemy URI 可填写视为三类专用适配已经完成。
- 无目标服务器或集群时，可完成协议、模拟服务与故障回归，但必须保留“真实环境待验收”状态。
- 用户数据继续使用 content_data_path/Workspace 用户内容根。凭据引用与平台配置遵守 data_path 边界；托管用户内容不进入账号持久卷。
- 本地工具和依赖安装遵守 E 盘工具根约定；产品源码不写入个人盘符。远端安装前缀由管理员配置。

## 3. 已核实基础与缺口

| 领域 | 已有基础 | 本次必须补齐 |
|---|---|---|
| 远程连接 | SSH 指纹信任、OS 凭据引用、隧道、连接管理、训练器预检 | 独立于推理服务的训练连接、持久训练监督、提交/状态/事件/取消/产物协议 |
| 训练 | 本机设备选择、router 预留、MLP/RNN、JobRunner | 路由前置、远程模型内核、输入输出传输、有界训练数据加载、远程重连 |
| 任务 | Job 七态、持久事件和补发、ExecutionContext 父子取消/退出确认 | 外部执行身份、应用启动对账、远端实际退出证明、跨重启的期限与租约 |
| 文件导入 | CSV 原生 DuckDB 路径；Workspace 大文件 Job、事务、写租约；默认大导入槽位 | 正式 Parquet 通道、批字节背压、schema 合同、全局预算、发布原子性与恢复 |
| Excel | 大文件后台解析、sheet 级进度 | XLSX 行流式、解压及单元格上限；不能继续并发持有全部 sheet DataFrame |
| 查询 | SQL 元数据预览、服务端 LIMIT、现有 SQL 白名单 | 全链路有界结果、目标方言校验、原生远端取消、pushdown 与结果落库 |
| 影子与合并 | 原数据保护、内存影子、跨源查询 | 避免全库 pandas 复制和全表合并；保留 DECIMAL/时区/原数据隔离 |
| 证据 | evidence v2、不可变分析结果、定义修订与绑定 | 数据集文件快照及训练/集群来源元数据；大结果不再编码为巨型 JSON |

关键源码依据：

- remote_runner/baa_remote_runner.py:16：仅预检 PyTorch/CUDA；main 只接受 --preflight --json。
- infrastructure/ssh_tunnel_manager.py:215、api/gpu.py:191：已有固定预检命令和就绪记录。
- Function/Analyze/Torch_MLP/analyze.py:66、Torch_RNN/analyze.py:294：现有模型仍在本机执行。
- data/session.py:395：DataSourceSnapshot 固定连接图和租约，不冻结数据值。
- data/jobs_store.py:173：启动恢复会终结未完成 Job，不能直接用于仍在远端运行的训练。
- data/sources/csv.py:28、api/datasource.py:437：上传路径可能同步加载，失败后全量 pandas 回退；需避免大文件静默跳坏行。
- data/sources/workspace_persistent.py:44、data/large_import_limiter.py:15：已有后台导入与槽位，继续复用。
- data/sources/excel.py:243：现有整 sheet 解析不能作为行流式能力。
- data/sources/_utils.py:328、data/merged_source.py:67：全库影子及逐表合并存在全量物化边界。
- data/sources/sql.py:196、:410、:552：预览已有界，但正式查询与缓存仍需统一预算和批读取。

行号以本次规划读取时为准。上述代码静态核对不构成本次规模、训练或集群运行验收。

## 4. 统一设计：数据快照、执行身份和预算

### 4.1 数据集与快照合同

新增 DatasetManifest v1，采用现有内容存储和 Artifact 管理，不建立第二套文件服务。

| 字段组 | 必需内容 |
|---|---|
| 身份 | dataset_id、snapshot_id、owner_id、workspace_id、session_id、schema_version |
| 来源 | source_id、来源类型、输入文件指纹或源查询标识、提取时间、原查询/过滤版本 |
| 语义 | 字段名/原名映射、Arrow 类型、DECIMAL precision/scale、时间单位/时区、缺失值策略、行序语义 |
| 文件 | 服务端生成的相对文件清单、format、size、sha256、行数、row-group/分区元数据 |
| 血缘 | 上游 snapshot、定义/绑定修订、生成 Job、转换计划、执行参数摘要 |
| 生命周期 | staging/ready/invalid/retired、提交版本、引用计数、租约、保留规则 |

明确区分两种模式：

- 可复现快照：在预算允许时复制/物化成受控不可变文件，哈希确认后才 ready；训练只接受这种模式。
- 外部文件引用：保存确定文件清单及指纹，读取时核验。文件变化使引用失效，不能宣称已冻结内容。

远程数据库的提取结果可以复核；源系统没有时间旅行/事务快照时，不能承诺重新执行原 SQL 会得到同一数据。数据源租约不能替代输入快照。

### 4.2 执行与幂等身份

区分 request_id、业务 execution_id、实际 attempt_id、remote_task_id、cluster_query_id、dataset snapshot_id。网络重传沿用幂等键；真正重试产生新 attempt，并保留 retry_of。

幂等键绑定规范化任务、输入快照、参数、目标主机指纹/runner 实例和资源策略哈希。同键同摘要返回原操作；同键不同摘要返回 conflict。提交超时先按幂等键查询，不能直接启动第二次训练或查询。

业务记录复用当前 SQLite 事实源和 Job 关联；远端监督器保留自己负责的任务事实。进程内 ContextVar、Queue 和 Future 只用于运行，不能承担跨重启恢复。

### 4.3 BudgetSpec v1

预算由后端根据用户配置、资源探测和工作区策略收紧；模型不能通过 SQL SET、任意环境变量或参数扩大。

- 通用：max_run_seconds、max_input/output_bytes、max_rows、max_file_count、max_log_bytes、max_concurrency。
- 本地：DuckDB 内存预算、进程 RSS、Arrow batch 字节、缓冲队列、线程、临时盘、快照与结果盘预算。
- 训练：目标 GPU、max_epochs、batch/window/hidden-size 上限、显存余量、CPU/RAM、进程/文件数量。
- 集群：查询时间、并发、结果行/字节、可强制扫描量/资源组；未知费用与扫描量保留 unavailable。

首个桌面验收档的建议值：DuckDB 1GiB、重任务 worker RSS 2GiB、Arrow batch 目标 16MiB、批队列 2、线程不超过 4、临时盘 8GiB。它们是待实测校准的默认档，不是已测性能承诺。

资源准入计算：当前源与旧版本 + 新快照/派生副本 + staging + 临时 spill + 输出产物 + 安全余量。不能只比较输入文件体积与空闲内存，也不能只限制每个 Session 的两个 worker 而忽略全局聚合。

DuckDB memory_limit 不代表整个进程 RSS 上限；Arrow 批大小也不代表进程硬限。重任务放到可监督 worker 进程：Linux 有可委派 cgroup 时使用其边界，Windows 使用适用的 Job Object/进程约束；其它平台显式标明监测式限额与可能超调，不冒称硬隔离。预算触发时终结自己的进程树并回收 staged 数据，不能杀死未知数据库/用户进程。

## 5. M3：远程 GPU 训练完整通道

### 5.1 连接与能力协商

复用主机指纹、OS 凭据引用和 SSH 管理基础，分离 training_ready 与 inference_ready。纯训练服务器不必额外启动 OpenAI 模型端口。

预检返回协议范围、runner_instance_id、build/kernel 版本、支持的 task/schema、Python/Torch/CUDA/驱动、GPU UUID、磁盘/RAM 能力、监督方式、能力有效期。历史 ready 标志过期后不能直接提交。

本地无 PyTorch 时仍可准备并派发远程任务；训练路由必须早于本机 _require_torch。控制端保持轻依赖，远端计算环境另行锁定版本。

### 5.2 固定 RPC 与监督器

保留旧预检入口；新增固定 --rpc-v1 入口。SSH 启动命令来自受信安装配置，所有任务内容经 stdin 的长度受限 JSON 帧传递。stdout 仅输出协议，stderr 单独有界日志。

Paramiko exec_command 本身仍经远端 shell，所以不能把模型参数、输入路径或模型生成文本拼成命令。远端入口只连接持久监督器；监督器使用固定内置 worker argv、shell=False 执行。

| 操作 | 请求要点 | 回执与语义 |
|---|---|---|
| submit/reserve | 幂等键、计划哈希、输入 manifest、预算 | 返回 task_id、受限上传槽位；尚未启动训练 |
| submit/commit | task_id、manifest 哈希、授权引用 | 校验全部文件，事务进入队列；重复 commit 不重复执行 |
| status | task_id 或幂等键 | 当前状态、attempt、实例身份、退出确认、产物 manifest 摘要 |
| events | task_id、after_seq、limit | 有界、有序事件；可重放、可检测保留窗口截断 |
| cancel | task_id、attempt、请求身份 | 取消受理与实际退出分别报告 |
| artifact | task_id、artifact_id | 仅返回该任务白名单产物元数据与允许传输的位置 |

远端状态：reserved → uploading → verified → queued → running → succeeded/failed；取消经过 canceling，确认退出后 canceled。阶段进度、网络可达性和日志状态不是额外 Job 终态。

远端监督器具有持久任务日志、启动对账、GPU 槽位和进程所有权记录。单纯 nohup、关闭 SSH channel 或保存 PID 均不足以完成监督契约。支持的部署档先确认 systemd 用户服务或等价托管方式；不能在客户端每次连接时偷偷安装/更新远端环境。

### 5.3 文件传输与数据授权

1. 先固定输入 snapshot 和投影/行筛选，预览数据将发送到哪个主机、哪些列、体积、保留期与任务预算。
2. 用户确认生成授权，绑定计划哈希、快照、主机指纹和有效期；参数、数据或目标变化使旧确认失效。默认 auto 不因为本地缺 GPU 就自行外传数据。
3. 服务器分配任务目录，仅接受该 manifest 的相对文件。拒绝路径穿越、符号链接、硬链接/特殊文件、文件数量或解压规模超限。
4. SFTP 使用 .part 文件；续传核验分块大小/哈希，最后核验全文件 SHA-256 后原子提交。不能仅信任远端已有长度，也不能任意续读 CSV 的字节偏移。
5. 输入以 canonical Parquet + JSON schema 为主。需要张量缓存时生成带 dtype/shape/哈希的 NPY shards，allow_pickle=False；禁止 object dtype、任意 Python 对象或脚本。
6. 输出采用 Parquet/JSON 指标、safetensors 权重和有限格式的可选图片。禁止自动 pickle/joblib/整对象 torch.load、任意动态 import 和执行下载内容。
7. 本地下载也要限制大小、文件数与类型，核验后才注册 Artifact/evidence；下载成功不等于模型加载验证通过。

### 5.4 模型内核与大输入

抽取已有 MLP/RNN 的版本化训练内核，让本地、远程共享模型、预处理和结果合同。旧 DataFrame 小数据入口保留，另加 DatasetHandle/批迭代适配。

- MLP：预处理仅在训练分区 fit，保存特征顺序、类别映射、缺失策略、seed；限制 one-hot 类别展开规模，按批读入训练和评估。
- RNN：按序列/时间做确定排序，惰性构造滑动窗口，窗口不得跨序列/训练验证边界；避免全量窗口与全量 GPU 张量。
- 返回规范包含预测、指标、breakdown、可查询输出、模型元数据和受控权重引用，保持现有分析结果兼容。
- 环境及 kernel 不兼容时明确失败，不靠反序列化猜测加载。模型下载与任意预训练权重扩展不属于该固定任务协议。

路由采用 auto/cpu/local_cuda/remote(connection_id)。显式 remote 失败时报告错误或保持队列；远端可能已受理时先对账，不静默启动本机第二份训练。auto 的远程分支必须已有目标与数据授权。

### 5.5 取消、重启与退出确认

- 远端 cancel 先持久化，再协作停止；超时后 TERM/KILL 仅作用于该 attempt 的已验证进程组/cgroup。
- 使用 runner 实例/boot 身份、PID birth、attempt token 防止 PID 复用误杀；子进程树回收且 GPU 槽位释放后才 exit_acknowledged。
- GPU 显存估计与 PyTorch allocator 限制不能作为硬隔离。一个 runner 的锁防止自身冲突；外部进程占用需启动前复查并持续监测，共享 GPU 需管理员约束。
- 远端截止时间和租约在控制端掉线后继续生效。持久化 UTC expires_at，live attempt 用本机 monotonic 计时；重启根据剩余有效期重建，不能复用另一进程的 monotonic 值。
- 本机记录 remote_task_id、实例、manifest、事件游标、transfer checkpoint、cancel intent。重连先查询并幂等续接；实例/状态库丢失进入待核实，禁止猜测训练已停。
- 修改 JobsStore 启动恢复：local backend 按既有规则收敛；remote-managed 进入 recovery_pending 阶段并由对账器处理，不被统一标失败/取消。此分支必须在首次远程提交前上线。
- 远端计算完成但本地仍在下载/校验时，本地 Job 继续运行；只有本地产物清单提交成功才 succeeded。用户此时取消可停止本地交付，仍保留远端真实终态与回收记录。

### 5.6 UI、工具与结果

GPU 设置页新增训练能力/协议/预算和授权预览，不把“推理连接成功”显示成“可训练”。任务面板展示准备、上传、排队、训练、下载、校验、对账以及退出确认。

继续使用 run_analysis 作为分析执行入口，扩展受校验的 execution_target/approved_plan_id；新增低频 prepare_training 工具生成只读计划。模型只获得受控 connection/plan ID，不获得主机凭据、任意命令或任意远端路径。

确认由 UI/后端创建 server grant，run_analysis 只能消费它；模型传 confirm=true 不能授权外传。任务状态/取消复用既有 Job API，若补 Agent 控制工具，必须按统一 Job 身份新增最小 facade，不混用团队 agent_task 身份。

## 6. BDA1：有界本地导入与分析

### 6.1 统一导入管线

上传文件、Workspace 文件、远端查询结果进入同一 ImportPlan → staging → validated snapshot → published dataset 管线；保留已有小文件快速路径与大文件槽位。

- CSV：优先 Arrow incremental reader 或受监督的 DuckDB 原生 scan。有限采样后固定 schema，后续类型漂移明确失败/进入用户确认的转换计划。禁止大文件全量 pandas fallback；坏行默认失败，若用户允许容错则计数并有界隔离，不能静默 ignore_errors。
- Parquet：Dataset/row-group 扫描，投影与过滤下推；限制 batch 的行/字节、单字段/单行大小、并发和队列。记录物理/逻辑类型、row-group 统计及分区元数据。
- XLSX：read_only 行流式组批；独立限制压缩文件、解压 XML、sheet、单元格、共享字符串、公式/缓存值及总行数。建议首档为 50MiB 文件、512MiB 解压 XML、32 sheets、500 万单元格，需规模实测后定值。
- XLS：保持小文件兼容和独立上限；超限要求转换，不承诺其与 XLSX 相同的流式能力。
- 压缩包/压缩 CSV 如支持，按解压字节与实际输出预算，不能仅按压缩体积准入。

Arrow open_csv 当前官方文档说明增量读取为单线程，类型推断在首块后冻结；显式 column_types 和统一缺失策略是正确性要求，不能宣传“分批必定多线程加速”。

### 6.2 schema 与数据准确性

ImportSchema v1 必须持久化：原始列名/规范名映射、类型/精度、源时区/时间单位、日期格式、空值字典、转换和坏行策略。保留前导零 ID、合法 NA 类别；不能把所有文本 NA 无条件转为空。

DECIMAL 不经 float/pandas round-trip 收窄；超精度时拒绝或走用户确认的无损字符串表示。Excel 日期依据单元格类型和 1900/1904 epoch，普通数值列不按值域猜日期。无时区时间与 UTC 时刻分开；样例与全量出现差异要显示类型冲突，不能静默换型。

### 6.3 分区、快照与剪枝

以日期/低基数业务键分区，禁止默认按用户 ID 等高基数字段分目录。初始文件目标 128–512MiB、row-group 16–64MiB 仅作为调优起点，结合列宽与工作负载实测；小分区合并，记录文件数上限。

新快照写入全新目录，验证 manifest 后才发布版本指针。不向活跃目录原地 overwrite，不以文件名 UUID 无碰撞代替事务原子性。读取固定文件清单，源文件改变时失效；验证 partition pruning 和 row-group pruning，不能只用 SQL 含 WHERE 证明已剪枝。

### 6.4 发布、单写者与恢复

复用已有事务/写租约，并加入工作区级单写者队列、全局大任务资源准入和 fencing token。明确一个 DuckDB 文件的 owner 进程：该数据库的查询/写入经其路由，重计算 worker 消费不可变 Parquet 快照并使用独立临时执行库，不直接打开仍由 owner 写入的数据库。owner 与 worker 的内存/磁盘同时计入准入，连接级设置不能冒称互相独立的实例预算。SQLite 中有租约并不自动消除 DuckDB 的跨进程锁限制。

权威 dataset catalog、import operation 和 active version pointer 置于同一个 DuckDB 数据提交事务中；JSON registry 作为可重建缓存。文件先完整写入不可变 staging 并核验，再提交 catalog 指针。两个不同数据库的写入不能被描述为一个原子事务；Job/全局索引通过 outbox/幂等 reconciliation 跟随 catalog。

崩溃恢复按 operation ID 判断：未提交 staging 清理或从完整 shard checkpoint 重建；已提交 catalog 重建 JSON/事件。CSV 不能任意按 byte offset 续读；无已验证 shard 边界就从上个完整块或整文件重启。

取消覆盖准入队列、批间、查询 interrupt 和发布前检查。原子提交窗口明确不可取消；提交已生效的迟到取消不得删除已发布版本，应返回 result_committed 并保留原始终态。

### 6.5 影子写入、跨源和工具链

- 小数据继续原有内存影子语义。大数据源标记 immutable/large；修改必须生成有预算的派生副本/overlay，并经明确计划授权，不全库复制到内存，不改原始文件。
- 派生复制保留 DuckDB/Arrow 类型；空间不足提前拒绝。新版本只在完整提交后切换分析上下文。
- 跨源合并按实际引用表规划；本地来源引用固定快照，远端来源只导入有界查询结果。不能把所有活跃远程表 SELECT * 拉入合并库。
- 每个消费端都要适配：query_data、profile_data、clean_data、run_analysis、chart、导出、Workspace 重挂载、跨源 JOIN、输入哈希和证据落盘。
- 大结果不能先变完整 DataFrame、巨型 JSON、全量 numpy 或所有图表点。画像/清洗按批统计，图表先聚合或显式采样；报告标明聚合/采样口径。算法不支持 DatasetHandle 时明确限制输入，不悄悄截断训练样本。
- 输入指纹从文件/shard/schema manifest 计算，不调用整表 to_json 再哈希。evidence 关联 snapshot 与有限预览，完整结果为列式文件；分页回读必须受原有 grant/session/workspace 限制。

## 7. BDA2：外部集群 SQL

### 7.1 共享接口与三个适配器

在 DataSource 新增 capability/批结果协议并兼容现有 execute_query 小结果返回：metadata、submit、status、fetch_batches、cancel、close、snapshot_result。能力声明包括 dialect、原生取消、分页、事务/快照、扫描指标与可强制预算。

| 适配器 | 首个支持接口 | 独立验证点 |
|---|---|---|
| Trino | 官方 Python client，标准 statement HTTP protocol | catalog/schema、nextUri/Query ID、业务错误、资源组、TLS/认证、返回地址信任 |
| Hive | HiveServer2/Thrift；P0 固定一套已验证 PyHive 或 impyla 驱动 | OperationHandle、FetchResults、CancelOperation、TLS/SASL、LDAP/Kerberos、队列限制 |
| SparkSQL | Spark Thrift Server；复用已验证 HS2 传输层 | Spark SQL 方言、ANSI 配置、timestamp LTZ/NTZ、DECIMAL、资源池和真实取消 |

Trino先落地，Hive随后，Spark按独立能力矩阵交付。Spark Connect 不伪装为 HS2；共用 Thrift 不表示 Hive/Spark SQL 的会话、类型和取消语义相同。

### 7.2 查询与网络语义

连接由用户/管理员配置并以 connection_id 引用；凭据保存在既有 OS/平台凭据边界，模型不能提供任意 DSN、主机、文件路径或认证 header。

SQL 使用目标方言严格 AST 校验，解析失败拒绝；与本地 DuckDB 校验分开。仅允许已授权 catalog/schema/table 的读取，禁止 SET/DDL/DML、外部文件/URL、扩展加载、多语句和未批准函数。集群账号本身保持只读，是执行层第二道约束。默认无静默 SQL 方言改写。

同集群过滤、投影、聚合、JOIN 下推。元数据和预览不运行昂贵 COUNT(*)；行数未知显示未知。客户端 LIMIT 限制结果行，不等于限制扫描、CPU 或账单。

Trino：按 nextUri 分页，HTTP 200 仍须检查 error；取消 DELETE 当前 nextUri 并核实终止。首次提交超时不能盲目重 POST。后续地址/重定向仅允许已批准集群端点，不能把凭据自动送给任意 nextUri/对象存储域；首版关闭 spooling，若未来开启需独立信任与下载预算。

Hive/Spark：保存操作 handle；fetchmany 的调用数不证明底层网络帧有界，须验证驱动/传输层最大帧和超长字段行为。取消保留 handle 并查询服务端终态，不以关闭 socket 判定查询已停。

### 7.3 结果背压与外部预算

结果按照双重行/实际字节上限形成批次，直接写本地 staging/shard。达到上限时取消远端、标记 incomplete，不把部分结果作为完整统计或正式训练输入。

每个请求设 server query timeout、资源组/队列及可支持的 scan/CPU 限制；同时有客户端超时/字节上限。服务端不能强制某项预算时必须披露；要求硬费用/扫描上限的策略应拒绝运行或等待明确放宽，而不是填一个假数。

记录 native_query_id、server_stats_source、扫描/CPU/排队/耗时、complete、取消确认与输出快照。未报告的指标保持 unavailable；GPU 秒、CPU 秒、LLM Token 和货币费用分别计量。

断网后能按原生身份恢复的查询先对账；驱动不支持恢复则记录 unknown/需要人工核实，不能靠重新执行 SELECT 猜测原任务状态。结果重试默认重建独立 staging，除非协议/快照明确支持无重复的分页恢复。

## 8. 数据模型、接口与文件落点

以下为拟议接口；实现时以兼容现有 facade 为原则，禁止为目录结构创建空框架。

### 8.1 记录与 API

- 数据集记录：DatasetManifest、ImportPlan、ImportOperation、DatasetVersion、DatasetLease。
- 训练记录：TrainingPlan、TransferGrant、RemoteExecution、RemoteAttempt、TransferCheckpoint、ResourceReservation。
- 查询记录：ClusterQuery、NativeOperation、QueryBudget、ResultSnapshot。
- 所有记录关联现有 owner/session/workspace/Job/Artifact/evidence，访问权限随原作用域，浏览器恢复不自动恢复服务器路径、活动 lease 或旧外传授权。

| API 组 | 规划入口 | 语义 |
|---|---|---|
| 数据集 | POST /api/session/<sid>/datasets/plans、POST .../datasets/imports | 计划与实际导入分离，受 schema/预算约束 |
| 数据集 | GET .../datasets/<id>/versions、GET .../datasets/<id>/preview | 有界元数据/预览；禁止返回完整 2GiB 数据 |
| 训练 | POST .../training/plans、POST .../training/plans/<id>/approve | 计划与用户确认分离；确认将当前计划/快照/主机/预算哈希固化为有期 grant |
| 训练 | POST .../training/runs | 只消费未过期的 server grant，幂等提交；模型字段不能自行签发授权 |
| 训练 | GET .../training/runs/<id>、POST .../training/runs/<id>/cancel | 补充远端对账与退出信息；关联既有 Job API |
| 集群 | POST /api/datasource/cluster/connections、POST .../queries | 受控连接配置、只读提交；现有 datasource 入口薄转发 |
| 集群 | GET .../queries/<id>、POST .../queries/<id>/cancel | 原生 query identity、统计与取消确认 |
| 事件/下载 | 复用 /jobs/events 与 Artifact 读取 | 不另建 SSE 事件总线；二进制数据不塞入事件正文 |

API 参数、前端读取、错误和服务端确认都要经过既有公共 client/请求所有权机制；新增 text/SSE 事件定义一次追加或完整替换语义并同步会话归档。

### 8.2 修改与新增文件

| 工作域 | 主要落点 |
|---|---|
| 共同合同 | 新增 data/dataset_manifest.py、data/dataset_catalog.py、infrastructure/resource_budget.py；扩展 data/sources/base.py、agent/tools/results.py |
| 大文件 | 新增 data/import_pipeline.py、data/import_schema.py、data/dataset_snapshot.py；修改 csv.py、excel.py、workspace_persistent.py、large_import_limiter.py |
| 数据库 owner/写队列 | 新增 data/workspace_db_owner.py；扩展 workspace.py、_utils.py、merged_source.py 和注册/恢复 facade |
| 远程协议 | remote_runner/baa_remote_runner.py、拟增 protocol.py、supervisor.py、torch_worker.py、requirements.txt |
| 远程客户端 | training_router.py、training_device.py、ssh_tunnel_manager.py、gpu_connections.py；拟增 remote_training_client.py、remote_training_transfer.py |
| 任务恢复 | data/jobs_store.py、agent/jobs.py、agent/execution.py；新增 remote_execution_store/reconciler 辅助模块 |
| 训练内核 | Torch_MLP/analyze.py、Torch_RNN/analyze.py 与共享版本化 kernel/dataset adapter |
| 集群 | 拟增 sources/cluster_base.py、trino.py、hive.py、spark_sql.py、cluster_query_store.py；修改 sql.py 和 validate.py 的目标方言入口 |
| UI/API | api/gpu.py、datasource.py、workspace.py、jobs.py 与训练/数据集 facade；现有 GPU 设置、数据源、预览、Job Vue island |
| 工具治理 | agent/tools/business/data.py、schemas.py、registry.py、job_migration.py、prompts.py、相关 Skills；低频能力按需发现 |
| 依赖 | 拟增 requirements/bigdata.txt、cluster-trino.txt、cluster-hs2.txt；core 不强制 PyTorch/JVM/Hadoop |
| 回归/文档 | tests 的对应合同/故障测试、required-regressions.json、逐项白名单、API 参考、生成清单、三平台打包说明 |

## 9. 分阶段实施与完成门槛

估时是熟悉项目的工程工作量，包含开发与针对性验证；真实服务器、集群和测试数据的等待时间另计。

| 阶段 | 内容 | 依赖 | 工作量估计 | 阶段完成门槛 |
|---|---|---|---|---|
| P0 | 确认目标/版本矩阵；snapshot、budget、执行身份、恢复合同 | 无 | 4–6 人日 | 合同与兼容迁移方案固定；不存在未知副作用授权 |
| P1 | BDA1 CSV/Parquet/XLSX、schema 与原子 catalog | P0 | 8–12 人日 | 有界导入、坏行与类型策略、取消/崩溃恢复通过 |
| P2 | 远端监督器、RPC、幂等、退出确认与 GPU 槽位 | P0 | 8–12 人日 | 模拟/本地协议测试覆盖断网、重复 commit、取消及实例变化 |
| P3 | SFTP 传输、客户端对账、MLP/RNN共享内核与远端训练 | P1、P2 | 8–12 人日 | 已有真实 CUDA 服务器完成两类模型任务及产物校验 |
| P4 | 全工具链有界消费、影子/跨源、数据源重挂载与证据 | P1 | 5–8 人日 | 2GiB数据不会从其它入口重新全量物化 |
| P5 | Trino → Hive → SparkSQL，含三类认证/分页/取消 | P0、P1 | 10–15 人日 | 三类真实服务分别通过独立验收，不以 mock 宣称完成 |
| P6 | 训练授权/状态、导入预算、集群设置与兼容 UI | 可随 P1–P5 增量 | 4–6 人日 | 双语、取消/未知状态、旧会话和窄窗口可用 |
| P7 | 规模/故障/平台集成、发布门禁与文档 | P3–P6 | 5–8 人日 | 完整验收矩阵通过，结果与限制可复现 |

总量约 52–79 人日。一名开发者约 11–16 周；两名熟悉项目的开发者在接口固定后并行，预计约 6–10 周，仍取决于真实环境与适配差异。Agent 并行可以降低实现等待，不能代替实际硬件、认证和负载验证。

并行边界：P1/P2 可并行；P4 与远程内核分别独占文件；P5 三适配器可共享确定的合同，但共享基类/注册/SQL validator 只能一个写者。对 agent.py、DataToolsMixin、全局 Job 恢复、manifest 和前端入口采用单人集成。

## 10. 验收矩阵

| 编号 | 必须验证的场景 | 通过证据 |
|---|---|---|
| M3-01 | 本机无 Torch，只连接训练服务器 | 控制端不加载权重/不安装 Torch，远端能力握手成功 |
| M3-02 | MLP 分类/回归、GRU/LSTM 真 CUDA 训练 | 真实设备/版本、输入快照、split/seed、预测/指标/模型 manifest 可核对 |
| M3-03 | 提交回执丢失、同请求重发、参数变更同键 | 前两者仅一个远端任务；变更冲突，无重复 GPU 计算 |
| M3-04 | 上传、排队、训练、下载各阶段断网/重启 | 通过 task/实例/哈希对账，正确续传/恢复，不自动重训 |
| M3-05 | 各阶段取消，阻塞 CUDA 与进程退出 | 取消请求、实际退出、GPU 槽位释放分别可观察，无迟到产物发布 |
| M3-06 | 坏哈希、越界路径、链接、超限 tensor/权重 | 明确拒绝，无任意代码执行或其它目录写入 |
| M3-07 | GPU 外部占用、双客户端、runner重启 | 槽位不重复分配；无法证明退出时保持占用/待核实 |
| BDA-01 | CSV 和 Parquet 各至少 2GiB 实体文件 | 记录真实物理字节、哈希、行数/聚合答案、RSS/临时盘/总盘峰值和时长 |
| BDA-02 | schema 跨批漂移、ID前导零、合法NA、日期epoch、DECIMAL、时区 | 与固定期望精确对照，无隐式损失或静默坏行 |
| BDA-03 | XLSX 多sheet、共享字符串、压缩膨胀、XLS超限 | 超限明确失败；取消粒度与实际进程内存受控 |
| BDA-04 | 分区/row-group剪枝、源文件变化 | 扫描文件/字节统计证明剪枝；变更使外部引用失效 |
| BDA-05 | staging中断、磁盘满、catalog已提交但registry失败 | 原版本可用，无半表；重启幂等恢复元数据 |
| BDA-06 | 并发导入、影子修改、跨源JOIN、画像/图表/导出 | 全局预算与单写者有效，原源不改，不经其它入口全表复制 |
| CL-01 | Trino/Hive/Spark 真实连接与权限 | 三类独立记录版本、认证与授权范围，错误不泄露凭据 |
| CL-02 | 大结果、单超长字段、HTTP200业务错误/Thrift失败 | 网络/批次/结果双限额生效，不把截断结果作完整统计 |
| CL-03 | 远端查询取消、网络丢失、服务端超时 | 原生query/operation终态可核实；未知时不误报停止 |
| CL-04 | 方言、DECIMAL、时间戳、资源组/scan限制 | 每个目标单独对照；不支持预算的情况明确披露或拒绝 |
| REG-01 | 小文件、本机MLP/RNN、原SQL/影子、会话/Workspace恢复 | 既有共享回归继续通过，新依赖缺失不破坏基础启动 |
| REG-02 | 本地/桌面/托管边界与安装包 | 真实支持矩阵、用户内容根、凭据引用、平台资源路径正确 |

规模环境首档建议：控制端 16GiB RAM、SSD；测试前根据预留公式计算空间，建议准备至少 40GiB 可用空间。2GiB Parquet 必须是实体文件，不用高压缩后的逻辑体积冒充；同时记录解压/逻辑规模。大文件生成与真实服务器测试须在实施阶段明确安排，本规划轮不执行。

性能报告用中位数、峰值和范围，固定机器/版本/数据/参数。至少三次同条件运行；不预先承诺吞吐或延迟百分比，不把 OS page cache 导致的加速解释为算法收益。

## 11. 错误、兼容与回滚

错误码至少区分：budget_exceeded、schema_drift、dataset_changed、snapshot_unavailable、idempotency_conflict、runner_incompatible、remote_state_unknown、cancel_pending、artifact_corrupt、cluster_permission_denied、unsupported_budget。UI 显示可执行的重试/核对动作与 request/query ID。

远程提交成功但回执未知时，只允许查询原幂等身份；输入/目标/预算变化需要新计划。不能用无限重试掩盖认证、权限、数据或费用问题。

新增 schema 采用增量迁移和版本检查；旧小源/旧 Job 保留既有语义。功能开关按 remote_training、large_dataset、cluster_trino、cluster_hive、cluster_spark 分开，关闭新提交仍须允许状态查询、取消与产物回收。

回滚不能删除已发布快照、引用中的 Artifact 或仍运行的远端任务；保留只读历史和对账能力。老版本无法读取新数据集格式时明确拒绝，不回落到原始路径全量 pandas 解析。

## 12. 真实环境前置与需要确认的事项

规划采用上述默认架构，不需要先等待才能设计。实施到对应阶段时确认：

- 远程服务器：SSH/SFTP可达、固定runner安装许可、Python/Torch/CUDA版本、GPU UUID/共享方式、监督器持久性、cgroup/磁盘配额能力；需要的数据外传与保留范围。
- 数据规模：真实 CSV/Parquet 形态、目标查询、可用 RAM/SSD、是否有超长文本/高基数/特殊时区；Excel 实际上限独立确认。
- 三类集群：具体版本/接口、TLS/证书、Basic/LDAP/OAuth/JWT或Kerberos方式、只读身份、catalog/schema授权、原生取消、资源组与扫描限制。
- 验收费用：使用已有服务器也可能有成本，按运行时长/资源上限授权；不因实施本计划自动购买云资源。
- 托管多租户若需要 SSH 训练，必须提供租户级连接/授权和持久对账设施；不能复用全局GPU连接配置。首版本地/桌面闭环先验收，托管只在隔离条件满足后开放。

## 13. 参考资料与当前交付边界

代码依据使用本文件第3节及项目 docs/03-conventions.md、17-gpu-connection-plan.md、optimization-engine.md、optimization-semantics.md。

官方资料（规划时读取；不表示本地已升级到该文档版本）：

- Arrow CSV incremental reader、schema/null/thread行为：https://arrow.apache.org/docs/python/csv.html
- DuckDB 资源与大于内存工作负载：https://duckdb.org/docs/2.0/guides/performance/how_to_tune_workloads.html
- DuckDB memory_limit 等配置：https://duckdb.org/docs/2.0/configuration/pragmas.html
- DuckDB 分区写入：https://duckdb.org/docs/2.0/data/partitioning/partitioned_writes.html
- Trino query protocol：https://trino.io/docs/current/develop/client-protocol.html

P0 将确定实际支持版本及锁文件；不能把当前文档中的新参数无条件施加给 requirements 允许的旧版本。

本轮只进行只读代码核对和规划文档编写。未修改业务代码、未执行测试/构建/编译、未生成大数据、未安装依赖、未连接远程服务器或集群、未提交/推送/合并/部署。下一步须在本规划确认后开始实施。
