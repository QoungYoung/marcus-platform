#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""wolf_neck_break_alert.py — 颈线放量突破的**盘中**QQ 提醒（每 5 分钟一跳）。

为什么需要：颈线（= 两只脚之间的最高点）在他那套体系里是「**加强/加速**」信号
（2026-01-12「突破颈线的加速」；2026-02-03「反弹 → 过颈线 → 加强」）——
不是结构成立的条件（结构由三根K线定），但突破当天是右侧加仓点，值得当天知道。

判据（全部用 ≤ 当下的数据）：
  · 价格：现价 > 颈线 × (1+0.5%)
  · 量能：今日累计额 ÷ (20 日均额 × 当日时间占比) ≥ 1.03
  · 只在交易时段跑；每天每个颈线只推一次（data/…/neck_alert_state.json 去重）

用法：
  python jobs/wolf_neck_break_alert.py            # 正常跑（到点才推）
  python jobs/wolf_neck_break_alert.py --force    # 忽略时段/去重，强制推一次（测试）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

IDX = "000001.SH"
BREAK_PCT = 0.005          # 突破幅度（0.5%）
VOL_RATIO_MIN = 1.03       # 同刻量比下限
STATE = os.path.join(ROOT, "data", "neck_alert_state.json")
HOLD_MIN = 30              # 突破后连续站在颈线上方多久算「站稳」
FAIL_PCT = 0.005           # 跌回颈线下方多少算「假突破」
MAX_PUSH_PER_DAY = 3       # 一天最多推几条（防吵）

# 上证日内累计成交额的时间分布（实测锚点，11:30 57% / 13:30 69% / 14:00 77% / 14:30 87%）
_SHARE = [(930, 0.06), (1000, 0.20), (1030, 0.34), (1100, 0.46), (1130, 0.57),
          (1300, 0.60), (1330, 0.69), (1400, 0.77), (1430, 0.87), (1440, 0.90), (1500, 1.00)]


def time_share(hm: str) -> float:
    """当日已过时间对应的成交额占比（线性插值）。"""
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


def tx_quote(code: str) -> dict | None:
    """腾讯实时：价/开高低/累计量额。"""
    c = ("sh" if code.endswith(".SH") else "sz") + code.split(".")[0]
    try:
        raw = urllib.request.urlopen(urllib.request.Request(
            f"https://qt.gtimg.cn/q={c}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}),
            timeout=10).read().decode("gbk", "ignore").split("~")
        return dict(px=float(raw[3]), prev=float(raw[4]), open=float(raw[5]),
                    high=float(raw[33]), low=float(raw[34]),
                    amount_wan=float(raw[37]), time=raw[30][8:12], name=raw[1])
    except Exception as e:
        print(f"[warn] 取价失败: {str(e)[:60]}", file=sys.stderr)
        return None


def daily(code: str, end: str, api: str = "index_daily", n: int = 60) -> pd.DataFrame:
    from core.tushare_relay import relay_query
    d = relay_query(api, ts_code=code, start_date="20260601", end_date=end)
    d = d.sort_values("trade_date").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "vol", "amount"):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce").astype(float)
    return d


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


def decide(stage: str, above: bool, below_fail: bool, vr: float,
           first_break: str, now_min: int, hm: str, px: float, neck: float,
           state_text: str, force: bool = False):
    """三段式判定（纯函数，便于单测）：返回 (stage, (title, body) 或 None)。"""
    if force:
        return stage, ("强制测试", f"现价 **{px:.2f}**｜颈线 **{neck:.2f}**｜量比 **{vr:.2f}×**")
    if stage in ("NONE", "FAIL", "HOLD_CLOSE") and above and vr >= VOL_RATIO_MIN:
        return "BREAK", ("突破（试探）",
                         f"现价 **{px:.2f}** 突破颈线 **{neck:.2f}** +{(px/neck-1)*100:.2f}%\n"
                         f"同刻量比 **{vr:.2f}×** ⇒ 放量突破\n"
                         f"⚠️ 这是**试探**：要连续站在颈线上方 {HOLD_MIN} 分钟、或收盘站上才算站稳")
    if stage == "BREAK" and above and first_break and now_min - _hm2min(first_break) >= HOLD_MIN:
        return "HOLD", ("站稳（加强成立）",
                        f"现价 **{px:.2f}** 已在颈线 **{neck:.2f}** 上方连续 "
                        f"{now_min - _hm2min(first_break)} 分钟\n"
                        f"同刻量比 **{vr:.2f}×** ⇒ 他说的「过颈线 → 加强」成立\n"
                        f"结构：{state_text}")
    if stage in ("BREAK", "HOLD") and below_fail and vr >= VOL_RATIO_MIN:
        return "FAIL", ("假突破（撤销）",
                        f"现价 **{px:.2f}** 已跌回颈线 **{neck:.2f}** 下方 {(1-px/neck)*100:.2f}%，"
                        f"且**是放量**（{vr:.2f}×）\n"
                        f"—— 他 2026-03-04：「缩量跌破不算，放量跌破就直接算」⇒ 这次突破作废")
    if hm >= "1455" and above and stage in ("BREAK", "HOLD"):
        return "HOLD_CLOSE", ("收盘站上颈线（确认）",
                              f"收盘 **{px:.2f}** 站在颈线 **{neck:.2f}** 上方 = 当日突破成立\n"
                              f"全天量比 **{vr:.2f}×**\n结构：{state_text}")
    return stage, None


