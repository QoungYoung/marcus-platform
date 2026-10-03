# -*- coding: utf-8 -*-
"""bt_fetch_mins.py — 为回测拉**分钟数据**（个股/ETF + 指数），按 (symbol, date) 落地到本地缓存。

数据源（2026-09-15 实测）：
  · 个股/ETF：`stk_mins`（promax 中继）—— 支持 1min/5min、支持 start/end 区间（119 天 4.4s）；
  · 指数：`stk_mins` 也能给指数码（如 `000001.SH`，单日 48~49 根），本地 ClickHouse `a_share_mins` 同源但只覆盖近两周。

缓存：`<out>/<ts_code>_<freq>.json` = {"ts_code":…, "freq":…, "bars": [[time, o,h,l,c,vol,amount], …]}

用法（容器内）：
  python jobs/bt_fetch_mins.py --symbols SH600039,SH600284,... --days 20260911 [--freq 5min] \
      --out /app/data/_bt_full/mins [--index 000001.SH]
  python jobs/bt_fetch_mins.py --symbols-file /app/data/_bt_full/_summary/legs_all.jsonl --days 20260909-20260914
"""
from __future__ import annotations

import os as _os, sys as _sys
# 让本工具**独立运行**也能找到仓库模块（2026-09-17 修：缺 `core/` 导致
# `import tushare_relay` 失败 → 驱动的每日 fetch 与并集回填长期静默失败 → 分钟档缺档）
_REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _d in (_REPO, _os.path.join(_REPO, "core"), _os.path.join(_REPO, "backend"),
           _os.path.join(_REPO, "apps", "paper-trading"), _os.path.join(_REPO, "apps", "main_line")):
    if _d not in _sys.path:
        _sys.path.insert(0, _d)

import argparse
import json
import os
import sys
import time

sys.path[:0] = ["/app", "/app/core"]


# ── 2026-09-20：本脚本**总是直连**，不用代理 ─────────────────────────────
# 实测：`127.0.0.1:7890` 端口在监听但上游不通 ⇒ 走代理时每次请求都要重试一轮
# （stk_mins 3 次 sleep 2+4+6=12s，再退备用源）⇒ 一天磨十几分钟、缺档标的还被静默跳过；
# 而**直连 promax 是通的**（pcd.mobcvb.cn:443 直连 OK，取 3 天 5min 仅 3.4s）。
# 驱动每天起一个新子进程 ⇒ 在本脚本入口摘掉代理变量，父进程无需重启。
# 需要彻底离线时置 `BT_MINS_OFFLINE=1`（直接返回，不再发请求）。
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("NO_PROXY", "*")
# 注意：**不要**在这里读 BT_RELAY_OFFLINE —— 回放主链设了它（防实时数据泄漏），
# 但分钟档是按 (标的, 日期) 取历史 bar，与 as-of 无关，必须允许联网。


def _mins_offline() -> bool:
    return str(os.getenv("BT_MINS_OFFLINE", "0")).strip().lower() in ("1", "true", "yes", "on")


def to_ts(sym: str) -> str:
    s = str(sym).strip().upper()
    if "." in s:
        return s
    return (s[2:8] + "." + s[:2]) if s[:2] in ("SH", "SZ") else s


# ⚠️ 账本 §9.475 ✓ 修正：**原"整进程放弃 stk_mins"是错的** ✗
#   实测（裸调 promax ✓）：「promax 恒返回 minute_data_pending」**不成立** ✗ ——
#     `GET …/pro/stk_mins?ts_code=603618.SH&freq=5min&start_date=20260324&end_date=20260324`
#     ⇒ 返回完整 5 分钟 K 线 ✓（`fields` 有无 / `freq` 大小写都不影响 ✓）
#   真因 ✓：① relay 侧 300s **缓存了失败结果** ✗ ② 本文件**一次失败就整进程不再试** ✗
#   ⇒ 改为：**每次独立尝试 ＋ 退避重试** ✓，并加**直连 promax 兜底**（绕过中继缓存 ✓）
_SKIP_STK_MINS = {"flag": False}   # 保留变量名（外部若有引用），但**不再用于跳过** ✓


