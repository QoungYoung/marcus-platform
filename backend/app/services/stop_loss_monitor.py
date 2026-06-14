#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时止损监控器 — 独立后台线程持续轮询持仓价格，触发止损规则自动卖出。

止损规则（优先级从高到低）：
  0a. 破底止损（锚点动态上移）：跌破 max(阶段底×0.97, 入场后最高收盘×0.90)
  0b. 成本止损：从未盈利→-4% / 曾小盈转亏→-3% / 无HWM→-6%
  1.  板块背离止损：个股日收益 - 板块日收益 < -3pp（差值法）
  2.  铁律二移动止盈（含HWM增强）：
      - HWM曾大盈≥5% → 保本离场(-1%)
      - 浮盈≥8%→保护线+6% / ≥5%→+3.5% / ≥3%→+1% / ≥1%→保本
  3.  大盘相对表现止损：大盘跌>2%且个股跌幅-大盘跌幅<-3pp→强审
  4.  T+1 保护：今日买入的持仓不执行止损卖出
  5.  早盘冷静期：09:30-09:45 不执行卖出（该窗口统计胜率 0%）

规则冲突 SOP：
  - 规则按优先级 0a→0b→1→2→3 依次评估，首个命中即执行，后续规则不再检查
  - 规则 4（T+1）和规则 5（冷静期）为前置拦截，不参与优先级竞争
  - 单日单票最多执行 3 次止损，同一价位不可重复触发
