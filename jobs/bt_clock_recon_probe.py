# -*- coding: utf-8 -*-
"""bt_clock_recon_probe.py — P1-a 的**隔离验收**：证明"DB 真钟 vs 钉钟"两处门修好了。

审计结论（`.dsh-tmp/gap_audit_4items_20260917.md` §3-⛔3）：
  · `t_db.claim_pending_trigger`（`t_db.py:401`）用**钉钟** cutoff 比 `created_at`（**真钟**）
    → 孤儿 pending 永不过期；
  · `t_gateway.classify_escalation`（`t_gateway.py:835`）用 `datetime.now() - claimed_at`
    → 钉钟 − 真钟 < 0 → **人工确认超时门永不触发**。

本脚本**不碰年跑的数据**：在本地 PG 里新建独立 schema `bt_clock_probe`
（`t_triggers` / `paper_positions` / `paper_trades` / `paper_account_info` / `t_risk_state` 的
DDL 从生产库逐列复制，**零行数据**），SQLAlchemy 连接用 connect 事件把 search_path 钉到该 schema
（`app.database.engine`），于是 `t_db` / `t_gateway` 的读写全部落在隔离 schema 里。

流程（全部在钉钟下跑，钉钟 = `--day/--hhmm`）：
  1. 造行：1 条 pending（created_at = DB `now()` = 真钟）、1 条 claimed（claimed_at = DB `now()`）；
  2. **BEFORE**：
     a. 调**生产** `t_db.claim_pending_trigger()` → 看孤儿 pending 是否被 cancelled（预期：不会）；
     b. 调**生产** `t_gateway.classify_escalation()` → 看 claimed 行是否被判"孤儿单超时"（预期：不会）；
  3. 调 `bt_clock_recon.ClockRecon.tick()`（钉钟推进 5 分钟后再 tick 一次）→ 归一 claimed_at + 清扫 pending；
  4. **AFTER**：重复 (a)(b) → 预期 pending 被 cancelled(reason=orphan_timeout)、claimed 行被判 human。

用法：
  python jobs/bt_clock_recon_probe.py --day 20260320 --hhmm 10:05 \
      --out .dsh-tmp/bt_clock_probe/report.json [--keep]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_env  # noqa: E402

SCHEMA = "bt_clock_probe"          # 仅用于 DDL 生成时读源库（不再建 schema）
PROBE_DB = "bt_clock_probe"        # ★ 探针用**独立 database**：SQLAlchemy 会回滚连接上的 SET search_path
PG = os.getenv("BT_PG_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
PG_PROBE = PG.rsplit("/", 1)[0] + "/" + PROBE_DB
TABLES = ("t_triggers", "paper_positions", "paper_trades", "paper_account_info", "t_risk_state")


def _ddl(conn, table: str) -> str:
    """从生产库 information_schema 逐列拼 CREATE TABLE（含默认值），不复制任何行。"""
    cur = conn.cursor()
    cur.execute("""SELECT column_name, data_type, character_maximum_length, numeric_precision,
                          numeric_scale, column_default, is_nullable
                   FROM information_schema.columns WHERE table_schema='public' AND table_name=%s
                   ORDER BY ordinal_position""", (table,))
    cols = []
    for name, dtype, clen, nprec, nscale, dflt, nullable in cur.fetchall():
        t = dtype
        if dtype == "character varying":
            t = "character varying(%s)" % (clen or 255)
        elif dtype in ("numeric", "decimal") and nprec:
            t = "numeric(%s,%s)" % (nprec, nscale or 0)
        piece = '"%s" %s' % (name, t)
        if dflt:
            piece += " DEFAULT %s" % dflt
        if nullable == "NO":
            piece += " NOT NULL"
        cols.append(piece)
    cur.execute("""SELECT a.attname FROM pg_index i JOIN pg_attribute a
                   ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
                   WHERE i.indrelid = ('public.%s')::regclass AND i.indisprimary""" % table)
    pk = [r[0] for r in cur.fetchall()]
    if pk:
        cols.append("PRIMARY KEY (%s)" % ",".join('"%s"' % c for c in pk))
    cur.close()
    return 'CREATE TABLE IF NOT EXISTS "%s"."%s" (%s)' % (SCHEMA, table, ", ".join(cols))


def setup_db(src_conn):
    import psycopg2
    """建独立 database `bt_clock_probe` 并复制 5 张表的 DDL（**零行数据**）。

    ⚠️ 为什么是 database 而不是 schema（2026-09-17 两次实测事故的结论）：
      用 `SET search_path TO <schema>, public` 隔离时，**`engine.connect()` 看到的是探针 schema，
      但 `SessionLocal()` 看到的是 public** —— 因为 `set search_path` 是在隐式事务里执行的，
      连接交还连接池/事务回滚后**被撤销** → `t_db.claim_pending_trigger()` 落到了 public.t_triggers
      （实测把 2,536 条 pending 扫成 cancelled/orphan_timeout、误认领 1 条；已用年跑自己的
      `prod_*.json` 逐行还原两次）。独立 database 不依赖任何 search_path 技巧，物理上到不了 public。
    """
    # CREATE/DROP DATABASE 不能在事务块里 → 用 autocommit 连接
    src_conn.autocommit = True
    cur = src_conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (PROBE_DB,))
    if cur.fetchone():
        cur.execute("DROP DATABASE %s WITH (FORCE)" % PROBE_DB)
    cur.execute("CREATE DATABASE %s" % PROBE_DB)
    src_conn.autocommit = False
    ddl = [_ddl(src_conn, t) for t in TABLES]      # DDL 从源库读（含默认值/主键）
    cur.close()
    probe = psycopg2.connect(PG_PROBE)
    pc = probe.cursor()
    import re as _re
    seqs = set()
    for stmt in ddl:
        seqs |= set(_re.findall(r"nextval\('([A-Za-z0-9_]+)'", stmt))
    for sq in sorted(seqs):                                # DEFAULT nextval(...) 依赖的序列要先建
        pc.execute("CREATE SEQUENCE IF NOT EXISTS %s" % sq)
    for stmt in ddl:
        pc.execute(stmt.replace('"%s".' % SCHEMA, ""))     # 在探针库里建在默认 schema
    probe.commit(); pc.close()
    return probe


def teardown(src_conn):
    src_conn.rollback()
    src_conn.autocommit = True
    cur = src_conn.cursor()
    try:
        cur.execute("DROP DATABASE %s WITH (FORCE)" % PROBE_DB)
    except Exception as e:
        print("[probe] ⚠️ 删除探针库失败：%s" % str(e)[:80], flush=True)
    cur.close()
    src_conn.autocommit = False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="20260320", help="钉钟日期（回放日）")
    ap.add_argument("--hhmm", default="10:05", help="钉钟时刻（bar）")
    ap.add_argument("--symbol", default="SH512480", help="探针标的（side=sell 走孤儿单分支）")
    ap.add_argument("--out", default="")
    ap.add_argument("--keep", action="store_true", help="保留隔离库（默认跑完删掉）")
    a = ap.parse_args()

    rep = {"day": a.day, "hhmm": a.hhmm, "symbol": a.symbol, "steps": {}}

    # ① 钉钟（与 bt_prod_run 同一实现：先钉钟，再 import 生产模块）
    import bt_prod_run as B
    B._pin_clock_dynamic()
    B.set_now(a.day, a.hhmm)
    pinned1 = dt.datetime.now()

    # ② 隔离 schema + SQLAlchemy search_path（在 import app.database 之前设好 PGOPTIONS 也不行
    #    —— SQLAlchemy 会把 search_path 重置回 public；用 connect 事件在**每个连接**上 SET）
    import psycopg2
    src_conn = psycopg2.connect(PG)          # 只用来读 DDL / 建删探针库
    conn = setup_db(src_conn)                # 探针自己的连接（落在探针库里）
    os.environ["DATABASE_URL"] = PG_PROBE
    rep["steps"]["probe_db"] = PROBE_DB

    import sqlalchemy
    import app.database as adb
    adb.engine.dispose()
    # ── 隔离硬校验（必须走**生产函数用的那条链路** `t_db.SessionLocal`）──────────────
    import app.services.t_db as _tdb_chk
    _s = _tdb_chk.SessionLocal()
    _dbname = _s.execute(sqlalchemy.text("select current_database()")).scalar()
    _rows = int(_s.execute(sqlalchemy.text("select count(*) from t_triggers")).scalar() or 0)
    _s.close()
    rep["steps"]["sessionlocal_database"] = _dbname
    rep["steps"]["unqualified_rows"] = _rows
    print("[probe] t_db.SessionLocal → database=%s，未限定名的 t_triggers 行数=%d" % (_dbname, _rows), flush=True)
    if _dbname != PROBE_DB or _rows > 100:
        print("[probe] ⛔ 隔离失败（database=%s / 行数=%d）→ 立即中止，绝不在生产库上跑"
              % (_dbname, _rows), flush=True)
        return 3

    import app.services.t_db as t_db
    import app.services.t_gateway as t_gw

    # ③ 造行 A：**claimed** 行（claimed_at 用 DB 真钟 —— 与修复前生产 SQL `now()` 同源）
    cur = conn.cursor()
    cur.execute("DELETE FROM t_triggers")
    cur.execute("""INSERT INTO t_triggers
                   (account_id, symbol, event_type, status, mode, reason, created_at, claimed_at, claimed_by)
                   VALUES ('stock', %s, 'custom_m5dump', 'claimed', 'agent', 'probe_claimed',
                           now(), now(), 'probe') RETURNING id, claimed_at""", (a.symbol,))
    claim_id, claim_at_raw = cur.fetchone()
    conn.commit(); cur.close()
    print("[probe] 钉钟=%s；造 claimed 行 id=%s（claimed_at = DB 真钟 %s）" % (pinned1, claim_id, claim_at_raw),
          flush=True)

    def _row(tid):
        c = conn.cursor()
        c.execute("SELECT id, status, reason, claimed_at, created_at FROM t_triggers WHERE id=%s", (tid,))
        r = c.fetchone(); c.close()
        return {"id": r[0], "status": r[1], "reason": r[2], "claimed_at": str(r[3]), "created_at": str(r[4])}

    def _esc(tid):
        row = _row(tid)
        trig = {"id": row["id"], "status": row["status"], "claimed_at": row["claimed_at"]}
        try:
            return t_gw.classify_escalation(a.symbol, "sell", trig)
        except Exception as e:
            return ("<err>", "%s: %s" % (type(e).__name__, str(e)[:100]))

    # ④ BEFORE：claimed 行在**生产函数**下的表现（钉钟 − 真钟 < 0）
    rep["steps"]["before_escalation"] = list(_esc(claim_id))
    rep["steps"]["sweep_clock_source"] = t_db.sweep_clock()
    rep["steps"]["claimed_at_clock_source"] = t_db.claimed_at_clock()
    print("[probe] BEFORE：classify_escalation(claimed) → %s（生产 t_db 钟源：sweep=%s claimed_at=%s）"
          % (rep["steps"]["before_escalation"], rep["steps"]["sweep_clock_source"],
             rep["steps"]["claimed_at_clock_source"]), flush=True)

    # ⑤ 对账器 tick1：把 claimed_at 从 DB 真钟归一到**钉钟**
    import bt_clock_recon as CR
    rec = CR.ClockRecon(db_url=PG_PROBE, verbose=True)
    t1 = rec.tick(a.hhmm)
    rep["steps"]["tick1"] = t1
    print("[probe] tick1=%s" % t1, flush=True)

    # ⑥ 造行 B：**pending** 行（真钟 created_at）→ 钉钟推进 5 分钟 → 应由对账器按**钉钟**清掉；
    #    生产那支清扫比的是 DB 墙钟（`sweep_clock()=db`），钉钟走 5 分钟时现实只过了几十秒 → 不动它。
    cur = conn.cursor()
    cur.execute("""INSERT INTO t_triggers
                   (account_id, symbol, event_type, status, mode, reason, created_at)
                   VALUES ('stock', %s, 'custom_m5dump', 'pending', 'agent', 'probe_pending', now())
                   RETURNING id""", (a.symbol,))
    pend_id = cur.fetchone()[0]
    conn.commit(); cur.close()
    t1b = rec.tick(a.hhmm)                      # 采纳（first_seen = 钉钟 10:05）
    B.set_now(a.day, "10:10")                   # 下一根 bar：钉钟 +5min
    t2 = rec.tick("10:10")                      # 按钉钟台账清扫
    rep["steps"]["tick1b_adopt"] = t1b
    rep["steps"]["tick2"] = t2
    print("[probe] pending id=%s；对账器 tick1b=%s\ntick2=%s" % (pend_id, t1b, t2), flush=True)

    # ⑥b AFTER
    rep["steps"]["after_pending"] = _row(pend_id)
    rep["steps"]["after_claimed"] = _row(claim_id)
    rep["steps"]["after_escalation"] = list(_esc(claim_id))
    print("[probe] AFTER ：pending id=%s → status=%s reason=%s；claimed id=%s claimed_at=%s；"
          "classify_escalation → %s"
          % (pend_id, rep["steps"]["after_pending"]["status"], rep["steps"]["after_pending"]["reason"],
             claim_id, rep["steps"]["after_claimed"]["claimed_at"], rep["steps"]["after_escalation"]),
          flush=True)

    # ⑦ 判定
    # pending：**钉钟** 5 分钟（两根 bar）后应由对账器清扫；生产那支（DB 同源钟）在回放里
    #          比的是**真实墙钟**，所以 BEFORE 那一步不会动它。
    ok_pending = (rep["steps"]["after_pending"]["status"] == "cancelled"
                  and rep["steps"]["after_pending"]["reason"] == "orphan_timeout"
                  and int(rep["steps"]["tick2"].get("orphan_cancelled") or 0) >= 1)
    # claimed：要求"超时 → human"这一结论成立，并记下**是哪一层**给的：
    #   · 经典 2 分钟超时（claimed_at 已被对账器归一到钉钟）→ `孤儿单超时未确认`
    #   · 生产 2026-09-17 08:08 新加的"时钟不同源"分支（claimed_at 超前进程钟）→ `不同源`
    _why_after = str(rep["steps"]["after_escalation"][1])
    ok_claimed = (rep["steps"]["after_escalation"][0] == "human" and "孤儿单" in _why_after)
    layer = ("classic_2min(claimed_at 已归一到钉钟)" if "超时未确认" in _why_after
             else ("prod_code_mismatch_branch(生产侧新分支)" if "不同源" in _why_after else "none"))
    rep["verdict"] = {"pending_orphan_sweep_by_pinned_clock": ok_pending,
                      "claimed_human_timeout": ok_claimed, "claimed_timeout_layer": layer,
                      "pass": bool(ok_pending and ok_claimed)}
    print("[probe] 判定：孤儿 pending 清扫（钉钟）%s / claimed 人工超时 %s（层=%s）→ %s"
          % ("PASS ✅" if ok_pending else "FAIL ⛔", "PASS ✅" if ok_claimed else "FAIL ⛔",
             layer, "全部 PASS ✅" if rep["verdict"]["pass"] else "存在 FAIL ⛔"), flush=True)

    conn.close()
    if not a.keep:
        teardown(src_conn)
        print("[probe] 隔离库 %s 已删除（未触碰年跑数据）" % PROBE_DB, flush=True)
    src_conn.close()
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print("[probe] 报告 → %s" % a.out, flush=True)
    return 0 if rep["verdict"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
