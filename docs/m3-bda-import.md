# M3/BDA 有界导入与 DatasetDataSource

2026-11-14。实现延续已有 import_schema、SnapshotWriter、DatasetHandle、DatasetCatalog 和 WorkspaceDBOwner；无第二套注册/预算框架。合同见 `29-training-bigdata-integration-contract.md`，数据基础和进程边界见 `m3-bda-foundation.md`。

## 导入流程与精度

`create_import_plan` 和 `run_import` 都使用固定 JSON 入口和受监督本地 Python worker，shell=False、isolated mode、禁写 pyc。规划中的采样、XLSX XML/共享字符串解析、全文件 SHA-256 同样受 RSS/CPU/期限监测。输入路径必须先由 API/workspace 后端授权；本模块不替代会话权限校验。

计划包含原始文件 stat/full SHA-256、source、固定 schema_policy、表映射、options、limits、budget 和 plan_hash。请求控制 JSON 有大小限制。实际导入先在私有 work 中复制授权输入，核对计划全文件哈希和 stat，再只解析该副本；不会把 mutable 外部路径当成已冻结快照。worker 不打开 catalog/workspace 的 live DuckDB 文件。监督器确认真实退出后才进行最终文件核验和目录发布。

CSV/CSV.GZ 用有界 lexical-record reader 保留原始字符串，再用 Arrow 执行固定 schema 转换。物理行、跨行 quoted record、字段、列数和实际解压字节都有上限；没有整表 pandas fallback。未闭合引号无法可靠重同步，明确失败。字段数或类型漂移默认失败；显式 `bad_rows='skip'` 及正数 max_bad_rows 才允许隔离，记录全量数量、各表数量和最多 20 条有界样例。输入行预算包含坏行，不把坏行无限排除在预算外。

Parquet 按元数据/row-group 批读，保留原生 DECIMAL、nullability 和时间精度；限制 footer 解码、实际 decoded bytes、batch、行和字段。单个极大原生字段仍可能在字节检查前分配内存，因此重导入放在受监督进程，不能把 batch 行数冒称 OS allocation hard cap。

XLSX 先逐成员核验真实解压量、XML DTD/entity、单元格坐标/数量、公式/错误，再用 openpyxl read_only 逐行组批。忽略恶意 dimension 声明，不并发持有全 sheet DataFrame。日期仅根据单元格类型及 1900/1904 epoch 转换，普通数值不猜日期。公式当前仅支持 reject；用户需导出明确的值版工作簿。

- 缺省 null tokens 只有空串，`NA` 是合法类别；可按列显式更改。
- ID 名称/前导零文本保留字符串。采样后整数列出现前导零，明确 schema_drift，不能悄悄变成整数。
- DECIMAL 不经 float/pandas round-trip；溢出或 scale 损失拒绝。原生高精度文件可保留，DuckDB 无法精确表达的查询明确 unsupported_precision。
- aware/naive 时间分开；缺少时区、隐式丢弃 offset、DST 歧义或文本亚微秒截断拒绝。原生 Parquet 纳秒保留在 canonical 文件；DuckDB timezone timestamp 查询精度为微秒，纳秒时区字段不能静默查询成微秒。
- 预览中的 DECIMAL、超出 JavaScript 安全整数的值使用文本，null 为 null，非有限 float 使用文本标记；不通过 pandas.to_json 收窄数字。

缺省值：batch 16MiB、同步 producer/consumer 队列 1、sample <=1024 行/256KiB、字段 1MiB、行 <=batch_bytes、列 4096、XLSX 压缩 50MiB/XML 512MiB/shared strings 8MiB/sheets 32/cells 500 万。实际值同时受 BudgetSpec 收紧。XLS 仅保留 <=5MB 的小文件兼容，不宣称流式 XLS。

SnapshotWriter 只写全新 Parquet 目录，缺省 shard target 128MiB，保存 row-group 元数据和实际观测最大行字节。分区需显式指定最多 3 个低基数键；缺省 64 个分区，硬上界 128，文件数受预算约束。空结果也写有 schema 的空文件。

