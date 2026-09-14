# -*- coding: utf-8 -*-
"""eval_roundtrip_windows.py — A4（做 T 时间窗 + 2 点决断）在 428 条参考腿上的对照。

狼大依据：
  · 2025-04-15 成文流程条件 2「当日只做上午 9:45–10:00、下午 14:00–14:30 这两个时间段」
  · 2026-09-02 14:03「2 点到了 力度不够 我先把早上博弈的先 T 了…结束今天半导体做 T 操作」
  · 2026-08-04 10:48「一旦突发跌破直接走；如果没跌破就找这半小时的高点」
变体（都带 A1 确认破黄线保护 = 幅度 ≥0.5% 或连续 2 根在黄线下）：
  A4a 目标只在窗口内兑现 + 到点(14:00)不达标收工   ← 生产落地口径
  A4b 目标随时兑现         + 到点(14:00)不达标收工
  A4c 目标只在窗口内兑现，不做到点收工
  V1i 盘中一碰 +3% 就卖（旧限价语义）| V3d 生产旧口径 | hold 持 T+5
用法: .venv/bin/python jobs/eval_roundtrip_windows.py [--tp 3.0] [--chase 0.05]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import eval_leg_metrics as M  # noqa: E402

WINDOWS = ((9 * 60 + 45, 10 * 60), (14 * 60, 14 * 60 + 30))
FORCE_HM = 14 * 60
TOL = 0.005
BREAK_PCT, BREAK_ROUNDS = 0.005, 2


def _load_eval():
    spec = importlib.util.spec_from_file_location("_eer", os.path.join(HERE, "eval_exit_rules.py"))
    mod = importlib.util.module_from_spec(spec)
    old = sys.argv
    sys.argv = ["eval_exit_rules.py", "--accounts", "stock"]
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    finally:
        sys.argv = old
    return mod


def hm_of(bar) -> int:
    t = str(bar.get("time") or "")
    try:
        return int(t[11:13]) * 60 + int(t[14:16])
    except (ValueError, IndexError):
        return -1


def in_win(hm: int) -> bool:
    return any(a <= hm <= b for a, b in WINDOWS)


def sim(rb, m5, eer, sym, d0, entry, mode, tp=3.0, hold=5):
    """mode ∈ {hold, V1i, V3d, A4a, A4b, A4c, A4d, closeTP}

    · hold   = 持到窗口末端（不设保护，纯基准）
    · V1i    = 盘中一碰 +tp% 即卖（旧"限价"语义），否则持到末端
    · closeTP= 当日**收盘** ≥ +tp% 就以收盘价卖（尾盘决断的乐观上限），否则持到末端
    · V3d    = closeTP + A1 确认破黄线保护
    · A4a    = 目标只在做T窗口内兑现 + 到点(14:00)不达标收工 + 保护（生产落地口径）
    · A4b    = 目标随时兑现 + 到点收工 + 保护
    · A4c    = 目标只在窗口内兑现，不做到点收工（+ 保护）
    · A4d    = A4a 但**去掉**破线保护（用于归因）
    """
    protect = mode in ("V3d", "A4a", "A4b", "A4c")
    rows = rb.get(sym)
    i = M.snap_idx(rb, sym, d0)
    if i is None:
        return None
    for k in range(1, hold + 1):
        if i + k >= len(rows):
            break
        d8, c = rows[i + k][0], rows[i + k][4]
        if mode in ("V3d", "closeTP") and (c / entry - 1.0) * 100 >= tp:
            return (c / entry - 1.0) * 100
        day = m5.day(sym, d8)
        if not day:
            continue
        vw = eer.vwap_series(day)
        below = 0
        for j, b in enumerate(day):
            px = float(b.get("close") or 0)
            if px <= 0:
                continue
            hm = hm_of(b)
            hit = (px / entry - 1.0) * 100 >= tp
            # 1) 目标
            if hit and mode in ("V1i", "V3d", "A4b"):
                return (px / entry - 1.0) * 100
            if hit and mode in ("A4a", "A4c") and in_win(hm):
                return (px / entry - 1.0) * 100
            # 2) 确认破黄线（保护）
            if protect and j >= 2 and vw and vw[j]:
                if px < vw[j]:
                    below += 1
                    if px <= vw[j] * (1 - BREAK_PCT) or below >= BREAK_ROUNDS:
                        return (px / entry - 1.0) * 100
                else:
                    below = 0
            # 3) 到点决断
            if mode in ("A4a", "A4b", "A4d") and hm >= FORCE_HM and px >= entry * (1 - TOL):
                return (px / entry - 1.0) * 100
    j = min(i + hold, len(rows) - 1)
    return (rows[j][4] / entry - 1.0) * 100


def row(name, r):
    pos = [x for x in r if x > 1e-9]
    neg = [x for x in r if x < -1e-9]
    mp = st.mean(pos) if pos else 0.0
    mn = st.mean(neg) if neg else 0.0
    win = len(pos) / len(r)
    be = abs(mn) / (mp + abs(mn)) if (mp + abs(mn)) else 0.0
    print("%-6s n=%-4d 胜率 %5.1f%% 平衡 %5.1f%% edge %+5.1fpp 盈亏比 %.2f 均值 %+6.2f 中位 %+6.2f 扣0.15%% %+6.2f"
          % (name, len(r), 100 * win, 100 * be, 100 * (win - be),
             (mp / abs(mn)) if mn else 0, st.mean(r), st.median(r), st.mean(r) - 0.15))
    return {"n": len(r), "win": round(win, 4), "be": round(be, 4), "edge_pp": round(100 * (win - be), 2),
            "mean": round(st.mean(r), 2), "median": round(st.median(r), 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tp", type=float, default=3.0)
    ap.add_argument("--hold", type=int, default=5, help="持有末端（交易日）：428 腿默认 5；做 T 腿实际是 1-2")
    ap.add_argument("--modes", default="hold,V1i,closeTP,V3d,A4a,A4b,A4c,A4d")
    ap.add_argument("--out", default=os.path.join(ROOT, ".dsh-tmp", "wolfbt", "legmetrics",
                                                 "eval_roundtrip_windows.json"))
    args = ap.parse_args()
    eer = _load_eval()
    legs = M.load_replay_legs()
    rb, m5 = M.ReplayBars(), eer.M5Bars()
    modes = tuple(x.strip() for x in args.modes.split(",") if x.strip())
    out = {"_meta": {"n": len(legs), "tp": args.tp, "hold": args.hold, "windows": WINDOWS},
           "variants": {}}
    print("== 持有末端 %d 个交易日 ==" % args.hold)
    for mode in modes:
        r = []
        for l in legs:
            v = sim(rb, m5, eer, l["ts_code"], l["arm_date"], float(l["entry_px"]), mode,
                    tp=args.tp, hold=args.hold)
            if v is not None:
                r.append(v)
        out["variants"][mode] = row(mode, r)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("→", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
