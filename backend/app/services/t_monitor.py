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
_BOARD_EXCLUDE = [x.strip() for x in os.getenv("WOLF_PICK_BOARD_EXCLUDE", "cyb,bj,kcb").split(",") if x.strip()]


def _board_tradable(symbol) -> bool:
    """2026-09-10 账户权限最终防线: 无权限板块(创业板/科创板/北交所)不评估不成交,
    防止任何来源(rotation/agent/rollover/手动)布出的腿被触发买入。"""
    s = str(symbol or "")
    p = s[:2]
    code = s[2:8]
    if "cyb" in _BOARD_EXCLUDE and p == "SZ" and code[:3] in ("300", "301"):
        return False
    if "kcb" in _BOARD_EXCLUDE and p == "SH" and code.startswith("688"):
        return False
    if "bj" in _BOARD_EXCLUDE and (p == "BJ" or code[:3] == "920" or code[:1] in ("4", "8")):
        return False
    return True


WOLF_T_FIELDS = ("minute.m5.t_sell", "index.intraday_dd", "quote.vwap_break", "index.m5_dump", "quote.dip_prev_low")


class TMonitor:
    """做T监控器：daemon 线程，30s 轮询 t_conditions，命中写 t_triggers。"""

    def __init__(self, interval_seconds: int = MONITOR_INTERVAL, trade_executor=None):
        self.interval = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._trade_executor = trade_executor  # ⑥ 253/254 无底仓建仓执行器（狼大建仓链），None 时保持纯做T gateway 路径
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
        self._wolf_done = set()   # (symbol, trigger_kind, date) 当日去抖，防同一腿反复触发
        self._wolf_bought_today = set()  # (symbol, date) 当日正T买入
        self._wolf_sold_today = set()    # (symbol, date) 当日确认制T出已卖

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
            self._arm_stock_exit_legs(datetime.now().strftime("%Y%m%d"))
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
                            self._arm_stock_exit_legs(today_d)
                        except Exception as e:
                            print(f"[TMonitor] 交易日持续腿结转异常: {e}")
                        if T_MONITOR_AUTO_MAINTAIN:   # 2026-09-02: 默认停自动维护(只留狼大做T条件)
                            self._status["daily_maintained"] = self._daily_maintain()
                            self._start_ai_maintain()
                    self._round()
                    self._settle_tsell_pending()  # 撤销式T出结算(放量过前高→撤销/超时→执行)
                    self._settle_pullback_sell()  # ②量能分层(2026-09-08): 缩量破位→反抽/尾盘确认离场
                    self._check_plan_triggers()   # 计划触发(复用同一监控器): 命中→唤醒交易agent
                    self._check_wolf_t_rules()    # 做T规则(向狼大看齐): 正T/倒T命中→写t_triggers
                    self._check_roundtrip_sell()  # B模型·等量换手(2026-09-08): 低吸后反弹≥+0.8%卖≤N旧仓
                    # day_end 已降级: 不做'未确认→必卖'(那批几乎全亏); 卖出仅靠确认制T出/defensive
                    self._check_defensive_t_reduce()  # 风险/结构恶化(量能不足+滞涨)→减已持T仓(08-27式)
                    self._check_board_half()  # 板上减半(狼大纪律②): 触及/接近涨停+浮盈达标→减半锁定
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

    def _check_plan_triggers(self) -> None:
        """计划触发检查(复用TMonitor 30s轮次): 命中armed计划→立即 run_trade_decision 唤醒交易agent。"""
        try:
            from app.services.plan_runner import plan_context
            from app.services.trade_graph import run_trade_decision
            block = plan_context()   # 评估armed计划, 命中→status=fired+写plan_replay_log, 返回上下文块
            if '🔔 计划命中' in block:
                res = run_trade_decision(
                    'auto_trade_plan_trigger',
                    'PLAN' + datetime.now().strftime('%Y%m%d%H%M%S'),
                    'plan trigger: 立即执行已命中的计划(回补/建仓/减半)')
                self._status['plan_fired'] = datetime.now().isoformat()
                print('[TMonitor] 🔔 计划触发→唤醒交易agent:', res.get('pi_stance'),
                      '|', (res.get('report') or '')[:120])
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] 计划触发检查异常: {e}")

    def _check_roundtrip_sell(self) -> None:
        """B模型·等量换手卖出检查(2026-09-08 落地): 当日低吸N后反弹≥+0.8%卖≤N股旧仓;
        昨低吸未完成→今日解锁继续(两日窗口); 超窗 stale 转人工。"""
        try:
            from app.services import roundtrip_sell as _rs
            if not _rs.ROUNDTRIP_ENABLED:
                return
            pend = _rs.pending_symbols()
            if not pend:
                return
            quotes = self._fetch_quotes_concurrent([s for s, _ in pend])
            for sym, st in pend:
                try:
                    q = quotes.get(sym) or {}
                    cur = float(q.get("current") or 0)
                    if cur <= 0:
                        continue
                    buy_avg = float(st.get("buy_avg") or 0)
                    target = buy_avg * (1 + _rs.ROUNDTRIP_SELL_UP)
                    avg = float(q.get("average") or q.get("avg_price") or 0)
                    vwap_break = bool(avg > 0 and cur < avg)   # 黄线破位优先离场（直跌保护）
                    if not vwap_break and cur < target:
                        continue
                    from app.services.t_gateway import (gateway_execute, get_sellable_ledger,
                                                        base_floor_shares)
                    acct = st.get("account") or "stock"
                    ledger = get_sellable_ledger(account_id=acct)
                    item = ledger.get(sym) or {}
                    sellable = int(item.get("sellable", 0) or 0)
                    floor = base_floor_shares(acct, sym, volume=sellable)
                    rem = _rs.remaining(sym)
                    vol = min(rem, max(sellable - floor, 0))
                    vol = (vol // 100) * 100
                    if vol < 100:
                        continue
                    gw = gateway_execute(
                        sym, "sell", cur, vol,
                        reason=f"[B等量换手] 低吸@{buy_avg:.2f}→反弹@{cur:.2f}(≥+0.8%) 卖回{vol}股",
                        decision_source="rule", account_id=acct)
                    if gw.get("status") == "success":
                        _rs.mark_sold(sym, vol)
                    else:
                        print(f"[RoundT] 换手卖出被拒 {sym}: {gw.get('reason')}")
                except Exception as e:
                    print(f"[RoundT] {sym} 检查异常: {e}")
        except Exception as e:
            print(f"[TMonitor] _check_roundtrip_sell 异常: {e}")

    def _prev_daily(self, sym, n=5):
        """最近 n 个交易日的 {close, high, low, vol}（读 data/recent_sync 或 stock_5m_bt）。"""
        import os as _os, json as _j
        D=_os.environ.get('DATA_DIR','/app/data')
        code6=''.join(ch for ch in str(sym) if ch.isdigit())[:6]
        data={}
        for root in ['stock_5m_bt','recent_sync']:
            p=_os.path.join(D,root,code6+'.json')
            try: d=_j.load(open(p,encoding='utf-8'))
            except Exception: continue
            for k,v in d.items():
                bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
                if bs: data.setdefault(k, {'close':float(bs[-1]['close']),
                                           'high':max(float(b['high']) for b in bs),
                                           'low':min(float(b['low']) for b in bs),
                                           'vol':sum(float(b.get('vol') or 0) for b in bs)})
        today=datetime.now().strftime('%Y%m%d')
        days=sorted(k for k in data if k<today and data[k].get('vol'))
        return [data[k] for k in days[-n:]]

    def _today_bars(self, sym):
        """当日 5min bars（读 recent_sync/stock_5m_bt/{code6}.json 的今天）。"""
        import os as _os, json as _j
        D=_os.environ.get('DATA_DIR','/app/data')
        code6=''.join(ch for ch in str(sym) if ch.isdigit())[:6]
        today=datetime.now().strftime('%Y%m%d')
        for root in ['recent_sync','stock_5m_bt']:
            p=_os.path.join(D,root,code6+'.json')
            try: d=_j.load(open(p,encoding='utf-8'))
            except Exception: continue
            if today in d: return d[today]
        return []

    def _log_cycle(self, sym, cyc):
        """把正T买→分时T出→收益 追加到 data/wolf_t_cycles.jsonl。"""
        import os as _os, json as _j
        D=_os.environ.get('DATA_DIR','/app/data')
        with open(_os.path.join(D,'wolf_t_cycles.jsonl'),'a',encoding='utf-8') as f:
            f.write(_j.dumps({'at':datetime.now().isoformat(),'symbol':sym,**cyc},ensure_ascii=False)+chr(10))

    def _insert_wolf_trigger(self, sym, kind, quote, reason):
        """把 wolf_t_rules 命中写成 t_triggers(pending/auto)，复用同一做T执行管道(不直接下单)。"""
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            current=float(quote.get('current') or 0)
        except Exception:
            current=0
        trig={
            "account_id": T_MONITOR_ACCOUNT, "condition_id": None, "symbol": sym,
            "event_type": kind, "trigger_price": None, "quote_price": current,
            "suggest_bid_price": round(current*0.999, 3), "suggest_ask_price": round(current*1.001, 3),
            "slippage_budget": 0.001,
            "snapshot": {"quote_time": now, "wolf_rule": reason, "trigger_kind": kind, "source": "wolf_t_rules"},
            "mode": "auto",
            "direction": "buy" if kind == "wolf_zheng_t_buy" else "sell",
        }
        try:
            tid = t_db.insert_trigger(trig)
            if tid:
                print(f"[TMonitor] wolf_t_rules触发 #{tid} {sym} {kind} @ {current} ({reason})")
        except Exception as e:
            print(f"[TMonitor] wolf_t_rules写触发异常: {e}")

    def _settle_pullback_sell(self) -> None:
        """②量能分层卖结算(2026-09-08): 缩量破位等待中的标的——
        反抽回 ref_up(支撑/黄线上沿) → 执行卖T仓; 14:45后仍未收回 → 尾盘确认离场; 处理完即清理。"""
        if not _PULLBACK_SELL:
            return
        try:
            from app.services.t_gateway import gateway_execute, resolve_sell_cap
            now_hm = datetime.now().hour * 100 + datetime.now().minute
            for sym in list(_PULLBACK_SELL.keys()):
                p = _PULLBACK_SELL[sym]
                try:
                    cur = 0.0
                    try:
                        from app.services.t_data_sources import fetch_tencent_quote, _normalize_symbol
                        _ns = _normalize_symbol(sym)
                        q = fetch_tencent_quote([_ns]).get(_ns) or {}
                        cur = float(q.get("current") or 0)
                    except Exception:
                        cur = 0.0
                    if cur <= 0:
                        continue
                    do_sell, reason_tag = False, ""
                    if cur >= float(p.get("ref_up") or 0):
                        do_sell, reason_tag = True, "反抽到离场位"
                    elif now_hm >= PULLBACK_END_HM:
                        do_sell, reason_tag = True, "14:45尾盘确认离场"
                    if not do_sell:
                        continue
                    cap = resolve_sell_cap(sym, account_id=p.get("account_id") or "stock")
                    vol = min(int(p.get("volume") or 0), cap)
                    vol = (vol // 100) * 100
                    if vol < 100:
                        del _PULLBACK_SELL[sym]
                        continue
                    gw = gateway_execute(sym, "sell", cur, vol,
                                         reason="[量能分层] %s %s 缩量破位后离场" % (reason_tag, p.get("kind")),
                                         trigger_id=p.get("trig_id"),
                                         decision_source="rule",
                                         account_id=p.get("account_id") or "stock")
                    if gw.get("status") == "success":
                        print(f"[TMonitor] 量能分层卖出 {sym} {vol}股@{cur} ({reason_tag})")
                    del _PULLBACK_SELL[sym]
                except Exception as e:
                    print(f"[TMonitor] pullback settle err {sym}: {str(e)[:100]}")
                    del _PULLBACK_SELL[sym]
        except Exception as e:
            print(f"[TMonitor] _settle_pullback_sell 异常: {str(e)[:120]}")

    def _settle_tsell_pending(self) -> None:
        """撤销式 T出 结算(2026-09-07): 对 pending 的 high_sell 观察——
        期间放量(vol>1.3×前均量)创新高(close>段高) → 撤销T出(继续持有等新高后新确认);
        无新高且超 TSELL_DELAY_S → 执行卖出; 14:45 后不强制(day_end 已降级, 允许跨日)。"""
        if not _TSELL_PENDING:
            return
        import time as _tm
        from app.services.t_gateway import gateway_execute
        from app.services.t_data_sources import fetch_minute_bars, fetch_tencent_quote
        today = datetime.now().strftime("%Y-%m-%d")
        for sym in list(_TSELL_PENDING.keys()):
            p = _TSELL_PENDING[sym]
            try:
                qs = _normalize_symbol(sym)
                bars = fetch_minute_bars(qs, freq="m5", count=60) or []
                tb = [b for b in bars if str(b.get("time") or b.get("trade_time")).startswith(today)]
                if not tb:
                    continue
                last = tb[-1]
                lc = float(last.get("close") or 0); lv = float(last.get("vol") or 0)
                dec = _tsell_undo_decide(p["hi"], p["base"], lc, lv, _tm.time() - p["ts"])
                q = (fetch_tencent_quote([qs]) or {}).get(qs) or {}
                cur = float(q.get("current") or 0)
                if dec == "undo":
                    t_db.update_trigger_status(p["trig_id"], "cancelled",
                                               reason=f"放量过前高({lc}>{p['hi']})→撤销本次T出(主升未完, 继续持有)")
                    print(f"[TMonitor] T出撤销 {sym} (放量过前高 {lc} > hi {p['hi']})")
                    del _TSELL_PENDING[sym]
                elif dec == "sell":
                    gw = gateway_execute(sym, "sell", cur if cur > 0 else p.get("last_price", cur),
                                         int(p["volume"]), reason="确认制T出(撤销式延迟无放量新高)",
                                         decision_source="ai_led", account_id=p.get("account_id", T_MONITOR_ACCOUNT))
                    ok = gw.get("status") == "success"
                    t_db.update_trigger_status(p["trig_id"], "executed" if ok else "blocked",
                                               reason=f"确认制T出(延迟确认): {gw.get('status')} | {str(gw.get('reason') or '')[:80]}")
                    print(f"[TMonitor] T出执行(延迟无新高) {sym} {p['volume']}股@{cur}: {gw.get('status')}")
                    del _TSELL_PENDING[sym]
            except Exception as ex:
                print(f"[TMonitor] settle_tsell err {sym}: {str(ex)[:100]}")

    def _check_wolf_t_rules(self) -> None:
        """做T规则(向狼大看齐): 对做T宇宙标的用实时quote算正T/倒T, 命中写 t_triggers(当日去抖)。"""
        try:
            from app.services.wolf_t_rules import zheng_t_buy_quote, dao_t_sell_quote
            conds = t_db.list_active_conditions(account_id=T_MONITOR_ACCOUNT)
            syms = sorted({c.get('symbol') for c in conds if _is_wolf_t_condition(c)})
            if not syms:
                return
            quotes = fetch_tencent_quote([_normalize_symbol(s) for s in syms])
            today = datetime.now().strftime('%Y%m%d')
            for sym in syms:
                q = quotes.get(_normalize_symbol(sym))
                if not q:
                    continue
                prev = self._prev_daily(sym, 5)
                if not prev:
                    continue
                b, rb = zheng_t_buy_quote(q, prev)
                d, rd = dao_t_sell_quote(q, prev)
                if b and (sym, 'wolf_zheng_t_buy', today) not in self._wolf_done:
                    try:
                        from app.services.wolf_t_rules import t_cycle_pnl
                        tb=self._today_bars(sym)
                        cyc=t_cycle_pnl(tb, 2.5) if tb else None
                        if cyc:
                            if cyc.get('sell'):
                                rb += ' | 正T买@%.2f→确认制T出@%.2f(+%.2f%%/日高+%.2f%%)' % (cyc['buy'], cyc['sell'], cyc['pnl'], cyc['pnl_dayhigh'])
                            else:
                                rb += ' | 正T买@%.2f 未确认→黄线/持有至次日/周五减T仓(不强制日结)' % (cyc['buy'])
                            self._log_cycle(sym, cyc)
                            if cyc.get('confirm'):
                                self._wolf_sold_today.add((sym, today))
                                self._insert_wolf_trigger(sym, 'wolf_confirm_sell', q, '确认制T出(停量+二次不过前高)')
                    except Exception:
                        pass
                    self._insert_wolf_trigger(sym, 'wolf_zheng_t_buy', q, rb)
                    self._wolf_bought_today.add((sym, today))
                    self._wolf_done.add((sym, 'wolf_zheng_t_buy', today))
                if d and (sym, 'wolf_dao_t_sell', today) not in self._wolf_done:
                    self._insert_wolf_trigger(sym, 'wolf_dao_t_sell', q, rd)
                    self._wolf_done.add((sym, 'wolf_dao_t_sell', today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] wolf_t_rules检查异常: {e}")

    def _check_day_end_de_t(self) -> None:
        """当日正T买(狼大T+0) + 尾盘14:45后仍未确认T出 → 减T仓(保留底仓); 隔日反T/周五例外. """
        try:
            now=datetime.now()
            if not (now.hour==14 and now.minute>=45) and now.hour!=15: return
            today=now.strftime('%Y%m%d')
            for sym in [s for s,t in self._wolf_bought_today if t==today]:
                if (sym, today) in self._wolf_sold_today: continue
                if (sym,'wolf_day_end_de_t',today) in self._wolf_done: continue
                q=fetch_tencent_quote([_normalize_symbol(sym)]).get(_normalize_symbol(sym))
                if not q: continue
                self._insert_wolf_trigger(sym, 'wolf_day_end_de_t', q, '当日正T买+尾盘未确认→减T仓(保留底仓)')
                self._wolf_done.add((sym,'wolf_day_end_de_t',today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] day_end_de_t异常: {e}")

    def _check_defensive_t_reduce(self) -> None:
        """防御性减T(风险/结构驱动): wave只做T + (量能不足 或 滞涨) → 写 wolf_defensive_t_reduce 减T触发(08-27/09-01式)."""
        try:
            from app.services.wolf_t_rules import defensive_t_reduce_quote, defensive_t_reduce_sw
            conds = t_db.list_active_conditions(account_id=T_MONITOR_ACCOUNT)
            active = {c.get('symbol') for c in conds if _is_wolf_t_condition(c)}
            today = datetime.now().strftime('%Y%m%d')
            syms = sorted(set(active) | {s for s,t in self._wolf_bought_today if t==today})
            if not syms: return
            quotes = fetch_tencent_quote([_normalize_symbol(s) for s in syms])
            for sym in syms:
                q = quotes.get(_normalize_symbol(sym))
                if not q: continue
                prev = self._prev_daily(sym, 5)
                if not prev: continue
                ok, reason = defensive_t_reduce_quote(q, prev, wave_op='t_only')
                if ok and (sym,'wolf_defensive_t_reduce',today) not in self._wolf_done:
                    self._insert_wolf_trigger(sym,'wolf_defensive_t_reduce',q,reason)
                    self._wolf_done.add((sym,'wolf_defensive_t_reduce',today))
                # 个股申万行业级防御(行业近高而个股未跟, 泛化非科技) → 独立触发 wolf_defensive_t_reduce_index
                sym_hi = float(q.get('high') or 0)
                sym_hi_prev = max([p.get('high') for p in prev if p.get('high')], default=0)
                iok, ireason = defensive_t_reduce_sw(sym, sym_hi, sym_hi_prev, wave_op='t_only')
                if iok and (sym,'wolf_defensive_t_reduce_index',today) not in self._wolf_done:
                    self._insert_wolf_trigger(sym,'wolf_defensive_t_reduce_index',q,ireason)
                    self._wolf_done.add((sym,'wolf_defensive_t_reduce_index',today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] defensive_t_reduce异常: {e}")

    def _check_board_half(self) -> None:
        """板上减半(狼大纪律②): 持仓当日触及/接近涨停(10%板>=9.5%, 20%板>=19.5%) 且 本轮浮盈>=3% -> 减半锁定.
        复用 trigger 管道写 wolf_board_half_sell(网关执行), 当日去抖."""
        try:
            from app.services.wolf_discipline import board_half
            from app.services.t_pool import _get_positions
            import json as _j, datetime as _dt
            pos_list = _get_positions()
            held = [p for p in pos_list if float(p.get('volume') or 0) > 0]
            if not held:
                return
            xq_syms = sorted({_normalize_symbol(p.get('symbol')) for p in held})
            quotes = fetch_tencent_quote(xq_syms)
            qmap = {s: {'current': float((quotes.get(s) or {}).get('current', 0) or 0),
                        'pre_close': float((quotes.get(s) or {}).get('pre_close', 0) or 0)} for s in xq_syms}
            portfolio = {"positions": [{"symbol": _normalize_symbol(p.get('symbol')),
                                        "avg_cost": float(p.get('avg_price') or p.get('avg_cost') or 0),
                                        "volume": float(p.get('volume') or 0)} for p in held]}
            bh = board_half(_j.dumps(portfolio, ensure_ascii=False), _dt.datetime.now(), quotes=qmap)
            today = _dt.datetime.now().strftime('%Y%m%d')
            for s in bh.get('active_sells') or []:
                sym = s.get('symbol')
                if (sym, 'wolf_board_half_sell', today) in self._wolf_done:
                    continue
                q = quotes.get(sym) or {}
                self._insert_wolf_trigger(sym, 'wolf_board_half_sell', q, s.get('reason', '板上减半锁定'))
                self._wolf_done.add((sym, 'wolf_board_half_sell', today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] board_half异常: {e}")

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

    def _arm_stock_exit_legs(self, today: str) -> int:
        """A方案(2026-09-08 用户拍板): 盘前/启动时对 stock 持仓自动布 3 条持续卖腿——
        黄线离场(custom_vwap_sell) / T出前高(high_sell, m5.t_sell) / 回撤跟踪(custom_trail_sell, 振幅自适应移动止盈)。
        幂等: 当日已有同键(symbol+trigger_kind)行(任意状态含manual/AI/auto)跳过, 不覆盖人工护栏。
        卖量仍由 _round 按 sellable−底仓floor 推导(底仓保护, 无T仓空间则自然跳过)。"""
        armed = 0
        try:
            import psycopg2 as _pg2
            _conn = _pg2.connect(os.getenv("DATABASE_URL",
                                           "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
            _cur = _conn.cursor()
            _cur.execute("SELECT DISTINCT symbol FROM paper_positions WHERE account_id='stock' AND volume > 0")
            syms = [str(r[0]) for r in _cur.fetchall()]
            _cur.close(); _conn.close()
        except Exception as e:
            print(f"[TMonitor] stock持仓读取失败: {e}")
            return 0
        if not syms:
            return 0
        templates = [
            ("custom_vwap_sell", {"and": [{"op": "==", "field": "quote.vwap_break", "value": True}]}),
            ("high_sell", {"and": [{"op": "==", "field": "minute.m5.t_sell", "value": True}]}),
            ("custom_trail_sell", {"and": [{"op": "==", "field": "quote.trail_break", "value": True}]}),
            # 步骤④/②: 跌破最近支撑位卖出腿（auto_exit 持续腿, 放量立减/缩量反抽减/尾盘确认）
            ("custom_support_sell", {"and": [{"op": "==", "field": "quote.break_support", "value": True}]}),
        ]
        keys_today = {(str(k.get("symbol")), str(k.get("trigger_kind")))
                       for k in t_db.list_condition_keys(T_MONITOR_ACCOUNT, today)}
        for sym in syms:
            for kind, expr in templates:
                if (sym, kind) in keys_today:
                    continue
                try:
                    cid = t_db.upsert_condition({
                        "account_id": T_MONITOR_ACCOUNT, "symbol": sym,
                        "trigger_kind": kind, "direction": "sell",
                        "trade_date": today, "status": "active", "armed": 1,
                        "publisher": "auto_exit", "expression": expr,
                    })
                    if cid:
                        armed += 1
                except Exception as e:
                    print(f"[TMonitor] auto_exit 布腿失败 {sym} {kind}: {e}")
        if armed:
            print(f"[TMonitor] stock持仓自动布卖出腿 {armed} 条 → {today}")
        return armed

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
        # 2026-09-08 防双卖: 同轮同一标的只允许成交一条卖腿(trail/vwap/high 同时命中时互斥,
        # 否则各自按轮初旧账本各卖一次把底仓卖穿, 512480 14:14 事故)
        self._sold_this_round = set()
        written = 0
        for cond in conditions:
            symbol = cond["symbol"]
            if not _board_tradable(symbol):
                continue
            if str(cond.get("direction") or "") == "sell" and symbol in self._sold_this_round:
                continue
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
                # 2026-09-09 根治: 条件缺当日换手基准 → 现场补算一次(当日缓存), 0.5% 仅最后保险
                self._ensure_benchmark_profile(cond)
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

    def _ensure_benchmark_profile(self, cond: Dict[str, Any]) -> None:
        """2026-09-09 根治: cond 缺当日换手基准时现场补算一次(当日缓存), 0.5% 仅最后保险。
        与 arm()/跨日结转同函数 compute_turnover_profile；写回 DB 供后续轮与次日结转复用。"""
        sym = cond.get("symbol")
        if not sym:
            return
        today = datetime.now().strftime("%Y%m%d")
        cache = getattr(self, "_bench_cache", None)
        if cache is None:
            cache = self._bench_cache = {}
        if cache.get(sym) == today:
            return
        cache[sym] = today          # 当日只尝试一次（失败保留 0.5% 兜底, 防每轮重拉）
        try:
            prof = cond.get("benchmark_turnover_profile") or {}
            if isinstance(prof, str):
                import json as _json
                prof = _json.loads(prof) if prof else {}
            ct = str(prof.get("computed_at") or "")[:10].replace("-", "")
            if prof.get("same_minute_avg") and ct == today:
                return
        except Exception:
            pass
        try:
            from app.services.t_turnover_profile import compute_turnover_profile
            np_ = compute_turnover_profile(sym)
        except Exception as e:
            print(f"[TMonitor] 基准补算失败 {sym}: {e}")
            np_ = None
        if not np_:
            return
        cond["benchmark_turnover_profile"] = np_
        try:
            import app.services.t_db as _tdb
            persist = {k: v for k, v in cond.items()
                       if k not in ("id", "created_at", "armed_at",
                                    "last_triggered_at", "trigger_count_today")}
            persist["status"] = "active"
            persist.setdefault("armed", 1)
            _tdb.upsert_condition(persist)
        except Exception as e:
            print(f"[TMonitor] 基准写库失败 {sym}: {e}")

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

    def _trail_break(self, symbol: str, cur: float, quote: dict) -> bool:
        """跌破当日高点×回撤阈值(动态移动止盈/破位保护)。阈值=振幅自适应:
        pct = max(0.004, min(0.015, amplitude×0.3))；env T_TRAIL_PCT 覆盖(>0 时)。
        不依赖任何静态价位——同一规则任何股票/任何交易日通用。"""
        try:
            if cur <= 0:
                return False
            hi = float(quote.get("high", 0) or 0)
            if hi <= 0 or cur > hi:
                return False
            amp = float(quote.get("amplitude", 0) or 0)
            try:
                fixed = float(os.getenv("T_TRAIL_PCT", "0") or 0)
            except Exception:
                fixed = 0.0
            pct = fixed if fixed > 0 else max(0.004, min(0.015, amp * 0.3))
            return cur <= hi * (1 - pct)
        except Exception:
            return False

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
            today = datetime.now().strftime("%Y%m%d")
            by_day = {}
            for b in sorted(bars, key=lambda x: str(x.get("time") or x.get("trade_time"))):
                # 时间戳为 12 位 YYYYMMDDHHMM：取前 8 位得到交易日期（旧 [:10] 会带小时导致
                # today 永远匹配不上、前日分组错乱 → A档 dip_prev_low/254 恒 False, 2026-09-07 修复）
                t = str(b.get("time") or b.get("trade_time"))[:8]
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
        # 波段支撑/压力位（步骤① 2026-09-08, support_resistance.compute_levels, 模块10min TTL缓存）
        _sup = []
        _res = []
        try:
            from app.services.support_resistance import compute_levels as _sr_compute
            _sr = _sr_compute(symbol)
            _sup = sorted([float(x["price"]) for x in _sr.get("support", []) if x.get("price")])
            _res = sorted([float(x["price"]) for x in _sr.get("resistance", []) if x.get("price")])
        except Exception as _sre:
            print(f"[TMonitor] 支撑/压力字段失败 {symbol}: {str(_sre)[:80]}")
        _s1 = _sup[-1] if _sup else 0.0
        _s2 = _sup[-2] if len(_sup) >= 2 else _s1
        _r1 = _res[0] if _res else 0.0
        _r2 = _res[1] if len(_res) >= 2 else _r1
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
            # 动态回撤保护(2026-09-08, 替代写死价位→任何标的/每日可复用):
            # 现价 ≤ 当日高点×(1-回撤阈值)；阈值=振幅自适应(max(0.4%, amp×0.3, ≤1.5%))，
            # 可用 env T_TRAIL_PCT 覆盖固定阈值。
            "trail_break": self._trail_break(symbol, _cur, quote),
            "support_l1": _s1,
            "support_l2": _s2,
            "resistance_l1": _r1,
            "resistance_l2": _r2,
            "break_support": bool(_s1 > 0 and _cur <= _s1),
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
            "direction": "buy" if _is_buy_side(cond) else "sell",
        }
        trig_id = t_db.insert_trigger(trig)
        if trig_id:
            # 状态机（2026-09-02 修订）：狼大形态条件（分时T出/黄线/急杀/缩量触低等
            # 表达式腿）= **非消费式持续腿**——命中后保持 active+armed（不销毁），
            # 由 _round 5 分钟冷却防刷；使"买腿回补 T仓 → 卖腿持续监控 T出/黄线"的
            # 做T循环闭环（狼大：底仓不动、T仓高抛低吸反复做）。
            # 其他做T条件仍消费式（迭代#56：触发即销毁，由 AI 重建移动基准）。
            # 2026-09-08: manual_guard 护栏卖腿命中即一次性消费；auto_exit(自动离场)持续监控
            _one_shot_guard = (str(cond.get("trigger_kind") or "") in ("custom_level_sell", "custom_vwap_sell")
                               and str(cond.get("publisher") or "") == "manual_guard")
            if _is_wolf_t_condition(cond) and not _one_shot_guard:
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
                            # 2026-09-07 试仓档(tranche_ladder): 254/253 低吸且标的在主线候选(ambush/trial)
                            # → 以档位上限放行(可沉淀底仓), 主升确认(normal, -1)走正常; none 保持做T原量
                            if trigger_kind in ("custom_prevlow", "custom_m5dump"):
                                try:
                                    import sys as _tl
                                    _tl.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                    "..", "..", "apps", "main_line"))
                                    from tranche_ladder import allowed_buy_volume
                                    _tlv, _tlr = allowed_buy_volume(
                                        symbol, trigger_kind, {"current": current},
                                        ledger, cond.get("account_id", T_MONITOR_ACCOUNT))
                                    if _tlv == -1:
                                        pass  # normal(主升确认): 正常建仓逻辑
                                    elif _tlv > 0:
                                        volume = max(_tlv, volume)   # 档位上限(不缩水原做T量)
                                    print(f"[TMonitor] tranche buy {symbol} {trigger_kind}: {_tlv} ({_tlr})")
                                except Exception as _tle:
                                    print(f"[TMonitor] tranche_ladder err: {str(_tle)[:80]}")
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
                            cond.get("account_id", T_MONITOR_ACCOUNT), symbol, volume=sellable)
                        max_sell = max(sellable - _floor, 0) if sellable > _floor else 0
                        volume = max_sell
                    volume = (volume // 100) * 100
                # ④破位禁低吸(2026-09-08): 现价已在最近支撑下方时禁止 254/253 自动低吸(防接刀)
                if (side == "buy" and trigger_kind in ("custom_prevlow", "custom_m5dump")
                        and (snapshot or {}).get("quote", {}).get("break_support")
                        and os.getenv("SR_NO_DIP_BUY", "1") != "0"):
                    t_db.update_trigger_status(trig_id, "blocked",
                                               reason="破位禁低吸(现价<=support_l1, ④门)")
                    print(f"[TMonitor] ④破位禁低吸 {symbol} {trigger_kind}")
                    return
                exec_ok = False
                # ⑥ 253/254 无底仓建仓 → 走狼大建仓链(而非做T gateway)；注 self._trade_executor 时生效，否则回退 gateway
                _no_hold_build = (
                    side == "buy"
                    and trigger_kind in ("custom_m5dump", "custom_prevlow")
                    and ((ledger or {}).get(symbol, {}).get("sellable", 0) or 0) <= 0
                    and self._trade_executor is not None
                )
                if _no_hold_build:
                    # 2026-09-07 试仓档方向门: 非主线候选(TOP1∪TOP2)标的无底仓不低吸建仓(防乱建)
                    _tier_none = False
                    try:
                        import sys as _tl2
                        _tl2.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         "..", "..", "apps", "main_line"))
                        from tranche_ladder import tier_for
                        _tier_none = tier_for(symbol)[0] == "none"
                    except Exception:
                        _tier_none = False
                    if _tier_none:
                        t_db.update_trigger_status(trig_id, "blocked", reason="非主线候选TOP1∪TOP2, 不低吸建仓(试仓档)")
                        print(f"[TMonitor] 试仓档拦无底仓建仓 {symbol}")
                    else:
                        try:
                            import datetime as _dtw
                            from app.services import wolf_253_build as _W
                            _today = _dtw.datetime.now().strftime("%Y%m%d")
                            if trigger_kind == "custom_m5dump":
                                _r = _W.build_253(self._trade_executor, symbol, quote, now_str=str(current),
                                                  account=cond.get("account_id", T_MONITOR_ACCOUNT),
                                                  snapshot=snapshot)  # P0-1: 透传快照供日志记录 m5_dump
                            else:
                                # 254 首现→建小底仓并记 base_254；其后 3 日内再次命中→分步回补(≤2次)
                                _chain = _W._chain_state().get(symbol) or {}
                                _vr = float(snapshot.get("vol_ratio") or 0)
                                if not _chain.get("base_254_date"):
                                    _r = _W.build_253(self._trade_executor, symbol, quote, now_str=str(current),
                                                      account=cond.get("account_id", T_MONITOR_ACCOUNT),
                                                      snapshot=snapshot)  # P0-1: 同上
                                    # 2026-09-08 修复(600004整天blocked): 仅建仓成功才记 base_254——
                                    # 首建失败(data_unavailable等)若也标记, 当日后续254命中全走refill被same_day拦, 全天建仓0
                                    if _r.get("status") == "success":
                                        _W.mark_base_254(symbol, _today)
                                else:
                                    _r = _W.refill_253(self._trade_executor, symbol, quote, _vr, _today,
                                                       account=cond.get("account_id", T_MONITOR_ACCOUNT))
                            exec_ok = _r.get("status") == "success"
                            print(f"[TMonitor] 狼大253/254建仓 {symbol}: {_r.get('status')} {str(_r.get('reason') or '')[:40]}")
                            t_db.update_trigger_status(trig_id, "executed" if exec_ok else "blocked",
                                                       reason="狼大253/254建仓: %s" % (_r.get("reason") or _r.get("status")))
                        except Exception as _we:
                            print(f"[TMonitor] 狼大253/254建仓异常 {symbol}: {_we}")
                            t_db.update_trigger_status(trig_id, "blocked", reason="wolf_253_build_exc")
                elif volume > 0:
                    # 板块级 G3 不做T门(2026-09-07): 持仓所属主题处洗盘收敛期 → 存量T仓不自动T出
                    # (盘前 sector_g3_state.json, 见 apps/main_line/sector_g3.py; env WOLF_NO_T_GATE=1 启用)
                    _g3_block, _g3_reason = False, ""
                    if side == "sell" and os.getenv("WOLF_NO_T_GATE", "0") != "0":
                        try:
                            import sys as _sg
                            _sg.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                            "..", "..", "apps", "main_line"))
                            from no_t_gate import g3_sell_blocked
                            _g3_block, _g3_reason = g3_sell_blocked(symbol)
                        except Exception as _ge:
                            print(f"[TMonitor] no_t_gate err: {str(_ge)[:80]}")
                    # 撤销式 T出(2026-09-07): high_sell(t_sell) 触发 → 进入观察期(不立即卖),
                    # 期间放量过前高则撤销(主升未完), 无新高且超 TSELL_DELAY_S 由 _settle_tsell_pending 执行
                    _tsell_defer = False
                    if not _g3_block and side == "sell" and trigger_kind == "high_sell" and _TSELL_UNDO                             and symbol not in _TSELL_PENDING:
                        try:
                            import time as _tm
                            _tb = self._today_bars(symbol)
                            if _tb:
                                _ctx = _tsell_hi_base(_tb)
                                if _ctx:
                                    _TSELL_PENDING[symbol] = {
                                        "hi": _ctx[0], "base": _ctx[1], "volume": int(volume),
                                        "trig_id": trig_id, "ts": _tm.time(),
                                        "account_id": cond.get("account_id", T_MONITOR_ACCOUNT)}
                                    t_db.update_trigger_status(
                                        trig_id, "claimed",
                                        reason=f"T出撤销式观察(hi={_ctx[0]:.3f}, {int(TSELL_DELAY_S)}s内放量过前高则撤销)")
                                    print(f"[TMonitor] T出进入撤销式观察 {symbol} hi={_ctx[0]:.3f} vol={volume}")
                                    _tsell_defer = True
                        except Exception as _te:
                            print(f"[TMonitor] tsell defer err: {str(_te)[:100]}")
                    # ②量能分层(2026-09-08): 跌破类离场腿缩量(<PULLBACK_VOL_RATIO)不立即卖——
                    # 进反抽减等待(claimed), 反抽回支撑/黄线上沿或14:45尾盘确认后由 _settle_pullback_sell 执行
                    _pb_defer = False
                    if (not _g3_block and not _tsell_defer and side == "sell"
                            and trigger_kind in ("custom_vwap_sell", "custom_trail_sell", "custom_support_sell")
                            and os.getenv("AUTO_PULLBACK_SELL", "1") != "0"
                            and symbol not in _PULLBACK_SELL):
                        try:
                            _vr = float((snapshot or {}).get("vol_ratio") or 0) if snapshot else 0.0
                            if 0 < _vr < PULLBACK_VOL_RATIO:
                                _q = (snapshot or {}).get("quote") or {}
                                _sup = float(_q.get("support_l1") or 0)
                                _vwap = float(_q.get("average") or 0) or float(quote.get("average") or 0)
                                _ref_up = max(_sup, _vwap)
                                if _ref_up <= 0:
                                    _ref_up = round(float(current) * 1.005, 4)
                                _PULLBACK_SELL[symbol] = {"kind": trigger_kind, "trig_id": trig_id,
                                                          "volume": int(volume), "ref_up": round(_ref_up, 4),
                                                          "sup": round(_sup, 4), "vwap": round(_vwap, 4),
                                                          "account_id": cond.get("account_id", T_MONITOR_ACCOUNT),
                                                          "ts": time.time()}
                                t_db.update_trigger_status(
                                    trig_id, "claimed",
                                    reason="缩量破位(%s量比%.2f<%.1f), 反抽减等待(ref_up=%.3f)" % (trigger_kind, _vr, PULLBACK_VOL_RATIO, _ref_up))
                                print(f"[TMonitor] {trigger_kind} 缩量破位进入反抽等待 {symbol} ref_up={_ref_up:.3f} vr={_vr}")
                                _pb_defer = True
                        except Exception as _pbe:
                            print(f"[TMonitor] pullback defer err: {str(_pbe)[:80]}")
                    if _g3_block:
                        exec_ok = False
                        print(f"[TMonitor] G3门拦截 {symbol} 卖腿: {_g3_reason}")
                        t_db.update_trigger_status(trig_id, "blocked", reason=_g3_reason + "（G3门）")
                    elif _tsell_defer or _pb_defer:
                        exec_ok = False   # 延迟执行(由 _settle_tsell_pending/_settle_pullback_sell 处理)
                    else:
                        gw = gateway_execute(symbol, side, current, volume,
                                             reason=f"条件命中自动执行（{trigger_kind}）",
                                             decision_source="ai_led",
                                             condition_id=cond.get("id"),
                                             account_id=cond.get("account_id", T_MONITOR_ACCOUNT))
                        exec_ok = gw.get("status") == "success"
                        if exec_ok and side == "sell":
                            # 同轮互斥: 该标的本轮已有卖腿成交, 其余离场腿本轮不再执行
                            self._sold_this_round.add(symbol)
                        print(f"[TMonitor] 自动执行 {symbol} {side} {volume}股@{current}: "
                              f"{gw.get('status')} {str(gw.get('reason') or '')[:40]}")
                        # 执行结果写入触发事件（供审计/复盘）
                        t_db.update_trigger_status(
                            trig_id, "executed" if exec_ok else "blocked",
                            reason=f"自动执行 {side} {volume}股 @{current}: {gw.get('status')} | {str(gw.get('reason') or '')[:120]} | level={gw.get('level')}")
                elif volume <= 0:
                    # 量推导为 0（卖腿仅剩底仓无T仓可卖 / 无底仓建仓规模不可用）→ 直接标记跳过，
                    # 避免孤儿 pending 事件（降级轮询兜底）；持仓仅100股(底仓)时不再当作"裸空"错误
                    _no_t_shop = (side == "sell")
                    t_db.update_trigger_status(
                        trig_id, "blocked",
                        reason=f"自动执行量推导为 0（{side}，{'仅底仓无T仓可卖，跳过卖出' if _no_t_shop else '无底仓建仓规模不可用'}）")
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
    # 2026-09-08: 手动护栏/自动离场卖腿(价位/黄线/回撤跟踪)纳入评估——此前因不含
    # WOLF_T_FIELDS字段被_round整轮跳过(155.2/588170黄线都不触发)
    if str(cond.get("trigger_kind") or "") in ("custom_level_sell", "custom_vwap_sell", "custom_trail_sell", "custom_support_sell"):
        return True
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


# ── T出"撤销式"(2026-09-07, 离线验证 3357 触发点): 放量过前高=主升未完 → 撤销本次 T出
# m5.t_sell 触发后不立即卖: 延迟观察 TSELL_DELAY_S, 期间若出现 vol>1.3×前均量 且 close>段高
# → 撤销(继续持有等新高后新确认); 无放量新高且达延迟 → 执行卖出; 跨日不强制尾盘卖(day_end 已降级)
_TSELL_PENDING = {}   # symbol -> {hi, base, volume, trig_id, ts, account_id}
_TSELL_UNDO = os.getenv("WOLF_TSELL_UNDO", "1") != "0"
TSELL_DELAY_S = float(os.getenv("TSELL_DELAY_S", "600"))


def _tsell_hi_base(bars):
    """重扫当日 m5(逻辑同 _t_signals_from_m5): t_sell 成立的段高点 hi 与高点前均量 base -> (hi,base) 或 None"""
    import numpy as _np
    try:
        closes = _np.array([float(b["close"]) for b in bars])
        vols = _np.array([float(b["vol"]) for b in bars])
        look = 8; n = len(closes)
        if n < look * 4 + 4:
            return None
        for i in range(look, n - look - 2):
            base = vols[max(0, i - look):i].mean()
            if base > 0 and vols[i] > base * 1.3:
                seg = closes[i:n - look]
                hi = float(seg.max()); hi_idx = i + int(seg.argmax())
                if hi_idx >= i + 2:
                    va = vols[hi_idx + 1:hi_idx + 1 + look].mean() if hi_idx + look < n else 0
                    vb = vols[max(i, hi_idx - look):hi_idx].mean()
                    if vb > 0 and va < vb * 0.8:
                        sh = float(closes[hi_idx + 1:].max()) if hi_idx + 1 < n else 0
                        if sh > hi * 0.98 and sh < hi * 1.005:
                            return (float(hi), float(vb))
        return None
    except Exception:
        return None


def _tsell_undo_decide(hi, base, last_close, last_vol, elapsed_s):
    """撤销式决策: 'undo'(放量过前高→撤销T出) / 'sell'(无新高且超延迟→执行) / 'wait'"""
    if base > 0 and last_vol > 1.3 * base and last_close > hi:
        return "undo"
    if elapsed_s >= TSELL_DELAY_S:
        return "sell"
    return "wait"


# 指数盘中回撤缓存（30s TTL，避免每轮拉腾讯）
_index_dd_cache = {"at": 0.0, "value": 0.0}
_m5_dump_cache = {"at": 0.0, "value": 0.0}
# ②量能分层卖(2026-09-08): 缩量破位→反抽减等待状态 + 支撑腿破位禁低吸
PULLBACK_VOL_RATIO = float(os.getenv("PULLBACK_VOL_RATIO", "1.2"))  # vol_ratio<该值视为缩量
PULLBACK_END_HM = 1445          # 14:45 后仍未反抽达标 → 尾盘确认离场
_PULLBACK_SELL: Dict[str, dict] = {}   # symbol -> pending(缩量破位待反抽/尾盘确认)

_prev_low_cache = {"at": 0.0, "sym": "", "value": False}


# ── 单例管理（对齐 candidate_pool_monitor 模式） ──
_monitor_instance: Optional[TMonitor] = None
_monitor_lock = threading.Lock()


def get_t_monitor(interval_seconds: int = MONITOR_INTERVAL, trade_executor=None) -> TMonitor:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = TMonitor(interval_seconds=interval_seconds, trade_executor=trade_executor)
        elif trade_executor is not None and _monitor_instance._trade_executor is None:
            _monitor_instance._trade_executor = trade_executor
        return _monitor_instance


def start_t_monitor(trade_executor=None) -> bool:
    monitor = get_t_monitor(trade_executor=trade_executor)
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
