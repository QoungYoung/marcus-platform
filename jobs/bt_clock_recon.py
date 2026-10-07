# -*- coding: utf-8 -*-
"""bt_clock_recon.py — 回测侧「DB 真钟 vs 钉钟」对账与清扫（P1-a，2026-09-17）。

## 问题（审计 `.dsh-tmp/gap_audit_4items_20260917.md` §3-⛔3，实测）

生产 SQL 用 **DB 真钟**（`now()` / `CURRENT_DATE`），而驱动把 Python 侧钉到**回放日**
（`bt_prod_run._pin_clock_dynamic()`：`date.today()/datetime.now()/time.time()` 全部钉死）。
两者不同源 → 两个"永不触发"的门：

1. `t_db.claim_pending_trigger`（`t_db.py:401`）用 **钉钟** `cutoff = datetime.now() - 300s`
   去比 `created_at`（**真钟**，2026-09-16/17 ≫ 钉钟的 2026-03/08）→ 恒不满足
   → **孤儿 pending 永不过期**（本地 PG 实测 2,528 条 pending）；
2. `t_gateway.classify_escalation`（`t_gateway.py:835`）用 `(datetime.now() - claimed_at) > 2min`
   → 钉钟 − 真钟 < 0 → **人工确认超时门永不触发** → 本该转 human 的孤儿单仍走 auto/agent。

## 回测侧最小修法（**不动生产代码、不改 DB schema、不写生产表**）

本模块在**每根 bar**做两件用**显式 UPDATE** 完成的事（不依赖生产函数里的那两行 SQL）：

· **认领时间戳归一**：把"本进程运行期间被认领"的行的 `claimed_at` 从 DB 真钟改写为**钉钟**
  （观测到该认领的那根 bar 的钉钟时刻）。此后 `classify_escalation` 的
  `datetime.now() - claimed_at` **同源** → 超时门按生产语义真正生效；
· **孤儿 pending 清扫**：用**自己的钉钟台账**（`id → 首次被看到的钉钟时刻`）算 pending 的年龄，
  超过 `timeout_seconds`（默认 300，与 `t_db.claim_pending_trigger` 一致）→
  `UPDATE ... SET status='cancelled', reason='orphan_timeout'`（reason 与生产逐字一致）。

**为什么不把 `created_at` 也改成钉钟**（三个实测理由，必须留着真钟）：
  ① `bt_agent_loop` 默认分支用 `created_at >= to_timestamp(run_since)`（`run_since` = **真实 epoch**）
     筛"本次运行新产生的 pending"→ 改了钉钟就**一条都认领不到**；
  ② `t_monitor._consecutive_hits`（`t_monitor.py:2630`）用 `created_at::date = CURRENT_DATE`
     （`CURRENT_DATE` 也是真钟）→ 改了钉钟就恒为 0（"同条件连续命中"计数失效）；
  ③ 真钟 `created_at` 正是"本次运行窗口"的天然标记，不需要动它。
  → 所以 pending 的年龄**只能**由本模块的台账（钉钟）来算；这也是"回测侧那半"的全部代价。

## 装配（jobs/ 侧，零生产改动）

`bt_local_pro.install_local_pro()`（它由 `bt_prod_run` 在**钉钟之后、进 bar 循环之前**调用，并拿到
`LocalMarket` 实例）会调 `bt_clock_recon.install(market=market)`：把 `market.write_recent_sync()`
—— **每根 bar 的第一件事** —— 包一层，于是每 bar 自动 tick 一次。
（若驱动方愿意显式调用，等价写法是 `import bt_clock_recon; bt_clock_recon.install(mon)`。）

## 开关 / CLI

· `BT_CLOCK_RECON=0` 关掉（退回旧行为：真钟，两处门永不触发）；
· `BT_CLOCK_RECON_TIMEOUT`（默认 300s）、`BT_CLOCK_RECON_STALE_MIN`（默认 2min）、
  `BT_CLOCK_RECON_LEGACY=keep|expire`（默认 expire：**运行开始时就已经 pending** 的遗留行
  也在首次被看到后按钉钟计时过期 —— 生产语义如此；`keep` 则只报告不清扫）；
· `python jobs/bt_clock_recon.py --report --account stock` → 打印当前 t_triggers 状态分布 +
  pending 年龄分布（钉钟/真钟都列），用于逐日对账。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_env  # noqa: E402


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


DEFAULT_DB_URL = os.getenv("BT_PG_URL") or os.getenv("DATABASE_URL") or \
    "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
ORPHAN_REASON = "orphan_timeout"        # 与 t_db.claim_pending_trigger 逐字一致


def _env_i(name: str, d: int) -> int:
    try:
        return int(float(os.environ.get(name, str(d))))
    except (TypeError, ValueError):
        return d


def _pinned_now() -> _dt.datetime:
    """当前"现在"：驱动已钉钟 → 钉钟值；未钉钟（单跑本模块）→ 真实钟。"""
    return _dt.datetime.now()


def _enabled() -> bool:
    return str(os.environ.get("BT_CLOCK_RECON", "1")).strip().lower() not in ("0", "false", "no")


class ClockRecon:
    """每 bar 对账器（见模块 docstring）。线程不安全 —— 只在驱动进程里被单线程 bar 循环调用。"""

    def __init__(self, db_url: str = "", timeout_seconds: int = 0, stale_claim_min: int = 0,
                 legacy: str = "", verbose: bool = True, stamp_executed_at: bool = False,
                 account: str = "", search_path: str = ""):
        self.db_url = db_url or DEFAULT_DB_URL
        # search_path：只为**验收脚本**把读写钉到隔离 schema（如 bt_clock_probe）；
        # 正常驱动留空 = 与生产同一张 t_triggers。见 bt_clock_recon_probe.py 的隔离纪律。
        self.search_path = search_path
        self.timeout = timeout_seconds or _env_i("BT_CLOCK_RECON_TIMEOUT", 300)
        self.stale_min = stale_claim_min or _env_i("BT_CLOCK_RECON_STALE_MIN", 2)
        self.legacy = (legacy or os.environ.get("BT_CLOCK_RECON_LEGACY", "expire")).strip().lower()
        self.verbose = verbose
        self.stamp_executed_at = stamp_executed_at
        self.account = account                      # "" = 全账户（与生产清扫口径一致）
        self._conn = None
        self._pending_since: dict = {}              # id → 首次被看到的钉钟时刻（ISO 字符串）
        self._claim_seen: dict = {}                 # id → 观测到"已认领"的钉钟时刻
        self._stamped: dict = {}                    # id → 已写回的 claimed_at（防重复 UPDATE）
        self._boot_max_id = None
        self._last_pinned = ""
        self.stats = {"ticks": 0, "pending_adopted": 0, "pending_legacy_adopted": 0,
                      "orphan_cancelled": 0, "claimed_stamped": 0, "stale_claims_seen": 0,
                      "pending_seen": 0, "errors": 0, "first_tick": "", "last_tick": ""}

    # ── 连接 ──────────────────────────────────────────────────────────
    def _db(self):
        import psycopg2
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.db_url)
            if self.search_path:
                import re as _re
                if not _re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", self.search_path):
                    raise ValueError("非法 search_path: %r" % self.search_path)
                cur = self._conn.cursor()
                cur.execute("SET search_path TO %s, public" % self.search_path)
                self._conn.commit()
                cur.close()
        return self._conn

    def _q(self, sql: str, args=()):
        conn = self._db()
        cur = conn.cursor()
        try:
            cur.execute(sql, args)
            rows = cur.fetchall() if cur.description else []
            conn.commit()
            return rows
        finally:
            cur.close()

    # ── 主循环 ────────────────────────────────────────────────────────
    def tick(self, hhmm: str = "") -> dict:
        """一根 bar 一次：先归一认领戳，再清扫超时 pending。返回本次动作摘要。"""
        if not _enabled():
            return {"disabled": True}
        now = _pinned_now()
        now_s = now.strftime("%Y-%m-%d %H:%M:%S")
        act = {"at": now_s, "hhmm": hhmm, "stamped": 0, "orphan_cancelled": 0,
               "stale_claims": 0, "pending_open": 0, "adopted": 0}
        try:
            rows = self._q(
                "SELECT id, status, created_at, claimed_at, reason FROM t_triggers "
                "WHERE status IN ('pending','claimed')"
                + (" AND account_id = %s" if self.account else "") + " ORDER BY id",
                ((self.account,) if self.account else ()))
        except Exception as e:                      # 库不可用 → 本 bar 放弃（不阻断驱动）
            self.stats["errors"] += 1
            if self.verbose:
                print("[clockrecon] ⚠️ 扫描失败（本 bar 跳过）：%s" % str(e)[:120], flush=True)
            return act
        if self._boot_max_id is None:
            try:
                self._boot_max_id = int((self._q("SELECT COALESCE(max(id),0) FROM t_triggers") or [[0]])[0][0])
            except Exception:
                self._boot_max_id = 0
        if not self.stats["first_tick"]:
            self.stats["first_tick"] = now_s

        # ① 认领戳归一（真钟 → 钉钟）：只对"本进程运行期间"看到被认领的行动手
        to_stamp = []
        for r in rows:
            tid, status, created_at, claimed_at = int(r[0]), str(r[1]), r[2], r[3]
            if status != "claimed" or not claimed_at:
                continue
            seen = self._claim_seen.get(tid)
            if seen is None:
                self._claim_seen[tid] = now_s
                seen = now_s
            if self._stamped.get(tid) == seen:
                continue
            # claimed_at 比钉钟"未来"（真钟）→ 改写为**观测到认领那一刻的钉钟**
            c_s = claimed_at.strftime("%Y-%m-%d %H:%M:%S") if hasattr(claimed_at, "strftime") else str(claimed_at)[:19]
            if c_s <= now_s:
                continue                            # 已经是钉钟口径（本次运行早先写回的）→ 不动
            to_stamp.append((tid, seen))
        for tid, ts in to_stamp:
            try:
                self._q("UPDATE t_triggers SET claimed_at = %s WHERE id = %s", (ts, tid))
                self._stamped[tid] = ts
                act["stamped"] += 1
                self.stats["claimed_stamped"] += 1
                if self.verbose:
                    print("[clockrecon] %s 认领戳归一 id=%s → %s（钉钟）" % (hhmm, tid, ts), flush=True)
            except Exception as e:
                self.stats["errors"] += 1
                print("[clockrecon] ⚠️ claimed_at 归一失败 id=%s: %s" % (tid, str(e)[:80]), flush=True)

        # ② 孤儿 pending 清扫（钉钟台账计时）
        cutoff = (now - _dt.timedelta(seconds=self.timeout)).strftime("%Y-%m-%d %H:%M:%S")
        open_pending, cancel_ids = 0, []
        for r in rows:
            tid, status = int(r[0]), str(r[1])
            if status != "pending":
                continue
            open_pending += 1
            seen = self._pending_since.get(tid)
            if seen is None:
                self._pending_since[tid] = now_s
                act["adopted"] += 1
                self.stats["pending_adopted"] += 1
                if self._boot_max_id and tid <= self._boot_max_id:
                    self.stats["pending_legacy_adopted"] += 1
                    if self.legacy == "keep":
                        self._pending_since[tid] = "9999-12-31 00:00:00"   # 只报告不清扫
                continue
            if seen <= cutoff:
                cancel_ids.append(tid)
        act["pending_open"] = open_pending
        for tid in cancel_ids:
            try:
                self._q("UPDATE t_triggers SET status = 'cancelled', reason = %s "
                        "WHERE id = %s AND status = 'pending'", (ORPHAN_REASON, tid))
                self._pending_since.pop(tid, None)
                act["orphan_cancelled"] += 1
                self.stats["orphan_cancelled"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                print("[clockrecon] ⚠️ 孤儿单清扫失败 id=%s: %s" % (tid, str(e)[:80]), flush=True)
        if cancel_ids and self.verbose:
            print("[clockrecon] %s 孤儿 pending 置 cancelled ×%d（reason=%s，钉钟 cutoff=%s）"
                  % (hhmm, len(cancel_ids), ORPHAN_REASON, cutoff), flush=True)

        # ③ 超时 claimed 观测（不改状态：生产语义是"升级 human"，由 classify_escalation 判定）
        stale = 0
        for r in rows:
            tid, status = int(r[0]), str(r[1])
            if status != "claimed":
                continue
            ts = self._stamped.get(tid) or self._claim_seen.get(tid)
            if not ts:
                continue
            try:
                if (_dt.datetime.strptime(now_s, "%Y-%m-%d %H:%M:%S")
                        - _dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() > self.stale_min * 60:
                    stale += 1
            except ValueError as _e_sil1:
                _silent_alert("bt_clock_recon.py:241", _e_sil1)
                continue
        act["stale_claims"] = stale
        self.stats["stale_claims_seen"] += stale
        self.stats["pending_seen"] += open_pending
        self.stats["ticks"] += 1
        self.stats["last_tick"] = now_s
        self._last_pinned = now_s
        return act

    def stale_claim_ids(self, hhmm: str = "") -> list:
        """当前"钉钟口径下已超时"的 claimed 行 id（给验收脚本断言用）。"""
        now = _pinned_now().strftime("%Y-%m-%d %H:%M:%S")
        out = []
        for tid, ts in list(self._stamped.items()) + [(k, v) for k, v in self._claim_seen.items()
                                                      if k not in self._stamped]:
            if not ts or ts.startswith("9999"):
                continue
            try:
                dt = (_dt.datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
                      - _dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds()
            except ValueError as _e_sil2:
                _silent_alert("bt_clock_recon.py:262", _e_sil2)
                continue
            if dt > self.stale_min * 60:
                out.append(int(tid))
        return sorted(set(out))

    def summary(self) -> dict:
        return dict(self.stats)


_INST: ClockRecon = None


def install(market=None, mon=None, **kw) -> ClockRecon:
    """在当前进程装配对账器；`market` 有 `write_recent_sync` 时**每 bar 自动 tick**。

    幂等：重复调用只装配一次（返回同一个实例）。
    """
    global _INST
    if _INST is not None:
        return _INST
    if not _enabled():
        print("[clockrecon] BT_CLOCK_RECON=0 → 不装配（DB 真钟 vs 钉钟的两处门不会触发）", file=sys.stderr)
        return None
    _INST = ClockRecon(**kw)
    wrapped = ""
    if market is not None and hasattr(market, "write_recent_sync"):
        orig = market.write_recent_sync

        def _wrapped(*a, **k):
            try:
                _INST.tick(str(k.get("hhmm") or (a[2] if len(a) > 2 else "")))
            except Exception as e:
                _INST.stats["errors"] += 1
                print("[clockrecon] ⚠️ tick 异常（不阻断驱动）：%s" % str(e)[:120], file=sys.stderr)
            return orig(*a, **k)
        market.write_recent_sync = _wrapped
        wrapped = "market.write_recent_sync（每 bar 第一件事）"
    if mon is not None and hasattr(mon, "_round"):
        orig_r = mon._round
        mon._round = lambda *a, **k: (_INST.tick(""), orig_r(*a, **k))[1]
        wrapped = (wrapped + " + mon._round").strip(" +")
    print("[clockrecon] 已装配：db=%s timeout=%ss stale_claim=%smin legacy=%s 挂钩=%s"
          % (_INST.db_url.split("@")[-1], _INST.timeout, _INST.stale_min, _INST.legacy,
             wrapped or "(无：需手动 tick)"), file=sys.stderr)
    return _INST


def instance() -> ClockRecon:
    return _INST


# ── CLI：状态快照（逐日对账用）─────────────────────────────────────────────
def snapshot(db_url: str = "", account: str = "stock") -> dict:
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(db_url or DEFAULT_DB_URL)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        where = " WHERE account_id = %s" if account else ""
        args = (account,) if account else ()
        cur.execute("SELECT status, count(*) AS n FROM t_triggers" + where + " GROUP BY status ORDER BY n DESC",
                    args)
        dist = {r["status"]: int(r["n"]) for r in cur.fetchall()}
        now = _pinned_now()
        cur.execute("SELECT now() AS db_now")
        db_now = cur.fetchone()["db_now"]
        cur.execute("SELECT count(*) AS n FROM t_triggers" + where +
                    (" AND" if where else " WHERE") + " status='pending' AND created_at < %s",
                    args + ((now - _dt.timedelta(seconds=300)).strftime("%Y-%m-%d %H:%M:%S"),))
        pinned_orphans = int(cur.fetchone()["n"])
        cur.execute("SELECT count(*) AS n FROM t_triggers" + where +
                    (" AND" if where else " WHERE") + " status='pending' AND created_at < %s",
                    args + ((db_now - _dt.timedelta(seconds=300)).strftime("%Y-%m-%d %H:%M:%S"),))
        real_orphans = int(cur.fetchone()["n"])
        cur.execute("SELECT min(created_at) AS a, max(created_at) AS b FROM t_triggers" + where +
                    (" AND" if where else " WHERE") + " status='pending'", args)
        rng = dict(cur.fetchone())
        cur.execute("SELECT count(*) AS n FROM t_triggers" + where +
                    (" AND" if where else " WHERE") + " status='claimed'", args)
        claimed = int(cur.fetchone()["n"])
        return {"pinned_now": now.strftime("%Y-%m-%d %H:%M:%S"), "db_now": str(db_now)[:19],
                "status": dist, "claimed": claimed, "pending_created_range": rng,
                "pending_orphan_by_pinned_clock": pinned_orphans,
                "pending_orphan_by_db_clock": real_orphans}
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="打印 t_triggers 状态快照")
    ap.add_argument("--db-url", default="")
    ap.add_argument("--account", default="stock")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not a.report:
        ap.print_help()
        return 0
    snap = snapshot(a.db_url, a.account)
    print(json.dumps(snap, ensure_ascii=False, indent=1) if a.json else
          "[clockrecon] 账户=%s 钉钟=%s DB真钟=%s\n  status=%s\n  pending 创建区间=%s\n"
          "  按**钉钟**算已超时孤儿 pending=%d（生产 SQL 用的就是这一支）\n"
          "  按**DB真钟**算=%d（生产实际生效的是这一支 → 永不触发）"
          % (a.account, snap["pinned_now"], snap["db_now"], snap["status"],
             snap["pending_created_range"], snap["pending_orphan_by_pinned_clock"],
             snap["pending_orphan_by_db_clock"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
