# -*- coding: utf-8 -*-
"""build_breadth_history.py — 逐日「涨跌家数」历史缓存（可中断续跑 ✓）

为什么：10-15 的"涨占比 7.8%（430:5108）"是**同步指标** ✗，用户要的是**提前预警** ✓。
  唯一能提前的是**广度背离**（指数还在高位，广度已持续走弱 ✓，本次实证领先约 15 个交易日 ✓）。
  要检验它必须先有逐日广度 ✓（平台 market_diagnosis 表已过期 95 天 ✗）⇒ 用 relay daily(trade_date) 自建 ✓。

用法：python jobs/build_breadth_history.py [--start 2025-10-01] [--chunk 150]
  · 缓存 data/_breadth_daily.json = {"YYYYMMDD": [上涨家数, 下跌家数, 平盘家数]} ✓
  · 每次最多抓 chunk 个缺日（默认 150 ✓ 约 1~3 分钟），可重复运行补齐 ✓（已抓的不重抓 ✓）
"""
import argparse, datetime as dt, json, os, sys, time
sys.path.insert(0, "core")

CACHE = "data/_breadth_daily.json"


def load_cache():
    try:
        return json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-06-01")
    ap.add_argument("--end", default=dt.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--chunk", type=int, default=150)
    a = ap.parse_args()
    from tushare_relay import relay_query
    sys.path.insert(0, "apps/main_line")
    import wave_ladder as W
    trade_days = [r[0] for r in W.load_rows() if a.start.replace("-", "") <= r[0] <= a.end.replace("-", "")]
    cache = load_cache()
    todo = [d for d in trade_days if d not in cache]
    print("交易日 %d 个，已缓存 %d，待抓 %d（本次最多 %d）" % (len(trade_days), len(trade_days) - len(todo), len(todo), a.chunk))
    t0 = time.time()
    for d in todo[:a.chunk]:
        try:
            df = relay_query("daily", trade_date=d)
            up = int((df["pct_chg"] > 0).sum())
            dn = int((df["pct_chg"] < 0).sum())
            fl = int(len(df) - up - dn)
            cache[d] = [up, dn, fl]
        except Exception as e:
            print("   %s 失败 %s %s" % (d, type(e).__name__, str(e)[:60]))
            continue
        if len(cache) % 40 == 0:
            json.dump(cache, open(CACHE, "w", encoding="utf-8"))
            print("   ... %s 已抓 %d/%d（%.1f 分钟）" % (d, len([x for x in trade_days if x in cache]), len(trade_days), (time.time() - t0) / 60))
    json.dump(cache, open(CACHE, "w", encoding="utf-8"))
    done = [x for x in trade_days if x in cache]
    print("缓存 %d 个交易日：%s → %s（用时 %.1f 分钟）" % (len(done), done[0] if done else "-", done[-1] if done else "-", (time.time() - t0) / 60))
    if len(done) < len(trade_days):
        print("还有 %d 个待抓 ⇒ 再跑一次本命令即可续跑 ✓" % (len(trade_days) - len(done)))


if __name__ == "__main__":
    main()
