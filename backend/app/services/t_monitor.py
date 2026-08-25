# -*- coding: utf-8 -*-
"""做T系统 · TMonitor 监控器（Worker daemon 线程，30s 周期）。

依据 final-t-plan.md §④ 与 spec t-monitor-trigger：
- 分层采样：核心底仓(≤10-20)腾讯 qt 直连(use_cache=False) + ThreadPoolExecutor(≤5) 并发 + jitter；观察池 30s-1min 缓存
- 盘中量比归一：[当前累计换手×(240/已开连续分钟)]/近N日同刻均值（修正 indicator.py turnover_rate/2.0 bug）
- 滞回/去抖/armed 状态机 + 复合企稳确认（价∧量能∧分时企稳）
- regime 前置 GATE（BLOCKED 不写 / MANUAL_ONLY 挂人）
- 命中 → 写 t_triggers(pending, snapshot{suggest_bid/ask, slippage_budget, confidence})
- 14:45 后禁新开仓；Worker 永不直接下单
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services import t_db
from app.services.t_data_sources import _normalize_symbol, fetch_tencent_quote
from app.services.t_regime import check_gate, compute_regime, _is_trading_time

MONITOR_INTERVAL = 30       # 秒
INITIAL_OFFSET = 20         # 错峰启动
MAX_WORKERS = 5             # 并发取价上限
JITTER = 3                  # ±3s
MAX_CORE_SYMBOLS = 20       # 核心底仓数量上限
MIN_TURNOVER_BASE = 0.5     # 量比基准兜底 %
COOLDOWN_SECONDS = 300      # 同条件去抖冷却（5min）


class TMonitor:
    """做T监控器：daemon 线程，30s 轮询 t_conditions，命中写 t_triggers。"""

    def __init__(self, interval_seconds: int = MONITOR_INTERVAL):
        self.interval = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._status = {
            "running": False,
            "last_round": None,
            "last_round_ms": 0,
            "conditions_checked": 0,
            "triggers_written": 0,
            "errors": 0,
        }

    # ── 生命周期 ──
    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="t-monitor")
        self._thread.start()
        self._status["running"] = True
        print("[TMonitor] ✅ 做T监控器已启动")
        return True

    def stop(self) -> None:
        self._stop.set()
        self._status["running"] = False
        print("[TMonitor] 做T监控器已停止")

    def status(self) -> Dict[str, Any]:
        return dict(self._status)

    # ── 主循环 ──
    def _run(self):
        time.sleep(INITIAL_OFFSET)  # 错峰
        while not self._stop.is_set():
            round_start = time.time()
            try:
                if _is_trading_time():
                    self._round()
                else:
                    time.sleep(60)  # 非交易时段低频等待
                    continue
            except Exception as e:
                self._status["errors"] += 1
                print(f"[TMonitor] 本轮异常: {e}")
            elapsed = (time.time() - round_start) * 1000
            self._status.update({
                "last_round": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_round_ms": round(elapsed, 1),
            })
            # jitter 等待
            wait = self.interval + ((time.time() * 1000) % (JITTER * 2 + 1) - JITTER)
            self._stop.wait(max(5.0, wait))

    def _round(self):
        """单轮：拉 regime → 读条件 → 并发取价 → 构建字段快照 → 表达式/默认逻辑评估 → 写触发。"""
        # 1) regime 前置（每轮一次，缓存 5s）
        regime_state = compute_regime()

        # 2) 当日有效条件
        conditions = t_db.list_active_conditions()
        if not conditions:
            return
        self._status["conditions_checked"] = len(conditions)

        # 3) 并发取价（核心标的）
        symbols = list({c["symbol"] for c in conditions})[:MAX_CORE_SYMBOLS]
        quotes = self._fetch_quotes_concurrent(symbols)

        # 3.5) 止损扫描（持仓标的现价 ≤ stop_loss_price → 止损卖腿，独立于条件触发）
        try:
            from app.services.t_gateway import gateway_execute, get_sellable_ledger
            ledger = get_sellable_ledger()
        except Exception as e:
            print(f"[TMonitor] 止损扫描初始化失败: {e}")
            ledger = {}

        # 4) 逐条件判断（表达式优先；无表达式回退默认复合确认逻辑）
        written = 0
        for cond in conditions:
            symbol = cond["symbol"]
            quote = quotes.get(_normalize_symbol(symbol))
            if not quote or not quote.get("current"):
                continue
            # 无底仓预拦截：
            #   卖腿：sellable=0 不触发（T+0 当日买回次日才可卖，卖腿必须有券）
            #   买腿：迭代#58（用户需求）——无底仓放行触发，等价"条件单建仓"：
            #     低吸/custom(buy) 命中后按建仓规模买入开仓（量/风控由网关+建仓规模兜底）；
            #     14:45 后禁新开仓仍由时段门拦截。有底仓时仍是加仓语义（底仓 30%）。
            try:
                pos_item = (ledger or {}).get(symbol) or {}
                cond_kind = cond.get("trigger_kind", "low_buy")
                if cond_kind in ("high_sell_then_buy_back", "high_sell") \
                        and int(pos_item.get("sellable", 0) or 0) <= 0:
                    continue
            except Exception:
                pass
            try:
                # 止损前置检查（每标的每轮一次：现价 ≤ 止损价 且 当日未止损过）
                self._check_stop_loss(symbol, quote, ledger)
                # 构建该标的字段快照（供表达式求值）
                snapshot = self._build_snapshot(cond, quote, regime_state)
                if self._evaluate_condition(cond, quote, regime_state, snapshot):
                    self._write_trigger(cond, quote, regime_state, snapshot)
                    written += 1
            except Exception as e:
                print(f"[TMonitor] 条件评估异常 {symbol}: {e}")
        self._status["triggers_written"] += written

    def _build_snapshot(self, cond: Dict[str, Any], quote: dict,
                        regime_state: dict) -> Dict[str, Any]:
        """构建字段快照（Agent 自由表达式可引用的全部字段）。

        字段注册表见 t_expr.FIELD_REGISTRY；此处按需采集（quote 实时 + 量比 + 分钟线衍生 + regime + 持仓 + 指数）。
        """
        symbol = cond["symbol"]
        snapshot: Dict[str, Any] = {}

        # quote.*（腾讯 qt 实时）
        snapshot["quote"] = {
            "current": float(quote.get("current", 0) or 0),
            "open": float(quote.get("open", 0) or 0),
            "high": float(quote.get("high", 0) or 0),
            "low": float(quote.get("low", 0) or 0),
            "pre_close": float(quote.get("pre_close", 0) or 0),
            "change_pct": float(quote.get("change_pct", 0) or 0),
            "turnover_rate": float(quote.get("turnover_rate", 0) or 0),
            "amplitude": float(quote.get("amplitude", 0) or 0),
            "vol": float(quote.get("vol", 0) or 0),
            "amount": float(quote.get("amount", 0) or 0),
        }
        # vol_ratio（盘中量比归一）
        vr = self._calc_volume_ratio(cond, quote)
        snapshot["vol_ratio"] = vr if vr is not None else 0.0
        # 量价关系派生字段（放量/缩量/上涨/下跌/放量上涨/缩量下跌/跌到企稳等，贴近交易语言）
        snapshot["quote"].update(self._build_vol_price(snapshot["quote"], vr))
        # minute.*（分钟线衍生，低频）
        snapshot["minute"] = self._build_minute_snapshot(symbol, quote)
        # 企稳引用（minute.m1.bounce → quote.stabilised）
        m1_bounce = bool(snapshot.get("minute", {}).get("m1", {}).get("bounce", False))
        snapshot["quote"]["stabilised"] = m1_bounce
        # regime.*
        snapshot["regime"] = {
            "state": regime_state.get("regime", "ACTIVE"),
            "gate_low_buy": regime_state.get("gate_low_buy", "ALLOWED"),
            "gate_high_sell": regime_state.get("gate_high_sell", "ALLOWED"),
            "interpret_sign": int(regime_state.get("interpret_sign", 1)),
        }
        # position.*（t 账户持仓）
        snapshot["position"] = self._build_position_snapshot(symbol)
        # index.*（指数实时，复用本轮 regime 已拉取的报价）
        snapshot["index"] = {
            "hs300_drop": float(regime_state.get("index_drop", 0) or 0),
            "sh_drop": 0.0,
            "sz_drop": 0.0,
        }
        # tech.*（技术指标：KDJ/MACD/RSI/MA，复用 get_realtime_indicators，带缓存）
        snapshot["tech"] = self._build_tech_snapshot(symbol, snapshot["quote"])
        return snapshot

    def _build_vol_price(self, q: Dict[str, Any], vol_ratio: float) -> Dict[str, Any]:
        """量价关系派生字段（贴近交易语言，Agent 可直接用单字段表达复合语义）。

        - volume_expand: 放量（量比 ≥ 1.5）
        - volume_shrink: 缩量（量比 ≤ 0.7）
        - price_up: 上涨（涨跌幅 > 0）
        - price_down: 下跌（涨跌幅 < 0）
        - up_with_volume: 放量上涨（价涨 ∧ 量比 ≥ 1.5）
        - up_with_low_volume: 缩量上涨（价涨 ∧ 量比 ≤ 0.7）
        - down_with_volume: 放量下跌（价跌 ∧ 量比 ≥ 1.5）
        - down_with_low_volume: 缩量下跌（价跌 ∧ 量比 ≤ 0.7）
        - panic_drop: 恐慌放量下跌（价跌超 2% ∧ 量比 ≥ 2.0）
        - near_day_low: 接近日内低点（现价 ≤ 日内最低 × 1.01）
        - stabilised: 企稳（分时不再创新低，见 minute.m1.bounce，此处引用）
        """
        current = float(q.get("current", 0) or 0)
        pre_close = float(q.get("pre_close", 0) or 0)
        day_low = float(q.get("low", 0) or 0)
        change_pct = float(q.get("change_pct", 0) or 0)
        v = vol_ratio if vol_ratio is not None else 0.0
        up = change_pct > 0
        down = change_pct < 0
        expand = v >= 1.5
        shrink = v <= 0.7
        return {
            "volume_expand": expand,
            "volume_shrink": shrink,
            "price_up": up,
            "price_down": down,
            "up_with_volume": up and expand,
            "up_with_low_volume": up and shrink,
            "down_with_volume": down and expand,
            "down_with_low_volume": down and shrink,
            "panic_drop": change_pct <= -2.0 and v >= 2.0,
            "near_day_low": (current > 0 and day_low > 0 and current <= day_low * 1.01),
            "stabilised": False,  # 由 minute.m1.bounce 提供（此处占位，快照合并时覆盖）
        }

    def _build_tech_snapshot(self, symbol: str, quote: dict) -> Dict[str, Any]:
        """技术指标字段（基于分钟线数据自算：MACD/KDJ/RSI/MA，日内实时、无 Tushare 依赖）。

        分钟级技术指标对做T触发更贴近（日内短线），且复用已有三源分钟线数据；
        字段名对齐 t_expr.FIELD_REGISTRY 的 tech.*（macd_dif/dea/bar、kdj_k/d/j、rsi_6/12/24、ma5/10/20/60）。
        """
        result = {
            "ma5": 0.0, "ma10": 0.0, "ma20": 0.0, "ma60": 0.0,
            "macd_dif": 0.0, "macd_dea": 0.0, "macd_bar": 0.0,
            "macd_golden_cross": False,
            "kdj_k": 50.0, "kdj_d": 50.0, "kdj_j": 50.0,
            "kdj_golden_cross": False, "kdj_overbought": False,
            "rsi_6": 50.0, "rsi_12": 50.0, "rsi_24": 50.0,
            "rsi_overbought": False, "rsi_oversold": False,
            "above_ma5": False, "above_ma20": False,
        }
        try:
            from app.services.t_data_sources import fetch_minute_bars
            m5 = fetch_minute_bars(symbol, freq="m5", count=120)
            if not m5 or len(m5) < 9:
                return result  # 分钟线不足，用默认值（保守）
            closes = [float(b["close"]) for b in m5]
            highs = [float(b["high"]) for b in m5]
            lows = [float(b["low"]) for b in m5]
            current = float(quote.get("current", 0) or 0)

            # MA
            result["ma5"] = _sma(closes, 5)
            result["ma10"] = _sma(closes, 10)
            result["ma20"] = _sma(closes, 20)
            result["ma60"] = _sma(closes, 60)
            # MACD (12,26,9)
            dif, dea, bar = _calc_macd_from_closes(closes)
            result["macd_dif"] = dif
            result["macd_dea"] = dea
            result["macd_bar"] = bar
            result["macd_golden_cross"] = dif > dea
            # KDJ (9,3,3) — 用最近9根高低 + 当前价
            k, d, j = _calc_kdj_from_bars(highs, lows, closes, current)
            result["kdj_k"], result["kdj_d"], result["kdj_j"] = k, d, j
            result["kdj_golden_cross"] = k > d
            result["kdj_overbought"] = j > 100 or k > 80
            # RSI
            result["rsi_6"] = _calc_rsi(closes, 6)
            result["rsi_12"] = _calc_rsi(closes, 12)
            result["rsi_24"] = _calc_rsi(closes, 24)
            result["rsi_overbought"] = result["rsi_6"] >= 80
            result["rsi_oversold"] = result["rsi_6"] <= 20
            result["above_ma5"] = current > result["ma5"] > 0
            result["above_ma20"] = current > result["ma20"] > 0
        except Exception as e:
            print(f"[TMonitor] 技术指标快照失败 {symbol}: {e}")
        return result

    def _build_minute_snapshot(self, symbol: str, quote: dict) -> Dict[str, Any]:
        """分钟线衍生字段（m1/m5）。取不到时给保守默认（0/False），避免误触发。"""
        result = {"m1": {}, "m5": {}}
        try:
            from app.services.t_data_sources import fetch_minute_bars
            m1 = fetch_minute_bars(symbol, freq="m1", count=120)
            m5 = fetch_minute_bars(symbol, freq="m5", count=60)
            if m1:
                today = datetime.now().strftime("%Y-%m-%d")
                today_lows = [b["low"] for b in m1 if str(b["time"]).startswith(today)]
                result["m1"] = {
                    "low_today": min(today_lows) if today_lows else 0.0,
                    "last_close": float(m1[-1]["close"]),
                    "bounce": self._stabilize_not_new_low(symbol, float(quote.get("current", 0) or 0)),
                }
            if m5:
                closes = [float(b["close"]) for b in m5]
                result["m5"] = {
                    "last_close": closes[-1] if closes else 0.0,
                    "ma5": _sma(closes, 5),
                    "ma10": _sma(closes, 10),
                    "ma20": _sma(closes, 20),
                }
        except Exception as e:
            print(f"[TMonitor] 分钟线快照失败 {symbol}: {e}")
        return result

    def _build_position_snapshot(self, symbol: str) -> Dict[str, Any]:
        """持仓字段（t 账户）。"""
        try:
            from app.services.t_gateway import get_sellable_ledger
            ledger = get_sellable_ledger()
            item = ledger.get(symbol) or {}
            avg = float(item.get("avg_price", 0) or 0)
            vol = int(item.get("volume", 0) or 0)
            pnl = 0.0
            # pnl_pct 需要现价，由调用方回填（此处用 quote 现价在快照里算）
            return {
                "sellable": int(item.get("sellable", 0) or 0),
                "volume": vol,
                "avg_price": avg,
                "pnl_pct": 0.0,
            }
        except Exception:
            return {"sellable": 0, "volume": 0, "avg_price": 0.0, "pnl_pct": 0.0}

    def _fetch_quotes_concurrent(self, symbols: List[str]) -> Dict[str, Optional[dict]]:
        """并发取价（腾讯 qt 直连），结果统一以归一化代码（sz159516）为键。

        修复（迭代#58c）：此前以原始 symbol（SZ159516）为键，而调用方用
        _normalize_symbol 小写键查询 → 全部 miss → 监控器对所有条件静默失效
        （"今日触发"恒为 0）。现两处键口径统一为归一化代码。
        """
        if not symbols:
            return {}
        result: Dict[str, Optional[dict]] = {}
        for i in range(0, len(symbols), MAX_WORKERS):
            batch = symbols[i:i + MAX_WORKERS]
            quotes = fetch_tencent_quote([_normalize_symbol(s) for s in batch])
            for ns in (_normalize_symbol(s) for s in batch):
                if ns in quotes and quotes[ns]:
                    result[ns] = quotes[ns]
        return result

    # ── 条件评估 ──
    def _evaluate_condition(self, cond: Dict[str, Any], quote: dict,
                            regime_state: dict, snapshot: Optional[dict] = None) -> bool:
        """条件评估：有 expression 走自由表达式求值；无则回退默认复合确认逻辑（实时路径）。"""
        return evaluate_condition_at(cond, quote, regime_state, snapshot, datetime.now())

    def _pass_common_gates(self, cond: Dict[str, Any], regime_state: dict) -> bool:
        """表达式通过后的通用护栏：regime GATE + 时段 + 状态机（实时路径）。"""
        return pass_common_gates(cond, regime_state, datetime.now())

    def _evaluate_default(self, cond: Dict[str, Any], quote: dict,
                          regime_state: dict, snapshot: Optional[dict] = None) -> bool:
        """默认复合企稳确认（实时路径）。"""
        return evaluate_default_at(cond, quote, regime_state, snapshot, datetime.now())

    def _calc_volume_ratio(self, cond: Dict[str, Any], quote: dict) -> Optional[float]:
        """盘中量比归一（实时路径）。"""
        return calc_volume_ratio_at(cond, quote, datetime.now())

    def _stabilize_not_new_low(self, symbol: str, current: float) -> bool:
        """分时企稳（实时路径，m1 分钟线判断）。"""
        return stabilize_not_new_low_at(symbol, current, datetime.now())

    # ── 写触发事件 ──
    def _write_trigger(self, cond: Dict[str, Any], quote: dict, regime_state: dict,
                       snapshot: Optional[dict] = None):
        """写入 t_triggers(pending, snapshot{suggest_bid/ask, slippage_budget, confidence, fields})。"""
        current = float(quote.get("current", 0) or 0)
        trigger_kind = cond.get("trigger_kind", "low_buy")
        # 滑点预算：0.1%（P4 标定 2-5 tick）
        slippage = 0.001
        gate = check_gate(trigger_kind, regime_state)
        mode = "human_confirm" if gate["mode"] == "human_confirm" else "auto"
        # 连续命中计数（同条件当日连续命中未实质改善 → 唤醒时提示 AI 调整/冷却）
        consecutive_hits = self._consecutive_hits(cond.get("id"), cond["symbol"])

        trig = {
            "account_id": "t",
            "condition_id": cond.get("id"),
            "symbol": cond["symbol"],
            "event_type": trigger_kind,
            "trigger_price": cond.get("target_price"),
            "quote_price": current,
            "suggest_bid_price": round(current * (1 - slippage), 3),
            "suggest_ask_price": round(current * (1 + slippage), 3),
            "slippage_budget": slippage,
            "snapshot": {
                "quote_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "trigger_price": cond.get("target_price"),
                "quote_price": current,
                "suggest_bid_price": round(current * (1 - slippage), 3),
                "suggest_ask_price": round(current * (1 + slippage), 3),
                "slippage_budget": slippage,
                "confidence": "expr_trigger" if cond.get("expression") else "low_buy_confirm",
                "turnover_rate": quote.get("turnover_rate"),
                "amplitude": quote.get("amplitude"),
                "expression_summary": _expr_summary(cond.get("expression")),
                "fields": snapshot or {},   # 触发时刻字段快照（Agent 决策直接用，不再重复取价）
                "consecutive_hits": consecutive_hits,   # AI 主导：连续命中计数
            },
            "mode": mode,
        }
        trig_id = t_db.insert_trigger(trig)
        if trig_id:
            # 消费式条件（迭代#56，用户需求）：触发后条件即销毁（consumed），
            # 不再冷却复用——由 AI 重新评估设定新条件（update_condition 语义=重建）。
            # 状态机：置 consumed + 计数 +1（保留计数供审计）
            t_db.update_condition_state(
                cond.get("id"),
                armed=0,
                status="consumed",
                last_triggered_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                trigger_count_today=int(cond.get("trigger_count_today") or 0) + 1,
            )
            print(f"[TMonitor] 触发写入 #{trig_id} {cond['symbol']} {trigger_kind} "
                  f"mode={mode} consec_hits={consecutive_hits} @ {current}")
            # MANUAL_ONLY（regime 谨慎/下跌市，低吸闸门挂人）：只写触发挂人工确认，
            # 不自动下单（修复：此前无条件自动执行，谨慎市也会绕过人工门自动买入）
            if mode == "human_confirm":
                t_db.update_trigger_status(
                    trig_id, "human_confirm",
                    reason="regime 低吸闸门 MANUAL_ONLY，挂人工确认（可执行/取消，2min 未确认自动取消）")
                return
            # 自动执行闭环（迭代#57，用户需求）：条件命中自动执行（止损/止盈自动卖出、
            # 低吸自动买入，volume 优先用 AI 在条件里设定的股数），不再逐次等 AI 决策；
            # 迭代#58：无底仓买腿 = 条件单建仓，量按建仓规模（单笔上限÷现价）。
            # 执行完成后报告 AI 复盘并重建新条件。
            try:
                from app.services.t_gateway import gateway_execute
                side = "buy" if _is_buy_side(cond) else "sell"
                cond_vol = int(cond.get("volume") or 0)
                if cond_vol > 0:
                    volume = (cond_vol // 100) * 100
                else:
                    # 回退规则：买腿 30% 底仓（min 100）；无底仓 = 建仓规模（单笔上限÷现价）；
                    # 卖腿 30%（保留底仓 100）
                    pos_item = (ledger or {}).get(symbol) or {}
                    sellable = int(pos_item.get("sellable", 0) or 0)
                    if side == "buy":
                        if sellable > 0:
                            volume = max(int(sellable * 0.3), 100)
                        else:
                            try:
                                from app.services.t_build import build_sizing
                                sizing = build_sizing(symbol, current)
                                volume = int(sizing.get("suggest_volume") or 0)
                            except Exception:
                                volume = 0
                    else:
                        max_sell = sellable
                        if sellable > 200:
                            max_sell = max(sellable - 100, 0)
                        volume = max(int(sellable * 0.3), 100) if sellable > 0 else 0
                        volume = min(volume, max_sell)
                    volume = (volume // 100) * 100
                exec_ok = False
                if volume > 0:
                    gw = gateway_execute(symbol, side, current, volume,
                                         reason=f"条件命中自动执行（{trigger_kind}）",
                                         decision_source="ai_led",
                                         condition_id=cond.get("id"))
                    exec_ok = gw.get("status") == "success"
                    print(f"[TMonitor] 自动执行 {symbol} {side} {volume}股@{current}: "
                          f"{gw.get('status')} {str(gw.get('reason') or '')[:40]}")
                    # 执行结果写入触发事件（供审计/复盘）
                    t_db.update_trigger_status(
                        trig_id, "executed" if exec_ok else "blocked",
                        reason=f"自动执行 {side} {volume}股 @{current}: {gw.get('status')}")
                elif volume <= 0:
                    # 量推导为 0（卖腿无可卖底仓 / 无底仓建仓规模不可用）→ 直接标记，
                    # 避免孤儿 pending 事件（降级轮询兜底）
                    t_db.update_trigger_status(
                        trig_id, "blocked",
                        reason=f"自动执行量推导为 0（{side}，无可卖底仓或建仓规模不可用）")
                # 消费式条件自动重建（迭代#56b/57）：本条件已 consumed，该标的仍有
                # 持仓且无其他 active 条件 → AI 重新评估生成新条件（移动基准）。
                # 执行后报告 AI = 调 AI 条件生成（含现价），失败回退规则公式。
                try:
                    from app.services.t_db import list_active_conditions
                    remain = list_active_conditions(symbol=symbol)
                    # 成交后刷新持仓（无底仓建仓场景：执行前无持仓/成本）
                    fresh_item = {}
                    try:
                        from app.services.t_gateway import get_sellable_ledger
                        fresh_item = get_sellable_ledger().get(symbol) or {}
                    except Exception:
                        pass
                    pos_volume = int(fresh_item.get("volume") or 0)
                    if not remain and pos_volume > 0:
                        from app.services.t_build import auto_gen_conditions_for_build
                        avg_price = float(fresh_item.get("avg_price") or 0)
                        if avg_price <= 0 and exec_ok:
                            avg_price = float(gw.get("price") or current)  # 无底仓建仓：无历史成本，用成交价
                        if avg_price > 0:
                            from datetime import date
                            today = date.today().strftime("%Y%m%d")
                            ok = auto_gen_conditions_for_build(
                                symbol, avg_price, trade_date=today,
                                quote_price=current)
                            if ok:
                                print(f"[TMonitor] 消费式条件自动重建 {symbol}（AI 重新评估，当日 @{current}）")
                except Exception as e:
                    print(f"[TMonitor] 条件自动重建失败 {symbol}: {e}")
            except Exception as e:
                print(f"[TMonitor] 自动执行失败（降级标记）: {e}")
                from app.services.t_bridge import agent_review_and_execute
                agent_review_and_execute(trig)

    def _consecutive_hits(self, condition_id: Optional[int], symbol: str) -> int:
        """同条件当日连续命中计数：从最新 t_triggers 往前数连续 ai_decided/await_retry/pending。"""
        if not condition_id:
            return 0
        try:
            from sqlalchemy import text
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                rows = db.execute(text(
                    "SELECT status FROM t_triggers "
                    "WHERE condition_id = :cid AND symbol = :sym "
                    "AND created_at::date = CURRENT_DATE "
                    "ORDER BY id DESC LIMIT 10"
                ), {"cid": condition_id, "sym": symbol}).mappings().all()
                n = 0
                for r in rows:
                    st = r.get("status")
                    if st in ("pending", "ai_decided", "await_retry"):
                        n += 1
                    else:
                        break
                return n
            finally:
                db.close()
        except Exception:
            return 0

    def _check_stop_loss(self, symbol: str, quote: dict, ledger: dict):
        """止损扫描（生产）：持仓标的现价 ≤ stop_loss_price → 止损卖腿（reason=stop_loss）。

        - 每标的每轮一次（符号条件共享同一止损价，取条件表中非零止损价）
        - 当日已止损过（t_triggers 含当日 stop_loss 事件）则跳过，防止重复卖
        - 卖量 = 可卖底仓全部（止损离场），走网关 ai_led 档位（不豁免风控）
        - 止损后冻结该标的全部条件（armed=0）
        """
        try:
            from app.services import t_db
            from app.services.t_gateway import gateway_execute
            item = (ledger or {}).get(symbol) or {}
            sellable = int(item.get("sellable", 0) or 0)
            if sellable <= 0:
                return
            current = float(quote.get("current", 0) or 0)
            if current <= 0:
                return
            stop_price = None
            conds = t_db.list_active_conditions(symbol=symbol)
            for c in conds or []:
                sp = float(c.get("stop_loss_price") or 0)
                if sp > 0:
                    stop_price = sp
                    break
            if not stop_price or current > stop_price:
                return
            # 当日已止损过则跳过
            from sqlalchemy import text
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                done = db.execute(text(
                    "SELECT 1 FROM t_triggers WHERE symbol = :sym AND event_type = 'stop_loss' "
                    "AND created_at::date = CURRENT_DATE LIMIT 1"
                ), {"sym": symbol}).scalar()
            finally:
                db.close()
            if done:
                return
            # 卖量：减半仓（-3% 止损语义，保留底仓继续做T；全卖会导致后续高抛
            # 触发但无券可卖，AI 反复"无底仓"放弃）
            half = (sellable // 2 // 100) * 100
            volume = half if half >= 100 else (sellable // 100) * 100
            if volume <= 0:
                return
            gw = gateway_execute(symbol, "sell", current, volume,
                                 reason="止损离场（stop_loss）", decision_source="ai_led",
                                 is_stop_loss=True)
            print(f"[TMonitor] 止损触发 {symbol} @ {current} x{volume}: {gw.get('status')}")
            # 冻结该标的全部条件（当日不再触发低吸/高抛）
            for c in conds or []:
                cid = c.get("id")
                if cid:
                    t_db.update_condition_state(cid, armed=0)
        except Exception as e:
            print(f"[TMonitor] 止损扫描异常 {symbol}: {e}")


# ── 单例管理（对齐 candidate_pool_monitor 模式） ──
_monitor_instance: Optional[TMonitor] = None
_monitor_lock = threading.Lock()


def get_t_monitor(interval_seconds: int = MONITOR_INTERVAL) -> TMonitor:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = TMonitor(interval_seconds=interval_seconds)
        return _monitor_instance


def start_t_monitor() -> bool:
    monitor = get_t_monitor()
    ok = monitor.start()
    # 桥不可达降级：启动低频轮询兜底线程（消费 pending 事件，执行仍经网关）
    try:
        from app.services.t_bridge import fallback_poll_loop
        if not getattr(monitor, "_fallback_started", False):
            monitor._fallback_started = True
            threading.Thread(
                target=fallback_poll_loop,
                args=(monitor._stop,),
                daemon=True,
                name="t-bridge-fallback",
            ).start()
            print("[TMonitor] ✅ 降级兜底轮询线程已启动")
    except Exception as e:
        print(f"[TMonitor] ⚠️ 降级兜底线程启动失败: {e}")
    return ok


def stop_t_monitor() -> None:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is not None:
            _monitor_instance.stop()


def get_t_monitor_status() -> Dict[str, Any]:
    monitor = get_t_monitor()
    return monitor.status()


def _sma(values: List[float], period: int) -> float:
    """简单移动平均（取序列最后 period 根的均值；不足则全量均值）。"""
    if not values:
        return 0.0
    window = values[-period:] if len(values) >= period else values
    return round(sum(window) / len(window), 4)


def _expr_summary(expression: Any) -> str:
    """表达式人类可读摘要（写进触发事件快照）。"""
    if not expression:
        return ""
    try:
        from app.services.t_expr import expression_summary
        return expression_summary(expression)
    except Exception:
        return str(expression)[:120]


def _ema(values: List[float], period: int) -> List[float]:
    """指数移动平均序列。"""
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _calc_macd_from_closes(closes: List[float]) -> tuple:
    """MACD(12,26,9) → (dif, dea, bar)。"""
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    bar = 2.0 * (dif[-1] - dea[-1])
    return round(dif[-1], 4), round(dea[-1], 4), round(bar, 4)


def _calc_kdj_from_bars(highs: List[float], lows: List[float], closes: List[float],
                        current: float) -> tuple:
    """KDJ(9,3,3) → (k, d, j)。用最近9根高低 + 当前价。"""
    if len(closes) < 9:
        return 50.0, 50.0, 50.0
    h9 = max(highs[-9:])
    l9 = min(lows[-9:])
    rsv = (current - l9) / (h9 - l9) * 100 if h9 > l9 else 50.0
    k = 2 / 3 * 50.0 + 1 / 3 * rsv
    d = 2 / 3 * 50.0 + 1 / 3 * k
    j = 3 * k - 2 * d
    return round(k, 2), round(d, 2), round(j, 2)


def _calc_rsi(closes: List[float], period: int = 6) -> float:
    """RSI(Wilder 平滑)。"""
    if len(closes) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        chg = closes[i] - closes[i - 1]
        gains.append(max(chg, 0))
        losses.append(max(-chg, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


# ────────────────────────────────────────────────────────────────
# 纯函数评估集（now 注入，回测与实时共用；TMonitor 方法为薄转发）
# ────────────────────────────────────────────────────────────────

def _is_buy_side(cond: Dict[str, Any]) -> bool:
    """条件执行方向（迭代#58）：direction 显式优先；缺省按 trigger_kind 默认。

    - low_buy/panic_vibrate → 买腿
    - direction=buy/买（custom 等自由类型显式声明）→ 买腿
    - 其余（high_sell/custom 未声明等）→ 卖腿（保持既有语义，避免未标方向的
      custom 突然变成买入）
    """
    kind = cond.get("trigger_kind", "low_buy")
    if kind in ("low_buy", "panic_vibrate"):
        return True
    d = str(cond.get("direction") or "").strip().lower()
    if d in ("buy", "买", "买入"):
        return True
    return False

def evaluate_condition_at(cond: Dict[str, Any], quote: dict, regime_state: dict,
                          snapshot: Optional[dict], now: datetime) -> bool:
    """条件评估（纯函数）：有 expression 走自由表达式求值；无则回退默认复合确认逻辑。"""
    expression = cond.get("expression")
    if expression:
        from app.services.t_expr import evaluate_expression
        try:
            if not evaluate_expression(expression, snapshot or {}):
                return False
        except Exception as e:
            print(f"[t-eval] 表达式求值异常 {cond.get('symbol')}: {e}")
            return False
        # 表达式通过后仍要过通用护栏（时段/状态机/regime 门）
        return pass_common_gates(cond, regime_state, now)
    return evaluate_default_at(cond, quote, regime_state, snapshot, now)


def pass_common_gates(cond: Dict[str, Any], regime_state: dict, now: datetime) -> bool:
    """表达式通过后的通用护栏：regime GATE + 时段 + 状态机（纯函数）。"""
    trigger_kind = cond.get("trigger_kind", "low_buy")
    gate = check_gate(trigger_kind, regime_state)
    if not gate["allowed"]:
        return False
    hm = now.hour * 100 + now.minute
    if hm >= 1445:
        return False
    if cond.get("armed") != 1:
        return False
    if cond.get("last_triggered_at"):
        try:
            last = datetime.strptime(str(cond["last_triggered_at"]), "%Y-%m-%d %H:%M:%S")
            if (now - last).total_seconds() < COOLDOWN_SECONDS:
                return False
        except (ValueError, TypeError):
            pass
    return True


def evaluate_default_at(cond: Dict[str, Any], quote: dict, regime_state: dict,
                        snapshot: Optional[dict], now: datetime) -> bool:
    """默认复合企稳确认（纯函数）：regime GATE ∧ 价到位 ∧ 量能企稳 ∧ 分时企稳 ∧ 状态机 ∧ 时段。

    量比/分时企稳优先取快照（回测快照重建器提供），缺省回退现场计算（实时路径）。
    """
    trigger_kind = cond.get("trigger_kind", "low_buy")

    # 0) regime GATE（低吸 BLOCKED 直接短路）
    gate = check_gate(trigger_kind, regime_state)
    if not gate["allowed"]:
        return False

    # 1) 时段：14:45 后禁新开仓
    hm = now.hour * 100 + now.minute
    if hm >= 1445:
        return False

    # 2) 状态机：armed + cooldown + 当日触发上限
    if cond.get("armed") != 1:
        return False
    if cond.get("last_triggered_at"):
        try:
            last = datetime.strptime(str(cond["last_triggered_at"]), "%Y-%m-%d %H:%M:%S")
            if (now - last).total_seconds() < COOLDOWN_SECONDS:
                return False
        except (ValueError, TypeError):
            pass

    # 3) 价格到位（低吸：current ≤ target；高抛：current ≥ sell_target）
    # 修复（迭代#58）：high_sell 与 high_sell_then_buy_back 同样检查高抛目标——
    # 此前仅 high_sell_then_buy_back 有价格门，high_sell 无表达式时量比达标即触发。
    current = float(quote.get("current", 0) or 0)
    target = float(cond.get("target_price") or 0)
    sell_target = float(cond.get("sell_target_price") or 0)
    if trigger_kind in ("low_buy", "panic_vibrate"):
        if target <= 0 or current > target:
            return False
    elif trigger_kind in ("high_sell", "high_sell_then_buy_back"):
        if sell_target <= 0 or current < sell_target:
            return False

    # 4) 量能企稳（量比归一 ≥ 阈值；快照优先；阈值 0 表示关闭量比过滤）
    raw_thresh = cond.get("vol_ratio_thresh")
    vol_thresh = float(raw_thresh) if raw_thresh is not None else 1.5
    vol_ratio = None
    if snapshot and snapshot.get("vol_ratio") is not None:
        try:
            vol_ratio = float(snapshot["vol_ratio"])
        except (TypeError, ValueError):
            vol_ratio = None
    if vol_ratio is None:
        vol_ratio = calc_volume_ratio_at(cond, quote, now)
    if vol_ratio is not None and vol_ratio < vol_thresh:
        return False

    # 5) 分时企稳（低吸：不再创新低；高抛：冲高；快照 stabilised 优先）
    stabilised = bool(snapshot and snapshot.get("quote", {}).get("stabilised"))
    stabilize = cond.get("stabilize_level", "not_new_low")
    if trigger_kind in ("low_buy", "panic_vibrate"):
        if stabilize == "not_new_low":
            ok = stabilised if snapshot else stabilize_not_new_low_at(cond["symbol"], current, now)
            if not ok:
                return False
    return True


def calc_volume_ratio_at(cond: Dict[str, Any], quote: dict, now: datetime) -> Optional[float]:
    """盘中量比归一（纯函数）：当前累计换手×时段伸缩 / 近N日同刻均值。

    公式：量比 = [当前累计换手 × (240/已开盘连续分钟)] / 近N日同刻均值
    基准从 benchmark_turnover_profile 读（JSON），缺省用 2% 兜底（P4 标定）。
    """
    turnover = float(quote.get("turnover_rate", 0) or 0)
    if turnover <= 0:
        return None
    # 已开盘连续分钟
    opened = 0
    if 930 <= now.hour * 100 + now.minute <= 1130:
        opened = (now.hour - 9) * 60 + now.minute - 30
    elif 1300 <= now.hour * 100 + now.minute <= 1500:
        opened = 120 + (now.hour - 13) * 60 + now.minute
    if opened <= 0:
        return None
    # 基准：condition 里存的同刻均值；缺省 2%
    profile = cond.get("benchmark_turnover_profile")
    base = None
    if isinstance(profile, (dict, str)):
        import json as _json
        try:
            p = _json.loads(profile) if isinstance(profile, str) else profile
            base = float(p.get("same_minute_avg") or 0)
        except (ValueError, TypeError, AttributeError):
            base = None
    if not base:
        base = MIN_TURNOVER_BASE
    scaled = turnover * (240.0 / opened)
    return round(scaled / base, 3)


def stabilize_not_new_low_at(symbol: str, current: float, now: datetime) -> bool:
    """分时企稳（纯函数）：用 m1 分钟线判断当日是否创新低（近 10 根最低 ≥ 当前 × 0.999）。"""
    try:
        from app.services.t_data_sources import fetch_minute_bars
        bars = fetch_minute_bars(symbol, freq="m1", count=120)
        if not bars:
            return True  # 无分钟线时放行（有腾讯 qt 实时兜底）
        today = now.strftime("%Y-%m-%d")
        today_lows = [b["low"] for b in bars if str(b["time"]).startswith(today)]
        if not today_lows:
            return True
        day_low = min(today_lows)
        # 当前价未创新低（或仅在日低上方 0.1% 内视为企稳）
        return current >= day_low * 0.999
    except Exception:
        return True
