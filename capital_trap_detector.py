"""
capital_trap_detector.py — 资本做局 / 利益输送检测模块

六种模式检测：
1. 关联收购+亲属提前减持 (华峰化学款)
2. 蹭热点+大股东批量减持 (利通电子款)
3. 高送转+减持
4. 业绩洗澡+低价定增
5. 忽悠式重组
6. 大宗交易绕道减持

每个检测输出风险等级: LOW / MEDIUM / HIGH / CRITICAL
"""

import re, json, os
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════
# 规则：容易出问题的关键词/信号
# ═══════════════════════════════════════════

SUSPICIOUS_REORG_KEYWORDS = [
    "关联交易", "关联收购", "关联方", "控股股东", "实际控制人",
    "发行股份购买资产", "注入资产", "收购少数股权",
]

RELATED_PARTY_KEYWORDS = [
    "控股股东", "实际控制人", "实控人", "兄弟公司", "关联方",
    "尤小平", "尤金焕",  # 华峰系
]

INSIDER_SELL_KEYWORDS = [
    "减持", "套现", "大宗交易", "股权转让",
    "减持计划", "拟减持",
]

HOT_SECTORS_2026 = [
    "AI", "人工智能", "算力", "芯片", "半导体", "低空经济",
    "eVTOL", "机器人", "人形机器人", "固态电池", "钠离子",
    "HBM", "先进封装", "CPO", "光通信", "数据要素",
]

WEAK_FUNDAMENTAL_SIGNALS = [
    "不构成重大资产重组",  # 门槛低
    "不构成重组上市",      # 规避借壳审核
    "业绩补偿", "未承诺业绩",
    "亏损", "净资产为负", "资不抵债",
]

HIGH_SONGZHUAN_KEYWORDS = [
    "高送转", "转增股本", "10送", "10转增",
]

BULK_TRADE_KEYWORDS = [
    "大宗交易", "折价", "溢价",
]

# 高送转
HIGH_SONGZHUAN = [
    "10送", "10转增", "10转", "10派",
    "高送转", "转增股本", "公积金转增",
]

# 亏损/洗澡
BATH_KEYWORDS = [
    "亏损", "预亏", "业绩变脸", "商誉减值", "计提减值",
    "大幅亏损", "资不抵债", "净资产为负",
]

# === 卖家类型权重 ===
# 同样是大股东/关联人减持，身份不同风险等级完全不同
# 卖家身份关键词
SELLER_CONTROLLER = [  # 实控人/控股股东 - 最负面
    "实际控制人", "实控人", "控股股东", "创始人",
    "控股股东", "大股东",
]
SELLER_EXECUTIVE = [   # 高管/董事长 - 负面
    "董事长", "总经理", "高管", "董事", "监事",
    "副总经理", "财务总监", "董秘", "核心技术人员",
]
SELLER_PE = [           # 创投/财务投资 - 中性
    "创投", "投资企业", "基金", "资本",
    "风险投资", "venture", "PE",
]
SELLER_BIG_FUND = [     # 国家大基金/产业基金 - 中性偏正面
    "国家大基金", "大基金", "产业基金", "国资",
    "国投", "中金",
]
SELLER_EMPLOYEE = [     # 员工持股平台 - 轻度
    "员工", "持股平台", "股权激励",
]

# 定增
PRIVATE_PLACEMENT = [
    "定向增发", "非公开发行", "定增", "增发",
]


def _check_related_party(target_name: str, news_items: list) -> dict:
    """检测模式1：关联收购"""
    risk = {"level": "LOW", "signals": [], "details": ""}
    for item in news_items:
        title = item.get("title", "") + item.get("snippet", "")
        # 有关联交易关键词
        has_related = any(kw in title for kw in RELATED_PARTY_KEYWORDS)
        has_reorg = any(kw in title for kw in SUSPICIOUS_REORG_KEYWORDS)
        has_sell = any(kw in title for kw in INSIDER_SELL_KEYWORDS)
        
        if has_reorg and has_related:
            risk["level"] = "HIGH"
            risk["signals"].append("关联收购: 卖方为控股股东/实控人")
        if has_sell and has_related:
            if risk["level"] in ("LOW", "MEDIUM"):
                risk["level"] = "MEDIUM"
            risk["signals"].append("关联方减持: 实控人/亲属套现")
            
    if "不构成重大资产重组" in str(news_items):
        risk["signals"].append("审核回避: 声明不构成重大资产重组")
        if risk["level"] == "LOW":
            risk["level"] = "MEDIUM"
            
    if risk["signals"]:
        risk["details"] = "; ".join(risk["signals"])
    return risk


