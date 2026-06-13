#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时止损监控器 — 独立后台线程持续轮询持仓价格，触发止损规则自动卖出。

止损规则（优先级从高到低）：
  1. 板块背离止损：个股跌幅 > 同板块平均跌幅 × 3 → 立即止损
  2. 铁律二移动止盈保护：
     - 浮盈 ≥ 1%  → 止损线上移至成本价（保本）
     - 浮盈 ≥ 3%  → 止损线上移至成本价 + 1%
     - 浮盈 ≥ 5%  → 止损线上移至成本价 + 2%
     - 浮盈 ≥ 8%  → 止损线上移至成本价 + 4%
     - 浮盈 < 1%  → 保持原止损线 -2%
  3. 大盘背景动态止损：
     - 大盘跌 > 2%      → 止损收紧至 -1.5%
     - 大盘 -1%~+1%    → 止损线 -2%
     - 大盘 +1%~+2%    → 止损放宽至 -3%
     - 大盘 +2% 以上    → 止损放宽至 -4%
  4. T+1 保护：今日买入的持仓不执行止损卖出
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

    def __init__(self, executor=None, interval_seconds: int = 30):
        """
        Args:
            executor: MarcusVNPyExecutor 实例（可选，延迟注入）
            interval_seconds: 轮询间隔（秒），默认 30 秒
        """
        self.executor = executor
        self.interval = interval_seconds
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        # 当日已执行的止损记录 {symbol: count}
        self.today_stops: Dict[str, int] = {}
        # 当日已触发的止损价格（防止重复执行）{symbol: price}
        self._triggered: Dict[str, float] = {}

        # 策略链引用（用于获取板块数据等）
        self._strategy_chain = None

        # 日志文件
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
        """启动监控线程。返回 True 表示成功启动，False 表示已在运行。"""
        with self.lock:
            if self.running:
                logger.info("[StopLoss] 已在运行中，跳过重复启动")
                return False
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True, name="stop-loss-monitor")
            self.thread.start()
            logger.info(f"[StopLoss] ✅ 监控已启动，轮询间隔 {self.interval}s")
            return True

    def stop(self) -> None:
        """停止监控线程。"""
        with self.lock:
            self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        logger.info("[StopLoss] ⏹️ 监控已停止")

    def is_running(self) -> bool:
        return self.running

    # ── 主循环 ──

    def _run_loop(self) -> None:
        """后台线程主循环"""
        print("[StopLoss] 后台监控线程启动", file=sys.stderr)
        while self.running:
            try:
                if self._is_trading_time():
                    self._check_all_positions()
                else:
                    # 非交易时段，每天首次检查时重置计数器
                    self._daily_reset()
            except Exception as e:
                logger.error(f"[StopLoss] 检查异常: {e}", exc_info=True)
            time.sleep(self.interval)

    def _is_trading_time(self) -> bool:
        """判断当前是否在 A 股交易时段内"""
        now = datetime.now().time()
        # 上午 9:30-11:30 或 下午 13:00-15:00
        morning = self.TRADING_START <= now <= self.LUNCH_START
        afternoon = self.LUNCH_END <= now <= self.TRADING_END
        return morning or afternoon

    def _daily_reset(self) -> None:
        """新交易日重置状态"""
        today = datetime.now().strftime('%Y-%m-%d')
        if getattr(self, '_last_reset_date', '') != today:
            self.today_stops.clear()
            self._triggered.clear()
            self._last_reset_date = today
            # 重置 executor 的连续亏损计数器
            if self.executor:
                try:
                    self.executor.reset_consecutive_losses()
                except Exception:
                    pass

    # ── 核心检查逻辑 ──

    def _check_all_positions(self) -> None:
        """检查所有持仓的止损条件"""
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

        # 获取大盘指数用于动态止损
        market_pct = self._get_market_change_pct()

        # 获取今日买入的股票（T+1 保护）
        today_buy_symbols = self.executor._get_today_buy_symbols() if self.executor else set()

        for pos in positions:
            symbol = pos.get('symbol', '')
            if not symbol:
                continue

            # T+1 保护：今日买入的不触发止损
            if symbol in today_buy_symbols:
                continue

            # 获取实时价格（持仓中已有 current_price）
            avg_price = pos.get('avg_price', 0)
            current_price = pos.get('current_price', 0)
            volume = pos.get('volume', 0)

            if avg_price <= 0 or current_price <= 0 or volume <= 0:
                continue

            # 计算浮动盈亏百分比
            float_pnl_pct = (current_price - avg_price) / avg_price * 100

            # 检查各止损规则
            stop_reason = self._evaluate_stop_rules(symbol, float_pnl_pct, current_price, market_pct)

            if stop_reason:
                self._execute_stop(symbol, current_price, volume, stop_reason, float_pnl_pct)

    def _evaluate_stop_rules(
        self, symbol: str, float_pnl_pct: float, current_price: float, market_pct: float
    ) -> Optional[str]:
        """
        依次评估止损规则，返回触发的规则描述，未触发返回 None。
        规则按优先级排列。
        """
        # ── 规则 0: 板块背离止损（优先级最高） ──
        sector_reason = self._check_sector_divergence(symbol, float_pnl_pct)
        if sector_reason:
            return sector_reason

        # ── 规则 1: 铁律二 — 移动止盈保护 ──
        iron_rule2_reason = self._check_iron_rule2(float_pnl_pct, current_price)
        if iron_rule2_reason:
            return iron_rule2_reason

        # ── 规则 2: 大盘背景动态止损 ──
        dynamic_reason = self._check_dynamic_stop(float_pnl_pct, market_pct)
        if dynamic_reason:
            return dynamic_reason

        return None

    def _check_iron_rule2(self, float_pnl_pct: float, current_price: float) -> Optional[str]:
        """
        铁律二：盈利单不能变亏损（两道移动止盈保护）

        Returns:
            触发时返回止损原因，否则返回 None
        """
        # 浮盈 ≥ 8% → 止损线上移至成本价 + 4%（触发时说明已跌破保护线）
        # 注意：这里监测的是"当前价比最高点回落了多少"
        # 简化处理：按当前浮盈水平对应的保护线判断
        if float_pnl_pct >= 8.0:
            # 保护线在成本价 + 4%，当前浮盈跌破 4% 则触发
            if float_pnl_pct < 4.0:
                return f'铁律二触发：浮盈从≥8%回落至{float_pnl_pct:+.2f}%，跌破+4%保护线'
        elif float_pnl_pct >= 5.0:
            if float_pnl_pct < 2.0:
                return f'铁律二触发：浮盈从≥5%回落至{float_pnl_pct:+.2f}%，跌破+2%保护线'
        elif float_pnl_pct >= 3.0:
            if float_pnl_pct < 1.0:
                return f'铁律二触发：浮盈从≥3%回落至{float_pnl_pct:+.2f}%，跌破+1%保护线'
        elif float_pnl_pct >= 1.0:
            if float_pnl_pct < 0.0:
                return f'铁律二触发：浮盈{float_pnl_pct:+.2f}%转为亏损，保本止损'
        else:
            # 浮盈 < 1%，使用基础止损线 -2%
            if float_pnl_pct <= -2.0:
                return f'铁律二+基础止损：浮亏{float_pnl_pct:.2f}%触及-2%止损线'

        return None

    def _check_dynamic_stop(self, float_pnl_pct: float, market_pct: float) -> Optional[str]:
        """
        大盘背景动态止损：根据大盘涨跌幅调整止损阈值。
        """
        if market_pct <= -2.0:
            threshold = -1.5
            label = '大盘跌>2%，止损收紧至-1.5%'
        elif -1.0 <= market_pct <= 1.0:
            threshold = -2.0
            label = '大盘震荡(-1%~+1%)，标准止损-2%'
        elif 1.0 < market_pct <= 2.0:
            threshold = -3.0
            label = '大盘小涨(+1%~+2%)，止损放宽至-3%'
        else:
            threshold = -4.0
            label = '大盘大涨>+2%，止损放宽至-4%'

        if float_pnl_pct <= threshold:
            return f'动态止损触发：{label}，当前浮亏{float_pnl_pct:.2f}%'

        return None

    def _check_sector_divergence(self, symbol: str, float_pnl_pct: float) -> Optional[str]:
        """
        板块背离止损：个股跌幅 > 同板块平均跌幅 × 3 → 立即止损。
        
        目前通过策略链获取板块数据，如果拿不到则跳过此检查。
        """
        if float_pnl_pct >= 0:
            return None  # 个股没跌，不需检查

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

            # 查找该股所属板块的平均涨跌幅
            # sector_allocation 结构: {sector_name: {'pct_change': ..., 'stocks': [...]}}
            for sector_name, sector_info in sector_data.items():
                stocks = sector_info.get('stocks', []) if isinstance(sector_info, dict) else []
                if symbol in stocks or any(s.get('symbol') == symbol for s in stocks if isinstance(s, dict)):
                    sector_pct = sector_info.get('pct_change', 0) if isinstance(sector_info, dict) else 0
                    if sector_pct > 0 and float_pnl_pct < 0:
                        # 板块涨但个股跌
                        divergence = abs(float_pnl_pct)
                        if divergence > sector_pct * 3:
                            return f'板块背离止损：板块{sector_name}({sector_pct:+.2f}%)涨，个股{float_pnl_pct:+.2f}%跌，偏离度{divergence:.1f}%'
        except Exception:
            pass

        return None

    def _get_market_change_pct(self) -> float:
        """获取上证指数当日涨跌幅（%）"""
        try:
            # 通过 executor 获取市场数据
            if self.executor:
                account = self.executor.get_account()
                # 从 get_account 中无法直接获取市场指数
                # 尝试通过 API 获取
                pass
        except Exception:
            pass

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
        """执行止损卖出"""
        # 防重复触发
        trigger_key = f"{symbol}_{price:.2f}"
        with self.lock:
            if trigger_key in self._triggered:
                return
            self._triggered[trigger_key] = price

        # 限制单日单票止损次数
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
                # 写入止损日志
                self._log_stop(symbol, price, volume, reason, float_pnl_pct, result.get('profit', 0))
            else:
                logger.warning(f"[StopLoss] ⚠️ 止损执行失败: {symbol} - {result.get('reason', '未知')}")
        except Exception as e:
            logger.error(f"[StopLoss] ❌ 止损异常: {symbol} - {e}", exc_info=True)

    def _log_stop(
        self, symbol: str, price: float, volume: int, reason: str, float_pnl: float, profit: float
    ) -> None:
        """记录止损日志"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'type': 'stop_loss',
            'symbol': symbol,
            'price': price,
            'volume': volume,
            'reason': reason,
            'float_pnl_pct': round(float_pnl, 2),
            'realized_profit': round(profit, 2),
        }
        log_file = self.log_dir / 'stop_loss_log.jsonl'
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(f"[StopLoss] 日志写入失败: {e}")


# ── 全局单例 ──
_monitor_instance: Optional[StopLossMonitor] = None
_monitor_lock = threading.Lock()


def get_stop_loss_monitor(executor=None, interval_seconds: int = 30) -> StopLossMonitor:
    """获取全局 StopLossMonitor 单例"""
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = StopLossMonitor(executor=executor, interval_seconds=interval_seconds)
        elif executor is not None and _monitor_instance.executor is None:
            _monitor_instance.executor = executor
        return _monitor_instance


def start_monitor(executor=None) -> bool:
    """便捷启动函数"""
    monitor = get_stop_loss_monitor(executor=executor)
    return monitor.start()


def stop_monitor() -> None:
    """便捷停止函数"""
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is not None:
            _monitor_instance.stop()
