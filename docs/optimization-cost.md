# B/E：质量成本基线与受控策略

本轮交付复用已有成本账本、基线汇总器、Token breakdown、Prompt Cache 和团队 Profile。没有调用真实模型、远程 MCP 或付费服务，没有改变默认模型，也没有启用默认裁剪策略。

## 已执行的验证

2026-11-14，在隔离 worktree 使用 Python 3.13、独立临时 BAA_DATA_DIR 运行：

```powershell
& 'E:/Tool/Python313/python.exe' -W error::ResourceWarning -m unittest discover -s tests -p 'test_optimization_cost.py' -v
& 'E:/Tool/Python313/python.exe' scripts/run_optimization_baseline.py --manifest tests/fixtures/optimization_cost/manifest.json --trials 3 --output-dir <临时输出目录>
```

- 专项回归：38 项通过。覆盖旧 SQLite 迁移、旧版跨范围重复回执合并/冲突回滚、真实零用量与缺失用量、失败尝试计费、重放去重、跨范围可见性、并发去重、不同币种/价格版本、缓存指纹/隔离、nullable provider usage、角色上限、批准映射和严格配对门禁。
- 固定离线任务：14 × 3 = 42 条试运行，84 项具名断言通过，共执行 108 次本地工具/适配器操作。工具次数包括受控适配器边界及其内部 SQL 操作；不是供应商工具调用数。
- 固定输入 SHA-256：`8ecb1da2fb129327c3cd77f8a79fd9e9873cb291cfc94079a0cc19db1d8f6895`。运行器先核验夹具字节哈希及所有案例的 snapshot_id。
- 所有试运行模式为 offline_contract、usage_source 为 unavailable；真实输入/输出 Token、缓存 Token 和实际费用均为 null。真实模型门禁返回 blocked。
- 首次迁移测试遇到 Windows SQLite 连接未关闭导致的临时文件清理错误，已用显式关闭修复；上述 38 项为修复后的结果。

以下是独立命令的本地实测。耗时包含本地夹具准备、SQL、文件与契约检查，首轮模块加载会扩大范围。小样本仅列中位数与最小/最大值。

| 案例 | 次数 | 操作中位数 | 耗时中位数 ms | 最小–最大 ms | 质量/证据/交付 |
|---|---:|---:|---:|---:|---|
| single-table-v1 | 3 | 2 | 0.481 | 0.429–0.967 | 全通过 |
| cross-table-v1 | 3 | 2 | 0.531 | 0.475–0.561 | 全通过 |
| knowledge-v1 | 3 | 2 | 0.431 | 0.429–0.504 | 全通过 |
| report-v1 | 3 | 6 | 13.564 | 13.485–59.597 | 全通过 |
| chart-v1 | 3 | 2 | 3.204 | 2.292–3.777 | 全通过 |
| file-read-v1 | 3 | 2 | 2.242 | 2.021–2.757 | 全通过 |
| mcp-v1 | 3 | 2 | 0.559 | 0.506–0.560 | 全通过 |
| workflow-v1 | 3 | 3 | 2.775 | 2.335–3.206 | 全通过 |
| dynamic-team-v1 | 3 | 3 | 1.142 | 1.130–4.765 | 全通过 |
| retry-v1 | 3 | 3 | 0.431 | 0.427–0.744 | 全通过 |
| quality-v1 | 3 | 2 | 0.516 | 0.457–0.627 | 全通过 |
| delivery-v1 | 3 | 2 | 4.047 | 2.850–4.767 | 全通过 |
| revision-v1 | 3 | 3 | 6.146 | 5.619–6.328 | 全通过 |
| cancel-v1 | 3 | 2 | 17.177 | 15.383–43.020 | 全通过 |

试运行会输出 trials.jsonl、summary.json 与 report.md，内容仅含不透明标识、哈希、数值指标、具名断言和质量布尔值，不复制问题、答案、SQL、工具正文或原始行。命令退出码为 0 表示离线契约通过，不表示真实模型优化门禁通过。真实导入模式门禁拒绝时退出码为 2。

## 实测边界

单表/跨表/数据质量/分母计算执行真实本地 SQLite 查询。退款先按订单预聚合，避免多笔部分退款重复累计订单收入；分母使用时间窗内已支付订单的 distinct order_id。知识场景执行本地 FTS5，不代表完整生产知识检索或嵌入质量。长报告场景使用现有 make_tool_result 和 read_tool_result_artifact 实际持久化、授权及分页精确回读。

