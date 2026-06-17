---
name: stock-picker-pipeline
description: >
  五层选股流水线。从热点事件到最终策略执行。
  触发：用户说"选股分析"、"产业链筛选"、"帮我看看[事件/行业]的标的"
---

## 🚫 When NOT To Use
- 用户需要的是行业整体行情/板块轮动/财经快讯概览 → 用 `investment-advisor`
- 用户需要的不是 A 股选股（如美股/港股/加密）→ 用 `deep-research` 或 vibe-trading 工具
- 用户只是要看持仓/账户/历史 → 通过 vibe-trading 交易连接器
- 非金融场景 → 不需要触发

## 🔗 Related Skills
- **investment-advisor**: 行业投研+全市场监测，stock-picker-pipeline 做具体标的筛选
- **deep-research**: 通用深度调研当不限于 A 股时
- **vibe-trading**: 提供回测、行情、因子分析等 MCP 工具，pipeline 是策略流程
  也由 cron 定时触发：热点事件变化时自动走完整管线
compatibility: >
  依赖：python3, yaml, json
  外部：event_collector_v3（可选复用）

# 选股流水线 — Stock Picker Pipeline

## 五层架构

Layer 1 - Serenity（产业链拆解）
: 从热点事件拆解产业链环节，定位瓶颈 -> 受益环节、关键公司

Layer 2 - UZI（量化初筛）
: 22维指标 × 180条规则全量扫描 -> 3-5只候选

Layer 3 - Buffett（质量深审）
: 护城河+现金流+管理层+安全边际，强制反方报告 -> 1-2只优质标的

Layer 4 - TradingAgents（交叉验证）
: 基本面/情绪/新闻/技术/风控 5角度多Agent辩论 -> 买入/观望/放弃

Layer 5 - QuantDinger（策略回测）
: 编写策略->回测->模拟盘 -> 落地执行

## 使用方式

手动：直接说需求，系统自动走完整管线
自动：热点事件触发时自动执行

## 数据说明

- A股行情：复用现有 event_collector_v3 数据源
- 产业链图谱：复用 industry_graph.json
- 量化因子：复用 factor_system 的18因子输出
