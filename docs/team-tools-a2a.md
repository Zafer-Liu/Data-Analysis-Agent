# 主 Agent 团队工具与 A2A 通讯

团队现在是主 Agent 的内置工具能力。普通对话无需开启 Teams 或挂载工作区即可构建、调用子 Agent；`teams_enabled` 保留为优先使用团队协作的偏好。简单任务由主 Agent 直接处理。

子 Agent 是应用内有独立提示上下文的受限模型执行，不是独立系统进程。它继承当前模型配置，只能访问显式任务材料、相关邮箱以及已授权的只读工具；不会继承整段主对话，也不能递归创建团队、操作文件、修改数据或向用户提问。

## 主 Agent 工具

| 工具 | 行为 |
| --- | --- |
| `agent_create` | 构建或复用会话子 Agent，不启动执行；返回成员、协议版本与发现/RPC 路径。 |
| `agent_delegate` | 立即执行有界子任务。仅提供 `prompt` 即可，默认使用 `default/analyst`，自动构建缺失成员。返回持久化 A2A Task、结果及质量复核。 |
| `agent_task` | 按 `team_name`、`task_id` 查询任务；`action="cancel"` 请求取消，默认 `get`。 |
| `team_create` / `team_list` / `team_status` | 管理本会话的团队与成员，查看邮箱、活动任务 ID、近期 A2A 任务和工具证据。 |
| `team_plan_create` / `team_delegate` | 预览或执行带依赖关系、并发上限、Token 预算和固定质量复核的动态团队计划。 |
| `send_message` | 发送邮箱上下文，不自动执行成员；委派工具才会执行任务。 |
| `team_delete` | 保留原有活动成员检查和证据删除确认。 |

示例工具参数：

```json
{
  "name": "SQL分析员",
  "team_name": "销售分析",
  "role": "sql_analyst",
  "instructions": "使用给定时间窗和净收入定义，说明每项结论的查询依据。",
  "profile": "sql-analyst"
}
```

随后调用 `agent_delegate`：

```json
{
  "team_name": "销售分析",
  "member_name": "SQL分析员",
  "prompt": "检查已连接数据源中2026年10月与9月的净收入变化，先确认表结构，给出SQL、口径与结论。"
}
```

可选 profile 为 `inherit`、`data-scout`、`sql-analyst`、`knowledge-researcher`、`quality-reviewer`、`report-writer`。配置只收紧工具、输出和工具调用预算，不自动切换模型。已有同名成员通过 `agent_create` 复用其现有配置；需要更新配置时使用 `team_create`，运行或排队中的成员拒绝重配。复用团队保留成员身份、邮箱和既有任务。

## 协议版本与接口

