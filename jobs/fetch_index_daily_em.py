# -*- coding: utf-8 -*-
"""fetch_index_daily_em.py — 取**指数日线**（东财公开接口）→ `data/index_daily_<code>.json`

为什么需要（2026-10-14 用户「把口径对齐」⇒ 做下去）：
  `wolf_line_regime`（生产 ⑫「白线在上 ∧ 缩量 ⇒ 当日没有买点」）的输入是
  **黄白线**（黄=等权/小盘、白=加权/大盘）＋ **指数缩量**。生产走实时/中继 ✓，
  但**回测必须 as-of** ✗：`wolf_index_breadth.huang_bai()` 只有实时取数（腾讯 qt ✗ 无中继分支），
  `index_amount_cache_*.json` 是全局缓存（含最新日期 ✗）⇒ 直接搬进臂里会灌入**未来数据** ✗。
  而库内只有上证（`data/指数数据/index_daily/000001.SH.*` ✓）、**没有中证1000** ✗ ⇒ 指数对算不出 ✓。

数据源：东财 `push2his` 日线（`klt=101` ✓ 公开、无需 key ✓；relay 的 brze key 已过期 ✗）。
输出（**纯历史、按日期过滤即 as-of** ✓）：
  data/index_daily_<CODE>.json = {"code":…, "rows": [["YYYYMMDD", close, pct_chg, amount], …]}

用法：.venv/bin/python jobs/fetch_index_daily_em.py [--codes 000001.SH,000852.SH] [--start 20241101]
"""
from __future__ import annotations
import argparse, json, os, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UA = {"User-Agent": "Mozilla/5.0"}
_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
        "?secid=%s&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
        "&klt=101&fqt=1&beg=%s&end=%s")


def _secid(code: str) -> str:
    """`000001.SH` → `1.000001`（东财：沪=1 / 深=0）。"""
    num, _, ex = str(code).partition(".")
    return ("1." if ex.upper() == "SH" else "0.") + num


def fetch(code: str, start: str, end: str) -> list:
    url = _URL % (_secid(code), start, end)
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=25) as r:
        d = json.loads(r.read().decode("utf-8", "replace")) or {}
    kl = ((d.get("data") or {}).get("klines") or [])
    rows = []
    for line in kl:
        p = str(line).split(",")
        if len(p) < 7:
            continue
        try:
            rows.append([p[0].replace("-", ""), float(p[2]), float(p[3]), float(p[6])])
        except Exception:
            continue
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="000001.SH,000852.SH")
    ap.add_argument("--start", default="20241101")
    ap.add_argument("--end", default="20261231")
    a = ap.parse_args()
    rc = 0
    for code in [c.strip() for c in a.codes.split(",") if c.strip()]:
        try:
            rows = fetch(code, a.start, a.end)
            if not rows:
                print("  %-11s ✗ 无数据" % code, file=sys.stderr)
                rc = 1
                continue
            fn = os.path.join(ROOT, "data", "index_daily_%s.json" % code.replace(".", "_"))
            with open(fn, "w", encoding="utf-8") as f:
                json.dump({"code": code, "src": "eastmoney:push2his", "rows": rows}, f)
            print("  %-11s ✓ %d 行 → %s（%s ~ %s）"
                  % (code, len(rows), os.path.basename(fn), rows[0][0], rows[-1][0]), flush=True)
        except Exception as e:
            print("  %-11s ✗ %s" % (code, str(e)[:110]), file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
