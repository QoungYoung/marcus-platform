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

try:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from bottom_struct import detect as _detect_v3      # v3：中间态 + MA5/MA10
except Exception:                                     # 兜底：用本文件的旧检测器
    _detect_v3 = None

ETF = "588170.SH"
IDX = "000001.SH"
CUT = "14:50"
# 截至 14:50 的累计量占全天量的比例（24 天实测：均值 94.8%，P10 91.4% / P90 96.9%）
PRE_CUT_SHARE = 0.948


# ────────────────────────── 取数 ──────────────────────────
def _relay():
    from core.tushare_relay import relay_query
    return relay_query


def fetch_daily(code: str, end: str, n: int = 160, api: str | None = None) -> pd.DataFrame:
    if api is None:
        api = "index_daily" if code.startswith(("0000", "3990")) else "fund_daily"
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


def wave_block(asof: str) -> dict | None:
    """浪型：从 PG 读「段位」（b_rebound_wave_state，as_of<=当日取最近一版）+ 锚点
    （b_rebound_anchors，status=confirmed 且 confirmed_at<=当日）。PG 不可达才退回 config 文件。
    账本 §4c：生产 wave_state 的锚点会漂，当参考可以、当输入不行。"""
    iso = f"{asof[:4]}-{asof[4:6]}-{asof[6:]}"
    try:
        import psycopg2
        try:
            from b_rebound_schema import ensure_schema          # 直跑：sys.path[0] = jobs/
        except ImportError:                                     # 被当包导入（pytest 等）
            from jobs.b_rebound_schema import ensure_schema
        dsn = os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:18789/marcus_trading"
        with psycopg2.connect(dsn, connect_timeout=6) as conn:
            ensure_schema(conn)        # 本地/新环境缺这两张表时自愈（2026-10-30 14:50 就是缺表 ✗）
            cur = conn.cursor()
            cur.execute("select as_of,level,completed,running,next_watch,rejudge_trigger,evidence,"
                        "coalesce(level_key,'') "
                        "from b_rebound_wave_state where as_of<=%s order by as_of desc limit 1", (asof,))
            w = cur.fetchone()
            cur.execute("select instrument,session_date,price,kind,label,role,coalesce(level,'') "
                        "from b_rebound_anchors where status='confirmed' and confirmed_at::date<=%s "
                        "order by session_date", (iso,))
            rows = cur.fetchall()
        if w:
            idx = [dict(date=r[1], price=float(r[2]), kind=r[3], label=r[4], role=r[5], level=r[6])
                   for r in rows if r[0] == "000001.SH"]
            etf = [dict(date=r[1], price=float(r[2]), kind=r[3], label=r[4], role=r[5], level=r[6])
                   for r in rows if r[0] == ETF]
            stale = None
            try:
                from datetime import date
                a8 = str(w[0])
                stale = (date(int(asof[:4]), int(asof[4:6]), int(asof[6:])) -
                         date(int(a8[:4]), int(a8[4:6]), int(a8[6:]))).days
            except Exception:
                pass
            return dict(source="pg", as_of=w[0], stale_days=stale,
                        level=w[1], completed=w[2], running=w[3],
                        next_watch=w[4], rejudge_trigger=w[5], evidence=w[6],
                        anchors_index=idx[-6:], anchors_etf=etf[-4:],
                        key_levels=idx,            # 全量（right_side 判据要用 2024-10-08 的 1浪顶，不能被 -6 截掉）
                        wave1_top=next((a["price"] for a in idx if "1浪顶" in (a.get("label") or "")), None),
                        naming=dict(level=w[1], level_key=w[7], running=w[3],
                                    allowed_labels=[a.get("label") for a in idx if a.get("label")],
                                    rule=("段位名必须照抄 level / level_key；子段只能用 allowed_labels "
                                          "或价格序列表达；**禁止自造编号（1-3/1-4/1-5 之类）**")),
                        note=("段位/锚点均来自 PG。level=机械判级（之后20日反向≥25% 记大级别，"
                              "≥10% 记小级别）；**判断段位时只用与当前级别相同的锚点**，"
                              "不同级别的锚点并存是正常的，不是冲突"))
    except Exception as e:
        print(f"[warn] 浪型 PG 读取失败，退回文件: {str(e)[:70]}", file=sys.stderr)
    p = os.path.join(ROOT, "config", "b_rebound_anchors.json")
    if not os.path.exists(p):
        return None
    try:
        j = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    cur_ = j.get("current") or {}
    if not cur_:
        return None
    stale = None
    try:
        from datetime import date
        a8 = str(cur_.get("as_of", "")).replace("-", "")
        stale = (date(int(asof[:4]), int(asof[4:6]), int(asof[6:])) -
                 date(int(a8[:4]), int(a8[4:6]), int(a8[6:]))).days
    except Exception:
        pass
    _fl = [dict(x, level=x.get("level", "")) for x in (j.get("index") or [])
           if str(x.get("known_from", "")).replace("-", "") <= asof]
    return dict(source="file", as_of=cur_.get("as_of"), stale_days=stale,
                level=cur_.get("level"), completed=cur_.get("completed"),
                running=cur_.get("running"), next_watch=cur_.get("next_watch"),
                rejudge_trigger=cur_.get("rejudge_trigger"),
                naming=dict(level=cur_.get("level"), level_key=cur_.get("level_key") or "",
                            running=cur_.get("running"),
                            allowed_labels=[x.get("label") for x in _fl if x.get("label")],
                            rule=("段位名必须照抄 level / level_key；子段只能用 allowed_labels "
                                  "或价格序列表达；**禁止自造编号（1-3/1-4/1-5 之类）**")),
                anchors_index=[dict(x, level=x.get("level", "")) for x in (j.get("index") or [])
                               if str(x.get("known_from", "")).replace("-", "") <= asof][-6:],
                key_levels=[dict(x, level=x.get("level", "")) for x in (j.get("index") or [])
                            if str(x.get("known_from", "")).replace("-", "") <= asof],
                wave1_top=next((x.get("price") for x in (j.get("index") or [])
                                if "1浪顶" in str(x.get("label") or "")
                                and str(x.get("known_from", "")).replace("-", "") <= asof), None))


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


