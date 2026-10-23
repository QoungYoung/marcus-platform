#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B反 风向标（14:50 收盘前读数）—— 只观察，不写策略链、不接交易、不接 gate。

给 Pi 分析提供「事实底稿」：四维读数（级别 / 结构 / 谁在买 / 够不够得着）
+ 底部结构（W底/颈线/红三兵/不破位黑K）。判断交给 Pi，本脚本不下结论。

用法：
    python jobs/wolf_brebound_1450.py                  # 盘中 14:50 口径（默认）
    python jobs/wolf_brebound_1450.py --asof 20261022  # 历史日回放（用日线）
    python jobs/wolf_brebound_1450.py --dry-run        # 只打印，不落盘
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ETF = "588170.SH"
IDX = "000001.SH"
CUT = "14:50"
# 截至 14:50 的累计量占全天量的比例（24 天实测：均值 94.8%，P10 91.4% / P90 96.9%）
PRE_CUT_SHARE = 0.948


# ────────────────────────── 取数 ──────────────────────────
def _relay():
    from core.tushare_relay import relay_query
    return relay_query


def fetch_daily(code: str, end: str, n: int = 160) -> pd.DataFrame:
    api = "index_daily" if code.endswith(".SH") and code.startswith("0000") else "fund_daily"
    rq = _relay()
    d = rq(api, ts_code=code, start_date="20240101", end_date=end)
    d = d.sort_values("trade_date").reset_index(drop=True).tail(n).reset_index(drop=True)
    for c in ("open", "high", "low", "close", "pre_close", "vol", "amount"):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce").astype(float)
    return d


def adjust_splits(d: pd.DataFrame) -> pd.DataFrame:
    """份额折算：pre_close 与上一根收盘比值偏离 >2% 即折算，向前复权。"""
    d = d.copy()
    k = np.ones(len(d))
    for i in range(1, len(d)):
        pc, prev = d.pre_close.values[i], d.close.values[i - 1]
        if prev and pc and abs(pc / prev - 1) > 0.02:
            k[i] = pc / prev
    cum = np.cumprod(k[::-1])[::-1] / k
    for c in ("open", "high", "low", "close"):
        d[c] = d[c].values * cum
    d["vol"] = d["vol"].values / cum
    return d


def today_bars(code: str, day: str, cut: str = CUT) -> pd.DataFrame | None:
    """当日 09:30–cut 的 1 分钟（含）。"""
    try:
        rq = _relay()
        d = rq("stk_mins", ts_code=code, freq="1min",
               start_date=f"{day[:4]}-{day[4:6]}-{day[6:]} 09:30:00",
               end_date=f"{day[:4]}-{day[4:6]}-{day[6:]} {cut}:00")
        return d.sort_values("trade_time") if len(d) else None
    except Exception as e:
        print(f"[warn] {code} 分钟取数失败: {str(e)[:80]}", file=sys.stderr)
        return None




def tx_today(code: str, day: str) -> dict | None:
    """腾讯实时（qt.gtimg.cn）：盘中累计 OHLC + 量额。14:50 唯一可靠的来源。
    字段：[3]现价 [4]昨收 [5]今开 [6]量(手) [33]高 [34]低 [37]额(万元) [30]时间"""
    import urllib.request
    tx_code = ("sh" if code.endswith(".SH") else "sz") + code.split(".")[0]
    try:
        raw = urllib.request.urlopen(urllib.request.Request(
            "https://qt.gtimg.cn/q=" + tx_code,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}),
            timeout=12).read().decode("gbk", "ignore")
        f = raw.split("~")
        if len(f) < 40 or not str(f[30]).startswith(day):
            return None
        return dict(open=float(f[5]), close=float(f[3]), high=float(f[33]), low=float(f[34]),
                    vol=float(f[6]), amount=float(f[37]) * 10.0,   # 万元→千元
                    quote_time=str(f[30]))
    except Exception as e:
        print(f"[warn] 腾讯取数失败 {code}: {str(e)[:70]}", file=sys.stderr)
        return None

def em_today(secid: str, day: str) -> dict | None:
    """东财当日累计 K（盘中实时可用）；vol=手、amount=元 → 转千元。stk_mins 收盘后才发布，14:50 只能靠它。"""
    import urllib.request
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s&klt=101&fqt=1"
           "&end=20500101&lmt=4&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57" % secid)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        j = json.loads(urllib.request.urlopen(req, timeout=12).read())
        for k in (j.get("data") or {}).get("klines") or []:
            p = k.split(",")
            if p[0].replace("-", "") == day:
                return dict(open=float(p[1]), close=float(p[2]), high=float(p[3]), low=float(p[4]),
                            vol=float(p[5]), amount=float(p[6]) / 1000.0)
    except Exception as e:
        print(f"[warn] 东财取数失败 {secid}: {str(e)[:70]}", file=sys.stderr)
    return None

