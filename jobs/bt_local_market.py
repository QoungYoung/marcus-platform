# -*- coding: utf-8 -*-
"""bt_local_market.py — **1min 版本地市场库（真 m1）**：让生产代码吃到真分钟线，而不是拿 m5 冒充 m1。

背景（为什么要这个文件）
------------------------
`jobs/bt_prod_run.py::LocalMarket` 只有一档粒度（`data/_bt_full/mins/` 的 **5min** 缓存），
而生产 `TMonitor` 内部有**多处按 m1 取数**：
  · `t_monitor.stabilize_not_new_low_at()` → `fetch_minute_bars(sym, freq="m1", count=120)`
  · `t_monitor._build_minute_snapshot()` → m1（low_today/last_close/bounce）+ m5（MA/T1/T出）
  · `t_pool`（m1 count=480）/ `t_build`（m1 count=120）
此前替身只能"m5 降级喂 m1"（`install_data_shims` 把 m1/m5 都指向 `market.mkline`）→ m1 语义是假的。
本文件提供 `LocalMarket1m`：**m1 走真 1min 缓存（`data/_bt_full/mins1/`），m5 由 1min 按上游口径聚合**。

上游口径（实测钉死，见 `jobs/bt_check_mins1.py`）
----------------------------------------------
1min bar 的时间戳是**分钟起点**（09:30…11:30, 13:01…15:00，241 根/日）；上游 5min bar 是
**区间终点**：`09:30` 单独一根（只含 09:30 那一分钟），09:31~09:35 → `09:35`，…，14:56~15:00 → `15:00`
（一天 **49 根**）。实测用这个口径聚合出来的 5min 与上游 5min **逐 bar 一致（vol 到最后一股）**，
只有 amount 有 ±几十元的聚合尾数差。

数据脏（上游 stk_mins 1min 的三种脏，`bt_check_mins1.py` 会统计）
---------------------------------------------------------------
  · 同 `trade_time` **重复行**（实测值完全相同）→ 去重；
  · **盘外行**：09:25~09:29（盘前）与 15:01~15:30+（把 15:00 快照重复贴几十行）→ 丢弃；
  · 午休行（11:31~12:59）→ 丢弃。
本模块在 load 时统一清洗，并把清洗量记在 `self.dirty`（供报告/审计）。

时间戳口径（**踩过的坑**）
--------------------------
交给生产代码时统一 `YYYYMMDDHHMM`（12 位）——`t_monitor._stock_dip_prev_low` 用 `str(time)[:8]`
分交易日；给带分隔符的 `2026-03-20 09:35:00` 会让 `[:8]` = `2026-03-` → 分组 <2 → **254 腿永不触发**。
（注意：`stabilize_not_new_low_at` 反过来用 `startswith("%Y-%m-%d")`，12 位下恒 False → `m1.bounce`
恒 True——**这是生产既有口径**，1min 模式与生产主源一致；详见 bt_diff_m1_m5.py 的对照。）

用法
----
    import bt_local_market as blm
    market = blm.build_market(day, mins1="data/_bt_full/mins1", mins="data/_bt_full/mins",
                              bars_db="data/_bt_full/bars.sqlite", mode="1m")
    hhmm_ref = {"hhmm": "09:31"}
    blm.install_data_shims(market, hhmm_ref)      # 频率感知版（m1 真 m1、m5 真 m5）
    ...
    market.write_recent_sync(day_dir, symbols, "09:31")

**接入 `bt_prod_run.py` 的精确切换点见 `jobs/bt_run_1m.py` 头注释（3 行 diff）。**
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

sys.path[:0] = []
import bt_env  # noqa: E402



def _atomic_json_dump(payload, path):
    """★ 账本 §9.628 ✓：**原子写** ✗（用户报 `Extra data` ✓）

    背景 ✓：`t_monitor` 读 `recent_sync/<code6>.json` 时会报
      `JSONDecodeError: Extra data: line 1 column N` ✗ —— 因为写方
      `open(p, "w") + json.dump(...)` **不是原子的** ✗，读者能读到**半截** ✗。
    做法 ✓：写同目录临时文件 ⇒ `os.replace`（同分区 ⇒ 原子 ✓）⇒ 读者只会看到
      **旧版本或新版本**，不会是半截 ✓。与 `bt_prod_run.py:194-196` 既有范式一致 ✓。
    """
    import json as _j
    import os as _o
    tmp = "%s.tmp.%d" % (path, _o.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as _f:
            _j.dump(payload, _f)
        _o.replace(tmp, path)
    except Exception as _e:
        try:
            _o.remove(tmp)
        except Exception as _e2:
            print("[atomic] 清理临时文件失败: %s" % str(_e2)[:60], flush=True)
        raise

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
DEFAULT_MINS1 = os.path.join(REPO, "data", "_bt_full", "mins1")
DEFAULT_MINS5 = os.path.join(REPO, "data", "_bt_full", "mins")
DEFAULT_BARS_DB = os.path.join(REPO, "data", "_bt_full", "bars.sqlite")

MODE_1M = "1m"
MODE_5M = "5m"

# 5min 结束刻度（上游口径；与 bt_prod_run.BAR_MINUTES 同一张表）
M5_LABELS = ("09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:10", "10:15", "10:20",
             "10:25", "10:30", "10:35", "10:40", "10:45", "10:50", "10:55", "11:00", "11:05", "11:10",
             "11:15", "11:20", "11:25", "11:30", "13:05", "13:10", "13:15", "13:20", "13:25", "13:30",
             "13:35", "13:40", "13:45", "13:50", "13:55", "14:00", "14:05", "14:10", "14:15", "14:20",
             "14:25", "14:30", "14:35", "14:40", "14:45", "14:50", "14:55", "15:00")

# 1min 回放网格（= 1min bar 的**标签**，241 根/日）：与既有 5min 网格同构——
#   时钟 hhmm = 最新**纳入**的 bar 的标签，`bars_upto(hhmm)` 取 `label <= hhmm`。
#   （严格无前视的变体见 `strict_lookahead=True` / `BAR_MINUTES_1M_STRICT`）
BAR_MINUTES_1M = ("09:30",) + tuple(
    "%02d:%02d" % (h, m) for h in (9, 10, 11) for m in range(60)
    if "09:30" < "%02d:%02d" % (h, m) <= "11:30") + tuple(
    "%02d:%02d" % (h, m) for h in (13, 14, 15) for m in range(60)
    if "13:00" < "%02d:%02d" % (h, m) <= "15:00")
def _make_strict_grid():
    """时钟网格：09:31…11:31, 13:01…15:01（= 每根 1min bar 的**完成时刻**）。"""
    out = []
    for h in (9, 10, 11):
        for m in range(60):
            hm = "%02d:%02d" % (h, m)
            if "09:31" <= hm <= "11:31":
                out.append(hm)
    for h in (13, 14, 15):
        for m in range(60):
            hm = "%02d:%02d" % (h, m)
            if "13:01" <= hm <= "15:01":
                out.append(hm)
    return tuple(out)


BAR_MINUTES_1M_STRICT = _make_strict_grid()


# ── 工具（与 bt_prod_run 同口径，避免跨文件依赖它——那个文件另有人在改） ──
def _code6(symbol: str) -> str:
    return "".join(ch for ch in str(symbol) if ch.isdigit())[:6]


def _sym(symbol: str) -> str:
    """`sh000001` / `SH600410` / `600410.SH` → 统一 `SH600410` 形态。"""
    s = str(symbol or "").strip().upper()
    if "." in s:
        a, _, b = s.partition(".")
        s = b + a
    for p in ("SH", "SZ", "BJ"):
        if s.startswith(p):
            return s
    if s.isdigit():
        return ("SH" if s[0] == "6" else ("BJ" if s[:2] in ("43", "83", "87", "92") else "SZ")) + s
    return s


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _norm_bar(b) -> dict:
    """原始行 → dict（两种格式都吃；指数上游只给 close 时用 close 兜 OHLC）。"""
    if isinstance(b, dict):
        d = dict(b)
        c = _num(d.get("close"))
        for k in ("open", "high", "low"):
            if _num(d.get(k)) is None:
                d[k] = c
        d["vol"] = _num(d.get("vol")) or 0.0
        d["amount"] = _num(d.get("amount")) or 0.0
        return d
    try:
        t, o, h, l, c, v, amt = b[1], _num(b[2]), _num(b[3]), _num(b[4]), _num(b[5]), b[6], b[7]
        if c is None:
            return {"time": str(t), "open": None, "high": None, "low": None, "close": None,
                    "vol": _num(v) or 0.0, "amount": _num(amt) or 0.0}
        return {"time": str(t), "open": o if o is not None else c, "high": h if h is not None else c,
                "low": l if l is not None else c, "close": c,
                "vol": _num(v) or 0.0, "amount": _num(amt) or 0.0}
    except (ValueError, IndexError, TypeError):
        return {"time": ""}


_IN_SESSION = lambda hm: ("09:30" <= hm <= "11:30") or ("13:00" <= hm <= "15:00")   # noqa: E731


def _clean_1min(bars: List[dict]) -> (List[dict], Dict[str, int]):
    """去重 + 只留盘中 + 升序。返回 (bars, 清洗统计)。"""
    seen, out, stat = set(), [], {"dup": 0, "out_of_session": 0, "null_close": 0}
    for b in bars:
        t = str(b.get("time") or "")
        hm = t[11:16]
        if len(t) < 16 or not _IN_SESSION(hm):
            stat["out_of_session"] += 1
            continue
        if hm in seen:
            stat["dup"] += 1
            continue
        if b.get("close") in (None, ""):
            stat["null_close"] += 1
            continue
        seen.add(hm)
        out.append(b)
    out.sort(key=lambda b: str(b.get("time")))
    return out, stat


def agg_5min(bars: List[dict]) -> List[dict]:
    """1min（分钟起点标签）→ 上游口径 5min（区间终点标签，49 根/日）。"""
    return agg_nmin(bars, 5, M5_LABELS)


def agg_nmin(bars: List[dict], minutes: int, labels=None) -> List[dict]:
    """通用聚合：每分钟归到"**大于等于**它的第一个 N 分钟刻度"；09:30 单独一根（5min 口径）。

    `labels` 为 None 时按 `minutes` 自动生成（09:30 单根 + 09:30+minutes … 15:00，跳过午休）。
    """
    if labels is None:
        lab, t = [], 9 * 60 + 30 + minutes
        while t <= 11 * 60 + 30:
            lab.append("%02d:%02d" % (t // 60, t % 60)); t += minutes
        t = 13 * 60 + minutes
        while t <= 15 * 60:
            lab.append("%02d:%02d" % (t // 60, t % 60)); t += minutes
        labels = tuple(lab)
    buck: Dict[str, List[dict]] = {}
    for b in bars:
        hm = str(b.get("time"))[11:16]
        if hm == "09:30" and minutes == 5:
            slot = "09:30"
        else:
            slot = next((L for L in labels if hm <= L), None)
        if slot:
            buck.setdefault(slot, []).append(b)
    out = []
    for slot in sorted(buck):
        g = buck[slot]
        day = str(g[0].get("time"))[:10]
        out.append({"time": "%s %s:00" % (day, slot),
                    "open": g[0]["open"], "high": max(x["high"] for x in g),
                    "low": min(x["low"] for x in g), "close": g[-1]["close"],
                    "vol": sum(x["vol"] for x in g), "amount": sum(x["amount"] for x in g)})
    return out


class LocalMarket1m:
    """1min 本地库 + 由 1min 聚合出的 5min（缺失时才回退到既有 5min 文件）。"""

    def __init__(self, mins1_dir: str, mins_dir: str, bars_db: str, day: str, mode: str = MODE_1M,
                 prev_days: int = 45, sync_prev: str = "daily", strict_lookahead: bool = False,
                 primary: str = "1", m5_agg: bool = True):
        self.mins1_dir = mins1_dir
        self.mins_dir = mins_dir
        self.bars_db = bars_db
        self.day = day
        self.mode = mode
        self.primary = str(primary)          # 主频："1" = 1min 模式；"5" = 5min 基线（对照用）
        self.m5_agg = bool(m5_agg)           # m5 是否由 1min 聚合（False = 只用 5min 文件）
        self.prev_days = int(prev_days)
        self.sync_prev = sync_prev            # "daily"（前几日聚合成一根日线，省 5~25× I/O）| "full"
        self.strict_lookahead = bool(strict_lookahead)
        self._m1: Dict[str, Dict[str, List[dict]]] = {}
        self._m5: Dict[str, Dict[str, List[dict]]] = {}
        self._daily_bars: Dict[str, List[dict]] = {}
        self._daily: Dict[str, List[dict]] = {}
        self.missing: set = set()
        self.dirty: Dict[str, Dict[str, int]] = {}
        self.agg_source: Dict[str, str] = {}      # (sym,day) → "agg1m" / "file5m" / "legacy5m"

    # ── 装载 ─────────────────────────────────────────────────────
    def _paths(self, symbol: str, freq: str):
        sym = _sym(symbol)
        code, ex = _code6(sym), sym[:2]
        out = glob.glob(os.path.join(self.mins1_dir, "%s_%s_%s_*.json" % (code, ex, freq)))
        if freq == "5min":
            out += glob.glob(os.path.join(self.mins_dir, "%s_%s_5min_*.json" % (code, ex)))
        return sorted(out)

    def load_symbol(self, symbol: str) -> Dict[str, List[dict]]:
        """真 m1：{交易日: [1min bars]}（已清洗，升序）。"""
        symbol = _sym(symbol)
        if symbol in self._m1:
            return self._m1[symbol]
        out: Dict[str, List[dict]] = {}
        for p in self._paths(symbol, "1min"):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception as _e_sil1:
                _silent_alert("bt_local_market.py:256", _e_sil1)
                continue
            dd = str(d.get("date") or "")
            raw = [_norm_bar(b) for b in (d.get("bars") or [])]
            bars, stat = _clean_1min(raw)
            if stat["dup"] or stat["out_of_session"]:
                self.dirty[os.path.basename(p)] = stat
            if dd and bars:
                out[dd] = bars
        self._m1[symbol] = out
        if not out:
            self.missing.add(symbol)
        return out

    def load_m5(self, symbol: str) -> Dict[str, List[dict]]:
        """m5：优先 1min 聚合（上游口径），缺失日回退 mins1 的 `_5min_` 文件、再回退既有 5min 缓存。"""
        symbol = _sym(symbol)
        if symbol in self._m5:
            return self._m5[symbol]
        out: Dict[str, List[dict]] = {}
        if self.m5_agg:
            for day, bars in self.load_symbol(symbol).items():
                out[day] = agg_5min(bars)
                self.agg_source[(symbol, day)] = "agg1m"
        files5 = {}
        for p in self._paths(symbol, "5min"):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception as _e_sil2:
                _silent_alert("bt_local_market.py:284", _e_sil2)
                continue
            dd = str(d.get("date") or "")
            bars = [_norm_bar(b) for b in (d.get("bars") or [])]
            bars = [b for b in bars if b.get("close") not in (None, "")]
            bars.sort(key=lambda b: str(b.get("time")))
            if dd and bars:
                files5.setdefault(dd, (p, bars))
        for day, (p, bars) in files5.items():
            if day not in out:
                out[day] = bars
                self.agg_source[(symbol, day)] = ("file5m" if self.mins1_dir in p else "legacy5m")
        self._m5[symbol] = out
        return out

    def daily_bars_db(self, symbol: str) -> List[dict]:
        """≤ 该交易日**之前**的日线（bars.sqlite；与 bt_prod_run.LocalMarket.daily 同口径）。"""
        symbol = _sym(symbol)
        if symbol in self._daily:
            return self._daily[symbol]
        ts = _code6(symbol) + "." + symbol[:2]
        rows = []
        try:
            c = sqlite3.connect(self.bars_db)
            cur = c.execute("SELECT trade_date, open, high, low, close, vol, amount, turnover_rate "
                            "FROM bars WHERE ts_code=? AND trade_date < ? ORDER BY trade_date", (ts, self.day))
            rows = [{"date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4],
                     "vol": r[5], "amount": r[6], "turnover_rate": r[7]} for r in cur.fetchall()]
            c.close()
        except Exception as e:
            print("[market1m] 日线取数失败 %s: %s" % (symbol, str(e)[:80]), file=sys.stderr)
        self._daily[symbol] = rows
        return rows

    # 兼容旧名
    daily = daily_bars_db

    # ── 每根 bar 的状态 ──────────────────────────────────────────
    def _cut(self, hhmm: str) -> str:
        """纳入上界：默认 `label <= hhmm`（与既有 5min 网格同构）；strict 时 `label < hhmm`。"""
        return hhmm

    def _keep(self, label_hm: str, hhmm: str) -> bool:
        return label_hm < hhmm if self.strict_lookahead else label_hm <= hhmm

    def _primary_by_day(self, symbol: str) -> Dict[str, List[dict]]:
        """主频的 {交易日: bars}（1min 模式 = 真 m1；5min 基线 = 5min 文件）。"""
        return self.load_symbol(symbol) if self.primary == "1" else self.load_m5(symbol)

    def bars_upto(self, symbol: str, hhmm: str) -> List[dict]:
        """当日主频 bars（≤ hhmm）。"""
        day = self._primary_by_day(symbol).get(self.day) or []
        return [b for b in day if self._keep(str(b.get("time"))[11:16], hhmm)]

    def bars_upto_m5(self, symbol: str, hhmm: str) -> List[dict]:
        day = self.load_m5(symbol).get(self.day) or []
        return [b for b in day if self._keep(str(b.get("time"))[11:16], hhmm)]

    def series(self, symbol: str, freq: str, hhmm: str, count: int = 0) -> List[dict]:
        """跨日升序序列（≤ hhmm），取末 `count` 根（count<=0 = 全部）。freq: m1/m5/m15/m30/m60。"""
        f = str(freq or "m5").lower().lstrip("m")
        f = {"1min": "1", "5min": "5", "15min": "15", "30min": "30", "60min": "60", "m1": "1"}.get(f, f)
        if self.primary == "5" and f in ("1", "5"):
            by_day = self.load_m5(symbol)      # 5min 基线的旧口径：m1 请求也用 m5 顶（降级冒充）
        elif f == "1":
            by_day = self.load_symbol(symbol)
        else:
            by_day = self.load_m5(symbol)
        out = []
        for d, bs in sorted(by_day.items()):
            # ⚠️ **这一行是修 look-ahead 的关键**：`bt_prod_run.LocalMarket.mkline` 没有这个
            #    `d > self.day` 守卫 → 回放 20260320 时会返回缓存里 08-31/09-01 的 bar
            #    （实测 SZ002768 的 m5.last_close 被算成 63.93，真实是 49.67）
            if d > self.day:
                continue
            for b in bs:
                if d == self.day and not self._keep(str(b.get("time"))[11:16], hhmm):
                    continue
                out.append(b)
        if f not in ("1", "5"):
            minutes = int(f)
            out = agg_nmin(out, minutes) if out else []
        return out[-int(count):] if count and count > 0 else out

    def cum(self, symbol: str, hhmm: str) -> Dict[str, float]:
        bs = [b for b in self.bars_upto(symbol, hhmm) if b.get("close") not in (None, "")]
        if not bs:
            return {}
        v = sum(float(b.get("vol") or 0) for b in bs)
        a = sum(float(b.get("amount") or 0) for b in bs)
        last = bs[-1]
        return {"open": float(bs[0]["open"]), "high": max(float(b["high"]) for b in bs),
                "low": min(float(b["low"]) for b in bs), "current": float(last["close"]),
                "vol": v, "amount": a, "n": len(bs)}

    def pre_close(self, symbol: str) -> float:
        d = self.daily_bars_db(symbol)
        if d:
            return float(d[-1]["close"] or 0)
        bs = self.load_symbol(symbol).get(self.day) or []
        return float(bs[0]["open"]) if bs else 0.0

    def float_shares(self, symbol: str) -> float:
        """流通股（股）：近 5 个已完成交易日的 量/换手率 反推（换手率单位 %）。"""
        d = [r for r in self.daily_bars_db(symbol) if r.get("turnover_rate") and r.get("vol")]
        if not d:
            return 0.0
        est = []
        for r in d[-5:]:
            tr = float(r["turnover_rate"] or 0)
            if tr > 0:
                est.append(float(r["vol"] or 0) * 100.0 / (tr / 100.0))
        return sum(est) / len(est) if est else 0.0

    def turnover_pct_cum(self, symbol: str, hhmm: str) -> float:
        c = self.cum(symbol, hhmm)
        fs = self.float_shares(symbol)
        if not c or fs <= 0:
            return 0.0
        return round(c["vol"] / fs * 100.0, 4)

    # ── 供给生产 ─────────────────────────────────────────────────
    def quote(self, symbol: str, hhmm: str) -> Optional[dict]:
        """腾讯 qt 口径 quote（字段名/单位与 `t_data_sources.fetch_tencent_quote` 一致）。

        1min 模式下是"截至当前**分钟**"的累计；字段口径与 5min 版逐字段相同，只是粒度更细。
        """
        c = self.cum(symbol, hhmm)
        if not c:
            # 2026-09-26 缺失诊断（`BT_DEBUG_QUOTE=1`，默认关 ✓，生产零影响 ✓）：
            #   打印内部取值 ⇒ 定位"到底是哪一环把这只票的报价丢了" ✓
            try:
                if str(os.getenv("BT_DEBUG_QUOTE", "0")).strip() in ("1", "true", "yes", "on"):
                    _pat = str(os.getenv("BT_DEBUG_QUOTE_SYMS", "") or "").strip()
                    _hit = (not _pat) or any(x and x in str(symbol) for x in _pat.split(","))
                    if _hit:
                        _bd = self._primary_by_day(symbol) or {}
                        _bu = self.bars_upto(symbol, hhmm) or []
                        _se = self.series(symbol, "5" if self.mode != "1m" else "1", hhmm, 320) or []
                        print("[DBG_QUOTE] %s hhmm=%s day=%s mode=%s｜primary_by_day 天数=%d(keys=%s)｜bars_upto=%d｜series=%d"
                              % (symbol, hhmm, self.day, getattr(self, "mode", "?"),
                                 len(_bd), list(_bd)[:3], len(_bu), len(_se)), flush=True)
            except Exception as _eQ:
                print("[DBG_QUOTE] 诊断失败: %s" % str(_eQ)[:80], flush=True)
            return None
        pc = self.pre_close(symbol)
        return {
            "name": symbol,
            "current": round(c["current"], 3),
            "pre_close": round(pc, 3),
            "open": round(c["open"], 3),
            "high": round(c["high"], 3),
            "low": round(c["low"], 3),
            "vol": round(c["vol"] / 100.0, 2),                     # 股 → 手
            "amount": round(c["amount"] / 10000.0, 2),             # 元 → 万元
            "turnover_rate": self.turnover_pct_cum(symbol, hhmm),
            "amplitude": round((c["high"] - c["low"]) / pc * 100, 2) if pc > 0 else 0.0,
            "average": round(c["amount"] / c["vol"], 3) if c["vol"] > 0 else round(c["current"], 3),
            "change_pct": round((c["current"] - pc) / pc * 100, 2) if pc > 0 else 0.0,
            "elapsed_s": 0.0,
        }

    def mkline(self, symbol: str, hhmm: str, count: int = 320) -> List[dict]:
        """主频分钟线（1min 模式 = 真 m1；5min 基线 = m5）。跨日、升序、**不含未来日**。"""
        return self.series(symbol, "1" if self.primary == "1" else "5", hhmm, count)

    def write_recent_sync(self, data_dir: str, symbols: List[str], hhmm: str) -> None:
        """写生产格式 `recent_sync/<code6>.json`：{day: [bars]}。

        生产 `t_monitor._today_bars/_prev_daily/_daily_dated`、`plan_runner._daily_close`、
        `wolf_judge._daily` 都读它。口径（**不改生产代码，喂它真实格式的数据**）：
          · **当日**：写 m1 bars（≤ 当前 bar）——1min 模式的价值就在这（`_today_bars` 拿到分钟级）；
          · **前几个交易日**：`sync_prev="daily"` 时每天**聚合成一根**（open=首/high=max/low=min/
            close=末/vol·amount=和）——上面所有消费者对历史日只取 close/high/low/vol（`_prev_daily`
            自己也是这么 reduce 的），语义等价，但 I/O 小一个数量级（240 步/日 × 50 标的不可忽视）。
            需要"历史日也保留分钟"时设 `sync_prev="full"`。
        """
        root = os.path.join(data_dir, "recent_sync")
        os.makedirs(root, exist_ok=True)
        for sym in symbols:
            by_day = self._primary_by_day(sym)
            if not by_day:
                continue
            days = sorted(d for d in by_day if d <= self.day)
            keep_prev = set(days[-self.prev_days:]) if self.prev_days > 0 else set(days)
            payload = {}
            for d in days:
                if d == self.day:
                    payload[d] = [dict(b) for b in by_day[d] if self._keep(str(b.get("time"))[11:16], hhmm)]
                elif d in keep_prev:
                    bs = by_day[d]
                    if self.sync_prev == "full":
                        payload[d] = [dict(b) for b in bs]
                    else:
                        payload[d] = [{"time": bs[0]["time"], "open": bs[0]["open"],
                                       "high": max(b["high"] for b in bs), "low": min(b["low"] for b in bs),
                                       "close": bs[-1]["close"], "vol": sum(b["vol"] for b in bs),
                                       "amount": sum(b["amount"] for b in bs), "_daily_agg": True}]
            p = os.path.join(root, _code6(sym) + ".json")
            _atomic_json_dump(payload, p)          # ★ §9.628 原子写 ✓
            # ★ 账本 §9.618 ✓：**同时写 `stock_5m_bt`**（生产里它也是合法的源 ✓，
            #   见 `apps/main_line/fetch_brze_target_5min.py`：`输出: data/stock_5m_bt/<code6>.json {date: [bars]}` ✓）
            #   为什么 ✓：各调用方的读取顺序**不一致** ✗（`plan_runner` 先读 `stock_5m_bt` ✗、
            #   `wolf_judge` 先读 `recent_sync` ✓）⇒ 只提供 recent_sync 时"半个调用方"第一次必失败 ✗
            #   （`t_monitor.py:6105` 假警报的来源 ✓）
            #   写一份同格式 ⇒ ①零异常 ✓ ②两种顺序都通 ✓ ③顺带**补覆盖**（监视器「去弱留强」✓）
            #   开关 `WOLF_BT_ALSO_STOCK5MBT`（**库内默认 0** ✓；回测置 1 ✓）
            if str(os.getenv("WOLF_BT_ALSO_STOCK5MBT", "0")).strip().lower() in ("1", "true", "yes", "on"):
                try:
                    _r2 = os.path.join(data_dir, "stock_5m_bt")
                    os.makedirs(_r2, exist_ok=True)
                    _atomic_json_dump(payload, os.path.join(_r2, _code6(sym) + ".json"))   # ★ §9.628 ✓
                except Exception as _e_s5:
                    print("[bt] 写 stock_5m_bt 失败 %s: %s" % (_code6(sym), str(_e_s5)[:60]), flush=True)

    # ── 自检 ─────────────────────────────────────────────────────
    def summary(self) -> Dict[str, Any]:
        return {"mode": self.mode, "primary": self.primary, "m5_agg": self.m5_agg,
                "day": self.day, "symbols_loaded": len(self._m1) + len(self._m5),
                "missing": sorted(self.missing), "dirty_files": len(self.dirty),
                "agg_source": {"agg1m": sum(1 for v in self.agg_source.values() if v == "agg1m"),
                               "file5m": sum(1 for v in self.agg_source.values() if v == "file5m"),
                               "legacy5m": sum(1 for v in self.agg_source.values() if v == "legacy5m")},
                "sync_prev": self.sync_prev, "strict_lookahead": self.strict_lookahead}


class LocalMarket5mFixed(LocalMarket1m):
    """**5min 基线（修掉 look-ahead）**：数据仍取既有 5min 缓存，只补上 `d > self.day` 守卫。

    为什么要这个类：`bt_prod_run.LocalMarket.mkline()` 会把缓存里**晚于当日**的 bar 当成"最新的
    count 根"返回（实测回放 20260320 时给出 2026-09-01 的 bar）→ 与 1min 模式对照时，
    差异里混着"前视污染"。本类把前视去掉，作为**公平的 m5 对照基准**。
    """

    def __init__(self, mins1_dir: str, mins_dir: str, bars_db: str, day: str, **kw):
        kw.pop("mode", None)
        super().__init__(mins1_dir, mins_dir, bars_db, day, mode=MODE_5M, primary="5", m5_agg=False, **kw)


def _fmt12(bars: List[dict]) -> List[dict]:
    """分钟 bar 时间戳统一成生产口径 **12 位 `YYYYMMDDHHMM`**（见模块头注释的坑）。"""
    out = []
    for b in bars:
        t = str(b.get("time") or "")
        if len(t) >= 16 and t[4] == "-":
            t = t[:10].replace("-", "") + t[11:16].replace(":", "")
        nb = dict(b)
        nb.pop("_daily_agg", None)
        nb["time"] = t
        out.append(nb)
    return out


def install_data_shims(market: "LocalMarket1m", hhmm_ref: Dict[str, str]):
    """把 `t_data_sources` 的取数点换成**频率感知**的本地库（m1 给真 m1、m5 给真 m5）。

    与 `bt_prod_run.install_data_shims` 的差别只有一条：原来 m1/m5 都返回 m5（降级冒充），
    这里按 `freq` 真正区分。其余（qt quote / 已 from-import 的引用替换 / 未替身即拒绝）逐条一致。
    """
    import app.services.t_data_sources as tds

    def fetch_tencent_quote(symbols, timeout: int = 8):
        out = {}
        for s in symbols or []:
            out[str(s)] = market.quote(str(s), hhmm_ref["hhmm"])
        return out

    def fetch_tencent_mkline(symbol, freq="m5", count=320):
        return _fmt12(market.series(str(symbol), freq, hhmm_ref["hhmm"], count))

    def fetch_minute_bars(symbol, freq="m5", count=320):
        return _fmt12(market.series(str(symbol), freq, hhmm_ref["hhmm"], count))

    def fetch_intraday_minutes_today(symbol, freq="1min"):
        f = "m1" if str(freq).startswith("1") else "m" + str(freq).replace("min", "")
        return _fmt12(market.series(str(symbol), f, hhmm_ref["hhmm"], 0))

    def fetch_brze_stk_mins(ts_code, freq="60min", **kw):
        return None

    def _raise_net(*a, **kw):
        print("[shim] ⛔ 未替身的网络取数被调用（会污染/前视）：%s" % (a[:1],), file=sys.stderr)
        return None

    names = {
        "fetch_tencent_quote": fetch_tencent_quote,
        "fetch_tencent_mkline": fetch_tencent_mkline,
        "fetch_minute_bars": fetch_minute_bars,
        "fetch_intraday_minutes_today": fetch_intraday_minutes_today,
        "fetch_brze_stk_mins": fetch_brze_stk_mins,
        "fetch_sina_minline": fetch_tencent_mkline,
    }
    origs = {k: getattr(tds, k, None) for k in names}
    for k, v in names.items():
        setattr(tds, k, v)
    n = 0
    # ★ 账本 §9.658 ✓（用户贴来一屏 `第三方的raise` ✗）：
    #   真凶 ✓：旧写法用 **`getattr(mod, k, None)`** 遍历 `sys.modules` ✗
    #     ⇒ **模块的 `__getattr__` 会被触发** ✗ ⇒ `typing` 的惰性求值（`__typing_subst__` ✓）
    #       会被跑几千次 ✓（实测 `typing.py:478/1178 ×2228/2126` ✗）
    #       ＋ `inspect.getsource` 去**读源码**（`read ×444` ✗）⇒ **纯浪费** ✓
    #   ⇒ 改为 **`vars(mod)`** ✓（**只查模块字典，不触发任何 `__getattr__`** ✓✓）
    #     语义完全等价 ✓（我们只关心"模块字典里有没有这个名字"✓）
    for mod in list(sys.modules.values()):
        try:
            _d = vars(mod)
        except TypeError:
            continue                      # 少数模块没有 __dict__ ✓（跳过 ✓）
        for k, old in origs.items():
            if old is not None and _d.get(k) is old:
                try:
                    setattr(mod, k, names[k])
                except Exception as _e_sm:
                    print("[shim] 替换 %s.%s 失败: %s" % (getattr(mod, "__name__", "?"), k, str(_e_sm)[:60]),
                          file=sys.stderr, flush=True)
                n += 1
    print("[shim] 1min 数据源替身已装（频率感知）：%s（另有 %d 处 from-import 引用被换）"
          % (",".join(names), n), file=sys.stderr)
    return names


def build_market(day: str, mins1: str = DEFAULT_MINS1, mins: str = DEFAULT_MINS5,
                 bars_db: str = DEFAULT_BARS_DB, mode: str = MODE_1M, **kw):
    """工厂：`1m` → LocalMarket1m；其它 → 委托 `bt_prod_run.LocalMarket`（保持旧行为不变）。"""
    if str(mode).lower() in ("1m", "m1", "1min", "minute"):
        return LocalMarket1m(mins1, mins, bars_db, day, mode=MODE_1M, **kw)
    import bt_prod_run as bpr
    return bpr.LocalMarket(mins, bars_db, day, mins1_dir=mins1)


def enabled() -> bool:
    """环境开关：`BT_BAR_MODE=1m`（默认 5m，行为与今天逐字节相同）。"""
    return str(os.getenv("BT_BAR_MODE", MODE_5M)).strip().lower() in ("1m", "m1", "1min", "minute")


def enable_1m_mode(module=None, **kw):
    """把 `bt_prod_run` 模块整体切到 1min（**不改那个文件**，运行期替换 3 个名字）。

        import bt_prod_run as bpr, bt_local_market as blm
        blm.enable_1m_mode(bpr)          # LocalMarket / install_data_shims / BAR_MINUTES 一起换
        bpr.main()                       # 之后照原样跑

    这是 `jobs/bt_run_1m.py` 用的方式；若你更愿意直接改 `bt_prod_run.py`，等价 diff 见该文件头注释。
    """
    if module is None:
        import bt_prod_run as module
    # ① 市场类：包一层，保留 `LocalMarket(a.mins, a.bars_db, day)` 的老调用形态
    orig_cls = module.LocalMarket

    def _factory(mins_dir, bars_db, day):
        return build_market(day, mins1=kw.get("mins1", DEFAULT_MINS1), mins=mins_dir,
                            bars_db=bars_db, mode=MODE_1M,
                            prev_days=kw.get("prev_days", 45), sync_prev=kw.get("sync_prev", "daily"),
                            strict_lookahead=kw.get("strict_lookahead", False))
    module.LocalMarket = _factory
    # ② 替身安装器：换成频率感知版
    module.install_data_shims = install_data_shims
    # ③ 回放网格：5min → 1min（strict 用完成时刻网格）
    module.BAR_MINUTES = (BAR_MINUTES_1M_STRICT if kw.get("strict_lookahead")
                          else BAR_MINUTES_1M)
    return {"market": _factory, "shims": install_data_shims, "bars": len(module.BAR_MINUTES),
            "orig_market": orig_cls}


if __name__ == "__main__":       # 自检：打印网格与缓存概况
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="")
    ap.add_argument("--mins1", default=DEFAULT_MINS1)
    ap.add_argument("--symbols", default="")
    a = ap.parse_args()
    print("BAR_MINUTES_1M: %d 根  %s … %s" % (len(BAR_MINUTES_1M), BAR_MINUTES_1M[:3], BAR_MINUTES_1M[-3:]))
    print("BAR_MINUTES_1M_STRICT: %d 根  %s … %s"
          % (len(BAR_MINUTES_1M_STRICT), BAR_MINUTES_1M_STRICT[:3], BAR_MINUTES_1M_STRICT[-3:]))
    if a.day:
        m = LocalMarket1m(a.mins1, DEFAULT_MINS5, DEFAULT_BARS_DB, a.day)
        for s in [x for x in a.symbols.split(",") if x]:
            m.load_symbol(s)
            d = m.load_m5(s)
            print("  %-11s m1 日数=%2d  m5 日数=%2d  当日 m1 根=%d  m5 根=%d"
                  % (s, len(m.load_symbol(s)), len(d), len(m.bars_upto(s, "15:00")),
                     len(m.bars_upto_m5(s, "15:00"))))
        print("summary:", m.summary())