def _detect_v3_or_local(h, l, c, o, v, j):
    if _detect_v3 is not None:
        return _detect_v3(h, l, c, o, v, j)
    return detect_bottom(h, l, c, o, v, j)


def vol_trend(code: str) -> dict | None:
    """盘中「缩转放」：腾讯分时（累计量）→ 差分得每分钟量 → 最近 30 分钟 vs 前 30 分钟。
    phase=缩转放 需要：量比 ≥1.3 且 现价在当日均价之上（放量且价格配合）。"""
    import urllib.request
    tx_code = ("sh" if code.endswith(".SH") else "sz") + code.split(".")[0]
    try:
        raw = urllib.request.urlopen(urllib.request.Request(
            f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={tx_code}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}),
            timeout=12).read().decode("utf-8", "ignore")
        rows = json.loads(raw)["data"][tx_code]["data"]["data"]
        if len(rows) < 40:
            return None
        t = [r.split()[0] for r in rows]
        px_s = [float(r.split()[1]) for r in rows]
        v = [float(r.split()[2]) for r in rows]
        dv = [v[i] - v[i - 1] for i in range(1, len(v))]          # 每分钟量（差分）
        # VWAP 用「每分钟价 × 每分钟量」，避免 amount/vol 单位不一致（ETF 会差 100 倍）
        num = sum(px_s[i + 1] * dv[i] for i in range(len(dv)))
        den = sum(dv) or 1.0
        avg = num / den
        last30 = sum(dv[-30:]); prev30 = sum(dv[-60:-30]) if len(dv) >= 60 else None
        ratio = (last30 / prev30) if prev30 else None
        px = px_s[-1]
        phase = "数据不足"
        if ratio is not None:
            if ratio >= 1.3 and avg and px > avg:
                phase = "缩转放（量比≥1.3 且价在均价上）"
            elif ratio >= 1.3:
                phase = "放量但价在均价下"
            elif ratio <= 0.8:
                phase = "放转缩"
            else:
                phase = "量能平"
        # ── 全天是否出现过「缩转放」（他 13:49「今天下午缩转放进来的资金」是**事件**，不是当下状态）──
        #   按 30 分钟分桶，找最淡的一桶，看它之后的桶有没有放大 ≥1.3 倍。
        # ★ 只取完整的 30 分钟桶：最后一桶常常只有几分钟，量必然最小 ⇒ 会被误判成"最淡"
        _bk = [sum(dv[i:i + 30]) for i in range(0, len(dv) - 29, 30)]
        turn = dict(occurred=False, from_time="", to_time="", ratio=None)
        if len(_bk) >= 2:
            _mn = min(range(len(_bk)), key=lambda k: _bk[k])
            _after = _bk[_mn + 1:]
            if _after and _bk[_mn] > 0:
                _mx = max(_after)
                _r = _mx / _bk[_mn]
                _idx = _mn + 1 + _after.index(_mx)
                turn = dict(occurred=bool(_r >= 1.3),
                            from_time=t[min(len(t) - 1, _mn * 30 + 29)],
                            to_time=t[min(len(t) - 1, _idx * 30 + 29)],
                            ratio=round(_r, 2))
        return dict(bars=len(rows), time=t[-1], price=px, intraday_turn=turn,
                    avg_price=round(avg, 2) if avg else None,
                    last30=round(last30 / 1e4, 0), prev30=round(prev30 / 1e4, 0) if prev30 else None,
                    ratio_30v30=round(ratio, 2) if ratio else None, phase=phase)
    except Exception as e:
        print(f"[warn] 分时量能失败 {code}: {str(e)[:60]}", file=sys.stderr)
        return None


