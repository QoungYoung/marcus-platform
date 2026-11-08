# -*- coding: utf-8 -*-
"""重建 A-J 窗口回测输入（含日线 + 15min + 5min 盘中位）。"""
import json, sqlite3, random, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services import point_engine as PE
from app.services.intraday_levels import compute_intraday_levels

CASES = [
 ("A","20250620","2025-06-20","2025-06-19","09:45","intraday","破位后等反抽到某个带卖出；不是跌破止损。"),
 ("B","20251121","2025-11-21","2025-11-20","14:55","tail_confirm","收盘跌破某个关键结构位则减仓；盘中不算。"),
 ("C","20251124","2025-11-24","2025-11-21","14:55","tail_confirm","跌破趋势后，若没有站回某个位，则减仓。"),
 ("D","20251203","2025-12-03","2025-12-02","14:55","tail_confirm","收盘没站上/跌破关键位则分档减仓；站回则加回。"),
 ("E","20250828","2025-08-28","2025-08-27","14:55","tail_confirm","收盘前不到某个收盘位，则减出来；缩量不急。"),
 ("F","20251118","2025-11-18","2025-11-17","14:55","tail_confirm","尾盘若没有站回某个区间，则保持/减仓。"),
 ("G","20251211","2025-12-11","2025-12-10","14:00","intraday","三买结构；前低跌破后3个15min不收回，等反抽减仓。"),
 ("H","20260114","2026-01-14","2026-01-13","14:00","intraday","已按4161减过一次；再破盘中位+放量+3个15min确认后继续下调。"),
 ("I","20250904","2025-09-04","2025-09-03","14:30","intraday","2:30定点；若跌破某个位，确认跌破的资金会在这里割肉，跌破则止损。"),
 ("J","20250801","2025-08-01","2025-07-31","13:15","intraday","盘中若快速放量跌破某个关口，直接减半；不是等收盘。"),
]

def main():
    D = json.load(open(ROOT / "data/index_daily_000001.json", encoding="utf-8"))
    D.sort(key=lambda r: str(r["trade_date"]))
    B = [{"trade_date": str(r["trade_date"]), "open": float(r["open"]), "high": float(r["high"]),
          "low": float(r["low"]), "close": float(r["close"]), "vol": float(r.get("vol") or 0)} for r in D]
    conn = sqlite3.connect(ROOT / "data/index_5m.db")
    out = []
    for cid, day8, day_iso, prev_iso, cutoff, phase, ctx in CASES:
        i = next(j for j, b in enumerate(B) if b["trade_date"] == day8)
        rows = list(conn.execute("select trade_time,open,high,low,close,vol from index_bars_5m "
                                 "where ts_code='000001.SH' and (trade_time like ? or trade_time like ?) "
                                 "and trade_time<=? order by trade_time",
                                 (prev_iso + "%", day_iso + "%", day_iso + " " + cutoff + ":00")))
        b5 = [{"trade_time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "vol": r[5]} for r in rows]
        today5 = [x for x in b5 if x["trade_time"][:10] == day_iso]
        prov = {"trade_date": day8, "open": today5[0]["open"], "high": max(x["high"] for x in today5),
                "low": min(x["low"] for x in today5), "close": today5[-1]["close"],
                "vol": sum(x["vol"] for x in today5)}
        res = PE.compute_points(B[:i] + [prov], asof=day8, domain="index", symbol="000001.SH")
        cands = []
        for p in res["points"]:
            cands.append({"id": p.get("level_id") or p.get("type", "") + "_" + str(p["price"]),
                          "type": p["type"], "price": round(float(p["price"]), 2),
                          "date": p.get("asof") or day8, "timeframe": "daily",
                          "confluence": p.get("confluence"), "state": p.get("state"),
                          "distance_pct": p.get("distance_pct")})
        for p in compute_intraday_levels(b5, day_iso, prov["close"]):
            cands.append({"id": "m15_" + p["type"] + "_" + str(p["price"]).replace(".", "_"),
                          "type": p["type"], "price": p["price"], "date": p["time"], "timeframe": "m15",
                          "confluence": 1, "state": p["state"],
                          "distance_pct": round((p["price"] - prov["close"]) / prov["close"] * 100, 2)})
        random.Random(107).shuffle(cands)
        recent = [[b["trade_date"], round(b["open"], 2), round(b["high"], 2), round(b["low"], 2),
                   round(b["close"], 2), round(b["vol"], 0)] for b in B[max(0, i - 59):i] + [prov]]
        ma = {}
        for n in (5, 13, 20, 34, 60, 144, 200):
            if len(recent) >= n:
                ma[str(n)] = round(sum(x[4] for x in recent[-n:]) / n, 2)
        out.append({"case_id": cid, "asof": day8, "phase": phase, "decision_time": cutoff,
                    "data_cutoff": cutoff, "is_final": False, "last_price": round(prov["close"], 2),
                    "today_ohlc": {"open": round(prov["open"], 2), "high": round(prov["high"], 2),
                                   "low": round(prov["low"], 2), "last": round(prov["close"], 2)},
                    "same_day_executable": True,
                    "execution_window": (["14:55", "15:00"] if phase == "tail_confirm" else ["now"]),
                    "confirmation": ("close_proxy" if phase == "tail_confirm" else "intraday"),
                    "data_warning": "今天未收盘；today_low/high 只到 %s，不是全天最低/最高" % cutoff,
                    "setup_context": ctx, "ma": ma, "recent_bars": recent, "candidates": cands})
        print(cid, phase, cutoff, "cands", len(cands))
    json.dump(out[:5], open(ROOT / "data/backtest_windows_A_E.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(out[5:], open(ROOT / "data/backtest_windows_F_J.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
