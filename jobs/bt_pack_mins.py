# -*- coding: utf-8 -*-
"""bt_pack_mins.py — 把 `bt_fetch_mins.py` 拉的分钟缓存，打包成**生产回测引擎**
（`backend/app/services/t_backtest.py::TBacktestEngine`）认识的缓存布局。

引擎的 `t_backtest_data.load_*` 约定：
  `<pack>/m5/<symbol>.json`        = {"YYYYMMDD": [{time:"YYYY-MM-DD HH:MM:SS", open,high,low,close,vol,amount}, …], …}
  `<pack>/m1/<symbol>.json`        = 同上（可选，用于"假跌破守卫"的分钟企稳确认）
  `<pack>/index_m5/<key>.json`     = key ∈ {sh, sz, hs300}
  `<pack>/index_daily/<ts>.json`   = [{trade_date:"YYYYMMDD", close, open, high, low, vol, amount}, …]
  `<pack>/stock_daily/<symbol>.json`

⚠️ 两个必须踩准的格式点（否则引擎静默拿不到数据）：
  1. `build_snapshot_at()` 用 `datetime.strptime(str(cur["time"]), "%Y-%m-%d %H:%M:%S")`
     → bar 的 `time` 必须是**带横杠的完整时间**（不是 stk_mins 的 `2026-09-11 09:35:00` 直接可用 ✔，
     但绝不能写成 `202609110935`）；
  2. `_day_key()` 取 `str(time)[:10].replace("-","")` → 日线键必须与 index_daily 的 `trade_date` 对齐。

用法（容器内）：
  python jobs/bt_pack_mins.py --mins /app/data/_bt_full/mins --pack /app/data/_bt_full/pack \
      --bars-db /app/data/_bt_full/bars.sqlite --index-sh 000001.SH
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys


def _iso(t: str) -> str:
    """stk_mins 的 '2026-09-11 09:35:00' → 'YYYY-MM-DD HH:MM:SS'（引擎要求的形态）。"""
    s = str(t).strip().replace("T", " ")
    if len(s) >= 19:
        return s[:19]
    if len(s) == 12 and s.isdigit():                      # 202609110935
        return "%s-%s-%s %s:%s:00" % (s[:4], s[4:6], s[6:8], s[8:10], s[10:12])
    if len(s) == 14 and s.isdigit():                      # 20260911093500
        return "%s-%s-%s %s:%s:%s" % (s[:4], s[4:6], s[6:8], s[8:10], s[10:12], s[12:14])
    return s


def load_mins_file(path: str):
    d = json.load(open(path, encoding="utf-8"))
    out = []
    for b in (d.get("bars") or []):
        # stk_mins 字段序：ts_code, trade_time, open, high, low, close, vol, amount
        def _num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        if isinstance(b, dict):
            t = b.get("trade_time") or b.get("time"); o, h, l, c = (_num(b.get(k)) for k in ("open","high","low","close"))
            v, amt = _num(b.get("vol")) or 0.0, _num(b.get("amount")) or 0.0
        else:
            t = b[1]; o, h, l, c = _num(b[2]), _num(b[3]), _num(b[4]), _num(b[5])
            v, amt = (_num(b[6]) or 0.0), (_num(b[7]) or 0.0)
        if None in (o, h, l, c):
            continue          # OHLC 缺值的 bar 直接丢（实测指数 09:30 竞价 bar 偶发 None）
        out.append({"time": _iso(t), "open": o, "high": h, "low": l, "close": c, "vol": v, "amount": amt})
    return out


def aggregate_5min(bars):
    """把同一 5min 槽内的多行聚合（实测：部分标的 `stk_mins` 返回的是**分钟级多行**，
    time 会重复出现 3~4 次；不聚合会让"累计量/量比"被重复计数）。
    聚合口径：open=首、high=max、low=min、close=末、vol/amount=求和；时间对齐到 5min 槽起点。"""
    buckets = {}
    for b in bars:
        t = b["time"]
        hh, mm = int(t[11:13]), int(t[14:16])
        slot = "%s %02d:%02d:00" % (t[:10], hh, (mm // 5) * 5)
        buckets.setdefault(slot, []).append(b)
    out = []
    for slot in sorted(buckets):
        g = buckets[slot]
        out.append({"time": slot,
                    "open": g[0]["open"], "high": max(x["high"] for x in g),
                    "low": min(x["low"] for x in g), "close": g[-1]["close"],
                    "vol": sum(x["vol"] for x in g), "amount": sum(x["amount"] for x in g)})
    return out


def write_m5(pack: str, symbol: str, bars, sub: str = "m5"):
    p = os.path.join(pack, sub, "%s.json" % symbol)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cur = {}
    if os.path.exists(p):
        try:
            cur = json.load(open(p, encoding="utf-8")) or {}
        except Exception:
            cur = {}
    for b in bars:
        day = b["time"][:10].replace("-", "")
        cur.setdefault(day, []).append(b)
    for day in cur:
        cur[day].sort(key=lambda x: x["time"])
    json.dump(cur, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return p, len(bars)


def _index_daily_from_files(ts_code: str):
    """指数日线兜底：data/指数数据/index_daily/<ts>.csv 或 data/index_daily_<code>.json。"""
    import csv as _csv
    cands = ["/app/data/指数数据/index_daily/%s.csv" % ts_code,
             "/app/data/index_daily_%s.json" % ts_code.split(".")[0]]
    for p in cands:
        try:
            if p.endswith(".csv"):
                rows = list(_csv.DictReader(open(p, encoding="utf-8")))
                out = []
                for r in rows:
                    d = str(r.get("trade_date") or "")[:10].replace("-", "")
                    if len(d) != 8:
                        continue
                    try:
                        out.append({"trade_date": d, "open": float(r["open"]), "high": float(r["high"]),
                                    "low": float(r["low"]), "close": float(r["close"]),
                                    "vol": float(r.get("vol") or 0), "amount": float(r.get("amount") or 0)})
                    except Exception:
                        continue
                if out:
                    return out
            else:
                d = json.load(open(p, encoding="utf-8"))
                out = []
                for r in (d if isinstance(d, list) else d.get("rows") or []):
                    dd = str(r.get("trade_date") or "")[:10].replace("-", "")
                    if len(dd) == 8 and r.get("close"):
                        out.append({"trade_date": dd, "open": r.get("open") or r.get("close"),
                                    "high": r.get("high") or r.get("close"), "low": r.get("low") or r.get("close"),
                                    "close": r.get("close"), "vol": r.get("vol") or 0, "amount": r.get("amount") or 0})
                if out:
                    return out
        except Exception:
            continue
    return []


def write_index_daily(pack: str, ts_code: str, bars_db: str, symbols=("000001.SH",)):
    os.makedirs(os.path.join(pack, "index_daily"), exist_ok=True)
    c = sqlite3.connect(bars_db)
    for ts in symbols:
        rows = c.execute("""SELECT trade_date, open, high, low, close, vol, amount FROM bars
                            WHERE ts_code=? ORDER BY trade_date""", (ts,)).fetchall()
        out = [{"trade_date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4],
                "vol": r[5], "amount": r[6]} for r in rows]
        if not out:
            # 日线缓存（mkt_bars_daily）**不含指数** → 回落到生产的指数日线文件/CSV
            out = _index_daily_from_files(ts)
        if not out:
            continue
        json.dump(out, open(os.path.join(pack, "index_daily", "%s.json" % ts), "w", encoding="utf-8"),
                  ensure_ascii=False)
    c.close()


def write_stock_daily(pack: str, symbols, bars_db: str):
    os.makedirs(os.path.join(pack, "stock_daily"), exist_ok=True)
    c = sqlite3.connect(bars_db)
    for sym in symbols:
        ts = (sym[2:8] + "." + sym[:2]) if sym[:2] in ("SH", "SZ") else sym
        rows = c.execute("""SELECT trade_date, open, high, low, close, vol, amount FROM bars
                            WHERE ts_code=? ORDER BY trade_date""", (ts,)).fetchall()
        if not rows:
            continue
        out = [{"trade_date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4],
                "vol": r[5], "amount": r[6]} for r in rows]
        json.dump(out, open(os.path.join(pack, "stock_daily", "%s.json" % sym), "w", encoding="utf-8"),
                  ensure_ascii=False)
    c.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mins", default="/app/data/_bt_full/mins")
    ap.add_argument("--pack", default="/app/data/_bt_full/pack")
    ap.add_argument("--bars-db", default="/app/data/_bt_full/bars.sqlite")
    ap.add_argument("--index-sh", default="000001.SH")
    a = ap.parse_args()
    files = sorted(f for f in os.listdir(a.mins) if f.endswith(".json"))
    syms, n = set(), 0
    for f in files:
        parts = f[:-5].split("_")          # 000001_SH_5min_20260911
        if len(parts) < 4:
            continue
        code, mkt, freq, day = parts[0], parts[1], parts[2], parts[3]
        ts = "%s.%s" % (code, mkt)
        bars = load_mins_file(os.path.join(a.mins, f))
        if not bars:
            continue
        bars = aggregate_5min(bars)          # ← 关键：同槽多行先聚合
        if ts == a.index_sh:
            write_m5(a.pack, "sh", bars, sub="index_m5")
        else:
            sym = (mkt + code) if mkt in ("SH", "SZ") else ts
            write_m5(a.pack, sym, bars, sub="m5")
            syms.add(sym)
        n += len(bars)
    write_index_daily(a.pack, a.index_sh, a.bars_db)
    write_stock_daily(a.pack, sorted(syms), a.bars_db)
    print("[pack] 打包 %d 根 bar / %d 只标的 → %s（index_m5/sh + index_daily + stock_daily）"
          % (n, len(syms), a.pack))
    return 0


if __name__ == "__main__":
    sys.exit(main())
