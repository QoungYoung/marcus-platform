#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓层级监控器 — 代码层自动加仓引擎。

职责：
  1. 评估每只持仓的当前层级（probe/confirm/sprint）
  2. 浮盈达标时自动触发层级升级评估
  3. 门控仲裁（Pi立场/回撤/连亏/保护线/趋势确认/概念TOP10）
  4. 全部门控通过后自动执行加仓下单
  5. 写入通知供 AI 在交易报告中引用

用法：
    monitor = get_position_tier_monitor(executor=executor)
    monitor.start()  # 与 StopLossMonitor 并行运行

架构：
    ┌─ 第1层: 层级评估 (evaluate_position_tier) ─┐
    └─ 第2层: 门控仲裁 (can_execute_add) ────────┘
    └─ 第3层: 自动执行 (execute_add_position) ───┘
    └─ 通知写入 ──────────────────────────────────┘
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

# ── 层级状态持久化路径 ──
TIER_STATE_FILE = Path(__file__).parent.parent.parent.parent / "data" / "position_tiers.json"


class TierEvaluation:
    """层级评估结果"""
    __slots__ = ('action', 'current_tier', 'target_tier', 'max_position_pct', 'signal')
    
    def __init__(self, action: str, signal: str, current_tier: str = 'probe',
                 target_tier: str = '', max_position_pct: float = 0.0):
        self.action = action
        self.current_tier = current_tier
        self.target_tier = target_tier
        self.max_position_pct = max_position_pct
        self.signal = signal


class GateResult:
    """门控裁决结果"""
    __slots__ = ('allowed', 'checks', 'trigger_action', 'reason')
    
    def __init__(self, allowed: bool, checks: List[tuple], 
                 trigger_action: str = '', reason: str = ''):
        self.allowed = allowed
        self.checks = checks
        self.trigger_action = trigger_action
        self.reason = reason


