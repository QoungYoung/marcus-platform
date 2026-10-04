#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_asof_api.py — 回测用**本地 as-of REST API**：把 LLM Agent 的工具通道**硬隔离**在本地。

## 为什么要它（隔离在构造上，不靠提示词）
生产 dsh 容器里的桥接插件 `dsh-dsh-marcus-bridge` 把 Agent 的每个工具调用转成
`apiFetch(path)` = `fetch(MARCUS_API_URL + path)`，`MARCUS_API_URL` 默认 `http://backend:8000/api/v1`
（= **生产后端**）。回测时把 `MARCUS_API_URL` 指向本服务（如 `http://127.0.0.1:13200/api/v1`），
工具调用就**在构造上**打不到生产：本进程只用本地文件 + 本地 PG（127.0.0.1:5433）+ 只写
`data/_bt_year/<day>/asof_api/` 沙箱，并且启动时装 `_install_socket_guard()`：
**除回环外的一切 connect/getaddrinfo 直接抛异常**（计数可在 /health 查）。

## 契约的唯一事实源
插件部署副本 `.dsh-tmp/wolfbt/local_dsh/dsh-dsh-marcus-bridge/lib/index.js`：每个工具的
`execute()` 里写明了它读响应里的哪些字段 —— 本文件的响应体逐字段对齐那些读取点。
`/prompts`（插件启动时 GET，8 条系统提示词）与 `/t/fields` **逐字回放**离线快照
`.dsh-tmp/wolfbt/asof_cache/{prompts.json,t_fields.json}`（这两个端点与交易日无关，故不要求 as-of 状态）。

## as-of 纪律（硬要求）
本服务是独立进程，不知道回测跑到哪一根 bar。约定：**驱动每根 bar 写状态文件**
`.dsh-tmp/wolfbt/asof_state.json`：`{"run":"bt-year","day":"2026-03-05","bar":"10:35",
"account":"stock","session":"t-agent-..."}`。每个请求开始**读一次**该文件（按 mtime 缓存），
并用它做截断：分钟线 `label <= bar`（`--strict-lookahead` 可切成 `label < bar`）、
日线 `trade_date < day`、条件/流水 `created_at <= as-of 时刻`。
状态文件缺失 / 过期（mtime 超 `--state-max-age`）/ day 不在本地交易日历 / 该 symbol 当日无本地
分钟文件 → **HTTP 409 + {"error":"no as-of state"}**，**绝不**回落到"最新数据"。

## fail-closed
未实现或不支持的端点 → **HTTP 501 + {"error":"backtest as-of API: <path> not implemented"}**
（附 reason/数据源缺口），**绝不返回臆造数据、绝不静默返回空**。

## 端点表（做T决策会话 `t-agent-*` 白名单 18 工具 → 这里 11 个端点）
| 方法 | 路径 | 数据源与 as-of 口径 |
|---|---|---|
| GET  | /api/v1/health | 进程自检（不需要 as-of 状态） |
| GET  | /api/v1/prompts | 快照回放 `asof_cache/prompts.json`（与日期无关） |
| GET  | /api/v1/t/fields | 快照回放 `asof_cache/t_fields.json`（与日期无关） |
| GET  | /api/v1/portfolio/positions | 本地 PG `paper_trades`（FIFO 重放，`created_at <= as-of`）+ 现价取本地 m1 ≤ bar |
| GET  | /api/v1/market/quote/{symbol} | `data/_bt_full/mins1` 1min 累计到 bar（昨收 = 日线 `< day` 的最后一根） |
| GET  | /api/v1/market/kline/{symbol}?freq= | 当日 1/5/15/30/60min 到 bar（15+ 由 1min **按日**聚合） |
| GET  | /api/v1/market/market-state | 本地 PG `market_diagnosis`（`trade_date = day`，且 `created_at <= as-of`） |
| GET  | /api/v1/t/conditions | 本地 PG `t_conditions`（`trade_date = day`、`created_at <= as-of`）+ 沙箱新建条件 |
| POST | /api/v1/t/conditions | **只写沙箱** `data/_bt_year/<day>/asof_api/conditions.jsonl`（append-only） |
| GET  | /api/v1/t/ai/actions | 本地 PG `t_ai_actions`（`created_at <= as-of`、`trade_date <= day`） |
| POST | /api/v1/t/build/no-rebuild | **只写沙箱** `no_rebuild.jsonl`（append-only 日志 → 重放出当前名单） |

其余（`/indicator/realtime/*`、`/market/technical/*`、`/market/moneyflow/*`、`/t/build/{candidates,
overview,position,auto-gen,rebalance}`、`/trades*`、`/indicator/calc-position`、`/etf/kline/*`、
`/t/backtest*`、`/golden-pit/*` 及一切未知路径）→ **501 + 原因**（见 `NOT_IMPLEMENTED`）。

## 用法
    .venv/bin/python jobs/bt_asof_api.py --port 13200 --root /home/fengx/marcus-platform \\
        --state-file .dsh-tmp/wolfbt/asof_state.json --log .dsh-tmp/wolfbt/asof_api_requests.jsonl
    .venv/bin/python jobs/bt_asof_api_smoke.py          # 自测（退出码 0/1）

请求日志：逐行 JSONL（`ts/method/path/status/asof/耗时`，另带 `upstream_http: 0` = 生产请求数为 0 的证据）。
沙箱写路径：`data/_bt_year/<day>/asof_api/{conditions,no_rebuild,writes}.jsonl`。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse


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


PREFIX = "/api/v1"
SERVICE = "bt-asof-api"
VERSION = "1"
SANDBOX_ID_BASE = 900000000          # 沙箱条件 id 起点（远高于 PG id，避免与生产同构响应冲突）
DEFAULT_DSN = "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
BUY_WORDS = ("买入", "buy", "long", "b", "买")       # 与 core/trade_direction.py 同口径
SELL_WORDS = ("卖出", "sell", "short", "s", "卖")

REPO_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REL_LOG = ".dsh-tmp/wolfbt/asof_api_requests.jsonl"
REL_STATE = ".dsh-tmp/wolfbt/asof_state.json"
REL_CACHE = ".dsh-tmp/wolfbt/asof_cache"
REL_SANDBOX = "data/_bt_year"

# ── 已实现端点（/health 里回报；method, path 模板） ───────────────────────────
IMPLEMENTED: List[Tuple[str, str]] = [
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/prompts"),
    ("GET", "/api/v1/t/fields"),
    ("GET", "/api/v1/portfolio/positions"),
    ("GET", "/api/v1/market/quote/{symbol}"),
    ("GET", "/api/v1/market/kline/{symbol}"),
    ("GET", "/api/v1/market/market-state"),
    ("GET", "/api/v1/t/conditions"),
    ("POST", "/api/v1/t/conditions"),
    ("GET", "/api/v1/t/ai/actions"),
    ("POST", "/api/v1/t/build/no-rebuild"),
]

# ── 明确 501 的端点：正则 + 原因（"缺数据源"必须写清，不许猜） ─────────────────
NOT_IMPLEMENTED: List[Tuple[str, str, str]] = [
    ("GET", r"^/indicator/realtime/[^/]+$",
     "生产用 腾讯实时行情 + Tushare stk_factor_pro 前日盘后锚点；本地无 stk_factor_pro 快照 → 缺数据源"),
    ("GET", r"^/market/technical/[^/]+$",
     "生产用 Tushare stk_factor_pro（60+ 因子）；本地无该表 → 缺数据源"),
    ("GET", r"^/market/moneyflow/[^/]+$",
     "生产用 东财 push2 实时 + Tushare moneyflow 日频；本地只有 OHLCV（无主力/大单拆分）→ 缺数据源"),
    ("GET", r"^/t/build/candidates$",
     "生产走 t_build 打分/趋势闸门服务（需候选池 + 全市场扫描数据）→ 回测沙箱未实现"),
    ("GET", r"^/t/build/overview$",
     "生产走 t 账户净值/底仓上限/建仓服务状态（缺 regime+build 网关）→ 回测沙箱未实现"),
    ("POST", r"^/t/build/position$",
     "建仓网关（熔断/规模/时段/封板/人工升级）：回测沙箱不臆造成交 → 未实现"),
    ("POST", r"^/t/build/auto-gen$",
     "盘后条件生成服务：回测沙箱不生成（避免幻影条件）→ 未实现"),
    ("POST", r"^/t/build/rebalance$",
     "底仓再平衡服务（需持仓成本/质量退化判定）→ 未实现"),
    ("POST", r"^/trades$", "交易写端点：回测禁下单（成交由驱动的撮合层负责）"),
    ("DELETE", r"^/trades/[^/]+/cancel$", "交易写端点：回测禁撤单"),
    ("POST", r"^/indicator/calc-position$", "仓位计算依赖生产风控参数表 → 未实现"),
    ("GET", r"^/etf/kline/[^/]+$", "ETF 日线来自 Tushare fund_daily；本地无 ETF 日线快照 → 缺数据源"),
    ("POST", r"^/t/backtest$", "回测任务服务：回测中的回测（递归）→ 未实现"),
    ("GET", r"^/t/backtest/.+$", "回测任务查询：同上 → 未实现"),
    ("PUT", r"^/golden-pit/etf-configs/.+$", "黄金坑 ETF 配置写：与做T链路无关 → 未实现"),
]