def _classify_seller(news_items: list) -> dict:
    """识别减持卖家身份，不同卖家风险等级不同"""
    all_text = " ".join(n.get("title", "") + n.get("snippet", "") for n in (news_items or []))
    
    # 按优先级判断：实控人 > 高管 > 创投 > 大基金 > 员工
    if any(kw in all_text for kw in SELLER_CONTROLLER):
        return {"type": "实控人/控股股东", "risk_weight": 5}
    if any(kw in all_text for kw in SELLER_EXECUTIVE):
        return {"type": "高管", "risk_weight": 4}
    if any(kw in all_text for kw in SELLER_PE):
        return {"type": "创投/财务投资", "risk_weight": 2}
    if any(kw in all_text for kw in SELLER_BIG_FUND):
        return {"type": "大基金/国资", "risk_weight": 1}
    if any(kw in all_text for kw in SELLER_EMPLOYEE):
        return {"type": "员工持股平台", "risk_weight": 2}
    return {"type": "未明确", "risk_weight": 3}


def _check_hot_concept_sell(target_name: str, news_items: list, price_data: dict) -> dict:
    """检测模式2：蹭热点+减持
    注意：同样减持，实控人卖 vs 大基金卖，性质完全不同
    """
    risk = {"level": "LOW", "signals": [], "details": ""}
    
    all_text = " ".join(n.get("title", "") for n in news_items)
    hot_matches = [s for s in HOT_SECTORS_2026 if s in all_text]
    
    if hot_matches:
        risk["signals"].append(f"涉及热点概念: {', '.join(hot_matches[:3])}")
        
        # 查是否有减持/套现/转让等计划
        sell_plans = [n for n in news_items if any(kw in (n.get("title","") + n.get("snippet","")) for kw in INSIDER_SELL_KEYWORDS)]
        if sell_plans:
            # 识别卖家身份
            seller_info = _classify_seller(sell_plans)
            seller_type = seller_info["type"]
            risk_weight = seller_info["risk_weight"]
            
            # 根据卖家身份确定风险等级
            if risk_weight >= 5:  # 实控人
                risk["level"] = "CRITICAL"
                risk["signals"].append(f"{seller_type}在减持: 实控人减持是最负面信号")
            elif risk_weight >= 4:  # 高管
                risk["level"] = "HIGH"
                risk["signals"].append(f"{seller_type}在减持: 高管集体减持偏负面")
            elif risk_weight >= 3:  # 未知
                risk["level"] = "HIGH"
                risk["signals"].append(f"{seller_type}在减持")
            else:  # 创投/大基金
                risk["level"] = "MEDIUM"
                risk["signals"].append(f"{seller_type}在减持: 基金到期退出，中性偏正常")
            
            risk["signals"].append(f"共{len(sell_plans)}条减持相关新闻")
            
            # 查是否股价在高位
            change_pct = price_data.get("change_pct", 0) if price_data else 0
            if change_pct > 0:
                risk["signals"].append(f"股价近期上涨{change_pct:+.1f}%，高位减持")
                
        if risk["level"] == "LOW":
            risk["level"] = "MEDIUM"
            risk["signals"].append("有热点概念但未发现减持")
            
    if risk["signals"]:
        risk["details"] = "; ".join(risk["signals"])
    return risk


