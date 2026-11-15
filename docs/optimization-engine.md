# 主引擎优化接入

本页记录 `agent/agent.py` 与 `agent/number_audit.py` 的实际接口。共享模块契约以 `27-optimization-integration-contract.md` 为准。

## 调用授权

主回合、原生工具、文本伪 XML fallback、保守并行和委派统一调用 `authorize_tool_invocation`。当前策略保存在 `_CURRENT_INVOCATION_POLICY`，通过实例 ID 隔离。主回合根据 typed activation、可信 Skill、当前请求发现、服务端确认、运行资源及配置的父策略构造策略。

阶段筛选与最多 20 个展示工具只影响模型看到的 schema。执行权限由策略计算；空 `allowed_tools` / Skill 白名单拒绝全部。模型参数 `confirm: true` 不能创造服务端授权。可选服务器字段如下。

| 字段 | 用途 |
| --- | --- |
| `agent._tool_invocation_policy` | 父能力、只读、动作及审批约束；Workflow 可以直接传入 |
| `agent._explicit_request_tools` | 当前请求已在服务器确认的显式授权工具 |
| `agent._workflow_approved_tools` | 已批准 Workflow 的工具授权事实 |
| `agent._mcp_tool_specs` | 选中的 MCP 工具的执行与副作用元数据 |

fallback 先转成普通 tool call，再经过同一参数验证、Hooks、执行、结果 envelope 和审计路径。确定性 Workflow 创建、导出确认、预取知识及断线后的 schema 重读也经过执行授权。

## 执行生命周期

API 的请求 root 为 `agent._execution_context`。已有 `_execution_bound_turn` 保持 public `run` 签名。模型创建、重试、压缩模型、流式 chunk、工具执行前后及结果发布均检查当前 `ExecutionContext`。

委派兼容旧参数，并增加 keyword-only 参数。

```python
agent._run_delegated_llm(
    member=member, prompt=prompt,
    execution_context=None,
    invocation_policy=None,
    quality_review=False,
    parent_claim_ids=None,
)
```

`execution_context` 是父 context。wrapper 创建一个 child，在同步模型/工具真正返回并退出 wrapper 的 `finally` 时 acknowledge；没有父 context 时创建本地 context。成员执行先调用 `begin_member_turn(..., execution_context=request_context)`，随后把返回的成员 context 传给 wrapper。完成调用传回成员 context、attempt ID、evidence 和 quality_review；成员自身的 acknowledge 由 store 执行。

`_submit_execution_worker` 为每次提交创建 child，并用 `copy_context` 传播授权与执行状态；语义快照和当前结果 grants 在 worker 内复制。Native Job bridge 在 worker wrapper 中 acknowledge child。关闭 SSE generator 只取消 Job/context，阻塞 worker 尚未返回时仍持有该 child。

## 工具结果与证据回读

原生和委派结果复用 `make_tool_result`，传入当前 `semantic_context`、`execution_context` 与受控的 `parent_claim_ids`。委派成功工具强制保存完整结果，`tool_events` 包含有界预览、envelope、artifact/claim 引用和 Hooks 事件。成员最终总结与实际子工具数字证据使用不同字段。

| 委派返回字段 | 内容 |
| --- | --- |
| `content` | 成员正文，复核与结构化节点为原始 JSON |
| `tool_events` | 实际工具事件、envelope 与受控引用 |
| `evidence` | claim/artifact 引用及当前子执行产生的 grants |
| `_number_evidence` | 仅供当前父回合内部数字核验的实际子工具结果 |
| `_data_tools` | 成功产出数据的子工具名称 |
| `usage` | nullable usage 汇总及逐调用 model_calls |
| `model_routing` | 实际模型选择及 gate 原因 |

父引擎用 `_accept_delegated_evidence` 合并内部证据与 grants；成员正文不能充当查询结果自证。主工具 envelope 保留子 claim/artifact 引用，并将有效子 claim 接成父 claim。

