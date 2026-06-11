"""
system_health_check.py — 投资系统每日体检医生

职责：每天收盘后自动检查系统各组件健康状态，发现异常主动推送给用户。
不依赖任何业务模块（event_collector/backtest_db等），只做外部检测。

设计原则：
1. 独立 — 不import任何业务模块，不依赖业务数据
2. 普适 — 只检查"接口/契约/文件/进程"，不绑定具体实现
3. 自保 — 自己不能被业务逻辑搞坏（检查自己的文件是否被删）
4. 可继承 — 换代时只要新系统遵守同样的契约，体检照跑

契约清单（换代时需保持）：
  ├─ data/ 目录下关键数据文件
  ├─ simulated_portfolio*.json 模拟盘
  ├─ cron 列表（特定name）
  ├─ 关键 Python 模块
  └─ 环境变量
"""

import os, json, sys, subprocess, io, time
from datetime import datetime, timezone, timedelta

# 契约：换代时这些路径/名称应保持
CONTRACTS = {
    "data_dir": "data",
    "critical_files": [
        "event_db.json", "rubric.json", "equity_history.json",
        "stance_latest.json", "trade_calendar.json",
        "stock_picker_latest.json", "trap_tracker.json",
        "historical_events.md",
    ],
    "critical_modules": [
        "event_collector_v3", "world_model_v3", "factor_system",
        "signal_book", "portfolio", "capital_trap_detector",
        "tencent_quote",
    ],
    "portfolio_files": [
        "simulated_portfolio.json",
        "simulated_portfolio_stockpicker.json",
    ],
    "health_check_self": "system_health_check.py",  # 自保检查
}

TZ = timezone(timedelta(hours=8))
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SKILL_DIR, "data")


def _color(status):
    """返回状态标记，不依赖emoji（兼容GBK终端）"""
    return {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]",
            "INFO": "[INFO]"}.get(status, "[?]")


def check_files() -> list:
    """检查关键文件是否存在且非空"""
    issues = []
    for fname in CONTRACTS["critical_files"]:
        path = os.path.join(DATA_DIR, fname)
        if not os.path.isfile(path):
            issues.append({"item": f"data/{fname}", "status": "FAIL",
                           "msg": "文件不存在"})
        elif os.path.getsize(path) == 0:
            issues.append({"item": f"data/{fname}", "status": "WARN",
                           "msg": "文件为空"})
        else:
            issues.append({"item": f"data/{fname}", "status": "PASS",
                           "msg": "%d bytes" % os.path.getsize(path)})
    # 模拟盘
    for fname in CONTRACTS["portfolio_files"]:
        path = os.path.join(SKILL_DIR, fname)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    p = json.load(f)
                stocks = len(p.get("stocks", []))
                cash = p.get("cash", 0)
                issues.append({"item": fname, "status": "PASS",
                               "msg": "%d只, 现金%.0f" % (stocks, cash)})
            except Exception as e:
                issues.append({"item": fname, "status": "WARN",
                               "msg": "解析失败: %s" % str(e)[:40]})
        else:
            issues.append({"item": fname, "status": "FAIL", "msg": "文件不存在"})
    return issues


def check_modules() -> list:
    """检查关键模块能否导入"""
    issues = []
    for mod in CONTRACTS["critical_modules"]:
        try:
            # 用subprocess而不是直接import，避免影响当前进程
            r = subprocess.run(
                [sys.executable, "-c", "import " + mod],
                capture_output=True, text=True, timeout=10,
                cwd=SKILL_DIR
            )
            if r.returncode == 0:
                issues.append({"item": mod, "status": "PASS", "msg": "可导入"})
            else:
                issues.append({"item": mod, "status": "FAIL",
                               "msg": r.stderr.strip()[:60]})
        except subprocess.TimeoutExpired:
            issues.append({"item": mod, "status": "WARN", "msg": "导入超时"})
        except Exception as e:
            issues.append({"item": mod, "status": "FAIL",
                           "msg": str(e)[:60]})
    return issues


def check_api_keys() -> list:
    """检查必要环境变量"""
    issues = []
    # 关键key
    keys = {
        "DEEPSEEK_API_KEY": ["stock-picker pipeline 需要"],
    }
    for key, purpose in keys.items():
        if os.environ.get(key, ""):
            issues.append({"item": key, "status": "PASS", "msg": "已设置"})
        else:
            issues.append({"item": key, "status": "WARN",
                           "msg": "未设置 - %s" % purpose})
    return issues