def _check_reorg_credibility(target_name: str, news_items: list) -> dict:
    """检测模式5：忽悠式重组"""
    risk = {"level": "LOW", "signals": [], "details": ""}
    
    all_text = " ".join(n.get("title", "") + n.get("snippet", "") for n in news_items)
    
    # 终止重组的记录
    if "终止重组" in all_text or "终止收购" in all_text or "终止" in all_text and "重组" in all_text:
        risk["level"] = "HIGH"
        risk["signals"].append("历史上曾终止重组，有忽悠式重组嫌疑")
        
    # 跨界收购
    if "跨界" in all_text:
        risk["signals"].append("跨界收购，协同效应存疑")
        if risk["level"] == "LOW":
            risk["level"] = "MEDIUM"
            
    if "终止" in all_text and "重组" in all_text:
        risk["signals"].append("重组多次反复")
        if risk["level"] == "LOW":
            risk["level"] = "MEDIUM"
            
    if risk["signals"]:
        risk["details"] = "; ".join(risk["signals"])
    return risk


def _check_songzhuan_sell(target_name: str, news_items: list) -> dict:
    """检测模式3：高送转+减持"""
    risk = {"level": "LOW", "signals": [], "details": ""}
    all_text = " ".join(n.get("title", "") + n.get("snippet", "") for n in news_items)

    has_sz = any(kw in all_text for kw in HIGH_SONGZHUAN)
    has_sell = any(kw in all_text for kw in INSIDER_SELL_KEYWORDS)

    if has_sz and has_sell:
        risk["level"] = "HIGH"
        risk["signals"].append("高送转+减持并行: 利用送转拉高股价配合减持")
    elif has_sz:
        risk["level"] = "MEDIUM"
        risk["signals"].append("有高送转方案，需关注同步减持风险")

    if risk["signals"]:
        risk["details"] = "; ".join(risk["signals"])
    return risk


def _check_bath_placement(target_name: str, news_items: list, price_data: dict) -> dict:
    """检测模式4：业绩洗澡+低价定增"""
    risk = {"level": "LOW", "signals": [], "details": ""}
    all_text = " ".join(n.get("title", "") + n.get("snippet", "") for n in news_items)

    has_bath = any(kw in all_text for kw in BATH_KEYWORDS)
    has_placement = any(kw in all_text for kw in PRIVATE_PLACEMENT)

    if has_bath and has_placement:
        risk["level"] = "HIGH"
        risk["signals"].append("亏损+定增: 先洗大澡压股价再低价定增，典型利益输送")
        # 检查是否低价
        change_pct = price_data.get("change_pct", 0) if price_data else 0
        if change_pct < -20:
            risk["signals"].append("股价已大跌%.0f%%，定增价可能进一步压低" % abs(change_pct))
    elif has_bath:
        risk["level"] = "MEDIUM"
        risk["signals"].append("有大额亏损/减值，需警惕后续低价融资")
    elif has_placement:
        risk["level"] = "MEDIUM"
        risk["signals"].append("有定增计划，需关注定价是否公允")

    if risk["signals"]:
        risk["details"] = "; ".join(risk["signals"])
    return risk


def _check_bulk_trade(target_name: str, news_items: list) -> dict:
    """检测模式6：大宗交易绕道减持"""
    risk = {"level": "LOW", "signals": [], "details": ""}
    all_text = " ".join(n.get("title", "") + n.get("snippet", "") for n in news_items)

    has_bulk = any(kw in all_text for kw in BULK_TRADE_KEYWORDS)
    has_sell = any(kw in all_text for kw in INSIDER_SELL_KEYWORDS)

    if has_bulk and has_sell:
        risk["level"] = "HIGH"
        risk["signals"].append("大宗交易+减持: 可能绕道减持，折价大宗交易后机构抛售")
        # 检测折价
        if "折价" in all_text:
            risk["signals"].append("折价大宗交易，接盘方有短期抛售动机")
    elif has_bulk:
        risk["level"] = "MEDIUM"
        risk["signals"].append("有大宗交易记录，需关注折价率和接盘方")

    if risk["signals"]:
        risk["details"] = "; ".join(risk["signals"])
    return risk


