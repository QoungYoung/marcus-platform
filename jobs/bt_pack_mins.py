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
        # ⚠️ **指数**的分钟上游只给收盘价（实测 `000001.SH` 的 5min 行是 [ts, time, None,None,None,close,None,None]）
        #    → 旧实现把"OHLC 有 None"的行全丢 → 指数 m5 变空 → `index.m5_dump` 恒 0 → 253 条件永不成立
        #    （2026-01 回测踩坑：51 个腿-标的日 0 触发）。指数只做"5min 跌幅"判定 → 用收盘价兜底 OHLC。
        if c is None:
            continue
        if o is None or h is None or l is None:
            o = o if o is not None else c
            h = h if h is not None else c
            l = l if l is not None else c
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
    # ⚠️ **按天替换**（不是追加）：pack 必须是分钟缓存的纯函数，否则重复打包会把同一天的 bar 叠一倍
    #    （实测：二次打包后 09-11 的触发从 28 次变成 0 次 —— 累计量被重复计数）
    by_day = {}
    for b in bars:
        by_day.setdefault(b["time"][:10].replace("-", ""), []).append(b)
    for day, arr in by_day.items():
        arr.sort(key=lambda x: x["time"])
        cur[day] = arr
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


def _sig(path: str):
    """文件签名 (mtime, size)；取不到返回 None。"""
    try:
        st = os.stat(path)
        return [int(st.st_mtime), int(st.st_size)]
    except OSError:
        return None


def _split_name(fname: str):
    """`000001_SH_5min_20260911.json` → (ts_code, symbol, day8)。"""
    parts = fname[:-5].split("_")
    if len(parts) < 4:
        return None, None, None
    code, mkt, _freq, day = parts[0], parts[1], parts[2], parts[3]
    ts = "%s.%s" % (code, mkt)
    sym = (mkt + code) if mkt in ("SH", "SZ") else ts
    return ts, sym, day


