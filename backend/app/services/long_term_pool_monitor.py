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

import sys
import json
import time
import threading
import logging
from datetime import datetime, time as dtime
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


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
        (dtime(9, 35), dtime(9, 43)),
        (dtime(9, 53), dtime(10, 2)),
        (dtime(10, 35), dtime(10, 43)),
        (dtime(13, 35), dtime(13, 43)),
        (dtime(14, 30), dtime(15, 0)),   # 尾盘不买
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
        }

    # ── 主循环 ──

    def _run_loop(self) -> None:
        print("[长期池] 后台监控线程启动 (间隔=300s, 偏移=40s)", file=sys.stderr)
        time.sleep(40)  # 初始偏移，错开其他监控器
        cycle = 0
        while self.running:
            cycle += 1
            try:
                if self._is_trading_time() and not self._is_morning_volatility():
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

    # ── 核心检查 ──

    def _check_candidates(self) -> None:
        if self.executor is None:
            return

        from app.services.long_term_pool import get_long_term_pool
        pool = get_long_term_pool()
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

        # 每日建仓上限
        total_today = sum(self.today_buys.values())
        if total_today >= self.max_daily_auto_buys:
            return

        self.last_check_time = datetime.now().isoformat()
        print(f"[长期池] 🔄 检查 {len(active)} 只候选 | {datetime.now().strftime('%H:%M:%S')}", file=sys.stderr)

        for entry in active:
            if total_today >= self.max_daily_auto_buys:
                break
            try:
                bought = self._evaluate_and_buy(entry)
                if bought:
                    total_today += 1
            except Exception as e:
                logger.error(f"[长期池] 评估 {entry.get('symbol')} 失败: {e}")

    def _evaluate_and_buy(self, entry: dict) -> bool:
        """对单个候选执行：已在持仓? → check_entry_filters → calc_position → buy"""
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
        except Exception:
            pass

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
            return False

        from app.services.long_term_pool import get_long_term_pool
        pool = get_long_term_pool()

        # ── 硬拦截 → 跳过但不淘汰 ──
        if result.hard_block or result.downgrade_multiplier <= 0:
            grade = "hard_block" if result.hard_block else "downgrade_zero"
            pool.update_check(symbol, grade)
            print(f"[长期池] {symbol} 硬拦截 ({grade})，跳过", file=sys.stderr)
            return False

        # ── 未通过 → 更新检查状态 ──
        if result.final_grade != "pass":
            pool.update_check(symbol, result.final_grade)
            return False

        # ── Step 2: 过滤通过，仓位计算 ──
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

            async def _run_calc():
                return await calc_position(CalcPositionRequest(
                    symbol=symbol,
                    signal_strength="medium",
                    chain_role=role,
                    tier="probe",
                    stance="green",  # 长期池固定用 green（不过滤）
                ))

            loop = asyncio.new_event_loop()
            try:
                pos_result = loop.run_until_complete(_run_calc())
            finally:
                loop.close()
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
        if buy_volume < 100:
            print(f"[长期池] {symbol} 建议股数 {buy_volume}<100，跳过", file=sys.stderr)
            return False

        buy_price = result.tech.current_price
        name = entry.get("name", "")
        reason = (
            f"[长期候选池自动建仓] {name} | "
            f"role={role} tier=probe | "
            f"过滤: L1={result.layer1_tech.grade} L2={result.layer2_capital.grade} "
            f"L3={result.layer3_overbought.grade}"
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
                f"({pos_result.quantity.probe_pct:.1f}%仓位)"
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
