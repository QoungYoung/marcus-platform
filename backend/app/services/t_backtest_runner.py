# -*- coding: utf-8 -*-
"""做T回测 · 任务执行器（worker 侧）：预取 → 回放 → 落库 + LLM 复核客户端。

对齐 worker_commands/worker_status 控制通道模式：API 进程写任务（pending），
worker 轮询执行；重活（预取+回放）全部在 worker，不阻塞 API。
复核模式（review_mode）：
- "llm"（默认）：POST bridge /backtest/review（回测会话沙盒，仅决策不交易），失败降级规则
- "rule"：纯规则（t_backtest._rule_review），可对照
"""
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.database import SessionLocal
from app.services.t_backtest import TBacktestEngine, caliber_notes

DATA_ROOT = Path("data/t_backtest")

# 自动选股逐日顺延上限（交易日）：窗口起始段连续无达标标的时，最多向后滚动多少个交易日
AUTO_SELECT_MAX_ROLL_DAYS = 20


# ────────────────────────────────────────────────────────────────
# DB 读写
# ────────────────────────────────────────────────────────────────

def create_task(symbol: str, start_date: str, end_date: str,
                conditions: List[Dict[str, Any]], init_shares: int = 1000,
                init_price: Optional[float] = None, net_asset: float = 200000.0,
                review_mode: str = "llm", symbols: Optional[List[str]] = None,
                build_mode: bool = False, build_limit_ratio: float = 0.55,
                select_source: str = "manual", select_limit: int = 10,
                rolling_build: bool = False,
                rolling_scan: bool = False,
                relax_mode: bool = False) -> Optional[int]:
    """创建回测任务（status=pending）。组合模式：symbols 列表或 select_source 自动选股。

    rolling_build=True：每日滚动建仓（对齐实盘 daily_auto：盘后扫描 → 次日建仓）。
    rolling_scan=True：滚动建仓 + 全市场历史扫描补充（回测版 scan_t_candidates_historical）。
    relax_mode=True：震荡市模式（仅回测）——放宽趋势闸门 + 门槛 0.72。
    """
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                """
                INSERT INTO t_backtest_tasks (
                    symbol, start_date, end_date, init_shares, init_price,
                    net_asset, review_mode, conditions_json, symbols_json,
                    build_mode, build_limit_ratio, select_source, select_limit,
                    rolling_build, rolling_scan, relax_mode
                ) VALUES (:symbol, :start, :end, :shares, :price, :asset, :mode, :conds,
                          :symbols, :build_mode, :build_limit, :sel_src, :sel_lim,
                          :rolling_build, :rolling_scan, :relax_mode)
                RETURNING id
                """
            ), {
                "symbol": symbol, "start": start_date, "end": end_date,
                "shares": init_shares, "price": init_price,
                "asset": net_asset, "mode": review_mode,
                "conds": json.dumps(conditions, ensure_ascii=False),
                "symbols": json.dumps(symbols or [], ensure_ascii=False),
                "build_mode": bool(build_mode), "build_limit": float(build_limit_ratio),
                "sel_src": select_source if select_source in ("manual", "pool", "scan") else "manual",
                "sel_lim": int(select_limit),
                "rolling_build": bool(rolling_build),
                "rolling_scan": bool(rolling_scan),
                "relax_mode": bool(relax_mode),
            }).fetchone()
            db.commit()
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-backtest] create_task 失败: {e}")
        return None


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    try:
        db = SessionLocal()
        try:
            row = db.execute(text("SELECT * FROM t_backtest_tasks WHERE id = :id"),
                             {"id": task_id}).mappings().first()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-backtest] get_task 失败: {e}")
        return None


def list_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT * FROM t_backtest_tasks ORDER BY id DESC LIMIT :lim"
            ), {"lim": limit}).mappings().all()
            return [dict(r) for r in rows]
        finally:
            db.close()
    except Exception as e:
        print(f"[t-backtest] list_tasks 失败: {e}")
        return []


def update_task_status(task_id: int, status: str, error_message: Optional[str] = None,
                       progress: Optional[int] = None) -> bool:
    try:
        db = SessionLocal()
        try:
            sets = ["status = :status"]
            params: Dict[str, Any] = {"status": status, "id": task_id}
            if error_message is not None:
                sets.append("error_message = :err")
                params["err"] = error_message
            if progress is not None:
                sets.append("progress = :progress")
                params["progress"] = progress
            if status in ("running",):
                sets.append("started_at = COALESCE(started_at, now())")
            elif status in ("completed", "failed", "cancelled"):
                sets.append("finished_at = now()")
            db.execute(text(
                f"UPDATE t_backtest_tasks SET {', '.join(sets)} WHERE id = :id"
            ), params)
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[t-backtest] update_task_status 失败: {e}")
        return False


