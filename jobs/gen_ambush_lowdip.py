# -*- coding: utf-8 -*-
"""gen_ambush_lowdip.py —— **低位方向「辨识度最高老龙头」埋伏候选**（账本 §9.514，用户「做」）

**他的原话**（`docs/wolf-exit-playbook.md:78`）：
  「之前的**低位方向**…找**辨识度最高老龙头**埋伏，强的留 弱的丢」（2026-09-04 15:07）
  ＋「**不确定但是不分散埋伏**」「**如果我看不懂我也宁可不做**」（尾段那节）
  ＋「**没抄底就没有做T资格**」（2026-08-25）⇒ ⇒ 这是**建仓**语义，不是正T ✗

**两个条件（都数据驱动、无名单）**：
  1. **低位方向** ＝ 该方向指数的**位置分位低**：收盘 ÷ 近 250 日最高 ≤ `WOLF_AMBUSH_LOW_MAX`（默认 0.75
     ⇒ 距 250 日高 **≤ −25%**）；且**未创新低**（近 20 日最低 ≥ 前 40 日最低，同 B1 口径）
  2. **辨识度最高老龙头** ＝ 方向内 **纯度 top-K**（＝与"方向成员 ≥20% 都有的概念"的共享比例，
     §9.506 实测有效：前 5 均值 **+2.75%** vs 全体 +0.98%）**∧ 成交额居前**（§9.505：前 3 **+1.51%**）

**输出**：`<DATA_DIR>/ambush_lowdip_candidates.json`（`{"day":…, "symbols":[…], "detail":[…]}`）
  ⚠️ 与既有 `ambush_candidates.json` **分开写** ✓（不clobber ✓）
**开关**：`WOLF_AMBUSH_LOWDIP`（默认 1 ＝ 开）；`WOLF_AMBUSH_TOP_K`（默认 3）
"""
from __future__ import annotations
import json, os, sqlite3, sys

BLOCKS = ["固态电池", "锂电池", "储能", "电池技术", "新能源", "新能源车", "电池化学品", "锂电专用设备",
          "半导体概念", "国产芯片", "存储芯片", "光刻机(胶)", "光刻胶", "Chiplet概念", "第三代半导体", "第四代半导体"]
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def enabled() -> bool:
    return str(os.getenv("WOLF_AMBUSH_LOWDIP", "1")).strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    if not enabled():
        print("  [ambush_lowdip] 关（WOLF_AMBUSH_LOWDIP=0）⇒ 跳过 ✓")
        return 0
    day = os.path.basename(str(os.getenv("DATA_DIR") or "").strip())
    if not (len(day) == 8 and day.isdigit()):
        day = sys.argv[1] if len(sys.argv) > 1 else ""
    if not day:
        print("  [ambush_lowdip] 拿不到日期 ✗"); return 0
    try:
        import psycopg2
    except Exception:
        print("  [ambush_lowdip] 无 psycopg2 ✗"); return 0
    dsn = os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
    cn = psycopg2.connect(dsn, connect_timeout=4)
    cn.set_session(readonly=True, autocommit=True)
    cur = cn.cursor()
    cur.execute("SET statement_timeout=8000")
    cur.execute("SELECT ts_code, concept_name FROM stock_concept_map")
    byc = {}
    for ts, c in cur.fetchall():
        byc.setdefault(ts, set()).add(c)
    cur.execute("SELECT concept_name, count(*) FROM stock_concept_map GROUP BY 1")
    allc = {r[0]: int(r[1]) for r in cur.fetchall()}
    tot = max(1, len(byc))
    db = os.path.join(REPO, "data", "_bt_full", "bars.sqlite")
    c2 = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    c2.execute("PRAGMA temp_store=MEMORY")
    out, detail = [], []
    for blk in BLOCKS:
        mem = [t for t in byc if blk in byc[t]]
        if len(mem) < 10:
            continue
        # ① 方向位置（用成员等权指数近似：成员收盘均值 ✓）
        q = ("SELECT ts_code, trade_date, close, amount FROM bars WHERE trade_date<=? AND ts_code IN (%s) "
             "AND trade_date>=date(?, '-2 day')" % ",".join("?" * len(mem)))
        # 取近 250 日：为省时间只取需要的列，按日聚合
        # ⚠️ 只算**本块成员**的等权指数 ✓（先前写成全市场 ⇒ 位置恒 ~1.0 ✗）
        rows = list(c2.execute(
            "SELECT trade_date, avg(close), sum(amount) FROM bars WHERE trade_date<=? AND ts_code IN (%s) "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 250" % ",".join("?" * len(mem)),
            (day, *mem)))
        if len(rows) < 60:
            continue
        closes = [float(r[1]) for r in rows][::-1]      # 由旧到新 ✓
        idx_hi = max(closes[-250:]) if len(closes) >= 1 else 0
        idx_last = closes[-1]
        lo20 = min(closes[-20:]); lo_prior = min(closes[-60:-20]) if len(closes) >= 60 else lo20
        pos = (idx_last / idx_hi) if idx_hi > 0 else 1.0
        low_ok = pos <= float(os.getenv("WOLF_AMBUSH_LOW_MAX", "0.75")) and lo20 >= lo_prior
        # ② 辨识度：纯度 top-K ∧ 成交额居前
        pool = [x for x in {c for t in mem for c in byc[t]}
                if sum(1 for t in mem if x in byc[t]) >= max(2, int(len(mem) * 0.20))
                and allc.get(x, 0) <= max(50, int(tot * 0.20))]
        amt = {r[0]: float(r[1] or 0) for r in c2.execute(
            "SELECT ts_code, amount FROM bars WHERE trade_date=? AND ts_code IN (%s)" % ",".join("?" * len(mem)), (day, *mem))}
        rank = sorted(mem, key=lambda t: (-(len(byc[t] & set(pool)) / float(len(pool) or 1)), -amt.get(t, 0.0), t))
        topk = rank[: int(os.getenv("WOLF_AMBUSH_TOP_K", "3"))]
        for t in topk:
            detail.append({"concept": blk, "symbol": t, "pos": round(pos, 3), "low_ok": bool(low_ok),
                           "purity": round(len(byc[t] & set(pool)) / float(len(pool) or 1), 3),
                           "amount": round(amt.get(t, 0.0), 0)})
            if low_ok and t not in out:
                out.append(t)
    p = os.path.join(os.getenv("DATA_DIR") or ".", "ambush_lowdip_candidates.json")
    try:
        json.dump({"day": day, "symbols": out, "detail": detail}, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        print("  [ambush_lowdip] %s ⇒ 低位方向命中 %d 只 ✓｜写 %s ✓" % (day, len(out), os.path.basename(p)))
        for d in detail[:6]:
            print("    %-10s %-11s 位置%.2f 低位=%s 纯度%.2f 成交额%.0f" % (
                d["concept"], d["symbol"], d["pos"], "✓" if d["low_ok"] else "✗", d["purity"], d["amount"]))
    except Exception as e:
        print("  [ambush_lowdip] 写盘失败: %s" % str(e)[:60])
    return 0


if __name__ == "__main__":
    sys.exit(main())
