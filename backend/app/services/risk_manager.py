# -*- coding: utf-8 -*-
"""风险管理器 v3：动态仓位缩放 + 紧急重训信号。

核心原则：
  - 连续仓位缩放（1.0→0.2），永不清仓
  - 线性区间 (dd 2%→5%) + 加速区间 (dd 5%→8%)
  - 紧急重训信号基于 Top-10 连续 3 日跑输基准
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RiskManager:
    """NAV 跟踪 + 动态仓位缩放 + 紧急重训检测。

    仓位缩放曲线：
      drawdown < 2%   → scale = 1.0（满仓）
      2% ≤ dd ≤ 5%    → scale = 1.0 → 0.6（线性下降）
      5% < dd ≤ 8%    → scale = 0.6 → 0.3（加速下降）
      dd > 8%          → scale = 0.2（地板，永不为零）
    """

    def __init__(self, initial_nav: float = 1.0,
                 max_position_scale: float = 1.0,
                 min_position_scale: float = 0.2,
                 dd_mild: float = 0.02,
                 dd_moderate: float = 0.05,
                 dd_severe: float = 0.08):
        self.nav = initial_nav
        self.peak_nav = initial_nav
        self.nav_history: List[Tuple[str, float]] = []  # [(date, nav), ...]

        self.max_scale = max_position_scale
        self.min_scale = min_position_scale
        self.dd_mild = dd_mild          # 2% — 开始降仓
        self.dd_moderate = dd_moderate  # 5% — 线性→加速转折
        self.dd_severe = dd_severe      # 8% — 地板

        # 紧急重训信号追踪
        self.daily_top10_returns: List[Tuple[str, float, float]] = []
        # [(date, top10_actual_return, benchmark_return), ...]
        self.consecutive_underperform_days = 0

        # 紧急重训频率限制
        self.last_emergency_retrain_date: Optional[str] = None
        self.emergency_retrain_min_interval = 5  # 至少间隔 5 个交易日

    # ── NAV 跟踪 ───────────────────────────────────────────

    def update_nav(self, date: str, daily_pnl_pct: float):
        """每日收盘后更新 NAV。

        Args:
            date: 交易日期 YYYYMMDD
            daily_pnl_pct: 当日组合收益率（小数，如 0.012 表示 +1.2%）
        """
        self.nav *= (1 + daily_pnl_pct)
        self.nav_history.append((date, self.nav))

        if self.nav > self.peak_nav:
            self.peak_nav = self.nav

        # 只保留最近 500 天
        if len(self.nav_history) > 500:
            self.nav_history = self.nav_history[-500:]

    @property
    def drawdown(self) -> float:
        """当前回撤比例（0.0 ~ 1.0）。"""
        if self.peak_nav <= 0:
            return 0.0
        return (self.peak_nav - self.nav) / self.peak_nav

    # ── 仓位缩放 ──────────────────────────────────────────

    def position_scale(self) -> float:
        """根据当前回撤返回仓位缩放系数。

        1.0 (dd<2%) → 线性 0.6 (dd=5%) → 加速 0.3 (dd=8%) → 地板 0.2
        """
        dd = self.drawdown

        if dd < self.dd_mild:
            return self.max_scale

        if dd <= self.dd_moderate:
            # 线性: 1.0 @ 2% → 0.6 @ 5%
            t = (dd - self.dd_mild) / (self.dd_moderate - self.dd_mild)
            return round(self.max_scale - t * (self.max_scale - 0.6), 3)

        if dd <= self.dd_severe:
            # 加速: 0.6 @ 5% → 0.3 @ 8%
            t = (dd - self.dd_moderate) / (self.dd_severe - self.dd_moderate)
            return round(0.6 - t * 0.3, 3)

        # 地板
        return self.min_scale

    # ── 紧急重训信号 ─────────────────────────────────────

    def record_top10_performance(self, date: str, top10_actual_return: float,
                                 benchmark_return: float):
        """记录每日 Top-10 预测表现。

        Args:
            date: 交易日期
            top10_actual_return: Top-10 预测股票当日实际平均收益（小数）
            benchmark_return: 基准指数当日收益（小数）
        """
        self.daily_top10_returns.append((date, top10_actual_return, benchmark_return))
        if len(self.daily_top10_returns) > 60:
            self.daily_top10_returns = self.daily_top10_returns[-60:]

        # 更新连续跑输计数
        if top10_actual_return < 0 and top10_actual_return < benchmark_return:
            self.consecutive_underperform_days += 1
        else:
            self.consecutive_underperform_days = 0

    def emergency_check(self) -> bool:
        """检查是否触发紧急重训信号。

        Returns True 当 Top-10 连续 3 个交易日跑输基准且平均收益为负。
        """
        return self.consecutive_underperform_days >= 3

    def can_emergency_retrain(self, date: str) -> bool:
        """检查紧急重训频率限制：距上次至少 5 个交易日。

        Args:
            date: 当前日期 YYYYMMDD
        """
        if self.last_emergency_retrain_date is None:
            return True

        try:
            last_dt = datetime.strptime(self.last_emergency_retrain_date, "%Y%m%d")
            curr_dt = datetime.strptime(date, "%Y%m%d")
            days_diff = (curr_dt - last_dt).days
            return days_diff >= self.emergency_retrain_min_interval
        except ValueError:
            return True

    def mark_emergency_retrain(self, date: str):
        """记录紧急重训日期。"""
        self.last_emergency_retrain_date = date

    # ── 状态查询 ─────────────────────────────────────────

    def get_status(self) -> dict:
        """返回当前风险状态摘要。"""
        return {
            "nav": round(self.nav, 6),
            "peak_nav": round(self.peak_nav, 6),
            "drawdown": round(self.drawdown, 4),
            "position_scale": self.position_scale(),
            "consecutive_underperform_days": self.consecutive_underperform_days,
            "emergency_triggered": self.emergency_check(),
            "last_emergency_retrain": self.last_emergency_retrain_date,
        }
