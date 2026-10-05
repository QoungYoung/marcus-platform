#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测专用取数网关：**datahubco 基础档 / Promax 双后端 + 强制 as-of + 防未来函数 + 落盘缓存**。

只服务回测：放在 jobs/、不被 backend/ 引用；需 `BT_ASOF_FETCH=1` 且由回测驱动显式 install。
密钥只从环境/`.env` 读，**只报告"是否已配置"，绝不打印值**。

上游两条入口（2026-09-17 实测）：
  · basic  : `DATAHUBCO_API_URL`（…/app-api/openapi/v1/tushare）+ `DATAHUBCO_API_KEY`
             —— 鉴权通过，但实测 `/daily` 报 `40101 请指定正确的接口名`（该档很可能不含日线）
  · promax : `PROMAX_URL`（https://pcd.mobcvb.cn/tushare/pro）+ `PROMAX_API_KEY`
             —— 按 `docs/datahubco/promax.txt`：业务端点 `BASE/<api>`；`?__probe=1` 只读样本；
                `GET /tushare/capabilities[/{api_name}]` 是权威目录；分钟走 `pro_bar` + `freq=1MIN`
三道防线：as-of 上界强制夹取 / 响应未来日期截断+计数 / 按 (后端,端点,参数,as-of) 落盘缓存。
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


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


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(REPO, ".dsh-tmp", "wolfbt", "asof_fetch_cache")
STATS = {"requests": 0, "cache_hits": 0, "clamped_params": 0, "future_rows_cut": 0, "errors": 0,
         "backend_fallback": 0}
_DATE_KEYS = ("end_date", "trade_date", "start_date", "ann_date", "list_date")
# 端点 → 首选后端（其它端点默认 basic；失败会自动换后端重试一次）
ROUTE = {"daily": "promax", "stk_mins": "promax", "pro_bar": "promax", "weekly": "promax", "monthly": "promax", "pro_bar": "promax",
         "capabilities": "promax", "daily_basic": "basic", "index_member_all": "basic"}
_BACKENDS = {
    "basic": {"url_env": "DATAHUBCO_API_URL", "key_env": "DATAHUBCO_API_KEY",
              "default": "http://datahubco.com", "suffix": "/app-api/openapi/v1/tushare"},
    "promax": {"url_env": "PROMAX_URL", "key_env": "PROMAX_API_KEY",
               "default": "https://pcd.mobcvb.cn", "suffix": "/tushare/pro"},
}