def claim_pending_task(consumer: str = "t-backtest-worker") -> Optional[Dict[str, Any]]:
    """原子领取一条 pending 任务（防多 worker 重复执行）。"""
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                """
                UPDATE t_backtest_tasks SET status = 'running', started_at = now()
                WHERE id = (
                    SELECT id FROM t_backtest_tasks
                    WHERE status = 'pending'
                    ORDER BY id LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """
            ), {}).mappings().first()
            db.commit()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-backtest] claim_pending_task 失败: {e}")
        return None


def cancel_task(task_id: int) -> bool:
    """取消任务：仅 pending/running 可取消。"""
    return update_task_status(task_id, "cancelled", error_message="user_cancelled")


def save_events(task_id: int, events: List[Dict[str, Any]]):
    try:
        db = SessionLocal()
        try:
            for ev in events:
                ev_copy = {k: v for k, v in ev.items() if k != "data"}
                data = ev.get("data")
                if not isinstance(data, dict):
                    data = {}
                # trade_day 提取：顶层 → data.trade_day → data.trigger.trade_day
                day = (ev.get("trade_day")
                       or data.get("trade_day")
                       or (data.get("trigger") or {}).get("trade_day")
                       if isinstance(data, dict) else None)
                # bar_time 提取：顶层 → data.bar_time → data.trigger.bar_time → data.next_bar
                bt = (ev.get("bar_time")
                      or data.get("bar_time")
                      or (data.get("trigger") or {}).get("bar_time")
                      or data.get("next_bar")
                      if isinstance(data, dict) else None)
                db.execute(text(
                    "INSERT INTO t_backtest_events (task_id, event_type, trade_day, bar_time, data_json) "
                    "VALUES (:task, :etype, :day, :bt, :data)"
                ), {
                    "task": task_id, "etype": ev.get("type", ev_copy.get("type", "event")),
                    "day": day, "bt": bt,
                    "data": json.dumps(ev, ensure_ascii=False),
                })
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[t-backtest] save_events 失败: {e}")


def save_trades(task_id: int, trades: List[Dict[str, Any]]):
    try:
        db = SessionLocal()
        try:
            for t in trades:
                db.execute(text(
                    "INSERT INTO t_backtest_trades (task_id, symbol, side, price, volume, realized_pnl, fees) "
                    "VALUES (:task, :symbol, :side, :price, :volume, :pnl, :fees)"
                ), {
                    "task": task_id, "symbol": t.get("symbol"), "side": t.get("side"),
                    "price": t.get("price"), "volume": t.get("volume"),
                    "pnl": t.get("realized_pnl", 0), "fees": t.get("fees", 0),
                })
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[t-backtest] save_trades 失败: {e}")


def save_equity(task_id: int, curve: List[Dict[str, Any]]):
    try:
        db = SessionLocal()
        try:
            for p in curve:
                db.execute(text(
                    "INSERT INTO t_backtest_equity_snapshots (task_id, trade_date, total_asset, realized_pnl, position, close) "
                    "VALUES (:task, :td, :asset, :pnl, :pos, :close)"
                ), {
                    "task": task_id, "td": p.get("trade_date"),
                    "asset": p.get("total_asset"), "pnl": p.get("realized_pnl", 0),
                    "pos": p.get("position"), "close": p.get("close"),
                })
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[t-backtest] save_equity 失败: {e}")


def save_metrics(task_id: int, metrics: Dict[str, Any], notes: List[str]):
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "INSERT INTO t_backtest_metrics (task_id, metrics_json, caliber_notes) "
                "VALUES (:task, :metrics, :notes) "
                "ON CONFLICT (task_id) DO UPDATE SET metrics_json = EXCLUDED.metrics_json, "
                "caliber_notes = EXCLUDED.caliber_notes"
            ), {
                "task": task_id,
                "metrics": json.dumps(metrics, ensure_ascii=False),
                "notes": json.dumps(notes, ensure_ascii=False),
            })
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[t-backtest] save_metrics 失败: {e}")


def _loads_maybe(value: Any) -> Any:
    """JSONB 列读取兼容：psycopg2 已解析为 dict/list 时直接用，否则 json.loads。"""
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "{}")
    except (ValueError, TypeError):
        return {}


def get_metrics(task_id: int) -> Optional[Dict[str, Any]]:
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT metrics_json, caliber_notes FROM t_backtest_metrics WHERE task_id = :id"
            ), {"id": task_id}).mappings().first()
            if not row:
                return None
            return {
                "metrics": _loads_maybe(row["metrics_json"]),
                "caliber_notes": _loads_maybe(row["caliber_notes"]),
            }
        finally:
            db.close()
    except Exception as e:
        print(f"[t-backtest] get_metrics 失败: {e}")
        return None


