# -*- coding: utf-8 -*-
"""
AI 交易回测引擎 - 模拟真实交易日，调用 DeepSeek 做决策，沙盒交易。
每个交易日模拟完整的调度任务流程，记录全部细节到 PostgreSQL。
"""
import asyncio
import json
import math
import uuid
import logging
import sys
import threading
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List, Callable, Any


from app.config import get_settings
from app.database import SessionLocal
from app.models.backtest_orm import (
    BacktestTask, BacktestDailyLog, BacktestTrade,
    BacktestPosition, BacktestEquitySnapshot, BacktestMonthlyMetric,
)
from app.services.local_data_provider import local_data
from app.core.trading.backtest_paper import BacktestPaperEngine

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter('[Engine] %(message)s'))
    logger.addHandler(_h)
    logger.propagate = False  # 避免重复输出


# ── 交易阶段定义（按真实交易日时间线） ──
TRADING_PHASES = [
    {"phase": "pre_market", "time": "09:00", "label": "盘前分析", "pi": False},
    {"phase": "market_scan_1", "time": "09:35", "label": "早盘扫描+上游建仓", "pi": True},
    {"phase": "market_scan_2", "time": "09:53", "label": "盘中扫描+中游跟进", "pi": True},
    {"phase": "market_scan_3", "time": "10:35", "label": "午前扫描+趋势确认", "pi": True},
    {"phase": "market_scan_4", "time": "13:35", "label": "午后扫描+修正建仓", "pi": True},
    {"phase": "market_scan_5", "time": "14:30", "label": "尾盘扫描+止盈止损", "pi": True},
    {"phase": "daily_review", "time": "16:00", "label": "每日复盘", "pi": True},
]


