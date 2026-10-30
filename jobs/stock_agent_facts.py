#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stock_agent_facts.py — P3 选股 agent 的事实底稿（只读）。

从主题 source 的成员池取候选（默认市值 top N；--full 用全部成员），算：
  1d/5d/20d/60d/120d、20/60/120日高低距离、成交额、换手、市值、拥挤、
  涨停次数、ST/上市状态、业绩快报、老龙头辨识度组件；
  筹码（cyq_perf）、股东户数、龙虎榜席位（20d）、大宗交易（180d）。
输出 data/mainline/{date}/stock_agent_facts.json，供 agent 选股。
"""
from __future__ import annotations

import argparse
import json
import time
import random
import pickle
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Set

import yaml
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "jobs"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from mainline_common import last_trade_date, pct, trade_dates, write_json  # noqa: E402
from core.tushare_relay import relay_query  # noqa: E402
from position_rules import classify, load_cfg  # noqa: E402

THEME = os.path.join(ROOT, "config", "mainline", "generated", "universe_theme.yaml")
MAINLINE_CFG = os.path.join(ROOT, "config", "mainline", "universe_mainline.yaml")


def load_yaml(path: str):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_crowd() -> Dict[str, float]:
    p = os.path.join(ROOT, "data", "fund_holdings", "aggregate.json")
    if not os.path.exists(p):
        return {}
    try:
        return {str(x.get("symbol")): float(x.get("sum_float_ratio") or 0)
                for x in json.load(open(p, encoding="utf-8"))}
    except Exception:
        return {}


_MEM_CACHE_PATH = os.path.join(ROOT, "data", "dc_members_cache.json")


def _load_mem_cache() -> Dict[str, Any]:
    try:
        return json.load(open(_MEM_CACHE_PATH, encoding="utf-8"))
    except Exception:
        return {}


def _save_mem_cache(c: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_MEM_CACHE_PATH), exist_ok=True)
        json.dump(c, open(_MEM_CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _dc_members(code: str, date8: str) -> Set[str]:
    cache = _load_mem_cache()
    key = f"{code}|{date8}"
    if key in cache and cache[key]:
        return set(cache[key])
    last = None
    for i in range(3):
        try:
            m = relay_query("dc_member", ts_code=code, trade_date=date8, limit=5000)
            if m is not None and len(m):
                members = set(m["con_code"].astype(str))
                cache[key] = sorted(members)
                _save_mem_cache(cache)
                return members
            last = "empty"
        except Exception as e:
            last = str(e)[:80]
        time.sleep(0.8 * (i + 1))
    print(f"[members] {code} {date8} 失败: {last}", file=sys.stderr)
    return set()


def members_for(t: Dict[str, Any], date8: str) -> Set[str]:
    out: Set[str] = set()
    for s in t.get("sources") or []:
        typ, code = s.get("type"), s.get("code")
        try:
            if typ == "csi":
                for wd in (date8, "20260930", "20260630", "20260331"):
                    w = relay_query("index_weight", index_code=code, trade_date=wd)
                    if w is not None and len(w):
                        out |= set(w["con_code"].astype(str)); break
            elif typ in {"sw_l2", "sw_l3"}:
                m = relay_query("index_member_all", limit=10000)
                col = "l3_code" if typ == "sw_l3" else "l2_code"
                out |= set(m[m[col].astype(str) == code]["ts_code"].astype(str))
            elif typ == "eastmoney":
                out |= _dc_members(code, date8)
        except Exception:
            continue
    return out



def _fetch_earnings(code: str, date8: str) -> Dict[str, Any]:
    try:
        d = relay_query("express", ts_code=code, limit=20)
        if d is None or len(d) == 0:
            return {"status": "missing"}
        d = d.sort_values("end_date", ascending=False)
        recs = []
        for _, r in d.iterrows():
            _ann = str(r.get("ann_date") or "")
            if _ann and _ann > date8:
                continue
            recs.append({
                "end_date": str(r.get("end_date") or ""),
                "revenue": float(r.get("revenue") or 0),
                "n_income": float(r.get("n_income") or 0),
                "announce_date": str(r.get("ann_date") or ""),
                "eps": float(r.get("diluted_eps") or 0),
                "roe": float(r.get("diluted_roe") or 0),
            })
        by = {x["end_date"]: x for x in recs}
        for x in recs:
            ed = x["end_date"]
            if len(ed) != 8: continue
            prev = by.get(str(int(ed[:4]) - 1) + ed[4:])
            if not prev: continue
            for k in ("revenue", "n_income"):
                pv = float(prev.get(k) or 0)
                if pv > 0:
                    x["yoy_" + k] = round((float(x.get(k) or 0) / pv - 1) * 100, 2)
        latest = recs[0]
        latest["status"] = "loss" if float(latest.get("n_income") or 0) <= 0 else "ok"
        return latest
    except Exception as e:
        return {"status": "missing", "error": str(e)[:80]}


_BASIC_CACHE = os.path.join(ROOT, "data", "stock_basic_cache.json")
_BARS_CACHE_DIR = os.path.join(ROOT, "data", "bars_cache")
_BARS_MEM_CACHE: Dict[str, Dict[str, Any]] = {}


def _stock_basic_map() -> Dict[str, Any]:
    try:
        if os.path.exists(_BASIC_CACHE) and time.time() - os.path.getmtime(_BASIC_CACHE) < 7 * 86400:
            raw = json.load(open(_BASIC_CACHE, encoding="utf-8"))
            return {str(x["ts_code"]): type("B", (), x) for x in raw}
    except Exception:
        pass
    try:
        d = relay_query("stock_basic", list_status="L", limit=6000)
        if d is not None and len(d):
            rows = d.to_dict("records")
            json.dump(rows, open(_BASIC_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
            return {str(x["ts_code"]): type("B", (), x) for x in rows}
    except Exception as e:
        print(f"[basic] 全量失败: {str(e)[:80]}", file=sys.stderr)
    return {}


def _load_bars_cache(date8: str) -> Dict[str, Any]:
    if date8 in _BARS_MEM_CACHE:
        return _BARS_MEM_CACHE[date8]
    p = os.path.join(_BARS_CACHE_DIR, f"{date8}.pkl")
    try:
        cache = pickle.load(open(p, "rb")) if os.path.exists(p) else {}
    except Exception:
        cache = {}
    _BARS_MEM_CACHE[date8] = cache
    return cache


def _save_bars_cache(date8: str, cache: Dict[str, Any]) -> None:
    _BARS_MEM_CACHE[date8] = cache
    try:
        os.makedirs(_BARS_CACHE_DIR, exist_ok=True)
        with open(os.path.join(_BARS_CACHE_DIR, f"{date8}.pkl"), "wb") as f:
            pickle.dump(cache, f)
    except Exception as e:
        print(f"[bars] cache save fail: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)


def _daily_chunk(part: List[str], start: str, end: str) -> Dict[str, Any]:
    for attempt in range(3):
        try:
            d = relay_query("daily", ts_code=",".join(part), start_date=start, end_date=end, limit=10000)
            if d is not None and len(d):
                return {str(code): g.sort_values("trade_date") for code, g in d.groupby("ts_code")}
        except Exception as e:
            msg = str(e)
            print(f"[bars] {len(part)} codes attempt {attempt+1} fail: {msg[:80]}", file=sys.stderr)
            if "429" in msg or "1000 calls" in msg or "rate" in msg.lower():
                time.sleep([5, 10, 20][min(attempt, 2)])
                continue
        time.sleep(1.2 * (attempt + 1))
    if len(part) <= 1:
        return {}
    mid = len(part) // 2
    left = _daily_chunk(part[:mid], start, end)
    right = _daily_chunk(part[mid:], start, end)
    left.update(right)
    return left


def _fetch_bars_multi(codes: List[str], start: str, end: str, date8: str, chunk: int = 15) -> Dict[str, Any]:
    cache = _load_bars_cache(date8)
    out = {c: cache[c] for c in codes if c in cache}
    missing = [c for c in codes if c not in cache]
    for i in range(0, len(missing), chunk):
        if i > 0:
            time.sleep(0.5 + random.random() * 0.5)
        part = missing[i:i+chunk]
        got = _daily_chunk(part, start, end)
        out.update(got)
        cache.update(got)
        _save_bars_cache(date8, cache)
        if len(got) < len(part):
            print(f"[bars] chunk {i} partial {len(got)}/{len(part)}", file=sys.stderr)
    return out


def _batch_chip(date8: str) -> Dict[str, Dict[str, Any]]:
    try:
        d = relay_query("cyq_perf", trade_date=date8, limit=6000)
        if d is None or len(d) == 0:
            return {}
        out = {}
        for _, r in d.iterrows():
            c50 = float(r.get("cost_50pct") or 0)
            c15 = float(r.get("cost_15pct") or 0)
            c85 = float(r.get("cost_85pct") or 0)
            out[str(r.get("ts_code"))] = {
                "date": str(r.get("trade_date") or ""),
                "winner_rate": float(r.get("winner_rate") or 0),
                "cost_5": float(r.get("cost_5pct") or 0),
                "cost_15": c15, "cost_50": c50, "cost_85": c85,
                "cost_95": float(r.get("cost_95pct") or 0),
                "weight_avg": float(r.get("weight_avg") or 0),
                "concentration": round((c85 - c15) / c50, 3) if c50 > 0 else None,
                "status": "ok",
            }
        return out
    except Exception:
        return {}


def _batch_holders(date8: str) -> Dict[str, Dict[str, Any]]:
    periods = ["20260930", "20260630", "20260331", "20251231", "20250930", "20250630", "20250331", "20241231"]
    by: Dict[str, list] = {}
    for p in periods:
        if p > date8: continue
        try:
            d = relay_query("stk_holdernumber", end_date=p, limit=6000)
            if d is None or len(d) == 0: continue
            for _, r in d.iterrows():
                ann = str(r.get("ann_date") or "")
                if ann and ann > date8: continue
                by.setdefault(str(r.get("ts_code")), []).append({
                    "end_date": str(r.get("end_date") or ""),
                    "ann_date": ann,
                    "holder_num": int(r.get("holder_num") or 0),
                })
        except Exception:
            continue
    out = {}
    for code, rows in by.items():
        if not rows:
            print(f"[holders] empty rows for {code}", file=sys.stderr)
            continue
        rows = sorted(rows, key=lambda x: x["end_date"])
        latest = rows[-1]; prev = rows[-2] if len(rows) > 1 else None
        cur = latest["holder_num"]; prevn = prev["holder_num"] if prev else None
        out[code] = {
            "end_date": latest["end_date"], "ann_date": latest["ann_date"],
            "holder_num": cur, "prev_holder_num": prevn,
            "trend": ("down" if prevn and cur < prevn else "up" if prevn and cur > prevn else "flat"),
            "status": "ok",
        }
    return out


def _batch_express(date8: str) -> Dict[str, Dict[str, Any]]:
    periods = ["20260630", "20251231", "20250630", "20241231", "20240630", "20231231", "20230630", "20221231"]
    by: Dict[str, list] = {}
    for p in periods:
        if p > date8: continue
        try:
            d = relay_query("express", period=p, limit=6000)
            if d is None or len(d) == 0: continue
            for _, r in d.iterrows():
                ann = str(r.get("ann_date") or "")
                if ann and ann > date8: continue
                by.setdefault(str(r.get("ts_code")), []).append({
                    "end_date": str(r.get("end_date") or ""),
                    "revenue": float(r.get("revenue") or 0),
                    "n_income": float(r.get("n_income") or 0),
                    "announce_date": ann,
                    "eps": float(r.get("diluted_eps") or 0),
                    "roe": float(r.get("diluted_roe") or 0),
                })
        except Exception:
            continue
    out = {}
    for code, rows in by.items():
        if not rows:
            print(f"[holders] empty rows for {code}", file=sys.stderr)
            continue
        rows = sorted(rows, key=lambda x: x["end_date"])
        latest = rows[-1]
        byd = {x["end_date"]: x for x in rows}
        ed = latest["end_date"]
        if len(ed) == 8:
            prev = byd.get(str(int(ed[:4]) - 1) + ed[4:])
            if prev:
                for k in ("revenue", "n_income"):
                    pv = float(prev.get(k) or 0)
                    if pv > 0:
                        latest["yoy_" + k] = round((float(latest.get(k) or 0) / pv - 1) * 100, 2)
        latest["status"] = "loss" if float(latest.get("n_income") or 0) <= 0 else "ok"
        out[code] = latest
    return out


def _fetch_chip(code: str, date8: str) -> Dict[str, Any]:
    try:
        start = (datetime.strptime(date8, "%Y%m%d") - timedelta(days=15)).strftime("%Y%m%d")
        d = relay_query("cyq_perf", ts_code=code, start_date=start, end_date=date8, limit=10)
        if d is None or len(d) == 0:
            return {"status": "missing"}
        r = d.sort_values("trade_date").iloc[-1]
        c50 = float(r.get("cost_50pct") or 0)
        c15 = float(r.get("cost_15pct") or 0)
        c85 = float(r.get("cost_85pct") or 0)
        close = float(r.get("weight_avg") or 0)
        return {
            "date": str(r.get("trade_date") or ""),
            "winner_rate": float(r.get("winner_rate") or 0),
            "cost_5": float(r.get("cost_5pct") or 0),
            "cost_15": c15, "cost_50": c50, "cost_85": c85,
            "cost_95": float(r.get("cost_95pct") or 0),
            "weight_avg": close,
            "concentration": round((c85 - c15) / c50, 3) if c50 > 0 else None,
            "status": "ok",
        }
    except Exception as e:
        return {"status": "missing", "error": str(e)[:80]}


def _attach_chip_price(chip: Dict[str, Any], close: float) -> Dict[str, Any]:
    try:
        c50 = float(chip.get("cost_50") or 0)
        c85 = float(chip.get("cost_85") or 0)
        chip["price_vs_cost50"] = round(close / c50 - 1, 4) if c50 > 0 else None
        chip["price_vs_cost85"] = round(close / c85 - 1, 4) if c85 > 0 else None
    except Exception:
        pass
    return chip


def _fetch_holders(code: str, date8: str) -> Dict[str, Any]:
    try:
        start = (datetime.strptime(date8, "%Y%m%d") - timedelta(days=730)).strftime("%Y%m%d")
        d = relay_query("stk_holdernumber", ts_code=code, start_date=start, end_date=date8, limit=10)
        if d is None or len(d) == 0:
            return {"status": "missing"}
        d = d.sort_values("end_date")
        latest = d.iloc[-1]
        prev = d.iloc[-2] if len(d) > 1 else None
        cur = int(latest.get("holder_num") or 0)
        prevn = int(prev.get("holder_num") or 0) if prev is not None else None
        return {
            "end_date": str(latest.get("end_date") or ""),
            "ann_date": str(latest.get("ann_date") or ""),
            "holder_num": cur,
            "prev_holder_num": prevn,
            "trend": ("down" if prevn and cur < prevn else "up" if prevn and cur > prevn else "flat"),
            "status": "ok",
        }
    except Exception as e:
        return {"status": "missing", "error": str(e)[:80]}


def _fetch_block(code: str, date8: str, close: float) -> Dict[str, Any]:
    try:
        start = (datetime.strptime(date8, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
        d = relay_query("block_trade", ts_code=code, start_date=start, end_date=date8, limit=100)
        if d is None or len(d) == 0:
            return {"status": "ok", "count": 0}
        amount = float(pd.to_numeric(d["amount"], errors="coerce").fillna(0).sum())
        avg = float(pd.to_numeric(d["price"], errors="coerce").fillna(0).mean())
        buyers = [str(x or "") for x in d.get("buyer", [])]
        sellers = [str(x or "") for x in d.get("seller", [])]
        return {
            "status": "ok", "count": int(len(d)), "amount": round(amount, 2),
            "avg_price": round(avg, 3),
            "avg_price_vs_close": round(avg / close - 1, 4) if close > 0 and avg > 0 else None,
            "window": "180d",
            "inst_buy": sum(1 for x in buyers if "机构" in x),
            "inst_sell": sum(1 for x in sellers if "机构" in x),
        }
    except Exception as e:
        return {"status": "missing", "error": str(e)[:80]}


def _attach_seats(cands: List[Dict[str, Any]], date8: str) -> None:
    dates = trade_dates(date8, start=(datetime.strptime(date8, "%Y%m%d") - timedelta(days=45)).strftime("%Y%m%d"))[-20:]
    rows = []
    for d in dates:
        try:
            x = relay_query("top_inst", trade_date=d, limit=10000)
            if x is not None and len(x):
                rows.append(x)
        except Exception:
            continue
    allr = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    for c in cands:
        if allr is None or len(allr) == 0 or "ts_code" not in allr.columns:
            c["seats"] = {"status": "missing", "window": "20d"}
            continue
        x = allr[allr["ts_code"].astype(str) == c["symbol"]]
        if len(x) == 0:
            c["seats"] = {"status": "ok", "window": "20d", "list_days": 0, "inst_net": 0, "hot_money_net": 0}
            continue
        inst = x[x["exalter"].astype(str).str.contains("机构", na=False)]
        hot = x[~x["exalter"].astype(str).str.contains("机构", na=False)]
        net = lambda y: float(pd.to_numeric(y["net_buy"], errors="coerce").fillna(0).sum()) if len(y) else 0.0
        c["seats"] = {
            "status": "ok", "window": "20d",
            "list_days": int(x["trade_date"].nunique()) if "trade_date" in x.columns else int(len(x)),
            "inst_net": round(net(inst), 2), "hot_money_net": round(net(hot), 2),
            "inst_rows": int(len(inst)), "hot_money_rows": int(len(hot)),
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--top", type=int, default=18)
    ap.add_argument("--full", action="store_true", help="使用主题全部成员（不只 top N）")
    ap.add_argument("--advanced-top", type=int, default=15, help="基础筛选后每主题拉高级字段的只数")
    ap.add_argument("--min-amount-yi", type=float, default=1.0, help="基础筛选：5日成交额下限（亿）")
    a = ap.parse_args()
    date8 = last_trade_date(a.date)
    _start = (datetime.strptime(date8, "%Y%m%d") - timedelta(days=400)).strftime("%Y%m%d")
    ds = trade_dates(a.date, start=_start)
    d20 = ds[-21] if len(ds) >= 21 else ds[0]
    d60 = ds[-61] if len(ds) >= 61 else ds[0]
    d120 = ds[-121] if len(ds) >= 121 else ds[0]
    d250 = ds[-251] if len(ds) >= 251 else ds[0]
    theme_json = json.load(open(os.path.join(ROOT, "data", "mainline", date8, "theme.json"), encoding="utf-8"))
    try:
        _ml = json.load(open(os.path.join(ROOT, "data", "mainline", date8, "mainline.json"), encoding="utf-8"))
    except Exception:
        _ml = {}
    _ml_active = bool(_ml.get("mainline_active"))
    rows = {r["id"]: r for r in theme_json.get("themes", []) if "error" not in r}
    cfg = load_yaml(THEME)
    tcfg = {t["id"]: t for t in (cfg.get("themes") or []) + (cfg.get("satellite_themes") or [])}
    # 只保留 L0 主线下的赛道/主题；避免把 L1 其它 trial 全塞进 P3
    _allowed_tracks = set()
    try:
        _mc = load_yaml(MAINLINE_CFG)
        for _m in (_mc.get("mainlines") or []):
            if _m.get("id") == (_ml.get("mainline") or ""):
                _allowed_tracks = set(_m.get("tracks") or [])
                break
    except Exception:
        pass
    active = []
    for _x in (theme_json.get("focus") or []) + (theme_json.get("trial") or []):
        _tid = _x.get("id")
        _t = tcfg.get(_tid) or {}
        if _allowed_tracks and _t.get("parent_track") not in _allowed_tracks:
            continue
        active.append(_tid)
    try:
        db = relay_query("daily_basic", trade_date=date8, limit=6000)
        dbmap = {str(r.ts_code): r for r in db.itertuples()}
    except Exception:
        dbmap = {}
    bmap = _stock_basic_map()
    crowd = load_crowd()
    pos_cfg = load_cfg()
    chip_map = _batch_chip(date8)
    holders_map = _batch_holders(date8)
    express_map = _batch_express(date8)

    def _limit_th(code: str) -> float:
        if code.startswith(("300", "301", "688")):
            return 19.8
        if code.startswith(("8", "4")):
            return 29.8
        return 9.8
    out_themes = []
    for tid in active:
        t = tcfg.get(tid) or {}
        tr = rows.get(tid) or {}
        mem = members_for(t, date8)
        if not mem:
            out_themes.append({"theme_id": tid, "name": t.get("name"), "error": "no members",
                               "verdict": tr.get("verdict"), "thesis_valid": tr.get("thesis_valid")})
            continue
        # 默认按市值取 top；--full / --top 0 使用全部成员
        _all = sorted(mem, key=lambda c: -float(getattr(dbmap.get(c), "circ_mv", 0) or 0))
        pool = _all if (a.full or a.top <= 0) else _all[: a.top]
        for x in [t.get("leader")] + (t.get("ladder") or []):
            if x and x.get("code") and x["code"] in mem and x["code"] not in pool:
                pool.append(x["code"])
        bars_map = _fetch_bars_multi(pool, d250, date8, date8)
        cands = []
        for code in pool:
            r = dbmap.get(code)
            name = getattr(r, "name", "") if r else ""
            if not name:
                br0 = bmap.get(code)
                name = str(getattr(br0, "name", "") or code) if br0 else code
            try:
                d = bars_map.get(code)
                if d is None or len(d) < 2:
                    continue
                d = d.sort_values("trade_date")
                close = [float(v) for v in d["close"]]
                high = [float(v) for v in d["high"]]
                low = [float(v) for v in d["low"]]
                amt = [float(v or 0) for v in d["amount"]]
                pctc = [float(v or 0) for v in d["pct_chg"]]
                dates = [str(v) for v in d["trade_date"]]
            except Exception:
                continue
            ret1 = pct(close[-1], close[-2]) if len(close) >= 2 else 0
            ret5 = pct(close[-1], close[-6]) if len(close) >= 6 else 0
            ret20 = pct(close[-1], close[-21]) if len(close) >= 21 else pct(close[-1], close[0])
            ret60 = pct(close[-1], close[-61]) if len(close) >= 61 else pct(close[-1], close[0])
            ret120 = pct(close[-1], close[-121]) if len(close) >= 121 else pct(close[-1], close[0])
            hi60 = max(high[-60:]) if len(high) >= 2 else close[-1]
            lo60 = min(low[-60:]) if len(low) >= 2 else close[-1]
            hi120 = max(high[-120:]) if len(high) >= 2 else close[-1]
            lo120 = min(low[-120:]) if len(low) >= 2 else close[-1]
            pos120 = ((close[-1] - lo120) / (hi120 - lo120)) if hi120 > lo120 else 0.5
            circ_mv = float(getattr(r, "circ_mv", 0) or 0) if r else 0
            turnover = float(getattr(r, "turnover_rate", 0) or 0) if r else 0
            br = bmap.get(code)
            bname = str(getattr(br, "name", "") or name)
            list_status = str(getattr(br, "list_status", "") or "")
            is_st = ("ST" in bname.upper()) or ("退" in bname)
            cands.append({
                "symbol": code, "name": name,
                "close": round(close[-1], 3),
                "ret1": round(ret1, 4), "ret5": round(ret5, 4), "ret20": round(ret20, 4),
                "ret60": round(ret60, 4), "ret120": round(ret120, 4),
                "dist_high20": round(pct(close[-1], max(high[-20:])), 4),
                "dist_low20": round(pct(close[-1], min(low[-20:])), 4),
                "dist_high60": round(pct(close[-1], hi60), 4),
                "dist_low60": round(pct(close[-1], lo60), 4),
                "dist_high120": round(pct(close[-1], hi120), 4),
                "dist_low120": round(pct(close[-1], lo120), 4),
                "range_pos_120": round(pos120, 4),
                "amount5_yi": round(sum(amt[-5:]) / max(1, len(amt[-5:])) / 1e5, 2),
                "turnover": round(turnover, 2),
                "circ_mv_yi": round(circ_mv / 1e4, 1),
                "crowding_float": round(crowd.get(code, 0.0), 2),
                "limit_up_20d": int(sum(1 for x in pctc[-20:] if x >= _limit_th(code))),
                "limit_up_120d": int(sum(1 for x in pctc[-120:] if x >= _limit_th(code))),
                "is_st": bool(is_st), "list_status": list_status,
                "is_leader": bool(t.get("leader") and t["leader"].get("code") == code),
                "is_ladder": any(x and x.get("code") == code for x in (t.get("ladder") or [])),
                "_pctc": pctc[-120:], "_dates": dates[-120:],
            })
        # 老龙头辨识度：候选池内 120 日涨幅前 3 的次数（pool 口径，不是全主题）
        n = min([len(c["_pctc"]) for c in cands]) if cands else 0
        if n > 0:
            for c in cands:
                c["top3_days_120_pool"] = 0
            for off in range(1, n + 1):
                vals = sorted(((c["symbol"], c["_pctc"][-off]) for c in cands), key=lambda x: -x[1])
                for sym, _ in vals[:3]:
                    for c in cands:
                        if c["symbol"] == sym:
                            c["top3_days_120_pool"] = c.get("top3_days_120_pool", 0) + 1
                            break
        for c in cands:
            c["pool_size"] = len(cands)
            c["leader"] = {"seed_role": ("leader" if c.get("is_leader") else "ladder" if c.get("is_ladder") else "none"),
                           "limit_up_120d": c.get("limit_up_120d", 0),
                           "top3_days_120_pool": c.get("top3_days_120_pool", 0),
                           "pool_size": len(cands)}
            c.pop("_pctc", None); c.pop("_dates", None)
        # 两阶段：先基础筛选，再对少量候选拉筹码/股东/业绩/席位
        keep = [c for c in cands if not c.get("is_st") and float(c.get("amount5_yi") or 0) >= a.min_amount_yi]
        if not keep:
            keep = cands
        keep.sort(key=lambda c: (not c.get("is_leader"), -float(c.get("amount5_yi") or 0)))
        if a.advanced_top and a.advanced_top > 0:
            keep = keep[: a.advanced_top]
        for c in keep:
            c["earnings"] = express_map.get(c["symbol"], {"status": "missing"})
            c["chip"] = _attach_chip_price(chip_map.get(c["symbol"], {"status": "missing"}), float(c["close"]))
            c["holders"] = holders_map.get(c["symbol"], {"status": "missing"})
            c["block_trade"] = _fetch_block(c["symbol"], date8, float(c["close"]))
            c["position"] = classify(c, pos_cfg, mainline_active=_ml_active)
        _attach_seats(keep, date8)
        cands = keep
        cands.sort(key=lambda x: -x["circ_mv_yi"])
        out_themes.append({
            "theme_id": tid, "name": t.get("name"),
            "verdict": tr.get("verdict"), "thesis_valid": tr.get("thesis_valid"),
            "review_required": tr.get("review_required"),
            "parent_track": t.get("parent_track"),
            "members": len(mem), "pool_mode": ("full" if (a.full or a.top <= 0) else "top"), "advanced_top": a.advanced_top, "candidates": cands,
        })
    out = {"as_of": date8, "holding_days": 5, "mainline": theme_json.get("mainline_regime"),
           "themes": out_themes}
    path = os.path.join(ROOT, "data", "mainline", date8, "stock_agent_facts.json")
    write_json(path, out)
    print(f"as_of={date8} themes={len(out_themes)}")
    for t in out_themes:
        print(f"  {t['name']}: members={t.get('members')} candidates={len(t.get('candidates',[]))} verdict={t.get('verdict')}")
    print(f"written: {path}")


if __name__ == "__main__":
    main()
