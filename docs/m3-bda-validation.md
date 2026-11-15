# M3 / BDA 实施与验收记录

日期：2026-11-14，UI 推送前复核更新于 2026-11-15。实现位于 optimization-goals-20261114 隔离工作树；实际提交与推送以 Git 记录为准，未合回原目录或部署。规划见 [28](28-remote-training-big-data-plan.md)，跨域合同见 [29](29-training-bigdata-integration-contract.md)。

## 已实现范围

| 工作域 | 实际实现 | 接入点 |
| --- | --- | --- |
| 数据合同与预算 | 不可变 Manifest、作用域隔离、DuckDB catalog 原子版本指针、幂等操作、单 owner 写队列、进程身份与退出后释放资源 | data/dataset_*.py、data/workspace_db_owner.py、infrastructure/resource_budget.py |
| 导入与精度 | CSV/CSV.GZ 有界读取、Parquet row-group 批读取、XLSX 行流式与解压限额；固定 schema、前导零、NA、DECIMAL、时区和双 Excel epoch | data/import_pipeline.py、data/import_schema.py、data/dataset_snapshot.py |
| 本地消费 | 有界查询和聚合画像、清洗 SQL、派生快照、按实际引用表合并、Workspace mount 恢复、有限预览及列式文件下载 | data/sources/dataset.py、data/dataset_operations.py、data/merged_source.py、agent/tools/business/data.py、api/datasets.py |
| 远程执行 | 固定 RPC v1、持久监督、幂等 reserve/commit、GPU 槽位、进程 birth/attempt 身份、持久截止时间、独立看门狗、取消与实际退出分离 | remote_runner/ |
| 训练内核 | 共享版本化 MLP 分类/回归和 GRU/LSTM；批量训练、train-only 拟合、惰性窗口、Parquet 预测和 safetensors 受控回读 | Function/Analyze/Torch_MLP/、Function/Analyze/Torch_RNN/ |
| 远程客户端 | SSH 指纹和凭据引用、SFTP 分块与全文件哈希续传、计划和用户批准、受控产物下载及血缘、冷启动对账和事件 outbox | infrastructure/remote_training_*.py、data/remote_execution_store.py、api/training.py |
| 三类集群 | Trino statement HTTP、HiveServer2、Spark Thrift Server；目标方言授权、原生身份、分页及网络帧限额、取消核实和结果快照 | data/sources/{cluster_base,trino,hive,spark_sql}.py、api/cluster.py |
| 界面与工具 | 数据集/训练/集群面板；训练独立预检、用户外传确认、预算、进度/未知/退出状态；模型只准备计划或消费 grant | compute-data、GPU/Job 面板与工具注册 |
| 发布边界 | 必需回归清单、引擎和可选驱动依赖、桌面固定 dataset-worker 分派及协议模块打包 | requirements/、.github/workflows/、packaging/ |

CPU/GPU、集群扫描、LLM Token 和费用分别记录。未获得的指标保留 unavailable。依赖和支持矩阵见各域文档；源代码修改不等于三平台安装包已验收。

## 集成时修复的状态问题

- 本地快照已提交时，可信 worker 的顶层 result_committed=true 回执保留成功结果；迟到 Stop 保留 cancel_requested，并停止未完成的自动激活。
- 冷启动没有内存 Session 时，临时 Job bridge 同步原 Job；native 事件和游标通过持久 outbox 交付，终态在 Job 确认前继续对账。
- 崩溃停在 created/queued 的 remote Job 恢复后使用原身份继续，保留取消意图。
- 本地 Stop 与远程状态更新之间崩溃时，恢复导入原 Job 已持久化的取消，不用旧运行状态清除它。
- 产物提交与取消在同一执行库事务内判定；取消不能覆盖已提交成功、使下载入口隐藏有效产物。
- 清洗和分析表使用新派生快照；后续查询返回明确 srcN 表名，裸表名按 SQL AST 选择来源，不以子串碰巧命中。

## 本地物理规模验收

使用 scripts/verify_bigdata_scale.py，两种合成实体文件各 220 万行，每种三次。每次确认 COUNT=2200000、SUM(amount)=10998900000，生成 18 个 shard，六次均成功。输入物理体积超过 2GiB，未使用高压缩逻辑体积替代。

| 格式 | 物理字节 | 耗时中位数 / 范围 | 进程树 RSS 峰值 | 临时目录峰值 |
| --- | ---: | --- | ---: | ---: |
| CSV | 2282244712 | 58.68s / 52.78–205.03s | 282284032 B | 2294410512 B |
| Parquet | 2301970202 | 22.07s / 20.33–38.88s | 350527488 B | 2314984369 B |

SHA-256：CSV b46f5dcaf4b0cfd27949cbc334e1b22a67e5c3b5a88e103d30975b1dc54e4c46；Parquet 126b30637b8ca4974dde197de7df5f2fc9456afe8f7955572eb230d23bb3440c。