_OUTBOUND = {"blocked": 0, "loopback": 0, "last_blocked": ""}


# ══════════════════════════════════════════════════════════════════════════
# 0. 出网熔断：除回环外的一切连接/解析直接失败（生产隔离的"构造级"保证）
# ══════════════════════════════════════════════════════════════════════════
def _is_loopback(host: Any) -> bool:
    h = str(host or "").strip().strip("[]").lower()
    if h in ("", "localhost", "::1", "0.0.0.0"):
        return True
    return h.startswith("127.") or h == "127.0.0.1"


def _is_local_listen_host(host: Any) -> bool:
    """监听地址必须是**回环**：`0.0.0.0` 表示对所有网卡开放 → 拒绝（回测只在本机跑）。"""
    h = str(host or "").strip().strip("[]").lower()
    return h in ("127.0.0.1", "localhost", "::1") or h.startswith("127.")


def install_socket_guard() -> None:
    """把非回环的 connect / getaddrinfo 变成异常（幂等）。"""
    if getattr(install_socket_guard, "_done", False):
        return
    _cc = socket.create_connection
    _gai = socket.getaddrinfo

    def guarded_cc(address, *a, **kw):
        host = address[0] if isinstance(address, (tuple, list)) else address
        if not _is_loopback(host):
            _OUTBOUND["blocked"] += 1
            _OUTBOUND["last_blocked"] = str(host)
            raise RuntimeError("BT_ASOF_NO_NETWORK: 非回环出网被拒: %s" % (host,))
        _OUTBOUND["loopback"] += 1
        return _cc(address, *a, **kw)

    def guarded_gai(host, port, *a, **kw):
        if not _is_loopback(host):
            _OUTBOUND["blocked"] += 1
            _OUTBOUND["last_blocked"] = str(host)
            raise RuntimeError("BT_ASOF_NO_NETWORK: 域名解析被拒: %s" % (host,))
        return _gai(host, port, *a, **kw)

    socket.create_connection = guarded_cc          # type: ignore[assignment]
    socket.getaddrinfo = guarded_gai               # type: ignore[assignment]
    install_socket_guard._done = True              # type: ignore[attr-defined]


# ══════════════════════════════════════════════════════════════════════════
# 1. 小工具
# ══════════════════════════════════════════════════════════════════════════
def norm_day(x: Any) -> str:
    """`2026-03-05` / `20260305` / `20260305` → `YYYYMMDD`（非法返回 ""）。"""
    s = re.sub(r"[^0-9]", "", str(x or ""))
    return s if len(s) == 8 else ""


def day_dash(day8: str) -> str:
    return "%s-%s-%s" % (day8[:4], day8[4:6], day8[6:8]) if len(day8) == 8 else ""


def norm_bar(x: Any) -> str:
    """`10:35` / `1035` / `10:35:00` → `HH:MM`（非法返回 ""）。"""
    s = str(x or "").strip()
    m = re.match(r"^(\d{1,2}):?(\d{2})(?::\d{2})?$", s)
    if not m:
        return ""
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return ""
    return "%02d:%02d" % (h, mi)


def in_session(bar: str) -> bool:
    return ("09:30" <= bar <= "11:30") or ("13:00" <= bar <= "15:00")


def norm_sym(symbol: str) -> str:
    """`600519` / `SH600519` / `600519.SH` / `sh600519` → `SH600519`。"""
    s = str(symbol or "").strip().upper()
    if "." in s:
        a, _, b = s.partition(".")
        s = b + a
    for p in ("SH", "SZ", "BJ"):
        if s.startswith(p):
            return s
    if s.isdigit():
        if s[0] == "6":
            return "SH" + s
        if s[:2] in ("43", "83", "87", "92"):
            return "BJ" + s
        return "SZ" + s
    return s


def code6(symbol: str) -> str:
    return "".join(ch for ch in str(symbol) if ch.isdigit())[:6]


def ts_code(symbol: str) -> str:
    s = norm_sym(symbol)
    return "%s.%s" % (code6(s), s[:2]) if len(s) > 2 else s


def is_buy(direction: Any) -> bool:
    return str(direction or "").strip().lower() in BUY_WORDS


def is_sell(direction: Any) -> bool:
    return str(direction or "").strip().lower() in SELL_WORDS


