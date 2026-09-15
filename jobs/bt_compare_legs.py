# -*- coding: utf-8 -*-
"""bt_compare_legs.py — 回测腿 vs **生产实盘腿**逐日对账（目标：重合率 ≥90%）。

生产侧真值：`t_conditions`（`publisher='switch'`、`direction='buy'`、`kind ∈ {custom_m5dump, custom_prevlow}`）
按 `trade_date` 分组 → 当日被布腿的标的集合（**两条路径的并集**：08:18 switch_builder + 09:20 rotation_switch_arm）。

回测侧：`jobs/bt_days.py` 产出的 `legs_by_day.json`（每天含 `legs` 列表）。

用法（容器内）：
  python jobs/bt_compare_legs.py --from 20260909 --to 20260915 \
      --summary /app/data/_bt_full/_summary/legs_by_day.json [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path[:0] = ["/app", "/app/backend"]


def prod_legs(d_from: str, d_to: str):
    import psycopg2
    url = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
    c = psycopg2.connect(url); cur = c.cursor()
    # ⚠️ 必须限定 publisher='switch'（否则会把 agent/做T 的腿也算进来）+ 只取**交易日**
    #   （实测：不加限定会把 09-12/09-13 周末也算成"生产布了 11 条腿"）
    cur.execute("""SELECT trade_date, symbol, trigger_kind, publisher
                   FROM t_conditions
                   WHERE trade_date BETWEEN %s AND %s AND direction='buy'
                     AND publisher='switch'
                     AND trigger_kind IN ('custom_m5dump','custom_prevlow')
                   GROUP BY 1,2,3,4 ORDER BY 1,2""", (d_from, d_to))
    out = {}
    for d8, sym, kind, pub in cur.fetchall():
        out.setdefault(str(d8), {}).setdefault(str(sym), set()).add(str(kind))
    cur.close(); c.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to", required=True)
    ap.add_argument("--summary", default="/app/data/_bt_full/_summary/legs_by_day.json")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    mine = json.load(open(a.summary, encoding="utf-8")) if os.path.exists(a.summary) else {}
    theirs = prod_legs(a.d_from, a.d_to)
    # 交易日历（以本地日线缓存为准）→ 过滤掉周末/假日
    td = set()
    try:
        import sqlite3
        c = sqlite3.connect(os.path.join(os.environ.get("DATA_DIR", "/app/data"), "_bt_full", "bars.sqlite"))
        td = {r[0] for r in c.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ?",
                                      (a.d_from, a.d_to))}
        c.close()
    except Exception:
        pass
    days = sorted(d for d in set(list(mine) + list(theirs)) if not td or d in td)
    rows, tot_hit, tot_prod, tot_mine = [], 0, 0, 0
    print("%-10s %-38s %-38s %s" % ("日期", "生产实盘腿", "回测腿", "重合"))
    for d8 in days:
        pset = set(theirs.get(d8, {}))
        mset = set(mine.get(d8, {}).get("legs") or [])
        hit = pset & mset
        tot_hit += len(hit); tot_prod += len(pset); tot_mine += len(mset)
        rows.append({"date": d8, "prod": sorted(pset), "bt": sorted(mset),
                     "hit": sorted(hit), "prod_only": sorted(pset - mset), "bt_only": sorted(mset - pset)})
        mark = "✅" if pset and pset == mset else ("✅" if not pset and not mset else "⚠️")
        print("%-10s %-38s %-38s %s %s" % (
            d8, ",".join(sorted(pset))[:36] or "—", ",".join(sorted(mset))[:36] or "—",
            "%d/%d" % (len(hit), len(pset)), mark))
    prec = (tot_hit / tot_mine) if tot_mine else 0.0
    rec = (tot_hit / tot_prod) if tot_prod else 0.0
    print("\n合计：生产 %d 条 / 回测 %d 条 / 重合 %d → 召回 %.0f%% 精确 %.0f%%"
          % (tot_prod, tot_mine, tot_hit, rec * 100, prec * 100))
    if a.json:
        json.dump({"rows": rows, "recall": round(rec, 4), "precision": round(prec, 4),
                   "prod_total": tot_prod, "bt_total": tot_mine, "hit": tot_hit},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("写出", a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
