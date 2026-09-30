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
  · 交易腿 agent（`--agent on`）：`jobs/bt_agent_loop.py`（= 生产 `t_bridge.fallback_poll_loop` 的循环）
    × `t_bridge.wake_and_decide`（POST `/chat` 唤醒 LLM）→ `t_ai_agent.handle_ai_decision`
    路由 exec/wait/abandon/update_condition；LLM 走 `jobs/bt_llm_replay.py` 录制/回放（可复现）。
    **默认 off**：不装 LLM 层、不消费 pending → 与加这个开关之前的行为逐字节一致。

用法：
  python jobs/bt_prod_run.py --day 20260320 [--root data/_bt_year] [--reset] [--dry-arm]
      [--mins data/_bt_full/mins] [--bars-db data/_bt_full/bars.sqlite] [--out <f>.json]
      [--agent on|off]
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
# ── 回测专属：给**本进程所有 libpq 连接**加 `lock_timeout`（2026-09-18 排障后加）──────────
# 现象：多臂并发跑时，某臂**新的一天**启动会执行 `_apply_paper_account_migration()` 的
#   `ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS account_id ...`（要 ACCESS EXCLUSIVE），
#   而另一臂的某个长事务仍 `idle in transaction`（持 ACCESS SHARE）⇒ ALTER 无限等待，
#   排在它后面的所有查询全被阻塞 ⇒ **整批臂静默停摆**（进程 state=S、utime 不增长、load 仅 0.25）。
#   PGOPTIONS 只作用于本进程（回测驱动自 spawn，生产 import 不到本文件）⇒ 生产零影响。
#   迁移本身已 try/except（超时只打 warn）：列早已存在的库里，这个 ALTER 失败**无害**。
os.environ.setdefault("PGOPTIONS", "-c lock_timeout=5000")
import bt_local_market as _blm  # noqa: E402
LOCAL_DB_URL = os.getenv("BT_PG_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
_NET_HITS: Dict[str, Any] = {}

BAR_MINUTES = ("09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:10", "10:15", "10:20",
               "10:25", "10:30", "10:35", "10:40", "10:45", "10:50", "10:55", "11:00", "11:05", "11:10",
               "11:15", "11:20", "11:25", "11:30", "13:05", "13:10", "13:15", "13:20", "13:25", "13:30",
               "13:35", "13:40", "13:45", "13:50", "13:55", "14:00", "14:05", "14:10", "14:15", "14:20",
               "14:25", "14:30", "14:35", "14:40", "14:45", "14:50", "14:55", "15:00")

# ── 可变的"现在"：钉钟后所有模块读到的都是它 ──────────────────────────
_NOW: Dict[str, Any] = {"dt": _dt.datetime(2026, 1, 1, 9, 15, 0)}
_REAL_EPOCH = {"t0": time.time(), "base": None,
               # ⚠️ 2026-09-30（账本 §9.343）：`time.time()` 被本模块**钉到模拟时钟** ✗
               #   ⇒ 「各步累计耗时」算出来的是**模拟时间** ✗，不是墙钟 ✓
               #   `perf_counter` **不在被钉名单** ✓ ⇒ 用它做**真实**计时 ✓
               "perf0": time.perf_counter()}


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
    global _REAL_DATETIME_CLS
    try:
        import datetime as _dm
        _REAL_DATETIME_CLS = _dm.datetime
    except Exception:
        _REAL_DATETIME_CLS = None
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


# ── 本地 as-of API 状态（工具通道「本地硬隔离」）──────────────────────
# 生产 dsh 的工具调用打 MARCUS_API_URL（生产 backend）。回测改用**本地 dsh + 本地 as-of API**
# （jobs/bt_local_dsh.sh + jobs/bt_asof_api.py）：dsh 是独立进程、不知道回放跑到哪根 bar，
# 故由驱动每根 bar 把 as-of 时刻落盘，API 每个请求读一次并按它截断。
# BT_ASOF_STATE_OFF=1 可关（不写文件 → API 侧 fail-closed 409，工具报错，不会拿错数据）。
BT_ASOF_STATE = os.getenv(
    "BT_ASOF_STATE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 ".dsh-tmp", "wolfbt", "asof_state.json"))
_ASOF_WARNED = {"n": 0}


def write_asof_state(day: str, hhmm: str, day_dir: str, account: str = "") -> None:
    """把当前 bar 的 as-of 时刻写给本地 as-of API（工具通道硬隔离的唯一时间源）。"""
    if str(os.getenv("BT_ASOF_STATE_OFF", "0")).strip() in ("1", "true", "yes"):
        return
    try:
        payload = {
            "run": os.path.basename(str(day_dir).rstrip("/")) or "",
            "day8": day, "bar": hhmm,
            "day": "%s-%s-%s" % (day[:4], day[4:6], day[6:8]),
            "account": account or "",
            "written_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = BT_ASOF_STATE + ".tmp"
        os.makedirs(os.path.dirname(BT_ASOF_STATE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, BT_ASOF_STATE)
    except Exception as e:                      # 只警告一次，绝不静默
        _ASOF_WARNED["n"] += 1
        if _ASOF_WARNED["n"] <= 3:
            print("[asof-state] 写入失败 %s: %s（工具通道将 fail-closed）" % (BT_ASOF_STATE, e),
                  file=sys.stderr)


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


# 指数取数修复开关（2026-09-25，账本 §9.29）：**默认 0 = 修复前的旧行为**（生产不受影响，bt_prod_run 只被回测用）。
# 为什么需要开关：这条修复让指数报价可用 ⇒ 黄白线复活 ⇒ 谨慎闸 1~3 可能触发 ⇒ **是行为变量**，
#   必须能与其它变量分离（T15 之前都是旧行为；T16 起若默认开，就会让 T16 变成"③+指数修复"两变量）。
# 回测 pins 置 1；要单变量对照的臂里显式置 0。
def _idx_mins1_fallback_on() -> bool:
    return str(os.getenv("WOLF_IDX_MINS1_FALLBACK", "0")).strip().lower() in ("1", "true", "yes", "on")


class LocalMarket:
    """本地分钟库（`data/_bt_full/mins/<code6>_<EX>_5min_<day>.json`）+ 日线（bars.sqlite）。"""

    def __init__(self, mins_dir: str, bars_db: str, day: str, mins1_dir: str = ""):
        self.mins_dir = mins_dir
        self.mins1_dir = mins1_dir or ""      # 5min 文件的第二来源（兄弟类 LocalMarket1m._paths 同款回退）
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
        # 2026-09-25 修（账本 §9.29）：**指数的 5min 文件只落在 mins1* 目录**（`<code>_<EX>_5min_<day>.json`），
        #   而本类原先只 glob `mins_dir` ⇒ 000852/000300/000016/399001 全部取不到（`market_missing` 75/75 天），
        #   进而黄白线不可用（谨慎闸 4 条子条件里 3 条从未触发）。兄弟类 `bt_local_market.LocalMarket1m._paths`
        #   早有 "5min 也看 mins1_dir" 的回退，这里补齐同一口径（只增解析来源，不改任何股票标的的取数结果）。
        paths = sorted(glob.glob(os.path.join(self.mins_dir, "%s_%s_5min_*.json" % (code, ex))))
        if self.mins1_dir and _idx_mins1_fallback_on():
            paths += sorted(glob.glob(os.path.join(self.mins1_dir, "%s_%s_5min_*.json" % (code, ex))))
        for p in paths:
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            dd = str(d.get("date") or "")
            bars = d.get("bars") or []
            if dd and bars:
                out[dd] = sorted((_norm_bar(b) for b in bars), key=lambda b: str(b.get("time")))
        # 2026-09-26 修（账本 §9.180）：**不缓存空结果** ✗ —— 原先 `self._m5[symbol] = out`
        #   会把"当时分钟档还没预取到"的空结果**缓存一整天** ⇒
        #   当日新入池的票（如埋伏腿 08:18 布腿后立刻被问价 ✗）**整日拿不到报价** ⇒ 整轮被跳过 ✗
        #   实测：`sh603228` 独立调用返回 5 天（含 0114 ✓），但重放里恒为 0 天 ✗
        if out:
            self._m5[symbol] = out
        else:
            self.missing.add(symbol)      # 仍记缺档（供告警 ✓），但**下次会重新 glob** ✓
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
        # 2026-09-25 修（账本 §9.29）：日线库**没有指数行** ⇒ 指数原先回退成"今日开盘"，
        #   使 `change_pct` 变成"开盘至今"而不是"相对昨收"（黄白线直接吃这个数 ⇒ 幅度被系统性压小）。
        #   改为取**前一交易日最后一根**分钟 bar 的收盘（分钟夹具里 160 天全在 ✓）。
        #   ⚠️ 与 mins1 回退同属"指数取数修复"⇒ 同一个开关 `WOLF_IDX_MINS1_FALLBACK`（默认 0=旧行为）。
        if not _idx_mins1_fallback_on():
            bs0 = self.load_symbol(symbol).get(self.day) or []
            return float(bs0[0]["open"]) if bs0 else 0.0
        allb = self.load_symbol(symbol) or {}
        days = sorted(allb)
        if self.day in days:
            i = days.index(self.day)
            if i > 0 and allb.get(days[i - 1]):
                return float(allb[days[i - 1]][-1].get("close") or 0)
            bs = allb.get(self.day) or []
            return float(bs[0]["open"]) if bs else 0.0
        return 0.0

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
            try:
                if str(os.getenv("BT_DEBUG_QUOTE", "0")).strip() in ("1", "true", "yes", "on"):
                    _pat = str(os.getenv("BT_DEBUG_QUOTE_SYMS", "") or "").strip()
                    if (not _pat) or any(x and x in str(symbol) for x in _pat.split(",")):
                        _bd = self.load_symbol(symbol) or {}
                        _bu = self.bars_upto(symbol, hhmm) or []
                        print("[DBG_QUOTE2] %s hhmm=%s day=%s｜load_symbol 天数=%d 末尾keys=%s｜bars_upto=%d"
                              % (symbol, hhmm, self.day, len(_bd), list(_bd)[-3:], len(_bu)), flush=True)
            except Exception as _eQ2:
                print("[DBG_QUOTE2] 诊断失败: %s" % str(_eQ2)[:70], flush=True)
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
            # ⛔ 2026-09-17 修前视：原先只对"当日"做 <= hhmm 过滤，**没有 d > self.day 守卫**，
            #    而 load_symbol 会 glob 该标的的全部缓存文件 → out[-count:] 取到的是缓存里
            #    **最后几天（未来）**的 bar。实测回放 20260320 10:00 时 mkline(SZ002768) 返回的是
            #    2026-08-31/09-01 的 bar（minute.m5.last_close 63.93 vs 真值 49.67），
            #    进而让 t1_shrink_expand/t_sell/dip_prev_low 等**由未来数据决定**。
            #    影响面：prod_*.json 里 3093 个 (标的,日) 中 607 个（19.6%）、覆盖 84 个交易日。
            if d > self.day:
                continue
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


def install_clock_shim() -> None:
    """**把"交易时段门"在回测里放开** ✗✗（账本 §9.242 —— 第五层、也是最根本的一层 ✓）。

    病灶（实测 ✓）：`t_regime._is_trading_time()` 用**真实墙上时钟** ✓
      （9:30-11:30 / 13:00-15:00、周一至周五 ✓），而回测常**在晚上跑** ✗
      ⇒ `t_monitor` 主循环里那一大段 `self._check_*()`（含 **`_check_ambush_discipline`** ✓）
        **整块被跳过** ✗ ⇒ 埋伏纪律/板上减半/被动止盈… **从来不会执行** ✗✗
      ⇒ 这就是"埋伏纪律 0 条日志"的**最终解释** ✓（前四层都是在"它压根没被调用"之上 ✗）

    修法：回测里把**两处**引用都替换成"恒为真" ✓
      （`t_regime` 模块内 ✓ ＋ **`t_monitor` 已 from-import 走的那份** ✓ —— 只改前者无效 ✗）
    开关：`WOLF_BT_ALWAYS_TRADING=1`（回测 pins ✓；生产不受影响 ✓）
    """
    if str(os.getenv("WOLF_BT_ALWAYS_TRADING", "0")).strip().lower() not in ("1", "true", "yes", "on"):
        return
    try:
        import app.services.t_regime as _tr
        import app.services.t_monitor as _tm
        _tr._is_trading_time = lambda *a, **k: True
        _tm._is_trading_time = lambda *a, **k: True
        print("[bt] 交易时段门已放开（回测视为盘中 ✓；否则 _check_* 整块不执行 ✗）", flush=True)
    except Exception as _e:
        print("[bt] 放开交易时段门失败: %s" % str(_e)[:80], flush=True)


def fanout_shims() -> None:
    """**补丁广播** ✓（账本 §9.246）—— 把替身/时钟**写进所有已导入模块的命名空间** ✓。

    病灶（本轮四次踩同一坑 ✗）：回测替换的是**模块属性** ✓，而各处是
      `from app.services.t_data_sources import fetch_tencent_quote` ✗ ——
      **from-import 会绑走原对象** ⇒ 之后再改模块属性**对它无效** ✗
      （实测：监控拿到的仍是**真行情函数** ⇒ 沙箱无网络 ⇒ `_cur=0` ⇒ 纪律整体失效 ✓）
    审计（§9.246）：此类点 **11 处**（`t_monitor`／`t_gateway`／`t_eod`／`t_pool`… ✓）；
      未归一化行情调用 **31 处** ✗；`datetime.now()` **381 处** ✗（回测里必须走**模拟日** ✓）

    修法：遍历 `sys.modules`，把三样东西**逐个模块地**替换 ✓
      ① `fetch_tencent_quote` ⇒ 本地库替身 ✓（一次覆盖 31 处符号问题 ✓）
      ② `_is_trading_time` ⇒ 恒真 ✓
      ③ `datetime` ⇒ 回测的**模拟时钟类** ✓（只替换还是"原类"的模块 ✓ ⇒ 覆盖 381 处 ✓）
    """
    if str(os.getenv("WOLF_BT_ALWAYS_TRADING", "0")).strip().lower() not in ("1", "true", "yes", "on"):
        return
    try:
        import sys as _sys
        import app.services.t_data_sources as _tds
        import app.services.t_regime as _tr
        _orig_cls = _REAL_DATETIME_CLS
        _fake = _tds.fetch_tencent_quote
        _n_a = _n_b = _n_c = 0
        for name, mod in list(_sys.modules.items()):
            if mod is None or not name.startswith(("app", "jobs", "core", "apps")):
                continue
            try:
                if getattr(mod, "fetch_tencent_quote", None) is not None and getattr(mod, "fetch_tencent_quote") is not _fake:
                    setattr(mod, "fetch_tencent_quote", _fake)
                    _n_a += 1
                if getattr(mod, "_is_trading_time", None) is not None and getattr(mod, "_is_trading_time") is not _tr._is_trading_time:
                    setattr(mod, "_is_trading_time", _tr._is_trading_time)
                    _n_b += 1
                _d = getattr(mod, "datetime", None)
                if _d is _orig_cls and _orig_cls is not None:
                    setattr(mod, "datetime", datetime.datetime)   # 模拟时钟类 ✓
                    _n_c += 1
            except Exception:
                continue
        print("[bt] 补丁广播 ✓：行情 %d 处｜时段门 %d 处｜模拟时钟 %d 处" % (_n_a, _n_b, _n_c), flush=True)
    except Exception as _e:
        print("[bt] 补丁广播失败(不致命): %s" % str(_e)[:80], flush=True)


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


def _reset_paper_account(account_id: str = "stock", initial: float = 250000.0,
                         day: str = "") -> None:
    """把本地副本里的模拟盘账户清成"起点状态"（只动本地 PG，生产库碰不到）。

    `day` = 本次年跑的第一个交易日：额外清掉**该日及以后**的 `t_ai_actions`。
    为什么必须清（2026-09-17 排查"收益为什么变低"）：AI 决策提示词会带
    "最近 5 次决策 + 该标的做T历史统计（exec 胜率…）"，而 `wake_agent` 侧**没有 as-of 过滤**；
    上一跑（甚至跑到 09-14）留在 `t_ai_actions` 里的行会被当成"历史"喂给回放早期的决策
    ⇒ **未来函数** + 每跑继承的历史不同 ⇒ 决策漂移、缓存永不命中。
    本表不在原来的 6 张清理表里（paper_*/t_conditions/t_triggers），故单列一步。
    """
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
    # AI 决策审计：清"本次年跑起点及以后"的行（含上一跑留下的未来交易日行）
    if day:
        try:
            _d8 = str(day)
            _dash = "%s-%s-%s" % (_d8[:4], _d8[4:6], _d8[6:8]) if len(_d8) == 8 else _d8
            cur.execute("DELETE FROM t_ai_actions WHERE trade_date >= %s", (_dash,))
            print("[reset] t_ai_actions 已清 %d 行（trade_date >= %s）—— 防止上一跑的决策/胜率统计"
                  "被当成历史喂进提示词（未来函数）" % (cur.rowcount, _dash), file=sys.stderr)
        except Exception as e:
            print("[reset] t_ai_actions 清理失败（继续）: %s" % str(e)[:90], file=sys.stderr)

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
    # ── 交易腿 agent（生产 t_bridge.fallback_poll_loop 的消费链）────────────────────────
    #    默认 off = 完全不碰 LLM 层/不消费 pending，行为与加开关之前一致（可回退）。
    ap.add_argument("--agent", choices=["on", "off"], default=os.getenv("BT_AGENT", "off"),
                    help="on=接生产交易腿 agent（唤醒 LLM 消费 pending）；off=旧行为（默认）")
    ap.add_argument("--agent-cache", default=os.getenv("BT_LLM_CACHE", os.path.join(bt_env.DATA, "_bt_llm_year2")),
                    help="LLM 录制/回放缓存根目录（默认 data/_bt_llm_year2）")
    ap.add_argument("--agent-mode", default=os.getenv("BT_LLM_MODE", "record"),
                    choices=["record", "replay"], help="record=真实外呼+落盘；replay=只读缓存")
    ap.add_argument("--agent-url", default=os.getenv("BT_AGENT_CHAT_URL", "http://127.0.0.1:13001/chat"),
                    help="PI_SERVER_URL 指向的 LLM /chat（本地隧道）")
    ap.add_argument("--agent-all-pending", action="store_true",
                    help="认领全部 pending（生产语义，含跨日遗留/孤儿单处置）；默认只认领本次运行产生的")
    ap.add_argument("--agent-max-total", type=int, default=0, help="当日 LLM 决策上限（0=不限）")
    ap.add_argument("--agent-session-tag", default=os.getenv("BT_AGENT_SESSION_TAG", ""),
                    help="dsh 会话后缀（默认 btasof-<day>-r<pid>）：dsh 会话**跨运行持久化**，"
                         "沿用历史会把上次的实时工具结果/旧决策带进本次提示词（实测 AI 会数"
                         "「同一时点已第 N 次触发」）；后缀保证一次运行内上下文照常累积、跨运行从零开始")
    ap.add_argument("--agent-session-tag-off", action="store_true",
                    help="不复用隔离后缀（=沿用生产 t-agent-<symbol> 会话，含历史上下文）")
    ap.add_argument("--agent-tool-guard", choices=["on", "off"],
                    default=os.getenv("BT_AGENT_TOOL_GUARD", "on"),
                    help="on（默认）=把 AI 工具口径钉到 as-of（提示词声明工具禁用 + 注入 as-of 数据块，"
                         "见 jobs/bt_agent_tools.py）；off=旧行为（工具通道拿生产实时价 → PIT 泄漏）")
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
    # 交易腿 agent 的 LLM 出口：`--agent on` 时把 PI_SERVER_URL 指到本地隧道（t_bridge 读它）。
    # 必须**在 import 生产模块之前**设（`app.config.get_settings()` 有 lru_cache），并清一次缓存兜底。
    # off 时一个字都不改 → 生产模块看到的 env 与旧行为完全一致。
    if a.agent == "on":
        os.environ["PI_SERVER_URL"] = a.agent_url
        os.environ["BT_LLM_MODE"] = a.agent_mode
        os.environ["BT_LLM_CACHE"] = a.agent_cache
        try:
            from app.config import get_settings
            get_settings.cache_clear()
        except Exception:
            pass
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
        # `external.*` 字段组：规则判据一条都不用（t_conditions 里含 external 的表达式 = 0 条），
        # LLM-off 年跑下纯开销；短路成与断网同形状的空快照，不再碰 ArkVol/FRED/美股。
        if str(os.getenv("BT_EXTERNAL_RISK_OFF", "1")).strip() not in ("0", "false", "no"):
            bt_local_pro.shortcut_external_risk()
    _pin_clock_dynamic()
    set_now(day, "09:15")
    print("[prod] day=%s cut=%s DATA_DIR=%s WS=%s DB=%s"
          % (day, cut, day_dir, os.environ["MARCUS_WORKSPACE"], LOCAL_DB_URL.split("@")[-1]), file=sys.stderr)

    # ①.5 复权价（2026-09-23；**默认关**，回测覆盖层 jobs/bt_env_pins.sh 里同值兜底）
    # 为什么需要：`bars.sqlite` / `mins` 是**不复权**序列 ⇒ 除权日（送转/分红）在价格上是跳空、
    #   在事实是**股本增加**；引擎按"股数不变"记账，于是把上涨记成暴跌。
    #   实测：T5 的 SH603061 2026-04-16 做 10转4.5（股本 6000万→8700万，数据源 pre_close 自调为
    #   229.85、pct_chg=+7.02%），引擎记成 **−26.3%** —— 单腿 −8,455（占权益 3.4%），
    #   而该腿真实盈亏约 **+2,379**；T5 全臂收益因此从 +12.56% 被压成 +7.73%。
    #   更严重的是**指标**：均线/波段低点/"60 日底线"建在不复权序列上 ⇒ 除权日附近假跌破 ⇒ 假止损
    #   （SH603061 就是被那个假的"跌破 60 日线"止损掉的；②趋势线止损 line=MA60 恰在除权跳空下方）。
    # 打开后切到 *_adj（前复权：最新价不变、历史价下移、除权日两侧连续）：
    #   bars_adj.sqlite / mins_adj / mins1_adj —— 由 .dsh-tmp/wolfbt/mk_adj_bars.py 与 mk_adj_mins.py 生成；
    #   因子来自行情自身（pre_close ≠ 前一交易日 close ⇒ 事件，倍数 = 前收/除权参考价），不依赖外部分红表。
    # ⚠️ 必须放在 ②relay ③分钟替身**之前**：那两个 shim 也要吃复权后的日线。
    if os.getenv("WOLF_ADJ_PRICE", "0").strip() not in ("0", "false", "no", ""):
        a.bars_db = os.path.join(REPO, "data", "_bt_full", "bars_adj.sqlite")
        a.mins = os.path.join(REPO, "data", "_bt_full", "mins_adj")
        os.environ["BT_MINS1"] = os.path.join(REPO, "data", "_bt_full", "mins1_adj")
        print("[prod] ⚠️ 复权价已启用（前复权）：bars=%s mins=%s mins1=%s"
              % (a.bars_db, a.mins, os.environ["BT_MINS1"]), file=sys.stderr)

    # ② relay / gzcloud 替身（日线 ≤ cut）
    import bt_run_pinned as brp
    shim = brp.install_relay_shim(a.bars_db, cut)
    gzshim = brp.install_gzcloud_shim(a.bars_db, cut)

    # ③ 本地分钟数据替身
    market = _blm.build_market(day, mins1=os.getenv("BT_MINS1", os.path.join(REPO, "data", "_bt_full", "mins1")),
                               mins=a.mins, bars_db=a.bars_db, mode=os.getenv("BT_BAR_MODE", "5m"))
    hhmm_ref = {"hhmm": "09:15"}
    install_clock_shim()   # **回测放开交易时段门** ✓（账本 §9.242；否则 _check_* 整块不执行 ✗）
    (_blm.install_data_shims if getattr(market, "mode", "5m") == "1m" else install_data_shims)(market, hhmm_ref)
    fanout_shims()   # **补丁广播** ✓（账本 §9.246：from-import 会绑走原对象 ✗）
    import bt_local_pro
    bt_local_pro.set_query_fn(_q)
    localpro = bt_local_pro.install_local_pro(market, cut, relay_fn=shim.relay_items)
    # ── 账户口径：`--account` 必须同时驱动**监控侧**（否则 A/B 白跑）──
    # 2026-09-17 事故：A/B 三臂跑完 20 天，**成交 0 笔、触发行 0 条、权益一条直线**——
    #   根因是 `--account` 只切了**模拟盘引擎**的账户，而 `t_monitor.T_MONITOR_ACCOUNT`
    #   是模块级常量 `os.getenv("T_MONITOR_ACCOUNT", "stock")`，本文件从未据 `--account` 设置它
    #   ⇒ 监控/触发/网关全在 stock 上跑，引擎却在 corpusab_* 上 ⇒ 快照里什么都没有。
    # 修法：在 import 生产模块**之前**把环境变量对齐（t_monitor 在 import 时读它）。
    try:
        if getattr(a, "account", None):
            os.environ["T_MONITOR_ACCOUNT"] = str(a.account)
            print("[prod] 监控账户 T_MONITOR_ACCOUNT=%s（与 --account 对齐）" % a.account,
                  file=sys.stderr)
    except Exception as _ae:
        print("[prod] 监控账户对齐失败: %s" % str(_ae)[:80], file=sys.stderr)

    # ── 回测专属的**性能开关默认值**（在此处设，避免"要改 env 才能生效"⇒ 必须重启驱动）──
    # 为什么放这里：这些开关只对回测有意义，而 env 是在驱动启动时一次性 source 的；
    #   `jobs/bt_env_from_prod.env` 后来才补的开关，对**已在跑的**驱动无效（子进程继承旧环境）。
    #   本文件是回测驱动（生产 import 不到），故在此 `setdefault` 即可让**下一天**就生效，且生产零影响。
    # 2026-09-17：`WOLF_STOP_SCAN_ONCE_PER_BAR` —— 治 20260202 `_round`=675.4s / `stop_loss`=492.4s
    #   （该 (标的,bar) 重活被重复算 ~3 次、每次 ~310ms）。
    # ⚠️ 2026-09-18 用户指出："说了多少次跟狼大保持一致"——**回测默认口径就应该是语料口径**，
    #   而不是把语料开关默认关掉、只拿"老机制"跑年跑（此前 gen=7~11 全部跑的是老机制，
    #   等于把几小时算力花在一个已知不符合语料的口径上）。生产仍然零影响：本文件只被回测驱动。
    # ── 口径审计（2026-09-19 C）：把生效的 WOLF_* 逐条打出来，标明来源 ──
    #   [pins]     = 来自 jobs/bt_env_pins.sh（手工维护的回测覆盖层，回测与生产不同且有意为之）
    #   [prod-env] = 沿用生产镜像环境（bt_env_from_prod.py 生成）
    def _audit_env() -> None:
        try:
            _pins = set()
            _pf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jobs", "bt_env_pins.sh")
            if os.path.isfile(_pf):
                import re as _re
                for _ln in open(_pf, encoding="utf-8"):
                    _m = _re.match(r"\s*export\s+(WOLF_[A-Z0-9_]+)=", _ln)
                    if _m:
                        _pins.add(_m.group(1))
            _rows = sorted((k, v) for k, v in os.environ.items() if k.startswith("WOLF_"))
            _np = sum(1 for k, _ in _rows if k in _pins)
            print("[pins] 生效 WOLF_* 共 %d 条：覆盖层 %d 条、沿用生产镜像 %d 条"
                  % (len(_rows), _np, len(_rows) - _np), flush=True)
            for _k, _v in _rows:
                print("[pins] %-34s %-8s = %s" % (_k, "pins" if _k in _pins else "prod-env", _v), flush=True)
        except Exception as _e:
            print("[pins] 口径审计失败(忽略): %s" % str(_e)[:80], flush=True)

    _audit_env()
    for _k, _v in (("WOLF_STOP_SCAN_ONCE_PER_BAR", "1"),
                   ("WOLF_STOP_COND_CACHE", "1"), ("WOLF_PRESCREEN_CACHE", "1"),
                   # ── 语料口径开关：回测**默认开**（与狼大语料一致）──
                   ("WOLF_T_TARGET_CORPUS", "1"),      # 做T目标 3-4 点（ETF 2 点）
                   ("WOLF_T_GATE_CORPUS", "1"),        # 买腿资格(有底仓)+接活 regime_gate
                   # 位置门**显式关**：语料"指数 3900 以上不做T"是 2026-08 的行情坐标，不辐射全局
                   ("WOLF_T_POSITION_GATE", "0"),
                   ("WOLF_T_CAPACITY_2TIER", "1"),     # 底仓 ≥65% + 日内T ≤20%
                   ("WOLF_T_LEG_PRICE_FILL", "1"),     # 成交价尊重腿设计价（堵"同价来回"）
                   # ── 噪声/性能（与语料无关，但不该再逐 bar 重复写行）──
                   # ── 入场线（挂线 13/34/144 + 只在下跌里买 + 破线撤腿）──
                   # ⚠️ 2026-09-18 自查发现：这两个此前**只实现了、没在驱动里默认开** ⇒ 等于没做（用户批评过的同一类错误）
                   ("WOLF_T_ENTRY_LINES", "1"),
                   # ── 语料：止损/尾段/加仓（回测默认开，生产零影响）──
                   ("WOLF_INDEX_BREAKDOWN", "1"),      # 指数破位判定（收盘跌破 MA60/144/200 ≥2 根）
                   # 开盘不追高闸·条件单补线（2026-09-23）：条件单成交原先不传 trigger_id
                   #   ⇒ 网关两台闸（不追高/开盘快速拉升）全跳过；grep bug 见 t_monitor._oc_cond_buy_block。
                   ("WOLF_OC_COND_BUY", "1"),
                   # 形态腿量比补齐（2026-09-24）：回测替身不提供 vol_ratio，
                   #   导致放量破位门哑 + AI 无判据可依。
                   ("WOLF_VOL_RATIO_FILL", "1"),
                   # G4 成交的可观测性标记（2026-09-23）：只加 reason 前缀，不参与交易判定。
                   ("WOLF_G4_REASON_TAG", "1"),
                   ("WOLF_STOP_BY_INDEX_BREAK", "1"),  # 止损只有指数破位才允许 + 地量不割
                   ("WOLF_TAIL_PHASE", "1"),           # 尾段：不建仓/不加仓，只做T
                   ("WOLF_ADD_STAGED", "1"),           # 加仓：有利润垫 + 分步 75→90→100
                   ("WOLF_WEEKEND_HEDGE_EXEC", "1"),   # 周末/长假前避险执行层（挂接已完成）
                   # ── 暴跌应对（A 指数反抽失败降仓 / B 避开高标）：语料 01-30 原话，回测默认开 ──
                   ("WOLF_DERISK_EXEC", "1"),          # 总开关（执行层）
                   ("WOLF_DERISK_A", "1"),             # 指数层：反抽不上前收 → 收盘前压到 ≤40%
                   ("WOLF_DERISK_B", "1"),             # 主题/个股层：指数未破位但组合杀跌 → 减半高标
                   # 风控减仓腿豁免趋势约束（0129 实测：derisk_cut 8 条里 5 条被趋势约束否掉）
                   ("WOLF_TREND_VETO_RISKOFF", "1"),
                   # 挂单日终作废 → 释放冻结资金/股数（A 股当日有效；实测未成交买单冻结 2.6 万整窗）
                   ("WOLF_EXPIRE_STALE_ORDERS", "1"),
                   # 建仓规模三旋钮（单笔 15% / 缺口分 3 步足量 / 总底仓 65%）+ 买腿最小有效规模
                   ("WOLF_BUILD_SIZE_CORPUS", "1"),
                   ("WOLF_MIN_BUY_NOTIONAL", "8000"),
                   # 中段风控（档位判定+管控期折减+事件修正；已用语料回放校验 6/6）
                   ("WOLF_CHOP_GUARD", "1"),
                   # 换手治理（同价来回/下去不补/做T时段窗/跨日冷却；语料 playbook:148/287/309、blueprint:231/232）
                   ("WOLF_TURNOVER_GUARD", "1"),
                   # 候选腿质量闸【已撤（2026-09-18 用户"改回去"）】：实测它在**执行层**一开就零成交
                   #   （7/7 买腿被拦，0.78 无语料出处、成交额 5 亿是自造、振幅 3% 只适用 ETF）。
                   #   筛选应落在**选股/入池层**（rotation_switch_arm.pick_buy + wolf_direction_position），
                   #   分数只作排序键。此处显式置 0，保留实现备查（WOLF_LEG_QUALITY 库内默认本就是 0）。
                   ("WOLF_LEG_QUALITY", "0"),
                   # 入池硬筛选（选股层：方向趋势/波动；腿 theme → etf_share_flow 主题指标，阈值用分位）
                   # 入池硬筛选【已撤 2026-09-18 用户"停掉、对齐狼大"】：口径方向错——1 月半导体 d20=-12% 时
                   #   狼大恰在回补/加仓（xls2026:258+/:277+），而该闸把低分位主题整条剔掉；且实测几乎不剔（无效闸门）。
                   ("WOLF_PICK_CORPUS_GATE", "0"),
                   # 个股离场：浮亏+破5日线 ⇒ 底仓锚失效（语料「破5日线丢」）
                   ("WOLF_MA5_EXIT", "1"),
                   # 底仓锚「浮亏限定版」（2026-09-25 用户拍板；账本 §9.110/§9.111）：盈利票不豁免、浮亏≤−3%⇒锚归零
                   ("WOLF_BASE_FLOOR_LOSS_EXEMPT", "1"),
                   # 底仓锚防下调反馈（实测高抛腿把锚 733→400→200→100 啃光整仓）
                   ("WOLF_BASE_FLOOR_NO_DOWN", "1"),
                   # A2 严格锚：完全不下调底仓锚（floor 恒 = 累计买入×ratio，清仓才 reset）
                   ("WOLF_BASE_FLOOR_STRICT", "1"),
                   # B 卖侧也受"日内做T仓 20%"约束（止损/破位豁免）
                   ("WOLF_T_CAPACITY_SELL", "1"),
                   # 每日布腿上限（用户 2026-09-19 拍板 3→8；生产镜像 env 里是 3，靠 pins 覆盖）
                   ("WOLF_PICK_MAX_LEGS", "8"),
                   # 触发静默（无T仓可卖 / G8 上影线命中 ⇒ 当日该腿不再重复生成；只减噪不加判据）
                   ("WOLF_TRIGGER_MUTE", "1"),
                   # 直连取数缓存（本地缓存 + 硬超时6s + 负缓存）——治 0128 那种取数 502 拖 958s
                   ("WOLF_HTTP_CACHE", "1"),
                   # LLM 跨臂共享缓存（同 as-of + 同 prompt 的答复跨臂复用）
                   ("WOLF_LLM_SHARED_CACHE", "1"),
                   # 主题档位闸（confirmed 满档 / not_confirmed·suspect 半档）+ as-of 文件过滤
                   # 主营校验 dsh：拦"概念错配"（电子城被挂半导体、华泰股份被挂AI…）——语料要求
                   # 的板块内正宗度校验，数据缺口用 dsh 主营判定作代理 ⇒ 回测显式开；
                   # dsh=否 的标的另落 member_reject_<day>.jsonl 复核清单
                   ("WOLF_THEME_MEMBER_CHECK", "1"),
                   # 主题「主类」确定性判定（前置 dsh；无主类词才问 LLM）
                   ("WOLF_MAIN_CLASS_RULE", "1"),
                   # 执行口腿闸（只拦买入）：堵"建议买价/条件单"绕过布腿闸的路径
                   ("WOLF_EXEC_GATE", "1"),
                   # 浪型 live 优先（带日期的是历史回填产物；实测 0105/0115 读到 op=exit 封掉整日新开仓）
                   ("WOLF_WAVE_LIVE_FIRST", "1"),
                   # 陈旧环境产物护栏（内容级 as-of 校验：过期或未来 ⇒ 视为缺失 fail-open）
                   ("WOLF_STALE_ARTIFACT_GUARD", "1"),
                   ("WOLF_THEME_TIER_GATE", "1"),
                   ("WOLF_ASOF_FILE_FILTER", "1"),
                   # 弱市不进新票：指数破位 ⇒ 新票腿 0（做T照旧；语料"下跌趋势就不做/收盘破位才走"）
                   ("WOLF_INDEX_NEW_BUY", "1"),
                   # 选板块第一要素（量能活跃+资金无5日连续流出）：模块默认关、且"开启需拍板"，
                   # 却曾在回测 env 文件里被打开（0105→0116 吃掉 36/82 条候选）⇒ 回测显式关
                   ("WOLF_THEME_VOLFUND_GATE", "0"),
                   ("WOLF_T_DECLINE_ONLY", "1"),
                   ("WOLF_PERSIST_KIND_THROTTLE", "1"),
                   ("WOLF_PERSIST_LEG_COOLDOWN", "1"),
                   # 板块权限前置过滤（2026-09-19 用户拍板 A）：把账户买不到的板（创业板/科创板/北交所）
                   # 在**选股/布腿阶段**就剔掉，而不是等 arm 才 ARM_SKIP_BOARD。
                   # ⚠️ 默认 **0**：jan10（B-only 单臂）必须逐位不变；jan11 版由 runner 显式 export 1。
                   ("WOLF_PICK_BOARD_EARLY", "0"),
                   # 不追高（确定性闸；语料 2025-04-03「冲上去一定不能追」/2025-10-09「任何时候不追高追涨」
                   # + 台账 §29.1「不在开盘追，想追延后到 14:00–14:30」）。库内默认 0 ⇒ 生产逐位不变。
                   ("WOLF_NO_CHASE", "1"),
                   # 底仓穿透（破位/减仓语义的卖腿可减底仓；语料 2025-11-23/2025-08-13）。
                   # 库内默认 0 ⇒ 生产逐位不变。
                   ("WOLF_SELL_BASE_EXEMPT", "1"),
                   # 个股结构门（蓝图:149「下跌不做」）：买腿在「破 MA20 且 MA10<MA20」的下跌结构里不执行。
                   # 库内默认 0 ⇒ 生产逐位不变。
                   ("WOLF_STOCK_TREND_GATE", "1"),
                   # 放量破位门（254 低吸要求「破前低+缩量」；放量破前低 = 杀跌不接）。库内默认 0。
                   ("WOLF_DIP_VOL_GATE", "1")):
        os.environ.setdefault(_k, _v)

    # ── as-of 外部取数接入（2026-09-17，仅回测；`BT_ASOF_FETCH=1` 才生效）──────────
    # 把 t_monitor 的日线兜底与雪球引擎行情换成"带 as-of 的 datahubco 取数"（进程内 monkeypatch）。
    # 默认关 ⇒ 行为与以前逐位一致；生产进程 import 不到该模块 ⇒ 零影响。
    try:
        if str(os.getenv("BT_ASOF_FETCH", "0")).strip().lower() in ("1", "true", "yes", "on"):
            import bt_asof_fetch as _baf
            _hk = _baf.install_backtest_hooks()
            print("[prod] as-of 外部取数接入: %s" % _hk, file=sys.stderr)
            # AI 决策上下文的 as-of 守卫（2026-09-17）：`t_ai_actions` 夹到回放日 + SQL CURRENT_DATE 对齐。
            # 不加这层，回放的提示词里会出现**未来交易日 / 上一跑**的决策与胜率统计（未来函数 + 逐跑不可比）。
            _aig = _baf.install_ai_context_guard()
            print("[prod] AI 决策上下文 as-of 守卫: %s" % _aig, file=sys.stderr)
            _ASOF_STATS = _baf.stats            # 日终打网关计数（hook 服务 vs 回退）
        else:
            print("[prod] as-of 外部取数接入: 未开启（BT_ASOF_FETCH=0，走原路径）", file=sys.stderr)
    except Exception as _ae:
        print("[prod] as-of 外部取数接入失败（继续走原路径）: %s" % str(_ae)[:120], file=sys.stderr)

    # ④ 生产模块  —— 顺带把"持续腿冷却/节流"开关**在启动时打出来**（此前没有任何标志行，
    # 无法区分"开关没传进来"与"判据没命中"，2026-09-18 吃过这个亏）
    import app.services.t_db as t_db
    try:
        import app.services.t_monitor as _tm0
        print("[prod] 持续腿冷却/节流开关: 冷却=%s(%ss) 节流=%s(%sbar) PERSIST_KINDS=%s"
              % (_tm0.PERSIST_LEG_COOLDOWN, _tm0.PERSIST_COOLDOWN_SEC,
                 _tm0.PERSIST_KIND_THROTTLE, _tm0.PERSIST_THROTTLE_BARS, ",".join(_tm0.PERSIST_KINDS)),
              file=sys.stderr)
    except Exception as _e0:
        print("[prod] 持续腿开关回显失败: %s" % str(_e0)[:80], file=sys.stderr)
    _forbid_bridge()
    from app.services import alert_hub as _ahP
    _ahP.install()                      # 全局异常钩子（用户「统一走QQ推送」✓；默认关 ✓）
    from app.services.t_monitor import TMonitor
    from app.services.t_gateway import gateway_execute, get_sellable_ledger  # noqa: F401

    # ④b 交易腿 agent 接线（`--agent on`）：**import 之后、bar 循环之前**装 LLM 录制/回放层，
    #     再把生产消费循环建好。off 时 agent_loop / llm_replay 都是 None → 逐 bar 零额外调用。
    agent_loop = None
    llm_replay = None
    if a.agent == "on":
        import app.services.t_bridge as t_bridge
        import app.services.t_ai_agent as t_ai_agent
        import bt_agent_loop
        from bt_llm_replay import LLMReplay
        # ⚠️ 生产 `t_bridge.wake_agent` 用的是 **urllib.request.urlopen**（不是 requests）→
        #    install() 必须同时换掉 requests 面与 urllib 面，否则"装了回放层却还在真外呼"。
        llm_replay = LLMReplay(agent="t_leg", as_of=day, cache_dir=a.agent_cache, mode=a.agent_mode)
        llm_replay.install(t_bridge)
        llm_replay.install(t_ai_agent)
        # ④c as-of「工具通道」修复（默认 on）：dsh 侧工具请求我们拦不到（协议只有 message/session_id/
        #     mode/model/thinking_level，工具在容器内执行、打的是生产 MARCUS_API_URL）→ 改为
        #     ① 在 message 里声明"回测口径、工具禁用"并改写生产提示词里的调工具邀请句；
        #     ② 把 AI 会去查的行情/持仓/分钟线/指标/大盘按 as-of 算好塞进提示词（= 工具结果替身）。
        #     详见 jobs/bt_agent_tools.py 的"为什么是提示词注入"。
        tool_guard = None
        if a.agent_tool_guard == "on":
            import bt_agent_tools
            svc = bt_agent_tools.AsOfToolService(market, hhmm_ref, day, account=a.account,
                                                 db_url=LOCAL_DB_URL, verbose=True)
            # 默认后缀带**本次运行的 pid**：`time.strftime` 在本驱动里已被钉到模拟时钟（同一天
            # 每次跑都一样）→ 不能用它做运行令牌；pid 天然一次性（且不进提示词指纹，回放不受影响）。
            _stag = "" if a.agent_session_tag_off else (
                a.agent_session_tag or ("btasof-%s-r%s" % (day, os.getpid())))
            tool_guard = bt_agent_tools.BacktestToolGuard(svc, day, enabled=True, verbose=True,
                                                          session_tag=_stag or None)
            tool_guard.guard_name = "t_leg_asof_tools"
            tool_guard.guard_version = bt_agent_tools.GUARD_VERSION
            llm_replay.set_prompt_guard(tool_guard.decorate)
            print("[prod] as-of 工具口径守卫已启用（%s）：每次 /chat 前注入 as-of 数据块 + 禁用工具声明；"
                  "会话后缀=%s" % (bt_agent_tools.GUARD_VERSION, tool_guard.session_tag or "(无)"),
                  file=sys.stderr)
        else:
            print("[prod] ⚠️ as-of 工具口径守卫 **关闭**（--agent-tool-guard off）："
                  "AI 工具通道会拿到生产实时数据 → PIT 泄漏，仅供对照", file=sys.stderr)
        agent_loop = bt_agent_loop.BtAgentLoop(
            account=a.account, consumer="bt-fallback", timeout_seconds=300,
            all_pending=a.agent_all_pending, max_total=a.agent_max_total, guard=tool_guard)
        # ⚠️ 2026-09-20：原先这里打的是 `run_since`（**真实 epoch**）——用 id 水位认领后该数字**无意义**
        #   且误导（"本次运行产生的 pending(>=1789813474.5)"）。改为打印真正生效的口径与水位。
        print("[prod] 交易腿 agent 已接线：url=%s mode=%s cache=%s 认领范围=%s（filter=%s wm=%s 水位=%s）"
              % (a.agent_url, a.agent_mode, a.agent_cache,
                 "全部 pending" if a.agent_all_pending else "本次运行产生的 pending",
                 getattr(agent_loop, "claim_filter", "?"), getattr(agent_loop, "claim_wm", "?"),
                 "未取(首认领时懒取)" if agent_loop._wm is None else agent_loop._wm), file=sys.stderr)
        print("[prod] LLM 拦截面：%s" % llm_replay.installed, file=sys.stderr)

    if a.reset:
        _reset_paper_account(a.account, a.initial, day=a.day)

    # ── 挂单日终作废（A 股当日有效）→ 释放上一日及更早未成交挂单冻结的资金/股数 ──
    # 2026-09-18 用户："建仓/冻结资金释放修复下"。实测两臂各有一笔未成交买单把 2.6 万冻结整窗。
    try:
        import psycopg2 as _pgx
        from app.services import t_order_expiry as _oex
        _xconn = _pgx.connect(LOCAL_DB_URL)
        _xconn.autocommit = True
        _xst = _oex.expire_stale_orders(conn=_xconn, account_id=a.account,
                                        today=str(a.day).replace("-", "")[:8])
        _xconn.close()
        if _xst.get("scanned"):
            print("[prod] 挂单日终作废: %s" % _xst, file=sys.stderr)
    except Exception as _xoe:
        print("[prod] 挂单作废失败: %s" % str(_xoe)[:100], file=sys.stderr)

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
                    # ⑱ 趋势/突破腿（2026-09-21）：现价 ≥ 突破位 且 不追高（≤ +3%）才成交。
                    if str(L.get("src") or "") == "trend" and L.get("level"):
                        try:
                            _p3 = os.path.join(REPO, "apps", "main_line")
                            if _p3 not in sys.path:
                                sys.path.insert(0, _p3)
                            import trend_channel as _TC3
                            rT = rsa.arm(conn, cur, sym, "trend_break_buy", "buy",
                                         _TC3.expr(L["level"]), day)
                            armed.append({"symbol": sym, "src": "trend", "trend": rT,
                                          "level": L.get("level")})
                        except Exception as _eT:
                            print("TREND_ARM_ERR %s %s" % (sym, str(_eT)[:80]), file=sys.stderr)
                        continue
                    # ★ 埋伏腿（2026-09-26 用户「做掉」）：`stage=ambush` 的腿走**独立腿型**
                    #   `wolf_ambush_buy` ✓ —— 语义是「低位方向：找辨识度最高的老龙头**埋伏**」✓
                    #   （**先手建仓**，不是摊薄 ✗）⇒ 网关侧已豁免「低吸只在趋势票上做」那道闸 ✓
                    #   为什么单列：普通分支只布 m5dump/prevlow 两种腿型 ✗ ⇒ 埋伏腿拿不到自己的身份 ✓
                    if str(L.get("stage")) == "ambush" or str(L.get("type")) == "wolf_ambush_buy":
                        #   历史（T35 全窗实测）：挂的"大盘急杀"式条件（`index.m5_dump ≥ 0.4`）
                        #   **只触发 3 次** ✗ ⇒ 21 条条件单全部 expired、**0 次成交** ✗
                        # 2026-09-26 用户「改掉」：**去掉自设的 253（大盘急杀 ✗ 无语料依据）**，
                        #   只留 **254（`quote.dip_prev_low` 触及前低 + 温和缩量 ≤0.9 ✓）** ——
                        #   语料口径「**不破前低 + 地量缩量**」（docs/wolf-dip-entry-rule.md，
                        #   2026-02-02 原话「大盘没过前低是前提，个股也没低于前低是基础条件」）✓
                        #   实测：253 全窗仅触发 3 次（近乎惰性 ✗）、254 触发 155 次 ✓
                        rP = rsa.arm(conn, cur, sym, "wolf_ambush_buy", "buy", rsa.ambush_expr(), day)
                        armed.append({"symbol": sym, "src": "ambush", "ambush_prev": rP})
                        continue
                    if L.get("type") == "buy_253/254" or L.get("src") == "switch_builder_0818":
                        r1 = rsa.arm(conn, cur, sym, "custom_m5dump", "buy", rsa.buy_253_expr(), day)
                        r2 = rsa.arm(conn, cur, sym, "custom_prevlow", "buy", rsa.BUY_254_EXPR, day)
                        armed.append({"symbol": sym, "src": "switch_0818", "253": r1, "254": r2})
                    else:
                        r1 = rsa.arm(conn, cur, sym, "custom_m5dump", "buy", rsa.buy_253_expr(), day)
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
            fanout_shims()   # **每日再广播一次** ✓（模块是随后才 import 的 ✗；幂等 ✓ 账本 §9.246）
            mon.run_discipline_checks()   # **纪律家族统一入口** ✓（账本 §9.243；否则埋伏纪律等全不执行 ✗）
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

    # ⑥b **无行情硬校验**：监控名单里任一标的当日无 bars → 大声报（绝不静默跳过）
    #    为什么：`TMonitor._round` 里 `if not quote or not quote.get("current"): continue` 会**静默跳过**
    #    没有行情的标的 → 数据缺口会被伪装成"策略没触发"（实测 588 标的日中 196 个缺分钟）。
    _missing_bars = []
    for _s in symbols:
        if not market.bars_upto(_s, "15:00"):
            _missing_bars.append(_s)
    if _missing_bars:
        print("[prod] ⚠️ %s 有 %d/%d 个监控标的**当日无行情**（这些标的整日不会被评估，"
              "成交/离场都会缺）: %s" % (day, len(_missing_bars), len(symbols), ",".join(_missing_bars[:12])),
              file=sys.stderr)

    # ⑦ 逐 bar 驱动
    bars = list(_blm.BAR_MINUTES_1M if getattr(market, "mode", "5m") == "1m" else BAR_MINUTES)
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
    # ── 当日批量预取（放在标的集合已确定之后；2026-09-17 修：原先放在 symbols 定义之前 → NameError，从未生效）──
    if str(os.getenv("BT_ASOF_FETCH", "0")).strip().lower() in ("1", "true", "yes", "on"):
        try:
            import bt_asof_fetch as _baf_pf
            _pf = _baf_pf.prefetch_day(day, bars[-1], list(symbols) + list(idx_syms),
                                       max_symbols=int(os.getenv("BT_ASOF_PREFETCH_MAX", "40")))
            print("[prod] as-of 预取: %s" % _pf, file=sys.stderr)
        except Exception as _pfe:
            print("[prod] as-of 预取失败（继续，循环内按需取）: %s" % str(_pfe)[:100], file=sys.stderr)

    for hhmm in bars:
        set_now(day, hhmm)
        write_asof_state(day, hhmm, day_dir, getattr(a, "account", ""))
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
        # ⑦a 交易腿 agent 消费循环（生产 `t_bridge.fallback_poll_loop` 的等价物）：
        #     放在**检查链末尾**（`_check_logic_time_stop()` 之后）→ 本根 bar 新写出的 pending
        #     当根就被认领/唤醒决策，不再跨 bar 堆积。
        _ag_delta = None
        if agent_loop is not None:
            _t0 = time.time()
            try:
                _ag_delta = agent_loop.poll()
            except Exception as _ae:
                import traceback
                print("[prod] %s %s agent 循环异常：%s" % (day, hhmm, str(_ae)[:200]), file=sys.stderr)
                traceback.print_exc()
            step_secs["_agent_loop"] = step_secs.get("_agent_loop", 0.0) + (time.time() - _t0)
            if _ag_delta and _ag_delta.get("claim"):
                print("[prod] %s agent 认领 %d 条 → exec=%d wait=%d abandon=%d update_condition=%d wake_failed=%d"
                      % (hhmm, _ag_delta.get("claim", 0), _ag_delta.get("exec", 0),
                         _ag_delta.get("wait", 0), _ag_delta.get("abandon", 0),
                         _ag_delta.get("update_condition", 0), _ag_delta.get("wake_failed", 0)),
                      file=sys.stderr)
        n1 = _count_triggers(a.account)
        nt = _count_trades(a.account)
        _bar_row = {"hhmm": hhmm, "triggers": n1 - n0, "trades_total": nt}
        if _ag_delta is not None:      # `--agent off` 时**不新增字段**（bars 与旧输出逐字节相同）
            _bar_row["agent"] = {"claim": _ag_delta.get("claim", 0), "exec": _ag_delta.get("exec", 0),
                                 "wait": _ag_delta.get("wait", 0), "abandon": _ag_delta.get("abandon", 0),
                                 "update_condition": _ag_delta.get("update_condition", 0),
                                 "wake_failed": _ag_delta.get("wake_failed", 0)}
        log.append(_bar_row)
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

    # ⑦c 日终交易腿 agent 统计（认领/exec/wait/abandon/update_condition/wake_failed + LLM 录放）
    agent_res: Dict[str, Any] = {"enabled": False}
    if agent_loop is not None:
        agent_res = {"enabled": True, "url": a.agent_url, "llm_mode": a.agent_mode,
                     "cache_dir": a.agent_cache,
                     "window": "all_pending" if a.agent_all_pending else "run_window",
                     "run_since": agent_loop.run_since,
                     "llm": (llm_replay.summary() if llm_replay is not None else None)}
        agent_res.update(agent_loop.summary())
        if tool_guard is not None:
            agent_res["tool_guard"] = tool_guard.summary()
            agent_res["tool_calls"] = tool_guard.tool_calls     # 每次注入的 as-of「工具结果替身」
        else:
            agent_res["tool_guard"] = {"enabled": False}
        print("[prod] 交易腿 agent 日统计：认领 %d / exec %d / wait %d / abandon %d / "
              "update_condition %d / rule_fallback %d / wake_failed %d / error %d / 跳过非本账户 %d"
              % (agent_res["claim"], agent_res["exec"], agent_res["wait"], agent_res["abandon"],
                 agent_res["update_condition"], agent_res["rule_fallback"],
                 agent_res["wake_failed"], agent_res["error"], agent_res["skipped_other_account"]),
              file=sys.stderr)
        print("[prod] agent 触发后状态分布：%s" % agent_res.get("by_status"), file=sys.stderr)
        if tool_guard is not None:
            _tg = agent_res["tool_guard"]
            print("[prod] as-of 工具口径：改写 %d 次请求 / 供给 as-of 数据 %d 项 / 各工具 %s / 无 as-of 版本 %s"
                  % (_tg["n_llm_requests_decorated"], _tg["n_tool_datasets_served"], _tg["by_tool"],
                     _tg["unavailable_tools"]), file=sys.stderr)
            for _tc in tool_guard.tool_calls:
                print("[prod]   tool_call #%s %s %s(%s) ← %s | as-of 字段: %s"
                      % (_tc["seq"], _tc["hhmm"], _tc["tool"],
                         json.dumps(_tc["args"], ensure_ascii=False), _tc["source"],
                         ",".join(_tc["asof_fields"]) or "-"), file=sys.stderr)
        if llm_replay is not None:
            _ls = agent_res["llm"]
            print("[prod] LLM 录放：mode=%s hit=%d recorded=%d miss=%d 缓存条数=%d 本次未用=%d "
                  "漂移告警=%d 错误=%d → %s"
                  % (_ls["mode"], _ls["hit"], _ls["recorded"], _ls["miss"], _ls["cache_records"],
                     _ls["unused"], len(_ls["warnings"]), len(_ls["errors"]), _ls["cache_file"]),
                  file=sys.stderr)

    # ⑧ 汇总
    res = {"day": day, "cut": cut, "symbols": symbols, "armed": armed,
           "triggers": _trigger_rows(a.account), "trades": _trade_rows(a.account),
           "positions": _position_rows(a.account), "account": _account_info(a.account),
           "bars": log, "market_missing": sorted(market.missing),
           "decision": {"cut": cut, "ok": dec_res.get("ok"),
                        "allowed": ((((dec_res.get("layers") or {}).get("L5_entry") or {}).get("value") or {}).get("allowed")),
                        "missing": dec_res.get("missing")},
           "missing_bars": _missing_bars,
           "agent": agent_res,
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
    try:
        _wall = time.perf_counter() - float(_REAL_EPOCH.get("perf0") or 0.0)
    except Exception:
        _wall = -1.0
    print("[prod] 当日**真实墙钟** %.1fs（perf_counter ✓；含 agent/LLM 等待 ✓）"
          % _wall, file=sys.stderr)
    print("[prod] 各步累计耗时(s)【⚠️ 模拟时钟口径 ✗ —— `time.time()` 已被钉到交易日/当前 bar，"
          "**不等于真实耗时** ✓；真实值看上面那行】：%s" % {k: round(v, 1) for k, v in
          sorted(step_secs.items(), key=lambda kv: -kv[1])[:10]}, file=sys.stderr)
    # `_round` 细分计时（2026-09-17 用户要求装）：日终打印各段耗时，按数据决定下一步优化
    try:
        import app.services.t_monitor as _tm
        _rt = _tm.round_timing()
        _seg = " ".join("%s=%.1f" % (k, v) for k, v in _rt["secs"].items())
        print("[prod] _round 细分(s)：%s | bars=%d triggers=%d"
              % (_seg, _rt["bars"], _rt["triggers"]), file=sys.stderr)
        # 止损扫描"是否空转"必须与耗时同屏：缓存省了多少 + 扫描异常条数（>0 = 止损判定被吞，绝不等于优化）
        _sc = _rt.get("stop_cond_cache") or {}
        try:
            _pk = _tm.persist_throttle_stats()
            print("[prod] 持续腿节流: 冷却=%(enabled_cooldown)s/%(cooldown_sec)ss 节流=%(enabled_throttle)s/"
                  "%(throttle_bars)sbar | 进入判定=%(eval)s 带last_triggered_at=%(have_lt)s "
                  "冷却跳过=%(cooldown_skip)s **节流跳过=%(throttle_skip)s**" % _pk, file=sys.stderr)
            _tm.persist_throttle_reset()
        except Exception as _pe:
            print("[prod] 持续腿节流统计打印失败: %s" % str(_pe)[:80], file=sys.stderr)
        print("[prod] 止损破位门跳过=%s ｜ 语料门拦买腿=%s ｜ 避险=%s"
              % (_rt.get("stop_gate_skip"), _rt.get("gate_corpus_skip"), _rt.get("weekend_hedge")),
              file=sys.stderr)
        try:
            _dr = _rt.get("derisk") or {}
            print("[prod] 暴跌应对: A=%(a)s(触发%(ta)s次) B=%(b)s(触发%(tb)s次) 计划卖腿=%(sells)s 已下=%(sd)s "
                  "仓位=%(pos_pct)s%%→目标%(cut_to)s 过点=%(ready)s | A理由=%(_ar)s | B理由=%(_br)s"
                  % {"a": _dr.get("a"), "ta": _dr.get("a_trigger"), "b": _dr.get("b"),
                     "tb": _dr.get("b_trigger"), "sells": _dr.get("sells"), "sd": _dr.get("sells_done"),
                     "pos_pct": _dr.get("pos_pct"), "cut_to": _dr.get("cut_to"), "ready": _dr.get("ready"),
                     "_ar": (_dr.get("a_reason") or "-")[:60], "_br": (_dr.get("b_reason") or "-")[:50]},
                  file=sys.stderr)
        except Exception as _de:
            print("[prod] 暴跌应对打印失败: %s" % str(_de)[:80], file=sys.stderr)
        print("[prod] 持续腿冷却跳过=%s ｜ 语料门拦买腿=%s"
              % (_rt.get("persist_cooldown_skip"), _rt.get("gate_corpus_skip")), file=sys.stderr)
        print("[prod] 止损条件缓存 hit=%s miss=%s | 止损扫描异常=%s%s"
              % (_sc.get("hit"), _sc.get("miss"), _rt.get("stop_scan_err"),
                 ("（首例 %s）" % _rt.get("stop_scan_first")) if _rt.get("stop_scan_err") else ""),
              file=sys.stderr)
        _tm.round_timing_reset()
        try:
            _tm.stop_scan_reset()       # 异常计数与耗时同频清零
        except Exception:
            pass
    except Exception as _te:
        print("[prod] _round 细分打印失败: %s" % str(_te)[:80], file=sys.stderr)
    try:
        import bt_asof_fetch as _baf2
        _st = _baf2.stats()
        if sum(int(v) for v in _st.values()) > 0:
            print("[prod] as-of 取数网关计数: %s" % _st, file=sys.stderr)
        # AI 上下文 as-of 守卫的日终计数：夹掉了多少未来/他跑的决策行、改写了多少条 SQL 真钟
        _pit, _sq = _baf2.ai_pit_stats(), _baf2.sql_clock_stats()
        if _pit.get("calls") or _sq.get("rewrites"):
            print("[prod] AI as-of 守卫: t_ai_actions 查询 %s 次 / 取回 %s 行 / **夹掉未来或他跑 %s 行** | "
                  "SQL 真钟改写 %s 次（错误 %s）"
                  % (_pit.get("calls"), _pit.get("fetched"), _pit.get("cut"),
                     _sq.get("rewrites"), _sq.get("errors")), file=sys.stderr)
    except Exception:
        pass
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
