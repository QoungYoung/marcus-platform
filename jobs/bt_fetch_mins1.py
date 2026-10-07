# -*- coding: utf-8 -*-
"""bt_fetch_mins1.py — 1min（+指数 1min/5min）分钟数据抓取器 → `data/_bt_full/mins1/`。

与 `bt_fetch_mins.py`（5min、逐 (标的,日) 单发）的区别（**全部实测得来**）：
  ① **按官方写法传参**（`docs/datahubco/promax.txt` §8.6）：分钟接口传**完整时间**
     `start_date="2026-01-07 00:00:00"` / `end_date="… 23:59:59"`，并显式传 `limit`。
     之前"指数 1min 拿不到"的真相是**参数形态**：8 位日期 + 无 limit → promax `minute_data_pending`、
     datahubco `limit must be between 1 and 2000`；改官方形态后 `000300.SH` 1min 单日直接 241 根。
  ② **单代码区间拉取**（个股）：`ts_code` 多代码批量**对本网关无效**（实测 2 个代码即
     `minute_data_pending`，单代码同参数正常）→ 走"**单代码 × 90 自然日**"区间，
     一次拿 59 个交易日 × 241 根 = 14,219 根 / 2.1s，比逐日单发快 59 倍。
     代价：**不带 `limit`**（带 limit=2000 会被截断成 9 天）。
  ③ **指数必须逐日**：指数区间查询只回"服务端已就绪的那些天"且**每次不同**（实测 000300.SH 1min
     同一区间两次分别回 6 天 / 8 天）→ 指数走"逐 (指数,日)" + 多轮重试；
     `minute_data_pending` 的官方口径是"**服务端正在后台补数**，稍后重试"，不是"没有数据"。
  ④ **退避重试**：429/502/503/504 指数退避（1/2/4s，最多 2 次）；并发 ≤4（文档：不要让几十个并发重复补数）。
  ⑤ **多源兜底**：`stk_mins`（promax→datahubco 自动路由）失败后退 `a_share_mins`（freq 大写）。

产物（**只写 `--out` 目录**，默认 `data/_bt_full/mins1/`）：
    <out>/<code6>_<EX>_1min_<day>.json     1min（个股/ETF/指数）
    <out>/<code6>_<EX>_5min_<day>.json     5min（**只给指数**；个股 5min 由 1min 聚合，见 bt_local_market.py）
    形如 `{"ts_code","freq","date","bars":[[ts_code,"YYYY-MM-DD HH:MM:SS",o,h,l,c,vol,amount], …]}`
    —— 与既有 `bt_fetch_mins.py` 完全同构；**bar 升序**（既有 5min 缓存是上游降序，读取方都自己排序，
       升序是为了让"bars[-1] 即最新"这种直觉不坑人）。
    <out>/_plan.json / _stats.json / _failures.json / _pending.json

幂等：目标文件已存在且 bar 数 ≥ `--min-bars`（1min 默认 200、5min 默认 40）→ 跳过；可随时重跑续抓。

用法：
  python jobs/bt_fetch_mins1.py --plan                     # 只打印计划（不联网）
  python jobs/bt_fetch_mins1.py --workers 3               # 区间轮 + 逐日补轮（默认 rounds=2）
  python jobs/bt_fetch_mins1.py --rounds 0                # 只跑区间轮（最快）
  python jobs/bt_fetch_mins1.py --only index --workers 2  # 只补指数
  python jobs/bt_fetch_mins1.py --retry-pending           # 只重试 _pending.json（服务端补完数后再跑）
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as _fut
import glob
import json
import os
import sys
import threading
import time

sys.path[:0] = []
import bt_env  # noqa: E402


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

bt_env.add_paths()

REPO = bt_env.REPO
DEFAULT_OUT = os.path.join(REPO, "data", "_bt_full", "mins1")
LEGACY_M5 = os.path.join(REPO, "data", "_bt_full", "mins")
SUMMARY = os.path.join(REPO, "data", "_bt_year", "_summary")

DAY_LO, DAY_HI = "20260105", "20260914"
FIELDS = "ts_code,trade_time,open,high,low,close,vol,amount"

# 生产用到的三个指数（`t_regime.INDEX_SYMBOLS`：hs300/sh/sz）在前；其余"能拿就拿"（补 hs300_drop 等）
PROD_INDEX = ["000300.SH", "000001.SH", "399001.SZ"]
EXTRA_INDEX = ["000905.SH", "000852.SH", "399006.SZ", "000688.SH", "000016.SH"]

_LOCK = threading.Lock()


# ── 代码/计划 ───────────────────────────────────────────────────────
def _sym_to_ts(sym: str) -> str:
    """`SH600584` / `sh600584` / `600584.SH` → `600584.SH`。"""
    s = str(sym or "").strip().upper()
    if "." in s:
        a, _, b = s.partition(".")
        return (b + "." + a) if a in ("SH", "SZ", "BJ") else s
    for p in ("SH", "SZ", "BJ"):
        if s.startswith(p):
            return s[2:] + "." + p
    return s


def _file_name(ts: str, freq: str, day: str) -> str:
    a, _, b = ts.partition(".")
    return "%s_%s_%s_%s.json" % (a, b, freq, day)


def _prod_summary_pairs():
    out = []
    for pat in (os.path.join(SUMMARY, "prod_*.json"),
                os.path.join(SUMMARY, "_prev_run", "prod_*.json")):
        for p in sorted(glob.glob(pat)):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception as _e_sil1:
                _silent_alert("bt_fetch_mins1.py:93", _e_sil1)
                continue
            day = str(d.get("day") or os.path.basename(p)[5:13])
            syms = [x for x in (d.get("symbols") or []) if x]
            if day and syms:
                out.append((day, syms))
    return out


def _legacy_m5_pairs():
    pairs = set()
    for p in glob.glob(os.path.join(LEGACY_M5, "*_5min_*.json")):
        parts = os.path.basename(p)[:-5].split("_")
        if len(parts) >= 4:
            pairs.add(("%s.%s" % (parts[0], parts[1]), parts[3]))
    return pairs


def build_plan(include_index=True, fill_prev=True):
    sym2days = collections.defaultdict(set)
    for day, syms in _prod_summary_pairs():
        if not (DAY_LO <= day <= DAY_HI):
            continue
        for s in syms:
            ts = _sym_to_ts(s)
            if ts:
                sym2days[ts].add(day)
    n_prod = sum(len(v) for v in sym2days.values())
    n_m5_only = 0
    for ts, day in _legacy_m5_pairs():
        if not (DAY_LO <= day <= DAY_HI):
            continue
        if day not in sym2days.get(ts, ()):
            n_m5_only += 1
        sym2days[ts].add(day)
    days_all = sorted({d for v in sym2days.values() for d in v})
    idxs = (PROD_INDEX + EXTRA_INDEX) if include_index else []
    for ts in idxs:
        sym2days.setdefault(ts, set()).update(days_all)

    # 前一日补齐：`_stock_dip_prev_low`/254 腿要"前一交易日"的 bar（分组 <2 → 恒 False）
    n_prev = 0
    if fill_prev:
        for ts in list(sym2days.keys()):
            if ts in idxs:
                continue
            for d in sorted(sym2days[ts]):
                i = days_all.index(d)
                if i > 0 and days_all[i - 1] not in sym2days[ts]:
                    sym2days[ts].add(days_all[i - 1])
                    n_prev += 1
    return dict(sym2days), days_all, {"prod_pairs": n_prod, "m5_only_pairs": n_m5_only,
                                      "prev_fill_pairs": n_prev, "n_index": len(idxs)}


# ── 取数原语 ────────────────────────────────────────────────────────
_RETRYABLE = ("429", "502", "503", "504", "minute_data_pending", "timeout", "timed out",
              "Connection", "ip_rate_limited", "upstream")


def _classify(err: str) -> str:
    e = str(err or "")
    if not e:
        return "ok"
    if "minute_data_pending" in e:
        return "pending"
    if "ip_rate_limited" in e or "429" in e:
        return "rate_limited"
    if "limit must be" in e or "invalid_params" in e or "date_range_too_large" in e:
        return "param"
    if "data_source_unavailable" in e:
        return "disabled"
    return "err"


def _fmt_range(d0, d1):
    return ("%s-%s-%s 00:00:00" % (d0[:4], d0[4:6], d0[6:8]),
            "%s-%s-%s 23:59:59" % (d1[:4], d1[4:6], d1[6:8]))


def _rows_to_bars(flds, items):
    fi = {k: j for j, k in enumerate(flds or [])}
    out = collections.defaultdict(list)
    for b in items or []:
        try:
            ts = str(b[fi.get("ts_code", 0)])
            out[ts].append([ts, str(b[fi.get("trade_time", 1)]), b[fi["open"]], b[fi["high"]],
                            b[fi["low"]], b[fi["close"]], b[fi.get("vol")], b[fi.get("amount")]])
        except Exception as _e_sil2:
            _silent_alert("bt_fetch_mins1.py:181", _e_sil2)
            continue
    for k in out:
        out[k].sort(key=lambda x: str(x[1]))
    return dict(out)


def call(code, freq, d0, d1, limit=None, tries=2, quiet=False):
    """单代码取数（`stk_mins` → 失败退 `a_share_mins`）→ ({day: [bars]}, err)。"""
    import tushare_relay as R
    s0, s1 = _fmt_range(d0, d1)
    errs = []
    for api in ("stk_mins", "a_share_mins"):
        for i in range(tries):
            try:
                kw = {"ts_code": code, "start_date": s0, "end_date": s1}
                if api == "stk_mins":
                    kw["freq"] = freq
                else:
                    kw["freq"] = freq.upper()
                if limit:
                    kw["limit"] = limit
                flds, items = R.relay_items(api, fields=FIELDS, **kw)
                if items:
                    by = _rows_to_bars(flds, items)
                    bars = by.get(code) or (list(by.values())[0] if len(by) == 1 else [])
                    out = collections.defaultdict(list)
                    for b in bars:
                        out[str(b[1])[:10].replace("-", "")].append(b)
                    return dict(out), ""
                errs.append("%s:empty" % api)
                break
            except Exception as e:
                msg = str(e)[:180]
                errs.append("%s:%s" % (api, msg))
                if any(k in msg for k in _RETRYABLE) and i + 1 < tries:
                    time.sleep(1 + 2 ** i)
                    continue
                break
    if not quiet:
        with _LOCK:
            print("[mins1] %s %s %s~%s 失败：%s" % (code, freq, d0, d1, " | ".join(errs)[:220]),
                  file=sys.stderr, flush=True)
    return {}, " | ".join(errs)


def _write(out_dir, ts, freq, day, bars):
    p = os.path.join(out_dir, _file_name(ts, freq, day))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts_code": ts, "freq": freq, "date": day, "bars": bars}, f, ensure_ascii=False)
    os.replace(tmp, p)


def _have(out_dir, ts, freq, day, min_bars):
    try:
        d = json.load(open(os.path.join(out_dir, _file_name(ts, freq, day)), encoding="utf-8"))
        return len(d.get("bars") or []) >= min_bars
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--workers", type=int, default=3, help="并发（文档：别让几十个并发重复补数；建议 ≤4）")
    ap.add_argument("--chunk", type=int, default=55, help="区间轮的交易日/块（上游 >~110 自然日直接 400）")
    ap.add_argument("--rounds", type=int, default=2, help="区间轮之后的逐日补轮次数（0=不补）")
    ap.add_argument("--round-sleep", type=int, default=45, help="补轮之间的等待秒数（等服务端后台补数）")
    ap.add_argument("--min-bars", type=int, default=200, help="1min 有效下限（241 根/日）")
    ap.add_argument("--min-bars-5m", type=int, default=40, help="5min 有效下限（48~49 根/日）")
    ap.add_argument("--only", default="all", choices=("all", "stock", "index"))
    ap.add_argument("--plan", action="store_true", help="只打印计划，不联网")
    ap.add_argument("--no-fill-prev", action="store_true")
    ap.add_argument("--retry-pending", action="store_true", help="只重试 _pending.json 里的条目")
    ap.add_argument("--symbols", default="")
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    sym2days, days_all, meta = build_plan(include_index=(a.only != "stock"),
                                          fill_prev=not a.no_fill_prev)
    idxs = set(PROD_INDEX + EXTRA_INDEX)
    if a.only == "stock":
        sym2days = {k: v for k, v in sym2days.items() if k not in idxs}
    elif a.only == "index":
        sym2days = {k: v for k, v in sym2days.items() if k in idxs}
    if a.symbols:
        want = {_sym_to_ts(x) for x in a.symbols.split(",") if x.strip()}
        sym2days = {k: v for k, v in sym2days.items() if k in want}

    n_pairs = sum(len(v) for v in sym2days.values())
    print("[mins1] 计划：%d 代码 / %d 个 (代码,日) / %d 交易日（%s~%s）→ %s"
          % (len(sym2days), n_pairs, len(days_all),
             days_all[0] if days_all else "-", days_all[-1] if days_all else "-", out))
    print("[mins1] 构成：prod 摘要 %d 对 + 既有 5min 缓存新增 %d 对 + 前一日补齐 %d 对；指数 %d 个"
          % (meta["prod_pairs"], meta["m5_only_pairs"], meta["prev_fill_pairs"], meta["n_index"]))
    if a.plan:
        for ts in sorted(sym2days):
            print("  %-11s %3d 天  %s" % (ts, len(sym2days[ts]), ",".join(sorted(sym2days[ts]))[:78]))
        return 0

    # 待抓工作集（1min：全部；5min：仅指数）
    work = {}                                    # (ts, freq) -> set(days)
    for ts, days in sym2days.items():
        work[(ts, "1min")] = set(days)
        if ts in idxs:
            work[(ts, "5min")] = set(days)
    if a.retry_pending:
        pp = os.path.join(out, "_pending.json")
        filt = {(x["ts_code"], x["freq"], x["day"]) for x in json.load(open(pp, encoding="utf-8"))} \
            if os.path.exists(pp) else set()
        if not filt:
            print("[mins1] _pending.json 为空 → 无事可做")
            return 0
        work = {k: {d for d in v if (k[0], k[1], d) in filt} for k, v in work.items()}
        work = {k: v for k, v in work.items() if v}

    stat = collections.Counter()
    failures, pendings = [], []
    t0 = time.time()

    def min_bars_of(freq):
        return a.min_bars if freq == "1min" else a.min_bars_5m

    def missing_map():
        miss = {}
        for (ts, freq), days in work.items():
            m = sorted(d for d in days if not _have(out, ts, freq, d, min_bars_of(freq)))
            if m:
                miss[(ts, freq)] = m
        return miss

    def record_failure(ts, freq, day, err, nbar=0):
        k = _classify(err)
        (pendings if k == "pending" else failures).append(
            {"ts_code": ts, "freq": freq, "day": day, "bars": nbar, "reason": k, "err": err[:300]})

    # ── Phase R：单代码区间轮（个股 + 指数；一次 ~59 交易日） ─────────
    r_tasks = []
    for (ts, freq), days in work.items():
        ds = sorted(days)
        for i in range(0, len(ds), a.chunk):
            grp = ds[i:i + a.chunk]
            r_tasks.append((ts, freq, grp[0], grp[-1], set(grp)))
    print("[mins1] 区间轮：%d 个请求（chunk=%d 交易日, workers=%d）" % (len(r_tasks), a.chunk, a.workers), flush=True)

    def run_range(task):
        ts, freq, d0, d1, want = task
        by, err = call(ts, freq, d0, d1, limit=None)
        got = 0
        for d in sorted(want):
            bars = by.get(d) or []
            if len(bars) >= min_bars_of(freq):
                _write(out, ts, freq, d, bars)
                got += 1
        return ts, freq, d0, d1, got, sorted(want - {d for d in want if len(by.get(d) or []) >= min_bars_of(freq)}), err

    def run_phase(tasks, fn, tag):
        n = 0
        with _fut.ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
            for f in _fut.as_completed([ex.submit(fn, t) for t in tasks]):
                try:
                    r = f.result()
                except Exception as e:
                    with _LOCK:
                        print("[mins1] %s 批次异常：%s" % (tag, str(e)[:200]), file=sys.stderr, flush=True)
                    continue
                n += 1
                if n % 20 == 0 or n == len(tasks):
                    print("[mins1] %s %d/%d  已写 %d 文件  待补 %d  %.0fs"
                          % (tag, n, len(tasks), stat["files"], len(pendings), time.time() - t0), flush=True)
        return n

    if r_tasks:
        def _rr(task):
            ts, freq, d0, d1, want = task
            by, err = call(ts, freq, d0, d1, limit=None)
            got = sorted(d for d in want if len(by.get(d) or []) >= min_bars_of(freq))
            for d in got:
                _write(out, ts, freq, d, by[d])
            return ts, freq, d0, d1, got, err
        with _fut.ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
            n = 0
            for f in _fut.as_completed([ex.submit(_rr, t) for t in r_tasks]):
                try:
                    ts, freq, d0, d1, got, err = f.result()
                except Exception as e:
                    with _LOCK:
                        print("[mins1] R 异常：%s" % str(e)[:200], file=sys.stderr, flush=True)
                    continue
                n += 1
                stat["files"] += len(got)
                stat["req_range"] += 1
                if err and not got:
                    record_failure(ts, freq, d0 + "~" + d1, err)
                if n % 20 == 0 or n == len(r_tasks):
                    print("[mins1] R %d/%d  已写 %d 文件  %.0fs（%.0f req/分）"
                          % (n, len(r_tasks), stat["files"], time.time() - t0,
                             n / max(1e-6, time.time() - t0) * 60), flush=True)

    # ── Phase D：逐 (代码, 日) 补轮（指数为主要受益者） ──────────────
    for rd in range(1, a.rounds + 1):
        miss = missing_map()
        d_tasks = [(ts, freq, d) for (ts, freq), ds in miss.items() for d in ds]
        if not d_tasks:
            print("[mins1] 无缺口，跳过补轮 %d" % rd)
            break
        print("[mins1] 补轮 %d：%d 个 (代码,日) 缺口（workers=%d）" % (rd, len(d_tasks), a.workers), flush=True)

        def _dd(task):
            ts, freq, day = task
            by, err = call(ts, freq, day, day, limit=2000)
            bars = by.get(day) or []
            if len(bars) >= min_bars_of(freq):
                _write(out, ts, freq, day, bars)
                return ts, freq, day, True, err
            return ts, freq, day, False, err

        with _fut.ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
            n = 0
            for f in _fut.as_completed([ex.submit(_dd, t) for t in d_tasks]):
                try:
                    ts, freq, day, ok, err = f.result()
                except Exception as e:
                    with _LOCK:
                        print("[mins1] D 异常：%s" % str(e)[:200], file=sys.stderr, flush=True)
                    continue
                n += 1
                stat["req_day"] += 1
                if ok:
                    stat["files"] += 1
                else:
                    record_failure(ts, freq, day, err)
                if n % 40 == 0 or n == len(d_tasks):
                    print("[mins1] D%d %d/%d  已写 %d 文件  待补 %d  %.0fs"
                          % (rd, n, len(d_tasks), stat["files"], len(pendings), time.time() - t0), flush=True)
        if rd < a.rounds and a.round_sleep > 0:
            print("[mins1] 等 %ds 让服务端后台补数…" % a.round_sleep, flush=True)
            time.sleep(a.round_sleep)

    miss = missing_map()
    n_miss = sum(len(v) for v in miss.values())
    with open(os.path.join(out, "_failures.json"), "w", encoding="utf-8") as f:
        json.dump(failures, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "_pending.json"), "w", encoding="utf-8") as f:
        json.dump(pendings + [{"ts_code": ts, "freq": fq, "day": d, "bars": 0, "reason": "still_missing",
                               "err": ""} for (ts, fq), ds in miss.items() for d in ds],
                  f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "_plan.json"), "w", encoding="utf-8") as f:
        json.dump({"days": days_all, "meta": meta, "codes": sorted(sym2days)},
                  f, ensure_ascii=False, indent=1)
    stat.update({"still_missing": n_miss, "fail": len(failures), "pending": len(pendings),
                 "secs": round(time.time() - t0, 1)})
    with open(os.path.join(out, "_stats.json"), "w", encoding="utf-8") as f:
        json.dump(dict(stat), f, ensure_ascii=False, indent=1)
    print("[mins1] 完成：%s" % dict(stat))
    if n_miss:
        by_freq = collections.Counter("%s/%s" % (fq, ts in idxs and "index" or "stock")
                                      for (ts, fq), ds in miss.items() for _ in ds)
        print("[mins1] 仍缺 %d 个（按频/类）：%s" % (n_miss, dict(by_freq)))
        print("[mins1] 样例：%s" % [("%s %s %s" % (ts, fq, d)) for (ts, fq), ds in list(miss.items())[:6] for d in ds[:2]])
    print("[mins1] ⏳ pending=%d（minute_data_pending=服务端后台补数）→ 稍后 `--retry-pending`" % len(pendings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
