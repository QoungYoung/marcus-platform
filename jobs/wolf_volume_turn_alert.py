#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""wolf_volume_turn_alert.py — 「缩转放」盘中监控 + QQ 推送（每 5 分钟）。

狼大 2026-11-06 11:21 原话（**定义**）：
  网友：「这里的缩转放只能是**分时相对缩量转放量**吧？毕竟已经缩了 3300 亿了，
        下午怎么放也不可能把这个补上。」
  他  ：「**对** 但是这个需要盘感 需要经验」

狼大 2026-11-06 11:16（**用法**）：
  「今天**第二次机会在下午可能的缩转放**，除此之外没有别的任何的买点了」

⇒ 判据拆成三条，缺一不推：
  ① **整体在缩**：今日折算全天量 ÷ 20 日均量 < 1.0（他说的"已经缩了 3300 亿"）
  ② **分时相对放量**：最近 15 分钟量 ÷ 前面最淡的 15 分钟桶 ≥ 1.3×（相对于今天自己的量）
  ③ **价格配合**：现价在当日 VWAP 之上，且最近 15 分钟价格上行

为什么不是日级：他明确否掉了"把上午缩的量在下午补回来"这种理解 —— 日级不可能缩转放。

用法：
  python jobs/wolf_volume_turn_alert.py            # 正常跑（到点才推）
  python jobs/wolf_volume_turn_alert.py --dry      # 只算不推
  python jobs/wolf_volume_turn_alert.py --force    # 忽略时段/去重（测试）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

IDX = "000001.SH"
ETF = "588170.SH"
BUCKET = 15                 # 分时桶（分钟）
TURN_MIN = 1.3              # 分时相对放量倍数
DAY_SHRINK_MAX = 1.0        # 整体必须缩量（<1.0× 20日均）
STATE = os.path.join(ROOT, "data", "volume_turn_state.json")
MAX_PUSH = 2                # 一天最多推 2 条（他说的"第二次机会"）

# 上证日内累计成交额占比（实测锚点）
_SHARE = [(930, 0.06), (1000, 0.20), (1030, 0.34), (1100, 0.46), (1130, 0.57),
          (1300, 0.60), (1330, 0.69), (1400, 0.77), (1430, 0.87), (1440, 0.90), (1500, 1.00)]


def time_share(hm: str) -> float:
    try:
        t = int(hm[:2]) * 100 + int(hm[2:4])
    except Exception:
        return 1.0
    if t <= _SHARE[0][0]:
        return _SHARE[0][1]
    for (t0, s0), (t1, s1) in zip(_SHARE, _SHARE[1:]):
        if t0 <= t <= t1:
            return s0 + (s1 - s0) * (t - t0) / max(1, t1 - t0)
    return 1.0


