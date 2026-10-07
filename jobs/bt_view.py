#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_view.py — 回测臂的**只读**账户视图（用户 2026-09-21：「那我该怎么看啊，刷新了还有」）

问题背景：本地回测库里躺着 **45 个账户 / 151 条持仓行**（drabt1/t2/t3/t4、draby26、drabj13…），
而 Marcus 界面的账户选择器把选择存在浏览器 localStorage（键 `marcus-account-storage`）——
**刷新页面既不会重置账户选择，也不会清掉库里的历史遗留**，所以"刷新了还有"。

这个脚本给一个**按账户隔离**的正确视图（只读，不写任何东西）：
    python -u jobs/bt_view.py                 # 默认看当前在跑的臂（读 .dsh-tmp/wolfbt/asof_state_t*.json 里最新的）
    python -u jobs/bt_view.py --account drabt4
    python -u jobs/bt_view.py --all           # 只列出所有账户的持仓概览（含停用的臂）
"""
import argparse
import glob
import json
import os
import sqlite3
import sys


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


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
BARS = os.path.join(REPO, "data", "_bt_full", "bars.sqlite")


def _latest_running_arm():
    """挑出**正在跑**的那条臂：优先看有没有活着的 bt_days 进程（env 里带 _bt_<tag>），
    退化为按 state 文件的**真实 mtime**（不能用 written_at —— 那是模拟时间，跨臂不可比）。"""
    import subprocess
    try:
        out = subprocess.run(["bash", "-c", "ps -ef | grep '[b]t_days.py' | head -3"],
                             capture_output=True, text=True, timeout=10).stdout
        for tok in out.split():
            if tok.startswith("/") and "_bt_" in tok:
                return tok.rstrip("/").split("_bt_")[-1]
    except Exception as _e_sil1:
        _silent_alert("bt_view.py:37", _e_sil1)
    files = glob.glob(os.path.join(REPO, ".dsh-tmp/wolfbt/asof_state_t*.json"))
    if not files:
        return "t4"
    f = max(files, key=os.path.getmtime)
    return os.path.basename(f)[len("asof_state_"):-len(".json")]


def _px(code6, day):
    try:
        con = sqlite3.connect("file:%s?mode=ro" % BARS, uri=True)
        ts = (code6[2:] + "." + code6[:2]) if code6[:2] in ("SH", "SZ") else code6
        r = con.execute("select close from bars where ts_code=? and trade_date<=? "
                        "order by trade_date desc limit 1", (ts, day)).fetchone()
        con.close()
        return float(r[0]) if r else None
    except Exception:
        return None


def show(account, verbose=True):
    import psycopg2
    con = psycopg2.connect(DB)
    cur = con.cursor()
    cur.execute("select available_cash, initial_capital, updated_at from paper_account_info where account_id=%s",
                (account,))
    r = cur.fetchone()
    if not r:
        print("账户 %s 不存在" % account)
        return
    cash, init, upd = float(r[0]), float(r[1] or 250000), r[2]
    day = str(upd)[:10].replace("-", "") if upd else ""
    cur.execute("select symbol, volume, frozen, avg_price, entry_date from paper_positions "
                "where account_id=%s and volume>0 order by symbol", (account,))
    pos = cur.fetchall()
    cur.execute("select count(*) from paper_trades where account_id=%s", (account,))
    ntr = int(cur.fetchone()[0] or 0)
    # 进度
    st = os.path.join(REPO, ".dsh-tmp/wolfbt/asof_state_%s.json" % account.replace("drab", ""))
    prog = ""
    if os.path.exists(st):
        try:
            d = json.load(open(st, encoding="utf-8"))
            prog = "%s %s（%s）" % (d.get("day8"), d.get("bar"), d.get("account"))
        except Exception as _e_sil2:
            _silent_alert("bt_view.py:82", _e_sil2)
    mv = 0.0
    print("═══ 账户 %s ═══ %s" % (account, ("进度 " + prog) if prog else ""))
    print("  初始 %.0f  现金 %.0f  快照时间 %s" % (init, cash, upd))
    if verbose:
        for s, v, fz, ap, ed in pos:
            p = _px(s, day) or float(ap or 0)
            val = v * p
            mv += val
            print("  持仓 %-10s %6d 股  成本 %7.2f  现价 %7.2f  市值 %9.0f  建仓 %s"
                  % (s, v, float(ap or 0), p, val, ed))
        if not pos:
            print("  持仓：无")
    tot = cash + mv
    print("  总资产 %.0f  收益 %+.0f（%+.2f%%）  成交累计 %d 笔"
          % (tot, tot - init, 100.0 * (tot - init) / init, ntr))
    con.close()


def show_all():
    import psycopg2
    con = psycopg2.connect(DB)
    cur = con.cursor()
    cur.execute("""select p.account_id, count(*) as n, sum(p.volume) as vol
                   from paper_positions p where p.volume>0 group by 1 order by 2 desc""")
    rows = cur.fetchall()
    print("本地回测库里**所有**账户的持仓（这就是界面会混着显示的东西）：")
    print("  %-14s %6s %10s" % ("账户", "持仓只数", "股数"))
    for acc, n, vol in rows:
        print("  %-14s %6d %10d" % (acc, n, int(vol or 0)))
    print("  合计 %d 个账户 / %d 行" % (len(rows), sum(int(r[1]) for r in rows)))
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.all:
        show_all()
    else:
        acc = a.account or ("drab" + _latest_running_arm())
        show(acc)