## 查询、快照及 Workspace

`DatasetDataSource` 暴露 `.handle`，使用固定 snapshot 文件清单建立只读视图。每次读取 pin/hash 核验文件；active pointer 更新不会改已有 handle。SQL 仅允许已知表的单条 SELECT/CTE/set query，解析失败关闭路径；拒绝任意文件/URL、未知 UDF、SET、DDL/DML、extension 操作等。

批查询同时检查消费端行数/实际 Arrow bytes、取消、墙钟、RSS 和 temp，并保留 source budget 的收紧关系。fetch 行数依据固定输出宽度或导入时观测的最坏行宽；不能证明变长表达式宽度时退到单行读取。返回批有字节检查，但 DuckDB/native 解码前的 OS 分配上限仍未保证。

`execute_query` 兼容返回 `(DataFrame, error)`，限制 100000 行/64MiB，超限返回明确错误，不采样；`legacy_bounded_frame` 成功时返回 Arrow-backed DataFrame，失败抛异常。metadata/schema 读取不需要全表 pandas。

分区手工剪枝只对类型可证明的等值条件生效；日期字符串/浮点隐式转换交给 DuckDB，避免错误剪枝。Parquet 自身有投影、过滤和 row-group 下推。`last_scan` 区分候选文件统计与 EXPLAIN ANALYZE 实测 bytes；不能用 WHERE 字符串或 DuckDB 的 rows_scanned 计数单独证明真实剪枝。

大 immutable source 的 writable_conn 明确失败，create_analysis_table 要求派生计划。`derive_snapshot` 在相同作用域的独立 worker 中执行 SELECT，写新 dataset/snapshot，保存 parent IDs/SQL hash；原源不修改，空派生保留类型。

Workspace 导入把已发布 handle 的 Parquet 视图和 `_baa_dataset_mounts` 元数据在 workspace owner 的一个事务中切换，使用 expected snapshot 防止并发覆盖。dataset catalog 与 workspace 是两个数据库，**不宣称二者共同原子提交**。registry 写失败后，以 mount 元数据调用 `reconcile_mounts(runtime)` 重建缓存。

Workspace 读取经 owner-thread producer、有界 queue 和消费者背压，读取期间固定 mount/file 身份。关闭 iterator 会取消 producer，并等它实际退出回调后才释放 reservation/允许排队写入。small shadow 只允许累计 100000 行/32MiB 的精确 Arrow 副本，避免 DECIMAL 变 float；大源不能全库复制到内存。

小 CSV 保留可写内存表路径（物理 <=25MB，载入上限 100 万行/128MiB）；小 Excel 保留旧 header/pivot 行为，并加 XLSX XML/cell 预检及精确注册。两者 legacy 查询有界。大 CSV/XLSX facade 返回 DatasetDataSource；XLSX job 数据库通过 sidecar 重新构造 handle，不重新加载大 sheet。

## 实际接口

```python
create_import_plan(input_path, *, owner_id, session_id,
                   workspace_id='', options=None, budget=None)
run_import(plan, *, catalog, progress=None, cancel=None)
import_file_source(input_path, *, owner_id='local-default', session_id='',
                   workspace_id='', options=None, budget=None, catalog=None,
                   progress=None, cancel=None)
DatasetDataSource(handle, *, budget=None, name='')
source.get_schema()
source.get_preview()
source.get_table_schema(table_name)
source.get_preview_table(table_name, max_rows=100)
source.execute_query_batches(sql, *, budget=None, cancel=None)
source.execute_query(sql)
source.legacy_bounded_frame(sql, *, budget=None, cancel=None)
source.get_capabilities()
source.derive_snapshot(sql, *, catalog, budget=None, idempotency_key='',
                       name='derived', progress=None, cancel=None)
source.explain_query(sql, *, analyze=False, budget=None, cancel=None)
source.interrupt()
source.close()
WorkspacePersistentSource(db_path, display_name='工作目录', db_lock=None)
workspace_source.execute_query_batches(sql, *, budget=None, cancel=None)
workspace_source.reconcile_mounts(runtime=None)
parse_workspace_csv_job(ctx, runtime, file_path, table_name, file_key,
                        file_hash, old_tables=None)
parse_workspace_excel_job(ctx, runtime, file_path, base_table_name, file_key,
                          file_hash, old_tables=None)
parse_excel_job(ctx, file_path, db_path, filename)
ExcelDataSource.from_database(file_path, filename, db_path)
```

