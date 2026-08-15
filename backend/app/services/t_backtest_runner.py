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


# ────────────────────────────────────────────────────────────────
# DB 读写
# ────────────────────────────────────────────────────────────────

def create_task(symbol: str, start_date: str, end_date: str,
                conditions: List[Dict[str, Any]], init_shares: int = 1000,
                init_price: Optional[float] = None, net_asset: float = 200000.0,
                review_mode: str = "llm", symbols: Optional[List[str]] = None,
                build_mode: bool = False, build_limit_ratio: float = 0.55,
                select_source: str = "manual", select_limit: int = 10) -> Optional[int]:
    """创建回测任务（status=pending）。组合模式：symbols 列表或 select_source 自动选股。"""
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                """
                INSERT INTO t_backtest_tasks (
                    symbol, start_date, end_date, init_shares, init_price,
                    net_asset, review_mode, conditions_json, symbols_json,
                    build_mode, build_limit_ratio, select_source, select_limit
                ) VALUES (:symbol, :start, :end, :shares, :price, :asset, :mode, :conds,
                          :symbols, :build_mode, :build_limit, :sel_src, :sel_lim)
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
                db.execute(text(
                    "INSERT INTO t_backtest_events (task_id, event_type, trade_day, bar_time, data_json) "
                    "VALUES (:task, :etype, :day, :bt, :data)"
                ), {
                    "task": task_id, "etype": ev.get("type", ev_copy.get("type", "event")),
                    "day": ev.get("trade_day") or (data or {}).get("trade_day") if isinstance(data, dict) else None,
                    "bt": (data or {}).get("bar_time") if isinstance(data, dict) else None,
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
    """bridge 地址（与 t_bridge._bridge_url 同源）。"""
    try:
        from app.config import get_settings
        settings = get_settings()
        return getattr(settings, "PI_SERVER_URL", "http://127.0.0.1:3001").rstrip("/")
    except Exception:
        return "http://127.0.0.1:3001"


def build_review_fn(task: Dict[str, Any]) -> Optional[callable]:
    """按 review_mode 构造复核回调：
    - llm：POST bridge /backtest/review（回测会话沙盒），失败降级规则（返回 None → 引擎规则模式）
    - rule：None（引擎内 _rule_review）
    """
    if task.get("review_mode", "llm") != "llm":
        return None
    url = bridge_base_url() + "/backtest/review"
    timeout = 45

    def review(rev_ctx: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "task_id": task.get("id"),
            "symbol": task.get("symbol"),
            "trigger": rev_ctx.get("trigger", {}),
            "regime": rev_ctx.get("regime", {}),
            "rule_hint": rev_ctx.get("rule_hint", {}),
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        decision = "auto" if body.get("decision") == "auto" else "human"
        return {"decision": decision, "reason": str(body.get("reason") or "")[:500]}

    return review


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
    if is_combined and not symbols and select_source in ("pool", "scan"):
        try:
            from app.services.t_build import scan_t_candidates
            # 自动选股（防前视）：精筛用 as_of=窗口首日前一交易日的日线（趋势/风险历史化）
            from datetime import datetime as _dt, timedelta as _td
            try:
                as_of = (_dt.strptime(start, "%Y-%m-%d") - _td(days=1)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                as_of = None
            cands = scan_t_candidates(limit=select_limit, source=select_source, as_of=as_of)
            symbols = [c.get("symbol") for c in cands
                       if c.get("symbol") and c.get("pass_gate")]
            if not symbols:
                update_task_status(task_id, "failed", error_message="自动选股无达标标的")
                return {"status": "failed", "error": f"自动选股({select_source})无达标标的"}
            print(f"[t-backtest] 自动选股({select_source}) 达标 {len(symbols)} 只: {symbols[:10]}")
        except Exception as e:
            print(f"[t-backtest] 自动选股失败: {e}")
            update_task_status(task_id, "failed", error_message=f"自动选股失败: {e}")
            return {"status": "failed", "error": f"自动选股失败: {e}"}
    if is_combined:
        symbol = "combined"

    # 1) 预取（幂等续拉）
    try:
        from app.services import t_backtest_data as btd
        days = btd.resolve_trade_days(start, end)
        gaps = []
        syms = symbols if is_combined else [symbol]
        for s in syms:
            r = btd.prefetch_m5(s, days, task_dir, is_index=False)
            gaps.extend(r.get("gaps", []))
            # 标的日线需覆盖建仓规则所需历史（趋势/风险 ≥40 根）：起始日前推 60 天
            from datetime import datetime as _dt, timedelta as _td
            try:
                ext_start = (_dt.strptime(start, "%Y-%m-%d") - _td(days=60)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                ext_start = start
            d = btd.prefetch_stock_daily([s], ext_start, end, task_dir)
            gaps.extend(d.get("gaps", []))
        if not btd.SKIP_INDEX_M5:
            for key, ts in (("hs300", "000300.SH"), ("sh", "000001.SH"), ("sz", "399001.SZ")):
                r = btd.prefetch_m5(key, days, task_dir, is_index=True, ts_code=ts)
                gaps.extend(r.get("gaps", []))
        d = btd.prefetch_index_daily(list(btd.INDEX_TS_CODES.values()), start, end, task_dir)
        gaps.extend(d.get("gaps", []))
        btd.write_gaps(task_dir, gaps)
    except Exception as e:
        print(f"[t-backtest] 预取失败: {e}")
        update_task_status(task_id, "failed", error_message=f"数据预取失败: {e}")
        return {"status": "failed", "error": f"数据预取失败: {e}"}

    # 2) 回放
    update_task_status(task_id, "running")
    task["conditions"] = conditions
    task["review_mode"] = task.get("review_mode", "llm")
    task["symbols"] = symbols
    task["build_mode"] = build_mode
    task["build_limit_ratio"] = build_limit_ratio
    review_fn = build_review_fn(task)
    if review_fn is None and task.get("review_mode", "llm") == "llm":
        print(f"[t-backtest] ⚠️ LLM 复核不可用（bridge 未就绪），任务 #{task_id} 降级规则模式")

    try:
        if is_combined:
            from app.services.t_backtest import TCombinedBacktestEngine
            engine = TCombinedBacktestEngine(task, str(task_dir), review_fn=review_fn)
            result = engine.run(cancel_event)
        else:
            from app.services.t_backtest import TBacktestEngine
            engine = TBacktestEngine(task, str(task_dir), review_fn=review_fn)
            result = engine.run(cancel_event)
    except Exception as e:
        print(f"[t-backtest] 回放失败 #{task_id}: {e}")
        update_task_status(task_id, "failed", error_message=f"回放异常: {e}")
        return {"status": "failed", "error": str(e)}

    # 3) 落库（组合模式：聚合各标的 events/trades/equity + 组合明细入 metrics）
    if is_combined:
        all_events: List[Dict[str, Any]] = []
        all_trades: List[Dict[str, Any]] = []
        for r in result.get("per_symbol", []):
            all_events.extend(r.get("events", []))
            all_trades.extend(r.get("ledger", {}).get("trades", []))
        save_events(task_id, all_events)
        save_trades(task_id, all_trades)
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
        save_events(task_id, result.get("events", []))
        save_trades(task_id, result.get("ledger", {}).get("trades", []))
        save_equity(task_id, result.get("equity_curve", []))
        save_metrics(task_id, result.get("metrics", {}), result.get("caliber_notes", []))

    if result.get("status") == "completed":
        update_task_status(task_id, "completed", progress=100, error_message="")
    else:
        update_task_status(task_id, "failed", error_message=result.get("error"))
    return result


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


def start_t_backtest_worker() -> bool:
    """启动回测任务执行线程（worker 进程调用）。"""
    try:
        import threading
        stop_event = threading.Event()
        t = threading.Thread(target=backtest_poll_loop, args=(stop_event,),
                             daemon=True, name="t-backtest-worker")
        t.start()
        print("[t-backtest] ✅ 回测任务执行线程已启动（轮询 pending 任务）")
        return True
    except Exception as e:
        print(f"[t-backtest] ⚠️ 回测执行线程启动失败: {e}")
        return False
