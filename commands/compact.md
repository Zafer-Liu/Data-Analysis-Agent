---
description: 立即压缩当前对话上下文，保留关键结论和最近内容
usage: "/compact [保留重点]"
aliases: [c]
argument-hint: "[可选：希望摘要保留的重点]"
arguments: optional
type: backend
handler-key: "server:compact"
uses-model: true
category: session
icon: "🗜️"
---
Compact the current conversation now while preserving key analysis context.
