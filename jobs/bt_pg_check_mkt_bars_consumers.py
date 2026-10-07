# -*- coding: utf-8 -*-
"""bt_pg_check_mkt_bars_consumers.py — 实测 `mkt_bars_daily` 回填后，**生产消费方**是否真的活了。

为什么单独一个脚本：`jobs/bt_pg_backfill_mkt_bars.py` 只负责把行灌进本地 PG；"灌进去有没有用"必须用
**生产代码本身**验证（不许自己写一份等价查询来自证）。本脚本在 **as-of 沙箱**里跑生产模块：

  · `wolf_mainline_select.load_universe()`          —— 主题成分（读 DATA_DIR/stock_pool.db 概念库）
  · `wolf_mainline_select.run(save=False, date8=D)` —— **真正吃 `mkt_bars_daily` 的那条链**
        （`SELECT trade_date, ts_code, pct_chg, amount FROM mkt_bars_daily WHERE pct_chg IS NOT NULL …`
          → 主题日收益面板 → r5/r20/breadth/share5 → 主线 + 池）
        `save=False` ⇒ 不写任何文件、不写 daily_artifacts（沙箱/生产文件零污染）。

会把结果与沙箱里既有的 `wolf_mainline_select.json`（年跑生产者按 as-of 生成的产物）做**语义对比**：
mainline / second / pool / pool_top / rank_all 是否一致；不一致就如实打印差异（不调参去凑）。

用法：
    .venv/bin/python jobs/bt_pg_check_mkt_bars_consumers.py --day 20260310
    .venv/bin/python jobs/bt_pg_check_mkt_bars_consumers.py --day 20260310 --date8 20260309   # 对齐沙箱 json 的 as-of
"""
from __future__ import annotations

import argparse
import json
import os
import sys

JOBS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(JOBS)


def _prep_env(data_dir: str, db_url: str):
    os.environ["DATABASE_URL"] = db_url
    os.environ["DATA_DIR"] = data_dir
    os.environ.setdefault("WOLF_MAINLINE_SELECT", "1")
    os.environ.setdefault("WOLF_MS_USE_GATE", "0")
    os.environ.setdefault("WOLF_MS_POOL_K", "3")
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    for sub in ("backend", "apps/main_line", "core", "jobs", ""):
        p = os.path.join(REPO, sub) if sub else REPO
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="20260310", help="沙箱日（data/_bt_year/<day>）")
    ap.add_argument("--date8", default=None, help="传给 run() 的日期（默认 = --day）")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--db-url", default=os.getenv("BT_PG_URL") or os.getenv("DATABASE_URL")
                    or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
    ap.add_argument("--lookback", type=int, default=60, help="与生产同口径的回看交易日数")
    a = ap.parse_args()
    a.date8 = a.date8 or a.day
    a.data_dir = a.data_dir or os.path.join(REPO, "data", "_bt_year", a.day)
    if not os.path.isdir(a.data_dir):
        print("⛔ 沙箱不存在：%s" % a.data_dir)
        return 2
    _prep_env(a.data_dir, a.db_url)

    print("[env ] DATA_DIR=%s" % a.data_dir)
    print("[env ] DATABASE_URL=…@%s" % a.db_url.split("@")[-1])
    print("[env ] date8=%s WOLF_MAINLINE_SELECT=%s pool_k=%s use_gate=%s"
          % (a.date8, os.environ["WOLF_MAINLINE_SELECT"], os.environ["WOLF_MS_POOL_K"],
             os.environ["WOLF_MS_USE_GATE"]))

    # ── 0) 面板可用性（就是 run() 里那条查询的原样口径）──
    import psycopg2
    pg = psycopg2.connect(a.db_url)
    with pg.cursor() as cur:
        cur.execute("""SELECT min(trade_date) FROM (SELECT DISTINCT trade_date FROM mkt_bars_daily
                       WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s) t""",
                    (a.date8, a.lookback))
        dmin = cur.fetchone()[0]
        cur.execute("""SELECT count(*), count(pct_chg), count(amount) FROM mkt_bars_daily
                       WHERE trade_date <= %s AND trade_date >= %s""", (a.date8, dmin))
        n, npc, namt = cur.fetchone()
        cur.execute("""SELECT count(DISTINCT trade_date) FROM mkt_bars_daily
                       WHERE trade_date <= %s AND trade_date >= %s""", (a.date8, dmin))
        nd = cur.fetchone()[0]
    print("[panel] 回看窗口 %s→%s：%d 个交易日 / %d 行（pct_chg 非空 %d，amount 非空 %d）"
          % (dmin, a.date8, nd, n, npc, namt))
    pg.close()

    from app.services import wolf_mainline_select as wms

    # ── 1) load_universe()（主题成分；读 stock_pool.db 概念库）──
    uni, lead, allc = wms.load_universe()
    print("[univ ] load_universe(): 主题=%d 带动板块代码=%d 全市场代码=%d"
          % (len(uni), len(lead), len(allc)))
    for th in sorted(uni, key=lambda t: -len(uni[t]))[:5]:
        print("[univ ]   %-14s %d 只" % (th, len(uni[th])))

    # ── 2) run()：真正消费 mkt_bars_daily 的那条链（save=False ⇒ 零落盘）──
    res = wms.run(save=False, date8=a.date8)
    print("[run  ] ok=%s reason=%s" % (res.get("ok"), res.get("reason")))
    if res.get("ok"):
        print("[run  ] date=%s mainline=%s second=%s" % (res.get("date"), res.get("mainline"), res.get("second")))
        print("[run  ] pool=%s" % (res.get("pool"),))
        print("[run  ] pool_top=%s pool_n=%s pool_k=%s" % (res.get("pool_top"), res.get("pool_n"), res.get("pool_k")))
        print("[run  ] r5=%s" % (res.get("r5"),))
        print("[run  ] pool_share5=%s" % (res.get("pool_share5"),))
        print("[run  ] rank_all=%s" % (res.get("rank_all"),))

    # ── 3) 与沙箱既有产物对比（年跑生产者按 as-of 生成的 json）──
    sp = os.path.join(a.data_dir, "wolf_mainline_select.json")
    if os.path.exists(sp):
        try:
            old = json.load(open(sp, encoding="utf-8"))
        except Exception as e:
            old = None
            print("[cmp  ] 读 %s 失败: %s" % (sp, e))
        if isinstance(old, dict):
            print("[cmp  ] 沙箱 json: date=%s mainline=%s second=%s pool=%s pool_top=%s"
                  % (old.get("date"), old.get("mainline"), old.get("second"), old.get("pool"), old.get("pool_top")))
            if res.get("ok"):
                same = {k: (old.get(k) == res.get(k)) for k in
                        ("mainline", "second", "pool", "pool_top", "pool_n", "rank_all", "r5")}
                print("[cmp  ] 字段一致: %s" % same)
                if not all(same.values()):
                    print("[cmp  ] ⚠️ 差异（不调参去凑，如实报）：")
                    for k, v in same.items():
                        if not v:
                            print("[cmp  ]   %-10s 沙箱=%s  本次重算=%s" % (k, old.get(k), res.get(k)))
        print("[cmp  ] （json 的 date 字段 = 面板最后一天 = 生产 as-of 的 cut；沙箱用 cut 调 run，"
              "所以 --date8 要对齐它才有可比性）")
    else:
        print("[cmp  ] 沙箱无 wolf_mainline_select.json（%s）" % sp)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
