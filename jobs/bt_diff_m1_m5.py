# -*- coding: utf-8 -*-
"""bt_diff_m1_m5.py — **同一 (标的,日) 上"5min 替身 vs 1min 替身"跑生产函数的差异对照**。

为什么需要它
------------
换粒度不是"数据更细"这么简单：生产 `TMonitor` 的字段由**具体函数**算出来，函数内部对
bar 的**根数/时间戳形态**有隐含假设。本脚本把同一根 bar 时刻的 5min 替身与 1min 替身各跑一次，
逐字段对比，并把差异归因成两类：
  · **粒度变细**（预期）：如 `minute.m5.ma5`（均线窗口从 25 分钟缩到 5 分钟）、`m1.low_today`、`t_sell`；
  · **bug/口径**（不可接受）：如时间戳格式导致 `_stock_dip_prev_low` 的 `[:8]` 分组失效（254 恒 False）。

覆盖的生产函数（**只读调用，不改生产代码**）
------------------------------------------
  · `TMonitor._build_minute_snapshot()` → `minute.m1.{low_today,last_close,bounce}` / `minute.m5.{ma5,ma10,ma20,t1_shrink_expand,t_sell}`
  · `TMonitor._stock_dip_prev_low()`   → 254 腿 A 档"挂前日低点"
  · `TMonitor._index_m5_dump()`        → 253 腿 C 档"指数 5min 急杀"
  · `TMonitor._today_bars()/_prev_daily()/_daily_dated()` → 读 `recent_sync/`（粒度与格式）
  · `market.quote()` → `quote.{current,open,high,low,vol,amount,average,turnover_rate,change_pct}`

隔离（**不污染正在跑的年跑 / 不写 repo data/**）
--------------------------------------------
  · 每次对照都在 `/tmp/bt_diff_*` 下建独立沙箱 `DATA_DIR`（`write_recent_sync` 只写沙箱）；
  · 钉钟用 `bt_prod_run._pin_clock_dynamic()+set_now()`（与年跑同一套，保证"生产读到的现在"一致）；
  · `WOLF_DATED_LIVE_FALLBACK=0`（关掉 `_daily_dated` 的实时源兜底，避免出网）；
  · 不连 PG、不写 t_triggers（只调纯计算/读文件的方法）。

用法：
  python jobs/bt_diff_m1_m5.py --day 20260320 --hhmm 09:35,10:00,13:30,14:30
  python jobs/bt_diff_m1_m5.py --day 20260320 --symbols SZ002587,SH600658 --all-symbols-check
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path[:0] = []
import bt_env  # noqa: E402


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

bt_env.add_paths()

REPO = bt_env.REPO
os.environ.setdefault("WOLF_DATED_LIVE_FALLBACK", "0")     # 关掉 _daily_dated 的实时源兜底（不出网）
os.environ.setdefault("BT_PG_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")


def _fmt12(bars):
    """生产口径 12 位 `YYYYMMDDHHMM`（与 `bt_prod_run.install_data_shims` 内的同名闭包一致）。

    ⚠️ `bt_prod_run._fmt12` **不是模块级函数**（它定义在 `install_data_shims` 里面）→ 这里自带一份。
    """
    out = []
    for b in bars:
        t = str(b.get("time") or "")
        if len(t) >= 16 and t[4] == "-":
            t = t[:10].replace("-", "") + t[11:16].replace(":", "")
        nb = dict(b)
        nb["time"] = t
        out.append(nb)
    return out


def _snapshot_for(mode, day, symbols, hhmm, mins1, mins5, bars_db, keep_sep_timestamps=False,
                  sync_prev="daily", strict=False):
    """在一个临时沙箱里，用指定粒度替身跑一遍生产函数，返回 {symbol: {...}}。"""
    import bt_prod_run as bpr
    import bt_local_market as blm
    import app.services.t_monitor as tm

    sandbox = tempfile.mkdtemp(prefix="bt_diff_%s_%s_" % (mode, day))
    os.environ["DATA_DIR"] = sandbox
    if mode == "1m":
        market = blm.LocalMarket1m(mins1, mins5, bars_db, day, sync_prev=sync_prev,
                                   strict_lookahead=strict)
        blm.install_data_shims(market, {"hhmm": hhmm})
    elif mode == "fixed5m":
        # 5min 基线 + 修掉 look-ahead（`d > self.day` 守卫）；数据仍是既有 5min 缓存
        market = blm.LocalMarket5mFixed(mins1, mins5, bars_db, day, sync_prev=sync_prev)
        blm.install_data_shims(market, {"hhmm": hhmm})
    else:
        # 现状口径：bt_prod_run.LocalMarket + 原装 shim（**mkline 会返回未来日的 bar**）
        market = bpr.LocalMarket(mins5, bars_db, day)
        bpr.install_data_shims(market, {"hhmm": hhmm})
    shims = None
    if keep_sep_timestamps:
        # 变体：把 12 位时间戳改回带分隔符（用来证明 `_stock_dip_prev_low` 的 `[:8]` 坑）
        import app.services.t_data_sources as tds
        def _mk(symbol, freq="m5", count=320):
            if hasattr(market, "series"):
                return [dict(b) for b in market.series(str(symbol), freq, hhmm, count)]
            return [dict(b) for b in market.mkline(str(symbol), hhmm, count)]
        tds.fetch_minute_bars = _mk
        tds.fetch_tencent_mkline = _mk
        for mod in list(sys.modules.values()):
            if getattr(mod, "fetch_minute_bars", None) is not None and mod is not tds:
                try:
                    if mod.fetch_minute_bars.__module__.startswith("app.services"):
                        mod.fetch_minute_bars = _mk
                except Exception as _e_sil1:
                    _silent_alert("bt_diff_m1_m5.py:102", _e_sil1)
    # 写 recent_sync + 钉钟（**钉钟已在 main() 里、import 生产模块之前调用过**：
    #   生产模块用 `from datetime import datetime`，import 那一刻就把类绑死了，
    #   之后再 patch `datetime.datetime` 对它们无效 —— 这是 bt_prod_run 注释里写的顺序要求）
    bpr.set_now(day, hhmm)
    market.write_recent_sync(sandbox, list(symbols) + ["sh000001"], hhmm)

    mon = tm.TMonitor.__new__(tm.TMonitor)          # 不跑 __init__（避免起线程池/连库）
    mon._dated_cache = {}
    mon._buy_date_cache = {}
    mon._prev_low_cache = {}
    _clear_ttl(mon)

    out = {}
    for s in symbols:
        q = market.quote(str(s), hhmm) or {}
        rec = {"quote": {k: q.get(k) for k in ("current", "open", "high", "low", "vol", "amount",
                                               "average", "turnover_rate", "change_pct", "pre_close",
                                               "amplitude")}}
        _clear_ttl(mon)
        try:
            rec["minute"] = mon._build_minute_snapshot(str(s), q)
        except Exception as e:
            rec["minute"] = {"error": str(e)[:160]}
        _clear_ttl(mon)
        try:
            rec["dip_prev_low"] = bool(mon._stock_dip_prev_low(str(s)))
        except Exception as e:
            rec["dip_prev_low"] = "err:%s" % str(e)[:80]
        _clear_ttl(mon)
        try:
            rec["today_bars_n"] = len(mon._today_bars(str(s)) or [])
            tb = mon._today_bars(str(s)) or []
            rec["today_first_last"] = [str(tb[0].get("time")) if tb else None,
                                       str(tb[-1].get("time")) if tb else None]
        except Exception as e:
            rec["today_bars_n"] = "err:%s" % str(e)[:60]
        _clear_ttl(mon)
        try:
            pd_ = mon._prev_daily(str(s), 5) or []
            rec["prev_daily"] = [[round(float(x.get("close") or 0), 4), round(float(x.get("high") or 0), 4),
                                  round(float(x.get("low") or 0), 4), round(float(x.get("vol") or 0), 1)]
                                 for x in pd_]
        except Exception as e:
            rec["prev_daily"] = "err:%s" % str(e)[:60]
        _clear_ttl(mon)
        try:
            dd = mon._daily_dated(str(s), 5) or []
            rec["daily_dated"] = [[x.get("date"), round(float(x.get("close") or 0), 4),
                                   round(float(x.get("low") or 0), 4)] for x in dd]
        except Exception as e:
            rec["daily_dated"] = "err:%s" % str(e)[:60]
        out[str(s)] = rec
    _clear_ttl(mon)
    try:
        out["__index__"] = {"index_m5_dump": mon._index_m5_dump(),
                            "index_m5_dump_bars": [(b.get("time"), b.get("close")) for b in
                                                   (market.series("sh000001", "m5", hhmm, 60)
                                                    if hasattr(market, "series") else
                                                    market.mkline("sh000001", hhmm, 60))[-3:]][:3]}
    except Exception as e:
        out["__index__"] = {"error": str(e)[:120]}
    out["__meta__"] = {"mode": mode, "day": day, "hhmm": hhmm, "sandbox": sandbox,
                       "market": (market.summary() if hasattr(market, "summary") else {"mode": "5m"}),
                       "sep_timestamps": keep_sep_timestamps}
    return out


def _clear_ttl(mon=None):
    """清掉生产模块的 TTL 缓存（与 bt_prod_run._reset_module_caches 同口径）。"""
    import app.services.t_monitor as tm
    tm._m5_dump_cache["at"] = 0.0
    tm._index_dd_cache["at"] = 0.0
    tm._prev_low_cache.clear()
    if mon is not None:
        try:
            mon._dated_cache.clear()
        except Exception as _e_sil2:
            _silent_alert("bt_diff_m1_m5.py:180", _e_sil2)


def _diff(rec5, rec1):
    """逐字段对比 → [(path, v5, v1, kind)]；kind ∈ {granularity, equal, DIFF}。"""
    rows = []

    def walk(k, a, b, path=""):
        if isinstance(a, dict) and isinstance(b, dict):
            for kk in sorted(set(a) | set(b)):
                walk(kk, a.get(kk), b.get(kk), path + "." + str(kk) if path else str(kk))
        else:
            if a == b:
                rows.append((path, a, b, "equal"))
            else:
                rows.append((path, a, b, "DIFF"))
    walk("", rec5, rec1)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="20260320")
    ap.add_argument("--hhmm", default="09:35,10:00,11:00,13:30,14:30")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--mins1", default=os.path.join(REPO, "data", "_bt_full", "mins1"))
    ap.add_argument("--mins5", default=os.path.join(REPO, "data", "_bt_full", "mins"))
    ap.add_argument("--bars-db", default=os.path.join(REPO, "data", "_bt_full", "bars.sqlite"))
    ap.add_argument("--out", default="")
    ap.add_argument("--all-symbols-check", action="store_true", help="对当日全部标的一次性做 missing_bars 对比")
    ap.add_argument("--sep-variant", action="store_true", help="额外跑一遍『带分隔符时间戳』变体（证明 [:8] 坑）")
    a = ap.parse_args()

    import bt_prod_run as bpr
    bpr._pin_clock_dynamic()          # ① 先钉钟（必须在 import 生产模块之前）
    import app.services.t_monitor as _tm   # noqa: F401  ← 让 t_monitor 在这次 patch 之后绑定 datetime
    import bt_local_market as blm
    syms = [x.strip() for x in a.symbols.split(",") if x.strip()]
    if not syms:
        p = os.path.join(REPO, "data", "_bt_year", "_summary", "prod_%s.json" % a.day)
        if os.path.exists(p):
            syms = (json.load(open(p, encoding="utf-8")).get("symbols") or [])[:12]
        else:
            m = blm.LocalMarket1m(a.mins1, a.mins5, a.bars_db, a.day)
            syms = sorted({os.path.basename(x).split("_")[0] + os.path.basename(x).split("_")[1]
                           for x in __import__("glob").glob(os.path.join(a.mins1, "*_1min_%s.json" % a.day))})[:12]
    print("[diff] day=%s 标的 %d 个 × bar %s" % (a.day, len(syms), a.hhmm))

    report = {"day": a.day, "symbols": syms, "bars": {}, "missing_bars": {}}
    n_diff = n_equal = 0
    detail_rows = []
    for hhmm in [x.strip() for x in a.hhmm.split(",") if x.strip()]:
        rL = _snapshot_for("legacy5m", a.day, syms, hhmm, a.mins1, a.mins5, a.bars_db)
        r5 = _snapshot_for("fixed5m", a.day, syms, hhmm, a.mins1, a.mins5, a.bars_db)
        r1 = _snapshot_for("1m", a.day, syms, hhmm, a.mins1, a.mins5, a.bars_db)
        rows = []
        for s in syms + ["__index__"]:
            rows += [("fixed5m_vs_1m." + s + "." + p, x, y, k)
                     for (p, x, y, k) in _diff(r5.get(s, {}), r1.get(s, {}))]
            rows += [("legacy5m_vs_fixed5m." + s + "." + p, x, y, k)
                     for (p, x, y, k) in _diff(rL.get(s, {}), r5.get(s, {}))]
        diffs = [r for r in rows if r[3] == "DIFF"]
        n_diff += len(diffs)
        n_equal += len([r for r in rows if r[3] == "equal"])
        report["bars"][hhmm] = {"legacy5m": rL, "fixed5m": r5, "1m": r1, "diffs": diffs}
        print("\n=== %s %s ：字段 %d 个，差异 %d 个（含 legacy5m↔fixed5m 的前视差）==="
              % (a.day, hhmm, len(rows), len(diffs)))
        for (p, x, y, _k) in diffs:
            kind = ("粒度" if p.split(".")[-1] in ("current", "high", "low", "vol", "amount", "average",
                                                  "turnover_rate", "amplitude", "change_pct",
                                                  "low_today", "last_close", "ma5", "ma10", "ma20")
                    else "口径")
            if p.startswith("legacy5m_vs_fixed5m"):
                kind = "前视"
            print("   [%s] %-52s A=%-26s B=%s" % (kind, p, json.dumps(x, ensure_ascii=False)[:26],
                                                   json.dumps(y, ensure_ascii=False)[:56]))
        detail_rows += rows

    if a.sep_variant:
        hhmm = [x.strip() for x in a.hhmm.split(",") if x.strip()][1]
        print("\n=== 变体：带分隔符时间戳（`str(time)[:8]` 坑复现）@%s ===" % hhmm)
        base5 = _snapshot_for("fixed5m", a.day, syms, hhmm, a.mins1, a.mins5, a.bars_db)
        sep1 = _snapshot_for("1m", a.day, syms, hhmm, a.mins1, a.mins5, a.bars_db, keep_sep_timestamps=True)
        ok5 = _snapshot_for("fixed5m", a.day, syms, hhmm, a.mins1, a.mins5, a.bars_db, keep_sep_timestamps=True)
        for s in syms[:6]:
            print("   %-11s dip_prev_low: 5m(12位)=%-5s 1m(12位)=%-5s | 5m(分隔符)=%-5s 1m(分隔符)=%-5s"
                  "  1m bounce(12位)=%-5s bounce(分隔符)=%s"
                  % (s, base5[s].get("dip_prev_low"),
                     report["bars"][hhmm]["1m"][s].get("dip_prev_low"), ok5[s].get("dip_prev_low"),
                     sep1[s].get("dip_prev_low"),
                     (report["bars"][hhmm]["1m"][s].get("minute") or {}).get("m1", {}).get("bounce"),
                     (sep1[s].get("minute") or {}).get("m1", {}).get("bounce")))
        report["sep_variant"] = {"day": a.day, "hhmm": hhmm,
                                 "digits": {s: {"5m": base5[s].get("dip_prev_low"),
                                                "1m": report["bars"][hhmm]["1m"][s].get("dip_prev_low")}
                                            for s in syms},
                                 "separator": {s: {"5m": ok5[s].get("dip_prev_low"),
                                                   "1m": sep1[s].get("dip_prev_low")} for s in syms}}

    if a.all_symbols_check:
        m5 = __import__("bt_prod_run", fromlist=["x"]).LocalMarket(a.mins5, a.bars_db, a.day)
        m1 = blm.LocalMarket1m(a.mins1, a.mins5, a.bars_db, a.day)
        miss5 = {s for s in syms if not m5.bars_upto(s, "15:00")}
        miss1 = {s for s in syms if not m1.bars_upto(s, "15:00")}
        miss1_m5 = {s for s in syms if not m1.bars_upto_m5(s, "15:00")}
        print("\n=== missing_bars（当日无 bar 的标的）===")
        print("   5min 模式缺: %d %s" % (len(miss5), sorted(miss5)[:8]))
        print("   1min 模式缺: %d %s" % (len(miss1), sorted(miss1)[:8]))
        print("   1min 模式(m5 面)缺: %d %s" % (len(miss1_m5), sorted(miss1_m5)[:8]))
        # 断言口径：**1min 缺的集合必须是 5min 缺的子集**（1min 只会更好或一样）
        subset_ok = miss1 <= miss5
        print("   断言 1min 缺失 ⊆ 5min 缺失（1min 只会更好）：%s" % subset_ok)
        report["missing_bars"] = {"5m": sorted(miss5), "1m": sorted(miss1), "1m_m5face": sorted(miss1_m5),
                                  "subset_ok": subset_ok}

    print("\n[diff] 字段级：一致 %d / 差异 %d" % (n_equal, n_diff))
    out = a.out or os.path.join(REPO, "data", "_bt_full", "mins1", "_reports",
                                "diff_m1_m5_%s.json" % a.day)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("[diff] 报告 → %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