def cum_bar(b: pd.DataFrame) -> dict:
    """分钟数据口径：vol=股、amount=元 → 统一转成 手 / 千元，与日线一致。"""
    return dict(open=float(b.open.iloc[0]), high=float(b.high.max()), low=float(b.low.min()),
                close=float(b.close.iloc[-1]), vol=float(b.vol.sum()) / 100.0,
                amount=float(b.amount.sum()) / 1000.0, last=str(b.trade_time.iloc[-1]),
                quote_time=str(b.trade_time.iloc[-1]).replace("-", "").replace(":", "").replace(" ", "")[:12])


def wave_from_pg(asof: str) -> dict | None:
    """级别：**直连生产 PG 取 as-of 浪型**（表 wave_state，session_date <= asof 的最近一行）。
    PG 不可达时才退回 wave_store（文件兜底），并显式标注 wave_path。"""
    def _fmt(d, lv, sl, op, mode, stage, note, rung, legs, origin, w1, prev, prior, path,
             stale=None, ver=None, snap=None, cycle_state=None, rung_sub=None, sub_raw=None, trans=None):
        stale_b = None if stale is None else str(stale).lower() == "true"
        w = dict(wave_path=path, session_date=d, level=lv, sub_level=sl, operation=op,
                 mode=mode, stage=stage, rung=rung, label_note=note,
                 rung_sub_level=rung_sub, sub_level_raw=sub_raw, leg_transition=trans,
                 leg_cycle_state=cycle_state, legs_stale=stale_b,
                 generator_version=ver, data_snapshot=snap,
                 anchor=dict(origin=origin, wave1_top=w1, prev_peak=prev, prior_peak=prior),
                 sub_legs=legs[-4:])
        if stale_b:
            w["wave_note"] = ("腿陈旧（最后一条腿距快照过久，或确认顶之后已创新高）⇒ "
                              "sub_level 已不再外推，级别仅供参考，不要当作结构依据；"
                              "结构判断以下面的价格摆动/底部结构为准")
        return w

    iso = f"{asof[:4]}-{asof[4:6]}-{asof[6:]}"
    try:
        import psycopg2
        dsn = os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:18789/marcus_trading"
        with psycopg2.connect(dsn, connect_timeout=6) as conn:
            cur = conn.cursor()
            cur.execute("select session_date, level, sub_level, operation, mode, stage, "
                        "payload->>'label_note', payload->'rung'->>'name', payload->'sub_legs', "
                        "payload->'levels', payload->>'legs_stale', payload->>'generator_version', "
                        "payload->>'data_snapshot', payload->>'leg_cycle_state', "
                        "payload->>'rung_sub_level', payload->>'sub_level_raw', payload->>'leg_transition' "
                        "from wave_state where session_date <= %s order by session_date desc limit 1", (iso,))
            r = cur.fetchone()
        if r:
            import json as _j
            lv = r[9] or {}
            legs = _j.loads(r[8]) if isinstance(r[8], str) else (r[8] or [])
            return _fmt(str(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], r[7], legs,
                        lv.get("origin"), lv.get("wave1_top"), lv.get("prev_peak"), lv.get("prior_peak"),
                        "pg", stale=r[10], ver=r[11], snap=r[12], cycle_state=r[13],
                        rung_sub=r[14], sub_raw=r[15], trans=r[16])
    except Exception as e:
        print(f"[warn] 浪型直连 PG 失败，退回 wave_store: {str(e)[:70]}", file=sys.stderr)
    try:
        sys.path.insert(0, os.path.join(ROOT, "backend"))
        from app.services.wave_store import get_wave
        w = get_wave() or {}
        lv = w.get("levels") or {}
        return _fmt(w.get("session_date") or w.get("date"), w.get("level"), w.get("sub_level"),
                    w.get("operation"), w.get("mode"), w.get("stage"), w.get("label_note"),
                    (w.get("rung") or {}).get("name"), w.get("sub_legs") or [],
                    lv.get("origin"), lv.get("wave1_top"), lv.get("prev_peak"), lv.get("prior_peak"), "file")
    except Exception as e:
        print(f"[warn] 浪型兜底也失败: {str(e)[:70]}", file=sys.stderr)
        return None


