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
                select_source: str = "manual", select_limit: int = 10,
                rolling_build: bool = False) -> Optional[int]:
    """创建回测任务（status=pending）。组合模式：symbols 列表或 select_source 自动选股。

    rolling_build=True：每日滚动建仓（对齐实盘 daily_auto：盘后扫描 → 次日建仓）。
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
                    rolling_build
                ) VALUES (:symbol, :start, :end, :shares, :price, :asset, :mode, :conds,
                          :symbols, :build_mode, :build_limit, :sel_src, :sel_lim,
                          :rolling_build)
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
    timeout = 45

    def review(rev_ctx: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "task_id": task.get("id"),
            "symbol": task.get("symbol"),
            "trigger": rev_ctx.get("trigger", {}),
            "regime": rev_ctx.get("regime", {}),
            "rule_hint": rev_ctx.get("rule_hint", {}),
            "position": rev_ctx.get("position", {}),
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        # AI 决策动作：exec / wait / abandon（兼容旧 decision:auto→exec, human→wait）
        action = str(body.get("action") or "")
        if action not in ("exec", "wait", "abandon"):
            action = "exec" if body.get("decision") == "auto" else "wait"
        return {"action": action, "decision": "auto" if action == "exec" else "human",
                "reason": str(body.get("reason") or "")[:500]}

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
            source = select_source
            cands = scan_t_candidates(limit=select_limit, source=source, as_of=as_of)
            symbols = [c.get("symbol") for c in cands
                       if c.get("symbol") and c.get("pass_gate")]
            # 候选池空 → 降级全市场扫描（保证自动选股可用）
            if not symbols and source == "pool":
                print("[t-backtest] 候选池为空，降级全市场扫描自动选股")
                cands = scan_t_candidates(limit=select_limit, source="scan", as_of=as_of)
                symbols = [c.get("symbol") for c in cands
                           if c.get("symbol") and c.get("pass_gate")]
            if not symbols:
                update_task_status(task_id, "failed", error_message="自动选股无达标标的")
                return {"status": "failed", "error": f"自动选股({source})无达标标的"}
            print(f"[t-backtest] 自动选股({source}) 达标 {len(symbols)} 只: {symbols[:10]}")
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
    task["rolling_build"] = bool(task.get("rolling_build", False))
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
                pct = round(done / total * 100) if total else 100
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