峰值按 200ms 采样，包含控制 worker 与其子进程。全组输入、已保留试验产物和活动 staging 的最大统计占盘为 6982254649 B。输出目录峰值不是最终快照大小；临时盘计数含私有输入副本和 staging。未将旧测量组或其它用户文件计入本次目录统计。采样可能漏掉短暂峰值，RSS/盘和期限为监测限额，未声称 OS 硬隔离。

原始记录保留在 tmp/bigdata-scale-acceptance-20261114/summary.json；该目录和实体输入遵守 tmp/ 忽略规则，不进入 Git。另有早期六次通过记录在 tmp/bigdata-scale-20261114，仅测进程树和输出目录，不与本表合并统计。CSV 波动明显，不能据此承诺吞吐提升百分比。

## 本轮本地门禁与界面

最终 npm run quality 已完整通过，退出码 0：Python 461 项（535.345s）、前端 115 项、图表 46 项；格式、lint、聊天构建、工具清单与资源依赖图检查均通过。完整日志保存在 [本地质量日志](../tmp/m3-bda-quality-20261114.log)。另有 97 个交付/回归文本文件的严格 UTF-8、LF 和空白检查通过，git diff --check 通过；新增清单文件均未被 Git 忽略。

定向结果：远程执行 18 项通过（162.048s），本地提交/恢复 20 项通过，数据工具 6 项、路由 13 项、有界合并 4 项、旧桌面启动器 5 项通过。定向项目大多已包含在共享门禁，不重复相加。Job/store 使用真实本地存储，CPU 模型实际训练；网络目标为受控 RPC/SFTP。

集成全量首次在 460 项 Python 中有 3 项失败（启动探测和等待期限），未将该次记录标为通过。修复测试构造失败时的进程清理、按任务真实截止时间等待，并在 runner 中保留不泄露 stderr 的探测失败码；断言和真实任务运行预算均保留。远程执行专项及随后重跑的完整共享门禁均已通过。

浏览器使用临时空数据根，无真实账号、业务数据、服务器或集群连接。合成 CSV 已完成计划（200）、后台提交（202）、Job 成功、快照发布、版本/预览（200）、复用激活（200）和实际 Parquet 下载（200）；下载后 SHA-256 与清单匹配，预览保留 001/002 的前导零。浏览器证明保存在 tmp/m3-bda-browser-proof.json。三面板可加载。桌面和手机、中英文及浅/深主题均检查，手机关闭按钮不再换行，无横向溢出。构建曾产生旧 chunk 的 404，重载最新构建后当前资源无失败；不把旧日志当成当前请求错误。

## 2026-11-15 推送前 UI 复核

使用真实本地应用和独立 Playwright 浏览器，检查 1440×960 桌面和 375×812 手机，覆盖数据集、训练、集群 SQL、GPU 设置和 Job 历史，以及中英文、浅色/深色。通过界面上传合成 CSV、准备计划、导入、刷新 Job 状态、选择快照、预览及列出下载文件。无未捕获页面异常或当前资源失败，无页面横向溢出。

修复两处实际显示问题：GPU 设置在手机上采用横向滚动导航，内容恢复完整宽度，表单输入 16px/44px，桌面双栏保持；工作目录未挂载提示补齐中英文，并注册 data-i18n-title 以随语言切换刷新。纯训练连接隐藏推理端口。截图保存在 tmp/ui-*-20261115.png，不进入发布包。当前改动仅涉及前端，沿用此前通过的 461 项 Python/46 项图表结果；前端 lint、构建及 115 项回归另行重跑。

## 验收限制与环境前置

1. 没有供应的真实 Linux SSH/CUDA 训练服务器。真实 SSH/SFTP/vault、systemd 重启、CUDA 阻塞取消、GPU UUID/显存和外部占用仍待 M3-01/02/04/05/07 硬件验收；本地 CPU 协议/模型训练不能替代。
2. 没有真实 Trino、HiveServer2、Spark Thrift Server。受控 HTTP/Thrift 合同已测试，实际版本/TLS/LDAP/权限、资源组、scan/CPU 限额、Spark NTZ 和取消仍待 CL-01–04。
3. 通用 DataFrame 算法与图表走有界兼容入口，超限明确失败，需先聚合或物化投影；不会截断训练样本。固定 MLP/RNN 内核支持 DatasetHandle；不宣称所有算法均为 out-of-core。
4. 首版集群认证为 Trino none/basic/JWT 和 HS2 none/LDAP。Kerberos、客户端证书、Spark Connect、任意会话属性未开放。云资源购买、DDP、AutoML 和通用 epoch 续训不在本期。
5. 桌面固定 worker 分派有源码测试；Windows/macOS 冻结包、Linux 服务和托管多租户隔离未实测。Windows 过长临时路径曾触发文件系统限制，本轮验收采用短临时根；安装包验收仍需覆盖默认用户内容根和长路径。托管 remote training 在隔离前保持拒绝。

下一环境验收需要管理员在产品设置中配置目标和凭据引用，确认外传列/快照、运行预算及保留期；不用聊天明文密钥。当前未连接、安装或部署到任何外部服务器。
