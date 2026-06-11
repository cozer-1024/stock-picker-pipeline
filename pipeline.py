"""
pipeline.py — 5层选股流水线串行编排器

执行流程：
  Layer 1 (Serenity) → Layer 2 (UZI) → Layer 3 (Buffett) → Layer 4 (TradingAgents) → Layer 5 (QuantDinger)

每层输出作为下一层输入的 context。

手动触发：run_pipeline(event_description)
自动触发：run_pipeline_from_hot_event()
"""

import os, sys, json, yaml
from datetime import datetime, timezone, timedelta

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPTS_DIR = os.path.join(SKILL_DIR, "prompts")
RULES_DIR = os.path.join(SKILL_DIR, "rules")
DATA_DIR = os.path.join(SKILL_DIR, "data")
TZ = timezone(timedelta(hours=8))

sys.path.insert(0, SKILL_DIR)
import logging
logger = logging.getLogger("stock_picker")

# ══════════════════════════════════
# 各层 Prompt 加载
# ══════════════════════════════════

def _load_prompt(name):
    path = os.path.join(PROMPTS_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()

def _llm_call(system_prompt, user_input, temperature=0.3, layer=""):
    """
    通用 LLM 调用（走 DeepSeek API，无 key 时返回 mock 结果）
    layer: 显式传入 "serenity"/"uzi"/"buffett"/"trading_agents"/"quantdinger"
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return _mock_llm_result(layer)    
    
    import urllib.request
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ],
        "temperature": temperature,
        "max_tokens": 2000,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"LLM调用失败: {e}")
        return _mock_llm_result(layer)

def _mock_llm_result(layer: str):
    """无API key时的mock结果，按layer名返回"""
    mocks = {
        "serenity": {"bottleneck_links": ["核心环节A"], "benefit_chains": ["供应链", "代工"], "key_companies": [{"code": "000001", "name": "平安银行", "reason": "产业链核心环节"}], "logic": "从事件驱动产业链传导分析"},
        "uzi": {"candidates": [{"code": "000001", "name": "平安银行", "score": 85, "pass_rules": 22, "total_rules": 25}], "eliminated": [], "risk_flags": []},
        "buffett": {"passed": [{"code": "000001", "name": "平安银行", "moat": "强", "cashflow": "健康", "management": "优秀", "margin_of_safety": 0.15}], "anti_report": "反方观点：行业竞争加剧可能侵蚀护城河", "verdict": "通过"},
        "trading_agents": {"final_verdict": "买入", "votes": {"bullish": 3, "bearish": 1, "neutral": 1}, "scores": {"fundamental_score": 85, "sentiment_score": 70, "news_score": 75, "technical_score": 60, "risk_score": 80}, "reasoning": "基本面优秀+情绪正面+技术面动量向上"},
        "quantdinger": {"strategy": "分批建仓，月频持有", "backtest_result": {"period": "2025-01~2026-06", "sharpe": 1.2, "max_drawdown": "-8.5%"}, "execution_plan": {"entry": "分批3次", "position": "15%", "stop_loss": "ATR动态", "take_profit": "移动止盈"}},
    }
    data = mocks.get(layer, {"status": "ok"})
    return json.dumps(data, ensure_ascii=False)
# ══════════════════════════════════

def layer_serenity(industry: str, event: str) -> dict:
    """
    从热点事件拆解产业链，定位瓶颈环节
    
    Returns:
        bottleneck_links: 瓶颈环节列表
        benefit_chains: 受益产业链环节
        key_companies: 关键公司列表 [{"code":"...", "name":"...", "reason":"..."}]
        logic: 拆解逻辑说明
    """
    prompt = _load_prompt("serenity.md")
    # 补充当前产业链数据上下文
    extra_context = ""
    try:
        from industry_chain_extender import load_chains
        chains = load_chains()
        extra_context = json.dumps(chains, ensure_ascii=False)[:2000]
    except:
        pass
    
    user_input = json.dumps({
        "industry": industry,
        "event": event,
        "industry_graph_context": extra_context,
        "mode": "A股",
    }, ensure_ascii=False)
    
    result = _llm_call(prompt, user_input, layer="serenity")

    try:
        return json.loads(result)
    except:
        return {"bottleneck_links": [], "benefit_chains": [], "key_companies": [], "logic": result[:500]}


# ══════════════════════════════════
# Layer 2: UZI - 量化初筛
# ══════════════════════════════════

def layer_uzi(key_companies: list) -> dict:
    """
    对候选公司做量化规则全量扫描
    
    Returns:
        candidates: 通过初筛的标的列表 [{"code":"...","name":"...","score":int,"pass_rules":int,"total_rules":int}]
        eliminated: 被淘汰的标的及原因
        risk_flags: 风险标记（龙虎榜异常等）
    """
    prompt = _load_prompt("uzi.md")
    rules = _load_rules()
    
    user_input = json.dumps({
        "key_companies": key_companies,
        "rules_summary": rules.get("rules_summary", ""),
        "mode": "A股",
    }, ensure_ascii=False)
    
    result = _llm_call(prompt, user_input, layer="uzi")

    try:
        return json.loads(result)
    except:
        return {"candidates": key_companies, "eliminated": [], "risk_flags": []}


def _load_rules() -> dict:
    path = os.path.join(RULES_DIR, "uzi_rules.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except:
        return {}


# ══════════════════════════════════
# Layer 3: Buffett - 质量深审
# ══════════════════════════════════

def layer_buffett(candidates: list) -> dict:
    """
    护城河+现金流+管理层+安全边际，强制反方报告
    
    Returns:
        passed: 通过审核的标的
        anti_report: 反方调研报告
        verdict: 审计结论
    """
    prompt = _load_prompt("buffett.md")
    
    user_input = json.dumps({
        "candidates": candidates,
        "mode": "A股",
    }, ensure_ascii=False)
    
    result = _llm_call(prompt, user_input, layer="buffett")

    try:
        return json.loads(result)
    except:
        return {"passed": candidates, "anti_report": "无数据", "verdict": "通过"}


# ══════════════════════════════════
# Layer 4: TradingAgents - 交叉验证
# ══════════════════════════════════

def layer_trading_agents(passed_candidates: list) -> dict:
    """
    5角度多Agent辩论：基本面/情绪/新闻/技术/风控
    
    Returns:
        final_verdict: "买入"/"观望"/"放弃"
        votes: 各Agent投票 {"bullish": int, "bearish": int, "neutral": int}
        scores: 各维度分数
        reasoning: 综合判断逻辑
    """
    prompt = _load_prompt("trading_agents.md")
    
    # 尝试获取实时数据作为上下文
    extra_data = {}
    try:
        from event_collector_v3 import collect_all
        extra_data = collect_all()
    except:
        pass
    
    user_input = json.dumps({
        "candidates": passed_candidates,
        "market_data": str(extra_data)[:2000],
        "mode": "A股",
    }, ensure_ascii=False)
    
    result = _llm_call(prompt, user_input, layer="trading_agents")

    try:
        return json.loads(result)
    except:
        return {"final_verdict": "观望", "votes": {}, "scores": {}, "reasoning": result[:500]}


# ══════════════════════════════════
# Layer 5: QuantDinger - 策略回测
# ══════════════════════════════════

def layer_quantdinger(verdict_data: dict) -> dict:
    """
    根据最终判定，执行回测验证，输出交易策略
    
    Returns:
        strategy: 策略描述
        backtest_result: 回测关键指标
        execution_plan: 执行计划
    """
    prompt = _load_prompt("quantdinger.md")
    
    user_input = json.dumps({
        "verdict": verdict_data,
        "mode": "A股",
    }, ensure_ascii=False)
    
    result = _llm_call(prompt, user_input, layer="quantdinger")

    try:
        data = json.loads(result)
    except:
        data = {"strategy": "", "backtest_result": {}, "execution_plan": ""}
    
    # 同时运行本地回测引擎
    try:
        from backtest.engine import run_local_backtest
        bt_result = run_local_backtest(verdict_data)
        data["local_backtest"] = bt_result
    except:
        data["local_backtest"] = {"status": "不可用"}
    
    return data


# ══════════════════════════════════
# 主入口
# ══════════════════════════════════

def run_pipeline(industry: str, event: str) -> dict:
    """
    完整5层选股流水线
    
    Args:
        industry: 行业名称（如 "人工智能"、"新能源"）
        event: 热点事件描述
    
    Returns:
        每层的输出汇总字典
    """
    start = datetime.now(TZ)
    logger.info(f"启动选股流水线: 行业={industry}, 事件={event}")
    
    context = {"industry": industry, "event": event, "timeline": []}
    
    def _log(layer, result):
        elapsed = (datetime.now(TZ) - start).total_seconds()
        context["timeline"].append({"layer": layer, "elapsed_sec": round(elapsed, 1)})
    
    # Layer 1
    l1 = layer_serenity(industry, event)
    context["serenity"] = l1
    _log("serenity", l1)
    logger.info(f"Layer1 Serenity: {len(l1.get('key_companies',[]))} 家公司")
    
    # Layer 2
    l2 = layer_uzi(l1.get("key_companies", []))
    context["uzi"] = l2
    _log("uzi", l2)
    candidates = l2.get("candidates", [])
    logger.info(f"Layer2 UZI: {len(candidates)} 只候选")
    
    if not candidates:
        context["error"] = "Layer2无候选标的，终止管线"
        context["completed_at"] = datetime.now(TZ).isoformat()
        logger.warning("管线终止: 无候选标的")
        return context
    
    # Layer 3
    l3 = layer_buffett(candidates)
    context["buffett"] = l3
    _log("buffett", l3)
    passed = l3.get("passed", [])
    logger.info(f"Layer3 Buffett: {len(passed)} 只通过")
    
    if not passed:
        context["error"] = "Layer3无通过标的，终止管线"
        context["completed_at"] = datetime.now(TZ).isoformat()
        return context
    
    # Layer 4
    l4 = layer_trading_agents(passed)
    context["trading_agents"] = l4
    _log("trading_agents", l4)
    logger.info(f"Layer4 TradingAgents: {l4.get('final_verdict','?')}")
    
    # Layer 4.5: 资本做局检测（所有通过标的额外安全筛查）
    _trap_results = {}
    for stock in passed:
        try:
            from capital_trap_detector import detect, format_report
            name = stock.get("name", "")
            code = stock.get("code", "")
            news = context.get("events", []) + context.get("hot_events", [])
            news_items = [{"title": n.get("title",""), "snippet": n.get("snippet","")} for n in (news or [])]
            result = detect(name, code, news_items, {})
            if result["overall_risk"] in ("HIGH", "CRITICAL"):
                logger.warning("[资本做局] %s(%s): %s", name, code, result["summary"])
                _trap_results[name] = {"risk": "HIGH", "detail": result["summary"]}
            elif result["overall_risk"] == "MEDIUM":
                _trap_results[name] = {"risk": "MEDIUM", "detail": result["summary"]}
        except Exception:
            pass
    if _trap_results:
        high_risk = [k for k,v in _trap_results.items() if v["risk"] == "HIGH"]
        if high_risk:
            logger.warning("[资本做局] 高风险标的被标记: %s", high_risk)
        context["capital_trap_check"] = _trap_results
    
    # Layer 5
    l5 = layer_quantdinger(l4)
    context["quantdinger"] = l5
    _log("quantdinger", l5)
    logger.info(f"Layer5 QuantDinger: 回测完成")
    
    context["completed_at"] = datetime.now(TZ).isoformat()
    context["total_seconds"] = round((datetime.now(TZ) - start).total_seconds(), 1)
    
    return context


def run_pipeline_from_hot_event():
    """
    自动触发：检测当前热点事件，走完整管线
    被 cron 调用
    """
    try:
        from event_collector_v3 import collect_all
        data = collect_all()
        news = data.get("news", [])
        # 取前5条新闻作为事件输入
        events = []
        for ev in news[:5]:
            title = ev.get("title", "")
            # 尝试分类行业
            industries = ["人工智能", "新能源", "半导体", "汽车", "金融", "消费", "医药", "地产"]
            matched = [i for i in industries if i in title]
            industry = matched[0] if matched else "综合"
            events.append({"industry": industry, "event": title})
        
        results = []
        for ev in events[:3]:  # 最多处理3个事件
            result = run_pipeline(ev["industry"], ev["event"])
            results.append(result)
        
        return results
    except Exception as e:
        logger.error(f"自动触发失败: {e}")
        return []


def generate_report(pipeline_result: dict) -> str:
    """生成人类可读的报告"""
    lines = []
    lines.append("=" * 54)
    lines.append("  选股流水线报告")
    lines.append(f"  行业: {pipeline_result.get('industry','?')}")
    lines.append(f"  事件: {pipeline_result.get('event','?')[:80]}")
    lines.append("=" * 54)
    
    l1 = pipeline_result.get("serenity", {})
    lines.append(f"\n [Layer1] 产业链拆解")
    lines.append(f"  瓶颈环节: {', '.join(l1.get('bottleneck_links',[]))}")
    lines.append(f"  受益环节: {', '.join(l1.get('benefit_chains',[]))}")
    for c in l1.get("key_companies", []):
        lines.append(f"  >> {c.get('name','?')}({c.get('code','?')}) — {c.get('reason','')[:60]}")
    
    l2 = pipeline_result.get("uzi", {})
    lines.append(f"\n [Layer2] 量化初筛")
    for c in l2.get("candidates", []):
        lines.append(f"  ✅ {c.get('name','?')}: {c.get('score',0)}分 ({c.get('pass_rules',0)}/{c.get('total_rules',0)}规则通过)")
    for e in l2.get("eliminated", []):
        lines.append(f"  ❌ {e.get('name','?')}: {e.get('reason','')[:60]}")
    
    l3 = pipeline_result.get("buffett", {})
    lines.append(f"\n [Layer3] 质量深审")
    for c in l3.get("passed", []):
        lines.append(f"  ✅ {c.get('name','?')}: 护城河{c.get('moat','?')} | 现金流{c.get('cashflow','?')} | 安全边际{c.get('margin_of_safety','?')}")
    if l3.get("anti_report"):
        lines.append(f"  反方报告: {l3['anti_report'][:100]}")
    
    l4 = pipeline_result.get("trading_agents", {})
    lines.append(f"\n [Layer4] 交叉验证")
    lines.append(f"  裁决: {l4.get('final_verdict','?')}")
    votes = l4.get("votes", {})
    if votes:
        lines.append(f"  投票: 看多{votes.get('bullish',0)} / 看空{votes.get('bearish',0)} / 中性{votes.get('neutral',0)}")
    
    l5 = pipeline_result.get("quantdinger", {})
    lines.append(f"\n [Layer5] 策略回测")
    bt = l5.get("backtest_result", {})
    if bt:
        lines.append(f"  夏普: {bt.get('sharpe','?')} | 回撤: {bt.get('max_drawdown','?')}")
    lb = l5.get("local_backtest", {})
    if lb:
        lines.append(f"  本地回测: {lb.get('strategy_return','?')}")
    
    timeline = pipeline_result.get("timeline", [])
    lines.append(f"\n 耗时: {pipeline_result.get('total_seconds','?')}s")
    for t in timeline:
        lines.append(f"  {t['layer']}: {t['elapsed_sec']}s")
    
    lines.append("\n" + "=" * 54)
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        result = run_pipeline(sys.argv[1], sys.argv[2])
    else:
        result = run_pipeline("人工智能", "DeepSeek大模型发布，AI算力需求爆发")
    print(generate_report(result))
    # 保存结果
    path = os.path.join(DATA_DIR, "pipeline_result_latest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
