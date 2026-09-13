# -*- coding: utf-8 -*-
"""replay_intraday_0812.py — 08-12 分钟级无未来函数 全链路回放。
预盘(09:20): 主线AI/科技 + t_only/4-4 + rotation_healthy=True(not suck) -> mainline_rotation
  布腿: 卖侧=crowded/high 半导/设备(vwap_break), 买侧=room 光/算/液冷(低吸)。
盘中(5min bar, 无未来): 当前bar收盘后判断(用截至当前bar的VWAP/prev_low), 下一根bar开盘成交。
"""
import sys, json, time
sys.path.insert(0, "/app/scripts/p0_probe")
from data_sources import fetch_brze_stk_mins

DATE = "20260812"
CAND = {
    "300308.SZ": ("中际旭创(光)", "buy"),
    "301018.SZ": ("申菱环境(液冷)", "buy"),
    "000977.SZ": ("浪潮信息(算力)", "buy"),
    "601138.SH": ("工业富联(算力)", "buy"),
    "002371.SZ": ("北方华创(半导设备)", "sell"),
    "688981.SH": ("中芯国际(芯片)", "sell"),
}
def bars_for(ts):
    for _ in range(3):
        b = fetch_brze_stk_mins(ts, "5min", trade_date=DATE)
        if b: return b
        time.sleep(3)
    return []

def replay(ts, nm, side, bars):
    runv = 0.0; runv_v = 0.0; day_high = None; prev_low = None
    for i in range(len(bars)):
        b = bars[i]; c = float(b["close"]); lo = float(b["low"]); hi = float(b["high"]); v = float(b["vol"]); amt = float(b.get("amount") or 0)
        runv += amt; runv_v += v
        vwap = runv / runv_v if runv_v else c
        day_high = max(day_high or hi, hi)
        if side == "sell":
            trig = (c < vwap)
        else:
            trig = (prev_low is not None and lo <= prev_low * 0.999) or (day_high and (hi - c) / day_high >= 0.02)
        if trig:
            px = float(bars[i+1]["open"]) if i+1 < len(bars) else c
            t = bars[i+1]["time"] if i+1 < len(bars) else b["time"]
            return {"side": side, "symbol": ts, "name": nm, "time": t, "price": round(px, 2)}
        prev_low = lo
    return None

def main():
    print("==== 08-12 分钟级无未来函数 回放 ====")
    sells = []; buys = []
    for ts, (nm, side) in CAND.items():
        bars = bars_for(ts)
        if not bars:
            print("%s %s: 无分钟数据" % (ts, nm)); continue
        t = replay(ts, nm, side, bars)
        print("%s %s [%s] bars=%d -> %s" % (ts, nm, side, len(bars), t))
        if t: (sells if side == "sell" else buys).append(t)
    print("---- 成交汇总 ----")
    print("卖出(vwap_break):", sells)
    print("买入(低吸):", buys)
    print("注: 仅示意触发, 未含手续费/253-254精确条件/仓位; 用下一bar开盘=无未来。")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
