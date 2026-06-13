# -*- coding: utf-8 -*-
"""Market data models."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class IndexResponse(BaseModel):
    symbol: str
    name: str
    current_price: float
    last_close: float
    change: float
    change_pct: float
    volume: float
    high: float
    low: float
    open_price: float
    gap_pct: float
    updated_at: datetime


class IndicesResponse(BaseModel):
    indices: List[IndexResponse]
    updated_at: datetime


class QuoteResponse(BaseModel):
    symbol: str
    name: str
    current: float
    change: float
    percent: float
    last_close: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None
    turnover_rate: Optional[float] = None
    amplitude: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    market_capital: Optional[float] = None
    float_market_capital: Optional[float] = None
    avg_price: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    updated_at: datetime


class SectorResponse(BaseModel):
    name: str
    score: float
    change_pct: float
    lead_stock: Optional[str] = None
    net_buy_amount: Optional[float] = None  # 北向资金增持市值(亿元)
    holding_market_cap: Optional[float] = None  # 北向资金持股总市值(亿元)


class SectorsResponse(BaseModel):
    sectors: List[SectorResponse]
    sentiment: str
    updated_at: datetime


class GlobalIndexResponse(BaseModel):
    name: str
    symbol: str
    current: float
    change: float
    change_pct: float
    update_time: str


class CommodityResponse(BaseModel):
    name: str
    symbol: str
    current: float
    change: float
    change_pct: float
    update_time: str


class GlobalMarketResponse(BaseModel):
    us_indices: List[GlobalIndexResponse]
    commodities: List[CommodityResponse]
    updated_at: datetime


class KlineData(BaseModel):
    """单条日K线数据 (Tushare daily 接口)"""
    ts_code: str          # 股票代码（如 000001.SZ）
    trade_date: str       # 交易日期 YYYYMMDD
    open: float           # 开盘价
    high: float           # 最高价
    low: float            # 最低价
    close: float          # 收盘价
    pre_close: float      # 昨收价（除权价）
    change: float         # 涨跌额
    pct_chg: float        # 涨跌幅(%)
    vol: float            # 成交量（手）
    amount: float         # 成交额（千元）


class KlineResponse(BaseModel):
    """K线查询响应"""
    symbol: str
    klines: List[KlineData]
    count: int
    updated_at: datetime
    adj: str = "qfq"  # 复权方式: qfq=前复权, hfq=后复权, None=不复权


class MoneyflowData(BaseModel):
    """单日资金流向数据 (Tushare moneyflow 接口)"""
    ts_code: str            # 股票代码
    trade_date: str         # 交易日期 YYYYMMDD
    buy_sm_vol: int         # 小单买入量（手）
    buy_sm_amount: float    # 小单买入金额（万元）
    sell_sm_vol: int        # 小单卖出量（手）
    sell_sm_amount: float   # 小单卖出金额（万元）
    buy_md_vol: int         # 中单买入量（手）
    buy_md_amount: float    # 中单买入金额（万元）
    sell_md_vol: int        # 中单卖出量（手）
    sell_md_amount: float   # 中单卖出金额（万元）
    buy_lg_vol: int         # 大单买入量（手）
    buy_lg_amount: float    # 大单买入金额（万元）
    sell_lg_vol: int        # 大单卖出量（手）
    sell_lg_amount: float   # 大单卖出金额（万元）
    buy_elg_vol: int        # 特大单买入量（手）
    buy_elg_amount: float   # 特大单买入金额（万元）
    sell_elg_vol: int       # 特大单卖出量（手）
    sell_elg_amount: float  # 特大单卖出金额（万元）
    net_mf_vol: int         # 净流入量（手）
    net_mf_amount: float    # 净流入额（万元）


class MoneyflowResponse(BaseModel):
    """资金流向查询响应"""
    symbol: str
    flows: List[MoneyflowData]
    count: int
    updated_at: datetime


class TechnicalData(BaseModel):
    """单日技术面因子数据 (Tushare stk_factor_pro 接口)"""
    ts_code: str            # 股票代码
    trade_date: str          # 交易日期 YYYYMMDD
    close: float             # 收盘价
    # MACD (12,26,9)
    macd: float              # MACD 柱状图 (DIF-DEA)
    macd_dif: float          # DIF 线
    macd_dea: float          # DEA 线
    # KDJ (9,3,3)
    kdj: float               # KDJ 值（J）
    kdj_k: float             # K 值
    kdj_d: float             # D 值
    # RSI (6,12,24)
    rsi_6: float             # RSI6
    rsi_12: float             # RSI12
    rsi_24: float             # RSI24
    # BOLL (20,2)
    boll_upper: float        # 上轨
    boll_mid: float          # 中轨
    boll_lower: float        # 下轨
    # 辅助指标
    atr: float               # ATR 真实波动率
    cci: float               # CCI 顺势指标
    wr: float                # WR 威廉指标


class TechnicalResponse(BaseModel):
    """技术指标查询响应"""
    symbol: str
    data: List[TechnicalData]
    count: int
    updated_at: datetime


class ProBarData(BaseModel):
    """单条 pro_bar 行情数据"""
    ts_code: str          # 股票代码
    trade_date: str        # 交易日期 YYYYMMDD
    open: float            # 开盘价
    high: float            # 最高价
    low: float             # 最低价
    close: float           # 收盘价
    vol: float             # 成交量（手）
    amount: float          # 成交额（千元）
    adj: Optional[str]     # 复权类型: None/qfq/hfq


class ProBarResponse(BaseModel):
    """pro_bar 行情查询响应"""
    symbol: str
    bars: List[ProBarData]
    count: int
    updated_at: datetime
