#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus × VN.PY 交易执行器
将 Marcus 的交易决策自动落库到 VN.PY 模拟交易系统

用法:
    marcus-trade buy SH600519 1700 100 --reason "财报超预期"
    marcus-trade sell SH600519 1720 50 --reason "止盈"
    marcus-trade account
    marcus-trade positions
    marcus-trade history --limit 20
"""

import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path

# 工作区根目录 — Marcus 使用独立 workspace
# Cross-platform workspace detection
from workspace_detector import WORKSPACE, VNPY_DIR, XUEQIU_DIR, AKSHARE_DIR, MARCUS_INTEGRATION_DIR, DATA_DIR

sys.path.insert(0, str(VNPY_DIR))
sys.path.insert(0, str(XUEQIU_DIR))
sys.path.insert(0, str(AKSHARE_DIR))
sys.path.insert(0, str(MARCUS_INTEGRATION_DIR))

from paper_engine import PaperTradingEngine


def parse_float_chinese(value):
    """解析中文数字格式 (带逗号、括号等后缀)"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # 移除逗号、空格
        cleaned = value.replace(',', '').replace(' ', '')
        # 提取数字部分 (支持负数和小数)
        import re
        match = re.match(r'^-?\d+\.?\d*', cleaned)
        if match:
            return float(match.group())
        return 0.0
    return 0.0


