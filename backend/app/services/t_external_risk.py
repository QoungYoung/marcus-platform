# -*- coding: utf-8 -*-
"""外部风险感知：美股（纳指/标普/费半）+ 美债10Y + 全球宏观(global_macro)。

供 mom_etf 门控 / AI 做T 快照 / agent get_us_market 工具使用。全部免费、只读、降级安全：
- 美股指数/费半：腾讯 qt (usIXIC/usINX/usDJI/usSOXX)，pct 用 current/pre_close 自算
  （美股行情 pct 字段索引与 A 股不同，不能直接信字段值）。
- 美债10Y：FRED fredgraph.csv?id=DGS10（免 key）。
- 全球宏观：复用 golden_pit global_macro（sentiment_score / global_macro_coefficient /
  liquidity_gate / global_trend / summary），service 内已有 TTL 缓存。
任何源失败 → 返回可用默认值/空，us_risk 按"部分信号"保守判定，不抛异常。
"""
from __future__ import annotations

import time
import urllib.request
from typing import Any, Dict, Optional

from app.services.t_data_sources import fetch_tencent_quote

US_SYMBOLS = {
    "nasdaq": "usIXIC",
    "sp500": "usINX",
    "dow": "usDJI",
    "sox": "usSOXX",
}

FRED_DGS10_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"

SOX_DROP_THRESHOLD = -1.0
SENTIMENT_LOW = 40
COEF_LOW = 0.9

_GM_CACHE: Dict[str, Any] = {"ts": 0.0, "data": {}}
_GM_TTL = 300.0
_ER_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_ER_TTL = 300.0


def external_risk_snapshot() -> Dict[str, Any]:
    """带 TTL 缓存的外部风险快照（mom_etf / t_monitor 高频调用用，5 分钟内不重复拉网络）。"""
    now = time.time()
    if _ER_CACHE["data"] is not None and (now - _ER_CACHE["ts"]) < _ER_TTL:
        return _ER_CACHE["data"]
    d = compute_external_risk()
    _ER_CACHE["ts"] = now
    _ER_CACHE["data"] = d
    return d


def _fetch_us_market_tencent() -> Dict[str, Dict[str, Any]]:
    q = fetch_tencent_quote(list(US_SYMBOLS.values()), timeout=8)
    out: Dict[str, Dict[str, Any]] = {}
    for key, code in US_SYMBOLS.items():
        d = q.get(code)
        if d:
            cur = float(d.get("current") or 0)
            pre = float(d.get("pre_close") or 0)
            out[key] = {"code": code, "price": round(cur, 3),
                        "pre_close": round(pre, 3),
                        "pct": round((cur - pre) / pre * 100, 2) if pre else 0.0}
    return out