def _direct_promax(ts_code: str, freq: str, day: str):
    """**直连 promax** 取分钟 ✓（绕过中继缓存 ✓；键取 `PROMAX_API_KEY` ✓，缺失时读仓库 `.env` ✓）。"""
    import json as _js, os as _os, urllib.request as _ur
    key = _os.getenv("PROMAX_API_KEY") or ""
    if not key:
        try:
            for ln in open(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), ".env"),
                           encoding="utf-8", errors="replace"):
                if ln.strip().startswith("PROMAX_API_KEY="):
                    key = ln.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            key = ""
    if not key:
        return [], []
    url = ("https://pcd.mobcvb.cn/tushare/pro/stk_mins?ts_code=%s&freq=%s&start_date=%s&end_date=%s&limit=10000"
           % (ts_code, freq, day, day))
    try:
        req = _ur.Request(url, headers={"X-API-KEY": key, "User-Agent": "marcus-bt/1.0"})
        with _ur.urlopen(req, timeout=30) as r:
            j = _js.loads(r.read().decode("utf-8", "replace"))
        d = (j or {}).get("data") or {}
        return (d.get("fields") or []), (d.get("items") or [])
    except Exception:
        return [], []


def _dedup(items, key_idx: int = 1):
    """按 (ts_code, trade_time) 去重（2026-09-19 深夜修）。

    ⚠️ 事故：`a_share_mins` 兜底对**同一根 bar 会返回 3 行完全相同的记录**（实测 20260302 SH601138：
    49 根 → 147 行，15:00/14:55…各三份），而本脚本原样落盘 ⇒ 分钟档里量能 ×3 ⇒ 依赖量比的判据
    （不追高、放量破前低、缩量破位、量能分层）全部会误判。去重键 = (ts_code, trade_time)，保序。
    """
    seen, out = set(), []
    for b in items or []:
        try:
            k = (str(b[0]), str(b[key_idx]))
        except Exception:
            out.append(b)
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(b)
    return out


def fetch(ts_code: str, freq: str, day: str, tries: int = 3):
    """先 `stk_mins`（promax，历史全）；失败再退本地 ClickHouse `a_share_mins`
    （列序不同：ts_code,trade_time,freq,open,high,low,close,vol,amount —— 必须显式对齐，
    实测数据与 stk_mins 同源、逐 bar 一致）。"""
    import tushare_relay as R
    err = ""
    _tries = tries   # 账本 §9.475 ✓：**不再整进程跳过** ✓（每次独立尝试 ✓）
    for i in range(_tries):
        try:
            flds, items = R.relay_items(
                "stk_mins", fields="ts_code,trade_time,open,high,low,close,vol,amount",
                ts_code=ts_code, freq=freq, start_date=day, end_date=day)
            if items:
                return _dedup(items, 1)      # stk_mins 路径同样去重（防御）
        except Exception as e:
            err = str(e)[:90]
            if "minute_data_pending" in err or "全部数据源失败" in err:
                pass   # 账本 §9.475 ✓：**不再置位跳过** ✗（继续退避重试 ✓，后面还有直连兜底 ✓）
        time.sleep(1 + i)
    # ── 账本 §9.475 ✓：**直连 promax 兜底**（中继失败/缓存失败时用 ✓）──────────────
    for _i in range(2):
        if str(os.getenv("WOLF_MINS_DEBUG", "0")).strip() == "1":
            print("  [mins-debug] 直连兜底 ts_code=%r freq=%r day=%r" % (ts_code, freq, day), flush=True)
        _f, _it = _direct_promax(ts_code, freq, day)
        if _it:
            try:
                _fi = {k: i for i, k in enumerate(_f or [])}
                _ts_i = _fi.get("ts_code", 0)
                _tm_i = _fi.get("trade_time", 1)
                _keys = [_fi.get(k) for k in ("open", "high", "low", "close", "vol", "amount")]
                if all(k is not None for k in _keys):
                    _out = [[r[_ts_i], r[_tm_i], freq.upper()] + [r[k] for k in _keys] for r in _it]
                    return _dedup(_out, 1)
                return _dedup(_it, 1)
            except Exception:
                return _dedup(_it, 1)
        time.sleep(1.5 * (_i + 1))

    try:
        flds, items = R.relay_items("a_share_mins", ts_code=ts_code, freq=freq.upper(),
                                    start_date="%s-%s-%s 00:00:00" % (day[:4], day[4:6], day[6:8]),
                                    end_date="%s-%s-%s 23:59:59" % (day[:4], day[4:6], day[6:8]))
        if items:
            fi = {k: i for i, k in enumerate(flds or [])}
            out = []
            for b in items:
                out.append([b[fi.get("ts_code", 0)], b[fi.get("trade_time", 1)],
                            b[fi["open"]], b[fi["high"]], b[fi["low"]], b[fi["close"]],
                            b[fi.get("vol")], b[fi.get("amount")]])
            out = _dedup(out, 1)             # ⚠️ a_share_mins 会返回三份相同 bar ⇒ 必须去重（量能×3 会毁掉量比判据）
            print("[mins] %s %s 走 a_share_mins 兜底 %d 根（去重后）" % (ts_code, day, len(out)), file=sys.stderr)
            return out
    except Exception as e2:
        err = "%s | a_share_mins: %s" % (err, str(e2)[:70])
    print("[mins] %s %s 失败: %s" % (ts_code, day, err), file=sys.stderr)
    return []