# ────────────────────────────────────────────────────────────────
# 复核客户端
# ────────────────────────────────────────────────────────────────

def bridge_base_url() -> str:
    """bridge 基址（与 t_bridge._bridge_url 同源）。

    PI_SERVER_URL 形如 http://dsh:3001/chat（已含 /chat 路径段），
    基址 = 去掉末尾路径段 → http://dsh:3001（各端点如 /backtest/review /chat 挂在其下）。
    """
    try:
        from app.config import get_settings
        settings = get_settings()
        raw = getattr(settings, "PI_SERVER_URL", "http://127.0.0.1:3001/chat").rstrip("/")
    except Exception:
        raw = "http://127.0.0.1:3001/chat"
    # 去掉末尾路径段（/chat）得到服务基址
    scheme_sep = raw.find("://")
    if scheme_sep >= 0:
        rest = raw[scheme_sep + 3:]
        host = rest.split("/", 1)[0]
        return raw[:scheme_sep + 3] + host
    return raw.rsplit("/", 1)[0] if "/" in raw else raw


def build_review_fn(task: Dict[str, Any]) -> Optional[callable]:
    """按 review_mode 构造复核回调：
    - llm：POST bridge /backtest/review（回测会话沙盒），失败降级规则（返回 None → 引擎规则模式）
    - rule：None（引擎内 _rule_review）
    回测复核响应升级为 AI 决策语义（action: exec/wait/abandon，兼容旧 decision:auto|human）。
    """
    if task.get("review_mode", "llm") != "llm":
        return None
    url = bridge_base_url() + "/backtest/review"
    # 迭代#56c：45s→120s + 重试 1 次——消费式重建后触发增多，LLM 决策
    # （thinking 推理）时快时慢，45s 超时导致"决策异常(保守等待): timed out"
    # 高抛兑现被跳过（#62：000021 三天高抛全 wait）
    timeout = 120

    def review(rev_ctx: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "task_id": task.get("id"),
            "symbol": rev_ctx.get("trigger", {}).get("symbol") or task.get("symbol"),
            "trigger": rev_ctx.get("trigger", {}),
            "regime": rev_ctx.get("regime", {}),
            "rule_hint": rev_ctx.get("rule_hint", {}),
            "position": rev_ctx.get("position", {}),
        }
        last_err: Optional[Exception] = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except Exception as e:
                last_err = e
                print(f"[t-backtest] LLM 复核重试 {attempt + 1}/2: {e}")
        else:
            # 两次都失败：回退规则决策（保守 wait 会导致高抛兑现丢失——
            # 由引擎 _review 的异常路径处理，这里抛出让引擎兜底）
            raise last_err or TimeoutError("LLM 复核失败")
        # AI 决策动作：exec / wait / abandon / update_condition
        # （兼容旧 decision:auto→exec, human→wait）
        action = str(body.get("action") or "")
        if action not in ("exec", "wait", "abandon", "update_condition"):
            action = "exec" if body.get("decision") == "auto" else "wait"
        return {"action": action, "decision": "auto" if action == "exec" else "human",
                "reason": str(body.get("reason") or "")[:500],
                "condition": body.get("condition") if action == "update_condition" else None}

    return review


# ────────────────────────────────────────────────────────────────
# 自动选股（逐日顺延）
# ────────────────────────────────────────────────────────────────