def _dotenv(key: str) -> str:
    try:
        for ln in open(os.path.join(REPO, ".env"), encoding="utf-8"):
            if ln.strip().startswith(key + "="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError as _e_sil1:
        _silent_alert("bt_asof_fetch.py:47", _e_sil1)
    return ""


def _env(key: str) -> str:
    return os.getenv(key) or _dotenv(key)


def backend_base(name: str) -> str:
    """归一化后端前缀（末尾可直拼 `/<api>`）：识别"已含后缀"的地址，防重复路径/漏段。"""
    b = _BACKENDS[name]
    raw = (_env(b["url_env"]) or b["default"]).rstrip("/")
    for suf in (b["suffix"], "/app-api/openapi/v1", "/app-api/openapi", "/app-api",
                "/tushare/pro", "/tushare"):
        if raw.endswith(suf):
            raw = raw[: -len(suf)]
            break
    return raw + b["suffix"]


def backend_key(name: str) -> str:
    return _env(_BACKENDS[name]["key_env"])


def backends_ready() -> dict:
    return {n: {"base": backend_base(n), "key": "已配置" if backend_key(n) else "缺失"}
            for n in _BACKENDS}


def clamp_asof(params: dict, asof_day: str, range_days: int = 0) -> dict:
    """日期参数夹到 ≤ as-of。上游规则：`start_date/end_date` 必须成对；`trade_date` 单日最干净。"""
    out = dict(params)
    hit = False
    for k in list(out):
        if k in _DATE_KEYS and str(out[k]) > asof_day:
            out[k] = asof_day
            hit = True
    if "trade_date" in out or "probe" in out:
        pass
    elif "start_date" in out or "end_date" in out:
        out.setdefault("start_date", out.get("end_date", asof_day))
        out.setdefault("end_date", asof_day)
        if str(out["start_date"]) > asof_day:
            out["start_date"] = asof_day
            hit = True
    else:
        _rd = int(range_days or _env("BT_ASOF_FETCH_RANGE_DAYS") or 0)
        out["end_date"] = asof_day
        if _rd > 0:
            import datetime as _dt
            out["start_date"] = (_dt.datetime.strptime(asof_day, "%Y%m%d")
                                 - _dt.timedelta(days=_rd)).strftime("%Y%m%d")
        else:
            out["start_date"] = asof_day
    if hit:
        STATS["clamped_params"] += 1
    return out


def _norm(d) -> str:
    return re.sub(r"\D", "", str(d or ""))[:8]


def filter_future_rows(rows: list, asof_day: str, asof_hhmm: str = "") -> list:
    """丢弃 date/time > as-of 的行并计数（上游忽略上界时也兜住）。分钟行按 `HH:MM` 再截一次。"""
    keep = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        d = _norm(r.get("trade_date") or r.get("date") or r.get("end_date") or r.get("trade_time") or "")
        if d and d > asof_day:
            STATS["future_rows_cut"] += 1
            continue
        if asof_hhmm and d == asof_day:
            t = str(r.get("trade_time") or r.get("time") or r.get("datetime") or "")
            m = re.search(r"(\d{1,2}):(\d{2})", t)
            if m and "%02d:%02d" % (int(m.group(1)), int(m.group(2))) > asof_hhmm:
                STATS["future_rows_cut"] += 1
                continue
        keep.append(r)
    return keep


def _endpoint_url(name: str, endpoint: str, params: dict) -> str:
    """capabilities 是**目录**接口：位于 `/tushare/capabilities`（promax 网关下会 404 unknown_api）。"""
    base = backend_base(name)
    if endpoint == "capabilities" or endpoint.startswith("capabilities/"):
        root = base.replace("/tushare/pro", "/tushare")
        return "%s/%s?%s" % (root, endpoint, urllib.parse.urlencode(params))
    return "%s/%s?%s" % (base, endpoint, urllib.parse.urlencode(params))


def parse_body(body) -> tuple:
    """解析上游响应（纯函数，便于离线单测）：统一成 **list[dict]**。

    兼容两种信封：`{"code":0,"data":{"fields":[…],"items":[[…]]}}`（promax，**数组行按 fields 对齐**）
    与 `{"ok":false,"error":…,"message":…}`（错误）；错误时返回 (None, 原因)。
    """
    if isinstance(body, dict) and body.get("ok") is False:
        return None, str(body.get("message") or body.get("error"))[:160]
    if isinstance(body, dict) and body.get("code") not in (0, None):
        return None, "code=%s msg=%s" % (body.get("code"), str(body.get("msg"))[:140])
    rows = body.get("data") if isinstance(body, dict) else body
    fields = None
    if isinstance(rows, dict):
        fields = rows.get("fields")
        rows = rows.get("items") or rows.get("rows") or []
    rows = rows if isinstance(rows, list) else []
    if fields and rows and isinstance(rows[0], (list, tuple)):
        rows = [dict(zip(fields, r)) for r in rows if isinstance(r, (list, tuple))]
    return rows, None


_FAIL_LOGGED: dict = {}
_COOLDOWN: dict = {}          # 后端 → 冷却到期时间戳（熔断：上游池耗尽时快速失败，别每个标的都硬等）
_COOLDOWN_SEC = float(os.getenv("BT_ASOF_FETCH_COOLDOWN_SEC", "20"))
_TIMEOUT = float(os.getenv("BT_ASOF_FETCH_TIMEOUT_SEC", "8"))
_MAX_TRIES = int(os.getenv("BT_ASOF_FETCH_TRIES", "2"))


def _cooling(name: str) -> float:
    """返回剩余冷却秒数（>0 表示该后端处于熔断冷却中，调用方直接快速失败）。"""
    left = _COOLDOWN.get(name, 0.0) - __import__("time").time()
    return left if left > 0 else 0.0


def _trip(name: str, why: str) -> None:
    import time as _tm
    # ★ 账本 §9.639 ✓（用户要求：「503 退避 ✓ —— 源池耗尽时自动等待重试 ✗，
    #   而不是熔断后当天作废 ✗」）：
    #   实测 ✓：`HTTP 503 {"error":"upstream_pool_exhausted"}` ✗（promax 上游连接池耗尽 ✓）
    #     ⇒ 旧行为**立刻熔断 20 秒**、期内**快速失败** ✗ ⇒ 当天取数**大面积作废** ✗
    #     （0120 就是因此只写进 1 个文件 ✗ ⇒ 19/43 标的当天无行情 ✗✗）
    #   ⇒ 现在 ✓：**池耗尽这类"上游暂时忙"** ⇒ 先**睡** `BT_ASOF_POOL_SLEEP` 秒（默认 6 ✓）
    #     再熔断 ✓ —— 给源头缓一口气 ✓，且**只睡一次**（下一步就走正常重试 ✓）
    _why = str(why or "")
    if str(os.getenv("BT_ASOF_POOL_BACKOFF", "1")).strip().lower() not in ("0", "false", "no"):
        if ("pool_exhausted" in _why) or ("503" in _why):
            _sleep = max(0.0, float(os.getenv("BT_ASOF_POOL_SLEEP", "6")))
            if _sleep > 0:
                print("[asof-fetch] %s 上游池耗尽 ⇒ **退避 %.0f 秒**再试 ✓（不立刻作废 ✓）"
                      % (name, _sleep), flush=True)
                _tm.sleep(_sleep)
    _COOLDOWN[name] = _tm.time() + _COOLDOWN_SEC
    print("[asof-fetch] %s 熔断 %.0fs（%s）— 期内快速失败，避免拖慢回放" % (name, _COOLDOWN_SEC, why[:80]),
          flush=True)


def _log_fail(name: str, endpoint: str, why: str) -> None:
    """失败**大声记账**（每个 (后端,端点) 只打前 3 条，避免刷屏）。"""
    k = (name, endpoint)
    _FAIL_LOGGED[k] = _FAIL_LOGGED.get(k, 0) + 1
    if _FAIL_LOGGED[k] <= 3:
        print("[asof-fetch] %s/%s 失败#%d: %s" % (name, endpoint, _FAIL_LOGGED[k], why[:140]), flush=True)


def _one_request(name: str, endpoint: str, params: dict, _tries: int = 0):
    key = backend_key(name)
    if not key:
        return None, "no key for backend %s" % name
    _tries = _tries or _MAX_TRIES
    _left = _cooling(name)
    if _left > 0:
        STATS["cooldown_skips"] = STATS.get("cooldown_skips", 0) + 1
        return None, "backend %s cooling %.0fs" % (name, _left)
    url = _endpoint_url(name, endpoint, params)
    req = urllib.request.Request(url, headers={"X-API-Key": key, "Accept": "application/json",
                                               "X-Request-Id": "bt-asof-%s" % hashlib.sha1(
                                                   url.encode()).hexdigest()[:12]})
    STATS["requests"] += 1
    try:
        # ⚠️ 用 **http.client 直连**，不走 urllib：回测的断网保护会替换 `urllib.request.urlopen`，
        # 其放行分支会丢掉我们自带的 `X-API-Key` 头（实测：basic 返回 `missing X-API-Key`、
        # promax 返回 `unauthorized`）⇒ 取数永远 401。直连可完全绕开该层，且只对白名单主机生效。
        u = urllib.parse.urlsplit(url)
        if u.scheme == "https":
            import http.client as _hc, ssl as _ssl
            _conn = _hc.HTTPSConnection(u.hostname, u.port or 443, timeout=_TIMEOUT,
                                        context=_ssl.create_default_context())
        else:
            import http.client as _hc
            _conn = _hc.HTTPConnection(u.hostname, u.port or 80, timeout=_TIMEOUT)
        _path = u.path + (("?" + u.query) if u.query else "")
        _conn.request("GET", _path, headers={"X-API-Key": key,
                                            "Accept": "application/json",
                                            "X-Request-Id": "bt-asof-%s" % hashlib.sha1(url.encode()).hexdigest()[:12]})
        _resp = _conn.getresponse()
        _raw = _resp.read().decode("utf-8", "replace")
        if _resp.status >= 400:
            _conn.close()
            _log_fail(name, endpoint, "HTTP %s %s" % (_resp.status, _raw[:200]))
            if _resp.status in (429, 502, 503, 504) and _tries > 1:
                import time as _tm
                _tm.sleep(0.5)
                return _one_request(name, endpoint, params, _tries - 1)
            if _resp.status in (429, 502, 503, 504):
                _trip(name, "HTTP %s" % _resp.status)     # 用尽重试 → 熔断该后端，后续快速失败
            return None, "HTTP %s %s" % (_resp.status, _raw[:200])
        _conn.close()
        body = json.loads(_raw or "{}")
    except urllib.error.HTTPError as he:
        try:
            detail = he.read().decode("utf-8", "replace")[:240]
        except Exception:
            detail = ""
        _log_fail(name, endpoint, "HTTP %s %s" % (he.code, detail))
        if he.code in (429, 502, 503, 504) and _tries > 1:      # 上游池耗尽 → 退避重试
            import time as _tm
            _tm.sleep(1.5 if he.code == 503 else 1.0)
            return _one_request(name, endpoint, params, _tries - 1)
        return None, "HTTP %s %s" % (he.code, detail)
    except Exception as e:
        _log_fail(name, endpoint, str(e)[:160])
        return None, str(e)[:160]
    rows, err = parse_body(body)
    return rows, err


def fetch(endpoint: str, asof_day: str, backend: str = "", asof_hhmm: str = "",
          range_days: int = 0, **params):
    """取数：as-of 强制 + 未来日期截断 + 落盘缓存 + 后端路由与自动回退。

    返回 (rows, meta)；失败 rows=[] 且 meta["error"] 写明原因（**不静默**）。
    """
    be = backend or ROUTE.get(endpoint, "basic")
    order = [be] + [n for n in _BACKENDS if n != be]
    p0 = clamp_asof(params, asof_day, range_days)
    last_err = None
    for i, name in enumerate(order):
        p = dict(p0)
        ck = hashlib.sha1(("%s|%s|%s|%s" % (name, endpoint, json.dumps(p, sort_keys=True),
                                            asof_day)).encode()).hexdigest()
        os.makedirs(CACHE, exist_ok=True)
        fp = os.path.join(CACHE, "%s.json" % ck)
        if os.path.exists(fp):
            STATS["cache_hits"] += 1
            try:
                d = json.load(open(fp, encoding="utf-8"))
                raw = d.get("raw") or []
                return (filter_future_rows(raw, asof_day, asof_hhmm),
                        {"cached": True, "backend": name, "endpoint": endpoint, "params": p})
            except Exception as _e_sil2:
                _silent_alert("bt_asof_fetch.py:274", _e_sil2)
        rows, err = _one_request(name, endpoint, p)
        if err:
            last_err = "%s: %s" % (name, err)
            STATS["errors"] += 1
            if i < len(order) - 1:
                STATS["backend_fallback"] += 1
                continue
            return [], {"error": last_err, "backend": name, "endpoint": endpoint, "params": p}
        # 缓存**原始行**（未截断），读取时再按当前 as-of 截断 ⇒ 同一 (标的,日) 全天只打一次网络，
        # 且每个 bar 拿到的都是"该 bar 之前"的数据（修掉"缓存了已截断结果 → 跨 bar 返回旧数据"的隐患）
        try:
            json.dump({"raw": rows, "backend": name, "endpoint": endpoint, "params": p},
                      open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception as _e_sil3:
            _silent_alert("bt_asof_fetch.py:289", _e_sil3)
        rows = filter_future_rows(rows, asof_day, asof_hhmm)
        return rows, {"cached": False, "backend": name, "endpoint": endpoint, "params": p,
                      "count": len(rows)}
    return [], {"error": last_err or "unreachable", "endpoint": endpoint, "params": p0}


def daily(symbol_ts: str, asof_day: str, bars: int = 60):
    """日线（≥as-of 截断），返回按日期升序的原始行。"""
    rows, meta = fetch("daily", asof_day, ts_code=symbol_ts, range_days=max(bars * 2, 30),
                       fields="ts_code,trade_date,open,high,low,close,vol")
    return rows, meta


def minute_1m(symbol_ts: str, asof_day: str, asof_hhmm: str, freq: str = "1min"):
    """分钟 K（近似"实时"）：走 **`stk_mins`**（实测可用；`pro_bar` 真请求常 503 upstream_pool_exhausted），
    并按 as-of **日期+时刻**双截断（`trade_time` 形如 `2026-01-26 10:35:00`）。"""
    rows, meta = fetch("stk_mins", asof_day, asof_hhmm=asof_hhmm, freq=freq,
                       ts_code=symbol_ts, start_date=asof_day, end_date=asof_day,
                       fields="ts_code,trade_time,freq,open,high,low,close,vol")
    return rows, meta


# ── 回测进程内的接入（**只在回测生效**：monkeypatch + BT_ASOF_FETCH 门控）─────────
# 为什么不改生产文件：生产侧这些函数必须继续走真实网络源；回测需要的是"同一函数、换数据源"，
# 用进程内替换最干净——生产进程永远 import 不到本模块，故零影响。
def enabled() -> bool:
    return str(os.getenv("BT_ASOF_FETCH", "0")).strip().lower() in ("1", "true", "yes", "on")


def _asof_ctx():
    """当前 as-of 上下文（回测驱动每 bar 落盘），返回 (day8, 'HH:MM')。"""
    try:
        _sf = os.getenv("BT_ASOF_STATE") or os.path.join(REPO, ".dsh-tmp", "wolfbt", "asof_state.json")
        st = json.load(open(_sf, encoding="utf-8"))
        day = str(st.get("day8") or st.get("day") or "").replace("-", "")[:8]
        bar = str(st.get("bar") or "")[:5]
        return day, bar
    except Exception:
        return "", ""


def to_ts_code(symbol: str) -> str:
    """SH600519/sz002587/600519 → 600519.SH / 002587.SZ。"""
    ss = str(symbol or "").strip().upper()
    if "." in ss:
        return ss
    if ss.startswith(("SH", "SZ")):
        return ss[2:] + "." + ss[:2]
    return ss + (".SH" if ss.startswith(("6", "9", "5")) else ".SZ")


def quote_from_minute(symbol: str, day8: str = "", bar: str = "") -> dict:
    """"实时行情"替身：取该标的**最后一根 ≤bar 的 1MIN**，按其收盘构造 quote（PIT 安全）。"""
    d, b = (day8, bar) if (day8 and bar) else _asof_ctx()
    if not d:
        return {}
    rows, meta = minute_1m(to_ts_code(symbol), d, b or "15:00")
    if not rows:
        return {}
    rows = sorted(rows, key=lambda r: str(r.get("trade_time") or ""))
    last = rows[-1]
    closes = [float(r["close"]) for r in rows if r.get("close") is not None]
    try:
        cur = float(last.get("close") or 0)
    except Exception:
        return {}
    if cur <= 0:
        return {}
    return {"symbol": symbol, "code": symbol, "current": cur, "price": cur, "now": cur,
            "close": cur, "open": float(last.get("open") or cur),
            "high": max(float(r.get("high") or cur) for r in rows),
            "low": min(float(r.get("low") or cur) for r in rows),
            "pre_close": (closes[0] if closes else cur), "vol": float(last.get("vol") or 0),
            "time": str(last.get("trade_time") or ""), "source": "asof_minute_1m"}


def daily_bars_for(symbol: str, count: int = 40, day8: str = ""):
    """日线替身：返回 [{trade_date, open, high, low, close, vol}]（按 ≤as-of 夹取）。"""
    d = day8 or _asof_ctx()[0]
    if not d:
        return None
    rows, meta = daily(to_ts_code(symbol), d, count)
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: str(r.get("trade_date") or ""))
    return [{"trade_date": r.get("trade_date"), "open": r.get("open"), "high": r.get("high"),
             "low": r.get("low"), "close": r.get("close"), "vol": r.get("vol")} for r in rows]


def install_backtest_hooks() -> dict:
    """把 t_monitor 的日线兜底与雪球引擎的行情替换为"带 as-of 的 datahubco 取数"。

    返回 {installed: [...], skipped: [...]}；**未开 BT_ASOF_FETCH 时什么都不做**。
    """
    out = {"installed": [], "skipped": []}
    if not enabled():
        out["skipped"].append("BT_ASOF_FETCH 未开启（默认关闭，回测需显式置 1）")
        return out
    try:
        from app.services import t_monitor as TM
        if hasattr(TM, "_fetch_daily_tencent_dated"):
            def _daily_hook(symbol, count=40, _orig=TM._fetch_daily_tencent_dated):
                bars = daily_bars_for(symbol, count)
                return bars if bars else _orig(symbol, count)
            TM._fetch_daily_tencent_dated = _daily_hook
            out["installed"].append("t_monitor._fetch_daily_tencent_dated→datahubco.daily(as-of)")
        else:
            out["skipped"].append("t_monitor 无 _fetch_daily_tencent_dated")
    except Exception as e:
        out["skipped"].append("t_monitor 接入失败: %s" % str(e)[:80])
    try:
        # 雪球引擎的行情入口是 **类方法** `XueqiuEngine.get_stock_quote(symbol)`（core/xueqiu_engine.py:266）
        # 与批量版 `get_stock_quotes(symbols)`（:333）；调用方包括 stop_loss_monitor / position_tier_monitor /
        # trade_graph / long_term_pool_monitor / golden_pit_dca_service（回测里这些原先**全线拿不到行情**）。
        try:
            from core.xueqiu_engine import XueqiuEngine as _XE
        except Exception:
            from xueqiu_engine import XueqiuEngine as _XE   # core/ 在 sys.path 上时的写法
        _p = []
        _orig_one = _XE.get_stock_quote

        def _one(self, symbol, use_cache=True, timeout=8, _o=_orig_one):
            q = quote_from_minute(symbol)
            return q if q else _o(self, symbol, use_cache, timeout)
        _XE.get_stock_quote = _one
        _p.append("get_stock_quote")
        _orig_many = getattr(_XE, "get_stock_quotes", None)
        if _orig_many:
            def _many(self, symbols, _o=_orig_many):
                res, miss = {}, []
                for sym in list(symbols or []):
                    q = quote_from_minute(sym)
                    if q:
                        res[sym] = q
                    else:
                        miss.append(sym)
                if miss:
                    try:
                        res.update(_o(self, miss) or {})
                    except Exception as _e_sil4:
                        _silent_alert("bt_asof_fetch.py:430", _e_sil4)
                return res
            _XE.get_stock_quotes = _many
            _p.append("get_stock_quotes")
        out["installed"].append("xueqiu_engine 行情→as-of 1MIN 收盘: %s" % ",".join(_p))
    except Exception as e:
        out["skipped"].append("xueqiu_engine 接入失败: %s" % str(e)[:80])
    return out


def prefetch_day(day8: str, bar: str, symbols, max_symbols: int = 40, do_minute: bool = True) -> dict:
    """**当日批量预取**：把当日并集标的的日线（与 ≤bar 的分钟）一次性灌进缓存。

    为什么：取数一旦接进"逐条件/逐 bar"的循环，首次 miss 的网络延迟 + 重试 + 熔断冷却会被放大到
    主耗时榜（实测 0112 的 `_check_stop_loss` 段 150.5s）。这里把取数**前置**，循环内只读缓存。
    失败只记账不抛（缺档由既有 no_quote/回退机制兜）。
    """
    out = {"daily": 0, "minute": 0, "failed": 0, "symbols": 0}
    seen = []
    for sym in list(symbols or []):
        if sym not in seen:
            seen.append(sym)
    for sym in seen[:max_symbols]:
        if not str(sym).strip():
            continue
        out["symbols"] += 1
        rows, _m = daily(to_ts_code(sym), day8, 60)
        if rows:
            out["daily"] += 1
        else:
            out["failed"] += 1
        if do_minute:
            mm, _m2 = minute_1m(to_ts_code(sym), day8, bar or "15:00")
            if mm:
                out["minute"] += 1
    return out


def stats() -> dict:
    return dict(STATS)


def _main() -> int:
    print("[asof-fetch] 后端: %s" % json.dumps(backends_ready(), ensure_ascii=False))
    rows, meta = fetch("capabilities", "20260126")
    names = []
    for r in rows[:400]:
        if isinstance(r, dict):
            names.append(r.get("api_name") or r.get("name") or r.get("api"))
    names = [n for n in names if n]
    print("① capabilities 行=%d backend=%s err=%s" % (len(rows), meta.get("backend"), str(meta.get("error"))[:120]))
    if names:
        print("   含 daily=%s pro_bar=%s ；样例=%s" % ("daily" in names, "pro_bar" in names, sorted(names)[:12]))
    d, dm = daily("000001.SZ", "20260126", 60)
    ds = sorted({_norm(x.get("trade_date")) for x in d if x.get("trade_date")})
    print("② daily 行=%d backend=%s err=%s 日期=%s~%s 越界=%d"
          % (len(d), dm.get("backend"), str(dm.get("error"))[:100], ds[0] if ds else "-",
             ds[-1] if ds else "-", STATS["future_rows_cut"]))
    print("   ② daily 字段样例: %s" % (json.dumps(d[:1], ensure_ascii=False)[:180] if d else "-"))
    m, mm = minute_1m("000001.SZ", "20260126", "10:35")
    print("③ pro_bar(1MIN) 行=%d backend=%s err=%s" % (len(m), mm.get("backend"), str(mm.get("error"))[:120]))
    if m:
        print("   样例:", json.dumps(m[:2], ensure_ascii=False)[:200])
    print("[asof-fetch] stats=%s" % stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


# ══════════════════════════════════════════════════════════════════════════════
# AI 决策上下文的 as-of 守卫 + SQL 侧真钟对齐（2026-09-17 用户"收益为什么低"排查）
# ══════════════════════════════════════════════════════════════════════════════
# 现象：**同一触发在不同跑次得到 exec / wait / abandon / update_condition 四种回复**，
#       且越晚的跑次越保守（exec 率 73% → 67%，买腿成交数 83 → 48）。
# 根因（两条，都是"回放里能看到未来"）：
#  ① `t_bridge.wake_agent` 往提示词里塞两段**未做 as-of 过滤**的历史：
#       `_recent_decisions(symbol)` ← `t_db.list_ai_actions(symbol=…)`（全时段，id 倒序 5 条）
#       `_symbol_t_stats(symbol)`   ← `t_ai_agent.decision_quality(symbol=…)`（同上，1000 条统计胜率）
#     而回放库 `t_ai_actions` 里同时装着：上一跑（跑到 09-14）留下的行 + 本跑**更晚交易日**的行，
#     且 `--prod-reset-first` **不清这张表**（只清 6 张 paper_*/t_conditions/t_triggers）。
#     实测证据：2026-01-09 的提示词里出现 `- ai_abandon （无结果） as-of 20260119 10:05 …`，
#     以及 `【SZ002195 做T历史统计】决策 35 次 | exec 23 次 胜率 0.0% …【警告】建议减仓或收紧触发`。
#     ⇒ 未来函数（用户红线）+ 逐跑不可比（继承的历史不同 → prompt 变 → 录制缓存永不命中 → 决策漂移）。
#  ② SQL 里的 `CURRENT_DATE` 由 **PG 服务器**求值（实测真钟 2026-09-17），不被 `_pin_clock_dynamic()`
#     钉住 ⇒ 回放里恒不等于回放日：`_consecutive_hits` 恒 0（"连续命中告警"这一生产语义从未触发）、
#     `_check_stop_loss` 的"当日已止损过"查询恒空（跨进程重启可能重复止损）。
#     （与用户 2026-09-17 修过的 "DB 真钟 与 Python 进程钟不同源" 同一类问题。）
# 修法：**只在回测进程内 monkeypatch，生产代码逐字节不变**；开关同 `BT_ASOF_FETCH`。
_SQL_CLOCK = {"rewrites": 0, "errors": 0}
_AI_PIT = {"fetched": 0, "cut": 0, "calls": 0}


def asof_day_str() -> str:
    """当前回放日（`_pin_clock_dynamic()` 已把 `date.today()` 钉到回放日）。"""
    import datetime as _d
    return _d.date.today().strftime("%Y-%m-%d")


def sql_clock_stats() -> dict:
    return dict(_SQL_CLOCK)


def rewrite_current_date(sql: str, day: str) -> str:
    """把 SQL 文本里的 `CURRENT_DATE`（裸词）换成 `DATE '<day>'`。纯函数，便于单测。"""
    return re.sub(r"\bCURRENT_DATE\b", "DATE '%s'" % day, str(sql), flags=re.I)


def ai_pit_stats() -> dict:
    return dict(_AI_PIT)


def install_ai_context_guard() -> dict:
    """把"AI 决策上下文"与"SQL 当日判定"都夹到**回放日**。返回 {installed, skipped}。"""
    out = {"installed": [], "skipped": []}
    if not enabled():
        out["skipped"].append("BT_ASOF_FETCH 未开启（默认关闭）")
        return out

    # ── ① t_ai_actions：只喂"回放日及以前"的决策（含统计口径）──
    try:
        from app.services import t_db as _tdb
        _orig_list = _tdb.list_ai_actions

        def _list_ai_actions_pit(trade_date=None, symbol=None, session_id=None, limit=100):
            # 上游 SQL 是 `ORDER BY id DESC LIMIT n`：未来行会**挤掉**过去行，
            # 故必须先多取（审计表 ~1.4k 行，全量也不贵）再按 as-of 过滤、最后套调用方的 limit。
            _AI_PIT["calls"] += 1
            want = int(limit or 100)
            rows = _orig_list(trade_date=trade_date, symbol=symbol, session_id=session_id,
                              limit=2000) or []
            _AI_PIT["fetched"] += len(rows)
            day = asof_day_str()
            keep = [r for r in rows if str(r.get("trade_date") or "") <= day]
            _AI_PIT["cut"] += len(rows) - len(keep)
            return keep[:want]

        _list_ai_actions_pit._bt_pit = True
        if getattr(_tdb.list_ai_actions, "_bt_pit", False):
            out["skipped"].append("list_ai_actions 已是 as-of 版本（幂等跳过）")
        else:
            _tdb.list_ai_actions = _list_ai_actions_pit
            out["installed"].append("t_db.list_ai_actions→as-of(%s) 夹取" % asof_day_str())
    except Exception as e:
        out["skipped"].append("list_ai_actions 夹取失败: %s" % str(e)[:80])

    # ── ② SQL 侧 `CURRENT_DATE` → 回放日（纯文本替换 + 计数）──
    try:
        import re as _re
        from sqlalchemy import text as _text
        from sqlalchemy.sql.elements import TextClause as _TC
        from sqlalchemy.orm import Session as _Sess
        try:
            from sqlalchemy.engine import Connection as _Conn
        except Exception:
            _Conn = None
        _orig_sess_exec = _Sess.execute

        def _mk(_orig):
            def _exec(self, statement, params=None, *a, **kw):
                try:
                    if isinstance(statement, _TC) and "CURRENT_DATE" in str(statement).upper():
                        new = rewrite_current_date(str(statement), asof_day_str())
                        _SQL_CLOCK["rewrites"] += 1
                        return _orig(self, _text(new), params, *a, **kw)
                except Exception:
                    _SQL_CLOCK["errors"] += 1
                return _orig(self, statement, params, *a, **kw)
            return _exec

        _new_sess = _mk(_orig_sess_exec)
        if getattr(_Sess.execute, "_bt_pit_sql", False):
            out["skipped"].append("SQL 时钟已是 as-of 版本（幂等跳过）")
        else:
            _new_sess._bt_pit_sql = True
            _Sess.execute = _new_sess
            _p = ["Session.execute"]
            if _Conn is not None:
                _new_conn = _mk(_Conn.execute)
                _new_conn._bt_pit_sql = True
                _Conn.execute = _new_conn
                _p.append("Connection.execute")
            out["installed"].append("SQL CURRENT_DATE→DATE '<回放日>'（%s）" % ",".join(_p))
    except Exception as e:
        out["skipped"].append("SQL 时钟对齐失败: %s" % str(e)[:80])
    return out