所有 iterator 使用者必须在成功耗尽后才发布完整结果；异常/取消不是 complete。提前停止要 close iterator。`progress(percent, message)`；`cancel()` 返回 true 或抛取消异常均停止。发布前可取消，提交窗口不取消；`run_import` 成功返回时版本已经提交，迟到进度异常/取消不会删除版本。

## 本地验证与接入要求

每次 PowerShell 使用唯一临时 BAA_DATA_DIR 和 E 盘 Python；不生成实体 2GiB 文件。最终 18 个 import 测试拆为两组，各 9/9 通过（19.317s、31.182s）；新增预览和双 epoch 断言分别定向复跑通过（4.196s、4.378s）。完整单次运行曾超过工具 60 秒窗口，已先确认测试进程退出再分组运行。

```powershell
& 'E:/Tool/Python313/python.exe' -u -m unittest discover -s tests -p test_dataset_import.py -k csv -k parquet -k partition -k exact_decimal -k fixed_schema -k long_quoted -v
& 'E:/Tool/Python313/python.exe' -u -m unittest discover -s tests -p test_dataset_import.py -k failed_import -k idempotent -k late_cancel -k multiple_sheets -k result_rows -k snapshot_pins -k source_changed -k workspace_stream -k xlsx_formula -v
```

覆盖真实小 Arrow/DuckDB 文件、全字节指纹变化、ID/NA/null/精确 DECIMAL/时区/双 epoch、多 sheet、坏行计数、字段/跨行/解压限额、公式/shared strings、分区正确性与实测 read-byte 剪枝、派生/空结果、旧 handle、取消和失败保留原版本、consumer 关闭后的 producer/写队列、workspace registry 重建、文件 pin 拒绝写入。未以 mock 代替这些本地数据路径。

主线程接入：

1. `api/datasets.py` 当前 `DatasetCatalog(**scope)`、`DatasetDataSource(handle, name=...)`、plan/run 构造方式已兼容；继续由 API 先授权输入路径，客户端只拿 plan/dataset/snapshot ID，不接受任意 root/manifest/path。
2. `run_import` 返回即已提交；主线程 worker 随后的 `context.check_canceled()` 不能把迟到取消覆盖为 canceled。激活相同 snapshot 可复用 `.handle.snapshot_id`，避免重复 source。
3. 对 legacy CSV/Excel/workspace 入口显式传递授权 scope/catalog 或统一改走 scoped import facade。兼容 helper 的 local-default/session='' 仅适合本地模式；现有 WorkspaceRuntime 没有 owner 字段时不能据此声称 hosted 租户隔离完成。workspace 共享 namespace 和 session API 需由主线程统一选择。
4. source 挂载/恢复保存普通 handle dict；merged source、DataTools、训练/图表/导出使用 capability/batch 协议，不能通过 `_conn`、全表 DataFrame 或 pandas JSON 绕过预算。不同 session/workspace 的 handle 仍须在入口鉴权。
5. UI 显示 schema_policy、bad_rows、complete、catalog_version 和稳定错误；不把截断/失败结果当完整快照。追加两个自有测试模块到正式清单由主线程完成。

仅本地 Windows、小文件和故障子进程已验证。OS 硬 RSS/cgroup/磁盘 quota、跨平台、真实 2GiB 吞吐/峰值与完整产品消费链仍待主线程/真实环境验收；Arrow 字节/行 guard 和 DuckDB memory_limit 不替代这些边界。
