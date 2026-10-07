# -*- coding: utf-8 -*-
"""bt_pg_backfill_diagnosis.py — 按日重放**生产端点** `GET /market/market-diagnosis`，把本地 PG 副本里
`market_diagnosis` 表在回测窗口内**缺失的交易日**补齐（PIT、可重复、只补缺日）。

## 背景（为什么）
`backend/app/api/market.py:1665 get_market_diagnosis()` 是 `market_diagnosis` 的**唯一写入方**
（`_save_market_diagnosis()` → ORM `MarketDiagnosis`，主键 `trade_date`），由调度任务
`config/tasks.yaml` 的 `morning_diagnosis`（cron `10 9 * * *` → `jobs/morning_diagnosis.py` → HTTP 调用该端点）
每天 09:10 触发。该任务 2026-07-15 才上线 ⇒ 生产库与本地副本里都**只有 45 行（20260715–20260916）**，
回测窗口 20260105–20260714 的空档在任何地方都没有数据可抄。

而 `t_regime._read_market_diagnosis()`（环境闸门 L1）与 `trade_graph._read_market_regime()` /
`api/indicator._get_market_regime_for_calc()` 都按 `trade_date = 当天` 查这张表：
  · 无行时 `t_regime` 把 state 当 `trend` → `regime_day = ACTIVE`（"环境闸门全期失真"）；
  · 无行时 `trade_graph` 返回 `("unknown", …, "⚠️ 今日尚未执行盘前诊断（9:10)…")`。

## 本脚本做什么
对窗口内每个交易日 D：**钉钟到 D 09:10**，用**同一份生产计算函数** `get_market_diagnosis()` 重算，
再把结果**按模型同字段**写进本地 PG。生产计算函数被原样调用（不是重写一份），只做两处**运行期替身**：
  1. `market._get_tushare_pro` → `PitPro`（本地/PIT 数据源，见下表）；
  2. `market._save_market_diagnosis` → 收集器（不落库），落库由本脚本按 `--start/--end/--dry-run` 控制，
     且**硬保护** `trade_date >= --protect-from`（默认 20260715）的真实行一律跳过。

### 数据源清单（`get_market_diagnosis()` 读了什么 / 回放时怎么供）
| 指标 | 生产调用 | 回放数据源 | 断网可用 |
|---|---|---|---|
| ① 平均振幅 | `pro.daily(trade_date=T-1, fields='ts_code,amount')` + `pro.daily(ts_code=batch,…)` | **本地** `data/_bt_full/bars.sqlite`（≤ cut） | ✅ |
| ② 连阳/连阴 | `pro.index_daily('000001.SH', …)` | `core.tushare_relay`（**联网**；磁盘缓存） | ❌ |
| ③ 板块轮动 | `pro.index_daily(10 个申万 L1, …)` | 同上 | ❌ |
| ④ 涨跌停比 | `pro.limit_list_d(trade_date=T-1, limit_type='U,D')` | 同上（**复刻 relay 原样行为**：该调用实测返回 0 行 → limit_up/down=0） | ❌ |
| ⑤ MA5 | 同 ② | 同 ② | ❌ |
| ⑥ 风格轮动（不影响 state） | `pro.index_classify` + `pro.index_daily(basket)` + `pro.moneyflow_ind_dc` | 同上 | ❌ |

### PIT（不许看到未来）
`cut = D 的前一个交易日`（= 生产 09:10 时 Tushare 能拿到的最新数据）；替身对**所有**返回行强制
`trade_date <= cut`。`limit_list_d` 用区间预取 + 按 cut 截断，行为与生产当日调用一致。

### 用法
    # 干跑 3 天（不写库）
    .venv/bin/python jobs/bt_pg_backfill_diagnosis.py --start 20260105 --end 20260310 --dry-run --limit 3
    # 补齐窗口内缺失日（默认 20260105–20260714，跳过 ≥20260715 的真实行）
    .venv/bin/python jobs/bt_pg_backfill_diagnosis.py
    # 一致性校验：对生产真实行所在区间抽 N 天重算，与库里真实行逐字段对比（**不写库**）
    .venv/bin/python jobs/bt_pg_backfill_diagnosis.py --verify-real 6
    # 覆盖度/state 分布
    .venv/bin/python jobs/bt_pg_backfill_diagnosis.py --report
环境变量：`BT_PG_URL`/`DATABASE_URL`（默认本地副本）、`BT_BARS_DB`。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
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


JOBS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(JOBS)
DEFAULT_PG = os.getenv("BT_PG_URL") or os.getenv("DATABASE_URL") \
    or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
DEFAULT_BARS_DB = os.getenv("BT_BARS_DB") or os.path.join(REPO, "data", "_bt_full", "bars.sqlite")
DEFAULT_CACHE = os.path.join(REPO, "data", "_bt_year", "_summary", "diag_replay_cache")
PROTECT_FROM = "20260715"          # ≥ 该日期的行 = 生产真实行，绝不改写
SW_SECTORS = ["801080.SI", "801180.SI", "801120.SI", "801750.SI", "801730.SI",
              "801880.SI", "801050.SI", "801760.SI", "801150.SI", "801230.SI"]
IDX_COLS = ["ts_code", "trade_date", "close", "open", "high", "low", "pre_close",
            "change", "pct_chg", "vol", "amount"]
DAILY_COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
              "pct_chg", "vol", "amount", "total_mv", "turnover_rate"]
LIMIT_COLS = ["trade_date", "ts_code", "industry", "name", "close", "pct_chg", "amount",
              "limit", "limit_times", "up_stat"]


# ══════════════════════════ 钉钟（as-of） ══════════════════════════
_ORIG: dict = {}


def pin_clock(day8: str, hhmm: str = "0910"):
    """把"现在"钉到 day8 的 hhmm（本地时区）。

    与 `jobs/bt_run_pinned.py::pin_clock` 同思路，但**保留原类**并允许**反复钉不同日期**
    （bt_run_pinned 的实现在第二次调用时会把已打补丁的类当"原类"，故不能复用）。
    约束：① 先预热 numpy/pandas 再打补丁（C 扩展看 date/datetime 的尺寸）；② 返回**真** date/datetime 实例。
    """
    import time as _time
    if not _ORIG:
        _ORIG.update({"date": _dt.date, "datetime": _dt.datetime, "time": _time.time,
                      "localtime": _time.localtime, "gmtime": _time.gmtime,
                      "strftime": _time.strftime, "ctime": _time.ctime, "asctime": _time.asctime})
        try:
            import numpy  # noqa: F401
            import pandas  # noqa: F401
        except Exception as _e_sil1:
            _silent_alert("bt_pg_backfill_diagnosis.py:96", _e_sil1)
    RD, RDT = _ORIG["date"], _ORIG["datetime"]
    d = RDT(int(day8[:4]), int(day8[4:6]), int(day8[6:8]), int(hhmm[:2]), int(hhmm[2:4]), 0)

    class _Date(RD):
        @classmethod
        def today(cls):
            return RD(d.year, d.month, d.day)

    class _Datetime(RDT):
        @classmethod
        def now(cls, tz=None):
            return RDT(d.year, d.month, d.day, d.hour, d.minute, 0)

        @classmethod
        def today(cls):
            return cls.now()

        @classmethod
        def utcnow(cls):
            return RDT.utcnow()

    _dt.date, _dt.datetime = _Date, _Datetime
    _time.time = lambda: _ORIG["time"]() + (d.timestamp() - _ORIG["time"]())
    st = _time.struct_time((d.year, d.month, d.day, d.hour, d.minute, 0, 0, 0, -1))
    _time.localtime = lambda *a: st
    _time.gmtime = lambda *a: st
    _time.ctime = lambda *a: _ORIG["strftime"]("%a %b %d %H:%M:%S %Y", st)
    _time.asctime = lambda *a: _ORIG["strftime"]("%a %b %d %H:%M:%S %Y", st)
    _time.strftime = lambda fmt, t=None: _ORIG["strftime"](fmt, st if t is None else t)
    return d


# ══════════════════════════ PIT 数据替身 ══════════════════════════
def _date_chunks(d0: str, d1: str, days: int = 25):
    """把 [d0,d1] 按日历天切块。

    ⚠️ relay 单次翻页上限 6 页（datahubco ≈ 30000 行）——一次性拉 170 天的 `moneyflow_ind_dc`
    （每天 ~330 个概念）会被**静默截断**（实测日志：`翻页达上限 6 页（已取 30000 行）`）→
    早期日期拿不到资金流。分块取可绕开。
    """
    cur = _dt.datetime.strptime(d0, "%Y%m%d")
    end = _dt.datetime.strptime(d1, "%Y%m%d")
    while cur <= end:
        nxt = min(cur + _dt.timedelta(days=days - 1), end)
        yield cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")
        cur = nxt + _dt.timedelta(days=1)


class DiskCache:
    """联网取数的磁盘缓存（可复跑/可审计）：key → JSON rows。"""

    def __init__(self, root: str, enabled: bool = True):
        self.root = root
        self.enabled = enabled
        self.hits = self.misses = 0
        if enabled:
            os.makedirs(root, exist_ok=True)

    def _p(self, key: str) -> str:
        return os.path.join(self.root, key.replace("/", "_").replace(",", "-")[:180] + ".json")

    def get(self, key: str, fetch):
        """返回 `(fields, items)`。

        ⚠️ **必须连 relay 返回的字段名一起缓存**：relay 各接口的列顺序不固定（实测 `limit_list_d`
        的 `limit` 在第 17 列而不是第 7 列），按位置猜列会把"涨跌停家数"读成 0。
        """
        p = self._p(key)
        if self.enabled and os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    d = json.load(fh)
                self.hits += 1
                if isinstance(d, dict):
                    return d.get("fields"), d.get("items") or []
                return None, d
            except Exception as _e_sil2:
                _silent_alert("bt_pg_backfill_diagnosis.py:174", _e_sil2)
        self.misses += 1
        fields, items = fetch()
        if self.enabled:
            try:
                with open(p, "w", encoding="utf-8") as fh:
                    json.dump({"fields": fields, "items": items}, fh, ensure_ascii=False)
            except Exception as _e_sil3:
                _silent_alert("bt_pg_backfill_diagnosis.py:182", _e_sil3)
        return fields, items


class PitPro:
    """`tushare.pro.client.DataApi` 的最小替身：只实现 `get_market_diagnosis()` 用到的方法。

    · 个股日线 → 本地 `bars.sqlite`（≤ cut，无网）；
    · 指数日线 / 涨跌停 / 概念资金流 / 申万分类 → `core.tushare_relay`（联网，磁盘缓存），一律 ≤ cut；
    · `limit_mode='two-call'`（默认）= 把 `limit_type='U,D'` 拆成 U、D 两次过滤再合并 —— **与生产存量真实行
      的 limit_up/limit_down 一致**（本机 relay 对 `'U,D'` 直接返回 0 行，见 `--limit-mode relay` 对照）；
      `'relay'` = 原样复刻本机 relay 的行为（0 行），仅作敏感性对照。
    """

    def __init__(self, bars_db: str, cache: DiskCache, cut: str, fetch_start: str, fetch_end: str,
                 limit_mode: str = "two-call", verbose: bool = False):
        import pandas as pd                       # noqa: F401
        self.pd = pd
        self.bars_db = bars_db
        self.cache = cache
        self.cut = str(cut)
        self.fetch_start, self.fetch_end = str(fetch_start), str(fetch_end)
        self.limit_mode = limit_mode
        self.verbose = verbose
        self._idx: dict = {}          # code -> [rows]
        self._idx_fields = None       # relay 返回的列名（按名取列，不按位置猜）
        self._lim_fields = None
        self._mf_fields = None
        self._cls = None
        self._cls_fields = None
        self._mf: dict = {}
        self._lim: dict = {}
        self.calls: dict = {}
        # 分块（构造期未钉钟 ⇒ 用真 datetime；relay 单次有页数上限，见 _date_chunks）
        self.chunks = list(_date_chunks(self.fetch_start, self.fetch_end, 25))

    # ---- 内部：sqlite ----
    def _sq(self):
        return sqlite3.connect("file:%s?mode=ro" % os.path.abspath(self.bars_db), uri=True, timeout=60)

    def _sqlite_daily(self, codes, d0, d1, cols):
        q = "SELECT %s FROM bars WHERE trade_date >= ? AND trade_date <= ?" % ",".join(cols)
        args = [d0, d1]
        if codes:
            q += " AND ts_code IN (%s)" % ",".join("?" * len(codes))
            args += list(codes)
        c = self._sq()
        try:
            return [list(r) for r in c.execute(q, args)]
        finally:
            c.close()

    def _time(self, method: str):
        self.calls[method] = self.calls.get(method, 0) + 1

    # ---- 指数日线（联网 + 缓存，按 code 集合懒加载整段）----
    def _idx_rows(self, codes):
        miss = [c for c in codes if c not in self._idx]
        if miss:
            def fetch():
                from core import tushare_relay as tr   # noqa
                items, flds = [], None
                for c0, c1 in self.chunks:
                    f, part = tr.relay_items("index_daily", fields="",
                                             ts_code=",".join(miss), start_date=c0, end_date=c1)
                    flds = flds or f
                    items.extend(part)
                return flds, items
            key = "v2_index_daily_%s_%s_%s" % ("-".join(sorted(miss)), self.fetch_start, self.fetch_end)
            flds, items = self.cache.get(key, fetch)
            if flds and self._idx_fields is None:
                self._idx_fields = list(flds)
            for r in items:
                self._idx.setdefault(str(r[0]), []).append(r)
            for c in miss:
                self._idx.setdefault(c, [])
                self._idx[c].sort(key=lambda x: str(x[1]))
        return [r for c in codes for r in self._idx.get(c, [])]

    def index_daily(self, ts_code=None, start_date=None, end_date=None, fields=None, **kw):
        self._time("index_daily")
        codes = [c.strip() for c in str(ts_code or "").split(",") if c.strip()]
        rows = self._idx_rows(codes)
        s, e = str(start_date or "19000101"), str(min(str(end_date or self.cut), self.cut))
        if not codes:                       # 无 code ⇒ relay 会返回全量，回放里禁止（避免拉全市场指数）
            rows = []
        rows = [r for r in rows if s <= str(r[1]) <= e]
        avail = self._idx_fields or IDX_COLS
        cols = [c.strip() for c in str(fields or "").split(",") if c.strip()] or avail
        cols = [c for c in cols if c in avail]
        return self.pd.DataFrame([[r[avail.index(c)] if avail.index(c) < len(r) else None
                                   for c in cols] for r in rows], columns=cols)

    # ---- 个股日线（本地 sqlite）----
    def daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None, fields=None, **kw):
        self._time("daily")
        cols = [c.strip() for c in str(fields or "").split(",") if c.strip()] or DAILY_COLS
        cols = [c for c in cols if c in DAILY_COLS]
        if trade_date:
            d = str(trade_date)
            if d > self.cut:                                   # 未来日 → 生产当时也拿不到
                return self.pd.DataFrame(columns=cols)
            rows = self._sqlite_daily(None, d, d, cols)
        else:
            codes = [c.strip() for c in str(ts_code or "").split(",") if c.strip()]
            s = str(start_date or "19000101")
            e = str(min(str(end_date or self.cut), self.cut))
            rows = self._sqlite_daily(codes, s, e, cols) if codes else []
        return self.pd.DataFrame(rows, columns=cols)

    # ---- 涨跌停（复刻 relay 行为）----
    def _limit_rows(self):
        if not self._lim:
            for c0, c1 in self.chunks:
                def fetch(c0=c0, c1=c1):
                    from core import tushare_relay as tr   # noqa
                    return tr.relay_items("limit_list_d", fields="", start_date=c0, end_date=c1)
                flds, items = self.cache.get("v2_limit_list_d_%s_%s" % (c0, c1), fetch)
                if flds and self._lim_fields is None:
                    self._lim_fields = list(flds)
                for r in items:
                    self._lim.setdefault(str(r[0]), []).append(r)
        return self._lim

    def limit_list_d(self, trade_date=None, limit_type=None, start_date=None, end_date=None,
                     fields=None, **kw):
        self._time("limit_list_d")
        lt = str(limit_type or "").strip().upper()
        allrows = self._limit_rows()
        out = []
        if trade_date:
            days = [str(trade_date)] if str(trade_date) <= self.cut else []
        else:
            s, e = str(start_date or "19000101"), str(min(str(end_date or self.cut), self.cut))
            days = [d for d in allrows if s <= d <= e]
        flds = self._lim_fields or LIMIT_COLS
        li = flds.index("limit") if "limit" in flds else None
        want = None
        if lt and "," in lt:
            # 本机 relay 对 limit_type='U,D' 实测返回 0 行；但生产存量真实行的 limit_up/down 是真实计数
            # ⇒ 生产运行期取到了 U/D，故默认按本意拆成 U ∪ D（--limit-mode relay 可复刻 0 行行为）。
            want = set() if self.limit_mode == "relay" else {x.strip() for x in lt.split(",") if x.strip()}
        elif lt:
            want = {lt}
        for d in days:
            for r in allrows.get(d, []):
                if want is None or (li is not None and li < len(r) and str(r[li]).upper() in want):
                    out.append(list(r))
        n = max([len(r) for r in out] or [len(flds)])
        cols = (flds + ["c%d" % i for i in range(len(flds), n)])[:n]
        return self.pd.DataFrame([r + [None] * (n - len(r)) for r in out], columns=cols)

    # ---- 概念资金流 ----
    def moneyflow_ind_dc(self, start_date=None, end_date=None, content_type=None, fields=None, **kw):
        self._time("moneyflow_ind_dc")
        if not self._mf:
            rows = []
            for c0, c1 in self.chunks:
                def fetch(c0=c0, c1=c1):
                    from core import tushare_relay as tr   # noqa
                    return tr.relay_items("moneyflow_ind_dc", fields="", start_date=c0, end_date=c1)
                flds, items = self.cache.get("v2_moneyflow_ind_dc_%s_%s" % (c0, c1), fetch)
                if flds and self._mf_fields is None:
                    self._mf_fields = list(flds)
                rows.extend(items)
            self._mf["rows"] = rows
        flds = self._mf_fields or ["trade_date", "content_type", "ts_code", "name", "net_amount"]
        di = flds.index("trade_date") if "trade_date" in flds else 0
        ci = flds.index("content_type") if "content_type" in flds else None
        s, e = str(start_date or "19000101"), str(min(str(end_date or self.cut), self.cut))
        rows = [list(r) for r in self._mf["rows"]
                if len(r) > di and s <= str(r[di]) <= e
                and (not content_type or (ci is not None and ci < len(r) and str(r[ci]) == str(content_type)))]
        n = max([len(r) for r in rows] or [len(flds)])
        cols = (flds + ["c%d" % i for i in range(len(flds), n)])[:n]
        return self.pd.DataFrame([r + [None] * (n - len(r)) for r in rows], columns=cols)

    def index_classify(self, level=None, src=None, fields=None, **kw):
        self._time("index_classify")
        if self._cls is None:
            def fetch():
                from core import tushare_relay as tr   # noqa
                return tr.relay_items("index_classify", fields="", level=level or "L1", src=src or "SW2021")
            self._cls_fields, self._cls = self.cache.get("v2_index_classify_%s_%s" % (level, src), fetch)
        cols = self._cls_fields or ["index_code", "industry_name", "level", "industry_code", "is_pub", "parent_code"]
        return self.pd.DataFrame([list(r)[:len(cols)] + [None] * max(0, len(cols) - len(r)) for r in self._cls],
                                 columns=cols)

    def __getattr__(self, name):
        raise AttributeError("PitPro 未实现 pro.%s（get_market_diagnosis 用到了新接口？请补替身）" % name)


# ══════════════════════════ 落库 ══════════════════════════
INSERT_SQL = """
INSERT INTO market_diagnosis (trade_date, state, label, suggestion, score_trend,
                              score_oscillation, score_extreme, indicators_json, created_at)