def _fetch_us10y_fred(timeout: int = 12) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(FRED_DGS10_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
        rows = []
        for ln in lines:
            if not ln or ln.startswith("date,") or ln.startswith("observation"):
                continue
            parts = ln.split(",")
            if len(parts) >= 2 and parts[0][:4].isdigit():
                try:
                    v = float(parts[1]) if parts[1] not in ("", ".") else None
                    rows.append((parts[0], v))
                except ValueError:
                    continue
        if len(rows) < 2:
            return None
        last_date, last_y = rows[-1]
        prev_y = rows[-2][1]
        return {"date": last_date, "yield": last_y, "prev_yield": prev_y,
                "chg": round(last_y - prev_y, 3) if last_y is not None and prev_y is not None else None}
    except Exception:
        return None


def get_global_macro() -> Dict[str, Any]:
    now = time.time()
    if _GM_CACHE["data"] and (now - _GM_CACHE["ts"]) < _GM_TTL:
        return _GM_CACHE["data"]
    try:
        from app.services.golden_pit_service import get_golden_pit_service
        status = get_golden_pit_service().get_status()
        gm = status.get("global_macro") or {}
    except Exception:
        gm = {}
    _GM_CACHE["ts"] = now
    _GM_CACHE["data"] = gm
    return gm


def compute_external_risk() -> Dict[str, Any]:
    us = _fetch_us_market_tencent()
    us10y = _fetch_us10y_fred()
    gm = get_global_macro()

    sox_pct = us.get("sox", {}).get("pct", 0.0)
    nasdaq_pct = us.get("nasdaq", {}).get("pct", 0.0)
    tech_pct = min(sox_pct, nasdaq_pct) if (sox_pct or nasdaq_pct) else 0.0

    reasons = []
    risk = False
    if tech_pct < SOX_DROP_THRESHOLD and tech_pct != 0.0:
        risk = True
        reasons.append("费半/纳指隔夜 %.2f%% < %s%%" % (tech_pct, SOX_DROP_THRESHOLD))
    if us10y and us10y.get("chg") is not None and us10y["chg"] > 0.05:
        risk = True
        reasons.append("美债10Y上行 +%.3f%%（%s%%）" % (us10y["chg"], us10y.get("yield")))
    if gm.get("liquidity_gate") == "close":
        risk = True
        reasons.append("全球流动性闸门关闭(global_macro)")
    if gm.get("sentiment_score") is not None and gm.get("sentiment_score") < SENTIMENT_LOW:
        risk = True
        reasons.append("全球情绪 %s < %s" % (gm.get("sentiment_score"), SENTIMENT_LOW))
    if gm.get("global_macro_coefficient") is not None and gm.get("global_macro_coefficient") < COEF_LOW:
        risk = True
        reasons.append("全球宏观系数 %s < %s" % (gm.get("global_macro_coefficient"), COEF_LOW))

    return {
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "us_market": us,
        "us10y": us10y,
        "global_macro": gm,
        "us_risk": risk,
        "us_risk_reason": "；".join(reasons) if reasons else "外部风险正常",
        "us_risk_score": len(reasons),
    }


def compute_market_judgment(as_of: Optional[str] = None) -> Dict[str, Any]:
    """改进版盘前市场判断（替换老投票式"震荡/趋势"诊断）。

    用已验证的框架：月度regime（趋势向上/震荡/趋势向下）+ 沪深300 vs MA20 + 外部风险。
    仅趋势向上月才可开新仓/满仓；震荡/向下月只做T不新开；外部风险升（SOX/纳指）→ 降科技仓。
    as_of: YYYYMMDD（缺省今天）。返回 {date, regime, csi300, external, recommendation}。
    """
    import datetime as _dt
    import pandas as _pd

    asof = as_of or _dt.datetime.now().strftime("%Y%m%d")
    ad = _dt.datetime.strptime(asof, "%Y%m%d").date()

    def _cd(slope, above):
        if slope is None or slope != slope:
            return "震荡"
        if slope > 0.5 and above:
            return "趋势向上"
        if slope < -0.5 and not above:
            return "趋势向下"
        return "震荡"

    regime = "震荡"
    c300 = {"above_ma20": None, "ma20": None, "close": None, "slope": None}
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        _start = (_dt.date(ad.year, ad.month, 1) - _dt.timedelta(days=460)).strftime("%Y%m%d")
        dfa = pro.index_daily(ts_code="000300.SH", start_date=_start, end_date=asof)
        if dfa is not None and not dfa.empty:
            dfa = dfa.sort_values("trade_date").reset_index(drop=True)
            closes = [float(x) for x in dfa["close"]]
            dd = _pd.DataFrame({"d": [str(x)[:10] for x in dfa["trade_date"]], "close": closes})
            dd["ma20"] = dd["close"].rolling(20).mean()
            dd["ma20_10"] = dd["ma20"].shift(10)
            dd["slope"] = (dd["ma20"] / dd["ma20_10"] - 1) * 100
            dd["above"] = dd["close"] > dd["ma20"]
            dd["tag"] = [_cd(s, a) for s, a in zip(dd["slope"], dd["above"])]
            dd["ym"] = dd["d"].str[:7]
            last = dd.iloc[-1]
            c300 = {"close": float(last["close"]), "ma20": round(float(last["ma20"]), 2),
                    "above_ma20": bool(last["above"]), "slope": round(float(last["slope"]), 3)}
            month = dd["d"].iloc[-1][:7]
            sub = dd[dd["ym"] == month]
            if len(sub):
                vc = sub["tag"].value_counts(normalize=True)
                fl = vc.get("震荡", 0.0); up = vc.get("趋势向上", 0.0); dn = vc.get("趋势向下", 0.0)
                if fl >= 0.5:
                    regime = "震荡"
                elif up >= dn and up >= 0.4:
                    regime = "趋势向上"
                elif dn >= up and dn >= 0.4:
                    regime = "趋势向下"
                else:
                    regime = "震荡"
    except Exception:
        pass

    ext = external_risk_snapshot()
    us_risk = bool(ext.get("us_risk"))
    us_reason = str(ext.get("us_risk_reason") or "")

    if regime == "趋势向上" and c300.get("above_ma20") and not us_risk:
        st_rec = "短期层：趋势向上月+沪深300>MA20+外部平稳 → 可开新仓/趋势跟踪"
        stance = "green"
    elif regime == "趋势向上" and us_risk:
        st_rec = "短期层：趋势向上月但外部科技风险升（%s）→ 科技仓位降/暂停新开" % us_reason
        stance = "yellow"
    elif regime == "趋势向下":
        st_rec = "短期层：趋势向下月 → 轻仓/防守，只做T不新开，别硬扛"
        stance = "red"
    else:
        st_rec = "短期层：震荡月 → 只做T摊薄已有底仓，不新开短线仓，等趋势向上月"
        stance = "yellow"

    # 景气中线层（高通胀+高景气，楼主投研框架）：主线选股中线持有吃β+业绩兑现
    if regime == "趋势向下":
        lt_rec = "景气中线层：趋势向下月，对高通胀/高景气主线**更慢/更谨慎**分批建仓（资源/煤化工/炼化/氟化工，吃β+业绩）"
    elif regime == "震荡":
        lt_rec = "景气中线层：震荡月，按高通胀+高景气主线（上游资源/中游剪刀差/炼化一体化/氟化工）**中线建仓**，吃β+业绩兑现"
    else:
        lt_rec = "景气中线层：趋势向上月，高通胀/高景气主线可正常建仓（趋势加成）"

    return {
        "date": asof,
        "regime": regime,
        "csi300": c300,
        "external": {"us_risk": us_risk, "us_risk_reason": us_reason},
        "stance": stance,
        "recommendation": st_rec,
        "short_term": {"action": st_rec, "stance": stance},
        "long_term": {"action": lt_rec, "enabled": regime != "趋势向上" or regime != "趋势向上"},
    }


if __name__ == "__main__":
    import json
    print(json.dumps(compute_external_risk(), ensure_ascii=False, indent=2, default=str))
