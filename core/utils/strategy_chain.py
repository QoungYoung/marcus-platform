#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 策略链管理模块
功能：管理盘前→盘中→交易→反馈的完整策略链路

策略状态流转:
盘前扫描 (9:00) → 初步策略
    ↓
盘中扫描 (20/50 分) → 微调策略
    ↓
自动交易 (25/55 分) → 执行 + 反馈标记
    ↓
下一次盘中扫描 → 验证 + 迭代
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd


class StrategyChain:
    """策略链管理器"""
    
    def __init__(self, state_path: str = None):
        """
        初始化策略链
        
        Args:
            state_path: 策略状态文件路径
        """
        if state_path is None:
            # 自动检测 workspace，兼容 Windows / Linux
            try:
                from workspace_detector import WORKSPACE
                state_path = str(WORKSPACE / "data" / "strategy_state.json")
            except ImportError:
                from pathlib import Path
                state_path = str(Path(__file__).parent.parent.parent / "data" / "strategy_state.json")
        
        self.state_path = state_path
        self.state = self._load_state()
    
    def _load_state(self) -> dict:
        """加载策略状态"""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载策略状态失败：{e}")
        
        # 初始化新状态
        return {
            "current_date": datetime.now().strftime("%Y-%m-%d"),
            "pre_market": None,
            "intraday_scans": [],
            "trades": [],
            "feedback_loop": []
        }
    
    def _save_state(self):
        """保存策略状态"""
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
    
    def record_intraday_scan(self, scan_data: dict):
        """记录盘中扫描结果"""
        self.state['intraday_scans'].append({
            'timestamp': scan_data.get('timestamp'),
            'stance': scan_data.get('stance'),
            'stance_code': scan_data.get('stance_code', 'yellow'),
            'position_limit': scan_data.get('position_limit', 80),
            'sentiment_score': scan_data.get('sentiment_score'),
            'holdings_news': scan_data.get('holdings_news', {}),
            'validation': scan_data.get('validation', {}),
            'adjusted_strategy': scan_data.get('adjusted_strategy', {}),
            'trade_feedback': scan_data.get('trade_feedback', []),
            'watchlist': scan_data.get('watchlist', []),
            'sector_allocation': scan_data.get('sector_allocation', {}),
            'downgrade': scan_data.get('downgrade', {}),  # 持续降级检测结果
        })
        # 限制最多保留 10 条记录
        if len(self.state['intraday_scans']) > 10:
            self.state['intraday_scans'] = self.state['intraday_scans'][-10:]
        self._save_state()
        print(f"✅ 盘中扫描已记录：{scan_data.get('timestamp')}")
    
    def record_trade(self, trade_data: dict):
        """记录交易执行"""
        self.state['trades'].append(trade_data)
        if len(self.state['trades']) > 50:
            self.state['trades'] = self.state['trades'][-50:]
        self._save_state()
        print(f"✅ 交易已记录：{trade_data.get('action')} @ {trade_data.get('timestamp')}")
    
    def record_feedback(self, feedback_data: dict):
        """记录反馈闭环（每日重置后追加，过期反馈自动过滤）"""
        # 每日首次记录前先重置（清理跨日残留 feedback_loop）
        self.reset_daily()
        self.state['feedback_loop'].append(feedback_data)
        if len(self.state['feedback_loop']) > 20:
            self.state['feedback_loop'] = self.state['feedback_loop'][-20:]
        self._save_state()
        print(f"✅ 反馈已记录：{feedback_data.get('type')}")
    
    def get_latest_feedback(self, limit: int = 5) -> list:
        """获取最新反馈"""
        return self.state['feedback_loop'][-limit:]
    
    def get_latest_scan(self) -> dict:
        """获取最新盘中扫描"""
        scans = self.state.get('intraday_scans', [])
        return scans[-1] if scans else {}
    
    def reset_daily(self):
        """每日重置策略状态"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.state["current_date"] != today:
            # 归档旧状态
            self._archive_state()
            # 重置新状态
            self.state = {
                "current_date": today,
                "pre_market": None,
                "intraday_scans": [],
                "trades": [],
                "feedback_loop": []
            }
            self._save_state()
            print(f"✅ 策略状态已重置为 {today}")
    
    def _archive_state(self):
        """归档旧状态到历史文件"""
        try:
            from workspace_detector import get_data_dir
            archive_dir = str(get_data_dir() / "strategy_history") + "/"
        except ImportError:
            archive_dir = str(Path(__file__).resolve().parents[2] / "data" / "strategy_history") + "/"
        os.makedirs(archive_dir, exist_ok=True)
        
        archive_path = os.path.join(archive_dir, f"{self.state['current_date']}_strategy.json")
        try:
            with open(archive_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            print(f"📁 已归档策略状态至：{archive_path}")
        except Exception as e:
            print(f"归档失败：{e}")
    
    def set_pre_market_strategy(self, report: dict):
        """
        设置盘前策略
        
        Args:
            report: 盘前扫描报告 (包含美股联动分析)
        """
        self.reset_daily()
        
        self.state["pre_market"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "us_market": report.get("us_market", {}),
            "sentiment": report.get("sentiment", {}),
            "initial_strategy": report.get("initial_strategy", {})
        }
        
        self._save_state()
        print("✅ 盘前策略已设置")
    
    def add_intraday_scan(self, scan_result: dict):
        """
        添加盘中扫描结果
        
        Args:
            scan_result: 盘中扫描报告
        """
        scan_entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "market_stance": scan_result.get("market_stance", ""),
            "sentiment_score": scan_result.get("sentiment_score", 0),
            "validation": scan_result.get("validation", {}),
            "adjusted_strategy": scan_result.get("adjusted_strategy", {}),
            "trade_feedback": scan_result.get("trade_feedback", [])
        }
        
        self.state["intraday_scans"].append(scan_entry)
        self._save_state()
        print(f"✅ 盘中扫描已记录 (共 {len(self.state['intraday_scans'])} 次)")
    
    def add_trade(self, trade_result: dict):
        """
        添加交易记录
        
        Args:
            trade_result: 交易执行结果
        """
        trade_entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": trade_result.get("symbol", ""),
            "action": trade_result.get("action", ""),
            "price": trade_result.get("price", 0),
            "volume": trade_result.get("volume", 0),
            "strategy_ref": trade_result.get("strategy_ref", ""),
            "order_id": trade_result.get("order_id", ""),
            "feedback": None  # 后续由盘中扫描填充
        }
        
        self.state["trades"].append(trade_entry)
        self._save_state()
        print(f"✅ 交易记录已添加 (今日共 {len(self.state['trades'])} 笔)")
    
    def update_trade_feedback(self, trade_index: int, feedback: dict):
        """
        更新交易反馈
        
        Args:
            trade_index: 交易记录索引
            feedback: 反馈数据 {current_pnl, strategy_valid, next_action}
        """
        if 0 <= trade_index < len(self.state["trades"]):
            self.state["trades"][trade_index]["feedback"] = feedback
            self._save_state()
            print(f"✅ 交易反馈已更新 #{trade_index}")
    
    def add_feedback_entry(self, feedback: dict):
        """
        添加反馈循环记录
        
        Args:
            feedback: 反馈数据
        """
        feedback_entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scan_ref": len(self.state["intraday_scans"]),
            "analyzed_trades": feedback.get("analyzed_trades", []),
            "strategy_iteration": feedback.get("strategy_iteration", {}),
            "next_watchlist": feedback.get("next_watchlist", [])
        }
        
        self.state["feedback_loop"].append(feedback_entry)
        self._save_state()
    
    def set_pi_confirmation(self, stance: str, position_limit: int, reason: str = "", watchlist: list = None):
        """
        Pi 分析确认 — 将 AI 分析的结论写入策略链
        
        Args:
            stance: green / yellow / red
            position_limit: 建议仓位上限
            reason: 分析理由
            watchlist: 建议观察列表
        """
        self.state["pi_confirmation"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stance": stance,
            "position_limit": position_limit,
            "reason": reason,
            "watchlist": watchlist or [],
        }
        self._save_state()
        print(f"[策略链] Pi确认已写入: stance={stance}, limit={position_limit}%")

    def get_pi_confirmation(self) -> dict:
        """
        获取最近一次 Pi 确认的立场和仓位上限。

        Returns:
            dict: {"stance": "yellow", "position_limit": 60, "reason": "...", ...}
                  若从未写入则返回空 dict。
        """
        return self.state.get("pi_confirmation", {})

    def get_current_strategy(self) -> dict:
        """
        获取当前有效策略
        
        Returns:
            dict: 当前策略 (盘前或最新微调)
        """
        if not self.state["intraday_scans"]:
            # 无盘中扫描，返回盘前策略
            return self.state.get("pre_market", {}).get("initial_strategy", {})
        
        # 返回最新盘中扫描的微调策略
        latest_scan = self.state["intraday_scans"][-1]
        return latest_scan.get("adjusted_strategy", {})
    
    def get_trade_history(self, limit: int = 10) -> list:
        """
        获取交易历史
        
        Args:
            limit: 返回数量限制
        
        Returns:
            list: 交易记录列表
        """
        return self.state["trades"][-limit:]
    
    def analyze_strategy_effectiveness(self) -> dict:
        """
        分析策略有效性
        
        Returns:
            dict: 策略有效性分析
        """
        trades = self.state["trades"]
        if not trades:
            return {"status": "no_trades", "message": "今日暂无交易"}
        
        # 统计有反馈的交易
        feedback_trades = [t for t in trades if t.get("feedback")]
        if not feedback_trades:
            return {"status": "no_feedback", "message": "暂无交易反馈"}
        
        # 计算胜率
        winning_trades = [t for t in feedback_trades if t["feedback"].get("current_pnl", 0) > 0]
        win_rate = len(winning_trades) / len(feedback_trades) * 100
        
        # 平均盈亏
        avg_pnl = sum([t["feedback"].get("current_pnl", 0) for t in feedback_trades]) / len(feedback_trades)
        
        # 策略验证
        valid_strategies = [t for t in feedback_trades if t["feedback"].get("strategy_valid", False)]
        strategy_accuracy = len(valid_strategies) / len(feedback_trades) * 100
        
        return {
            "status": "analyzed",
            "total_trades": len(trades),
            "feedback_trades": len(feedback_trades),
            "win_rate": round(win_rate, 2),
            "avg_pnl": round(avg_pnl, 2),
            "strategy_accuracy": round(strategy_accuracy, 2),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def get_full_state(self) -> dict:
        """获取完整策略状态"""
        return self.state.copy()
    
    def analyze_and_iterate(self) -> dict:
        """
        【增强版】分析交易结果并迭代策略
        根据今日交易表现自动调整明日策略，并对比历史模式
        
        Returns:
            dict: 迭代后的策略调整建议，包含跨周模式对比
        """
        trades = self.state.get("trades", [])
        
        if not trades:
            return {"action": "maintain", "reason": "今日无交易"}
        
        # 统计交易结果
        buy_trades = [t for t in trades if t.get("action") == "buy"]
        sell_trades = [t for t in trades if t.get("action") == "sell"]
        
        # 统计止损/止盈触发
        stop_loss_count = 0
        take_profit_count = 0
        float_to_loss_count = 0  # 浮盈→亏损模式计数
        total_pnl = 0
        
        for t in sell_trades:
            pnl = t.get("pnl", 0)
            total_pnl += pnl
            # 新SOP止损线：-2%（对齐铁律二基础止损）
            if pnl < -0.02:
                stop_loss_count += 1
            elif pnl > 0.05:
                take_profit_count += 1
            
            # 检测「浮盈→亏损」模式
            feedback = t.get("feedback", {})
            if feedback:
                max_float = feedback.get("max_float_pnl", 0)
                if max_float > 0.01 and pnl < 0:
                    float_to_loss_count += 1
        
        # 根据交易结果生成策略调整
        iteration = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stats": {
                "total_trades": len(trades),
                "buy_count": len(buy_trades),
                "sell_count": len(sell_trades),
                "stop_loss_count": stop_loss_count,
                "take_profit_count": take_profit_count,
                "float_to_loss_count": float_to_loss_count,
                "total_pnl": round(total_pnl, 4)
            },
            "adjustments": []
        }
        
        # 策略迭代逻辑（阈值对齐新SOP）
        if stop_loss_count >= 2:
            iteration["action"] = "tighten_risk"
            iteration["adjustments"] = [
                "降低仓位至 30% 以下",
                "收紧止损线至 -1.5%（大盘弱势时）",
                "增加基本面过滤条件",
                "减少追高操作",
                "启用铁律二移动止盈保护（浮盈≥1%止损上移至成本价）"
            ]
            iteration["position_limit"] = 30
            iteration["stop_loss"] = -0.015
            print(f"⚠️ 策略调整: 止损触发 {stop_loss_count} 次，从严风控")
            
        elif float_to_loss_count >= 1:
            # 「浮盈→亏损」模式警惕
            iteration["action"] = "tighten_stop"
            iteration["adjustments"] = [
                f"检测到 {float_to_loss_count} 笔「浮盈→亏损」交易",
                "铁律二疑似未执行，强制启用移动止盈",
                "所有新开仓预设浮盈保护线（+1%止损上移至成本价）",
                "仓位上限降低 20%"
            ]
            iteration["position_limit"] = 40
            iteration["stop_loss"] = -0.02
            print(f"⚠️ 策略调整: 浮盈→亏损 {float_to_loss_count} 笔，铁律二强制执行")
            
        elif total_pnl > 0.05:
            iteration["action"] = "maintain"
            iteration["adjustments"] = [
                "维持当前策略",
                "继续关注热点概念",
                "可以适当激进"
            ]
            iteration["position_limit"] = 60
            print(f"✅ 策略维持: 盈利 {total_pnl:.2%}，保持积极")
            
        elif total_pnl < -0.03:
            iteration["action"] = "cautious"
            iteration["adjustments"] = [
                "降低仓位至 20%",
                "加强止损检查（大盘背景动态止损）",
                "减少新开仓位",
                "优先处理现有持仓",
                "检查是否存在跨周复现的错误模式"
            ]
            iteration["position_limit"] = 20
            iteration["stop_loss"] = -0.015
            print(f"⚠️ 策略调整: 亏损 {total_pnl:.2%}，保持谨慎")
            
        else:
            iteration["action"] = "maintain"
            iteration["adjustments"] = ["维持当前策略"]
            iteration["position_limit"] = 50
            print(f"➡️ 策略维持: 交易平淡，保持观察")
        
        # ── 跨周模式对比 ──
        cross_week = self._check_cross_week_pattern(float_to_loss_count, stop_loss_count)
        iteration["cross_week"] = cross_week
        
        # 保存迭代结果
        self.state["strategy_iteration"] = iteration
        # Step 6 核心：同时写入 daily_strategy，供盘中 crons 实时读取
        self.state["daily_strategy"] = {
            'timestamp': iteration['timestamp'],
            'action': iteration['action'],
            'position_limit': iteration.get('position_limit', 50),
            'stop_loss': iteration.get('stop_loss', -0.02),
            'adjustments': iteration.get('adjustments', []),
            'stats': iteration.get('stats', {}),
            'cross_week': cross_week,
            'reason': f"根据{'止损触发' if iteration['action']=='tighten_risk' else ('浮盈转亏损' if iteration['action']=='tighten_stop' else ('亏损' if iteration['action']=='cautious' else '盈利'))}自动调整"
        }
        self._save_state()

        return iteration
    
    def _check_cross_week_pattern(self, float_to_loss_count: int, stop_loss_count: int) -> dict:
        """
        检查当前错误模式是否在上周已出现（跨周模式识别）。
        
        Returns:
            dict: {"recurring": bool, "patterns": [str], "action": str}
        """
        result = {"recurring": False, "patterns": [], "action": "none"}
        
        try:
            # 加载上周归档的策略状态
            from pathlib import Path
            from workspace_detector import get_data_dir
            archive_dir = get_data_dir() / "strategy_history"
        except ImportError:
            from pathlib import Path
            archive_dir = Path(__file__).resolve().parents[2] / "data" / "strategy_history"
        
        if not archive_dir.exists():
            return result
        
        # 查找上周的归档文件（最近 7-14 天）
        one_week_ago = datetime.now() - timedelta(days=7)
        two_weeks_ago = datetime.now() - timedelta(days=14)
        
        last_week_files = []
        for f in archive_dir.glob("*_strategy.json"):
            try:
                date_str = f.stem.replace("_strategy", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if two_weeks_ago <= file_date <= one_week_ago:
                    last_week_files.append(f)
            except ValueError:
                continue
        
        # 分析上周的错误模式
        last_week_patterns = set()
        for f in sorted(last_week_files):
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    data = json.load(fp)
                iteration = data.get("strategy_iteration", {})
                stats = iteration.get("stats", {})
                # 如果上周也有止损触发，记录
                if stats.get("stop_loss_count", 0) >= 2:
                    last_week_patterns.add("止损频发")
                if stats.get("float_to_loss_count", 0) >= 1:
                    last_week_patterns.add("浮盈→亏损")
                if iteration.get("action") in ("tighten_risk", "cautious"):
                    last_week_patterns.add("风控收紧")
            except Exception:
                continue
        
        # 对比本周模式
        current_patterns = set()
        if stop_loss_count >= 2:
            current_patterns.add("止损频发")
        if float_to_loss_count >= 1:
            current_patterns.add("浮盈→亏损")
        
        recurring = current_patterns & last_week_patterns
        
        if recurring:
            result["recurring"] = True
            result["patterns"] = list(recurring)
            result["action"] = "escalate"
            print(f"🔴 跨周模式复现: {', '.join(recurring)} — 需升级处理优先级")
        
        return result
    
    def print_summary(self):
        """打印策略摘要"""
        print("\n" + "="*60)
        print("📋 Marcus 策略链状态")
        print("="*60)
        print(f"日期：{self.state['current_date']}")
        
        if self.state["pre_market"]:
            pm = self.state["pre_market"]
            print(f"\n🌙 盘前策略:")
            print(f"  立场：{pm['initial_strategy'].get('stance', 'N/A')}")
            print(f"  仓位上限：{pm['initial_strategy'].get('position_limit', 0)}%")
            sentiment = pm.get("sentiment", {})
            print(f"  情绪分数：{sentiment.get('score', 0)}/100 ({sentiment.get('level', 'N/A')})")
        
        print(f"\n📊 盘中扫描：{len(self.state['intraday_scans'])} 次")
        print(f"⚡ 交易执行：{len(self.state['trades'])} 笔")
        print(f"🔁 反馈循环：{len(self.state['feedback_loop'])} 次")
        
        # 策略有效性
        effectiveness = self.analyze_strategy_effectiveness()
        if effectiveness["status"] == "analyzed":
            print(f"\n📈 策略有效性:")
            print(f"  胜率：{effectiveness['win_rate']}%")
            print(f"  平均盈亏：{effectiveness['avg_pnl']}%")
            print(f"  策略准确率：{effectiveness['strategy_accuracy']}%")
        
        print("="*60)


if __name__ == "__main__":
    # 测试策略链
    chain = StrategyChain()
    chain.print_summary()