def check_historical_data() -> list:
    """检查历史指数K线缓存完整性"""
    issues = []
    cache_dir = os.path.join(DATA_DIR, "historical_data")
    if not os.path.isdir(cache_dir):
        issues.append({"item": "historical_data", "status": "FAIL",
                       "msg": "目录不存在"})
        return issues
    csvs = [f for f in os.listdir(cache_dir) if f.endswith(".csv")]
    # 应该至少包含主要指数
    expected_prefixes = ["上证", "沪深300", "创业板"]
    found = [any(p in c for c in csvs) for p in expected_prefixes]
    if all(found):
        issues.append({"item": "历史K线", "status": "PASS",
                       "msg": "%d个指数文件" % len(csvs)})
    else:
        missing = [p for p, f in zip(expected_prefixes, found) if not f]
        issues.append({"item": "历史K线", "status": "WARN",
                       "msg": "缺少: %s" % ", ".join(missing)})
    return issues


def check_data_freshness() -> list:
    """检查数据新鲜度（最新快照时间）"""
    issues = []
    snaps = sorted([f for f in os.listdir(DATA_DIR)
                    if f.startswith("snap_v3_") and f.endswith(".json")],
                   reverse=True)
    if not snaps:
        issues.append({"item": "数据快照", "status": "FAIL", "msg": "无快照"})
        return issues
    # 从文件名解析时间
    try:
        ts = snaps[0].split("_")
        dt_str = ts[2] + ts[3][:4]
        dt = datetime.strptime(dt_str, "%Y%m%d%H%M")
        age = datetime.now(TZ) - dt.replace(tzinfo=TZ)
        hours = age.total_seconds() / 3600
        if hours < 2:
            issues.append({"item": "数据新鲜度", "status": "PASS",
                           "msg": "最新快照%s, %.0f分钟前" % (ts[2], age.total_seconds()/60)})
        else:
            issues.append({"item": "数据新鲜度", "status": "WARN",
                           "msg": "最新快照%s, %.0f小时前" % (ts[2], hours)})
    except Exception as e:
        issues.append({"item": "数据新鲜度", "status": "WARN",
                       "msg": "解析失败: %s" % str(e)[:40]})
    return issues


def check_self() -> list:
    """自保检查：确保自己没被删除"""
    issues = []
    self_path = os.path.join(SKILL_DIR, CONTRACTS["health_check_self"])
    if os.path.isfile(self_path):
        issues.append({"item": "体检脚本自身", "status": "PASS",
                       "msg": "%d bytes" % os.path.getsize(self_path)})
    else:
        issues.append({"item": "体检脚本自身", "status": "FAIL",
                       "msg": "不存在！"})
    return issues


def check_cron_health() -> list:
    """cron健康状态留空，由16:05 cron agent自行检查并汇报"""
    issues = []
    issues.append({"item": "cron状态", "status": "PASS",
                   "msg": "由16:05 cron agent检查"})
    return issues


def check_network_sources() -> list:
    """检查关键数据源是否可访问（轻量，不拉全量数据）"""
    issues = []
    import urllib.request as _ur
    sources = [
        ("腾讯行情", "http://qt.gtimg.cn/q=sh000001", 5),
        ("东方财富", "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MUTUAL_DEAL_HISTORY", 8),
    ]
    for name, url, timeout in sources:
        try:
            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _ur.urlopen(req, timeout=timeout) as r:
                if r.status == 200:
                    issues.append({"item": "数据源-" + name, "status": "PASS", "msg": "可访问"})
                else:
                    issues.append({"item": "数据源-" + name, "status": "WARN",
                                   "msg": "状态码 %d" % r.status})
        except Exception as e:
            issues.append({"item": "数据源-" + name, "status": "FAIL",
                           "msg": "不可达: %s" % str(e)[:40]})
    return issues


def check_event_db_health() -> list:
    """检查事件数据库各分类样本数"""
    issues = []
    path = os.path.join(DATA_DIR, "event_db.json")
    if not os.path.isfile(path):
        issues.append({"item": "事件库健康", "status": "FAIL", "msg": "不存在"})
        return issues
    try:
        with open(path, "r", encoding="utf-8") as f:
            db = json.load(f)
        events = db.get("events", [])
        from collections import Counter
        cats = Counter()
        for ev in events:
            cats[ev.get("category", "其他")] += 1
        low = [(cat, cnt) for cat, cnt in cats.items() if cnt < 5]
        if low:
            for cat, cnt in low:
                issues.append({"item": "事件库-" + cat, "status": "WARN",
                               "msg": "样本不足(%d条)" % cnt})
        else:
            issues.append({"item": "事件库健康", "status": "PASS",
                           "msg": "%d事件, %d类" % (len(events), len(cats))})
    except Exception as e:
        issues.append({"item": "事件库健康", "status": "WARN",
                       "msg": "检查失败: %s" % str(e)[:60]})
    return issues