def auto_select_symbols_rolling(select_source: str, select_limit: int,
                                start_date: str, end_date: str,
                                progress_cb: Optional[callable] = None,
                                aggregate: bool = False,
                                event_cb: Optional[callable] = None,
                                relax: bool = False) -> tuple:
    """自动选股（逐日滚动）：从回测窗口首日起按交易日顺序扫描，某日无达标标的自动顺延次日。

    用户需求：当天没有选股标的时不再直接失败，滚动到下一个有达标标的的交易日。
    progress_cb(i, n)：每扫描一个交易日回调一次（i 从 0 起），供 run_task 上报实时进度
    （自动选股可能耗时数分钟，不能让前端一直停在 0%）。
    event_cb(events)：每扫完一个交易日回调一次（list 事件，供实时落库到时间明细）。
    aggregate=True（每日滚动建仓任务）：不只在第一个有标的的交易日停下，而是继续扫完
    窗口，取全部达标标的的并集（≤ select_limit 只）——滚动引擎会逐日重打分，
    只有 1 只候选会白白浪费掉后续几天才达标的标的。

    Returns:
        (symbols, selected_start, last_err)
        - symbols: 选出的达标代码列表（空 = 全窗口无达标）
        - selected_start: 命中的交易日（YYYYMMDD）；无命中为 None
        - last_err: 扫描过程中的最后一次异常（无异常为 None，用于失败信息兜底）
    """
    from datetime import datetime as _dt, timedelta as _td
    from app.services.t_backtest_data import resolve_trade_days
    from app.services.t_build import (scan_t_candidates, _fetch_daily_bars,
                                      _quality_from_daily)

    cand_days: List[str] = []
    try:
        cand_days = resolve_trade_days(start_date, end_date)
    except Exception as e:
        print(f"[t-backtest] 交易日历获取失败: {e}")
    if not cand_days:
        # 交易日历不可用：退化为只在原起始日扫描一次（保持旧行为）
        cand_days = [str(start_date).replace("-", "")]

    source = select_source
    last_err: Optional[Exception] = None
    found_symbols: List[str] = []
    selected_start: Optional[str] = None
    total_days = len(cand_days[:AUTO_SELECT_MAX_ROLL_DAYS])
    for i, d in enumerate(cand_days[:AUTO_SELECT_MAX_ROLL_DAYS]):
        if progress_cb is not None:
            try:
                progress_cb(i, total_days)
            except Exception:
                pass
        try:
            as_of = (_dt.strptime(d, "%Y%m%d") - _td(days=1)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            as_of = None
        # 回测口径：质量分/趋势/风险全部用 as_of 历史日线（防前视，与回放引擎建仓阶段一致）。
        # 此前精筛用实时 calc_t_quality（今天/最近交易日的振幅、换手、成交额），
        # 低波动/缩量震荡市里会把历史日期全部误拒（实测 q_pass 1/28 → 历史口径 25/28）。
        def _hist_quality(sym, bars):
            return _quality_from_daily(bars)

        def _hist_bars(sym):
            return _fetch_daily_bars(sym, count=40, as_of=as_of)

        try:
            cands = scan_t_candidates(limit=select_limit, source=source, as_of=as_of,
                                      quality_override_fn=_hist_quality,
                                      bars_fn=_hist_bars,
                                      coarse_max_batches=None,  # 回测扫全市场，不抽样前300
                                      relax=relax)
        except Exception as e:
            last_err = e
            print(f"[t-backtest] 自动选股失败 as_of={as_of}: {e}（顺延下一交易日）")
            continue
        syms = [c.get("symbol") for c in cands
                if c.get("symbol") and c.get("pass_gate")]
        # 候选池空 → 降级全市场扫描（保证自动选股可用，同样用历史口径）
        if not syms and source == "pool":
            print(f"[t-backtest] as_of={as_of} 候选池为空，降级全市场扫描自动选股")
            try:
                cands = scan_t_candidates(limit=select_limit, source="scan", as_of=as_of,
                                          quality_override_fn=_hist_quality,
                                          bars_fn=_hist_bars,
                                          coarse_max_batches=None,
                                          relax=relax)
                syms = [c.get("symbol") for c in cands
                        if c.get("symbol") and c.get("pass_gate")]
            except Exception as e:
                last_err = e
                print(f"[t-backtest] 降级全市场扫描失败 as_of={as_of}: {e}（顺延下一交易日）")
        # 实时事件：本交易日扫描结果（前端时间明细从 0% 就开始追加）
        if event_cb is not None:
            try:
                event_cb([{
                    "type": "auto_select",
                    "trade_day": d,
                    "data": {
                        "as_of": as_of,
                        "source": source,
                        "scanned": len(cands),
                        "passed": len(syms),
                        "symbols": syms[:20],
                        "error": str(last_err)[:120] if last_err else "",
                    },
                }])
            except Exception:
                pass
        if syms:
            print(f"[t-backtest] 自动选股({source}) as_of={as_of} 达标 {len(syms)} 只: {syms[:10]}")
            if selected_start is None:
                selected_start = d
            for s in syms:
                if s not in found_symbols:
                    found_symbols.append(s)
            # 非滚动建仓：第一个有标的的交易日即返回（保持原语义）；
            # 滚动建仓（aggregate）：继续扫完窗口，收集并集直到满 select_limit
            if not aggregate or len(found_symbols) >= select_limit:
                return found_symbols[:select_limit], selected_start, None
        else:
            print(f"[t-backtest] 自动选股 as_of={as_of} 无达标标的，顺延下一交易日")
    if found_symbols:
        return found_symbols[:select_limit], selected_start, None
    if len(cand_days) > AUTO_SELECT_MAX_ROLL_DAYS:
        print(f"[t-backtest] 自动选股顺延已达上限 {AUTO_SELECT_MAX_ROLL_DAYS} 个交易日，放弃")
        last_err = last_err or RuntimeError(
            f"顺延已达 {AUTO_SELECT_MAX_ROLL_DAYS} 个交易日上限")
    return [], None, last_err


# ────────────────────────────────────────────────────────────────
# 执行入口（worker 调用）
# ────────────────────────────────────────────────────────────────

def run_task(task_id: int, cancel_event: Optional[Any] = None) -> Dict[str, Any]:
    """执行一个回测任务：预取 → 回放（单标的或组合）→ 落库。返回任务结果。"""
    task = get_task(task_id)
    if not task:
        return {"status": "failed", "error": "任务不存在"}

    try:
        conditions = _loads_maybe(task.get("conditions_json"))
        if not isinstance(conditions, list):
            conditions = []
    except Exception:
        conditions = []
    try:
        symbols = _loads_maybe(task.get("symbols_json"))
        if not isinstance(symbols, list):
            symbols = []
    except Exception:
        symbols = []
    build_mode = bool(task.get("build_mode", False))
    build_limit_ratio = float(task.get("build_limit_ratio") or 0.55)

    task_dir = DATA_ROOT / f"task_{task_id}"
    symbol = task["symbol"]
    start = task.get("start_date") or ""
    end = task.get("end_date") or ""

    # 组合模式：候选列表 = symbols 或 自动选股（select_source: manual/pool/scan）
    is_combined = bool(build_mode) or (len(symbols) > 0 and symbol in ("", "combined"))
    select_source = str(task.get("select_source") or ("manual" if symbols else "pool"))
    select_limit = int(task.get("select_limit") or 10)
    rolling_scan = bool(task.get("rolling_scan", False))
    relax_mode = bool(task.get("relax_mode", False))
    # rolling_scan：候选由引擎每日历史全市场扫描产生（回测版 scan_t_candidates_historical），
    # 不在此处做实时自动选股（生产 scan_t_candidates 用实时行情，有前视风险）
    if is_combined and not symbols and select_source in ("pool", "scan") and not rolling_scan:
        # 自动选股（防前视）：精筛用 as_of=窗口首日前一交易日的日线（趋势/风险历史化）。
        # 首日无达标标的时逐日顺延（用户需求：当天没有选股标的 → 滚动到下一交易日，
        # 不再直接报"自动选股无达标标的"失败）。
        def _sel_progress(i: int, n: int) -> None:
            try:
                # 自动选股阶段：2% → 8%（按扫描的交易日数均分，避免长时间停在 0%）
                pct = 2 + round(i / max(n, 1) * 6)
                update_task_status(task_id, "running", progress=min(pct, 8))
            except Exception:
                pass
        symbols, selected_start, _scan_err = auto_select_symbols_rolling(
            select_source, select_limit, start, end, progress_cb=_sel_progress,
            aggregate=bool(task.get("rolling_build", False)),
            event_cb=lambda evs: save_events(task_id, evs),
            relax=relax_mode)
        if not symbols:
            err = f"自动选股({select_source})在回测区间内无达标标的（已逐日顺延扫描）"
            if _scan_err is not None:
                err += f"；{_scan_err}"
            update_task_status(task_id, "failed", error_message=err)
            return {"status": "failed", "error": err}
        # 选股完成汇总事件
        try:
            save_events(task_id, [{
                "type": "auto_select_done",
                "trade_day": (selected_start or start.replace("-", "")),
                "data": {
                    "source": select_source,
                    "count": len(symbols),
                    "symbols": symbols[:20],
                    "selected_start": selected_start,
                },
            }])
        except Exception as e:
            print(f"[t-backtest] 选股完成事件落库失败: {e}")
        # 顺延命中：窗口起始日滚动到第一个有达标标的的交易日（同步落库，报告/前端展示一致）
        if selected_start and selected_start != start.replace("-", ""):
            new_start = f"{selected_start[:4]}-{selected_start[4:6]}-{selected_start[6:8]}"
            print(f"[t-backtest] 起始日无达标标的，回测窗口顺延 {start} → {new_start}")
            start = new_start
            try:
                db = SessionLocal()
                try:
                    db.execute(text(
                        "UPDATE t_backtest_tasks SET start_date = :s WHERE id = :id"
                    ), {"s": new_start, "id": task_id})
                    db.commit()
                finally:
                    db.close()
            except Exception as e:
                print(f"[t-backtest] 更新任务起始日失败: {e}")
        print(f"[t-backtest] 自动选股({select_source}) 达标 {len(symbols)} 只: {symbols[:10]}")
    if is_combined:
        symbol = "combined"

    # 1) 预取（幂等续拉）——选股/预取都耗时，这里开始按阶段上报实时进度
    update_task_status(task_id, "running", progress=10)
    try:
        from datetime import datetime as _dt, timedelta as _td
        from app.services import t_backtest_data as btd
        days = btd.resolve_trade_days(start, end)
        gaps = []
        syms = symbols if is_combined else [symbol]
        for idx, s in enumerate(syms):
            # 标的 m5 + 日线预取：10% → 40%（按标的均分）
            try:
                pct = 10 + round(idx / max(len(syms), 1) * 30)
                update_task_status(task_id, "running", progress=pct)
            except Exception:
                pass
            r = btd.prefetch_m5(s, days, task_dir, is_index=False)
            gaps.extend(r.get("gaps", []))
            # 标的日线需覆盖建仓规则所需历史（趋势/风险 ≥40 根）：起始日前推 60 天
            try:
                ext_start = (_dt.strptime(start, "%Y-%m-%d") - _td(days=60)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                ext_start = start
            d = btd.prefetch_stock_daily([s], ext_start, end, task_dir)
            gaps.extend(d.get("gaps", []))
        update_task_status(task_id, "running", progress=42)
        # 全市场滚动扫描（rolling_scan）：实时粗筛活跃池（限 300 只）→ 批量预取
        # 活跃池日线（按交易日批量，pro.daily(trade_date=...)）+ m5。
        # 粗筛用生产实时行情（活跃度近似，只决定候选范围）；打分/建仓全部用
        # as_of 历史日线（无价格前视）。
        rolling_scan = bool(task.get("rolling_scan", False))
        if rolling_scan and is_combined:
            try:
                from app.services.t_build import _fetch_all_a_symbols, _coarse_filter_active
                all_syms = _fetch_all_a_symbols()
                all_codes = [r["symbol"] for r in (all_syms or [])]
                task["_all_symbols"] = all_codes
                print(f"[t-backtest] rolling_scan 全市场列表 {len(all_codes)} 只，实时粗筛活跃池")
                active = _coarse_filter_active(all_syms, max_batches=6, batch_size=50)  # ≤300 只
                active_codes = [a[2:] if a[:2] in ("sh", "sz") else a for a in active]
                task["_scan_pool"] = active_codes
                print(f"[t-backtest] rolling_scan 粗筛活跃池 {len(active_codes)} 只，批量预取日线+m5")
                # 日线预取（按交易日批量，覆盖建仓规则历史 70 天 + 窗口期）
                try:
                    _ext8 = (_dt.strptime(start, "%Y-%m-%d") - _td(days=70)).strftime("%Y%m%d")
                    _end8 = end.replace("-", "")
                    _all_days = btd.resolve_trade_days(
                        f"{_ext8[:4]}-{_ext8[4:6]}-{_ext8[6:8]}", end)
                    _filter = set(active_codes)
                    d2 = btd.prefetch_all_daily_by_trade_date(_all_days, task_dir, symbol_filter=_filter)
                    gaps.extend(d2.get("gaps", []))
                except Exception as e:
                    print(f"[t-backtest] rolling_scan 批量日线预取失败: {e}")
                # m5 预取（迭代修复"次日开盘价不可用"）：对窗口内每个交易日跑历史扫描，
                # 收集所有 pass_gate 标的的并集 → 对并集预取 m5。
                # 此前只预取窗口首日的 top30，导致每日滚动扫描选出的新标的
                # （如 000767 score0.84）无 m5 缓存被"次日开盘价不可用"误拒。
                try:
                    from app.services import t_build as _tb
                    _m5_codes: List[str] = []
                    for _td_ in days:  # days = 窗口交易日（YYYYMMDD）
                        try:
                            _asof = f"{_td_[:4]}-{_td_[4:6]}-{_td_[6:8]}"
                            _hist = _tb.scan_t_candidates_historical(
                                active_codes, str(task_dir), as_of=_asof,
                                quality_fn=_tb._quality_from_daily, limit=20,
                                relax=relax_mode)
                            for c in _hist:
                                if c.get("symbol") and c.get("pass_gate") \
                                        and c["symbol"] not in _m5_codes:
                                    _m5_codes.append(c["symbol"])
                        except Exception as _e:
                            print(f"[t-backtest] rolling_scan 日扫描失败 {_td_}: {str(_e)[:60]}")
                    if not _m5_codes:
                        _m5_codes = active_codes[:30]
                    print(f"[t-backtest] rolling_scan 全窗口扫描达标并集 {len(_m5_codes)} 只，预取 m5")
                    for code in _m5_codes:
                        r = btd.prefetch_m5(code, days, task_dir, is_index=False)
                        gaps.extend(r.get("gaps", []))
                except Exception as e:
                    print(f"[t-backtest] rolling_scan m5 预取失败: {e}")
            except Exception as e:
                print(f"[t-backtest] rolling_scan 全市场列表失败: {e}")
                rolling_scan = False
        update_task_status(task_id, "running", progress=46)
        if not btd.SKIP_INDEX_M5:
            for key, ts in (("hs300", "000300.SH"), ("sh", "000001.SH"), ("sz", "399001.SZ")):
                r = btd.prefetch_m5(key, days, task_dir, is_index=True, ts_code=ts)
                gaps.extend(r.get("gaps", []))
        d = btd.prefetch_index_daily(list(btd.INDEX_TS_CODES.values()), start, end, task_dir)
        gaps.extend(d.get("gaps", []))
        btd.write_gaps(task_dir, gaps)
        update_task_status(task_id, "running", progress=50)
    except Exception as e:
        print(f"[t-backtest] 预取失败: {e}")
        update_task_status(task_id, "failed", error_message=f"数据预取失败: {e}")
        return {"status": "failed", "error": f"数据预取失败: {e}"}

    # 2) 回放（进度 50% → 100%，逐日更新）
    update_task_status(task_id, "running", progress=50)
    task["conditions"] = conditions
    task["review_mode"] = task.get("review_mode", "llm")
    task["symbols"] = symbols
    task["build_mode"] = build_mode
    task["build_limit_ratio"] = build_limit_ratio
    task["rolling_build"] = bool(task.get("rolling_build", False))
    task["relax_mode"] = relax_mode
    review_fn = build_review_fn(task)
    if review_fn is None and task.get("review_mode", "llm") == "llm":
        print(f"[t-backtest] ⚠️ LLM 复核不可用（bridge 未就绪），任务 #{task_id} 降级规则模式")

    try:
        if is_combined:
            from app.services.t_backtest import TCombinedBacktestEngine
            engine = TCombinedBacktestEngine(task, str(task_dir), review_fn=review_fn)
        else:
            from app.services.t_backtest import TBacktestEngine
            engine = TBacktestEngine(task, str(task_dir), review_fn=review_fn)

        # 实时进度：逐日更新 progress 列 + 增量落库事件/权益（前端轮询即可看到进展）
        live = {"events": 0, "equity": 0}

        def _progress(done, total, events_delta=None, equity_point=None):
            try:
                # 回放阶段进度 = 50% → 100%（选股/预取已占 0-50%）
                pct = round(50 + done / total * 50) if total else 100
                update_task_status(task_id, "running", progress=max(0, min(100, pct)))
            except Exception as e:
                print(f"[t-backtest] 进度更新失败: {e}")
            if events_delta:
                try:
                    save_events(task_id, events_delta)
                    live["events"] += len(events_delta)
                except Exception as e:
                    print(f"[t-backtest] 实时事件落库失败: {e}")
            if equity_point:
                try:
                    save_equity(task_id, [equity_point])
                    live["equity"] += 1
                except Exception as e:
                    print(f"[t-backtest] 实时权益落库失败: {e}")

        result = engine.run(cancel_event, progress_cb=_progress)
    except Exception as e:
        print(f"[t-backtest] 回放失败 #{task_id}: {e}")
        update_task_status(task_id, "failed", error_message=f"回放异常: {e}")
        return {"status": "failed", "error": str(e)}

    # 3) 落库（实时进度已增量写 events/equity；此处补剩余增量 + trades/metrics）
    if is_combined:
        all_events: List[Dict[str, Any]] = []
        all_trades: List[Dict[str, Any]] = []
        for r in result.get("per_symbol", []):
            all_events.extend(r.get("events", []))
            all_trades.extend(r.get("ledger", {}).get("trades", []))
        # 事件已实时落库（组合引擎透传子引擎事件），仅补剩余
        remain = all_events[live["events"]:]
        if remain:
            save_events(task_id, remain)
        save_trades(task_id, all_trades)
        # 组合权益曲线是聚合结果，实时阶段只落了各标的部分快照——直接覆盖为组合曲线
        try:
            from sqlalchemy import text as _text
            db = SessionLocal()
            try:
                db.execute(_text("DELETE FROM t_backtest_equity_snapshots WHERE task_id = :id"),
                           {"id": task_id})
                db.commit()
            finally:
                db.close()
        except Exception:
            pass
        save_equity(task_id, result.get("equity_curve", []))
        portfolio = dict(result.get("portfolio") or {})
        portfolio["build_decisions"] = result.get("build_decisions", [])
        portfolio["per_symbol"] = [{
            "symbol": r.get("symbol"),
            "build": r.get("build"),
            "metrics": r.get("metrics", {}),
            "ledger": r.get("ledger", {}),
        } for r in result.get("per_symbol", [])]
        save_metrics(task_id, portfolio, result.get("caliber_notes", []))
    else:
        all_events = result.get("events", [])
        remain = all_events[live["events"]:]
        if remain:
            save_events(task_id, remain)
        save_trades(task_id, result.get("ledger", {}).get("trades", []))
        curve = result.get("equity_curve", [])
        if live["equity"] < len(curve):
            save_equity(task_id, curve[live["equity"]:])
        save_metrics(task_id, result.get("metrics", {}), result.get("caliber_notes", []))

    # 回测 AI 决策审计 + outcome 落库（供决策质量统计：exec 胜率/abandon 正确率）
    _persist_backtest_ai_outcomes(task_id, result, is_combined)

    if result.get("status") == "completed":
        update_task_status(task_id, "completed", progress=100, error_message="")
    else:
        update_task_status(task_id, "failed", error_message=result.get("error"))
    return result


def _persist_backtest_ai_outcomes(task_id: int, result: Dict[str, Any], is_combined: bool):
    """把回测 AI 决策的成交结果（ai_outcomes）写入 t_ai_actions（ai_exec + outcome）。

    回测 review 事件里 action=exec 的触发即 AI 决策成交；outcome 来自引擎 _compute_fill_outcomes。
    用于 exec 胜率/abandon 正确率统计（与实盘同口径）。
    """
    try:
        from datetime import date
        from app.services import t_db
        outcomes = []
        if is_combined:
            for r in result.get("per_symbol", []):
                outcomes.extend(r.get("ai_outcomes", []))
        else:
            outcomes = result.get("ai_outcomes", [])
        if not outcomes:
            return
        td = date.today().strftime("%Y-%m-%d")
        # 回测事件流中 action=exec 的 review → 关联 outcome（按 symbol+fill 时间顺序）
        for oc in outcomes:
            sym = oc.get("symbol", "")
            t_db.insert_ai_action(
                session_id=f"t-backtest-{task_id}", trade_date=td, symbol=sym,
                action_type="ai_exec",
                input_snapshot={"source": "backtest", "task_id": task_id},
                output={"reason": "回测 AI 决策成交", "side": oc.get("side")},
                gateway_result={"status": "success", "price": oc.get("fill_price")},
                outcome=oc,
            )
    except Exception as e:
        print(f"[t-backtest] 回测 outcome 落库失败: {e}")


# ────────────────────────────────────────────────────────────────
# worker 轮询执行（对齐 t_bridge.fallback_poll_loop 模式）
# ────────────────────────────────────────────────────────────────

_POLL_INTERVAL = 5.0


def backtest_poll_loop(stop_event: Any):
    """worker daemon：轮询 pending 回测任务，领取即执行（重活不阻塞 API）。"""
    while not stop_event.is_set():
        try:
            task = claim_pending_task(consumer="t-backtest-worker")
            if task:
                print(f"[t-backtest] 领取任务 #{task['id']} {task.get('symbol')} 开始执行")
                run_task(int(task["id"]), cancel_event=stop_event)
            else:
                time.sleep(_POLL_INTERVAL)
        except Exception as e:
            print(f"[t-backtest] 轮询异常: {e}")
            time.sleep(_POLL_INTERVAL)


def _requeue_stale_running_tasks() -> None:
    """worker 启动时：把遗留 running 的回测任务重置为 pending。

    单 worker 部署——本进程刚启动说明之前执行该任务的进程一定已中断，
    遗留 running 必须恢复为 pending 才能被重新领取（否则会一直假死卡在 xx%）。
    """
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_backtest_tasks SET status = 'pending', progress = 0 "
                "WHERE status = 'running'"
            ))
            db.commit()
        finally:
            db.close()
        print("[t-backtest] ✅ 遗留 running 任务已重置为 pending（部署/重启后自动恢复）")
    except Exception as e:
        print(f"[t-backtest] ⚠️ 重置遗留任务失败: {e}")


def start_t_backtest_worker() -> bool:
    """启动回测任务执行线程（worker 进程调用）。启动前先恢复遗留 running 任务。"""
    try:
        import threading
        _requeue_stale_running_tasks()
        stop_event = threading.Event()
        t = threading.Thread(target=backtest_poll_loop, args=(stop_event,),
                             daemon=True, name="t-backtest-worker")
        t.start()
        print("[t-backtest] ✅ 回测任务执行线程已启动（轮询 pending 任务）")
        return True
    except Exception as e:
        print(f"[t-backtest] ⚠️ 回测执行线程启动失败: {e}")
        return False
