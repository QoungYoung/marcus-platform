# -*- coding: utf-8 -*-
"""bt_probe_idx_min.py — **指数分钟可得性矩阵**（指数 × 日期 × 取数源 → 根数/失败原因）。

为什么单独做这件事
------------------
`t_regime.INDEX_SYMBOLS = {hs300: sh000300, sh: sh000001, sz: sz399001}` —— 这三个指数
决定 L2（日内前哨）/L3（硬保险丝 HALT）。回测里指数分钟此前**只有 `000001.SH` 的 5min、
且只有 22 天**（`data/_bt_full/mins/000001_SH_5min_*.json`，1/5–3/20）→ 其余日子
`_index_m5_dump()` 恒 0.0。要按 1min 重放，先得把"到底哪个源能拿到哪些指数/哪些日子"钉死。

源与口径（按 `docs/datahubco/promax.txt` 官方写法）
--------------------------------------------------
  · `stk_mins`     : ts_code/freq/start_date/end_date **建议传完整时间**，`limit` 显式传（≤5000）；
                     多代码用英文逗号分隔（建议 ≤10/批）；日期范围过大按 7 天分段。
  · `idx_mins`     : 注册表里"可用"的**指数分钟**专用接口（promax.txt §10 索引）。
  · `a_share_mins` : 本地 ClickHouse 分钟历史，`freq` 用**大写**（5MIN）。
  · 腾讯 ifzq      : 生产主源 `t_data_sources.fetch_tencent_mkline`（只回"最近 N 根"，无量取历史）。
  · 新浪 sdkline   : 生产备源 `fetch_sina_minline`（同样只回最近 N 根）。

失败原因分类（parent 要求，不要把"参数传错"记成"数据不存在"）：
  `ok` / `empty`（源应答但无数据）/ `pending`（503 minute_data_pending：服务端在后台补数，**需重试**）
  / `param`（limit、invalid_params、date_range_too_large 等参数/形态错误）/ `disabled`（503 data_source_unavailable）
  / `http_xxx` / `err`

用法：
  python jobs/bt_probe_idx_min.py                        # 默认矩阵（8 指数 × 4 日期 × 全源）
  python jobs/bt_probe_idx_min.py --dates 20260311 --indices 000300.SH,000001.SH
  python jobs/bt_probe_idx_min.py --json data/_bt_full/mins1/_probe/idx_min_matrix.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as _fut
import json
import os
import re
import sys
import time


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

DEFAULT_INDICES = ["000300.SH", "000001.SH", "399001.SZ", "000905.SH",
                   "000852.SH", "399006.SZ", "000688.SH", "000016.SH"]
DEFAULT_DATES = ["20260107", "20260311", "20260601", "20260901"]
FIELDS = "ts_code,trade_time,open,high,low,close,vol,amount"


def _classify(err: str) -> str:
    e = str(err or "")
    if not e:
        return "ok"
    if "minute_data_pending" in e:
        return "pending"
    if "data_source_unavailable" in e:
        return "disabled"
    if "limit must be" in e or "invalid_params" in e or "date_range_too_large" in e:
        return "param"
    m = re.search(r"HTTP (\d{3})", e)
    if m:
        return "http_%s" % m.group(1)
    return "err"


def _ts_full(day: str, end: bool = False) -> str:
    """`20260107` → `2026-01-07 00:00:00` / `23:59:59`（文档：分钟接口建议传完整时间）。"""
    d = "%s-%s-%s" % (day[:4], day[4:6], day[6:8])
    return d + (" 23:59:59" if end else " 00:00:00")


def _norm(fields, items):
    fi = {k: i for i, k in enumerate(fields or [])}
    out = []
    for b in items or []:
        try:
            out.append({"ts_code": b[fi.get("ts_code", 0)], "time": str(b[fi.get("trade_time", 1)]),
                        "close": float(b[fi["close"]]), "low": float(b[fi["low"]]),
                        "high": float(b[fi["high"]]), "vol": float(b[fi.get("vol", 0)] or 0)})
        except Exception as _e_sil1:
            _silent_alert("bt_probe_idx_min.py:79", _e_sil1)
            continue
    out.sort(key=lambda x: x["time"])
    return out


def _relay(api, ts_code, freq, day, limit, full_time=True):
    import tushare_relay as R
    kw = {"ts_code": ts_code, "freq": freq}
    if api == "stk_mins":
        kw["start_date"] = _ts_full(day) if full_time else day
        kw["end_date"] = _ts_full(day, True) if full_time else day
    else:  # a_share_mins 只吃完整时间
        kw["start_date"] = _ts_full(day)
        kw["end_date"] = _ts_full(day, True)
    if limit:
        kw["limit"] = limit
    return R.relay_items(api, fields=FIELDS, **kw)


def probe_one(ind: str, day: str, source: str) -> dict:
    """一个 (指数, 日, 源) 的探测结果。source 形如 `stk_mins:1min:limit5000:full`。"""
    parts = source.split(":")
    api, freq = parts[0], parts[1] if len(parts) > 1 else ""
    limit = 0
    full_time = "date8" not in parts
    for p in parts:
        if p.startswith("limit"):
            limit = int(p[5:])
    row = {"index": ind, "date": day, "source": source, "n": 0, "first": None, "last": None,
           "verdict": "", "err": ""}
    t0 = time.time()
    try:
        if api in ("stk_mins", "a_share_mins", "idx_mins"):
            fields, items = _relay(api, ind, freq, day, limit, full_time)
            bars = _norm(fields, items)
            row["n"] = len(bars)
            if bars:
                row["first"], row["last"] = bars[0]["time"], bars[-1]["time"]
            row["verdict"] = "ok" if bars else "empty"
        elif api in ("tencent", "sina"):
            import app.services.t_data_sources as tds
            sym = ("sh" if ind.endswith(".SH") else "sz") + ind[:6]
            if api == "tencent":
                bars = tds.fetch_tencent_mkline(sym, freq=freq, count=limit or 320) or []
            else:
                bars = tds.fetch_sina_minline(sym, scale=int(freq.lstrip("m")), datalen=limit or 320) or []
            row["n"] = len(bars)
            if bars:
                row["first"], row["last"] = str(bars[0]["time"]), str(bars[-1]["time"])
            row["verdict"] = "ok" if bars else "empty"
        else:
            row["verdict"], row["err"] = "err", "unknown source"
    except Exception as e:
        msg = str(e)[:220]
        row["verdict"], row["err"] = _classify(msg), msg
    row["secs"] = round(time.time() - t0, 2)
    return row


def _srcs_for(api: str, freq: str) -> list:
    return ["%s:%s:limit5000:full" % (api, freq), "%s:%s:limit2000:date8" % (api, freq)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", default=",".join(DEFAULT_INDICES))
    ap.add_argument("--dates", default=",".join(DEFAULT_DATES))
    ap.add_argument("--sources", default="", help="留空=内置全矩阵")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--json", default="data/_bt_full/mins1/_probe/idx_min_matrix.json")
    ap.add_argument("--tencent-scan", type=int, default=0,
                    help=">0 时额外扫腾讯 ifzq 的可回溯深度（count=该值）")
    a = ap.parse_args()

    inds = [x.strip() for x in a.indices.split(",") if x.strip()]
    dates = [x.strip() for x in a.dates.split(",") if x.strip()]
    if a.sources:
        srcs = [x.strip() for x in a.sources.split(",") if x.strip()]
    else:
        # 官方写法（promax.txt §8.6）：分钟接口建议传**完整时间** + 显式 limit；
        # 但 datahubco 面的 `idx_mins` 实测要求 `start_date` 用 `YYYYMMDD`（HTTP 400
        # "start_date must use YYYYMMDD"）→ 两个形态都测，别把"形态错"当"没数据"。
        srcs = ["stk_mins:1min:limit5000:full", "stk_mins:5min:limit5000:full",
                "stk_mins:1min:limit2000:date8", "stk_mins:5min:limit2000:date8",
                "idx_mins:1min:limit5000:date8", "idx_mins:5min:limit5000:date8",
                "a_share_mins:1MIN:limit5000:full", "a_share_mins:5MIN:limit5000:full"]

    jobs = [(i, d, s) for i in inds for d in dates for s in srcs]
    print("[probe] %d 指数 × %d 日期 × %d 源 = %d 次探测（workers=%d）"
          % (len(inds), len(dates), len(srcs), len(jobs), a.workers), flush=True)
    rows = []
    t0 = time.time()
    with _fut.ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        futs = {ex.submit(probe_one, i, d, s): (i, d, s) for i, d, s in jobs}
        for k, f in enumerate(_fut.as_completed(futs), 1):
            try:
                r = f.result()
            except Exception as e:                      # 兜底：线程内未捕获异常
                i, d, s = futs[f]
                r = {"index": i, "date": d, "source": s, "n": 0, "verdict": "err",
                     "err": str(e)[:200], "secs": 0}
            rows.append(r)
            if k % 10 == 0 or k == len(jobs):
                print("[probe] %d/%d  %.0fs" % (k, len(jobs), time.time() - t0), flush=True)

    # ── 矩阵打印：行 = 指数×日期，列 = 源 ──────────────────────────
    rows.sort(key=lambda r: (r["index"], r["date"], r["source"]))
    print("\n%-11s %-9s | %s" % ("指数", "日期", " | ".join("%-26s" % s for s in srcs)))
    for i in inds:
        for d in dates:
            cells = []
            for s in srcs:
                r = next((x for x in rows if x["index"] == i and x["date"] == d and x["source"] == s), None)
                if r is None:
                    cells.append("%-26s" % "-")
                elif r["verdict"] == "ok":
                    cells.append("%-26s" % ("%d 根 %s" % (r["n"], str(r["first"])[11:16] + "→" + str(r["last"])[11:16])))
                else:
                    cells.append("%-26s" % (r["verdict"] + ("" if not r["err"] else ":" + r["err"][:18])))
            print("%-11s %-9s | %s" % (i, d, " | ".join(cells)))

    # ── 结论汇总：每个 (指数, 日期) 用哪个源能拿到 ─────────────────
    print("\n[probe] 各 (指数,日期) 可用源：")
    ok_map = {}
    for i in inds:
        for d in dates:
            oks = [x["source"] for x in rows if x["index"] == i and x["date"] == d and x["verdict"] == "ok"]
            ok_map["%s %s" % (i, d)] = oks
            print("  %-11s %-9s -> %s" % (i, d, ", ".join(oks) if oks else "❌ 全源失败"))
    print("[probe] 各 (指数,源) 通过率：")
    for i in inds:
        for s in srcs:
            sub = [x for x in rows if x["index"] == i and x["source"] == s]
            nok = sum(1 for x in sub if x["verdict"] == "ok")
            vd = {}
            for x in sub:
                vd[x["verdict"]] = vd.get(x["verdict"], 0) + 1
            if nok or any(v != "empty" for v in vd):
                print("  %-11s %-26s ok=%d/%d  %s" % (i, s, nok, len(sub), vd))

    if a.tencent_scan:
        print("\n[probe] 腾讯 ifzq 可回溯深度（freq=m5, count=%d）：" % a.tencent_scan)
        for ind in inds:
            r = probe_one(ind, dates[0], "tencent:m5:limit%d:full" % a.tencent_scan)
            print("  %-11s -> %s n=%s first=%s last=%s %s"
                  % (ind, r["verdict"], r["n"], r["first"], r["last"], r["err"][:60]))

    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "indices": inds, "dates": dates,
           "sources": srcs, "rows": rows, "available": ok_map}
    if a.json:
        os.makedirs(os.path.dirname(a.json), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("[probe] 矩阵已写 %s（%d 行）" % (a.json, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
