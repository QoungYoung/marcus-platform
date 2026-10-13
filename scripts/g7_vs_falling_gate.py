# -*- coding: utf-8 -*-
"""对照量化：G7（指数层「大跌之日多买票」）vs B 闸（个股层「不在下跌中买」）（§9.812）

任务来源：台账 `wolf-buy-parameter-ledger.md:6409` 原话——
  「G7 是**指数层**日偏向（回测未启用），B/C 是**个股层**形态；将来启用 G7 时两者会在
   『指数大跌 + 个股走低』的日子上**相抵**，届时要**另量谁优先**，不要默认叠加」✓

本脚本用**已有的真实成交**量化（离线 ✓ 不跑新臂 ✓ 不改任何开关 ✓）：
 ① 指数日型：`mkt_bars_daily` 的上证 pct_chg → `wolf_day_rules.day_bias()`（**生产同一个函数** ✓）
 ② 真实买成交：`paper_trades direction='buy'`（bt PG:5433 ✓，多臂 ✓）
 ③ 结局：买 → **同臂同标的的下一笔卖**（近似 FIFO ✓）算每股盈亏 × 成交量
 ④ B 闸拦截：从各臂 `prod_*.json` 的 `triggers[].reason` 里数 `[COND-TIMING]`（B/C 闸 ✓）
输出：按日型分组的表 + 结论建议 ✓
"""
import glob
import json
import os
import re
import statistics
from collections import defaultdict

import psycopg2

DSN = "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
sys_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
import sys
sys.path.insert(0, sys_path); sys.path.insert(0, os.path.join(sys_path, "app", "services"))
from app.services import wolf_day_rules as W  # noqa: E402


def daytypes(cur):
    # ★ 修正（首轮 `mkt_bars_daily` 里**只有个股** ✗ ⇒ 取到 000001.SZ 平安银行 ✗ 日型全错 ✗）：
    #   改用真正的指数日线源 `data/index_daily_hist.json` 的 **000001.SH 上证综指** ✓
    idx = None
    _hist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "index_daily_hist.json")
    rows_i = []
    try:
        _d = json.load(open(_hist, encoding="utf-8"))
        idx = "000001.SH" if "000001.SH" in _d else (list(_d)[0] if _d else None)
        rows_i = [(r.get("trade_date"), r.get("pct_chg")) for r in (_d.get(idx) or [])]
    except Exception as _e:
        print("  指数日线读失败:", str(_e)[:80])
    if rows_i:
        out = {}
        for d, p in rows_i:
            if p is None:
                continue
            out[str(d)[:8]] = (float(p), W.day_bias(float(p)))
        return idx, out
    # 兜底：库里找（若将来入库了指数 ✓）
    cur.execute("select distinct ts_code from mkt_bars_daily where ts_code like '000300%' or ts_code like '000001.SH'")
    codes = [r[0] for r in cur.fetchall()]
    idx = codes[0] if codes else None
    cur.execute("select trade_date, pct_chg from mkt_bars_daily where ts_code=%s and pct_chg is not null", (idx,))
    out = {}
    for d, p in cur.fetchall():
        out[str(d).replace("-", "")[:8]] = (float(p), W.day_bias(float(p)))
    return idx, out


def main():
    c = psycopg2.connect(DSN, connect_timeout=8)
    cur = c.cursor()
    idx, dt = daytypes(cur)
    print("指数代码 = %s（G7 日型用 %s.day_bias ✓）｜覆盖 %d 个交易日" % (idx, "wolf_day_rules", len(dt)))
    cur.execute("""select account_id, symbol, trade_date, direction, price, volume, created_at
                   from paper_trades where coalesce(voided,0)=0 order by account_id, symbol, created_at""")
    rows = cur.fetchall()
    # 配对：买 → 同臂同标的的下一笔卖
    pos = defaultdict(list)
    pairs = []
    for acc, sym, d, dr, px, vol, ts in rows:
        k = (acc, sym)
        dr_l = str(dr or "").lower()
        if dr_l in ("buy", "买入"):
            pos[k].append([float(px or 0), float(vol or 0), str(d)])
        elif dr_l in ("sell", "卖出"):
            if pos[k]:
                bpx, bvol, bday = pos[k].pop(0)
                q = min(bvol, float(vol or 0))
                pairs.append({"acc": acc, "sym": sym, "day": bday, "buy": bpx, "sell": float(px or 0),
                              "qty": q, "pnl": (float(px or 0) - bpx) * q})
    print("配对的买→卖 = %d 组（近似 FIFO ✓）" % len(pairs))
    # ★ 补一列：按**指数涨跌幅分位**分组（`day_bias` 阈值偏严 ⇒ 本窗口 1599/1614 落入 neutral ✗，
    #   分位分组才能看清"大跌日 vs 大涨日"的真实差别 ✓）
    _pcts = sorted(v[0] for v in dt.values())
    def _q(x):
        import bisect
        return bisect.bisect_left(_pcts, x) / max(1, len(_pcts))
    grp = defaultdict(lambda: {"n": 0, "pnl": 0.0, "lst": []})
    for p in pairs:
        bias = dt.get(p["day"], (None, "neutral"))[1]
        _pv = dt.get(p["day"], (0.0, ""))[0]
        _qq = _q(_pv)
        if _qq >= 0.8:
            bias = bias + "|指数前20%强"
        elif _qq <= 0.2:
            bias = bias + "|指数后20%弱"
        g = grp[bias]
        g["n"] += 1; g["pnl"] += p["pnl"]; g["lst"].append(p["pnl"])
    # B/C 闸拦截（真实 reason ✓）
    blocks = defaultdict(int)
    for root in ("data/_bt_t41", "data/_bt_c2610122052", "data/_bt_draby26", "data/_bt_draymar"):
        for fp in glob.glob(os.path.join(root, "_summary", "prod_*.json")):
            try:
                d = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            day = str(d.get("day") or "")
            for t in (d.get("triggers") or []):
                if "[COND-TIMING]" in str(t.get("reason") or ""):
                    bias = dt.get(day, (None, "neutral"))[1]
                    blocks[bias] += 1
    print("\n%-10s %6s %14s %12s %12s %10s" % ("上证日型", "买成交", "配对盈亏合计", "单笔均值", "单笔中位", "B/C闸拦下"))
    order = ["big_down", "down", "neutral", "up", "big_up"]
    seen = [k for k in order if k in grp] + [k for k in grp if k not in order]
    for k in seen:
        g = grp[k]
        if not g["n"]:
            continue
        print("%-10s %6d %14.0f %12.1f %12.1f %10d"
              % (k, g["n"], g["pnl"], g["pnl"] / g["n"], statistics.median(g["lst"]), blocks.get(k, 0)))
    print("\n（日型定义：wolf_day_rules.day_bias(上证 pct_chg) ✓；big_down=大跌日、big_up=大涨日 ✓）")


if __name__ == "__main__":
    main()
