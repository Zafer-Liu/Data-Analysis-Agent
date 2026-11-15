# 首批正确性与发布回归门禁

本轮覆盖统一数据选源、知识检索证据准入，以及干净检出的必需回归。后续语义修订、团队取消和模型策略不属于本批实现。

## 行为契约

### 数据选源

- 查询、分析和图表共用数据获取策略；同步及 JobRunner 路径均使用改写后的 SQL。
- `srcN__` 显式选源不可用时返回错误；跨源查询缺少合并连接时返回错误。
- 显式选择的来源失败，不转用包含同名表的其他来源。
- 无显式来源的查询沿用原有选源与失败回退行为，引用记录实际成功来源。
- 分析/图表产生的 `data_refs` 来自实际取数；聚类及十分位标签的补充取数也使用共享路由。
- 分析产出的派生表继续通过既有写入路径持久化。

### 知识检索

- 向量及词法候选必须有正分，并通过对应准入阈值；关键词候选必须真实命中。
- 文档的词法与 FTS 命中合为文本排名，再与向量排名进行 RRF。结构化定义和文档均使用两个独立通道。
- 全局 Top-K 继续限制所有普通知识类型的合计结果数。结果附 `matched_channels`，供检索诊断使用。
- 查询嵌入失败时保留文本检索；签名不兼容或无效的文档向量不能形成证据。
- 文档 FTS 使用与查询一致的 jieba 分词。首次打开旧库时仅重建派生 FTS 索引，保留原始片段、嵌入及来源。

默认配置：

| 配置 | 默认值 | 含义 |
|---|---|---|
| `BAA_KB_MIN_STRUCTURED_SCORE` | 0.015 | 结构化结果最终 RRF 阈值 |
| `BAA_KB_MIN_CHUNK_SCORE` | 0.015 | 文档结果最终 RRF 阈值，允许单个真实通道命中 |
| `BAA_KB_MIN_STRUCTURED_LEXICAL_SCORE` | 0.12 | 结构化词法准入 |
| `BAA_KB_MIN_STRUCTURED_VECTOR_SCORE` | 0.55 | 结构化向量准入 |
| `BAA_KB_MIN_CHUNK_LEXICAL_SCORE` | 0.12 | 文档词法准入 |
| `BAA_KB_MIN_CHUNK_VECTOR_SCORE` | 0.55 | 文档向量准入 |

RRF 是排名分，不是相似度或概率。环境变量中的显式阈值仍生效；阈值提高后可能排除单通道结果。

## 必需回归清单

[`tests/required-regressions.json`](../tests/required-regressions.json) 是共享回归的唯一清单。Python 和 Node 两组都必须非空，所列文件及夹具必须存在于检出目录。Python 模块发现零个测试也会失败。其他本地实验不因通配符而自动进入发布门禁。

新增共享回归时：

1. 使用合成或脱敏输入，并关闭真实 LLM、外部服务及模型下载。
2. 将测试和全部夹具加入清单，在 `.gitignore` 中逐项增加对应例外。
3. 需要构建产物的前端测试必须在聊天构建完成后执行。

新增取数回归覆盖同名表的第二来源、后台取数、引用、跨源查询、失效来源以及兼容入口。检索回归覆盖无关文档、单通道命中、嵌入失败、中文 FTS、旧索引迁移和全局 Top-K。

## CI 与构建契约

- `quality.yml` 对 PR 与 main 分支更新执行共享 Python 契约和前端构建/契约。
- 桌面发布流程复用同一 Python 清单，移除缺少 `tests/` 时跳过的路径；原有图表回归继续执行。
- 聊天构建输出 `static/dist/.vite/manifest.json`。校验器检查入口守卫、资源存在性、依赖记录、动态模块反向引用入口，以及模板缓存版本与 `package.json` 的一致性。
- 源码前端校验需要先执行新的聊天构建，生成依赖 manifest。旧产物本身不能证明符合新契约。

## 验证入口与当前状态

2026-11-14 已在隔离工作树执行完整 `quality` 流水线：298 项共享 Python 回归、76 项前端回归及 46 项图表回归通过；格式、lint、工具清单、聊天构建及依赖校验通过。当前清单包含 24 个 Python 文件、11 个前端文件和 3 个必需夹具。实际模型试运行未执行，离线试运行不构成真实降本证明。完整记录见 [optimization-validation.md](optimization-validation.md)。

以下入口用于重现验证。

只检查必需文件：

```bash
python scripts/run_required_regressions.py --check-only
```

执行共享 Python 回归：

```bash
python scripts/run_required_regressions.py --suite python
```

构建并验证聊天产物：

```bash
npm run build:chat
```

构建后执行共享前端回归：

```bash
npm run test:frontend
```

这些检查使用现有环境；无需为本批修改调用真实模型或部署应用。