`read_tool_result` 支持 `tr_` 和 `ec_`。`ec_` 通过 `read_evidence_claim` 回读 `lineage` / `sql` / `result`，offset 分页、每页最多 4000 字符，并保留同 session/workspace 的 grant/hash 检查。恢复输入仍按原有最多 20 个普通结果窗口处理，claim grants 独立保留；本轮新产生的受控引用不因展示窗口丢失。不存在按任意 ID 跨 session 查找或自动授权的路径。

知识原生与委派调用均转发 `definition_ref`。`_knowledge_followup_required` 为真时，即使已完成知识预取，也保留补读工具。

## 结构化复核

单次委派、动态团队以及 Workflow verifier 通过 `QUALITY_REVIEW_INSTRUCTION`、`parse_quality_review`、`quality_review_blocks` 处理以下原始 JSON。

```json
{"decision":"pass","issues":[],"evidence":["ec_reference"],"missing_evidence":[]}
```

`pass` 带 issues 或 missing_evidence、无效 JSON、缺少必需字段，以及没有引用已提供证据的数字复核均进入 `needs_review`。不再通过中文词串猜测审核结论；复核响应不追加工具页脚。审核输入保留成员正文及受控引用，并允许 `read_tool_result` 分页检查完整材料。

`member['_structured_output_only']` 与旧 Workflow 原始 JSON 指令均兼容。纯文本材料的审核可以基于所提供材料进行；不得把成员自己的总结当成独立数据来源。

## 数字核验

只要当前回合有成功的数据子工具，纯委派也触发主数字核验。核验输入排除协调工具的最终总结、envelope summary/debug、模型生成的委派参数正文，以及注入的数字修复提示；SQL 数值阈值保留原有宽松 allowance。

原有年份、编号、小整数、极小小数、范围、数量级与舍入抑制保留。普通整数也不再通过任意子串匹配命中。百分比接受以下有限依据。

- 工具/用户明确给出的 `%` / `‰` 数值，或声明了单位的 `_pct` / `_percent` / `_rate` / `_ratio` 字段。
- 同一结构化记录内声明关系的 numerator/denominator、part/total、count/total、missing_count/row_count 等字段；显式单位冲突会拒绝。
- 同一记录的 previous/current 对应增长率。
- 报告同一句内写明的同单位分子/分母，且两个值都能在实际工具证据中找到。

不会枚举无关字段的任意数字比例。`number_evidence_gaps` 返回缺少带单位比率或分母的具体缺口；一次修复后仍未满足时保留核验提示。

## 模型与 usage

`resolve_profile_model` 接收服务器设置的 `_approved_role_model_policy`、`_model_routing_gate`。缺少有效 mapping/gate 时继承用户模型。有效 gate 只能选择同 provider 的已批准测试模型；调用仅使用局部 model 变量，不改共享 `self.model`。

委派 `usage['model_calls']` 及原生 usage/error 事件的 `model_calls` 包含 `model_call_id`、attempt、model/provider、nullable input/output/cache、usage_available、usage_source 与 status。失败重试条目保留 unavailable；取消状态使用 canceled。委派模型失败异常还附 `exc.delegated_usage`，动态任务失败路径将它传给预算层。模型未报告 usage 时保持 None，不伪造为真实零。

API/session、成本 store 持久化、Workflow store 的成本聚合与最终回归清单由主线程接入。

## 离线验证

针对性回归文件为 `tests/test_agent_optimization_integration.py` 与 `tests/test_number_audit_goals.py`。FakeModel 和阻塞工具覆盖取消后无调用/无迟到发布、Job 退出租约、父子权限、空白名单、cap 与权限分离、fallback、完整结果与 claim 回读、结构审核、数字自证、百分比及模型继承。

PowerShell 中设置唯一临时 `BAA_DATA_DIR` 与 `PYTHONDONTWRITEBYTECODE=1`，使用 E 盘 Python 运行 unittest discovery。测试不调用真实 LLM；离线结果不构成启用真实模型成本 gate 的证据。共享全量验证与构建由主线程执行。
