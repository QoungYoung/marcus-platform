# -*- coding: utf-8 -*-
"""bt_agent_loop.py — 回测里的**交易腿 agent 消费者循环**（对齐生产 `t_bridge.fallback_poll_loop`）。

## 为什么需要
生产做T链的两层之一（交易腿 agent）不在 `TMonitor` 的 bar 逻辑里，而在**线程**里：生产
`start_t_monitor()` 会起 `t_bridge.fallback_poll_loop(stop_event)`，它反复
`t_db.claim_pending_trigger("t-fallback", 300)` → `t_bridge.wake_and_decide(trig)`
（= `wake_agent()` POST `/chat` → `t_ai_agent.handle_ai_decision` 路由 exec/wait/abandon/update_condition
→ exec 走 `t_gateway.gateway_execute`），唤醒失败再降级 `t_bridge.agent_review_and_execute`。
`jobs/bt_prod_run.py` 只逐 bar 调 `TMonitor` 的方法、**从不 `start_t_monitor()`** → 这条消费链在回测里
从未运行 → 触发只写 `pending` 不被消费（LLM-off 年跑里 pending 累积到 2,4xx 条的根因）。

本模块把那**同一个 while 循环**搬进回测驱动，逐 bar 调用一次：

    while True:
        trig = <认领一条 pending>
        if not trig: break
        res = t_bridge.wake_and_decide(trig)
        if res.get("status") != "wake_failed": <记账>
        else: t_bridge.agent_review_and_execute(trig)     # 生产同款降级

## 与生产的两处**有意**差异（都为了回测安全，逐条写明）
1. **认领限定 `account_id`**：生产 `claim_pending_trigger` 按 `status='pending'` **无差别**认领，且它的
   "孤儿单处置"也按 status 全表扫。回测库里还有别的账户（如 `t`）的行，不能动 → 这里用**同构 SQL**
   （`FOR UPDATE SKIP LOCKED` + `claimed_by/claimed_at`，语义与生产一致）但加 `account_id = :account`。
   另加防御：万一认领回来的行不属于本账户 → 原状态回写 `pending`（清 claimed_by/claimed_at）并记账。
2. **默认只认领"本次运行产生"的 pending**（`id > 本次运行开始时的最大触发 id`，即 **id 水位**；
   2026-09-19 起；`BT_AGENT_CLAIM_FILTER=time` 回退到旧的"真实 epoch"口径——见下方 ⚠️）：
   回测库是**跨日累积**的，里面躺着别的模拟日留下的 pending。按生产语义全量认领会
   ① 用今天的行情去重放别的日子的旧触发（时间口径错）；② 一次跑触发上千次 LLM 外呼（不可控）。
   要生产原样（含孤儿单处置、全量认领）→ `BtAgentLoop(..., all_pending=True)` / `--agent-all-pending`。

⚠️ **2026-09-19 修（用户："上次回测怎么正常呢，我们也没改什么东西啊"）**：旧口径 `since` 取的是
**真实 epoch**（`real_epoch_now()`），而它在"created_at 由 DB 真钟写入"时恰好成立。当天把
`t_triggers.created_at` 统一到 **Python 钉钟**（= 回放日）后，回放里 `created_at(2026-01) < to_timestamp(
真实 epoch 2026-09)` ⇒ **一条 pending 都认领不到** ⇒ wolf_* 触发全积压、AI 裁决链整条哑火
（实测 0105→0130：只有 `blocked`/`pending`，没有一处 `executed/await_retry/cancelled` 的 AI 裁决）。
改用 **id 水位**：语义相同（"本次运行之后写入的行"）但**不依赖任何钟**。

调用面：`BtAgentLoop(account="stock").poll()` 每根 bar 调一次，返回本次增量统计。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


try:                                    # 单独跑本文件时把仓库布局塞进 sys.path（被 bt_prod_run import 时已就位）
    import bt_env                      # noqa: F401
    bt_env.add_paths()
except Exception:                       # pragma: no cover
    pass


# ⚠️ 2026-09-30（账本 §9.370）：把 wake 再拆两段 —— 只在本进程给函数套计时壳（生产不动 ✓）
_SPLIT = {"http": 0.0, "route": 0.0}


def install_split_timing() -> None:
    """env WOLF_AGENT_SPLIT_TIMING=1 时启用 ✓（默认关 ⇒ 零影响 ✓）"""
    import os as _os
    if str(_os.getenv("WOLF_AGENT_SPLIT_TIMING", "0")).strip().lower() not in ("1", "true", "yes", "on"):
        return
    import time as _t
    import app.services.t_bridge as _tb
    try:
        import app.services.t_ai_agent as _ta
    except Exception:
        _ta = None

    def _wrap(mod, name, key):
        fn = getattr(mod, name, None)
        if fn is None or getattr(fn, "_split_wrapped", False):
            return
        def _inner(*a, **kw):
            t0 = _t.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                _SPLIT[key] = _SPLIT.get(key, 0.0) + (_t.perf_counter() - t0)
        _inner._split_wrapped = True
        setattr(mod, name, _inner)

    _wrap(_tb, "wake_agent", "http")
    if _ta is not None:
        _wrap(_ta, "handle_ai_decision", "route")
    print("[bt-agent] 已启用 wake 两段拆分计时（http=桥回合 / route=路由执行 ✓）", flush=True)


install_split_timing()   # 导入即装（env 关则零动作 ✓）

_CAP_STATE = {"n": 0, "limit": None}


def _ensure_capture() -> None:
    """每根 bar 调一次 ✓：若当前 `urlopen` 不是我们的壳就重装 ✓（防被 as-of 层顶掉 ✗）"""
    import os as _os
    if str(_os.getenv("WOLF_AGENT_CAPTURE", "0")).strip().lower() not in ("1", "true", "yes", "on"):
        return
    import urllib.request as _u
    if getattr(_u.urlopen, "_wolf_capture", False):
        return
    if _CAP_STATE["limit"] is None:
        _CAP_STATE["limit"] = int(_os.getenv("WOLF_AGENT_CAPTURE_N", "2") or 2)
    _install_capture_once()


def install_agent_capture() -> None:
    """env WOLF_AGENT_CAPTURE=1 时启用：把发给桥的 /chat 载荷 dump 成 JSONL（默认关 ✓）"""
    import os as _os
    if str(_os.getenv("WOLF_AGENT_CAPTURE", "0")).strip().lower() not in ("1", "true", "yes", "on"):
        return
    _CAP_STATE["limit"] = int(_os.getenv("WOLF_AGENT_CAPTURE_N", "2") or 2)
    _install_capture_once()


def _install_capture_once() -> None:
    import os as _os
    import json as _j
    import time as _t
    import urllib.request as _u
    path = str(_os.getenv("WOLF_AGENT_CAPTURE_PATH")
               or "/home/fengx/marcus-platform/.dsh-tmp/wolfbt/agent_msg_dump.jsonl")
    limit = _CAP_STATE["limit"] or 2
    state = _CAP_STATE
    _orig = _u.urlopen

    def _inner(req, *a, **kw):
        try:
            url = getattr(req, "full_url", None) or str(req)
            data = getattr(req, "data", None)
            if "/chat" in str(url) and data and state["n"] < limit:
                state["n"] += 1
                t0 = _t.perf_counter()
                try:
                    body = json.loads(bytes(data).decode("utf-8", "replace"))
                except Exception:
                    body = {"_raw": str(data)[:200]}
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(_j.dumps({"n": state["n"], "at": _t.strftime("%H:%M:%S"),
                                       "url": url, "body": body}, ensure_ascii=False) + "\n")
                print("[bt-agent] 已抓包 #%d（%d 字 ✓）⇒ %s" % (state["n"], len(str(body)), path), flush=True)
                fh = None
                t0 = t0
        except Exception as _e_sil1:
            _silent_alert("bt_agent_loop.py:150", _e_sil1)
        return _orig(req, *a, **kw)

    _inner._wolf_capture = True
    _u.urlopen = _inner
    print("[bt-agent] 已启用抓包（/chat 载荷 dump ⇒ %s ✓，最多 %d 条 ✓）" % (path, limit), flush=True)


install_agent_capture()   # 导入即装（env 关则零动作 ✓）


def real_epoch_now() -> float:
    """**真实**墙上时钟 epoch（秒）。`bt_prod_run` 把 `time.time()` 钉到模拟时刻，故不能用它。"""
    try:
        return float(time.clock_gettime(time.CLOCK_REALTIME))
    except Exception:                   # pragma: no cover
        return float(time.time())


def _empty_stats() -> Dict[str, Any]:
    return {"poll_calls": 0, "claim": 0, "exec": 0, "wait": 0, "abandon": 0,
            "update_condition": 0, "rule_fallback": 0, "other": 0,
            "wake_failed": 0, "error": 0, "skipped_other_account": 0,
            "by_action": {}, "by_status": {}, "claimed_ids": [], "wake_failed_ids": []}


class BtAgentLoop:
    """生产 `fallback_poll_loop` 的回测等价物：每次 `poll()` 把当前可认领的 pending 消费干净。"""

    def __init__(self, account: str = "stock", consumer: str = "bt-fallback",
                 timeout_seconds: int = 300, all_pending: bool = False,
                 run_since: Optional[float] = None,
                 max_per_poll: int = 200, max_total: int = 0,
                 verbose: bool = True, guard: Optional[Any] = None):
        self.account = str(account)
        self.consumer = str(consumer)
        self.timeout_seconds = int(timeout_seconds)
        # all_pending=False（默认）→ 只认领 run_since（本次运行开始）之后写入的行
        self.all_pending = bool(all_pending)
        self.run_since: Optional[float] = None if self.all_pending else (
            float(run_since) if run_since is not None else real_epoch_now())
        # ⚠️ 2026-09-19：认领范围改用 **id 水位**（与 created_at 用哪个钟无关）。
        #   背景：原判据 `created_at >= to_timestamp(run_since)`（run_since=**真实 epoch**）在"created_at
        #   由 DB 真钟写入"时成立；当天把 t_triggers.created_at 统一到 **Python 钉钟**（回放日）后，
        #   回放里 created_at(1 月) < to_timestamp(9 月) ⇒ **一条都认领不到** ⇒ wolf_* 触发全积压成
        #   pending、AI 决策链整条哑火（实测 0105→0130 只有 blocked/pending，没有一处 AI 裁决）。
        #   id 水位表达的是同一个语义（"本次运行之后写入的行"），但**不依赖任何钟**。
        #   BT_AGENT_CLAIM_FILTER=time 可回退旧口径。
        self.claim_filter = (os.getenv("BT_AGENT_CLAIM_FILTER", "id") or "id").strip().lower()
        self._wm: Optional[int] = None
        # ⚠️ **2026-09-20 修（用户："还有哪些不合理亏损…自己修复下"；夜间逐笔排查发现）**：
        #   id 水位原来是**懒取**的（第一次 `_claim_pending()` 时才 `SELECT MAX(id)`），而第一次认领发生在
        #   开盘后的第一个 poll（实测 09:40），此刻 **09:35 那一批规则触发已经写库** ⇒ 水位把它们一起框在
        #   外面 ⇒ **每个回放日的 09:35 批次（wolf_fib_target_sell / wolf_profit_take_sell /
        #   wolf_defensive_t_reduce / wolf_zheng_t_buy）永远不会被认领**，永久躺在 `pending`。
        #   证据（jan10/0108）：09:35 批次 id 186385–186396，当日第一条 AI 裁决是 **#186399**；
        #   全臂 111 条 pending 全部 `created_at=09:35`、`claimed_at` 全空（jan11 117 条、jan12 前 14 天 78 条）。
        #   生产里 `fallback_poll_loop` 从 09:15 就在轮询，09:35 批次照常认领 ⇒ **回测少了这批裁决 = 不拟真**。
        #   修法：水位在**构造时**取（`bt_prod_run` 在 bar 循环**之前**构造本对象 ⇒ 水位=上一日最后一行的 id，
        #   当日全部触发都在水位之上）。开关 `BT_AGENT_CLAIM_WM`：`first-poll`=旧（默认，库内零变化）/
        #   `eager`=构造时取（回测 pins 打开）。生产不用本模块 ⇒ 无论如何都零生产影响。
        self.claim_wm = (os.getenv("BT_AGENT_CLAIM_WM", "first-poll") or "first-poll").strip().lower()
        if self.claim_filter == "id" and not self.all_pending and self.claim_wm == "eager":
            self.snapshot_watermark()
        if verbose:
            print("[bt-agent] 认领口径：filter=%s wm=%s 水位=%s（all_pending=%s）"
                  % (self.claim_filter, self.claim_wm,
                     "未取" if self._wm is None else self._wm, self.all_pending), file=sys.stderr)
        self.max_per_poll = int(max_per_poll or 0)      # 0 = 不限（生产语义）
        self.max_total = int(max_total or 0)            # 0 = 不限（生产语义）
        self.verbose = bool(verbose)
        # as-of 工具口径守卫（`bt_agent_tools.BacktestToolGuard`，可空）：
        # 唤醒前把"当前触发"交给它 → 它会用触发标的 + 当前 bar 生成 as-of 数据块写进提示词。
        self.guard = guard
        self.stats = _empty_stats()

    # ── 认领：`t_db.claim_pending_trigger` 的 account 过滤版（同构 SQL）──
    def snapshot_watermark(self) -> Optional[int]:
        """**构造时**取 id 水位（= 本臂当前最大触发 id）。幂等；失败不抛（留着懒取兜底）。

        语义 = "本次运行开始之前就存在的行都不算"，与生产"轮询器从开盘前就在跑"一致。
        必须在 bar 循环**之前**调用，否则当日的早盘批次会被水位挡在外面（见 __init__ 的 ⚠️）。
        """
        if self._wm is not None:
            return self._wm
        try:
            from sqlalchemy import text
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                self._wm = int(db.execute(text(
                    "SELECT COALESCE(MAX(id), 0) FROM t_triggers WHERE account_id = :acc"
                ), {"acc": self.account}).scalar() or 0)
            finally:
                db.close()
        except Exception as e:                                  # pragma: no cover
            print("[bt-agent] ⚠️ 构造时取水位失败（回退首次认领时懒取）: %s" % str(e)[:100], file=sys.stderr)
            self._wm = None
        return self._wm

    def _claim_pending(self) -> Optional[Dict[str, Any]]:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            if self.all_pending:
                # 生产 claim_pending_trigger 里的"孤儿单处置"（pending 超时 → cancelled）。
                # 只在 all_pending 模式做：默认模式下它会把**别的模拟日**的行也改掉。
                cutoff = datetime.now() - timedelta(seconds=self.timeout_seconds)
                db.execute(text(
                    "UPDATE t_triggers SET status = 'cancelled', reason = 'orphan_timeout' "
                    "WHERE status = 'pending' AND account_id = :acc AND created_at < :cutoff"
                ), {"acc": self.account, "cutoff": cutoff})
            params: Dict[str, Any] = {"consumer": self.consumer, "acc": self.account,
                                      "claimed_at": datetime.now()}
            since_sql = ""
            if not self.all_pending:
                if self.claim_filter == "time":
                    since_sql = "AND created_at >= to_timestamp(:since)"
                    params["since"] = float(self.run_since or 0.0)
                else:
                    if self._wm is None:      # 兜底懒取（仅在 BT_AGENT_CLAIM_WM=first-poll 或构造时取失败时走）
                        self._wm = int(db.execute(text(
                            "SELECT COALESCE(MAX(id), 0) FROM t_triggers WHERE account_id = :acc"
                        ), {"acc": self.account}).scalar() or 0)
                    since_sql = "AND id > :wm"
                    params["wm"] = int(self._wm)
            row = db.execute(text(
                "UPDATE t_triggers SET status = 'claimed', claimed_by = :consumer, claimed_at = :claimed_at "
                "WHERE id = ("
                "  SELECT id FROM t_triggers "
                "  WHERE status = 'pending' AND account_id = :acc " + since_sql + " "
                "  ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
                ") RETURNING id, account_id, condition_id, symbol, event_type, trigger_price, "
                "quote_price, suggest_bid_price, suggest_ask_price, slippage_budget, snapshot, "
                "mode, status, direction"
            ), params).mappings().first()
            db.commit()
            return dict(row) if row else None
        except Exception as e:
            try:
                db.rollback()
            except Exception as _e_sil2:
                _silent_alert("bt_agent_loop.py:293", _e_sil2)
            print("[bt-agent] claim 失败: %s" % str(e)[:200])
            return None
        finally:
            db.close()

    def _restore_pending(self, trigger_id: int, why: str) -> None:
        """非本账户的行：原状态回写 pending（并清掉认领标记），绝不消费。"""
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_triggers SET status = 'pending', claimed_by = NULL, claimed_at = NULL "
                "WHERE id = :id"
            ), {"id": int(trigger_id)})
            db.commit()
        except Exception as e:
            print("[bt-agent] 回写 pending 失败 #%s: %s" % (trigger_id, str(e)[:120]))
        finally:
            db.close()
        self.stats["skipped_other_account"] += 1
        print("[bt-agent] ⏭ 跳过非本账户触发 #%s（%s）→ 已回写 pending" % (trigger_id, why))

    def _decide_one(self, trig, t_bridge, _t9, _T):
        """单条触发的判定与记账（串行/并行**共用** ✓ —— 逐字等同原逻辑 ✓）"""
        tid = int(trig.get("id") or 0)
        self.stats["claim"] += 1
        self.stats["claimed_ids"].append(tid)
        if self.guard is not None:
            self.guard.set_trigger(trig)
        try:
            _p2 = _t9.perf_counter()
            result = t_bridge.wake_and_decide(trig) or {}
            _T["wake"] += _t9.perf_counter() - _p2
        except Exception as e:
            self.stats["error"] += 1
            print("[bt-agent] wake_and_decide 异常 #%s: %s → 走降级" % (tid, str(e)[:160]))
            result = {"status": "wake_failed", "reason": "wake 异常: %s" % str(e)[:120]}
        finally:
            if self.guard is not None:
                self.guard.clear()
        if result.get("status") != "wake_failed":
            action = str(result.get("action") or "")
            key = action if action in ("exec", "wait", "abandon", "update_condition") else "other"
            self.stats[key] += 1
            self.stats["by_action"][action or "-"] = self.stats["by_action"].get(action or "-", 0) + 1
            st = str(result.get("status") or "-")
            self.stats["by_status"][st] = self.stats["by_status"].get(st, 0) + 1
            if result.get("fallback"):
                self.stats["rule_fallback"] += 1
            if self.verbose:
                print("[bt-agent] AI 决策完成 #%s %s → %s/%s %s"
                      % (tid, trig.get("symbol"), st, action,
                         str(result.get("reason") or "")[:60]))
        else:
            self.stats["wake_failed"] += 1
            self.stats["wake_failed_ids"].append(tid)
            fallback = t_bridge.agent_review_and_execute(trig) or {}
            st = str(fallback.get("status") or "-")
            self.stats["by_status"][st] = self.stats["by_status"].get(st, 0) + 1
            if self.verbose:
                print("[bt-agent] 唤醒失败 #%s %s → 降级标记 %s"
                      % (tid, trig.get("symbol"), st))

    def _claim_batch(self, k: int, t_bridge, _t9, _T) -> list:
        """按串行同款口径**先认领一批** ✓（账户不符照旧回写 pending ✓）"""
        batch = []
        n = 0
        while len(batch) < k:
            if self.max_per_poll and n >= self.max_per_poll:
                print("[bt-agent] ⚠️ 本轮已达 max_per_poll=%d，剩余 pending 留到下一根 bar"
                      % self.max_per_poll)
                break
            if self.max_total and self.stats["claim"] + len(batch) >= self.max_total:
                print("[bt-agent] ⚠️ 已达 max_total=%d，停止认领（当日预算）" % self.max_total)
                break
            _p1 = _t9.perf_counter()
            trig = self._claim_pending()
            _T["claim"] += _t9.perf_counter() - _p1
            if not trig:
                break
            if str(trig.get("account_id")) != self.account:
                self._restore_pending(trig.get("id"), "account_id=%s" % trig.get("account_id"))
                n += 1
                continue
            n += 1
            batch.append(trig)
        return batch

    # ── 一根 bar 调一次：把可认领的 pending 消费干净 ──
    def poll(self) -> Dict[str, Any]:
        import app.services.t_bridge as t_bridge
        before = json.loads(json.dumps(self.stats, ensure_ascii=False, default=str))
        self.stats["poll_calls"] += 1
        try:
            _ensure_capture()            # 账本 §9.372：每根 bar 自愈式保活抓包壳 ✓
        except Exception as _e_sil3:
            _silent_alert("bt_agent_loop.py:391", _e_sil3)
        # ⚠️ 2026-09-30（账本 §9.369）：**三段真实计时**（perf_counter ✓ 不受钉时钟影响 ✓）
        import time as _t9
        _T = self.stats.setdefault("_t", {"claim": 0.0, "wake": 0.0, "loop": 0.0})
        _p0 = _t9.perf_counter()
        _c0, _w0 = _T["claim"], _T["wake"]
        _par = 0
        try:
            _par = int(os.getenv("WOLF_AGENT_PARALLEL", "0") or 0)
        except Exception:
            _par = 0
        if _par >= 2:
            # 账本 §9.375：**只并行"等生成"** ✓ —— 认领/执行/顺序与原逻辑一致 ✓
            from concurrent.futures import ThreadPoolExecutor
            _batch = self._claim_batch(_par, t_bridge, _t9, _T)
            if _batch:
                _cache = {}
                _pw = _t9.perf_counter()
                try:
                    with ThreadPoolExecutor(max_workers=_par) as _ex:
                        _futs = {_ex.submit(t_bridge.wake_agent, _t): _t for _t in _batch}
                        for _f, _t in _futs.items():
                            try:
                                _cache[int(_t.get("id") or 0)] = _f.result()
                            except Exception as _e:
                                _cache[int(_t.get("id") or 0)] = None
                                print("[bt-agent] 并行取回复失败 #%s: %s"
                                      % (_t.get("id"), str(_e)[:120]))
                finally:
                    _T["wake"] += _t9.perf_counter() - _pw
                _orig_wake = t_bridge.wake_agent

                def _cached_wake(trigger, context=None, **kw):
                    return _cache.pop(int(trigger.get("id") or 0), None) or _orig_wake(trigger, context=context)

                t_bridge.wake_agent = _cached_wake
                try:
                    for _t in _batch:
                        self._decide_one(_t, t_bridge, _t9, _T)
                finally:
                    t_bridge.wake_agent = _orig_wake
                print("[bt-agent] ⚡ 并行取回复：一次并行 %d 条 ✓（并发 %d 路 ✓）"
                      % (len(_batch), _par), flush=True)
        n = 0
        while True:
            if self.max_per_poll and n >= self.max_per_poll:
                print("[bt-agent] ⚠️ 本轮已达 max_per_poll=%d，剩余 pending 留到下一根 bar"
                      % self.max_per_poll)
                break
            if self.max_total and self.stats["claim"] >= self.max_total:
                print("[bt-agent] ⚠️ 已达 max_total=%d，停止认领（当日预算）" % self.max_total)
                break
            _p1 = _t9.perf_counter()
            trig = self._claim_pending()
            _T["claim"] += _t9.perf_counter() - _p1
            if not trig:
                break
            if str(trig.get("account_id")) != self.account:
                self._restore_pending(trig.get("id"), "account_id=%s" % trig.get("account_id"))
                n += 1
                continue
            n += 1
            tid = int(trig.get("id") or 0)
            self.stats["claim"] += 1
            self.stats["claimed_ids"].append(tid)
            if self.guard is not None:
                self.guard.set_trigger(trig)            # as-of 工具口径：本次决策的标的/事件
            try:
                _p2 = _t9.perf_counter()
                result = t_bridge.wake_and_decide(trig) or {}
                _T["wake"] += _t9.perf_counter() - _p2
            except Exception as e:                      # 生产在 while 外兜异常（行会卡在 claimed）
                self.stats["error"] += 1
                print("[bt-agent] wake_and_decide 异常 #%s: %s → 走降级" % (tid, str(e)[:160]))
                result = {"status": "wake_failed", "reason": "wake 异常: %s" % str(e)[:120]}
            finally:
                if self.guard is not None:
                    self.guard.clear()                  # 决策结束即清上下文（避免串标的）
            if result.get("status") != "wake_failed":
                action = str(result.get("action") or "")
                key = action if action in ("exec", "wait", "abandon", "update_condition") else "other"
                self.stats[key] += 1
                self.stats["by_action"][action or "-"] = self.stats["by_action"].get(action or "-", 0) + 1
                st = str(result.get("status") or "-")
                self.stats["by_status"][st] = self.stats["by_status"].get(st, 0) + 1
                if result.get("fallback"):
                    self.stats["rule_fallback"] += 1
                if self.verbose:
                    print("[bt-agent] AI 决策完成 #%s %s → %s/%s %s"
                          % (tid, trig.get("symbol"), st, action,
                             str(result.get("reason") or "")[:60]))
            else:
                self.stats["wake_failed"] += 1
                self.stats["wake_failed_ids"].append(tid)
                fallback = t_bridge.agent_review_and_execute(trig) or {}
                st = str(fallback.get("status") or "-")
                self.stats["by_status"][st] = self.stats["by_status"].get(st, 0) + 1
                if self.verbose:
                    print("[bt-agent] 唤醒失败 #%s %s → 降级标记 %s"
                          % (tid, trig.get("symbol"), st))
        after = self.stats
        delta = {k: after[k] - before[k] for k in after if isinstance(after.get(k), int)}
        delta["claimed_ids"] = after["claimed_ids"][len(before.get("claimed_ids") or []):]
        _T["loop"] += (_t9.perf_counter() - _p0) - (_T["claim"] - _c0) - (_T["wake"] - _w0)
        if self.stats["poll_calls"] % 10 == 0:
            _c, _w, _l = _T["claim"], _T["wake"], _T["loop"]
            _tot = _c + _w + _l
            print("[bt-agent] 三段真实耗时: claim %.1fs (%.0f%%) | wake %.1fs (%.0f%%) | "
                  "其余 %.1fs (%.0f%%) | 总 %.1fs | poll %d / 裁决 %d"
                  % (_c, (_c / _tot * 100 if _tot else 0), _w, (_w / _tot * 100 if _tot else 0),
                     _l, (_l / _tot * 100 if _tot else 0), _tot,
                     self.stats["poll_calls"], self.stats.get("claim", 0)), flush=True)
        if self.stats["poll_calls"] % 10 == 0 and (_SPLIT["http"] or _SPLIT["route"]):
            _h, _r = _SPLIT["http"], _SPLIT["route"]
            _w = _T["wake"]
            print("[bt-agent]   ↳ wake 内部拆分: 桥回合(HTTP) %.1fs (%.0f%% of wake) | 路由执行 %.1fs (%.0f%%) | "
                  "wake 其余 %.1fs (%.0f%%)"
                  % (_h, (_h / _w * 100 if _w else 0), _r, (_r / _w * 100 if _w else 0),
                     max(0.0, _w - _h - _r), (max(0.0, _w - _h - _r) / _w * 100 if _w else 0)), flush=True)
        return delta

    def summary(self) -> Dict[str, Any]:
        s = dict(self.stats)
        s["account"] = self.account
        s["consumer"] = self.consumer
        s["window"] = "all_pending" if self.all_pending else "run_window"
        s["run_since"] = self.run_since
        s["claimed_ids"] = s["claimed_ids"][:200]
        s["wake_failed_ids"] = s["wake_failed_ids"][:200]
        return s


def main() -> int:
    ap = argparse.ArgumentParser(description="交易腿 agent 消费者循环（回测用；单独跑需已备好 DATABASE_URL/DATA_DIR）")
    ap.add_argument("--account", default=os.getenv("BT_AGENT_ACCOUNT", "stock"))
    ap.add_argument("--consumer", default="bt-fallback")
    ap.add_argument("--once", action="store_true", help="只轮询一次（默认循环到无 pending）")
    ap.add_argument("--all-pending", action="store_true", help="全量认领（生产语义，含孤儿单处置）")
    ap.add_argument("--max-total", type=int, default=0)
    a = ap.parse_args()
    loop = BtAgentLoop(account=a.account, consumer=a.consumer,
                       all_pending=a.all_pending, max_total=a.max_total)
    if a.once:
        print(json.dumps(loop.poll(), ensure_ascii=False, indent=1))
    else:
        while True:
            d = loop.poll()
            if not d.get("claim"):
                break
    print(json.dumps(loop.summary(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