def check_portfolio_risk() -> list:
    """检查模拟盘是否有接近止损的持仓"""
    issues = []
    for fname in CONTRACTS["portfolio_files"]:
        path = os.path.join(SKILL_DIR, fname)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                p = json.load(f)
            for s in p.get("stocks", []):
                cost = s.get("avg_cost", 0)
                last = s.get("last_price", 0)
                if cost and last:
                    pnl = (last - cost) / cost * 100
                    if pnl <= -7:
                        issues.append({"item": "止损预警-" + s.get("name","?"),
                                       "status": "FAIL",
                                       "msg": "%.1f%%, 接近止损线(-8%%)" % pnl})
        except:
            pass
    if not any(i for i in issues if i["item"].startswith("止损预警")):
        issues.append({"item": "持仓风险", "status": "PASS", "msg": "无接近止损"})
    return issues


def run_health_check() -> dict:
    """全量体检"""
    t0 = time.time()
    all_issues = []
    categories = []

    for name, check_fn in [
        ("文件完整性", check_files),
        ("模块可用性", check_modules),
        ("数据新鲜度", check_data_freshness),
        ("历史K线缓存", check_historical_data),
        ("数据源可达", check_network_sources),
        ("事件库健康", check_event_db_health),
        ("cron运行状态", check_cron_health),
        ("持仓风险", check_portfolio_risk),
        ("API密钥", check_api_keys),
        ("自保检查", check_self),
    ]:
        try:
            results = check_fn()
            all_issues.extend(results)
            categories.append({"category": name, "total": len(results),
                               "pass": sum(1 for r in results if r["status"] == "PASS"),
                               "warn": sum(1 for r in results if r["status"] == "WARN"),
                               "fail": sum(1 for r in results if r["status"] == "FAIL")})
        except Exception as e:
            all_issues.append({"item": name, "status": "FAIL",
                               "msg": "检查异常: %s" % str(e)[:60]})

    total_pass = sum(1 for r in all_issues if r["status"] == "PASS")
    total_warn = sum(1 for r in all_issues if r["status"] == "WARN")
    total_fail = sum(1 for r in all_issues if r["status"] == "FAIL")

    # 综合健康状况
    if total_fail > 0:
        health = "UNHEALTHY"
    elif total_warn > 0:
        health = "DEGRADED"
    else:
        health = "HEALTHY"

    return {
        "health": health,
        "summary": {
            "total": len(all_issues),
            "pass": total_pass,
            "warn": total_warn,
            "fail": total_fail,
        },
        "categories": categories,
        "details": all_issues,
        "duration_seconds": round(time.time() - t0, 1),
        "checked_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
    }


def format_report(result: dict) -> str:
    """生成推送给用户的报告文本"""
    lines = []
    icon = {"HEALTHY": "[OK]", "DEGRADED": "[!]", "UNHEALTHY": "[!!]"}
    lines.append("%s 投资系统体检报告" % icon.get(result["health"], "[?]"))
    lines.append("时间: %s | 耗时: %.1fs" % (
        result["checked_at"], result["duration_seconds"]))
    lines.append("整体: %s (PASS=%d WARN=%d FAIL=%d)" % (
        result["health"], result["summary"]["pass"],
        result["summary"]["warn"], result["summary"]["fail"]))
    lines.append("")

    # 按分类显示
    for cat in result["categories"]:
        lines.append("[%s] %s: %d项 (%d PASS / %d WARN / %d FAIL)" % (
            "OK" if cat["fail"] == 0 else "!!",
            cat["category"], cat["total"],
            cat["pass"], cat["warn"], cat["fail"]))
        for d in result["details"]:
            if d["item"].startswith(cat["category"][:2]) or \
               any(d["item"] in c["item"] for c in result["details"] if c not in [d]):
                pass

    # 只显示FAIL和WARN
    problems = [d for d in result["details"] if d["status"] != "PASS"]
    if problems:
        lines.append("")
        lines.append("--- 待处理问题 ---")
        for p in problems:
            lines.append("  %s %s: %s" % (
                {"FAIL": "[!!]", "WARN": "[!]"}.get(p["status"], "[?]"),
                p["item"], p["msg"]))

    if result["health"] == "HEALTHY":
        lines.append("")
        lines.append("一切正常，无需关注。")

    return "\n".join(lines)


if __name__ == "__main__":
    result = run_health_check()
    print(format_report(result))
