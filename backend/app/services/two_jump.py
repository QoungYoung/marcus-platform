# -*- coding: utf-8 -*-
"""two_jump.py — 二段跳（分钟级，直接口径，不用日线代理）。

他 2026-11-04 13:06：「趋势方面**只有半导体**达成今天的二段跳」。
13:06 那一刻，半导体确实已经完成「指数针底之后首次创新高」，而存储/CPO/PCB 还没到。

定义（可计算）：
    t0           = 指数当日最低点的分钟
    first_high_i = 方向在 t0 之后首次 price > (t0 之前当日最高) 的分钟
    jump_rank    = 按 first_high_i 排序；第 1 名就是二段跳
    broke_prev   = 指数低点是否跌破前一交易日低点（11-04 是 False —— 不出新低本身是信号）

数据：
    · 当天（live）：腾讯分时（web.ifzq.gtimg.cn，200ms 级，免费）
    · 历史（回放）：Tushare stk_mins（promax；限频/偶发 503，失败 fail-open）
    · 前低：PG index_daily

纪律：只读；任何异常返回 {} / ""（fail-open）；不参与门禁、不下单。
"""
from __future__ import annotations

import datetime as _dt
import json
import urllib.request
from typing import Dict, List, Optional, Tuple

NL = chr(10)
INDEX = "000001.SH"
DIRS = [("半导体", "512480.SH"), ("CPO", "300308.SZ"), ("存储", "603986.SH"), ("PCB", "002384.SZ")]


def _tx_code(code: str) -> str:
    return ("sh" if code.endswith(".SH") else "sz") + code.split(".")[0]


def _tx_series(code: str) -> List[Tuple[str, float]]:
    """腾讯分时：[[hhmm, price, cumvol, amount], ...] → [(hhmm, price)]。"""
    c = _tx_code(code)
    raw = urllib.request.urlopen(urllib.request.Request(
        f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={c}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}),
        timeout=10).read().decode("utf-8", "ignore")
    rows = json.loads(raw)["data"][c]["data"]["data"]
    out = []
    for r in rows:
        p = r.split()
        if len(p) >= 2:
            out.append((p[0], float(p[1])))
    return out


def _hist_series(code: str, day: str) -> List[Tuple[str, float]]:
    """Tushare 分钟线（历史回放）。09:30 集合竞价剔除，与腾讯口径一致。"""
    from core.tushare_relay import relay_query
    d = relay_query("stk_mins", ts_code=code, freq="1min",
                    start_date="%s 09:30:00" % day, end_date="%s 15:00:00" % day)
    d = d.sort_values("trade_time")
    out = []
    for _, r in d.iterrows():
        hm = str(r["trade_time"])[11:16]
        if hm == "09:30":
            continue
        out.append((hm, float(r["close"])))
    return out


def _prev_low(day: str) -> Optional[float]:
    try:
        import psycopg2
        from core import dsn
        with psycopg2.connect(dsn.main(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("select low from index_daily where ts_code=%s and trade_date < %s "
                            "order by trade_date desc limit 1",
                            (INDEX, "%s-%s-%s" % (day[:4], day[4:6], day[6:8])))
                r = cur.fetchone()
        return float(r[0]) if r else None
    except Exception:
        return None


def compute(day: Optional[str] = None) -> Dict:
    """→ {day, t0, index_low, prev_low, broke_prev, dirs:[...], winner}；失败返回 {}。"""
    today = _dt.date.today().strftime("%Y%m%d")
    day = (day or today).replace("-", "")[:8]
    live = (day == today)
    try:
        idx = _tx_series(INDEX) if live else _hist_series(INDEX, day)
        if len(idx) < 30:
            return {}
        i0 = min(range(len(idx)), key=lambda i: idx[i][1])
        t0_hm, idx_low = idx[i0]
        prev_low = _prev_low(day)
        dirs = []
        for name, code in DIRS:
            try:
                s = _tx_series(code) if live else _hist_series(code, day)
            except Exception:
                dirs.append({"name": name, "code": code, "high_time": None, "note": "取数失败"})
                continue
            pmap = dict(s)
            if t0_hm not in pmap or len(s) < 30:
                dirs.append({"name": name, "code": code, "high_time": None, "note": "无对齐分时"})
                continue
            pre = [p for hm, p in s if hm < t0_hm]
            post = [(hm, p) for hm, p in s if hm > t0_hm]
            if not pre:
                dirs.append({"name": name, "code": code, "high_time": None, "note": "低点前无样本"})
                continue
            if len(post) < 5:
                dirs.append({"name": name, "code": code, "high_time": None,
                             "note": "低点后样本不足（指数仍在创新低/刚转向）"})
                continue
            pre_high = max(pre)
            hit = next(((hm, p) for hm, p in post if p > pre_high), None)
            dirs.append({"name": name, "code": code,
                         "pre_high": round(pre_high, 3),
                         "high_time": hit[0] if hit else None,
                         "high_px": round(hit[1], 3) if hit else None,
                         "note": "" if hit else "未新高"})
        ranked = sorted([d for d in dirs if d.get("high_time")], key=lambda x: x["high_time"])
        for i, d in enumerate(ranked, 1):
            d["rank"] = i
        return {
            "day": day, "live": live, "t0": t0_hm, "index_low": round(idx_low, 3),
            "prev_low": round(prev_low, 3) if prev_low is not None else None,
            "broke_prev": (idx_low < prev_low) if prev_low is not None else None,
            "dirs": dirs, "winner": (ranked[0]["name"] if ranked else None),
            "as_of": idx[-1][0], "bars": len(idx),
            "index_still_low": (i0 == len(idx) - 1),
        }
    except Exception:
        return {}


def context(day: Optional[str] = None) -> str:
    r = compute(day)
    if not r:
        return ""
    broke = ("破前低" if r.get("broke_prev") else "未破前低") if r.get("broke_prev") is not None else "前低未知"
    lines = ["## 二段跳（分钟级，as_of %s %s）" % (r["day"], r["as_of"]),
             "- 指数低点 %s（%.3f，%s%s）%s" % (
                 r["t0"], r["index_low"], broke,
                 ("，前低 %.3f" % r["prev_low"]) if r.get("prev_low") is not None else "",
                 "｜**低点=当前，仍在创新低**" if r.get("index_still_low") else "")]
    for d in r["dirs"]:
        if d.get("high_time"):
            lines.append("- %s %s：首新高 %s（rank %d）" % (d["name"], d["code"], d["high_time"], d.get("rank") or 99))
        else:
            lines.append("- %s %s：%s" % (d["name"], d["code"], d.get("note") or "未新高"))
    lines.append("- 判定：二段跳=%s" % (r.get("winner") or "暂无（等待方向先创新高）"))
    return NL.join(lines) + NL + NL
