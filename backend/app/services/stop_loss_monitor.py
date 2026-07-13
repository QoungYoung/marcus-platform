#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时止损监控器 — 独立后台线程持续轮询持仓价格，触发止损规则自动卖出。

止损规则（优先级从高到低）：
  0a. 破底止损（锚点动态上移）：跌破 max(阶段底×0.97, 入场后最高收盘×0.90)
  0b. 成本止损：从未盈利→-4% / 曾小盈转亏→-3% / 无HWM→-6%
  1.  板块背离止损：个股日收益 - 板块日收益 < -3pp（差值法）
  2.  铁律二移动止盈（v2.0 振幅分档统一版）：
      - HWM曾大盈≥5% → 保本离场(-1%)
      - 低波<3%: T1≥1%→保本 | T2≥3%→+1% | T3≥5%→+2%
      - 中波3-6%: T1≥2%→保本 | T2≥5%→+2% | T3≥8%→+4%
      - 高波>6%: T1≥3%→保本 | T2≥7%→+3% | T3≥10%→+5%
  2.5 技术指标背离止损（五大信号综合判定）：
      - ≥4 个信号触发 → 清仓 (100%)
      - ≥3 个信号触发 → 减仓 (50%)
      信号: ①MACD红柱缩量 ②RSI顶背离 ③量价背离 ④KDJ J>100 ⑤布林上轨外
  3.  大盘相对表现止损：大盘跌>2%且个股跌幅-大盘跌幅<-3pp→强审
  4.  T+1 保护：今日买入的持仓不执行止损卖出
  5.  早盘冷静期：09:30-09:45 不执行卖出（该窗口统计胜率 0%）

规则冲突 SOP：
  - 规则按优先级 0a→0b→1→2→2.5→3 依次评估，首个命中即执行，后续规则不再检查
  - 规则 4（T+1）和规则 5（冷静期）为前置拦截，不参与优先级竞争
  - 单日单票最多执行 3 次止损，同一价位不可重复触发
