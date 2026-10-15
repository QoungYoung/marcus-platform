# -*- coding: utf-8 -*-
"""sync_index_daily_multi.py — 多指数日线缓存（供「三大指数同步」判据用）。

为什么不用 datahubco：实测（2026-10-15）relay_query('index_daily') 对 399001.SZ/399006.SZ/000300.SH/000852.SH
**只返回到 2026-08-03**（滞后 2 个多月 ✗）。东财 push2his 与上证同一个源、能取到今天 ✓，故：
  ① 首选东财 kline（klt=101 日线 / fqt=1 前复权）；
  ② 失败再退 tushare 中继（datahubco→promax）；
  ③ 输出与 wave_ladder 同格式：data/index_daily_<code>.json = {"code","src","rows":[[date,close,high,amount]]} ✓
（wave_ladder.load_rows 直接能读 ✓）

用法::  python jobs/sync_index_daily_multi.py [--days 900] [--only 399001.SZ]
"""
import argparse, json, os, sys, time, urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
DATA = os.environ.get("DATA_DIR", "data")
CODES = ["000001.SH", "399001.SZ", "399006.SZ", "000300.SH", "000852.SH"]


def em_secid(code: str) -> str:
    num, mkt = code.split(".")
    return ("1." if mkt == "SH" else "0.") + num


def fetch_em(code: str, days: int):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
           "&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt=%d"
           % (em_secid(code), days))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    j = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore"))
    kl = ((j.get("data") or {}).get("klines")) or []
    rows = []
    for line in kl:
        p = line.split(",")
        rows.append([p[0].replace("-", ""), float(p[2]), float(p[3]), float(p[6])])   # date, close, high, amount
    return rows


def fetch_relay(code: str, days: int):
    from tushare_relay import relay_query
    end = time.strftime("%Y%m%d")
    start = time.strftime("%Y%m%d", time.localtime(time.time() - days * 86400 * 1.6))
    df = relay_query("index_daily", ts_code=code, start_date=start, end_date=end)
    rows = []
    for _, r in (df.iterrows() if df is not None else []):
        rows.append([str(r["trade_date"]), float(r["close"]), float(r.get("high") or r["close"]), float(r.get("amount") or 0)])
    return sorted(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=900)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    codes = [args.only] if args.only else CODES
    os.makedirs(DATA, exist_ok=True)
    ok = 0
    for code in codes:
        rows, src = [], ""
        try:
            rows, src = fetch_em(code, args.days), "eastmoney:push2his"
        except Exception as e:
            print("  %-12s 东财失败(%s) → 退中继" % (code, type(e).__name__))
        if not rows:
            try:
                rows, src = fetch_relay(code, args.days), "tushare_relay"
            except Exception as e:
                print("  %-12s 中继也失败：%s %s" % (code, type(e).__name__, str(e)[:80]))
                continue
        if not rows:
            continue
        p = os.path.join(DATA, "index_daily_%s.json" % code.replace(".", "_"))
        json.dump({"code": code, "src": src, "rows": rows}, open(p + ".tmp", "w", encoding="utf-8"),
                  ensure_ascii=False)
        os.replace(p + ".tmp", p)
        ok += 1
        print("  %-12s n=%-4d %s → %s（src=%s）" % (code, len(rows), rows[0][0], rows[-1][0], src))
    print("写成功 %d/%d → %s" % (ok, len(codes), DATA))
    if ok < len(codes):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
