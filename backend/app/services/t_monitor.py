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
import os
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

# 2026-09-02 架构修正: 只监控股票任务账户, 只跑狼大做T表达式, 停自动维护
T_MONITOR_ACCOUNT = os.getenv("T_MONITOR_ACCOUNT", "stock")
T_MONITOR_AUTO_MAINTAIN = os.getenv("T_MONITOR_AUTO_MAINTAIN", "0") == "1"
# 狼大做T表达式字段(唯一允许)：分时T出(t_sell) + 正T买点(index.intraday_dd 大盘盘中回撤2-3%低吸)
# + 黄线跌破离场(quote.vwap_break, 狼大8-04『黄线跌破直接走』)
# T1缩转放(t1_shrink_expand) 已由个股5min验证无预测力(2026-09-02) → 暂缓, 不再作为自动买腿
WOLF_T_FIELDS = ("minute.m5.t_sell", "index.intraday_dd", "quote.vwap_break", "index.m5_dump", "quote.dip_prev_low")


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
            "daily_maintained": None,
            "ai_maintained": None,
            "ai_maintain_running": False,
        }
        self._daily_maintained = ""
        self._ai_thread: Optional[threading.Thread] = None

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
        # 2026-09-03 修复：启动即补当日狼大持续腿——跨日/重启后监控条件不再丢失
        # （worker 常在非交易时段重启；这里无条件幂等补一次，交易日切换再补一次）
        try:
            self._roll_wolf_legs(datetime.now().strftime("%Y%m%d"))
        except Exception as e:
            print(f"[TMonitor] 启动持续腿结转异常: {e}")
        while not self._stop.is_set():
            round_start = time.time()
            try:
                if _is_trading_time():
                    today_d = datetime.now().strftime("%Y%m%d")
                    if self._daily_maintained != today_d:
                        self._daily_maintained = today_d
                        # 2026-09-03：狼大持续腿跨日结转不依赖自动维护开关——
                        # _daily_maintain 停用后也必须让 249/250/252/253/254 每个交易日可用
                        try:
                            self._roll_wolf_legs(today_d)
                        except Exception as e:
                            print(f"[TMonitor] 交易日持续腿结转异常: {e}")
                        if T_MONITOR_AUTO_MAINTAIN:   # 2026-09-02: 默认停自动维护(只留狼大做T条件)
                            self._status["daily_maintained"] = self._daily_maintain()
                            self._start_ai_maintain()
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

    def _roll_wolf_legs(self, today: str) -> int:
        """狼大持续腿跨日结转（2026-09-03 修复生产监控条件丢失）。

        背景：狼大做T条件(249/250/252/253/254 等)按“交易日”建行
        (唯一键 account+symbol+trigger_kind+trade_date)，是非消费式持续腿
        (命中后保持 active，5分钟冷却防刷)；而 t_monitor 每轮只读“当日”
        active 条件。此前持续腿只在建仓当天(trade_date=当天)存在，跨日后
        昨日行 status 仍 active 但因日期不是今日 → list_active_conditions
        读不到 → 报告显示“没有监控条件”。

        本函数幂等：把交易日 today 之前仍 active 的狼大表达式腿，按
        (symbol, trigger_kind) 取最近一日，复制一份到 today（保留表达式/
        价格/止损等全部配置）；今日已有同键行(任意状态, 含人工停用/消费)
        则跳过——不复活用户已停用/已消费的腿；成功后归档旧日源行。
        """
        rolled = 0
        try:
            prev = t_db.list_active_conditions(
                account_id=T_MONITOR_ACCOUNT, before_trade_date=today)
            wolf = [c for c in prev if _is_wolf_t_condition(c)]
            if not wolf:
                return 0
            keys_today = {(k.get("symbol"), k.get("trigger_kind"))
                          for k in t_db.list_condition_keys(T_MONITOR_ACCOUNT, today)}
            # prev 已按 trade_date DESC, id DESC → 首次出现即最近一日
            latest: Dict[Any, Dict[str, Any]] = {}
            for c in wolf:
                key = (c.get("symbol"), c.get("trigger_kind"))
                if key not in latest:
                    latest[key] = c
            expired_ids: List[int] = []
            for (sym, kind), src in latest.items():
                if (sym, kind) in keys_today:
                    continue
                copy = {k: v for k, v in src.items()
                        if k not in ("id", "created_at", "armed_at",
                                     "last_triggered_at", "trigger_count_today",
                                     "trade_date", "status")}
                copy.update({
                    "account_id": T_MONITOR_ACCOUNT,
                    "symbol": sym,
                    "trigger_kind": kind,
                    "trade_date": today,
                    "status": "active",
                })
                copy.setdefault("armed", 1)
                # 2026-09-03：跨日结转时刷新个股换手基准（近5已完成交易日均值，
                # 每日重算一次）——旧 wolf 条件无 benchmark 时兜底 0.5%，使系统
                # vol_ratio 与行情量比系统性差一个量级（药明 4.49 vs 1.50）。
                try:
                    prof = copy.get("benchmark_turnover_profile") or {}
                    ct_date = str(prof.get("computed_at") or "")[:10].replace("-", "")
                    if not prof.get("same_minute_avg") or ct_date != today:
                        from app.services.t_turnover_profile import compute_turnover_profile
                        _np = compute_turnover_profile(sym)
                        if _np:
                            copy["benchmark_turnover_profile"] = _np
                except Exception as e:
                    print(f"[TMonitor] 换手基准刷新失败 {sym}: {e}")
                cid = t_db.upsert_condition(copy)
                if cid:
                    rolled += 1
                    if src.get("id"):
                        expired_ids.append(int(src["id"]))
            for cid in expired_ids:
                t_db.update_condition_state(cid, status="expired")
        except Exception as e:
            print(f"[TMonitor] 狼大持续腿跨日结转失败: {e}")
        if rolled:
            print(f"[TMonitor] 狼大持续腿跨日结转 {rolled} 条 → {today}")
            self._status["wolf_legs_rolled"] = f"{today}:{rolled}"
        return rolled

    def _daily_maintain(self) -> dict:
        """每日一次（每交易日首次轮询前）：归档昨日条件 + 为缺条件的持仓补生成当日双条件。

        规则兜底：只对“今日无任何 active 条件”的持仓标的生成（不覆盖 AI/手动已有条件）；
        顺带让 V反/探针等 t 账户持仓（T+1 后可卖）每天自动获得做T条件。
        """
        res = {"expired": 0, "filled": 0}
        try:
            res["expired"] = t_db.expire_daily_conditions()
        except Exception as e:
            print(f"[TMonitor] 每日条件归档失败: {e}")
        try:
            # 来源：t 账户持仓（paper_positions），开盘前亦可稳定读取；
            # 条件仅补齐“今日尚无 active 条件”的标的后，交由盘中报价驱动是否触发。
            from app.services.t_pool import _get_positions, build_t_conditions, calc_t_quality
            for pos in _get_positions():
                sym = pos.get("symbol")
                avg = float(pos.get("avg_price") or 0)
                if not sym or avg <= 0:
                    continue
                if t_db.list_active_conditions(symbol=sym):
                    continue
                amp = None
                try:
                    q = calc_t_quality(sym)
                    amp = (q.get("factors") or {}).get("amp_median")
                except Exception:
                    amp = None
                for cond in build_t_conditions(avg, amp):
                    cond = {**cond, "account_id": t_db.ACCOUNT_T,
                            "symbol": sym, "trade_date": None}
                    if t_db.upsert_condition(cond):
                        res["filled"] += 1
        except Exception as e:
            print(f"[TMonitor] 每日条件补生成失败: {e}")
        print(f"[TMonitor] 每日维护: 归档{res['expired']}条, 补生成{res['filled']}条")
        return res

    def _start_ai_maintain(self) -> None:
        """每日自动 AI 维护：后台线程为持仓重建当日做T条件（AI 优先、规则兜底）。"""
        if self._ai_thread and self._ai_thread.is_alive():
            return
        self._status["ai_maintain_running"] = True
        self._ai_thread = threading.Thread(target=self._ai_maintain_loop, daemon=True,
                                           name="t-ai-maintain")
        self._ai_thread.start()

    def _ai_maintain_loop(self) -> None:
        """AI 维护主体：对 t 账户持仓逐标的 auto_gen_conditions_for_build（AI→规则回退→双腿补齐）。"""
        res = {"ai_ok": 0, "rule_fallback": 0, "fail": 0, "skipped_user": 0}
        try:
            from app.services.t_build import auto_gen_conditions_for_build
            from app.services.t_pool import _get_positions
            today = datetime.now().strftime("%Y%m%d")
            for pos in _get_positions():
                sym = pos.get("symbol")
                avg = float(pos.get("avg_price") or 0)
                if not sym or avg <= 0:
                    continue
                # 人工条件优先：今日已有 publisher=user 的条件则不动
                try:
                    acts = t_db.list_active_conditions(symbol=sym)
                    if any((c.get("publisher") or "") == "user" for c in acts):
                        res["skipped_user"] += 1
                        continue
                except Exception:
                    pass
                try:
                    ok = auto_gen_conditions_for_build(sym, avg, trade_date=today)
                    if ok:
                        res["ai_ok"] += 1
                    else:
                        res["rule_fallback"] += 1
                except Exception as e:
                    res["fail"] += 1
                    print(f"[TMonitor] AI维护 %s 异常: {e}" % sym)
                time.sleep(2)  # 多标的错峰，避免 LLM 桥连发
        except Exception as e:
            print(f"[TMonitor] AI维护循环异常: {e}")
        self._status["ai_maintain_running"] = False
        self._status["ai_maintained"] = f"{datetime.now():%Y-%m-%d %H:%M} {res}"
        print(f"[TMonitor] AI维护完成: {res}")

    def _round(self):
        """单轮：拉 regime → 读条件 → 并发取价 → 构建字段快照 → 表达式/默认逻辑评估 → 写触发。"""
        # 1) regime 前置（每轮一次，缓存 5s）
        regime_state = compute_regime()

        # 2) 当日有效条件（只读目标账户 + 只跑狼大做T表达式条件, 屏蔽其他做T）
        conditions = t_db.list_active_conditions(account_id=T_MONITOR_ACCOUNT)
        conditions = [c for c in conditions if _is_wolf_t_condition(c)]
        if not conditions:
            return
        self._status["conditions_checked"] = len(conditions)

        # 3) 并发取价（核心标的）
        symbols = list({c["symbol"] for c in conditions})[:MAX_CORE_SYMBOLS]
        quotes = self._fetch_quotes_concurrent(symbols)

        # 3.5) 止损扫描（持仓标的现价 ≤ stop_loss_price → 止损卖腿，独立于条件触发）
        try:
            from app.services.t_gateway import gateway_execute, get_sellable_ledger
            ledger = get_sellable_ledger(T_MONITOR_ACCOUNT)
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
            # 狼大持续腿触发冷却（2026-09-02）：分时T出/黄线等表达式腿命中后
            # 5 分钟不再重复写触发（非消费式持续腿防刷；分时形态天然低频，黄线靠此限频）
            if _is_wolf_t_condition(cond):
                _lt = cond.get("last_triggered_at") or ""
                if _lt:
                    try:
                        from datetime import datetime as _dt
                        _last = _dt.strptime(_lt, "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - _last).total_seconds() < 300:
                            continue
                    except Exception:
                        pass
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
                    self._write_trigger(cond, quote, regime_state, snapshot, ledger)
                    written += 1
            except Exception as e:
                print(f"[TMonitor] 条件评估异常 {symbol}: {e}")
        self._status["triggers_written"] += written

    def _index_intraday_dd(self) -> float:
        """上证指数当日盘中最大回撤%（从日高逐bar更新；30s TTL 缓存）。
        狼大正T买点(1-12『利用盘中大盘带下来的机会做正T』)：
        dd∈[2%,3%) → 低吸信号；dd≥3% 系统性风险不买（验证 docs/zt-dip-verification-report.md）。"""
        now = time.time()
        if now - _index_dd_cache["at"] < 30:
            return _index_dd_cache["value"]
        try:
            from app.services.t_data_sources import fetch_tencent_mkline
            bars = fetch_tencent_mkline("sh000001", freq="m5", count=60)
            today = datetime.now().strftime("%Y%m%d")
            today_bars = [b for b in (bars or []) if str(b.get("time", "")).startswith(today)]
            if len(today_bars) < 10:
                _index_dd_cache["at"] = now; _index_dd_cache["value"] = 0.0
                return 0.0
            dh = 0.0; mdd = 0.0
            for b in sorted(today_bars, key=lambda x: str(x.get("time"))):
                hi = float(b.get("high") or 0); cl = float(b.get("close") or 0)
                if hi > dh: dh = hi
                if dh > 0 and cl > 0:
                    mdd = max(mdd, (dh - cl) / dh * 100)
            _index_dd_cache["at"] = now; _index_dd_cache["value"] = round(mdd, 3)
            return round(mdd, 3)
        except Exception as e:
            print(f"[TMonitor] 指数盘中回撤计算失败: {e}")
            return 0.0

    def _index_m5_dump(self) -> float:
        """上证指数最新5min单根跌幅%（较前一根收盘；C档急杀信号≥0.4；30s TTL）。
        狼大'盘中带下来'的分时形态——验证 backtest_zt_signal_compare: 单根>=0.4% 16天 T+1+0.82%。"""
        now = time.time()
        if now - _m5_dump_cache["at"] < 30:
            return _m5_dump_cache["value"]
        try:
            from app.services.t_data_sources import fetch_tencent_mkline
            bars = fetch_tencent_mkline("sh000001", freq="m5", count=60)
            bars = sorted(bars or [], key=lambda b: str(b.get("time")))
            dump = 0.0
            if len(bars) >= 2:
                c0 = float(bars[-1].get("close") or 0)
                c1 = float(bars[-2].get("close") or 0)
                if c0 > 0 and c1 > 0:
                    dump = (c0 - c1) / c1 * 100
            _m5_dump_cache["at"] = now; _m5_dump_cache["value"] = round(dump, 3)
            return round(dump, 3)
        except Exception as e:
            print(f"[TMonitor] 指数急杀计算失败: {e}")
            return 0.0

    def _stock_dip_prev_low(self, symbol: str) -> bool:
        """个股当日5min最低 ≤ 前一交易日5min最低×1.005（A档：触及/跌破前日低点）。
        狼大2025-03-06『挂前一天的低点 能买进去就做正T』；配 vol_ratio<=0.7 缩量。
        fetch_minute_bars m5 count=320 ≈ 6.5 交易日，取最近非今日组的 min low。30s TTL。"""
        now = time.time()
        key = symbol
        if now - _prev_low_cache.get("at", 0) < 30 and _prev_low_cache.get("sym") == key:
            return _prev_low_cache.get("value", False)
        try:
            from app.services.t_data_sources import fetch_minute_bars
            bars = fetch_minute_bars(symbol, freq="m5", count=320) or []
            if len(bars) < 100:
                return False
            today = datetime.now().strftime("%Y-%m-%d")
            by_day = {}
            for b in sorted(bars, key=lambda x: str(x.get("time") or x.get("trade_time"))):
                t = str(b.get("time") or b.get("trade_time"))[:10]
                by_day.setdefault(t, []).append(b)
            days = sorted(by_day.keys())
            if len(days) < 2:
                return False
            today_low = min(float(b["low"]) for b in by_day.get(today, [by_day[days[-1]][0]]))
            prev_day = days[-2] if today in days else days[-1]
            prev_low = min(float(b["low"]) for b in by_day[prev_day])
            ok = prev_low > 0 and today_low <= prev_low * 1.005
            _prev_low_cache.update({"at": now, "sym": key, "value": ok})
            return ok
        except Exception as e:
            print(f"[TMonitor] 前日低点计算失败 {symbol}: {e}")
            return False

    def _build_snapshot(self, cond: Dict[str, Any], quote: dict,
                        regime_state: dict) -> Dict[str, Any]:
        """构建字段快照（Agent 自由表达式可引用的全部字段）。

        字段注册表见 t_expr.FIELD_REGISTRY；此处按需采集（quote 实时 + 量比 + 分钟线衍生 + regime + 持仓 + 指数）。
        """
        symbol = cond["symbol"]
        snapshot: Dict[str, Any] = {}

        # quote.*（腾讯 qt 实时）
        _cur = float(quote.get("current", 0) or 0)
        _avg = float(quote.get("average", 0) or 0)
        snapshot["quote"] = {
            "current": _cur,
            "open": float(quote.get("open", 0) or 0),
            "high": float(quote.get("high", 0) or 0),
            "low": float(quote.get("low", 0) or 0),
            "pre_close": float(quote.get("pre_close", 0) or 0),
            "change_pct": float(quote.get("change_pct", 0) or 0),
            "turnover_rate": float(quote.get("turnover_rate", 0) or 0),
            "amplitude": float(quote.get("amplitude", 0) or 0),
            "vol": float(quote.get("vol", 0) or 0),
            "amount": float(quote.get("amount", 0) or 0),
            "average": _avg,
            # 分时黄线跌破（狼大8-04『绝对不能破的点就是日均线那条黄线 一旦突发跌破直接走』）
            "vwap_break": bool(_avg > 0 and _cur < _avg),
            "dip_prev_low": self._stock_dip_prev_low(symbol),
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
        # position.*（监控账户持仓）
        snapshot["position"] = self._build_position_snapshot(
            symbol, cond.get("account_id", T_MONITOR_ACCOUNT))
        # index.*（指数实时，复用本轮 regime 已拉取的报价 + 盘中回撤=正T买点信号）
        snapshot["index"] = {
            "hs300_drop": float(regime_state.get("index_drop", 0) or 0),
            "sh_drop": 0.0,
            "sz_drop": 0.0,
            "intraday_dd": self._index_intraday_dd(),
            "m5_dump": self._index_m5_dump(),
        }
        # tech.*（技术指标：KDJ/MACD/RSI/MA，复用 get_realtime_indicators，带缓存）
        snapshot["tech"] = self._build_tech_snapshot(symbol, snapshot["quote"])
        # external.*（外部风险：美股纳指/费半/美债10Y/全球宏观，TTL 缓存降级）
        try:
            from app.services.t_external_risk import external_risk_snapshot
            _ext = external_risk_snapshot()
            snapshot["external"] = {
                "us_risk": bool(_ext.get("us_risk")),
                "us_risk_reason": str(_ext.get("us_risk_reason") or ""),
                "us_risk_score": int(_ext.get("us_risk_score") or 0),
                "nasdaq_pct": (_ext.get("us_market") or {}).get("nasdaq", {}).get("pct", 0.0),
                "sox_pct": (_ext.get("us_market") or {}).get("sox", {}).get("pct", 0.0),
                "gm_liquidity_gate": (_ext.get("global_macro") or {}).get("liquidity_gate", ""),
            }
        except Exception:
            snapshot["external"] = {"us_risk": False, "us_risk_reason": "", "us_risk_score": 0,
                                    "nasdaq_pct": 0.0, "sox_pct": 0.0, "gm_liquidity_gate": ""}
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
        """分钟线衍生字段（m1/m5）+ 做T信号(T1缩转放/分时T出)。取不到时给保守默认，避免误触发。"""
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
                # ── 做T信号（狼大体系, t_signal 逻辑）──
                t1, t_sell = _t_signals_from_m5(m5)
                result["m5"]["t1_shrink_expand"] = bool(t1)   # T1 缩转放(正T买点)
                result["m5"]["t_sell"] = bool(t_sell)         # 分时T出(第一次高点后停量+二次拉升无量不过前高)
        except Exception as e:
            print(f"[TMonitor] 分钟线快照失败 {symbol}: {e}")
        return result

    def _build_position_snapshot(self, symbol: str,
                                    account_id: Optional[str] = None) -> Dict[str, Any]:
        """持仓字段（监控账户）。"""
        try:
            from app.services.t_gateway import get_sellable_ledger
            ledger = get_sellable_ledger(account_id or T_MONITOR_ACCOUNT)
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
                       snapshot: Optional[dict] = None, ledger: Optional[dict] = None):
        """写入 t_triggers(pending, snapshot{suggest_bid/ask, slippage_budget, confidence, fields})。"""
        current = float(quote.get("current", 0) or 0)
        trigger_kind = cond.get("trigger_kind", "low_buy")
        symbol = cond["symbol"]
        # 滑点预算：0.1%（P4 标定 2-5 tick）
        slippage = 0.001
        gate = check_gate(trigger_kind, regime_state)
        mode = "human_confirm" if gate["mode"] == "human_confirm" else "auto"
        # 连续命中计数（同条件当日连续命中未实质改善 → 唤醒时提示 AI 调整/冷却）
        consecutive_hits = self._consecutive_hits(cond.get("id"), cond["symbol"])

        trig = {
            "account_id": cond.get("account_id", T_MONITOR_ACCOUNT),
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
            # 状态机（2026-09-02 修订）：狼大形态条件（分时T出/黄线/急杀/缩量触低等
            # 表达式腿）= **非消费式持续腿**——命中后保持 active+armed（不销毁），
            # 由 _round 5 分钟冷却防刷；使"买腿回补 T仓 → 卖腿持续监控 T出/黄线"的
            # 做T循环闭环（狼大：底仓不动、T仓高抛低吸反复做）。
            # 其他做T条件仍消费式（迭代#56：触发即销毁，由 AI 重建移动基准）。
            if _is_wolf_t_condition(cond):
                t_db.update_condition_state(
                    cond.get("id"),
                    armed=1,
                    status="active",
                    last_triggered_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    trigger_count_today=int(cond.get("trigger_count_today") or 0) + 1,
                )
            else:
                t_db.update_condition_state(
                    cond.get("id"),
                    armed=0,
                    status="consumed",
                    last_triggered_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    trigger_count_today=int(cond.get("trigger_count_today") or 0) + 1,
                )
            print(f"[TMonitor] 触发写入 #{trig_id} {cond['symbol']} {trigger_kind} "
                  f"mode={mode} consec_hits={consecutive_hits} @ {current}")
            # 迭代#58d（用户需求）：无需人工确认——MANUAL_ONLY（谨慎/下跌市低吸闸门）
            # 只作标记（mode 字段），不拦截自动执行；命中即按网关自动买入/卖出。
            # 其余硬风控（STOP_ALL/日亏熔断/连续亏损/裸空/跌停）仍在网关层把关。
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
                        # 狼大『T出=出 T 仓，黄线跌破直接走』（2026-09-02 修订）：
                        # 卖腿一次卖光 T 仓（sellable-100），底仓 100 不动——
                        # 不再 30% 分批：T仓单批大时 30% 卖不完，违背"当天低吸
                        # 当天 T出"节奏，且单日多次分批卖与狼大"每天进出一次"不符。
                        # T仓=0（只剩底仓）时 max_sell=0 → blocked 不卖底仓。
                        # 2026-09-03：底仓保留数按标的覆盖（默认100；SH588170 ETF 底仓66,900），
                        # 大底仓标的卖腿只清 T仓（持仓-底仓），绝不清底仓。
                        from app.services.t_gateway import base_floor_shares
                        _floor = base_floor_shares(
                            cond.get("account_id", T_MONITOR_ACCOUNT), symbol)
                        max_sell = max(sellable - _floor, 0) if sellable > _floor else 0
                        volume = max_sell
                    volume = (volume // 100) * 100
                exec_ok = False
                if volume > 0:
                    gw = gateway_execute(symbol, side, current, volume,
                                         reason=f"条件命中自动执行（{trigger_kind}）",
                                         decision_source="ai_led",
                                         condition_id=cond.get("id"),
                                         account_id=cond.get("account_id", T_MONITOR_ACCOUNT))
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
                    remain = list_active_conditions(
                        symbol=symbol,
                        account_id=cond.get("account_id", T_MONITOR_ACCOUNT))
                    # 成交后刷新持仓（无底仓建仓场景：执行前无持仓/成本）
                    fresh_item = {}
                    try:
                        from app.services.t_gateway import get_sellable_ledger
                        fresh_item = (get_sellable_ledger(
                            cond.get("account_id", T_MONITOR_ACCOUNT))
                            .get(symbol) or {})
                    except Exception:
                        pass
                    pos_volume = int(fresh_item.get("volume") or 0)
                    if not remain and pos_volume > 0:
                        from app.services.t_build import auto_gen_conditions_for_build, no_rebuild_symbols
                        # 迭代#58g：只减不补等禁重建标的——触发后不自动重建
                        # （防止消费式重建给它们补出低吸买腿）
                        if symbol in no_rebuild_symbols():
                            print(f"[TMonitor] 禁重建标的 {symbol}：触发后不自动重建（只减不补等语义）")
                        else:
                            avg_price = float(fresh_item.get("avg_price") or 0)
                            if avg_price <= 0 and exec_ok:
                                avg_price = float(gw.get("price") or current)  # 无底仓建仓：无历史成本，用成交价
                            if avg_price > 0:
                                from datetime import date
                                today = date.today().strftime("%Y%m%d")
                                ok = auto_gen_conditions_for_build(
                                    symbol, avg_price, trade_date=today,
                                    quote_price=current,
                                    account_id=cond.get("account_id",
                                                        T_MONITOR_ACCOUNT))
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
            conds = t_db.list_active_conditions(symbol=symbol,
                                                account_id=T_MONITOR_ACCOUNT)
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
                                 is_stop_loss=True,
                                 account_id=T_MONITOR_ACCOUNT)
            print(f"[TMonitor] 止损触发 {symbol} @ {current} x{volume}: {gw.get('status')}")
            # 迭代#58g：仅在止损**成交**后冻结当日条件——
            # 此前无条件冻结：T+1 当日买入 sellable=0 时止损被拒（rejected），
            # 仍把低吸/高抛条件冻成 armed=0 并与消费式重建打架（每轮刷屏）。
            if gw.get("status") == "success":
                for c in conds or []:
                    cid = c.get("id")
                    if cid:
                        t_db.update_condition_state(cid, armed=0)
        except Exception as e:
            print(f"[TMonitor] 止损扫描异常 {symbol}: {e}")


def _is_wolf_t_condition(cond: Dict[str, Any]) -> bool:
    """只允许狼大做T表达式条件(expression 含 minute.m5.t_sell / t1_shrink_expand); 其他做T条件(V反/探针/默认)不评估。"""
    expr = cond.get("expression")
    if not isinstance(expr, dict):
        return False
    import json as _json
    s = _json.dumps(expr, ensure_ascii=False)
    return any(f in s for f in WOLF_T_FIELDS)


# 结构性伪信号 bar（2026-09-02 复测发现, docs/t1-guard-reback-report.md）：
# 开盘两根(09:30/09:35)与午休后第一根(13:05)是 A股 结构性放量 bar——
# 指数184天中 T1 91% 命中午休效应假信号。T1 触发点排除这三根（阈值保持1.2x）。
T1_EXCLUDE_BARS = ("0930", "0935", "1305")


def _bar_hhmm(b) -> str:
    """从 bar 提取 HHMM（兼容腾讯 time='YYYYMMDDHHMM' / 带分隔符格式）"""
    t = str(b.get("trade_time") or b.get("time") or "")
    t = t.strip()
    if len(t) >= 12 and t.isdigit():
        return t[8:12]
    for sep in (" ", "T"):
        if sep in t:
            t = t.split(sep)[1]
    parts = t.split(":")
    if len(parts) >= 2:
        return parts[0] + parts[1]
    return t


def _t_signals_from_m5(m5):
    """做T信号(狼大体系): T1缩转放(日内缩量后放量=正T买点) + 分时T出(7-29原话: 放量反弹→第一次分时高点→停量→第二次拉升无量不过前高)
    输入: m5 bars [{time, close, vol,...}] 返回 (t1, t_sell)
    2026-09-02: T1 触发点排除 09:30/09:35/13:05 结构性伪信号 bar。"""
    import numpy as _np
    t1 = False; t_sell = False
    try:
        closes = _np.array([float(b["close"]) for b in m5])
        vols = _np.array([float(b["vol"]) for b in m5])
        times = [_bar_hhmm(b) for b in m5]
        n = len(closes)
        # T1 缩转放: 近8根缩量(末端<=起点) 且 最新放量(>前8均量1.2)；触发点排除结构性 bar
        if n >= 18 and times[-1] not in T1_EXCLUDE_BARS:
            prev = vols[-9:-1]
            if prev.mean() > 0:
                t1 = bool(prev[-1] <= prev[0] and vols[-1] > prev.mean() * 1.2)
        # 分时T出(7-29): 放量反弹(vol>前look1.3) → 第一次分时高点 → 高点后停量(<0.8) → 第二次拉升无量不过前高
        look = 8
        if n >= look * 4 + 4:
            start = None
            for i in range(look, n - look - 2):
                base = vols[max(0, i-look):i].mean()
                if base > 0 and vols[i] > base * 1.3:
                    start = i; break
            if start is not None:
                seg = closes[start:n-look]
                hi = float(seg.max()); hi_idx = start + int(seg.argmax())
                if hi_idx >= start + 2:
                    va = vols[hi_idx+1:hi_idx+1+look].mean() if hi_idx+look < n else 0
                    vb = vols[max(start, hi_idx-look):hi_idx].mean()
                    if vb > 0 and va < vb * 0.8:
                        after = closes[hi_idx+1:]
                        sh = float(after.max()) if len(after) else 0
                        t_sell = bool(sh > hi * 0.98 and sh < hi * 1.005)
    except Exception:
        pass
    return t1, t_sell


# 指数盘中回撤缓存（30s TTL，避免每轮拉腾讯）
_index_dd_cache = {"at": 0.0, "value": 0.0}
_m5_dump_cache = {"at": 0.0, "value": 0.0}
_prev_low_cache = {"at": 0.0, "sym": "", "value": False}


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
    """盘中换手节奏比（纯函数）：当前累计换手×时段伸缩 / 个股换手基准。

    公式：vol_ratio = [当前累计换手 × (240/已开盘连续分钟)] / 基准
    基准从 benchmark_turnover_profile.same_minute_avg 读（近5已完成交易日
    日换手均值, 见 t_turnover_profile），缺省用 MIN_TURNOVER_BASE(0.5%) 兜底。
    注意：这是"按当前节奏外推全天换手 ÷ 个股全天基准"的倍数（≈行情量比），
    不是累计换手率本身，也不是行情软件"每分钟均量/近5日同刻均量"的严格同刻口径。
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