def levels_vs_price(closes, anchors: list[dict], cur: float) -> list[dict]:
    """浪型点位对照：现价 vs 每个结构点，附「连续收盘 > 该点」的天数。
    点位来自 b_rebound_anchors（不硬编码），换锚点这里就跟着变。"""
    out = []
    for a in anchors or []:
        try:
            p = float(a["price"])
        except Exception:
            continue
        n = 0
        for c in reversed(list(closes)):
            if c > p:
                n += 1
            else:
                break
        out.append(dict(label=a.get("label") or "", price=round(p, 2), level=a.get("level") or "",
                        gap_pct=round((cur / p - 1) * 100, 2), above=bool(cur > p), streak_days=n))
    return out


def production_wave(asof: str) -> dict | None:
    """生产浪型 wave_state（**裁决源**）：它被 gate/选股/交易链消费，段位名以它为准。
    b_rebound_wave_state 只负责「在这一档里的哪一段」。"""
    try:
        import psycopg2
        dsn = os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:18789/marcus_trading"
        iso = f"{asof[:4]}-{asof[4:6]}-{asof[6:]}"
        with psycopg2.connect(dsn, connect_timeout=6) as conn:
            cur = conn.cursor()
            cur.execute("select session_date,level,sub_level,rung,status,operation "
                        "from wave_state where session_date<=%s order by session_date desc limit 1", (iso,))
            r = cur.fetchone()
        if not r:
            return None
        return dict(session_date=str(r[0]), level=r[1], sub_level=r[2], rung=r[3],
                    status=r[4], operation=r[5],
                    note="生产浪型=段位裁决源；b_rebound_wave_state 的 level_key 若与 sub_level 不一致，必须写进【反证】")
    except Exception as e:
        print(f"[warn] 生产浪型读取失败: {str(e)[:60]}", file=sys.stderr)
        return None


