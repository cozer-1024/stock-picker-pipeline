"""
backtest/engine.py — 轻量回测引擎

提供基础的策略回测功能，基于现有的 backtest_engine.py 做简化包装。
支持K线数据加载 + 策略执行 + 绩效统计。
"""
import os, sys, json, csv
from datetime import datetime, timezone, timedelta

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(SKILL_DIR, "data")
TZ = timezone(timedelta(hours=8))


def load_price_data(code: str, days: int = 500) -> list:
    """
    从现有缓存加载个股日线数据
    目前返回mock数据，后续接入腾讯/东方财富日线接口
    """
    # TODO: 接入 event_collector_v3 的实时行情
    return []


def run_local_backtest(verdict_data: dict) -> dict:
    """
    本地回测
    
    根据 verdict_data 中的最终判定，执行简化版回测
    
    Returns:
        strategy_return: 策略收益
        benchmark_return: 基准收益
        sharpe: 夏普比率
        max_drawdown: 最大回撤
    """
    return {
        "status": "待接入实时行情后生效",
        "strategy_return": "N/A",
        "benchmark_return": "N/A",
        "sharpe": "N/A",
        "max_drawdown": "N/A",
    }