class PositionTierMonitor:
    """
    持仓层级监控器 — 代码层自动加仓引擎。

    与 StopLossMonitor 并行运行，各司其职：
      - StopLossMonitor: 止损/保护线 → 卖出
      - PositionTierMonitor: 浮盈达标 → 加仓买入
    """

    # A股交易时段
    TRADING_START = dtime(9, 30)
    TRADING_END = dtime(15, 0)
    LUNCH_START = dtime(11, 30)
    LUNCH_END = dtime(13, 0)
    # 早盘冷静期：09:30-09:45 不加仓（波动剧烈）
    MORNING_QUIET_END = dtime(9, 45)
    # 尾盘禁止加仓：14:30 后
    CLOSING_CUTOFF = dtime(14, 30)

    # ── 加仓层级阈值 ──
    TIER_THRESHOLDS = {
        'probe_to_confirm': 0.01,    # 试探仓 → 确认仓：浮盈 ≥ 1%
        'confirm_to_sprint': 0.03,   # 确认仓 → 冲刺仓：浮盈 ≥ 3%
        'probe_to_sprint': 0.03,     # 试探仓 → 冲刺仓（跳级）：浮盈 ≥ 3%
    }

    # ── 层级仓位上限 ──
    TIER_CAPS = {
        'probe': 0.10,
        'confirm': 0.18,
        'sprint': 0.25,
    }

    # ── 补仓阈值：实际仓位 < 目标上限 × 此比例时触发 REFILL ──
    REFILL_THRESHOLD = 0.8

    def __init__(self, executor=None, interval_seconds: int = 33):
        self.executor = executor
        self.interval = interval_seconds
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        # 内存中的层级状态 {symbol: tier_info}
        self.tier_states: Dict[str, dict] = {}
        # 通知队列 [[type, message, timestamp], ...] 供 AI 读取
        self.notifications: List[dict] = []
        # 当日已加仓计数 {symbol: count}
        self.today_adds: Dict[str, int] = {}
        # 最近一次评价时间 {symbol: timestamp}（防重复触发）
        self._last_eval: Dict[str, float] = {}
        # 单日单票最大加仓次数
        self.MAX_ADDS_PER_DAY = 3
        # 趋势强度缓存 {cache_key: (timestamp, ...)}（板块资金5分钟缓存）
        self._trend_cache: Dict[str, tuple] = {}
        # 概念TOP10拦截每日推送去重 {symbol: date_str}
        self._concept_top10_notified: Dict[str, str] = {}
        # 股票概念缓存 {symbol: (date_str, set)}
        self._stock_concepts_cache: Dict[str, tuple] = {}
        # 实时MA缓存 {cache_key: (timestamp, result)}
        self._realtime_ma_cache: Dict[str, tuple] = {}
        # AI判断缓存 {cache_key: (timestamp, result)}
        self._ai_judge_cache: Dict[str, tuple] = {}

        # 加载持久化的层级状态
        self._load_tier_states()

        # 通知日志文件
        self.log_dir = self._resolve_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_log_dir(self) -> Path:
        try:
            from workspace_detector import DATA_DIR
            return Path(str(DATA_DIR))
        except Exception:
            return Path(__file__).parent.parent.parent.parent / "data"

    # ── 层级状态持久化 ──

    def _save_tier_states(self) -> None:
        """持久化当前层级状态到 JSON 文件"""
        try:
            with self.lock:
                TIER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(TIER_STATE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.tier_states, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"[加仓] 层级状态保存失败: {e}")

    def _load_tier_states(self) -> None:
        """从 JSON 文件加载层级状态"""
        try:
            if TIER_STATE_FILE.exists():
                with open(TIER_STATE_FILE, 'r', encoding='utf-8') as f:
                    self.tier_states = json.load(f)
            else:
                self.tier_states = {}
        except Exception:
            self.tier_states = {}
            logger.warning("[加仓] 层级状态加载失败，使用空状态")

    # ── 生命周期 ──

    def start(self) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self._daily_reset()
            self.thread = threading.Thread(
                target=self._run_loop, daemon=True, name="position-tier-monitor"
            )
            self.thread.start()
            logger.info(f"[加仓] ✅ 加仓监控已启动，轮询间隔 {self.interval}s")
            return True

    def stop(self) -> None:
        with self.lock:
            self.running = False
        self._save_tier_states()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        logger.info("[加仓] ⏹️ 加仓监控已停止")

    def is_running(self, check_thread: bool = True) -> bool:
        """检查监控是否在运行。"""
        if not self.running:
            return False
        if check_thread:
            return self.thread is not None and self.thread.is_alive()
        return True

    # ── 主循环 ──

    def _run_loop(self) -> None:
        print("[加仓] 后台加仓监控线程启动 (间隔=33s, 偏移=10s)", file=sys.stderr)
        time.sleep(10)  # 初始偏移，错开与其他监控器的首轮执行
        cycle = 0
        while self.running:
            cycle += 1
            try:
                if not self._is_trading_day():
                    if cycle % 20 == 1:
                        print(f"[加仓] ⏸️ 非交易日，跳过 (cycle={cycle})", file=sys.stderr)
                elif self._is_trading_time() and not self._is_blocked_window():
                    print(f"[加仓] 🔄 第 {cycle} 轮层级检查 | {datetime.now().strftime('%H:%M:%S')}", file=sys.stderr)
                    summary = self._check_all_positions()
                    if summary.get("total", 0) > 0 or summary.get("outflow", 0) > 0:
                        print(
                            f"[加仓] {summary['total']}只持仓 | "
                            f"触发{summary['triggered']} | "
                            f"未达标{summary['hold']} | "
                            f"流出{summary['outflow']} | "
                            f"已执行{summary['executed']} | "
                            f"拦截{summary['blocked']} | "
                            f"跳过{summary['skipped']} | "
                            f"去重{summary['dedup']}",
                            file=sys.stderr
                        )
                    else:
                        print(f"[加仓] 无持仓数据", file=sys.stderr)
                else:
                    if cycle % 20 == 1:
                        label = "非交易时段" if not self._is_trading_time() else "禁止窗口"
                        print(f"[加仓] ⏸️ {label}，跳过 (cycle={cycle})", file=sys.stderr)
                    self._daily_reset()
            except Exception as e:
                logger.error(f"[加仓] 检查异常: {e}", exc_info=True)
            time.sleep(self.interval)

    def _is_trading_time(self) -> bool:
        now = datetime.now().time()
        morning = self.TRADING_START <= now <= self.LUNCH_START
        afternoon = self.LUNCH_END <= now <= self.TRADING_END
        return morning or afternoon

    def _is_trading_day(self) -> bool:
        """检查今天是否为交易日（带日缓存，避免频繁API调用）"""
        today = datetime.now().strftime('%Y-%m-%d')
        if getattr(self, '_last_trading_day_check_date', '') == today:
            return getattr(self, '_last_trading_day_result', True)
        try:
            from core.utils.trade_day_utils import is_today_trade_day
            is_trade, reason = is_today_trade_day()
            self._last_trading_day_check_date = today
            self._last_trading_day_result = is_trade
            if not is_trade:
                logger.info(f"[加仓] 非交易日: {reason}")
            return is_trade
        except Exception:
            return True  # API 不可用时默认视为交易日

    def _is_blocked_window(self) -> bool:
        """返回是否处于禁止加仓的时间窗口"""
        now = datetime.now().time()
        # 早盘冷静期 09:30-09:45
        if now < self.MORNING_QUIET_END:
            return True
        # 午后不交易
        if now >= self.LUNCH_END:
            return True
        return False

    def _is_morning_volatility(self) -> bool:
        """早盘冷静期"""
        now = datetime.now().time()
        return self.TRADING_START <= now < self.MORNING_QUIET_END

    def _daily_reset(self) -> None:
        today = datetime.now().strftime('%Y-%m-%d')
        if getattr(self, '_last_reset_date', '') != today:
            self.today_adds.clear()
            self._last_eval.clear()
            self._concept_top10_notified.clear()
            self._ai_judge_cache.clear()
            self._last_reset_date = today

    # ══════════════════════════════════════════════════
    # 第 1 层：层级评估
    # ══════════════════════════════════════════════════

    def evaluate_position_tier(self, symbol: str, float_pnl_pct: float,
                               current_tier: str) -> TierEvaluation:
        """代码层自动评估加仓层级。"""
        pnl_pct = float_pnl_pct / 100.0 if float_pnl_pct > 1 else float_pnl_pct  # 统一为小数

        # 试探仓 → 冲刺仓（跳级）：浮盈 ≥ 3%
        if current_tier in ('probe', 'unknown', '') and pnl_pct >= 0.03:
            return TierEvaluation(
                action='UPGRADE_TO_SPRINT',
                current_tier='probe',
                target_tier='sprint',
                max_position_pct=self.TIER_CAPS['sprint'],
                signal=f'浮盈 {pnl_pct:.1%} ≥ 3%，跳过确认仓直接触发冲刺仓评估'
            )

        # 试探仓 → 确认仓：浮盈 ≥ 1%
        if current_tier in ('probe', 'unknown', '') and pnl_pct >= 0.01:
            return TierEvaluation(
                action='UPGRADE_TO_CONFIRM',
                current_tier='probe',
                target_tier='confirm',
                max_position_pct=self.TIER_CAPS['confirm'],
                signal=f'浮盈 {pnl_pct:.1%} ≥ 1%，触发确认仓评估'
            )

        # 确认仓 → 冲刺仓：浮盈 ≥ 3%
        if current_tier == 'confirm' and pnl_pct >= 0.03:
            return TierEvaluation(
                action='UPGRADE_TO_SPRINT',
                current_tier='confirm',
                target_tier='sprint',
                max_position_pct=self.TIER_CAPS['sprint'],
                signal=f'浮盈 {pnl_pct:.1%} ≥ 3%，触发冲刺仓评估'
            )

        # 已达到最高层级或未满足条件
        if current_tier == 'sprint':
            return TierEvaluation(action='MAX_TIER', signal=f'已达冲刺仓（最高层级），浮盈 {pnl_pct:.1%}')

        return TierEvaluation(action='HOLD', signal=f'浮盈 {pnl_pct:.1%} 未满足升级条件')

    # ══════════════════════════════════════════════════
    # 第 2 层：门控仲裁
    # ══════════════════════════════════════════════════

    def can_execute_add(self, symbol: str, evaluation: TierEvaluation,
                        current_price: float, avg_price: float,
                        account: dict, pi_stance: str = 'yellow') -> GateResult:
        """加仓执行门控 — 所有条件必须通过。"""
        checks = []
        total_asset = account.get('total_asset', 100000)
        position_value = account.get('position_value', 0)
        total_position_pct = position_value / total_asset if total_asset > 0 else 0
        add_position_pct = evaluation.max_position_pct

        # ── 门控 1：Pi 立场检查 ──
        if pi_stance == 'red':
            checks.append(('BLOCKED', 'RED 立场禁止加仓'))
            return GateResult(allowed=False, checks=checks)
        checks.append(('ALLOWED', f'{pi_stance.upper()} 立场通过'))

        # ── 门控 2：总回撤检查 ──
        try:
            drawdown = self._get_total_drawdown(account)
            if drawdown >= 0.05:
                checks.append(('BLOCKED', f'总回撤 {drawdown:.1%} ≥ 5%，硬禁止'))
                return GateResult(allowed=False, checks=checks)
            checks.append(('PASSED', f'回撤 {drawdown:.1%} < 5%'))
        except Exception as e:
            checks.append(('SKIPPED', f'回撤检查异常: {e}'))

        # ── 门控 3：连续亏损检查 ──
        try:
            consecutive = self._get_consecutive_losses()
            if consecutive >= 3:
                checks.append(('BLOCKED', f'连续亏损 {consecutive} 笔，熔断'))
                return GateResult(allowed=False, checks=checks)
            checks.append(('PASSED', f'连续亏损 {consecutive} < 3'))
        except Exception as e:
            checks.append(('SKIPPED', f'连亏检查异常: {e}'))

        # ── 门控 4：铁律二保护线检查 ──
        protection_blocked, protection_reason = self._check_protection_line(
            symbol, current_price, avg_price, evaluation
        )
        if protection_blocked:
            checks.append(('BLOCKED', protection_reason))
            return GateResult(allowed=False, checks=checks)
        checks.append(('PASSED', '保护线通过'))

        # ── 门控 5：今日加仓次数 ──
        daily_adds = self.today_adds.get(symbol, 0)
        if daily_adds >= self.MAX_ADDS_PER_DAY:
            checks.append(('BLOCKED', f'今日已加仓 {daily_adds} 次，达上限'))
            return GateResult(allowed=False, checks=checks)
        checks.append(('PASSED', f'今日加仓 {daily_adds}/{self.MAX_ADDS_PER_DAY}'))

        # ── 门控 5.5：涨停板检查 ──
        limit_up, limit_detail = self._check_limit_up(symbol)
        if limit_up:
            checks.append(('BLOCKED', f'涨停板: {limit_detail}'))
            return GateResult(allowed=False, checks=checks)
        checks.append(('PASSED', f'非涨停: {limit_detail}'))

        # ── 门控 6：趋势强度过滤（核心MA5>MA20 + 辅助5选2） ──
        trend = self.check_trend_strength(symbol)
        if not trend['passed']:
            failed_desc = ', '.join(trend['failed_items'])
            aux_info = f"辅助{trend.get('aux_passed', 0)}/{trend.get('aux_total', 4)}"
            checks.append(('BLOCKED', f'趋势强度未通过: {failed_desc} ({aux_info})'))
            return GateResult(allowed=False, checks=checks)
        aux_info = f"辅助{trend.get('aux_passed', 0)}/{trend.get('aux_total', 4)}"
        checks.append(('PASSED', f'趋势强度通过（核心MA5>MA20 + {aux_info}）'))

        # ── 门控 7：概念板块 TOP10 —— 降仓而非拦截 ──
        concept_passed, concept_detail = self._check_concept_top10_gate(symbol)
        if concept_passed:
            checks.append(('PASSED', f'概念TOP10: {concept_detail}'))
            self._last_concept_in_main_theme = True
        else:
            checks.append(('DOWNGRADE', f'概念TOP10降仓50%: {concept_detail}'))
            self._last_concept_in_main_theme = False
            self._notify_concept_top10_block(symbol, concept_detail)

        # ── 全部通过 ──
        checks.append(('PASSED', '全部门控通过'))
        return GateResult(allowed=True, checks=checks)

    def _get_total_drawdown(self, account: dict) -> float:
        """计算总回撤比例"""
        try:
            initial = account.get('initial_capital', 100000)
            total_pnl = 0
            if 'float_pnl' in account:
                total_pnl = account['float_pnl']
            if 'realized_pnl' in account:
                total_pnl += account['realized_pnl']
            elif hasattr(self, 'executor') and self.executor:
                # 尝试从 executor 获取
                try:
                    acc = self.executor.get_account()
                    # 解析 total_profit
                    profit_str = acc.get('total_profit', '0')
                    if isinstance(profit_str, str):
                        import re
                        match = re.search(r'[-+]?\d+\.?\d*', profit_str)
                        if match:
                            total_pnl = float(match.group())
                except Exception:
                    pass

            if initial <= 0:
                return 0
            return -min(0, total_pnl) / initial
        except Exception:
            return 0

    def _get_consecutive_losses(self) -> int:
        """获取连续亏损笔数"""
        try:
            if self.executor and hasattr(self.executor, '_consecutive_losses'):
                return self.executor._consecutive_losses
        except Exception:
            pass
        return 0

    def _check_protection_line(self, symbol: str, current_price: float,
                               avg_price: float, evaluation: TierEvaluation) -> tuple:
        """
        铁律二保护线检查。
        
        根据目标层级确定保护线：
          confirm → T1 保本线（成本价）
          sprint  → T2 保护线（成本+X%，X 由振幅决定）
        
        Returns:
            (blocked: bool, reason: str)
        """
        try:
            float_pnl_pct = (current_price - avg_price) / avg_price

            # 获取振幅档位
            amplitude_tier, amplitude_val = self._get_amplitude_info(symbol)

            if evaluation.target_tier == 'confirm':
                # T1 保本线 = 成本价，即浮盈不能 < 0
                if float_pnl_pct < 0:
                    return True, f'T1保本线：浮盈 {float_pnl_pct:.1%} < 0，已跌破成本价'
                # 浮盈距离保本线不足 0.5% → 太危险，不加仓
                if float_pnl_pct < 0.005:
                    return True, f'T1保本线：浮盈 {float_pnl_pct:.1%} 距离保本线不足0.5%'

            elif evaluation.target_tier == 'sprint':
                # T2 保护线 = 成本+X%
                if amplitude_tier == '低波':
                    x = 0.01
                elif amplitude_tier == '中波':
                    x = 0.02
                else:
                    x = 0.03
                protection_pct = x
                if float_pnl_pct < protection_pct:
                    return True, f'T2保护线：浮盈 {float_pnl_pct:.1%} < 成本+{x:.0%}({amplitude_tier})'
                # 浮盈距离保护线不足 0.5%
                if float_pnl_pct - protection_pct < 0.005:
                    return True, f'T2保护线：浮盈 {float_pnl_pct:.1%} 距离保护线不足0.5%'

            return False, '保护线安全'

        except Exception as e:
            logger.debug(f"[加仓] 保护线检查异常 {symbol}: {e}")
            return False, '保护线检查跳过'

    # ── 概念板块 TOP10 辅助方法 ──

    def _get_stock_concept_names(self, symbol: str) -> set:
        """查询 stock_pool.db 获取股票所属的所有概念板块名称，带当日缓存"""
        today = datetime.now().strftime('%Y-%m-%d')
        cached = self._stock_concepts_cache.get(symbol)
        if cached:
            names, cache_date = cached
            if cache_date == today:
                return names

        try:
            from app.api.indicator import _normalize_to_ts_code
            import sqlite3
            ts_code = _normalize_to_ts_code(symbol)
            pool_db = self.log_dir / "stock_pool.db"
            if not pool_db.exists():
                logger.warning(f"[加仓] stock_pool.db 不存在，无法获取 {symbol} 的概念板块")
                return set()

            conn = sqlite3.connect(str(pool_db))
            cursor = conn.cursor()
            bare_code = ts_code.split('.')[0] if '.' in ts_code else ts_code
            cursor.execute(
                "SELECT concept_name FROM stock_concept_map WHERE ts_code LIKE ?",
                (f"%{bare_code}%",)
            )
            names = {row[0] for row in cursor.fetchall()}
            conn.close()
            self._stock_concepts_cache[symbol] = (names, today)
            return names
        except Exception as e:
            logger.debug(f"[加仓] 获取 {symbol} 概念板块失败: {e}")
            return set()

    def _fetch_top_concept_names(self, sort_by: str, top_n: int = 10) -> set:
        """
        获取当日概念排名 TOP N 的概念名称集合。

        Args:
            sort_by: 'pct_change'（涨幅排名）或 'main_net'（主力净流入排名）
            top_n: 取前 N 名

        Returns:
            概念名称集合，已缓存 5 分钟
        """
        cache_key = f'top_concepts_{sort_by}'
        cached = self._trend_cache.get(cache_key)
        if cached:
            ts_cached, names = cached
            if time.time() - ts_cached < 300:
                return names

        try:
            import urllib.request, ssl, json as _json
            ctx = ssl.create_default_context()
            url = f'http://localhost:8000/api/v1/market/concept-fund-flow?limit={top_n}&sort_by={sort_by}'
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                data = _json.loads(resp.read().decode('utf-8'))
                sectors = data.get('sectors', []) or data.get('concepts', [])
                names = {s.get('name', '') for s in sectors if s.get('name')}
                self._trend_cache[cache_key] = (time.time(), names)
                return names
        except Exception as e:
            logger.debug(f"[加仓] 获取 TOP10 概念 ({sort_by}) 失败: {e}")
            # 如果有旧缓存，降级使用
            if cached:
                _, old_names = cached
                return old_names
            return set()

    def _call_ai_judge(self, prompt: str) -> dict:
        """
        调用 deepseek-v4-flash 做概念语义匹配判断（关闭思考模式，低延迟）。
        """
        import requests as _requests

        settings = None
        try:
            from app.config import get_settings
            settings = get_settings()
        except Exception:
            pass

        api_key = settings.DEEPSEEK_API_KEY if settings else ''
        api_host = settings.DEEPSEEK_API_HOST if settings else 'api.deepseek.com'

        if not api_key:
            logger.warning("[加仓] DeepSeek API Key 未配置，无法进行AI概念匹配")
            return {'matched': False, 'matched_concept': '', 'in_which': 'none', 'reason': 'API Key未配置'}

        payload = {
            'model': 'deepseek-v4-flash',
            'messages': [
                {'role': 'system', 'content': '你是一个概念板块语义匹配工具。只输出JSON，不要解释。'},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': 0.0,
            'max_tokens': 200,
            'thinking': {'type': 'disabled'},
            'response_format': {'type': 'json_object'},
        }

        try:
            resp = _requests.post(
                f'https://{api_host}/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
                json=payload,
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(f"[加仓] AI判断调用失败: status={resp.status_code}")
                return {'matched': False, 'matched_concept': '', 'in_which': 'none', 'reason': f'API HTTP {resp.status_code}'}

            raw_text = resp.text
            if not raw_text or not raw_text.strip():
                logger.warning(f"[加仓] AI返回空body, status={resp.status_code}")
                return {'matched': False, 'matched_concept': '', 'in_which': 'none', 'reason': 'API返回空响应'}
            try:
                data = json.loads(raw_text)
            except Exception:
                logger.warning(f"[加仓] AI返回非JSON: {raw_text[:200]}")
                return {'matched': False, 'matched_concept': '', 'in_which': 'none', 'reason': 'API返回非JSON格式'}
            message = data.get('choices', [{}])[0].get('message', {})
            content = message.get('content', '') or ''
            if not content:
                # 兼容推理类模型（如 deepseek-v4-flash）将答案放在 reasoning_content 的情况
                content = message.get('reasoning_content', '') or ''
            if not content:
                logger.warning(f"[加仓] AI返回content为空, keys={list(message.keys())}")
                return {'matched': False, 'matched_concept': '', 'in_which': 'none', 'reason': 'AI返回content为空'}
            # 去除可能的 markdown 代码块包装
            content = content.strip()
            if content.startswith('```'):
                lines = content.split('\n')
                content = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
                content = content.strip()
            # strip 或 markdown 剥离后可能变空，必须再次检查
            if not content:
                logger.warning(f"[加仓] AI返回content经strip/markdown处理后为空, raw={raw_text[:200]}")
                return {'matched': False, 'matched_concept': '', 'in_which': 'none', 'reason': 'AI返回content为空（strip后）'}
            import json as _json
            try:
                return _json.loads(content)
            except _json.JSONDecodeError:
                # content 可能是中文推理文本（reasoning_content 回退场景），尝试从中提取 JSON
                import re as _re
                match = _re.search(r'\{[^{}]*"matched"[^{}]*\}', content)
                if match:
                    try:
                        return _json.loads(match.group())
                    except _json.JSONDecodeError:
                        pass
                logger.warning(f"[加仓] AI返回content非JSON: {content[:200]}")
                return {'matched': False, 'matched_concept': '', 'in_which': 'none', 'reason': 'AI返回content非JSON格式'}
        except Exception as e:
            logger.warning(f"[加仓] AI判断异常: {e}")
            return {'matched': False, 'matched_concept': '', 'in_which': 'none', 'reason': f'调用异常: {e}'}

    def _check_concept_top10_gate(self, symbol: str) -> tuple:
        """
        概念主线检查：判断该股票是否在当日主线中。

        Returns:
            (in_main_theme: bool, detail: str)
            True = 在主线中，正常仓位
            False = 不在主线，降仓 50%
        """
        try:
            stock_concepts = self._get_stock_concept_names(symbol)
            if not stock_concepts:
                return False, '未找到所属概念板块（stock_pool.db 无该股映射），降仓50%'

            top_change = self._fetch_top_concept_names('pct_change', 10)
            top_inflow = self._fetch_top_concept_names('main_net', 10)

            if not top_change and not top_inflow:
                return False, '无法获取当日概念TOP10数据，降仓50%'

            # ── 快路径：字符串精确/包含匹配 ──
            in_change_exact = stock_concepts & top_change
            in_inflow_exact = stock_concepts & top_inflow
            if in_change_exact and in_inflow_exact:
                return True, f'双榜命中: 涨幅TOP10[{", ".join(sorted(in_change_exact)[:3])}] + 主力TOP10[{", ".join(sorted(in_inflow_exact)[:3])}]'
            if in_change_exact:
                return True, f'概念涨幅TOP10命中: {", ".join(sorted(in_change_exact)[:3])}'
            if in_inflow_exact:
                return True, f'主力净流入TOP10命中: {", ".join(sorted(in_inflow_exact)[:3])}'

            # ── 检查AI缓存 ──
            stock_hash = ','.join(sorted(stock_concepts))
            change_hash = ','.join(sorted(top_change))
            inflow_hash = ','.join(sorted(top_inflow))
            cache_key = f'{symbol}|{stock_hash}|{change_hash}|{inflow_hash}'
            cached = self._ai_judge_cache.get(cache_key)
            if cached:
                ts_cached, ai_result = cached
                if time.time() - ts_cached < 300:
                    if ai_result.get('matched'):
                        mc = ai_result.get('matched_concept', '')
                        iw = ai_result.get('in_which', '')
                        return True, f'AI语义匹配命中: {mc} ({iw})'
                    else:
                        return False, f'AI判断未命中: {ai_result.get("reason", "概念不匹配")}，降仓50%'

            # ── 慢路径：AI 语义匹配 ──
            stock_concepts_str = '\n'.join(f'- {c}' for c in sorted(stock_concepts))
            top_change_str = '\n'.join(f'- {c}' for c in sorted(top_change)) if top_change else '(空)'
            top_inflow_str = '\n'.join(f'- {c}' for c in sorted(top_inflow)) if top_inflow else '(空)'

            prompt = f"""判断「股票所属概念」列表中是否有任何一个概念，与「涨幅TOP10」或「主力TOP10」中的某个概念语义相同或指向同一板块。

语义匹配标准（宽松）：
1. 名称相似：如"机器人概念"≈"机器人"，"AI智能体"≈"人工智能"，"低空经济"≈"低空飞行器"
2. 产业链同向：如"锂电池"≈"新能源车"，"光伏"≈"太阳能"
3. 简称/全称：如"CPO"≈"光电共封装"，"PCB"≈"印制电路板"
4. 同一板块的不同命名方式：如"人形机器人"≈"人行机器人"，"半导体"≈"芯片"
5. 概念名称中有1-3个字不同但指向同一板块的，视为匹配

股票所属概念：
{stock_concepts_str}

当日概念涨幅TOP10：
{top_change_str}

主力净流入TOP10：
{top_inflow_str}

请只回复一个JSON对象，不要任何其他内容：
{{"matched": true或false, "matched_concept": "匹配到的概念名（false时为空字符串）", "in_which": "change/inflow/both/none", "reason": "一句话说明匹配理由（命中时说明哪个概念匹配了哪个TOP10概念，未命中时说明为什么都不匹配）"}}"""

            ai_result = self._call_ai_judge(prompt)
            self._ai_judge_cache[cache_key] = (time.time(), ai_result)

            if ai_result.get('matched'):
                mc = ai_result.get('matched_concept', '')
                iw = ai_result.get('in_which', '')
                return True, f'AI语义匹配命中: {mc} ({iw})'

            reason = ai_result.get('reason', '概念不匹配')
            stock_sample = ', '.join(sorted(stock_concepts)[:5])
            return False, (
                f'降仓50% | AI判断未命中: {reason} | '
                f'所属概念 [{stock_sample}{"…" if len(stock_concepts) > 5 else ""}] '
                f'未语义匹配涨幅/主力TOP10'
            )

        except Exception as e:
            logger.warning(f"[加仓] AI概念TOP10门控异常 {symbol}: {e}")
            return False, f'降仓50% | AI概念TOP10检查异常: {e}'

    def _notify_concept_top10_block(self, symbol: str, detail: str) -> None:
        """
        概念TOP10降仓通知，每日每只股票仅推送一次。
        """
        today = datetime.now().strftime('%Y-%m-%d')
        last_notified = self._concept_top10_notified.get(symbol, '')
        if last_notified == today:
            return

        try:
            from app.services.qqbot_service import send_qq_notification
            msg = (
                f"[加仓降仓·概念TOP10]\n"
                f"标的: {symbol}\n"
                f"原因: {detail}\n"
                f"时间: {datetime.now().strftime('%H:%M:%S')}\n"
                f"提示: 该股所属概念板块未进入当日涨幅/主力净流入 TOP10，"
                f"仓位减半处理"
            )
            send_qq_notification(msg)
            self._concept_top10_notified[symbol] = today
            print(f"[加仓] 📱 QQ推送: {symbol} 概念TOP10降仓50%", file=sys.stderr)
        except Exception as e:
            logger.debug(f"[加仓] QQ推送失败: {e}")

    # ── 实时技术指标缓存 ──
    def _fetch_realtime_ma(self, symbol: str) -> dict:
        """
        获取盘中实时 MA5/MA10/MA20，5 分钟缓存。

        数据源: /api/v1/indicator/realtime/{symbol}
        （腾讯 qt.gtimg.cn 实时行情 + Tushare 历史日线混合计算）

        Returns:
            {'ma5': float, 'ma10': float, 'ma20': float, 'current_price': float, 'source': str}
            失败时返回空 dict
        """
        cache_key = f'realtime_ma_{symbol}'
        cached = self._realtime_ma_cache.get(cache_key)
        if cached:
            ts_cached, result = cached
            if time.time() - ts_cached < 300:
                return result

        try:
            import urllib.request, ssl, json as _json
            ctx = ssl.create_default_context()
            url = f'http://localhost:8000/api/v1/indicator/realtime/{symbol}'
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                data = _json.loads(resp.read().decode('utf-8'))
                rt = data.get('realtime', {}) or {}
                result = {
                    'ma5': rt.get('ma5', 0) or 0,
                    'ma10': rt.get('ma10', 0) or 0,
                    'ma20': rt.get('ma20', 0) or 0,
                    'current_price': rt.get('current_price', 0) or 0,
                    'source': rt.get('data_source', 'intraday_estimate'),
                }
                self._realtime_ma_cache[cache_key] = (time.time(), result)
                return result
        except Exception as e:
            logger.debug(f"[加仓] 实时MA获取失败 {symbol}: {e}")
            if cached:
                _, old_result = cached
                return old_result
            return {}

    def check_trend_strength(self, symbol: str) -> dict:
        """
        趋势强度过滤 — 区分「噪声浮盈」和「趋势浮盈」。

        核心（必须全部通过）：
          1. MA5 > MA20（多头排列）→ 实时MA（腾讯行情+Tushare历史）
          2. 当日主力资金净流入 > 0（主力看好）
        辅助（3选2即可）：
          1. MA5 斜率 > 0（趋势向上）→ 实时MA5 vs 前5日MA5
          2. 量比 > 0.8（非缩量下跌）
          3. 所属板块主力净流入 > 0（板块有资金支持）

        Returns:
            {
                'passed': bool,
                'failed_items': [str],
                'checks': {...},
                'rule': 'core_dual_plus_2aux',
                'aux_passed': int,
                'core_passed': bool,
            }
        """
        checks = {}
        try:
            from app.api.indicator import _normalize_to_ts_code
            from app.config import get_settings
            import tushare as ts
            import urllib.request, ssl, json as _json

            # ── 获取实时 MA 数据（盘中估算） ──
            rt_ma = self._fetch_realtime_ma(symbol)
            rt_ma5 = rt_ma.get('ma5', 0) if rt_ma else 0
            rt_ma20 = rt_ma.get('ma20', 0) if rt_ma else 0
            rt_source = rt_ma.get('source', '') if rt_ma else ''

            # ── 获取 Tushare 日线（量比 + MA5斜率 + 降级兜底） ──
            settings = get_settings()
            token = settings.get_tushare_token()
            pro = ts.pro_api(token)
            ts_code = _normalize_to_ts_code(symbol)

            from datetime import datetime as dt, timedelta
            end_d = dt.now().strftime("%Y%m%d")
            start_d = (dt.now() - timedelta(days=30)).strftime("%Y%m%d")
            df = pro.daily(ts_code=ts_code, start_date=start_d, end_date=end_d, limit=15)

            has_daily = df is not None and not df.empty and len(df) >= 5
            if has_daily:
                df = df.sort_values("trade_date", ascending=True)
                closes = df['close'].values

            # ── 条件 1：MA5 斜率 > 0 ──
            if rt_ma5 > 0 and has_daily and len(closes) >= 10:
                # 盘中实时 MA5 vs 前5日 MA5（基于日线收盘价）
                ma5_prev = float(sum(closes[-10:-5]) / 5)
                if ma5_prev > 0:
                    ma5_slope = (rt_ma5 - ma5_prev) / ma5_prev
                    checks['ma5_slope'] = {
                        'passed': ma5_slope > 0,
                        'value': f'{ma5_slope:.2%}',
                        'threshold': '> 0',
                        'detail': f'实时MA5 {rt_ma5:.2f} vs 前5日MA5 {ma5_prev:.2f} [{rt_source}]'
                    }
                else:
                    checks['ma5_slope'] = {'passed': False, 'value': 'N/A', 'threshold': '> 0', 'detail': 'MA5计算异常'}
            elif has_daily and len(closes) >= 10:
                # 降级: 全部用日线计算
                ma5_now = float(sum(closes[-5:]) / 5)
                ma5_prev = float(sum(closes[-10:-5]) / 5)
                if ma5_prev > 0:
                    ma5_slope = (ma5_now - ma5_prev) / ma5_prev
                    checks['ma5_slope'] = {
                        'passed': ma5_slope > 0,
                        'value': f'{ma5_slope:.2%}',
                        'threshold': '> 0',
                        'detail': f'MA5 {ma5_now:.2f} vs 前5日MA5 {ma5_prev:.2f} [日线降级]'
                    }
                else:
                    checks['ma5_slope'] = {'passed': False, 'value': 'N/A', 'threshold': '> 0', 'detail': 'MA5计算异常'}
            else:
                checks['ma5_slope'] = {
                    'passed': False, 'value': 'N/A', 'threshold': '> 0',
                    'detail': f'数据不足(需≥10条日线)'
                }

            # ── 条件 2：量比 > 0.8 ──
            if has_daily:
                try:
                    last_vol = float(df['vol'].values[-1])
                    recent_avg_vol = float(sum(df['vol'].values[-6:-1]) / 5) if len(df) >= 6 else last_vol
                    vol_ratio = last_vol / recent_avg_vol if recent_avg_vol > 0 else 0
                    checks['volume_ratio'] = {
                        'passed': vol_ratio > 0.8,
                        'value': f'{vol_ratio:.2f}',
                        'threshold': '> 0.8'
                    }
                except Exception:
                    checks['volume_ratio'] = {'passed': True, 'value': 'N/A', 'threshold': '> 0.8', 'detail': '计算跳过'}
            else:
                checks['volume_ratio'] = {'passed': True, 'value': 'N/A', 'threshold': '> 0.8', 'detail': '日线数据不足'}

            # ── 条件 3：板块资金净流入 > 0（5分钟缓存） ──
            sector_net = 0
            sector_name = ''
            try:
                cache_key = f'concept_flow_{symbol}'
                cached = self._trend_cache.get(cache_key)
                if cached:
                    ts_cached, sector_net, sector_name = cached
                    if time.time() - ts_cached < 300:
                        pass
                    else:
                        sector_net, sector_name = self._fetch_sector_flow(symbol)
                        self._trend_cache[cache_key] = (time.time(), sector_net, sector_name)
                else:
                    sector_net, sector_name = self._fetch_sector_flow(symbol)
                    self._trend_cache[cache_key] = (time.time(), sector_net, sector_name)
            except Exception:
                pass

            checks['sector_flow'] = {
                'passed': sector_net > 0,
                'value': f'{sector_net / 1e8:.2f}亿',
                'threshold': '> 0',
                'detail': f'所属{sector_name}板块' if sector_name else '板块信息获取失败'
            }

            # ── 条件 4：MA5 > MA20（多头排列） ──
            if rt_ma5 > 0 and rt_ma20 > 0:
                checks['ma_align'] = {
                    'passed': rt_ma5 > rt_ma20,
                    'value': f'实时MA5={rt_ma5:.2f} MA20={rt_ma20:.2f}',
                    'threshold': 'MA5 > MA20',
                    'detail': f'[{rt_source}]'
                }
            elif has_daily:
                ma5 = float(sum(closes[-5:]) / 5) if len(closes) >= 5 else 0
                ma20 = float(sum(closes[-20:]) / 20) if len(closes) >= 20 else 0
                if ma5 > 0 and ma20 > 0:
                    checks['ma_align'] = {
                        'passed': ma5 > ma20,
                        'value': f'MA5={ma5:.2f} MA20={ma20:.2f}',
                        'threshold': 'MA5 > MA20',
                        'detail': '[日线降级]'
                    }
                else:
                    checks['ma_align'] = {'passed': False, 'value': 'N/A', 'threshold': 'MA5 > MA20', 'detail': 'MA计算异常'}
            else:
                checks['ma_align'] = {'passed': False, 'value': 'N/A', 'threshold': 'MA5 > MA20', 'detail': '数据不足'}

            # ── 条件 5：主力资金流向（当日主力 > 0，5分钟缓存） ──
            main_net_today = 0.0
            try:
                cache_key = f'moneyflow_{symbol}'
                cached = self._trend_cache.get(cache_key)
                if cached:
                    ts_cached, main_net_today = cached
                    if time.time() - ts_cached < 300:
                        pass
                    else:
                        main_net_today = self._fetch_moneyflow_today(symbol)
                        self._trend_cache[cache_key] = (time.time(), main_net_today)
                else:
                    main_net_today = self._fetch_moneyflow_today(symbol)
                    self._trend_cache[cache_key] = (time.time(), main_net_today)
            except Exception:
                pass

            checks['moneyflow'] = {
                'passed': main_net_today > 0,
                'value': f'{main_net_today / 1e8:.2f}亿' if main_net_today != 0 else '0',
                'threshold': '> 0',
                'detail': f'当日主力净流入' + ('' if main_net_today > 0 else '（主力流出中）')
            }

            # ── 综合判定：核心(MA5>MA20 + 个股资金>0) + 辅助 3选2 ──
            ma_ok = checks.get('ma_align', {}).get('passed', False)
            mf_ok = checks.get('moneyflow', {}).get('passed', False)
            core_passed = ma_ok and mf_ok
            aux_keys = ['ma5_slope', 'volume_ratio', 'sector_flow']
            aux_passed = sum(1 for k in aux_keys if checks.get(k, {}).get('passed', False))
            aux_total = len(aux_keys)

            failed_items = []
            if not ma_ok:
                failed_items.append('ma_align(核心)')
            if not mf_ok:
                failed_items.append('moneyflow(核心)')
            for k in aux_keys:
                if not checks.get(k, {}).get('passed', False):
                    failed_items.append(k)

            all_passed = core_passed and aux_passed >= 2

            return {
                'passed': all_passed,
                'failed_items': failed_items,
                'checks': checks,
                'rule': 'core_dual_plus_2aux',
                'aux_passed': aux_passed,
                'aux_total': aux_total,
                'core_passed': core_passed,
            }

        except Exception as e:
            logger.debug(f"[加仓] 趋势强度检查异常 {symbol}: {e}")
            failed = list(checks.keys()) if checks else ['ma5_slope', 'volume_ratio', 'sector_flow', 'ma_align']
            return {'passed': False, 'failed_items': failed, 'checks': checks}

    def _fetch_sector_flow(self, symbol: str) -> tuple:
        """
        从概念板块资金流 API 获取该标的所属板块的主力净流入。
        
        Returns:
            (sector_net_inflow: float, sector_name: str)
        """
        try:
            import urllib.request, ssl, json as _json
            ctx = ssl.create_default_context()
            url = 'http://localhost:8000/api/v1/market/concept-fund-flow?limit=30&sort_by=main_net'
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                data = _json.loads(resp.read().decode('utf-8'))
                concepts = data.get('concepts', [])
                for concept in concepts:
                    stocks = concept.get('stocks', [])
                    if isinstance(stocks, list):
                        for s in stocks:
                            s_code = s.get('symbol', '') if isinstance(s, dict) else str(s)
                            if symbol in s_code or s_code in symbol:
                                return concept.get('main_net', 0), concept.get('name', '')
        except Exception:
            pass
        return 0, ''

    def _fetch_moneyflow_today(self, symbol: str) -> float:
        """
        获取个股当日主力净流入金额。
        通过 /api/v1/market/moneyflow 接口获取，5 分钟缓存。

        Returns:
            当日主力净流入（元）
        """
        try:
            import urllib.request, ssl, json as _json
            ctx = ssl.create_default_context()
            url = f'http://localhost:8000/api/v1/market/moneyflow/{symbol}'
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                data = _json.loads(resp.read().decode('utf-8'))
                return float(data.get('main_net', 0))
        except Exception:
            pass
        return 0.0

    def _check_limit_up(self, symbol: str) -> tuple:
        """
        检查股票是否涨停，返回 (is_limit_up, detail)。

        根据板块确定涨停阈值：
        - 主板 (60xxxx/00xxxx): 10%
        - 创业板 (30xxxx): 20%
        - 科创板 (688xxx): 20%
        - 北交所 (8xxxxx/4xxxxx): 30%
        """
        try:
            import urllib.request, ssl, json as _json
            ctx = ssl.create_default_context()
            url = f'http://localhost:8000/api/v1/market/quote/{symbol}'
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                data = _json.loads(resp.read().decode('utf-8'))
                change_pct = float(data.get('percent', 0) or 0)
                if change_pct <= 0:
                    return False, f'涨幅{change_pct:.1f}%'

                # 确定涨停阈值
                bare = symbol.replace('SH', '').replace('SZ', '').replace('BJ', '').replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
                if len(bare) >= 6:
                    bare = bare[-6:]
                if bare.startswith('688'):
                    limit = 20.0
                elif bare.startswith(('300', '301')):
                    limit = 20.0
                elif bare.startswith(('8', '4')):
                    limit = 30.0
                else:
                    limit = 10.0

                if change_pct >= limit - 0.1:
                    return True, f'{change_pct:.1f}% 触及涨停板({limit}%)，禁止加仓'
                return False, f'涨幅{change_pct:.1f}% < 涨停{limit}%'
        except Exception:
            return False, '涨停检查异常，放行'

    # ── 振幅辅助 ──

    _amplitude_cache: Dict[str, tuple] = {}

    def _get_amplitude_info(self, symbol: str) -> tuple:
        """获取振幅档位和百分比值，带交易日缓存"""
        today = datetime.now().strftime("%Y%m%d")
        cached = self._amplitude_cache.get(symbol)
        if cached:
            tier, amp_val, cached_date = cached
            if cached_date == today:
                return tier, amp_val

        tier = "中波"
        avg_amp = 3.0
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
        return tier, avg_amp

    # ══════════════════════════════════════════════════
    # 第 3 层：自动执行
    # ══════════════════════════════════════════════════

    def execute_add_position(self, symbol: str, evaluation: TierEvaluation,
                             current_price: float, account: dict,
                             pi_stance: str = 'yellow',
                             pos_mv: float = None,
                             gate: GateResult = None) -> Optional[dict]:
        """代码层自动执行加仓下单。直接用 tier 目标仓位计算加仓量，不受新仓约束限制。"""
        total_asset = account.get('total_asset', 100000)
        available_cash = account.get('available_cash', 0)
        if pos_mv is None:
            pos_mv = self._get_position_market_value(symbol)
        current_position_mv = pos_mv

        if total_asset <= 0:
            self._add_notification(symbol, 'BLOCKED', '总资产为0，无法计算')
            return None

        target_pct = evaluation.max_position_pct  # 如 sprint=0.25

        # 不在概念主线 → 目标仓位上限减半
        if not getattr(self, '_last_concept_in_main_theme', True):
            target_pct = target_pct * 0.5
        current_pct = current_position_mv / total_asset if total_asset > 0 else 0

        # 当前仓位已超目标上限，跳过
        if current_pct >= target_pct:
            self._add_notification(
                symbol, 'SKIPPED',
                f'当前仓位 {current_pct:.1%} 已达目标上限 {target_pct:.0%}，无需加仓'
            )
            return None

        # 目标仓位金额 = 总资产 × 目标百分比
        target_amount = total_asset * target_pct
        # 加仓金额 = 目标 - 当前持仓（已持部分不算现金消耗）
        add_amount = max(0, target_amount - current_position_mv)
        # 现金约束：最多用到可用现金（保留 5% 缓冲防止零头失败）
        max_by_cash = max(0, available_cash - total_asset * 0.05)
        add_amount = min(add_amount, max_by_cash)
        add_shares = int(add_amount / current_price / 100) * 100
        add_pct = add_amount / total_asset if total_asset > 0 else 0

        if add_shares < 100:
            # 加仓检测：如最低股数超出仓位限制，买入最低股数100
            min_cost = 100 * current_price
            if available_cash >= min_cost:
                add_shares = 100
                add_amount = min_cost
                add_pct = min_cost / total_asset if total_asset > 0 else 0
                print(
                    f"[加仓] {symbol} 建议股数不足100，强制买入100股 (成本{min_cost:.0f}) | "
                    f"目标{target_pct:.0%}→{target_amount:.0f} 当前{current_position_mv:.0f}({current_pct:.1%})",
                    file=sys.stderr
                )
            else:
                self._add_notification(
                    symbol, 'SKIPPED',
                    f'目标仓位 {target_pct:.0%} → 目标金额 {target_amount:.0f}，'
                    f'当前 {current_position_mv:.0f}（{current_pct:.1%}），'
                    f'加仓 {add_shares} 股不足100'
                    + (f' | 现金不足 (可用{available_cash:.0f})' if add_amount < target_amount - current_position_mv else '')
                )
                print(
                    f"[加仓] ⚠️ {symbol} 加仓量不足: {add_shares}股 | "
                    f"目标{target_pct:.0%}→{target_amount:.0f} 当前{current_position_mv:.0f}({current_pct:.1%}) "
                    f"可用{available_cash:.0f}",
                    file=sys.stderr
                )
                return None

        # 下单前最后一次保护线检查
        avg_price = self._get_position_avg_price(symbol)
        if avg_price > 0:
            blocked, reason = self._check_protection_line(
                symbol, current_price, avg_price, evaluation
            )
            if blocked:
                self._add_notification(symbol, 'BLOCKED', f'下单前保护线检查失败: {reason}')
                return None

        # 执行下单
        if self.executor is None:
            self._add_notification(symbol, 'BLOCKED', 'executor 未注入')
            return None

        try:
            is_refill = getattr(evaluation, 'action', '') == 'REFILL'
            label = '补仓' if is_refill else '自动加仓'
            result = self.executor.buy(
                symbol=symbol,
                price=current_price,
                volume=add_shares,
                reason=(
                    f'[TierMonitor{label}] {evaluation.signal} | '
                    f'当前仓位 {current_pct:.1%} → 目标 {target_pct:.0%} | '
                    f'加仓 {add_shares} 股 ({add_pct:.1%}) | '
                    f'层级 {evaluation.current_tier}'
                    + (f'→{evaluation.target_tier}' if not is_refill else '（补仓）')
                )
            )

            if result.get('status') == 'executed':
                # 更新层级状态（补仓不改变层级）
                if not is_refill:
                    with self.lock:
                        self.tier_states[symbol] = {
                            'tier': evaluation.target_tier,
                            'updated_at': datetime.now().isoformat(),
                            'avg_price': current_price,
                        }
                        self.today_adds[symbol] = self.today_adds.get(symbol, 0) + 1
                    self._save_tier_states()
                else:
                    with self.lock:
                        self.today_adds[symbol] = self.today_adds.get(symbol, 0) + 1

                self._add_notification(
                    symbol, 'EXECUTED',
                    f'代码层{label}: {evaluation.current_tier}'
                    + (f'→{evaluation.target_tier}' if not is_refill else '补仓')
                    + f', +{add_shares}股, 浮盈信息, '
                    f'仓位 {current_pct:.1%}→{current_pct + add_pct:.1%}'
                )
                # ── QQ 推送：包含完整门控结果 ──
                self._send_execution_qq_notification(
                    symbol, evaluation, gate, add_shares, add_pct,
                    current_pct, current_price, is_refill
                )
                logger.info(
                    f"[加仓] ✅ {label} {symbol}: "
                    f"{evaluation.current_tier}"
                    f"{'→' + evaluation.target_tier if not is_refill else '补仓'}"
                    f", +{add_shares}股 @ {current_price}"
                )
                print(
                    f"[加仓] ✅ {symbol} {label} +{add_shares}股 @ {current_price} | "
                    f"{evaluation.signal}",
                    file=sys.stderr
                )
                return result
            else:
                reason = result.get("reason", "未知")
                self._add_notification(
                    symbol, 'FAILED',
                    f'下单失败: {reason}'
                )
                print(f"[加仓] ❌ {symbol} 下单被拒: {reason}", file=sys.stderr)
                return None  # 返回 None 让上层正确计入 skipped

        except Exception as e:
            self._add_notification(symbol, 'ERROR', f'加仓异常: {e}')
            logger.error(f"[加仓] ❌ 加仓异常 {symbol}: {e}", exc_info=True)
            return None

    # ── 持仓辅助 ──

    def _get_position_market_value(self, symbol: str) -> float:
        """获取某只持仓的当前市值"""
        try:
            positions = self.executor.get_positions() if self.executor else []
            for pos in positions:
                if pos.get('symbol') == symbol:
                    return pos.get('current_price', 0) * pos.get('volume', 0)
        except Exception:
            pass
        return 0.0

    def _get_total_position_market_value(self) -> float:
        """获取所有持仓的总市值"""
        try:
            positions = self.executor.get_positions() if self.executor else []
            total = 0.0
            for pos in positions:
                total += pos.get('current_price', 0) * pos.get('volume', 0)
            return total
        except Exception:
            pass
        return 0.0

    def _get_position_avg_price(self, symbol: str) -> float:
        """获取某只持仓的均价"""
        try:
            positions = self.executor.get_positions() if self.executor else []
            for pos in positions:
                if pos.get('symbol') == symbol:
                    return pos.get('avg_price', 0)
        except Exception:
            pass
        return 0.0

    def _get_position_tier(self, symbol: str) -> str:
        """获取持仓当前层级"""
        with self.lock:
            state = self.tier_states.get(symbol, {})
            return state.get('tier', 'probe')

    # ══════════════════════════════════════════════════
    # 核心检查逻辑（由主循环调用）
    # ══════════════════════════════════════════════════

    def _check_all_positions(self) -> dict:
        """主循环调用的全持仓检查，按主力净流入降序处理，净流出直接跳过。"""
        summary = {
            "total": 0, "triggered": 0, "hold": 0, "executed": 0,
            "blocked": 0, "skipped": 0, "dedup": 0, "outflow": 0,
        }

        if self.executor is None:
            return summary

        try:
            account = self.executor.get_account()
            positions = self.executor.get_positions()
        except Exception as e:
            logger.warning(f"[加仓] 获取账户/持仓失败: {e}")
            return summary

        if not positions:
            return summary

        # 获取 Pi 立场
        pi_stance = self._get_pi_stance()

        # 今日买入的跳过（T+1 下不加今天买的）
        today_buy_symbols = (
            self.executor._get_today_buy_symbols() if self.executor else set()
        )

        # ── 第一遍：收集有效持仓 + 主力资金流向 → 排序 ──
        enriched = []  # [(moneyflow, symbol, pos), ...]
        hold_details = []

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

            # 获取主力净流入（缓存 5 分钟）
            moneyflow = self._fetch_moneyflow_today(symbol)

            # 主力净流出 → 不加仓，标记并跳过
            if moneyflow < 0:
                summary["outflow"] += 1
                self._add_notification(
                    symbol, 'BLOCKED',
                    f'主力净流出 {moneyflow/1e8:.2f}亿，跳过加仓（建议关注是否减仓）'
                )
                print(
                    f"[加仓] 💸 {symbol} 主力净流出 {moneyflow/1e8:.2f}亿，跳过加仓",
                    file=sys.stderr
                )
                continue

            enriched.append((moneyflow, symbol, pos))

        # 按主力净流入降序排列：龙头优先加仓
        enriched.sort(key=lambda x: x[0], reverse=True)

        if enriched:
            flows = ', '.join(f"{s}({mf/1e8:.1f}亿)" for mf, s, _ in enriched)
            print(f"[加仓] 📊 加仓排序: {flows}", file=sys.stderr)

        # ── 第二遍：按排序逐只评估 + 门控 + 执行 ──
        for moneyflow, symbol, pos in enriched:
            avg_price = pos.get('avg_price', 0)
            current_price = pos.get('current_price', 0)
            volume = pos.get('volume', 0)

            summary["total"] += 1
            float_pnl_pct = (current_price - avg_price) / avg_price * 100

            # ── 第 1 层：层级评估 ──
            current_tier = self._get_position_tier(symbol)
            evaluation = self.evaluate_position_tier(
                symbol, float_pnl_pct, current_tier
            )

            if evaluation.action in ('HOLD', 'MAX_TIER'):
                # ── 补仓检测：层级已达上限但仓位未满 ──
                if evaluation.action == 'MAX_TIER':
                    tier_cap = self.TIER_CAPS.get(current_tier, 0.10)
                    total_asset = account.get('total_asset', 100000)
                    pos_mv = current_price * volume
                    current_pct = pos_mv / total_asset if total_asset > 0 else 0
                    refill_threshold_pct = tier_cap * self.REFILL_THRESHOLD
                    if current_pct < refill_threshold_pct:
                        evaluation = TierEvaluation(
                            action='REFILL',
                            current_tier=current_tier,
                            target_tier=current_tier,
                            max_position_pct=tier_cap,
                            signal=f'补仓：当前 {current_pct:.1%} < {tier_cap:.0%}×{self.REFILL_THRESHOLD:.0%}={refill_threshold_pct:.1%}'
                        )
                    else:
                        summary["hold"] += 1
                        hold_details.append(f"{symbol}({current_tier}→{evaluation.signal})")
                        continue
                else:
                    summary["hold"] += 1
                    hold_details.append(f"{symbol}({current_tier}→{evaluation.signal})")
                    continue

            summary["triggered"] += 1

            # 防重复触发：同一符号 5 分钟内不重复评估
            now_ts = time.time()
            last_eval = self._last_eval.get(symbol, 0)
            if now_ts - last_eval < 300:
                summary["dedup"] += 1
                continue
            self._last_eval[symbol] = now_ts

            # ── 第 2 层：门控仲裁 ──
            gate = self.can_execute_add(
                symbol, evaluation, current_price, avg_price,
                account, pi_stance
            )

            if gate.allowed:
                # ── 第 3 层：执行加仓 ──
                success = self.execute_add_position(symbol, evaluation, current_price, account, pi_stance, current_price * volume, gate)
                if success:
                    summary["executed"] += 1
                else:
                    summary["skipped"] += 1
                    print(
                        f"[加仓] ⚠️ {symbol} 门控通过但执行失败 | "
                        f"层级 {evaluation.current_tier}→{evaluation.target_tier} | "
                        f"详见 tier_notifications 日志",
                        file=sys.stderr
                    )
            else:
                summary["blocked"] += 1
                block_reasons = [c[1] for c in gate.checks if c[0] == 'BLOCKED']
                if block_reasons:
                    print(
                        f"[加仓] 🚫 {symbol} 门控拦截: {'; '.join(block_reasons)}",
                        file=sys.stderr
                    )
                    self._add_notification(
                        symbol, 'BLOCKED',
                        f'加仓拦截 ({evaluation.target_tier}): {"; ".join(block_reasons)}'
                    )

        # 有持仓但全部未触发时，记录详情
        if summary["total"] > 0 and summary["triggered"] == 0:
            logger.debug(f"[加仓] {summary['total']}只持仓均未满足层级升级条件: {', '.join(hold_details)}")

        return summary

    def _get_pi_stance(self) -> str:
        """获取 Pi 最新立场"""
        try:
            from core.utils.strategy_chain import StrategyChain
            chain = StrategyChain()
            pi_conf = chain.get_pi_confirmation()
            if pi_conf:
                return pi_conf.get('stance', 'yellow')
        except Exception:
            pass
        return 'yellow'

    # ── 通知管理 ──

    def _add_notification(self, symbol: str, ntype: str, message: str) -> None:
        """添加通知到队列"""
        notif = {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'type': ntype,  # EXECUTED / BLOCKED / SKIPPED / FAILED / ERROR
            'message': message,
        }
        with self.lock:
            self.notifications.append(notif)
            # 保留最近 500 条
            if len(self.notifications) > 500:
                self.notifications = self.notifications[-500:]

        # 写入通知日志文件
        self._log_notification(notif)

    def _log_notification(self, notif: dict) -> None:
        """写入通知日志"""
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            log_file = self.log_dir / f"tier_notifications_{today}.jsonl"
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(notif, ensure_ascii=False) + '\n')
        except Exception:
            pass

    def _send_execution_qq_notification(self, symbol: str, evaluation: TierEvaluation,
                                         gate: GateResult, add_shares: int,
                                         add_pct: float, current_pct: float,
                                         current_price: float, is_refill: bool) -> None:
        """构建并发送包含完整门控结果的 QQ 加仓通知"""
        try:
            from app.services.qqbot_service import send_qq_notification

            is_refill_flag = is_refill
            label = '补仓' if is_refill_flag else '自动加仓'
            tier_info = (f"{evaluation.current_tier}→{evaluation.target_tier}"
                         if not is_refill_flag else f"{evaluation.current_tier}（补仓）")

            # ── 解析门控结果 ──
            gate_lines = []
            if gate and gate.checks:
                for status, detail in gate.checks:
                    if status == 'ALLOWED':
                        gate_lines.append(f"  ✓ {detail}")
                    elif status == 'PASSED':
                        gate_lines.append(f"  ✓ {detail}")
                    elif status == 'DOWNGRADE':
                        gate_lines.append(f"  ⚠ {detail}")
                    elif status == 'BLOCKED':
                        gate_lines.append(f"  ✗ {detail}")
            gate_detail = '\n'.join(gate_lines) if gate_lines else '  (门控详情缺失)'

            msg = (
                f"[加仓·{label}] {symbol}\n"
                f"━━━━━━━━━━━━━━\n"
                f"触发信号: {evaluation.signal}\n"
                f"层级: {tier_info}\n"
                f"价格: {current_price:.2f}\n"
                f"加仓: +{add_shares}股 (+{add_pct:.1%})\n"
                f"仓位: {current_pct:.1%}→{current_pct + add_pct:.1%}\n"
                f"━━━━━━━━━━━━━━\n"
                f"门控确认:\n{gate_detail}\n"
                f"━━━━━━━━━━━━━━\n"
                f"时间: {datetime.now().strftime('%H:%M:%S')}"
            )
            send_qq_notification(msg)
            print(f"[加仓] 📱 QQ推送: {symbol} {label}成功, 门控{len(gate_lines)}项确认", file=sys.stderr)
        except Exception as e:
            logger.debug(f"[加仓] QQ推送失败: {e}")

    def get_notifications(self, since: Optional[str] = None) -> List[dict]:
        """获取通知列表。since 为 ISO 时间字符串，仅返回此时间之后的通知。"""
        with self.lock:
            notifs = list(self.notifications)
        if since:
            notifs = [n for n in notifs if n['timestamp'] > since]
        return notifs

    def get_tier_status(self) -> Dict[str, Any]:
        """获取所有持仓的层级状态"""
        result = []
        try:
            positions = self.executor.get_positions() if self.executor else []
            for pos in positions:
                symbol = pos.get('symbol', '')
                if not symbol:
                    continue
                tier = self._get_position_tier(symbol)
                avg_price = pos.get('avg_price', 0)
                current_price = pos.get('current_price', 0)
                float_pnl_pct = (current_price - avg_price) / avg_price * 100 if avg_price > 0 else 0

                result.append({
                    'symbol': symbol,
                    'tier': tier,
                    'avg_price': round(avg_price, 2),
                    'current_price': round(current_price, 2),
                    'float_pnl_pct': round(float_pnl_pct, 2),
                    'today_adds': self.today_adds.get(symbol, 0),
                    'next_tier': self._get_next_tier(tier, float_pnl_pct / 100 if float_pnl_pct > 1 else float_pnl_pct),
                })
        except Exception as e:
            logger.warning(f"[加仓] get_tier_status 异常: {e}", exc_info=True)
        return {
            'positions': result,
            'total_notifications': len(self.notifications),
            'running': self.running,
        }

    def _get_next_tier(self, current_tier: str, float_pnl_pct: float) -> Optional[str]:
        """计算下一个可达层级"""
        if current_tier == 'sprint':
            return None
        if float_pnl_pct >= 0.03:
            return 'sprint'
        if float_pnl_pct >= 0.01 and current_tier == 'probe':
            return 'confirm'
        return None


# ── 全局单例 ──
_monitor_instance: Optional[PositionTierMonitor] = None
_monitor_lock = threading.Lock()


def get_position_tier_monitor(executor=None, interval_seconds: int = 33) -> PositionTierMonitor:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = PositionTierMonitor(
                executor=executor, interval_seconds=interval_seconds
            )
        elif executor is not None and _monitor_instance.executor is None:
            _monitor_instance.executor = executor
        return _monitor_instance


def start_tier_monitor(executor=None) -> bool:
    monitor = get_position_tier_monitor(executor=executor)
    return monitor.start()


def stop_tier_monitor() -> None:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is not None:
            _monitor_instance.stop()


def get_tier_status() -> Dict[str, Any]:
    monitor = get_position_tier_monitor()
    return monitor.get_tier_status()


def get_tier_notifications(since: Optional[str] = None) -> List[dict]:
    monitor = get_position_tier_monitor()
    return monitor.get_notifications(since=since)
