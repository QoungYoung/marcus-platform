# -*- coding: utf-8 -*-
"""作废一笔成交（账本 §9.747）—— 用于"事后认定该拦"的买入回收

用户 2026-10-07：「撤回那笔该拦的买入吧」✓
  → 指 `2026-01-21 SH688403 汇成股份 买入 23.050×4400 = ¥101,420`（`trend_break_buy` ✓），
    当日浪型 `d3/3-4 · operation=t_only` ✗ ⇒ 按狼大口径「只做T、**不追不新建主升**」✓ **它该拦** ✓
    （对照 ✓：0105 SZ002156、0114 SZ300346 两笔同为 `trend_break_buy`，但当日 `operation=build` ✓
      ⇒ 那两笔**不拦** ✓ —— 判据是"当日档"，不是腿型本身 ✓）

做法 ✓（**照 `jobs/repair_paper_contamination.py` 的先例** ✓）：
  ① **作废成交** ✓：`voided=1` ＋ `void_reason` ＋ `voided_at`（**保留审计痕迹，不物理删除** ✓）
  ② **删持仓** ✓：仅当"持仓量 == 该笔数量 且 entry_date == 该笔交易日" ✓（防止误删后来的加仓 ✓）
  ③ **退现金** ✓：`amount × (1 + 费率)` —— 费率取**引擎同口径** ✓
     （`paper_engine.buy()` 扣的是 `price*volume*1.0005` ✓ ⇒ 默认 **0.0005** ✓）
  ④ 打印前后对照 ✓（决策依据 ＋ 现金/持仓变化 ✓）

安全 ✓：
  · 默认 **dry-run** ✓，只有 `--apply` 才写库 ✓
  · 写前**四道校验**（方向/未作废/无后续同标的成交/持仓与成交一致 ✓），任一不符即**拒绝执行** ✗
  · 订单行**保留** ✓（它是"曾发生过"的审计记录 ✓，只是成交被作废 ✓）

用法 ✓：
  .venv/bin/python jobs/bt_void_trade.py --account drabt35d --id 12568 --reason "…"            # 只看
  .venv/bin/python jobs/bt_void_trade.py --account drabt35d --id 12568 --reason "…" --apply    # 落库
"""
from __future__ import annotations

import argparse
import os
import sys

DSN_DEFAULT = "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
DEFAULT_FEE = 0.0005          # ★ 与 `paper_engine.buy()` 的 `price*volume*1.0005` 同口径 ✓


