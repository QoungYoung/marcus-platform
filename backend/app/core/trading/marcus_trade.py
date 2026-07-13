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
        self._consecutive_losses: int = 0           # 连续亏损计数器
        
        # ── 极端流出日防御 ──
        self._extreme_outflow_scans: int = 0        # 连续极端流出扫描计数
        self._extreme_outflow_triggered: bool = False  # 当日是否已触发减仓

        # ── 卖出趋势约束缓存 ──
        self._trend_constraint_cache: dict = {}  # symbol -> (timestamp, ma5, ma20)
        self._tech_divergence_cache: dict = {}   # (symbol, date) -> ((signals, details), timestamp)
    
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
        """查询 trades.db 获取今日买入的股票代码集合（兼容旧接口，内部使用）"""
        return set(self._get_today_buy_volumes().keys())

    def _get_today_buy_volumes(self) -> dict:
        """查询 trades.db 获取今日买入的股票代码→股数映射（用于 T+1 拦截）
        Returns: {symbol_str: total_volume_int}
        """
        today_volumes = {}
        try:
            import sqlite3
            db_path = Path(self.data_dir) / "trades.db"
            if not db_path.exists():
                return today_volumes
            conn = sqlite3.connect(str(db_path), timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            cursor = conn.cursor()
            today_str = datetime.now().strftime('%Y-%m-%d')
            cursor.execute(
                "SELECT symbol, SUM(volume) FROM trades WHERE direction='买入' AND date(created_at)=? AND (voided = 0 OR voided IS NULL) GROUP BY symbol",
                (today_str,)
            )
            for row in cursor.fetchall():
                today_volumes[row[0]] = int(row[1] or 0)
            conn.close()
        except Exception as e:
            print(f"[T+1] 查询今日买入记录失败（非致命）：{e}", file=sys.stderr)
        return today_volumes
    
    def _get_pi_recommended_limit(self) -> int:
        """从策略链获取 Pi 建议的仓位上限百分比"""
        try:
            from core.utils.strategy_chain import StrategyChain
            chain = StrategyChain()
            pi_conf = chain.get_pi_confirmation()
            return int(pi_conf.get('position_limit', 60))
        except Exception:
            return 60
    
    # ── 极端流出日防御（遗漏#1：针对 6/10 东睦僵尸持仓的直接解决方案）──
    
    def record_market_outflow_scan(self, main_net_outflow_billion: float) -> dict:
        """
        记录本轮市场主力净流出扫描，追踪连续极端流出。
        触发条件：全市场主力净流出 > 800亿 + 连续3轮扫描确认空头。
        
        Returns:
            {"triggered": bool, "scans": int, "action": str}
        """
        result = {"triggered": False, "scans": self._extreme_outflow_scans, "action": "none"}
        
        if main_net_outflow_billion > 800:
            self._extreme_outflow_scans += 1
            result["scans"] = self._extreme_outflow_scans
            # v1.5: 渐进式防御
            if self._extreme_outflow_scans == 1:
                result["action"] = "1轮→仓位保留70%"
            elif self._extreme_outflow_scans == 2:
                result["action"] = "2轮→仓位保留40%"
            elif self._extreme_outflow_scans >= 3 and not self._extreme_outflow_triggered:
                self._extreme_outflow_triggered = True
                result["triggered"] = True
                result["action"] = "3轮→仓位保留20%"
                print(f"[极端流出] ⚠️ 连续 {self._extreme_outflow_scans} 轮确认全市场主力净流出 > 800亿，渐进防御已触发3轮！", file=sys.stderr)
        else:
            # 中断连续性（非极端流出轮次重置计数器）
            if self._extreme_outflow_scans > 0:
                print(f"[极端流出] 连续性中断（本轮流出 {main_net_outflow_billion:.0f}亿），重置计数器", file=sys.stderr)
            self._extreme_outflow_scans = 0
        
        return result
    
    def execute_extreme_outflow_defense(self) -> list:
        """
        v1.5：极端流出日渐进式强制减仓。
        - 第1轮扫描：保留70%（减仓30%）
        - 第2轮扫描：保留40%（减仓60%）
        - 第3轮扫描：保留20%（减仓80%）
        在尾盘 14:30 前由调度器调用。
        
        Returns:
            [{"symbol": str, "sold_volume": int, "reason": str}, ...]
        """
        results = []
        # 根据扫描轮次决定保留比例
        scans = self._extreme_outflow_scans
        if scans == 1:
            keep_ratio = 0.70
        elif scans == 2:
            keep_ratio = 0.40
        else:
            keep_ratio = 0.20
        
        try:
            today_volumes = self._get_today_buy_volumes()
            positions = self.get_positions()

            for pos in positions:
                symbol = pos.get('symbol', '')
                total_vol = pos.get('volume', 0)
                if not symbol or total_vol <= 0:
                    continue
                today_buy_vol = today_volumes.get(symbol, 0)
                available = total_vol - today_buy_vol
                if available <= 0:
                    print(f"[极端流出防御] T+1 锁定全部{total_vol}股，跳过 {symbol}", file=sys.stderr)
                    continue

                sell_ratio = 1.0 - keep_ratio
                sell_vol = max(100, int(available * sell_ratio / 100) * 100)
                sell_vol = min(sell_vol, available)
                if sell_vol < 100:
                    continue
                
                avg_price = pos.get('avg_price', 0)
                sell_result = self.sell(symbol, avg_price, sell_vol, reason="极端流出日强制减仓50%")
                
                results.append({
                    'symbol': symbol,
                    'sold_volume': sell_vol,
                    'total_volume': total_vol,
                    'status': sell_result.get('status', 'unknown'),
                    'reason': '极端流出日强制减仓50%'
                })
                print(f"[极端流出防御] {symbol} 减仓 {sell_vol}/{total_vol} 股", file=sys.stderr)
        except Exception as e:
            print(f"[极端流出防御] 执行异常: {e}", file=sys.stderr)
        
        return results
    
    def has_extreme_outflow_triggered(self) -> bool:
        """查询当日是否已触发极端流出防御"""
        return self._extreme_outflow_triggered
    
    def reset_extreme_outflow(self) -> None:
        """新交易日重置极端流出状态"""
        self._extreme_outflow_scans = 0
        self._extreme_outflow_triggered = False
    
    def _get_market_outflow_billion(self) -> float:
        """查询全市场主力净流出（亿元），用于极端流出日检测。
        所有东财 API 失败时回退到今日缓存，并标注时点。"""
        import urllib.request, json, ssl, urllib.error
        
        def _parse_and_save(raw_net: float) -> float:
            """解析单位并存入缓存"""
            # 统一转为亿元
            if abs(raw_net) > 10000:
                raw_net /= 100000000  # 元 → 亿元
            elif abs(raw_net) > 100 and abs(raw_net) < 10000:
                raw_net /= 10000  # 万元 → 亿元
            outflow = abs(raw_net) if raw_net < 0 else 0
            try:
                from core.utils.eastmoney_cache import get_em_cache
                get_em_cache().save("market_outflow", outflow)
            except Exception:
                pass
            return outflow
        
        ctx = ssl.create_default_context()
        
        # 尝试 1: Backend API（通过 Pi Server 代理）
        try:
            pi_url = "http://localhost:3001/api/v1/market/moneyflow-mkt"
            req = urllib.request.Request(pi_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
            if resp_data.get("data"):
                main_net = resp_data["data"].get("main_net", 0)
                if isinstance(main_net, (int, float)) and main_net != 0:
                    return _parse_and_save(main_net)
        except (urllib.error.HTTPError, urllib.error.URLError, ConnectionRefusedError, 
                TimeoutError, OSError, json.JSONDecodeError, Exception):
            pass
        
        # 尝试 2: akshare 直接调用
        try:
            import akshare as ak
            df = ak.stock_market_fund_flow()
            if df is not None and len(df) > 0:
                total_row = df[df['名称'] == '沪深两市']
                if len(total_row) > 0:
                    return _parse_and_save(total_row.iloc[0].get('主力净流入-净额', 0))
                sh = df[df['名称'] == '上证']
                sz = df[df['名称'] == '深证']
                total_net = 0
                if len(sh) > 0: total_net += sh.iloc[0].get('主力净流入-净额', 0)
                if len(sz) > 0: total_net += sz.iloc[0].get('主力净流入-净额', 0)
                if total_net != 0:
                    return _parse_and_save(total_net)
        except (ConnectionRefusedError, TimeoutError, OSError) as e:
            pass  # 连接拒绝 → 回退缓存
        except Exception:
            pass  # 数据解析失败 → 回退缓存
        
        # 尝试 3: 全部失败 → 回退今日缓存
        try:
            from core.utils.eastmoney_cache import get_em_cache
            cache = get_em_cache()
            cached_val, meta = cache.load_with_fallback("market_outflow")
            if meta["from_cache"]:
                print(
                    f"[极端流出] ⚠️ 东财 API 不可用，使用今日缓存数据 "
                    f"(流出={cached_val:.0f}亿, 时点={meta['cached_at']})",
                    file=sys.stderr
                )
                return cached_val
        except Exception:
            pass
        
        return 0
    
    # ── 过滤器拒绝率追踪（遗漏#2）──
    
    _filter_log_path = DATA_DIR / "filter_rejections.jsonl"
    
    @classmethod
    def log_filter_rejection(cls, scan_round: str, filter_name: str, 
                              total_input: int, passed: int, 
                              details: dict = None) -> None:
        """
        记录每轮扫描中每个过滤器的拒绝数/通过数。
        用于周复盘时分析哪个过滤器最严、是否需要放宽。
        
        Args:
            scan_round: 扫描轮次标识（如 'morning_10:50'）
            filter_name: 过滤器名称（如 '资金TOP5∩涨幅TOP5', '换手率2-15%'）
            total_input: 输入候选数
            passed: 通过数
            details: 额外信息（可选）
        """
        try:
            import json
            from datetime import datetime
            record = {
                'timestamp': datetime.now().isoformat(),
                'scan_round': scan_round,
                'filter': filter_name,
                'total_input': total_input,
                'passed': passed,
                'rejected': total_input - passed,
                'pass_rate': round(passed / total_input * 100, 1) if total_input > 0 else 0,
                'details': details or {}
            }
            with open(cls._filter_log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"[FilterLog] 记录失败: {e}", file=sys.stderr)
    
    @classmethod
    def get_filter_rejection_stats(cls, date_str: str = None) -> dict:
        """
        读取过滤器拒绝率统计，按过滤器名称汇总。
        
        Args:
            date_str: 日期过滤（如 '2026-06-13'），为空则全部统计
        Returns:
            {filter_name: {"total_input": N, "passed": N, "avg_pass_rate": N, "rounds": N}}
        """
        import json
        from collections import defaultdict
        stats = defaultdict(lambda: {"total_input": 0, "passed": 0, "total_rejected": 0, "rounds": 0})
        
        if not cls._filter_log_path.exists():
            return dict(stats)
        
        with open(cls._filter_log_path, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    if date_str and not rec.get('timestamp', '').startswith(date_str):
                        continue
                    fn = rec.get('filter', 'unknown')
                    stats[fn]["total_input"] += rec.get("total_input", 0)
                    stats[fn]["passed"] += rec.get("passed", 0)
                    stats[fn]["total_rejected"] += rec.get("rejected", 0)
                    stats[fn]["rounds"] += 1
                except json.JSONDecodeError:
                    continue
        
        result = {}
        for fn, s in stats.items():
            total = s["total_input"]
            result[fn] = {
                "total_input": total,
                "passed": s["passed"],
                "total_rejected": s["total_rejected"],
                "avg_pass_rate": round(s["passed"] / total * 100, 1) if total > 0 else 0,
                "rounds": s["rounds"]
            }
        return result
    
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
        
        # 用雪球实时价格计算持仓市值和浮动盈亏（腾讯 qt.gtimg.cn）
        from xueqiu_engine import XueqiuEngine
        xq_config = str(XUEQIU_DIR / "config.json")
        xueqiu = XueqiuEngine(config_file=xq_config)
        position_value = 0
        float_pnl = 0
        
        try:
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
        
        float_pnl = position_value - total_cost
        
        # 已实现盈亏 = 从 trades 表查询
        import sqlite3
        data_dir = Path(self.data_dir) if isinstance(self.data_dir, str) else self.data_dir
        conn = sqlite3.connect(str(data_dir / "trades.db"), timeout=30)
        cursor = conn.cursor()
        conn.execute("PRAGMA busy_timeout=30000")
        cursor.execute('SELECT SUM(profit) FROM trades WHERE direction = "卖出" AND (voided = 0 OR voided IS NULL)')
        realized_pnl = cursor.fetchone()[0] or 0
        conn.close()
        
        # 总盈亏 = 总资产 - 初始资金（保证与 total_asset 始终一致）
        frozen_cash = parse_float_chinese(raw.get('冻结资金', 0))
        # 🔧 修复：总资产应包含冻结资金（委托未成交时资金已冻结但尚未转为持仓）
        total_asset = available_cash + frozen_cash + position_value
        total_pnl = total_asset - initial_capital
        # 🔧 derived_float_pnl = total_pnl - realized_pnl（保证三数自洽）
        derived_float_pnl = total_pnl - realized_pnl
        
        return {
            'initial_capital': initial_capital,
            'available_cash': available_cash,
            'frozen_cash': frozen_cash,
            'position_value': position_value,
            'total_asset': total_asset,
            'total_profit': f"{total_pnl:+,.2f} ({total_pnl/initial_capital*100:+.2f}%)",
            'position_count': len(positions),
            'float_pnl': derived_float_pnl,
            'realized_pnl': realized_pnl
        }
    
    def check_risk(self, symbol: str, price: float, volume: int, side: str,
                   skip_trend_constraint: bool = False) -> dict:
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
        
        # ── 规则 0.6: T+1 拦截（卖出方向，按股数而非按标的） ──
        if side == 'sell':
            positions = self.engine.get_positions()
            pos = next((p for p in positions if p.get('symbol') == symbol), None)
            if pos:
                today_volumes = self._get_today_buy_volumes()
                today_buy_vol = today_volumes.get(symbol, 0)
                available = pos.get('volume', 0) - today_buy_vol
                if available <= 0:
                    risk_data['reason'] = f'T+1 拦截：{symbol} 今日买入{today_buy_vol}股，无可卖股数'
                    risk_data['t1_blocked'] = True
                    self._log_risk(risk_data)
                    return {'allowed': False, 'reason': f'T+1 拦截（{symbol}今日买入{today_buy_vol}股，无可卖）', 'data': risk_data}
                if volume > available:
                    risk_data['reason'] = f'T+1 部分锁定：{symbol} 需卖{volume}股，仅{available}股可卖（{today_buy_vol}股锁仓）'
                    risk_data['t1_blocked'] = True
                    self._log_risk(risk_data)
                    return {'allowed': False, 'reason': f'T+1 部分锁定（{symbol}仅{available}股可卖）', 'data': risk_data}
        
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

            # ── 规则 3.5: 卖出趋势约束（MA5>MA20 时阻止手动卖出，止损卖出豁免） ──
            if not skip_trend_constraint:
                trend_block = self._check_sell_trend_constraint(
                    symbol, avg_cost=pos.get('avg_cost', 0) or pos.get('avg_price', 0), cur_price=price)
            else:
                trend_block = ""
            if trend_block:
                risk_data['reason'] = trend_block
                risk_data['trend_blocked'] = True
                self._log_risk(risk_data)
                return {'allowed': False, 'reason': trend_block, 'data': risk_data}

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

    def _check_sell_trend_constraint(self, symbol: str, avg_cost: float, cur_price: float) -> str:
        """
        卖出趋势约束：当 MA5 > MA20（趋势完好）时检查技术背离信号。

        - 技术背离 ≥3 个信号 → 趋势可能衰竭，放行卖出
        - 技术背离 <3 个信号 → 趋势完好，阻止手动/AI卖出

        防止 AI 主观判断导致过早卖出趋势完好的持仓。

        Returns:
            空字符串 = 通过（允许卖出），非空 = 阻止原因
        """
        if avg_cost <= 0 or cur_price <= 0:
            return ""
        float_pnl_pct = (cur_price / avg_cost - 1) * 100
        # 盈利或严重亏损时不拦截（盈利可自由止盈，严重亏损交给止损规则）
        if float_pnl_pct >= 0:
            return ""
        if float_pnl_pct <= -4.0:
            return ""

        import time as _time
        # 5分钟缓存，避免频繁 Tushare 调用
        cache_key = f"trend_{symbol}"
        cached = self._trend_constraint_cache.get(cache_key)
        if cached:
            ts, ma5, ma20 = cached
            if _time.time() - ts < 300:
                if ma5 > ma20 > 0:
                    return self._evaluate_trend_divergence(
                        symbol, cur_price, float_pnl_pct, ma5, ma20
                    )
                return ""

        try:
            from app.api.indicator import _normalize_to_ts_code
            from app.config import get_settings
            import tushare as ts
            from datetime import datetime as _dt, timedelta as _td

            settings = get_settings()
            token = settings.get_tushare_token()
            if not token:
                return ""
            pro = ts.pro_api(token)
            ts_code = _normalize_to_ts_code(symbol)
            end_d = _dt.now().strftime("%Y%m%d")
            start_d = (_dt.now() - _td(days=60)).strftime("%Y%m%d")
            df = pro.daily(ts_code=ts_code, start_date=start_d, end_date=end_d, limit=30)

            if df is None or df.empty or len(df) < 20:
                self._trend_constraint_cache[cache_key] = (_time.time(), 0, 0)
                return ""

            df = df.sort_values("trade_date", ascending=True)
            closes = df['close'].values
            ma5 = float(sum(closes[-5:]) / 5)
            ma20 = float(sum(closes[-20:]) / 20)
            self._trend_constraint_cache[cache_key] = (_time.time(), ma5, ma20)

            if ma5 > ma20 > 0:
                return self._evaluate_trend_divergence(
                    symbol, cur_price, float_pnl_pct, ma5, ma20
                )
        except Exception:
            pass
        return ""

    def _evaluate_trend_divergence(
        self, symbol: str, cur_price: float, float_pnl_pct: float,
        ma5: float, ma20: float,
    ) -> str:
        """Check tech divergence signals when MA5 > MA20. Allow sale if ≥3 signals."""
        try:
            from app.core.trading._tech_divergence import check_tech_divergence_signals

            signals, _details = check_tech_divergence_signals(
                symbol=symbol,
                current_price=cur_price,
                float_pnl_pct=float_pnl_pct,
                cache=getattr(self, '_tech_divergence_cache', None),
                cache_key=f"trend_div_{symbol}",
            )

            if sum(signals) >= 3:
                # Technicals confirm trend exhaustion, allow the sale
                import logging
                logging.getLogger(__name__).info(
                    f"[TrendConstraint] {symbol} 技术背离≥3({sum(signals)}/5)，"
                    f"趋势可能衰竭，放行卖出"
                )
                return ""

        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(
                f"[TrendConstraint] 技术背离检查失败 {symbol}: {e}"
            )

        return (
            f"趋势约束阻止卖出: MA5({ma5:.2f}) > MA20({ma20:.2f})，"
            f"趋势完好，浮亏{float_pnl_pct:.2f}%未触发硬止损，禁止手动卖出"
        )

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
        order_id = self.engine.buy(symbol, price, volume, reason)
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

        # 推送 QQ 通知
        self._notify_buy(symbol, price, volume, reason, total_cost)

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
    
    def sell(self, symbol: str, price: float, volume: int, reason: str = "",
             skip_trend_constraint: bool = False) -> dict:
        """卖出操作"""
        # 归一化 symbol（兼容 301566.SZ / SZ301566 / 301566 多种输入格式）
        symbol = self.engine._normalize_symbol(symbol)
        # 风控检查
        risk_result = self.check_risk(symbol, price, volume, 'sell',
                                      skip_trend_constraint=skip_trend_constraint)
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
        order_id = self.engine.sell(symbol, price, volume, reason)
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
        
        # 推送 QQ 通知
        self._notify_sell(symbol, price, volume, reason, profit, avg_cost)
        
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

    def _notify_buy(self, symbol: str, price: float, volume: int, reason: str, cost: float = 0) -> None:
        """买入成交后推送 QQ 通知。"""
        try:
            from app.services.qqbot_service import send_qq_notification

            # 获取股票名称
            stock_name = symbol
            try:
                from app.api.portfolio import get_stock_name
                stock_name = get_stock_name(symbol)
            except Exception:
                pass

            clean_reason = reason.strip() if reason else '自动交易'
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 区分代码层自动加仓 vs 手动买入
            is_tier_add = reason.startswith('[TierMonitor自动加仓]')
            if is_tier_add:
                tag = '🟢 **自动加仓**'
            else:
                tag = '🟢 **买入成交**'

            message = (
                f"{tag}\n\n"
                f"标的: {stock_name} ({symbol})\n"
                f"价格: {price:.2f}  |  数量: {volume}股\n"
                f"金额: {cost:.2f}"
            )
            if cost > 0:
                message += f" (含佣金约 {cost - price * volume:.2f})"
            message += f"\n\n> {clean_reason}\n\n"
            message += f"时间: {now_str}"

            send_qq_notification(message)
            import logging
            logging.getLogger(__name__).info(f"[MarcusTrade] 📨 QQ买入通知已发送: {symbol}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[MarcusTrade] QQ买入通知发送失败: {e}")

    def _notify_sell(self, symbol: str, price: float, volume: int, reason: str, profit: float, 
                     avg_cost: float = 0) -> None:
        """卖出成交后推送 QQ 通知。覆盖止损卖出和止盈卖出。"""
        try:
            from app.services.qqbot_service import send_qq_notification

            # 提取规则类型
            is_stop_loss = reason.startswith('[StopLoss自动]')
            is_take_profit = not is_stop_loss and ('止盈' in reason or '盈利' in reason or 
                          '≥10%' in reason or '≥15%' in reason or '≥20%' in reason)

            if is_stop_loss:
                clean_reason = reason.replace('[StopLoss自动] ', '').strip()
                tag = '🔴 **止损卖出**'
            elif is_take_profit:
                clean_reason = reason.strip()
                tag = '🟢 **止盈卖出**'
            else:
                clean_reason = reason.strip() if reason else '手动卖出'
                tag = '📤 **卖出成交**'

            # 获取股票名称
            stock_name = symbol
            try:
                from app.api.portfolio import get_stock_name
                stock_name = get_stock_name(symbol)
            except Exception:
                pass

            # 计算盈亏百分比
            pnl_pct = round((price - avg_cost) / avg_cost * 100, 2) if avg_cost > 0 else 0
            sign = '+' if profit >= 0 else ''
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            message = (
                f"{tag}\n\n"
                f"标的: {stock_name} ({symbol})\n"
                f"价格: {price:.2f}  |  数量: {volume}股\n"
                f"盈亏: {sign}{profit:.2f}"
            )
            if avg_cost > 0:
                message += f" ({sign}{pnl_pct}%)"
            message += f"\n\n> {clean_reason}\n\n"
            message += f"时间: {now_str}"

            send_qq_notification(message)
            import logging
            logging.getLogger(__name__).info(f"[MarcusTrade] 📨 QQ通知已发送: {symbol}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[MarcusTrade] QQ通知发送失败: {e}")
    
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
        
        # 按 FIFO 计算真实持仓（与 portfolio.calculate_positions_from_db 保持一致）
        cursor.execute(
            'SELECT symbol, direction, price, volume FROM trades '
            'WHERE voided = 0 OR voided IS NULL '
            'ORDER BY COALESCE(trade_date, DATE(created_at)), id'
        )
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
                    'avg_price': avg_price,
                    'current_price': avg_price,   # 默认用成本价，下面实时覆盖
                })

        # 补全实时 current_price（止损监控需要）— 走腾讯 qt.gtimg.cn 实时行情
        if result:
            try:
                from xueqiu_engine import XueqiuEngine
                # config.json 仅用于 token（腾讯接口无需认证），不存在也能正常工作
                xq_config = str(XUEQIU_DIR / "config.json")
                xq = XueqiuEngine(config_file=xq_config)
                print(f"[雪球] 获取 {len(result)} 只持仓实时价格 (腾讯 qt.gtimg.cn)...", file=sys.stderr)
                fetched = 0
                for pos in result:
                    try:
                        quote = xq.get_stock_quote(pos['symbol'], use_cache=False)
                        if quote:
                            pos['current_price'] = quote.get('current', pos['avg_price'])
                            pos['name'] = quote.get('name', '')
                            fetched += 1
                            print(f"[雪球]   {pos['symbol']} → ¥{pos['current_price']} (name={pos.get('name', '?')})", file=sys.stderr)
                    except Exception:
                        print(f"[雪球]   {pos['symbol']} ⚠️ 获取失败，使用成本价 ¥{pos['avg_price']}", file=sys.stderr)
                print(f"[雪球] 完成: {fetched}/{len(result)} 只获取成功", file=sys.stderr)
            except Exception as e:
                print(f"[雪球] ⚠️ 引擎加载失败: {e}", file=sys.stderr)

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
        """重置连续亏损计数器 + 极端流出状态（新交易日调用）"""
        self._consecutive_losses = 0
        self.reset_extreme_outflow()
    
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
    
    def void_trade(self, trade_id: int, reason: str) -> dict:
        """撤回一笔成交（软删除，不计入持仓）"""
        import sqlite3
        from datetime import datetime as _dt

        db_path = str(Path(self.data_dir) / "trades.db")
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, symbol, direction, price, volume, voided FROM trades WHERE id = ?", (trade_id,))
            row = cur.fetchone()
            if not row:
                return {"success": False, "error": f"交易 {trade_id} 不存在"}
            if row[5]:
                return {"success": False, "error": f"交易 {trade_id} 已被撤回"}

            cur.execute(
                "UPDATE trades SET voided = 1, void_reason = ?, voided_at = ? WHERE id = ?",
                (reason, _dt.now().strftime("%Y-%m-%d %H:%M:%S"), trade_id)
            )
            conn.commit()
            print(
                f"[交易撤回] ✅ #{trade_id} {row[1]} {row[2]} {row[4]}股 @ {row[3]} | 原因: {reason}",
                file=sys.stderr
            )
            return {"success": True, "trade_id": trade_id, "symbol": row[1],
                    "direction": row[2], "volume": row[4], "reason": reason}
        finally:
            conn.close()

    def unvoid_trade(self, trade_id: int) -> dict:
        """恢复一笔已撤回的成交"""
        import sqlite3

        db_path = str(Path(self.data_dir) / "trades.db")
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, symbol, direction, price, volume, voided FROM trades WHERE id = ?", (trade_id,))
            row = cur.fetchone()
            if not row:
                return {"success": False, "error": f"交易 {trade_id} 不存在"}
            if not row[5]:
                return {"success": False, "error": f"交易 {trade_id} 未被撤回"}

            cur.execute(
                "UPDATE trades SET voided = 0, void_reason = NULL, voided_at = NULL WHERE id = ?",
                (trade_id,)
            )
            conn.commit()
            print(
                f"[交易恢复] ✅ #{trade_id} {row[1]} {row[2]} {row[4]}股 @ {row[3]} 已恢复",
                file=sys.stderr
            )
            return {"success": True, "trade_id": trade_id, "symbol": row[1],
                    "direction": row[2], "volume": row[4]}
        finally:
            conn.close()

    def get_voided_trades(self) -> list:
        """获取所有已撤回的交易"""
        import sqlite3

        db_path = str(Path(self.data_dir) / "trades.db")
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, symbol, direction, price, volume, amount, profit, "
                "created_at, trade_date, void_reason, voided_at "
                "FROM trades WHERE voided = 1 ORDER BY voided_at DESC"
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

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
