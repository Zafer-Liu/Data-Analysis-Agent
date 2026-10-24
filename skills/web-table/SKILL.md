---
name: web-table
description: 把网页、JSON、CSV、PDF 链接里的表格抓取注册为可查询数据源（数据获取入口）
icon: 🌐
tags: [mode-business, data-preparation, web-import]
allowedTools: [fetch_table, browser_navigate, browser_extract_tables, browse_webpage, get_schema, query_data, generate_chart]
---
# 网页表格导入

用户给一个链接（或要求把某个网页/PDF 的表格"抓下来"）时，把表格注册为当前会话的数据源，注册后立即用常规 SQL / 图表工具分析。注册是可叠加的：已有上传数据不受影响。

## Tool routing

1. 用户给出具体 URL 且要求导入表格时，直接调用 `fetch_table(url)`——一步完成抓取 + 建表 + 注册，支持 HTML 页面、JSON 接口、CSV 文件和 PDF 文档。返回结果中列出每张表的名称、行数、列名和截断标记。
2. `fetch_table` 返回 FETCH-001（无表格）且页面需要 JavaScript 渲染时：先 `browser_navigate(url)` 打开页面，确认表格已渲染，再调用 `browser_extract_tables()` 提取当前页面的全部 `<table>` 并注册。
3. 仅当需要先阅读页面内容、确认目标链接时才用 `browse_webpage`；不要用它替代 `fetch_table` 做导入。
4. 注册成功后，向用户报告表名、行数与截断情况，然后用 `get_schema` 确认结构，进入常规分析（`query_data` / `generate_chart`）。
5. 多表时提醒用户表名清单，让用户指定分析哪张表；不要未经确认就猜测用户要哪张。
6. 私网/本地地址会被拒绝；找不到表格时如实说明，不要编造表内容。

## Implementation reference

- 抓取与归一化管线：`data/table_extract.py`（上限常量、垃圾表过滤、表头识别、数值提升）
- 多表数据源：`data/sources/extracted.py`（`ExtractedTableDataSource`）
- Agent 分发与实时刷新：`agent/agent.py` 中 `fetch_table` / `browser_extract_tables` 分支
- PDF 上传入口：`api/datasource.py` 的 `upload_file`
