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
import json
import os
import runpy
import sys
import time

sys.path[:0] = ["/app", "/app/apps/main_line", "/app/jobs", "/app/core"]


class GzcloudShim:
    """老版本代码走的是 **gzcloud HTTP**（`requests.post(GZ, json={api_name, token, params, fields})`），
    该服务已失效（2026-09-13 起改用 relay）。回放 09-13 之前的版本时必须把这个协议也替身掉，
    否则脚本拿到空数据 → 例如 `stock_confirm_judge` 会在 `df["d"]` 上 KeyError。

    路由规则：POST body 里有 `api_name` **或** URL 含 gzcloud → 本地应答；其余原样转发真实 requests。
    覆盖接口：trade_cal / daily / daily_basic / index_daily（其余回落 relay 并按日期截断）。
    """

    def __init__(self, bars_db: str, as_of: str, real_post):
        self.bars_db = bars_db
        self.as_of = str(as_of)
        self._real_post = real_post
        self.n_local = self.n_remote = 0
        self._dates = None

    def trade_days(self):
        if self._dates is None:
            import sqlite3
            c = sqlite3.connect(self.bars_db)
            self._dates = [r[0] for r in c.execute(
                "SELECT DISTINCT trade_date FROM bars WHERE trade_date <= ? ORDER BY trade_date", (self.as_of,))]
            c.close()
        return self._dates

    def _fields_items(self, api, params, fields):
        f = [x.strip() for x in str(fields or "").split(",") if x.strip()]
        import sqlite3
        c = sqlite3.connect(self.bars_db)
        try:
            if api == "trade_cal":
                s = str(params.get("start_date") or "19000101"); e = min(str(params.get("end_date") or self.as_of), self.as_of)
                days = [d for d in self.trade_days() if s <= d <= e]
                return f or ["cal_date", "is_open"], [[d, 1] for d in days]
            if api in ("daily", "daily_basic"):
                cols = [x for x in f if x in ("ts_code", "trade_date", "open", "high", "low", "close",
                                              "pre_close", "pct_chg", "vol", "amount", "total_mv", "turnover_rate")]
                if not cols:
                    cols = ["ts_code", "trade_date", "close", "vol"]
                if params.get("trade_date"):
                    d = min(str(params["trade_date"]), self.as_of)
                    rows = c.execute("SELECT %s FROM bars WHERE trade_date=?" % ",".join(cols), (d,)).fetchall()
                else:
                    s = str(params.get("start_date") or "19000101")
                    e = min(str(params.get("end_date") or self.as_of), self.as_of)
                    rows = c.execute("SELECT %s FROM bars WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date"
                                     % ",".join(cols), (str(params.get("ts_code")), s, e)).fetchall()
                return cols, [list(r) for r in rows]
        finally:
            c.close()
        # 其余接口回落 relay（走 RelayShim → 自身带 as-of 截断与真 relay 兜底，不会递归）
        import tushare_relay
        try:
            return tushare_relay.relay_items(api, fields=fields, **params)
        except Exception:
            return [], []

    def post(self, url, **kw):
        body = kw.get("json") or {}
        if isinstance(body, dict) and body.get("api_name"):
            api = str(body["api_name"]); params = body.get("params") or {}; fields = body.get("fields") or ""
            flds, items = self._fields_items(api, params, fields)
            self.n_local += 1
            return _FakeJsonResponse({"data": {"fields": flds, "items": items}})
        if "gzcloud" in str(url):
            self.n_local += 1
            return _FakeJsonResponse({"data": {"items": []}})
        self.n_remote += 1
        return self._real_post(url, **kw)


class _FakeJsonResponse:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status
        try:
            self.text = json.dumps(payload, ensure_ascii=False)[:2000]
        except Exception:
            self.text = "<unserializable>"

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %s" % self.status_code)


def install_gzcloud_shim(bars_db: str, as_of: str) -> GzcloudShim:
    import requests as _rq
    real_post = _rq.post
    shim = GzcloudShim(bars_db, as_of, real_post)
    _rq.post = shim.post
    return shim


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
    # ⚠️ **必须连"日历类"时间函数一起钉**：`time.strftime('%Y%m%d')` 用的是 `time.localtime()`，
    #    它不走 `time.time()`（C 层直接读系统钟）→ 只钉 time.time 会让 `upto=今天`，
    #    于是 `glob('mainline_gate_*.json')` 挑到**最新那天的 gate**（实测：09-11 的回放读到了 09-15 的 gate，
    #    第三个主题从生产真实的 农业 变成 资源/周期 → 确认域整块跑偏）。
    _st = time.struct_time((d.year, d.month, d.day, 9, 15, 0, 0, 0, -1))
    time.localtime = lambda *a: _st
    time.gmtime = lambda *a: _st
    time.ctime = lambda *a: time.strftime("%a %b %d %H:%M:%S %Y", _st)
    time.asctime = lambda *a: time.strftime("%a %b %d %H:%M:%S %Y", _st)
    _real_strftime = time.strftime
    time.strftime = lambda fmt, t=None: _real_strftime(fmt, _st if t is None else t)
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
    ap.add_argument("--code-dir", default="",
                    help="该日**在跑的代码版本树**（data/_bt_code/rev_<rev>）；给定时优先于 /app 下的现行代码")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[])
    a, unknown = ap.parse_known_args()          # 目标脚本自己的参数一律透传（含 `--date` 之类）
    a.extra = list(a.extra or []) + list(unknown or [])

    os.environ["DATA_DIR"] = a.data_dir
    if a.code_dir:
        # 关键：把该日版本树插到 sys.path 最前 → import 到的是"当天在跑的代码"，不是今天的
        for sub in ("apps/main_line", "jobs", "backend", "core", "config", "scripts", ""):
            pth = os.path.join(a.code_dir, sub) if sub else a.code_dir
            if os.path.isdir(pth):
                sys.path.insert(0, pth)
        print("[code] 使用版本树 %s" % a.code_dir, file=sys.stderr)
    pin_clock(a.as_of)
    shim = None if a.no_relay_shim else install_relay_shim(a.bars_db, a.as_of)
    gzshim = None if a.no_relay_shim else install_gzcloud_shim(a.bars_db, a.as_of)

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
    if gzshim is not None:
        print("[gzshim] %s as_of=%s local=%d passthrough=%d"
              % (os.path.basename(a.script), a.as_of, gzshim.n_local, gzshim.n_remote), file=sys.stderr)
    if shim is not None:
        print("[shim] %s as_of=%s local=%d remote=%d dropped_future=%d err=%d %s"
              % (os.path.basename(a.script), a.as_of, shim.n_local, shim.n_remote, shim.n_dropped,
                 shim.n_err, ("first_err=" + shim.first_err) if shim.first_err else ""),
              file=sys.stderr)
    print("[pinned] %s rc=%d %.1fs" % (os.path.basename(a.script), rc, time.time() - t0), file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
