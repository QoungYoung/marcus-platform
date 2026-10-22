# -*- coding: utf-8 -*-
"""狼大选股规则树 v0：分层 + 阶段/位置依赖，不用单因子排序。

阶段：
  strong / extended / weak / downtrend / low_flat / oversold
层级：
  leader(rank1) / second(rank2-4/verified_all) / other
规则：
  strong/extended → 二供/二线（rank 2-4），不追 rank1
  weak/downtrend  → rank1 龙头 / 最抗跌
  low_flat/oversold → flat_low_days 高，再看 rel20
"""
from __future__ import annotations
import glob, json, os, sqlite3
from typing import Any, Dict, List
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT_CANDS = [
    os.path.join(_HERE, "..", "..", "..", ".."),
    os.path.join(_HERE, "..", "..", ".."),
    "/app",
]
ROOT = next((os.path.normpath(p) for p in _ROOT_CANDS
             if os.path.exists(os.path.normpath(os.path.join(p, "data", "theme_strength_daily.parquet")))),
            os.path.normpath(_ROOT_CANDS[0]))
DATA = os.path.join(ROOT, "data")
_POOL_CANDS = [
    os.path.join(ROOT, ".dsh-tmp", "buyside", "prod_data", "stock_pool.db"),
    os.path.join(ROOT, "data", "stock_pool.db"),
    "/app/data/stock_pool.db",
]
POOL = next((p for p in _POOL_CANDS if os.path.exists(p)), _POOL_CANDS[0])
_C = {}


def _bars():
    if "bars" not in _C:
        old = pd.DataFrame()
        op = os.path.join(ROOT, ".dsh-tmp", "buyside", "bars.parquet")
        if os.path.exists(op):
            try:
                old = pd.read_parquet(op, columns=["ts_code", "trade_date", "high", "low", "close", "amount"])
            except Exception:
                old = pd.DataFrame()
        new = pd.read_parquet(os.path.join(DATA, "bars_combined.parquet"),
                              columns=["ts_code", "trade_date", "close", "amount"])
        if len(old):
            df = pd.concat([old, new], ignore_index=True).drop_duplicates(["ts_code", "trade_date"], keep="first")
        else:
            df = new.copy()
        for c in ("high", "low"):
            if c not in df: df[c] = df["close"]
        df["high"] = df["high"].fillna(df["close"]); df["low"] = df["low"].fillna(df["close"])
        _C["bars"] = df
    return _C["bars"]


def _strength():
    if "st" not in _C:
        _C["st"] = pd.read_parquet(os.path.join(DATA, "theme_strength_daily.parquet"))
    return _C["st"]


def _breadth():
    if "br" not in _C:
        _C["br"] = pd.read_parquet(os.path.join(DATA, "theme_breadth_daily.parquet"))
    return _C["br"]


def _fund():
    if "fund" not in _C:
        try:
            rows = json.load(open(os.path.join(DATA, "fund_holdings", "aggregate.json"), encoding="utf-8"))
            _C["fund"] = {str(r["symbol"]): r for r in rows}
        except Exception:
            _C["fund"] = {}
    return _C["fund"]


def _names():
    if "names" not in _C:
        _C["names"] = {}
        try:
            con = sqlite3.connect(POOL)
            for code, name in con.execute("select ts_code,name from stock_pool"):
                _C["names"][str(code)] = str(name)
        except Exception:
            pass
    return _C["names"]