def detect(target_name: str, stock_code: str = "",
           news_items: list = None,
           price_data: dict = None) -> dict:
    """
    全量检测：对一个标的跑所有资本做局模式检测

    Args:
        target_name: 股票名称/描述
        stock_code: 股票代码（可选）
        news_items: 近期相关新闻列表 [{"title":..., "snippet":...}]
        price_data: 行情数据 {"change_pct": ...}

    Returns:
        {"overall_risk": "LOW/MEDIUM/HIGH/CRITICAL",
         "checks": [{"pattern": "关联收购", "level": ..., "details": ...}],
         "summary": "一句话摘要"}
    """
    if news_items is None:
        news_items = []

    results = []

    # 模式1: 关联收购
    r1 = _check_related_party(target_name, news_items)
    if r1["signals"]:
        results.append({"pattern": "关联收购+利益输送", **r1})

    # 模式2: 热点+减持
    r2 = _check_hot_concept_sell(target_name, news_items, price_data or {})
    if r2["signals"]:
        results.append({"pattern": "蹭热点+减持", **r2})

    # 模式3: 高送转+减持
    r3 = _check_songzhuan_sell(target_name, news_items)
    if r3["signals"]:
        results.append({"pattern": "高送转+减持", **r3})

    # 模式4: 业绩洗澡+低价定增
    r4 = _check_bath_placement(target_name, news_items, price_data or {})
    if r4["signals"]:
        results.append({"pattern": "业绩洗澡+定增", **r4})

    # 模式5: 忽悠式重组
    r5 = _check_reorg_credibility(target_name, news_items)
    if r5["signals"]:
        results.append({"pattern": "忽悠式重组", **r5})

    # 模式6: 大宗交易绕道减持
    r6 = _check_bulk_trade(target_name, news_items)
    if r6["signals"]:
        results.append({"pattern": "大宗交易绕道减持", **r6})

    # 综合风险等级
    levels = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    max_level = "LOW"
    for r in results:
        if levels.get(r.get("level", "LOW"), 0) > levels.get(max_level, 0):
            max_level = r["level"]

    # 摘要
    high_signals = [r for r in results if r.get("level") in ("HIGH", "CRITICAL")]
    if high_signals:
        summary = "[!] 检测到%d个高风险信号: %s" % (
            len(high_signals),
            "; ".join(h["details"] for h in high_signals[:2])
        )
    elif results:
        summary = "中低风险: " + "; ".join(r.get("details","") for r in results[:2])
    else:
        summary = "未检测到明显做局信号"

    return {
        "overall_risk": max_level,
        "checks": results,
        "summary": summary,
        "detected_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
    }


def format_report(detection: dict) -> str:
    """格式化输出检测报告(无emoji,兼容GBK终端)"""
    risk_icon = {"LOW": "[OK]", "MEDIUM": "[!]", "HIGH": "[!!]", "CRITICAL": "[!!!]"}
    icon = risk_icon.get(detection.get("overall_risk", "LOW"), "[?]")
    
    lines = [
        "%s 资本做局检测: %s" % (icon, detection.get("overall_risk", "?")),
        detection.get("summary", ""),
    ]
    
    for c in detection.get("checks", []):
        c_icon = risk_icon.get(c.get("level", "LOW"), "[?]")
        lines.append("  %s %s: %s" % (c_icon, c["pattern"], c.get("details", "")))
    
    return "\n".join(lines)


if __name__ == "__main__":
    # 简单自测
    test_news = [
        {"title": "华峰化学重启60亿关联收购，实控人亲属已套现5.8亿",
         "snippet": "发行股份及支付现金购买华峰集团资产"},
        {"title": "华峰化学关于筹划重组的一般性风险提示公告",
         "snippet": "不构成重大资产重组"},
    ]
    test_price = {"change_pct": -1.55}
    result = detect("华峰化学", "002064", test_news, test_price)
    print(format_report(result))
    print("\n---\nJSON:", json.dumps(result, ensure_ascii=False, indent=2))
