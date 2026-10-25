# -*- coding: utf-8 -*-
"""紧急重训信号（原 `risk_manager` 的仓位缩放已删除，2026-10-25）。

保留原因：`model_update_scheduler` 需要「Top-10 连续 3 日跑输基准 → 紧急重训」。
仓位/防御统一由 `defense_cash` 的 G0-G3 决定；本文件不再包含任何 NAV 缩放、
回撤地板、position_scale 逻辑。
"""
import logging
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class RiskManager:
    """只做紧急重训信号：连续跑输计数 + 频率限制。"""

    def __init__(self):
        self.daily_top10_returns: List[Tuple[str, float, float]] = []
        self.consecutive_underperform_days = 0
        self.last_emergency_retrain_date: Optional[str] = None
        self.emergency_retrain_min_interval = 5  # 至少间隔 5 个交易日

    def record_top10_performance(self, date: str, top10_actual_return: float,
                                 benchmark_return: float):
        """记录每日 Top-10 预测表现。"""
        self.daily_top10_returns.append((date, top10_actual_return, benchmark_return))
        if len(self.daily_top10_returns) > 60:
            self.daily_top10_returns = self.daily_top10_returns[-60:]
        if top10_actual_return < 0 and top10_actual_return < benchmark_return:
            self.consecutive_underperform_days += 1
        else:
            self.consecutive_underperform_days = 0

    def emergency_check(self) -> bool:
        """Top-10 连续 3 个交易日跑输基准且平均收益为负。"""
        return self.consecutive_underperform_days >= 3

    def can_emergency_retrain(self, date: str) -> bool:
        """距上次至少 5 个交易日。"""
        if self.last_emergency_retrain_date is None:
            return True
        try:
            last_dt = datetime.strptime(self.last_emergency_retrain_date, "%Y%m%d")
            curr_dt = datetime.strptime(date, "%Y%m%d")
            return (curr_dt - last_dt).days >= self.emergency_retrain_min_interval
        except ValueError:
            return True

    def mark_emergency_retrain(self, date: str):
        self.last_emergency_retrain_date = date

    def get_status(self) -> dict:
        return {
            "consecutive_underperform_days": self.consecutive_underperform_days,
            "emergency_triggered": self.emergency_check(),
            "last_emergency_retrain": self.last_emergency_retrain_date,
        }
