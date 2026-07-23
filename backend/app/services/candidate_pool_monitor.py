# -*- coding: utf-8 -*-
"""候选池实时监控器 — 30 秒轮询，回调到位自动建仓。

与 StopLossMonitor 并行运行，共享同一个 MarcusVNPyExecutor。
入池候选标的在后台持续监控，一旦 check_entry_filters 通过，
自动执行 stance 判断 → calc_position → 建仓，无需等待下一个 Pi 窗口。

安全护栏：
  - 仅 green/yellow 立场自动建仓，red 立场跳过
  - Pi 窗口期间不建仓（避免与 Pi 决策冲突）
  - 早盘冷静期 9:30-9:45 不建仓
  - 每日自动建仓上限 3 笔（不含 Pi 手动建仓）
  - 午后 13:00 后更严格：额外检查涨幅≤3% + 分位≤60%
  - 硬拦截 (hard_block) 永不建仓
  - 单票上限、总仓位上限、现金底线均由 calc_position 验证
"""

import sys
import json
import time
import threading
import logging
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class CandidatePoolMonitor:
    """候选池实时监控器 — daemon 线程，30 秒轮询"""

    # A 股交易时段
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

    def __init__(self, executor=None, interval_seconds: int = 37):
        self.executor = executor
        self.interval = interval_seconds
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        # 每日自动建仓计数
        self.today_buys: Dict[str, int] = {}     # symbol → count
        self.max_daily_auto_buys = 3              # 每日自动建仓上限
        self.max_per_symbol_per_day = 1           # 单票每日最多自动买1次
        self._last_reset_date = ""

        # 通知记录
        self.notifications: List[Dict[str, Any]] = []

    # ── 生命周期 ──

    def start(self) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.thread = threading.Thread(
                target=self._run_loop, daemon=True, name="candidate-pool-monitor"
            )
            self.thread.start()
            logger.info(f"[建仓] ✅ 监控已启动，轮询间隔 {self.interval}s")
            return True

    def stop(self) -> None:
        with self.lock:
            self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        logger.info("[建仓] ⏹️ 监控已停止")

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
            "notifications": self.notifications[-20:],
        }

    # ── 主循环 ──

    def _run_loop(self) -> None:
        print("[建仓] 后台监控线程启动 (间隔=37s, 偏移=20s)", file=sys.stderr)
        time.sleep(20)  # 初始偏移，错开与其他监控器的首轮执行
        cycle = 0
        while self.running:
            cycle += 1
            self._cycle = cycle
            try:
                if not self._is_trading_day():
                    if cycle % 20 == 1:
                        print(f"[建仓] ⏸️ 非交易日，跳过 (cycle={cycle})", file=sys.stderr)
                elif self._is_trading_time() and not self._is_morning_volatility():
                    self._daily_reset()
                    self._check_candidates()
                else:
                    if cycle % 20 == 1:
                        label = "非交易时段" if not self._is_trading_time() else "早盘冷静期"
                        print(f"[建仓] ⏸️ {label}，跳过 (cycle={cycle})", file=sys.stderr)
            except Exception as e:
                logger.error(f"[建仓] 检查异常: {e}", exc_info=True)
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
                logger.info(f"[建仓] 非交易日: {reason}")
            return is_trade
        except Exception:
            logger.warning("[建仓] 交易日判定API不可用，降级为允许交易")
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

    def _is_afternoon(self) -> bool:
        now = datetime.now().time()
        return now >= self.LUNCH_END

    # ── 核心检查 ──

    def _check_candidates(self) -> None:
        if self.executor is None:
            return

        from app.services.candidate_pool import get_candidate_pool
        pool = get_candidate_pool()
        waiting = pool.get_waiting()
        if not waiting:
            return

        # Pi 窗口期间不建仓
        if self._is_pi_window():
            print(f"[建仓] ⏸️ Pi 窗口期，{len(waiting)} 只等待中，跳过自动建仓", file=sys.stderr)
            return

        # 每日自动建仓上限
        total_today = sum(self.today_buys.values())
        if total_today >= self.max_daily_auto_buys:
            print(f"[建仓] ⛔ 已达每日上限({self.max_daily_auto_buys}只)，跳过", file=sys.stderr)
            return

        print(f"[建仓] 🔄 第 {self._cycle} 轮检查 | {len(waiting)} 只候选 | {datetime.now().strftime('%H:%M:%S')}", file=sys.stderr)

        for entry in waiting:
            if total_today >= self.max_daily_auto_buys:
                break
            try:
                bought = self._evaluate_and_buy(entry)
                if bought:
                    total_today += 1
            except Exception as e:
                logger.error(f"[建仓] 评估 {entry.get('symbol')} 失败: {e}")

    def _evaluate_and_buy(self, entry: dict) -> bool:
        """对单个候选标的执行：check_entry_filters → stance check → calc_position → buy"""
        import asyncio
        from app.api.indicator import check_entry_filters
        from app.models.indicator import EntryCheckRequest

        symbol = entry.get("symbol", "")
        if not symbol:
            return False

        # 单票每日自动买上限
        if self.today_buys.get(symbol, 0) >= self.max_per_symbol_per_day:
            return False

        # ── 已在持仓中？跳过 ──
        try:
            positions = self.executor.engine.get_positions() if self.executor else {}
            held_symbols = {p.get('symbol', '') for p in positions} if isinstance(positions, list) else set()
            if symbol in held_symbols:
                return False
        except Exception as e:
            logger.error(f"[建仓] 查询持仓失败，保守拒绝买入 {symbol}: {e}")
            return False

        # ── Step 1: 重跑入场过滤 ──
        try:
            async def _run_filters():
                return await check_entry_filters(EntryCheckRequest(symbol=symbol))

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_run_filters())
            finally:
                loop.close()
        except Exception as e:
            logger.warning(f"[建仓] check_entry_filters failed for {symbol}: {e}")
            return False

        # ── 硬拦截 → 移出候选池 ──
        if result.hard_block or result.downgrade_multiplier <= 0:
            from app.services.candidate_pool import get_candidate_pool
            pool = get_candidate_pool()
            pool.mark_expired(symbol, f"结构恶化: {result.final_decision}")
            logger.info(f"[建仓] {symbol} → expired (hard_block={result.hard_block})")
            return False

        # ── 仍未通过 → 更新 last_reject ──
        if result.final_grade != "pass":
            entry["last_reject"] = {
                "final_grade": result.final_grade,
                "downgrade_multiplier": result.downgrade_multiplier,
                "layer1": result.layer1_tech.grade,
                "layer2": result.layer2_capital.grade,
                "layer3": result.layer3_overbought.grade,
                "hard_block": result.hard_block,
                "checked_at": datetime.now().isoformat(),
            }
            entry["checks_count"] = entry.get("checks_count", 0) + 1
            from app.services.candidate_pool import get_candidate_pool
            pool = get_candidate_pool()
            pool._save(pool._data)
            return False

        # ── Step 2: 过滤通过！执行 stance 检查 ──
        try:
            from core.utils.strategy_chain import StrategyChain
            chain = StrategyChain()
            pi_conf = chain.get_pi_confirmation()
            stance = pi_conf.get("stance", "yellow") if pi_conf else "yellow"
            position_limit = pi_conf.get("position_limit", 60) if pi_conf else 60
        except Exception:
            stance = "yellow"
            position_limit = 60

        # Red 立场不自动建仓
        if stance == "red":
            logger.info(f"[建仓] {symbol} 过滤通过但 stance=red，跳过自动建仓")
            return False

        # ── 午后额外检查：涨幅≤3% 且 分位≤60% ──
        if self._is_afternoon():
            if result.buy_confirmation.change_pct > 3:
                logger.info(f"[建仓] {symbol} 午后涨幅 {result.buy_confirmation.change_pct:.1f}%>3%，跳过")
                return False
            intraday_pct = result.tech.intraday_percentile
            if intraday_pct is not None and intraday_pct > 60:
                logger.info(f"[建仓] {symbol} 午后分位 {intraday_pct:.0f}%>60%，跳过")
                return False

        # ── Step 3: 仓位计算 ──
        chain_role = entry.get("chain_role", "mid")
        # 映射 chain_role 到 calc_position 的枚举值
        if "上游" in chain_role or "upstream" in chain_role:
            role = "upstream"
        elif "下游" in chain_role or "downstream" in chain_role:
            role = "downstream"
        else:
            role = "mid"

        try:
            from app.api.indicator import calc_position
            from app.models.indicator import CalcPositionRequest

            async def _run_calc():
                return await calc_position(CalcPositionRequest(
                    symbol=symbol,
                    signal_strength="medium",   # 自动建仓默认 medium
                    chain_role=role,
                    tier="probe",               # 自动建仓仅试探仓
                    stance=stance,
                ))

            loop = asyncio.new_event_loop()
            try:
                pos_result = loop.run_until_complete(_run_calc())
            finally:
                loop.close()
        except Exception as e:
            logger.warning(f"[建仓] calc_position failed for {symbol}: {e}")
            return False

        # ── Step 4: 验证 ──
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
            logger.info(f"[建仓] {symbol} 仓位验证失败: {'; '.join(failures)}")
            return False

        # ── Step 5: 执行建仓 ──
        buy_volume = pos_result.quantity.probe_shares
        if buy_volume < 100:
            logger.info(f"[建仓] {symbol} 建议股数 {buy_volume} < 100，跳过")
            return False

        buy_price = result.tech.current_price
        reason = (
            f"[CandidatePool自动建仓] 候选池回调到位自动建仓 | "
            f"stance={stance} role={role} tier=probe | "
            f"过滤: L1={result.layer1_tech.grade} L2={result.layer2_capital.grade} "
            f"L3={result.layer3_overbought.grade}"
        )

        try:
            buy_result = self.executor.buy(
                symbol=symbol,
                price=buy_price,
                volume=buy_volume,
                reason=reason,
            )
        except Exception as e:
            logger.error(f"[建仓] buy failed for {symbol}: {e}")
            return False

        if buy_result.get("status") in ("executed", "filled", "matched"):
            self.today_buys[symbol] = self.today_buys.get(symbol, 0) + 1

            from app.services.candidate_pool import get_candidate_pool
            pool = get_candidate_pool()
            pool.mark_promoted(symbol)

            msg = (
                f"✅ [建仓] 自动建仓: {symbol} "
                f"@{buy_price:.2f} × {buy_volume}股 "
                f"({pos_result.quantity.probe_pct:.1f}%仓位) | {entry.get('name', '')}"
            )
            logger.info(msg)
            print(msg, file=sys.stderr)

            self.notifications.append({
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "name": entry.get("name", ""),
                "price": buy_price,
                "volume": buy_volume,
                "amount": pos_result.quantity.probe_amount,
                "pct": pos_result.quantity.probe_pct,
            })
            return True
        else:
            reason_text = buy_result.get("reason", "未知")
            logger.warning(f"[建仓] {symbol} 建仓失败: {reason_text}")
            return False


# ── 全局单例 ──

_monitor_instance: Optional[CandidatePoolMonitor] = None
_monitor_lock = threading.Lock()


def get_candidate_pool_monitor(executor=None, interval_seconds: int = 37) -> CandidatePoolMonitor:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = CandidatePoolMonitor(executor=executor, interval_seconds=interval_seconds)
        elif executor is not None and _monitor_instance.executor is None:
            _monitor_instance.executor = executor
        return _monitor_instance


def start_pool_monitor(executor=None) -> bool:
    monitor = get_candidate_pool_monitor(executor=executor)
    return monitor.start()


def stop_pool_monitor() -> None:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is not None:
            _monitor_instance.stop()


def get_pool_monitor_status() -> Dict[str, Any]:
    monitor = get_candidate_pool_monitor()
    return monitor.status()
