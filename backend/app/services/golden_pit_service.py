# -*- coding: utf-8 -*-
"""黄金坑评分引擎 v2 — 按宽基指数分别追踪，三重确认底部区域。

模型来源: arkvol.com 作者「壬戍帅潘安」的三重判断体系:
  1. 蛋糕理论 (global capital flow) — A股资金外流达历史低位
  2. 宽基贪婪 (per-index greed) — 贪婪值 < 0.35 确认黄金坑, < 0.40 预警
  3. 细分板块 (sector fund greed) — 板块基金跌到极端值
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


from app.services.arkvol_service import ArkvolService, ArkvolServiceError

logger = logging.getLogger(__name__)


# ── 跟踪的 A 股宽基指数 ──
# data_source 说明:
#   "arkvol"   → 从 ArkVol alla 系列获取 greed + close（6 大宽基）
#   "pi_server"→ 从 Tushare fund_daily 获取 K 线，用价格分位作为 greed 代理
# ── 指数分级体系 (基于 ultimate 回测 2026-07-30) ──
# tier 说明:
#   core      → 必做: adjCAGR≥15%, Win≥70%, Stability≥0.70, 满仓权重最高
#   satellite → 选做: adjCAGR≥10%, 弹性大/历史短, 中等仓位配置
#   defense   → 可选: adjCAGR≥10%但稳定性差或收益偏低, 小仓位防御
#   drop      → 放弃: adjCAGR<8%或胜率近50%, 收益不如货币基金
#   watch     → 观察: 无回测数据, 仅作预警信号
# position_weight: 在组合中的建议仓位占比 (总和=1.0)
# ── 固定贪婪阈值说明 ──
# use_fixed_greed: True → 用 pit_greed/entry_greed 固定值判定状态 (推荐, 回测最优)
#                  False → 用滚动窗口 percentile (pit_pct/entry_pct) 判定
# pit_greed: 低于此值 → 黄金坑 (golden_pit), 来自 ultimate 回测最优固定阈值
# entry_greed: 低于此值 → 预警 (warning), pit_greed × ~1.15 作为提前预警线
# entry_offset: 跌破 pit_greed 后第 N 天为最优入场点 (回测: 0=当天最优)
# 数据来源: scripts/backtest_golden_pit_ultimate.py 全量回测 (2020-2026)
CHINA_INDICES: Dict[str, Dict[str, Any]] = {
    # ═══ 核心 (必做) — 高胜率+高收益+高稳定性 ═══
    # 科创50: adjCAGR +15.9%, Win 73%, 52 trades, Stability 0.74
    "588000": {"name": "科创50",   "priority": 4, "data_source": "arkvol",  "tier": "core",
               "signal_quality": "strong", "exp_15d": 5.5, "exp_20d": 8.5, "position_weight": 0.20,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 3,
               "pit_greed": 0.348, "entry_greed": 0.400, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 1.2, "pre_turn_cap": 0.20,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 80, "exit_half_pct": 40, "exit_fallback_days": 20,
               "buy_time": "14:44", "buy_time_pit": "09:37"},
    # 中证500: adjCAGR +16.2%, Win 72%, 29 trades, Stability 0.74 (satellite→core)
    "510500": {"name": "中证500",  "priority": 5, "data_source": "arkvol",  "tier": "core",
               "signal_quality": "strong", "exp_15d": 4.0, "exp_20d": 6.0, "position_weight": 0.20,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 3,
               "pit_greed": 0.345, "entry_greed": 0.395, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 1.2, "pre_turn_cap": 0.20,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 40, "exit_half_pct": 40, "exit_fallback_days": 20,
               "buy_time": "09:36"},
    # ═══ 卫星 (选做) — 高收益但波动大或历史短 ═══
    # 中证1000: adjCAGR +44.5%, Win 47%, 53 trades, Stability 0.52
    "159845": {"name": "中证1000", "priority": 3, "data_source": "arkvol",  "tier": "satellite",
               "signal_quality": "good",   "exp_15d": 3.5, "exp_20d": 5.0, "position_weight": 0.15,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 3,
               "pit_greed": 0.391, "entry_greed": 0.440, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 1.0, "pre_turn_cap": 0.12,
               "dca_strategy": "uniform_3", "dca_fallback": 15,
               "trend_factors": {"declining": 0.15, "full": 1.3},
               "exit_full_pct": 80, "exit_half_pct": 30, "exit_fallback_days": 20,
               "buy_time": "09:36", "buy_time_pit": "14:44"},
    # 创业板指: adjCAGR +22.6%, Win 58%, 43 trades, Stability 0.52
    "159915": {"name": "创业板指", "priority": 2, "data_source": "arkvol",  "tier": "satellite",
               "signal_quality": "good",   "exp_15d": 3.0, "exp_20d": 4.5, "position_weight": 0.15,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 3,
               "pit_greed": 0.328, "entry_greed": 0.380, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 1.0, "pre_turn_cap": 0.12,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 70, "exit_half_pct": 70, "exit_fallback_days": 20,
               "buy_time": "09:36"},
    # 道琼斯指数: adjCAGR +11.8%, Win 79%, 14 trades, Stability 0.91, 仅575天数据 (core→satellite)
    "513400": {"name": "道琼斯指数", "priority": 8, "data_source": "arkvol", "tier": "satellite",
               "signal_quality": "strong", "exp_15d": 2.5, "exp_20d": 3.5, "position_weight": 0.10,
               "use_fixed_greed": False, "entry_pct": 10, "pit_pct": 3,
               "pit_greed": 0.380, "entry_greed": 0.494, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 1.0, "pre_turn_cap": 0.15,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 99, "exit_half_pct": 99, "exit_fallback_days": 10,
               "buy_time": "09:36"},
    # ═══ 防御 (可选) — 稳定但收益偏低或参数敏感 ═══
    # 沪深300: adjCAGR +11.0%, Win 63%, 35 trades, Stability 0.63
    "510300": {"name": "沪深300",  "priority": 6, "data_source": "arkvol",  "tier": "defense",
               "signal_quality": "good",   "exp_15d": 1.5, "exp_20d": 2.5, "position_weight": 0.08,
               "use_fixed_greed": True, "entry_pct": 5, "pit_pct": 3,
               "pit_greed": 0.357, "entry_greed": 0.410, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 0.8, "pre_turn_cap": 0.12,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 40, "exit_half_pct": 40, "exit_fallback_days": 20,
               "buy_time": "09:36"},
    # 纳斯达克: adjCAGR +11.9%, Win 89%, 36 trades, Stability 0.30 ⚠️参数极度敏感 (core→defense)
    "159632": {"name": "纳斯达克", "priority": 10, "data_source": "arkvol", "tier": "defense",
               "signal_quality": "strong", "exp_15d": 2.0, "exp_20d": 3.0, "position_weight": 0.06,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 4,
               "pit_greed": 0.512, "entry_greed": 0.560, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 0.8, "pre_turn_cap": 0.08,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 99, "exit_half_pct": 99, "exit_fallback_days": 60,
               "buy_time": "09:37", "buy_time_pit": "14:15"},
    # 恒生指数: adjCAGR +10.5%, Win 67%, 30 trades, Stability 0.80
    "513600": {"name": "恒生指数", "priority": 9, "data_source": "arkvol", "tier": "defense",
               "signal_quality": "good", "exp_15d": 1.5, "exp_20d": 2.5, "position_weight": 0.06,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 5,
               "pit_greed": 0.368, "entry_greed": 0.420, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 0.8, "pre_turn_cap": 0.12,
               "dca_strategy": "uniform_3", "dca_fallback": 15,
               "trend_factors": {"declining": 0.10, "full": 1.3},
               "exit_full_pct": 60, "exit_half_pct": 30, "exit_fallback_days": 60,
               "buy_time": "09:36"},
    # ═══ 放弃 (回测确认: 年化过低) ═══
    # 上证50: adjCAGR +5.1%, Win 52%, 54 trades, Stability 0.97 — 收益不如货基
    "510050": {"name": "上证50",   "priority": 7, "data_source": "arkvol",  "tier": "drop",
               "signal_quality": "weak",   "exp_15d": 0.5, "exp_20d": 1.0, "position_weight": 0.0,
               "use_fixed_greed": True, "entry_pct": 5, "pit_pct": 3,
               "pit_greed": 0.403, "entry_greed": 0.450, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 0.0, "pre_turn_cap": 0.0,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 99, "exit_half_pct": 99, "exit_fallback_days": 10,
               "buy_time": "09:36"},
    # ═══ 卫星 (选做) — 海外指数 ═══
    # 韩国KOSPI: ArkVol 贪婪值来自 019455, ETF 交易代码 513310
    # adjCAGR +21%, Win 92%, 12 trades (pit=0.42), 仅660天数据 → satellite
    "513310": {"name": "韩国KOSPI", "priority": 11, "data_source": "arkvol", "tier": "satellite",
               "arkvol_code": "019455",
               "signal_quality": "good", "exp_15d": 8.0, "exp_20d": 12.0, "position_weight": 0.10,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 3,
               "pit_greed": 0.380, "entry_greed": 0.440, "entry_offset": 0,
               "turning_days": 0, "position_multiplier": 1.0, "pre_turn_cap": 0.08,
               "dca_strategy": "lump_entry", "dca_fallback": 15,
               "exit_full_pct": 60, "exit_half_pct": 99, "exit_fallback_days": 40,
               "buy_time": "09:36"},
    # ═══ 观察 (仅预警) ═══
    "562660": {"name": "中证2000", "priority": 1, "data_source": "arkvol", "tier": "watch",
               "signal_quality": "inferred", "exp_15d": None, "exp_20d": None, "position_weight": 0.0,
               "use_fixed_greed": False, "entry_pct": 10, "pit_pct": 5, "turning_days": 2,
               "position_multiplier": 0.0, "pre_turn_cap": 0.0,
               "exit_full_pct": 50, "exit_half_pct": 50, "exit_fallback_days": 60,
               "buy_time": "09:36"},
}

# 仓位分级: 拐点确认度 → 仓位比例 (单次定投占 max_total 的比例)
POSITION_TIERS = {
    "pre_turn":   0.03,   # 拐点前: 单次≤3%, 累计≤15%
    "turning":    0.50,   # 拐点确认 (连续2天回升): 50%
    "accelerate": 0.75,   # 加速 (连续3天回升): 75%
    "full":       1.00,   # 满仓 (连续4+天回升): 100%
}
PRE_TURN_CUMULATIVE_CAP = 0.15  # 拐点前累计上限

# ── DCA 趋势调节因子 (全局默认) ──
# 趋势状态 → 仓位乘数, 与 DCA 基准权重相乘
# 分指数可在 CHINA_INDICES 中通过 trend_factors 字段覆盖
DEFAULT_TREND_FACTORS: Dict[str, float] = {
    "declining":    0.10,   # 贪婪仍在下降 → 飞刀减速
    "bottoming":    0.50,   # 首次回升 1 天 → 初步试探
    "turning":      1.00,   # 连续回升 2 天 → 标准节奏
    "accelerating": 1.20,   # 连续回升 3 天 → 加快速度
    "full":         1.50,   # 连续回升 4+ 天 → 快速满仓
}

# 加速阈值保护: greed 回升到此比例以上时, 趋势因子上限=1.0 (防止追高)
TREND_ACCELERATION_CAP_RATIO = 1.0  # greed / entry_greed >= 1.0 时禁止加速

# 拐点检测: 连续 N 天贪婪值回升→拐点确认
TURNING_CONSECUTIVE_DAYS = 2   # 连续回升天数 → 拐点确认

# 假信号过滤参数
FAKE_SIGNAL_REBOUND_DAYS = 2   # N天内反弹回P10以上 → 假信号
SIGNAL_CLUSTER_DAYS = 5         # N天内多个信号 → 合并，取最低greed日

# 保留绝对阈值作为参考值（仅科创50/上证50曾触发）
GREED_ABSOLUTE_WARNING = 0.40
GREED_ABSOLUTE_PIT = 0.35

# 核心信号阈值: 用 expanding-window percentile (每个指数自己的历史分位)
PERCENTILE_GOLDEN_PIT = 5    # <= P5  → 黄金坑确认（信号最强）
PERCENTILE_WARNING = 10      # <= P10 → 预警（信号有效）

# 百分位计算窗口: 只取最近 N 天，避免 expanding-window 导致 Px 贪婪阈值漂移
PERCENTILE_WINDOW_DAYS = 500

PIT_WINDOW_DAYS = 15

SIGNAL_QUALITY_LABEL = {
    "strong": "信号强 (Win% >= 80%, Avg 15d > 5%)",
    "good":   "信号有效 (Win% >= 60%, Avg 15d > 3%)",
    "weak":   "信号弱 (不建议单独使用)",
}

STATUS_MAP = {
    "normal":     {"label": "正常",    "color": "#22c55e"},
    "warning":    {"label": "预警",    "color": "#f97316"},
    "golden_pit": {"label": "黄金坑",  "color": "#ef4444"},
}


def _trend_label(trend: str, factor: float) -> str:
    """生成通俗中文趋势标签。"""
    name_map = {
        "declining": "下跌中",
        "bottoming": "初步企稳",
        "turning": "拐点确认",
        "accelerating": "趋势加速",
        "full": "强势上涨",
    }
    name = name_map.get(trend, trend)
    if factor <= 0.15:
        return f"{name} · 仅投{int(factor * 100)}%试探"
    elif factor < 1.0:
        return f"{name} · 半速建仓({int(factor * 100)}%)"
    elif factor >= 1.5:
        return f"{name} · 全速建仓"
    elif factor > 1.0:
        return f"{name} · 加速建仓({int(factor * 100)}%)"
    return f"{name} · 标准节奏"


STRATEGY_LABELS: Dict[str, str] = {
    "uniform_3": "3日等权", "uniform_5": "5日等权", "uniform_7": "7日等权",
    "uniform_10": "10日等权", "uniform_15": "15日等权",
    "front_loaded": "前重后轻", "back_loaded": "前轻后重",
    "triangle": "三角加权", "lump_entry": "一次性建仓",
}


def _strategy_label(strategy: str) -> str:
    """DCA 策略代码 → 中文标签。"""
    return STRATEGY_LABELS.get(strategy, strategy)


def _compute_resonance(pit_count: int) -> float:
    """根据黄金坑指数数量计算共振乘数。"""
    if pit_count >= 4:
        return 1.3
    elif pit_count >= 3:
        return 1.2
    elif pit_count >= 2:
        return 1.0
    return 0.6


def _display_config() -> Dict[str, Any]:
    """返回前端展示所需的统一配置元数据。"""
    return {
        "status_colors": {
            "normal": STATUS_MAP["normal"]["color"],
            "warning": STATUS_MAP["warning"]["color"],
            "golden_pit": STATUS_MAP["golden_pit"]["color"],
        },
        "status_labels": {
            "normal": STATUS_MAP["normal"]["label"],
            "warning": STATUS_MAP["warning"]["label"],
            "golden_pit": STATUS_MAP["golden_pit"]["label"],
        },
        "strategy_labels": dict(STRATEGY_LABELS),
        "exit_labels": {
            "half_exit": "减持 50%",
            "full_exit": "清仓",
            "stop_profit": "止盈",
            "fallback_exit": "兜底退出",
        },
        "trend_icons": {
            "declining": "↓",
            "bottoming": "→",
            "recovering": "↑",
        },
        "trend_colors": {
            "declining": "#e5484d",
            "bottoming": "#c98a12",
            "recovering": "#27a06b",
        },
    }


def get_trend_factor(trend: str, days_rising: int, fund_code: str = "",
                     current_greed: float = 0.0, entry_greed: float = 999.0) -> float:
    """根据趋势状态返回仓位调节因子, 支持分指数覆盖。

    趋势状态映射 (全局默认):
      declining    (days_rising=0) → 0.10x
      bottoming    (days_rising=1) → 0.50x
      turning      (days_rising=2) → 1.00x
      accelerating (days_rising=3) → 1.20x
      full         (days_rising≥4) → 1.50x

    加速阈值保护: 当 current_greed >= entry_greed 时, 因子上限=1.0
    """
    # 确定趋势状态键
    if days_rising >= 4:
        state_key = "full"
    elif days_rising >= 3:
        state_key = "accelerating"
    elif days_rising >= 2:
        state_key = "turning"
    elif days_rising >= 1:
        state_key = "bottoming"
    else:
        state_key = "declining"

    # 读取分指数覆盖或全局默认
    if fund_code and fund_code in CHINA_INDICES:
        idx_trend_factors = CHINA_INDICES[fund_code].get("trend_factors", {})
        factor = idx_trend_factors.get(state_key, DEFAULT_TREND_FACTORS.get(state_key, 1.0))
    else:
        factor = DEFAULT_TREND_FACTORS.get(state_key, 1.0)

    # 加速阈值保护: greed 已回到 entry_greed 以上 → 禁止加速
    if current_greed > 0 and entry_greed is not None and entry_greed > 0 and current_greed >= entry_greed:
        factor = min(factor, 1.0)

    return factor


def _trading_days_between(start_date: str, end_date: str) -> int:
    """估算两个日期之间的交易日数（简化为自然日 * 5/7）。"""
    try:
        d1 = datetime.strptime(start_date, "%Y-%m-%d")
        d2 = datetime.strptime(end_date, "%Y-%m-%d")
        days = (d2 - d1).days
        # 粗略估算交易日：自然日 * 5/7
        return max(0, round(days * 5 / 7))
    except (ValueError, TypeError):
        return 0


def _add_trading_days(date_str: str, trading_days: int) -> str:
    """给定起始日期和交易日数，估算目标日期。"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        cal_days = round(trading_days * 7 / 5)
        result = d + timedelta(days=cal_days)
        return result.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str


