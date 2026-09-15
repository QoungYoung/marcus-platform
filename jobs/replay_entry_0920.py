# -*- coding: utf-8 -*-
"""replay_entry_0920.py — 按 **09:20 口径**（数据只到 T−1）重放当日选股 + 两道建仓门。

为什么需要：布腿器 09:20 跑，**当日 K 线还不存在** → 它的决策口径 = 数据只到 T−1；
收盘后再跑 DRY 会多一根当日 K 线（= 离线验收里的 decision day=T），结论与实盘**不可比**
（实测同一天两套口径选出的腿不同）。本脚本把 `rotation_switch_arm._today()` 钉到切点日
（cut = T−1），并用**一次拉全市场日线**建的本地 panel 顶替 `_gz` 的逐票取数，
这样跑的是**生产代码的选股逻辑**（`pick_buy`）+ 生产的两道门（`wolf_entry_filters`），
只是数据被截断到切点。

用法（容器内，relay 可达）：
  python jobs/replay_entry_0920.py --date 20260915 --cut 20260914 [--chains Chiplet概念,光刻机(胶)]
                                  [--legs-from-log] [--panel-days 90] [--daily-basic empty|cut]
输出：控制台 + `data/replay_entry_<date>.json`
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import sys
import time

sys.path[:0] = ["/app", "/app/apps/main_line", "/app/jobs", "/app/core"]
DATA = os.environ.get("DATA_DIR", "/app/data")


def panels(cut: str, n_days: int, cache: str = ""):
    """→ (days, daily, mv)：全市场日线 panel（cut 之前 n_days 个交易日）+ 当日 daily_basic 市值。

    `cache` 非空 → pickle 复用（panel 建一次约 8~10 分钟；换 `--daily-basic` 变体时不必重拉）。
    """
    if cache and os.path.exists(cache):
        import pickle
        with open(cache, "rb") as f:
            days, daily, mv = pickle.load(f)
        print("[panel] 复用缓存 %s（%d 只 / %d 日 / mv %d）" % (cache, len(daily), len(days), len(mv)),
              flush=True)
        return days, daily, mv
    import wolf_ma144_regime as M
    import tushare_relay as R
    rows = M.closes(refresh=False) or []
    days = [str(d) for d, _c in rows if str(d) <= str(cut)][-int(n_days):]
    daily, mv = {}, {}
    for i, d in enumerate(days):
        t0 = time.time()
        _f, items = R.relay_items("daily", fields="ts_code,trade_date,close,low,high,amount",
                                  trade_date=d)
        for it in (items or []):
            ts = str(it[0])
            daily.setdefault(ts, []).append((str(it[1]), it[2], it[3], it[4], it[5]))
        print("[panel] %s %d 行 %d/%d (%.1fs)" % (d, len(items or []), i + 1, len(days), time.time() - t0),
              flush=True)
    try:
        _f, mitems = R.relay_items("daily_basic", fields="ts_code,total_mv", trade_date=str(cut))
        mv = {str(x[0]): x[1] for x in (mitems or [])}
    except Exception as e:
        print("[panel] daily_basic 取数失败:", str(e)[:80], flush=True)
    if cache:
        try:
            import pickle
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "wb") as f:
                pickle.dump((days, daily, mv), f)
            print("[panel] 缓存写出 %s" % cache, flush=True)
        except Exception as e:
            print("[panel] 缓存写失败:", str(e)[:80], flush=True)
    return days, daily, mv


def make_fake_gz(daily, mv_by_date):
    """把 `rotation_switch_arm._gz` 换成"从本地 panel 取数"，字段顺序与生产请求一致。"""
    def _gz(api, params, fields):
        p = params or {}
        if api == "daily_basic":
            return list((mv_by_date.get(str(p.get("trade_date"))) or {}).items())
        if api == "daily":
            ts = str(p.get("ts_code") or "")
            s, e = str(p.get("start_date") or ""), str(p.get("end_date") or "")
            out = []
            for d, c, lo, hi, amt in sorted(daily.get(ts) or []):
                if s <= d <= e:
                    out.append((ts, d, c, amt, lo, hi))       # 与请求的 ts_code,trade_date,close,amount,low,high 对齐
            return out
        return []
    return _gz


def legs_from_log(date8: str):
    """从当日 09:2x 的调度日志里取实盘布腿与买链（`DECISION ...` 行）。"""
    for p in sorted(glob.glob(os.path.join("/app/logs/rotation_switch_arm", "*.json"))):
        try:
            j = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if not str(j.get("started_at") or "").startswith("%s-%s-%s" % (date8[:4], date8[4:6], date8[6:])):
            continue
        txt = str(j.get("output") or "")
        for ln in txt.splitlines():
            if ln.startswith("DECISION"):
                try:
                    chains = ast.literal_eval(ln.split("buy_chains", 1)[1].split(" buy_legs")[0].strip())
                    legs = ast.literal_eval(ln.split("buy_legs", 1)[1].strip())
                except Exception:
                    continue
                return {"file": os.path.basename(p), "chains": chains, "legs": legs}
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="决策日（T，如 20260915）")
    ap.add_argument("--cut", required=True, help="数据切点（T−1，如 20260914）")
    ap.add_argument("--chains", default="", help="逗号分隔的买链（默认取自当日 09:20 日志）")
    ap.add_argument("--panel-days", type=int, default=90)
    ap.add_argument("--panel-cache", default="", help="panel pickle 缓存路径（复用/加速多变体）")
    ap.add_argument("--held-at-cut", default="",
                    help="**09:20 时点的持仓**（逗号分隔，用于 exclude）。默认取当前持仓 —— "
                         "注意：当日已被买进的票现在也在持仓里，会把 09:20 的候选错误剔除（实测："
                         "SZ002156/SH600584 当日 10:2x 才买进，回放时必须仍在候选域里）")
    ap.add_argument("--daily-basic", default="empty", choices=["empty", "cut"],
                    help="市值预筛用哪天的 daily_basic：empty=模拟盘前取不到（=09:20 实况）/ cut=用切点日")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    import importlib
    arm = importlib.import_module("rotation_switch_arm")
    EF = importlib.import_module("wolf_entry_filters")

    armed = legs_from_log(a.date)
    if a.chains:
        chains = [(c.strip(), "mainline") for c in a.chains.split(",") if c.strip()]
    else:
        chains = [(c, s) for c, s in (armed.get("chains") or [])]
    print("[replay] 实盘 09:20 日志=%s chains=%s" % (armed.get("file"), chains), flush=True)
    print("[replay] 实盘布腿=%s" % [l.get("symbol") for l in (armed.get("legs") or [])], flush=True)

    days, daily, mv = panels(a.cut, a.panel_days, a.panel_cache)
    print("[replay] panel: %d 只票 / %d 个交易日（%s → %s）"
          % (len(daily), len(days), days[0] if days else "-", days[-1] if days else "-"), flush=True)

    arm._today = lambda: str(a.cut)                       # ① 数据切点 = 09:20 实况
    arm._gz = make_fake_gz(daily, {str(a.cut): mv} if a.daily_basic == "cut" else {})
    if a.held_at_cut:
        held = {x.strip() for x in a.held_at_cut.split(",") if x.strip()}
        print("[replay] 排除集=**切点日持仓(手工给定)** %d 只: %s" % (len(held), sorted(held)), flush=True)
    else:
        held = {p["symbol"] for p in (arm.held_positions() or [])}
        print("[replay] 排除集=当前持仓 %d 只（⚠️ 含当日新买进的票）: %s" % (len(held), sorted(held)), flush=True)

    dom, repl = [], []
    for chain, side in chains:
        try:
            picks = arm.pick_buy(chain, exclude=held, limit=3, domain_out=dom)
        except Exception as e:
            print("[replay] pick_buy %s ERR %s" % (chain, str(e)[:120]), flush=True)
            continue
        print("[replay] pick_buy %-12s → %s" % (chain, [p["symbol"] for p in picks]), flush=True)
        for p in picks:
            repl.append({"symbol": p["symbol"], "chain": chain, "side": side})
    print("[replay] 候选域 %d 只（其中 dist+pos 齐全 %d 只）"
          % (len(dom), len([d for d in dom if d.get("dist_prevlow") is not None and d.get("pos") is not None])),
          flush=True)

    os.environ["WOLF_ENTRY_QDOMAIN"] = "cand"
    legs = armed.get("legs") or repl
    kept, blocked = EF.filter_legs(legs, a.cut, domain=dom)          # ② 用切点口径判实盘那几条腿
    print("[replay] 切点口径判**实盘腿** %s → 保留 %s / 拦 %s"
          % ([l.get("symbol") for l in legs], [l.get("symbol") for l in kept],
             [(b["symbol"], b["why"]) for b in blocked]), flush=True)

    k2, b2 = EF.filter_legs(legs, a.cut, domain=[])                  # ③ 对照：旧口径（终选腿）
    print("[replay] 旧口径(legs) 对照 → 保留 %s / 拦 %s"
          % ([l.get("symbol") for l in k2], [b["symbol"] for b in b2]), flush=True)

    out = {"date": a.date, "cut": a.cut, "daily_basic": a.daily_basic, "chains": chains,
           "armed_legs": [l.get("symbol") for l in (armed.get("legs") or [])],
           "replayed_legs": [l["symbol"] for l in repl],
           "fidelity_same": sorted([l["symbol"] for l in repl]) ==
                            sorted([l.get("symbol") for l in (armed.get("legs") or [])]),
           "domain_n": len(dom), "domain": dom,
           "kept": [l.get("symbol") for l in kept],
           "blocked": blocked, "legacy_blocked": b2}
    p = a.json or os.path.join(DATA, "replay_entry_%s.json" % a.date)
    try:
        json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[replay] 写出 %s" % p, flush=True)
    except Exception as e:
        print("[replay] 写盘失败 %s" % str(e)[:80], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
