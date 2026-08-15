# -*- coding: utf-8 -*-
"""做T回测 · 回放引擎 + 快照重建 + 历史 regime + 账本 + 撮合（t-backtest 核心）。

对应 design.md D1/D4/D5/D6：
- 独立单进程 m5 tick 循环（不复用 TMonitor 线程），确定性回放
- 快照重建器与实盘 TMonitor._build_snapshot 同构，数据源换成历史缓存
- 历史 regime：L2/L3 用历史指数 m5 当日涨跌幅精确复现，L1 用指数日线 MA20/60 近似
- 撮合复刻 t_gateway.validate_order_at（状态全注入回测上下文），下一根 m5 bar close ± 滑点成交
- 账本显式建模 T+0 闭环（底仓高抛 → 低吸买回 → 次日结转）

数据流（防前视）：评估点只使用 bar_time <= tick 的历史数据；日线基准只用 trade_date < T 的数据。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.t_expr import evaluate_expression, expression_summary
from app.services.t_gateway import validate_order_at
from app.services.t_monitor import (_calc_kdj_from_bars, _calc_macd_from_closes,
                                   _calc_rsi, _sma, evaluate_condition_at)
from app.services.t_regime import compose_regime

# 成交/费用假设（默认保守口径，design.md D6）
DEFAULT_SLIPPAGE = 0.001      # 0.1% 滑点（对齐实盘 slippage_budget）
DEFAULT_FEE_RATE = 0.0005     # 单边手续费（近似）
BACKTEST_VOL_RATIO_LOOKBACK = 10   # 量比基准回看 N 个交易日
DEFAULT_INIT_SHARES = 1000    # C1 固定假设底仓（可配置）
COOLDOWN_SECONDS = 300        # 同条件去抖（对齐 t_monitor）
MAX_DAILY_TRIGGERS_PER_COND = 3  # 单条件单日触发上限（护栏）

# 量比口径说明（design.md 口径 2）：回测 = 当前 bar 量 / 近 N 日同刻均量
VOL_RATIO_CALIBER = "minute_volume_vs_same_minute_avg"


# ────────────────────────────────────────────────────────────────
# 回测账本（T+0 闭环）
# ────────────────────────────────────────────────────────────────

class TBacktestLedger:
    """单标的口径回测账本。

    T+0 语义：
    - base_shares：底仓基数（可卖来源，跨日结转）
    - sold_today：当日已卖（来自底仓）
    - bought_today：当日低吸买回（T+1，当日不可再卖，次日结转进底仓）
    - sellable = base_shares - sold_today
    """

    def __init__(self, symbol: str, init_shares: int, init_price: float,
                 net_asset: float):
        self.symbol = symbol
        self.base_shares = int(init_shares)
        self.cost_price = float(init_price)
        self.net_asset = float(net_asset)
        self.sold_today = 0
        self.bought_today = 0
        self.buy_legs_today = 0  # 当日买腿成交次数（低吸加仓上限用）
        self.realized_pnl = 0.0
        self.day_turnover = 0.0
        self.trades: List[Dict[str, Any]] = []
        # 初始现金 = 净值 - 底仓市值（成本价计）
        self.cash = self.net_asset - self.base_shares * self.cost_price
        # 成本漂移累计（买回价 ≠ 底仓成本导致）
        self.cost_drift = 0.0
        self.max_drawdown = 0.0
        self.consecutive_losses = 0
        self._peak_equity = self.net_asset

    def sellable(self) -> int:
        return max(self.base_shares - self.sold_today, 0)

    def total_shares(self) -> int:
        return self.base_shares - self.sold_today + self.bought_today

    def quote_ledger(self) -> Dict[str, Dict[str, Any]]:
        """validate_order_at 所需的 ledger 形状。"""
        return {
            self.symbol: {
                "sellable": self.sellable(),
                "volume": self.total_shares(),
                "avg_price": self.cost_price,
            }
        }

    def do_sell(self, price: float, volume: int, slippage: float = DEFAULT_SLIPPAGE,
                fee_rate: float = DEFAULT_FEE_RATE) -> Dict[str, Any]:
        """高抛卖腿：卖底仓（成本价计），已实现盈亏 = 价差 - 费用 - 滑点。"""
        vol = int(volume)
        gross = price * vol
        cost = self.cost_price * vol
        fees = gross * (fee_rate + slippage)
        realized = gross - cost - fees
        self.realized_pnl += realized
        self.sold_today += vol
        self.day_turnover += gross
        self.cash += gross - fees
        self.consecutive_losses = self.consecutive_losses + 1 if realized < 0 else 0
        trade = {
            "symbol": self.symbol, "side": "sell", "price": round(price, 3),
            "volume": vol, "realized_pnl": round(realized, 3), "fees": round(fees, 3),
        }
        self.trades.append(trade)
        return trade

    def do_buy(self, price: float, volume: int, slippage: float = DEFAULT_SLIPPAGE,
               fee_rate: float = DEFAULT_FEE_RATE) -> Dict[str, Any]:
        """低吸买腿：当日买回（T+1 不可卖），费用 + 滑点从现金扣。"""
        vol = int(volume)
        gross = price * vol
        fees = gross * (fee_rate + slippage)
        self.bought_today += vol
        self.buy_legs_today += 1
        self.day_turnover += gross
        self.cash -= gross + fees
        trade = {
            "symbol": self.symbol, "side": "buy", "price": round(price, 3),
            "volume": vol, "realized_pnl": 0.0, "fees": round(fees, 3),
        }
        self.trades.append(trade)
        return trade

    def end_of_day(self):
        """收盘结转：底仓 = 底仓 - 当日已卖 + 当日买回（买回 T+1 次日可卖），成本加权更新。"""
        sold = self.sold_today
        bought = self.bought_today
        new_base = self.base_shares - sold + bought
        if bought > 0 and new_base > 0:
            old_cost = self.cost_price
            buy_amount = sum(
                t["price"] * t["volume"] for t in self.trades
                if t["side"] == "buy" and t.get("settled") is not True
            )
            # 新成本 = (原底仓市值 + 买回成本) / 新底仓
            new_cost = (old_cost * self.base_shares + buy_amount) / new_base
            self.cost_price = round(new_cost, 4)
            self.cost_drift += (self.cost_price - old_cost)
            for t in self.trades:
                if t["side"] == "buy":
                    t["settled"] = True
        self.base_shares = max(new_base, 0)
        self.sold_today = 0
        self.bought_today = 0
        self.buy_legs_today = 0

    def equity(self, price: float) -> float:
        """按当前价估值总资产。"""
        return self.cash + self.total_shares() * price

    def update_equity_track(self, price: float):
        e = self.equity(price)
        if e > self._peak_equity:
            self._peak_equity = e
        dd = (self._peak_equity - e) / self._peak_equity * 100 if self._peak_equity > 0 else 0.0
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    def summary(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "final_shares": self.base_shares,
            "final_cost": self.cost_price,
            "cost_drift": round(self.cost_drift, 4),
            "realized_pnl": round(self.realized_pnl, 2),
            "max_drawdown_pct": round(self.max_drawdown, 2),
            "trade_count": len(self.trades),
            "cash": round(self.cash, 2),
        }


# ────────────────────────────────────────────────────────────────
# 历史 regime（design.md D5）
# ────────────────────────────────────────────────────────────────

def _index_daily_grade_from_closes(closes: List[float]) -> str:
    """L1 日频基准近似（防前视：只用截至 T-1 收盘的日线 close）。

    实盘 L1 依赖 market_diagnosis（无历史），此处用 HS300 日线近似（design.md 口径 3）。
    """
    if len(closes) < 21:
        return "ACTIVE"
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else ma20
    cur = closes[-1]
    if cur < ma60 * 0.9:  # 深度破位 → 极端
        return "HALT"
    if cur < ma20:  # 日线走弱 → 震荡/弱势
        return "CAUTIOUS"
    return "ACTIVE"


def _day_key(bar_time: Any) -> str:
    """bar time → 8 位交易日键（YYYYMMDD，与 index_daily.trade_date 对齐）。"""
    return str(bar_time)[:10].replace("-", "")


def _index_intraday_drop(index_m5_today: List[dict], prev_close: float) -> float:
    """L2/L3 输入：指数当日涨跌幅（截至当前 bar 的 close vs 昨收，精确复现）。"""
    if not index_m5_today or prev_close <= 0:
        return 0.0
    cur = float(index_m5_today[-1]["close"])
    return (cur - prev_close) / prev_close * 100


def _index_daily_drop(idx_daily: List[dict], trade_day: str, prev_close: float) -> float:
    """降级口径：指数日线收盘涨跌幅（指数分钟不可用时的 L2/L3 输入，当日收盘确认值）。"""
    today_close = 0.0
    for b in idx_daily:
        if str(b["trade_date"]) == trade_day:
            today_close = float(b["close"])
            break
    if prev_close <= 0 or today_close <= 0:
        return 0.0
    return (today_close - prev_close) / prev_close * 100


def regime_at_tick(day_bars_index: Dict[str, List[dict]], index_daily_map: Dict[str, List[dict]],
                   trade_day: str, prev_trade_day: Optional[str], now: datetime) -> Dict[str, Any]:
    """历史 regime（截至 tick，无前视）：L1 日线近似（只用 T-1 及以前）+ L2/L3 指数当日涨跌幅。

    trade_day/prev_trade_day 均为 8 位 YYYYMMDD（与 index_daily.trade_date 一致）。
    L2/L3 输入：指数 m5 可用（day_bars_index 非空）→ 盘中精确口径；
    否则（brze index_min 权限受限）→ 日线收盘口径（_index_daily_drop，caliber_notes 已标注）。
    """
    hs300 = "000300.SH"
    daily = index_daily_map.get(hs300, [])
    # L1：只用 trade_date < 当日的日线 close（防前视）
    grade_closes = [float(b["close"]) for b in daily if str(b["trade_date"]) < trade_day]
    day_grade = _index_daily_grade_from_closes(grade_closes)

    warn = False
    drop_map: Dict[str, float] = {}
    for key, ts in (("hs300", "000300.SH"), ("sh", "000001.SH"), ("sz", "399001.SZ")):
        idx_daily = index_daily_map.get(ts, [])
        prev_close = 0.0
        if prev_trade_day:
            for b in idx_daily:
                if str(b["trade_date"]) == prev_trade_day:
                    prev_close = float(b["close"])
                    break
        today_bars = [b for b in day_bars_index.get(ts, []) if _day_key(b["time"]) == trade_day]
        if today_bars:
            drop = _index_intraday_drop(today_bars, prev_close)
        else:
            drop = _index_daily_drop(idx_daily, trade_day, prev_close)
        drop_map[key] = round(drop, 2)
        if drop <= -0.8:
            warn = True

    composed = compose_regime(day_grade, warn, drop_map.get("hs300", 0.0))
    composed.update({
        "day_grade": day_grade,
        "intraday_warn": warn,
        "index_drop": drop_map.get("hs300", 0.0),
        "index_drops": drop_map,
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return composed


# ────────────────────────────────────────────────────────────────
# 快照重建器（design.md D4，与 TMonitor._build_snapshot 同构）
# ────────────────────────────────────────────────────────────────

def build_snapshot_at(symbol: str, bars_up_to: List[dict], trade_day: str,
                      regime: Dict[str, Any], ledger: TBacktestLedger,
                      vol_ratio_base: Dict[str, float],
                      pre_close: float) -> Dict[str, Any]:
    """重建回测字段快照（纯函数，只用 bar_time <= tick 的数据）。

    Args:
        bars_up_to: 截至当前 tick 的标的 m5 bars（升序）。
        trade_day: YYYYMMDD。
        regime: regime_at_tick 结果。
        ledger: 回测账本（position.*）。
        vol_ratio_base: {same_minute: 均量}（由回测引擎预计算，近 N 日同刻）。
        pre_close: 昨日收盘（标的）。
    """
    cur = bars_up_to[-1]
    open_ = float(cur["open"])
    close = float(cur["close"])
    high = float(cur["high"])
    low = float(cur["low"])
    vol = float(cur["vol"])
    amount = float(cur.get("amount", 0) or 0)
    change_pct = (close - pre_close) / pre_close * 100 if pre_close > 0 else 0.0

    # 量比（回测口径：分钟量 / 近 N 日同刻均量）
    hm = datetime.strptime(str(cur["time"]), "%Y-%m-%d %H:%M:%S")
    same_minute_key = f"{hm.hour:02d}:{hm.minute:02d}"
    base_vol = vol_ratio_base.get(same_minute_key, 0.0)
    vol_ratio = round(vol / base_vol, 3) if base_vol > 0 else 0.0

    q = {
        "current": close, "open": open_, "high": high, "low": low,
        "pre_close": pre_close, "change_pct": round(change_pct, 2),
        "turnover_rate": 0.0,  # 历史无换手率（口径差异 2）
        "amplitude": round((high - low) / pre_close * 100, 2) if pre_close > 0 else 0.0,
        "vol": vol, "amount": amount,
    }
    # 量价派生（复用实盘语义：放量/缩量/涨跌/恐慌等）
    up = change_pct > 0
    down = change_pct < 0
    expand = vol_ratio >= 1.5
    shrink = vol_ratio <= 0.7
    q.update({
        "volume_expand": expand,
        "volume_shrink": shrink,
        "price_up": up,
        "price_down": down,
        "up_with_volume": up and expand,
        "up_with_low_volume": up and shrink,
        "down_with_volume": down and expand,
        "down_with_low_volume": down and shrink,
        "panic_drop": change_pct <= -2.0 and vol_ratio >= 2.0,
        "near_day_low": (close > 0 and low > 0 and close <= low * 1.01),
        "stabilised": _stabilised_at(bars_up_to, trade_day, close),
    })

    # minute.* / tech.*（m5 重算，复用 t_monitor 纯计算）
    closes = [float(b["close"]) for b in bars_up_to]
    highs = [float(b["high"]) for b in bars_up_to]
    lows = [float(b["low"]) for b in bars_up_to]
    day_lows = [float(b["low"]) for b in bars_up_to if str(b["time"]).startswith(trade_day)]
    minute = {
        "m1": {"low_today": min(day_lows) if day_lows else 0.0,
               "last_close": close, "bounce": _stabilised_at(bars_up_to, trade_day, close)},
        "m5": {"last_close": close,
               "ma5": _sma(closes, 5), "ma10": _sma(closes, 10), "ma20": _sma(closes, 20)},
    }
    tech = {
        "ma5": _sma(closes, 5), "ma10": _sma(closes, 10),
        "ma20": _sma(closes, 20), "ma60": _sma(closes, 60),
    }
    if len(closes) >= 26:
        dif, dea, bar = _calc_macd_from_closes(closes)
        tech.update({"macd_dif": dif, "macd_dea": dea, "macd_bar": bar,
                     "macd_golden_cross": dif > dea})
    if len(closes) >= 9:
        k, d, j = _calc_kdj_from_bars(highs, lows, closes, close)
        tech.update({"kdj_k": k, "kdj_d": d, "kdj_j": j,
                     "kdj_golden_cross": k > d,
                     "kdj_overbought": j > 100 or k > 80})
    tech.update({
        "rsi_6": _calc_rsi(closes, 6), "rsi_12": _calc_rsi(closes, 12),
        "rsi_24": _calc_rsi(closes, 24),
        "rsi_overbought": _calc_rsi(closes, 6) >= 80,
        "rsi_oversold": _calc_rsi(closes, 6) <= 20,
        "above_ma5": close > tech["ma5"] > 0,
        "above_ma20": close > tech["ma20"] > 0,
    })

    return {
        "quote": q,
        "vol_ratio": vol_ratio,
        "minute": minute,
        "regime": {
            "state": regime.get("regime", "ACTIVE"),
            "gate_low_buy": regime.get("gate_low_buy", "ALLOWED"),
            "gate_high_sell": regime.get("gate_high_sell", "ALLOWED"),
            "interpret_sign": int(regime.get("interpret_sign", 1)),
        },
        "position": {
            "sellable": ledger.sellable(),
            "volume": ledger.total_shares(),
            "avg_price": ledger.cost_price,
            "pnl_pct": round((close - ledger.cost_price) / ledger.cost_price * 100, 2)
            if ledger.cost_price > 0 else 0.0,
        },
        "index": {
            "hs300_drop": float(regime.get("index_drops", {}).get("hs300", 0.0)),
            "sh_drop": float(regime.get("index_drops", {}).get("sh", 0.0)),
            "sz_drop": float(regime.get("index_drops", {}).get("sz", 0.0)),
        },
        "tech": tech,
    }


def _stabilised_at(bars_up_to: List[dict], trade_day: str, current: float) -> bool:
    """分时企稳（回测口径）：当前价未跌破当日 m1/m5 最低价（复用实盘语义）。"""
    day_lows = [float(b["low"]) for b in bars_up_to if str(b["time"]).startswith(trade_day)]
    if not day_lows:
        return True
    return current >= min(day_lows) * 0.999


def compute_vol_ratio_base(all_m5: List[dict], lookback: int = BACKTEST_VOL_RATIO_LOOKBACK) -> Dict[str, float]:
    """量比基准：近 N 个交易日的同刻分钟均量（按 bar 时间的小时:分钟）。"""
    # 按交易日分组，取最近 lookback 个交易日（排除当前评估日由回放层处理）
    by_day: Dict[str, List[dict]] = {}
    for b in all_m5:
        d = str(b["time"])[:10]
        by_day.setdefault(d, []).append(b)
    days = sorted(by_day.keys())
    base: Dict[str, List[float]] = {}
    for d in days[-lookback:]:
        for b in by_day[d]:
            hm = str(b["time"])[11:16]
            base.setdefault(hm, []).append(float(b["vol"]))
    return {k: sum(v) / len(v) for k, v in base.items()}


def compute_vol_ratio_base_up_to(all_m5: List[dict], trade_day: str,
                                 lookback: int = BACKTEST_VOL_RATIO_LOOKBACK) -> Dict[str, float]:
    """量比基准（防前视）：只用 trade_date < trade_day 的 bars，取最近 lookback 个交易日。"""
    prior = [b for b in all_m5 if _day_key(b["time"]) < trade_day]
    return compute_vol_ratio_base(prior, lookback)


# ────────────────────────────────────────────────────────────────
# 回放引擎
# ────────────────────────────────────────────────────────────────

class TBacktestEngine:
    """m5 tick 回放引擎：逐日逐 tick 重建快照 → 条件求值 → 复核 → 撮合。"""

    def __init__(self, task: Dict[str, Any], data_dir: str,
                 review_fn: Optional[callable] = None,
                 slippage: float = DEFAULT_SLIPPAGE,
                 fee_rate: float = DEFAULT_FEE_RATE):
        self.task = task
        self.data_dir = data_dir
        self.review_fn = review_fn          # 复核回调（LLM 或规则），签名 (trigger_ctx) -> {decision, reason}
        self.slippage = slippage
        self.fee_rate = fee_rate
        self.symbol = task["symbol"]
        self.init_shares = int(task.get("init_shares", DEFAULT_INIT_SHARES))
        self.init_price = float(task.get("init_price", 0) or 0)
        self.net_asset = float(task.get("net_asset", 200000.0))
        self.conditions: List[Dict[str, Any]] = task.get("conditions", [])
        # 条件无 id 时补 _bt_index（双条件生成器产出的条件无 DB id，须可区分计数）
        for _i, _c in enumerate(self.conditions):
            if not _c.get("id"):
                _c["_bt_index"] = _i + 1
        self.start_trade_day = str(task.get("start_trade_day") or "")  # 滚动建仓：从该日起回放（含）
        self.events: List[Dict[str, Any]] = []   # 触发/复核/拦截/缺口全事件流
        self._pending_buyback: Optional[Dict[str, Any]] = None  # high_sell_then_buy_back 买回挂单
        self._ai_fills: List[Dict[str, Any]] = []  # AI 决策成交记录（outcome 回填用）

    # ── 数据加载（回放期零网络）──
    def _load(self):
        from pathlib import Path
        from app.services.t_backtest_data import load_index_daily, load_m5
        d = Path(self.data_dir)
        self.m5 = load_m5(self.symbol, d)
        self.index_m5 = {
            key: load_m5(key, d) for key in ("hs300", "sh", "sz")
        }
        self.index_daily = {
            ts: load_index_daily(ts, d)
            for ts in ("000300.SH", "000001.SH", "399001.SZ")
        }
        # 交易日（8 位 YYYYMMDD，与指数日线对齐）；滚动建仓时只回放 start_trade_day 起
        self.trade_days = sorted({_day_key(b["time"]) for b in self.m5})
        if self.start_trade_day:
            self.trade_days = [d for d in self.trade_days if d >= self.start_trade_day]
        # 初始价缺省：回测首日首根 open（start_trade_day 存在时取该日）
        if not self.init_price and self.m5:
            if self.start_trade_day:
                first_bars = [b for b in self.m5 if _day_key(b["time"]) == self.start_trade_day]
                self.init_price = float(first_bars[0]["open"]) if first_bars else float(self.m5[0]["open"])
            else:
                self.init_price = float(self.m5[0]["open"])

    # ── 主循环 ──
    def run(self, cancel_event: Optional[Any] = None,
            progress_cb: Optional[callable] = None) -> Dict[str, Any]:
        """回放主循环。

        progress_cb(done_days, total_days, events_delta, equity_point)：每个交易日收盘后调用，
        供实时进度/事件流展示（events_delta = 当日新增事件，equity_point = 当日权益快照，可为 None）。
        """
        self._load()
        if not self.m5:
            return {"status": "failed", "error": "标的 m5 数据为空", "events": self.events}
        if not self.trade_days:
            return {"status": "failed", "error": "无交易日", "events": self.events}

        ledger = TBacktestLedger(self.symbol, self.init_shares, self.init_price, self.net_asset)
        equity_curve: List[Dict[str, Any]] = []
        summary: Dict[str, Any] = {
            "triggers": 0, "reviews": 0, "executed": 0, "blocked": 0,
            "escalated_human": 0, "data_gaps": 0, "ai_wait": 0, "ai_abandon": 0,
            "stop_losses": 0,
        }

        total_days = len(self.trade_days)
        for day_idx, trade_day in enumerate(self.trade_days):
            day_events_start = len(self.events)
            if cancel_event is not None and cancel_event.is_set():
                self.events.append({"type": "cancelled", "trade_day": trade_day})
                break
            prev_trade_day = self.trade_days[day_idx - 1] if day_idx > 0 else None
            day_bars = [b for b in self.m5 if _day_key(b["time"]) == trade_day]
            if not day_bars:
                summary["data_gaps"] += 1
                self.events.append({"type": "data_gap", "trade_day": trade_day, "reason": "无当日 m5"})
                ledger.end_of_day()
                continue

            # 当日量比基准（防前视：只用 < 当日数据）
            vol_base = compute_vol_ratio_base_up_to(self.m5, trade_day)
            # 标的昨收：前一日最后一根 m5 close
            pre_close = 0.0
            if prev_trade_day:
                prev_bars = [b for b in self.m5 if _day_key(b["time"]) == prev_trade_day]
                if prev_bars:
                    pre_close = float(prev_bars[-1]["close"])
            if pre_close <= 0:
                pre_close = float(day_bars[0]["open"])  # 首日：以开盘价近似昨收

            # 条件当日快照（复制，armed/冷却状态按日初始化）
            day_conds = [dict(c) for c in self.conditions]
            for c in day_conds:
                c.setdefault("armed", 1)
                c["last_triggered_at"] = None

            # 当日触发计数（单条件上限）
            day_trigger_count: Dict[int, int] = {}

            for i, bar in enumerate(day_bars):
                if cancel_event is not None and cancel_event.is_set():
                    break
                tick_dt = datetime.strptime(str(bar["time"]), "%Y-%m-%d %H:%M:%S")
                regime = regime_at_tick(self.index_m5, self.index_daily,
                                        trade_day, prev_trade_day, tick_dt)
                # 0) 先处理高抛后买回挂单（T+0 闭环买腿，走网关规则）
                self._process_buyback(day_bars, i, regime, ledger, summary)
                # 0.5) 止损检查（bar 最低价 ≤ 止损价 → 止损卖腿，冻结当日条件）
                self._process_stop_loss(day_bars, i, regime, ledger, summary,
                                        day_conds, trade_day)
                bars_up_to = day_bars[: i + 1]

                # 触发判定（复用 evaluate_condition_at：表达式 + 通用护栏 + 默认逻辑）
                hit_any = False
                for c in day_conds:
                    try:
                        snapshot = build_snapshot_at(
                            self.symbol, bars_up_to, trade_day, regime, ledger,
                            vol_base, pre_close,
                        )
                    except Exception:
                        continue
                    if self._cond_gate(c, tick_dt, day_trigger_count):
                        continue
                    try:
                        quote = {"current": float(bar["close"])}
                        ok = evaluate_condition_at(c, quote, regime, snapshot, tick_dt)
                    except Exception as e:
                        self.events.append({"type": "eval_error", "trade_day": trade_day,
                                            "bar": str(bar["time"]), "reason": str(e)[:120]})
                        ok = False
                    if ok:
                        hit_any = True
                        cid = c.get("id") or c.get("_bt_index", 0)
                        day_trigger_count[cid] = day_trigger_count.get(cid, 0) + 1
                        c["last_triggered_at"] = tick_dt
                        self._handle_trigger(c, bar, day_bars, i, snapshot, regime, ledger,
                                             trade_day, summary, cancel_event)
                        break  # 同一 tick 只处理第一个命中条件（对齐实盘轮询语义）
                if not hit_any:
                    pass

            # 收盘：结转 + 权益快照
            ledger.end_of_day()
            last_close = float(day_bars[-1]["close"])
            ledger.update_equity_track(last_close)
            equity_point = {
                "trade_date": trade_day,
                "total_asset": round(ledger.equity(last_close), 2),
                "realized_pnl": round(ledger.realized_pnl, 2),
                "position": ledger.total_shares(),
                "close": last_close,
            }
            equity_curve.append(equity_point)
            if progress_cb is not None:
                try:
                    progress_cb(day_idx + 1, total_days,
                                self.events[day_events_start:], equity_point)
                except Exception as e:
                    print(f"[t-backtest] progress_cb 异常: {e}")

        # 汇总
        ai_outcomes = self._compute_fill_outcomes()
        result = {
            "status": "completed",
            "symbol": self.symbol,
            "trade_days": len(self.trade_days),
            "window": f"{self.trade_days[0]}~{self.trade_days[-1]}",
            "ledger": ledger.summary(),
            "equity_curve": equity_curve,
            "summary": summary,
            "events": self.events,
            "ai_outcomes": ai_outcomes,
            "metrics": compute_metrics(ledger, equity_curve, summary,
                                       init_price=self.init_price, init_shares=self.init_shares,
                                       ai_outcomes=ai_outcomes),
            "caliber_notes": caliber_notes(),
        }
        return result

    def _compute_fill_outcomes(self) -> List[Dict[str, Any]]:
        """回测 outcome：对每笔 AI 成交，用该标的全量 m5 计算成交后 6 根 bar 走向（防前视）。"""
        if not self._ai_fills:
            return []
        bars_by_day: Dict[str, List[dict]] = {}
        for b in self.m5:
            bars_by_day.setdefault(_day_key(b["time"]), []).append(b)
        outcomes: List[Dict[str, Any]] = []
        for f in self._ai_fills:
            day_bars = bars_by_day.get(f["trade_day"], [])
            # 定位成交 bar（bar_time 匹配或按时间排序后第一个 ≥ fill 的 bar）
            start_idx = None
            for i, b in enumerate(day_bars):
                if str(b.get("time", "")) >= f["bar_time"]:
                    start_idx = i
                    break
            if start_idx is None or start_idx + 1 >= len(day_bars):
                continue
            # 评估窗口：成交 bar 之后 6~12 根（30-60 分钟，低吸反弹周期）
            window = day_bars[start_idx + 1: start_idx + 13]
            if len(window) < 3:
                continue
            # entry 用成交 bar 的 close（市场走向基准，不含滑点成本）
            entry = float(day_bars[start_idx].get("close") or 0) or f["fill_price"]
            exit_price = float(window[-1]["close"] or 0)
            pct = (exit_price - entry) / entry * 100 if entry else 0.0
            high = max(float(b.get("high") or 0) for b in window)
            low = min(float(b.get("low") or 0) for b in window)
            side = f["side"]
            hit_target = high >= entry * 1.01 if side == "buy" else low <= entry * 0.99
            hit_stop = low <= entry * 0.985 if side == "buy" else high >= entry * 1.015
            outcomes.append({
                "symbol": f["symbol"], "side": side, "fill_price": round(f["fill_price"], 3),
                "entry_price": round(entry, 3),
                "exit_price": round(exit_price, 3), "bars_after": len(window),
                "direction": "up" if pct >= 0 else "down", "pct_change": round(pct, 3),
                "hit_target": bool(hit_target), "hit_stop": bool(hit_stop),
                "trade_day": f["trade_day"],
            })
        return outcomes


    # ── 条件护栏（armed/冷却/单日触发上限）──
    def _cond_gate(self, cond: Dict[str, Any], now: datetime,
                   day_trigger_count: Dict[int, int]) -> bool:
        if cond.get("armed") != 1:
            return True
        if cond.get("last_triggered_at"):
            try:
                last = datetime.strptime(str(cond["last_triggered_at"]), "%Y-%m-%d %H:%M:%S")
                if (now - last).total_seconds() < COOLDOWN_SECONDS:
                    return True
            except (ValueError, TypeError):
                pass
        cid = cond.get("id") or cond.get("_bt_index", 0)
        if day_trigger_count.get(cid, 0) >= MAX_DAILY_TRIGGERS_PER_COND:
            return True
        return False

    # ── 触发 → 复核 → 撮合 ──
    def _handle_trigger(self, cond: Dict[str, Any], bar: dict, day_bars: List[dict],
                        bar_idx: int, snapshot: Dict[str, Any], regime: Dict[str, Any],
                        ledger: TBacktestLedger, trade_day: str,
                        summary: Dict[str, Any], cancel_event: Optional[Any]):
        summary["triggers"] += 1
        trigger_kind = cond.get("trigger_kind", "low_buy")
        side = "buy" if trigger_kind in ("low_buy", "panic_vibrate") else "sell"
        current = float(bar["close"])
        slippage = 0.001
        trigger = {
            "symbol": self.symbol,
            "condition_id": cond.get("id") or cond.get("_bt_index"),
            "event_type": trigger_kind,
            "trigger_price": cond.get("target_price"),
            "quote_price": current,
            "suggest_bid_price": round(current * (1 - slippage), 3),
            "suggest_ask_price": round(current * (1 + slippage), 3),
            "slippage_budget": slippage,
            "snapshot": {"quote_time": str(bar["time"]), "fields": snapshot},
            "mode": regime.get("gate_low_buy", "ALLOWED") if side == "buy"
                    else regime.get("gate_high_sell", "ALLOWED"),
            "trade_day": trade_day,
            "bar_time": str(bar["time"]),
        }
        self.events.append({"type": "trigger", "data": trigger})

        # 复核：规则模式（无 DB 纯规则）或 LLM（review_fn）→ AI 决策动作
        action, reason = self._review(trigger, regime, ledger, summary)
        self.events.append({"type": "review", "data": {
            "trigger_id": len(self.events), "action": action,
            "reason": reason, "mode": "llm" if self.review_fn else "rule",
        }})
        if action == "wait":
            summary["ai_wait"] += 1
            self.events.append({"type": "ai_wait", "data": {
                "trigger": trigger, "reason": reason,
            }})
            return
        if action == "abandon":
            summary["ai_abandon"] += 1
            summary["escalated_human"] += 1
            self.events.append({"type": "escalated", "data": {
                "trigger": trigger, "reason": reason,
            }})
            return

        # 撮合：下一根 bar close ± 滑点
        next_bar = day_bars[bar_idx + 1] if bar_idx + 1 < len(day_bars) else None
        if next_bar is None:
            self.events.append({"type": "blocked", "data": {
                "trigger": trigger, "reason": "当日无下一根 bar（收盘触发不撮合）",
            }})
            summary["blocked"] += 1
            return

        exec_price = float(next_bar["close"]) * (1 + self.slippage) if side == "buy" \
            else float(next_bar["close"]) * (1 - self.slippage)
        # 数量：低吸按可卖底仓 30%（对齐 t_bridge 默认），最小 100 股
        sellable = ledger.sellable()
        if side == "buy":
            volume = max(int(sellable * 0.3), 100) if sellable > 0 else 100
            volume = (volume // 100) * 100
        else:
            volume = max(int(sellable * 0.3), 100) if sellable > 0 else 0
            volume = (volume // 100) * 100
        if volume <= 0:
            self.events.append({"type": "blocked", "data": {
                "trigger": trigger, "reason": "可卖量不足（底仓耗尽）",
            }})
            summary["blocked"] += 1
            return

        ctx = self._gateway_ctx(regime, ledger, exec_price)
        check = validate_order_at(self.symbol, side, exec_price, volume, ctx,
                                  condition_id=trigger["condition_id"], reason="t-backtest")
        if not check["pass"]:
            self.events.append({"type": "blocked", "data": {
                "trigger": trigger, "reason": check["reason"], "level": check.get("level"),
            }})
            summary["blocked"] += 1
            return

        if side == "buy":
            trade = ledger.do_buy(exec_price, volume, self.slippage, self.fee_rate)
        else:
            trade = ledger.do_sell(exec_price, volume, self.slippage, self.fee_rate)
            # T+0 闭环：高抛成交后挂买回单（high_sell_then_buy_back 语义，复归价 = 卖价×(1-0.4%)）
            if trigger.get("event_type") == "high_sell_then_buy_back":
                self._pending_buyback = {
                    "volume": volume,
                    "limit": round(exec_price * 0.996, 3),
                    "symbol": self.symbol,
                }
        summary["executed"] += 1
        self.events.append({"type": "trade", "data": {
            "trigger": trigger, "trade": trade, "exec_price": round(exec_price, 3),
            "next_bar": str(next_bar["time"]),
        }})
        # 记录成交（供 outcome 回填：成交后 6 根 bar 走向，防前视只用成交后）
        self._ai_fills.append({
            "symbol": self.symbol, "side": side, "fill_price": exec_price,
            "trade_day": trade_day, "bar_time": str(next_bar["time"]),
        })

    def _gateway_ctx(self, regime: Dict[str, Any], ledger: TBacktestLedger,
                     quote_price: float) -> Dict[str, Any]:
        """构造 validate_order_at 所需的回测上下文（状态全注入，无 DB/网络）。"""
        return {
            "regime": regime.get("regime", "ACTIVE"),
            "quote": {"current": quote_price, "change_pct": 0.0},
            "ledger": ledger.quote_ledger(),
            "net_asset": self.net_asset,
            "daily": {"realized_pnl": ledger.realized_pnl,
                      "daily_turnover_amount": ledger.day_turnover},
            "daily_buy_legs": ledger.buy_legs_today,
            "risk": {},
            "sell_in_transit": False,
            "trigger_status": "pending",
            "cost_ratio_ok": True,
        }

    def _process_buyback(self, day_bars: List[dict], bar_idx: int,
                         regime: Dict[str, Any], ledger: TBacktestLedger,
                         summary: Dict[str, Any]):
        """处理高抛后买回挂单：当前 bar close ≤ 复归价 → 下一根成交（buy 腿，走网关）。"""
        if not self._pending_buyback:
            return
        pb = self._pending_buyback
        close = float(day_bars[bar_idx]["close"])
        if close > pb["limit"]:
            return  # 未到复归价，继续等待
        next_bar = day_bars[bar_idx + 1] if bar_idx + 1 < len(day_bars) else None
        if next_bar is None:
            return  # 当日收盘未成交，挂单作废（次日由新触发重建）
        self._pending_buyback = None  # 本挂单结束（无论成交与否）
        exec_price = float(next_bar["close"]) * (1 + self.slippage)
        volume = pb["volume"]
        ctx = self._gateway_ctx(regime, ledger, exec_price)
        check = validate_order_at(self.symbol, "buy", exec_price, volume, ctx,
                                  reason="t-backtest-buyback")
        if not check["pass"]:
            self.events.append({"type": "blocked", "data": {
                "trigger": {"event_type": "buyback"}, "reason": f"买回被网关拒绝: {check['reason']}",
            }})
            summary["blocked"] += 1
            return
        trade = ledger.do_buy(exec_price, volume, self.slippage, self.fee_rate)
        summary["executed"] += 1
        self.events.append({"type": "trade", "data": {
            "trigger": {"event_type": "buyback", "symbol": self.symbol},
            "trade": trade, "exec_price": round(exec_price, 3),
            "next_bar": str(next_bar["time"]),
        }})

    def _process_stop_loss(self, day_bars: List[dict], bar_idx: int,
                           regime: Dict[str, Any], ledger: TBacktestLedger,
                           summary: Dict[str, Any], day_conds: List[Dict[str, Any]],
                           trade_day: str):
        """止损撮合（mark-to-market）：持仓存在且 bar 最低价 ≤ 止损价 → 卖出可卖底仓止损。

        成交价取 min(bar 开盘, 止损价)（止损单按限价成交近似，跳空击穿按开盘价）；
        止损后当日该标的高抛/低吸条件冻结（armed=0），计入 realized_pnl。
        """
        if ledger.sellable() <= 0:
            return
        stop_price = None
        for c in day_conds:
            sp = float(c.get("stop_loss_price") or 0)
            if sp > 0:
                stop_price = sp
                break
        if not stop_price:
            return
        bar = day_bars[bar_idx]
        bar_low = float(bar["low"])
        if bar_low > stop_price:
            return
        # 成交价：止损限价（未跳空）或开盘价（跳空击穿）
        open_ = float(bar["open"])
        exec_price = min(open_, stop_price) if open_ > 0 else stop_price
        volume = ledger.sellable()
        volume = (volume // 100) * 100
        if volume <= 0:
            return
        trigger = {
            "symbol": self.symbol,
            "condition_id": None,
            "event_type": "stop_loss",
            "trigger_price": stop_price,
            "quote_price": bar_low,
            "mode": "ALLOWED",
            "trade_day": trade_day,
            "bar_time": str(bar["time"]),
        }
        self.events.append({"type": "trigger", "data": trigger})
        ctx = self._gateway_ctx(regime, ledger, exec_price)
        check = validate_order_at(self.symbol, "sell", exec_price, volume, ctx,
                                  reason="t-backtest-stop-loss")
        if not check["pass"]:
            self.events.append({"type": "blocked", "data": {
                "trigger": trigger, "reason": f"止损被网关拒绝: {check['reason']}",
            }})
            summary["blocked"] += 1
            return
        trade = ledger.do_sell(exec_price, volume, self.slippage, self.fee_rate)
        summary["executed"] += 1
        summary["stop_losses"] = summary.get("stop_losses", 0) + 1
        self.events.append({"type": "trade", "data": {
            "trigger": trigger, "trade": trade, "exec_price": round(exec_price, 3),
            "next_bar": str(bar["time"]), "reason": "stop_loss",
        }})
        # 止损后冻结当日条件（高抛/低吸均不再触发）
        for c in day_conds:
            c["armed"] = 0
        # 高抛挂单作废（止损离场）
        self._pending_buyback = None
        print(f"[t-backtest] 止损触发 {self.symbol} @ {exec_price} x{volume} "
              f"(bar 最低 {bar_low}) 于 {trade_day}")

    def _review(self, trigger: Dict[str, Any], regime: Dict[str, Any],
                ledger: TBacktestLedger, summary: Dict[str, Any]):
        """复核决策：LLM（review_fn）→ AI 决策动作（exec/wait/abandon）；规则（无 DB 纯规则）。

        返回 (action, reason)，action ∈ exec/wait/abandon。
        规则模式对齐 classify_escalation 语义：auto→exec、human→abandon（回测中不撮合）。
        """
        summary["reviews"] += 1
        if self.review_fn is not None:
            try:
                r = self.review_fn({
                    "trigger": trigger,
                    "regime": regime,
                    "rule_hint": _rule_review(trigger, regime, ledger),
                })
                action = str(r.get("action") or "")
                if action not in ("exec", "wait", "abandon"):
                    # 兼容旧语义：decision auto→exec、human→wait（保守）
                    action = "exec" if r.get("decision") == "auto" else "wait"
                return action, str(r.get("reason") or "LLM 决策")
            except Exception as e:
                return "wait", f"决策异常(保守等待): {str(e)[:120]}"
        action, reason = _rule_review(trigger, regime, ledger)
        return action, reason


def _rule_review(trigger: Dict[str, Any], regime: Dict[str, Any],
                 ledger: TBacktestLedger) -> tuple:
    """无 DB 规则复核（回测专用，对齐 classify_escalation 6 类语义）：
    regime 极端 / 连续触风控 / 无底仓低吸 / 接近日亏预警线 → abandon（回测中不撮合）；
    否则 exec。返回 (action, reason)，action ∈ exec/abandon。

    买卖腿区分（P0-2）：高抛卖腿是兑现离场动作，HALT/CAUTIOUS 下仍应 exec；
    只有低吸买腿在 HALT（及连续风控/日亏预警）下 abandon。
    """
    side = "buy" if trigger.get("event_type") in ("low_buy", "panic_vibrate") else "sell"
    if side == "sell":
        # 高抛/止损卖腿：兑现离场，极端市况不拦（反而应离场）；无底仓由撮合层 0 股拦截
        return "exec", "高抛兑现（卖腿）"
    if regime.get("regime") == "HALT":
        return "abandon", "regime=HALT 极端市况"
    if ledger.consecutive_losses >= 2:
        return "abandon", "连续触犯风控，强制放弃"
    if side == "buy" and ledger.sellable() <= 0:
        return "abandon", "无底仓标的低吸（新开仓风险）"
    pnl_pct = ledger.realized_pnl / ledger.net_asset * 100 if ledger.net_asset else 0.0
    if pnl_pct <= -1.0:
        return "abandon", f"接近日亏预警线（{pnl_pct:.2f}%）"
    return "exec", ""


def compute_metrics(ledger: TBacktestLedger, equity_curve: List[dict],
                    summary: Dict[str, Any], init_price: float,
                    init_shares: int, ai_outcomes: Optional[List[dict]] = None) -> Dict[str, Any]:
    """回测指标：收益/胜率/闭环率/回撤/基准对比/滑点实测 + AI 决策质量（exec 胜率）。"""
    final_asset = equity_curve[-1]["total_asset"] if equity_curve else ledger.net_asset
    total_return = (final_asset - ledger.net_asset) / ledger.net_asset * 100 if ledger.net_asset else 0.0
    trades = ledger.trades
    sell_trades = [t for t in trades if t["side"] == "sell"]
    wins = [t for t in sell_trades if t.get("realized_pnl", 0) > 0]
    losses = [t for t in sell_trades if t.get("realized_pnl", 0) < 0]
    win_rate = len(wins) / len(sell_trades) * 100 if sell_trades else 0.0
    avg_win = sum(t["realized_pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t["realized_pnl"] for t in losses) / len(losses) if losses else 0.0
    gross_win = sum(t["realized_pnl"] for t in wins)
    gross_loss = abs(sum(t["realized_pnl"] for t in losses))
    # 基准：买入持有（首日开盘价买入，末日收盘估值，纯价格收益）
    bh_return = 0.0
    if equity_curve and init_price > 0:
        last_close = equity_curve[-1].get("close", 0) or 0
        if last_close > 0:
            bh_return = (last_close - init_price) / init_price * 100

    # AI 决策质量（基于 outcome，方向归一：低吸买涨/高抛卖跌）
    ai_exec_win = ai_exec_total = 0
    ai_exec_pcts: List[float] = []
    if ai_outcomes:
        for oc in ai_outcomes:
            side = oc.get("side", "buy")
            pct = float(oc.get("pct_change") or 0)
            norm = -pct if side == "sell" else pct
            ai_exec_total += 1
            ai_exec_pcts.append(norm)
            if norm > 0:
                ai_exec_win += 1

    return {
        "total_return_pct": round(total_return, 2),
        "final_asset": round(final_asset, 2),
        "initial_asset": round(ledger.net_asset, 2),
        "win_rate_pct": round(win_rate, 2),
        "total_sell_trades": len(sell_trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "trigger_count": summary.get("triggers", 0),
        "review_count": summary.get("reviews", 0),
        "executed_count": summary.get("executed", 0),
        "blocked_count": summary.get("blocked", 0),
        "escalated_human_count": summary.get("escalated_human", 0),
        "ai_wait_count": summary.get("ai_wait", 0),
        "ai_abandon_count": summary.get("ai_abandon", 0),
        "ai_exec_win_rate_pct": round(ai_exec_win / ai_exec_total * 100, 2) if ai_exec_total else None,
        "ai_exec_count": ai_exec_total,
        "ai_exec_avg_pct": round(sum(ai_exec_pcts) / len(ai_exec_pcts), 3) if ai_exec_pcts else None,
        "stop_loss_count": summary.get("stop_losses", 0),
        "data_gap_count": summary.get("data_gaps", 0),
        "execution_rate_pct": round(summary.get("executed", 0) / summary.get("triggers", 1) * 100, 2)
        if summary.get("triggers") else 0.0,
        "agent_block_rate_pct": round(summary.get("escalated_human", 0) / summary.get("triggers", 1) * 100, 2)
        if summary.get("triggers") else 0.0,
        "max_drawdown_pct": ledger.max_drawdown,
        "buy_hold_return_pct": round(bh_return, 2),
    }


def caliber_notes() -> List[str]:
    """口径差异声明（spec：报告 MUST 显式标注）。"""
    return [
        "回放粒度: m5 bar 收盘评估（实盘为 30s 轮询，触发时刻偏差 ≤5min）",
        "量比口径: 当前 bar 分钟量 / 近N日同刻均量（实盘为换手率×时段伸缩/同刻均值，无历史换手率）",
        "regime L1: 指数日线 MA20/60 近似（实盘依赖 market_diagnosis 当日诊断，历史无此数据）",
        "regime L2/L3: 指数 m5 可用时盘中精确口径；brze index_min 权限受限时降级为指数日线收盘口径（当日收盘涨跌幅判定当日档位）",
        "成交假设: 触发后下一根 m5 close ± 0.1% 滑点（实盘为撮合引擎+滑点预算）",
        "初始底仓: 固定假设（默认 1000 股 @ 回测首日价，实盘为持仓成本）",
        "LLM 复核: 回测会话为沙盒环境，决策落库；规则模式可对照",
    ]


# ────────────────────────────────────────────────────────────────
# 组合回测引擎（多标的多日：Agent 选股建仓模拟 → 各自做T → 组合汇总）
# ────────────────────────────────────────────────────────────────

def _default_t_conditions(avg_price: float) -> List[Dict[str, Any]]:
    """建仓后自动生成做T条件（双条件：低吸 + 高抛回补）。

    阈值按波动率自适应（amp_med 缺省用下限）：高抛 = 成本×(1+max(1.5%, amp×0.6))、
    低吸 = 成本×(1−max(2.0%, amp×0.6))、止损 = 成本×(1−3%)（绑定建仓成本）。
    回测规则模式无 m5 振幅时走下限（1.5%/2.0%/3.0%）。
    """
    from app.services.t_pool import build_t_conditions
    return build_t_conditions(avg_price)


class TCombinedBacktestEngine:
    """组合回测：建仓阶段（t_build 规则历史化）→ 逐标的 TBacktestEngine → 组合权益汇总。

    数据源全部来自预取缓存（m5/指数日线/标的日线），回放期零网络。
    """

    def __init__(self, task: Dict[str, Any], data_dir: str,
                 review_fn: Optional[callable] = None,
                 slippage: float = DEFAULT_SLIPPAGE,
                 fee_rate: float = DEFAULT_FEE_RATE):
        self.task = task
        self.data_dir = data_dir
        self.review_fn = review_fn
        self.slippage = slippage
        self.fee_rate = fee_rate
        self.symbols: List[str] = task.get("symbols") or []
        self.build_mode = bool(task.get("build_mode", False))
        self.rolling_build = bool(task.get("rolling_build", False))  # 每日滚动建仓（对齐实盘 daily_auto）
        self.net_asset = float(task.get("net_asset", 200000.0))
        self.build_limit_ratio = float(task.get("build_limit_ratio", 0.55))
        self.conditions = task.get("conditions") or []
        self.start_date = str(task.get("start_date") or "")
        self.end_date = str(task.get("end_date") or "")

    def run(self, cancel_event: Optional[Any] = None,
            progress_cb: Optional[callable] = None) -> Dict[str, Any]:
        """组合回放主循环。

        progress_cb(done_units, total_units, events_delta, equity_point)：
        进度单位 = 建仓阶段每标的 1 单位 + 做T阶段每标的天数；事件增量透传子引擎当日事件。
        """
        from pathlib import Path
        from app.services.t_backtest_data import (load_index_daily, load_m5,
                                                  load_stock_daily)
        from app.services import t_build

        d = Path(self.data_dir)
        self.index_daily = {ts: load_index_daily(ts, d) for ts in ("000300.SH", "000001.SH", "399001.SZ")}
        # 交易日（从首个有数据的标的 m5 推导）
        trade_days: List[str] = []
        m5_map: Dict[str, List[dict]] = {}
        for sym in self.symbols:
            bars = load_m5(sym, d)
            m5_map[sym] = bars
            if bars:
                trade_days = sorted({_day_key(b["time"]) for b in bars}) or trade_days
        if not trade_days:
            return {"status": "failed", "error": "无标的 m5 数据", "build_decisions": [], "per_symbol": []}

        # 每日滚动建仓模式（对齐实盘 daily_auto_select/daily_auto_build）：
        # 每个交易日盘后用 as_of=当日 的日线对候选池打分 → 次日开盘建仓 → 子引擎从建仓日起回放。
        if self.rolling_build and self.build_mode:
            return self._run_rolling_build(trade_days, m5_map, cancel_event, progress_cb)

        # 进度单位：建仓阶段每标的 1 单位 + 做T阶段各标的天数（built 确定后精算，见下方）
        total_units = len(self.symbols) + sum(
            len({_day_key(b["time"]) for b in bars}) for bars in m5_map.values() if bars)
        done_units = 0

        def _report_progress(events_delta=None, equity_point=None):
            if progress_cb is not None:
                try:
                    progress_cb(done_units, total_units,
                                events_delta or [], equity_point)
                except Exception as e:
                    print(f"[t-backtest] 组合 progress_cb 异常: {e}")

        # 建仓阶段（窗口期初，T-1 及以前日线防前视）
        build_decisions: List[Dict[str, Any]] = []
        built: List[Dict[str, Any]] = []   # {symbol, price(建仓价), shares, cost, quality_score}
        allocated_value = 0.0
        for sym in self.symbols:
            done_units += 1
            _report_progress()
            if cancel_event is not None and cancel_event.is_set():
                break
            if not self.build_mode:
                # 非建仓模式：全部按固定底仓参与做T（默认 1000 股 @ 首日价）
                first_bars = [b for b in m5_map.get(sym, []) if _day_key(b["time"]) == trade_days[0]]
                price = float(first_bars[0]["open"]) if first_bars else 0.0
                shares = int(self.task.get("init_shares", DEFAULT_INIT_SHARES))
                built.append({"symbol": sym, "price": price, "shares": shares,
                              "cost": price, "source": "fixed_hold"})
                build_decisions.append({"symbol": sym, "decision": "fixed_hold",
                                        "price": price, "shares": shares, "reasons": ["非建仓模式固定底仓"]})
                continue
            # 建仓规则模拟（防前视：as_of = 首日前一交易日）
            as_of = trade_days[0]
            bars_daily = load_stock_daily(sym, d, as_of=as_of)
            if len(bars_daily) < 25:
                build_decisions.append({"symbol": sym, "decision": "rejected", "reasons": ["日线不足"]})
                continue
            daily_bars_t = [{"date": b["trade_date"], "open": b["open"], "close": b["close"],
                             "high": b["high"], "low": b["low"], "vol": b["vol"], "amount": b["amount"]}
                            for b in bars_daily]
            quality = t_build._quality_from_daily(daily_bars_t)
            r = t_build.build_score(sym, source="pool", as_of=as_of,
                                    quality_override=quality, bars=daily_bars_t)
            if not r["pass_gate"]:
                build_decisions.append({"symbol": sym, "decision": "rejected",
                                        "score": r["score"], "reasons": r["reasons"] or ["打分未达标"]})
                continue
            # 建仓价 = 窗口首日开盘价
            first_bars = [b for b in m5_map.get(sym, []) if _day_key(b["time"]) == trade_days[0]]
            price = float(first_bars[0]["open"]) if first_bars else 0.0
            if price <= 0:
                build_decisions.append({"symbol": sym, "decision": "rejected", "reasons": ["首日价不可用"]})
                continue
            # 规模（注入组合状态：净值/已分配/未持有）
            sizing = t_build.build_sizing(sym, price, net_asset=self.net_asset,
                                          total_floor_value=allocated_value,
                                          symbol_value=0.0, regime="ACTIVE")
            if not sizing["pass"]:
                build_decisions.append({"symbol": sym, "decision": "rejected",
                                        "score": r["score"], "reasons": [sizing["reason"] or "规模校验不过"]})
                continue
            shares = sizing["suggest_volume"]
            if shares <= 0:
                build_decisions.append({"symbol": sym, "decision": "rejected", "reasons": ["建议股数不足"]})
                continue
            built.append({"symbol": sym, "price": price, "shares": shares,
                          "cost": price, "source": "build_rule",
                          "score": r["score"], "trend": r["trend"]})
            allocated_value += price * shares
            build_decisions.append({"symbol": sym, "decision": "built", "price": price,
                                    "shares": shares, "score": r["score"], "reasons": r["reasons"]})

        if not built:
            empty_portfolio = _combine_metrics([], [], [], self.net_asset)
            empty_portfolio["note"] = "建仓阶段无标的达标，组合未建仓"
            return {"status": "completed", "portfolio": empty_portfolio,
                    "build_decisions": build_decisions,
                    "per_symbol": [], "equity_curve": [], "caliber_notes": caliber_notes() + [
                        "建仓口径: t_build 规则模拟（build_score≥门槛 ∧ 趋势闸门 ∧ 资金≤净值×55%），建仓价=窗口首日开盘；可T质量分用历史日线近似（_quality_from_daily）"]}

        # 精算总单位：只有实际建仓的标的进入做T阶段
        total_units = len(self.symbols) + sum(
            len({_day_key(x["time"]) for x in m5_map.get(bb["symbol"], []) if x})
            for bb in built)

        # 做T阶段：逐标的实例化单标的引擎（net_asset = 该标的建仓支出，避免组合资金重复计算）
        per_symbol: List[Dict[str, Any]] = []
        cash = self.net_asset - sum(b["price"] * b["shares"] for b in built)
        total_asset_by_day: Dict[str, float] = {}
        # 做T进度累计：建仓阶段已占 len(symbols) 单位，做T阶段每个标的占其交易日数
        done_units = len(self.symbols)
        for b in built:
            if cancel_event is not None and cancel_event.is_set():
                break
            conds = self.conditions or _default_t_conditions(b["price"])
            sub_asset = b["price"] * b["shares"]   # 该标的预算 = 建仓支出
            sub_task = {
                "symbol": b["symbol"], "init_shares": b["shares"],
                "init_price": b["price"], "net_asset": sub_asset,
                "conditions": conds,
            }
            eng = TBacktestEngine(sub_task, str(d), review_fn=self.review_fn,
                                  slippage=self.slippage, fee_rate=self.fee_rate)
            sub_days = len({_day_key(x["time"]) for x in m5_map.get(b["symbol"], []) if x})
            base_units = done_units  # 本标的起始单位（含建仓 + 之前标的已回放天数）

            def _sub_progress(done, _total, events_delta=None, _eq=None):
                # 子引擎进度映射到组合单位：base_units + 当前标的已完成天数
                frac = (done / _total) * sub_days if _total else sub_days
                cur_units = base_units + frac
                if progress_cb is not None:
                    try:
                        progress_cb(round(cur_units, 2), total_units,
                                    events_delta or [], None)
                    except Exception as e:
                        print(f"[t-backtest] 组合子引擎 progress_cb 异常: {e}")

            r = eng.run(cancel_event, progress_cb=_sub_progress if progress_cb else None)
            done_units += sub_days
            r["build"] = {k: b[k] for k in ("symbol", "price", "shares", "source") if k in b}
            per_symbol.append(r)
            # 组合权益 = 闲置现金 + Σ(建仓成本 + 子引擎盈亏累计)
            # 子引擎 net_asset=sub_asset 满仓模型，equity_curve 首日 = 建仓市值（成本基准），
            # 后续日 = 首日 + 市值/做T盈亏变化；组合以建仓成本为基准叠加，首日权益=净值。
            sub_curve = r.get("equity_curve", [])
            base_total = sub_curve[0]["total_asset"] if sub_curve else sub_asset
            for pt in sub_curve:
                day = pt["trade_date"]
                delta = pt["total_asset"] - base_total  # 相对建仓日的累计盈亏
                total_asset_by_day[day] = total_asset_by_day.get(day, cash) + sub_asset + delta

        _report_progress()

        # 组合权益曲线（按交易日升序）
        equity_curve = [{"trade_date": day, "total_asset": round(v, 2)}
                        for day, v in sorted(total_asset_by_day.items())]
        combined = _combine_metrics(per_symbol, built, equity_curve, self.net_asset)

        return {
            "status": "completed",
            "portfolio": combined,
            "build_decisions": build_decisions,
            "per_symbol": per_symbol,
            "equity_curve": equity_curve,
            "caliber_notes": caliber_notes() + [
                "建仓口径: t_build 规则模拟（build_score≥门槛 ∧ 趋势闸门 ∧ 资金≤净值×55%），建仓价=窗口首日开盘；可T质量分用历史日线近似（_quality_from_daily）",
            ],
        }


    def _run_rolling_build(self, trade_days: List[str], m5_map: Dict[str, List[dict]],
                           cancel_event: Optional[Any], progress_cb: Optional[callable]) -> Dict[str, Any]:
        """每日滚动建仓：逐日盘后打分 → 次日开盘建仓 → 各标的从建仓日起做T。

        对齐实盘节奏：daily_auto_select(盘后扫描) → daily_auto_build(次日建仓)。
        防前视：每日打分只用 as_of=当日（含）以前日线；建仓价 = 次日开盘价。
        """
        from pathlib import Path
        from app.services.t_backtest_data import load_stock_daily
        from app.services import t_build

        d = Path(self.data_dir)
        build_decisions: List[Dict[str, Any]] = []
        # 每个标的记录建仓日与成本（用于组合权益基准）
        builds: List[Dict[str, Any]] = []   # {symbol, price, shares, build_day, source}
        allocated_value = 0.0
        total_units = len(self.symbols) + sum(
            len({_day_key(b["time"]) for b in bars}) for bars in m5_map.values() if bars)
        done_units = 0

        def _report_progress(events_delta=None, equity_point=None):
            if progress_cb is not None:
                try:
                    progress_cb(done_units, total_units, events_delta or [], equity_point)
                except Exception as e:
                    print(f"[t-backtest] 滚动组合 progress_cb 异常: {e}")

        for idx, trade_day in enumerate(trade_days):
            if cancel_event is not None and cancel_event.is_set():
                break
            done_units += 1
            _report_progress()
            # 盘后（当日收盘后）对未建仓标的打分，as_of=当日（防前视）
            next_day = trade_days[idx + 1] if idx + 1 < len(trade_days) else None
            for sym in self.symbols:
                if any(b["symbol"] == sym for b in builds):
                    continue  # 已建仓
                bars_daily = load_stock_daily(sym, d, as_of=trade_day)
                if len(bars_daily) < 25:
                    build_decisions.append({"symbol": sym, "decision": "rejected",
                                            "reasons": ["日线不足"], "as_of": trade_day})
                    continue
                daily_bars_t = [{"date": b["trade_date"], "open": b["open"], "close": b["close"],
                                 "high": b["high"], "low": b["low"], "vol": b["vol"], "amount": b["amount"]}
                                for b in bars_daily]
                quality = t_build._quality_from_daily(daily_bars_t)
                r = t_build.build_score(sym, source="pool", as_of=trade_day,
                                        quality_override=quality, bars=daily_bars_t)
                if not r["pass_gate"]:
                    build_decisions.append({"symbol": sym, "decision": "rejected",
                                            "score": r["score"], "as_of": trade_day,
                                            "reasons": r["reasons"] or ["打分未达标"]})
                    continue
                # 次日建仓（无次日则窗口末建仓不参与做T——跳过）
                if next_day is None:
                    build_decisions.append({"symbol": sym, "decision": "rejected",
                                            "score": r["score"], "as_of": trade_day,
                                            "reasons": ["窗口末日达标无做T日"]})
                    continue
                next_bars = [b for b in m5_map.get(sym, []) if _day_key(b["time"]) == next_day]
                price = float(next_bars[0]["open"]) if next_bars else 0.0
                if price <= 0:
                    build_decisions.append({"symbol": sym, "decision": "rejected",
                                            "score": r["score"], "as_of": trade_day,
                                            "reasons": ["次日开盘价不可用"]})
                    continue
                # 规模（注入组合已分配资金，防止超净值55%）
                sizing = t_build.build_sizing(sym, price, net_asset=self.net_asset,
                                              total_floor_value=allocated_value,
                                              symbol_value=0.0, regime="ACTIVE")
                if not sizing["pass"]:
                    build_decisions.append({"symbol": sym, "decision": "rejected",
                                            "score": r["score"], "as_of": trade_day,
                                            "reasons": [sizing["reason"] or "规模校验不过"]})
                    continue
                shares = sizing["suggest_volume"]
                if shares <= 0:
                    continue
                builds.append({"symbol": sym, "price": price, "shares": shares,
                               "build_day": next_day, "source": "rolling_build",
                               "score": r["score"], "trend": r["trend"]})
                allocated_value += price * shares
                build_decisions.append({"symbol": sym, "decision": "built", "price": price,
                                        "shares": shares, "score": r["score"],
                                        "build_day": next_day, "reasons": r["reasons"] or []})
                print(f"[t-backtest] 滚动建仓 {sym} @ {price} x{shares} 于 {next_day}")

        # 各标的从建仓日起做T（子引擎 start_trade_day）
        per_symbol: List[Dict[str, Any]] = []
        cash = self.net_asset - sum(b["price"] * b["shares"] for b in builds)
        total_asset_by_day: Dict[str, float] = {}
        base_units = len(self.symbols)
        for b in builds:
            if cancel_event is not None and cancel_event.is_set():
                break
            conds = self.conditions or _default_t_conditions(b["price"])
            sub_asset = b["price"] * b["shares"]
            sub_task = {
                "symbol": b["symbol"], "init_shares": b["shares"],
                "init_price": b["price"], "net_asset": sub_asset,
                "conditions": conds, "start_trade_day": b["build_day"],
            }
            eng = TBacktestEngine(sub_task, str(d), review_fn=self.review_fn,
                                  slippage=self.slippage, fee_rate=self.fee_rate)
            sub_days = len({_day_key(x["time"]) for x in m5_map.get(b["symbol"], []) if x
                            and _day_key(x["time"]) >= b["build_day"]})
            done_start = base_units

            def _sub_progress(done, _total, events_delta=None, _eq=None):
                frac = (done / _total) * sub_days if _total else sub_days
                if progress_cb is not None:
                    try:
                        progress_cb(round(done_start + frac, 2), total_units,
                                    events_delta or [], None)
                    except Exception as e:
                        print(f"[t-backtest] 滚动子引擎 progress_cb 异常: {e}")

            r = eng.run(cancel_event, progress_cb=_sub_progress if progress_cb else None)
            base_units += sub_days
            r["build"] = {k: b[k] for k in ("symbol", "price", "shares", "source", "build_day") if k in b}
            per_symbol.append(r)
            # 组合权益：净值 + Σ(各标的相对建仓日盈亏)（现金→持仓不改变总权益）
            sub_curve = r.get("equity_curve", [])
            base_total = sub_curve[0]["total_asset"] if sub_curve else sub_asset
            for pt in sub_curve:
                day = pt["trade_date"]
                delta = pt["total_asset"] - base_total
                total_asset_by_day[day] = total_asset_by_day.get(day, self.net_asset) + delta

        _report_progress()
        equity_curve = [{"trade_date": day, "total_asset": round(v, 2)}
                        for day, v in sorted(total_asset_by_day.items())]
        combined = _combine_metrics(per_symbol, builds, equity_curve, self.net_asset)
        return {
            "status": "completed",
            "portfolio": combined,
            "build_decisions": build_decisions,
            "per_symbol": per_symbol,
            "equity_curve": equity_curve,
            "caliber_notes": caliber_notes() + [
                "建仓口径: 每日滚动建仓（对齐实盘 daily_auto：盘后用 as_of=当日 日线打分，"
                "次日开盘建仓；build_score≥门槛 ∧ 趋势闸门 ∧ 资金≤净值×55%），子引擎从建仓日起回放做T",
            ],
        }

def _combine_ai_exec_win_rate(per_symbol: List[Dict[str, Any]]) -> Optional[float]:
    """组合 exec 胜率：按各标的成交数加权（win_rate × count 求和 / count 求和）。"""
    total_count = sum(r.get("metrics", {}).get("ai_exec_count", 0) or 0 for r in per_symbol)
    if not total_count:
        return None
    weighted = sum(
        (r.get("metrics", {}).get("ai_exec_win_rate_pct", 0) or 0)
        * (r.get("metrics", {}).get("ai_exec_count", 0) or 0)
        for r in per_symbol
    )
    return round(weighted / total_count, 2)


def _combine_metrics(per_symbol: List[Dict[str, Any]], built: List[Dict[str, Any]],
                     equity_curve: List[dict], net_asset: float) -> Dict[str, Any]:
    """组合指标汇总：总收益/触发与成交/建仓数/胜率（合并全部成交）。"""
    total_return = 0.0
    if equity_curve:
        last = equity_curve[-1]["total_asset"]
        total_return = (last - net_asset) / net_asset * 100 if net_asset else 0.0
    triggers = sum(r.get("metrics", {}).get("trigger_count", 0) for r in per_symbol)
    executed = sum(r.get("metrics", {}).get("executed_count", 0) for r in per_symbol)
    blocked = sum(r.get("metrics", {}).get("blocked_count", 0) for r in per_symbol)
    escalated = sum(r.get("metrics", {}).get("escalated_human_count", 0) for r in per_symbol)
    ai_wait = sum(r.get("metrics", {}).get("ai_wait_count", 0) for r in per_symbol)
    ai_abandon = sum(r.get("metrics", {}).get("ai_abandon_count", 0) for r in per_symbol)
    realized = sum(r.get("ledger", {}).get("realized_pnl", 0) for r in per_symbol)
    sells = [t for r in per_symbol for t in r.get("ledger", {}).get("trades", []) if t.get("side") == "sell"]
    wins = [t for t in sells if t.get("realized_pnl", 0) > 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0.0
    # 组合最大回撤：基于组合权益曲线（非单标的最大值——单标的深跌不代表组合）
    max_dd = 0.0
    if equity_curve:
        peak = equity_curve[0]["total_asset"]
        for p in equity_curve:
            v = p["total_asset"]
            if v > peak:
                peak = v
            if peak > 0:
                dd = (peak - v) / peak * 100
                if dd > max_dd:
                    max_dd = dd
    max_dd = round(max_dd, 2)
    return {
        "symbols": len(built),
        "built_count": len(built),
        "initial_asset": round(net_asset, 2),
        "final_asset": round(equity_curve[-1]["total_asset"], 2) if equity_curve else round(net_asset, 2),
        "total_return_pct": round(total_return, 2),
        "trigger_count": triggers,
        "executed_count": executed,
        "blocked_count": blocked,
        "escalated_human_count": escalated,
        "ai_wait_count": ai_wait,
        "ai_abandon_count": ai_abandon,
        "ai_exec_count": sum(r.get("metrics", {}).get("ai_exec_count", 0) or 0 for r in per_symbol),
        "ai_exec_win_rate_pct": _combine_ai_exec_win_rate(per_symbol),
        "realized_pnl": round(realized, 2),
        "win_rate_pct": round(win_rate, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "per_symbol_return": {r.get("symbol", "?"): round(r.get("metrics", {}).get("total_return_pct", 0), 2)
                              for r in per_symbol},
    }
