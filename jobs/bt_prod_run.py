# -*- coding: utf-8 -*-
"""bt_prod_run.py — **直接用生产代码跑回测**（本地 PG + 本地分钟数据 + 钉钟）。

架构（用户 2026-09-16 拍板）：本地起 PG → 把生产数据迁进来 → `DATABASE_URL` 指向本地副本
→ 生产链原样跑，**不重写任何判据**。本模块只做四件"环境"事：

  ① **时钟**：把 `datetime.now()/date.today()/time.time()/time.strftime` 全部钉到 `(交易日, 当前 bar)`，
     每个 5min bar 推进会**重钉一次**（TTL 缓存自然失效）；
  ② **数据目录**：`DATA_DIR` = 沙箱（`data/_bt_year/<day>`），并造一个 workspace farm
     `MARCUS_WORKSPACE=<farm>`，其中 `farm/data -> 沙箱` → `workspace_detector.DATA_DIR` 也落在沙箱，
     **杜绝写穿生产 data/**；
  ③ **取数**：`tushare relay`/`gzcloud` 走 `bt_run_pinned` 的本地 SQLite 替身（≤ cut）；
     `t_data_sources.fetch_tencent_quote / fetch_tencent_mkline / fetch_minute_bars` 走**本地分钟库**；
     生产自己的文件读取（`_today_bars/_prev_daily/_daily_dated` → `recent_sync/<code6>.json`）
     由我们**按 bar 写出真实格式的文件**，不改生产代码；
  ④ **库**：`DATABASE_URL=postgresql://…@127.0.0.1:5433/marcus_trading`（本地副本）。

在此之上，**全部业务逻辑都是生产代码**：
  · 布腿：`jobs/rotation_switch_arm.py::arm()`（253/254 表达式 + 条件落库）
  · 触发：`app.services.t_monitor.TMonitor._round()` 及全部 `_check_*` / `_settle_*`
  · 撮合：`app.services.t_gateway.gateway_execute()` → `paper_engine.PaperTradingEngine`（PG 落地）

用法：
  python jobs/bt_prod_run.py --day 20260320 [--root data/_bt_year] [--reset] [--dry-arm]
      [--mins data/_bt_full/mins] [--bars-db data/_bt_full/bars.sqlite] [--out <f>.json]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

sys.path[:0] = []
import bt_env  # noqa: E402
bt_env.add_paths()

REPO = bt_env.REPO
LOCAL_DB_URL = os.getenv("BT_PG_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
_NET_HITS: Dict[str, Any] = {}

BAR_MINUTES = ("09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:10", "10:15", "10:20",
               "10:25", "10:30", "10:35", "10:40", "10:45", "10:50", "10:55", "11:00", "11:05", "11:10",
               "11:15", "11:20", "11:25", "11:30", "13:05", "13:10", "13:15", "13:20", "13:25", "13:30",
               "13:35", "13:40", "13:45", "13:50", "13:55", "14:00", "14:05", "14:10", "14:15", "14:20",
               "14:25", "14:30", "14:35", "14:40", "14:45", "14:50", "14:55", "15:00")

# ── 可变的"现在"：钉钟后所有模块读到的都是它 ──────────────────────────
_NOW: Dict[str, Any] = {"dt": _dt.datetime(2026, 1, 1, 9, 15, 0)}
_REAL_EPOCH = {"t0": time.time(), "base": None}


def _pin_clock_dynamic():
    """把 datetime/date/time 的"现在"接到 `_NOW` 上；**必须在 import 生产模块之前调用**。

    与 `bt_run_pinned.pin_clock(as_of)` 的区别：那个是一次性钉死到某天 09:15；
    这里保留变量，bar 循环每根 bar 只改 `_NOW` 即可（生产模块持有的 `datetime` 类引用不变）。
    """
    try:
        import numpy  # noqa: F401  ← 预热 C 扩展（顺序不能动，见 bt_run_pinned 注释）
        import pandas  # noqa: F401
    except Exception:
        pass
    _real_date, _real_dt = _dt.date, _dt.datetime

    class _Date(_real_date):
        @classmethod
        def today(cls):
            n = _NOW["dt"]
            return _real_date(n.year, n.month, n.day)

    class _Datetime(_real_dt):
        @classmethod
        def now(cls, tz=None):
            n = _NOW["dt"]
            return _real_dt(n.year, n.month, n.day, n.hour, n.minute, n.second)

        @classmethod
        def today(cls):
            return cls.now()

        @classmethod
        def utcnow(cls):
            return cls.now()

    _dt.date = _Date
    _dt.datetime = _Datetime

    _real_time = time.time
    _real_strftime = time.strftime

    def _now_ts():
        base = _REAL_EPOCH["base"]
        if base is None:
            return _real_time()
        # 每次 set_now 都会把 base 前移 5 分钟 → TTL 缓存（30s）在 bar 之间自然失效
        return base + (_real_time() - _REAL_EPOCH["t0"])

    def _localtime(*a):
        n = _NOW["dt"]
        return time.struct_time((n.year, n.month, n.day, n.hour, n.minute, n.second, 0, 0, -1))

    time.time = _now_ts
    time.localtime = _localtime
    time.gmtime = _localtime
    time.strftime = lambda fmt, t=None: _real_strftime(fmt, _localtime() if t is None else t)
    time.ctime = lambda *a: time.strftime("%a %b %d %H:%M:%S %Y")
    time.asctime = lambda *a: time.strftime("%a %b %d %H:%M:%S %Y")


def set_now(day: str, hhmm: str) -> _dt.datetime:
    n = _dt.datetime(int(day[:4]), int(day[4:6]), int(day[6:8]),
                     int(hhmm[:2]), int(hhmm[3:5]), 0)
    _NOW["dt"] = n
    _REAL_EPOCH["base"] = n.timestamp()
    _REAL_EPOCH["t0"] = _REAL_TIME()      # 真实钟（打补丁前保存的引用）
    return n


_REAL_TIME = time.time


# ── 本地分钟/日线数据 ────────────────────────────────────────────────
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


def _norm_bar(b) -> dict:
    """分钟原始行 → 字典。两种格式都吃：
      · 生产导出数组 `[ts_code, time, open, high, low, close, vol, amount]`（`data/_bt_full/mins`）；
      · pack 字典 `{time, open, high, low, close, vol, amount}`。
    """
    if isinstance(b, dict):
        d = dict(b)
        _c = d.get("close")
        for k in ("open", "high", "low"):
            if d.get(k) in (None, ""):
                d[k] = _c
        for k in ("vol", "amount"):
            if d.get(k) in (None, ""):
                d[k] = 0.0
        return d
    try:
        t, o, h, l, c, v, amt = b[1], b[2], b[3], b[4], b[5], b[6], b[7]
        # ⚠️ 指数走本地 ClickHouse 兜底时**只有 close**（open/high/low 为 null，实测 20260105 的
        #    `000001_SH_5min_20260105.json`）→ 用 close 兜住，否则 float(None) 直接 KeyError/TypeError
        #    （指数只用于 `index.m5_dump` / `index.intraday_dd`，两处都只读 close/high）。
        if c is None:
            return {"time": str(t), "open": None, "high": None, "low": None,
                    "close": None, "vol": float(v or 0), "amount": float(amt or 0)}
        cf = float(c)
        return {"time": str(t), "open": float(o) if o is not None else cf,
                "high": float(h) if h is not None else cf,
                "low": float(l) if l is not None else cf,
                "close": cf, "vol": float(v or 0), "amount": float(amt or 0)}
    except (ValueError, IndexError, TypeError):
        return {"time": ""}


class LocalMarket:
    """本地分钟库（`data/_bt_full/mins/<code6>_<EX>_5min_<day>.json`）+ 日线（bars.sqlite）。"""

    def __init__(self, mins_dir: str, bars_db: str, day: str):
        self.mins_dir = mins_dir
        self.bars_db = bars_db
        self.day = day
        self._m5: Dict[str, Dict[str, List[dict]]] = {}     # symbol -> {day: [bars]}
        self._daily: Dict[str, List[dict]] = {}
        self._idx_m5: Dict[str, List[dict]] = {}
        self.missing: set = set()

    # — 装载 —
    def load_symbol(self, symbol: str) -> Dict[str, List[dict]]:
        symbol = _sym(symbol)
        if symbol in self._m5:
            return self._m5[symbol]
        code = _code6(symbol)
        ex = symbol[:2]
        out: Dict[str, List[dict]] = {}
        for p in sorted(glob.glob(os.path.join(self.mins_dir, "%s_%s_5min_*.json" % (code, ex)))):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            dd = str(d.get("date") or "")
            bars = d.get("bars") or []
            if dd and bars:
                out[dd] = sorted((_norm_bar(b) for b in bars), key=lambda b: str(b.get("time")))
        self._m5[symbol] = out
        if not out:
            self.missing.add(symbol)
        return out

    def load_index(self, symbol: str = "sh000001") -> List[dict]:
        symbol = _sym(symbol)
        if symbol in self._idx_m5:
            return self._idx_m5[symbol]
        bars = [b for _d, bs in sorted(self.load_symbol(symbol).items()) for b in bs]
        bars.sort(key=lambda b: str(b.get("time")))
        self._idx_m5[symbol] = bars
        return bars

    def daily(self, symbol: str) -> List[dict]:
        """≤ 该交易日**之前**的日线（含 turnover_rate / vol，手）。"""
        symbol = _sym(symbol)
        if symbol in self._daily:
            return self._daily[symbol]
        ts = _code6(symbol) + "." + symbol[:2]
        rows = []
        try:
            c = sqlite3.connect(self.bars_db)
            cur = c.execute(
                "SELECT trade_date, open, high, low, close, vol, amount, turnover_rate "
                "FROM bars WHERE ts_code=? AND trade_date < ? ORDER BY trade_date", (ts, self.day))
            rows = [{"date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4],
                     "vol": r[5], "amount": r[6], "turnover_rate": r[7]} for r in cur.fetchall()]
            c.close()
        except Exception as e:
            print("[market] 日线取数失败 %s: %s" % (symbol, str(e)[:80]), file=sys.stderr)
        self._daily[symbol] = rows
        return rows

    # — 每根 bar 的状态 —
    def bars_upto(self, symbol: str, hhmm: str) -> List[dict]:
        """当日 ≤ hhmm 的 m5 bars。"""
        day = self.load_symbol(symbol).get(self.day) or []
        return [b for b in day if str(b.get("time"))[11:16] <= hhmm]

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
        d = self.daily(symbol)
        if d:
            return float(d[-1]["close"] or 0)
        bs = self.load_symbol(symbol).get(self.day) or []
        return float(bs[0]["open"]) if bs else 0.0

    def float_shares(self, symbol: str) -> float:
        """流通股（股）：用近 5 个已完成交易日的 量/换手率 反推（换手率单位 %）。"""
        d = [r for r in self.daily(symbol) if r.get("turnover_rate") and r.get("vol")]
        if not d:
            return 0.0
        tail = d[-5:]
        est = []
        for r in tail:
            tr = float(r["turnover_rate"] or 0)
            if tr > 0:
                est.append(float(r["vol"] or 0) * 100.0 / (tr / 100.0))   # 手→股；换手% → 比例
        return sum(est) / len(est) if est else 0.0

    def turnover_pct_cum(self, symbol: str, hhmm: str) -> float:
        """当日累计换手率(%)：累计股数 / 流通股。等价于生产"近5日换手基准 × 量比"的变形。"""
        c = self.cum(symbol, hhmm)
        fs = self.float_shares(symbol)
        if not c or fs <= 0:
            return 0.0
        return round(c["vol"] / fs * 100.0, 4)

    # — 供给生产 —
    def quote(self, symbol: str, hhmm: str) -> Optional[dict]:
        """腾讯 qt 口径的 quote（字段名/单位与 `t_data_sources.fetch_tencent_quote` 一致）。"""
        c = self.cum(symbol, hhmm)
        if not c:
            return None
        pc = self.pre_close(symbol)
        vol_lots = c["vol"] / 100.0                 # 股 → 手
        amt_wan = c["amount"] / 10000.0             # 元 → 万元
        return {
            "name": symbol,
            "current": round(c["current"], 3),
            "pre_close": round(pc, 3),
            "open": round(c["open"], 3),
            "high": round(c["high"], 3),
            "low": round(c["low"], 3),
            "vol": round(vol_lots, 2),
            "amount": round(amt_wan, 2),
            "turnover_rate": self.turnover_pct_cum(symbol, hhmm),
            "amplitude": round((c["high"] - c["low"]) / pc * 100, 2) if pc > 0 else 0.0,
            "average": round(c["amount"] / c["vol"], 3) if c["vol"] > 0 else round(c["current"], 3),
            "change_pct": round((c["current"] - pc) / pc * 100, 2) if pc > 0 else 0.0,
            "elapsed_s": 0.0,
        }

    def mkline(self, symbol: str, hhmm: str, count: int = 320) -> List[dict]:
        """分钟线（腾讯 mkline 口径）：跨日、升序、含当日 ≤ hhmm。"""
        out = []
        for d, bs in sorted(self.load_symbol(symbol).items()):
            for b in bs:
                if d == self.day and str(b.get("time"))[11:16] > hhmm:
                    continue
                out.append(b)
        return out[-int(count):] if count else out

    def write_recent_sync(self, data_dir: str, symbols: List[str], hhmm: str) -> None:
        """按生产格式写 `data/recent_sync/<code6>.json`：{day: [bars]}（当日只写 ≤ 当前 bar）。

        生产 `t_monitor._today_bars/_prev_daily/_daily_dated` 直接读这个文件 ——
        **不改生产代码**，喂它真实格式的数据。
        """
        root = os.path.join(data_dir, "recent_sync")
        os.makedirs(root, exist_ok=True)
        for sym in symbols:
            by_day = self.load_symbol(sym)
            if not by_day:
                continue
            payload = {}
            for d, bs in sorted(by_day.items()):
                if d > self.day:
                    continue
                payload[d] = [b for b in bs if d < self.day or str(b.get("time"))[11:16] <= hhmm]
            p = os.path.join(root, _code6(sym) + ".json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(payload, f)


def install_data_shims(market: LocalMarket, hhmm_ref: Dict[str, str]):
    """把 `t_data_sources` 的四个网络取数点换成上面这个本地库（含"已被 from-import 拿走的引用"）。"""
    import app.services.t_data_sources as tds

    def fetch_tencent_quote(symbols, timeout: int = 8):
        out = {}
        for s in symbols or []:
            out[str(s)] = market.quote(str(s), hhmm_ref["hhmm"])
        return out

    def _fmt12(bars):
        """分钟 bar 的时间戳统一成生产口径 **12 位 `YYYYMMDDHHMM`**。

        为什么必须是这个格式（生产实测）：`t_monitor._stock_dip_prev_low` 用 `str(time)[:8]`
        取交易日分组（注释里写明"时间戳为 12 位 YYYYMMDDHHMM"，2026-09-07 修过一次）——
        若给带分隔符的 `2026-03-20 09:35:00`，`[:8]` = `2026-03-` → 所有 bar 挤进同一组
        → `len(days)<2` → **254 前日低点恒 False → 254 永不触发**。
        """
        out = []
        for b in bars:
            t = str(b.get("time") or "")
            if len(t) >= 16 and t[4] == "-":
                t = t[:10].replace("-", "") + t[11:16].replace(":", "")
            nb = dict(b)
            nb["time"] = t
            out.append(nb)
        return out

    def fetch_tencent_mkline(symbol, freq="m5", count=320):
        return _fmt12(market.mkline(str(symbol), hhmm_ref["hhmm"], count))

    def fetch_minute_bars(symbol, freq="m5", count=320):
        return _fmt12(market.mkline(str(symbol), hhmm_ref["hhmm"], count))

    def fetch_intraday_minutes_today(symbol, freq="1min"):
        bs = market.bars_upto(str(symbol), hhmm_ref["hhmm"])
        return _fmt12([dict(b) for b in bs])

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
    # 已被 `from ... import x` 拿走引用的模块（t_monitor 等）→ 扫 sys.modules 换掉
    n = 0
    for mod in list(sys.modules.values()):
        for k, old in origs.items():
            if old is not None and getattr(mod, k, None) is old:
                setattr(mod, k, names[k])
                n += 1
    print("[shim] 数据源替身已装：%s（另有 %d 处 from-import 引用被换）" % (",".join(names), n), file=sys.stderr)
    return names


def _reset_paper_account(account_id: str = "stock", initial: float = 250000.0) -> None:
    """把本地副本里的模拟盘账户清成"起点状态"（只动本地 PG，生产库碰不到）。"""
    import psycopg2
    conn = psycopg2.connect(LOCAL_DB_URL)
    conn.autocommit = True
    cur = conn.cursor()
    for sql in (
        "DELETE FROM paper_trades WHERE account_id=%s",
        "DELETE FROM paper_orders WHERE account_id=%s",
        "DELETE FROM paper_positions WHERE account_id=%s",
        "DELETE FROM paper_account_info WHERE account_id=%s",
        "DELETE FROM t_conditions WHERE account_id=%s",
        "DELETE FROM t_triggers WHERE account_id=%s",
    ):
        try:
            cur.execute(sql, (account_id,))
        except Exception as e:
            print("[reset] %s → %s" % (sql.split()[1], str(e)[:70]), file=sys.stderr)
    # ⚠️ `paper_orders.orderid` 是**全局主键**（不是 (account_id, orderid)），而 `PaperTradingEngine.buy()`
    #    的订单号只按**本账户** MAX(orderid) 续号 → 本账户清空后计数器归零，就会与**其它账户**的历史单号
    #    撞号：`_save_order` 的 `ON CONFLICT (orderid) DO UPDATE` 把 INSERT 变成"改别人的单"，
    #    于是 `match_order(orderid, account_id=本账户)` 查不到 → 返回 False → `cancel_order` 同样查不到
    #    → **早退、跳过解冻** → 冻结资金永久卡住（实测 31,249.617 卡死，可用资金少 12.5%），
    #    并且污染了 t 账户的单据行（本地副本）。
    #    修法：重置时把计数器顶到**同前缀全局最大号**之上，绝不与任何账户撞号。
    # 与引擎 `_resolve_order_prefix()` 同口径：新版 = "<account>_order"（账户级前缀，跨账户不撞号），
    # 旧版（PAPER_ORDER_PREFIX_LEGACY=1）= ORD。这里**跟着引擎口径走**，避免两边不一致。
    _legacy = str(os.getenv("PAPER_ORDER_PREFIX_LEGACY", "0")).strip().lower() in ("1", "true", "yes", "on")
    _prefix = "ORD" if _legacy else (account_id + "_order")
    try:
        cur.execute("SELECT COALESCE(MAX(CAST(SUBSTRING(orderid FROM %s) AS INTEGER)), 0) "
                    "FROM paper_orders WHERE orderid LIKE %s AND SUBSTRING(orderid FROM %s) ~ '^[0-9]+$'",
                    (len(_prefix) + 1, _prefix + "%", len(_prefix) + 1))
        _gmax = int(cur.fetchone()[0] or 0)
    except Exception as _e:
        _gmax, _ = 0, print("[reset] ⚠️ 全局单号探测失败：%s" % str(_e)[:80], file=sys.stderr)
    cur.execute("INSERT INTO paper_account_info (account_id, initial_capital, available_cash, frozen_cash,"
                " order_counter, updated_at) VALUES (%s,%s,%s,0,%s,now())",
                (account_id, initial, initial, _gmax))
    print("[reset] 订单计数器起点 = 全局同前缀最大号 %d（防跨账户撞号）" % _gmax, file=sys.stderr)
    cur.close()
    conn.close()
    print("[reset] 本地 %s 账户已重置为 %.0f 元空仓" % (account_id, initial), file=sys.stderr)


def install_vnpy_stubs() -> None:
    """本机没装 `vnpy` / `PySide6`（GUI 依赖、体积大）→ 用最小替身让 import 链通过。

    ⚠️ 这**不影响本路径的成交语义**：`gateway_execute` 构造执行器时**不传 bridge**
    （`MarcusVNPyExecutor(engine=engine, account_id=…)`）→ `self.bridge is None` → 直接走
    `PaperTradingEngine` 落库（生产 `paper_trades/paper_positions/paper_account_info` 即此路径）。
    为防止"替身导致悄悄多走/少走一层"，`_forbid_bridge()` 会在 VNPyBridge 被实例化时直接抛错。
    """
    try:
        import vnpy  # noqa: F401
        import PySide6  # noqa: F401
        print("[stub] 检测到真实 vnpy/PySide6，无需替身", file=sys.stderr)
        return
    except Exception:
        pass
    stub = os.getenv("BT_STUB_DIR") or os.path.join(REPO, "jobs", "bt_stubs")
    if os.path.isdir(stub):
        sys.path.insert(0, stub)
        print("[stub] 已启用 vnpy/PySide6 最小替身（%s）" % stub, file=sys.stderr)


def _forbid_bridge() -> None:
    """替身前提下必须保证 `VNPyBridge` **不被实例化**（否则行为与生产分叉）。"""
    try:
        from app.core.trading.vnpy_bridge import VNPyBridge
        _real_init = VNPyBridge.__init__

        def _init(self, *a, **kw):
            raise RuntimeError(
                "回测禁止实例化 VNPyBridge（本机 vnpy 是替身）。gateway_execute 不传 bridge，"
                "正常路径不会走到这里；走到了说明代码路径变了，必须人工确认。")
        VNPyBridge.__init__ = _init
        print("[stub] 已禁止 VNPyBridge 实例化", file=sys.stderr)
    except Exception as e:
        print("[stub] VNPyBridge 守护安装失败：%s" % str(e)[:80], file=sys.stderr)


def _farm_root(day_dir: str) -> str:
    """造一个 `MARCUS_WORKSPACE` farm：`<farm>/data -> 沙箱`，其余指向仓库。"""
    day_dir = os.path.abspath(day_dir)
    farm = os.path.join(os.path.dirname(day_dir), "_farm", os.path.basename(day_dir))
    os.makedirs(farm, exist_ok=True)
    for name in ("apps", "backend", "core", "jobs", "config", "scripts", "frontend", "packages"):
        src = os.path.join(REPO, name)
        dst = os.path.join(farm, name)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
    dst = os.path.join(farm, "data")
    if os.path.lexists(dst):
        os.unlink(dst)
    os.symlink(day_dir, dst)
    return farm


def _reset_module_caches(mon) -> None:
    """清掉生产模块里的 TTL / 当日缓存（否则 bar 之间会沿用上一根 bar 的旧值）。"""
    import app.services.t_monitor as tm
    tm._index_dd_cache["at"] = 0.0
    tm._m5_dump_cache["at"] = 0.0
    try:
        import app.services.t_regime as tr
        tr._regime_cache["result"] = None
        tr._regime_cache["ts"] = 0
    except Exception:
        pass
    try:
        mon._dated_cache.clear()
    except Exception:
        pass
    try:
        import app.services.t_data_sources as tds
        for attr in ("_QUOTE_CACHE", "_RT_CACHE"):
            if hasattr(tds, attr):
                getattr(tds, attr).clear()
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, help="交易日 YYYYMMDD")
    ap.add_argument("--root", default=os.path.join(bt_env.DATA, "_bt_year"), help="沙箱根")
    ap.add_argument("--mins", default=os.path.join(bt_env.DATA, "_bt_full", "mins"))
    ap.add_argument("--bars-db", default=os.path.join(bt_env.DATA, "_bt_full", "bars.sqlite"))
    ap.add_argument("--index", default="sh000001")
    ap.add_argument("--account", default="stock")
    ap.add_argument("--initial", type=float, default=250000.0)
    ap.add_argument("--reset", action="store_true", help="重置本地模拟盘（清空持仓/成交/条件/触发）")
    ap.add_argument("--dry-arm", action="store_true", help="不布条件，只跑已有的 t_conditions")
    ap.add_argument("--no-exit-legs", action="store_true", help="不调 _arm_stock_exit_legs/_roll_wolf_legs")
    ap.add_argument("--no-decision", action="store_true", help="不重放每日决策对象（会用生产 WOLF_DECISION_GATE 现状）")
    ap.add_argument("--hours", default="", help="只跑指定 bar 区间，如 09:35-10:30")
    ap.add_argument("--out", default="", help="结果 JSON 落盘路径")
    a = ap.parse_args()
    day = a.day
    # ⚠️ 必须**绝对路径**：`_farm_root` 会把 `farm/<day>/data` 做成指向沙箱的**符号链接**，
    #    相对路径的链接目标会按"链接所在目录"解析 → 变成断链 → 生产 `PaperTradingEngine.__init__`
    #    的 `os.makedirs(data_dir, exist_ok=True)` 判定 isdir=False → **FileExistsError** →
    #    `_write_trigger` 的自动执行整块被 except 吞掉 → 触发全留在 pending、**成交恒为 0**
    #    （2026-09-16 实测：年跑用相对 --root，2 小时里 0 成交；同一代码用绝对 --root 的 smoke 正常）。
    a.root = os.path.abspath(a.root)
    day_dir = os.path.join(a.root, day)
    if not os.path.isdir(day_dir):
        print("⛔ 沙箱不存在：%s" % day_dir, file=sys.stderr)
        return 2
    cut = ""
    try:
        cut = str((json.load(open(os.path.join(day_dir, "_seed.json"), encoding="utf-8")) or {}).get("cut") or "")
    except Exception:
        pass
    if not cut:
        print("⛔ 沙箱缺 _seed.json/cut（先用 bt_seed_day.py 造）", file=sys.stderr)
        return 2

    # ① 环境（必须在 import 生产模块之前）
    os.environ["DATABASE_URL"] = LOCAL_DB_URL
    os.environ["DATA_DIR"] = day_dir
    os.environ["MARCUS_WORKSPACE"] = _farm_root(day_dir)
    for sub in ("backend", "apps/paper-trading", "apps/main_line", "core", "jobs", ""):
        p = os.path.join(REPO, sub) if sub else REPO
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    install_vnpy_stubs()
    if str(os.getenv("BT_NET_OFFLINE", "1")).strip() in ("1", "true", "yes"):
        import bt_local_pro
        _NET_HITS.update(bt_local_pro.install_net_offline())
        # 断网后黄金坑状态取数必然失败 → 让它直接走生产降级分支，省掉每根 bar 的 deadline 等待
        # （cProfile 实测 3.06s/bar）。只在本驱动启用，seed/波浪路径不受影响（见函数 docstring）。
        if str(os.getenv("BT_GOLDENPIT_DEGRADED", "1")).strip() not in ("0", "false", "no"):
            bt_local_pro.mark_goldenpit_degraded()
    _pin_clock_dynamic()
    set_now(day, "09:15")
    print("[prod] day=%s cut=%s DATA_DIR=%s WS=%s DB=%s"
          % (day, cut, day_dir, os.environ["MARCUS_WORKSPACE"], LOCAL_DB_URL.split("@")[-1]), file=sys.stderr)

    # ② relay / gzcloud 替身（日线 ≤ cut）
    import bt_run_pinned as brp
    shim = brp.install_relay_shim(a.bars_db, cut)
    gzshim = brp.install_gzcloud_shim(a.bars_db, cut)

    # ③ 本地分钟数据替身
    market = LocalMarket(a.mins, a.bars_db, day)
    hhmm_ref = {"hhmm": "09:15"}
    install_data_shims(market, hhmm_ref)
    import bt_local_pro
    bt_local_pro.set_query_fn(_q)
    localpro = bt_local_pro.install_local_pro(market, cut, relay_fn=shim.relay_items)

    # ④ 生产模块
    import app.services.t_db as t_db
    _forbid_bridge()
    from app.services.t_monitor import TMonitor
    from app.services.t_gateway import gateway_execute, get_sellable_ledger  # noqa: F401

    if a.reset:
        _reset_paper_account(a.account, a.initial)

    # ⑤ 布腿 = 生产 arm()；条件集合先取"持仓 + 全部已布条件"
    symbols: List[str] = []
    armed: List[dict] = []
    if not a.dry_arm:
        try:
            sys.path.insert(0, os.path.join(REPO, "jobs"))
            import rotation_switch_arm as rsa
            import psycopg2
            conn = psycopg2.connect(LOCAL_DB_URL)
            conn.autocommit = True
            cur = conn.cursor()
            rsa.expire_old(cur, day)
            for fn in ("legs_switch.jsonl", "legs.jsonl"):
                p = os.path.join(day_dir, fn)
                if not os.path.exists(p):
                    continue
                for ln in open(p, encoding="utf-8"):
                    ln = ln.strip()
                    if not ln:
                        continue
                    L = json.loads(ln)
                    side = str(L.get("side") or "buy")
                    if side != "buy":
                        continue
                    sym = L["symbol"]
                    if sym not in symbols:
                        symbols.append(sym)
                    if L.get("type") == "buy_253/254" or L.get("src") == "switch_builder_0818":
                        r1 = rsa.arm(conn, cur, sym, "custom_m5dump", "buy", rsa.BUY_253_EXPR, day)
                        r2 = rsa.arm(conn, cur, sym, "custom_prevlow", "buy", rsa.BUY_254_EXPR, day)
                        armed.append({"symbol": sym, "src": "switch_0818", "253": r1, "254": r2})
                    else:
                        r1 = rsa.arm(conn, cur, sym, "custom_m5dump", "buy", rsa.BUY_253_EXPR, day)
                        r2 = rsa.arm(conn, cur, sym, "custom_prevlow", "buy", rsa.BUY_254_EXPR, day)
                        armed.append({"symbol": sym, "src": "arm_0920", "253": r1, "254": r2})
            cur.close()
            conn.close()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("⛔ 布腿失败：%s" % str(e)[:200], file=sys.stderr)
            return 3
    # 持仓（本地副本）也要有行情（卖腿/止损评估）
    try:
        held = list(get_sellable_ledger(a.account).keys())
    except Exception:
        held = []
    for s in held:
        if s not in symbols:
            symbols.append(s)
    symbols = sorted(set(symbols))
    print("[prod] 布腿 %d 条 / 标的 %d 只（持仓 %d）" % (len(armed), len(symbols), len(held)), file=sys.stderr)

    # ⑤b 每日决策对象（生产：盘后 19:45 job 产出当日对象，**次日**盘中用，max_age=4 天）
    #     `t_gateway._decision_gate` 在 WOLF_DECISION_GATE=1 时"没有对象就整日拦买"——
    #     回测必须把这层也重放出来，否则全年买入会被静默拦空（本日实测：0 成交）。
    dec_res = {}
    if not a.no_decision:
        try:
            from app.services import daily_decision as dd
            dec_res = dd.run(cut, save=True)
            _l5 = ((dec_res.get("layers") or {}).get("L5_entry") or {}).get("value") or {}
            print("[prod] 决策对象(%s) ok=%s 允许买入=%s 拦阻=%s 缺失层=%s"
                  % (cut, dec_res.get("ok"), _l5.get("allowed"),
                     len(_l5.get("blockers") or []), dec_res.get("missing")), file=sys.stderr)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("[prod] 决策对象生成失败：%s" % str(e)[:200], file=sys.stderr)

    # ⑥ 建监控器（生产对象；**不启线程**，我们逐 bar 手动调它的方法）
    mon = TMonitor(interval_seconds=30)
    # 逐日结转 + 持仓离场腿（生产 _run() 里的同一对调用）
    if not a.no_exit_legs:
        try:
            n1 = mon._roll_wolf_legs(day)
            n2 = mon._arm_stock_exit_legs(day)
            print("[prod] 持续腿结转=%s / 持仓离场腿=%s" % (n1, n2), file=sys.stderr)
        except Exception as e:
            print("[prod] 结转/离场腿异常：%s" % str(e)[:160], file=sys.stderr)
    # 条件里可能带新的标的（如离场腿）→ 补进行情集合
    try:
        for c in t_db.list_active_conditions(account_id=a.account):
            s = c.get("symbol")
            if s and s not in symbols:
                symbols.append(s)
    except Exception:
        pass
    symbols = sorted(set(symbols))
    idx_syms = [a.index]

    # ⑦ 逐 bar 驱动
    bars = list(BAR_MINUTES)
    if a.hours:
        lo, _, hi = a.hours.partition("-")
        bars = [b for b in bars if (not lo or b >= lo) and (not hi or b <= hi)]
    log: List[dict] = []
    step_secs: Dict[str, float] = {}
    checks = ("_round", "_settle_tsell_pending", "_settle_pullback_sell", "_check_wolf_t_rules",
              "_check_roundtrip_sell", "_check_defensive_t_reduce", "_check_board_half",
              "_check_profit_take", "_check_weekend_hedge", "_check_hedge_refill", "_check_fib_target",
              "_check_passive_stop", "_check_boll_sell", "_check_boll_mid_exit",
              "_check_position_discipline", "_check_index_level_stop", "_check_logic_time_stop")
    for hhmm in bars:
        set_now(day, hhmm)
        hhmm_ref["hhmm"] = hhmm
        market.write_recent_sync(day_dir, symbols + idx_syms, hhmm)
        _reset_module_caches(mon)
        n0 = _count_triggers(a.account)
        try:
            for _step in checks:
                _t0 = time.time()
                try:
                    getattr(mon, _step)()
                finally:
                    step_secs[_step] = step_secs.get(_step, 0.0) + (time.time() - _t0)
            if False:
                pass
        except Exception as e:
            import traceback
            print("[prod] %s %s 轮次异常：%s" % (day, hhmm, str(e)[:200]), file=sys.stderr)
            traceback.print_exc()
        n1 = _count_triggers(a.account)
        nt = _count_trades(a.account)
        log.append({"hhmm": hhmm, "triggers": n1 - n0, "trades_total": nt})
        if n1 > n0:
            print("[prod] %s 触发 +%d（累计 %d）/ 成交累计 %d" % (hhmm, n1 - n0, n1, nt), file=sys.stderr)

    # ⑦b 日终账户自检：冻结资金必须归零
    #   为什么：`paper_orders.orderid` 是**全局主键**而订单号按账户续号 → 撞号时 `_save_order` 会改到
    #   别人的单 → `match_order/cancel_order` 查不到本账户单 → **跳过解冻** → 冻结资金静默泄漏
    #   （实测卡死 31,249.617 = 可用资金的 12.5%）。这里日终显式检查，泄漏即报。
    try:
        _ai = _account_info(a.account)
        _fz = float((_ai[0] if _ai else {}).get("frozen_cash") or 0)
        if _fz > 0.01:
            print("[prod] ⚠️ %s 日终冻结资金未归零：%.2f（疑似撞号/未解冻，见 commit 说明）" % (day, _fz),
                  file=sys.stderr)
        else:
            print("[prod] %s 日终冻结资金 = 0 ✓" % day, file=sys.stderr)
    except Exception as _e:
        print("[prod] 日终自检失败：%s" % str(_e)[:80], file=sys.stderr)

    # ⑧ 汇总
    res = {"day": day, "cut": cut, "symbols": symbols, "armed": armed,
           "triggers": _trigger_rows(a.account), "trades": _trade_rows(a.account),
           "positions": _position_rows(a.account), "account": _account_info(a.account),
           "bars": log, "market_missing": sorted(market.missing),
           "decision": {"cut": cut, "ok": dec_res.get("ok"),
                        "allowed": ((((dec_res.get("layers") or {}).get("L5_entry") or {}).get("value") or {}).get("allowed")),
                        "missing": dec_res.get("missing")},
           "frozen_cash_end": float(((_account_info(a.account) or [{}])[0]).get("frozen_cash") or 0),
           "step_secs": {k: round(v, 1) for k, v in sorted(step_secs.items(), key=lambda kv: -kv[1])},
           "net_hits": sorted(_NET_HITS.items(), key=lambda kv: -kv[1])[:30]}
    out = a.out or os.path.join(bt_env.DATA, "_bt_prod", "%s.json" % day)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("[prod] 完成：触发 %d / 成交 %d / 持仓 %d → %s"
          % (len(res["triggers"]), len(res["trades"]), len(res["positions"]), out), file=sys.stderr)
    if gzshim is not None:
        print("[gzshim] local=%d passthrough=%d" % (gzshim.n_local, gzshim.n_remote), file=sys.stderr)
    print("[prod] 各步累计耗时(s): %s" % {k: round(v, 1) for k, v in
          sorted(step_secs.items(), key=lambda kv: -kv[1])[:10]}, file=sys.stderr)
    if _NET_HITS:
        print("[net] 被拦下的出网尝试: %s" % sorted(_NET_HITS.items(), key=lambda kv: -kv[1])[:15], file=sys.stderr)
    if getattr(localpro, "unserved", None):
        top = sorted(localpro.unserved.items(), key=lambda kv: -kv[1])[:20]
        print("[localpro] 未本地服务（返回空）: %s" % top, file=sys.stderr)
    if getattr(localpro, "calls", None):
        print("[localpro] 调用统计: %s" % sorted(localpro.calls.items(), key=lambda kv: -kv[1])[:12], file=sys.stderr)
    if shim is not None and getattr(shim, "offline_calls", None):
        top = sorted(shim.offline_calls.items(), key=lambda kv: -kv[1])[:20]
        print("[shim] offline（未本地覆盖 → 返回空，需评估）: %s" % top, file=sys.stderr)
    if shim is not None:
        print("[shim] local=%d remote=%d dropped_future=%d err=%d %s"
              % (shim.n_local, shim.n_remote, shim.n_dropped, shim.n_err,
                 ("first_err=" + shim.first_err) if shim.first_err else ""), file=sys.stderr)
    return 0


def _q(sql: str, args=()):
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(LOCAL_DB_URL)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _count_triggers(account: str) -> int:
    try:
        return int((_q("SELECT count(*) c FROM t_triggers WHERE account_id=%s", (account,))[0])["c"])
    except Exception:
        return 0


def _count_trades(account: str) -> int:
    try:
        return int((_q("SELECT count(*) c FROM paper_trades WHERE account_id=%s AND coalesce(voided,0)=0",
                       (account,))[0])["c"])
    except Exception:
        return 0


def _trigger_rows(account: str):
    try:
        return _q("SELECT id, symbol, event_type, status, trigger_price, quote_price, left(coalesce(reason,''),200) reason "
                  "FROM t_triggers WHERE account_id=%s ORDER BY id", (account,))
    except Exception:
        return []


def _trade_rows(account: str):
    try:
        return _q("SELECT id, symbol, direction, price, volume, amount, reason, created_at FROM paper_trades "
                  "WHERE account_id=%s AND coalesce(voided,0)=0 ORDER BY id", (account,))
    except Exception:
        return []


def _position_rows(account: str):
    try:
        return _q("SELECT symbol, direction, volume, avg_price, entry_date FROM paper_positions "
                  "WHERE account_id=%s AND volume>0 ORDER BY symbol", (account,))
    except Exception:
        return []


def _account_info(account: str):
    try:
        return _q("SELECT * FROM paper_account_info WHERE account_id=%s", (account,))
    except Exception:
        return []


if __name__ == "__main__":
    sys.exit(main())
