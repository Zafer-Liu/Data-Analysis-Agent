# 执行、取消与结构化复核

本轮复用现有 JobRunner、SQLite 事件、Task 存储及 Workflow。主引擎接入见 [optimization-engine.md](optimization-engine.md)。

## 执行上下文

`ExecutionContext` 维护 request/attempt 身份、父取消信号、总截止时间、子执行和实际退出确认。API 为每次请求创建 root，委派和 worker 使用 child；取消与超时不会自动确认退出。成员或数据源租约在全部子执行真正退出后释放。

聊天 stop 可携带 request_id，旧请求的取消/清理不能影响新回合。尚未退出的请求返回明确占用状态，避免重叠派发。A2A、团队计划和 Workflow 取消使用同一上下文；已取消或超时任务的迟到输出不再发布。进程死亡可证明时才清理孤儿，缺乏归属证据的旧任务保守保持占用。

## 授权

`ToolInvocationPolicy` 使用 ToolSpec、激活、Skill、数据/Workspace 可用性、父级权限和服务端确认事实。排名或模型参数不产生权限。主调用、委派与文本 fallback 使用相同检查；agent_task 的 get/list 为读取，取消仍需实际授权。

## 审核与证据

复核使用原始 JSON：decision=pass/rework/escalate，以及 issues/evidence/missing_evidence。无效 JSON、pass 带问题或缺失证据不会成为成功。成员工具事件保留有界预览和完整 Artifact/claim 引用，长材料可以受控分页回读。最终成员总结不充当数字的独立证据。

## 验证

执行域新增 30 项回归覆盖父子取消、截止时间、活跃子任务租约、请求重入、旧 attempt、迟到结果、进程孤儿、授权、结构化审核及 API/Workflow 生命周期。最终集成结果统一记录在 [optimization-validation.md](optimization-validation.md)。