_IND_THEME = {
    "互联网": "AI/算力/科技", "软件服务": "AI/算力/科技", "IT设备": "AI/算力/科技",
    "通信设备": "AI/算力/科技", "电信运营": "AI/算力/科技",
    "半导体": "半导体/芯片", "元器件": "半导体/芯片",
    "专用机械": "机器人/智能制造", "电气设备": "机器人/智能制造",
    "汽车整车": "汽车/智驾", "汽车配件": "汽车/智驾",
    "小金属": "资源/周期", "铜": "资源/周期", "铝": "资源/周期", "黄金": "资源/周期",
    "化工原料": "资源/周期", "农药化肥": "资源/周期",
    "医疗保健": "医药", "化学制药": "医药", "中成药": "医药", "生物制药": "医药",
    "火力发电": "电力/公用", "水力发电": "电力/公用", "新型电力": "电力/公用",
    "银行": "金融", "证券": "金融", "保险": "金融",
    "建筑施工": "稳增长/基建", "港口": "稳增长/基建", "房地产": "稳增长/基建",
    "食品": "消费/内需", "白酒": "消费/内需", "家用电器": "消费/内需",
    "影视音像": "传媒/游戏", "互联网服务": "传媒/游戏",
}


def _industry_rows():
    if "indrows" not in _C:
        rows = []
        try:
            con = sqlite3.connect(POOL)
            rows = list(con.execute("select ts_code,name,industry from stock_pool"))
        except Exception:
            pass
        _C["indrows"] = rows
    return _C["indrows"]


def _tm():
    if "tm" not in _C:
        try:
            from eval_select_v0 import theme_map
            _C["tm"] = theme_map()
        except Exception:
            _C["tm"] = {}
    return _C["tm"]


def _chain(date8: str):
    files = sorted(glob.glob(os.path.join(DATA, "chain_map_20*.json")))
    for f in reversed(files):
        if "_v2_cmp" in f:
            continue
        d = os.path.basename(f)[10:18]
        if d <= date8:
            try: return json.load(open(f, encoding="utf-8"))
            except Exception: return None
    return None


def _fallback_theme_stage(subtheme: str, date8: str) -> Dict[str, Any]:
    """主题统计缺失时，用子主题成分按同一口径现算。"""
    key = ("fb", subtheme, date8)
    if key in _C:
        return _C[key]
    try:
        try:
            from .subthemes import stocks as _sub_stocks
        except ImportError:
            from subthemes import _sub_stocks  # type: ignore
        codes = [c for c in _sub_stocks(subtheme)]
        df = _bars()
        x = df[(df.ts_code.isin(codes)) & (df.trade_date <= date8)]
        if not len(x):
            _C[key] = {}
            return {}
        piv = x.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index().tail(60)
        if len(piv) < 21:
            _C[key] = {}
            return {}
        last = piv.iloc[-1]
        r5 = float((last / piv.iloc[-6] - 1).mean())
        r20 = float((last / piv.iloc[-21] - 1).mean())
        ma20 = piv.rolling(20).mean()
        b20 = float((last > ma20.iloc[-1]).mean())
        b5 = float((piv.iloc[-6] > ma20.iloc[-6]).mean()) if len(piv) >= 26 else b20
        out = {"theme": subtheme, "date": date8, "r20": r20, "r5": r5,
               "breadth20": b20, "breadth_chg5": b20 - b5, "source": "subtheme_fallback"}
        _C[key] = out
        return out
    except Exception:
        _C[key] = {}
        return {}


def theme_stage(theme: str, date8: str, subtheme: str | None = None) -> Dict[str, Any]:
    s = _strength(); b = _breadth()
    out = {"theme": theme, "date": date8, "r20": None, "r5": None, "breadth20": None,
           "breadth_chg5": None, "stage": "unknown"}
    if not s.empty:
        x = s[(s.theme == theme) & (s.trade_date <= date8)].sort_values("trade_date").tail(1)
        if len(x):
            r = x.iloc[0]; out.update({"r20": float(r.r20), "r5": float(r.r5), "share5": float(r.share5)})
    if not b.empty:
        x = b[(b.theme == theme) & (b.trade_date <= date8)].sort_values("trade_date").tail(1)
        if len(x):
            r = x.iloc[0]; out.update({"breadth20": float(r.breadth20),
                                       "breadth_chg5": float(r.breadth_chg5) if pd.notna(r.breadth_chg5) else None})
    if out["r20"] is None and subtheme:
        fb = _fallback_theme_stage(subtheme, date8)
        if fb:
            out.update({k: fb[k] for k in ("r20", "r5", "breadth20", "breadth_chg5", "source") if k in fb})
    if out["r20"] is None:
        return out
    r20, r5, b20, bchg = out["r20"], out["r5"] or 0, out["breadth20"] or 0, out["breadth_chg5"] or 0
    if r20 < -0.08 and b20 < 0.2:
        out["stage"] = "oversold"
    elif r20 < 0 and b20 < 0.3 and abs(r5) < 0.03:
        out["stage"] = "low_flat"
    elif r20 < 0 and r5 < 0:
        out["stage"] = "downtrend"
    elif r20 < 0 and r5 > 0:
        out["stage"] = "weak"
    elif r20 > 0.08 and (r5 < 0 or bchg < -0.05):
        out["stage"] = "extended"
    else:
        out["stage"] = "strong"
    return out