def bottom_for(code: str, day: str) -> dict:
    """任意指数的底部结构（v3 中间态 + MA5/MA10）。含今日盘中 bar。"""
    try:
        d = fetch_daily(code, day, api="index_daily")
        q = tx_today(code, day)
        cols = ["open", "high", "low", "close", "vol"]
        if q:
            row = dict(trade_date=day, open=q["open"], high=q["high"], low=q["low"],
                       close=q["close"], vol=q["vol"])
            if d.trade_date.iloc[-1] == day:
                for c in cols:
                    d.loc[d.index[-1], c] = row[c]
            else:
                d = pd.concat([d, pd.DataFrame([row])], ignore_index=True)
        h, l, c, o, v = (d[x].values.astype(float) for x in ("high", "low", "close", "open", "vol"))
        return _detect_v3_or_local(h, l, c, o, v, len(d) - 1)
    except Exception as e:
        return {"state": f"检测失败: {str(e)[:60]}"}


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
    # ── 「缩转放」的**日级**判据（30v30 只作盘中动能参考）──
    #   他 11-04 13:00/13:44 说的缩转放 =「前几日缩量 → 今天放量」的日级转折。
    #   实测 14:50 时最后 30 分钟天然是全天最淡的一段 ⇒ 30v30 几乎恒 <1 ⇒ 用那个判据这条永远不成立 ✗
    #   amt 已经把盘中累计量按 PRE_CUT_SHARE 折算成全天口径，所以这里可以直接比。
    volume_turn = None
    try:
        if a20 == a20 and a20 > 0 and len(ia) >= 2:
            _prev = float(ia[-2]) / 1e5
            _rp, _rt = _prev / a20, amt / a20
            _ph = ("缩转放" if (_rp < 1.0 and _rt >= 1.03)
                   else "放量（之前没缩）" if _rt >= 1.03
                   else "缩量延续" if _rp < 1.0 else "量能平")
            volume_turn = dict(prev_day_ratio=round(_rp, 2), today_ratio=round(_rt, 2),
                               prev_day_amount_yi=round(_prev), ma20_amount_yi=round(a20),
                               phase=_ph,
                               note="缩转放=前一日<1.0× 且 今日≥1.03×（日级；30v30 只是盘中动能）")
    except Exception as e:
        print(f"[warn] volume_turn: {str(e)[:60]}", file=sys.stderr)
    if intraday:
        a20 = np.mean(ia[-21:-1]) / 1e5

    # 结构
    ih, il, ic_, io, iv = (idx_d[x].values.astype(float) for x in ("high", "low", "close", "open", "vol"))
    bottom = detect_bottom(ih, il, ic_, io, iv, len(idx_d) - 1)

    # 上方/下方结构点（ETF 摆动）
    piv = [i for i in range(5, j - 4) if l[i] == min(l[i - 5:i + 6]) or h[i] == max(h[i - 5:i + 6])]
    up = sorted({round(h[i], 3) for i in piv if h[i] > c[j]})[:4]
    dn = sorted({round(l[i], 3) for i in piv if l[i] < c[j]}, reverse=True)[:3]

    # 口径：盘中 14:50 / 盘后 / 回放 —— agent 必须据此决定用"今天"还是"明日"表述
    hm = (cb or {}).get("quote_time", "")[8:12] if intraday else ""
    if not intraday:
        mode, mode_note = "回放", "历史回放：结论是复盘口径，不是当日决策"
    elif "1440" <= hm <= "1500":
        mode, mode_note = "盘中14:50", "14:50 盘中口径：可以给「今天做不做」"
    elif "0930" <= hm < "1440":
        mode = "盘中"
        mode_note = ("盘中口径但**未到 14:50**：量比不可算（同刻均值要 14:50 口径）；"
                     "**结论必须标「待收盘确认」**，不能当成定论。可以说今天在发生什么，"
                     "不要说「今天就是起点」。")
    else:
        mode = "盘后"
        mode_note = ("盘后口径：今天已收盘，**不能写「今天可以建/今天做」**；"
                     "只能给「明日若 X → Y」的条件句。四行的目标/失效位照常给，它们是明天的参考位。")
    warn = ""
    if (not intraday) and day == datetime.now().strftime("%Y%m%d"):
        warn = "盘中实时数据未取到 ⇒ 本次用的是上一交易日日线，读数不代表今日"
    elif intraday and not cut_ok:
        warn = f"快照时间 {qt[8:12] if len(qt)>12 else '?'}，非 14:50 口径：量比未计算，量额为累计值"
    wv = wave_block(day) or {}
    _levels = levels_vs_price(idx_d.close.values, wv.get("key_levels") or wv.get("anchors_index") or [],
                              float(idx_d.close.iloc[-1]))
    _rs = next((x for x in _levels if "1浪顶" in (x.get("label") or "")), None)
    _etf_levels = levels_vs_price(etf_d.close.values, wv.get("anchors_etf") or [], float(etf_d.close.iloc[-1]))

    return dict(
        asof=day, cutoff=cut, intraday=bool(intraday), data_warning=warn,
        etf=dict(code=ETF, price=round(c[j], 3), pct_yest=round((c[j] / c[j - 1] - 1) * 100, 2),
                 off_hi60_pct=round((c[j] / h[max(0, j - 59):j + 1].max() - 1) * 100, 1),
                 is_brebound_position=bool(c[j] / h[max(0, j - 59):j + 1].max() - 1 <= -0.15),
                 pos_pct=round(pos * 100), vol_ratio=(round(vr, 2) if vr is not None else None),
                 yest=dict(vol_ratio=round(vr1, 2), pos_pct=round(pos1 * 100)),
                 broke_yest_low=bool(broke)),
        mode=mode, mode_note=mode_note,
        market=dict(sh_amount_yi=round(amt),
                    sh_vol_shou=(round(ib["vol"], 0) if ib else None),
                    sh_pos_pct=(round((ib["close"] - ib["low"]) / max(1e-9, ib["high"] - ib["low"]) * 100)
                                if ib else None),
                    note_unit="sh_vol_shou=手；sh_pos_pct=上证收盘位置；ETF 的收位在 etf.pos_pct，勿混",
                    sh_ratio=(round(amt / a20, 2) if (cut_ok and a20 == a20) else None),
                    note=(None if cut_ok else "非 14:50 口径，仅报累计值，量比不计算")),
        bottom_structure=dict(index=bottom_for(IDX, day), chinext=bottom_for("399006.SZ", day)),
        # 浪型点位对照（他 11-04 14:05「3674 以上 3 天收盘 = B反 右侧一买」）
        levels=dict(index=_levels, etf=_etf_levels,
                    right_side_level=(_rs or {}).get("price") or wv.get("wave1_top"),
                    right_side_streak=(_rs or {}).get("streak_days"),
                    right_side_ready=bool(_rs and (_rs.get("streak_days") or 0) >= 3),
                    note="streak_days=连续收盘 > 该点位的天数；right_side_ready 用「1浪顶」那一条，需 ≥3 天"),
        intraday_volume=dict(index=vol_trend(IDX), chinext=vol_trend("399006.SZ"), etf=vol_trend(ETF)),
        volume_turn=volume_turn,
        wave=wave_block(day),
        production_wave=production_wave(day),
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
    # 今日实时数据没取到 ⇒ 不能用昨日数据冒充今日读数：**非零退出**，
    # 让调度器推「❌失败」而不是让 Pi 分析一份过期的底稿（on_failure 已开）。
    if a.asof == datetime.now().strftime("%Y%m%d") and not r.get("intraday"):
        print("[FATAL] 今日实时行情未取到（腾讯/东财均失败），拒绝用昨日数据冒充；"
              "本次不推送读数。", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