# ────────────────────────── 底部结构 ──────────────────────────
def detect_bottom(h, l, c, o, v, j, tol=0.03, gap=(5, 60), reb=(0.03, 0.20), fresh=40):
    """W底/双底 + 颈线 + 红三兵 + 不破位黑K。参数按教科书定义固定，不优化。"""
    if j < 45:
        return dict(state="数据不足")
    piv = [i for i in range(5, j - 4) if l[i] == min(l[i - 5:i + 6])]
    best = None
    for a in range(len(piv) - 1):
        for b in range(a + 1, len(piv)):
            i1, i2 = piv[a], piv[b]
            if not (gap[0] <= i2 - i1 <= gap[1]) or j - i2 > fresh:
                continue
            if abs(l[i2] / l[i1] - 1) > tol:
                continue
            mid = int(np.argmax(h[i1:i2 + 1])) + i1
            if mid in (i1, i2):
                continue
            neck = h[mid]
            r = neck / min(l[i1], l[i2]) - 1
            if not (reb[0] <= r <= reb[1]):
                continue
            if c[i2:j + 1].min() < min(l[i1], l[i2]) * 0.97:
                continue
            cand = dict(l1=float(l[i1]), l2=float(l[i2]), i1=i1, i2=i2, neck=float(neck), mid=mid, reb=round(r * 100, 1))
            if best is None or i2 > best["i2"]:
                best = cand
    if best is None:
        return dict(state="未成立")
    red3_idx = None
    for k in range(max(3, best["i2"] - 20), j + 1):
        if all(c[m] > o[m] for m in (k - 2, k - 1, k)) and c[k] > c[k - 1] > c[k - 2] \
                and (h[k - 2] - c[k - 2]) <= (c[k - 2] - o[k - 2]) + 1e-9:
            red3_idx = k
    first_break = None
    for k in range(best["i2"], j + 1):
        if c[k] > best["neck"] * 1.005 and v[k] > 1.2 * np.mean(v[max(0, k - 19):k + 1]):
            first_break = k
            break
    black = None
    if first_break is not None:
        for k in range(first_break, j + 1):
            if c[k] < o[k] and (o[k] - c[k]) / o[k] >= 0.02 and l[k] >= min(best["l1"], best["l2"]) * 0.99 \
                    and v[k] >= 0.8 * np.mean(v[max(0, k - 19):k + 1]):
                black = k
                break
    since_b = (j - first_break) if first_break is not None else None
    if first_break is not None and since_b <= 20:
        st = "突破颈线（%d 根前，仍在入场窗口）" % since_b
    elif first_break is not None:
        st = "已走远（突破在 %d 根前）" % since_b
    else:
        st = "磨底中（双底已成，等突破）"
    return dict(state=st, **best,
                dist_neck_pct=round((c[j] / best["neck"] - 1) * 100, 2),
                red3_age=(j - red3_idx) if red3_idx is not None else None,
                black_age=(j - black) if black is not None else None,
                first_break_age=since_b)


