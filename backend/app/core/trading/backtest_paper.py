# -*- coding: utf-8 -*-
"""
回测 PaperTradingEngine 包装器
将真实 PaperTradingEngine 对接回测系统，使用同一套佣金/持仓计算逻辑。
"""
import sys
import os
from datetime import date as _date
from pathlib import Path
from typing import Dict, List, Optional

# 将 paper-trading 目录加入 sys.path
_paper_dir = Path(__file__).parent.parent.parent.parent.parent / "apps" / "paper-trading"
if str(_paper_dir) not in sys.path:
    sys.path.insert(0, str(_paper_dir))

from paper_engine import PaperTradingEngine


class BacktestPaperEngine:
    """
    回测专用 PaperTradingEngine 包装器。

    与真实系统使用完全相同的：
    - 佣金计算（0.05%）
    - 持仓均摊（FIFO 成本）
    - 订单/成交 SQLite 持久化
    - 资金冻结/解冻逻辑
    - match_order 立即撮合成交

    绕过：
    - Xueqiu 股票名称查询（回测不需要）
    """

    def __init__(self, task_id: str, initial_capital: float = 1_000_000):
        self.task_id = task_id
        data_dir = str(Path(__file__).parent.parent.parent.parent.parent
                       / "data" / "backtest" / task_id / "paper")
        os.makedirs(data_dir, exist_ok=True)

        # 创建独立引擎实例
        self._engine = PaperTradingEngine(data_dir=data_dir,
                                           initial_capital=initial_capital)
        # 代理 _get_stock_name 避免网络调用
        self._engine._get_stock_name = lambda s: ""

        # 累计佣金（与 paper_engine 内部 1.0005 倍率对齐：A 股 0.05% 手续费 + 印花税 0.1% 仅卖出）
        self.total_commission = 0.0
        self.COMMISSION_BUY = 0.0005   # 买入：手续费 0.05%
        self.COMMISSION_SELL = 0.0015  # 卖出：手续费 0.05% + 印花税 0.1%

        # T+1 锁定：按买入批次追踪每只股票的买入日期和股数
        # 只有当日买入的股数被锁定，之前买入的股数可自由卖出
        self._buy_lots: Dict[str, List[dict]] = {}  # symbol → [{"date": _date, "volume": int}, ...]
        # 当前回测模拟交易日（由回测引擎在每日循环开始前调用 set_current_date 更新）
        self._current_date: Optional[_date] = None

        # 最近一次下单失败的具体原因 (T+1 锁定 / 资金不足 / 持仓不足 / 撮合拒绝)
        # 供 place_order / 止损调用方读取, 避免统一报"资金/持仓不足"的误导信息
        self.last_error: str = ""

        # 启动时从 PG backtest_trades 重建买入批次，按 (symbol, trade_date) 汇总 volume，
        # 确保进程重启后 T+1 锁定恢复完整。
        self._bootstrap_t1_state()

    def _bootstrap_t1_state(self) -> None:
        """从 PG 加载历史 buy 记录, 重建 T+1 买入批次字典
        按 (symbol, trade_date) 汇总 volume，保留完整的买入日期分布,
        使得加仓后旧股数不受 T+1 锁定影响。
        容错: PG 不可用 / 表不存在 / 任何异常 → 静默降级, _buy_lots 保持空
        """
        try:
            from sqlalchemy import func
            from app.database import SessionLocal
            from app.models.backtest_orm import BacktestTrade
            db = SessionLocal()
            try:
                rows = db.query(
                    BacktestTrade.symbol,
                    BacktestTrade.trade_date,
                    func.sum(BacktestTrade.volume).label("total_volume")
                ).filter(
                    BacktestTrade.task_id == self.task_id,
                    BacktestTrade.direction == "buy"
                ).group_by(BacktestTrade.symbol, BacktestTrade.trade_date
                ).order_by(BacktestTrade.symbol, BacktestTrade.trade_date).all()
                loaded = 0
                for sym, trade_date, total_vol in rows:
                    if not sym or trade_date is None or not total_vol:
                        continue
                    engine_sym = self._to_engine_sym(sym)
                    vol = int(total_vol)
                    lots = self._buy_lots.setdefault(engine_sym, [])
                    lots.append({"date": trade_date, "volume": vol})
                    loaded += 1
                if loaded > 0:
                    print(f"[BacktestPaperEngine] {self.task_id[:8]} 重建 T+1 批次: {loaded} 条记录")
            finally:
                db.close()
        except Exception as e:
            print(f"[BacktestPaperEngine] {self.task_id[:8]} T+1 bootstrap 失败 (降级到空): {e}")

    # ── 符号转换 ──

    @staticmethod
    def _to_engine_sym(symbol: str) -> str:
        """000001.SZ → SZ000001"""
        s = symbol.strip().upper()
        if s.endswith(".SH"):
            return "SH" + s[:-3]
        if s.endswith(".SZ"):
            return "SZ" + s[:-3]
        return s

    # ── T+1 控制 ──

    def set_current_date(self, d: _date) -> None:
        """回测引擎每日循环开始前调用，更新当前模拟交易日
        用于 T+1 校验、买入批次记录 和 引擎 FIFO 日期排序"""
        self._current_date = d
        self._engine._trade_date = d.isoformat()  # 同步到 engine 避免 created_at 排序错误

    def _get_available_volume(self, symbol: str) -> int:
        """返回可卖出股数（买入日期 < 当前日期的 lot volume 之和）"""
        if self._current_date is None:
            return 0
        engine_sym = self._to_engine_sym(symbol)
        lots = self._buy_lots.get(engine_sym, [])
        return sum(lot["volume"] for lot in lots if lot["date"] < self._current_date)

    def _get_locked_volume(self, symbol: str) -> int:
        """返回被 T+1 锁定的股数（买入日期 == 当前日期的 lot volume 之和）"""
        if self._current_date is None:
            return 0
        engine_sym = self._to_engine_sym(symbol)
        lots = self._buy_lots.get(engine_sym, [])
        return sum(lot["volume"] for lot in lots if lot["date"] == self._current_date)

    def _get_last_buy_date(self, symbol: str) -> Optional[_date]:
        """返回该标的最晚买入日期（兼容旧接口）"""
        engine_sym = self._to_engine_sym(symbol)
        lots = self._buy_lots.get(engine_sym, [])
        if not lots:
            return None
        return max(lot["date"] for lot in lots)

    def _is_t1_locked(self, symbol: str) -> bool:
        """检查该标的可卖股数是否为 0（全部被 T+1 锁定）"""
        if self._current_date is None:
            return False
        return self._get_available_volume(symbol) == 0

    def get_t1_status(self, symbol: str) -> dict:
        """查询 T+1 状态（供回测引擎/Pi 决策使用）
        Returns:
            {locked: bool, locked_volume: int, available_volume: int,
             last_buy_date: date|None, unlock_date: date|None, reason: str}
        """
        engine_sym = self._to_engine_sym(symbol)
        available = self._get_available_volume(symbol)
        locked_vol = self._get_locked_volume(symbol)
        last_buy = self._get_last_buy_date(symbol)
        if self._current_date is None:
            return {"locked": False, "locked_volume": 0, "available_volume": 0,
                    "last_buy_date": None, "unlock_date": None,
                    "reason": "无日期上下文"}
        if last_buy is None:
            return {"locked": False, "locked_volume": 0, "available_volume": 0,
                    "last_buy_date": None, "unlock_date": None,
                    "reason": "无 T+1 锁定记录"}
        if locked_vol > 0:
            from datetime import timedelta as _td
            unlock = self._current_date + _td(days=1)
            return {"locked": True, "locked_volume": locked_vol,
                    "available_volume": available,
                    "last_buy_date": last_buy, "unlock_date": unlock,
                    "reason": f"A 股 T+1 规则：{locked_vol}股当日买入锁仓，{available}股可卖，{unlock} 解锁"}
        return {"locked": False, "locked_volume": 0, "available_volume": available,
                "last_buy_date": last_buy, "unlock_date": None,
                "reason": "已过 T+1 锁定"}

    def _deduct_lots(self, symbol: str, volume: int) -> None:
        """FIFO 从非锁定批次中扣减已卖出股数（先扣最早的非锁定批次）"""
        engine_sym = self._to_engine_sym(symbol)
        lots = self._buy_lots.get(engine_sym, [])
        remaining = volume
        # 按日期排序，先扣最早的
        sorted_lots = sorted(
            [(i, lot) for i, lot in enumerate(lots) if lot["date"] < self._current_date],
            key=lambda x: x[1]["date"]
        )
        for idx, lot in sorted_lots:
            if remaining <= 0:
                break
            deduct = min(lot["volume"], remaining)
            lot["volume"] -= deduct
            remaining -= deduct
        # 清理 volume 归零的批次
        self._buy_lots[engine_sym] = [lot for lot in lots if lot["volume"] > 0]

    # ── 下单 ──

    def buy(self, symbol: str, price: float, volume: int) -> Optional[str]:
        """买入。返回订单ID，失败返回None
        回测场景下按批次记录买入日期和股数用于 T+1 校验
        失败原因同时写入 self.last_error, 供日志读取具体原因 (资金不足/跌停)"""
        oid = self._engine.buy(self._to_engine_sym(symbol), price, volume)
        if oid and self._current_date is not None:
            engine_sym = self._to_engine_sym(symbol)
            lots = self._buy_lots.setdefault(engine_sym, [])
            # 同日合并 volume
            for lot in lots:
                if lot["date"] == self._current_date:
                    lot["volume"] += volume
                    break
            else:
                lots.append({"date": self._current_date, "volume": volume})
        if not oid:
            self.last_error = f"资金不足: 需 {price * volume * 1.0005:.2f}, " \
                              f"可用 {self._engine.available_cash:.2f}"
        else:
            self.last_error = ""
        return oid

    def sell(self, symbol: str, price: float, volume: int) -> Optional[str]:
        """卖出。返回订单ID，失败返回None
        A 股 T+1 规则：只有非当日买入的股数可卖。
        如果可卖股数 >= volume，允许卖出并从批次中扣减（FIFO）。
        失败原因同时写入 self.last_error, 供 place_order / 止损日志读取具体原因"""
        available = self._get_available_volume(symbol)
        if available <= 0:
            st = self.get_t1_status(symbol)
            self.last_error = f"T+1锁定: {st['reason']}"
            print(f"[T+1] {symbol} 卖出被拒: {st['reason']}")
            return None
        if volume > available:
            st = self.get_t1_status(symbol)
            self.last_error = f"T+1部分锁定: 需卖{volume}股, 仅{available}股可卖, {st.get('locked_volume', 0)}股锁仓"
            print(f"[T+1] {symbol} 卖出被拒(超额): {self.last_error}")
            return None
        oid = self._engine.sell(self._to_engine_sym(symbol), price, volume)
        if oid:
            self._deduct_lots(symbol, volume)
        self.last_error = ""
        return oid

    def match_order(self, order_id: str, fill_price: float) -> bool:
        """撮合订单。返回是否成交（True/False）"""
        return self._engine.match_order(order_id, fill_price)

    def place_order(self, symbol: str, direction: str, price: float,
                    volume: int) -> dict:
        """统一下单接口。返回 {success, order_id, filled, message}
        message 优先使用 self.last_error (T+1 锁定/超卖/资金不足的具体原因),
        旧版固定 "资金/持仓不足" 已升级"""
        if direction == "buy":
            oid = self.buy(symbol, price, volume)
        else:
            oid = self.sell(symbol, price, volume)

        if not oid:
            # 优先用 self.last_error (T+1 等具体原因), 兜底用 "资金/持仓不足"
            msg = self.last_error or "资金/持仓不足"
            self.last_error = ""
            return {"success": False, "message": msg, "order_id": None, "filled": False}

        # 立即按同一价格撮合（回测场景：行情已知、即时成交）
        ok = self.match_order(oid, fill_price=price)

        # 累计佣金（按成交金额 × 费率）
        if ok:
            amount = price * volume
            rate = self.COMMISSION_BUY if direction == "buy" else self.COMMISSION_SELL
            self.total_commission += round(amount * rate, 2)

        return {
            "success": bool(ok),
            "order_id": oid,
            "filled": bool(ok),
            "message": "成交" if ok else "撮合失败",
        }

    # ── 账户查询 ──

    def get_account(self, day_df=None) -> dict:
        """获取账户状态（与真实 executor 格式兼容）

        Args:
            day_df: 当日行情 DataFrame (回测时传入, 用于按当前价算 position_value + 浮盈)
                    列需含 'close'，索引为 ts_code (000001.SZ 格式)
                    缺省时 position_value 用 avg_price 算 (无浮盈)
        """
        engine = self._engine
        position_value = 0.0   # 当前价市值
        cost_value = 0.0       # 成本市值
        float_pnl = 0.0        # 浮盈 = current_value - cost_value
        for sym, pos in engine.positions.items():
            vol = pos.volume
            cost = float(getattr(pos, "avg_price", 0))
            cost_value += vol * cost
            if day_df is not None:
                # 把 SH000001 → 000001.SZ 格式
                ts_code = f"{sym[2:]}.{sym[:2]}" if len(sym) > 2 and sym[:2] in ("SH", "SZ") else sym
                cur = None
                try:
                    if ts_code in day_df.index:
                        cur = float(day_df.loc[ts_code, "close"])
                except Exception:
                    cur = None
                if cur is None or cur <= 0:
                    cur = cost  # 降级用成本价
                position_value += vol * cur
                float_pnl += vol * (cur - cost)
            else:
                position_value += vol * cost

        total_asset = engine.available_cash + engine.frozen_cash + position_value
        return {
            "initial_capital": engine.initial_capital,
            "available_cash": round(engine.available_cash, 2),
            "frozen_cash": round(engine.frozen_cash, 2),
            "position_value": round(position_value, 2),
            "cost_value": round(cost_value, 2),
            "float_pnl": round(float_pnl, 2),
            "total_asset": round(total_asset, 2),
            "position_count": len(engine.positions),
            "return_pct": round((total_asset / engine.initial_capital - 1) * 100, 2),
        }

    def get_positions(self) -> list:
        """获取持仓列表（PG 兼容格式），包含 T+1 可卖/锁定股数"""
        result = []
        for sym, pos in self._engine.positions.items():
            # SZ000001 → 000001.SZ
            display_sym = f"{sym[2:]}.{sym[:2]}"
            available = self._get_available_volume(display_sym)
            locked = self._get_locked_volume(display_sym)
            result.append({
                "symbol": display_sym,
                "volume": pos.volume,
                "available_volume": available,
                "locked_volume": locked,
                "avg_cost": round(pos.avg_price, 3),
                "frozen": pos.frozen,
                "entry_date": pos.entry_date,
                "highest_price": pos.highest_price,
            })
        return result