def main() -> int:
    ap = argparse.ArgumentParser(description="作废一笔成交（默认 dry-run ✓）")
    ap.add_argument("--account", required=True)
    ap.add_argument("--id", type=int, required=True, help="paper_trades.id ✓")
    ap.add_argument("--reason", default="", help="作废原因（写进 void_reason ✓）")
    ap.add_argument("--fee", type=float, default=DEFAULT_FEE, help="买入费率（默认 0.0005 ✓ 与引擎同口径）")
    ap.add_argument("--dsn", default=os.getenv("DATABASE_URL") or DSN_DEFAULT)
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只打印 ✓）")
    ap.add_argument("--disarm-conditions", action="store_true",
                    help="同时把该标的**未结束的条件**置为 expired/armed=0 ✓"
                         "（仓位已回收 ⇒ 它的卖出腿不该再武装 ✗，否则今天剩余时段会去卖不存在的仓 ✓）")
    a = ap.parse_args()

    try:
        import psycopg2
        import psycopg2.extras
    except Exception as exc:  # noqa: BLE001
        print("  ✗ 缺 psycopg2：%s" % exc)
        return 2

    conn = psycopg2.connect(a.dsn, connect_timeout=8)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM paper_trades WHERE id=%s AND account_id=%s", (a.id, a.account))
    t = cur.fetchone()
    if not t:
        print("  ✗ 找不到成交 id=%s（account=%s）" % (a.id, a.account))
        return 2
    sym, day = t["symbol"], str(t["trade_date"] or "")[:10]
    amt = float(t["amount"] or 0) or float(t["price"]) * int(t["volume"])
    refund = amt * (1.0 + a.fee)
    print("  目标成交 ⇒ #%s %s %s %s %.3f×%s = ¥%.2f ｜ 已作废=%s"
          % (t["id"], day, sym, t["direction"], float(t["price"]), t["volume"], amt, t.get("voided")))
    print("  理由行 ⇒ %s" % str(t.get("reason"))[:110])

    # ── 四道校验 ✓ ──────────────────────────────────────────────
    errs = []
    if "买" not in str(t["direction"]) and str(t["direction"]).lower() != "buy":
        errs.append("该笔不是买入（本脚本只处理买入回收 ✗）")
    if t.get("voided"):
        errs.append("该笔**已作废** ✗")
    cur.execute("""SELECT count(*) n FROM paper_trades
                   WHERE account_id=%s AND symbol=%s AND id > %s AND coalesce(voided,0)=0""",
                (a.account, sym, a.id))
    n_after = int((cur.fetchone() or {}).get("n") or 0)
    if n_after:
        errs.append("该标的在它之后还有 %d 笔未作废成交 ✗（回收会破坏 FIFO/成本，请人工处理 ✓）" % n_after)
    cur.execute("""SELECT symbol, volume, avg_price, entry_date FROM paper_positions
                   WHERE account_id=%s AND symbol=%s""", (a.account, sym))
    pos = cur.fetchone()
    if not pos:
        errs.append("找不到对应持仓 ✗")
    else:
        if int(pos["volume"] or 0) != int(t["volume"] or 0):
            errs.append("持仓量 %s ≠ 该笔数量 %s ✗（后续有加/减仓 ⇒ 不自动回收 ✓）"
                        % (pos["volume"], t["volume"]))
        if str(pos["entry_date"] or "")[:10] != day:
            errs.append("持仓建仓日 %s ≠ 该笔交易日 %s ✗" % (pos["entry_date"], day))
    cur.execute("SELECT available_cash, order_counter FROM paper_account_info WHERE account_id=%s", (a.account,))
    acc = cur.fetchone() or {}
    cash0 = float(acc.get("available_cash") or 0)

    print("  ── 校验：%s" % ("**全部通过 ✓**" if not errs else "**未通过 ✗**"))
    for e in errs:
        print("     ✗ %s" % e)
    print("  ── 将执行（%s ✓）:" % ("--apply" if a.apply else "dry-run"))
    print("     ① 作废成交 #%s（voided=1 ＋ void_reason ✓ 保留审计痕迹 ✓）" % a.id)
    print("     ② 删持仓 %s（%s 股 @%s，建仓 %s ✓）" % (sym, t["volume"], t["price"], day))
    print("     ③ 退现金 ¥%.4f（= ¥%.2f × (1+%.6f) ✓ 与引擎同口径 ✓）" % (refund, amt, a.fee))
    print("        现金：%.4f ⇒ **%.4f**" % (cash0, cash0 + refund))
    n_cond = 0
    if a.disarm_conditions:
        cur.execute("""SELECT count(*) n FROM t_conditions
                       WHERE account_id=%s AND symbol=%s AND status='active'""", (a.account, sym))
        n_cond = int((cur.fetchone() or {}).get("n") or 0)
        print("     ④ 停掉该标的 %d 条未结束条件（status='expired' ＋ armed=0 ✓）" % n_cond)
    if errs:
        print("  ⇒ 拒绝执行 ✗（校验未过 ✓）")
        conn.rollback(); conn.close()
        return 3
    if not a.apply:
        print("  （dry-run ✓ 未写库；要落库请加 --apply ✓）")
        conn.rollback(); conn.close()
        return 0

    with conn.cursor() as c2:
        c2.execute("""UPDATE paper_trades SET voided=1, void_reason=%s, voided_at=now()
                      WHERE id=%s AND account_id=%s AND coalesce(voided,0)=0""", (a.reason or "MANUAL_VOID", a.id, a.account))
        n1 = c2.rowcount
        c2.execute("""DELETE FROM paper_positions
                      WHERE account_id=%s AND symbol=%s AND entry_date=%s AND volume=%s""",
                   (a.account, sym, pos["entry_date"], t["volume"]))
        n2 = c2.rowcount
        c2.execute("""UPDATE paper_account_info SET available_cash = available_cash + %s, updated_at=now()
                      WHERE account_id=%s""", (refund, a.account))
        n3 = c2.rowcount
        n4 = 0
        if a.disarm_conditions:
            c2.execute("""UPDATE t_conditions SET status='expired', armed=0
                          WHERE account_id=%s AND symbol=%s AND status='active'""", (a.account, sym))
            n4 = c2.rowcount
    if n1 != 1 or n2 != 1 or n3 != 1:
        print("  ✗ 落库结果异常（作废 %s / 删持仓 %s / 账户 %s）⇒ 回滚 ✓" % (n1, n2, n3))
        conn.rollback(); conn.close()
        return 4
    conn.commit()

    cur.execute("SELECT available_cash, order_counter FROM paper_account_info WHERE account_id=%s", (a.account,))
    acc2 = cur.fetchone() or {}
    cur.execute("SELECT count(*) n FROM paper_trades WHERE account_id=%s AND coalesce(voided,0)=0", (a.account,))
    n_live = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute("SELECT count(*) n FROM paper_positions WHERE account_id=%s", (a.account,))
    n_pos = int((cur.fetchone() or {}).get("n") or 0)
    conn.close()
    print("  ✅ 已作废并回收 ✓（成交作废 %d ／ 删持仓 %d ／ 账户更新 %d ／ 停条件 %d ✓）" % (n1, n2, n3, n4))
    print("     现金 ⇒ **%.4f** ｜ 未作废成交 %d 笔 ｜ 持仓 %d 只" % (float(acc2.get("available_cash") or 0), n_live, n_pos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
