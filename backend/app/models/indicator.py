# -*- coding: utf-8 -*-
"""Technical indicator models — Fibonacci retracement, daily K-channel & trade advice."""
from typing import Optional
from pydantic import BaseModel


class FibonacciRequest(BaseModel):
    """斐波那契回撤计算请求"""
    symbol: str
    high: Optional[float] = None   # 阶段顶部价格（不传则从K线自动获取）
    low: Optional[float] = None    # 阶段底部价格（不传则从K线自动获取）


class FibonacciLevel(BaseModel):
    """单个斐波那契回撤价位"""
    ratio: float       # 回撤比例 (0.382 / 0.618 / 0.786)
    price: float       # 对应价位
    label: str         # 中文标签


class FibonacciResponse(BaseModel):
    """斐波那契回撤计算响应"""
    symbol: str
    high: float                    # 使用的阶段顶部
    low: float                     # 使用的阶段底部
    diff: float                    # 高低差价
    current_price: float           # 当前价格
    levels: list[FibonacciLevel]   # 三个关键价位
    position_zone: str             # 当前价格所处区间
    zone_suggestion: str           # 区间建议


class DailyChannelRequest(BaseModel):
    """日内K值通道计算请求"""
    symbol: str
    avg_price: Optional[float] = None  # 分时均价（不传则从行情获取）


class DailyChannelResponse(BaseModel):
    """日内K值通道计算响应"""
    symbol: str
    avg_price: float               # 分时均价
    constant_k: float = 0.98848    # K常数
    top_line: float                # 日内压力线（均价/K）
    bottom_line: float             # 日内支撑线（均价×K）
    channel_width_pct: float       # 通道宽度百分比
    current_price: float           # 当前价格
    position: str                  # 当前价格在通道中的位置


# ── 操作建议（牛股计算器完整决策树）──

class TradeAdviceRequest(BaseModel):
    """操作建议请求（含持仓上下文字段）"""
    symbol: str
    cost: Optional[float] = None        # 成本价（有则为持仓模式）
    high: Optional[float] = None        # 阶段顶部（不传则自动提取）
    low: Optional[float] = None         # 阶段底部（不传则自动提取）
    avg_price: Optional[float] = None   # 分时均价（用于K值通道计算）
    buy_date: Optional[str] = None      # 建仓日期 YYYY-MM-DD


class TradeAdviceResponse(BaseModel):
    """操作建议响应"""
    symbol: str
    name: str = ""                     # 股票名称
    current_price: float               # 当前价格
    change_pct: float = 0              # 当日涨跌幅(%)
    mode: str                          # 模式：'holding' 持仓模式 / 'observing' 观察模式
    
    # 斐波那契回撤价位
    fib_382: float
    fib_618: float
    fib_786: float
    
    # K值通道
    k_channel_top: float = 0           # 日内压力线
    k_channel_bottom: float = 0        # 日内支撑线
    k_channel_width_pct: float = 0     # 通道宽度(%)
    
    # 持仓模式专属
    cost: Optional[float] = None       # 成本价
    hold_days: Optional[int] = None    # 已持仓交易日数
    days_since_high: Optional[int] = None  # 距上次创新高交易日数
    high_water_mark: Optional[float] = None  # 持仓期间最高价
    
    # 决策结果
    signal: str                        # 操作建议文本（如"破底止损""常规买点"等）
    signal_class: str                  # CSS 类：danger/warning/gold/blue/cyan/normal
    signal_details: list[str] = []     # 触发信号的详细原因列表
    risk_flags: list[str] = []         # 风险标记列表
