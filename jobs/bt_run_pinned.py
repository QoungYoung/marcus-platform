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

sys.path[:0] = []
import bt_env  # noqa: E402
bt_env.add_paths()


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
                if not days and s > e:
                    # ⚠️ 回测专用兜底：生产某些脚本把窗口起点写死（如 `stock_confirm_judge._trade_days()`
                    #    里 `start_date="20260601"`，那是"当时近 90 个交易日"的硬编码）。钉到更早的日期时
                    #    窗口变成空集 → 生产脚本拿到 0 行 → 直接 KeyError。这里按"as_of 往前 200 自然日"重算，
                    #    保持生产脚本"取近 90 个交易日"的原意；**只影响回测**（生产窗口非空，不走这条）。
                    try:
                        import datetime as _d2
                        _a = _d2.datetime(int(self.as_of[:4]), int(self.as_of[4:6]), int(self.as_of[6:8]))
                        s2 = (_a - _d2.timedelta(days=200)).strftime("%Y%m%d")
                        days = [d for d in self.trade_days() if s2 <= d <= e]
                        print("[pinned] trade_cal 窗口为空(%s→%s) → 回测兜底改用 %s→%s（%d 个交易日）"
                              % (s, e, s2, e, len(days)), file=sys.stderr)
                    except Exception:
                        pass
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
        self._td_cache = None
        self.first_err = ""
        self.offline_calls = {}

    def _conn(self):
        import sqlite3
        c = sqlite3.connect(self.bars_db, check_same_thread=False)
        return c

    def _real_relay_items(self, api_name, fields, params):
        """真实 relay（用打补丁前保存的原函数；**不能**再走模块属性，否则自递归）。

        `BT_RELAY_OFFLINE=1`：未本地覆盖的接口**直接返回空**（不联网）——回测里"联网取数"既慢
        又可能引入未来数据；置 1 后把这些接口名计数并在收尾打印，便于逐个判断要不要本地补齐。
        """
        if str(os.getenv("BT_RELAY_OFFLINE", "0")).strip() in ("1", "true", "yes"):
            self.offline_calls[api_name] = self.offline_calls.get(api_name, 0) + 1
            return [], []
        if self._real_fn is None:
            return [], []
        return self._real_fn(api_name, fields=fields, **params)

    def _trade_days(self):
        if not hasattr(self, "_td_cache") or self._td_cache is None:
            self._td_cache = [r[0] for r in self._conn().execute(
                "SELECT DISTINCT trade_date FROM bars ORDER BY trade_date")]
        return self._td_cache

    def relay_items(self, api_name: str, fields: str = "", **params):
        f = [x.strip() for x in str(fields or "").split(",") if x.strip()]
        # ⚠️ `trade_cal` 必须本地服务 + **空窗口兜底**：生产有脚本把窗口起点写死（如 20260601），
        #    钉到更早日期时窗口为空 → 那些脚本拿到 0 个交易日 → 直接 KeyError（实测 stock_confirm_judge）。
        if api_name == "trade_cal":
            _s = str(params.get("start_date") or "19000101")
            _e = min(str(params.get("end_date") or self.as_of), self.as_of)
            _days = [d for d in self._trade_days() if _s <= d <= _e]
            if not _days and _s > _e:
                import datetime as _d3
                _a = _d3.datetime(int(self.as_of[:4]), int(self.as_of[4:6]), int(self.as_of[6:8]))
                _s2 = (_a - _d3.timedelta(days=200)).strftime("%Y%m%d")
                _days = [d for d in self._trade_days() if _s2 <= d <= _e]
                print("[shim] trade_cal 窗口为空(%s→%s) → 回测兜底 %s→%s（%d 个交易日）"
                      % (_s, _e, _s2, _e, len(_days)), file=sys.stderr)
            self.n_local += 1
            return (fields or "cal_date,is_open"), [[d, 1] for d in _days]
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
        # ⚠️ **整市场按日**（`daily` + 只给 trade_date，不给 ts_code）：`stock_confirm_judge._fetch_market`
        #    就是这么取的（fields="ts_code,trade_date,close,vol"），旧版 shim 只支持"按 ts_code"→ 落到远端，
        #    远端返回结构不符 → 生产脚本 `KeyError: 'd'` → 确认域为空 → pathA 无票 → 布腿 0 条。
        if api_name == "daily" and params.get("trade_date") and not params.get("ts_code"):
            cols = [c for c in f if c in ("ts_code", "trade_date", "open", "high", "low", "close",
                                          "pre_close", "pct_chg", "vol", "amount", "total_mv", "turnover_rate")]
            if not cols:
                cols = ["ts_code", "trade_date", "close", "vol"]
            d = min(str(params["trade_date"]), self.as_of)
            self.n_local += 1
            rows = self._conn().execute(
                "SELECT %s FROM bars WHERE trade_date=? ORDER BY ts_code" % ",".join(cols), (d,)).fetchall()
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
    # `pro.*`（客户端实例方法）是**另一条**取数路径：只补模块级函数拦不住它。
    # 实测：波浪 agent 的 `pro.index_daily` 走真实中继 → 本机无代理 ProxyError → `wave_state.json`
    # 写成空占位 → 决策层 L5 默认放行。这里用 `bt_local_pro` 把 `get_tushare_pro()` 也接管。
    if str(os.getenv("BT_LOCAL_PRO", "1")).strip() not in ("0", "false", "no"):
        try:
            import bt_local_pro

            class _Mkt:
                """只给 `_LocalPro` 需要的两样：bars_db + 指数分钟（按日聚合出指数日线）。"""

                def __init__(self, bars_db: str):
                    self.bars_db = bars_db
                    self._idx: Dict[str, list] = {}

                def load_index(self, symbol: str = "000001.SH") -> list:
                    if symbol in self._idx:
                        return self._idx[symbol]
                    p = os.path.join(bt_env.DATA, "_bt_idx_m5", "%s.json" % str(symbol).replace(".", "_"))
                    bars = []
                    try:
                        bars = json.load(open(p, encoding="utf-8"))
                    except Exception:
                        bars = []
                    self._idx[symbol] = bars
                    return bars

            _mp = _Mkt(bars_db)
            shim.local_pro = bt_local_pro.install_local_pro(_mp, as_of, relay_fn=shim.relay_items)
        except Exception as e:
            print("[shim] local_pro 安装失败：%s" % str(e)[:100], file=sys.stderr)
    return shim


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--data-dir", required=True, help="沙箱目录（脚本的读写都在这里）")
    ap.add_argument("--bars-db", default=os.path.join(bt_env.DATA, "_bt_full", "bars.sqlite"))
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
    # 出网一律切断（默认）：否则代理不可用时每个 relay 调用都要 30-60s 重试，波浪步实测 4 分钟跑不完。
    if str(os.getenv("BT_NET_OFFLINE", "1")).strip() not in ("0", "false", "no"):
        try:
            import bt_local_pro
            bt_local_pro.install_net_offline()
            print("[shim] 已切断出网（BT_NET_OFFLINE=1）", file=sys.stderr)
        except Exception as _ne:
            print("[shim] 断网失败：%s" % str(_ne)[:80], file=sys.stderr)
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