实现依据 [A2A v1.0.1 规范](https://a2a-protocol.org/v1.0.1/specification/)与[官方 protobuf 定义](https://github.com/a2aproject/A2A/blob/v1.0.1/specification/a2a.proto)，线协议版本为 **1.0**，绑定为 **JSONRPC over HTTP**。不兼容 0.3 的方法名、`kind`、小写状态或角色字段。

每个会话成员有独立地址；将以下发现地址直接配置给客户端。多成员应用没有用一个全局 well-known 地址代表全部会话：

```text
GET /api/session/{sid}/teams/{team_name}/agents/{member_name}/.well-known/agent-card.json
POST /api/session/{sid}/teams/{team_name}/agents/{member_name}/a2a
```

Agent Card 的 `supportedInterfaces` 返回绝对 RPC URL、`protocolBinding: "JSONRPC"`、`protocolVersion: "1.0"`。团队名和成员名作为 URL 路径段时需进行百分号编码。

所有 RPC 使用 `Content-Type: application/json`、`A2A-Version: 1.0`。服务复用应用登录态和会话所有权校验；云端或桌面模式还要求当前用户登录。这是应用内的会话成员端点，部署时使用现有应用身份会话，未新增匿名公共端点或独立的 A2A token 登录方式。

| 方法 | 参数 | 成功的 `result` |
| --- | --- | --- |
| `SendMessage` | `message`，可选 `configuration`、`metadata` | `{"task": Task}` |
| `GetTask` | `id`，可选 `historyLength` | 直接为 `Task` |
| `CancelTask` | `id`，可选 `metadata` | 直接为 `Task` |

请求示例：

```json
{
  "jsonrpc": "2.0",
  "id": "request-1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "message-1",
      "role": "ROLE_USER",
      "parts": [{"text": "核对所提供数据的计算口径，并给出证据缺口。"}]
    },
    "configuration": {
      "acceptedOutputModes": ["text/plain"],
      "historyLength": 2,
      "returnImmediately": false
    }
  }
}
```

`SendMessage` 同步等待该成员完成，将交付保存在团队邮箱和 Task 中，不写入主对话历史。HTTP 调用返回该成员自身的 Task；主 Agent 工具路径在成员完成后另行执行固定质量复核。客户端需要复核时可显式向质量复核成员发送任务。

模型继承当前会话选择；HTTP 执行持有数据源快照并在结束时释放。用量按本次请求逐模型调用记录，托管模型沿用付费余额、配额校验及云端计费，重复投递同一有效消息不会再次执行或计费。

## 消息、任务与取消

- 请求消息角色为 `ROLE_USER`，成员输出为 `ROLE_AGENT`；文本 part 使用 `{"text": "..."}`。
- JSON-RPC `id`、`messageId`、Task `id`、`contextId`、`artifactId` 分别标识请求、消息、执行任务、上下文和产物，彼此不混用。
- 一个任务通常经过 `TASK_STATE_SUBMITTED`、`TASK_STATE_WORKING`，最终到 `TASK_STATE_COMPLETED`、`TASK_STATE_FAILED` 或 `TASK_STATE_CANCELED`。成功交付包含文本 Artifact，错误在 status message 中说明。
- 终态任务不重新启动。后续任务提交新 `messageId`，省略 `taskId`，可沿用 `contextId`。当前有界成员不支持对正在执行的任务追加输入。
- 在保留的任务记录内，重复的 `messageId` 和相同内容返回原任务；同 ID 不同内容返回参数错误。保留最多约 200 个近期任务，活动任务不因数量上限而删除；该机制不是跨无限历史的 exactly-once 保证。
- 同一成员不同时执行两个任务。动态计划对同名成员串行调度，依赖任务收到前置任务结果。动态任务保存对应的 A2A Task ID，取消团队计划会同步取消其成员任务，并阻止后续调用。
- 清空团队邮箱不会删除 A2A Task 历史和产物；解散含任务证据的团队仍须明确确认强制删除。
- `CancelTask` 将任务置为取消，模型与工具调用边界检查该状态。已经发出的提供方请求可能继续消耗用量，返回后不发布其迟到结果；运行成员等待执行器确认取消后才能开始新任务。取消不等同于杀死远程模型调用。
- 每个成员执行的总截止时间覆盖所有模型与工具轮次。同步工具和不支持 timeout 的模型客户端可能无法立即中断，返回后仍会检查截止时间。
- `GetTask.historyLength=0` 省略历史消息内容；省略该参数时返回保留的历史。成员端点不能查询或取消其他成员的 Task。

邮箱旧字段继续服务现有团队面板，任务分配与结果消息附带 A2A 结构，旧记录按需增量迁移。团队与 Task 仍然按应用数据目录中的会话存储隔离，线程内使用锁和原子文件替换；此文件存储适用于现有单进程服务，不提供跨多个服务进程的分布式队列或一致性保障。

## 支持范围与错误

当前仅支持文本输入/输出和同步发送。不声明 streaming、推送或扩展 Agent Card，拒绝 `returnImmediately=true`、非空 tenant、RPC batch 和通知请求。不实现远端 Agent URL 注册或跨网络主动调用客户端。现有聊天 SSE 是产品事件流，不是 A2A SSE。

| 错误码 | 含义 |
| --- | --- |
| `-32700` / `-32600` / `-32602` | JSON 解析、请求或参数错误 |
| `-32601` | 未知方法 |
| `-32603` | 服务内部错误 |
| `-32001` / `-32002` | 任务不存在或不可取消 |
| `-32003` / `-32004` | 推送或操作不支持 |
| `-32005` | 输入/输出媒体类型不支持 |
| `-32009` | 协议版本不支持；缺失版本头也拒绝 |

RPC 返回 JSON-RPC 2.0 信封，保留请求 ID，`result` 与 `error` 二选一。身份与额度问题沿用 HTTP 401/402/403；协议错误通过 JSON-RPC 返回。终结后的任务执行错误使用失败 Task 表达。

## 验证范围

回归用例位于 `tests/test_agent_team_tools.py`、`tests/test_team_a2a.py` 和 `tests/test_team_a2a_api.py`。覆盖工具曝光、注册一致性、无工作区构建、任务隔离、去重、取消与迟到结果、证据保留、只读 SQL、HTTP 请求校验、身份与计费衔接。

本次默认仅做 diff 卫生、UTF-8 和 LF 检查；测试、构建、类型检查和应用启动须经用户授权后执行。手动验收时先关闭 Teams 且不挂载工作区，在普通对话要求主 Agent 创建并调用子 Agent；查看团队面板的成员、结果与工具证据，再使用返回的 Task ID 验证查询和取消。最后用已登录的客户端按上述版本头验证三种 RPC 方法及重复消息不重执行。
