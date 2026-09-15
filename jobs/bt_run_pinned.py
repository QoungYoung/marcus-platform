# -*- coding: utf-8 -*-
"""bt_run_pinned.py — 用**钉住的时钟 + 本地日线**跑一个没有 `--date` 的生产脚本（全拟真回测的 as-of 打桩层）。

为什么需要：`position_class.py`（概念高低位）、`rotation_universe.py`（方向层池）、
`derive_sub_universe.py`（子方向）、`stock_confirm_judge.py`（确认域）都没有 `--date`，内部用
`date.today()` / `datetime.now()` / 直连 relay 取"最新"数据 → 直接跑就是**前视**。

本脚本在**子进程**里把三件事钉住，然后 `runpy` 执行目标脚本：
  1. **时钟**：`datetime.date.today()` / `datetime.datetime.now()|today()` / `time.time()` 全部偏移到 as-of 当天 09:15；
  2. **取数**：把 `tushare_relay.relay_items(...)` 换成"本地 SQLite 日线缓存（强制 ≤ as_of）"，未覆盖的接口
     （指数/资金流等）回落到真实 relay 并**按日期字段过滤掉 as_of 之后的行**；
  3. **数据目录**：`DATA_DIR` 指向沙箱（调用方给），脚本的读写都落在沙箱里。

用法：
  python jobs/bt_run_pinned.py --as-of 20260910 --data-dir /app/data/_bt_full/20260911 \
      --bars-db /app/data/_bt_full/bars.sqlite --script apps/main_line/rotation_universe.py [-- 额外参数]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import runpy
import sys
import time

sys.path[:0] = ["/app", "/app/apps/main_line", "/app/jobs", "/app/core"]


def pin_clock(as_of: str):
    """把"现在"钉到 as_of 09:15（本地时区）。

    ⚠️ 两个必须遵守的约束（2026-09-15 实测踩坑）：
      ① **先预热 C 扩展**（pandas/numpy）再打补丁：否则它们初始化时看到 `datetime.date` 的
         PyObject 尺寸变了（"datetime.date size changed, may indicate binary incompatibility"）
         → 直接 **SIGSEGV**（实测 rc=-11）；
      ② 打补丁的类 `today()/now()` **返回真实 date/datetime 实例**（不是子类实例）：
         C 扩展拿到子类实例同样可能出错。
    """
    try:
        import numpy  # noqa: F401  预热（顺序不能动）
        import pandas  # noqa: F401
    except Exception:
        pass
    d = _dt.datetime(int(as_of[:4]), int(as_of[4:6]), int(as_of[6:8]), 9, 15, 0)
    offset = d.timestamp() - time.time()
    _real_date, _real_dt = _dt.date, _dt.datetime

    class _Date(_real_date):
        @classmethod
        def today(cls):
            return _real_date(d.year, d.month, d.day)

    class _Datetime(_real_dt):
        @classmethod
        def now(cls, tz=None):
            return _real_dt(d.year, d.month, d.day, 9, 15, 0)

        @classmethod
        def today(cls):
            return cls.now()

        @classmethod
        def utcnow(cls):
            return cls.now()

    _dt.date = _Date
    _dt.datetime = _Datetime
    _real_time = time.time
    time.time = lambda: _real_time() + offset
    return d


class RelayShim:
    """`relay_items` 的本地替身：daily/daily_basic 走 SQLite（≤ as_of）；其余回落真实 relay 并按日过滤。"""

    DATE_FIELDS = ("trade_date", "date", "ann_date", "end_date", "trade_time", "cal_date")

    def __init__(self, bars_db: str, as_of: str, real_fn=None):
        self.bars_db = bars_db
        self.as_of = str(as_of)
        self._real_fn = real_fn            # ⚠️ 必须是**打补丁之前**的原函数，否则会自递归
        self.n_local = self.n_remote = self.n_dropped = self.n_err = 0
        self.first_err = ""

    def _conn(self):
        import sqlite3
        c = sqlite3.connect(self.bars_db, check_same_thread=False)
        return c

    def _real_relay_items(self, api_name, fields, params):
        """真实 relay（用打补丁前保存的原函数；**不能**再走模块属性，否则自递归）。"""
        if self._real_fn is None:
            return [], []
        return self._real_fn(api_name, fields=fields, **params)

    def relay_items(self, api_name: str, fields: str = "", **params):
        f = [x.strip() for x in str(fields or "").split(",") if x.strip()]
        if api_name == "daily" and params.get("ts_code"):
            cols = [c for c in f if c in ("ts_code", "trade_date", "open", "high", "low", "close",
                                          "pre_close", "pct_chg", "vol", "amount", "total_mv", "turnover_rate")]
            if not cols:
                cols = ["ts_code", "trade_date", "close", "amount", "low", "high"]
            s = str(params.get("start_date") or "19000101")
            e = min(str(params.get("end_date") or self.as_of), self.as_of)
            self.n_local += 1
            rows = self._conn().execute(
                "SELECT %s FROM bars WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date"
                % ",".join(cols), (str(params["ts_code"]), s, e)).fetchall()
            return cols, [list(r) for r in rows]
        if api_name == "daily_basic" and params.get("trade_date"):
            d = min(str(params["trade_date"]), self.as_of)
            self.n_local += 1
            rows = self._conn().execute(
                "SELECT ts_code,total_mv FROM bars WHERE trade_date=? AND total_mv IS NOT NULL", (d,)).fetchall()
            return ["ts_code", "total_mv"], [list(r) for r in rows]
        # 其余接口：真实 relay + 按日期字段丢弃 as_of 之后的行（防前视）
        try:
            flds, items = self._real_relay_items(api_name, fields, params)
        except Exception as e:
            self.n_err += 1
            if not self.first_err:
                self.first_err = "%s: %s" % (api_name, str(e)[:120])
            return f or [], []
        self.n_remote += 1
        idx = None
        for cand in self.DATE_FIELDS:
            if cand in (flds or []):
                idx = flds.index(cand)
                break
        if idx is not None and items:
            kept = []
            for r in items:
                v = str(r[idx]).replace("-", "")[:8]
                if len(v) == 8 and v.isdigit():
                    if v > self.as_of:
                        self.n_dropped += 1
                        continue
                kept.append(r)
            items = kept
        return flds, items

    # 兼容 `get_relay()` 形态（有些脚本用 relay.get_relay().query_items）
    def query_items(self, api_name: str, fields: str = "", **params):
        return self.relay_items(api_name, fields=fields, **params)

    def available_sources(self):
        return ["sqlite_shim", "real_relay"]


def install_relay_shim(bars_db: str, as_of: str) -> RelayShim:
    import tushare_relay
    real_fn = tushare_relay.relay_items          # 先抓住原函数（防自递归）
    shim = RelayShim(bars_db, as_of, real_fn=real_fn)
    # ⚠️ **只替换 `relay_items`，不碰 `get_relay`**：原版 `relay_items` 内部会调 `get_relay()`，
    #    若把它也换成 shim → 回落路径变成 shim→原函数→shim 的**无限递归**（实测 RecursionError）。
    tushare_relay.relay_items = shim.relay_items
    print("[shim] 已替换 tushare_relay.relay_items（get_relay 保持原样，避免自递归）", file=sys.stderr)
    return shim


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--data-dir", required=True, help="沙箱目录（脚本的读写都在这里）")
    ap.add_argument("--bars-db", default="/app/data/_bt_full/bars.sqlite")
    ap.add_argument("--script", required=True)
    ap.add_argument("--no-relay-shim", action="store_true")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[])
    a, unknown = ap.parse_known_args()          # 目标脚本自己的参数一律透传（含 `--date` 之类）
    a.extra = list(a.extra or []) + list(unknown or [])

    os.environ["DATA_DIR"] = a.data_dir
    pin_clock(a.as_of)
    shim = None if a.no_relay_shim else install_relay_shim(a.bars_db, a.as_of)

    extra = [x for x in (a.extra or []) if x != "--"]   # 兼容调用方用 `--` 分隔
    sys.argv = [a.script] + extra
    t0 = time.time()
    try:
        runpy.run_path(a.script, run_name="__main__")
        rc = 0
    except SystemExit as e:
        rc = int(e.code or 0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        rc = 1
    if shim is not None:
        print("[shim] %s as_of=%s local=%d remote=%d dropped_future=%d err=%d %s"
              % (os.path.basename(a.script), a.as_of, shim.n_local, shim.n_remote, shim.n_dropped,
                 shim.n_err, ("first_err=" + shim.first_err) if shim.first_err else ""),
              file=sys.stderr)
    print("[pinned] %s rc=%d %.1fs" % (os.path.basename(a.script), rc, time.time() - t0), file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
