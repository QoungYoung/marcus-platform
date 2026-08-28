# -*- coding: utf-8 -*-
"""长期观察候选池监控器 — 后台守护线程，5分钟轮询，条件满足自动建仓。

与短期候选池监控器 (CandidatePoolMonitor) 的区别：
- 轮询间隔 300s（vs 37s），长期观察不需要高频
- 无 PI 窗口跳过（仅尾盘 14:30-15:00 不建仓）
- 不做 stance 立场检查（长期视角不受短期立场限制）
- 不做午后额外检查（涨幅/分位不限制）
- 硬拦截不淘汰，只是跳过
- 已在持仓中的标的自动跳过
- 日建仓上限 5 笔（安全阀）
"""

import os
import sys
import json
import time
import threading
import logging
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

# 适配本地/Docker: 探测包含 core/utils/trade_day_utils.py 的项目根目录
_p = Path(__file__).resolve().parent
for _ in range(5):
    if (_p / "core" / "utils" / "trade_day_utils.py").exists():
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
        break
    _p = _p.parent


def _accept_entry_grade(result) -> bool:
    """长期池接受建仓的过滤结果：pass 全接受；probe_only 仅当 L2 极端超跌豁免时接受。

    2026-08-28 落地：L2 极端超跌豁免（5日主力<0 但 L1 过 + 前5日跌幅≥15%）
    降级为仅试探仓，避免误杀超跌反弹尾部大肉；其余 probe_only 仍拒绝。
    """
    if result.final_grade == "pass":
        return True
    if result.final_grade == "probe_only" and getattr(result, "l2_oversold_exempt", False):
        return True
    return False


# ── 弱市切红利ETF（REGIME_DIVIDEND_ETF_ENABLED=1 启用，2026-08-28 回测落地）──
ETF_SYMBOL = "SH515080"            # 中证红利ETF
ETF_ALIASES = {"SH515080", "515080", "515080.SH"}


