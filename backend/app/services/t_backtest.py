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
import json
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
        self.day_realized_pnl = 0.0  # 当日已实现盈亏（日亏损熔断用，收盘重置）
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
        self.day_realized_pnl += realized
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
        """收盘结转：底仓 = 底仓 - 当日已卖 + 当日买回（买回 T+1 次日可卖），成本加权更新。

        成本加权口径：当日卖出的部分已按卖出价实现盈亏（do_sell 已计 realized_pnl），
        不再参与成本结转——新成本 = (剩余底仓市值 + 买回成本) / 新底仓股数。
        此前公式用 base_shares（含已卖）作分子导致卖出日成本虚高（如 200股@37.03
        高抛卖100@39.4买回100@38.6 后成本被算成 56.35 而非 ~37.8）。
        """
        sold = self.sold_today
        bought = self.bought_today
        new_base = self.base_shares - sold + bought
        if bought > 0 and new_base > 0:
            old_cost = self.cost_price
            buy_amount = sum(
                t["price"] * t["volume"] for t in self.trades
                if t["side"] == "buy" and t.get("settled") is not True
            )
            # 新成本 = (剩余底仓市值 + 买回成本) / 新底仓（已卖部分已实现盈亏，不结转）
            new_cost = (old_cost * max(self.base_shares - sold, 0) + buy_amount) / new_base
            self.cost_price = round(new_cost, 4)
            self.cost_drift += (self.cost_price - old_cost)
            for t in self.trades:
                if t["side"] == "buy":
                    t["settled"] = True
        self.base_shares = max(new_base, 0)
        self.sold_today = 0
        self.bought_today = 0
        self.buy_legs_today = 0
        self.day_realized_pnl = 0.0

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
        self._stopped_out: bool = False            # 止损离场标志（止损后不再触发做T）
        # 消费式条件自动重建（迭代#56b）：单日重建上限防无限生成
        self._daily_rebuilds: int = 0
        self._max_daily_rebuilds: int = int(task.get("max_daily_condition_rebuilds", 8))
        self._summary: Optional[Dict[str, Any]] = None

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
            "stop_losses": 0, "ai_condition_updates": 0, "condition_rebuilds": 0,
        }
        self._summary = summary  # 消费式重建计数引用（_maybe_rebuild_conditions 用）

        total_days = len(self.trade_days)
        for day_idx, trade_day in enumerate(self.trade_days):
            day_events_start = len(self.events)
            if cancel_event is not None and cancel_event.is_set():
                self.events.append({"type": "cancelled", "trade_day": trade_day})
                break
            prev_trade_day = self.trade_days[day_idx - 1] if day_idx > 0 else None
            # 消费式条件重建计数按日重置（迭代#56b）
            self._daily_rebuilds = 0
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

            # 止损离场后：该标的后续交易日不再做T（#36 迭代：避免多次止损反复离场）
            if self._stopped_out:
                ledger.end_of_day()
                last_close = float(day_bars[-1]["close"])
                ledger.update_equity_track(last_close)
                equity_curve.append({
                    "trade_date": trade_day,
                    "total_asset": round(ledger.equity(last_close), 2),
                    "realized_pnl": round(ledger.realized_pnl, 2),
                    "position": ledger.total_shares(),
                    "close": last_close,
                })
                continue

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

                # 0.7) 消费式条件重建判定移到 _maybe_rebuild_conditions（仅 exec 后调用）
                # 触发判定（复用 evaluate_condition_at：表达式 + 通用护栏 + 默认逻辑）
                hit_any = False
                for c in day_conds:
                    tkind = c.get("trigger_kind", "low_buy")
                    # 无底仓预拦截：做T条件必须依托底仓——
                    #   卖腿：sellable=0 不评估（T+0 当日买回次日才可卖，sellable 会递减）
                    #   买腿：total_shares=0 不评估（低吸=有底仓加仓，无持仓属新开仓走建仓流程），
                    #         避免"持仓0股无弹药"反复唤醒 AI 放弃刷屏。
                    #   自由跑（T_BUY_TIER_LIMIT_ENABLED=0）：买腿放行——卖出清仓后
                    #   允许重新建仓再 T（用户#49：低吸 4 次触发全被"无底仓"预拦截吃掉）
                    free_run = _free_run_enabled()
                    if tkind in ("high_sell_then_buy_back", "high_sell"):
                        if ledger.sellable() <= 0:
                            continue
                    elif tkind in ("low_buy", "panic_vibrate"):
                        if ledger.total_shares() <= 0 and not free_run:
                            continue
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
                        action = self._handle_trigger(
                            c, bar, day_bars, i, snapshot, regime, ledger,
                            trade_day, summary, cancel_event, day_conds)
                        # 消费式条件（迭代#56，用户需求）：触发后条件即销毁——
                        # 从当日剩余 bar 的评估中移除，不再冷却复用。
                        # 仅 exec（成交）后自动重建新条件（AI 重新评估）；
                        # wait/abandon/update_condition 不重建：
                        #   wait=存疑不追（重建会立刻再触发形成循环）、
                        #   abandon=放弃本标的、update_condition=AI 已提供新条件
                        try:
                            day_conds.remove(c)
                        except ValueError:
                            pass
                        if action == "exec":
                            self._maybe_rebuild_conditions(
                                day_conds, ledger, trade_day, tick_dt,
                                quote_price=float(bar["close"]),
                                consumed_kind=tkind)
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
                        summary: Dict[str, Any], cancel_event: Optional[Any],
                        day_conds: Optional[List[Dict[str, Any]]] = None) -> str:
        """返回决策动作：exec/wait/abandon/update_condition/blocked/closed（消费式重建判定用）。"""
        summary["triggers"] += 1
        trigger_kind = cond.get("trigger_kind", "low_buy")
        side = "buy" if trigger_kind in ("low_buy", "panic_vibrate") else "sell"
        # 卖腿无底仓短路：高抛/止损触达但可卖底仓为 0 → 直接 blocked（不唤醒 AI，
        # 避免 LLM 反复"无券可卖"放弃刷屏；规则模式同样短路）
        if side == "sell" and ledger.sellable() <= 0:
            summary["blocked"] += 1
            self.events.append({"type": "blocked", "data": {
                "trigger": {"symbol": self.symbol, "event_type": trigger_kind,
                            "trigger_price": cond.get("target_price"),
                            "quote_price": float(bar["close"]),
                            "trade_day": trade_day, "bar_time": str(bar["time"])},
                "reason": "无可用底仓（可卖 0 股），卖腿跳过",
            }})
            return "blocked"
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

        # 自动执行闭环（迭代#57，用户需求）：触发后不再逐次等 AI 决策——
        # 止损/止盈自动卖出、低吸自动买入（volume 优先用 AI 在条件里设定的股数），
        # 仍走网关风控（日亏熔断/无底仓/档位等硬校验）。
        # 执行完成后由 _maybe_rebuild_conditions 报告 AI 重建新条件（移动基准）。
        # 规则风控前置（自动执行也必须过 _rule_review 的 HALT/连亏/日亏预警拦截）
        rule_action, rule_reason = _rule_review(trigger, regime, ledger)
        if rule_action != "exec":
            summary["ai_abandon"] += 1
            summary["escalated_human"] += 1
            self.events.append({"type": "escalated", "data": {
                "trigger": trigger, "reason": f"自动执行被规则风控拦截: {rule_reason}",
            }})
            return "abandon"
        action = "auto_exec"  # 标记：自动执行（非 AI 逐次决策）
        self.events.append({"type": "review", "data": {
            "trigger_id": len(self.events), "action": action,
            "reason": "条件命中自动执行（AI 设定条件含股数）",
            "mode": "llm" if self.review_fn else "rule",
        }})

        # 撮合：下一根 bar close ± 滑点
        next_bar = day_bars[bar_idx + 1] if bar_idx + 1 < len(day_bars) else None
        if next_bar is None:
            self.events.append({"type": "blocked", "data": {
                "trigger": trigger, "reason": "当日无下一根 bar（收盘触发不撮合）",
            }})
            summary["blocked"] += 1
            return "closed"

        exec_price = float(next_bar["close"]) * (1 + self.slippage) if side == "buy" \
            else float(next_bar["close"]) * (1 - self.slippage)
        # 数量：优先用 AI 在条件里设定的 volume（股数，100 的整数倍）；
        # 未设定时回退规则：低吸按可卖底仓 30%（min 100），高抛 30%（保留底仓 100）
        sellable = ledger.sellable()
        cond_vol = int(cond.get("volume") or 0)
        if cond_vol > 0:
            volume = (cond_vol // 100) * 100
            # 卖腿不超过可卖量
            if side == "sell" and volume > sellable:
                volume = (sellable // 100) * 100
        elif side == "buy":
            if sellable > 0:
                volume = max(int(sellable * 0.3), 100)
            else:
                # 自由跑：卖出清仓后低吸 = 重新建仓（min 100 股，A股最小单位）
                volume = 100
            volume = (volume // 100) * 100
        else:
            # 高抛卖量 ≤ 可卖底仓 30%（min 100 股），且底仓充足(>200股)时保留至少 100 股
            # （迭代#38：防卖飞踏空；小底仓仍可全卖做T）
            max_sell = sellable
            if sellable > 200:
                max_sell = max(sellable - 100, 0)
            volume = max(int(sellable * 0.3), 100) if sellable > 0 else 0
            volume = min(volume, max_sell)
            volume = (volume // 100) * 100
        if volume <= 0:
            self.events.append({"type": "blocked", "data": {
                "trigger": trigger, "reason": "可卖量不足（底仓耗尽）",
            }})
            summary["blocked"] += 1
            return "blocked"

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
            # T+0 闭环：高抛成交后挂买回单（复归价 = 卖价×(1-0.2%)，迭代#38：
            # 0.996→0.998 放宽回补价，减少上涨趋势中卖飞踏空）
            if trigger.get("event_type") == "high_sell_then_buy_back":
                self._pending_buyback = {
                    "volume": volume,
                    "limit": round(exec_price * 0.998, 3),
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
        return "exec"

    def _maybe_rebuild_conditions(self, day_conds: List[Dict[str, Any]],
                                  ledger: TBacktestLedger, trade_day: str,
                                  tick_dt: datetime, quote_price: Optional[float] = None,
                                  consumed_kind: str = ""):
        """消费式条件自动重建（迭代#56b）：exec 成交后立即重建该类型条件——
        用户语义"触发即销毁，AI 重新评估触发条件"：每条条件消费后都重新生成，
        让做T连续进行（#61 归因：此前要求 day_conds 全空才重建，双条件只消费
        一条时另一条仍在（价格够不着）→ 该标的当天做T停止）。

        【移动条件】重建基准 = 现价（quote_price）而非成本（迭代#56c，用户观点：
        止损/止盈条件不可能连续相同——第一次 1.00 止损跌破后，下次止损必须随
        现价移动（如 0.95 附近），否则重建相同条件会立即再触发形成循环）。
        quote_price 缺失时回退成本基准。

        仅 exec 后调用——wait/abandon/update_condition 不触发重建。
        防无限重建：单日重建次数上限（_max_daily_rebuilds，默认 8）。
        """
        if self._stopped_out or ledger.total_shares() <= 0:
            return
        if self._daily_rebuilds >= self._max_daily_rebuilds:
            return
        try:
            amp_med = _amp_median_from_m5(self.m5)
            # 移动基准：现价（重建时价格已变，条件随行情移动）；无现价回退成本
            base_price = quote_price if quote_price and quote_price > 0 else ledger.cost_price
            # 重建上下文（迭代#57c）：告诉 AI 刚触发过什么价位的什么条件，
            # 让它设"上次触发价有利侧"的移动条件（高抛在上次触发价上方、
            # 低吸在下方），避免 AI 因不知道历史而重复设同价
            rebuild_ctx = {
                "last_trigger_kind": consumed_kind,
                "last_trigger_price": quote_price,
            }
            new_conds = _gen_t_conditions(
                self.review_fn, self.symbol, base_price,
                amp_med=amp_med, task_id=self.task.get("id"), use_cache=False,
                quote_price=quote_price, rebuild_ctx=rebuild_ctx)
            if new_conds:
                # 只重建被消费的类型（consumed_kind），避免未消费类型被重复重建
                # （如高抛消费后只重建高抛，低吸条件若还在则不动）
                if consumed_kind:
                    new_conds = [c for c in new_conds
                                 if c.get("trigger_kind") == consumed_kind]
                # 移动条件保护（迭代#57b，用户观点：止损止盈不能连续相同——
                # 000767 5/22 同一价位 4.865 连卖 4 次 = AI 重建条件按成本算
                # 触发价 ≤ 现价 → 立即再触发。强制：重建条件触发价必须在
                # 现价"有利侧"（高抛 > 现价、低吸 < 现价、止损 < 现价），
                # 否则丢弃（等价格移动后再触发，避免同价反复循环）
                if quote_price and quote_price > 0:
                    filtered = []
                    for nc in new_conds:
                        k = nc.get("trigger_kind", "")
                        tp = float(nc.get("target_price") or 0)
                        sp = float(nc.get("stop_loss_price") or 0)
                        if k in ("high_sell_then_buy_back", "high_sell"):
                            if tp > quote_price and (sp <= 0 or sp < quote_price):
                                filtered.append(nc)
                        elif k in ("low_buy", "panic_vibrate"):
                            if tp < quote_price:
                                filtered.append(nc)
                        else:
                            filtered.append(nc)
                    new_conds = filtered
                if not new_conds:
                    # 现价未到有利侧（如刚触发后现价仍高于新高抛目标）→ 本轮回合跳过，
                    # 条件不武装，等价格进一步移动（单日上限仍计数，防死循环）
                    return
                self._daily_rebuilds += 1
                for nc in new_conds:
                    nc = dict(nc)
                    nc.setdefault("armed", 1)
                    nc["last_triggered_at"] = None
                    day_conds.append(nc)
                self.events.append({"type": "condition_rebuild", "data": {
                    "trade_day": trade_day, "bar_time": str(tick_dt),
                    "count": len(new_conds), "consumed_kind": consumed_kind,
                    "reason": "消费式条件触发后自动重建（AI 重新评估，移动基准）",
                }})
                if self._summary is not None:
                    self._summary["condition_rebuilds"] = \
                        self._summary.get("condition_rebuilds", 0) + 1
        except Exception as e:
            print(f"[t-backtest] 条件自动重建失败 {self.symbol}: {e}")

    def _gateway_ctx(self, regime: Dict[str, Any], ledger: TBacktestLedger,
                     quote_price: float) -> Dict[str, Any]:
        """构造 validate_order_at 所需的回测上下文（状态全注入，无 DB/网络）。"""
        return {
            "regime": regime.get("regime", "ACTIVE"),
            "quote": {"current": quote_price, "change_pct": 0.0},
            "ledger": ledger.quote_ledger(),
            "net_asset": self.net_asset,
            "daily": {"realized_pnl": ledger.day_realized_pnl,
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
        # 卖量：减半仓（对齐 P0-3 spec：-3% 减半，保留底仓继续做T；全卖会
        # 导致后续高抛天天触发但无券可卖，AI 反复"无底仓"放弃刷屏）
        sellable = ledger.sellable()
        half = (sellable // 2 // 100) * 100
        volume = half if half >= 100 else (sellable // 100) * 100
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
                                  reason="t-backtest-stop-loss", is_stop_loss=True)
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
        # 止损后冻结当日条件（高抛/低吸均不再触发）+ 标记止损离场（后续交易日不再触发，
        # 避免多次止损反复离场——#36 迭代：000063/000034 止损 3 次拖累）
        for c in day_conds:
            c["armed"] = 0
        self._stopped_out = True
        # 高抛挂单作废（止损离场）
        self._pending_buyback = None
        print(f"[t-backtest] 止损触发 {self.symbol} @ {exec_price} x{volume} "
              f"(bar 最低 {bar_low}) 于 {trade_day}")

    def _review(self, trigger: Dict[str, Any], regime: Dict[str, Any],
                ledger: TBacktestLedger, summary: Dict[str, Any]):
        """复核决策：LLM（review_fn）→ AI 决策动作（exec/wait/abandon/update_condition）；
        规则（无 DB 纯规则）。返回 (action, reason, cond_update)，
        action ∈ exec/wait/abandon/update_condition，cond_update 仅 update_condition 时非空。
        规则模式对齐 classify_escalation 语义：auto→exec、human→abandon（回测中不撮合）。
        """
        summary["reviews"] += 1
        if self.review_fn is not None:
            try:
                # 注入回测账本实时持仓（LLM 决策依据：可卖底仓/持仓量/成本——
                # 此前 AI 只能看 DB 真实账户（回测沙盒为空）→ 高抛"无底仓可卖"误放弃）
                position = {
                    "symbol": self.symbol,
                    "sellable": ledger.sellable(),
                    "volume": ledger.total_shares(),
                    "avg_price": ledger.cost_price,
                    "realized_pnl": round(ledger.realized_pnl, 2),
                    "day_turnover": round(ledger.day_turnover, 2),
                }
                r = self.review_fn({
                    "trigger": trigger,
                    "regime": regime,
                    "rule_hint": _rule_review(trigger, regime, ledger),
                    "position": position,
                })
                action = str(r.get("action") or "")
                cond_update: Optional[Dict[str, Any]] = None
                if action == "update_condition":
                    cond_update = r.get("condition") if isinstance(r.get("condition"), dict) else None
                    if not cond_update:
                        return "wait", "update_condition 缺 condition（保守等待）", None
                elif action not in ("exec", "wait", "abandon"):
                    # 兼容旧语义：decision auto→exec、human→wait（保守）
                    action = "exec" if r.get("decision") == "auto" else "wait"
                return action, str(r.get("reason") or "LLM 决策"), cond_update
            except Exception as e:
                # 迭代#56c：LLM 复核异常（超时/网络）→ 回退规则决策而非保守 wait——
                # _rule_review 区分买卖腿：高抛 exec 兑现（不丢利润）、低吸按风控判断。
                # 此前一律 wait 导致"决策异常(保守等待): timed out"下高抛连续被跳过
                # （#62：000021 三天高抛全 wait，+11.8% 缩水）
                rule_action, rule_reason = _rule_review(trigger, regime, ledger)
                return rule_action, f"LLM 决策异常，规则兜底({rule_action}): {str(e)[:80]}", None
        action, reason = _rule_review(trigger, regime, ledger)
        return action, reason, None

    def _apply_condition_update(self, cond_update: Dict[str, Any],
                                day_conds: Optional[List[Dict[str, Any]]] = None):
        """AI update_condition 落地：消费式语义（迭代#56，用户需求）——
        原条件触发后已消费，AI 给出的新条件 = **重建**一条新条件（新增到
        day_conds 与 self.conditions，带新 _bt_index），当日剩余 bar 与
        后续交易日生效；不再原地 patch 已消费条件。"""
        kind = str(cond_update.get("trigger_kind") or "")
        if not kind:
            return
        # 允许更新的字段白名单（防 AI 写入无意义字段）
        updatable = ("target_price", "sell_target_price", "stop_loss_price",
                     "vol_ratio_thresh", "stabilize_level", "time_window")
        patch = {k: v for k, v in cond_update.items()
                 if k in updatable and v is not None and v != ""}
        if not patch:
            return
        # 重建新条件（沿用原条件模板 + AI patch；id 置 None → 引擎补新 _bt_index）
        template = None
        for c in list(self.conditions) + list(day_conds or []):
            if c.get("trigger_kind") == kind:
                template = c
                break
        new_cond = dict(template or {})
        new_cond.pop("id", None)
        new_cond.pop("_bt_index", None)
        new_cond.pop("last_triggered_at", None)
        for k, v in patch.items():
            new_cond[k] = v
        new_cond["armed"] = 1
        # 注入 day_conds（当日剩余 bar 生效）与 self.conditions（后续交易日生效）
        if day_conds is not None:
            day_conds.append(new_cond)
        self.conditions.append(new_cond)
        # 补 _bt_index（引擎按 id 缺失时用 _bt_index 区分计数）
        if not new_cond.get("id"):
            new_cond["_bt_index"] = max(
                [int(c.get("_bt_index") or 0) for c in self.conditions] + [0]) + 1
        print(f"[t-backtest] AI 重建条件 {self.symbol} {kind}: "
              f"{json.dumps(patch, ensure_ascii=False)}")


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
    # 无底仓标的低吸：默认放弃（新开仓风险）；自由跑时放行——卖出清仓后可重新建仓再 T
    if side == "buy" and ledger.sellable() <= 0 and not _free_run_enabled():
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
        "ai_condition_update_count": summary.get("ai_condition_updates", 0),
        "condition_rebuild_count": summary.get("condition_rebuilds", 0),
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
        "可T质量口径: 与生产 calc_t_quality 同一打分公式与硬门槛（振幅3~10/价差>0.5/成交额≥5亿）；回测用 as_of 日线近似输入（振幅=近6日日线振幅中位、OC=日线口径、往返度=0、换手率未知跳过），生产用实时 m5/m1",
        "regime L1: 指数日线 MA20/60 近似（实盘依赖 market_diagnosis 当日诊断，历史无此数据）",
        "regime L2/L3: 指数 m5 可用时盘中精确口径；brze index_min 权限受限时降级为指数日线收盘口径（当日收盘涨跌幅判定当日档位）",
        "成交假设: 触发后下一根 m5 close ± 0.1% 滑点（实盘为撮合引擎+滑点预算）",
        "初始底仓: 固定假设（默认 1000 股 @ 回测首日价，实盘为持仓成本）",
        "LLM 复核: 回测会话为沙盒环境，决策落库；规则模式可对照",
    ]


# ────────────────────────────────────────────────────────────────
# 组合回测引擎（多标的多日：Agent 选股建仓模拟 → 各自做T → 组合汇总）
# ────────────────────────────────────────────────────────────────

def _amp_median_from_m5(m5: List[dict]) -> Optional[float]:
    """从预取 m5 计算标的近 6 日日内振幅中位（%），供动态条件/止损使用。"""
    try:
        from app.services.t_pool import _calc_daily_amplitudes, _median
        if not m5:
            return None
        amps = _calc_daily_amplitudes(m5)
        # 近 6 日（m5 缓存按时间升序，取最后 6 个交易日）
        by_day: Dict[str, List[dict]] = {}
        for b in m5:
            by_day.setdefault(_day_key(b["time"]), []).append(b)
        days = sorted(by_day.keys())[-6:]
        recent = [b for b in m5 if _day_key(b["time"]) in days]
        amps = _calc_daily_amplitudes(recent)
        return _median(amps) if amps else None
    except Exception:
        return None


def _free_run_enabled() -> bool:
    """AI 自由跑模式（与实盘网关开关同源）：档位上限+回转额上限均关闭时为自由跑。

    自由跑语义：卖出清仓后允许重新建仓再 T（低吸买腿不再要求有底仓）、
    买腿不设档位上限、日回转额不限（AI 自主决策，网关只做硬风控兜底）。
    """
    try:
        import app.services.t_gateway as gw
        return not gw.T_BUY_TIER_LIMIT_ENABLED and not gw.T_TURNOVER_LIMIT_ENABLED
    except Exception:
        return False


def _default_t_conditions(avg_price: float, amp_med: Optional[float] = None) -> List[Dict[str, Any]]:
    """建仓后自动生成做T条件（双条件：低吸 + 高抛回补）。

    阈值按波动率自适应：高抛 = 成本×(1+max(1.5%, amp×0.6))、
    低吸 = 成本×(1−max(2.0%, amp×0.6))、止损 = 成本×(1−max(3%, amp×0.40))
    （动态止损对齐 marcus stop_loss_monitor 振幅自适应口径）。
    amp_med 缺省时用下限（1.5%/2.0%/3.0%）。
    """
    from app.services.t_pool import build_t_conditions
    return build_t_conditions(avg_price, amp_med)


def _gen_t_conditions(review_fn: Optional[callable], symbol: str, price: float,
                      amp_med: Optional[float] = None,
                      task_id: Optional[Any] = None,
                      use_cache: bool = True,
                      quote_price: Optional[float] = None,
                      rebuild_ctx: Optional[dict] = None) -> List[Dict[str, Any]]:
    """回测建仓条件生成：LLM 模式（review_fn 存在）→ AI 自主设定条件（bridge /conditions/generate，
    带缓存）；桥不可达/解析失败回退规则公式 _default_t_conditions。规则模式直接用公式。

    会话隔离（迭代#54b）：session = t-backtest-conds-{task_id}-{symbol}——
    ① 按标的隔离（此前全部标的共用 t-backtest-conds → bridge 复读首标条件）；
    ② 按任务隔离（防跨回测任务 resume 旧会话：同一 symbol 多次回测共享
    conditions:t-backtest-conds-{symbol} 会恢复上一次任务的对话历史，干扰条件设定）。
    生产 auto_gen_conditions_for_build 用 t-agent-{symbol}（trade 模式，与决策会话同源，
    条件设定+决策本来就是一个连续会话，合理）。

    止损钳制（迭代#52：#51 报告 AI 把止损放宽到 -6%/-4.8%，坏标的扛单多亏一倍）：
    AI 生成的 stop_loss_price 不得低于规则值 price×(1−max(3%, amp×0.55))——
    AI 可收紧止损，不可放宽（止损下限由系统兜底，防 AI 过度乐观扛单）。
    """
    if review_fn is not None:
        try:
            from app.services.t_bridge import generate_conditions
            session_id = f"t-backtest-conds-{task_id or 0}-{symbol}"
            # 消费式重建（迭代#56b）：use_cache=False 强制 AI 重新评估，
            # 避免缓存命中返回相同条件导致"消费→重建相同→再触发"死循环
            res = generate_conditions(symbol, price, amp_med=amp_med,
                                      session_id=session_id, use_cache=use_cache,
                                      quote_price=quote_price,
                                      rebuild_ctx=rebuild_ctx)
            if res and res.get("conditions"):
                conds = res["conditions"]
                # 止损钳制（迭代#52 下限 + 迭代#56c 上限）：
                #   - 不得低于规则值（更低=更宽，取 max）
                #   - **必须低于成本 99%**（AI 可能把现价误当成本基准——
                #     #61 中 000636 止损 60.07 > 成本 29.6 → 每根 bar 触发止损连卖）
                #     → 高于成本 99% 直接回退规则值
                rule_stop = round(price * (1 - max(0.03, (amp_med or 3.0) / 100 * 0.55)), 2)
                cost_cap = round(price * 0.99, 2)
                for c in conds:
                    sp = c.get("stop_loss_price")
                    if not sp:
                        c["stop_loss_price"] = rule_stop
                    else:
                        stop = round(max(float(sp), rule_stop), 2)
                        if stop > cost_cap:
                            stop = rule_stop
                        c["stop_loss_price"] = stop
                print(f"[t-backtest] AI 条件生成 {symbol}（{res.get('source')}）"
                      f" 止损钳制[{rule_stop}, {cost_cap}]")
                return conds
        except Exception as e:
            print(f"[t-backtest] AI 条件生成失败 {symbol}: {e}（回退规则公式）")
    return _default_t_conditions(price, amp_med)


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
        self.rolling_scan = bool(task.get("rolling_scan", False))    # 滚动建仓+全市场历史扫描补充
        self.relax_mode = bool(task.get("relax_mode", False))       # 震荡市模式（仅回测）：放宽趋势闸门+门槛
        self._all_symbols: Optional[List[str]] = task.get("_all_symbols") or None
        self._scan_pool: Optional[List[str]] = task.get("_scan_pool") or None
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
        # 交易日：优先从标的 m5 推导；rolling_scan（symbols 为空）时用指数日线推导
        trade_days: List[str] = []
        m5_map: Dict[str, List[dict]] = {}
        for sym in self.symbols:
            bars = load_m5(sym, d)
            m5_map[sym] = bars
            if bars:
                trade_days = sorted({_day_key(b["time"]) for b in bars}) or trade_days
        if not trade_days and self.rolling_scan:
            for ts_bars in self.index_daily.values():
                if ts_bars:
                    trade_days = sorted({str(b["trade_date"]).replace("-", "") for b in ts_bars})
                    if trade_days:
                        break
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
                                    quality_override=quality, bars=daily_bars_t,
                                    relax=self.relax_mode)
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
            conds = self.conditions or _gen_t_conditions(
                self.review_fn, b["symbol"], b["price"],
                _amp_median_from_m5(m5_map.get(b["symbol"], [])),
                task_id=self.task.get("id"))
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
            # 当日实时事件：建仓决策/盘后扫描汇总（前端时间明细实时追加，扫描段不再空白）
            day_events: List[Dict[str, Any]] = []
            _report_progress()
            # 盘后（当日收盘后）对未建仓标的打分，as_of=当日（防前视）
            next_day = trade_days[idx + 1] if idx + 1 < len(trade_days) else None
            if next_day is None:
                break  # 窗口末日不建仓（无做T日）
            # 当日候选：
            #   - 固定候选（self.symbols）中未建仓的（对齐实盘候选池）
            #   - rolling_scan 时补充全市场历史扫描（对齐实盘 daily_auto_select 全市场扫描）
            cand_syms: List[str] = list(self.symbols)
            if getattr(self, "rolling_scan", False):
                try:
                    from app.services import t_build as _tb
                    _pool = self._scan_pool or self._all_symbols
                    if not _pool:
                        _raw = _tb._fetch_all_a_symbols()
                        _pool = [r["symbol"] for r in (_raw or [])]
                        self._all_symbols = _pool
                    scan_cands = _tb.scan_t_candidates_historical(
                        _pool, self.data_dir, as_of=trade_day,
                        quality_fn=_tb._quality_from_daily, limit=20,
                        relax=self.relax_mode)
                    scan_syms = [c["symbol"] for c in scan_cands if c.get("pass_gate")]
                    cand_syms = list(dict.fromkeys(cand_syms + scan_syms))
                except Exception as e:
                    print(f"[t-backtest] 滚动建仓全市场扫描失败: {e}")
            # 当日候选打分（P0-2 节奏修复：先全量打分收集 pending，按 score 降序，
            # 受 max_daily_auto/max_symbols_being_built 停建——对齐生产 daily_auto_build）
            pending: List[Dict[str, Any]] = []
            for sym in cand_syms:
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
                                        quality_override=quality, bars=daily_bars_t,
                                        relax=self.relax_mode)
                if not r["pass_gate"]:
                    build_decisions.append({"symbol": sym, "decision": "rejected",
                                            "score": r["score"], "as_of": trade_day,
                                            "reasons": r["reasons"] or ["打分未达标"]})
                    continue
                next_bars = [b for b in m5_map.get(sym, []) if _day_key(b["time"]) == next_day]
                # 选出来后实时补拉 m5（用户反馈优化）：缓存缺失时 worker 环境直接
                # prefetch_m5 拉取并落盘缓存（回放仍零网络、确定性不变）。
                # 彻底消除"次日开盘价不可用"误拒（此前预取猜测覆盖不全）。
                if not next_bars:
                    try:
                        from app.services.t_backtest_data import load_m5, prefetch_m5
                        _m5 = load_m5(sym, d)
                        if not _m5 and self.rolling_scan:
                            prefetch_m5(sym, trade_days, d, is_index=False)
                            _m5 = load_m5(sym, d)
                        if _m5:
                            m5_map[sym] = _m5
                            next_bars = [b for b in _m5 if _day_key(b["time"]) == next_day]
                    except Exception as e:
                        print(f"[t-backtest] 滚动建仓 m5 补拉失败 {sym}: {str(e)[:80]}")
                price = float(next_bars[0]["open"]) if next_bars else 0.0
                if price <= 0:
                    build_decisions.append({"symbol": sym, "decision": "rejected",
                                            "score": r["score"], "as_of": trade_day,
                                            "reasons": ["次日开盘价不可用"]})
                    continue
                pending.append({"symbol": sym, "score": float(r["score"] or 0),
                                "price": price, "next_bars": next_bars,
                                "r": r, "daily_bars_t": daily_bars_t})
            # 按 score 降序（区分度恢复后排序才有意义——P0-1 连续趋势分）
            pending.sort(key=lambda x: x["score"], reverse=True)
            # 当日建仓上限（对齐生产）：max_daily_auto=3 / max_symbols_being_built=5
            try:
                _bp = t_build._params()
                max_daily = int(_bp.get("max_daily_auto", 3))
                max_symbols = int(_bp.get("max_symbols_being_built", 5))
            except Exception:
                max_daily, max_symbols = 3, 5
            daily_built = 0
            for cand in pending:
                if daily_built >= max_daily or len(builds) >= max_symbols:
                    break  # 当日已满/在途已满，其余次日重新打分
                sym, price, r = cand["symbol"], cand["price"], cand["r"]
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
                daily_built += 1
                # P1-3：build_decisions 落盘 trend/quality 完整字段（归因可复现）
                build_decisions.append({"symbol": sym, "decision": "built", "price": price,
                                        "shares": shares, "score": r["score"],
                                        "build_day": next_day, "reasons": r["reasons"] or [],
                                        "trend": r["trend"], "quality": (r.get("quality") or {}).get("score")})
                day_events.append({
                    "type": "build_decision",
                    "trade_day": trade_day,
                    "data": {
                        "decision": "built",
                        "symbol": sym,
                        "price": price,
                        "shares": shares,
                        "build_day": next_day,
                        "score": r["score"],
                        "reasons": r["reasons"] or [],
                    },
                })
                print(f"[t-backtest] 滚动建仓 {sym} @ {price} x{shares} 于 {next_day} "
                      f"(score={r['score']} trend={r['trend'].get('score')})")
            # 当日盘后扫描汇总事件（无建仓也上报，让 50%→回放 阶段有时间明细）
            day_events.append({
                "type": "rolling_scan",
                "trade_day": trade_day,
                "data": {
                    "as_of": trade_day,
                    "build_day": next_day,
                    "built_count": daily_built,
                    "built_symbols": [b["symbol"] for b in builds if b.get("build_day") == next_day],
                    "pending_count": len(pending),
                },
            })
            _report_progress(events_delta=day_events)

        # 各标的从建仓日起做T（子引擎 start_trade_day）
        per_symbol: List[Dict[str, Any]] = []
        cash = self.net_asset - sum(b["price"] * b["shares"] for b in builds)
        total_asset_by_day: Dict[str, float] = {}
        base_units = len(self.symbols)
        for b in builds:
            if cancel_event is not None and cancel_event.is_set():
                break
            conds = self.conditions or _gen_t_conditions(
                self.review_fn, b["symbol"], b["price"],
                _amp_median_from_m5(m5_map.get(b["symbol"], [])),
                task_id=self.task.get("id"))
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
    stop_losses = sum(r.get("metrics", {}).get("stop_loss_count", 0) for r in per_symbol)
    realized = sum(r.get("ledger", {}).get("realized_pnl", 0) for r in per_symbol)
    # 卖出明细从事件流提取（ledger.summary 不含 trades）
    sells = [e.get("data", {}).get("trade") for r in per_symbol
             for e in (r.get("events") or []) if e.get("type") == "trade"
             and e.get("data", {}).get("trade", {}).get("side") == "sell"]
    sells = [t for t in sells if t]
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
        "ai_condition_update_count": sum(
            r.get("metrics", {}).get("ai_condition_update_count", 0) or 0
            for r in per_symbol),
        # 消费式条件重建次数（迭代#57c：组合聚合缺失导致 #63/#65 报告 rebuild=None）
        "condition_rebuild_count": sum(
            r.get("metrics", {}).get("condition_rebuild_count", 0) or 0
            for r in per_symbol),
        "ai_exec_count": sum(r.get("metrics", {}).get("ai_exec_count", 0) or 0 for r in per_symbol),
        "ai_exec_win_rate_pct": _combine_ai_exec_win_rate(per_symbol),
        "realized_pnl": round(realized, 2),
        "win_rate_pct": round(win_rate, 2),
        "stop_loss_count": stop_losses,
        "max_drawdown_pct": round(max_dd, 2),
        "per_symbol_return": {r.get("symbol", "?"): round(r.get("metrics", {}).get("total_return_pct", 0), 2)
                              for r in per_symbol},
    }