def tx(code: str):
    """腾讯分时（累计量）+ 实时快照。"""
    tx_code = ("sh" if code.endswith(".SH") else "sz") + code.split(".")[0]
    raw = urllib.request.urlopen(urllib.request.Request(
        f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={tx_code}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}),
        timeout=12).read().decode("utf-8", "ignore")
    rows = json.loads(raw)["data"][tx_code]["data"]["data"]
    q = urllib.request.urlopen(urllib.request.Request(
        f"https://qt.gtimg.cn/q={tx_code}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}),
        timeout=10).read().decode("gbk", "ignore").split("~")
    return rows, q


def analyze(rows, q) -> dict | None:
    """→ {turn, bucket_ratio, fade_time, peak_time, vwap, px, above_vwap, last15_pct}"""
    if len(rows) < 40:
        return None
    t = [r.split()[0] for r in rows]
    px_s = [float(r.split()[1]) for r in rows]
    v = [float(r.split()[2]) for r in rows]
    dv = [v[i] - v[i - 1] for i in range(1, len(v))]
    if not dv:
        return None
    # VWAP（价×量加权，避开 amount/vol 单位不一致）
    num = sum(px_s[i + 1] * dv[i] for i in range(len(dv)))
    den = sum(dv) or 1.0
    vwap = num / den
    px = px_s[-1]
    # 分桶（只取完整桶）
    n = len(dv) // BUCKET
    buckets = [sum(dv[i * BUCKET:(i + 1) * BUCKET]) for i in range(n)]
    if len(buckets) < 3:
        return None
    fade_i = min(range(len(buckets)), key=lambda i: buckets[i])
    cur_i = len(buckets) - 1
    ratio = buckets[cur_i] / buckets[fade_i] if buckets[fade_i] > 0 else 0.0
    # 最近 15 分钟价格变化
    k = min(BUCKET, len(px_s) - 1)
    last15_pct = (px_s[-1] / px_s[-1 - k] - 1) * 100 if k > 0 and px_s[-1 - k] else 0.0
    return dict(px=px, vwap=round(vwap, 2), above_vwap=bool(px > vwap),
                bucket_ratio=round(ratio, 2),
                fade_time=t[min(len(t) - 1, fade_i * BUCKET + BUCKET - 1)],
                now_time=t[min(len(t) - 1, cur_i * BUCKET + BUCKET - 1)],
                last15_pct=round(last15_pct, 2), buckets=len(buckets),
                prev=float(q[4]), pct=(px / float(q[4]) - 1) * 100 if float(q[4]) else 0.0)


def day_amount_ratio(hm: str):
    """→ (对20日均的倍数, 今日折算亿元, 对昨日的倍数, 昨日亿元)

    **对昨日**才是他说的"缩量"（他 11-06 10:59「缩量太大了」= 相对 11-05 的约 9500 亿天量）；
    对 20 日均只作参考展示 —— 用它当门槛会把真实缩转放挡掉（11-06 13:48 实测 ✗）。
    """
    import pandas as pd
    from core.tushare_relay import relay_query
    amt_wan = None
    try:
        q = urllib.request.urlopen(urllib.request.Request(
            "https://qt.gtimg.cn/q=sh000001",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}),
            timeout=10).read().decode("gbk", "ignore").split("~")
        amt_wan = float(q[37])                       # 万元（累计）
    except Exception as e:
        print(f"[warn] 取量失败: {str(e)[:60]}", file=sys.stderr)
    try:
        d = relay_query("index_daily", ts_code=IDX, start_date="20260601", end_date="20991231")
        a = pd.to_numeric(d.sort_values("trade_date").amount, errors="coerce") / 1e5   # 千元→亿元
        a20 = a.iloc[-21:-1].mean() if len(a) > 21 else None
        prev_yi = float(a.iloc[-1]) if len(a) else None
        if amt_wan is None:
            return None, None, None, prev_yi
        sh = max(0.05, time_share(hm))
        est_yi = amt_wan / sh / 1e4                  # 万元 → 亿元
        return ((round(est_yi / a20, 2) if a20 else None), round(est_yi),
                (round(est_yi / prev_yi, 2) if prev_yi else None), (round(prev_yi) if prev_yi else None))
    except Exception as e:
        print(f"[warn] 日级量比失败: {str(e)[:60]}", file=sys.stderr)
        return None, (round(amt_wan / max(0.05, time_share(hm)) / 1e4) if amt_wan else None), None, None