def _etf_pivot_enabled() -> bool:
    """弱市切红利ETF 开关：1=启用（上证收盘<MA20 时清个股→满仓 515080，转强卖出）。"""
    return os.getenv("REGIME_DIVIDEND_ETF_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def _rank_candidate(result) -> float:
    """候选排序分（2026-08-28：当日过条件候选多于每日上限时，取分高者优先建仓）。

    基础 = 降级系数；加分：L1 通过 / MACD 金叉 / MA5>MA20 / 5日主力>0（L2 关但作为排序信号）
    / 今日主力>0；减分：日内分位偏高（追高）、涨幅>5%。
    """
    score = max(0.0, float(getattr(result, "downgrade_multiplier", 1.0) or 0))
    try:
        if result.layer1_tech.passed:
            score += 0.15
        if getattr(result.tech, "macd_status", "") == "金叉":
            score += 0.10
        if getattr(result.tech, "ma_status", "") == "MA5>MA20":
            score += 0.10
        if getattr(result.layer2_capital, "d5_main_net", 0) > 0:
            score += 0.10
        if getattr(result.layer2_capital, "today_main_net", 0) > 0:
            score += 0.05
        pct = getattr(result.tech, "intraday_percentile", None)
        if pct is not None:
            score -= 0.15 * max(0.0, min(pct / 50.0, 2.0))
        if result.buy_confirmation and getattr(result.buy_confirmation, "change_pct", 0) > 5:
            score -= 0.10
    except Exception:
        pass
    return round(score, 3)


class LongTermPoolMonitor:
    """长期候选池自动建仓监控器"""

    # 交易时段
    TRADING_START = dtime(9, 30)
    TRADING_END = dtime(15, 0)
    LUNCH_START = dtime(11, 30)
    LUNCH_END = dtime(13, 0)
    MORNING_QUIET_END = dtime(9, 45)

    # Pi 交易窗口时段（此时不自动建仓，避免与 Pi 决策冲突）
    PI_WINDOWS = [
        (dtime(9, 35), dtime(9, 40)),
        (dtime(9, 53), dtime(9, 58)),
        (dtime(10, 35), dtime(10, 40)),
        (dtime(13, 0), dtime(15, 0)),    # 午后不建仓
    ]

    def __init__(self, executor=None, interval_seconds: int = 300):
        self.executor = executor
        self.interval = interval_seconds
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        # 每日自动建仓计数
        self.today_buys: Dict[str, int] = {}     # symbol → count
        self.max_daily_auto_buys = 5              # 每日自动建仓上限（安全阀）
        self.max_per_symbol_per_day = 1           # 单票每日最多自动买 1 次
        self._last_reset_date = ""

        # 通知记录
        self.notifications: List[Dict[str, Any]] = []
        self.last_check_time: Optional[str] = None

        # 弱市切红利ETF 状态（REGIME_DIVIDEND_ETF_ENABLED=1 时生效）
        self._etf_holding = False                 # 当前是否持有 515080
        self._etf_last_action_day = ""            # 同一方向当日只执行一次
        self._regime_cache: Optional[bool] = None # 弱市判定缓存（600s TTL）
        self._regime_cache_ts = 0.0

        # 当日候选排序（2026-08-28：过条件后按 _rank_candidate 取前 N 建仓）
        self._last_ranking: List[tuple] = []      # [(symbol, name, score), ...]

    # ── 生命周期 ──

    def start(self) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.thread = threading.Thread(
                target=self._run_loop, daemon=True, name="long-term-pool-monitor"
            )
            self.thread.start()
            logger.info(f"[长期池] ✅ 监控已启动，轮询间隔 {self.interval}s")
            return True

    def stop(self) -> None:
        with self.lock:
            self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        logger.info("[长期池] ⏹️ 监控已停止")

    def is_running(self, check_thread: bool = True) -> bool:
        if not self.running:
            return False
        if check_thread:
            return self.thread is not None and self.thread.is_alive()
        return True

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "thread_alive": self.thread.is_alive() if self.thread else False,
            "interval_seconds": self.interval,
            "today_buys": dict(self.today_buys),
            "max_daily_auto_buys": self.max_daily_auto_buys,
            "has_executor": self.executor is not None,
            "is_trading_time": self._is_trading_time(),
            "is_pi_window": self._is_pi_window(),
            "last_check_time": self.last_check_time,
            "notifications": self.notifications[-20:],
            "regime_dividend_etf": {
                "enabled": _etf_pivot_enabled(),
                "weak": self._regime_cache,
                "holding_etf": self._etf_holding,
                "last_action_day": self._etf_last_action_day,
            },
            "last_ranking": [
                {"symbol": s, "name": n, "score": sc}
                for s, n, sc in (self._last_ranking or [])[:10]
            ],
        }

    # ── 主循环 ──

    def _run_loop(self) -> None:
        print("[长期池] 后台监控线程启动 (间隔=300s, 偏移=40s)", file=sys.stderr)
        time.sleep(40)  # 初始偏移，错开其他监控器
        cycle = 0
        while self.running:
            cycle += 1
            try:
                if not self._is_trading_day():
                    if cycle % 4 == 1:
                        print(f"[长期池] ⏸️ 非交易日，跳过 (cycle={cycle})", file=sys.stderr)
                elif self._is_trading_time() and not self._is_morning_volatility():
                    self._daily_reset()
                    self._check_candidates()
                else:
                    if cycle % 4 == 1:  # 约 20 分钟打印一次
                        label = "非交易时段" if not self._is_trading_time() else "早盘冷静期"
                        print(f"[长期池] ⏸️ {label}，跳过 (cycle={cycle})", file=sys.stderr)
            except Exception as e:
                logger.error(f"[长期池] 检查异常: {e}", exc_info=True)
            time.sleep(self.interval)

    def _daily_reset(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_reset_date != today:
            self.today_buys.clear()
            self._last_reset_date = today

    # ── 时间判断 ──

    def _is_trading_time(self) -> bool:
        now = datetime.now().time()
        return (
            (self.TRADING_START <= now <= self.LUNCH_START) or
            (self.LUNCH_END <= now <= self.TRADING_END)
        )

    def _is_trading_day(self) -> bool:
        """检查今天是否为交易日（带日缓存，避免频繁API调用）。

        硬守卫：周末一定不是交易日，不依赖外部 API。
        """
        if datetime.now().weekday() >= 5:
            return False

        today = datetime.now().strftime('%Y-%m-%d')
        if getattr(self, '_last_trading_day_check_date', '') == today:
            return getattr(self, '_last_trading_day_result', True)
        try:
            from core.utils.trade_day_utils import is_today_trade_day
            is_trade, reason = is_today_trade_day()
            self._last_trading_day_check_date = today
            self._last_trading_day_result = is_trade
            if not is_trade:
                logger.info(f"[长期池] 非交易日: {reason}")
            return is_trade
        except Exception as e:
            logger.warning(f"[长期池] 交易日判定API不可用，降级为允许交易: {e}")
            return True  # API 不可用时默认视为交易日（已在顶部拦截周末）

    def _is_morning_volatility(self) -> bool:
        now = datetime.now().time()
        return self.TRADING_START <= now < self.MORNING_QUIET_END

    def _is_pi_window(self) -> bool:
        """Pi 交易窗口期间不自动建仓，避免冲突。"""
        now = datetime.now().time()
        for start, end in self.PI_WINDOWS:
            if start <= now <= end:
                return True
        return False

    def _get_pi_stance(self) -> str:
        """获取 Pi 最新立场"""
        try:
            from core.utils.strategy_chain import StrategyChain
            chain = StrategyChain()
            pi_conf = chain.get_pi_confirmation()
            if pi_conf:
                return pi_conf.get('stance', 'yellow')
        except Exception:
            pass
        return 'yellow'

    # ── 弱市切红利ETF（可选开关）──

    def _regime_weak(self) -> Optional[bool]:
        """上证指数收盘 < MA20 → 弱市。数据失败返回 None（不阻断，维持原逻辑）。600s 缓存。"""
        now = time.time()
        if self._regime_cache_ts and now - self._regime_cache_ts < 600:
            return self._regime_cache
        weak: Optional[bool] = None
        try:
            from app.core.trading._api_config import get_tushare_pro
            pro = get_tushare_pro()
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")
            df = pro.index_daily(ts_code="000001.SH", start_date=start, end_date=end)
            if df is not None and not df.empty and len(df) >= 20:
                df = df.sort_values("trade_date")
                closes = df["close"].astype(float).tolist()
                weak = closes[-1] < sum(closes[-20:]) / 20
        except Exception as e:
            logger.warning(f"[长期池] 弱市判定失败（不阻断）: {e}")
        self._regime_cache, self._regime_cache_ts = weak, now
        return weak

    @staticmethod
    def _norm_symbol(sym: str) -> str:
        s = (sym or "").strip().upper()
        if s.startswith(("SH", "SZ", "BJ")):
            return s
        if s.endswith((".SH", ".SZ", ".BJ")):
            return s[-2:] + s[:-3]
        if s.isdigit() and len(s) == 6:
            return ("SH" if s[0] in "56" else "SZ") + s
        return s

    def _quote_price(self, symbol: str) -> float:
        """取现价（雪球），失败返回 0。"""
        try:
            from app.config import get_settings
            settings = get_settings()
            from xueqiu_engine import XueqiuEngine
            engine = XueqiuEngine(config_file=str(settings.workspace_path / "core" / "config.json"))
            q = engine.get_stock_quote(symbol)
            if q:
                return float(q.get("current", 0) or 0)
        except Exception as e:
            logger.warning(f"[长期池] 取价失败 {symbol}: {e}")
        return 0.0

    def _etf_pivot(self, weak: bool) -> None:
        """弱市：清可卖个股 → 满仓买 515080；转强：卖 515080。
        同一方向每日只执行一次；T+1 保护（当日买入不卖）；失败不污染状态。
        """
        if self.executor is None:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        if self._etf_last_action_day == today:
            return
        try:
            positions = self.executor.get_positions() or []
            account = self.executor.get_account() or {}
        except Exception as e:
            logger.error(f"[长期池] 弱市切ETF 获取账户失败: {e}")
            return
        today_buys = set()
        try:
            today_buys = set(self.executor._get_today_buy_symbols() or set())
        except Exception:
            pass

        if weak:
            # ── 清个股（保留 ETF、T+1 不卖）──
            sold = 0
            for p in positions:
                sym = self._norm_symbol(p.get("symbol", ""))
                vol = int(p.get("volume") or 0)
                if sym in ETF_ALIASES or sym in today_buys or vol <= 0:
                    continue
                price = float(p.get("current_price") or 0)
                if price <= 0:
                    price = self._quote_price(sym)
                if price <= 0:
                    logger.warning(f"[长期池] 弱市切ETF 无法取价，跳过卖出 {sym}")
                    continue
                try:
                    res = self.executor.sell(symbol=sym, price=price, volume=vol,
                                             reason="[弱市切红利ETF] 上证<MA20清仓避险",
                                             skip_trend_constraint=True)
                    if res and res.get("status") in ("executed", "filled", "matched"):
                        sold += vol
                except Exception as e:
                    logger.error(f"[长期池] 弱市切ETF 卖出异常 {sym}: {e}")
            # ── 买 ETF（可用现金 98%，留缓冲）──
            cash = float(account.get("available_cash") or 0)
            etf_price = self._quote_price(ETF_SYMBOL)
            if cash > 5000 and etf_price > 0:
                shares = int(cash * 0.98 / etf_price / 100) * 100
                if shares >= 100:
                    try:
                        res = self.executor.buy(symbol=ETF_SYMBOL, price=etf_price, volume=shares,
                                                reason="[弱市切红利ETF] 满仓买入中证红利ETF(515080)")
                        if res and res.get("status") in ("executed", "filled", "matched"):
                            self._etf_holding = True
                            msg = f"🛡️ [弱市切红利ETF] 上证<MA20，清个股{sold}股 → 买入515080 {shares}份（现金{cash:.0f}）"
                            logger.info(msg)
                            print(msg, file=sys.stderr)
                            self.notifications.append({"timestamp": datetime.now().isoformat(), "event": "pivot_to_etf",
                                                       "sold_shares": sold, "etf_shares": shares})
                    except Exception as e:
                        logger.error(f"[长期池] 弱市切ETF 买入异常: {e}")
            else:
                logger.warning(f"[长期池] 弱市切ETF 现金不足或无报价: cash={cash:.0f} price={etf_price}")
            self._etf_last_action_day = today
        else:
            # ── 转强：卖 ETF ──
            sold_etf = 0
            for p in positions:
                sym = self._norm_symbol(p.get("symbol", ""))
                vol = int(p.get("volume") or 0)
                if sym not in ETF_ALIASES or vol <= 0:
                    continue
                price = float(p.get("current_price") or 0)
                if price <= 0:
                    price = self._quote_price(sym)
                if price <= 0:
                    continue
                try:
                    res = self.executor.sell(symbol=sym, price=price, volume=vol,
                                             reason="[弱市切红利ETF] 上证重回MA20上方，卖出ETF",
                                             skip_trend_constraint=True)
                    if res and res.get("status") in ("executed", "filled", "matched"):
                        sold_etf += vol
                except Exception as e:
                    logger.error(f"[长期池] 弱市切ETF 卖ETF异常: {e}")
            if sold_etf > 0:
                self._etf_holding = False
                msg = f"🛡️ [弱市切红利ETF] 上证重回MA20上方，卖出515080 {sold_etf}份，恢复个股建仓"
                logger.info(msg)
                print(msg, file=sys.stderr)
                self.notifications.append({"timestamp": datetime.now().isoformat(), "event": "pivot_back", "etf_shares": sold_etf})
            self._etf_last_action_day = today

    # ── 核心检查 ──

    def _check_candidates(self) -> None:
        if self.executor is None:
            return

        from app.services.long_term_pool import get_long_term_pool
        pool = get_long_term_pool()

        # ── 弱市切红利ETF（可选）：弱市清个股买ETF、转强卖ETF；弱市跳过候选建仓 ──
        if _etf_pivot_enabled():
            weak = self._regime_weak()
            if weak is not None:
                self._etf_pivot(weak)
                if weak:
                    print(f"[长期池] ⏸️ 弱市(上证<MA20)，跳过候选建仓（REGIME_DIVIDEND_ETF_ENABLED）",
                          file=sys.stderr)
                    return

        # ── 清仓复位：已 promoted 但持仓已清 → 恢复 active ──
        promoted = pool.get_promoted()
        if promoted:
            try:
                positions = self.executor.get_positions() if self.executor else {}
                held_symbols = {p.get('symbol', '') for p in positions} if isinstance(positions, list) else set()
                for entry in promoted:
                    sym = entry.get("symbol", "")
                    if sym and sym not in held_symbols:
                        pool.reset_to_active(sym)
            except Exception:
                pass

        active = pool.get_active()
        if not active:
            if hasattr(self, '_last_active_count') and self._last_active_count != 0:
                print(f"[长期池] 无 active 候选", file=sys.stderr)
            self._last_active_count = 0
            return

        self._last_active_count = len(active)

        # Pi 窗口期间不建仓
        if self._is_pi_window():
            print(f"[长期池] ⏸️ Pi 窗口期，{len(active)} 只活跃，跳过建仓", file=sys.stderr)
            return

        # Red 立场不建仓
        stance = self._get_pi_stance()
        if stance == 'red':
            print(f"[长期池] ⛔ Pi 立场 RED，{len(active)} 只活跃，禁止建仓", file=sys.stderr)
            return

        # 每日建仓上限
        total_today = sum(self.today_buys.values())
        if total_today >= self.max_daily_auto_buys:
            return

        self.last_check_time = datetime.now().isoformat()
        print(f"[长期池] 🔄 检查 {len(active)} 只候选 | {datetime.now().strftime('%H:%M:%S')}", file=sys.stderr)

        # ── 先收集当日全部合格候选（含排序分），再取最优前 N 建仓
        #    （2026-08-28 优化：替代按池顺序先到先买）──
        eligible = []  # (entry, result, score)
        for entry in active:
            try:
                ev = self._evaluate(entry)
                if ev is not None:
                    eligible.append(ev)
            except Exception as e:
                logger.error(f"[长期池] 评估 {entry.get('symbol')} 失败: {e}")

        eligible.sort(key=lambda x: x[2], reverse=True)
        self._last_ranking = [(e[0].get("symbol", ""), e[0].get("name", ""), e[2]) for e in eligible]
        if eligible:
            rank_str = " > ".join(f"{s}({sc:.2f})" for s, _, sc in self._last_ranking[:10])
            print(f"[长期池] 📊 当日合格 {len(eligible)} 只，排序: {rank_str}", file=sys.stderr)

        total_today = sum(self.today_buys.values())
        for entry, result, score in eligible:
            if total_today >= self.max_daily_auto_buys:
                break
            try:
                if self._execute_buy(entry, result, score):
                    total_today += 1
            except Exception as e:
                logger.error(f"[长期池] 建仓 {entry.get('symbol')} 失败: {e}")

    def _evaluate(self, entry: dict):
        """只评估候选：持仓/日限/弱市 → check_entry_filters → 分级 → 排序分。
        返回 (entry, result, score) 或 None（未通过）。不执行下单。"""
        import asyncio
        from app.api.indicator import check_entry_filters
        from app.models.indicator import EntryCheckRequest

        symbol = entry.get("symbol", "")
        if not symbol:
            return None

        # 弱市切红利ETF：弱市禁止个股建仓（双保险）
        if _etf_pivot_enabled():
            weak = self._regime_weak()
            if weak:
                return None

        # 单票每日自动买上限
        if self.today_buys.get(symbol, 0) >= self.max_per_symbol_per_day:
            return None

        # ── 已在持仓中？跳过 ──
        try:
            positions = self.executor.get_positions() if self.executor else {}
            held_symbols = {p.get('symbol', '') for p in positions} if isinstance(positions, list) else set()
            if symbol in held_symbols:
                return None
        except Exception as e:
            logger.error(f"[长期池] 查询持仓失败，保守拒绝 {symbol}: {e}")
            return None

        # ── Step 1: 入场过滤 ──
        try:
            async def _run_filters():
                return await check_entry_filters(EntryCheckRequest(symbol=symbol))

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_run_filters())
            finally:
                loop.close()
        except Exception as e:
            logger.warning(f"[长期池] check_entry_filters failed for {symbol}: {e}")
            return None

        from app.services.long_term_pool import get_long_term_pool
        pool = get_long_term_pool()

        # ── 硬拦截 → 跳过但不淘汰 ──
        if result.hard_block or result.downgrade_multiplier <= 0:
            grade = "hard_block" if result.hard_block else "downgrade_zero"
            pool.update_check(symbol, grade)
            print(f"[长期池] {symbol} 硬拦截 ({grade})，跳过", file=sys.stderr)
            return None

        # ── 未通过 → 更新检查状态 ──
        # 2026-08-28：L2 极端超跌豁免（final_grade=probe_only + l2_oversold_exempt=True）允许试探仓建仓
        if not _accept_entry_grade(result):
            pool.update_check(symbol, result.final_grade)
            return None

        score = _rank_candidate(result)
        return (entry, result, score)

    def _execute_buy(self, entry: dict, result, score: float) -> bool:
        """对已通过评估的候选执行：calc_position → 验证 → 建仓。"""
        symbol = entry.get("symbol", "")
        from app.services.long_term_pool import get_long_term_pool
        pool = get_long_term_pool()

        # ── Step 2: 仓位计算 ──
        chain_role = entry.get("chain_role", "mid")
        if "上游" in chain_role or "upstream" in chain_role:
            role = "upstream"
        elif "下游" in chain_role or "downstream" in chain_role:
            role = "downstream"
        else:
            role = "mid"

        try:
            from app.api.indicator import calc_position
            from app.models.indicator import CalcPositionRequest

            pos_result = calc_position(CalcPositionRequest(
                symbol=symbol,
                signal_strength="medium",
                chain_role=role,
                tier="probe",
                stance="green",  # 长期池固定用 green（不过滤）
            ))
        except Exception as e:
            logger.warning(f"[长期池] calc_position failed for {symbol}: {e}")
            return False

        # ── Step 3: 验证 ──
        if not pos_result.all_pass:
            failures = []
            v = pos_result.validation
            if not v.single_cap_ok:
                failures.append(v.single_cap_detail)
            if not v.total_position_ok:
                failures.append(v.total_position_detail)
            if not v.cash_reserve_ok:
                failures.append(v.cash_reserve_detail)
            if not v.max_loss_ok:
                failures.append(v.max_loss_detail)
            print(f"[长期池] {symbol} 仓位验证失败: {'; '.join(failures)}", file=sys.stderr)
            pool.update_check(symbol, "validation_failed")
            return False

        # ── Step 4: 执行建仓 ──
        buy_volume = pos_result.quantity.probe_shares
        if getattr(result, "l2_oversold_exempt", False):
            # 极端超跌豁免：仓位再压至 5% 总资产以内（仅试探仓）
            cap5 = int(pos_result.total_asset * 0.05 / pos_result.current_price / 100) * 100
            if cap5 > 0:
                buy_volume = min(buy_volume, cap5)
        if buy_volume < 100:
            # 长期候选池：如最低股数超出仓位限制，买入最低股数100
            min_cost = 100 * pos_result.current_price
            if pos_result.available_cash >= min_cost:
                buy_volume = 100
                print(f"[长期池] {symbol} 建议股数不足100，强制买入100股 (成本{min_cost:.0f})", file=sys.stderr)
            else:
                print(f"[长期池] {symbol} 建议股数 {buy_volume}<100 且现金不足({pos_result.available_cash:.0f}<{min_cost:.0f})，跳过", file=sys.stderr)
                return False

        buy_price = result.tech.current_price
        name = entry.get("name", "")
        reason = (
            f"[长期候选池自动建仓] {name} | "
            f"role={role} tier=probe | "
            f"过滤: L1={result.layer1_tech.grade} L2={result.layer2_capital.grade} "
            f"L3={result.layer3_overbought.grade}"
            + (" L2豁免=极端超跌" if getattr(result, "l2_oversold_exempt", False) else "")
            + f" | 排序分={score}"
        )

        try:
            buy_result = self.executor.buy(
                symbol=symbol, price=buy_price, volume=buy_volume, reason=reason
            )
        except Exception as e:
            logger.error(f"[长期池] {symbol} 买入异常: {e}")
            return False

        if buy_result and buy_result.get("status") in ("executed", "filled", "matched"):
            pool.mark_promoted(symbol)
            self.today_buys[symbol] = self.today_buys.get(symbol, 0) + 1

            msg = (
                f"✅ [长期候选池] 自动建仓: {symbol} {name} "
                f"@{buy_price:.2f} × {buy_volume}股 "
                f"({pos_result.quantity.probe_pct:.1f}%仓位, 排序分{score})"
            )
            logger.info(msg)
            print(msg, file=sys.stderr)

            self.notifications.append({
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "name": name,
                "price": buy_price,
                "volume": buy_volume,
                "amount": pos_result.quantity.probe_amount,
                "pct": pos_result.quantity.probe_pct,
                "score": score,
            })
            return True
        else:
            reason_text = buy_result.get("reason", "未知") if buy_result else "无返回"
            print(f"[长期池] {symbol} 建仓失败: {reason_text}", file=sys.stderr)
            return False


# ── 全局单例 ──

_monitor_instance: Optional[LongTermPoolMonitor] = None
_monitor_lock = threading.Lock()


def get_long_term_pool_monitor(executor=None, interval_seconds: int = 300) -> LongTermPoolMonitor:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = LongTermPoolMonitor(executor=executor, interval_seconds=interval_seconds)
        elif executor is not None and _monitor_instance.executor is None:
            _monitor_instance.executor = executor
        return _monitor_instance


def start_lt_pool_monitor(executor=None) -> bool:
    monitor = get_long_term_pool_monitor(executor=executor)
    return monitor.start()


def stop_lt_pool_monitor() -> None:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is not None:
            _monitor_instance.stop()


def get_lt_pool_monitor_status() -> Dict[str, Any]:
    global _monitor_instance
    if _monitor_instance is None:
        return {"running": False, "reason": "未初始化"}
    return _monitor_instance.status()