def _drop_day(pack: str, sub: str, key: str, day: str) -> None:
    """源文件被删除时，把该天从 pack 里摘掉（保持"pack 是分钟缓存的纯函数"语义）。"""
    p = os.path.join(pack, sub, "%s.json" % key)
    if not os.path.exists(p):
        return
    try:
        cur = json.load(open(p, encoding="utf-8")) or {}
    except Exception:
        return
    if day in cur:
        cur.pop(day, None)
        json.dump(cur, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def _pack_hint() -> str:
    """argparse 之前粗略取 `--pack`（用于 `.skip_pack` 标记判定）。"""
    import sys as _s
    argv = _s.argv
    for i, x in enumerate(argv):
        if x == "--pack" and i + 1 < len(argv):
            return argv[i + 1]
        if x.startswith("--pack="):
            return x.split("=", 1)[1]
    return ""


def main() -> int:
    # ── 2026-09-20：回放里可跳过打包 ─────────────────────────────────────────
    # 为什么：`--prod-only` 回放链里 **没有任何消费者** —— `bt_prod_run` 不读 pack（它读 `--mins`），
    #   读 pack 的 `bt_account` 那一步又被 `--prod-only` 跳过；而当天若有新取的分钟档，
    #   打包就要重打上百个文件（实测 112–447s；无变化时 0.1s）。
    # 安全性：产物随时可用本脚本从 `data/_bt_full/mins` 重建，消费方只有
    #   bt_account / bt_intraday / bt_reconcile 三个分析工具。
    # 开关：env `BT_SKIP_PACK=1`，或运行根下放标记文件 `.skip_pack`（可在不改父进程 env 时即时生效）。
    try:
        import os as _os
        _root = _os.path.dirname(_os.path.abspath(_pack_hint() or "."))
        if (str(_os.getenv("BT_SKIP_PACK", "0")).strip().lower() in ("1", "true", "yes", "on")
                or (_root and _os.path.exists(_os.path.join(_root, ".skip_pack")))):
            print("[pack] 已跳过（BT_SKIP_PACK / .skip_pack 标记）：产物仅分析工具消费，回放交易链不读",
                  file=sys.stderr)
            return 0
    except Exception:
        pass
    import time as _t
    ap = argparse.ArgumentParser()
    ap.add_argument("--mins", default="/app/data/_bt_full/mins")
    ap.add_argument("--pack", default="/app/data/_bt_full/pack")
    ap.add_argument("--bars-db", default="/app/data/_bt_full/bars.sqlite")
    ap.add_argument("--index-sh", default="000001.SH")
    a = ap.parse_args()
    t0 = _t.time()
    inc = str(os.environ.get("BT_PACK_INCREMENTAL", "1")).strip().lower() not in ("0", "false", "no", "off")
    man_p = os.path.join(a.pack, "_pack_manifest.json")
    man = {}
    if inc and os.path.exists(man_p):
        try:
            man = json.load(open(man_p, encoding="utf-8")) or {}
        except Exception:
            man = {}
    prev = (man.get("mins") or {}) if inc else {}
    prev_bars = (man.get("bars") or {}) if inc else {}
    prev_daily_sig = man.get("bars_db") if inc else None

    files = sorted(f for f in os.listdir(a.mins) if f.endswith(".json"))
    syms, n = set(), 0
    new_sig, new_bars = {}, {}
    n_reuse = n_repack = n_drop = 0
    for f in files:
        fp = os.path.join(a.mins, f)
        sg = _sig(fp)
        ts, sym, day = _split_name(f)
        if sym is None:
            continue
        if prev.get(f) == sg:                       # ← 增量：源文件没变 → 不读不聚合不写
            new_sig[f] = sg
            new_bars[f] = int(prev_bars.get(f, 0))
            n += new_bars[f]
            n_reuse += 1
            if ts != a.index_sh:
                syms.add(sym)
            continue
        bars = load_mins_file(fp)
        if not bars:
            continue
        bars = aggregate_5min(bars)
        if ts == a.index_sh:
            write_m5(a.pack, "sh", bars, sub="index_m5")
        else:
            write_m5(a.pack, sym, bars, sub="m5")
            syms.add(sym)
        n += len(bars)
        n_repack += 1
        new_sig[f] = sg
        new_bars[f] = len(bars)
    if inc:                                          # 源文件消失 → 摘掉对应天
        for f in set(prev) - set(new_sig):
            ts, sym, day = _split_name(f)
            if sym is None or not day:
                continue
            _drop_day(a.pack, "index_m5" if ts == a.index_sh else "m5",
                      "sh" if ts == a.index_sh else sym, day)
            n_drop += 1

    # 日线部分：bars.sqlite 未变且文件在 → 跳过（否则重建）
    db_sig = _sig(a.bars_db)
    if inc and prev_daily_sig == db_sig and os.path.exists(
            os.path.join(a.pack, "index_daily", "%s.json" % a.index_sh)):
        pass                                   # 指数日线未变 → 跳过
    else:
        write_index_daily(a.pack, a.index_sh, a.bars_db)
    if inc and prev_daily_sig == db_sig:       # 日线库未变 → 只补缺失的标的
        miss = [s for s in sorted(syms)
                if not os.path.exists(os.path.join(a.pack, "stock_daily", "%s.json" % s))]
        if miss:
            write_stock_daily(a.pack, miss, a.bars_db)
    else:
        write_stock_daily(a.pack, sorted(syms), a.bars_db)
    try:
        json.dump({"mins": new_sig, "bars": new_bars, "bars_db": db_sig},
                  open(man_p, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    print("[pack] %s 打包 %d 根 bar / %d 只标的 → %s（复用 %d 文件 / 重打 %d / 摘除 %d 天，耗时 %.1fs）"
          % ("增量" if inc else "全量", n, len(syms), a.pack, n_reuse, n_repack, n_drop,
             _t.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
