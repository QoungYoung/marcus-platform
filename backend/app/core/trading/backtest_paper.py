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

        # T+1 锁定：记录每只股票最后一次买入的交易日
        # 卖出时只能卖 _last_buy_date[symbol] 之前的持仓（T+1 之前不允许卖）
        self._last_buy_date: Dict[str, _date] = {}
        # 当前回测模拟交易日（由回测引擎在每日循环开始前调用 set_current_date 更新）
        self._current_date: Optional[_date] = None

        # 最近一次下单失败的具体原因 (T+1 锁定 / 资金不足 / 持仓不足 / 撮合拒绝)
        # 供 place_order / 止损调用方读取, 避免统一报"资金/持仓不足"的误导信息
        self.last_error: str = ""

        # ⚠️ P0 修复: 后端进程重启会导致 _last_buy_date 内存清空, T+1 形同虚设.
        # 启动时从 PG backtest_trades 重建: 对每只票取最后一次 buy 的 trade_date.
        # (SQLite positions.entry_date 用的是服务器时间 datetime.now(), 不能用于回测)
        self._bootstrap_t1_state()

    def _bootstrap_t1_state(self) -> None:
        """从 PG 加载历史 buy 记录, 重建 T+1 内存字典
        容错: PG 不可用 / 表不存在 / 任何异常 → 静默降级, _last_buy_date 保持空
        (旧逻辑行为), 至少不会让新代码崩
        """
        try:
            from sqlalchemy import func, asc
            from app.database import SessionLocal
            from app.models.backtest_orm import BacktestTrade
            db = SessionLocal()
            try:
                # 查 (symbol, MAX(trade_date) WHERE direction=buy GROUP BY symbol)
                rows = db.query(
                    BacktestTrade.symbol,
                    func.max(BacktestTrade.trade_date).label("last_buy")
                ).filter(
                    BacktestTrade.task_id == self.task_id,
                    BacktestTrade.direction == "buy"
                ).group_by(BacktestTrade.symbol).all()
                loaded = 0
                for sym, last_buy in rows:
                    if not sym or last_buy is None:
                        continue
                    engine_sym = self._to_engine_sym(sym)
                    self._last_buy_date[engine_sym] = last_buy
                    loaded += 1
                if loaded > 0:
                    print(f"[BacktestPaperEngine] {self.task_id[:8]} 重建 T+1 状态: {loaded} 只票")
            finally:
                db.close()
        except Exception as e:
            # 降级: 不要因为 T+1 bootstrap 失败导致整个回测引擎起不来
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
        用于 T+1 校验、_last_buy_date 记录 和 引擎 FIFO 日期排序"""
        self._current_date = d
        self._engine._trade_date = d.isoformat()  # 同步到 engine 避免 created_at 排序错误

    def _is_t1_locked(self, symbol: str) -> bool:
        """检查该标的是否处于 T+1 锁定状态（当日买入当日不可卖）"""
        if self._current_date is None:
            return False  # 未启用日期上下文时不做拦截（兼容旧调用）
        engine_sym = self._to_engine_sym(symbol)
        last_buy = self._last_buy_date.get(engine_sym)
        if last_buy is None:
            return False
        # 当日买入的股票，当日不可卖（A 股 T+1 严格规则）
        return self._current_date <= last_buy

    def get_t1_status(self, symbol: str) -> dict:
        """查询 T+1 状态（供回测引擎/Pi 决策使用）
        Returns:
            {locked: bool, last_buy_date: date|None, unlock_date: date|None, reason: str}
        """
        engine_sym = self._to_engine_sym(symbol)
        last_buy = self._last_buy_date.get(engine_sym)
        if last_buy is None or self._current_date is None:
            return {"locked": False, "last_buy_date": None,
                    "unlock_date": None, "reason": "无 T+1 锁定记录"}
        if self._current_date <= last_buy:
            from datetime import timedelta as _td
            unlock = last_buy + _td(days=1)
            return {"locked": True, "last_buy_date": last_buy,
                    "unlock_date": unlock,
                    "reason": f"A 股 T+1 规则：当日买入的股票当日不可卖，{unlock} 解锁"}
        return {"locked": False, "last_buy_date": last_buy,
                "unlock_date": None, "reason": "已过 T+1 锁定"}

    # ── 下单 ──

    def buy(self, symbol: str, price: float, volume: int) -> Optional[str]:
        """买入。返回订单ID，失败返回None
        回测场景下记录 _last_buy_date 用于 T+1 校验
        失败原因同时写入 self.last_error, 供日志读取具体原因 (资金不足/跌停)"""
        oid = self._engine.buy(self._to_engine_sym(symbol), price, volume)
        if oid and self._current_date is not None:
            self._last_buy_date[self._to_engine_sym(symbol)] = self._current_date
        if not oid:
            # 资金不足是 buy 唯一失败原因 (paper_engine.buy line 458-460)
            self.last_error = f"资金不足: 需 {price * volume * 1.0005:.2f}, " \
                              f"可用 {self._engine.available_cash:.2f}"
        else:
            self.last_error = ""
        return oid

    def sell(self, symbol: str, price: float, volume: int) -> Optional[str]:
        """卖出。返回订单ID，失败返回None
        ⚠️ A 股 T+1 规则：当日买入的股票当日不可卖（即使已成交）
        失败时可通过 get_t1_status(symbol) 查询锁定原因
        失败原因同时写入 self.last_error, 供 place_order / 止损日志读取具体原因"""
        if self._is_t1_locked(symbol):
            st = self.get_t1_status(symbol)
            self.last_error = f"T+1锁定: {st['reason']}"
            print(f"[T+1] {symbol} 卖出被拒: {st['reason']}")
            return None
        self.last_error = ""
        return self._engine.sell(self._to_engine_sym(symbol), price, volume)

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
        """获取持仓列表（PG 兼容格式）"""
        result = []
        for sym, pos in self._engine.positions.items():
            # SZ000001 → 000001.SZ
            display_sym = f"{sym[2:]}.{sym[:2]}"
            result.append({
                "symbol": display_sym,
                "volume": pos.volume,
                "avg_cost": round(pos.avg_price, 3),
                "frozen": pos.frozen,
                "entry_date": pos.entry_date,
                "highest_price": pos.highest_price,
            })
        return result