def _theme_candidates(theme: str, date8: str, subtheme: str | None = None) -> List[dict]:
    cm = _chain(date8)
    out = []
    sub_codes = None
    if subtheme:
        try:
            from .subthemes import stocks as _sub_stocks
        except ImportError:
            from subthemes import stocks as _sub_stocks
        sub_codes = _sub_stocks(subtheme)
    if cm:
        for t in cm.get("themes", []):
            if t.get("theme") != theme:
                continue
            for seg in t.get("segments", []):
                leaders = seg.get("leading_verified") or []
                for i, x in enumerate(leaders, 1):
                    if sub_codes is not None and x.get("ts") not in sub_codes:
                        continue
                    out.append({"code": x.get("ts"), "name": x.get("name"), "rank": i, "role": "leader",
                                "segment": seg.get("label"), "source": "chain_map"})
                lead = {x.get("ts") for x in leaders}
                for x in seg.get("verified_all") or []:
                    if x.get("ts") in lead: continue
                    if sub_codes is not None and x.get("ts") not in sub_codes:
                        continue
                    out.append({"code": x.get("ts"), "name": x.get("name"), "rank": None, "role": "second",
                                "segment": seg.get("label"), "source": "chain_map"})
    # 补齐主题全成分：chain_map 只覆盖市值/主营核实的环节龙头，
    # 他的低位/辨识度票（如蜂助手）不在里面，必须从 theme_map/子主题补 other 域。
    have = {x["code"] for x in out}
    if sub_codes is not None:
        for c in sorted(sub_codes):
            if c not in have:
                out.append({"code": c, "name": _names().get(c, c), "rank": None, "role": "other",
                            "segment": None, "source": "subtheme"})
                have.add(c)
        seen, uniq = set(), []
        for x in out:
            if x["code"] and x["code"] not in seen:
                seen.add(x["code"]); uniq.append(x)
        return uniq
    for c in _tm().get(theme, []):
        if c and c not in have:
            out.append({"code": c, "name": _names().get(c, c), "rank": None, "role": "other",
                        "segment": None, "source": "theme_map"})
            have.add(c)
    # 行业兜底：概念表未覆盖的票（如蜂助手=互联网）也纳入 other 域
    for code, name, ind in _industry_rows():
        if code in have:
            continue
        if _IND_THEME.get(str(ind)) == theme:
            out.append({"code": code, "name": name, "rank": None, "role": "other",
                        "segment": None, "source": "industry_map"})
            have.add(code)
    # dedupe
    seen, uniq = set(), []
    for x in out:
        if x["code"] and x["code"] not in seen:
            seen.add(x["code"]); uniq.append(x)
    return uniq


def _features_many(codes: List[str], theme: str, date8: str, stage: dict) -> Dict[str, dict]:
    df = _bars()
    sub = df[(df.ts_code.isin(codes)) & (df.trade_date <= date8)].sort_values(["ts_code", "trade_date"])
    if sub.empty:
        return {}
    sub = sub.groupby("ts_code").tail(80)
    out = {}
    for code, d in sub.groupby("ts_code"):
        out[code] = _features_from_group(d, theme, stage)
    return out


