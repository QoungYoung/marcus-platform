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


# ── 仓位计算 ──

class CalcPositionRequest(BaseModel):
    """仓位计算请求"""
    symbol: str                        # 股票代码
    signal_strength: str = "medium"    # 信号强度: low / medium / high
    chain_role: str = "mid"            # 产业链角色: upstream(上游) / mid(中游) / downstream(下游)
    tier: str = "probe"                # 加仓层级: probe(试探) / confirm(确认) / sprint(冲刺)
    stance: str = "yellow"             # 市场立场: green / yellow / red


class CalcPositionStopLoss(BaseModel):
    """止损信息"""
    volatility_tier: str               # 振幅档位: 低波 / 中波 / 高波
    dynamic_stop_pct: float            # 动态止损率(%)
    hard_stop_price: float             # 硬止损价
    max_loss_per_share: float          # 每股最大亏损
    total_max_loss: float              # 单笔最大亏损金额
    iron_rule2_t1_pct: float           # 铁律二触发线1: 浮盈≥x%→成本价
    iron_rule2_t1_5_pct: float         # 铁律二触发线1.5: 浮盈≥x%→成本价+y%(渐进保护)
    iron_rule2_t1_5_plus_pct: float    # 铁律二触发线1.5加价幅度(%)
    iron_rule2_t2_pct: float           # 铁律二触发线2: 浮盈≥x%→成本价+y%
    iron_rule2_t2_plus_pct: float      # 铁律二触发线2加价幅度(%)
    iron_rule2_t3_pct: float           # 铁律二触发线3: 浮盈≥x%→成本价+y%
    iron_rule2_t3_plus_pct: float      # 铁律二触发线3加价幅度(%)


class CalcPositionQuantity(BaseModel):
    """数量计算结果"""
    max_shares: int                    # 最大可买股数
    max_amount: float                  # 最大可买金额
    rec_shares: int                    # 建议买入股数
    rec_amount: float                  # 建议买入金额
    rec_pct: float                     # 建议买入占总资产%
    probe_shares: int                  # 试探仓股数
    probe_amount: float                # 试探仓金额
    probe_pct: float                   # 试探仓占总资产%


class CalcPositionValidation(BaseModel):
    """验证结果"""
    single_cap_ok: bool                # 单票上限验证
    single_cap_detail: str             # 单票上限详情
    total_position_ok: bool            # 总仓位验证
    total_position_detail: str         # 总仓位详情
    cash_reserve_ok: bool              # 现金底线验证
    cash_reserve_detail: str           # 现金底线详情
    max_loss_ok: bool                  # 单笔亏损验证
    max_loss_detail: str               # 单笔亏损详情
    pre_condition_ok: Optional[bool] = None  # 前仓条件验证
    pre_condition_detail: Optional[str] = None  # 前仓条件详情


class CalcPositionResponse(BaseModel):
    """仓位计算响应"""
    symbol: str
    name: str = ""
    # 账户概览
    total_asset: float                 # 总资产
    available_cash: float              # 可用资金
    position_value: float              # 持仓市值
    position_ratio: float              # 当前仓位(%)
    # 约束条件
    signal_strength: str
    single_stock_cap_pct: float        # 单票上限%
    chain_role: str
    role_cap_pct: float                # 环节上限%
    tier: str
    tier_condition: str               # 前仓条件描述
    stance: str
    total_cap_pct: float               # 总仓上限%
    amplitude: float                   # 近5日日均振幅(%)
    amplitude_tier: str                # 振幅档位
    index_pct: float                   # 大盘涨跌幅(%)
    # 波动率 & 趋势强度
    volatility_level: str = ""         # ATR波动率档位: 低波/中波/高波/极高/无数据
    atr_pct: float = 0.0               # ATR/价格 (%)
    volatility_coef: float = 1.0       # 波动率仓位系数
    adx: float = 0.0                   # ADX 趋势强度
    adx_coef: float = 1.0              # ADX 仓位系数
    # 价格
    current_price: float
    # 数量计算
    quantity: CalcPositionQuantity
    # 止损
    stop_loss: CalcPositionStopLoss
    # 验证
    validation: CalcPositionValidation
    # 警告
    warnings: list[str] = []
    all_pass: bool = False
    # 数据源标识（回测时填 "backtest_local"）
    data_source: Optional[str] = None
    trade_date: Optional[str] = None
    phase_time: Optional[str] = None