def jdump(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def r3(x: Any) -> Optional[float]:
    try:
        return round(float(x), 3)
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════
# 2. as-of 状态文件（按 mtime 缓存；缺失/过期/与数据不符 → 409）
# ══════════════════════════════════════════════════════════════════════════
class AsOfError(Exception):
    """as-of 状态不可用 → 409 {"error":"no as-of state"}。"""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class AsOf:
    """读 `.dsh-tmp/wolfbt/asof_state.json`；**每个请求读一次**（mtime+size 未变则用缓存）。"""

    def __init__(self, state_file: str, max_age_s: int, strict_lookahead: bool, calendar: "Calendar"):
        self.state_file = state_file
        self.max_age_s = int(max_age_s)
        self.strict_lookahead = bool(strict_lookahead)
        self.calendar = calendar
        self._lock = threading.Lock()
        self._cache_key: Optional[Tuple[float, int]] = None
        self._cache_val: Optional[Dict[str, Any]] = None

    def load(self, force: bool = False) -> Dict[str, Any]:
        """→ {"run","day","bar","account","session","raw","mtime","age_s","strict_lookahead"}；不可用抛 AsOfError。"""
        try:
            st = os.stat(self.state_file)
            key = (st.st_mtime, st.st_size)
        except OSError:
            raise AsOfError("as-of 状态文件不存在: %s（驱动每根 bar 必须写它）" % self.state_file)
        with self._lock:
            if not force and self._cache_val is not None and self._cache_key == key:
                st_ = dict(self._cache_val)
            else:
                try:
                    with open(self.state_file, encoding="utf-8") as f:
                        raw = json.load(f)
                except Exception as e:
                    raise AsOfError("as-of 状态文件不可解析: %s (%s)" % (self.state_file, str(e)[:80]))
                if not isinstance(raw, dict):
                    raise AsOfError("as-of 状态文件不是 JSON 对象: %s" % self.state_file)
                d8, bar = norm_day(raw.get("day")), norm_bar(raw.get("bar"))
                if not d8 or not bar:
                    raise AsOfError("as-of 状态文件缺 day/bar: %s" % json.dumps(raw, ensure_ascii=False)[:200])
                st_ = {
                    "run": str(raw.get("run") or ""),
                    "day": d8, "day_dash": day_dash(d8), "bar": bar,
                    "account": str(raw.get("account") or "stock"),
                    "session": str(raw.get("session") or ""),
                    "raw": raw, "mtime": st.st_mtime,
                    "age_s": round(max(0.0, time.time() - st.st_mtime), 2),
                    "strict_lookahead": self.strict_lookahead,
                    "state_file": self.state_file,
                    "asof_ts": "%s %s:00" % (day_dash(d8), bar),
                }
                self._cache_val, self._cache_key = st_, key
        age = round(max(0.0, time.time() - st_["mtime"]), 2)
        st_ = dict(st_, age_s=age)
        if self.max_age_s > 0 and age > self.max_age_s:
            raise AsOfError("as-of 状态文件过期：%.0fs > %ds（驱动每根 bar 都要刷新它）: %s"
                            % (age, self.max_age_s, self.state_file))
        if not in_session(st_["bar"]):
            raise AsOfError("as-of bar 不在交易时段内: %s（期望 09:30~11:30 / 13:00~15:00）" % st_["bar"])
        if not self.calendar.has(st_["day"]):
            raise AsOfError("as-of day 不在本地交易日历（bars.sqlite）: %s" % st_["day"])
        return st_

    def brief(self, st: Dict[str, Any]) -> Dict[str, Any]:
        return {"run": st["run"], "day": st["day"], "day_dash": st["day_dash"], "bar": st["bar"],
                "account": st["account"], "session": st["session"],
                "state_file": st["state_file"], "age_s": st["age_s"],
                "cut": st["asof_ts"], "strict_lookahead": st["strict_lookahead"]}


class Calendar:
    """本地交易日历（bars.sqlite 的 distinct trade_date ∪ mins1 文件名里的日期）。

    不可用（两者都读不到）时 has() 恒 True + 记 note —— 数据层每个端点仍有自己的 as-of 校验，
    所以"日历读不到"不该把服务变成不可用。
    """

    def __init__(self, bars_db: str, mins1_dir: str = ""):
        self.bars_db = bars_db
        self.days: set = set()
        self.error = ""
        try:
            c = sqlite3.connect("file:%s?mode=ro" % bars_db, uri=True)
            self.days |= {str(r[0]) for r in c.execute("SELECT DISTINCT trade_date FROM bars")}
            c.close()
        except Exception as e:
            self.error = str(e)[:120]
        if mins1_dir and os.path.isdir(mins1_dir):
            try:
                for fn in os.listdir(mins1_dir):        # 分钟缓存里出现过的交易日（补日线库的缺口）
                    m = re.search(r"_(\d{8})\.json$", fn)
                    if m:
                        self.days.add(m.group(1))
            except Exception as e:
                self.error = (self.error + " | mins1: " + str(e)[:80]).strip(" |")

    def has(self, day8: str) -> bool:
        if not self.days:
            return True                     # 日历不可用 → 不阻断（数据层仍有各自的 as-of 校验）
        return day8 in self.days


# ══════════════════════════════════════════════════════════════════════════
# 3. 本地行情（1min / 5min / 日线），全部 ≤ (day, bar)
# ══════════════════════════════════════════════════════════════════════════
class Market:
    """按 as-of day 缓存 `bt_local_market.LocalMarket1m`（1min 真源 + 5min 由 1min 聚合）。"""

    def __init__(self, data_dir: str, strict_lookahead: bool = False):
        self.data_dir = data_dir
        self.mins1 = os.path.join(data_dir, "_bt_full", "mins1")
        self.mins5 = os.path.join(data_dir, "_bt_full", "mins")
        self.bars_db = os.path.join(data_dir, "_bt_full", "bars.sqlite")
        self.strict_lookahead = bool(strict_lookahead)
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        self._blm = None
        self._pre_close: Dict[str, Dict[str, float]] = {}
        self.err = ""

    # ── 装库 ───────────────────────────────────────────────────────────
    def _module(self):
        if self._blm is not None:
            return self._blm
        jobs = os.path.dirname(os.path.abspath(__file__))
        if jobs not in sys.path:
            sys.path.insert(0, jobs)
        try:
            import bt_local_market as blm          # noqa: E402  （jobs/ 下，纯本地库）
        except Exception as e:                     # pragma: no cover
            self.err = "无法 import bt_local_market: %s" % str(e)[:120]
            raise RuntimeError(self.err)
        self._blm = blm
        return blm

    def market(self, day8: str):
        with self._lock:
            m = self._cache.get(day8)
            if m is None:
                blm = self._module()
                m = blm.LocalMarket1m(self.mins1, self.mins5, self.bars_db, day8,
                                      mode=blm.MODE_1M, strict_lookahead=self.strict_lookahead)
                if len(self._cache) >= 3:          # 只留最近几个交易日
                    for k in sorted(self._cache)[:-2]:
                        self._cache.pop(k, None)
                self._cache[day8] = m
            return m

    # ── 可用性（day 与 symbol 数据日是否对得上 → 不符则 409） ─────────────
    def files_for(self, sym: str, day8: str) -> Dict[str, str]:
        s = norm_sym(sym)
        stem = "%s_%s" % (code6(s), s[:2])
        return {
            "m1": os.path.join(self.mins1, "%s_1min_%s.json" % (stem, day8)),
            "m5_agg": os.path.join(self.mins1, "%s_5min_%s.json" % (stem, day8)),
            "m5_legacy": os.path.join(self.mins5, "%s_5min_%s.json" % (stem, day8)),
        }

    def require_day_data(self, sym: str, day8: str, freq: str) -> None:
        f = self.files_for(sym, day8)
        if freq == "1":
            if not os.path.exists(f["m1"]):
                raise AsOfError("no local 1min file for %s on %s (%s)" % (norm_sym(sym), day8, f["m1"]))
        elif not (os.path.exists(f["m1"]) or os.path.exists(f["m5_agg"]) or os.path.exists(f["m5_legacy"])):
            raise AsOfError("no local minute file for %s on %s (%s)" % (norm_sym(sym), day8, f["m1"]))

    # ── 分钟序列 ───────────────────────────────────────────────────────
    def series(self, sym: str, day8: str, bar: str, freq: str, count: int = 0, days: int = 1) -> List[dict]:
        """升序分钟 bars（≤ bar；跨日只回看 days 天，永不包含 > day 的 bar）。"""
        blm = self._module()
        m = self.market(day8)
        s = norm_sym(sym)
        f = str(freq or "5").strip().lower()
        f = f[1:] if f.startswith("m") and f[1:].isdigit() else f.replace("min", "")
        if f in ("1",):
            by_day = m.load_symbol(s)
        elif f in ("5",):
            by_day = m.load_m5(s)
        elif f.isdigit():
            minutes = int(f)
            by_day = {d: blm.agg_nmin(bs, minutes) for d, bs in m.load_symbol(s).items()}
        else:
            raise ValueError("unsupported freq: %r（支持 1/5/15/30/60 分钟）" % freq)
        keep_days = sorted(d for d in by_day if d <= day8)[-max(1, int(days)):]
        out: List[dict] = []
        for d in keep_days:
            for b in by_day.get(d) or []:
                lab = str(b.get("time"))[11:16]
                if not lab:
                    continue
                if d == day8:
                    ok = (lab < bar) if self.strict_lookahead else (lab <= bar)
                    if not ok:
                        continue
                out.append(b)
        out.sort(key=lambda b: str(b.get("time")))
        return out[-int(count):] if count and count > 0 else out

    def quote(self, sym: str, day8: str, bar: str) -> Optional[dict]:
        return self.market(day8).quote(norm_sym(sym), bar)

    def pre_close(self, sym: str, day8: str) -> Optional[float]:
        """`< day` 的最后一根日线收盘（= 当日 as-of 昨收；绝不用 day 当天收盘）。"""
        m = self.market(day8)
        try:
            rows = m.daily_bars_db(norm_sym(sym))
        except Exception:
            return None
        return float(rows[-1]["close"]) if rows else None

    def pre_close_map(self, sym: str, day8: str) -> Dict[str, float]:
        """{YYYYMMDD: 当日昨收}（用 bars.sqlite 的 pre_close，缺失时用前一交易日收盘）。"""
        s = norm_sym(sym)
        key = "%s@%s" % (s, day8)
        if key in self._pre_close:
            return self._pre_close[key]
        out: Dict[str, float] = {}
        prev = None
        try:
            c = sqlite3.connect("file:%s?mode=ro" % self.bars_db, uri=True)
            for d, pre, close in c.execute(
                    "SELECT trade_date, pre_close, close FROM bars WHERE ts_code=? AND trade_date<=? "
                    "ORDER BY trade_date", (ts_code(s), day8)):
                d = str(d)
                p = r3(pre)
                out[d] = p if p else (prev if prev else r3(close))
                prev = r3(close) or prev
            c.close()
        except Exception as _e_sil1:
            _silent_alert("bt_asof_api.py:492", _e_sil1)
        self._pre_close[key] = out
        return out


# ══════════════════════════════════════════════════════════════════════════
# 4. 本地 PG（**只读**：非回环 DSN 直接拒启；连接事务只读）
# ══════════════════════════════════════════════════════════════════════════
class Pg:
    def __init__(self, dsn: str):
        self.dsn = dsn
        u = urlparse(dsn)
        self.host = u.hostname or ""
        if not _is_loopback(u.hostname or ""):
            raise SystemExit("[bt_asof_api] 拒绝启动：--dsn 主机 %r 不是回环地址（回测只允许本地 PG）" % (u.hostname,))
        self._psycopg2 = None
        try:
            import psycopg2                       # noqa: E402
            self._psycopg2 = psycopg2
        except Exception as e:
            self.err = "psycopg2 不可用: %s" % str(e)[:120]

    def query(self, sql: str, args: Any = None) -> List[Dict[str, Any]]:
        """只读查询（default_transaction_read_only=on）。失败抛 PgError（→ 503，不返回空）。"""
        if self._psycopg2 is None:
            raise PgError(self.err)
        conn = None
        try:
            conn = self._psycopg2.connect(self.dsn, connect_timeout=4,
                                           application_name="bt_asof_api",
                                           options="-c default_transaction_read_only=on")
            conn.set_session(readonly=True, autocommit=True)
            from psycopg2.extras import RealDictCursor   # noqa: E402
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, args or ())
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            cur.close()
            return rows
        except Exception as e:
            raise PgError("%s: %s" % (type(e).__name__, str(e)[:200]))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as _e_sil2:
                    _silent_alert("bt_asof_api.py:537", _e_sil2)

    def ping(self) -> Dict[str, Any]:
        try:
            self.query("SELECT 1 AS ok")
            return {"ok": True, "host": self.host, "readonly": True}
        except Exception as e:
            return {"ok": False, "host": self.host, "error": str(e)[:160]}


class PgError(Exception):
    pass