MCP transport、Workflow 编排和团队成员生成使用明确的本地受控适配器。团队结果通过真实结构化 review parser；取消场景使用真实 ExecutionContext 并检查退出前父租约保留。Workflow 重连通过持久记录读取，检查不再次执行查询。图表场景检查 spec 与数据依据，未运行渲染器。定义修订场景验证本地不可变快照，生产本体存储由语义域回归覆盖。

这些结果不证明真实 LLM 的语义正确率、模型延迟、缓存命中率或降本幅度。测试中构造的 actual_model 格式记录仅用于纯函数门禁回归，不写入离线报告，也不充当实际用量。

## 成本账本兼容接口

`CostEventStore.record` 保留原有 keyword 参数，并支持以下可选字段：

```python
record(..., input_tokens=None, output_tokens=None,
       cached_input_tokens=None, cache_write_tokens=None,
       status='unknown', usage_source='legacy_unknown',
       measurement_mode='legacy_unknown', model_call_id='', attempt=1,
       retry_of_event_id='') -> bool
model_cost_event_id(model_call_id, *, attempt=1, scope_type='session',
                    workspace_id='', session_id='', scope_id='') -> str
```

- 使用全调用范围唯一的 model_call_id；同一实际调用的多次回执沿用同一 call id/attempt。真实重试必须增加 attempt 或换 call id。事件 ID helper 可加入 owner id，避免仅局部唯一的 provider id 跨 owner 冲突；scope 参数作为兼容视图参数保留，不改变同一次调用的事件 ID。
- 同一 workspace/session 的 call id/attempt 只形成一条费用记录。会话、团队和 Workflow 的观察通过 cost_event_scopes 关联；summary 和 list_events 的范围过滤可查询任一关联，不重复收费。list_events 返回 canonical event 的原始 scope 字段及完整 scopes 数组。较早按范围记录的同 call/attempt 重复行在迁移中合并、保留各范围并更新重试引用；若已计量数值、价格或来源冲突，迁移事务回滚，要求人工核对原记录，不选择任意费用。
- 不带 model_call_id 的旧调用仍按 event_id 更新，无法凭空推断旧会话/任务汇总是否为同一次调用。不要把累计团队/Workflow 用量伪装为一条模型调用，否则会与逐调用记录重复累计。
- 缺失 usage 使用 None；不要传 `value or 0`。供应商明确报告的零仍为零。实际费用只接受 actual_model 与 provider_reported/operator_verified，失败/取消/超时调用若已计量仍计入费用。
- 旧 NOT NULL 列保持兼容，通过 availability flags 读取为 null；历史默认零与非零值都保留 legacy_unknown，不能据此声称已知真实 usage。
- 新的未知回执不擦除已有计量。更正用量而未重新定价时清空旧费用。actual/offline/estimated 的顶层混合总量为 null，分模式见 measurement_totals，细分来源/状态见 groups。缓存趋势同样按模式/来源/状态分组。
- occurred_at 使用含时区的 ISO 时间，存储统一为 UTC。跨价格版本或币种的成本总额为 unavailable。

`finalize_prompt_breakdown(breakdown, usage, *, usage_source='provider_reported', measurement_mode='actual_model')` 新增 measurement_mode 与 usage_complete。usage_available 表示输入/输出计数可用；usage_complete 额外检查总数与缓存计数一致。空 usage 不成为零。Anthropic 风格的 input_tokens 会加回已报告的 cache read/write；provider-neutral prompt_tokens 不重复加回。offline_adapter 自动标为 offline_contract。

## 固定试运行与严格门禁

```python
manifest_fingerprint(manifest) -> str
prompt_fingerprint(prompt) -> str
import_real_trials(manifest, records_or_jsonl_path, *, variant=None) -> list[dict]
evaluate_optimization_gate(manifest, before, after, *, policy_id='context-v1') -> dict
```

上述函数在 data.optimization_trials；data.cost_baseline 也提供 evaluate_optimization_gate 兼容入口。基线聚合器支持脱敏严格导入、逐字段 measured_count、缺失值保留、模式分组与中位数/范围。不同对照配置或模式的混合数值不再汇成一个平均/中位数。

