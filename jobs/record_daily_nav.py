# -*- coding: utf-8 -*-
"""record_daily_nav.py —— **记录账户净值**（账本 §9.524，用户「记录下来吧」）

**为什么需要**：只看"期末持仓"会误判（3 月过程回撤看不到、4 月兑现看不到）✓
  ⇒ 每记一行（现金 ＋ 持仓按**当日收盘**标记 ✓）⇒ 过程与兑现画在同一条曲线上 ✓

**关键坑（都已踩过 ✓）**：
  1 符号格式 ✓：PG 存 `SZ301511`，而 `bars.sqlite` 用 `301511.SZ`（§9.477 ✓）
  2 **不做前视** ✓：只取 `trade_date <= 当日` 的收盘 ✓（当日取不到 ⇒ 退最近一根 ✓）
  3 现金 ≠ 净值 ✗：必须加**持仓市值** ✓（逐日 JSON 里 `positions` 是空的 ✗）

**输出**：`<DATA_DIR>/nav.jsonl` ✓（每行：`{"day","at","cash","mv","equity","ret","n_pos"}``）
用法：`.venv/bin/python jobs/record_daily_nav.py [--day 20260428] [--account drabt35]`
"""
from __future__ import annotations
import json, os, sqlite3, sys, time


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


def conv(s: str) -> str:
    s = str(s).strip().upper()
    return (s[2:] + "." + s[:2]) if (len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ")) else s


def main() -> int:
    day = ""
    for i, a in enumerate(sys.argv):
        if a == "--day" and i + 1 < len(sys.argv):
            day = sys.argv[i + 1]
    if not day:
        b = os.path.basename(str(os.getenv("DATA_DIR") or "").strip())
        day = b if (len(b) == 8 and b.isdigit()) else time.strftime("%Y%m%d")
    acc = os.getenv("T_MONITOR_ACCOUNT", "drabt35") or "drabt35"
    dsn = os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
    cash = mv = 0.0
    n_pos = 0
    try:
        import psycopg2
        cn = psycopg2.connect(dsn, connect_timeout=4)
        cn.set_session(readonly=True, autocommit=True)
        c = cn.cursor()
        c.execute("SELECT available_cash, frozen_cash FROM paper_account_info WHERE account_id=%s", (acc,))
        r = c.fetchone()
        if r:
            cash = float(r[0] or 0) + float(r[1] or 0)
        c.execute("SELECT symbol, volume FROM paper_positions WHERE account_id=%s", (acc,))
        poss = c.fetchall()
        cn.close()
    except Exception as e:
        print("  [nav] 账户读取失败: %s" % str(e)[:70]); return 1
    db = os.path.join(REPO, "data", "_bt_full", "bars.sqlite")
    try:
        cc = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        cc.execute("PRAGMA temp_store=MEMORY")
        for sym, vol in poss:
            n_pos += 1
            try:
                rr = list(cc.execute("SELECT close FROM bars WHERE ts_code=? AND trade_date<=? "
                                     "ORDER BY trade_date DESC LIMIT 1", (conv(sym), day)))
                if rr:
                    mv += float(rr[0][0] or 0) * int(vol or 0)
            except Exception as _e_sil1:
                _silent_alert("record_daily_nav.py:64", _e_sil1)
        cc.close()
    except Exception as _e_sil2:
        _silent_alert("record_daily_nav.py:67", _e_sil2)
    eq = cash + mv
    # ★ 账本 §9.531 ✓（用户「为什么执着于写 json 而不是落库」✓）：**落库** ✓
    #   为什么 ✓：文件不原子/不持久/难查询/同一语义散落多份 ✗（今天就吃了 `ambush_promoted.json` 0 个的亏 ✗）
    #   为什么以前用文件 ✓：①回测沙箱按"天目录"组织（可整目录拷贝/回放 ✓）②多臂并行用独立目录天然隔离 ✓
    #     ③PG 是**共享库**（本地==云端 ✗）⇒ 回测写库有污染风险 ✗ ⇒ 我们才用 5433 回测库 ✓
    #   ⇒ ⇒ **分工** ✓：**状态/事件 ⇒ 落库** ✓（可查、可跨账户、可跨重置 ✓）；**按天快照 ⇒ 文件** ✓（回放用 ✓）
    try:
        import psycopg2 as _pg
        _cn = _pg.connect(dsn, connect_timeout=4)
        try:
            _cn.autocommit = True
            _c = _cn.cursor()
            _c.execute("""CREATE TABLE IF NOT EXISTS bt_nav (
                account_id text NOT NULL, day text NOT NULL, at text NOT NULL,
                cash double precision, mv double precision, equity double precision,
                ret double precision, n_pos integer, created_at timestamptz DEFAULT now(),
                PRIMARY KEY (account_id, day, at))""")
            _c.execute("INSERT INTO bt_nav (account_id,day,at,cash,mv,equity,ret,n_pos) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                       "ON CONFLICT (account_id,day,at) DO NOTHING",
                       (acc, day, time.strftime("%H:%M:%S"), round(cash, 2), round(mv, 2), round(eq, 2),
                        round((eq / 250000.0 - 1) * 100, 3), n_pos))
        finally:
            _cn.close()
    except Exception as _e:
        print("  [nav] 落库失败（文件仍写 ✓）: %s" % str(_e)[:70])
    # ★ 账本 §9.533 ✓（用户方案：「每个臂做一个 sqlite，数据落库 ⇒ 不用存这么多 json，且都能查」✓）
    try:
        import sys as _sys2
        _sys2.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import arm_db as _adb
        _c2 = _adb.connect(acc)
        _adb.put_nav(_c2, acc, day, round(cash, 2), round(mv, 2), round(eq, 2),
                     round((eq / 250000.0 - 1) * 100, 3), n_pos)
        _c2.close()
    except Exception as _e2:
        print("  [nav] 写臂库失败: %s" % str(_e2)[:60])
    row = {"day": day, "at": time.strftime("%H:%M:%S"), "account": acc, "cash": round(cash, 2),
           "mv": round(mv, 2), "equity": round(eq, 2), "ret": round((eq / 250000.0 - 1) * 100, 3), "n_pos": n_pos}
    p = os.path.join(os.getenv("DATA_DIR") or os.path.join(REPO, "data", "_bt_t35", day), "nav.jsonl")
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print("  [nav] 写盘失败: %s" % str(e)[:60]); return 1
    print("  [nav] %s %s ⇒ 现金 %.0f ＋ 持仓 %.0f ⇒ **净值 %.0f（%+.2f%%）** ✓（%d 只 ✓）"
          % (day, row["at"], cash, mv, eq, row["ret"], n_pos), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