VALUES (%(trade_date)s, %(state)s, %(label)s, %(suggestion)s, %(score_trend)s,
        %(score_oscillation)s, %(score_extreme)s, %(indicators_json)s, %(created_at)s)
ON CONFLICT (trade_date) DO NOTHING
"""


def row_from_result(res: dict, marker: dict | None = None) -> dict:
    """与 `_save_market_diagnosis()` 完全同字段（含 score_extreme=score.get('extreme',0)）。"""
    d = res["diagnosis"]
    ind = dict(res["indicators"])
    if marker:
        ind["_backfill"] = marker          # 额外键（消费者都走 .get()，安全）；标明这是回放生成的行
    return {
        "trade_date": res["trade_date"],
        "state": d["state"], "label": d["label"], "suggestion": d["suggestion"],
        "score_trend": d["score"]["trend"], "score_oscillation": d["score"]["oscillation"],
        "score_extreme": d["score"].get("extreme", 0),
        "indicators_json": json.dumps(ind, ensure_ascii=False),
        "created_at": _ORIG["datetime"].now().strftime("%Y-%m-%d %H:%M:%S") if _ORIG else "",
    }


_STYLE_STATE = {"fallback": None}


def install_style_rotation_guard(market, strict: bool = False):
    """给 ⑥ 风格轮动加一层**降级包装**（默认开，`--strict-style` 关）。

    为什么需要：`backend/app/api/market.py::_compute_style_rotation()` 里
    `result["suggestion"] = suggestion_map[p_leader]` —— 用**小写篮子名**（`'defense'`）去查键为
    大写模式名（`'DEFENSE'`）的字典 ⇒ 当价格/资金两个维度同时确认同一篮子（`p_days>=3 and f_days>=3`）
    时**必抛 KeyError**，而它在端点里没有被 try/except 包住 ⇒ 整次盘前诊断 500、当天不落库。
    实测：回测窗口里的 **20260316**（cut=20260313）正好命中（`price_5d.leader=flow_10d.leader=defense`）。

    降级动作**不是自造规则**：返回的字典与生产函数自己在"数据不足"分支（`price_result is None or
    flow_result is None`）里返回的**完全同构**（style_regime=NEUTRAL / consecutive_days=0 /
    suggestion='风格轮动数据不足，维持均衡配置'），只多一个 `_error` 说明；且 ⑥ **不参与**
    state/label/score 判定（①–⑤ 决定），因此该降级不影响本行的 state/scores。
    """
    orig = market._compute_style_rotation

    def guarded(pro, start_date, start_date_10d, end_date):
        _STYLE_STATE["fallback"] = None
        try:
            return orig(pro, start_date, start_date_10d, end_date)
        except Exception as ex:
            if strict:
                raise
            msg = "%s: %s" % (type(ex).__name__, str(ex)[:120])
            _STYLE_STATE["fallback"] = msg
            try:
                baskets = market._get_sw_style_baskets()
            except Exception:
                baskets = {}
            return {"baskets": baskets, "price_5d": None, "flow_10d": None,
                    "style_regime": "NEUTRAL", "consecutive_days": 0,
                    "suggestion": "风格轮动数据不足，维持均衡配置", "divergence_warning": None,
                    "_error": msg}

    market._compute_style_rotation = guarded
    return orig


def compute_one(day: str, cut: str, pro: PitPro, market, quiet: bool = True):
    """钉钟到 day 09:10，调**生产函数** `get_market_diagnosis()`（落库被本脚本接管）。"""
    pro.cut = cut
    pin_clock(day, "0910")
    box = {}
    market._save_market_diagnosis = lambda r: box.update(res=r)
    res = market.get_market_diagnosis()
    if "res" not in box:                   # 端点内部若直接落库（未来改版）→ 兜底
        box["res"] = res
    return box["res"]


# ══════════════════════════ main ══════════════════════════
def _setup(args):
    os.environ["DATABASE_URL"] = args.db_url
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    data_dir = args.data_dir or os.path.join(REPO, "data", "_bt_year", "20260310")
    os.environ["DATA_DIR"] = data_dir
    for sub in ("backend", "apps/main_line", "core", "jobs", ""):
        p = os.path.join(REPO, sub) if sub else REPO
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    return data_dir


def trade_days(bars_db: str, d0: str, d1: str):
    c = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(bars_db), uri=True, timeout=60)
    try:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ? ORDER BY 1", (d0, d1))]
    finally:
        c.close()


def report(pg, start: str, end: str):
    with pg.cursor() as cur:
        cur.execute("""SELECT count(*), min(trade_date), max(trade_date) FROM market_diagnosis
                       WHERE trade_date BETWEEN %s AND %s""", (start, end))
        n, a, b = cur.fetchone()
        cur.execute("""SELECT state, count(*) FROM market_diagnosis WHERE trade_date BETWEEN %s AND %s
                       GROUP BY 1 ORDER BY 2 DESC""", (start, end))
        dist = cur.fetchall()
        cur.execute("""SELECT count(*) FROM market_diagnosis""")
        total = cur.fetchone()[0]
    print("[report] 窗口 %s→%s 覆盖 %d 天（%s→%s）；全表 %d 行" % (start, end, n, a, b, total))
    print("[report] state 分布: %s" % ", ".join("%s=%d" % (s, c) for s, c in dist))
    return n, dist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260714")
    ap.add_argument("--protect-from", default=PROTECT_FROM,
                    help=">= 该日期的行视为生产真实行，绝不写（默认 20260715）")
    ap.add_argument("--db-url", default=DEFAULT_PG)
    ap.add_argument("--bars-db", default=DEFAULT_BARS_DB)
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE)
    ap.add_argument("--data-dir", default=None, help="沙箱 DATA_DIR（默认 data/_bt_year/20260310）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 天（调试）")
    ap.add_argument("--limit-mode", choices=["relay", "two-call"], default="two-call",
                    help="涨跌停(④)口径：two-call=U∪D（默认，与生产存量真实行里的 limit_up/down 计数一致）；"
                         "relay=原样复刻本机 relay 对 limit_type='U,D' 的返回（实测 0 行，仅作敏感性对照）")
    ap.add_argument("--no-cache", action="store_true", help="不读/不写 relay 磁盘缓存（强制重新联网取数）")
    ap.add_argument("--strict-style", action="store_true",
                    help="⑥ 风格轮动不做降级包装：命中生产 KeyError 的交易日直接判为『算不出』（不落库）")
    ap.add_argument("--verify-real", type=int, default=0, metavar="N",
                    help="对生产真实行区间抽 N 天重算并逐字段比对（不写库）")
    ap.add_argument("--report", action="store_true", help="只打印覆盖度/state 分布")
    a = ap.parse_args()
    _setup(a)

    import pandas as pd  # noqa: F401
    import psycopg2
    pg = psycopg2.connect(a.db_url)

    if a.report:
        report(pg, a.start, a.end)
        pg.close()
        return 0

    from app.api import market as market_mod
    # ⚠️ 唯一的两处运行期替身：数据源 + 落库（生产计算函数本身原样调用）
    market_mod._get_tushare_pro = lambda: pro
    install_style_rotation_guard(market_mod, strict=a.strict_style)

    # 交易日全集（含 cut 需要的前一日）与取数窗口
    alldays = trade_days(a.bars_db, "20250101", "20261031")
    idx_of = {d: i for i, d in enumerate(alldays)}

    def cut_of(d):
        i = idx_of.get(d)
        return alldays[i - 1] if i and i > 0 else None

    cache = DiskCache(a.cache_dir, enabled=not a.no_cache)
    fetch_start = (_dt.datetime.strptime(a.start, "%Y%m%d") - _dt.timedelta(days=90)).strftime("%Y%m%d")
    pro = PitPro(a.bars_db, cache, cut=a.start, fetch_start=fetch_start,
                 fetch_end=max(a.end, "20260914"), limit_mode=a.limit_mode)
    print("[env ] DB=…@%s｜bars.sqlite=%s｜cache=%s" % (a.db_url.split("@")[-1], a.bars_db, a.cache_dir))
    print("[env ] 生成窗口 %s→%s（保护 >= %s 的真实行）｜limit_mode=%s"
          % (a.start, a.end, a.protect_from, a.limit_mode))

    # ── 模式一：一致性校验（对生产真实行区间抽 N 天重算）──
    if a.verify_real:
        with pg.cursor() as cur:
            cur.execute("""SELECT trade_date, state, label, suggestion, score_trend, score_oscillation,
                                  score_extreme, indicators_json
                           FROM market_diagnosis WHERE trade_date >= %s ORDER BY trade_date""", (a.protect_from,))
            real = cur.fetchall()
        if not real:
            print("⛔ 没有 >= %s 的真实行可校验" % a.protect_from)
            return 2
        step = max(1, len(real) // a.verify_real)
        picks = real[::step][:a.verify_real]
        print("[veri] 抽样 %d 天（共 %d 行真实数据）：%s" % (len(picks), len(real), [p[0] for p in picks]))
        print("[veri] %-9s %-12s %-26s %-26s %-6s %-6s %s" %
              ("date", "state", "label", "suggestion", "trd", "osc", "判定"))
        same = 0
        for td, state, label, sugg, trd, osc, ext, ijs in picks:
            cut = cut_of(td)
            try:
                res = compute_one(td, cut, pro, market_mod)
                r = row_from_result(res)
            except Exception as e:
                print("[veri] %-9s 重算失败 %s: %s" % (td, type(e).__name__, str(e)[:90]))
                continue
            ok = (r["state"] == state and r["label"] == label and abs(r["score_trend"] - trd) < 1e-9
                  and abs(r["score_oscillation"] - osc) < 1e-9)
            same += 1 if ok else 0
            print("[veri] %-9s %-12s %-26s %-26s %-6s %-6s %s%s" %
                  (td, "%s/%s" % (state, r["state"]), "%s | %s" % (label, r["label"]),
                   "%s | %s" % (str(sugg)[:12], str(r["suggestion"])[:12]),
                   "%g|%g" % (trd, r["score_trend"]), "%g|%g" % (osc, r["score_oscillation"]),
                   "✅一致" if ok else "⚠️不一致", ""))
            if not ok:
                try:
                    old_ind = json.loads(ijs) if ijs else {}
                    new_ind = json.loads(r["indicators_json"])
                    for k in ("amplitude", "consecutive", "sector_rotation", "limit_ratio", "ma5_direction"):
                        ov, nv = old_ind.get(k), new_ind.get(k)
                        if isinstance(ov, dict) and isinstance(nv, dict) and ov != nv:
                            print("[veri]     %s: 生产=%s 重算=%s" %
                                  (k, {x: ov.get(x) for x in ("value", "max_any", "label", "angle_deg", "limit_up", "limit_down")},
                                   {x: nv.get(x) for x in ("value", "max_any", "label", "angle_deg", "limit_up", "limit_down")}))
                except Exception as _e_sil4:
                    _silent_alert("bt_pg_backfill_diagnosis.py:587", _e_sil4)
        print("[veri] 一致性：%d/%d 天 state+label+两个 score 全部相同" % (same, len(picks)))
        print("[veri] 替身调用计数: %s" % pro.calls)
        pg.close()
        return 0

    # ── 模式二：补齐窗口内缺失日 ──
    days = trade_days(a.bars_db, a.start, a.end)
    if a.limit:
        days = days[:a.limit]
    with pg.cursor() as cur:
        cur.execute("SELECT trade_date FROM market_diagnosis WHERE trade_date BETWEEN %s AND %s",
                    (a.start, a.end))
        have = {r[0] for r in cur.fetchall()}
    todo = [d for d in days if d not in have and d < a.protect_from]
    print("[plan] 窗口交易日 %d；已有 %d；待生成 %d；跳过(>=%s) %d"
          % (len(days), len(have), len(todo), a.protect_from,
             len([d for d in days if d >= a.protect_from])))

    done, fail = [], []
    t0 = (_ORIG["time"]() if _ORIG else time.time())      # 真实钟（pin_clock 之后 time.time() 是 as-of）
    for i, d in enumerate(todo, 1):
        cut = cut_of(d)
        if not cut:
            fail.append((d, "无前一交易日"))
            continue
        try:
            res = compute_one(d, cut, pro, market_mod)
            _mk = {"src": "jobs/bt_pg_backfill_diagnosis.py", "cut": cut, "limit_mode": a.limit_mode,
                   "generated_at": _ORIG["datetime"].now().strftime("%Y-%m-%d %H:%M:%S")}
            if _STYLE_STATE.get("fallback"):
                _mk["style_rotation_error"] = _STYLE_STATE["fallback"]
            row = row_from_result(res, marker=_mk)
            if row["trade_date"] != d:
                raise RuntimeError("端点写出 trade_date=%s ≠ %s（钉钟失效？）" % (row["trade_date"], d))
            if not a.dry_run:
                with pg.cursor() as cur:
                    cur.execute(INSERT_SQL, row)
                    n = cur.rowcount
                pg.commit()
                if n == 0:
                    print("[warn] %s 未写入（已存在？）" % d)
            done.append(d)
            if i <= 3 or i % 20 == 0 or i == len(todo):
                print("[run ] %d/%d %s cut=%s → state=%s osc=%g trd=%g%s"
                      % (i, len(todo), d, cut, row["state"], row["score_oscillation"],
                         row["score_trend"], "（dry-run）" if a.dry_run else ""), flush=True)
        except Exception as e:
            fail.append((d, "%s: %s" % (type(e).__name__, str(e)[:110])))
            print("[fail] %s cut=%s %s: %s" % (d, cut, type(e).__name__, str(e)[:110]), flush=True)
    print("[run ] 生成 %d 天，失败 %d 天，用时 %.0fs%s"
          % (len(done), len(fail), (_ORIG["time"]() if _ORIG else time.time()) - t0,
             "（dry-run，未落库）" if a.dry_run else ""))
    if fail:
        print("[run ] 失败明细: %s" % fail[:10])
    print("[src ] 替身调用计数: %s（disk cache hit=%d miss=%d）" % (pro.calls, cache.hits, cache.misses))
    if not a.dry_run:
        report(pg, a.start, a.end)
        with pg.cursor() as cur:      # 真实性校验：>= protect_from 的行必须与生产一致（没被动过）
            cur.execute("""SELECT state, count(*) FROM market_diagnosis WHERE trade_date >= %s
                           GROUP BY 1 ORDER BY 2 DESC""", (a.protect_from,))
            print("[guard] >=%s 真实行 state 分布（应未被本脚本改动）: %s"
                  % (a.protect_from, cur.fetchall()))
    pg.close()
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