class BacktestEngine:
    """AI 交易回测引擎"""

    def __init__(self):
        self.settings = get_settings()
        self._cancel_flags: Dict[str, bool] = {}
        self._engines: Dict[str, BacktestPaperEngine] = {}  # task_id -> engine

    # ── Public API ──

    def cancel_task(self, task_id: str):
        self._cancel_flags[task_id] = True

    def is_cancelled(self, task_id: str) -> bool:
        return self._cancel_flags.get(task_id, False)

    # ── 交易日计算 ──

    def get_trade_days(self, start: date, end: date) -> List[date]:
        """获取日期范围内的交易日列表（排除周末）"""
        days = []
        current = start
        while current <= end:
            if current.weekday() < 5:  # Mon-Fri
                days.append(current)
            current += timedelta(days=1)
        return days

    # ── 主回测流程 ──

    async def run(self, task_id: str, start_date: date, end_date: date,
                  initial_capital: float, on_event: Callable = None,
                  include_chinext: bool = True):
        """运行回测，进度写入全局 _stream_queues[task_id]"""
        print(f"\n[Engine] ====== START {task_id[:8]} {start_date}~{end_date} ======", flush=True)
        logger.info(f"START {task_id[:8]} {start_date}~{end_date}")
        from app.api.backtest import _stream_queues

        async def emit(event_type: str, message: str = "", progress: float = 0, data: dict = None):
            payload = {"event": event_type, "message": message, "progress": progress, "data": data or {}}
            # 推送全局队列（SSE 消费者）
            q = _stream_queues.get(task_id)
            if q:
                await q.put(payload)
            # 同时回调（兼容旧逻辑）
            if on_event:
                await on_event(payload)

        db = SessionLocal()
        try:
            # ── 防重入 / 僵尸清理 ──
            task = db.query(BacktestTask).filter(BacktestTask.id == task_id).first()
            if not task:
                return
            # 检查是否是超时僵尸任务（上次更新 > 10 分钟前）
            if task.status == "running" and task.started_at:
                stale_seconds = (datetime.now() - task.started_at).total_seconds()
                if stale_seconds > 600 and task.progress < 5:
                    logger.warning(f"[Engine] 任务 {task_id} 疑似僵尸（{stale_seconds:.0f}s），重置状态")
                    task.status = "failed"
                    task.error_message = f"进程重启，上次运行已失效"
                    task.completed_at = datetime.now()
                    db.commit()
                    return
            if task.status == "running":
                logger.warning(f"[Engine] 任务 {task_id} 已在运行，跳过重复启动")
                return
            task.status = "running"
            task.started_at = datetime.now()
            db.commit()

            await emit("status", "回测引擎启动中...", 0)
            await asyncio.sleep(0.01)  # 让 SSE 先 flush

            # ── 获取交易日列表 ──
            trade_days = self.get_trade_days(start_date, end_date)
            total_days = len(trade_days)
            task.total_days = total_days
            task.completed_days = 0
            db.commit()

            await emit("log", f"共 {total_days} 个交易日", 1,
                       {"trade_days": [d.isoformat() for d in trade_days[:5]]})

            # ── 初始化 PaperTradingEngine（真实佣金/持仓计算） ──
            account = BacktestPaperEngine(task_id, initial_capital)
            self._engines[task_id] = account  # 供沙盒 API 同步
            daily_returns = []
            # 动态坐标起点：首日收盘后的总资产 = 100%（后续 % 都是相对首日）
            dynamic_baseline = None
            # 前一日总资产（用于计算当日收益率）
            prev_total_asset = None

            await emit("log", f"沙盒账户初始化完成 - 初始资金: ¥{initial_capital:,.0f}", 2)

            # ── 初始化数据源（极轻量，仅加载股票列表，数据按需读取） ──
            await emit("status", "正在准备本地数据源...", 3)
            local_data.load(start_date, end_date, include_chinext=include_chinext)
            symbols = local_data._loaded_symbols
            await emit("log", f"数据源就绪: {len(symbols)} 只标的 (懒加载模式, "
                       f"创业板={'含' if include_chinext else '不含'}), "
                       f"{total_days} 个交易日", 5)

            # ── 逐日模拟 ──
            for day_idx, trade_date in enumerate(trade_days):
                await asyncio.sleep(0)  # 每个交易日前 yield
                if self.is_cancelled(task_id):
                    await emit("status", "回测已取消", 100)
                    task.status = "cancelled"
                    db.commit()
                    return

                base_progress = 6 + int((day_idx / total_days) * 89)
                date_str = trade_date.isoformat()

                task.current_day = trade_date
                task.completed_days = day_idx + 1
                task.progress = base_progress
                db.commit()

                # 同步 T+1 上下文：每次日循环开始前告知 account 当前模拟日
                account.set_current_date(trade_date)

                await emit("status",
                           f"[{day_idx+1}/{total_days}] {date_str} 交易进行中...",
                           base_progress)

                # 获取当日日线行情 (DataFrame，高效)
                logger.info(f"[Engine] 查询 {date_str} 日线...")
                day_df = local_data.get_all_daily_quotes(trade_date)
                logger.info(f"[Engine] {date_str} 日线: {len(day_df) if day_df is not None and not day_df.empty else 0} 条")
                if day_df is None or day_df.empty:
                    await emit("log", f"[{date_str}] 无本地行情数据，跳过", base_progress)
                    continue

                # ── 盘前构建板块资金流背景（当日所有 Pi 阶段共享） ──
                prev_td = trade_date - timedelta(days=1)
                while prev_td.weekday() >= 5:
                    prev_td -= timedelta(days=1)
                prev_date_str = prev_td.isoformat()
                sector_context = self._build_sector_context(prev_td)

                # ── 保存 pre_market 日志 ──
                db.add(BacktestDailyLog(
                    task_id=task_id, trade_date=trade_date,
                    day_index=day_idx + 1, phase="pre_market",
                    phase_time="09:00", event_type="scan_report",
                    content=f"盘前分析 | {prev_date_str} 板块资金流:\n{sector_context}",
                    metadata_json={"prev_date": prev_date_str, "sector": sector_context},
                ))
                db.commit()

                await emit("log", f"[{date_str}] 盘前分析完成 ({prev_date_str} 板块资金流)", base_progress)

                # ── 前日 DataFrame（市场概况用） ──
                prev_day_df = local_data.get_all_daily_quotes(prev_td)

                # ── 执行各 Pi 阶段（跳过 pre_market，那是纯数据准备） ──
                for phase_cfg in TRADING_PHASES:
                    await asyncio.sleep(0)  # 让出 event loop，避免阻塞 HTTP 请求
                    if self.is_cancelled(task_id):
                        return

                    phase_id = phase_cfg["phase"]
                    phase_label = phase_cfg["label"]

                    if not phase_cfg["pi"]:
                        continue  # 跳过 pre_market（已处理）

                    await emit("log",
                               f"[{date_str} {phase_cfg['time']}] {phase_label}",
                               base_progress)

                    # 分钟行情：仅取持仓标的
                    t_parts = phase_cfg["time"].split(":")
                    t_h, t_m = int(t_parts[0]), int(t_parts[1])
                    held_syms = [p["symbol"] for p in account.get_positions()]
                    minute_quotes = local_data.get_minute_quotes_for_held(
                        trade_date, t_h, t_m, held_syms
                    )

                    # ── 构建增量扫描报告（模拟真实 market_scan.py，Pi 可通过工具读取） ──
                    # ⚠️ 反未来函数: 涨跌家数传 prev_day_df (前日),不传 day_df (当日全量)
                    scan_report = self._build_scan_report(
                        date_str, phase_cfg["time"], day_df,
                        prev_day_df, minute_quotes, sector_context, account,
                    )
                    db.add(BacktestDailyLog(
                        task_id=task_id, trade_date=trade_date,
                        day_index=day_idx + 1, phase=phase_id,
                        phase_time=phase_cfg["time"], event_type="scan_report",
                        content=scan_report,
                        metadata_json={
                            "phase_time": phase_cfg["time"],
                            "held_count": len(held_syms),
                            "account_summary": account.get_account(),
                        },
                    ))
                    db.commit()

                    await self._run_pi_phase(
                        db, task_id, trade_date, day_idx + 1,
                        phase_id, phase_label, phase_cfg["time"],
                        account, day_df, prev_day_df, minute_quotes,
                        held_syms, sector_context, emit, base_progress,
                    )

                    # ── 更新持仓市值（从 DataFrame 按需取） ──
                    self._update_positions_market_value_df(account, day_df)

                # ── 每日收盘快照 ──
                self._update_positions_market_value_df(account, day_df)
                # 传入 day_df 让 get_account 用当前价算 position_value + 浮盈
                acc = account.get_account(day_df=day_df)

                # ── 盘中最大回撤估算 (用分钟数据找当日持仓最低价) ──
                intraday_low_equity = acc["total_asset"]  # 默认: 收盘价=最低
                intraday_drawdown_pct = 0.0
                try:
                    positions = account.get_positions()
                    if positions:
                        min_equity = acc["available_cash"] + acc["frozen_cash"]  # 现金不变
                        for p in positions:
                            sym = p["symbol"]
                            vol = p["volume"]
                            if vol <= 0:
                                continue
                            # 取当日分钟最低价 (09:30-15:00)
                            day_low = local_data.get_intraday_low(sym, trade_date)
                            if day_low is None:
                                day_low = float(day_df.loc[sym, "close"]) if sym in day_df.index else p["avg_cost"]
                            min_equity += day_low * vol
                        if min_equity > 0 and min_equity < acc["total_asset"]:
                            intraday_low_equity = round(min_equity, 2)
                            intraday_drawdown_pct = round((1 - min_equity / acc["total_asset"]) * 100, 4)
                except Exception:
                    pass  # 静默降级, 不影响主流程

                # 动态坐标起点：首日总资产 = 100（资产指数）→ 后续天的指数 = 当日总资产/首日总资产×100
                # 视觉效果: 起点100,涨到110表示盈利10%,跌到95表示亏5%
                if dynamic_baseline is None:
                    dynamic_baseline = acc["total_asset"]
                baseline_index = (acc["total_asset"] / dynamic_baseline) * 100 if dynamic_baseline > 0 else 100
                daily_return = (acc["total_asset"] / initial_capital - 1) * 100
                # 不含浮盈的总资产 = 现金 + 成本市值（剔除浮盈影响，看策略纯粹收益）
                cost_based_asset = acc["available_cash"] + acc["frozen_cash"] + acc["cost_value"]
                cost_based_return = (cost_based_asset / initial_capital - 1) * 100
                # 当日收益率 = (今日 - 昨日) / 昨日 * 100
                if prev_total_asset is not None and prev_total_asset > 0:
                    daily_pct = round((acc["total_asset"] / prev_total_asset - 1) * 100, 4)
                else:
                    daily_pct = 0.0  # 首日没有"昨日"
                prev_total_asset = acc["total_asset"]
                daily_returns.append({
                    "date": date_str,
                    "return": daily_return,
                    "baseline_return": round(baseline_index, 4),
                    "cost_based_return": round(cost_based_return, 4),
                    "asset": acc["total_asset"],
                    "float_pnl": acc.get("float_pnl", 0),
                    "cost_value": acc.get("cost_value", 0),
                    "cost_based_asset": round(cost_based_asset, 2),
                    "daily_pct": daily_pct,
                })

                snapshot = BacktestEquitySnapshot(
                    task_id=task_id,
                    trade_date=trade_date,
                    total_asset=acc["total_asset"],
                    available_cash=acc["available_cash"],
                    position_value=acc["position_value"],
                    cost_value=acc.get("cost_value", 0),
                    float_pnl=acc.get("float_pnl", 0),
                    cost_based_asset=round(cost_based_asset, 2),
                    cost_based_return=round(cost_based_return, 4),
                    daily_pct=daily_pct,
                    daily_return=round(daily_return, 4),
                    cumulative_return=round(daily_return, 4),
                    baseline_return=round(baseline_index, 4),
                    intraday_low_equity=intraday_low_equity,
                    intraday_drawdown_pct=intraday_drawdown_pct,
                )
                db.add(snapshot)

                # 保存持仓快照
                for p in account.get_positions():
                    try:
                        cur = float(day_df.loc[p["symbol"], "close"])
                    except KeyError:
                        cur = p["avg_cost"]
                    mv = cur * p["volume"]
                    db.add(BacktestPosition(
                        task_id=task_id, trade_date=trade_date,
                        symbol=p["symbol"], volume=p["volume"],
                        avg_cost=round(p["avg_cost"], 3),
                        current_price=round(cur, 3),
                        market_value=round(mv, 2),
                        float_pnl=round(mv - p["avg_cost"] * p["volume"], 2),
                        float_pnl_pct=round((cur / p["avg_cost"] - 1) * 100 if p["avg_cost"] > 0 else 0, 4),
                    ))

                db.commit()

                log_entry = BacktestDailyLog(
                    task_id=task_id, trade_date=trade_date, day_index=day_idx + 1,
                    phase="close", phase_time="15:00", event_type="equity",
                    content=f"收盘 - 总资产: ¥{acc['total_asset']:,.0f} | "
                            f"浮盈: {acc.get('float_pnl', 0):+,.0f} | "
                            f"收益率: {daily_return:+.2f}% (资产指数 {baseline_index:.2f}) | "
                            f"持仓: {acc['position_count']}只",
                    metadata_json=acc,
                )
                db.add(log_entry)
                db.commit()

                await emit("equity", "", base_progress, {
                    "date": date_str,
                    "total_asset": round(acc["total_asset"], 2),
                    "cost_based_asset": round(cost_based_asset, 2),
                    "cash": round(acc["available_cash"], 2),
                    "position_value": round(acc["position_value"], 2),
                    "cost_value": round(acc.get("cost_value", 0), 2),
                    "float_pnl": round(acc.get("float_pnl", 0), 2),
                    "return_pct": round(daily_return, 2),
                    "cost_based_return": round(cost_based_return, 2),
                    "daily_pct": daily_pct,
                    "baseline_return": round(baseline_index, 2),
                    "intraday_low_equity": intraday_low_equity,
                    "intraday_drawdown_pct": intraday_drawdown_pct,
                })

            # ── 计算月度指标 ──
            await emit("status", "正在计算绩效指标...", 95)
            self._calculate_monthly_metrics(db, task_id, daily_returns)

            # ── 计算总体指标 ──
            acc_final = account.get_account()
            total_return = (acc_final["total_asset"] / initial_capital - 1) * 100
            total_trades = len(account._engine.trade_log) if hasattr(account._engine, 'trade_log') else 0
            win_trades = 0
            win_rate = 0

            # 年化收益
            years = total_days / 252 if total_days > 0 else 1
            annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0

            # 最大回撤
            peak = 1.0
            max_dd = 0.0
            for dr in daily_returns:
                cum = 1 + dr["return"] / 100
                if cum > peak:
                    peak = cum
                dd = (peak - cum) / peak * 100
                if dd > max_dd:
                    max_dd = dd

            # 夏普比率
            rets = [d["return"] for d in daily_returns]
            if len(rets) > 1:
                mean_ret = sum(rets) / len(rets)
                var = sum((r - mean_ret) ** 2 for r in rets) / (len(rets) - 1)
                std = math.sqrt(var) if var > 0 else 1
                sharpe = (mean_ret / std) * math.sqrt(252 / total_days * 252) if total_days > 0 else 0
            else:
                sharpe = 0

            # ── 完成 ──
            task.status = "completed"
            task.completed_at = datetime.now()
            task.progress = 100
            db.commit()

            metrics = {
                "total_return": round(total_return, 2),
                "annual_return": round(annual_return, 2),
                "max_drawdown": round(max_dd, 2),
                "sharpe_ratio": round(sharpe, 3),
                "win_rate": round(win_rate, 1),
                "total_trades": total_trades,
                "final_equity": round(acc_final["total_asset"], 2),
                "total_commission": round(account.total_commission, 2),
                "total_days": total_days,
            }

            await emit("complete",
                       f"回测完成! 总收益: {total_return:+.2f}% | "
                       f"年化: {annual_return:+.2f}% | 最大回撤: {max_dd:.2f}% | 夏普: {sharpe:.3f}",
                       100, {"metrics": metrics})

        except Exception as e:
            logger.error(f"Backtest engine error: {e}", exc_info=True)
            task = db.query(BacktestTask).filter(BacktestTask.id == task_id).first()
            if task:
                task.status = "failed"
                task.error_message = str(e)
                task.completed_at = datetime.now()
                db.commit()
            await emit("error", f"回测失败: {str(e)}", 0)
        finally:
            db.close()
            self._cancel_flags.pop(task_id, None)
            self._accounts.pop(task_id, None)

    # ── 板块资金流背景 ──

    def _build_sector_context(self, prev_trade_date: date) -> str:
        """构建前日板块资金流背景（盘前阶段调用，同日 Pi 阶段复用）"""
        lines = []
        mf = local_data.get_market_flow(prev_trade_date)
        if mf:
            lines.append(f"前日全市场主力净流入: {mf['net_amount']:+.1f}亿 | "
                         f"超大单: {mf['buy_elg']:.1f}亿 | 上证: {mf['close_sh']:.0f}({mf['pct_sh']:+.2f}%)")

        industries = local_data.get_industry_flow(prev_trade_date, top_n=6)
        if industries:
            lines.append("前日行业资金流入 TOP6:")
            for ind in industries:
                lines.append(f"  {ind['name']}: 净{ind['net_amount']:+.1f}亿 | 涨跌{ind['pct_change']:+.2f}%")

        concepts = local_data.get_concept_flow(prev_trade_date, top_n=6)
        if concepts:
            lines.append("前日概念资金流入 TOP6:")
            for c in concepts:
                lines.append(f"  {c['name']}: 净{c['net_amount']:+.1f}亿 | {c['pct_change']:+.2f}% | 龙头: {c['lead_stock']}")

        return "\n".join(lines) if lines else "(无板块数据)"

    # ── 盘中扫描报告（模拟真实 market_scan.py，每个 Pi 阶段生成一份） ──

    def _build_scan_report(self, date_str: str, phase_time: str, day_df,
                           prev_day_df, minute_quotes: dict,
                           sector_context: str,
                           account: BacktestPaperEngine) -> str:
        """构建增量扫描报告

        ⚠️ 反未来函数:
          - 涨跌家数(2868涨/2464跌) 必须用 prev_day_df (前日收盘), 不能用 day_df
          - day_df 是当日全量收盘后才知道的数据,在 09:35 等盘中阶段使用 = 未来函数
          - 持仓实时行情用 day_df 取 pre_close (昨收) 是 OK 的,因为昨收是已知数据
        """
        lines = [f"=== 盘中扫描报告 {date_str} {phase_time} ===", ""]
        s = account.get_account()
        lines.append(f"[账户] 总资产: ¥{s['total_asset']:,.0f} | "
                     f"可用: ¥{s['available_cash']:,.0f} | 持仓: {s['position_count']}只 | "
                     f"收益率: {s['return_pct']:+.2f}%")

        # 市场概况: 必须用前日数据 (反未来函数)
        if prev_day_df is not None and not prev_day_df.empty:
            chg = prev_day_df["pct_chg"]
            # 提取 prev_day_df 的日期 (MultiIndex 第一级 = trade_date)
            try:
                if prev_day_df.index.nlevels >= 1:
                    prev_date_val = prev_day_df.index.get_level_values(0)[0]
                    prev_label = str(pd.Timestamp(prev_date_val))[:10]
                else:
                    prev_label = "前日"
            except Exception:
                prev_label = "前日"
            lines.append(f"[市场-前日{prev_label}收盘] {len(prev_day_df)}标 | "
                         f"{int((chg>0).sum())}涨/{int((chg<0).sum())}跌 | "
                         f"均幅 {float(chg.mean()):+.2f}%")
        elif day_df is not None and not day_df.empty:
            # 兜底: 早期数据无前日, 标记为"数据缺失"避免误用当日数据
            lines.append(f"[市场] (前日数据缺失,涨跌家数未知 - 防未来函数)")

        # 持仓实时行情 (用 day_df 取 pre_close 是允许的,昨收为已知数据)
        if minute_quotes:
            lines.append(f"\n[持仓实时行情 {phase_time}]")
            for sym, q in list(minute_quotes.items())[:10]:
                try:
                    pre = float(day_df.loc[sym, "pre_close"])
                    chg_val = (q["close"] - pre) / pre * 100 if pre > 0 else 0
                except (KeyError, Exception):
                    chg_val = 0
                lines.append(f"  {sym}: {q['close']:.2f} ({chg_val:+.2f}%)")

        # 板块资金流（当日复用盘前的）
        lines.append(f"\n[板块资金流]")
        lines.append(sector_context[:500] if sector_context else "(暂无)")

        return "\n".join(lines)

    # ── 市场数据摘要 ──

    def _build_market_summary_df(self, date_str: str, day_df, prev_day_df,
                                  minute_quotes: dict, phase_time: str) -> str:
        """构建市场摘要。仅提供整体概况，不推荐具体标的（Pi 自行选股）"""
        lines = [f"## {date_str} 市场行情 (模拟时刻: {phase_time})"]

        # 前日整体概况
        if prev_day_df is not None and not prev_day_df.empty:
            chg = prev_day_df["pct_chg"]
            gainers_n = int((chg > 0).sum())
            losers_n = int((chg < 0).sum())
            avg_chg = float(chg.mean())
            lines.append(f"- 前日概况: {gainers_n}涨/{losers_n}跌 | 均幅: {avg_chg:+.2f}%")
        else:
            lines.append("- (无前日数据)")

        # 当日盘中实时行情（仅持仓标的，不让 Pi 依赖预选股）
        if minute_quotes:
            lines.append(f"\n### 持仓实时行情 ({phase_time}, +0~3min)")
            for sym, q in list(minute_quotes.items())[:10]:
                try:
                    pre = float(day_df.loc[sym, "pre_close"]) if day_df is not None else None
                    if pre is None and prev_day_df is not None:
                        pre = float(prev_day_df.loc[sym, "close"])
                except KeyError:
                    pre = q["close"]
                chg_val = (q["close"] - pre) / pre * 100 if pre and pre > 0 else 0
                lines.append(f"  {sym}: {q['close']:.2f} ({chg_val:+.2f}%) | {q.get('time','--')}")
        else:
            lines.append("\n(无持仓)")

        return "\n".join(lines)

    # ── Pi 决策阶段 ──

    async def _run_pi_phase(self, db, task_id: str, trade_date: date, day_idx: int,
                            phase_id: str, phase_label: str, phase_time: str,
                            account, day_df, prev_day_df,
                            minute_quotes: dict, held_syms: List[str],
                            sector_context: str, emit, progress: float):
        """执行 Pi AI 决策阶段"""

        date_str = trade_date.isoformat()

        # ── 构建市场摘要（前日板块资金流 + 当日概况 + 持仓行情） ──
        market_summary = (
            f"## 盘前板块资金流 ({date_str})\n{sector_context}\n\n"
            + self._build_market_summary_df(
                date_str, day_df, prev_day_df, minute_quotes, phase_time,
            )
        )

        # ── 构建账户状态（从 DataFrame 按需取价） ──
        account_summary = self._build_account_summary_df(account, day_df)

        # ── 构建交易指令 ──
        trade_instruction = self._get_trade_instruction(phase_id)

        # ── 构建 Pi 提示词 ──
        prompt = self._build_pi_prompt(
            date_str, phase_label, trade_instruction,
            market_summary, account_summary,
        )

        # ── 保存扫描报告日志 ──
        log_entry = BacktestDailyLog(
            task_id=task_id, trade_date=trade_date, day_index=day_idx,
            phase=phase_id, phase_time=phase_time,
            event_type="scan_report" if "scan" in phase_id else "pi_analysis",
            content=market_summary[:500],
            metadata_json={"market_summary": market_summary, "account": account_summary},
        )
        db.add(log_entry)
        db.commit()

        await emit("log",
                   f"[{date_str} {phase_time}] 已生成市场分析报告，正在请求 AI 决策...",
                   progress)

        # ── 构建完整消息（含 backtest 上下文 + 时分） ──
        full_prompt = self._build_full_prompt(task_id, trade_date, phase_id, phase_time, prompt)

        # ── 调用 Pi Server (backtest 模式) ──
        try:
            pi_reply = await self._call_pi_server(task_id, trade_date, full_prompt)
        except Exception as e:
            await emit("log", f"[{date_str} {phase_time}] Pi Server 调用失败: {e}", progress)
            log_entry = BacktestDailyLog(
                task_id=task_id, trade_date=trade_date, day_index=day_idx,
                phase=phase_id, phase_time=phase_time,
                event_type="error", content=f"Pi调用失败: {e}",
                metadata_json={"prompt_snapshot": full_prompt},
            )
            db.add(log_entry)
            db.commit()
            return

        # ── 保存 Pi 报告（含完整 prompt + reply 快照） ──
        log_entry = BacktestDailyLog(
            task_id=task_id, trade_date=trade_date, day_index=day_idx,
            phase=phase_id, phase_time=phase_time,
            event_type="pi_trade",
            content=pi_reply[:1000],
            metadata_json={"full_reply": pi_reply, "prompt_snapshot": full_prompt},
        )
        db.add(log_entry)
        db.commit()

        await emit("log",
                   f"[{date_str} {phase_time}] Pi 决策完成 ({len(pi_reply)} 字符)",
                   progress)

        # ── 推送 Pi 报告到前端 ──
        await emit("pi_report", pi_reply[:2000], progress,
                   {"date": date_str, "phase": phase_label, "phase_time": phase_time,
                    "full_length": len(pi_reply)})

        # ── 更新持仓市值（DataFrame 按需取价） ──
        self._update_positions_market_value_df(account, day_df)

    # ── 市场数据摘要 ──

    def _build_market_summary(self, date_str: str, quotes: dict, day_data: dict) -> str:
        """构建市场数据摘要文本"""
        lines = [f"## {date_str} 市场行情"]
        index_change = day_data.get("index_change", 0)
        lines.append(f"- 上证指数: {index_change:+.2f}%")
        lines.append(f"- 覆盖标的: {len(quotes)}只")

        gainers = []
        losers = []
        for sym, q in quotes.items():
            chg = q.get("change_pct", 0)
            if chg > 0:
                gainers.append((sym, chg))
            elif chg < 0:
                losers.append((sym, chg))

        gainers.sort(key=lambda x: -x[1])
        losers.sort(key=lambda x: x[1])

        if gainers:
            lines.append(f"\n涨幅前列:")
            for sym, chg in gainers[:5]:
                lines.append(f"  {sym}: {chg:+.2f}%")
        if losers:
            lines.append(f"\n跌幅前列:")
            for sym, chg in losers[:5]:
                lines.append(f"  {sym}: {chg:+.2f}%")

        return "\n".join(lines)

    # ── 账户状态 ──

    def _build_account_summary_df(self, account: BacktestPaperEngine, day_df) -> str:
        s = account.get_account()
        lines = [
            f"总资产: ¥{s['total_asset']:,.0f}",
            f"可用资金: ¥{s['available_cash']:,.0f}",
            f"持仓市值: ¥{s['position_value']:,.0f}",
            f"持仓数量: {s['position_count']}只",
            f"累计收益率: {s['return_pct']:+.2f}%",
        ]
        positions = account.get_positions()
        if positions:
            lines.append("\n当前持仓:")
            for p in positions:
                sym = p["symbol"]
                try:
                    cur_price = float(day_df.loc[sym, "close"])
                except KeyError:
                    cur_price = p["avg_cost"]
                pnl_pct = (cur_price / p["avg_cost"] - 1) * 100 if p["avg_cost"] > 0 else 0
                lines.append(f"  {sym}: {p['volume']}股 | "
                             f"成本 {p['avg_cost']:.2f} | 现价 {cur_price:.2f} | "
                             f"盈亏 {pnl_pct:+.2f}%")
        else:
            lines.append("\n当前无持仓")
        return "\n".join(lines)

    # ── 交易指令 ──

    def _get_trade_instruction(self, phase_id: str) -> str:
        """根据阶段获取交易指令"""
        instructions = {
            "market_scan_1":
                "这是早盘建仓窗口。重点：\n"
                "1. 分析盘前数据和当日开盘走势\n"
                "2. 寻找强势标的，优先选择涨幅居前且成交量放大的标的\n"
                "3. 使用不超过 40% 的可用资金建仓\n"
                "4. 单只标的仓位不超过总资产的 15%",
            "market_scan_2":
                "这是盘中跟进窗口。重点：\n"
                "1. 确认已建仓标的走势是否站稳\n"
                "2. 对不符合预期的持仓考虑止损\n"
                "3. 如有强势标的出现，可追加建仓\n"
                "4. 使用不超过 30% 的可用资金操作",
            "market_scan_3":
                "这是午前确认窗口。重点：\n"
                "1. 评估上午整体走势\n"
                "2. 趋势确认的可加仓，趋势走坏的要减仓\n"
                "3. 使用不超过 30% 的可用资金",
            "market_scan_4":
                "这是午后调整窗口。重点：\n"
                "1. 关注下午开盘方向\n"
                "2. 有把握的标的可新建仓\n"
                "3. 使用不超过 30% 的可用资金",
            "market_scan_5":
                "这是尾盘窗口。重点：\n"
                "1. **只卖不买** - 严格禁止新开仓\n"
                "2. 逐只检查持仓，止损位触发则卖出\n"
                "3. 趋势破位的减仓\n"
                "4. 为明日留出充足的现金仓位",
            "daily_review":
                "这是每日复盘。请基于今日交易数据进行总结分析，不需要执行新的交易。",
        }
        return instructions.get(phase_id, "请基于市场数据分析并做出交易决策。")

    # ── Pi 提示词 ──

    def _build_pi_prompt(self, date_str: str, phase_label: str,
                         trade_instruction: str, market_summary: str,
                         account_summary: str) -> str:
        return (
            f"你是 Marcus AI 交易系统，一个专业的A股右侧交易专家。\n\n"
            f"当前模拟日期: {date_str}\n"
            f"当前阶段: {phase_label}\n\n"
            f"## 交易指令\n{trade_instruction}\n\n"
            f"## 市场数据\n{market_summary}\n\n"
            f"## 账户状态\n{account_summary}\n\n"
            f"请基于以上数据做出交易决策。\n\n"
            f"在回复的末尾，请在单独一行给出你的交易指令，格式为:\n"
            f"TRADE: BUY <symbol> <volume>股 @ <price> REASON: <理由>\n"
            f"TRADE: SELL <symbol> <volume>股 @ <price> REASON: <理由>\n"
            f"或: TRADE: HOLD REASON: <理由>\n\n"
            f"约束:\n"
            f"- symbol 格式为 000001.SZ 或 600519.SH\n"
            f"- price 使用当日收盘价（已提供）\n"
            f"- volume 为股数（整数，100的倍数）\n"
            f"- 严格遵守风控纪律，单只股票仓位不超过15%"
        )

    # ── Pi Server 调用 ──

    def _build_full_prompt(self, task_id: str, trade_date, phase_id: str,
                           phase_time: str, base_prompt: str) -> str:
        """构建发送给 Pi Server 的完整消息（[BKT:...] 前缀激活工具回测路由）"""
        date_str = trade_date.isoformat() if hasattr(trade_date, 'isoformat') else str(trade_date)
        # [BKT:task_id|YYYY-MM-DD|HH:MM] 前缀会被 Pi Server 解析
        # Pi 看不到此前缀，工具用其中时分做分钟级快照
        context_prefix = f"[BKT:{task_id}|{date_str}|{phase_time}] "
        return context_prefix + base_prompt

    async def _call_pi_server(self, task_id: str, trade_date, full_prompt: str) -> str:
        """调用 Pi Server（线程池 + 3 次重试，避免启动时序问题）"""
        import urllib.request
        import ssl as _ssl

        pi_url = self.settings.PI_SERVER_URL
        date_str = trade_date.isoformat() if hasattr(trade_date, 'isoformat') else str(trade_date)
        session_id = f"backtest_{task_id}_{date_str.replace('-', '')}"

        logger.info(f"[Engine] 发送 Pi 请求: {len(full_prompt)} 字符 → {pi_url}")

        last_error = None
        for attempt in range(3):
            try:
                def _sync_call():
                    payload = json.dumps({
                        "message": full_prompt, "session_id": session_id,
                        "mode": "trade",
                    }).encode("utf-8")
                    req = urllib.request.Request(
                        pi_url, data=payload,
                        headers={"Content-Type": "application/json"}, method="POST")
                    ctx = _ssl.create_default_context()
                    with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        return data.get("reply", "")

                loop = asyncio.get_event_loop()
                reply = await loop.run_in_executor(None, _sync_call)
                logger.info(f"[Engine] Pi 响应: {len(reply)} 字符")
                return reply
            except Exception as e:
                last_error = str(e)
                if attempt < 2:
                    wait = (attempt + 1) * 3
                    logger.warning(f"[Engine] Pi 调用失败 (重试 {attempt+1}/3, {wait}s 后): {last_error}")
                    await asyncio.sleep(wait)
        raise RuntimeError(f"Pi Server 3次重试均失败: {last_error}")

    def _fallback_decision(self, prompt: str) -> str:
        """无 Pi Server 时的基于规则模拟决策"""
        return (
            "根据当前市场数据分析，今日市场整体走势平稳。\n"
            "考虑到风险控制原则，当前阶段采取观望策略。\n\n"
            "TRADE: HOLD REASON: 市场数据不足或API未配置，保持当前仓位观望"
        )

    # ── 交易解析与执行 ──

    def _parse_and_execute_trades(self, db, task_id: str, trade_date: date,
                                   day_idx: int, phase_id: str, phase_time: str,
                                   pi_reply: str, account,
                                   quotes: dict, emit, progress: float) -> int:
        """解析 Pi 回复中的交易指令并执行"""
        import re
        executed = 0

        # symbol → 名称缓存 (跨多笔 buy/sell 共用)
        name_cache: Dict[str, str] = {}

        def _get_stock_name(sym: str) -> str:
            """从本地 stock_basic_data.parquet 查名称（覆盖全 A 股 5854 只）"""
            if sym in name_cache:
                return name_cache[sym]
            try:
                name = local_data.get_stock_name(sym) or ""
            except Exception:
                name = ""
            name_cache[sym] = name
            return name

        def _is_sh_stock(sym: str) -> bool:
            """判断是否沪市股票(沪市有过户费,深市免征)"""
            return sym.endswith(".SH")

        # 解析 BUY/SELL 指令
        trade_pattern = r'TRADE:\s*(BUY|SELL)\s+(\S+)\s+(\d+)\s*股?\s*@\s*([\d.]+)\s*REASON:\s*(.+)'
        for match in re.finditer(trade_pattern, pi_reply, re.IGNORECASE):
            direction = match.group(1).upper()
            symbol = match.group(2).strip()
            volume = int(match.group(3))
            signal_price = float(match.group(4))  # Pi 信号价
            reason = match.group(5).strip()

            # 获取实际行情价格 (来自该 phase 的分钟快照,无未来函数)
            q = quotes.get(symbol, {})
            actual_price = q.get("close", signal_price)

            # 滑点 (百分比, 信号价 → 实际成交价)
            slippage_pct = round((actual_price - signal_price) / signal_price * 100, 4) \
                if signal_price > 0 else 0.0

            if direction == "BUY":
                # 买入：取买入前已有的 avg_price (FIFO 成本基准)
                pre_avg_price = 0.0
                try:
                    engine_sym = account._to_engine_sym(symbol)
                    pre_pos = account._engine.positions.get(engine_sym)
                    if pre_pos and pre_pos.volume > 0:
                        pre_avg_price = float(getattr(pre_pos, "avg_price", 0))
                except Exception:
                    pass

                oid = account.buy(symbol, actual_price, volume)
                if oid:
                    amount = actual_price * volume
                    commission = round(amount * account.COMMISSION_BUY, 2)
                    # 买入无印花税,沪市有过户费(0.001%)
                    transfer_fee = round(amount * 0.00001, 2) if _is_sh_stock(symbol) else 0.0
                    stock_name = _get_stock_name(symbol)
                    db.add(BacktestTrade(
                        task_id=task_id, trade_date=trade_date,
                        symbol=symbol, stock_name=stock_name,
                        direction="buy",
                        price=actual_price, volume=volume,
                        amount=amount, commission=commission,
                        reason=reason,
                        phase_time=phase_time,
                        signal_price=signal_price,
                        actual_price=actual_price,
                        stamp_tax=0.0,
                        transfer_fee=transfer_fee,
                        slippage_pct=slippage_pct,
                        net_profit=0.0,
                    ))
                    executed += 1
            elif direction == "SELL":
                # 卖出前记录 avg_price 作为成本基准（用于算 profit）
                pre_avg_price = 0.0
                pre_volume = 0
                try:
                    engine_sym = account._to_engine_sym(symbol)
                    pre_pos = account._engine.positions.get(engine_sym)
                    if pre_pos:
                        pre_avg_price = float(getattr(pre_pos, "avg_price", 0))
                        pre_volume = int(getattr(pre_pos, "volume", 0))
                except Exception:
                    pass

                oid = account.sell(symbol, actual_price, volume)
                if oid:
                    amount = actual_price * volume
                    commission = round(amount * account.COMMISSION_SELL, 2)  # 0.15% 含 0.1% 印花税
                    # 拆分明细: 手续费 0.05% + 印花税 0.1%
                    stamp_tax = round(amount * 0.001, 2)
                    transfer_fee = round(amount * 0.00001, 2) if _is_sh_stock(symbol) else 0.0
                    # 实现毛盈亏 = (sell_price - cost_avg) * volume
                    if pre_avg_price > 0 and pre_volume > 0:
                        profit = round((actual_price - pre_avg_price) * volume, 2)
                        profit_pct = round((actual_price / pre_avg_price - 1) * 100, 4) if pre_avg_price > 0 else 0
                    else:
                        profit = 0.0
                        profit_pct = 0.0
                    # 净盈亏 = 毛盈亏 - 印花税 - 过户费 (commission 字段已含印花税,需拆)
                    net_profit = round(profit - stamp_tax - transfer_fee, 2)
                    stock_name = _get_stock_name(symbol)
                    db.add(BacktestTrade(
                        task_id=task_id, trade_date=trade_date,
                        symbol=symbol, stock_name=stock_name,
                        direction="sell",
                        price=actual_price, volume=volume,
                        amount=amount, commission=commission,
                        profit=profit, profit_pct=profit_pct,
                        reason=reason,
                        phase_time=phase_time,
                        signal_price=signal_price,
                        actual_price=actual_price,
                        stamp_tax=stamp_tax,
                        transfer_fee=transfer_fee,
                        slippage_pct=slippage_pct,
                        net_profit=net_profit,
                    ))
                    executed += 1

        if executed > 0:
            db.commit()

        return executed

    # ── 持仓市值更新 ──

    def _update_positions_market_value_df(self, account: BacktestPaperEngine, day_df):
        pass  # PaperTradingEngine 自行管理持仓，无需外部更新市值

    # ── 月度指标 ──

    def _calculate_monthly_metrics(self, db, task_id: str,
                                    daily_returns: List[dict]):
        """计算月度绩效指标

        修复:
          - 月度收益: 月末资产 / 月初资产 - 1 (不用 daily_return 累加,那是从初始的累计)
          - 交易笔数: 只算 sell (一笔完整交易 = 一次买入 + 一次卖出)
          - 胜率: 盈利 sell / (盈利+亏损) sell (排除盈亏=0 的 T+0 边界单)
          - 月内最大回撤: 按日收益率从月初开始累乘, 跟踪 peak
        """
        # 1) 按月聚合每月首末总资产 + 每日收益率
        monthly_first: Dict[str, float] = {}
        monthly_last: Dict[str, float] = {}
        monthly_daily_rets: Dict[str, list] = {}
        for dr in daily_returns:
            month = dr["date"][:7]
            asset = float(dr.get("asset") or 0)
            if asset <= 0:
                continue
            if month not in monthly_first:
                monthly_first[month] = asset
            monthly_last[month] = asset  # 末值 = 遍历最后一天的资产
            monthly_daily_rets.setdefault(month, []).append(float(dr.get("daily_pct") or 0))

        # 2) 按月聚合交易笔数 + 胜率 (sell 笔数, 盈利 sell 笔数)
        trades_by_month: Dict[str, dict] = {}
        all_trades = db.query(BacktestTrade).filter(
            BacktestTrade.task_id == task_id
        ).all()
        for t in all_trades:
            m = t.trade_date.isoformat()[:7]
            td = trades_by_month.setdefault(m, {"total": 0, "wins": 0, "losses": 0})
            if t.direction == "sell":
                td["total"] += 1
                if (t.profit or 0) > 0:
                    td["wins"] += 1
                elif (t.profit or 0) < 0:
                    td["losses"] += 1

        for month in sorted(monthly_first.keys()):
            start = monthly_first[month]
            end = monthly_last[month]
            # 月度收益 = 月末 / 月初 - 1
            month_return = (end / start - 1) * 100 if start > 0 else 0
            td = trades_by_month.get(month, {"total": 0, "wins": 0, "losses": 0})

            # 月内最大回撤 (从月初开始累乘日收益)
            peak = 1.0
            max_dd = 0.0
            cum = 1.0
            for r in monthly_daily_rets[month]:
                cum *= (1 + r / 100)
                if cum > peak:
                    peak = cum
                dd = (peak - cum) / peak * 100
                if dd > max_dd:
                    max_dd = dd

            # 胜率: 盈利 sell / (盈利+亏损) sell (排除盈亏=0)
            denom = td["wins"] + td["losses"]
            win_rate = round(td["wins"] / denom * 100, 2) if denom > 0 else 0

            db.add(BacktestMonthlyMetric(
                task_id=task_id, month=month,
                return_pct=round(month_return, 4),
                trades_count=td["total"],
                win_count=td["wins"],
                win_rate=win_rate,
                max_drawdown=round(max_dd, 4),
            ))
        db.commit()


# 全局单例
backtest_engine = BacktestEngine()