def _hm2min(x):
    try:
        return int(x[:2]) * 60 + int(x[2:4])
    except Exception:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略时段与去重（测试用）")
    ap.add_argument("--dry", action="store_true", help="只算不推（联调用）")
    a = ap.parse_args()

    q = tx_quote(IDX)
    if not q:
        return 2
    hm = q.get("time") or ""
    if not a.force and not ("0930" <= hm <= "1500"):
        print(f"[skip] 非交易时段 {hm}")
        return 0

    day = datetime.now().strftime("%Y%m%d")
    d = daily(IDX, day)
    if d.empty:
        print("[skip] 取不到日线")
        return 2
    # 今日盘中的临时 bar（量额按时间占比还原成全天口径）
    sh = max(0.05, time_share(hm))
    today = dict(trade_date=day, open=q["open"], high=q["high"], low=q["low"], close=q["px"],
                 vol=q["amount_wan"] / sh, amount=q["amount_wan"] / sh)
    if d.trade_date.iloc[-1] != day:
        d = pd.concat([d, pd.DataFrame([today])], ignore_index=True)
    h, l, c, o, v = (d[x].values.astype(float) for x in ("high", "low", "close", "open", "vol"))
    from bottom_struct import detect
    st = detect(h, l, c, o, v, len(d) - 1)
    neck = st.get("neck")
    if not neck:
        print(f"[skip] 还没有颈线（状态：{st.get('state')}）")
        return 0

    px = q["px"]
    above = px > neck * (1 + BREAK_PCT)
    # ★ 单位：腾讯 amount 是**万元**，tushare index_daily.amount 是**千元**（差 10 倍）
    #   统一换成亿元再比，否则量比会算成 0.12× 这种假值（实测踩过 ✗）
    a20_yi = float(d.amount.iloc[-21:-1].mean()) / 1e5 if len(d) > 21 else 0.0
    est_full_yi = q["amount_wan"] / sh / 1e4                # 万元 → 亿元
    vr = (est_full_yi / a20_yi) if a20_yi else 0.0
    print(f"现价 {px:.2f} | 颈线 {neck:.2f} | 突破 {'✓' if above else '✗'} "
          f"({(px/neck-1)*100:+.2f}%) | 同刻量比 {vr:.2f}× | {st.get('state')}")

    if not (above or a.force):
        return 0
    if not (vr >= VOL_RATIO_MIN or a.force):
        print(f"[skip] 突破但没放量（{vr:.2f}× < {VOL_RATIO_MIN}）")
        return 0

    # ── 三段式状态机：突破(试探) → 站稳(加强成立) / 假突破(撤销) ──
    #   只看盘中一瞬间会骗人（冲高回落最常见）⇒ 必须有时长与「跌回」两个方向的事件。
    #   他 2026-03-04：「缩量跌破不算…放量跌破就直接算」⇒ 跌回也要看量。
    try:
        st_json = json.load(open(STATE, encoding="utf-8")) or {}
    except Exception:
        st_json = {}
    key = f"{day}:{round(float(neck), 2)}"
    rec = st_json.get(key) or {"stage": "NONE", "pushes": 0}
    if not isinstance(rec, dict):                      # 兼容旧格式（string 时间戳）
        rec = {"stage": "BREAK", "pushes": 1, "first_break": str(rec)}

    now_min = _hm2min(hm)
    above = px > neck * (1 + BREAK_PCT)
    below_fail = px < neck * (1 - FAIL_PCT)
    stage = rec.get("stage") or "NONE"
    stage, msg = decide(stage, above, below_fail, vr, rec.get("first_break") or "",
                        now_min, hm, px, neck, st.get("state") or "", force=a.force)
    if msg and stage == "BREAK" and not rec.get("first_break"):
        rec["first_break"] = hm
    rec["stage"] = stage
    rec["last_px"] = round(px, 2)
    rec["last_hm"] = hm
    print(f"stage={stage} above={above} below_fail={below_fail} vr={vr:.2f} msg={'✓' if msg else '-'}")

    if not msg:
        st_json[key] = rec
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            json.dump(st_json, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
        return 0
    if isinstance(msg, str):
        title, body = "颈线突破", ""
    else:
        title, body = msg

    txt = (f"【{title}】{q['name']} {hm[:2]}:{hm[2:]}\n{body}\n"
           f"结构：{st.get('state')}\n"
           f"—— 他 2026-02-03：「反弹 → **过颈线 → 加强**」；过颈线是加强信号，不是结构成立条件")
    if a.dry:
        print("[dry] 不推送：\n" + txt)
        return 0
    if rec.get("pushes", 0) >= MAX_PUSH_PER_DAY and not a.force:
        print(f"[skip] 今天已推 {rec.get('pushes')} 条，达上限")
        st_json[key] = rec
        return 0
    if push(txt):
        rec["pushes"] = int(rec.get("pushes", 0)) + 1
        rec[f"pushed_{rec['pushes']}"] = f"{title}@{hm}"
        st_json[key] = rec
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            json.dump(st_json, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
        print("[QQ] 已推送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
