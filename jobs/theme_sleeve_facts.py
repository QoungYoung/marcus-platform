#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_sleeve_facts.py — 题材 sleeve 证据卡 v2（实验版，不交易）。

从 L2 non-mainline trial/watch 生成：
  · 题材强度：ret1/ret5/excess5/breadth/amount_ratio
  · 题材成员：全成员（dc_member/index_weight，缓存）
  · B4：按题材成员直接聚合涨停/炸板/最高板/梯队
  · 套牢：cyq_perf 的 winner_rate / price_vs_cost85
  · 角色：1 龙头 / 2 风向 / 3 个 10CM 中流砥柱
输出 data/mainline/{date}/theme_sleeve_facts.json。
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "backend"), os.path.join(ROOT, "core"), os.path.join(ROOT, "jobs")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tushare_relay import relay_query  # noqa: E402
from stock_agent_facts import members_for, _fetch_bars_multi  # noqa: E402


def load_yaml(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _f(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def _i(v):
    f = _f(v)
    return 0 if f != f else int(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--skip-intraday", action="store_true")
    a = ap.parse_args()
    base = os.path.join(ROOT, "data", "mainline", a.date)
    theme = json.load(open(os.path.join(base, "theme.json"), encoding="utf-8"))
    ml = json.load(open(os.path.join(base, "mainline.json"), encoding="utf-8"))
    mcfg = load_yaml(os.path.join(ROOT, "config", "mainline", "universe_mainline.yaml"))
    tcfg = load_yaml(os.path.join(ROOT, "config", "mainline", "generated", "universe_theme.yaml"))
    ts = {x["id"]: x for x in (tcfg.get("themes") or []) + (tcfg.get("satellite_themes") or [])}
    main_tracks = set()
    for m in mcfg.get("mainlines") or []:
        if m.get("id") == ml.get("mainline"):
            main_tracks = set(m.get("tracks") or [])
            break

    # 当日行情（一次全市场）
    daily = {}
    try:
        d = relay_query("daily", trade_date=a.date, limit=6000)
        if d is not None:
            for _, r in d.iterrows():
                daily[str(r.get("ts_code"))] = {"pct": _f(r.get("pct_chg")), "close": _f(r.get("close")),
                                                "amount": _f(r.get("amount"))}
    except Exception as e:
        print("[theme] daily fail:", str(e)[:100], flush=True)

    # 涨停梯队：当天没有就退上一交易日，并标记 stale
    lrows, b4_date, b4_stale = [], a.date, False
    try:
        d = relay_query("limit_list_d", trade_date=a.date, limit=6000)
        if d is not None and len(d):
            lrows = d.to_dict("records")
        else:
            from mainline_common import last_trade_date
            prev = last_trade_date((datetime.strptime(a.date, "%Y%m%d") - __import__("datetime").timedelta(days=1)).strftime("%Y%m%d"))
            d = relay_query("limit_list_d", trade_date=prev, limit=6000)
            if d is not None and len(d):
                lrows = d.to_dict("records"); b4_date = prev; b4_stale = True
    except Exception as e:
        print("[theme] limit_list fail:", str(e)[:100], flush=True)
    lim = {str(r.get("ts_code")): r for r in lrows}

    # 筹码：一次全市场
    chips = {}
    try:
        d = relay_query("cyq_perf", trade_date=a.date, limit=6000)
        if d is not None:
            for _, r in d.iterrows():
                chips[str(r.get("ts_code"))] = {"winner": _f(r.get("winner_rate")),
                                                "cost85": _f(r.get("cost_85pct")),
                                                "cost15": _f(r.get("cost_15pct")),
                                                "cost50": _f(r.get("cost_50pct"))}
    except Exception as e:
        print("[theme] cyq fail:", str(e)[:100], flush=True)

    full = {z.get("id"): z for z in (theme.get("themes") or [])}
    ihist_path = os.path.join(ROOT, "data", "theme_intraday_history.json")
    try:
        ihist = json.load(open(ihist_path, encoding="utf-8"))
    except Exception:
        ihist = {}
    hist_path = os.path.join(ROOT, "data", "theme_b4_history.json")
    try:
        hist = json.load(open(hist_path, encoding="utf-8"))
    except Exception:
        hist = {}
    lk_path = os.path.join(ROOT, "data", "theme_linkage_history.json")
    try:
        lk = json.load(open(lk_path, encoding="utf-8"))
    except Exception:
        lk = {}
    out = []
    for x in (theme.get("focus") or []) + (theme.get("trial") or []) + (theme.get("watch") or []):
        tid = x.get("id")
        t = ts.get(tid) or {}
        if t.get("parent_track") in main_tracks:
            continue
        fx = full.get(tid) or {}
        mem = []
        try:
            _srcs = list(t.get("sources") or []) + list(((t.get("resolution") or {}).get("selected")) or [])
            _t2 = dict(t); _t2["sources"] = _srcs
            mem = sorted(set(members_for(_t2, a.date)))
        except Exception:
            mem = []
        # 历史人气：成员 120 日内进入主题涨幅前 3 的次数
        pop = {}
        try:
            from datetime import timedelta as _td
            d120 = (datetime.strptime(a.date, "%Y%m%d") - _td(days=200)).strftime("%Y%m%d")
            bm = _fetch_bars_multi(mem, d120, a.date, a.date) if mem else {}
            pcts = {}
            for c, df in bm.items():
                if df is None or len(df) < 2:
                    continue
                pcts[c] = [float(v or 0) for v in df.sort_values("trade_date")["pct_chg"].tail(120)]
            n = min([len(v) for v in pcts.values()]) if pcts else 0
            for c in pcts:
                pop[c] = 0
            for off in range(1, n + 1):
                vals = sorted(((c, v[-off]) for c, v in pcts.items()), key=lambda x: -x[1])
                for c, _ in vals[:3]:
                    pop[c] = pop.get(c, 0) + 1
        except Exception as e:
            print("[theme] popular fail:", str(e)[:100], flush=True)
        # 角色
        cand = []
        for c in mem:
            q = daily.get(c) or {}
            lr = lim.get(c) or {}
            times = _i(lr.get("limit_times"))
            cand.append({"symbol": c, "pct": q.get("pct"), "amount": q.get("amount"), "close": q.get("close"),
                         "limit": str(lr.get("limit") or ""), "limit_times": times, "popular_120": pop.get(c, 0),
                         "first_time": str(lr.get("first_time") or ""), "open_times": _i(lr.get("open_times")),
                         "fd_amount": _f(lr.get("fd_amount"))})
        def _key(z):
            ft = z.get("first_time") or "999999"
            return (z["limit_times"], -int(ft) if str(ft).isdigit() else -999999,
                    z.get("fd_amount") or 0, z["pct"] or -99, z["amount"] or 0)
        cand.sort(key=_key, reverse=True)
        leader = cand[0] if cand else None
        rest = cand[1:]
        wind = [z for z in rest if z["symbol"].startswith(("300", "301", "688")) and (z["pct"] or 0) > 0][:2]
        wind_syms = {z["symbol"] for z in wind}
        mid = [z for z in rest if z["symbol"] not in wind_syms and not z["symbol"].startswith(("300", "301", "688")) and (z["pct"] or 0) > 0][:3]
        # B4 直接按成员聚合
        u = [z for z in cand if z["limit"] == "U"]
        zz = [z for z in cand if z["limit"] == "Z"]
        dd = [z for z in cand if z["limit"] == "D"]
        times = [z["limit_times"] for z in u]
        ladder = {"1": sum(1 for v in times if v == 1), "2": sum(1 for v in times if v == 2), "3+": sum(1 for v in times if v >= 3)}
        mx = max(times) if times else 0
        open_sum = sum(int(z.get("open_times") or 0) for z in u)
        open_max = max([int(z.get("open_times") or 0) for z in u] or [0])
        tag = "plain"
        if u or zz:
            if open_max >= 2 or (zz and (len(zz) / max(1, len(u) + len(zz))) >= 0.4 and len(zz) >= 2):
                tag = "failed"
            elif len(u) >= 4 and mx <= 1:
                tag = "graduation"
            elif ladder["1"] >= 1 and ladder["2"] >= 1 and (ladder["3+"] >= 1 or ladder["2"] >= 2):
                tag = "complete"
        # 套牢：成员平均
        ov = []
        for z in cand:
            ch = chips.get(z["symbol"])
            if ch and ch.get("cost85") and z.get("close"):
                ov.append({"winner": ch["winner"], "pv85": z["close"] / ch["cost85"] - 1})
        overhang = None
        if ov:
            overhang = {"winner_rate": round(sum(v["winner"] for v in ov) / len(ov), 2),
                        "price_vs_cost85": round(sum(v["pv85"] for v in ov) / len(ov), 4),
                        "n": len(ov)}
        rec = {"theme_id": tid, "name": t.get("name") or x.get("name"), "parent_track": t.get("parent_track"),
               "verdict": x.get("verdict"), "ret1": fx.get("ret1"), "ret5": fx.get("ret5"),
               "excess5": fx.get("excess5"), "breadth": fx.get("breadth"), "amount_ratio": fx.get("amount_ratio"),
               "members": len(mem), "leader": (t.get("leader") or {}),
               "roles": {"leader": (leader or {}).get("symbol"), "wind": [z["symbol"] for z in wind],
                         "mid": [z["symbol"] for z in mid]},
               "b4": {"date": b4_date, "stale": b4_stale, "u": len(u), "z": len(zz), "d": len(dd),
                      "max_times": mx, "ladder": ladder, "tag": tag, "open_sum": open_sum, "open_max": open_max},
               "overhang": overhang,
               "popular_120": {x["symbol"]: x.get("popular_120", 0)
                                  for x in ([leader] + wind + mid if leader else [])
                                  if x},
               "popular_top3": [x["symbol"] for x in sorted(cand, key=lambda z: -(z.get("popular_120") or 0))[:3]],
               "role_detail": {
                   "leader": ({k: leader.get(k) for k in ("symbol", "first_time", "open_times", "fd_amount", "limit_times", "pct")} if leader else None),
                   "wind": [{k: x.get(k) for k in ("symbol", "first_time", "open_times", "fd_amount", "limit_times", "pct")} for x in wind],
                   "mid": [{k: x.get(k) for k in ("symbol", "first_time", "open_times", "fd_amount", "limit_times", "pct")} for x in mid],
               }}
        tier = "watch"
        if tag in ("graduation", "failed"):
            tier = "reject"
        elif tag == "complete" and (fx.get("breadth") or 0) >= 0.5:
            tier = "probe_candidate"
        elif (fx.get("ret1") or 0) > 0.03 and (fx.get("breadth") or 0) > 0.5:
            tier = "watch_strong"
        rec["tier"] = tier
        if not a.skip_intraday:
            try:
                from app.services.t_data_sources import fetch_tencent_mkline
                syms = [rec["roles"].get("leader")] + list(rec["roles"].get("wind") or []) + list(rec["roles"].get("mid") or [])
                syms = [x for x in dict.fromkeys(syms) if x]
                vals = []
                for sym in syms[:6]:
                    _s = sym.split(".")[0]
                    _sym = ("sh" if sym.endswith(".SH") else "sz") + _s
                    bars = fetch_tencent_mkline(_sym, freq="m1", count=500) or []
                    v1330 = 0.0; vopen = 0.0
                    for b in bars:
                        t = str(b.get("time") or "")
                        if len(t) >= 16:
                            hm = t[11:16]
                        elif len(t) >= 12:
                            hm = t[-4:-2] + ":" + t[-2:]
                        else:
                            hm = ""
                        if "13:30" <= hm < "13:35":
                            v1330 += float(b.get("vol") or 0)
                        elif "09:30" <= hm < "10:00":
                            vopen += float(b.get("vol") or 0)
                    if v1330 or vopen:
                        vals.append({"symbol": sym, "v1330": round(v1330, 0), "vopen": round(vopen, 0),
                                     "r": round(v1330 / vopen, 3) if vopen else None})
                rec["intraday"] = {"n": len(vals), "roles": vals}
                old_i = ihist.get(tid) or []
                prev_i = [x for x in old_i if str(x.get("date")) < str(a.date)][-5:]
                if prev_i:
                    # ★ 2026-10-30 修：这里原来叫 `base`（dict）⇒ **覆盖了同函数上面的
                    #   `base = data/mainline/<date>` 路径** ⇒ 文末 os.path.join(base, ...) 抛
                    #   TypeError: expected str, bytes or os.PathLike object, not dict
                    #   （16:45 链就是这样挂的：前几日 intraday 历史一有 prev_i 就触发 ✗）。
                    base_map = {}
                    for pv in prev_i:
                        for z in pv.get("roles") or []:
                            base_map.setdefault(z["symbol"], []).append(z.get("r"))
                    rec["intraday"]["baseline"] = {k: round(sum([v for v in vs if isinstance(v, (int, float))]) / max(1, len([v for v in vs if isinstance(v, (int, float))])), 3) for k, vs in base_map.items()}
                old_i = [x for x in old_i if str(x.get("date")) != str(a.date)]
                old_i.append({"date": a.date, "roles": vals})
                ihist[tid] = old_i[-60:]
            except Exception as e:
                rec["intraday"] = {"error": str(e)[:120]}
        # 板块内联动：大哥先涨 / 二哥不掉 / 中流砥柱承接 / 分时同步
        _lp = (daily.get((leader or {}).get("symbol")) or {}).get("pct") if leader else None
        _wind_up = sum(1 for z in wind if (daily.get(z["symbol"]) or {}).get("pct", -99) > 0)
        _mid_hold = sum(1 for z in mid if (daily.get(z["symbol"]) or {}).get("pct", -99) >= -1.0)
        _sync = None
        if rec.get("intraday") and rec["intraday"].get("roles"):
            rs = [z.get("r") for z in rec["intraday"]["roles"] if z.get("r") is not None]
            if rs:
                _sync = {"mean": round(sum(rs) / len(rs), 2), "burst": sum(1 for x in rs if x >= 1.5), "n": len(rs)}
        _state = "weak"
        _flags = []
        if _lp is not None and _lp <= 0:
            _state = "broken"; _flags.append("leader_down")
        elif _lp is not None and _wind_up >= 1 and _mid_hold >= 2:
            _state = "linked"; _flags.append("leader_up+wind+mid")
        elif _lp is not None and _lp > 0:
            _flags.append("leader_up")
        rec["internal_linkage"] = {"leader_pct": _lp, "wind_up": _wind_up, "mid_hold": _mid_hold,
                                    "sync": _sync, "state": _state, "flags": _flags}
        _oldl = lk.get(tid) or []
        _prev_linked = any(str(x.get("date")) < str(a.date) and x.get("state") == "linked" for x in _oldl)
        rec["first_linkage"] = bool(_state == "linked" and not _prev_linked)
        if _state == "broken":
            rec["tier"] = "reject"
        elif _state == "linked" and rec["first_linkage"] and (fx.get("ret1") or 0) > 0.03 and (fx.get("breadth") or 0) >= 0.5 and tag not in ("failed", "graduation"):
            rec["tier"] = "probe_candidate"
        elif _state == "linked" and rec.get("tier") == "watch":
            rec["tier"] = "watch_strong"
        _oldl = [x for x in _oldl if str(x.get("date")) != str(a.date)]
        _oldl.append({"date": a.date, "state": _state, "leader_pct": _lp, "wind_up": _wind_up, "mid_hold": _mid_hold})
        lk[tid] = _oldl[-120:]
        old = hist.get(tid) or []
        cur = {"date": b4_date, "u": len(u), "z": len(zz), "max_times": mx, "tag": tag}
        prev = next((x for x in reversed(old) if str(x.get("date")) < str(cur["date"])), None)
        if not old or old[-1].get("date") != cur["date"]:
            old.append(cur)
            hist[tid] = old[-120:]
        rec["b4_history"] = {"prev": prev, "n": len(old)}
        out.append(rec)
    out.sort(key=lambda z: (z["tier"] != "probe_candidate", z["tier"] != "watch_strong", -(z.get("ret1") or 0)))
    try:
        json.dump(lk, open(lk_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[theme] linkage history save fail:", str(e)[:100], flush=True)
    try:
        json.dump(ihist, open(ihist_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[theme] intraday history save fail:", str(e)[:100], flush=True)
    try:
        json.dump(hist, open(hist_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[theme] b4 history save fail:", str(e)[:100], flush=True)
    res = {"as_of": a.date, "sleeve": "theme", "mainline": ml.get("mainline"), "tracks": sorted(main_tracks), "themes": out}
    p = os.path.join(base, "theme_sleeve_facts.json")
    json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written", p, "themes", len(out))
    for z in out:
        print(z["tier"], z["theme_id"], "| b4", z["b4"]["tag"], z["b4"]["u"], "/", z["b4"]["z"],
              "| overhang", z["overhang"], "| roles", z["roles"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