def push(text: str) -> bool:
    try:
        from core.qq_notifier import send_c2c_message
        openid = (os.environ.get("QQ_BOT_RECIPIENT") or "").strip()
        if not openid:
            print("[warn] 未配置 QQ_BOT_RECIPIENT", file=sys.stderr)
            return False
        return bool(send_c2c_message(openid, text))
    except Exception as e:
        print(f"[warn] 推送失败: {str(e)[:80]}", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    rows, q = tx(IDX)
    hm = q[30][8:12] if len(q) > 30 else ""
    if not a.force and not ("0930" <= hm <= "1500"):
        print(f"[skip] 非交易时段 {hm}")
        return 0
    info = analyze(rows, q)
    if not info:
        print("[skip] 分时数据不足")
        return 2
    dr, est_yi, dy, prev_yi = day_amount_ratio(hm)

    # ★ 2026-11-06 13:48 实测修正：「缩量」的基准是**昨日**，不是 20 日均。
    #   他 10:59「缩量太大了」指的是相对 11-05 的约 9500 亿天量；
    #   而 20 日均（约 7600 亿）口径下今天 8658 亿是 1.13×"放量" ⇒ 会把真实的缩转放挡掉 ✗
    shrink = (dy is not None and dy < DAY_SHRINK_MAX)
    turn = info["bucket_ratio"] >= TURN_MIN
    price_ok = info["above_vwap"] and info["last15_pct"] > 0
    print(f"{hm} 价{info['px']:.2f} VWAP{info['vwap']} | 分时桶比 {info['bucket_ratio']}×"
          f"（最淡 {info['fade_time']} → 现在）｜近15分 {info['last15_pct']:+.2f}%"
          f"｜今日折算 {dy}× 昨日（{est_yi}亿 vs {prev_yi}亿；20日均 {dr}×）⇒ 整体{'缩量' if shrink else '不缩'} "
          f"｜分时{'放量' if turn else '未放'} ｜价{'配合' if price_ok else '未配合'}")

    if not (shrink and turn and price_ok) and not a.force:
        return 0

    try:
        st = json.load(open(STATE, encoding="utf-8")) or {}
    except Exception:
        st = {}
    day = datetime.now().strftime("%Y%m%d")
    rec = st.get(day) or {"pushes": 0, "keys": []}
    key = f"{info['fade_time']}->{info['now_time']}"
    if key in rec.get("keys", []) and not a.force:
        print("[skip] 这个转折点已推过")
        return 0
    if rec.get("pushes", 0) >= MAX_PUSH and not a.force:
        print(f"[skip] 今天已推 {rec['pushes']} 条")
        return 0

    n = int(rec.get("pushes", 0)) + 1
    # ★ 2026-11-06：必须带当天的基调，否则"缩转放=买点"会被读成"重仓信号"
    #   他当天 13:02「冷静 谨慎」、11:40「缩量叠加周五⇒相对保守、留点钱周一」、
    #   11:37「数三失败⇒还在筑双脚阶段，没到 B反」
    _tone = ("⚠️ 今日基调（他 11-06）：13:02「冷静 谨慎」｜11:40「缩量叠加周五⇒相对保守，留点钱周一」"
             "｜11:37「数三失败⇒还在筑双脚阶段，没到 B反」\n"
             "⇒ 缩转放是**第二次机会**，不是重仓信号；他原话「除此之外没有别的任何的买点了」")
    txt = (f"【缩转放】{q[1]} {hm[:2]}:{hm[2:]}（今日第 {n} 次）\n"
           f"现价 **{info['px']:.2f}**（{info['pct']:+.2f}%）｜VWAP {info['vwap']}\n"
           f"分时：**{info['fade_time']} 最淡 → 现在放大 {info['bucket_ratio']}×**，"
           f"近 15 分钟 {info['last15_pct']:+.2f}%\n"
           f"整体：今日折算 **{dy}× 昨日**（约 {est_yi} 亿 vs 昨日 {prev_yi} 亿；"
           f"20 日均 {dr}×）⇒ {'缩量' if shrink else '非缩量'}\n"
           f"—— 他 11-06 11:21：「缩转放只能是**分时相对缩量转放量**」\n"
           f"{_tone}")
    if a.dry:
        print("[dry] 不推送：\n" + txt)
        return 0
    if push(txt):
        rec["pushes"] = n
        rec.setdefault("keys", []).append(key)
        rec["last"] = f"{hm} {info['fade_time']}->{info['now_time']} {info['bucket_ratio']}x"
        st[day] = rec
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
        print("[QQ] 已推送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
