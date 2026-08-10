# -*- coding: utf-8 -*-
"""
离线入场过滤引擎 — 回测专用，从本地 parquet 数据计算技术指标并运行三层过滤。

与实时 check_entry_filters 的区别:
  - 数据源: stock_daily.parquet + moneyflow.parquet (替代 Xueqiu + 实时指标 API)
  - 跳过 RSR (雪球专有指标)
  - 跳过日内分位检查 (无盘中数据上下文)
  - 跳过时间门控 (回测以收盘价/14:55 价格买入)
  - 跳过资金效率检查 (简化)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class FilterResult:
    """入场过滤结果。"""
    passed: bool = False
    final_grade: str = "blocked"   # "pass" | "probe_only" | "downgraded" | "blocked"
    downgrade_multiplier: float = 0.0  # 1.0 = 全仓, 0.5 = 试探仓, 0.0 = 禁止
    hard_block: bool = False
    hard_block_reasons: List[str] = field(default_factory=list)
    layer1_passed: bool = True
    layer2_passed: bool = True
    layer3_passed: bool = True
    details: List[str] = field(default_factory=list)


# ── 技术指标计算 ──

def compute_ma(closes: List[float], period: int) -> float:
    """简单移动均线。"""
    if len(closes) < period:
        return 0.0
    return sum(closes[-period:]) / period


def compute_ema(data: List[float], period: int) -> float:
    """指数移动均线 (EMA)。"""
    if len(data) < 2:
        return data[-1] if data else 0.0
    k = 2.0 / (period + 1)
    ema = data[0]
    for price in data[1:]:
        ema = price * k + ema * (1 - k)
    return ema


def compute_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9
                 ) -> Tuple[float, float, float]:
    """计算 MACD (DIF, DEA, MACD柱)。"""
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)
    # 用慢线窗口后的数据重算 DIF 序列
    difs = []
    for i in range(slow - 1, len(closes)):
        ema_f = compute_ema(closes[:i + 1], fast)
        ema_s = compute_ema(closes[:i + 1], slow)
        difs.append(ema_f - ema_s)
    dif = difs[-1] if difs else 0.0
    dea = compute_ema(difs, signal)
    macd_bar = 2 * (dif - dea)
    return dif, dea, macd_bar


def compute_kdj(highs: List[float], lows: List[float], closes: List[float],
                period: int = 9) -> Tuple[float, float, float]:
    """计算 KDJ (K, D, J) 使用标准递推公式。

    Returns: (k, d, j) — 最近一日的 K/D/J 值。
    同时返回前一日的 K/D 值用于死叉检测。
    """
    if len(closes) < period + 1:
        return 50.0, 50.0, 50.0

    # 滚动计算 K/D 序列
    k_vals = []
    d_vals = []
    k_val = 50.0
    d_val = 50.0

    for i in range(period - 1, len(closes)):
        hi = max(highs[i - period + 1:i + 1])
        lo = min(lows[i - period + 1:i + 1])
        rsv_i = (closes[i] - lo) / (hi - lo) * 100 if hi != lo else 50.0
        k_val = 2 / 3 * k_val + 1 / 3 * rsv_i
        d_val = 2 / 3 * d_val + 1 / 3 * k_val
        k_vals.append(k_val)
        d_vals.append(d_val)

    if len(k_vals) < 2:
        return k_vals[-1], d_vals[-1], 3 * k_vals[-1] - 2 * d_vals[-1]

    j_val = 3 * k_vals[-1] - 2 * d_vals[-1]
    return k_vals[-1], d_vals[-1], j_val


def compute_prev_kdj(highs: List[float], lows: List[float], closes: List[float],
                     period: int = 9) -> Tuple[float, float, float]:
    """计算前一日 KDJ (K, D, J)。"""
    if len(closes) < period + 2:
        return 50.0, 50.0, 50.0
    return compute_kdj(highs[:-1], lows[:-1], closes[:-1], period)


def compute_rsi(closes: List[float], period: int = 6) -> float:
    """计算 RSI。"""
    if len(closes) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def compute_cci(highs: List[float], lows: List[float], closes: List[float],
                period: int = 14) -> float:
    """计算 CCI (Commodity Channel Index)。"""
    if len(closes) < period:
        return 0.0
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(-period, 0)]
    tp_ma = sum(tp) / period
    md = sum(abs(t - tp_ma) for t in tp) / period
    if md == 0:
        return 0.0
    return (tp[-1] - tp_ma) / (0.015 * md)


# ── KDJ 死叉检测 ──

def check_kdj_death_cross(prev_k: float, prev_d: float, cur_k: float, cur_d: float
                          ) -> Tuple[bool, float, str]:
    """KDJ 高位死叉检测。Returns: (passed, multiplier, detail)。"""
    if prev_k <= 0 or prev_d <= 0 or cur_k <= 0 or cur_d <= 0:
        return True, 1.0, "KDJ数据不可用，跳过"
    was_golden = prev_k >= prev_d
    is_dead = cur_k < cur_d
    crossed = was_golden and is_dead
    if not crossed:
        return True, 1.0, f"KDJ K({cur_k:.1f}) 无新增死叉"
    if cur_k >= 80:
        return False, 0.0, f"KDJ高位死叉: K({cur_k:.1f})≥80 → 禁止入场"
    elif cur_k >= 70:
        return True, 0.5, f"KDJ中位死叉: K({cur_k:.1f}) 70-80 → 仅试探仓"
    else:
        return True, 1.0, f"KDJ低位死叉: K({cur_k:.1f})<70 → 不显著"


# ── MACD DIF 收敛检测 ──

def check_macd_dif_converging(difs: List[float]) -> bool:
    """检查 MACD DIF 是否连续2日收敛 (DIF 绝对值缩小)。"""
    if len(difs) < 3:
        return False
    return abs(difs[-1]) < abs(difs[-2])


# ── K线形态识别 ──

def detect_patterns(bars: List[Dict]) -> Tuple[bool, str]:
    """K线顶部反转形态识别。Returns: (hard_block, detail)。"""
    if not bars or len(bars) < 2:
        return False, ""
    bar0 = bars[-1]  # 最近一日
    bar1 = bars[-2]  # 前一日
    try:
        o0, h0, l0, c0 = bar0["open"], bar0["high"], bar0["low"], bar0["close"]
        o1, h1, l1, c1 = bar1["open"], bar1["high"], bar1["low"], bar1["close"]
    except (KeyError, TypeError):
        return False, ""
    # 射击之星
    body = abs(c0 - o0)
    upper_shadow = h0 - max(o0, c0)
    lower_shadow = min(o0, c0) - l0
    if body > 0 and upper_shadow >= 2.0 * body and upper_shadow > lower_shadow * 1.5:
        return True, f"[形态] 射击之星: 上影线({upper_shadow:.2f})>=实体({body:.2f})x2"
    # 看跌吞没
    if c1 > o1 and c0 < o0 and o0 > c1 and c0 < o1:
        return True, "[形态] 看跌吞没: 阴线完全吞没阳线"
    return False, ""


# ── 量价背离检测 ──

def detect_volume_divergence(bars: List[Dict]) -> Tuple[bool, str]:
    """量价背离检测 (5日窗口)。Returns: (warning, detail)。"""
    if not bars or len(bars) < 5:
        return False, ""
    recent = bars[-5:]
    try:
        highs = [b["high"] for b in recent]
        volumes = [b["vol"] for b in recent]
    except (KeyError, TypeError):
        return False, ""
    cur_high = highs[-1]
    max_high = max(highs)
    if cur_high < max_high:
        return False, ""
    cur_vol = volumes[-1]
    max_vol = max(volumes)
    if cur_vol < max_vol:
        return True, f"量价背离: 价创新高但量({cur_vol/10000:.0f}万)<前峰({max_vol/10000:.0f}万)"
    return False, ""


# ── 超买过滤 (Layer 3) ──

def eval_overbought(rsi6: float, kdj_j: float, cci: float,
                    pattern_hard_block: bool = False,
                    divergence_warning: bool = False
                    ) -> Tuple[bool, float, bool, List[str]]:
    """Layer 3 超买过滤。Returns: (passed, multiplier, hard_block, reasons)。"""
    RSI_THRESHOLDS = [(75, 1), (85, 2), (95, 3)]
    KDJ_THRESHOLDS = [(95, 1), (105, 2), (120, 3)]
    CCI_THRESHOLDS = [(150, 1), (200, 2), (300, 3)]
    SEVERITY_MULTIPLIER = {0: 1.0, 1: 0.5, 2: 0.0, 3: 0.0}

    def _classify(value: float, thresholds: List[Tuple], name: str,
                  unit: str = "") -> Dict:
        severity = 0
        for upper, sev in thresholds:
            if value >= upper:
                severity = max(severity, sev)
        if severity == 0:
            return {"severity": 0, "detail": f"[OK] {name}({value:.0f}{unit}) 正常", "hard": False}
        if severity == 1:
            return {"severity": 1, "detail": f"[WARN] {name}({value:.0f}{unit}) 偏高→仅试探仓", "hard": False}
        if severity == 2:
            return {"severity": 2, "detail": f"[BLOCK] {name}({value:.0f}{unit}) 严重超买→禁止", "hard": False}
        lower = thresholds[-1][0]
        return {"severity": 3, "detail": f"[HARD] {name}({value:.0f}{unit})≥{lower}→硬禁止", "hard": True}

    results = [
        _classify(rsi6, RSI_THRESHOLDS, "RSI6"),
        _classify(kdj_j, KDJ_THRESHOLDS, "KDJ-J"),
        _classify(cci, CCI_THRESHOLDS, "CCI"),
    ]
    if pattern_hard_block:
        results.append({"severity": 3, "detail": "K线顶部形态", "hard": True})
    if divergence_warning:
        results.append({"severity": 1, "detail": "量价背离", "hard": False})

    severities = [r["severity"] for r in results]
    warning_count = sum(1 for s in severities if s >= 1)
    highest_sev = max(severities)
    if warning_count >= 2 and highest_sev < 3:
        highest_sev += 1

    passed = highest_sev < 2
    multiplier = SEVERITY_MULTIPLIER[highest_sev]
    hard_block = any(r["hard"] for r in results)
    reasons = [r["detail"] for r in results if r["severity"] >= 2 or r["hard"]]
    return passed, multiplier, hard_block, reasons


# ── 主入口 ──

def evaluate_entry_filters_offline(
    symbol: str,
    daily_bars: List[Dict],           # [{open, high, low, close, vol, amount, trade_date}, ...] 至少30条，升序
    moneyflow_series: List[Dict],     # [{trade_date, net_mf_amount}, ...] 至少15条，升序
) -> FilterResult:
    """离线入场三层过滤。

    Args:
        symbol: 股票代码 (如 '000001.SZ')
        daily_bars: 日K线数据，按 trade_date 升序排列，至少30条
        moneyflow_series: 资金流向数据，按 trade_date 升序排列，至少15条

    Returns:
        FilterResult: 包含通过状态、降级乘数、阻断原因等
    """
    result = FilterResult()
    if len(daily_bars) < 20:
        result.details.append("数据不足(<20条日线)，跳过过滤")
        result.final_grade = "blocked"
        result.hard_block = True
        result.hard_block_reasons.append("数据不足")
        return result

    closes = [b["close"] for b in daily_bars]
    highs = [b["high"] for b in daily_bars]
    lows = [b["low"] for b in daily_bars]
    volumes = [b.get("vol", 0) for b in daily_bars]

    # ── 计算技术指标 ──
    ma5 = compute_ma(closes, 5)
    ma20 = compute_ma(closes, 20)
    macd_dif, macd_dea, macd_bar = compute_macd(closes)
    kdj_k, kdj_d, kdj_j = compute_kdj(highs, lows, closes)
    rsi6 = compute_rsi(closes, 6)
    cci = compute_cci(highs, lows, closes)

    # ── Layer 1: 技术面 ──
    layer1_passed = True
    downgrade_multiplier = 1.0

    # 1a. MA 检查 (仅日线 MA5/MA20)
    if ma5 > 0 and ma20 > 0:
        if ma5 > ma20:
            result.details.append(f"[OK] MA5({ma5:.2f}) > MA20({ma20:.2f}) — 通过")
        else:
            result.details.append(f"[WARN] MA5({ma5:.2f}) < MA20({ma20:.2f}) — 趋势待确认")
            # 离线回测中没有板块资金流入和分时均价，直接降级
            downgrade_multiplier = min(downgrade_multiplier, 0.5)
    else:
        result.details.append("[WARN] MA5/MA20数据不可用，跳过MA检查")

    # 1b. MACD 检查
    macd_status = "金叉" if macd_dif > macd_dea else ("死叉" if macd_dif < macd_dea else "持平")
    if macd_status == "金叉":
        result.details.append(f"[OK] MACD金叉(DIF>DEA) — 通过")
    elif macd_status == "死叉":
        if check_macd_dif_converging([b.get("macd_dif", 0) for b in daily_bars[-5:]] if any("macd_dif" in b for b in daily_bars[-5:]) else [macd_dif, closes[-2] * 0.001]):
            # 简化收敛检测: 如果最近两天 DIF 绝对值缩小
            dif_vals = []
            for i in range(max(0, len(closes) - 5), len(closes)):
                d, _, _ = compute_macd(closes[:i + 1])
                dif_vals.append(abs(d))
            converging = len(dif_vals) >= 2 and dif_vals[-1] < dif_vals[-2]
            if converging:
                result.details.append("[WARN] MACD死叉但DIF连续2日收敛 → 可观察")
                downgrade_multiplier = min(downgrade_multiplier, 0.5)
            else:
                result.details.append("[WARN] MACD死叉且DIF未收敛 → 趋势转弱，降仓50%")
                downgrade_multiplier = min(downgrade_multiplier, 0.5)
        else:
            result.details.append("[WARN] MACD死叉 → 降仓50%")
            downgrade_multiplier = min(downgrade_multiplier, 0.5)

    # 1c. RSR — 跳过 (雪球专有)
    result.details.append("[SKIP] RSR检查跳过 (回测无可用的雪球数据)")

    # 1d. 日内分位 — 跳过 (无盘中数据)
    result.details.append("[SKIP] 日内分位检查跳过 (离线回测)")

    # 1e. 资金效率 — 跳过 (简化)
    result.details.append("[SKIP] 资金效率检查跳过 (离线回测)")

    # 1f. KDJ 高位死叉
    prev_k, prev_d, prev_j = compute_prev_kdj(highs, lows, closes)
    kdj_pass, kdj_mult, kdj_detail = check_kdj_death_cross(prev_k, prev_d, kdj_k, kdj_d)
    result.details.append(f"{'[OK]' if kdj_pass else '[BLOCK]'} {kdj_detail}")
    if not kdj_pass:
        layer1_passed = False
    downgrade_multiplier = min(downgrade_multiplier, kdj_mult)

    result.layer1_passed = layer1_passed

    # ── Layer 2: 主力行为 ──
    layer2_passed = True
    if moneyflow_series and len(moneyflow_series) >= 10:
        # 当日主力净流入
        today_mf = moneyflow_series[-1].get("net_mf_amount", 0) or 0
        # 5日主力净流入
        d5_mf = sum(m.get("net_mf_amount", 0) or 0 for m in moneyflow_series[-5:])
        # 10日主力净流入
        d10_mf = sum(m.get("net_mf_amount", 0) or 0 for m in moneyflow_series[-10:])

        # 2a. 5日主力检查
        if d5_mf < 0:
            result.details.append(f"[BLOCK] 5日主力({d5_mf/1e8:.2f}亿) < 0 → 排除")
            layer2_passed = False
            downgrade_multiplier = 0.0
        else:
            result.details.append(f"[OK] 5日主力({d5_mf/1e8:.2f}亿) > 0 — 通过")

        # 2b. 5日 > 10日 加速
        if layer2_passed and d5_mf > 0 and d10_mf > 0 and d5_mf > d10_mf:
            result.details.append(f"[OK] 5日主力 > 10日主力 → 加速建仓")

        # 2c. 今日出货检查
        if layer2_passed and today_mf < 0:
            result.details.append(f"[WARN] 今日主力({today_mf/1e8:.2f}亿) < 0 → 降仓50%")
            downgrade_multiplier = min(downgrade_multiplier, 0.5)

        # 2d. 10日大幅流出排除
        if layer2_passed and d10_mf < -500000000:
            accelerating = d5_mf < d10_mf * 0.5
            reversing = d5_mf > 0 and d5_mf > d10_mf
            if accelerating and not reversing:
                result.details.append(f"[BLOCK] 10日主力({d10_mf/1e8:.2f}亿) < -5亿 且加速流出 → 排除")
                layer2_passed = False
                downgrade_multiplier = 0.0
    else:
        result.details.append("[WARN] 资金流向数据不足，跳过Layer 2")

    result.layer2_passed = layer2_passed

    # ── Layer 3: 超买过滤 ──
    pattern_block, pattern_detail = detect_patterns(daily_bars)
    if pattern_detail:
        result.details.append(f"[HARD] {pattern_detail}" if pattern_block else pattern_detail)
    div_warning, div_detail = detect_volume_divergence(daily_bars)
    if div_detail:
        result.details.append(div_detail)

    l3_passed, l3_mult, l3_hard, l3_reasons = eval_overbought(
        rsi6, kdj_j, cci, pattern_block, div_warning
    )
    result.layer3_passed = l3_passed
    downgrade_multiplier = min(downgrade_multiplier, l3_mult)
    if l3_hard:
        result.hard_block = True
        result.hard_block_reasons.extend(l3_reasons)

    # ── 综合判定 ──
    all_pass = layer1_passed and layer2_passed and l3_passed
    result.passed = all_pass

    if downgrade_multiplier <= 0:
        result.final_grade = "blocked"
    elif downgrade_multiplier <= 0.5:
        result.final_grade = "probe_only"
    elif downgrade_multiplier < 1.0:
        result.final_grade = "downgraded"
    else:
        result.final_grade = "pass"

    result.downgrade_multiplier = downgrade_multiplier
    result.details.append(
        f"综合判定: {result.final_grade} (乘数={downgrade_multiplier:.2f})"
    )
    return result