真实导入要求完整输入/输出 usage、全部调用尝试的不透明 model_call_ids、实际成本及来源、固定模型/provider、价格版本/币种、缓存/团队配置标识、runtime/tool 契约版本、manifest/prompt/snapshot 指纹与 pair_id。使用 price_table 定价时还须有明确的 cache read/write 计数，不能把缺失缓存用量按零定价；所有已知缓存计数之和不得超过输入总量。不支持原始内容或未知字段。失败运行可以导入留档，但不能进入启用门槛。导入校验声明和完整性，不验真供应商发票；operator_verified 和批准配置必须来自受控服务端数据，不能来自模型参数。

门禁逐案例要求至少三次、前后 pair_id 集合完全一致、所有配置/模型/价格/快照/问题指纹相同、调用身份不重放、全部质量/证据/交付通过、预期证据断言和引用齐备、单份交付；每一对费用/重试/返工不增加，各案例耗时中位数不增加。各案例费用中位数之和须至少下降 1%，不同币种不得相加。返回 eligible、eligibility、policy_id、reasons、manifest_hash、comparison_hash、models 及可比较指标。离线记录一律无法通过真实模型门槛。

```powershell
& 'E:/Tool/Python313/python.exe' scripts/run_optimization_baseline.py --manifest <获准固定清单.json> --import-before <before.jsonl> --import-after <after.jsonl> --policy-id context-v1 --output-dir <输出目录>
```

真实模型试运行尚未执行；本轮不请求或隐含授权付费调用。

## 缓存、上下文和模型档位

Prompt Cache 继续兼容旧参数。stable_prompt_cache_key 的完整 schema、stable_prefix、stable_revision、provider/model/stage 指纹均为不透明哈希；对象键排序不影响身份，实际序列顺序保留。user/workspace 使用无歧义编码隔离。build_cacheable_prompt 复制输入，返回 messages/tools/cache_key/metadata；只把明确指定的 system/developer 稳定规则放在前部，动态知识、记忆、项目指令、问题与任务状态由调用者放入 suffix。不得把用户动态内容冒充稳定规则。

```python
assemble_stable_context(*, stable_rules, dynamic_context, history, tools,
                        provider, model, workflow_stage='', stable_revision='') -> dict
recommend_context_optimization(breakdown, *, max_recommendations=3,
                               gate=None, policy_id='context-v1') -> dict
recommend_role_limits(profile, *, requested_output_tokens=None,
                      requested_tool_calls=None) -> dict
resolve_profile_model(profile, *, inherited_model, inherited_provider='',
                      tier_mapping=None, gate=None) -> dict
```

前两个上下文函数位于 agent.context_optimization；模型适配位于 agent.teams.model_policy。推荐只提供有界目标与顺序，不自动裁剪、不把重叠的 history/tool-result 估算相加，不移除证据或用户约束，也不扩张工具权限。角色限制取调用者上限与现有 Profile 上限的交集，0 次工具保持 0，复核要求保留。

resolve_profile_model 返回 model/provider/applied/reason/policy_version。tier_mapping 必须为服务端受控 `{version, policy_id, approved: true, tiers: {tier: {provider, model}}}`。还需对应 actual_model 有效 gate、同一 policy_id、可追溯 manifest/comparison hash 和 gate 中已验证的候选模型。provider 必须等于继承 provider，不能通过此 helper 切换凭据。缺失批准、映射、门禁或模型验证均继承用户所选模型。现有 Profile 预设保持原样。

## 主线程集成

- 逐模型调用创建 ID 和 attempt，把完整 optional provenance/status 传给账本；不要仅在 usage 非空时记事件，也不要把未知计量转零。失败/取消回执和真实重试仍须记录。
- 适配 session/团队/Workflow 使用调用级计量。需同时保持相同 call-attempt 身份及 session/workspace，才能利用跨范围视图去重；累计值没有调用身份时明确保留未知关系。
- 在主 Agent 的 prompt assembly 中明确划分稳定规则和动态指令，传入完整 schema、stable_prefix/revision。helpers 本轮未写入共享 agent.py/session.py。
- 新增文件应加入公开白名单及必需回归：tests/test_optimization_cost.py、tests/fixtures/optimization_cost/manifest.json、snapshot.json，新增实现模块和此文档；由主线程统一修改 .gitignore 与 required-regressions.json。
- 共享全套回归、构建与浏览器验收由主线程执行。本域只运行上述专项测试和独立离线基线，未提交、推送或部署。
