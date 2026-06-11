---
name: stock-picker-pipeline
description: >
  五层选股流水线。从热点事件到最终策略执行。
  触发：用户说"选股分析"、"产业链筛选"、"帮我看看[事件/行业]的标的"
  也由 cron 定时触发：热点事件变化时自动走完整管线
compatibility: >
  依赖：python3, yaml, json
  外部：event_collector_v3（可选复用）
---

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