"""

import sys
import os
import json
import time
import threading
import logging
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ── K线数据缓存（避免频繁 Tushare API 调用导致超时）──
# 缓存 key: ts_code → (timestamp, high, low, close)
# TTL: 120 秒（足够覆盖一次扫描周期，第二次请求命中缓存）
_kline_cache: Dict[str, tuple] = {}
_kline_cache_ttl: float = 3600.0  # 90天K线高低点一天内不变，缓存1小时
_kline_cache_lock = threading.Lock()


def _cached_fetch_kline(ts_code: str) -> Optional[tuple]:
    """带缓存的 K 线获取，避免重复 Tushare HTTP 调用"""
    with _kline_cache_lock:
        if ts_code in _kline_cache:
            ts, high, low, close_val = _kline_cache[ts_code]
            if time.time() - ts < _kline_cache_ttl:
                return (high, low, close_val)

    try:
        from app.api.indicator import _fetch_kline_high_low
        high, low, close_val = _fetch_kline_high_low(ts_code, days=90)
        with _kline_cache_lock:
            _kline_cache[ts_code] = (time.time(), high, low, close_val)
        return (high, low, close_val)
    except Exception as e:
        logger.warning(f"[StopLoss] K线缓存获取失败 {ts_code}: {e}")
        return None


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

    def __init__(self, executor=None, interval_seconds: int = 31):
        self.executor = executor
        self.interval = interval_seconds
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        self.today_stops: Dict[str, int] = {}
        self._triggered: Dict[str, float] = {}
        self._strategy_chain = None
        self._tech_divergence_cache: Dict[str, tuple] = {}  # (symbol, date_str) -> (signals_tuple, timestamp)

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

    def is_running(self, check_thread: bool = True) -> bool:
        """检查监控是否在运行。

        Args:
            check_thread: 是否同时校验后台线程存活状态（更可靠）
        Returns:
            True 表示监控正在运行
        """
        if not self.running:
            return False
        if check_thread:
            return self.thread is not None and self.thread.is_alive()
        return True

    def status(self) -> Dict[str, Any]:
        """获取监控器运行状态详情（含持仓止损距离）。"""
        positions = []
        try:
            positions = self.get_position_stop_distances()
        except Exception:
            pass

        # 如果止损距离计算返回空（executor data_dir 不对等），
        # 回退到直接从 trades.db 读基础持仓（与 /api/v1/portfolio 同源）
        if not positions:
            try:
                from app.api.portfolio import calculate_positions_from_db
                basic_positions, _ = calculate_positions_from_db()
                # 转换为前端兼容的格式（虽然缺少止损字段）
                for p in basic_positions:
                    positions.append({
                        "symbol": p["symbol"],
                        "name": p.get("name", ""),
                        "avg_price": round(p.get("avg_price", 0), 2),
                        "current_price": 0,
                        "volume": p.get("volume", 0),
                        "float_pnl_pct": 0,
                        "t1_locked": False,
                        "daily_stops_used": 0,
                        "nearest_trigger": {"rule": None, "distance_pct": None, "danger_level": "no_data"},
                        "rule_distances": {},
                    })
            except Exception:
                pass

        return {
            "running": self.running,
            "thread_alive": self.thread.is_alive() if self.thread else False,
            "thread_name": self.thread.name if self.thread else None,
            "interval_seconds": self.interval,
            "today_stops_count": len(self.today_stops),
            "today_stops": dict(self.today_stops),
            "has_executor": self.executor is not None,
            "is_trading_time": self._is_trading_time(),
            "is_morning_volatility": self._is_morning_volatility(),
            "position_count": len(positions),
            "triggered_count": sum(1 for p in positions if p.get("nearest_trigger", {}).get("danger_level") == "triggered"),
            "positions": positions,
        }

    # ── 主循环 ──

    def _run_loop(self) -> None:
        print("[StopLoss] 后台监控线程启动 (间隔=31s, 偏移=0s)", file=sys.stderr)
        cycle = 0
        while self.running:
            cycle += 1
            try:
                if self._is_trading_time():
                    print(f"[止损] 🔄 第 {cycle} 轮检查 | {datetime.now().strftime('%H:%M:%S')}", file=sys.stderr)
                    self._check_all_positions()
                else:
                    if cycle % 20 == 1:  # 非交易时段每 10 分钟才打印一次
                        print(f"[StopLoss] ⏸️ 非交易时段，跳过检查 (cycle={cycle})", file=sys.stderr)
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

    # ── 持仓天数 ──

    def _get_holding_days(self, symbol: str) -> Optional[int]:
        """获取某只股票的首笔买入距今自然天数，用于 T1 保本门槛判断。"""
        try:
            if self.executor is None:
                return None
            import sqlite3
            from pathlib import Path
            data_dir = Path(self.executor.data_dir)
            db_path = data_dir / "trades.db"
            if not db_path.exists():
                return None
            conn = sqlite3.connect(str(db_path), timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")
            cursor = conn.cursor()
            cursor.execute(
                "SELECT MIN(created_at) FROM trades WHERE symbol = ? AND direction = '买入'",
                (symbol,)
            )
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                first_date = row[0][:10]  # "2026-07-03" from "2026-07-03T09:35:00"
                from datetime import date as dt_date
                first_dt = dt_date.fromisoformat(first_date)
                return (dt_date.today() - first_dt).days
        except Exception:
            pass
        return None

    # ── HWM 辅助（P0-2: 监控器内部直接更新） ──

    def _update_highest_price(self, symbol: str, current_price: float) -> None:
        """将当前行情价格更新到 positions 表的 highest_price 字段"""
        try:
            if self.executor and hasattr(self.executor, 'engine'):
                self.executor.engine.update_position_meta(symbol, highest_price=current_price)
        except Exception:
            pass

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
            print(f"[StopLoss] 持仓为空，跳过检查", file=sys.stderr)
            return

        print(f"[止损] 持仓 {len(positions)} 只，开始止损评估...", file=sys.stderr)

        market_pct = self._get_market_change_pct()
        today_volumes = self.executor._get_today_buy_volumes() if self.executor else {}
        t1_skipped = []

        for pos in positions:
            symbol = pos.get('symbol', '')
            if not symbol:
                continue

            total_vol = pos.get('volume', 0)
            today_buy_vol = today_volumes.get(symbol, 0)
            available = total_vol - today_buy_vol
            if available <= 0:
                t1_skipped.append(symbol)
                continue

            avg_price = pos.get('avg_price', 0)
            current_price = pos.get('current_price', 0)
            volume = available  # 只卖非锁定的股数

            if avg_price <= 0 or current_price <= 0 or volume <= 0:
                continue

            # 每次轮询更新持仓最高价到 positions 表
            self._update_highest_price(symbol, current_price)

            float_pnl_pct = (current_price - avg_price) / avg_price * 100

            # P0-2: 每次轮询主动更新 HWM
            self._ensure_hwm(symbol, current_price)

            stop_reason, sell_ratio = self._evaluate_stop_rules(
                symbol, float_pnl_pct, current_price, avg_price, market_pct
            )

            if stop_reason:
                self._execute_stop(symbol, current_price, volume, stop_reason,
                                   float_pnl_pct, sell_ratio=sell_ratio)

        if t1_skipped:
            print(f"[止损] ⏭️ T+1 锁定跳过: {', '.join(t1_skipped)}", file=sys.stderr)

    def _evaluate_stop_rules(
        self, symbol: str, float_pnl_pct: float, current_price: float,
        avg_price: float, market_pct: float
    ):
        """依次评估止损规则，按优先级返回第一个触发的规则。

        Returns: (Optional[str], float) = (reason, sell_ratio)
        """

        # ── 规则 0a: 破底止损（锚点动态上移） ──
        break_low_reason = self._check_break_low_stop(symbol, current_price)
        if break_low_reason:
            return break_low_reason, 1.0

        # ── 规则 0b: 成本止损（仅处理未盈利/降级场景，大盈转亏交给规则2） ──
        cost_stop_reason = self._check_cost_stop(symbol, float_pnl_pct, current_price, avg_price)
        if cost_stop_reason:
            return cost_stop_reason, 1.0

        # ── 规则 1: 板块背离止损（差值法） ──
        sector_reason = self._check_sector_divergence(symbol, float_pnl_pct)
        if sector_reason:
            return sector_reason, 1.0

        # ── 规则 2: 铁律二移动止盈（含HWM增强） ──
        iron_rule2_reason = self._check_iron_rule2(symbol, float_pnl_pct, current_price, avg_price)
        if iron_rule2_reason:
            return iron_rule2_reason, 1.0

        # ── 规则 2.5: 技术指标背离止损（五大信号综合判定） ──
        tech_reason, tech_ratio = self._check_technical_divergence(symbol, current_price, float_pnl_pct)
        if tech_reason:
            return tech_reason, tech_ratio

        # ── 规则 3: 大盘相对表现止损 ──
        dynamic_reason = self._check_dynamic_stop(float_pnl_pct, market_pct, symbol)
        if dynamic_reason:
            return dynamic_reason, 1.0

        return None, 1.0

    # ── 规则 0a: 破底止损（锚点动态上移） ──

    def _check_break_low_stop(self, symbol: str, current_price: float) -> Optional[str]:
        """
        P1-1: 破底止损锚点动态上移。
        止损价 = max(90天阶段最低 × 0.97, 入场后最高收盘价 × 0.90)
        """
        if current_price <= 0:
            return None
        try:
            from app.api.indicator import _normalize_to_ts_code
            ts_code = _normalize_to_ts_code(symbol)
            cached = _cached_fetch_kline(ts_code)
            if cached is None:
                return None
            _high, stage_low, _close = cached
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

    # ── 规则 2: 铁律二移动止盈（v2.1 振幅分档+渐进保护版） ──

    # 振幅缓存（按交易日缓存，日K线盘中不变）
    _amplitude_cache: Dict[str, tuple] = {}  # symbol -> (tier, amplitude_pct, date_str)

    @staticmethod
    def _get_iron_rule2_thresholds(amplitude_tier: str) -> dict:
        """返回振幅档位对应的铁律二保护线阈值（渐进版 v2.1）。

        T1.5 为 T1→T2 之间的渐进保护线，浮盈每增加约 1.5% 保护线上移 1%，
        避免浮盈 3-5% 区间回吐全部利润才在成本价离场。

        | 波动档 | T1→保本 | T1.5→保护 | T2→保护 | T3→保护 |
        |:------:|:------:|:--------:|:------:|:------:|
        | 低波<3% | ≥1%→0% | ≥2%→+0.5% | ≥3%→+1% | ≥5%→+2% |
        | 中波3-6%| ≥2%→0% | ≥3.5%→+1% | ≥5%→+2% | ≥8%→+4% |
        | 高波>6% | ≥3%→0% | ≥5%→+1.5% | ≥7%→+3% | ≥10%→+5% |
        """
        if amplitude_tier == "低波":
            return {"t1_pct": 1.0, "t1_5_pct": 2.0, "t1_5_plus_pct": 0.5,
                    "t2_pct": 3.0, "t2_plus_pct": 1.0, "t3_pct": 5.0, "t3_plus_pct": 2.0}
        elif amplitude_tier == "中波":
            return {"t1_pct": 2.0, "t1_5_pct": 3.5, "t1_5_plus_pct": 1.0,
                    "t2_pct": 5.0, "t2_plus_pct": 2.0, "t3_pct": 8.0, "t3_plus_pct": 4.0}
        else:  # 高波
            return {"t1_pct": 3.0, "t1_5_pct": 5.0, "t1_5_plus_pct": 1.5,
                    "t2_pct": 7.0, "t2_plus_pct": 3.0, "t3_pct": 10.0, "t3_plus_pct": 5.0}

    def _get_amplitude_tier(self, symbol: str) -> str:
        """获取个股近5日日均振幅档位。

        数据来源：Tushare 日K线（已收盘的完整日线，盘中不变化）。
        缓存策略：按交易日缓存，同一交易日不重复查询 Tushare。
        同时缓存原始振幅值供 _get_amplitude_pct 使用。
        """
        today = datetime.now().strftime("%Y%m%d")
        cached = self._amplitude_cache.get(symbol)
        if cached:
            tier, amp_val, cached_date = cached
            if cached_date == today:
                return tier

        tier = "中波"  # 默认
        avg_amp = 3.0   # 默认值
        try:
            from app.api.indicator import _normalize_to_ts_code
            from app.config import get_settings
            import tushare as ts
            settings = get_settings()
            token = settings.get_tushare_token()
            pro = ts.pro_api(token)
            ts_code = _normalize_to_ts_code(symbol)
            from datetime import datetime as dt, timedelta
            end_d = dt.now().strftime("%Y%m%d")
            start_d = (dt.now() - timedelta(days=30)).strftime("%Y%m%d")
            df = pro.daily(ts_code=ts_code, start_date=start_d, end_date=end_d, limit=5)
            if df is not None and not df.empty:
                df = df.sort_values("trade_date", ascending=False)
                amps = []
                for _, row in df.head(5).iterrows():
                    close = float(row["close"])
                    if close > 0:
                        amp = (float(row["high"]) - float(row["low"])) / close * 100
                        amps.append(amp)
                if amps:
                    avg_amp = sum(amps) / len(amps)
                    if avg_amp < 3.0:
                        tier = "低波"
                    elif avg_amp <= 6.0:
                        tier = "中波"
                    else:
                        tier = "高波"
        except Exception:
            pass

        self._amplitude_cache[symbol] = (tier, round(avg_amp, 2), today)
        return tier

    def _get_amplitude_pct(self, symbol: str) -> float:
        """获取个股近5日日均振幅原始百分比值。

        缓存复用 _get_amplitude_tier 的结果，同一交易日不重复查询。
        """
        # 先触发 _get_amplitude_tier 确保缓存是最新的
        self._get_amplitude_tier(symbol)
        cached = self._amplitude_cache.get(symbol)
        if cached:
            _, amp_val, _ = cached
            return amp_val
        return 3.0  # 默认值

    def _check_iron_rule2(
        self, symbol: str, float_pnl_pct: float, current_price: float, avg_price: float
    ) -> Optional[str]:
        """
        铁律二：盈利单不能变亏损（v2.1 振幅分档+渐进保护版）。

        根据近5日日均振幅确定档位，每条保护线为成本价上移幅度：
        | 波动档 | T1·保本 | T1.5·保护 | T2·保护线 | T3·保护线 |
        |:------:|:-----:|:--------:|:-------:|:-------:|
        | 低波<3% | ≥1%→0% | ≥2%→+0.5% | ≥3%→+1% | ≥5%→+2% |
        | 中波3-6%| ≥2%→0% | ≥3.5%→+1% | ≥5%→+2% | ≥8%→+4% |
        | 高波>6% | ≥3%→0% | ≥5%→+1.5% | ≥7%→+3% | ≥10%→+5% |
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

        # ── 振幅分档保护线 ──
        amplitude_tier = self._get_amplitude_tier(symbol)
        rules = self._get_iron_rule2_thresholds(amplitude_tier)

        t1 = rules["t1_pct"]
        t1_5, t1_5_protect = rules["t1_5_pct"], rules["t1_5_plus_pct"]
        t2, t2_protect = rules["t2_pct"], rules["t2_plus_pct"]
        t3, t3_protect = rules["t3_pct"], rules["t3_plus_pct"]

        # 确定当前保护线
        protect_pct = None
        protect_desc = None

        if float_pnl_pct >= t3:
            protect_pct = t3_protect
            protect_desc = f'T3·浮盈≥{t3}%→保护线+{t3_protect}%'
        elif float_pnl_pct >= t2:
            protect_pct = t2_protect
            protect_desc = f'T2·浮盈≥{t2}%→保护线+{t2_protect}%'
        elif float_pnl_pct >= t1_5:
            protect_pct = t1_5_protect
            protect_desc = f'T1.5·浮盈≥{t1_5}%→保护线+{t1_5_protect}%'
        elif float_pnl_pct >= t1:
            # T1 保本：持仓 ≤ 3 天不启用，避免刚买入被盘中波动震出去
            holding_days = self._get_holding_days(symbol)
            if holding_days is not None and holding_days <= 3:
                return None
            protect_pct = 0.0  # 保本线 = 成本价
            protect_desc = f'T1·浮盈≥{t1}%→保本线(成本价)'

        # 触发判断
        if protect_pct is not None and float_pnl_pct < protect_pct:
            return (
                f'铁律二触发({amplitude_tier}波): {protect_desc}，'
                f'当前浮盈{float_pnl_pct:+.2f}%跌破保护线'
            )

        return None

    # ── 规则 2.5: 技术指标背离止损（五大信号综合判定） ──

    def _check_technical_divergence(
        self, symbol: str, current_price: float, float_pnl_pct: float
    ):
        """
        五大技术信号综合判定：
        ① MACD红柱连续2日缩量（价格涨但柱子变短）
        ② RSI顶背离（价格创新高，RSI未创新高）
        ③ 量价背离（价格上涨但成交量较前一波明显萎缩）
        ④ KDJ(J值) > 100（极端超买）
        ⑤ 价格脱离布林上轨外运行

        Returns:
            (None, 1.0)          — 信号不足 3 个，不触发
            (reason_str, 0.5)    — ≥3 个信号，减仓一半
            (reason_str, 1.0)    — ≥4 个信号，清仓
        """
        if float_pnl_pct <= 0:
            # 只在盈利状态下检查技术背离，亏损交给其他止损规则
            return None, 1.0

        today_str = datetime.now().strftime('%Y%m%d')
        cache_key = f"{symbol}_{today_str}"
        now = time.time()

        # 检查缓存（同一天内不重复请求 Tushare）
        if cache_key in self._tech_divergence_cache:
            cached_result, cached_ts = self._tech_divergence_cache[cache_key]
            if now - cached_ts < 3600:  # 1小时缓存
                signals, signal_names = cached_result
                return self._eval_tech_signals(signals, signal_names, symbol)

        try:
            from app.api.indicator import _normalize_to_ts_code
            from app.config import get_settings
            import tushare as ts

            settings = get_settings()
            token = settings.get_tushare_token()
            if not token:
                return None, 1.0
            pro = ts.pro_api(token)
            ts_code = _normalize_to_ts_code(symbol)
            end_d = datetime.now().strftime("%Y%m%d")
            start_d = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")

            # ── 获取技术指标（stk_factor_pro）──
            df_tech = pro.stk_factor_pro(
                ts_code=ts_code, start_date=start_d, end_date=end_d,
                fields='trade_date,close,macd_dif_qfq,macd_dea_qfq,'
                       'rsi_qfq_6,kdj_k_qfq,kdj_d_qfq,boll_upper_qfq'
            )

            # ── 获取日K线（成交量）──
            df_daily = pro.daily(
                ts_code=ts_code, start_date=start_d, end_date=end_d,
                fields='trade_date,close,vol'
            )

            if df_tech is None or df_tech.empty or len(df_tech) < 20:
                return None, 1.0
            if df_daily is None or df_daily.empty or len(df_daily) < 10:
                return None, 1.0

            df_tech = df_tech.sort_values("trade_date", ascending=True)
            df_daily = df_daily.sort_values("trade_date", ascending=True)

            # ── 对齐两个数据源的日期 ──
            tech_dates = set(df_tech['trade_date'].values)
            df_daily = df_daily[df_daily['trade_date'].isin(tech_dates)]

            closes = [float(v) for v in df_tech['close'].values]
            macd_difs = [float(v) for v in df_tech['macd_dif_qfq'].values]
            macd_deas = [float(v) for v in df_tech['macd_dea_qfq'].values]
            rsi6s = [float(v) for v in df_tech['rsi_qfq_6'].values]
            kdj_ks = [float(v) for v in df_tech['kdj_k_qfq'].values]
            kdj_ds = [float(v) for v in df_tech['kdj_d_qfq'].values]
            boll_uppers = [float(v) for v in df_tech['boll_upper_qfq'].values]
            volumes = [float(v) for v in df_daily['vol'].values]

            if len(closes) < 20:
                return None, 1.0

            # ── 计算 MACD 柱（bar = 2 * (DIF - DEA)）──
            macd_bars = [2.0 * (dif - dea) for dif, dea in zip(macd_difs, macd_deas)]
            # 取最近 5 根
            recent_bars = macd_bars[-5:] if len(macd_bars) >= 5 else macd_bars
            recent_closes_for_macd = closes[-5:] if len(closes) >= 5 else closes

            signals = [False] * 5
            signal_details = []

            # ── 信号 1: MACD 红柱连续 2 日缩量 ──
            if len(recent_bars) >= 4:
                bar_last3 = recent_bars[-3:]
                close_last4 = recent_closes_for_macd[-4:]
                bar_positive = all(b > 0 for b in bar_last3)
                bar_shrinking = bar_last3[0] > bar_last3[1] > bar_last3[2]
                price_rising = close_last4[-1] > close_last4[0]
                if bar_positive and bar_shrinking and price_rising:
                    signals[0] = True
                    signal_details.append(
                        f"MACD红柱缩量(bar:{bar_last3[0]:.4f}>{bar_last3[1]:.4f}>{bar_last3[2]:.4f})"
                    )

            # ── 信号 2: RSI 顶背离 ──
            if len(closes) >= 20 and len(rsi6s) >= 20:
                closes_20 = closes[-20:]
                rsis_20 = rsi6s[-20:]
                peak_idx = closes_20.index(max(closes_20))
                peak_close = closes_20[peak_idx]
                peak_rsi = rsis_20[peak_idx]
                cur_close = closes_20[-1]
                cur_rsi = rsis_20[-1]
                # 当前价格接近前高（≥99%）且 RSI 明显低于前高对应 RSI
                if peak_idx < len(closes_20) - 1:  # 前高不是今天
                    if cur_close >= peak_close * 0.99 and cur_rsi < peak_rsi * 0.95:
                        signals[1] = True
                        signal_details.append(
                            f"RSI顶背离(价{cur_close:.2f}≈前高{peak_close:.2f}, RSI{cur_rsi:.1f}<前高RSI{peak_rsi:.1f})"
                        )

            # ── 信号 3: 量价背离 ──
            if len(volumes) >= 10:
                vol_5d = sum(volumes[-5:]) / 5
                vol_prev_5d = sum(volumes[-10:-5]) / 5
                if vol_prev_5d > 0:
                    price_up = closes[-1] > closes[-6] if len(closes) >= 6 else False
                    vol_decline = vol_5d < vol_prev_5d * 0.8
                    if price_up and vol_decline:
                        signals[2] = True
                        signal_details.append(
                            f"量价背离(近5日均量{vol_5d/1e6:.1f}M<前5日均量{vol_prev_5d/1e6:.1f}M×0.8)"
                        )

            # ── 信号 4: KDJ J > 100 ──
            if len(kdj_ks) >= 1 and len(kdj_ds) >= 1:
                j_val = 3.0 * kdj_ks[-1] - 2.0 * kdj_ds[-1]
                if j_val > 100:
                    signals[3] = True
                    signal_details.append(f"KDJ J={j_val:.1f}>100")

            # ── 信号 5: 布林上轨外 ──
            if len(boll_uppers) >= 1 and boll_uppers[-1] > 0:
                if current_price > boll_uppers[-1]:
                    signals[4] = True
                    signal_details.append(
                        f"布林上轨外(现价{current_price:.2f}>上轨{boll_uppers[-1]:.2f})"
                    )

        except Exception as e:
            logger.warning(f"[StopLoss] 技术背离计算失败 {symbol}: {e}")
            return None, 1.0

        # ── 缓存结果 ──
        self._tech_divergence_cache[cache_key] = ((signals.copy(), signal_details.copy()), now)

        return self._eval_tech_signals(signals, signal_details, symbol)

    def _eval_tech_signals(self, signals: list, signal_details: list, symbol: str):
        """根据信号数量决定操作：≥4清仓，≥3减半，<3不操作"""
        count = sum(signals)
        detail_str = '; '.join(signal_details)

        if count >= 4:
            return (
                f"技术指标背离清仓({count}/5): {detail_str}",
                1.0
            )
        elif count >= 3:
            return (
                f"技术指标背离减仓({count}/5): {detail_str}",
                0.5
            )
        return None, 1.0

    # ── 规则 3: 大盘相对表现止损（P1-2） ──

    def _check_dynamic_stop(self, float_pnl_pct: float, market_pct: float, symbol: str = "") -> Optional[str]:
        """
        动态止损（大盘感知 + 振幅因子）。
        
        阈值 = -max(f(大盘涨跌), 近5日日均振幅 × 0.4)
        - 大盘跌>2% → 收紧至 -1.5% / 大盘震荡 → -2% / 大盘涨 → 放宽
        - 高振幅个股自动扩宽止损空间，避免被正常波动击穿
        - 强审：大盘跌>2%且个股跑输>3pp → 立即触发
        """
        # 强审：大盘跌 >2% 且个股跌幅显著大于大盘
        if market_pct <= -2.0 and (float_pnl_pct - market_pct) < -3.0:
            return (
                f'动态止损-相对弱势：大盘{market_pct:+.2f}%，'
                f'个股{float_pnl_pct:+.2f}%，跑输 {abs(float_pnl_pct - market_pct):.1f}pp'
            )

        # 大盘感知基础阈值
        if market_pct <= -2.0:
            market_threshold = 1.5
            label = '大盘跌>2%'
        elif -1.0 <= market_pct <= 1.0:
            market_threshold = 2.0
            label = '大盘震荡'
        elif 1.0 < market_pct <= 2.0:
            market_threshold = 3.0
            label = '大盘小涨'
        else:
            market_threshold = 4.0
            label = '大盘大涨'

        # 振幅因子：至少 40% 的日均振幅作为止损空间
        amp_threshold = 2.0  # 默认
        if symbol:
            amp_pct = self._get_amplitude_pct(symbol)
            amp_threshold = amp_pct * 0.4

        threshold = -max(market_threshold, amp_threshold)
        amp_note = f' +振幅{amp_threshold:.1f}%' if amp_threshold > market_threshold else ''

        if float_pnl_pct <= threshold:
            return f'动态止损触发：{label}{amp_note}，止损{-threshold:.1f}%，当前浮亏{float_pnl_pct:.2f}%'

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

    # ── 持仓止损距离计算 ──

    def get_position_stop_distances(self) -> List[Dict[str, Any]]:
        """计算每个持仓到各止损线的距离（正值=安全距离，负值=已触发）。

        返回每个持仓的最近止损线和详细规则距离。
        单次调用超时上限 10 秒，避免因 Tushare API 慢导致接口超时。
        """
        if self.executor is None:
            return []

        start_time = time.time()
        deadline = start_time + 10  # 总超时 10 秒

        try:
            positions = self.executor.get_positions()
        except Exception as e:
            logger.warning(f"[StopLoss] 获取持仓失败: {e}")
            return []

        if not positions:
            return []

        market_pct = self._get_market_change_pct()
        today_volumes = self.executor._get_today_buy_volumes() if self.executor else {}
        results = []

        for pos in positions:
            symbol = pos.get('symbol', '')
            if not symbol:
                continue

            name = pos.get('name', '')
            avg_price = pos.get('avg_price', 0)
            current_price = pos.get('current_price', 0)
            total_volume = pos.get('volume', 0)

            if avg_price <= 0 or current_price <= 0 or total_volume <= 0:
                continue

            today_buy_vol = today_volumes.get(symbol, 0)
            volume = total_volume - today_buy_vol  # 可卖股数
            t1_locked = volume <= 0
            float_pnl_pct = round((current_price - avg_price) / avg_price * 100, 2)

            # 超时保护：剩余时间不足 2 秒则退出，返回已计算的部分结果
            if time.time() > deadline - 2:
                logger.warning(f"[StopLoss] 距离计算超时，已处理 {len(results)}/{len(positions)} 只")
                break

            distances = {
                "rul0a_break_low": self._calc_break_low_distance(symbol, current_price),
                "rul0b_cost_stop": self._calc_cost_stop_distance(symbol, float_pnl_pct, current_price, avg_price),
                "rul1_sector": self._calc_sector_distance(symbol, float_pnl_pct),
                "rul2_iron": self._calc_iron_rule2_distance(symbol, float_pnl_pct, current_price, avg_price),
                "rul2_5_tech": self._calc_tech_divergence_distance(symbol, current_price, float_pnl_pct),
                "rul3_dynamic": self._calc_dynamic_distance(float_pnl_pct, market_pct, symbol),
            }

            # 过滤掉不适用(None)的规则，找出最危险（距离最小）的
            applicable = {k: v for k, v in distances.items() if v is not None}
            min_rule = None
            min_distance = None
            if applicable:
                min_rule = min(applicable, key=applicable.get)
                min_distance = round(applicable[min_rule], 2)

            # 距离等级
            if min_distance is None:
                danger_level = "no_rules"
            elif min_distance < 0:
                danger_level = "triggered"  # 已触发，应止损
            elif min_distance < 1:
                danger_level = "critical"   # 小于 1%，非常危险
            elif min_distance < 3:
                danger_level = "warning"    # 小于 3%，需要关注
            elif min_distance < 5:
                danger_level = "caution"    # 小于 5%，轻微关注
            else:
                danger_level = "safe"

            results.append({
                "symbol": symbol,
                "name": name,
                "avg_price": round(avg_price, 2),
                "current_price": round(current_price, 2),
                "volume": volume,
                "float_pnl_pct": float_pnl_pct,
                "t1_locked": t1_locked,
                "daily_stops_used": self.today_stops.get(symbol, 0),
                "nearest_trigger": {
                    "rule": min_rule,
                    "distance_pct": min_distance,
                    "danger_level": danger_level,
                },
                "rule_distances": {k: round(v, 2) if v is not None else None for k, v in distances.items()},
            })

        # 按危险程度排序：已触发 > 危急 > 警告 > 关注 > 安全
        order = {"triggered": 0, "critical": 1, "warning": 2, "caution": 3, "safe": 4, "no_rules": 5}
        results.sort(key=lambda x: order.get(x["nearest_trigger"]["danger_level"], 5))
        return results

    # ── 各规则的距离计算（正值=距触发还远，负值=已触发） ──

    def _calc_break_low_distance(self, symbol: str, current_price: float) -> Optional[float]:
        """规则 0a：当前价到破底止损线的安全距离(%)"""
        try:
            from app.api.indicator import _normalize_to_ts_code
            ts_code = _normalize_to_ts_code(symbol)
            cached = _cached_fetch_kline(ts_code)
            if cached is None:
                return None
            _high, stage_low, _close = cached
            if stage_low <= 0 or current_price <= 0:
                return None

            base_stop = stage_low * 0.97
            hwm_stop = 0.0
            try:
                hwm_data = self._ensure_hwm(symbol, current_price)
                hwm = hwm_data.get('high_price', 0)
                if hwm > stage_low:
                    hwm_stop = hwm * 0.90
            except Exception:
                pass

            stop_price = max(base_stop, hwm_stop)
            return round((current_price - stop_price) / current_price * 100, 2)
        except Exception:
            return None

    def _calc_cost_stop_distance(
        self, symbol: str, float_pnl_pct: float, current_price: float, avg_price: float
    ) -> Optional[float]:
        """规则 0b：当前浮盈到成本止损线的距离(%)"""
        if avg_price <= 0:
            return None

        hwm = None
        max_profit_pct = 0
        try:
            chain = self.strategy_chain
            if chain:
                hwm_data = chain.get_high_water_mark(symbol)
                if hwm_data:
                    hwm = hwm_data.get('high_price', 0)
                    if hwm and hwm > avg_price:
                        max_profit_pct = round((hwm - avg_price) / avg_price * 100, 2)
        except Exception:
            pass

        # 曾大盈(≥5%) → 不在成本止损范围内，交给规则2
        if max_profit_pct >= 5:
            return None

        # 曾小盈(3-5%) → -3% 止损线
        if max_profit_pct >= 3:
            return round(float_pnl_pct + 3.0, 2)  # distance to -3%

        # 从未盈利 → -4% 止损线
        if hwm is not None:
            return round(float_pnl_pct + 4.0, 2)  # distance to -4%

        # 无 HWM → -6% 底线
        if hwm is None:
            return round(float_pnl_pct + 6.0, 2)  # distance to -6%

        return None

    def _calc_sector_distance(self, symbol: str, float_pnl_pct: float) -> Optional[float]:
        """规则 1：当前背离值到 -3pp 触发线的距离(%)"""
        if float_pnl_pct >= 0:
            return None  # 仅在亏损时适用

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
                    divergence = float_pnl_pct - sector_pct
                    return round(divergence + 3.0, 2)  # distance to -3pp
        except Exception:
            pass

        return None

    def _calc_iron_rule2_distance(
        self, symbol: str, float_pnl_pct: float, current_price: float, avg_price: float
    ) -> Optional[float]:
        """规则 2：当前浮盈到铁律二保护线的距离(%)"""
        # HWM 大盈保本
        try:
            hwm_data = self._ensure_hwm(symbol, current_price)
            hwm = hwm_data.get('high_price', 0)
            if hwm > avg_price:
                max_profit_pct = round((hwm - avg_price) / avg_price * 100, 2)
                if max_profit_pct >= 5:
                    return round(float_pnl_pct + 1.0, 2)  # distance to -1%
        except Exception:
            pass

        # 振幅分档保护线
        amplitude_tier = self._get_amplitude_tier(symbol)
        rules = self._get_iron_rule2_thresholds(amplitude_tier)
        t1 = rules["t1_pct"]
        t1_5, t1_5_protect = rules["t1_5_pct"], rules["t1_5_plus_pct"]
        t2, t2_protect = rules["t2_pct"], rules["t2_plus_pct"]
        t3, t3_protect = rules["t3_pct"], rules["t3_plus_pct"]

        if float_pnl_pct >= t3:
            return round(float_pnl_pct - t3_protect, 2)
        elif float_pnl_pct >= t2:
            return round(float_pnl_pct - t2_protect, 2)
        elif float_pnl_pct >= t1_5:
            return round(float_pnl_pct - t1_5_protect, 2)
        elif float_pnl_pct >= t1:
            # T1 保本：持仓 ≤ 3 天跳过
            holding_days = self._get_holding_days(symbol)
            if holding_days is not None and holding_days <= 3:
                return None  # T1 未启用，返回 None 让上层选其他规则
            return round(float_pnl_pct, 2)  # 保本线 = 0
        else:
            return None  # 未进入盈利区，规则2不适用

    def _calc_dynamic_distance(
        self, float_pnl_pct: float, market_pct: float, symbol: str = ""
    ) -> Optional[float]:
        """规则 3：当前浮亏到动态止损线的距离(%)"""
        # 强审条件：大盘跌>2% 且跑输>3pp
        if market_pct <= -2.0:
            strong_divergence = float_pnl_pct - market_pct
            if strong_divergence < -3.0:
                return round(strong_divergence + 3.0, 2)

        # 大盘感知阈值
        if market_pct <= -2.0:
            market_threshold = 1.5
        elif -1.0 <= market_pct <= 1.0:
            market_threshold = 2.0
        elif 1.0 < market_pct <= 2.0:
            market_threshold = 3.0
        else:
            market_threshold = 4.0

        # 振幅因子
        amp_threshold = 2.0
        if symbol:
            try:
                amp_pct = self._get_amplitude_pct(symbol)
                amp_threshold = amp_pct * 0.4
            except Exception:
                pass

        threshold = -max(market_threshold, amp_threshold)
        return round(float_pnl_pct - threshold, 2)  # distance to dynamic stop

    def _calc_tech_divergence_distance(
        self, symbol: str, current_price: float, float_pnl_pct: float
    ) -> Optional[float]:
        """规则 2.5：到技术背离触发所需的信号数距离。
        正值=还需N个信号才触发 0=已触发放量 -1=已触发清仓 None=无法计算"""
        if float_pnl_pct <= 0:
            return None  # 只在盈利时检查

        today_str = datetime.now().strftime('%Y%m%d')
        cache_key = f"{symbol}_{today_str}"
        cached = self._tech_divergence_cache.get(cache_key)
        if cached is None:
            return None  # 尚未计算，下次扫描会有

        (signals_list, _details), _ts = cached
        count = sum(signals_list)

        if count >= 4:
            return -1.0  # 已触发清仓
        elif count >= 3:
            return -0.5  # 已触发减仓
        else:
            # 距触发还差 (3 - count) 个信号
            return float(3 - count)

    # ── 止损执行 ──

    def _execute_stop(
        self, symbol: str, price: float, volume: int, reason: str, float_pnl_pct: float,
        sell_ratio: float = 1.0
    ) -> None:
        # 早盘冷静期：09:30-09:45 不执行卖出（09:35窗口统计胜率 0%）
        if self._is_morning_volatility():
            logger.info(
                f"[StopLoss] ⏸️ 早盘冷静期，延迟卖出: {symbol} @ {price} | {reason}"
            )
            print(f"[止损] ⏸️ {symbol} 早盘冷静期，延迟卖出: {reason}", file=sys.stderr)
            return

        trigger_key = f"{symbol}_{price:.2f}"
        with self.lock:
            if trigger_key in self._triggered:
                print(f"[止损] ⏭️ {symbol} 已触发过 (key={trigger_key})，跳过", file=sys.stderr)
                return
            self._triggered[trigger_key] = price

        daily_count = self.today_stops.get(symbol, 0)
        if daily_count >= 3:
            logger.warning(f"[StopLoss] {symbol} 今日已止损 {daily_count} 次，跳过")
            print(f"[止损] ⛔ {symbol} 今日已止损{daily_count}次达上限，跳过", file=sys.stderr)
            return

        # 根据 sell_ratio 计算实际卖出量（至少 1 手）
        sell_volume = max(int(volume * sell_ratio), 100) if sell_ratio < 1.0 else volume
        if sell_volume > volume:
            sell_volume = volume

        action_tag = "清仓" if sell_ratio >= 0.99 else f"减仓{sell_ratio*100:.0f}%"
        logger.info(f"[StopLoss] 🔴 触发止损: {symbol} @ {price} x{sell_volume}({action_tag}) | {reason}")
        print(f"[止损] 🔴 {symbol} 止损[{action_tag}] @ {price} x{sell_volume} | 浮盈{float_pnl_pct:+.2f}% | {reason}", file=sys.stderr)

        if self.executor is None:
            logger.error(f"[StopLoss] executor 未注入，无法执行止损: {symbol}")
            print(f"[止损] ❌ {symbol} executor未注入，无法卖出", file=sys.stderr)
            return

        try:
            result = self.executor.sell(
                symbol=symbol,
                price=price,
                volume=sell_volume,
                reason=f'[StopLoss自动] {reason}',
                skip_trend_constraint=True
            )
            if result.get('status') == 'executed':
                self.today_stops[symbol] = daily_count + 1
                logger.info(f"[StopLoss] ✅ 止损已执行: {symbol} @ {price} x{sell_volume}")
                print(f"[止损] ✅ {symbol} 已卖出 {sell_volume}股 @ {price} | {reason}", file=sys.stderr)
                self._log_stop(symbol, price, sell_volume, reason, float_pnl_pct, result.get('profit', 0))
            else:
                logger.warning(f"[StopLoss] ⚠️ 止损执行失败: {symbol} - {result.get('reason', '未知')}")
                print(f"[止损] ⚠️ {symbol} 卖出被拒: {result.get('reason', '未知')}", file=sys.stderr)
        except Exception as e:
            logger.error(f"[StopLoss] ❌ 止损异常: {symbol} - {e}", exc_info=True)
            print(f"[止损] ❌ {symbol} 卖出异常: {e}", file=sys.stderr)

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


def get_stop_loss_monitor(executor=None, interval_seconds: int = 31) -> StopLossMonitor:
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


def get_monitor_status() -> Dict[str, Any]:
    """获取止损监控运行状态的便捷函数。"""
    monitor = get_stop_loss_monitor()
    return monitor.status()


def get_position_distances() -> List[Dict[str, Any]]:
    """获取所有持仓止损距离的便捷函数。"""
    monitor = get_stop_loss_monitor()
    return monitor.get_position_stop_distances()
