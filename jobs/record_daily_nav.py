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
            except Exception:
                pass
        cc.close()
    except Exception:
        pass
    eq = cash + mv
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