def _features_from_group(d, theme: str, stage: dict) -> Dict[str, Any]:
    if len(d) < 5:
        return {}
    close = d.close.astype(float).values
    high = d.high.astype(float).values
    low = d.low.astype(float).values
    amt = d.amount.astype(float).values
    code = str(d.iloc[-1].ts_code)
    ret20 = close[-1] / close[-21] - 1 if len(close) >= 21 else None
    ret5 = close[-1] / close[-6] - 1 if len(close) >= 6 else None
    pct_chg = close[-1] / close[-2] - 1 if len(close) >= 2 and close[-2] else None
    r60 = close[-1] / close[-61] - 1 if len(close) >= 61 else None
    low20 = float(low[-20:].min()) if len(low) >= 20 else None
    dist_low20 = close[-1] / low20 - 1 if low20 else None
    fresh_accel = (pct_chg - ret5 / 5) if pct_chg is not None and ret5 is not None else None
    high20 = float(high[-20:].max()) if len(high) >= 20 else None
    new_high20 = bool(high20 and close[-1] >= high20 * 0.999)
    window = min(20, len(d))
    cw, hw, lw = close[-window:], high[-window:], low[-window:]
    amp = (hw - lw) / cw
    lo, hi = float(cw.min()), float(cw.max())
    flat = int(sum(1 for a, c in zip(amp, cw) if pd.notna(a) and a < 0.02 and c <= lo + 0.5 * (hi - lo)))
    vol_active5 = 0
    if len(amt) >= 15:
        base = amt[-15:-5].mean()
        vol_active5 = int(sum(1 for v in amt[-5:] if base > 0 and v > base))
    name = _names().get(code, "")
    keywords = [k for k in theme.replace("/", " ").split() if len(k) >= 1]
    name_hit = any(k in name for k in keywords)
    f = _fund().get(code) or {}
    rel20 = (ret20 - stage["r20"]) if ret20 is not None and stage.get("r20") is not None else None
    return {"ret20": ret20, "ret5": ret5, "r60": r60, "pct_chg": pct_chg, "new_high20": new_high20,
            "dist_low20": dist_low20, "fresh_accel": fresh_accel,
            "rel20": rel20, "flat_low_days": flat,
            "vol_active5": vol_active5, "hist": len(d), "name_hit": name_hit,
            "fund_n": f.get("n_funds"), "fund_float": f.get("sum_float_ratio")}