"""

import sys
import os
import json
import time
import threading
import logging
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class StopLossMonitor:
    """
    实时止损监控器。
    
    用法:
        monitor = StopLossMonitor(executor_instance)
        monitor.start()   # 启动后台线程
        monitor.stop()    # 停止后台线程
    """

    # A股交易时段（北京时间）
    TRADING_START = dtime(9, 30)
    TRADING_END = dtime(15, 0)
    LUNCH_START = dtime(11, 30)
    LUNCH_END = dtime(13, 0)
    # 早盘冷静期：09:30-09:45 开盘波动剧烈，统计显著性显示此窗口卖出胜率 0%
    MORNING_QUIET_END = dtime(9, 45)

    def __init__(self, executor=None, interval_seconds: int = 30):
        self.executor = executor
        self.interval = interval_seconds
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        self.today_stops: Dict[str, int] = {}
        self._triggered: Dict[str, float] = {}
        self._strategy_chain = None

        self.log_dir = self._resolve_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_log_dir(self) -> Path:
        try:
            from workspace_detector import DATA_DIR
            return Path(str(DATA_DIR))
        except Exception:
            return Path(__file__).parent.parent.parent.parent / "data"

    @property
    def strategy_chain(self):
        if self._strategy_chain is None:
            try:
                from core.utils.strategy_chain import StrategyChain
                self._strategy_chain = StrategyChain()
            except Exception:
                pass
        return self._strategy_chain

    # ── 生命周期 ──

    def start(self) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True, name="stop-loss-monitor")
            self.thread.start()
            logger.info(f"[StopLoss] ✅ 监控已启动，轮询间隔 {self.interval}s")
            return True

    def stop(self) -> None:
        with self.lock:
            self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        logger.info("[StopLoss] ⏹️ 监控已停止")

    def is_running(self) -> bool:
        return self.running

    # ── 主循环 ──

    def _run_loop(self) -> None:
        print("[StopLoss] 后台监控线程启动", file=sys.stderr)
        while self.running:
            try:
                if self._is_trading_time():
                    self._check_all_positions()
                else:
                    self._daily_reset()
            except Exception as e:
                logger.error(f"[StopLoss] 检查异常: {e}", exc_info=True)
            time.sleep(self.interval)

    def _is_trading_time(self) -> bool:
        now = datetime.now().time()
        morning = self.TRADING_START <= now <= self.LUNCH_START
        afternoon = self.LUNCH_END <= now <= self.TRADING_END
        return morning or afternoon

    def _is_morning_volatility(self) -> bool:
        """早盘冷静期（09:30-09:45）：延迟卖出，等波动收敛后再执行。"""
        now = datetime.now().time()
        return self.TRADING_START <= now < self.MORNING_QUIET_END

    def _daily_reset(self) -> None:
        today = datetime.now().strftime('%Y-%m-%d')
        if getattr(self, '_last_reset_date', '') != today:
            self.today_stops.clear()
            self._triggered.clear()
            self._last_reset_date = today
            if self.executor:
                try:
                    self.executor.reset_consecutive_losses()
                except Exception:
                    pass

    # ── HWM 辅助（P0-2: 监控器内部直接更新） ──

    def _ensure_hwm(self, symbol: str, current_price: float) -> dict:
        """确保 HWM 已更新并返回最新数据"""
        try:
            chain = self.strategy_chain
            if chain and current_price > 0:
                chain.update_high_water_mark(symbol, current_price)
                return chain.get_high_water_mark(symbol) or {}
        except Exception:
            pass
        return {}

    # ── 核心检查逻辑 ──

    def _check_all_positions(self) -> None:
        if self.executor is None:
            return

        try:
            account = self.executor.get_account()
            positions = self.executor.get_positions()
        except Exception as e:
            logger.warning(f"[StopLoss] 获取账户/持仓失败: {e}")
            return

        if not positions:
            return

        market_pct = self._get_market_change_pct()
        today_buy_symbols = self.executor._get_today_buy_symbols() if self.executor else set()

        for pos in positions:
            symbol = pos.get('symbol', '')
            if not symbol:
                continue

            if symbol in today_buy_symbols:
                continue

            avg_price = pos.get('avg_price', 0)
            current_price = pos.get('current_price', 0)
            volume = pos.get('volume', 0)

            if avg_price <= 0 or current_price <= 0 or volume <= 0:
                continue

            float_pnl_pct = (current_price - avg_price) / avg_price * 100

            # P0-2: 每次轮询主动更新 HWM
            self._ensure_hwm(symbol, current_price)

            stop_reason = self._evaluate_stop_rules(
                symbol, float_pnl_pct, current_price, avg_price, market_pct
            )

            if stop_reason:
                self._execute_stop(symbol, current_price, volume, stop_reason, float_pnl_pct)

    def _evaluate_stop_rules(
        self, symbol: str, float_pnl_pct: float, current_price: float,
        avg_price: float, market_pct: float
    ) -> Optional[str]:
        """依次评估止损规则，按优先级返回第一个触发的规则。"""
        
        # ── 规则 0a: 破底止损（锚点动态上移） ──
        break_low_reason = self._check_break_low_stop(symbol, current_price)
        if break_low_reason:
            return break_low_reason

        # ── 规则 0b: 成本止损（仅处理未盈利/降级场景，大盈转亏交给规则2） ──
        cost_stop_reason = self._check_cost_stop(symbol, float_pnl_pct, current_price, avg_price)
        if cost_stop_reason:
            return cost_stop_reason

        # ── 规则 1: 板块背离止损（差值法） ──
        sector_reason = self._check_sector_divergence(symbol, float_pnl_pct)
        if sector_reason:
            return sector_reason

        # ── 规则 2: 铁律二移动止盈（含HWM增强） ──
        iron_rule2_reason = self._check_iron_rule2(symbol, float_pnl_pct, current_price, avg_price)
        if iron_rule2_reason:
            return iron_rule2_reason

        # ── 规则 3: 大盘相对表现止损 ──
        dynamic_reason = self._check_dynamic_stop(float_pnl_pct, market_pct)
        if dynamic_reason:
            return dynamic_reason

        return None

    # ── 规则 0a: 破底止损（锚点动态上移） ──

    def _check_break_low_stop(self, symbol: str, current_price: float) -> Optional[str]:
        """
        P1-1: 破底止损锚点动态上移。
        止损价 = max(90天阶段最低 × 0.97, 入场后最高收盘价 × 0.90)
        """
        if current_price <= 0:
            return None
        try:
            from app.api.indicator import _normalize_to_ts_code, _fetch_kline_high_low
            ts_code = _normalize_to_ts_code(symbol)
            _high, stage_low, _close = _fetch_kline_high_low(ts_code)
            if stage_low <= 0:
                return None

            # 基础锚点：阶段底部 3% 容错
            base_stop = stage_low * 0.97

            # 动态锚点：入场后 HWM × 0.90（趋势上移保护）
            hwm_stop = 0.0
            try:
                hwm_data = self._ensure_hwm(symbol, current_price)
                hwm = hwm_data.get('high_price', 0)
                if hwm > stage_low:
                    hwm_stop = hwm * 0.90
            except Exception:
                pass

            stop_price = max(base_stop, hwm_stop)

            if current_price < stop_price:
                return (
                    f'破底止损：当前价 {current_price:.2f} '
                    f'跌破动态止损线 {stop_price:.2f}'
                    f'（阶段底 {stage_low:.2f}×0.97={base_stop:.2f}'
                    f'{f" / HWM {hwm:.2f}×0.90={hwm_stop:.2f}" if hwm_stop > 0 else ""}）'
                )
        except Exception as e:
            logger.debug(f"[StopLoss] 破底检查跳过 {symbol}: {e}")
        return None

    # ── 规则 0b: 成本止损（仅处理未盈利场景） ──

    def _check_cost_stop(
        self, symbol: str, float_pnl_pct: float, current_price: float, avg_price: float
    ) -> Optional[str]:
        """
        P0-3: 智能成本止损，仅处理「从未盈利」和「小盈转亏」。
        「大盈转亏」交给规则 2 的 HWM 增强逻辑处理，消除重叠。
        """
        if avg_price <= 0:
            return None

        hwm = None
        hwm_days = None
        try:
            chain = self.strategy_chain
            if chain:
                hwm_data = chain.get_high_water_mark(symbol)
                if hwm_data:
                    hwm = hwm_data.get('high_price', 0)
                    hwm_days = hwm_data.get('days_since_high', 0)
        except Exception:
            pass

        max_profit_pct = (
            round((hwm - avg_price) / avg_price * 100, 2)
            if hwm and hwm > avg_price else 0
        )

        # 曾大盈(≥5%) → 不在此处理，交给规则2
        if max_profit_pct >= 5:
            return None

        # 曾小盈(3-5%) → -3% 止损
        if max_profit_pct >= 3 and float_pnl_pct <= -3.0:
            return (
                f'成本止损-小盈转亏：曾浮盈 +{max_profit_pct:.1f}% → '
                f'现亏损 {float_pnl_pct:.2f}% 触及 -3% 止损线（成本 {avg_price:.2f}）'
            )

        # 从未盈利 → -4% 快速止损
        if hwm is not None and float_pnl_pct <= -4.0:
            days_tag = f' 持仓约{hwm_days}天' if hwm_days else ''
            return (
                f'成本止损-未盈利-4%：从未盈利{days_tag}，'
                f'当前亏损 {float_pnl_pct:.2f}% 触及止损线（成本 {avg_price:.2f}）'
            )

        # 无 HWM 数据 → -6% 保守底线
        if hwm is None and float_pnl_pct <= -6.0:
            return (
                f'成本止损-6%（降级）：当前价 {current_price:.2f}'
                f' 亏损 {float_pnl_pct:.2f}% 触及底线（成本 {avg_price:.2f}）'
            )

        return None

    # ── 规则 1: 板块背离止损（P0-1: 差值法） ──

    def _check_sector_divergence(self, symbol: str, float_pnl_pct: float) -> Optional[str]:
        """
        P0-1: 板块背离止损——差值法替代 3x 乘法。
        触发条件：个股日收益 - 板块日收益 < -3pp（个股跑输板块 3 个百分点以上）
        """
        if float_pnl_pct >= 0:
            return None

        try:
            chain = self.strategy_chain
            if chain is None:
                return None

            latest_scan = chain.get_latest_scan()
            if not latest_scan:
                return None

            sector_data = latest_scan.get('sector_allocation', {})
            if not sector_data:
                return None

            for sector_name, sector_info in sector_data.items():
                stocks = sector_info.get('stocks', []) if isinstance(sector_info, dict) else []
                if symbol in stocks or any(s.get('symbol') == symbol for s in stocks if isinstance(s, dict)):
                    sector_pct = sector_info.get('pct_change', 0) if isinstance(sector_info, dict) else 0
                    # 差值法：个股 - 板块 < -3pp
                    divergence = float_pnl_pct - sector_pct
                    if divergence < -3.0:
                        return (
                            f'板块背离止损：板块{sector_name}({sector_pct:+.2f}%)，'
                            f'个股{float_pnl_pct:+.2f}%，跑输 {abs(divergence):.1f}pp'
                        )
        except Exception:
            pass

        return None

    # ── 规则 2: 铁律二移动止盈（P0-3: HWM增强 + P1回吐比例收紧） ──

    def _check_iron_rule2(
        self, symbol: str, float_pnl_pct: float, current_price: float, avg_price: float
    ) -> Optional[str]:
        """
        铁律二：盈利单不能变亏损。
        P0-3: HWM 增强——检测曾大盈后回撤。
        P1: 回吐比例收紧（专家组建议）。
        """
        # ── HWM 增强：曾大盈(≥5%) 转亏损 → 保本离场 ──
        try:
            hwm_data = self._ensure_hwm(symbol, current_price)
            hwm = hwm_data.get('high_price', 0)
            if hwm > avg_price:
                max_profit_pct = round((hwm - avg_price) / avg_price * 100, 2)
                if max_profit_pct >= 5 and float_pnl_pct <= -1.0:
                    return (
                        f'铁律二-HWM保本：曾浮盈 +{max_profit_pct:.1f}%'
                        f'（最高 {hwm:.2f}）→ 现亏损 {float_pnl_pct:.2f}% → 保本离场'
                    )
        except Exception:
            pass

        # ── 标准移动止盈（回吐比例收紧） ──
        # P1: ≥8%→保护线+6%(回吐25%) / ≥5%→+3.5%(回吐30%) / ≥3%→+1% / ≥1%→保本
        if float_pnl_pct >= 8.0:
            if float_pnl_pct < 6.0:
                return f'铁律二触发：浮盈从≥8%回落至{float_pnl_pct:+.2f}%，跌破+6%保护线'
        elif float_pnl_pct >= 5.0:
            if float_pnl_pct < 3.5:
                return f'铁律二触发：浮盈从≥5%回落至{float_pnl_pct:+.2f}%，跌破+3.5%保护线'
        elif float_pnl_pct >= 3.0:
            if float_pnl_pct < 1.0:
                return f'铁律二触发：浮盈从≥3%回落至{float_pnl_pct:+.2f}%，跌破+1%保护线'
        elif float_pnl_pct >= 1.0:
            if float_pnl_pct < 0.0:
                return f'铁律二触发：浮盈{float_pnl_pct:+.2f}%转为亏损，保本止损'
        else:
            if float_pnl_pct <= -2.0:
                return f'铁律二+基础止损：浮亏{float_pnl_pct:.2f}%触及-2%止损线'

        return None

    # ── 规则 3: 大盘相对表现止损（P1-2） ──

    def _check_dynamic_stop(self, float_pnl_pct: float, market_pct: float) -> Optional[str]:
        """
        P1-2: 个股 vs 大盘相对表现。
        大盘跌 >2% 且个股跑输大盘 >3pp → 强审止损。
        否则使用标准阈值。
        """
        # 强审：大盘跌 >2% 且个股跌幅显著大于大盘
        if market_pct <= -2.0 and (float_pnl_pct - market_pct) < -3.0:
            return (
                f'动态止损-相对弱势：大盘{market_pct:+.2f}%，'
                f'个股{float_pnl_pct:+.2f}%，跑输 {abs(float_pnl_pct - market_pct):.1f}pp'
            )

        # 标准阈值
        if market_pct <= -2.0:
            threshold = -1.5
            label = '大盘跌>2%，止损-1.5%'
        elif -1.0 <= market_pct <= 1.0:
            threshold = -2.0
            label = '大盘震荡，止损-2%'
        elif 1.0 < market_pct <= 2.0:
            threshold = -3.0
            label = '大盘小涨，止损放宽至-3%'
        else:
            threshold = -4.0
            label = '大盘大涨，止损放宽至-4%'

        if float_pnl_pct <= threshold:
            return f'动态止损触发：{label}，当前浮亏{float_pnl_pct:.2f}%'

        return None

    # ── 大盘行情获取 ──

    def _get_market_change_pct(self) -> float:
        try:
            import urllib.request, ssl
            ctx = ssl.create_default_context()
            url = 'http://localhost:8000/api/v1/market/indices'
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                indices = data.get('indices', [])
                for idx in indices:
                    name = idx.get('name', '')
                    if '上证' in name or 'shanghai' in name.lower():
                        return float(idx.get('change_pct', 0))
        except Exception as e:
            logger.debug(f"[StopLoss] 获取大盘指数失败: {e}")
        return 0.0

    # ── 止损执行 ──

    def _execute_stop(
        self, symbol: str, price: float, volume: int, reason: str, float_pnl_pct: float
    ) -> None:
        # 早盘冷静期：09:30-09:45 不执行卖出（09:35窗口统计胜率 0%）
        if self._is_morning_volatility():
            logger.info(
                f"[StopLoss] ⏸️ 早盘冷静期，延迟卖出: {symbol} @ {price} | {reason}"
            )
            return

        trigger_key = f"{symbol}_{price:.2f}"
        with self.lock:
            if trigger_key in self._triggered:
                return
            self._triggered[trigger_key] = price

        daily_count = self.today_stops.get(symbol, 0)
        if daily_count >= 3:
            logger.warning(f"[StopLoss] {symbol} 今日已止损 {daily_count} 次，跳过")
            return

        logger.info(f"[StopLoss] 🔴 触发止损: {symbol} @ {price} | {reason}")
        print(f"[StopLoss] 🔴 {symbol} 止损 @ {price} | 浮盈{float_pnl_pct:+.2f}% | {reason}", file=sys.stderr)

        if self.executor is None:
            logger.error(f"[StopLoss] executor 未注入，无法执行止损: {symbol}")
            return

        try:
            result = self.executor.sell(
                symbol=symbol,
                price=price,
                volume=volume,
                reason=f'[StopLoss自动] {reason}'
            )
            if result.get('status') == 'executed':
                self.today_stops[symbol] = daily_count + 1
                logger.info(f"[StopLoss] ✅ 止损已执行: {symbol} @ {price} x{volume}")
                self._log_stop(symbol, price, volume, reason, float_pnl_pct, result.get('profit', 0))
            else:
                logger.warning(f"[StopLoss] ⚠️ 止损执行失败: {symbol} - {result.get('reason', '未知')}")
        except Exception as e:
            logger.error(f"[StopLoss] ❌ 止损异常: {symbol} - {e}", exc_info=True)

    def check_time_falsification(self, symbols: list = None) -> list:
        triggered = []
        try:
            from core.utils.strategy_chain import StrategyChain
            chain = StrategyChain()

            if symbols is None:
                if self.executor:
                    positions = self.executor.get_positions()
                    symbols = [p.get('symbol', '') for p in positions if p.get('symbol')]
                else:
                    return triggered

            for symbol in symbols:
                if not symbol:
                    continue
                result = chain.check_time_falsification(symbol)
                if result:
                    triggered.append(result)
                    logger.warning(
                        f"[StopLoss] ⏰ 时间证伪: {symbol} "
                        f"已 {result['days_since_high']} 个交易日未创新高"
                    )
        except Exception as e:
            logger.error(f"[StopLoss] 时间证伪检查异常: {e}")
        return triggered

    def _log_stop(
        self, symbol: str, price: float, volume: int, reason: str, float_pnl: float, profit: float
    ) -> None:
        """写入止损日志到 PostgreSQL"""
        rule = 'unknown'
        if '破底' in reason:
            rule = 'break_low'
        elif '成本止损' in reason:
            rule = 'cost_stop'
        elif '板块背离' in reason:
            rule = 'sector'
        elif '铁律二' in reason:
            rule = 'iron_rule2'
        elif '动态止损' in reason:
            rule = 'dynamic'
        elif '时间证伪' in reason:
            rule = 'time_falsification'

        try:
            from app.database import SessionLocal
            from app.models.stop_loss_log import StopLossLog
            db = SessionLocal()
            try:
                db.add(StopLossLog(
                    timestamp=datetime.now(),
                    symbol=symbol,
                    rule=rule,
                    price=price,
                    volume=volume,
                    float_pnl_pct=round(float_pnl, 2),
                    realized_profit=round(profit, 2),
                    executed='yes',
                    reason=reason,
                ))
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[StopLoss] 日志写入失败: {e}")


# ── 全局单例 ──
_monitor_instance: Optional[StopLossMonitor] = None
_monitor_lock = threading.Lock()


def get_stop_loss_monitor(executor=None, interval_seconds: int = 30) -> StopLossMonitor:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = StopLossMonitor(executor=executor, interval_seconds=interval_seconds)
        elif executor is not None and _monitor_instance.executor is None:
            _monitor_instance.executor = executor
        return _monitor_instance


def start_monitor(executor=None) -> bool:
    monitor = get_stop_loss_monitor(executor=executor)
    return monitor.start()


def stop_monitor() -> None:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is not None:
            _monitor_instance.stop()