def main() -> int:
    if _mins_offline():
        print("[mins] BT_MINS_OFFLINE=1 → 跳过取数（缺档按缺失处理）", file=sys.stderr)
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--symbols-file", default="", help="jsonl，取其中 symbol 字段")
    ap.add_argument("--index", default="000001.SH")
    ap.add_argument("--days", required=True, help="YYYYMMDD 或 A-B 区间")
    ap.add_argument("--freq", default="5min")
    ap.add_argument("--out", default="/app/data/_bt_full/mins")
    a = ap.parse_args()

    syms = [x.strip() for x in a.symbols.split(",") if x.strip()]
    if a.symbols_file and os.path.exists(a.symbols_file):
        seen = set()
        for ln in open(a.symbols_file, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            s = o.get("symbol") or o.get("ts_code")
            if s and s not in seen:
                seen.add(s); syms.append(s)
    if a.index:
        syms.append(a.index)
    if "-" in a.days and len(a.days) == 17:
        d0, d1 = a.days.split("-")
        days = []
        import datetime as _dt
        x = _dt.date(int(d0[:4]), int(d0[4:6]), int(d0[6:8]))
        e = _dt.date(int(d1[:4]), int(d1[4:6]), int(d1[6:8]))
        while x <= e:
            if x.weekday() < 5:
                days.append(x.strftime("%Y%m%d"))
            x += _dt.timedelta(days=1)
    else:
        days = [a.days]
    os.makedirs(a.out, exist_ok=True)
    print("[mins] 标的 %d 个 × %d 天 × %s → %s" % (len(syms), len(days), a.freq, a.out), flush=True)
    n_new = n_bar = 0
    for sym in syms:
        ts = to_ts(sym)
        for d in days:
            path = os.path.join(a.out, "%s_%s_%s.json" % (ts.replace(".", "_"), a.freq, d))
            if os.path.exists(path):
                continue
            bars = fetch(ts, a.freq, d)
            if not bars:
                continue
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ts_code": ts, "freq": a.freq, "date": d,
                           "bars": [list(b) for b in bars]}, f, ensure_ascii=False)
            n_new += 1; n_bar += len(bars)
            print("[mins] %-11s %s  %d 根" % (ts, d, len(bars)), flush=True)
    print("[mins] 完成：新写 %d 个文件 / %d 根 bar" % (n_new, n_bar))
    return 0


if __name__ == "__main__":
    sys.exit(main())