def select_for_theme(theme: str, date8: str, top: int = 5, line: str = "core",
                     subtheme: str | None = None) -> Dict[str, Any]:
    if subtheme:
        try:
            from .subthemes import parent as _sub_parent
        except ImportError:
            from subthemes import parent as _sub_parent
        theme = _sub_parent(subtheme) or theme
    st = theme_stage(theme, date8, subtheme=subtheme)
    cands = _theme_candidates(theme, date8, subtheme=subtheme)
    feat = _features_many([x["code"] for x in cands], theme, date8, st)
    rows = [{**x, **feat[x["code"]]} for x in cands if x["code"] in feat and feat[x["code"]]]
    # 全链路卫生过滤：北交所/ST 不进选股
    rows = [r for r in rows if not str(r.get("code", "")).endswith(".BJ")
            and "ST" not in str(r.get("name", "")).upper()]
    if not rows:
        return {"theme": theme, "subtheme": subtheme, "stage": st, "line": line, "picks": [], "watch": []}
    stage = st["stage"]
    chain_rows = [r for r in rows if r.get("source") == "chain_map"]
    theme_rows = [r for r in rows if r.get("source") != "industry_map"] or rows
    if line == "core":
        # 核心线：只做链主/二供，弱市不新开核心
        if stage in ("weak", "downtrend", "low_flat", "oversold"):
            return {"theme": theme, "subtheme": subtheme, "stage": st, "line": line,
                    "rule": "core→弱市不新开", "picks": [], "watch": []}
        pool = chain_rows or rows
        pool = [r for r in pool if r.get("rank") == 1 or r.get("role") == "second"]
        pool.sort(key=lambda r: (r.get("rank") or 99, -(r.get("rel20") or -9)))
        picks = pool[:top]
        for p in picks:
            p["rule"] = "core→链主/二供"; p["stage"] = stage; p["line"] = line
        return {"theme": theme, "subtheme": subtheme, "stage": st, "line": line, "rule": "core→链主/二供",
                "picks": picks, "watch": []}
    if line == "T":
        pool = chain_rows or rows
        pool = [r for r in pool if (r.get("vol_active5") or 0) >= 3]
        pool.sort(key=lambda r: (-(r.get("vol_active5") or 0), -(r.get("rel20") or -9)))
        picks = pool[:top]
        for p in picks:
            p["rule"] = "T→量能/弹性"; p["stage"] = stage; p["line"] = line
        return {"theme": theme, "subtheme": subtheme, "stage": st, "line": line, "rule": "T→量能/弹性",
                "picks": picks, "watch": []}
    if stage in ("strong", "extended"):
        pool = [r for r in theme_rows if r.get("rank") in (2, 3, 4) or r.get("role") == "second" or r.get("source") == "theme_map"]
        pool = pool or [r for r in rows if r.get("rank") != 1]
        sort_key = lambda r: (r.get("rank") or 99, -(r.get("rel20") or -9))
        rule = "strong→二供/二线(rank2-4)"
    elif stage == "weak":
        pool = theme_rows
        sort_key = lambda r: (-(r.get("rel20") or -9), r.get("rank") or 99)
        rule = "weak→最抗跌/最强"
    elif stage == "downtrend":
        pool = [r for r in theme_rows if r.get("rank") == 1] or theme_rows
        sort_key = lambda r: (-(r.get("rel20") or -9), r.get("rank") or 99)
        rule = "downtrend→只做龙头"
    elif stage in ("low_flat", "oversold"):
        pool = theme_rows
        sort_key = lambda r: (-r.get("flat_low_days", 0), -(r.get("rel20") or -9))
        rule = "low_flat→横盘多时"
    else:
        pool = [r for r in rows if r.get("rank") == 1] or rows
        sort_key = lambda r: (r.get("rank") or 99, -(r.get("rel20") or -9))
        rule = "default→rank1"
    pool.sort(key=sort_key)
    picks = pool[:top]
    for p in picks:
        p["rule"] = rule
        p["stage"] = stage
        p["line"] = line
    # 个股先动 watch：chain_map 之外的 other 域，按量能活跃 + 相对强度 + 名字辨识度
    picked = {p["code"] for p in picks}
    other = [r for r in rows if r.get("source") != "chain_map" and r["code"] not in picked]
    other.sort(key=lambda r: (-(r.get("vol_active5") or 0), -(r.get("rel20") or -9), 0 if r.get("name_hit") else 1))
    watch = other[:5]
    for p in watch:
        p["rule"] = "individual-first watch"
        p["stage"] = stage
    # rebound_watch：低位轮动的盘口候选，不要求 chain，不要求 vol=5
    reb = [r for r in rows if (r.get("vol_active5") or 0) >= 4
           and 0.03 <= (r.get("pct_chg") or -9) <= 0.09
           and not r.get("new_high20")
           and (r.get("rel20") or -9) > 0]
    reb.sort(key=lambda r: (-(r.get("pct_chg") or 0), -(r.get("rel20") or -9), -(r.get("vol_active5") or 0)))
    rebound = reb[:5]
    excluded_mb = []
    kept = []
    try:
        try:
            from .main_business import assess as _mb
        except ImportError:
            from main_business import assess as _mb
        for p in rebound:
            a = _mb(p["code"], subtheme or theme)
            p["main_business"] = a
            p["rule"] = "rebound_watch"
            p["stage"] = stage
            if a.get("keep") is False:
                excluded_mb.append(p)
            else:
                kept.append(p)
    except Exception:
        kept = rebound
    rebound = kept
    return {"theme": theme, "subtheme": subtheme, "stage": st, "line": line, "rule": rule,
            "picks": picks, "watch": watch, "rebound_watch": rebound,
            "excluded_main_business": excluded_mb}


