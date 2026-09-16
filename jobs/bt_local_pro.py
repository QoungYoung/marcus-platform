# -*- coding: utf-8 -*-
"""bt_local_pro.py — 生产 `pro.*`（tushare 中继客户端）的**本地替身**（回测共用）。

为什么单独成模块：`pro.daily/pro.index_daily/...` 有**两条**取数路径——模块级
`tushare_relay.relay_items(...)` 和 客户端实例方法（`get_tushare_pro().daily(...)`）。
只补前者拦不住后者（实测：钉住 relay 后 `index_daily`/`fund_daily` 仍走真实中继联网，
本机无代理 → ProxyError，波浪 agent 因此取不到指数数据、`wave_state.json` 写成空占位）。

`_LocalPro` 能本地服务的接口走本地 SQLite（**强制 ≤ cut**），其余返回空 DataFrame 并记账；
配合 `RelayShim`（`bt_run_pinned`）就构成"回测不出网"的完整取数面。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict


def _q(sql: str, args=()):
    """查本地库（复用 bt_prod_run 的约定；由调用方通过 _QUERY['fn'] 注入，避免循环依赖）。"""
    return _QUERY["fn"](sql, args) if _QUERY.get("fn") else []



_NET_HITS: Dict[str, int] = {}


def install_net_offline() -> Dict[str, int]:
    """把所有出网调用**立即**变成异常（不等待 30-40s 超时/重试）。

    回测里网络既慢又只能带来未来数据；生产代码对网络失败本来就有一整套降级
    （日志 + 返回空），语义与真实断网一致。这里把尝试过的 host/path 计数返回，
    供收尾打印成**取数账本**。
    """
    # 本机有 http(s)_proxy 指向 127.0.0.1:7890（容器里那套），一旦它不可用，**连回环地址**
    # 也会被塞进代理 → ProxyError（实测：波浪 agent 的 LLM 调用卡 180s 超时）。
    # 回环必须直连：写死 NO_PROXY/no_proxy。
    for _k in ("NO_PROXY", "no_proxy"):
        _cur = os.environ.get(_k, "")
        _add = [x for x in ("127.0.0.1", "localhost", "::1") if x not in _cur]
        if _add:
            os.environ[_k] = (("," + _cur) if _cur else "") .join([""]) if False else (
                (_cur + "," if _cur else "") + ",".join(_add))

    def _is_local(url) -> bool:
        """本机回环地址**放行**：本地 LLM 隧道（波浪/交易腿 agent，127.0.0.1:13001）
        与本地 PG 都在这条路径上，切断它们会把 agent 一起切掉（实测：波浪步 rc=1，
        报 `BT_NET_OFFLINE: http://127.0.0.1:13001/chat`）。"""
        u = str(url)
        return ("127.0.0.1" in u) or ("localhost" in u) or ("[::1]" in u) or ("0.0.0.0" in u)

    def _note(url) -> None:
        try:
            key = str(url).split("?")[0][:120]
        except Exception:
            key = "?"
        _NET_HITS[key] = _NET_HITS.get(key, 0) + 1

    class _NetOffline(RuntimeError):
        pass

    try:
        import requests

        _rg, _rp = requests.get, requests.post
        _rsr = requests.sessions.Session.request

        def _mk(fn, url, *a, **kw):
            if _is_local(url):
                return fn(url, *a, **kw)
            _note(url)
            raise _NetOffline("BT_NET_OFFLINE: %s" % str(url)[:80])

        requests.get = lambda url, *a, **kw: _mk(_rg, url, *a, **kw)
        requests.post = lambda url, *a, **kw: _mk(_rp, url, *a, **kw)
        try:
            requests.sessions.Session.request = (lambda self, method, url, *a, **kw:
                                                 _mk(lambda u, *aa, **kk: _rsr(self, method, u, *aa, **kk),
                                                     url, *a, **kw))
        except Exception:
            pass
    except Exception:
        pass
    try:
        import urllib.request as _ur
        _ruo = _ur.urlopen
        _ur.urlopen = lambda url, *a, **kw: _mk(_ruo, getattr(url, "full_url", url), *a, **kw)
    except Exception:
        pass
    return _NET_HITS


_QUERY: Dict[str, Any] = {}
_RELAY_FN: Dict[str, Any] = {}


def set_query_fn(fn) -> None:
    _QUERY["fn"] = fn


class _LocalPro:
    """生产 `pro.*`（tushare 中继客户端）的**本地替身**：能本地服务的走本地库且 **强制 ≤ cut**，
    其余接口返回空 DataFrame 并**记账**（收尾打印）——绝不联网、绝不前视。

    为什么要这一层：生产里除 `relay_items` 之外还有一条 `pro.daily(...)` / `pro.fund_daily(...)`
    的取数路径（如 `support_resistance.get_daily_bars`），只补 `relay_items` 是补不住的
    （实测：钉住 relay 后仍反复出现 `[tushare_relay] fund_daily 日期跨度超限` 联网调用）。
    """

    def __init__(self, market: "LocalMarket", cut: str):
        self.market = market
        self.cut = str(cut or "19000101")
        self.calls: Dict[str, int] = {}
        self.unserved: Dict[str, int] = {}

    # — 内部 —
    def _df(self, rows, cols):
        try:
            import pandas as pd
        except Exception:
            return rows
        return pd.DataFrame(rows, columns=cols)

    def _clamp(self, d) -> str:
        d = str(d or "").strip()
        return min(d, self.cut) if d and d.isdigit() else self.cut

    def _bars(self, ts_code, start, end):
        import sqlite3
        cols = ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
                "pct_chg", "vol", "amount", "turnover_rate")
        c = sqlite3.connect(self.market.bars_db)
        q = ("SELECT ts_code, trade_date, open, high, low, close, NULL, NULL, vol, amount, turnover_rate "
             "FROM bars WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date")
        rows = c.execute(q, (ts_code, start, end)).fetchall()
        c.close()
        return self._df([list(r) for r in rows], cols)

    # — 入口 —
    def __getattr__(self, api: str):
        if api.startswith("_"):
            raise AttributeError(api)

        def _call(**kw):
            return self.call(api, **kw)
        return _call

    def call(self, api: str, **kw):
        self.calls[api] = self.calls.get(api, 0) + 1
        end = self._clamp(kw.get("end_date") or kw.get("trade_date") or self.cut)
        start = str(kw.get("start_date") or "19900101")
        ts_code = str(kw.get("ts_code") or "")
        try:
            if api in ("daily", "daily_basic"):
                if kw.get("trade_date"):
                    import sqlite3
                    c = sqlite3.connect(self.market.bars_db)
                    rows = c.execute("SELECT ts_code, trade_date, open, high, low, close, vol, amount, turnover_rate"
                                     " FROM bars WHERE trade_date=?", (end,)).fetchall()
                    c.close()
                    return self._df([list(r) for r in rows],
                                    ("ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount",
                                     "turnover_rate"))
                return self._bars(ts_code, start, end)
            if api == "trade_cal":
                import sqlite3
                c = sqlite3.connect(self.market.bars_db)
                days = [r[0] for r in c.execute(
                    "SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                    (start, self._clamp(kw.get("end_date") or self.cut)))]
                c.close()
                return self._df([[d, 1] for d in days], ("cal_date", "is_open"))
            if api == "stock_basic":
                rows = _q("SELECT ts_code, symbol, name, area, industry, market, list_date FROM stock_pool")
                return self._df([list(r.values()) for r in rows],
                                ("ts_code", "symbol", "name", "area", "industry", "market", "list_date"))
            if api == "index_daily":
                bars = self.market.load_index(ts_code or "000001.SH")
                by = {}
                for b in bars:
                    d = str(b.get("time"))[:10].replace("-", "")
                    if d > self.cut:
                        continue
                    cur = by.get(d)
                    if not cur:
                        by[d] = {"trade_date": d, "open": b["open"], "high": b["high"],
                                 "low": b["low"], "close": b["close"], "vol": b.get("vol") or 0,
                                 "amount": b.get("amount") or 0}
                    else:
                        cur["high"] = max(cur["high"], b["high"])
                        cur["low"] = min(cur["low"], b["low"])
                        cur["close"] = b["close"]
                        cur["vol"] += b.get("vol") or 0
                        cur["amount"] += b.get("amount") or 0
                rows = [by[d] for d in sorted(by)]
                return self._df([[r["trade_date"], ts_code, r["open"], r["high"], r["low"], r["close"],
                                  r["vol"], r["amount"]] for r in rows],
                                ("trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount"))
        except Exception as e:
            print("[localpro] %s 本地服务失败：%s" % (api, str(e)[:100]), file=sys.stderr)
        self.unserved[api] = self.unserved.get(api, 0) + 1
        return self._df([], ())

    def query(self, api: str, **kw):
        return self.call(str(api), **kw)


def install_local_pro(market: Any, cut: str, relay_fn=None) -> "_LocalPro":
    """把 `get_tushare_pro()` 换成 `_LocalPro`（覆盖 `pro.*` 与 `pro.query(...)` 两条路径）。"""
    pro = _LocalPro(market, cut)
    if relay_fn is not None:
        _RELAY_FN["fn"] = relay_fn
    patched = []
    try:
        import app.core.trading._api_config as ac
        ac.get_tushare_pro = lambda: pro
        patched.append("_api_config")
    except Exception as e:
        print("[localpro] _api_config 打补丁失败：%s" % str(e)[:80], file=sys.stderr)
    try:
        import app.api.market as am
        am._get_tushare_pro = lambda: pro
        patched.append("api.market")
    except Exception as e:
        print("[localpro] api.market 打补丁失败：%s" % str(e)[:80], file=sys.stderr)
    if str(os.getenv("BT_RELAY_OFFLINE", "0")).strip() in ("1", "true", "yes"):
        try:
            import tushare_relay
            cls = tushare_relay.TushareRelay

            # `get_relay()` 返回的是 TushareRelay **实例**，只替换模块级函数是拦不住它的
            # → 连类方法一起换成 RelayShim（offline 模式下 RelayShim 不会回调网络，故无递归风险）
            cls.relay_items = lambda self, api_name, fields="", **params: _RELAY_FN["fn"](
                api_name, fields=fields, **params)
            patched.append("TushareRelay.relay_items")
        except Exception as e:
            print("[localpro] TushareRelay 打补丁失败：%s" % str(e)[:80], file=sys.stderr)
    print("[localpro] 已接管 tushare 客户端：%s" % patched, file=sys.stderr)
    return pro


_RELAY_FN: Dict[str, Any] = {}