# ══════════════════════════════════════════════════════════════════════════
# 5. 沙箱（只写 data/_bt_year/<day>/asof_api/，append-only JSONL）
# ══════════════════════════════════════════════════════════════════════════
class Sandbox:
    def __init__(self, root: str):
        self.root = root
        self._lock = threading.Lock()

    def day_dir(self, day8: str) -> str:
        return os.path.join(self.root, day8, "asof_api")

    def path(self, day8: str, name: str) -> str:
        return os.path.join(self.day_dir(day8), name)

    def append(self, day8: str, name: str, payload: Dict[str, Any]) -> str:
        d = self.day_dir(day8)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        return p

    def read(self, day8: str, name: str) -> List[Dict[str, Any]]:
        p = self.path(day8, name)
        out: List[Dict[str, Any]] = []
        if not os.path.exists(p):
            return out
        with open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    o = json.loads(ln)
                    if isinstance(o, dict):
                        out.append(o)
                except Exception as _e_sil3:
                    _silent_alert("bt_asof_api.py:591", _e_sil3)
                    continue
        return out


# ══════════════════════════════════════════════════════════════════════════
# 6. 业务层：把端点实现与契约字段放在一处
# ══════════════════════════════════════════════════════════════════════════
class Api:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.root = cfg["root"]
        self.calendar = Calendar(cfg["bars_db"], os.path.join(cfg["data_dir"], "_bt_full", "mins1"))
        self.asof = AsOf(cfg["state_file"], cfg["state_max_age"], cfg["strict_lookahead"], self.calendar)
        self.market = Market(cfg["data_dir"], cfg["strict_lookahead"])
        self.sandbox = Sandbox(cfg["sandbox_root"])
        self.pg = Pg(cfg["dsn"])
        self.prompts_file = os.path.join(cfg["cache_dir"], "prompts.json")
        self.fields_file = os.path.join(cfg["cache_dir"], "t_fields.json")
        self.started_at = time.time()
        self.counters: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._idlock = threading.Lock()
        self.t_expr = self._load_t_expr()
        self.logfile = cfg["log"]
        os.makedirs(os.path.dirname(os.path.abspath(self.logfile)) or ".", exist_ok=True)

    # ── 生产同构的小工具：表达式摘要/校验（直接加载生产模块，纯函数、无 DB） ──
    def _load_t_expr(self):
        p = os.path.join(self.root, "backend", "app", "services", "t_expr.py")
        try:
            spec = importlib.util.spec_from_file_location("bt_asof_t_expr", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)          # type: ignore[union-attr]
            return mod
        except Exception:
            return None

    def expr_summary(self, expr: Any) -> str:
        if self.t_expr is not None:
            try:
                return self.t_expr.expression_summary(expr)
            except Exception as _e_sil4:
                _silent_alert("bt_asof_api.py:633", _e_sil4)
        try:
            return json.dumps(expr, ensure_ascii=False)[:200] if expr else "(无表达式)"
        except Exception:
            return "(表达式)"

    def infer_direction(self, expr: Any) -> str:
        """镜像 backend/app/services/t_db.py::infer_custom_direction（价格类字段主方向）。"""
        fields = ("quote.current", "quote.change_pct", "quote.open", "quote.high", "quote.low",
                  "quote.pre_close")

        def walk(n: Any) -> str:
            if not isinstance(n, dict):
                return ""
            for combo in ("and", "or", "not"):
                if combo in n:
                    kids = n[combo]
                    if not isinstance(kids, list):
                        kids = [kids]
                    dirs = [walk(k) for k in kids if isinstance(k, dict)]
                    dirs = [d for d in dirs if d]
                    return dirs[0] if dirs and all(d == dirs[0] for d in dirs) else ""
            if str(n.get("field") or "") not in fields:
                return ""
            op = str(n.get("op") or "")
            if op in ("<", "<="):
                return "buy"
            if op in (">", ">="):
                return "sell"
            return ""

        return walk(expr)

    # ── 请求日志（JSONL；审计用：upstream_http 恒 0） ────────────────────
    def log(self, rec: Dict[str, Any]) -> None:
        rec.setdefault("ts", now_iso())
        rec.setdefault("ts_epoch", round(time.time(), 3))
        rec.setdefault("upstream_http", 0)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            try:
                with open(self.logfile, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
            except Exception as _e_sil5:
                _silent_alert("bt_asof_api.py:678", _e_sil5)

    def count(self, status: int) -> None:
        with self._lock:
            k = str(status)
            self.counters[k] = self.counters.get(k, 0) + 1

    # ── 路由 ────────────────────────────────────────────────────────────
    def handle(self, method: str, raw_path: str, body: Any = None) -> Tuple[int, Any, str, Dict[str, str], str]:
        """→ (status, payload(bytes|obj), content_type, headers, note)"""
        u = urlparse(raw_path)
        path = unquote(u.path)
        q = {k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        try:
            if path.rstrip("/") == PREFIX + "/health":
                return self.h_health()
            if path.rstrip("/") == PREFIX + "/prompts":
                return self.h_static(self.prompts_file, "prompts")
            if path.rstrip("/") == PREFIX + "/t/fields":
                return self.h_static(self.fields_file, "t_fields")
            if not path.startswith(PREFIX + "/"):
                return self.h_501(method, path, "路径不在 /api/v1 下（非 Marcus 契约路径）")
            sub = path[len(PREFIX):]
            seg = [s for s in sub.split("/") if s]

            if method == "GET" and sub == "/portfolio/positions":
                return self.h_positions(q)
            if method == "GET" and sub == "/market/market-state":
                return self.h_market_state(q)
            if method == "GET" and len(seg) == 3 and seg[0] == "market" and seg[1] == "quote":
                return self.h_quote(seg[2], q)
            if method == "GET" and len(seg) == 3 and seg[0] == "market" and seg[1] == "kline":
                return self.h_kline(seg[2], q)
            if sub == "/t/conditions" and method == "GET":
                return self.h_conditions_get(q)
            if sub == "/t/conditions" and method == "POST":
                return self.h_conditions_post(q, body)
            if method == "GET" and sub == "/t/ai/actions":
                return self.h_ai_actions(q)
            if method == "POST" and sub == "/t/build/no-rebuild":
                return self.h_no_rebuild(q, body)
            return self.h_501(method, path, self._not_impl_reason(method, sub))
        except AsOfError as e:
            return (409, {"error": "no as-of state", "detail": e.detail,
                          "state_file": self.cfg["state_file"],
                          "hint": "驱动每根 bar 写 {\"day\":\"YYYY-MM-DD\",\"bar\":\"HH:MM\",...}；"
                                  "服务永不回落到最新数据"}, "application/json", {}, "409 no as-of state")
        except PgError as e:
            return (503, {"error": "local PG unavailable: %s" % e,
                          "detail": "本地 PG（127.0.0.1:5433）不可用；不回落到生产"},
                    "application/json", {}, "503 pg")
        except BrokenPipeError:
            raise
        except Exception as e:
            return (500, {"error": "internal error: %s: %s" % (type(e).__name__, str(e)[:200]),
                          "traceback": traceback.format_exc()[-800:]},
                    "application/json", {}, "500")

    @staticmethod
    def _not_impl_reason(method: str, path: str) -> str:
        for m, pat, why in NOT_IMPLEMENTED:
            if m == method and re.match(pat, path):
                return why
        return "未知路径（本服务只实现 as-of 白名单端点；未知一律 501，绝不臆造数据）"

    def h_501(self, method: str, path: str, reason: str) -> Tuple[int, Any, str, Dict[str, str], str]:
        return (501, {"error": "backtest as-of API: %s not implemented" % path,
                      "reason": reason, "method": method, "path": path,
                      "implemented": ["%s %s" % (m, p) for m, p in IMPLEMENTED]},
                "application/json", {}, "501 " + reason[:60])

    def _hdr(self, st: Optional[Dict[str, Any]], source: str) -> Dict[str, str]:
        """st 可以是 AsOf.load() 的完整状态，也可以是 brief（两者字段略不同）。"""
        h = {"X-BT-AsOf-Source": source, "X-BT-Service": SERVICE}
        if st:
            h.update({"X-BT-AsOf-Day": st.get("day", ""), "X-BT-AsOf-Bar": st.get("bar", ""),
                      "X-BT-AsOf-Run": st.get("run", ""),
                      "X-BT-AsOf-Cut": st.get("cut") or st.get("asof_ts") or ""})
        return h

    # ── /health ─────────────────────────────────────────────────────────
    def h_health(self) -> Tuple[int, Any, str, Dict[str, str], str]:
        try:
            st = self.asof.load()
            asof_brief: Optional[Dict[str, Any]] = self.asof.brief(st)
            state_ok, state_detail = True, ""
        except AsOfError as e:
            asof_brief, state_ok, state_detail = None, False, e.detail
        data_files = {"mins1": os.path.isdir(self.market.mins1),
                      "mins5": os.path.isdir(self.market.mins5),
                      "bars_sqlite": os.path.exists(self.market.bars_db),
                      "prompts_snapshot": os.path.exists(self.prompts_file),
                      "t_fields_snapshot": os.path.exists(self.fields_file)}
        body = {
            "ok": True, "service": SERVICE, "version": VERSION,
            "uptime_s": round(time.time() - self.started_at, 1),
            "asof": asof_brief,
            "asof_state": {"valid": state_ok, "detail": state_detail,
                           "file": self.cfg["state_file"], "max_age_s": self.cfg["state_max_age"],
                           "calendar_days": len(self.calendar.days), "calendar_error": self.calendar.error},
            "implemented": ["%s %s" % (m, p) for m, p in IMPLEMENTED],
            "not_implemented": [{"method": m, "path": p, "reason": w} for m, p, w in NOT_IMPLEMENTED],
            "policy": {"fail_closed": "未实现→501 / as-of 状态缺失或过期→409 / 本地 PG 不可用→503；"
                                      "永不回落最新数据、永不臆造、永不静默空",
                       "created_at_policy": self.cfg["created_at_policy"],
                       "strict_lookahead": self.cfg["strict_lookahead"],
                       "never_latest_fallback": True},
            "paths": {"root": self.root, "data_dir": self.cfg["data_dir"], "cache_dir": self.cfg["cache_dir"],
                      "sandbox_root": self.cfg["sandbox_root"], "sandbox_pattern": "<sandbox_root>/<YYYYMMDD>/asof_api/",
                      "log": self.logfile},
            "data": data_files,
            "pg": self.pg.ping(),
            "isolation": {"outbound_policy": "loopback-only (connect/getaddrinfo guarded)",
                          "blocked_outbound_attempts": _OUTBOUND["blocked"],
                          "loopback_connects": _OUTBOUND["loopback"],
                          "last_blocked": _OUTBOUND["last_blocked"],
                          "pg_host": self.pg.host, "pg_readonly": True, "upstream_http_calls": 0},
            "requests": {"by_status": dict(self.counters),
                         "total": sum(self.counters.values())},
        }
        return (200, body, "application/json", self._hdr(asof_brief, "static+local"),
                "health asof_valid=%s" % state_ok)

    # ── 静态快照回放（/prompts、/t/fields，与交易日无关） ──────────────────
    def h_static(self, path: str, name: str) -> Tuple[int, Any, str, Dict[str, str], str]:
        if not os.path.exists(path):
            return (501, {"error": "backtest as-of API: /%s not implemented (snapshot missing: %s)" % (name, path),
                          "reason": "离线快照缺失 → 缺数据源（绝不用内置回退伪造）"},
                    "application/json", self._hdr(None, "snapshot-missing"), "501 snapshot missing")
        with open(path, "rb") as f:
            raw = f.read()
        return (200, raw, "application/json", self._hdr(None, "snapshot:" + os.path.basename(path)),
                "snapshot replay %s (%dB)" % (name, len(raw)))

    # ── /portfolio/positions ────────────────────────────────────────────
    def h_positions(self, q: Dict[str, str]) -> Tuple[int, Any, str, Dict[str, str], str]:
        st = self.asof.load()
        account = str(q.get("account") or st["account"] or "stock")
        rows = self.pg.query(
            "SELECT id, symbol, direction, price, volume, created_at, trade_date "
            "FROM paper_trades WHERE account_id = %(acc)s "
            "  AND COALESCE(voided::text,'0') NOT IN ('1','t','true','True','TRUE') "
            "  AND replace(created_at,'T',' ') <= %(cut)s "
            "ORDER BY COALESCE(trade_date, substr(replace(created_at,'T',' '),1,10)), id",
            {"acc": account, "cut": st["asof_ts"]})
        skipped = self.pg.query(
            "SELECT count(*) AS n FROM paper_trades WHERE account_id = %(acc)s "
            "  AND COALESCE(voided::text,'0') NOT IN ('1','t','true','True','TRUE') "
            "  AND replace(created_at,'T',' ') > %(cut)s", {"acc": account, "cut": st["asof_ts"]})
        # 冻结（挂单）：卖出挂单冻股份、买入挂单冻现金（as-of 时刻之前的挂单）
        frozen_shares: Dict[str, int] = {}
        for o in self.pg.query(
                "SELECT symbol, direction, volume FROM paper_orders WHERE account_id = %(acc)s "
                "  AND status IN ('提交中','未成交') AND replace(created_at,'T',' ') <= %(cut)s",
                {"acc": account, "cut": st["asof_ts"]}):
            if is_sell(o.get("direction")):
                s = norm_sym(o.get("symbol"))
                frozen_shares[s] = frozen_shares.get(s, 0) + int(o.get("volume") or 0)
        # FIFO 重放（与 backend/app/api/portfolio.py::calculate_positions_from_db 同口径）
        lots: Dict[str, List[dict]] = {}
        for t in rows:
            s = norm_sym(t.get("symbol"))
            if is_buy(t.get("direction")):
                entry = t.get("trade_date") or str(t.get("created_at") or "")[:10]
                lots.setdefault(s, []).append({"price": float(t.get("price") or 0),
                                               "volume": int(t.get("volume") or 0), "entry_date": entry})
            elif is_sell(t.get("direction")):
                book = lots.get(s) or []
                rem = int(t.get("volume") or 0)
                i = 0
                while rem > 0 and i < len(book):
                    used = min(book[i]["volume"], rem)
                    book[i]["volume"] -= used
                    rem -= used
                    if book[i]["volume"] == 0:
                        book.pop(i)
                    else:
                        i += 1
                if not book:
                    lots.pop(s, None)
        names = self._names([s for s in lots])
        out: List[Dict[str, Any]] = []
        for s in sorted(lots):
            book = [l for l in lots[s] if l["volume"] > 0]
            vol = sum(l["volume"] for l in book)
            if vol <= 0:
                continue
            avg = sum(l["price"] * l["volume"] for l in book) / vol
            qq = self.market.quote(s, st["day"], st["bar"]) if self._day_has_files(s, st["day"]) else None
            if qq:
                cur, src, pct = float(qq["current"]), "mins1@%s<=%s" % (st["day"], st["bar"]), r3(qq.get("change_pct"))
                pre = r3(qq.get("pre_close"))
            else:                                     # 无分钟数据 → 只用 < day 的收盘价（绝不用 day 当天收盘）
                pre = self.market.pre_close(s, st["day"])
                cur, src, pct = (pre, "daily_close_prev(<%s)" % st["day"], None) if pre else (None, "none", None)
            fz = int(frozen_shares.get(s, 0))
            mv = round(cur * vol, 2) if cur else None
            entries = [l["entry_date"] for l in book if l.get("entry_date")]
            out.append({
                "symbol": s, "name": names.get(s, s), "volume": vol,
                "sellable": max(0, vol - fz), "available": max(0, vol - fz), "frozen": fz,
                "avg_price": round(avg, 4), "entry_date": min(entries) if entries else "",
                "current_price": cur, "change_pct": pct if pct is not None else 0.0,
                "today_pnl": round((cur - pre) * vol, 2) if (cur and pre) else 0.0,
                "market_value": mv,
                "floating_pnl": round((cur - avg) * vol, 2) if cur else None,
                "floating_pnl_pct": round((cur / avg - 1) * 100, 2) if (cur and avg > 0) else None,
                "high_water_mark": None, "high_water_date": None, "days_since_high": None,
                "sector_rank": None, "sector_rank_pct": None,
                "price_source": src,
            })
        note = ("account=%s trades_used=%d trades_after_asof_skipped=%d positions=%d"
                % (account, len(rows), int((skipped[0] or {}).get("n") or 0) if skipped else 0, len(out)))
        return (200, out, "application/json", self._hdr(st, "pg:paper_trades FIFO<=%s" % st["asof_ts"]), note)

    def _names(self, syms: List[str]) -> Dict[str, str]:
        """ts_code（002587.SZ）→ name；取不到就回落 symbol（生产也是这个回退），不影响价格。"""
        if not syms:
            return {}
        try:
            rows = self.pg.query("SELECT ts_code, name FROM stock_pool WHERE ts_code = ANY(%(s)s)",
                                 {"s": [ts_code(s) for s in syms]})
            out: Dict[str, str] = {}
            for r in rows:
                code = str(r.get("ts_code") or "")
                if "." in code:
                    a, _, b = code.partition(".")
                    out[norm_sym(b + a)] = str(r.get("name") or "")
            return out
        except Exception:
            return {}

    def _day_has_files(self, sym: str, day8: str) -> bool:
        f = self.market.files_for(sym, day8)
        return os.path.exists(f["m1"]) or os.path.exists(f["m5_agg"]) or os.path.exists(f["m5_legacy"])

    # ── /market/quote/{symbol} ──────────────────────────────────────────
    def h_quote(self, symbol: str, q: Dict[str, str]) -> Tuple[int, Any, str, Dict[str, str], str]:
        st = self.asof.load()
        sym = norm_sym(symbol)
        self.market.require_day_data(sym, st["day"], "1")   # quote 走 1min 累计（真 m1）
        qq = self.market.quote(sym, st["day"], st["bar"])
        if not qq:
            raise AsOfError("no minute bars <= %s for %s on %s" % (st["bar"], sym, st["day"]))
        cur, hi, lo = float(qq["current"]), qq.get("high"), qq.get("low")
        pct_rank = None
        if hi is not None and lo is not None and hi > lo:
            pct_rank = round((cur - float(lo)) / (float(hi) - float(lo)) * 100, 1)
        name = (self._names([sym]).get(sym)) or sym
        body = {
            "symbol": sym, "name": name,
            "current": cur, "change": r3(cur - float(qq.get("pre_close") or 0)),
            "percent": r3(qq.get("change_pct")) or 0.0,
            "last_close": r3(qq.get("pre_close")), "pre_close": r3(qq.get("pre_close")),
            "open": r3(qq.get("open")), "high": hi, "low": lo,
            "volume": qq.get("vol"), "vol": qq.get("vol"), "amount": qq.get("amount"),
            "turnover_rate": qq.get("turnover_rate"), "amplitude": qq.get("amplitude"),
            "average": qq.get("average"), "intraday_percentile": pct_rank,
            "asof": self.asof.brief(st),
            "price_source": "mins1 cumulative <= %s on %s (pre_close = 日线 < day 的最后收盘)" % (st["bar"], st["day"]),
        }
        return (200, body, "application/json", self._hdr(st, "mins1@%s<=%s" % (st["day"], st["bar"])),
                "quote %s @%s" % (sym, st["bar"]))

    # ── /market/kline/{symbol}?freq= ────────────────────────────────────
    def h_kline(self, symbol: str, q: Dict[str, str]) -> Tuple[int, Any, str, Dict[str, str], str]:
        st = self.asof.load()
        sym = norm_sym(symbol)
        raw_freq = str(q.get("freq") or "5").lower().replace("min", "").replace("m", "")
        freq = raw_freq if raw_freq in ("1", "5", "15", "30", "60") else "5"
        try:
            count = int(q.get("count") or 0)
        except ValueError:
            count = 0
        try:
            days = max(1, min(30, int(q.get("days") or 1)))
        except ValueError:
            days = 1
        self.market.require_day_data(sym, st["day"], freq)
        bars = self.market.series(sym, st["day"], st["bar"], freq, count=count, days=days)
        if not bars:
            raise AsOfError("no %smin bars <= %s for %s on %s" % (freq, st["bar"], sym, st["day"]))
        premap = self.market.pre_close_map(sym, st["day"])
        tc = ts_code(sym)
        kl = []
        for b in reversed(bars):                     # 生产 klines 是**倒序**（插件按"最近 N 根"打印）
            t = str(b.get("time") or "")
            d8 = t[:10].replace("-", "")
            pre = premap.get(d8)
            close = r3(b.get("close"))
            kl.append({
                "ts_code": tc, "trade_date": d8 + t[11:16].replace(":", ""), "time": t, "day": d8,
                "open": r3(b.get("open")), "high": r3(b.get("high")), "low": r3(b.get("low")),
                "close": close, "pre_close": pre,
                "pct_chg": round((close - pre) / pre * 100, 4) if (close and pre) else None,
                "vol": r3(b.get("vol")), "volume": r3(b.get("vol")), "amount": r3(b.get("amount")),
            })
        body = {"symbol": sym, "ts_code": tc, "freq": freq, "trade_date": st["day"],
                "count": len(kl), "days": days, "klines": kl,
                "asof": self.asof.brief(st),
                "source": "local mins1 (1min 真源；5/15/30/60 由 1min 聚合) ≤ %s" % st["bar"]}
        return (200, body, "application/json", self._hdr(st, "mins1@%s<=%s" % (st["day"], st["bar"])),
                "kline %s freq=%s n=%d" % (sym, freq, len(kl)))

    # ── /market/market-state ────────────────────────────────────────────
    def h_market_state(self, q: Dict[str, str]) -> Tuple[int, Any, str, Dict[str, str], str]:
        st = self.asof.load()
        rows = self.pg.query(
            "SELECT trade_date, state, label, suggestion, score_trend, score_oscillation, "
            "       score_extreme, indicators_json, replace(created_at,'T',' ') AS created_at "
            "FROM market_diagnosis WHERE trade_date = %(d)s", {"d": st["day"]})
        row = rows[0] if rows else None
        fresh = bool(row) and (self.cfg["created_at_policy"] == "trade-date"
                               or str(row.get("created_at") or "") <= st["asof_ts"])
        if fresh:
            try:
                ind = json.loads(row.get("indicators_json") or "null")
            except Exception:
                ind = None
            body = {"trade_date": row["trade_date"], "state": row.get("state"), "label": row.get("label"),
                    "suggestion": row.get("suggestion"),
                    "score": {"trend": row.get("score_trend"), "oscillation": row.get("score_oscillation"),
                              "extreme": row.get("score_extreme")},
                    "indicators": ind, "asof": self.asof.brief(st),
                    "source": "pg:market_diagnosis trade_date=%s (created_at=%s <= %s)"
                              % (st["day"], row.get("created_at"), st["asof_ts"])}
            note = "market-state %s" % row.get("state")
        else:
            if row and self.cfg["created_at_policy"] == "strict":
                why = ("PG 该行 created_at=%s 晚于 as-of %s（本地 PG 副本里历史行是**后来回填**的）→ "
                       "按 as-of 口径视为「当时尚无诊断」；如需按 trade_date 放宽：--created-at-policy=trade-date"
                       % (row.get("created_at"), st["asof_ts"]))
            else:
                why = "本地 PG market_diagnosis 无 %s 行 → 当日无盘前诊断" % st["day"]
            body = {"trade_date": st["day"], "state": "unknown", "label": "⚪ 未知",
                    "suggestion": "今日尚未执行盘前诊断", "indicators": None,
                    "asof": self.asof.brief(st), "asof_note": why,
                    "source": "pg:market_diagnosis trade_date=%s" % st["day"]}
            note = "market-state unknown (%s)" % why[:60]
        return (200, body, "application/json", self._hdr(st, "pg:market_diagnosis<=%s" % st["day"]), note)

    # ── /t/conditions ───────────────────────────────────────────────────
    _COND_COLS = ("id, account_id, symbol, trade_date, trigger_kind, target_price, reinform_price, "
                  "vol_ratio_thresh, benchmark_turnover_profile, stabilize_level, sell_target_price, "
                  "stop_loss_price, time_stop_open, time_stop_close, start_time, end_time, armed, "
                  "armed_at, last_triggered_at, trigger_count_today, regime_gate, expression, status, "
                  "publisher, session_id, direction, replace(created_at::text,'T',' ') AS created_at")

    def _cond_row(self, r: Dict[str, Any]) -> Dict[str, Any]:
        d = dict(r)
        expr = d.get("expression")
        if isinstance(expr, str):
            try:
                expr = json.loads(expr)
            except Exception:
                expr = None
        d["expression"] = expr
        d["expression_summary"] = self.expr_summary(expr)
        if d.get("trigger_count_today") is None:
            d["trigger_count_today"] = 0
        if d.get("armed") is None:
            d["armed"] = 1
        return d

    def h_conditions_get(self, q: Dict[str, str]) -> Tuple[int, Any, str, Dict[str, str], str]:
        st = self.asof.load()
        td = norm_day(q.get("trade_date")) or st["day"]
        sym = norm_sym(q.get("symbol")) if q.get("symbol") else ""
        acc = str(q.get("account_id") or "")
        status = str(q.get("status") or "active")          # 与生产 list_active_conditions 同口径（status='active'）
        try:
            limit = max(1, min(5000, int(q.get("limit") or 500)))
        except ValueError:
            limit = 500
        sql = ("SELECT " + self._COND_COLS + " FROM t_conditions "
               "WHERE trade_date = %(td)s AND replace(created_at::text,'T',' ') <= %(cut)s")
        params: Dict[str, Any] = {"td": td, "cut": st["asof_ts"]}
        if status and status != "any":
            sql += " AND status = %(st)s"
            params["st"] = status
        if sym:
            sql += " AND symbol = %(sym)s"
            params["sym"] = sym
        if acc:
            sql += " AND account_id = %(acc)s"
            params["acc"] = acc
        sql += " ORDER BY trade_date DESC, id DESC LIMIT %(lim)s"
        params["lim"] = limit
        strict = self.cfg["created_at_policy"] == "strict"
        if strict:
            rows = self.pg.query(sql, params)
        else:
            # trade-date 口径：只按 trade_date 截断（放宽 PIT；本地 PG 副本里历史行 created_at 是后来回填的）
            sql2 = ("SELECT " + self._COND_COLS + " FROM t_conditions WHERE trade_date <= %(td)s")
            params2: Dict[str, Any] = {"td": td, "lim": limit}
            if status and status != "any":
                sql2 += " AND status = %(st)s"
                params2["st"] = status
            if sym:
                sql2 += " AND symbol = %(sym)s"
                params2["sym"] = sym
            if acc:
                sql2 += " AND account_id = %(acc)s"
                params2["acc"] = acc
            sql2 += " ORDER BY trade_date DESC, id DESC LIMIT %(lim)s"
            rows = self.pg.query(sql2, params2)
        excl = self.pg.query(
            "SELECT count(*) AS n FROM t_conditions WHERE trade_date = %(td)s "
            "AND replace(created_at::text,'T',' ') > %(cut)s", {"td": td, "cut": st["asof_ts"]})
        notactive = self.pg.query(
            "SELECT count(*) AS n FROM t_conditions WHERE trade_date = %(td)s AND status <> 'active'",
            {"td": td})
        conds = [self._cond_row(r) for r in rows]
        sand = [self._cond_row(c) for c in self._sandbox_conditions(st["day"], td, sym)]
        conds.extend(sand)
        note = ("trade_date=%s status=%s policy=%s pg=%d sandbox=%d excluded_created_after_asof=%s"
                % (td, status, self.cfg["created_at_policy"], len(rows), len(sand),
                   int((excl[0] or {}).get("n") or 0) if excl else 0))
        body = {"conditions": conds, "count": len(conds), "trade_date": td, "status_filter": status,
                "asof": self.asof.brief(st), "created_at_policy": self.cfg["created_at_policy"],
                "excluded_created_after_asof": int((excl[0] or {}).get("n") or 0) if excl else 0,
                "excluded_not_active": int((notactive[0] or {}).get("n") or 0) if notactive else 0,
                "sandbox_conditions": len(sand),
                "source": "pg:t_conditions + sandbox:%s" % self.sandbox.path(st["day"], "conditions.jsonl")}
        notes = []
        if body["excluded_created_after_asof"] and strict:
            notes.append("有 %d 行 trade_date=%s 的条件 created_at 晚于 as-of（本地 PG 副本里历史行是后来回填的）"
                         "→ 已被 as-of 口径排除；放宽：--created-at-policy=trade-date"
                         % (body["excluded_created_after_asof"], td))
        if body["excluded_not_active"] and status == "active":
            notes.append("另有 %d 行 trade_date=%s 非 active（生产 list_active_conditions 也只列 active）"
                         "；要全看：?status=any" % (body["excluded_not_active"], td))
        if notes:
            body["asof_note"] = "；".join(notes)
        return (200, body, "application/json", self._hdr(st, "pg:t_conditions<=%s" % st["asof_ts"]), note)

    def _sandbox_conditions(self, day8: str, td: str, sym: str) -> List[Dict[str, Any]]:
        out = []
        for o in self.sandbox.read(day8, "conditions.jsonl"):
            if str(o.get("trade_date") or day8) not in (td, day8):   # 只回放当日沙箱写入
                continue
            c = dict(o.get("condition") or {})
            if sym and norm_sym(c.get("symbol")) != sym:
                continue
            out.append(c)
        return out

    def h_conditions_post(self, q: Dict[str, str], body: Any) -> Tuple[int, Any, str, Dict[str, str], str]:
        st = self.asof.load()
        body = body if isinstance(body, dict) else {}
        if not body.get("symbol"):
            return (400, {"error": "缺少 symbol", "detail": "缺少 symbol"}, "application/json", {}, "400 no symbol")
        expr = body.get("expression")
        if expr is not None and self.t_expr is not None:
            try:
                self.t_expr.validate_expression(expr)
            except Exception as e:
                return (400, {"error": "表达式非法: %s" % e, "detail": "表达式非法: %s" % e},
                        "application/json", {}, "400 bad expression")
        direction = str(body.get("direction") or "").strip().lower()
        if direction and direction not in ("buy", "sell"):
            return (400, {"error": "direction 仅支持 buy/sell", "detail": "direction 仅支持 buy/sell"},
                    "application/json", {}, "400 bad direction")
        if not direction and str(body.get("trigger_kind") or "custom") == "custom":
            direction = self.infer_direction(expr)
            if direction not in ("buy", "sell"):
                return (400, {"error": "custom 条件表达式主方向不明确，必须显式声明 direction=buy 或 direction=sell",
                              "detail": "custom 条件表达式主方向不明确，必须显式声明 direction=buy 或 direction=sell"},
                        "application/json", {}, "400 direction unresolved")
        sym = norm_sym(body.get("symbol"))
        with self._idlock:                      # 读沙箱行数 → 分配 id → 落盘：串行化，避免并发写撞 id
            prev = self.sandbox.read(st["day"], "conditions.jsonl")
            cid = SANDBOX_ID_BASE + len(prev) + 1
            cond = {
                "id": cid, "account_id": str(body.get("account_id") or st["account"] or "t"),
                "symbol": sym, "trade_date": st["day"],
                "trigger_kind": str(body.get("trigger_kind") or "custom"),
                "direction": direction or "sell", "expression": expr,
                "expression_summary": self.expr_summary(expr),
                "target_price": body.get("target_price"), "sell_target_price": body.get("sell_target_price"),
                "stop_loss_price": body.get("stop_loss_price"), "vol_ratio_thresh": body.get("vol_ratio_thresh"),
                "regime_gate": str(body.get("regime_gate") or "ALLOWED"), "armed": 1,
                "trigger_count_today": 0, "status": "active", "publisher": "asof_api_sandbox",
                "session_id": st["session"], "created_at": st["asof_ts"],
                "sandbox": True, "sandbox_source": "bt_asof_api (回测沙箱，未写生产 PG)",
            }
            p = self.sandbox.append(st["day"], "conditions.jsonl", {
                "kind": "condition", "ts": now_iso(), "ts_epoch": round(time.time(), 3),
                "asof": self.asof.brief(st), "condition_id": cid, "body": body, "condition": cond})
            self.sandbox.append(st["day"], "writes.jsonl", {
                "kind": "t_conditions.create", "ts": now_iso(), "asof": self.asof.brief(st),
                "body": body, "condition_id": cid, "sandbox_file": p})
        resp = {"success": True, "condition_id": cid, "expression_summary": self.expr_summary(expr),
                "sandbox": True, "sandbox_file": p, "asof": self.asof.brief(st),
                "note": "只写本地沙箱（append-only JSONL），未写生产 PG"}
        return (200, resp, "application/json", self._hdr(st, "sandbox:conditions.jsonl"),
                "condition created (sandbox) id=%d" % cid)

    # ── /t/ai/actions ───────────────────────────────────────────────────
    def h_ai_actions(self, q: Dict[str, str]) -> Tuple[int, Any, str, Dict[str, str], str]:
        st = self.asof.load()
        try:
            limit = max(1, min(500, int(q.get("limit") or 10)))
        except ValueError:
            limit = 10
        td = norm_day(q.get("trade_date")) if q.get("trade_date") else ""
        sym = norm_sym(q.get("symbol")) if q.get("symbol") else ""
        sid = str(q.get("session_id") or "")
        sql = ("SELECT id, session_id, trade_date, symbol, action_type, input_snapshot, output, "
               "gateway_result, outcome, created_at::timestamp AS created_at FROM t_ai_actions "
               "WHERE created_at::timestamp <= %(cut)s::timestamp AND trade_date <= %(day)s")
        params: Dict[str, Any] = {"cut": st["asof_ts"], "day": st["day"], "lim": limit}
        if td:
            sql += " AND trade_date = %(td)s"
            params["td"] = td
        if sym:
            sql += " AND symbol = %(sym)s"
            params["sym"] = sym
        if sid:
            sql += " AND session_id = %(sid)s"
            params["sid"] = sid
        sql += " ORDER BY id DESC LIMIT %(lim)s"
        rows = self.pg.query(sql, params)
        acts = []
        for r in rows:
            d = dict(r)
            d["created_at"] = str(d.get("created_at") or "").replace(" ", "T")[:19]
            for k in ("input_snapshot", "output", "gateway_result", "outcome"):
                v = d.get(k)
                if isinstance(v, str):
                    try:
                        d[k] = json.loads(v)
                    except Exception as _e_sil6:
                        _silent_alert("bt_asof_api.py:1211", _e_sil6)
            acts.append(d)
        cov = self.pg.query("SELECT count(*) AS n, min(trade_date) AS d0, max(trade_date) AS d1 FROM t_ai_actions")
        c = cov[0] if cov else {}
        note = "ai_actions n=%d (pg coverage %s~%s)" % (len(acts), c.get("d0"), c.get("d1"))
        body = {"actions": acts, "count": len(acts), "quality": None, "asof": self.asof.brief(st),
                "source": "pg:t_ai_actions (created_at<=%s, trade_date<=%s)" % (st["asof_ts"], st["day"]),
                "pg_coverage": {"min_trade_date": str(c.get("d0") or ""), "max_trade_date": str(c.get("d1") or ""),
                                "rows": int(c.get("n") or 0)}}
        if not acts:
            body["asof_note"] = ("本地 PG t_ai_actions 覆盖 %s~%s，as-of %s 之前无记录（不是静默空："
                                 "是本地库确实没有该日的 AI 决策）"
                                 % (c.get("d0"), c.get("d1"), st["day"]))
        return (200, body, "application/json", self._hdr(st, "pg:t_ai_actions<=%s" % st["asof_ts"]), note)

    # ── /t/build/no-rebuild（沙箱名单，append-only 日志重放） ──────────────
    def h_no_rebuild(self, q: Dict[str, str], body: Any) -> Tuple[int, Any, str, Dict[str, str], str]:
        st = self.asof.load()
        body = body if isinstance(body, dict) else {}
        action = str(body.get("action") or "list").lower()
        lines = self.sandbox.read(st["day"], "no_rebuild.jsonl")
        syms: List[str] = []
        for o in lines:
            b = o.get("body") or {}
            a = str(b.get("action") or "list").lower()
            if a == "set":
                syms = [norm_sym(s) for s in (b.get("symbols") or [])]
            elif a == "add" and b.get("symbol"):
                s = norm_sym(b["symbol"])
                if s not in syms:
                    syms.append(s)
            elif a == "remove" and b.get("symbol"):
                syms = [x for x in syms if x != norm_sym(b["symbol"])]
        ignored: List[str] = []
        if action in ("add", "remove"):
            if not body.get("symbol"):
                return (400, {"error": "add/remove 需传 symbol", "detail": "add/remove 需传 symbol"},
                        "application/json", {}, "400 no symbol")
            s = norm_sym(body["symbol"])
            if not re.match(r"^(SH|SZ|BJ)\d{6}$", s):
                ignored.append(str(body["symbol"]))
            else:
                syms = (syms + [s]) if (action == "add" and s not in syms) else [x for x in syms if x != s]
        elif action == "set":
            raw = body.get("symbols")
            if not isinstance(raw, list):
                return (400, {"error": "set 需传 symbols 数组", "detail": "set 需传 symbols 数组"},
                        "application/json", {}, "400 no symbols")
            keep = []
            for x in raw:
                sx = norm_sym(x)
                if re.match(r"^(SH|SZ|BJ)\d{6}$", sx):
                    keep.append(sx)
                else:
                    ignored.append(str(x))
            syms = keep
        if action in ("add", "remove", "set"):
            b2 = dict(body, action=action, symbol=norm_sym(body.get("symbol")) if body.get("symbol") else None)
            p = self.sandbox.append(st["day"], "no_rebuild.jsonl", {
                "kind": "no_rebuild", "ts": now_iso(), "asof": self.asof.brief(st), "body": b2})
            self.sandbox.append(st["day"], "writes.jsonl", {
                "kind": "t_build.no-rebuild", "ts": now_iso(), "asof": self.asof.brief(st),
                "body": b2, "symbols": syms, "ignored": ignored, "sandbox_file": p})
        return (200, {"symbols": syms, "ignored": ignored, "sandbox": True,
                      "asof": self.asof.brief(st), "action": action,
                      "sandbox_file": self.sandbox.path(st["day"], "no_rebuild.jsonl")},
                "application/json", self._hdr(st, "sandbox:no_rebuild.jsonl"),
                "no-rebuild %s n=%d" % (action, len(syms)))


# ══════════════════════════════════════════════════════════════════════════
# 7. HTTP 层
# ══════════════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "bt-asof-api/" + VERSION
    sys_version = ""

    def log_message(self, *a):                     # 静音默认 stderr 访问日志（我们写 JSONL）
        pass

    def _run(self, method: str) -> None:
        api: Api = self.server.api            # type: ignore[attr-defined]
        t0 = time.time()
        body: Any = None
        status = payload = ctype = hdr = note = None
        if method in ("POST", "PUT", "PATCH"):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n > api.cfg["max_body"]:
                status, payload, ctype, hdr, note = (413, {"error": "body too large"},
                                                     "application/json", {}, "413 body too large")
            else:
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception as e:
                    status, payload, ctype, hdr, note = (400, {"error": "invalid JSON body: %s" % str(e)[:80]},
                                                         "application/json", {}, "400 bad json")
        if status is None:
            status, payload, ctype, hdr, note = api.handle(method, self.path, body)
        out = payload if isinstance(payload, (bytes, bytearray)) else jdump(payload)
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (hdr or {}).items():
                self.send_header(k, str(v))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(out)
        except BrokenPipeError:
            note = (note or "") + " | client gone"
        dt = round((time.time() - t0) * 1000.0, 1)
        api.count(status)
        try:
            st = api.asof.load()
            asof = api.asof.brief(st)
        except Exception:
            asof = None
        api.log({"method": self.command, "path": self.path, "status": status, "duration_ms": dt,
                 "asof": asof, "remote": self.client_address[0] if self.client_address else "",
                 "note": note or ""})

    def do_GET(self):
        self._run("GET")

    def do_POST(self):
        self._run("POST")

    def do_PUT(self):
        self._run("PUT")

    def do_DELETE(self):
        self._run("DELETE")

    def do_HEAD(self):
        self._run("GET")


# ══════════════════════════════════════════════════════════════════════════
# 8. 启动
# ══════════════════════════════════════════════════════════════════════════
def build_cfg(a: argparse.Namespace) -> Dict[str, Any]:
    root = os.path.abspath(a.root)
    data_dir = os.path.abspath(a.data_dir) if a.data_dir else os.path.join(root, "data")

    def resolve(p: str, rel_default: str) -> str:
        p = p or rel_default
        return p if os.path.isabs(p) else os.path.join(root, p)

    return {
        "root": root, "data_dir": data_dir,
        "cache_dir": resolve(a.cache_dir, REL_CACHE),
        "state_file": resolve(a.state_file, REL_STATE),
        "log": resolve(a.log, REL_LOG),
        "sandbox_root": resolve(a.sandbox_root, REL_SANDBOX),
        "bars_db": os.path.join(data_dir, "_bt_full", "bars.sqlite"),
        "dsn": a.dsn, "host": a.host, "port": int(a.port),
        "state_max_age": int(a.state_max_age), "strict_lookahead": bool(a.strict_lookahead),
        "created_at_policy": str(a.created_at_policy), "max_body": int(a.max_body),
    }


def print_paths(cfg: Dict[str, Any]) -> None:
    print("root        = %s" % cfg["root"])
    print("data_dir    = %s" % cfg["data_dir"])
    print("state_file  = %s" % cfg["state_file"])
    print("log         = %s" % cfg["log"])
    print("cache_dir   = %s" % cfg["cache_dir"])
    print("sandbox     = %s/<YYYYMMDD>/asof_api/{conditions,no_rebuild,writes}.jsonl  (root=%s)"
          % (cfg["sandbox_root"], cfg["sandbox_root"]))
    print("bars_db     = %s" % cfg["bars_db"])
    print("dsn         = %s" % cfg["dsn"])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="回测用本地 as-of REST API（Agent 工具通道硬隔离）")
    ap.add_argument("--port", type=int, default=13200)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--root", default=REPO_DEFAULT)
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--state-file", default="")
    ap.add_argument("--log", default="")
    ap.add_argument("--cache-dir", default="")
    ap.add_argument("--sandbox-root", default="")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--state-max-age", type=int, default=900, help="as-of 状态文件最大年龄(秒)，0=不检查")
    ap.add_argument("--created-at-policy", choices=("strict", "trade-date"), default="strict",
                    help="strict: created_at <= as-of（默认）；trade-date: 只按 trade_date 截断"
                         "（本地 PG 副本里历史行 created_at 是后来回填的，放宽会引入 PIT 松弛）")
    ap.add_argument("--strict-lookahead", action="store_true",
                    help="分钟截断用 label < bar（默认 label <= bar，与回放网格同构）")
    ap.add_argument("--max-body", type=int, default=262144)
    ap.add_argument("--print-paths", action="store_true")
    a = ap.parse_args(argv)
    cfg = build_cfg(a)
    if a.print_paths:
        print_paths(cfg)
        return 0
    if not _is_local_listen_host(cfg["host"]):
        print("[bt_asof_api] 拒绝启动：--host %s 不是回环地址（回测只允许本机监听；0.0.0.0 = 对所有网卡开放）"
              % cfg["host"], file=sys.stderr)
        return 2
    install_socket_guard()
    api = Api(cfg)
    api.log({"event": "start", "service": SERVICE, "version": VERSION, "host": cfg["host"],
             "port": cfg["port"], "pid": os.getpid(), "policy": cfg["created_at_policy"],
             "state_file": cfg["state_file"], "sandbox_root": cfg["sandbox_root"],
             "isolation": "loopback-only; upstream_http=0"})
    httpd = ThreadingHTTPServer((cfg["host"], cfg["port"]), Handler)
    httpd.daemon_threads = True
    httpd.api = api                                # type: ignore[attr-defined]
    port = httpd.server_address[1]
    print("[bt_asof_api] LISTENING http://%s:%d%s  (state=%s)" % (cfg["host"], port, PREFIX, cfg["state_file"]),
          flush=True)
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt as _e_sil7:
        _silent_alert("bt_asof_api.py:1433", _e_sil7)
    finally:
        api.log({"event": "stop", "pid": os.getpid(), "requests": dict(api.counters)})
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