def individual_first(date8: str, top: int = 20) -> List[dict]:
    """个股先动 watch：全市场扫量能活跃 + 相对市场强度，不假设主题。"""
    df = _bars()
    codes = sorted(set(df[df.trade_date <= date8].ts_code.unique()))
    feat = _features_many(codes, "", date8, {"r20": None})
    rows = []
    for c, f in feat.items():
        if not f or (f.get("hist") or 0) < 30:
            continue
        rows.append({"code": c, "name": _names().get(c, c), **f})
    if not rows:
        return []
    market = sorted([r["ret20"] for r in rows if r.get("ret20") is not None])
    mkt = market[len(market) // 2] if market else 0.0
    for r in rows:
        r["rel_mkt"] = (r.get("ret20") or 0) - mkt
    pool = [r for r in rows if (r.get("vol_active5") or 0) >= 4 and (r.get("rel_mkt") or -9) > 0]
    pool.sort(key=lambda r: (-(r.get("vol_active5") or 0), -(r.get("rel_mkt") or -9), -(r.get("hist") or 0)))
    for r in pool[:top]:
        r["rule"] = "individual-first"
    return pool[:top]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--top", type=int, default=5)
    a = ap.parse_args()
    print(json.dumps(select_for_theme(a.theme, a.date, a.top), ensure_ascii=False, indent=1))


# ── core 软 overlay（不硬删，只给仓位/排序信号）──────────────────────────
_OVERLAY = {
    "alive": 1.0,
    "rotated": 0.8,
    "warning": 0.5,
    "stale": 0.5,
    "dead": 0.25,
    "unknown": 0.5,
}


def apply_bellwether_gate(picks: list[dict], ctx: dict) -> list[dict]:
    """风向标动作闸：不是仓位乘数，warning/dead 直接限制新买。"""
    state = str((ctx or {}).get("state") or "unknown")
    perms = (ctx or {}).get("permissions") or {}
    out = []
    for p in picks:
        role = str(p.get("role") or "")
        allow = True
        reason = ""
        if state == "dead":
            allow = False; reason = "dead: 禁止该主题新买"
        elif state in ("warning", "stale", "unknown") and role == "second":
            allow = False; reason = f"{state}: 禁止新做 second/补涨"
        p["bellwether_state"] = state
        p["bellwether_code"] = (ctx or {}).get("bellwether_code")
        p["bellwether_name"] = (ctx or {}).get("bellwether_name")
        p["bellwether_permissions"] = perms
        p["bellwether_gate"] = "allow" if allow else "block"
        p["bellwether_gate_reason"] = reason
        if allow: out.append(p)
    return out


def apply_bellwether_overlay(picks: list[dict], ctx: dict) -> list[dict]:
    """旧接口保留兼容：现在返回 action gate，不再产生仓位乘数。"""
    return apply_bellwether_gate(picks, ctx)


def select_core(theme: str, date8: str, top: int = 5, subtheme: str | None = None) -> Dict[str, Any]:
    """core 软 overlay 版本：选股仍走 stage×role；风向标只改仓位/标注。"""
    r = select_for_theme(theme, date8, top=top, line="core", subtheme=subtheme)
    try:
        try:
            from .bellwether_context import for_subtheme
        except ImportError:
            from bellwether_context import for_subtheme
        ctx = for_subtheme(subtheme or theme, date=date8)
    except Exception:
        ctx = {"subtheme": subtheme or theme, "state": "unknown", "permissions": {}}
    r["bellwether"] = ctx
    r["picks"] = apply_bellwether_gate(r.get("picks") or [], ctx)
    return r