def _describe_entry_strategy(cfg: Dict[str, Any]) -> str:
    """生成入场策略的人类可读描述。"""
    # DCA 分配方式
    dca = cfg.get("dca_strategy", "lump_entry")
    dca_label = {
        "lump_entry": "一次性打入",
        "uniform_3": "前3天分批",
        "uniform_5": "前5天分批",
        "uniform_7": "前7天分批",
        "uniform_10": "前10天分批",
        "uniform_15": "前15天分批",
        "front_loaded": "递减加权",
        "back_loaded": "递增加权",
        "triangle": "三角加权",
    }.get(dca, dca)

    if cfg.get("use_fixed_greed"):
        pit = cfg.get("pit_greed")
        entry = cfg.get("entry_greed")
        offset = cfg.get("entry_offset", 0)
        desc = f"固定阈值 greed≤{pit}"
        if entry is not None and entry != pit:
            desc += f" (预警≤{entry})"
        if offset:
            desc += f" 滞后{offset}天入场"
        else:
            desc += " 当天入场"
        desc += f" · {dca_label}"
        return desc
    else:
        pit_pct = cfg.get("pit_pct", 5)
        entry_pct = cfg.get("entry_pct", 10)
        return f"滚动百分位 P{pit_pct}入坑 P{entry_pct}预警 · {dca_label}"


def _describe_exit_strategy(cfg: Dict[str, Any]) -> str:
    """生成出场策略的人类可读描述。"""
    exit_full = cfg.get("exit_full_pct", 50)
    exit_half = cfg.get("exit_half_pct", 50)
    fallback = cfg.get("exit_fallback_days", 60)

    if exit_full == 99 and exit_half == 99:
        return f"固定持有 {fallback}天"
    elif exit_full == exit_half:
        return f"全仓止盈 P{exit_full} 兜底{fallback}天"
    else:
        return f"分批止盈 P{exit_half}/P{exit_full} 兜底{fallback}天"