class MarcusVNPyExecutor:
    """Marcus × VN.PY 交易执行器"""
    
    def __init__(self):
        self.data_dir = str(DATA_DIR)
        self.engine = PaperTradingEngine(data_dir=self.data_dir)
        self.trade_log_path = DATA_DIR / "marcus_trades.jsonl"
        self.risk_log_path = DATA_DIR / "marcus_risk.jsonl"
        
        # 确保数据目录存在
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # ── 回撤熔断 ──
    # 连续亏损计数器（跨会话持久化需要时可以改为文件存储）
    self._consecutive_losses: int = 0
    
    def _get_total_drawdown_pct(self) -> float:
        """获取当前总回撤百分比（负数表示亏损）"""
        try:
            account = self.get_account()
            total_pnl_str = account.get('total_profit', '0')
            import re
            match = re.search(r'([+-]?\d+\.?\d*)', str(total_pnl_str))
            if match:
                total_pnl = float(match.group(1))
            else:
                total_pnl = 0.0
            initial = account.get('initial_capital', 100000)
            return (total_pnl / initial) * 100 if initial > 0 else 0.0
        except Exception:
            return 0.0
    
    def _get_today_buy_symbols(self) -> set:
        """查询 trades.db 获取今日买入的股票代码集合（用于 T+1 拦截）"""
        today_symbols = set()
        try:
            import sqlite3
            db_path = Path(self.data_dir) / "trades.db"
            if not db_path.exists():
                return today_symbols
            conn = sqlite3.connect(str(db_path), timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            cursor = conn.cursor()
            today_str = datetime.now().strftime('%Y-%m-%d')
            cursor.execute(
                "SELECT DISTINCT symbol FROM trades WHERE direction='买入' AND date(created_at)=?",
                (today_str,)
            )
            for row in cursor.fetchall():
                today_symbols.add(row[0])
            conn.close()
        except Exception as e:
            print(f"[T+1] 查询今日买入记录失败（非致命）：{e}", file=sys.stderr)
        return today_symbols
    
    def _get_pi_recommended_limit(self) -> int:
        """从策略链获取 Pi 建议的仓位上限百分比"""
        try:
            from core.utils.strategy_chain import StrategyChain
            chain = StrategyChain()
            pi_conf = chain.get_pi_confirmation()
            return int(pi_conf.get('position_limit', 60))
        except Exception:
            return 60
    
    def _log_trade(self, trade_record: dict) -> None:
        """记录交易到 JSONL 日志文件"""
        try:
            with open(self.trade_log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(trade_record, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"[日志] 记录交易失败: {e}")
    
    def get_account(self) -> dict:
        """获取账户信息 (标准化字段名)"""
        raw = self.engine.get_account_info()
        
        # 计算正确的总盈亏 = 浮动盈亏 + 已实现盈亏
        initial_capital = parse_float_chinese(raw.get('初始资金', 1000000))
        available_cash = parse_float_chinese(raw.get('可用资金', 0))
        
        # 持仓成本
        positions = self.engine.get_positions()
        total_cost = sum(pos['volume'] * pos['avg_price'] for pos in positions)
        
        # 用雪球实时价格计算持仓市值和浮动盈亏
        import sys
        from pathlib import Path
        xueqiu_dir = Path(__file__).parent.parent / "xueqiu-data-query"
        sys.path.insert(0, str(xueqiu_dir))
        from xueqiu_engine import XueqiuEngine
        
        xueqiu_config = xueqiu_dir / "config.json"
        position_value = 0
        float_pnl = 0
        
        if xueqiu_config.exists():
            try:
                xueqiu = XueqiuEngine(config_file=str(xueqiu_config))
                for pos in positions:
                    try:
                        quote = xueqiu.get_stock_quote(pos['symbol'], use_cache=False)
                        if quote:
                            current_price = quote.get('current', pos['avg_price'])
                            position_value += current_price * pos['volume']
                        else:
                            position_value += pos['avg_price'] * pos['volume']
                    except:
                        position_value += pos['avg_price'] * pos['volume']
            except Exception as e:
                print(f"[警告] 获取实时价格失败：{e}")
                position_value = total_cost
        else:
            position_value = total_cost
        
        float_pnl = position_value - total_cost
        
        # 已实现盈亏 = 从 trades 表查询
        import sqlite3
        data_dir = Path(self.data_dir) if isinstance(self.data_dir, str) else self.data_dir
        conn = sqlite3.connect(str(data_dir / "trades.db"), timeout=30)
        cursor = conn.cursor()
        conn.execute("PRAGMA busy_timeout=30000")
        cursor.execute('SELECT SUM(profit) FROM trades WHERE direction = "卖出"')
        realized_pnl = cursor.fetchone()[0] or 0
        conn.close()
        
        # 总盈亏 = 浮动盈亏 + 已实现盈亏
        total_pnl = float_pnl + realized_pnl
        total_asset = available_cash + position_value
        
        return {
            'initial_capital': initial_capital,
            'available_cash': available_cash,
            'frozen_cash': parse_float_chinese(raw.get('冻结资金', 0)),
            'position_value': position_value,
            'total_asset': total_asset,
            'total_profit': f"{total_pnl:+,.2f} ({total_pnl/initial_capital*100:+.2f}%)",
            'position_count': len(positions),
            'float_pnl': float_pnl,
            'realized_pnl': realized_pnl
        }
    
    def check_risk(self, symbol: str, price: float, volume: int, side: str) -> dict:
        """
        风控检查（增强版 — 包含回撤熔断、T+1拦截、仓位利用率检查）
        
        Returns:
            {"allowed": bool, "reason": str, "data": dict}
        """
        account = self.get_account()
        required_cash = price * volume * 1.003  # 含佣金估算
        
        risk_data = {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'side': side,
            'price': price,
            'volume': volume,
            'required_cash': required_cash
        }
        
        # ── 规则 0: 回撤熔断（优先级最高） ──
        drawdown_pct = self._get_total_drawdown_pct()
        if side == 'buy' and drawdown_pct <= -5.0:
            risk_data['reason'] = f'回撤熔断：总回撤 {drawdown_pct:.2f}% 已达 -5% 硬禁止线，停止所有买入'
            risk_data['drawdown_pct'] = drawdown_pct
            self._log_risk(risk_data)
            return {'allowed': False, 'reason': f'回撤熔断（{drawdown_pct:.2f}%）', 'data': risk_data}
        
        # ── 规则 0.5: 连续亏损熔断 ──
        if side == 'buy' and self._consecutive_losses >= 3:
            risk_data['reason'] = f'连续亏损熔断：已连续亏损 {self._consecutive_losses} 笔，停止当日所有买入'
            self._log_risk(risk_data)
            return {'allowed': False, 'reason': f'连续亏损熔断（{self._consecutive_losses}笔）', 'data': risk_data}
        
        # ── 规则 0.6: T+1 拦截（卖出方向） ──
        if side == 'sell':
            today_buy_symbols = self._get_today_buy_symbols()
            if symbol in today_buy_symbols:
                risk_data['reason'] = f'T+1 拦截：{symbol} 为今日买入，当日不可卖出'
                risk_data['t1_blocked'] = True
                self._log_risk(risk_data)
                return {'allowed': False, 'reason': f'T+1 拦截（{symbol}今日买入）', 'data': risk_data}
        
        # 规则 1: 资金检查
        if side == 'buy' and required_cash > account['available_cash']:
            risk_data['reason'] = '资金不足'
            risk_data['available'] = account['available_cash']
            self._log_risk(risk_data)
            return {'allowed': False, 'reason': '资金不足', 'data': risk_data}
        
        # 规则 2: 单笔最大仓位 (40%)
        max_position = account['initial_capital'] * 0.40
        if side == 'buy' and required_cash > max_position:
            # 自动调整到上限（而不是拒绝）
            adjusted_volume = int(max_position / price / 100) * 100  # 100股整数倍
            if adjusted_volume >= 100:
                risk_data['reason'] = '自动调整到单笔最大仓位'
                risk_data['max_allowed'] = max_position
                risk_data['adjusted_volume'] = adjusted_volume
                risk_data['adjusted_price'] = price
                self._log_risk(risk_data)
                return {'allowed': True, 'reason': '自动调整到单笔最大仓位', 'data': risk_data, 'adjusted': True, 'adjusted_volume': adjusted_volume}
            else:
                risk_data['reason'] = '超过单笔最大仓位 (40%)'
                risk_data['max_allowed'] = max_position
                self._log_risk(risk_data)
                return {'allowed': False, 'reason': '超过单笔最大仓位 (40%)', 'data': risk_data}
        
        # 规则 3: 卖出时检查持仓
        if side == 'sell':
            positions = self.engine.get_positions()
            pos = next((p for p in positions if p.get('symbol') == symbol), None)
            if not pos:
                risk_data['reason'] = '无持仓'
                self._log_risk(risk_data)
                return {'allowed': False, 'reason': '无持仓', 'data': risk_data}
            if volume > pos.get('volume', 0):
                risk_data['reason'] = '卖出数量超过持仓'
                risk_data['have'] = pos.get('volume', 0)
                self._log_risk(risk_data)
                return {'allowed': False, 'reason': '卖出数量超过持仓', 'data': risk_data}
        
        # ── 规则 4: 仓位利用率检查（仅买入方向，软警告不硬拦截） ──
        if side == 'buy':
            pi_limit = self._get_pi_recommended_limit()
            actual_position_ratio = account.get('position_value', 0) / account.get('total_asset', 1) * 100
            # 如果 Pi 建议仓位 > 实际仓位的 3 倍（即利用率 < 33%），注入警告
            if pi_limit > 0 and (pi_limit > actual_position_ratio * 3) and pi_limit >= 20:
                risk_data['position_utilization_warning'] = (
                    f'仓位利用率过低：Pi建议{pi_limit}%，实际持仓{actual_position_ratio:.1f}%（利用率{actual_position_ratio/pi_limit*100:.0f}%），'
                    f'请关注仓位脱节问题'
                )
        
        # 风控通过
        risk_data['status'] = 'passed'
        risk_data['drawdown_pct'] = round(drawdown_pct, 2)
        if 'position_utilization_warning' in risk_data:
            risk_data['status'] = 'passed_with_warning'
        self._log_risk(risk_data)
        return {'allowed': True, 'reason': '风控通过', 'data': risk_data}
    
    def _log_risk(self, risk_data: dict):
        """记录风控日志"""
        with open(self.risk_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(risk_data, ensure_ascii=False) + "\n")
    
    def buy(self, symbol: str, price: float, volume: int, reason: str = "") -> dict:
        """买入操作 - 通过完整订单流程成交，失败时解冻资金"""
        # 风控检查
        risk_result = self.check_risk(symbol, price, volume, 'buy')

        # 检查是否需要自动调整仓位
        if risk_result.get('adjusted') and risk_result.get('adjusted_volume'):
            volume = risk_result['adjusted_volume']
            print(f"[风控] 自动调整 {symbol} 买入数量: {volume}股", file=sys.stderr)

        if not risk_result['allowed']:
            return {
                'status': 'rejected',
                'reason': risk_result['reason'],
                'risk_data': risk_result['data']
            }

        # 计算总成本 (含佣金)
        total_cost = price * volume * 1.0003  # 0.03% 佣金

        account = self.get_account()
        if total_cost > account['available_cash']:
            return {
                'status': 'rejected',
                'reason': '资金不足',
                'required': total_cost,
                'available': account['available_cash']
            }

        # 通过完整订单流程执行（与 sell() 保持一致）
        order_id = self.engine.buy(symbol, price, volume)
        if not order_id:
            return {'status': 'failed', 'reason': 'VN.PY 买入失败'}

        # 自动成交 (模拟)，失败时解冻资金
        match_ok = self.engine.match_order(order_id, price)
        if not match_ok:
            self.engine.cancel_order(order_id)
            print(f"[交易] ⚠️ {symbol} 撮合失败，资金已解冻", file=sys.stderr)
            return {'status': 'failed', 'reason': 'VN.PY 撮合失败，资金已解冻'}

        # 创建订单记录
        trade_record = {
            'type': 'buy',
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'price': price,
            'volume': volume,
            'order_id': order_id,
            'reason': reason,
            'status': 'executed',
            'cost': total_cost
        }
        self._log_trade(trade_record)

        return {
            'status': 'executed',
            'order_id': order_id,
            'symbol': symbol,
            'price': price,
            'volume': volume,
            'reason': reason,
            'cost': total_cost,
            'timestamp': trade_record['timestamp']
        }
    
    def sell(self, symbol: str, price: float, volume: int, reason: str = "") -> dict:
        """卖出操作"""
        # 风控检查
        risk_result = self.check_risk(symbol, price, volume, 'sell')
        if not risk_result['allowed']:
            return {
                'status': 'rejected',
                'reason': risk_result['reason'],
                'risk_data': risk_result['data']
            }
        
        # === 在卖出前计算盈亏 ===
        # 获取当前持仓成本（卖出前）
        positions = self.engine.get_positions()
        pos = next((p for p in positions if p.get('symbol') == symbol), None)
        avg_cost = pos.get('avg_price', 0) if pos else 0
        profit = (price - avg_cost) * volume if avg_cost > 0 else 0.0
        
        # 执行卖出
        order_id = self.engine.sell(symbol, price, volume)
        if not order_id:
            return {'status': 'failed', 'reason': 'VN.PY 卖出失败'}
        
        # 自动成交 (模拟)
        self.engine.match_order(order_id, price)
        
        # 记录交易日志
        trade_record = {
            'type': 'sell',
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'price': price,
            'volume': volume,
            'order_id': order_id,
            'reason': reason,
            'status': 'executed',
            'profit': profit
        }
        self._log_trade(trade_record)
        
        # 记录交易结果用于连续亏损追踪
        self.record_trade_result(symbol, profit)
        
        return {
            'status': 'executed',
            'order_id': order_id,
            'symbol': symbol,
            'price': price,
            'volume': volume,
            'reason': reason,
            'profit': profit,
            'timestamp': trade_record['timestamp']
        }
    
    def _calc_profit(self, symbol: str, sell_price: float, volume: int) -> float:
        """计算卖出盈亏 (简化版)"""
        # 从数据库获取真实持仓成本
        positions = self.get_positions_from_db()
        pos = next((p for p in positions if p.get('symbol') == symbol), None)
        if pos:
            cost_price = pos.get('avg_price', 0)
            return (sell_price - cost_price) * volume
        return 0.0
    
    def get_positions_from_db(self) -> list:
        """从 trades.db 直接查询真实持仓（修复数据不同步问题）"""
        import sqlite3
        from pathlib import Path
        
        data_dir = Path(self.data_dir) if isinstance(self.data_dir, str) else self.data_dir
        conn = sqlite3.connect(str(data_dir / "trades.db"), timeout=30)
        cursor = conn.cursor()
        conn.execute("PRAGMA busy_timeout=30000")
        
        # 按 FIFO 计算真实持仓
        cursor.execute('SELECT symbol, direction, price, volume FROM trades ORDER BY created_at')
        trades = cursor.fetchall()
        
        # FIFO 成本计算
        positions = {}
        for symbol, direction, price, vol in trades:
            if symbol not in positions:
                positions[symbol] = []
            
            if direction == '买入':
                positions[symbol].append({'price': price, 'volume': vol})
            else:  # 卖出
                remaining = vol
                lots = positions[symbol]
                i = 0
                while remaining > 0 and i < len(lots):
                    lot = lots[i]
                    used = min(lot['volume'], remaining)
                    lot['volume'] -= used
                    remaining -= used
                    if lot['volume'] == 0:
                        lots.pop(i)
                    else:
                        i += 1
        
        # 转换为持仓格式
        result = []
        for symbol, lots in positions.items():
            total_vol = sum(lot['volume'] for lot in lots)
            if total_vol > 0:
                total_cost = sum(lot['price'] * lot['volume'] for lot in lots)
                avg_price = total_cost / total_vol
                result.append({
                    'symbol': symbol,
                    'volume': total_vol,
                    'avg_price': avg_price
                })
        
        conn.close()
        return result
    
    def get_positions(self) -> list:
        """获取持仓（优先从数据库读取）"""
        return self.get_positions_from_db()
    
    def record_trade_result(self, symbol: str, profit: float) -> None:
        """
        记录交易结果用于连续亏损追踪。
        卖出时调用此方法，profit > 0 重置计数器，profit <= 0 累加。
        """
        if profit > 0:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
        print(f"[风控] 连续亏损计数: {self._consecutive_losses} (最近: {symbol} {profit:+.2f})", file=sys.stderr)
    
    def reset_consecutive_losses(self) -> None:
        """重置连续亏损计数器（新交易日调用）"""
        self._consecutive_losses = 0
    
    def get_trade_history(self, limit: int = 20) -> list:
        """获取交易历史"""
        if not self.trade_log_path.exists():
            return []
        
        trades = []
        with open(self.trade_log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    trades.append(json.loads(line))
        
        return trades[-limit:]
    
    def show_account(self):
        """显示账户信息"""
        account = self.get_account()
        print("\n" + "=" * 60)
        print("📈 Marcus × VN.PY 模拟交易账户")
        print("=" * 60)
        print(f"  初始资金：  ¥{account['initial_capital']:,.2f}")
        print(f"  可用资金：  ¥{account['available_cash']:,.2f}")
        print(f"  冻结资金：  ¥{account['frozen_cash']:,.2f}")
        print(f"  持仓市值：  ¥{account['position_value']:,.2f}")
        print(f"  总资产：    ¥{account['total_asset']:,.2f}")
        print(f"  总盈亏：    {account['total_profit']}")
        print(f"  持仓数量：  {account['position_count']}")
        print("=" * 60)
    
    def show_positions(self):
        """显示持仓"""
        positions = self.get_positions()
        
        print("\n" + "=" * 70)
        print("📊 当前持仓")
        print("=" * 70)
        
        if not positions:
            print("  暂无持仓")
        else:
            print(f"{'代码':<15} {'数量':>10} {'成本价':>12} {'成本市值':>15}")
            print("-" * 70)
            for pos in positions:
                symbol = pos.get('symbol', 'N/A')
                volume = pos.get('volume', 0)
                avg_price = pos.get('avg_price', 0)
                market_value = volume * avg_price
                print(f"{symbol:<15} {volume:>10} {avg_price:>12.2f} {market_value:>15.2f}")
        
        print("=" * 70)
    
    def show_history(self, limit: int = 20):
        """显示交易历史"""
        trades = self.get_trade_history(limit)
        
        print("\n" + "=" * 90)
        print("📜 Marcus 交易历史")
        print("=" * 90)
        
        if not trades:
            print("  暂无交易记录")
        else:
            print(f"{'时间':<20} {'类型':<8} {'代码':<12} {'价格':>10} {'数量':>8} {'理由':<25}")
            print("-" * 90)
            for t in reversed(trades):
                ts = t.get('timestamp', 'N/A')[:19].replace('T', ' ')
                trade_type = "🟢 买入" if t.get('type') == 'buy' else "🔴 卖出"
                reason = t.get('reason', 'N/A')[:22]
                print(f"{ts:<20} {trade_type:<8} {t.get('symbol', 'N/A'):<12} "
                      f"{t.get('price', 0):>10.2f} {t.get('volume', 0):>8} {reason:<25}")
        
        print("=" * 90)


def main():
    parser = argparse.ArgumentParser(
        description='Marcus × VN.PY 交易执行器',
        prog='marcus-trade'
    )
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # buy 命令
    buy_parser = subparsers.add_parser('buy', help='买入')
    buy_parser.add_argument('symbol', help='股票代码 (如 SH600519)')
    buy_parser.add_argument('price', type=float, help='价格')
    buy_parser.add_argument('volume', type=int, help='数量 (股)')
    buy_parser.add_argument('--reason', '-r', default='', help='交易理由')
    
    # sell 命令
    sell_parser = subparsers.add_parser('sell', help='卖出')
    sell_parser.add_argument('symbol', help='股票代码')
    sell_parser.add_argument('price', type=float, help='价格')
    sell_parser.add_argument('volume', type=int, help='数量')
    sell_parser.add_argument('--reason', '-r', default='', help='交易理由')
    
    # account 命令
    subparsers.add_parser('account', help='查询账户')
    
    # positions 命令
    subparsers.add_parser('positions', help='查询持仓')
    
    # history 命令
    history_parser = subparsers.add_parser('history', help='查询交易历史')
    history_parser.add_argument('--limit', '-l', type=int, default=20, help='显示数量')
    
    # profit 命令
    subparsers.add_parser('profit', help='查询盈亏汇总')
    
    args = parser.parse_args()
    
    executor = MarcusVNPyExecutor()
    
    if args.command == 'buy':
        result = executor.buy(args.symbol, args.price, args.volume, args.reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif args.command == 'sell':
        result = executor.sell(args.symbol, args.price, args.volume, args.reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif args.command == 'account':
        executor.show_account()
    
    elif args.command == 'positions':
        executor.show_positions()
    
    elif args.command == 'history':
        executor.show_history(args.limit)
    
    elif args.command == 'profit':
        executor.engine.show_profit_summary()
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
