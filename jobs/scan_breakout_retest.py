# -*- coding: utf-8 -*-
"""扫描历史 K 线的「突破 → 回踩不破/跌破」事件（B8 判例库）。

定义（扫描口径，不是策略参数）：
  · 突破日 i：close[i] > max(close[i-N:i])（N=20，仅用于找事件）
  · 回踩日 j：i+1..i+5 内，首次 low[j] <= level*1.005
  · 不破：close[j] >= level；跌破：close[j] < level
  · 前向：j+3 / j+5 / j+10 收盘收益

输出：data/breakout_retest_cases.json + 控制台摘要。
用法：python jobs/scan_breakout_retest.py
"""
import json, os, sys
from statistics import mean
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "main_line"))
import breakout_context as bc

SYMBOLS = [("sh000001", "上证指数"), ("sh512480", "半导体ETF"),
           ("sh588170", "科创半导体ETF"), ("sh512800", "银行ETF"),
           ("sh518880", "黄金ETF")]
N = 20
TOUCH = 1.005
MAX_WAIT = 5


def scan(code, name):
    bars = bc.fetch_daily(code)
    if not bars:
        return []
    ds = sorted(bars)
    c = [float(bars[d]["close"]) for d in ds]
    lo = [float(bars[d]["low"]) for d in ds]
    out = []
    last_i = -999
    for i in range(N, len(ds) - MAX_WAIT - 11):
        if i - last_i < 10:          # 同一趋势内去重（扫描便利，不是策略参数）
            continue
        level = max(c[i - N:i])
        if not (c[i] > level and c[i - 1] <= level):
            continue
        for j in range(i + 1, min(i + MAX_WAIT, len(ds) - 11) + 1):
            if lo[j] <= level * TOUCH:
                hold = c[j] >= level
                state = "hold" if hold else "break"
                base = j
                nxt = None
                if not hold and j + 1 < len(ds):
                    nxt = c[j + 1]
                    base = j + 1
                    if nxt < level:
                        state = "break_confirmed"
                    else:
                        state = "false_break"
                fwd = {}
                for k in (3, 5, 10):
                    if base + k < len(ds):
                        fwd["fwd%d" % k] = round((c[base + k] / c[base] - 1) * 100, 2)
                last_i = j
                out.append({"symbol": code, "name": name, "breakout": ds[i],
                            "retest": ds[j], "level": round(level, 3),
                            "breakout_close": round(c[i], 3),
                            "retest_low": round(lo[j], 3), "retest_close": round(c[j], 3),
                            "confirm_close": round(nxt, 3) if nxt is not None else None,
                            "state": state, "hold": bool(hold), "fwd": fwd})
                break
    return out


def main():
    rows = []
    for code, name in SYMBOLS:
        r = scan(code, name)
        rows += r
        print("%s %s: 突破→5日内回踩 %d 次；不破 %d / 跌破 %d" %
              (code, name, len(r), sum(1 for x in r if x["hold"]), sum(1 for x in r if not x["hold"])))
    # 统计
    holds = [x for x in rows if x["hold"]]
    breaks = [x for x in rows if x["state"] == "break_confirmed"]
    false_b = [x for x in rows if x["state"] == "false_break"]
    def stat(xs, k):
        v = [x["fwd"][k] for x in xs if k in x.get("fwd", {})]
        return round(mean(v), 2) if v else None
    summary = {
        "events": len(rows), "hold": len(holds), "break": len(breaks),
        "hold_rate": round(len(holds) / max(len(rows), 1), 3),
        "hold_fwd3": stat(holds, "fwd3"), "hold_fwd5": stat(holds, "fwd5"), "hold_fwd10": stat(holds, "fwd10"),
        "break_confirmed": len(breaks), "false_break": len(false_b),
        "break_fwd3": stat(breaks, "fwd3"), "break_fwd5": stat(breaks, "fwd5"), "break_fwd10": stat(breaks, "fwd10"),
        "false_fwd3": stat(false_b, "fwd3"), "false_fwd5": stat(false_b, "fwd5"), "false_fwd10": stat(false_b, "fwd10"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    os.makedirs("data", exist_ok=True)
    json.dump({"summary": summary, "rows": rows}, open("data/breakout_retest_cases.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    # 控制台样例
    print("\n最近 20 个回踩事件：")
    for x in rows[-20:]:
        print("%s %s 突破%s level=%.3f 回踩%s low=%.3f close=%.3f %s fwd3=%s fwd5=%s" %
              (x["symbol"], x["name"], x["breakout"], x["level"], x["retest"],
               x["retest_low"], x["retest_close"], "不破" if x["hold"] else "跌破",
               x["fwd"].get("fwd3"), x["fwd"].get("fwd5")))


if __name__ == "__main__":
    main()