class GoldenPitService:
    """黄金坑评分服务 v2 — 逐宽基指数追踪。"""

    def __init__(self, arkvol: Optional[ArkvolService] = None):
        self._arkvol = arkvol or ArkvolService()
        self._last_known_greed: Dict[str, float] = {}  # 用于盘中阈值穿越检测
        self._cache: Dict[str, tuple] = {}  # page_id → (data, timestamp)

    # ═══════════════════════════════════════════════════════════════
    # Data fetching helpers
    # ═══════════════════════════════════════════════════════════════

    def _cached_fetch(self, page_id: str, ttl: int = 7200) -> Dict[str, Any]:
        """带 TTL 缓存的 ArkVol API 调用。数据每日更新一次，默认缓存 2 小时。"""
        now = time.time()
        if page_id in self._cache:
            data, ts = self._cache[page_id]
            if now - ts < ttl:
                return data
        data = self._arkvol.fetch_page(page_id)
        self._cache[page_id] = (data, now)
        return data

    def _cached_ai_summary(self, ttl: int = 7200) -> Dict[str, Any]:
        """带 TTL 缓存的 ai-summary 调用。"""
        cache_key = "ai-summary"
        now = time.time()
        if cache_key in self._cache:
            data, ts = self._cache[cache_key]
            if now - ts < ttl:
                return data
        data = self._arkvol.fetch_ai_summary()
        self._cache[cache_key] = (data, now)
        return data

    @staticmethod
    def _fetch_pi_server_kline(etf_code: str, limit: int = 250) -> List[Dict]:
        """通过 Tushare 获取 ETF 日K线，统一为 {date, close} 格式。"""
        from datetime import datetime as dt, timedelta
        from app.core.trading._api_config import get_tushare_pro

        try:
            pro = get_tushare_pro()
            if pro is None:
                logger.warning("Tushare pro 不可用，无法获取 %s K线", etf_code)
                return []

            # 符号标准化：SH562660 → 562660.SH
            s = etf_code.strip().upper()
            if s.startswith("SH"):
                ts_code = f"{s[2:]}.SH"
            elif s.startswith("SZ"):
                ts_code = f"{s[2:]}.SZ"
            elif s.startswith("159") or s.startswith("16"):
                ts_code = f"{s}.SZ"
            else:
                ts_code = f"{s}.SH"

            end_date = dt.now().strftime("%Y%m%d")
            start_date = (dt.now() - timedelta(days=limit * 2)).strftime("%Y%m%d")

            df = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)

            if df is None or df.empty:
                ts_code_sz = f"{s[2:]}.SZ" if s.startswith("SH") else f"{s[2:]}.SH"
                if ts_code_sz != ts_code:
                    df = pro.fund_daily(ts_code=ts_code_sz, start_date=start_date, end_date=end_date)

            if df is None or df.empty:
                return []

            df = df.sort_values("trade_date", ascending=True)
            normalized = []
            for _, row in df.iterrows():
                ts = str(row["trade_date"])
                normalized.append({
                    "date": f"{ts[:4]}-{ts[4:6]}-{ts[6:]}",
                    "close": float(row["close"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                })

            return normalized[-limit:] if len(normalized) > limit else normalized
        except Exception as e:
            logger.warning("Tushare ETF kline 获取失败 (%s): %s", etf_code, e)
            return []

    @staticmethod
    def _calculate_price_percentile(current_price: float, closes: List[float]) -> float:
        """计算当前价格在滚动窗口中的分位数 (0-100)。

        分位越低表示价格越接近区间低点（越恐慌）。
        """
        if not closes or len(closes) < 5:
            return 50.0
        count_below = sum(1 for c in closes if c < current_price)
        return round(count_below / len(closes) * 100, 1)

    @staticmethod
    def _price_based_greed(current_price: float, closes: List[float]) -> float:
        """从价格位置合成 greed 代理值 (0-1 尺度)。

        0 = 处于滚动窗口最低价（极端恐慌/黄金坑）
        1 = 处于滚动窗口最高价（极端贪婪）
        """
        if not closes or len(closes) < 5:
            return 0.50
        min_p = min(closes)
        max_p = max(closes)
        if max_p <= min_p:
            return 0.50
        return round((current_price - min_p) / (max_p - min_p), 4)

    @staticmethod
    def _price_decline_rate(closes: List[float], window: int = 5) -> float:
        """从价格计算 N 日平均跌幅（正值=下跌）。"""
        if len(closes) < window + 1:
            return 0.0
        recent = closes[-window - 1:]
        if len(recent) < 2:
            return 0.0
        total_decline = recent[0] - recent[-1]
        return round(total_decline / recent[0] / window, 4) if recent[0] != 0 else 0.0

    # ═══════════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════════

    def get_status(self) -> Dict[str, Any]:
        """获取完整的 per-index 黄金坑状态 + 窗口信息 + 三重确认 + 预测。

        优先从 DB 快照读取（每日 15:30 定时落库），无数据时回退 ArkVol API。
        """
        db_result = self._get_status_from_db()
        if db_result is not None:
            return db_result
        return self._get_status_from_api()

    def _get_status_from_api(self) -> Dict[str, Any]:
        """从 ArkVol API 获取完整状态。使用 ai-summary (POST, 轻量) 替代 alla (GET, 重型)。"""
        with ThreadPoolExecutor(max_workers=3) as executor:
            f_ai = executor.submit(self._cached_ai_summary)
            f_gcf = executor.submit(self._cached_fetch, "global-capital-flow")
            f_tech = executor.submit(self._cached_fetch, "alla-tech")
            ai_data = f_ai.result()
            gcf_data = f_gcf.result()
            tech_data = f_tech.result()
            global_macro = self._parse_global_macro_overlay(gcf_data)

        as_of = ai_data.get("asof", "")

        # 从 ai-summary snapshot 提取指数数据（替代 _extract_arkvol_indices）
        arkvol_indices = self._extract_from_ai_summary(ai_data)

        # Pi Server 指数并行
        pi_server_indices: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=1) as executor:
            f_pi = executor.submit(self._extract_pi_server_indices, as_of)
            pi_server_indices = f_pi.result()

        all_indices = arkvol_indices + pi_server_indices
        all_indices.sort(key=lambda x: x["priority"])

        # ── 全球宏观后处理 ──
        self._apply_global_macro_to_indices(all_indices, global_macro)

        # 三重确认 + 预测
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_conf = executor.submit(self._compute_triple_confirmation, all_indices, gcf_data, tech_data)
            f_pred = executor.submit(self._predict_next_entry, all_indices)
            confirmation = f_conf.result()
            prediction = f_pred.result()

        window = self._detect_golden_pit_window(all_indices)

        # 优先用 AI 摘要结论，拼接本地分析
        ai_conclusion = ai_data.get("conclusion", "")
        local_summary = self._build_v2_summary(all_indices, window, confirmation, prediction)
        summary = ai_conclusion + "\n\n——\n" + local_summary if ai_conclusion else local_summary

        return {
            "as_of": as_of,
            "golden_pit_window": window,
            "indices": all_indices,
            "triple_confirmation": confirmation,
            "prediction": prediction,
            "summary": summary,
            "global_macro": global_macro,
        }

    @staticmethod
    def _arkvol_code_map() -> Dict[str, str]:
        """构建 ArkVol fund_code → CHINA_INDICES key 的映射。

        支持 arkvol_code 字段: 当配置中 data_source="arkvol" 且指定了 arkvol_code 时，
        ArkVol API 返回的 fund_code 与 CHINA_INDICES 的 key 不同，需要映射。
        例如: 513310 (ETF) → ArkVol 019455 (韩国指数)
        """
        mapping = {}
        for key, cfg in CHINA_INDICES.items():
            if cfg.get("data_source") == "arkvol":
                arkvol_key = cfg.get("arkvol_code", key)
                mapping[arkvol_key] = key
        return mapping

    def _extract_from_ai_summary(self, ai_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 ai-summary 返回的 snapshot 数组重建指数状态。"""
        arkvol_map = self._arkvol_code_map()  # ArkVol fund_code → config key
        snapshot_list = ai_data.get("snapshot", [])
        as_of = ai_data.get("asof", "")

        result = []
        seen_codes = set()
        for snap in snapshot_list:
            snap_code = str(snap.get("fund_code", ""))
            seen_codes.add(snap_code)
            if snap_code not in arkvol_map:
                continue
            config_key = arkvol_map[snap_code]
            cfg = CHINA_INDICES[config_key]

            history = snap.get("history", [])
            sorted_series = sorted(history, key=lambda x: x.get("date", ""))
            current_greed = float(sorted_series[-1].get("greed", 0)) if sorted_series else 0.0

            percentile = self._calculate_percentile(current_greed, sorted_series)
            # 用 ArkVol 的 change_5 替代本地计算的 decline_rate
            change_5 = snap.get("change_5", 0) or 0
            change_20 = snap.get("change_20", 0) or 0
            decline_rate = round(-change_5, 4)

            status = self._determine_status(cfg, current_greed, percentile)

            absolute_triggered = current_greed < GREED_ABSOLUTE_PIT

            index_info = self._build_index_info(
                code=config_key, cfg=cfg, value=current_greed, close=0,
                percentile=percentile, decline_rate=decline_rate,
                status=status, absolute_triggered=absolute_triggered,
                data_source="arkvol", sorted_series=sorted_series,
                as_of=as_of,
            )
            index_info["change_5"] = round(change_5, 4)
            index_info["change_20"] = round(change_20, 4)
            result.append(index_info)

        missing = set(arkvol_map.keys()) - seen_codes
        if missing:
            logger.warning("ai-summary 未返回以下基金代码 (已配置但缺失): %s", missing)

        return result

    def get_history(self, index: str = "all", days: int = 60) -> Dict[str, Any]:
        """获取历史贪婪值趋势数据，用于前端折线图。优先使用 ai-summary。"""
        ai_data = self._cached_ai_summary()
        as_of = ai_data.get("asof", datetime.now().strftime("%Y-%m-%d"))
        snapshot_list = ai_data.get("snapshot", [])

        result_series: Dict[str, List[Dict]] = {}
        result_indices: Dict[str, str] = {}

        snap_map = {s.get("fund_code", ""): s for s in snapshot_list}

        for code, cfg in CHINA_INDICES.items():
            if index != "all" and code != index:
                continue

            ds = cfg.get("data_source", "arkvol")
            if ds == "arkvol":
                arkvol_lookup = cfg.get("arkvol_code", code)
                snap = snap_map.get(arkvol_lookup)
                if snap:
                    history = snap.get("history", [])
                    sorted_data = sorted(history, key=lambda x: x.get("date", ""))
                    result_series[code] = sorted_data[-days:] if len(sorted_data) > days else sorted_data
                    result_indices[code] = cfg["name"]
            elif ds == "pi_server":
                etf_code = cfg.get("etf_code", "")
                if etf_code:
                    bars = self._fetch_pi_server_kline(etf_code, limit=days + 30)
                    if bars:
                        closes_120 = [float(b.get("close", 0)) for b in bars[-120:]] if len(bars) >= 120 else [float(b.get("close", 0)) for b in bars]
                        series = [
                            {
                                "date": b.get("date", ""),
                                "greed": self._price_based_greed(float(b.get("close", 0)), closes_120),
                                "close": float(b.get("close", 0)),
                            }
                            for b in bars
                        ]
                        result_series[code] = series[-days:] if len(series) > days else series
                        result_indices[code] = cfg["name"]

        return {
            "as_of": as_of,
            "series": result_series,
            "indices": result_indices,
        }

    def _reconstruct_series_from_db(self, db, fund_code: str, days: int = 120) -> List[Dict]:
        """从 DB 快照重建 sorted_series，格式兼容 _build_index_info。"""
        from app.models.golden_pit import GoldenPitSnapshot

        rows = (
            db.query(GoldenPitSnapshot)
            .filter(GoldenPitSnapshot.fund_code == fund_code)
            .order_by(GoldenPitSnapshot.date.desc())
            .limit(days)
            .all()
        )
        rows = list(reversed(rows))
        return [
            {"date": r.date, "greed": r.greed_value, "close": r.close_price or 0}
            for r in rows
        ]

    def _get_status_from_db(self) -> Optional[Dict[str, Any]]:
        """尝试从 DB 快照重建完整状态。最新快照不存在或历史不足 60 天时返回 None。"""
        try:
            from app.database import SessionLocal
            from app.models.golden_pit import GoldenPitSnapshot

            db = SessionLocal()
            try:
                # 查询 DB 中最新的快照日期（而非 today），避免与 save_daily_snapshot 的 as_of 日期不匹配
                latest_date_row = (
                    db.query(GoldenPitSnapshot.date)
                    .order_by(GoldenPitSnapshot.date.desc())
                    .first()
                )
                if not latest_date_row:
                    return None
                latest_date = latest_date_row[0]

                today_snaps = (
                    db.query(GoldenPitSnapshot)
                    .filter(GoldenPitSnapshot.date == latest_date)
                    .all()
                )
                if not today_snaps:
                    return None

                snap_map = {s.fund_code: s for s in today_snaps}

                indices = []
                for code, cfg in CHINA_INDICES.items():
                    snap = snap_map.get(code)
                    if not snap:
                        continue

                    sorted_series = self._reconstruct_series_from_db(db, code)
                    if len(sorted_series) < 60:
                        logger.info("DB 快照历史不足 (%s: %d天)，回退 API", code, len(sorted_series))
                        return None

                    index_info = self._build_index_info(
                        code=code, cfg=cfg,
                        value=snap.greed_value,
                        close=snap.close_price or 0,
                        percentile=snap.percentile or 50.0,
                        decline_rate=snap.decline_rate_5d or 0.0,
                        status=snap.status,
                        absolute_triggered=(snap.greed_value or 0) < GREED_ABSOLUTE_PIT,
                        data_source=cfg.get("data_source", "arkvol"),
                        sorted_series=sorted_series,
                        as_of=latest_date,
                    )
                    index_info["change_5"] = snap.change_5
                    index_info["change_20"] = snap.change_20
                    indices.append(index_info)
            finally:
                db.close()

            if not indices:
                return None

            indices.sort(key=lambda x: x["priority"])

            with ThreadPoolExecutor(max_workers=2) as executor:
                f_gcf = executor.submit(self._cached_fetch, "global-capital-flow")
                f_tech = executor.submit(self._cached_fetch, "alla-tech")
                gcf_data = f_gcf.result()
                tech_data = f_tech.result()
                global_macro = self._parse_global_macro_overlay(gcf_data)

            # ── 全球宏观后处理 ──
            self._apply_global_macro_to_indices(indices, global_macro)

            confirmation = self._compute_triple_confirmation(indices, gcf_data, tech_data)
            prediction = self._predict_next_entry(indices)
            window = self._detect_golden_pit_window(indices)
            summary = self._build_v2_summary(indices, window, confirmation, prediction)

            return {
                "as_of": latest_date,
                "golden_pit_window": window,
                "indices": indices,
                "triple_confirmation": confirmation,
                "prediction": prediction,
                "summary": summary,
                "global_macro": global_macro,
                "_source": "db",
            }
        except Exception as e:
            logger.warning("从 DB 重建黄金坑状态失败，回退 API: %s", e)
            return None

    def get_snapshots(self, days: int = 30) -> List[Dict[str, Any]]:
        """从数据库读取历史快照。"""
        try:
            from app.database import SessionLocal
            from app.models.golden_pit import GoldenPitSnapshot

            db = SessionLocal()
            try:
                cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                rows = (
                    db.query(GoldenPitSnapshot)
                    .filter(GoldenPitSnapshot.date >= cutoff)
                    .order_by(GoldenPitSnapshot.date.asc(), GoldenPitSnapshot.fund_code.asc())
                    .all()
                )
                return [
                    {
                        "date": r.date,
                        "fund_code": r.fund_code,
                        "index_name": r.index_name,
                        "greed_value": r.greed_value,
                        "close_price": r.close_price,
                        "percentile": r.percentile,
                        "status": r.status,
                        "decline_rate_5d": r.decline_rate_5d,
                    }
                    for r in rows
                ]
            finally:
                db.close()
        except Exception as e:
            logger.warning("读取黄金坑快照失败: %s", e)
            return []

    def save_daily_snapshot(self) -> List[Any]:
        """保存每日快照到数据库。"""
        try:
            from app.database import SessionLocal
            from app.models.golden_pit import GoldenPitSnapshot

            # 清除缓存，确保盘前同步拿到最新 API 数据
            self._cache.pop("ai-summary", None)
            self._cache.pop("global-capital-flow", None)
            self._cache.pop("alla-tech", None)

            status = self._get_status_from_api()
            # as_of 反映数据的实际交易日，datetime.now() 仅作兜底
            today = status["as_of"] or datetime.now().strftime("%Y-%m-%d")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            db = SessionLocal()
            snapshots = []
            try:
                # 删除当天已有记录，避免重复（幂等）
                db.query(GoldenPitSnapshot).filter(
                    GoldenPitSnapshot.date == today
                ).delete()

                for idx in status["indices"]:
                    snap = GoldenPitSnapshot(
                        date=today,
                        fund_code=idx["fund_code"],
                        index_name=idx["index_name"],
                        greed_value=idx["greed"],
                        close_price=idx.get("close"),
                        percentile=idx.get("percentile"),
                        status=idx["status"],
                        decline_rate_5d=idx.get("decline_rate"),
                        change_5=idx.get("change_5"),
                        change_20=idx.get("change_20"),
                        created_at=now,
                    )
                    db.add(snap)
                    snapshots.append(snap)
                db.commit()
                logger.info("黄金坑快照已保存: %s, %d 条", today, len(snapshots))
            finally:
                db.close()
            return snapshots
        except Exception as e:
            logger.error("保存黄金坑快照失败: %s", e)
            return []

    # ═══════════════════════════════════════════════════════════════
    # QQ 报告与预警
    # ═══════════════════════════════════════════════════════════════

    def format_morning_report(self, status: Optional[Dict[str, Any]] = None) -> str:
        """生成 QQ 盘前报告 (8:50 AM)。"""
        if status is None:
            status = self.get_status()

        as_of = status["as_of"]
        window = status["golden_pit_window"]
        indices = status["indices"]
        conf = status["triple_confirmation"]
        pred = status["prediction"]

        lines = [f"📊 黄金坑盘前报告 — {as_of}", "━━━━━━━━━━━━━━━━━━━", ""]

        # 按 tier 分组显示
        tier_order = ["core", "satellite", "defense", "watch", "drop"]
        tier_labels = {
            "core": "🏆 核心 (必做)", "satellite": "📡 卫星 (选做)",
            "defense": "🛡 防御 (可选)", "watch": "👀 观察 (仅预警)", "drop": "❌ 放弃",
        }
        tier_icons = {"golden_pit": "🔴", "warning": "🟠", "normal": "🟢"}

        pit_count = 0
        for tier_name in tier_order:
            tier_indices = [i for i in indices if i.get("tier") == tier_name]
            if not tier_indices:
                continue
            tier_indices.sort(key=lambda x: x["priority"])
            lines.append(tier_labels.get(tier_name, tier_name))

            for idx in tier_indices:
                icon = tier_icons.get(idx["status"], "⚪")
                detail = ""
                if idx["status"] == "golden_pit" and idx.get("entry_date"):
                    pit_count += 1
                    detail = f" ({idx['entry_date']}入坑，第{idx.get('days_in_pit', '?')}天)"
                    if idx.get("absolute_triggered"):
                        detail += " ★双重确认"
                elif idx["status"] == "warning":
                    dw = idx.get("days_in_warning", 0)
                    if dw > 0:
                        detail = f" (P10第{dw}天"
                        if idx.get("days_to_pit"):
                            detail += f"，预计{idx['eta_date']}入坑"
                        if idx.get("is_fake_signal"):
                            detail += " ⚠假信号风险"
                        detail += ")"
                elif idx.get("decline_rate"):
                    detail = f" (日跌{idx['decline_rate']:.3f})"

                # 趋势方向
                trend_icon = {"declining": "↓", "bottoming": "→", "recovering": "↑"}.get(
                    idx.get("trend", ""), "")
                trend_label = {"declining": "跌", "bottoming": "底", "recovering": "升"}.get(
                    idx.get("trend", ""), "")

                # 信号质量 + 仓位建议
                sq = idx.get("signal_quality", "")
                sq_short = {"strong": "强", "good": "中", "weak": "弱", "inferred": "?"}.get(sq, "")
                ds_tag = "[价]" if idx.get("data_source") == "pi_server_price" else ""
                pos_label = ""
                if idx.get("position_tier_label") and idx.get("tier") not in ("drop", "watch"):
                    pos_label = f" → {idx['position_tier_label']}"

                lines.append(
                    f"{icon} {idx['index_name']:6s} {idx['greed']:.2f}  "
                    f"P{idx['percentile']:.0f} {trend_icon}{trend_label} "
                    f"{STATUS_MAP[idx['status']]['label']}{detail}"
                    f"  [{sq_short}]{ds_tag}{pos_label}"
                )
            lines.append("")

        lines.append("")

        phase = window.get("phase", "idle")
        if phase == "buying":
            rising = window.get("turning_leader_rising", 0)
            lines.append(
                f"📍 买入窗口：{window['leading_index']}拐点确认 "
                f"({window['start_date']}起, 第{window['current_day']}天, 已回升{rising}天)"
            )
            lines.append(f"   拐点确认: {window['turning_count']}个指数  加仓节奏: 50%→75%→100%")
        elif phase == "waiting":
            pit_count = window.get("pit_count", 0)
            warn_count = window.get("warning_count", 0)
            lines.append(
                f"📍 {pit_count}个指数已入黄金坑 ({warn_count}个预警)  "
                f"领先:{window['leading_index']}  |  等待贪婪值回升确认拐点"
            )
        else:
            lines.append("📍 当前无黄金坑信号")

        lines.append("")

        # 三重确认
        l1 = conf["layer1"]
        l2 = conf["layer2"]
        l3 = conf["layer3"]
        lines.append(f"{'☑' if l1['confirmed'] else '☐'} 蛋糕理论: {l1['status']}")
        lines.append(f"{'☑' if l2['confirmed'] else '☐'} 宽基确认: {l2['status']}")
        lines.append(f"{'☑' if l3['confirmed'] else '☐'} 细分板块: {l3['status']}")

        # 全球宏观
        gm = status.get("global_macro", {})
        if gm:
            gate_icon = "🔒" if gm.get("liquidity_gate") == "closed" else "🔓"
            lines.append(f"{gate_icon} 全球宏观: {gm.get('summary', '')}")
            # 资金持续流向
            cf = gm.get("capital_flow", {})
            if cf.get("summary"):
                lines.append(f"💰 资金流向: {cf['summary']}")
            # 背离警告
            divergent = [i for i in indices if i.get("turning_validation") == "divergent"]
            if divergent:
                names = ", ".join(i["index_name"] for i in divergent)
                lines.append(f"⚠️ 全球趋势背离: {names} 仓位已限制在拐点前水平")

        if pred and pred.get("next_index"):
            lines.append(f"💡 预测: {pred['next_index']} 预计 {pred['eta_days']} 天后入坑 ({pred['eta_date']})")

        turning_count = sum(1 for i in indices if i.get("turning_point_confirmed"))
        pre_count = sum(1 for i in indices if i.get("position_tier") == "pre_turn")
        if phase != "idle":
            if turning_count > 0:
                lines.append(f"💡 拐点已确认 ({turning_count}个指数): 快速加仓 50%→75%→100%")
            elif pre_count > 0:
                lines.append(f"💡 拐点前 ({pre_count}个指数): 轻仓累积, 等待贪婪值连续回升确认拐点")

        # 退出信号
        exit_indices = [i for i in indices if i.get("exit_signal")]
        if exit_indices:
            lines.append("")
            lines.append("🚪 退出信号:")
            for ei in exit_indices:
                icon = {"half_exit": "🟡", "full_exit": "🔴", "stop_profit": "🟠"}.get(ei["exit_signal"], "⚪")
                lines.append(f"  {icon} {ei['index_name']}: {ei['exit_reason']}")

        return "\n".join(lines)

    def check_threshold_crossings(self, status: Optional[Dict[str, Any]] = None) -> List[str]:
        """检测阈值穿越，返回需要推送的预警消息列表。

        Args:
            status: 可选，传入已有的 status 避免重复 API 调用。不传则自动获取。
        """
        if status is None:
            status = self.get_status()
        indices = status["indices"]
        alerts = []

        # 加载昨日快照用于对比 (percentile 值)
        prev_percentile = self._load_previous_percentile()

        for idx in indices:
            code = idx["fund_code"]
            current_pct = idx["percentile"]
            prev_pct = prev_percentile.get(code)

            if prev_pct is None:
                continue

            # 检测 P10 预警线穿越 (percentile 从 >10 变为 <=10)
            if current_pct > PERCENTILE_WARNING and prev_pct <= PERCENTILE_WARNING:
                continue  # 反弹中，不预警
            if prev_pct > PERCENTILE_WARNING and current_pct <= PERCENTILE_WARNING:
                ds_tag = " [价格分位]" if idx.get("data_source") == "pi_server_price" else ""
                alerts.append(
                    f"⚠️ {idx['index_name']} 进入预警区 (分位 {idx['percentile']:.0f}%){ds_tag}\n"
                    f"   📉 'greed': {idx['greed']:.4f}  "
                    f"预计 {idx.get('eta_date', '?')} 进入黄金坑"
                )

            # 检测 P5 黄金坑确认 (percentile 从 >5 变为 <=5)
            if prev_pct > PERCENTILE_GOLDEN_PIT and current_pct <= PERCENTILE_GOLDEN_PIT:
                window = status["golden_pit_window"]
                abs_note = " [双重确认]" if idx.get("absolute_triggered") else ""
                ds_tag = " [价格分位]" if idx.get("data_source") == "pi_server_price" else ""
                alerts.append(
                    f"🔴 {idx['index_name']} 进入黄金坑！(分位 {idx['percentile']:.0f}%){abs_note}{ds_tag}\n"
                    f"   📍 窗口：{window['start_date']} - {window['exit_date']}（{PIT_WINDOW_DAYS}交易日）\n"
                    f"   📍 转折点预计：{window['midpoint_date']}\n"
                    f"   📍 信号质量：{SIGNAL_QUALITY_LABEL.get(idx.get('signal_quality', ''), '未知')}\n"
                    f"   💡 回测预期：15天 +{idx.get('expected_15d', '?')}% | 20天 +{idx.get('expected_20d', '?')}%"
                )

        return alerts

    def _load_previous_percentile(self) -> Dict[str, float]:
        """从数据库加载上一个交易日的分位值。"""
        try:
            from app.database import SessionLocal
            from app.models.golden_pit import GoldenPitSnapshot

            db = SessionLocal()
            try:
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                rows = (
                    db.query(GoldenPitSnapshot)
                    .filter(GoldenPitSnapshot.date == yesterday)
                    .all()
                )
                if not rows:
                    two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
                    rows = (
                        db.query(GoldenPitSnapshot)
                        .filter(GoldenPitSnapshot.date == two_days_ago)
                        .all()
                    )
                return {r.fund_code: (r.percentile or 50.0) for r in rows}
            finally:
                db.close()
        except Exception:
            return {}

    # ═══════════════════════════════════════════════════════════════
    # 内部计算方法
    # ═══════════════════════════════════════════════════════════════

    def _extract_arkvol_indices(self, alla_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 ArkVol alla 数据提取贪婪值驱动的指数状态。"""
        # 构建映射: ArkVol fund_code → (config_key, config)
        arkvol_map = self._arkvol_code_map()
        config_by_arkvol = {}
        for arkvol_key, config_key in arkvol_map.items():
            config_by_arkvol[arkvol_key] = (config_key, CHINA_INDICES[config_key])

        # 当前快照
        items = alla_data.get("items", [])
        current_map: Dict[str, Dict] = {}
        for item in items:
            arkvol_code = item.get("fund_code", "")
            if arkvol_code in arkvol_map:
                current_map[arkvol_code] = {
                    "greed": float(item.get("greed", 0)),
                    "close": float(item.get("close", 0)),
                }

        # 历史 series
        series_data = alla_data.get("original_page_data", {}).get("series", {}).get("data", {})

        result = []
        for arkvol_code, (config_key, cfg) in config_by_arkvol.items():
            current = current_map.get(arkvol_code, {})
            greed = current.get("greed", 0.0)
            close = current.get("close", 0.0)

            raw_series = series_data.get(arkvol_code, [])
            sorted_series = sorted(raw_series, key=lambda x: x.get("date", ""))

            percentile = self._calculate_percentile(greed, sorted_series)
            decline_rate = self._calculate_decline_rate(sorted_series)

            status = self._determine_status(cfg, greed, percentile)

            absolute_triggered = greed < GREED_ABSOLUTE_PIT
            data_source = "arkvol_greed"

            index_info = self._build_index_info(
                code=config_key, cfg=cfg, value=greed, close=close,
                percentile=percentile, decline_rate=decline_rate,
                status=status, absolute_triggered=absolute_triggered,
                data_source=data_source, sorted_series=sorted_series,
                as_of=alla_data.get("as_of", ""),
            )
            result.append(index_info)

        return result

    def _extract_pi_server_indices(self, as_of: str) -> List[Dict[str, Any]]:
        """从 Pi Server ETF K 线数据提取价格分位驱动的指数状态。"""
        pi_codes = {code: cfg for code, cfg in CHINA_INDICES.items() if cfg.get("data_source") == "pi_server"}
        if not pi_codes:
            return []

        result = []
        for code, cfg in pi_codes.items():
            etf_code = cfg.get("etf_code", "")
            if not etf_code:
                continue

            bars = self._fetch_pi_server_kline(etf_code, limit=250)
            if not bars or len(bars) < 10:
                logger.warning("Pi Server %s K线数据不足，跳过", etf_code)
                continue

            # 用最近 120 根 bar 做滚动窗口
            window_bars = bars[-120:]
            closes_120 = [float(b.get("close", 0)) for b in window_bars]
            current_price = closes_120[-1] if closes_120 else 0.0
            current_close = closes_120[-1] if closes_120 else 0.0

            # 从价格位置计算分位和合成 greed
            percentile = self._calculate_price_percentile(current_price, closes_120)
            synthetic_greed = self._price_based_greed(current_price, closes_120)
            decline_rate = self._price_decline_rate(closes_120)

            status = self._determine_status(cfg, synthetic_greed, percentile)

            absolute_triggered = synthetic_greed < GREED_ABSOLUTE_PIT
            data_source = "pi_server_price"

            # 构建一个与 ArkVol series 兼容的 sorted_series (用价格代替 greed)
            sorted_series = [
                {"date": b.get("date", ""), "greed": self._price_based_greed(
                    float(b.get("close", 0)), closes_120
                ), "close": float(b.get("close", 0))}
                for b in bars
            ]

            index_info = self._build_index_info(
                code=code, cfg=cfg, value=synthetic_greed, close=current_close,
                percentile=percentile, decline_rate=decline_rate,
                status=status, absolute_triggered=absolute_triggered,
                data_source=data_source, sorted_series=sorted_series,
                as_of=as_of,
            )
            result.append(index_info)

        return result

    def _build_index_info(
        self,
        code: str,
        cfg: Dict[str, Any],
        value: float,
        close: float,
        percentile: float,
        decline_rate: float,
        status: str,
        absolute_triggered: bool,
        data_source: str,
        sorted_series: List[Dict],
        as_of: str,
    ) -> Dict[str, Any]:
        """构建统一的指数状态字典，含 Day 1 检测和仓位分级。"""
        tier = cfg.get("tier", "drop")
        position_weight = cfg.get("position_weight", 0.0)
        today_str = as_of or datetime.now().strftime("%Y-%m-%d")

        index_info = {
            "fund_code": code,
            "index_name": cfg["name"],
            "priority": cfg["priority"],
            "tier": tier,
            "position_weight": position_weight,
            "greed": round(value, 4),
            "prev_greed": round(float(sorted_series[-2].get("greed", 0)), 4) if len(sorted_series) >= 2 else None,
            "close": round(close, 4),
            "percentile": round(percentile, 1),
            "status": status,
            "decline_rate": round(decline_rate, 4),
            "absolute_triggered": absolute_triggered,
            "data_source": data_source,
            "signal_quality": cfg.get("signal_quality", "unknown"),
            "expected_15d": cfg.get("exp_15d"),
            "expected_20d": cfg.get("exp_20d"),
            # 入场 / 出场策略 (回测最优参数)
            "entry_strategy": _describe_entry_strategy(cfg),
            "exit_strategy": _describe_exit_strategy(cfg),
            "entry_offset": cfg.get("entry_offset", 0),
            "pit_greed": cfg.get("pit_greed"),
            "entry_greed": cfg.get("entry_greed"),
            "exit_full_pct": cfg.get("exit_full_pct"),
            "exit_half_pct": cfg.get("exit_half_pct"),
            "exit_fallback_days": cfg.get("exit_fallback_days"),
            # DCA 策略参数 (v5)
            "dca_strategy": cfg.get("dca_strategy", "uniform_10"),
            "dca_label": _strategy_label(cfg.get("dca_strategy", "uniform_10")),
            "dca_fallback": cfg.get("dca_fallback", 10),
            "trend_factors": cfg.get("trend_factors"),
            "position_multiplier": cfg.get("position_multiplier", 1.0),
            # 趋势标签 (DCA v5 展示用)
            "trend_label": "—",
            # Day 1 检测字段
            "p10_entry_date": None,
            "days_in_warning": 0,
            "is_fake_signal": False,
            "is_first_p10_cross": False,
            # 趋势检测字段
            "trend": "declining",
            "days_rising": 0,
            "prev_greed": None,
            "signal_trigger_greed": None,  # DCA二次信号检测用: 信号触发日的贪婪值
            "turning_point_confirmed": False,
            "turning_start_date": None,
            "last_change": 0.0,
            # 仓位分级
            "position_tier": None,
            "position_tier_label": None,
            # 退出信号
            "exit_signal": None,
            "exit_reason": "",
            # ETA
            "days_to_pit": None,
            "eta_date": None,
            "entry_date": None,
            "days_in_pit": None,
        }

        # ── Day 1 检测: 用全量序列固定阈值找首次穿越日 ──
        entry_pct = cfg.get("entry_pct", PERCENTILE_WARNING)
        fixed_entry = cfg.get("entry_greed") if cfg.get("use_fixed_greed") else None
        if sorted_series and len(sorted_series) >= 60:
            p10_entry_date, days_in_warning, is_first_cross = self._detect_p10_entry(
                sorted_series, today_str, entry_pct=entry_pct,
                fixed_threshold=fixed_entry,
            )
            index_info["p10_entry_date"] = p10_entry_date
            index_info["days_in_warning"] = days_in_warning
            index_info["is_first_p10_cross"] = is_first_cross

            # 假信号检测: 曾破P10但已反弹, 且从未到P5
            if status == "normal" and p10_entry_date and days_in_warning <= FAKE_SIGNAL_REBOUND_DAYS:
                index_info["is_fake_signal"] = True

            # 二次信号检测用: 最近30天窗口内的最低贪婪值 (新低>5%触发重置)
            if sorted_series and len(sorted_series) >= 2:
                window_greeds = [float(s.get("greed", 0)) for s in sorted_series[-30:]]
                index_info["signal_trigger_greed"] = round(min(window_greeds), 4)

            # ── 趋势检测 + 仓位分级 ──
            if status in ("golden_pit", "warning"):
                td = cfg.get("turning_days", TURNING_CONSECUTIVE_DAYS)
                trend = self._detect_trend(sorted_series, turning_days=td)
                index_info["trend"] = trend["trend"]
                index_info["days_rising"] = trend["days_rising"]
                index_info["turning_point_confirmed"] = trend["turning_confirmed"]
                index_info["last_change"] = trend["last_change"]

                if trend["turning_confirmed"]:
                    # 拐点起始日 = 第一天的回升日期
                    if trend["days_rising"] < len(sorted_series):
                        idx_turn = len(sorted_series) - trend["days_rising"] - 1
                        index_info["turning_start_date"] = sorted_series[max(0, idx_turn)].get("date", "")
                    if trend["days_rising"] >= 4:
                        index_info["position_tier"] = "full"
                        index_info["position_tier_label"] = "强势上涨"
                    elif trend["days_rising"] >= 3:
                        index_info["position_tier"] = "accelerate"
                        index_info["position_tier_label"] = "趋势加速"
                    else:
                        index_info["position_tier"] = "turning"
                        index_info["position_tier_label"] = "拐点确认"
                else:
                    index_info["position_tier"] = "pre_turn"
                    index_info["position_tier_label"] = "跌势未止"

                # 计算当前趋势因子 (DCA v5 展示用)
                trend_factor = get_trend_factor(
                    trend=index_info.get("trend", "declining"),
                    days_rising=index_info.get("days_rising", 0),
                    fund_code=code,
                    current_greed=index_info.get("greed", 0.0),
                    entry_greed=index_info.get("entry_greed") or 999.0,
                )
                index_info["trend_factor"] = round(trend_factor, 2)
                index_info["trend_label"] = _trend_label(index_info.get("trend", "declining"), trend_factor)

            elif tier in ("drop", "watch"):
                index_info["position_tier"] = None
                index_info["position_tier_label"] = "跳过 (不入金)"

            # ── 退出信号检测 (per-index 参数) ──
            exit_full_pct = cfg.get("exit_full_pct", 50)
            exit_half_pct = cfg.get("exit_half_pct", 30)
            exit_info = self._detect_exit_signal(
                sorted_series,
                index_info["turning_point_confirmed"],
                index_info["percentile"],
                exit_full_pct=exit_full_pct,
                exit_half_pct=exit_half_pct,
            )
            index_info["exit_signal"] = exit_info["signal"]
            index_info["exit_reason"] = exit_info["reason"]

            # ── 兜底退出: 拐点确认后超过 exit_fallback_days 天，强制清仓 ──
            if (index_info["exit_signal"] is None
                    and index_info["turning_point_confirmed"]
                    and index_info["turning_start_date"]):
                fallback = cfg.get("exit_fallback_days")
                if fallback:
                    days_since_turn = _trading_days_between(
                        index_info["turning_start_date"], today_str
                    )
                    if days_since_turn >= fallback:
                        index_info["exit_signal"] = "fallback_exit"
                        index_info["exit_reason"] = (
                            f"拐点确认{index_info['turning_start_date']}后已过"
                            f"{days_since_turn}天≥{fallback}天兜底线，强制退出"
                        )

        # ── ETA 预测 (预警区 → 黄金坑) ──
        pit_pct = cfg.get("pit_pct", PERCENTILE_GOLDEN_PIT)
        if status == "warning" and decline_rate > 0.0001 and percentile > pit_pct:
            sorted_vals = sorted([float(s.get("greed", 0)) for s in sorted_series])
            p5_val = sorted_vals[max(0, int(len(sorted_vals) * 0.05))]
            gap = value - p5_val
            if gap > 0:
                days_to = max(1, round(gap / decline_rate))
                index_info["days_to_pit"] = days_to
                index_info["eta_date"] = _add_trading_days(today_str, days_to)

        # ── 黄金坑入坑日期回测 ──
        # 用滚动窗口计算固定 P(pit_pct) 贪婪阈值，与 _calculate_percentile 逻辑一致。
        if status == "golden_pit" and sorted_series and len(sorted_series) >= 60:
            greeds = [float(s.get("greed", 0)) for s in sorted_series]
            dates = [s.get("date", "") for s in sorted_series]

            if cfg.get("use_fixed_greed") and cfg.get("pit_greed") is not None:
                pit_threshold = cfg["pit_greed"]
            else:
                pit_pct = cfg.get("pit_pct", PERCENTILE_GOLDEN_PIT)
                window_greeds = greeds[-PERCENTILE_WINDOW_DAYS:] if len(greeds) > PERCENTILE_WINDOW_DAYS else greeds
                all_sorted = sorted(window_greeds)
                threshold_idx = int(len(all_sorted) * pit_pct / 100)
                pit_threshold = all_sorted[min(threshold_idx, len(all_sorted) - 1)]

            # 从今天往前找：贪婪值 > 阈值 = 不在坑内，其后一天就是 Day 1
            entry_idx = 0  # 默认：全部历史数据都在坑内
            for i in range(len(greeds) - 1, -1, -1):
                if greeds[i] > pit_threshold:
                    entry_idx = i + 1
                    break
            if entry_idx < len(greeds):
                index_info["entry_date"] = dates[entry_idx]
                index_info["days_in_pit"] = len(greeds) - entry_idx

        return index_info

    def _detect_p10_entry(
        self, sorted_series: List[Dict], today_str: str,
        entry_pct: int = PERCENTILE_WARNING,
        fixed_threshold: Optional[float] = None,
    ) -> tuple:
        """检测当前是否在预警信号中，以及 Day 1 是哪天。

        当 fixed_threshold 不为 None 时使用固定贪婪阈值 (回测最优),
        否则使用滚动窗口百分位阈值。

        Returns:
            (p10_entry_date, days_in_warning, is_first_cross)
        """
        greeds = [float(s.get("greed", 0)) for s in sorted_series]
        dates = [s.get("date", "") for s in sorted_series]

        if len(greeds) < 60:
            return (None, 0, False)

        if fixed_threshold is not None:
            entry_threshold = fixed_threshold
        else:
            window_greeds = greeds[-PERCENTILE_WINDOW_DAYS:] if len(greeds) > PERCENTILE_WINDOW_DAYS else greeds
            all_sorted = sorted(window_greeds)
            threshold_idx = int(len(all_sorted) * entry_pct / 100)
            entry_threshold = all_sorted[min(threshold_idx, len(all_sorted) - 1)]
        if greeds[-1] > entry_threshold:
            return (None, 0, False)

        # 往回找到最近一次贪婪值高于阈值的位置，其后一天就是 Day 1
        entry_idx = 0  # 默认：全部历史数据都在预警区内
        for i in range(len(greeds) - 1, -1, -1):
            if greeds[i] > entry_threshold:
                entry_idx = i + 1
                break

        if entry_idx >= len(greeds):
            return (None, 0, False)

        p10_entry_date = dates[entry_idx]
        days_in = _trading_days_between(p10_entry_date, today_str) + 1
        is_first_cross = days_in <= FAKE_SIGNAL_REBOUND_DAYS + 1

        return (p10_entry_date, days_in, is_first_cross)

    def _calculate_percentile(self, current_greed: float, series: List[Dict], window: int = None) -> float:
        """计算当前贪婪值在自身历史中的分位数（越低越恐慌）。

        使用滚动窗口而非 expanding-window：窗口大小恒定 (默认500天)，
        Px 对应的贪婪阈值不会随数据累积而漂移。
        """
        if window is None:
            window = PERCENTILE_WINDOW_DAYS
        if not series:
            return 50.0
        # 只取最近 window 天，避免 expanding-window 漂移
        window_series = series[-window:] if len(series) > window else series
        greeds = sorted([float(s.get("greed", 0)) for s in window_series])
        if not greeds or len(greeds) < 2:
            return 50.0
        count_below = sum(1 for g in greeds if g < current_greed)
        return round(count_below / len(greeds) * 100, 1)

    def _calculate_decline_rate(self, series: List[Dict], window: int = 5) -> float:
        """计算最近 N 日的平均贪婪值日跌幅（正值=下跌，负值=上涨）。"""
        if len(series) < window + 1:
            return 0.0
        recent = sorted(series, key=lambda x: x.get("date", ""))[-window - 1:]
        greeds = [float(s.get("greed", 0)) for s in recent]
        if len(greeds) < 2:
            return 0.0
        total_decline = greeds[0] - greeds[-1]
        return round(total_decline / window, 4)

    @staticmethod
    def _determine_status(cfg: Dict[str, Any], greed: float, percentile: float) -> str:
        """判定指数状态: 优先使用固定贪婪阈值 (回测最优), 其次使用滚动百分位。

        use_fixed_greed=True 时用 pit_greed/entry_greed 固定值比较,
        消除 expanding-window percentile 的 Px 漂移问题。
        """
        if cfg.get("use_fixed_greed"):
            pit_greed = cfg.get("pit_greed")
            entry_greed = cfg.get("entry_greed")
            if pit_greed is not None and greed <= pit_greed:
                return "golden_pit"
            elif entry_greed is not None and greed <= entry_greed:
                return "warning"
            return "normal"
        else:
            pit_pct = cfg.get("pit_pct", PERCENTILE_GOLDEN_PIT)
            entry_pct = cfg.get("entry_pct", PERCENTILE_WARNING)
            if percentile <= pit_pct:
                return "golden_pit"
            elif percentile <= entry_pct:
                return "warning"
            return "normal"

    @staticmethod
    def _detect_trend(sorted_series: List[Dict], turning_days: int = None) -> Dict[str, Any]:
        """检测贪婪值趋势方向，判断是否已过拐点。

        拐点 = 贪婪值从连续下降转为连续回升。连续 N 天回升确认拐点。

        Returns:
            trend: "declining" | "bottoming" | "recovering"
            days_rising: 连续回升天数
            turning_confirmed: 是否已确认拐点
        """
        if turning_days is None:
            turning_days = TURNING_CONSECUTIVE_DAYS

        if len(sorted_series) < 5:
            return {"trend": "declining", "days_rising": 0,
                    "turning_confirmed": False, "last_change": 0.0}

        greeds = [float(s.get("greed", 0)) for s in sorted_series]

        days_rising = 0
        for i in range(len(greeds) - 1, 0, -1):
            if greeds[i] > greeds[i - 1]:
                days_rising += 1
            else:
                break

        last_change = round(greeds[-1] - greeds[-2], 4) if len(greeds) >= 2 else 0.0

        if days_rising >= turning_days:
            return {"trend": "recovering", "days_rising": days_rising,
                    "turning_confirmed": True, "last_change": last_change}
        elif days_rising == turning_days - 1 and turning_days >= 2:
            return {"trend": "bottoming", "days_rising": days_rising,
                    "turning_confirmed": False, "last_change": last_change}
        elif days_rising >= 1 and turning_days == 1:
            return {"trend": "recovering", "days_rising": days_rising,
                    "turning_confirmed": True, "last_change": last_change}
        else:
            return {"trend": "declining", "days_rising": 0,
                    "turning_confirmed": False, "last_change": last_change}

    @staticmethod
    def _detect_exit_signal(
        sorted_series: List[Dict],
        turning_confirmed: bool,
        percentile: float,
        exit_full_pct: int = 50,
        exit_half_pct: int = 30,
    ) -> Dict[str, Any]:
        """检测退出信号（全量回测校准 per-index 参数）。

        只在拐点确认后才发出退出信号（拐点前不退出）。
        退出规则:
          - percentile >= exit_full_pct → full_exit (清仓)
          - percentile >= exit_half_pct → half_exit (卖一半)
          - 拐点后连续2天回落且曾回到 exit_half_pct → stop_profit (止盈保护)

        Returns:
            {signal: null|"half_exit"|"full_exit"|"stop_profit", reason: str}
        """
        result = {"signal": None, "reason": ""}

        if not turning_confirmed:
            return result

        if len(sorted_series) < 5:
            return result

        greeds = [float(s.get("greed", 0)) for s in sorted_series]

        # 全清退出 (per-index threshold)
        if percentile >= exit_full_pct:
            result["signal"] = "full_exit"
            result["reason"] = f"贪婪值回升至 P{percentile:.0f}≥P{exit_full_pct}，建议清仓"
            return result

        # 减半退出 (per-index threshold)
        if percentile >= exit_half_pct:
            result["signal"] = "half_exit"
            result["reason"] = f"贪婪值回升至 P{percentile:.0f}≥P{exit_half_pct}，建议减持 50%"
            return result

        # 拐点后连续回落 → 止盈保护
        days_declining = 0
        for i in range(len(greeds) - 1, 0, -1):
            if greeds[i] < greeds[i - 1]:
                days_declining += 1
            else:
                break
        if days_declining >= 2:
            max_greed = max(greeds[-10:]) if len(greeds) >= 10 else max(greeds)
            all_vals = sorted(greeds)
            max_pct = sum(1 for g in all_vals if g <= max_greed) / len(all_vals) * 100
            if max_pct >= exit_half_pct:
                result["signal"] = "stop_profit"
                result["reason"] = (
                    f"拐点后连续{days_declining}天回落（曾回升至P{max_pct:.0f}≥P{exit_half_pct}），建议止盈"
                )
                return result

        return result

    def _detect_golden_pit_window(self, indices: List[Dict[str, Any]]) -> Dict[str, Any]:
        """检测黄金坑窗口。

        窗口定义:
          - waiting: 有指数在黄金坑/预警中，但拐点未确认 → 轻仓等待
          - buying:  至少一个指数拐点确认 → 窗口开启，加仓买入
          - idle:    无信号
        窗口从第一个指数拐点确认日开始，关闭条件是所有指数贪婪值回升到合理位置。
        """
        today_str = datetime.now().strftime("%Y-%m-%d")

        tradeable = [i for i in indices if i.get("tier") in ("core", "satellite", "defense")]

        turning = [i for i in tradeable if i.get("turning_point_confirmed")]
        signals = [i for i in tradeable if i["status"] in ("warning", "golden_pit")]

        pit_count = sum(1 for i in signals if i["status"] == "golden_pit")
        warning_count = sum(1 for i in signals if i["status"] == "warning")

        base = {
            "phase": "idle",
            "start_date": None,
            "exit_date": None,
            "midpoint_date": None,
            "leading_index": None,
            "leading_tier": None,
            "current_day": 0,
            "pit_count": pit_count,
            "warning_count": warning_count,
            "turning_count": len(turning),
            "resonance_multiplier": _compute_resonance(pit_count),
        }

        # 统一计算窗口起始日: 取所有信号中最早的 entry_date/eta_date
        # 保证 waiting→buying 阶段切换时 window_start 不变，DCA 日志不会丢失
        all_signals = signals + [t for t in turning if t not in signals]
        candidate_dates = []
        for s in all_signals:
            d = s.get("entry_date") or s.get("eta_date")
            if d:
                candidate_dates.append(d)
        window_start = min(candidate_dates) if candidate_dates else today_str
        current_day = _trading_days_between(window_start, today_str) + 1 if window_start else 1

        if turning:
            turning.sort(key=lambda x: x.get("turning_start_date") or "9999")
            leader = turning[0]
            turning_start = leader.get("turning_start_date")
            days_since_turning = _trading_days_between(turning_start, today_str) + 1 if turning_start else 0
            return {
                **base,
                "active": True,
                "phase": "buying",
                "start_date": window_start,
                "exit_date": _add_trading_days(window_start, PIT_WINDOW_DAYS),
                "midpoint_date": _add_trading_days(window_start, PIT_WINDOW_DAYS // 2),
                "turning_start_date": turning_start,
                "days_since_turning": max(1, days_since_turning),
                "leading_index": leader["index_name"],
                "leading_tier": leader.get("tier"),
                "current_day": max(1, current_day),
                "turning_leader_rising": leader.get("days_rising", 0),
            }
        elif signals:
            signals.sort(key=lambda x: x.get("p10_entry_date") or "9999")
            return {
                **base,
                "active": False,
                "phase": "waiting",
                "start_date": window_start,
                "exit_date": _add_trading_days(window_start, PIT_WINDOW_DAYS),
                "midpoint_date": _add_trading_days(window_start, PIT_WINDOW_DAYS // 2),
                "leading_index": signals[0]["index_name"],
                "leading_tier": signals[0].get("tier"),
                "current_day": max(1, current_day),
            }
        else:
            return {
                **base,
                "active": False,
                "phase": "idle",
            }

    # ═══════════════════════════════════════════════════════════════
    # Global macro overlay
    # ═══════════════════════════════════════════════════════════════

    def _parse_global_macro_overlay(self, gcf_data: Dict[str, Any]) -> Dict[str, Any]:
        """从 global-capital-flow 数据解析宏观叠加层。

        Returns:
            liquidity_gate, sentiment_score, sentiment_label,
            global_trend, global_macro_coefficient, summary
        """
        score = float(gcf_data.get("sentiment_score", 50))
        label = str(gcf_data.get("sentiment_label", "未知"))

        liquidity_gate = "closed" if score <= 20 else "open"

        if score <= 20:
            macro_coef = 0.0
        elif score <= 35:
            macro_coef = 0.5
        elif score <= 75:
            macro_coef = 1.0
        else:
            macro_coef = 0.8

        global_trend = self._compute_global_trend(gcf_data)
        capital_flow = self._compute_capital_flow_persistence(gcf_data)

        trend_labels = {"rising": "回升中", "declining": "下降中", "flat": "持平", "unknown": "未知"}
        summary = (
            f"全球风险偏好: {label}({score:.0f}), "
            f"闸门: {'关闭' if liquidity_gate == 'closed' else '开启'}, "
            f"趋势: {trend_labels.get(global_trend, '未知')}, "
            f"仓位系数: {macro_coef:.1f}x"
        )

        return {
            "liquidity_gate": liquidity_gate,
            "sentiment_score": score,
            "sentiment_label": label,
            "global_trend": global_trend,
            "global_macro_coefficient": macro_coef,
            "capital_flow": capital_flow,
            "summary": summary,
        }

    def _compute_global_trend(self, gcf_data: Dict[str, Any]) -> str:
        """从 GCF 数据计算全球风险偏好趋势方向。

        优先用 items 中各市场的 momentum_5 均值（已由 API 预计算），
        其次从 series 提取 A 股 share 占比序列推断趋势。
        """
        # 方案 1: items 中每个市场有 momentum_5 字段，取各市场均值
        items = gcf_data.get("items", [])
        if items:
            momentums = [
                it.get("momentum_5", 0) or 0
                for it in items
                if it.get("eligible")
            ]
            if momentums:
                avg = sum(momentums) / len(momentums)
                if avg > 0.02:
                    return "rising"
                elif avg < -0.02:
                    return "declining"
                else:
                    return "flat"

        # 方案 2: 从 series 提取 A 股 share 占比近 5 天趋势
        series_list = gcf_data.get("series", [])
        if series_list and len(series_list) >= 5:
            a_shares = []
            for item in series_list[-5:]:
                shares = item.get("shares", {})
                a_shares.append(float(shares.get("a_share", 0)))
            if len(a_shares) >= 3:
                rising = sum(1 for i in range(1, len(a_shares)) if a_shares[i] > a_shares[i - 1])
                declining = sum(1 for i in range(1, len(a_shares)) if a_shares[i] < a_shares[i - 1])
                if rising >= 3:
                    return "rising"
                elif declining >= 3:
                    return "declining"
                else:
                    return "flat"

        return "unknown"

    def _compute_capital_flow_persistence(
        self, gcf_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """从 series.shares 计算各市场资金持续流向。

        Returns:
            {markets: {id: {name, direction, consecutive_days, cumulative_pp}}, summary: str}
        """
        items = gcf_data.get("items", [])
        series_list = gcf_data.get("series", [])

        if not series_list or len(series_list) < 3:
            return {"markets": {}, "summary": ""}

        # 构建 market id → 显示名 映射
        name_map = {it["id"]: it.get("name", it["id"]) for it in items if it.get("id")}

        # 从 series 提取各市场每日 share 值
        dates_series = [
            (s.get("date", ""), s.get("shares", {}))
            for s in series_list
            if isinstance(s.get("shares"), dict)
        ]
        if len(dates_series) < 3:
            return {"markets": {}, "summary": ""}

        market_ids = list(dates_series[-1][1].keys())
        markets = {}

        for mid in market_ids:
            # 提取该市场最近 60 天的 share 序列
            shares_seq = [
                (d, float(shares.get(mid, 0)))
                for d, shares in dates_series[-60:]
            ]
            if len(shares_seq) < 3:
                continue

            # 从最新日开始向前，统计连续同向天数和累计变化
            latest_share = shares_seq[-1][1]
            direction = None
            consecutive_days = 0
            cumulative_pp = 0.0

            for i in range(len(shares_seq) - 1, 0, -1):
                curr = shares_seq[i][1]
                prev = shares_seq[i - 1][1]
                # 忽略 0 值
                if curr == 0 and prev == 0:
                    continue
                change = curr - prev

                day_direction = "inflow" if change > 0.0001 else ("outflow" if change < -0.0001 else "flat")

                if day_direction == "flat":
                    break

                if direction is None:
                    direction = day_direction

                if day_direction != direction:
                    break

                consecutive_days += 1
                cumulative_pp += change

            if direction is None:
                direction = "flat"

            direction_label = {"inflow": "流入", "outflow": "流出", "flat": "持平"}
            markets[mid] = {
                "name": name_map.get(mid, mid),
                "current_share": round(latest_share, 2),
                "direction": direction,
                "direction_label": direction_label.get(direction, "持平"),
                "consecutive_days": consecutive_days,
                "cumulative_pp": round(cumulative_pp, 2),
            }

        # 生成摘要: 先流出再流入
        outflows = [
            (mid, m) for mid, m in markets.items()
            if m["direction"] == "outflow" and m["consecutive_days"] >= 2
        ]
        inflows = [
            (mid, m) for mid, m in markets.items()
            if m["direction"] == "inflow" and m["consecutive_days"] >= 2
        ]
        outflows.sort(key=lambda x: x[1]["consecutive_days"], reverse=True)
        inflows.sort(key=lambda x: x[1]["consecutive_days"], reverse=True)

        parts = []
        for mid, m in outflows:
            parts.append(f"{m['name']}连续{m['consecutive_days']}日流出({m['cumulative_pp']:+.1f}pp)")
        for mid, m in inflows:
            parts.append(f"{m['name']}连续{m['consecutive_days']}日流入({m['cumulative_pp']:+.1f}pp)")

        # 构建份额变化曲线（最近 60 个交易日）
        share_history = []
        for d, shares in dates_series[-60:]:
            entry = {"date": d}
            for mid, val in shares.items():
                entry[mid] = round(float(val), 2)
            share_history.append(entry)

        return {
            "markets": markets,
            "summary": "; ".join(parts) if parts else "",
            "share_history": share_history,
        }

    def _apply_global_macro_to_indices(
        self, indices: List[Dict[str, Any]], global_macro: Dict[str, Any]
    ) -> None:
        """将全球宏观数据应用到各指数: 拐点验证 + 宏观退出信号。"""
        global_trend = global_macro.get("global_trend", "unknown")
        global_score = global_macro.get("sentiment_score", 50)

        for idx in indices:
            turning_confirmed = idx.get("turning_point_confirmed", False)

            # ── 拐点验证: 全球趋势背离时 cap 仓位 ──
            if turning_confirmed and global_trend in ("declining", "flat", "unknown"):
                trend_cn = {"rising": "回升中", "declining": "下降中", "flat": "持平", "unknown": "未知"}.get(global_trend, global_trend)
                idx["turning_validation"] = "divergent"
                idx["turning_validation_reason"] = (
                    f"全球风险偏好趋势{trend_cn}，"
                    f"A股拐点可能为假信号，仓位限制在拐点前水平"
                )
                if idx.get("position_tier") not in (None, "pre_turn"):
                    idx["position_tier"] = "pre_turn"
                    idx["position_tier_label"] = "全球背离 · 暂缓建仓"
            elif turning_confirmed:
                idx["turning_validation"] = "validated"

            # ── 宏观退出: 全球极度贪婪 → 提前止盈 ──
            if turning_confirmed and global_score > 80:
                existing = idx.get("exit_signal")
                if existing not in ("full_exit", "half_exit"):
                    idx["exit_signal"] = "half_exit"
                    idx["exit_reason"] = (
                        f"全球风险偏好极度贪婪({global_score:.0f})，建议减持50%"
                    )

    def _compute_triple_confirmation(
        self, indices: List[Dict[str, Any]],
        gcf_data: Optional[Dict[str, Any]] = None,
        tech_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """三重确认状态。可传入预取的 API 数据避免重复请求。"""
        # Layer 1: 蛋糕理论 — 全球资金流向
        layer1 = {"label": "蛋糕理论", "status": "未知", "confirmed": False}
        try:
            gcf = gcf_data or {}
            score = gcf.get("sentiment_score")
            if score is not None:
                score = float(score)
                if score < 30:
                    layer1 = {"label": "蛋糕理论", "status": "A股资金外流处历史低位", "confirmed": True}
                else:
                    layer1 = {"label": "蛋糕理论", "status": f"资金外流未到底 (score={score})", "confirmed": False}
        except Exception as e:
            layer1 = {"label": "蛋糕理论", "status": f"数据不可用: {e}", "confirmed": False}

        # Layer 2: 宽基贪婪
        pit_names = [i["index_name"] for i in indices if i["status"] == "golden_pit"]
        warning_names = [i["index_name"] for i in indices if i["status"] == "warning"]
        double_confirm = [i["index_name"] for i in indices if i["status"] == "golden_pit" and i.get("absolute_triggered")]
        layer2_confirmed = len(pit_names) > 0
        layer2_status = f"{len(pit_names)}个在黄金坑" if pit_names else f"{len(warning_names)}个预警"
        if pit_names:
            layer2_status += f" ({', '.join(pit_names)})"
        if double_confirm:
            layer2_status += f" | 双重确认: {', '.join(double_confirm)}"
        layer2 = {
            "label": "宽基贪婪",
            "status": layer2_status,
            "confirmed": layer2_confirmed,
            "details": [f"{i['index_name']}: {i['status']}" for i in sorted(indices, key=lambda x: x["priority"])],
        }

        # Layer 3: 细分板块
        layer3 = {"label": "细分板块", "status": "未知", "confirmed": False}
        try:
            tech = tech_data or {}
            items = tech.get("items", [])
            if items:
                extreme_sectors = []
                for item in items:
                    greed = item.get("greed")
                    name = item.get("etf_name") or item.get("index_name") or item.get("name", "")
                    if greed is not None and float(greed) < GREED_ABSOLUTE_PIT:
                        extreme_sectors.append(name)
                if extreme_sectors:
                    layer3 = {"label": "细分板块", "status": f"{len(extreme_sectors)}个板块已入黄金坑", "confirmed": True}
                else:
                    layer3 = {"label": "细分板块", "status": "暂未触发", "confirmed": False}
        except Exception as e:
            layer3 = {"label": "细分板块", "status": f"数据不可用: {e}", "confirmed": False}

        return {"layer1": layer1, "layer2": layer2, "layer3": layer3}

    def _predict_next_entry(self, indices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """预测下一个进入黄金坑的指数。"""
        candidates = [
            i for i in indices
            if i["status"] != "golden_pit" and i.get("days_to_pit") is not None
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x.get("days_to_pit", 999))
        next_idx = candidates[0]
        return {
            "next_index": next_idx["index_name"],
            "eta_days": next_idx["days_to_pit"],
            "eta_date": next_idx["eta_date"],
            "decline_rate": next_idx["decline_rate"],
        }

    def _build_v2_summary(
        self,
        indices: List[Dict[str, Any]],
        window: Dict[str, Any],
        confirmation: Dict[str, Any],
        prediction: Optional[Dict[str, Any]],
    ) -> str:
        """生成 v2 自然语言解读。"""
        parts = []

        pit_indices = [i for i in indices if i["status"] == "golden_pit"]
        warning_indices = [i for i in indices if i["status"] == "warning"]

        phase = window.get("phase", "idle")
        if phase == "buying":
            rising = window.get("turning_leader_rising", 0)
            parts.append(f"买入窗口已开启: {window['leading_index']}拐点确认，{window['start_date']}起第{window['current_day']}天。")
            parts.append(f"拐点确认{window['turning_count']}个指数，已回升{rising}天，加仓节奏50%→75%→100%。")
            strong = [i["index_name"] for i in pit_indices if i.get("signal_quality") == "strong"]
            if strong:
                parts.append(f"强信号: {', '.join(strong)}（回测Win%≥80%），优先加仓。")
        elif phase == "waiting":
            parts.append(f"黄金坑信号：{window['pit_count']}个指数已入坑/{window['warning_count']}个预警，但贪婪值仍在下跌中。")
            parts.append("黄金坑≠买入窗口。需等待贪婪值连续回升（拐点确认）后，才会开启买入窗口。当前仅轻仓累积(单次≤3%/累计≤15%)。")
        else:
            parts.append("当前无黄金坑信号，各宽基指数情绪正常。")

        if prediction and prediction.get("next_index"):
            parts.append(f"预测: {prediction['next_index']} 预计 {prediction['eta_days']} 天后进入黄金坑。")

        layers_ok = sum(1 for k in ["layer1", "layer2", "layer3"] if confirmation[k]["confirmed"])
        if layers_ok == 3:
            parts.append("三重确认全部达成，黄金坑信号高度可靠。")
        elif layers_ok >= 2:
            parts.append(f"三重确认达成{layers_ok}/3，信号可靠性中等。")

        return "".join(parts)

    # ═══════════════════════════════════════════════════════════════
    # 向后兼容 v1 API
    # ═══════════════════════════════════════════════════════════════

    def get_score(self) -> Dict[str, Any]:
        """v1 兼容: 返回简化综合评分。"""
        status = self.get_status()
        indices = status["indices"]
        # 将 per-index 状态转为综合评分: 按最差状态 + 最低 percentile 转换
        pit_count = sum(1 for i in indices if i["status"] == "golden_pit")
        warn_count = sum(1 for i in indices if i["status"] == "warning")
        min_pct = min((i["percentile"] for i in indices), default=50.0)
        # 评分 = 100 - 最低分位，最低分位越低分数越高
        inverted = max(0, min(100, round(100 - min_pct, 1)))

        factors = [
            {
                "key": "per_index",
                "name": "宽基指数追踪",
                "weight": 1.0,
                "description": "逐指数贪婪值追踪",
                "raw": f"{pit_count}在坑/{warn_count}预警",
                "raw_label": f"{pit_count}在坑/{warn_count}预警",
                "score": round(inverted, 1),
                "weighted": round(inverted, 1),
            }
        ]

        # 按信号最强的指数的信号质量确定颜色深度
        strong_pit = sum(1 for i in indices if i["status"] == "golden_pit" and i.get("signal_quality") == "strong")
        double_confirmed = sum(1 for i in indices if i["status"] == "golden_pit" and i.get("absolute_triggered"))

        return {
            "score": round(inverted, 1),
            "level": "golden_pit" if pit_count > 0 else ("alert" if warn_count > 0 else "normal"),
            "level_label": (
                f"黄金坑区域 ({pit_count}个指数, {double_confirmed}个双重确认)"
                if pit_count > 0
                else ("预警区域" if warn_count > 0 else "正常区域")
            ),
            "level_color": (
                "#dc2626" if double_confirmed > 0
                else ("#ef4444" if pit_count > 0
                      else ("#f97316" if warn_count > 0 else "#22c55e"))
            ),
            "as_of": status["as_of"],
            "factors": factors,
            "summary": status["summary"],
            "errors": None,
        }

    def get_factors(self) -> Dict[str, Any]:
        """v1 兼容: 返回因子明细。"""
        score = self.get_score()
        return {"as_of": score["as_of"], "factors": score["factors"]}