# ────────────────────────── 读数 ──────────────────────────
def read(day: str, cut: str = CUT) -> dict:
    etf_d = adjust_splits(fetch_daily(ETF, day))
    idx_d = fetch_daily(IDX, day)

    # 当日（盘中）用分钟覆盖最后一行；历史日用日线
    cb = tx_today(ETF, day) or em_today("1." + ETF.split(".")[0], day)
    ib = tx_today(IDX, day) or em_today("1." + IDX.split(".")[0], day)
    intraday = cb is not None
    if not intraday:                      # 历史回放：退回分钟
        e_bars = today_bars(ETF, day, cut)
        intraday = e_bars is not None and str(e_bars.trade_time.iloc[-1])[:10].replace("-", "") == day
        cb = cum_bar(e_bars) if intraday else None
        ib = cum_bar(today_bars(IDX, day, cut)) if intraday else None
    if intraday:
        if etf_d.trade_date.iloc[-1] != day:
            etf_d = pd.concat([etf_d, pd.DataFrame([dict(
                trade_date=day, open=cb["open"], high=cb["high"], low=cb["low"],
                close=cb["close"], pre_close=etf_d.close.iloc[-1], vol=cb["vol"] / PRE_CUT_SHARE,
                amount=cb["amount"] / PRE_CUT_SHARE, pct_chg=np.nan)])], ignore_index=True)
        else:
            etf_d.loc[etf_d.index[-1], ["open", "high", "low", "close", "vol", "amount"]] = \
                [cb["open"], cb["high"], cb["low"], cb["close"], cb["vol"] / PRE_CUT_SHARE, cb["amount"] / PRE_CUT_SHARE]
        if ib is not None:
            if idx_d.trade_date.iloc[-1] != day:
                idx_d = pd.concat([idx_d, pd.DataFrame([dict(
                    trade_date=day, open=ib["open"], high=ib["high"], low=ib["low"],
                    close=ib["close"], pre_close=idx_d.close.iloc[-1],
                    vol=ib["vol"] / PRE_CUT_SHARE, amount=ib["amount"] / PRE_CUT_SHARE)])], ignore_index=True)
            else:
                idx_d.loc[idx_d.index[-1], ["open", "high", "low", "close", "vol", "amount"]] = \
                    [ib["open"], ib["high"], ib["low"], ib["close"], ib["vol"] / PRE_CUT_SHARE, ib["amount"] / PRE_CUT_SHARE]

    j = len(etf_d) - 1
    c, h, l, v = (etf_d[x].values.astype(float) for x in ("close", "high", "low", "vol"))
    pos = (c[j] - l[j]) / max(1e-9, h[j] - l[j])
    qt = (cb or {}).get("quote_time", "") if intraday else ""
    cut_ok = (not intraday) or ("1440" <= qt[8:12] <= "1500")
    vr = (v[j] / np.mean(v[max(0, j - 20):j])) if cut_ok else None
    vr1 = v[j - 1] / np.mean(v[max(0, j - 21):j - 1])
    pos1 = (c[j - 1] - l[j - 1]) / max(1e-9, h[j - 1] - l[j - 1])
    broke = l[j] < l[j - 1]

    # 市场（沪市）
    ia = idx_d.amount.values.astype(float)
    amt = ia[-1] / 1e5
    a20 = np.mean(ia[max(0, len(ia) - 20):len(ia) - (1 if intraday else 0)]) / 1e5 if len(ia) > 21 else np.nan
    if intraday:
        a20 = np.mean(ia[-21:-1]) / 1e5

    # 结构
    ih, il, ic_, io, iv = (idx_d[x].values.astype(float) for x in ("high", "low", "close", "open", "vol"))
    bottom = detect_bottom(ih, il, ic_, io, iv, len(idx_d) - 1)

    # 上方/下方结构点（ETF 摆动）
    piv = [i for i in range(5, j - 4) if l[i] == min(l[i - 5:i + 6]) or h[i] == max(h[i - 5:i + 6])]
    up = sorted({round(h[i], 3) for i in piv if h[i] > c[j]})[:4]
    dn = sorted({round(l[i], 3) for i in piv if l[i] < c[j]}, reverse=True)[:3]

    warn = ""
    if (not intraday) and day == datetime.now().strftime("%Y%m%d"):
        warn = "盘中实时数据未取到 ⇒ 本次用的是上一交易日日线，读数不代表今日"
    elif intraday and not cut_ok:
        warn = f"快照时间 {qt[8:12] if len(qt)>12 else '?'}，非 14:50 口径：量比未计算，量额为累计值"
    return dict(
        asof=day, cutoff=cut, intraday=bool(intraday), data_warning=warn,
        etf=dict(code=ETF, price=round(c[j], 3), pct_yest=round((c[j] / c[j - 1] - 1) * 100, 2),
                 pos_pct=round(pos * 100), vol_ratio=(round(vr, 2) if vr is not None else None),
                 yest=dict(vol_ratio=round(vr1, 2), pos_pct=round(pos1 * 100)),
                 broke_yest_low=bool(broke)),
        market=dict(sh_amount_yi=round(amt),
                    sh_ratio=(round(amt / a20, 2) if (cut_ok and a20 == a20) else None),
                    note=(None if cut_ok else "非 14:50 口径，仅报累计值，量比不计算")),
        bottom_structure=bottom,
        wave=wave_from_pg(day),
        space=dict(above=up, below=dn, dist_above_pct=round((up[0] / c[j] - 1) * 100, 1) if up else None),
        source_note="盘中量已按截至 %s 的累计量折算（÷%.3f）；历史日用日线" % (cut, PRE_CUT_SHARE) if intraday else "日线口径",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--cut", default=CUT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        r = read(a.asof, a.cut)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)[:300]}, ensure_ascii=False))
        raise
    print(json.dumps({"ok": True, **r}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