# ── 入场过滤检查 ──

class EntryCheckRequest(BaseModel):
    """入场过滤检查请求"""
    symbol: str                                        # 股票代码
    sector_net_inflow: Optional[float] = None           # 所属板块主力资金净流入（元），用于 MA5<MA20 时的备用检查
    volume_ratio: Optional[float] = None                # 量比，已知可传入，否则从行情计算


class LayerResult(BaseModel):
    """单层过滤结果"""
    passed: bool                        # 本层是否通过
    grade: str                          # ✅通过 / ⚠️降级 / 🚫排除
    details: list[str] = []             # 逐项检查详情
    downgrade_reason: str = ""          # 降级原因（如有）
    downgrade_action: str = ""          # 降级动作（如 降仓50%、仅试探仓≤5%）


class EntryCheckTechDetail(BaseModel):
    """技术面过滤详情"""
    ma5: float = 0
    ma20: float = 0
    ma_status: str = ""                 # MA5>MA20 / MA5<MA20
    macd_status: str = ""               # 金叉 / 死叉 / 收敛中
    macd_dif_converging: bool = False   # DIF 连续2日收敛
    rsr: Optional[float] = None
    intraday_percentile: Optional[float] = None
    capital_efficiency: Optional[float] = None
    rsi6: float = 0
    kdj_j: float = 0
    current_price: float = 0
    avg_price: Optional[float] = None   # 分时均价


class EntryCheckCapitalDetail(BaseModel):
    """主力资金过滤详情"""
    today_main_net: float = 0           # 今日主力净流入（元）
    d5_main_net: float = 0              # 5日主力净流入（元）
    d10_main_net: float = 0             # 10日主力净流入（元）
    d5_gt_d10: bool = False             # 5日 > 10日（加速建仓）
    today_selling: bool = False         # 今日主力 < 0（出货）
    xs_net: float = 0                   # 小单净流入（元）
    xs_outflow: bool = False            # 小单净流出（加分）
    data_available: bool = True         # 数据是否可用


class EntryBuyConfirmation(BaseModel):
    """买入确认规则"""
    change_pct: float = 0               # 当日涨幅(%)
    action: str = ""                    # 直接入场 / 等3-5分钟横盘 / 等2-3分钟量比>1.5 / 放弃
    wait_minutes: int = 0               # 需等待分钟数
    volume_ratio: Optional[float] = None  # 量比
    volume_ratio_ok: Optional[bool] = None  # 量比是否达标


class EntryCheckResponse(BaseModel):
    """入场过滤检查响应"""
    symbol: str
    name: str = ""
    # 技术面数据
    tech: EntryCheckTechDetail
    # 三层过滤
    layer1_tech: LayerResult            # 第一层 — 技术面
    layer2_capital: LayerResult         # 第二层 — 主力行为
    layer3_overbought: LayerResult      # 第三层 — 超买过滤
    # 综合判定
    final_decision: str                 # ✅可建仓 / ⚠️仅试探仓 / 🚫禁止建仓
    final_grade: str                    # pass / probe_only / blocked
    max_position_pct: float = 0         # 最大建议仓位%(相对总资产)
    downgrade_multiplier: float = 1.0   # 降仓系数(1.0=全仓, 0.5=半仓, 0.0=禁止)
    # 硬拦截（不可被产业链信号豁免）
    hard_block: bool = False            # 代码层硬拦截标志
    hard_block_reasons: list[str] = []  # 硬拦截原因列表
    # 买入确认
    buy_confirmation: EntryBuyConfirmation
    # 汇总
    all_layers_pass: bool = False
    summary: str = ""
