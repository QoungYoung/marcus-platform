#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/bt_dashboard.py — 生产代码回测的**只读**监控看板服务。

用途
----
本机正在跑「逐日重放生产交易链」的年跑（`jobs/bt_days.py` 驱动、每天一个
`jobs/bt_prod_run.py` 子进程，成交写进本地 PostgreSQL）。本服务把三处真实数据源
拼成一个看板所需的快照：

  1. 本地 PG `paper_account_info / paper_trades / paper_positions / t_triggers / t_conditions`
     （回测账户 `--account`，默认 `stock`），以及 `stock_pool(ts_code, symbol, name)` 提供**标的名称**
     （按 6 位代码映射；缺失给 null，绝不编造）；
  2. 逐日产物 `data/_bt_year/_summary/prod_<YYYYMMDD>.json`（**注意：里面的 triggers/trades
     是当天收盘时的全量快照**，本服务按 `id` 去重、并按「id 首次出现的那一天」归属到交易日）；
  3. 日线 `data/_bt_full/bars.sqlite`、分钟文件存在性 `data/_bt_full/mins/`、
     波浪层 `_summary/wave_backfill.json`、父进程日志 `.dsh-tmp/wolfbt/logs/year_prod.log`、
     以及 **`/proc/*/cmdline` 扫描**（本机没有 ps/pkill）判断跑批进程存活。

硬性约束
--------
* 只用标准库 + psycopg2（仓库 .venv 已装）；
* **只读**：不写任何库、不写任何文件；PG 会话显式设为 `readonly=True`；sqlite 以
  `mode=ro` 打开；所有聚合都在内存里做；
* 不执行任何跑批命令，页面只提供「复制监控命令」；
* 唯一对外发送的**第三方静态文件**是 `GET /vendor/gsap.min.js`：直接从
  `frontend/node_modules/gsap/dist/gsap.min.js` 只读转发（不复制、不改写 frontend/），
  文件不存在时返回 404 → 页面自动降级为无动效且完全可用；
* 缺失的字段一律给 `null`，绝不编造数值。

启动
----
    .venv/bin/python jobs/bt_dashboard.py            # http://127.0.0.1:8799
    .venv/bin/python jobs/bt_dashboard.py --port 8901 --root data/_bt_year
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import signal
import sqlite3
import statistics
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlparse, parse_qs

try:  # 仓库 .venv 已装 psycopg2；缺失时服务仍可启动，只是账户/成交/持仓为空并给出告警
    import psycopg2
    import psycopg2.extras
except Exception:  # pragma: no cover
    psycopg2 = None

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

DEFAULT_PG = "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
# 本机已装的 GSAP（离线自托管；只读转发，绝不改动 frontend/）
GSAP_PATH = os.path.join(REPO, "frontend", "node_modules", "gsap", "dist", "gsap.min.js")
BUY_FEE = 1.0 + 0.000396   # 买入：佣金 0.000086 + 过户 0.00001 + 其他 0.0003（见 backend/app/core/trading/backtest_paper.py）
SELL_FEE = 1.0 - 0.000896  # 卖出：佣金 0.000086 + 印花税 0.0005 + 过户 0.00001 + 其他 0.0003

DAY_RE = re.compile(r"^\d{8}$")
SYMBOL_RE = re.compile(r"^(SH|SZ|BJ)\d{6}$")

# 被拦原因归类（关键字来自 t_triggers.reason / prod_*.json 的真实文案；按此顺序判定）
BLOCK_RULES = (
    ("自动执行量推导为 0", "自动执行量推导为 0"),
    ("[G6]", "[G6] 笔数/额度上限"),
    ("决策对象准入拒绝", "决策对象准入拒绝"),
    ("破位禁低吸", "破位禁低吸"),
    ("趋势约束阻止卖出", "趋势约束阻止卖出"),
    ("资金不足", "资金不足"),
)


# ──────────────────────────────────────────────────────────────────────────────
# 小工具
# ──────────────────────────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def epoch_iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def safe_read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except Exception:
        return None


def parse_kind(reason: str | None) -> str | None:
    """`reason` → 腿型。例：`条件命中自动执行（custom_prevlow）` → `custom_prevlow`。"""
    if not reason:
        return None
    r = reason.strip()
    m = re.search(r"[（(]([A-Za-z0-9_]+)[）)]", r)
    if m:
        return m.group(1)
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", r)
    if m:
        inner = m.group(2)
        m2 = re.search(r"\b([a-z][a-z0-9_]{3,})\b", inner)
        return ("[%s] %s" % (m.group(1), m2.group(1))) if m2 else "[%s]" % m.group(1)
    m = re.match(r"^([^：:]{2,24})[：:]", r)
    if m:
        return m.group(1)
    return r[:24] if r else None


def classify_blocked(reason: str | None) -> str:
    r = reason or ""
    for needle, label in BLOCK_RULES:
        if needle in r:
            return label
    return "其他"


def to_ts_code(symbol: str) -> str:
    """`SH600977` → `600977.SH`（bars.sqlite 的 ts_code 形态）。"""
    if not symbol or len(symbol) < 8:
        return symbol or ""
    return "%s.%s" % (symbol[2:], symbol[:2])


def to_symbol(ts_code: str) -> str:
    if not ts_code or "." not in ts_code:
        return ts_code or ""
    code, mkt = ts_code.split(".", 1)
    return "%s%s" % (mkt.upper(), code)


def iso_day(day: str | None) -> str | None:
    """`20260302` → `2026-03-02`；已带分隔符的原样返回。"""
    if not day:
        return None
    d = day.replace("-", "").strip()
    if len(d) == 8 and d.isdigit():
        return "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
    return day


def compact_day(day: str | None) -> str | None:
    if not day:
        return None
    d = day.replace("-", "").strip()
    return d if len(d) == 8 and d.isdigit() else None


def scan_procs() -> list[dict]:
    """扫 /proc/*/cmdline 找跑批进程（本机没有 ps/pkill）。

    ⚠️ 只认「python 解释器 + 脚本参数」这一种形态：编排脚本常用
    `bash -c "python jobs/bt_x.py; python jobs/bt_days.py ..."` 把多个命令串起来，
    若按整条命令串做子串匹配，就会把**只是提到** bt_days.py 的 shell 包装进程误判成"跑批存活"。
    """
    out: list[dict] = []
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if not raw:
            continue
        parts = [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]
        if len(parts) < 2:
            continue
        exe = os.path.basename(parts[0])
        if "python" not in exe:          # 排除 bash -c "…"、nohup 包装等
            continue
        kind = None
        for arg in parts[1:4]:           # 解释器后面的脚本参数
            base = os.path.basename(arg)
            if base == "bt_days.py":
                kind = "bt_days"
                break
            if base == "bt_prod_run.py":
                kind = "bt_prod_run"
                break
            if base == "bt_dashboard.py":
                kind = "bt_dashboard"
                break
        if not kind:
            continue
        line = " ".join(parts)
        m = re.search(r"--day\s+(\d{8})", line)
        try:
            started = os.path.getmtime("/proc/%s" % pid)
        except OSError:
            started = None
        out.append(
            {
                "pid": int(pid),
                "kind": kind,
                "day": m.group(1) if m else None,
                "started_at": epoch_iso(started),
                "started_epoch": started,
                "cmdline": line[:400],
            }
        )
    out.sort(key=lambda r: (r["kind"], r["pid"]))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Store：把磁盘/PG 的只读视图聚合成快照
# ──────────────────────────────────────────────────────────────────────────────
class Store:
    def __init__(self, args: argparse.Namespace):
        self.root = os.path.abspath(args.root)
        self.bars_db = os.path.abspath(args.bars)
        self.mins_dir = os.path.abspath(args.mins)
        self.log_path = os.path.abspath(args.log)
        self.pg_url = args.pg
        self.account = args.account
        self.ttl = max(0.5, float(args.ttl))

        self._lock = threading.RLock()
        self._sig = None
        self._files: dict[str, dict] = {}
        self._days: list[str] = []
        self._day_meta: dict[str, dict] = {}
        self._trig_by_id: dict[int, dict] = {}
        self._trig_by_day: dict[str, list[int]] = {}
        self._theme_pairs: dict[str, list[tuple[str, str]]] = {}
        self._active_sig = None
        self._active = None
        self._warn_theme_unmapped = 0
        self._last_rebuild = 0.0

        self._cal = None            # 交易日历（bars.sqlite 的 distinct trade_date）
        self._cal_sig = None
        self._log = {}              # 日志解析结果（按 mtime 缓存）
        self._log_sig = None
        self._wave = None
        self._wave_sig = None
        self._pg = {}               # PG 查询结果（按 TTL 缓存）
        self._pg_at = 0.0
        self._pg_err = None
        self._names: dict[str, str] = {}   # 6 位代码 → 名称（来自 PG stock_pool）
        self._bars_cache: dict[str, list[tuple[str, float]]] = {}
        self._bars_at = 0.0
        self._mins_stats = None
        self._mins_key = None
        self._mins_sig = None

    # ── 逐日产物：增量解析 ────────────────────────────────────────────────
    def _list_prod_files(self) -> list[tuple[str, str, float, int]]:
        out = []
        pat = os.path.join(self.root, "_summary", "prod_*.json")
        for path in glob.glob(pat):
            base = os.path.basename(path)
            m = re.match(r"prod_(\d{8})\.json$", base)
            if not m:
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            out.append((m.group(1), path, st.st_mtime, st.st_size))
        out.sort(key=lambda r: r[0])
        return out

    @staticmethod
    def _extract(data: dict) -> dict:
        """从一个 prod_<day>.json 里取看板需要的字段（原始文件很大，只留必要部分）。"""
        acc = data.get("account") or []
        return {
            "day": data.get("day"),
            "cut": data.get("cut"),
            "symbols": data.get("symbols") or [],
            "armed": data.get("armed") or [],
            "triggers": data.get("triggers") or [],
            "trades": data.get("trades") or [],
            "step_secs": {k: v for k, v in (data.get("step_secs") or {}).items()
                          if isinstance(v, (int, float))},
            "decision": data.get("decision") or {},
            "frozen_cash_end": data.get("frozen_cash_end"),
            "account": (acc[0] if acc else {}),
            "market_missing": data.get("market_missing") or [],
            "net_hits": data.get("net_hits") or [],
            "bars": data.get("bars") or [],
        }

    def refresh(self) -> None:
        with self._lock:
            entries = self._list_prod_files()
            sig = tuple((d, p, mt, sz) for (d, p, mt, sz) in entries)
            if sig == self._sig:
                return
            for day, path, mt, sz in entries:
                cached = self._files.get(day)
                if cached and cached.get("_mtime") == mt and cached.get("_size") == sz:
                    continue
                data = safe_read_json(path)
                if data is None:
                    continue
                rec = self._extract(data)
                rec["_mtime"] = mt
                rec["_size"] = sz
                rec["_path"] = path
                self._files[day] = rec
            for day in list(self._files):
                if day not in {d for d, _, _, _ in entries}:
                    self._files.pop(day, None)
            self._rebuild()
            self._sig = sig

    def _rebuild(self) -> None:
        """按交易日升序重放逐日产物，把「全量快照」去重成「每日增量」。"""
        days = sorted(self._files)
        trig_by_id: dict[int, dict] = {}
        trig_by_day: dict[str, list[int]] = {}
        day_meta: dict[str, dict] = {}
        seen: set[int] = set()
        for day in days:
            rec = self._files[day]
            new_ids: list[int] = []
            for t in rec["triggers"]:
                tid = t.get("id")
                if tid is None or tid in seen:
                    continue
                seen.add(tid)
                trig_by_id[tid] = {
                    "id": tid,
                    "symbol": t.get("symbol"),
                    "event_type": t.get("event_type"),
                    "status": t.get("status"),
                    "price": t.get("quote_price") if t.get("quote_price") is not None else t.get("trigger_price"),
                    "trigger_price": t.get("trigger_price"),
                    "quote_price": t.get("quote_price"),
                    "reason": t.get("reason") or "",
                    "day": iso_day(day),
                    "day_compact": day,
                    "block": classify_blocked(t.get("reason")) if t.get("status") in ("blocked", "cancelled") else None,
                }
                new_ids.append(tid)
            trig_by_day[day] = new_ids
            step = rec["step_secs"]
            trades_new = len(rec["trades"])
            day_meta[day] = {
                "day": day,
                "date": iso_day(day),
                "cut": rec.get("cut"),
                "symbols": len(rec["symbols"]),
                "armed": len(rec["armed"]),
                "triggers_new": len(new_ids),
                "triggers_snapshot": len(rec["triggers"]),
                "trades_snapshot": trades_new,
                "step_total": round(sum(step.values()), 1),
                "step_secs": step,
                "decision": rec.get("decision"),
                "frozen_cash_end": rec.get("frozen_cash_end"),
                "account_cash_end": rec["account"].get("available_cash"),
                "market_missing": rec.get("market_missing") or [],
                "files_mtime": rec["_mtime"],
                "size": rec["_size"],
            }
        self._days = days
        self._trig_by_id = trig_by_id
        self._trig_by_day = trig_by_day
        self._day_meta = day_meta
        self._theme_pairs = self._load_theme_pairs(days)
        self._mins_stats = None
        self._last_rebuild = time.time()

    # ── 逐日 legs 文件 → symbol→主题（真实映射，来自 data/_bt_year/<day>/legs*.jsonl）──
    def _load_theme_pairs(self, days: list[str]) -> dict[str, list[tuple[str, str]]]:
        pairs: dict[str, list[tuple[str, str]]] = {}
        for day in days:
            ddir = os.path.join(self.root, day)
            for fn in ("legs.jsonl", "legs_switch.jsonl"):
                path = os.path.join(ddir, fn)
                if not os.path.exists(path):
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                            except Exception:
                                continue
                            sym, theme = rec.get("symbol"), rec.get("theme")
                            if sym and theme:
                                pairs.setdefault(sym, []).append((day, theme))
                except OSError:
                    continue
        for sym in pairs:
            pairs[sym].sort()
        return pairs

    def theme_for(self, symbol: str, day: str | None = None) -> str | None:
        seq = self._theme_pairs.get(symbol)
        if not seq:
            return None
        if day is None:
            return seq[-1][1]
        best = None
        for d, theme in seq:
            if d <= day:
                best = theme
            else:
                break
        return best if best is not None else None

    # ── 交易日历（bars.sqlite，只读）────────────────────────────────────────
    def _sqlite(self) -> sqlite3.Connection:
        conn = sqlite3.connect("file:%s?mode=ro" % quote(self.bars_db), uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def calendar(self) -> list[str]:
        with self._lock:
            try:
                st = os.stat(self.bars_db)
                sig = (st.st_mtime, st.st_size)
            except OSError:
                return self._cal or []
            if self._cal is not None and sig == self._cal_sig:
                return self._cal
            days: list[str] = []
            try:
                conn = self._sqlite()
                try:
                    cur = conn.execute("SELECT DISTINCT trade_date FROM bars ORDER BY trade_date")
                    days = [r[0] for r in cur.fetchall()]
                finally:
                    conn.close()
            except Exception:
                days = self._cal or []
            self._cal, self._cal_sig = days, sig
            return days

    def bars_for(self, symbol: str, start: str, end: str, limit: int = 400) -> list[dict]:
        ts = to_ts_code(symbol)
        try:
            conn = self._sqlite()
            try:
                cur = conn.execute(
                    "SELECT trade_date, open, high, low, close, pre_close, pct_chg, vol, amount "
                    "FROM bars WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
                    "ORDER BY trade_date LIMIT ?",
                    (ts, start, end, limit),
                )
                return [
                    {
                        "day": iso_day(r["trade_date"]),
                        "open": r["open"],
                        "high": r["high"],
                        "low": r["low"],
                        "close": r["close"],
                        "pre_close": r["pre_close"],
                        "pct_chg": r["pct_chg"],
                        "vol": r["vol"],
                        "amount": r["amount"],
                    }
                    for r in cur.fetchall()
                ]
            finally:
                conn.close()
        except Exception:
            return []

    def closes(self, symbols: list[str], start: str, end: str) -> dict[str, list[tuple[str, float]]]:
        """每个标的在 [start,end] 的收盘序列（升序），供净值重建做「最近 ≤ 当日」取值。"""
        out: dict[str, list[tuple[str, float]]] = {}
        try:
            conn = self._sqlite()
            try:
                for sym in symbols:
                    cur = conn.execute(
                        "SELECT trade_date, close FROM bars WHERE ts_code=? AND trade_date>=? "
                        "AND trade_date<=? ORDER BY trade_date",
                        (to_ts_code(sym), start, end),
                    )
                    out[sym] = [(r[0], r[1]) for r in cur.fetchall() if r[1] is not None]
            finally:
                conn.close()
        except Exception:
            pass
        return out

    # ── 父进程日志（心跳来源之二）──────────────────────────────────────────
    def log_info(self) -> dict:
        with self._lock:
            try:
                st = os.stat(self.log_path)
                sig = (st.st_mtime, st.st_size)
            except OSError:
                return {"exists": False, "path": self.log_path}
            if self._log_sig == sig and self._log:
                return self._log
            info = {"exists": True, "path": self.log_path, "mtime": st.st_mtime,
                    "mtime_iso": epoch_iso(st.st_mtime), "size": st.st_size}
            head_lines, tail_lines = [], []
            try:
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
                    head_lines = [next(fh, "") for _ in range(8)]
                    fh.seek(max(0, st.st_size - 8192))
                    tail_lines = fh.read().splitlines()
            except Exception:
                pass
            head = "".join(head_lines)
            m = re.search(r"(\d+)\s*个交易日[：:]\s*(\d{8})\s*[→\-]>?\s*(\d{8})", head)
            info["days_total"] = int(m.group(1)) if m else None
            info["run_start"] = m.group(2) if m else None
            info["run_end"] = m.group(3) if m else None
            started, completed = [], []
            try:
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        m2 = re.match(r"\[days\]\s+(\d{8})\s+cut=", line)
                        if m2:
                            completed.append(m2.group(1))
                            continue
                        m3 = re.match(r"\[days\]\s+(\d{8})\s", line)
                        if m3:
                            started.append(m3.group(1))
            except Exception:
                pass
            info["last_completed"] = completed[-1] if completed else None
            info["completed_count"] = len(completed)
            info["last_started"] = started[-1] if started else None
            info["tail"] = [ln for ln in tail_lines[-6:] if ln.strip()]
            self._log, self._log_sig = info, sig
            return info

    # ── 波浪层覆盖率 ──────────────────────────────────────────────────────
    def wave_stats(self) -> dict:
        path = os.path.join(self.root, "_summary", "wave_backfill.json")
        with self._lock:
            try:
                st = os.stat(path)
                sig = (st.st_mtime, st.st_size)
            except OSError:
                return {"ok": 0, "err": 0, "total": 0, "path": path, "exists": False}
            if self._wave is not None and sig == self._wave_sig:
                return self._wave
            data = safe_read_json(path) or {}
            ok = err = 0
            ok_days, err_days = [], []
            for day, rec in data.items():
                status = (rec or {}).get("status")
                if status == "ok":
                    ok += 1
                    ok_days.append(day)
                else:
                    err += 1
                    err_days.append(day)
            out = {"ok": ok, "err": err, "total": len(data), "path": path, "exists": True,
                   "err_days": sorted(err_days)[:12], "mtime_iso": epoch_iso(st.st_mtime)}
            self._wave, self._wave_sig = out, sig
            return out

    # ── 本地 PG（只读会话）────────────────────────────────────────────────
    def pg_fetch(self) -> dict:
        with self._lock:
            if time.time() - self._pg_at < self.ttl and (self._pg or self._pg_err):
                return {"account": self._pg.get("account"), "trades": self._pg.get("trades") or [],
                        "positions": self._pg.get("positions") or [], "error": self._pg_err}
            self._pg_at = time.time()
            if psycopg2 is None:
                self._pg_err = "psycopg2 不可用：请在仓库 .venv 下运行（.venv/bin/python jobs/bt_dashboard.py）"
                return {"account": None, "trades": [], "positions": [], "error": self._pg_err}
            acc = None
            trades: list[dict] = []
            positions: list[dict] = []
            names: dict[str, str] = {}
            err = None
            try:
                conn = psycopg2.connect(self.pg_url, connect_timeout=4)
                try:
                    conn.set_session(readonly=True, autocommit=True)
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                    cur.execute(
                        "SELECT account_id, initial_capital, available_cash, frozen_cash, order_counter,"
                        " updated_at FROM paper_account_info WHERE account_id=%s",
                        (self.account,),
                    )
                    row = cur.fetchone()
                    acc = dict(row) if row else None
                    cur.execute(
                        "SELECT id, orderid, symbol, direction, price, volume, amount, profit, trade_date,"
                        " created_at, reason FROM paper_trades"
                        " WHERE account_id=%s AND coalesce(voided,0)=0 ORDER BY id",
                        (self.account,),
                    )
                    for r in cur.fetchall():
                        trades.append(dict(r))
                    cur.execute(
                        "SELECT symbol, volume, avg_price, entry_date, highest_price, updated_at"
                        " FROM paper_positions WHERE account_id=%s AND volume>0 ORDER BY symbol",
                        (self.account,),
                    )
                    for r in cur.fetchall():
                        positions.append(dict(r))
                    # 标的名称：按 6 位代码建表（ts_code 形如 002587.SZ / 920139.BJ）
                    cur.execute("SELECT ts_code, symbol, name FROM stock_pool ORDER BY ts_code")
                    for r in cur.fetchall():
                        code = (r.get("ts_code") or "").split(".")[0].strip()
                        if not (len(code) == 6 and code.isdigit()):
                            code = (r.get("symbol") or "").strip()
                            code = code[-6:] if len(code) >= 6 else code
                        if len(code) == 6 and code.isdigit() and r.get("name") and code not in names:
                            names[code] = r["name"]
                finally:
                    conn.close()
            except Exception as exc:
                err = "%s: %s" % (type(exc).__name__, str(exc)[:220])
            self._pg = {"account": acc, "trades": trades, "positions": positions}
            if names or not err:
                self._names = names          # PG 失败时保留上一次的表，避免名称整片变 null
            self._pg_err = err
            return {"account": acc, "trades": trades, "positions": positions, "error": err,
                    "names": len(names)}

    # ── 本次运行 vs 上一次遗留（跑批被重启时 _summary 里的旧 prod_*.json 会留下来）──
    def active_view(self) -> dict:
        """只用**当前这次跑批**产出的 prod_*.json 参与统计。

        背景（实测踩到）：年跑被重启且 `--prod-reset-first` 重置了 PG，但 `_summary/` 里
        上一次跑到 20260317 的旧产物还在。若不加区分，触发归属/覆盖率会把两次运行混在一起。

        判定规则（按可信度排序，两条都写进 coverage 供人核对）：
          A. 有活的 bt_days / bt_prod_run 进程 → 进程启动时刻之后写出的产物才算本次运行
             （文件 mtime ≥ 启动时刻 − 5s）；更早的判为遗留。
          B. 没有进程时退化为：若文件数明显多于日志里 `cut=` 行数（> +3），
             则以日志的 last_completed 为界，只保留 ≤ 它的产物。
        """
        procs = scan_procs()
        live = [p for p in procs if p["kind"] in ("bt_days", "bt_prod_run")]
        starts = [p.get("started_epoch") for p in live if p.get("started_epoch")]
        run_start = min(starts) if starts else None
        log = self.log_info()
        completed_count = log.get("completed_count") or 0
        last_completed = log.get("last_completed")
        sig = (tuple(self._days), run_start, completed_count, last_completed)
        if self._active is not None and self._active_sig == sig:
            return self._active

        stale: list[str] = []
        rule = "none"
        if run_start:
            rule = "proc-start"
            stale = [d for d in self._days if (self._files[d]["_mtime"] or 0) < run_start - 5.0]
        elif self._days and completed_count and len(self._days) > completed_count + 3 and last_completed:
            rule = "log-last-completed"
            stale = [d for d in self._days if d > last_completed]

        stale_set = set(stale)
        days = [d for d in self._days if d not in stale_set]
        day_set = set(days)
        trig_by_id = {i: t for i, t in self._trig_by_id.items() if t["day_compact"] in day_set}
        trig_by_day = {d: [i for i in ids if i in trig_by_id] for d, ids in self._trig_by_day.items() if d in day_set}
        day_meta = {d: m for d, m in self._day_meta.items() if d in day_set}
        out = {"days": days, "day_set": day_set, "stale": stale, "rule": rule,
               "run_start_epoch": run_start, "run_start_iso": epoch_iso(run_start),
               "trig_by_id": trig_by_id, "trig_by_day": trig_by_day, "day_meta": day_meta,
               "procs": procs, "live": live}
        self._active, self._active_sig = out, sig
        return out

    def name_for(self, symbol: str | None) -> str | None:
        """`SZ002587` → `奥拓电子`（来自 PG stock_pool；查不到返回 None，页面显示 —）。"""
        if not symbol:
            return None
        code = symbol[2:] if SYMBOL_RE.match(symbol) else symbol
        return self._names.get(code)

    # ── 分钟数据缺口（估算）───────────────────────────────────────────────
    def mins_stats(self, days: list[str] | None = None) -> dict:
        days = list(days if days is not None else self._days)
        key = tuple(days)
        with self._lock:
            if self._mins_stats is not None and self._mins_key == key:
                return self._mins_stats
            armed_total = missing = 0
            missing_samples: list[str] = []
            for day in days:
                rec = self._files.get(day) or {}
                for a in rec.get("armed") or []:
                    sym = a.get("symbol")
                    if not sym or len(sym) < 8:
                        continue
                    armed_total += 1
                    fn = "%s_%s_5min_%s.json" % (sym[2:], sym[:2], day)
                    if not os.path.exists(os.path.join(self.mins_dir, fn)):
                        missing += 1
                        if len(missing_samples) < 8:
                            missing_samples.append("%s/%s" % (day, sym))
            out = {
                "armed_total": armed_total,
                "missing": missing,
                "missing_pct": (round(100.0 * missing / armed_total, 2) if armed_total else None),
                "samples": missing_samples,
                "mins_dir": self.mins_dir,
                "estimated": True,
            }
            self._mins_stats, self._mins_key = out, key
            return out

    # ──────────────────────────────────────────────────────────────────────
    # 快照
    # ──────────────────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        self.refresh()
        warnings: list[dict] = []
        act = self.active_view()          # 只取本次运行产出的逐日产物（排除上一次遗留）
        procs = act["procs"]
        procs_live = act["live"]
        active_days = act["days"]
        day_meta = act["day_meta"]
        trig_by_id = act["trig_by_id"]
        trig_by_day = act["trig_by_day"]
        log = self.log_info()
        pg = self.pg_fetch()
        if act["stale"]:
            warnings.append({
                "level": "warn",
                "text": "检测到 %d 个上一次跑批遗留的逐日产物（判定规则 %s），已从触发归属/覆盖率/每日统计中排除：%s%s"
                        % (len(act["stale"]), act["rule"],
                           ", ".join(iso_day(d) for d in act["stale"][:6]),
                           " 等" if len(act["stale"]) > 6 else ""),
            })

        # ── 心跳 ────────────────────────────────────────────────────────────
        activity: list[tuple[str, float]] = []
        if log.get("mtime"):
            activity.append(("year_prod.log", log["mtime"]))
        for day in active_days[-3:]:
            mt = (self._files.get(day) or {}).get("_mtime")
            if mt:
                activity.append(("prod_%s.json" % day, mt))
        for pat, tag in ((os.path.join(self.root, "_summary", "prod_*.log"), "prod_<day>.log"),
                         (os.path.join(self.root, "_summary", "*.log"), "summary/*.log")):
            cands = glob.glob(pat)
            if cands:
                newest = max(cands, key=lambda p: os.path.getmtime(p))
                try:
                    activity.append((tag, os.path.getmtime(newest)))
                except OSError:
                    pass
        if activity:
            src, last_ts = max(activity, key=lambda r: r[1])
            last_activity = epoch_iso(last_ts)
            seconds_since = max(0.0, round(time.time() - last_ts, 1))
        else:
            src, last_activity, seconds_since = None, None, None

        cal = self.calendar()
        run_start = log.get("run_start")
        run_end = log.get("run_end")
        if not run_start and active_days:
            run_start = active_days[0]
        if not run_end and cal:
            run_end = cal[-1]
        window = [d for d in cal if run_start and run_end and run_start <= d <= run_end]
        days_total = log.get("days_total") or (len(window) or None)
        last_completed = log.get("last_completed") or (active_days[-1] if active_days else None)
        days_done = len([d for d in window if last_completed and d <= last_completed]) or None
        started_day = log.get("last_started")
        prod_proc_day = next((p["day"] for p in procs_live if p["kind"] == "bt_prod_run" and p["day"]), None)
        candidates = [d for d in (prod_proc_day, started_day) if d and last_completed and d > last_completed]
        current_day = max(candidates) if candidates else None

        pace = None
        mtimes = sorted((self._files[d]["_mtime"], d) for d in active_days if self._files.get(d))
        deltas = []
        for i in range(1, len(mtimes)):
            delta = mtimes[i][0] - mtimes[i - 1][0]
            if 5.0 <= delta <= 3600.0:  # 排除停机造成的大间隔
                deltas.append(delta)
        if deltas:
            pace = round(statistics.median(deltas[-30:]), 1)
        eta = None
        if pace and days_total and days_done:
            eta = int(round(pace * max(0, days_total - days_done)))

        fresh = bool(seconds_since is not None and seconds_since <= 60)
        heartbeat = {
            "now": now_iso(),
            "last_activity": last_activity,
            "seconds_since": seconds_since,
            # alive = 进程在 /proc 里，**或**活动时间戳在 60s 内（跑批驱动可能是短命子进程，
            # 只见心跳不见进程时不应误报死亡）；proc_found 给出硬证据。
            "alive": bool(procs_live) or fresh,
            "days_done": days_done,
            "days_total": days_total,
            "current_day": current_day,
            "pace_sec_per_day": pace,
            "eta_seconds": eta,
            # ── 以下为附加证据字段（页面展示用，非契约必需）──
            "proc_found": bool(procs_live),
            "heartbeat_fresh": fresh,
            "last_activity_source": src,
            "last_completed": last_completed,
            "procs": procs_live,
            "procs_all": procs,
            "log_tail": log.get("tail") or [],
            "run_window": [run_start, run_end],
            "generated_at": now_iso(),
        }

        if not self._days:
            warnings.append({"level": "info", "text": "未发现逐日产物 data/_bt_year/_summary/prod_*.json —— 跑批尚未产出任何一天"})
        elif not active_days:
            warnings.append({"level": "warn", "text": "所有 prod_*.json 都被判定为上一次跑批的遗留产物，本次运行尚无产出"})

        # ── 账户 / 成交 / 持仓（PG）─────────────────────────────────────────
        acc = pg["account"]
        if pg["error"]:
            warnings.append({"level": "error", "text": "本地 PG 读取失败：%s" % pg["error"]})
        elif not acc:
            warnings.append({"level": "warn", "text": "PG 中没有 account_id=%s 的账户行（paper_account_info）" % self.account})

        initial = float(acc["initial_capital"]) if acc and acc.get("initial_capital") is not None else None
        trades_pg = pg["trades"]

        # ── 净值重建（口径见页脚/覆盖率面板）───────────────────────────────
        symbols = sorted({t["symbol"] for t in trades_pg} | {p["symbol"] for p in pg["positions"]})
        curve_start = run_start or (active_days[0] if active_days else None)
        curve_end = last_completed or (active_days[-1] if active_days else None)
        # 曲线要覆盖「窗口内全部交易日」，并且必须含**进行中的那一天**：PG 里当天已有成交，
        # 若曲线停在昨天，重建现金与账户可用现金就不是同口径（差额会被当成误差）。
        pg_last_day = None
        for t in trades_pg:
            d = compact_day(t.get("trade_date"))
            if d and (pg_last_day is None or d > pg_last_day):
                pg_last_day = d
        if pg_last_day and (curve_end is None or pg_last_day > curve_end):
            curve_end = pg_last_day
        in_progress_day = last_completed if not curve_end else (curve_end if last_completed and curve_end > last_completed else None)
        curve: list[dict] = []
        account_block = {
            "initial": initial,
            "available_cash": float(acc["available_cash"]) if acc and acc.get("available_cash") is not None else None,
            "frozen_cash": float(acc["frozen_cash"]) if acc and acc.get("frozen_cash") is not None else None,
            "equity": None, "realized": None, "float": None, "ret_pct": None, "max_dd_pct": None,
        }
        daily: list[dict] = []
        recon_delta = None
        if initial is not None and curve_start and curve_end:
            cal_win = [d for d in window if curve_start <= d <= curve_end]
            closes = self.closes(symbols, curve_start, curve_end)
            ptr = {s: 0 for s in symbols}
            last_close: dict[str, float] = {}
            by_day: dict[str, list[dict]] = {}
            for t in trades_pg:
                d = compact_day(t.get("trade_date"))
                if d:
                    by_day.setdefault(d, []).append(t)
            cash = initial
            realized = 0.0
            vols: dict[str, int] = {}
            peak = initial
            prev_equity = initial
            for day in cal_win:
                for t in sorted(by_day.get(day, []), key=lambda r: r["id"]):
                    amt = float(t["price"]) * int(t["volume"])
                    if t["direction"] == "买入":
                        cash -= amt * BUY_FEE
                        vols[t["symbol"]] = vols.get(t["symbol"], 0) + int(t["volume"])
                    else:
                        cash += amt * SELL_FEE
                        vols[t["symbol"]] = vols.get(t["symbol"], 0) - int(t["volume"])
                        realized += float(t.get("profit") or 0.0)
                for s in symbols:  # 用最近 ≤ 当日的收盘价计价
                    seq = closes.get(s) or []
                    i = ptr[s]
                    while i < len(seq) and seq[i][0] <= day:
                        last_close[s] = seq[i][1]
                        i += 1
                    ptr[s] = i
                mv = sum(v * last_close.get(s, 0.0) for s, v in vols.items() if v)
                equity = cash + mv
                peak = max(peak, equity)
                dd = (equity - peak) / peak * 100.0 if peak else 0.0
                curve.append({
                    "date": iso_day(day), "day": day,
                    "equity": round(equity, 2), "cash": round(cash, 2), "mv": round(mv, 2),
                    "realized": round(realized, 2), "float": round(equity - initial - realized, 2),
                    "dd_pct": round(dd, 3),
                    "partial": bool(in_progress_day and day >= in_progress_day),
                })
                buys = sum(1 for t in by_day.get(day, []) if t["direction"] == "买入")
                sells = sum(1 for t in by_day.get(day, []) if t["direction"] != "买入")
                tids = trig_by_day.get(day) or []
                trig = [trig_by_id[i] for i in tids]
                executed = sum(1 for x in trig if x["status"] == "executed")
                blocked = sum(1 for x in trig if x["status"] in ("blocked", "cancelled"))
                daily.append({
                    "date": iso_day(day), "day": day,
                    "pnl": round(equity - prev_equity, 2),
                    "buys": buys, "sells": sells,
                    "triggers": len(trig), "executed": executed, "blocked": blocked,
                    "step_total": (day_meta.get(day) or {}).get("step_total"),
                    "armed": (day_meta.get(day) or {}).get("armed"),
                    "symbols": (day_meta.get(day) or {}).get("symbols"),
                    "partial": bool(in_progress_day and day >= in_progress_day),
                })
                prev_equity = equity
            if curve:
                last = curve[-1]
                account_block.update({
                    "equity": last["equity"],
                    "realized": last["realized"],
                    "float": last["float"],
                    "ret_pct": round((last["equity"] - initial) / initial * 100.0, 3) if initial else None,
                    "max_dd_pct": round(min(c["dd_pct"] for c in curve), 3),
                })
                if account_block["available_cash"] is not None:
                    recon_delta = round(last["cash"] - account_block["available_cash"], 2)
                    account_block["cash_model"] = last["cash"]
                    account_block["cash_delta_vs_account"] = recon_delta
                account_block["as_of"] = last["date"]
                account_block["as_of_partial"] = bool(last.get("partial"))
                account_block["equity_basis"] = "重建口径（见页脚说明）"

        # ── 持仓 ────────────────────────────────────────────────────────────
        positions = []
        last_day_iso = iso_day(curve_end)
        close_map = {}
        if symbols and curve_start and curve_end:
            for s, seq in self.closes(symbols, curve_start, curve_end).items():
                if seq:
                    close_map[s] = seq[-1][1]
        cal_sorted = window or cal
        for p in pg["positions"]:
            sym = p["symbol"]
            volume = int(p.get("volume") or 0)
            avg = float(p.get("avg_price") or 0.0)
            last = close_map.get(sym)
            pnl = round((last - avg) * volume, 2) if last is not None else None
            pnl_pct = round((last - avg) / avg * 100.0, 3) if (last is not None and avg) else None
            entry = compact_day(p.get("entry_date"))
            held = None
            if entry and cal_sorted:
                idx = [i for i, d in enumerate(cal_sorted) if d >= entry and d <= (curve_end or d)]
                held = len(idx) if idx else None
            positions.append({
                "symbol": sym,
                "name": self.name_for(sym),
                "volume": volume,
                "avg_price": round(avg, 4),
                "last": last,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "entry_date": iso_day(entry),
                "days_held": held,
                "cost": round(avg * volume, 2),
                "market_value": round((last or 0.0) * volume, 2) if last is not None else None,
                "highest_price": p.get("highest_price"),
                "theme": self.theme_for(sym, curve_end),
                "as_of": last_day_iso,
            })
        positions.sort(key=lambda r: (r["pnl"] is None, -(r["pnl"] or 0.0)))

        # ── 成交（PG 权威：trade_date 是 ISO）───────────────────────────────
        trades = []
        for t in trades_pg:
            reason = t.get("reason")
            day = compact_day(t.get("trade_date"))
            trades.append({
                "id": t["id"],
                "symbol": t["symbol"],
                "name": self.name_for(t["symbol"]),
                "side": "buy" if t["direction"] == "买入" else "sell",
                "direction": t["direction"],
                "price": float(t["price"]),
                "volume": int(t["volume"]),
                "amount": float(t.get("amount") or 0.0),
                "day": iso_day(t.get("trade_date")),
                "kind": parse_kind(reason),
                "pnl": (round(float(t["profit"]), 2) if t.get("profit") is not None else None),
                "reason": reason,
                "created_at": t.get("created_at"),
                "orderid": t.get("orderid"),
                "theme": self.theme_for(t["symbol"], day),
            })
        trades.sort(key=lambda r: (r["day"] or "", r["id"]), reverse=True)

        # ── 触发（逐日产物去重 + 按 id 首次出现的交易日归属）────────────────
        trigs_all = sorted(trig_by_id.values(), key=lambda r: r["id"])
        trigs_out = [dict(t, name=self.name_for(t["symbol"])) for t in trigs_all[-400:]][::-1]
        blocked_counter: dict[str, dict] = {}
        for t in trigs_all:
            if t["status"] not in ("blocked", "cancelled"):
                continue
            key = t["block"] or "其他"
            rec = blocked_counter.setdefault(key, {"reason": key, "n": 0, "symbols": {}, "days": [],
                                                   "samples": []})
            rec["n"] += 1
            rec["symbols"][t["symbol"]] = rec["symbols"].get(t["symbol"], 0) + 1
            if t["day_compact"] not in rec["days"]:
                rec["days"].append(t["day_compact"])
            if len(rec["samples"]) < 5:
                rec["samples"].append({"id": t["id"], "symbol": t["symbol"], "name": self.name_for(t["symbol"]),
                                       "day": t["day"],
                                       "status": t["status"], "price": t["price"],
                                       "reason": (t["reason"] or "")[:160]})
        blocked_top = []
        for rec in blocked_counter.values():
            syms = sorted(rec["symbols"].items(), key=lambda kv: -kv[1])[:6]
            blocked_top.append({
                "reason": rec["reason"], "n": rec["n"],
                "symbols": [{"symbol": s, "n": n} for s, n in syms],
                "first_day": iso_day(min(rec["days"])) if rec["days"] else None,
                "last_day": iso_day(max(rec["days"])) if rec["days"] else None,
                "samples": rec["samples"],
            })
        blocked_top.sort(key=lambda r: -r["n"])

        # ── 归因：腿型 / 主题 / 标的 ────────────────────────────────────────
        def _agg(keyfn):
            agg: dict[str, dict] = {}
            for t in trades:
                k = keyfn(t) or "未标注"
                rec = agg.setdefault(k, {"name": k, "n": 0, "amount": 0.0, "pnl": 0.0})
                rec["n"] += 1
                rec["amount"] += t["amount"]
                rec["pnl"] += t["pnl"] or 0.0
            for rec in agg.values():
                rec["amount"] = round(rec["amount"], 2)
                rec["pnl"] = round(rec["pnl"], 2)
            return sorted(agg.values(), key=lambda r: -r["n"])

        by_kind = _agg(lambda t: t["kind"])
        by_theme = [{"name": r["name"], "n": r["n"], "pnl": r["pnl"]} for r in _agg(lambda t: t["theme"])]
        by_symbol = [{"symbol": r["name"], "name": self.name_for(r["name"]), "n": r["n"], "pnl": r["pnl"],
                      "amount": r["amount"], "theme": self.theme_for(r["name"], curve_end)}
                     for r in _agg(lambda t: t["symbol"])]

        # ── 覆盖率与口径 ────────────────────────────────────────────────────
        wave = self.wave_stats()
        mins = self.mins_stats(active_days)
        decision_blocked_days = [d for d, m in sorted(day_meta.items())
                                 if (m.get("decision") or {}).get("missing")
                                 or (m.get("decision") or {}).get("ok") is False]
        coverage = {
            "prod_days": len(active_days),
            "prod_days_on_disk": len(self._days),
            "stale_prod_days": len(act["stale"]),
            "stale_prod_samples": [iso_day(d) for d in act["stale"][:10]],
            "stale_rule": act["rule"],
            "run_started_at": act["run_start_iso"],
            "wave_ok_days": wave.get("ok"),
            "wave_err_days": wave.get("err"),
            "wave_total_days": wave.get("total"),
            "wave_err_samples": wave.get("err_days") or [],
            "decision_blocked_days": len(decision_blocked_days),
            "decision_blocked_samples": [iso_day(d) for d in decision_blocked_days[:8]],
            "minute_missing_pct": mins.get("missing_pct"),
            "note": self._coverage_note(trades, wave, mins, procs_live, recon_delta, pg,
                                        stale_n=len(act["stale"]), rule=act["rule"]),
            "recon_delta": recon_delta,
            "account": self.account,
            "run_window": [iso_day(run_start), iso_day(run_end)],
            "prod_days_total_target": days_total,
            "mins": mins,
        }

        # ── 告警 ────────────────────────────────────────────────────────────
        if seconds_since is not None and seconds_since > 60:
            warnings.append({"level": "warn",
                             "text": "心跳静止：最后活动 %.0f 秒前（%s），超过 60 秒阈值" % (seconds_since, src or "-")})
        if not procs_live:
            if fresh:
                warnings.append({"level": "info",
                                 "text": "/proc 扫描未发现 bt_days / bt_prod_run 进程，但最后活动在 %.0f 秒内（驱动可能是短命子进程）"
                                         % (seconds_since or 0)})
            else:
                since_txt = ("%.0f 秒前" % seconds_since) if seconds_since is not None else "早于可读范围"
                warnings.append({"level": "warn",
                                 "text": "/proc 扫描未发现 bt_days / bt_prod_run 进程，且最后活动已 %s —— 跑批可能已停止/暂停，或尚未启动"
                                         % since_txt})
        if account_block.get("frozen_cash"):
            warnings.append({"level": "warn", "text": "frozen_cash=%.2f 非零：有委托未落地，账上资金被冻结" % account_block["frozen_cash"]})
        if recon_delta is not None and abs(recon_delta) >= 1.0:
            warnings.append({"level": "info",
                             "text": "重建现金与账户可用现金差 %.2f 元（费用模型口径差异，非数据缺失）" % recon_delta})
        if wave.get("exists") and wave.get("err"):
            warnings.append({"level": "warn", "text": "波浪层 %d/%d 天失败" % (wave["err"], wave["total"])})
        if decision_blocked_days:
            warnings.append({"level": "info",
                             "text": "决策对象缺失（默认放行）的日子 %d 天：%s"
                                     % (len(decision_blocked_days), ", ".join(iso_day(d) for d in decision_blocked_days[:5]))})
        if mins.get("missing"):
            warnings.append({"level": "info",
                             "text": "分钟文件缺口约 %.2f%%（估算：%d/%d 条 armed 无对应 5min 文件）"
                                     % (mins["missing_pct"] or 0.0, mins["missing"], mins["armed_total"])})
        unmapped = [t for t in trades if not t["theme"]]
        if trades and unmapped:
            warnings.append({"level": "info",
                             "text": "%d/%d 笔成交无主题映射（legs*.jsonl 未覆盖该标的）" % (len(unmapped), len(trades))})
        if not trades and not pg["error"]:
            warnings.append({"level": "info", "text": "账户 %s 暂无成交（paper_trades 为空）" % self.account})

        return {
            "heartbeat": heartbeat,
            "account": account_block,
            "curve": curve,
            "daily": daily,
            "positions": positions,
            "trades": trades,
            "triggers": trigs_out,
            "triggers_total": len(trigs_all),
            "triggers_returned": len(trigs_out),
            "blocked_top": blocked_top,
            "by_kind": by_kind,
            "by_theme": by_theme,
            "by_symbol": by_symbol,
            "coverage": coverage,
            "warnings": warnings,
            "meta": {
                "account": self.account,
                "root": self.root,
                "bars_db": self.bars_db,
                "mins_dir": self.mins_dir,
                "log": self.log_path,
                "sources": ["本地 PG paper_*/t_triggers/stock_pool", "_summary/prod_*.json", "_bt_full/bars.sqlite",
                            "_bt_full/mins/", "_summary/wave_backfill.json", ".dsh-tmp/wolfbt/logs/year_prod.log",
                            "/proc/*/cmdline"],
                "generated_at": now_iso(),
                "cache_ttl_s": self.ttl,
            },
        }

    def _coverage_note(self, trades, wave, mins, procs, recon_delta, pg,
                       stale_n: int = 0, rule: str = "none") -> str:
        parts = []
        parts.append("口径：生产链逐日重放（bt_days → bt_prod_run，LLM-off 版本）+ 本地 PG 落库；"
                     "账户 %s，起点 %.0f 元。" % (self.account, (pg.get("account") or {}).get("initial_capital") or 0.0))
        parts.append("净值/回撤为**重建**口径：cash = 起点 + Σ(买入 -(价×量×1.000396) / 卖出 +(价×量×0.999104))，"
                     "再按当日收盘计价（缺失日用最近 ≤ 当日的收盘），equity = cash + 持仓市值；"
                     "曲线覆盖窗口内全部交易日（含没有成交的日子），末点若为进行中的交易日会在页面上标注。")
        parts.append("触发按 prod_*.json 的 id 首次出现日归属（这些文件是当日全量快照，已按 id 去重）。")
        parts.append("波浪层 %s/%s 天 ok；分钟缺口为**估算**（armed 标的 × 当日 5min 文件存在性）。"
                     % (wave.get("ok"), wave.get("total")))
        parts.append("沙箱复用：日志显示 seed 沙箱按日递增复用（cut 逐日前移），不是每天重建。")
        if stale_n:
            parts.append("⚠ 本次统计只用**当前这次跑批**的产物：判定规则 %s，已排除 %d 个上一次遗留的 prod_*.json。"
                         % (rule, stale_n))
        if recon_delta is not None:
            parts.append("重建现金 vs 账户可用现金差 %.2f 元（费用模型口径差异）。" % recon_delta)
        parts.append("跑批进程：%s。" % ("、".join("%s(pid %d)" % (p["kind"], p["pid"]) for p in procs) if procs else "未发现"))
        return " ".join(parts)

    # ──────────────────────────────────────────────────────────────────────
    # 个股 / 单日
    # ──────────────────────────────────────────────────────────────────────
    def symbol_detail(self, symbol: str) -> dict:
        self.refresh()
        act = self.active_view()
        if not SYMBOL_RE.match(symbol or ""):
            return {"error": "symbol 形态应为 SH600977 / SZ000001 / BJ920139"}
        log = self.log_info()
        active_days = act["days"]
        last_completed = log.get("last_completed") or (active_days[-1] if active_days else None)
        start = log.get("run_start") or (active_days[0] if active_days else None)
        pg = self.pg_fetch()
        bars = self.bars_for(symbol, start, last_completed, 400) if (start and last_completed) else []
        sym_name = self.name_for(symbol)
        trades = []
        for t in pg["trades"]:
            if t["symbol"] != symbol:
                continue
            day = compact_day(t.get("trade_date"))
            trades.append({
                "id": t["id"], "symbol": symbol, "name": sym_name,
                "side": "buy" if t["direction"] == "买入" else "sell",
                "direction": t["direction"], "price": float(t["price"]), "volume": int(t["volume"]),
                "amount": float(t.get("amount") or 0.0), "pnl": t.get("profit"),
                "day": iso_day(day), "day_compact": day, "kind": parse_kind(t.get("reason")),
                "reason": t.get("reason"), "created_at": t.get("created_at"),
            })
        trades.sort(key=lambda r: (r["day"] or "", r["id"]))
        trigs = [dict(t, name=sym_name) for t in act["trig_by_id"].values() if t["symbol"] == symbol]
        trigs.sort(key=lambda r: r["id"])
        position = None
        for p in pg["positions"]:
            if p["symbol"] == symbol:
                position = {
                    "symbol": symbol, "name": sym_name, "volume": int(p.get("volume") or 0),
                    "avg_price": float(p.get("avg_price") or 0.0), "entry_date": iso_day(compact_day(p.get("entry_date"))),
                    "highest_price": p.get("highest_price"),
                }
                if bars:
                    last = bars[-1]["close"]
                    position["last"] = last
                    position["pnl"] = round((last - position["avg_price"]) * position["volume"], 2)
                    position["pnl_pct"] = (round((last - position["avg_price"]) / position["avg_price"] * 100.0, 3)
                                           if position["avg_price"] else None)
                break
        # 快照字段（t_triggers.snapshot）：按 trigger id 对齐到交易日（本服务已把 id→交易日
        # 从逐日产物里重建出来；t_triggers.created_at 是**重放墙钟时间**，不能当交易日用）。
        snap = None
        snap_note = None
        snap_days: list[str] = []
        try:
            if psycopg2 is not None:
                conn = psycopg2.connect(self.pg_url, connect_timeout=4)
                try:
                    conn.set_session(readonly=True, autocommit=True)
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                    cur.execute(
                        "SELECT id, event_type, status, quote_price, trigger_price, created_at, snapshot"
                        " FROM t_triggers WHERE account_id=%s AND symbol=%s AND snapshot IS NOT NULL"
                        " ORDER BY id",
                        (self.account, symbol),
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                finally:
                    conn.close()
                latest = None
                for r in rows:
                    meta = act["trig_by_id"].get(r["id"])
                    day = (meta or {}).get("day")
                    if day:
                        snap_days.append(day)
                    latest = (r, day)  # 表按 id 升序 → 最后一行即 id 最大
                if latest and latest[0]:
                    raw, day = latest
                    snap = self._flatten_snapshot(raw.get("snapshot"))
                    snap["_trigger_id"] = raw.get("id")
                    snap["_trigger_day"] = day or "未知（该 id 不在逐日产物里）"
                    snap["_event_type"] = raw.get("event_type")
                    snap["_status"] = raw.get("status")
                    snap["_quote_price"] = raw.get("quote_price")
                    if snap_days:
                        snap_note = ("该标的在 t_triggers 里有 %d 行带 snapshot（回测重置后累积，可对齐到交易日 "
                                     "%s → %s）；此处展示 id 最大的一行。表外的更早交易日没有快照留存，本页不补造。"
                                     % (len(snap_days), min(snap_days), max(snap_days)))
                    else:
                        snap_note = ("该标的在 t_triggers 里有 %d 行带 snapshot，但这些 id 不在逐日产物中，"
                                     "无法归属到交易日（created_at 是重放墙钟时间）。" % len(rows))
                else:
                    snap_note = "该标的在 t_triggers 中没有带 snapshot 的行。"
        except Exception as exc:
            snap_note = "snapshot 读取失败：%s" % str(exc)[:120]
        realized = round(sum(float(t["pnl"] or 0.0) for t in trades if t["side"] == "sell"), 2)
        return {
            "symbol": symbol,
            "name": sym_name,
            "ts_code": to_ts_code(symbol),
            "theme": self.theme_for(symbol, last_completed),
            "window": [iso_day(start), iso_day(last_completed)],
            "bars": bars,
            "trades": trades,
            "triggers": trigs[-300:],
            "triggers_total": len(trigs),
            "position": position,
            "snapshot": snap,
            "snapshot_note": snap_note,
            "snapshot_days": ({"count": len(snap_days), "first": min(snap_days), "last": max(snap_days)}
                              if snap_days else {"count": 0, "first": None, "last": None}),
            "stats": {
                "buys": sum(1 for t in trades if t["side"] == "buy"),
                "sells": sum(1 for t in trades if t["side"] == "sell"),
                "realized": realized,
                "triggers": len(trigs),
                "executed": sum(1 for t in trigs if t["status"] == "executed"),
                "blocked": sum(1 for t in trigs if t["status"] in ("blocked", "cancelled")),
            },
            "generated_at": now_iso(),
        }

    @staticmethod
    def _flatten_snapshot(snap, depth: int = 0, max_depth: int = 3) -> dict:
        """把 t_triggers.snapshot 压成可展示的扁平键值（原样取值，不做推断）。"""
        if not isinstance(snap, dict):
            return {}
        out: dict = {}
        for k, v in snap.items():
            if isinstance(v, dict) and depth < max_depth:
                for k2, v2 in Store._flatten_snapshot(v, depth + 1, max_depth).items():
                    out["%s.%s" % (k, k2)] = v2
            elif isinstance(v, (list, tuple)):
                if len(v) <= 4 and all(isinstance(x, (int, float, str, bool)) or x is None for x in v):
                    out[k] = list(v)
                else:
                    out[k] = "[%d 项]" % len(v)
            else:
                out[k] = v
        return out

    def day_detail(self, day: str) -> dict:
        self.refresh()
        act = self.active_view()
        d = compact_day(day)
        if not d:
            return {"error": "day 形态应为 20260302 或 2026-03-02"}
        rec = self._files.get(d)
        meta = act["day_meta"].get(d) or {}
        log = self.log_info()
        active_days = act["days"]
        last_completed = log.get("last_completed") or (active_days[-1] if active_days else None)
        pg = self.pg_fetch()
        trades = []
        for t in pg["trades"]:
            if compact_day(t.get("trade_date")) != d:
                continue
            trades.append({
                "id": t["id"], "symbol": t["symbol"], "name": self.name_for(t["symbol"]),
                "side": "buy" if t["direction"] == "买入" else "sell",
                "price": float(t["price"]), "volume": int(t["volume"]),
                "amount": float(t.get("amount") or 0.0), "pnl": t.get("profit"),
                "kind": parse_kind(t.get("reason")), "reason": t.get("reason"),
                "created_at": t.get("created_at"),
                "theme": self.theme_for(t["symbol"], d),
            })
        tids = act["trig_by_day"].get(d) or []
        trigs = [dict(act["trig_by_id"][i], name=self.name_for(act["trig_by_id"][i]["symbol"])) for i in tids]
        trigs.sort(key=lambda r: r["id"])
        step = (rec or {}).get("step_secs") or {}
        step_rows = sorted(({"name": k, "sec": round(v, 2)} for k, v in step.items()),
                           key=lambda r: -r["sec"])
        return {
            "day": iso_day(d),
            "day_compact": d,
            "date": iso_day(d),
            "exists": rec is not None,
            "cut": (rec or {}).get("cut"),
            "armed": [dict(a, name=self.name_for(a.get("symbol"))) for a in ((rec or {}).get("armed") or [])],
            "symbols": [{"symbol": sym, "name": self.name_for(sym)}
                        for sym in ((rec or {}).get("symbols") or [])],
            "trades": trades,
            "triggers": trigs,
            "counts": {
                "triggers": len(trigs),
                "executed": sum(1 for t in trigs if t["status"] == "executed"),
                "blocked": sum(1 for t in trigs if t["status"] in ("blocked", "cancelled")),
                "pending": sum(1 for t in trigs if t["status"] == "pending"),
                "info": sum(1 for t in trigs if t["status"] == "info"),
                "buys": sum(1 for t in trades if t["side"] == "buy"),
                "sells": sum(1 for t in trades if t["side"] == "sell"),
                "symbols": len((rec or {}).get("symbols") or []),
                "armed": len((rec or {}).get("armed") or []),
            },
            "step_secs": step_rows,
            "step_total": meta.get("step_total"),
            "decision": (rec or {}).get("decision"),
            "frozen_cash_end": (rec or {}).get("frozen_cash_end"),
            "account_cash_end": meta.get("account_cash_end"),
            "market_missing": (rec or {}).get("market_missing") or [],
            "note": (None if rec is not None else
                     "该交易日没有 prod_*.json（布腿为 0 条时不会跑生产链），因此没有当日触发/成交产物。"),
            "last_completed": iso_day(last_completed),
            "generated_at": now_iso(),
        }


# ──────────────────────────────────────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "bt-dashboard/1.0"
    protocol_version = "HTTP/1.1"
    store: Store = None  # type: ignore

    def log_message(self, fmt, *args):  # 保持 stdout/stderr 干净，只留一行访问日志
        sys.stderr.write("[bt_dashboard] %s - %s\n" % (self.address_string(), fmt % args))

    # ── helpers ───────────────────────────────────────────────────────────
    def _send(self, body: bytes, ctype: str, status: int = 200, cache: str = "no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8", status)

    def _file(self, path: str, ctype: str, cache: str = "no-store"):
        try:
            with open(path, "rb") as fh:
                self._send(fh.read(), ctype, cache=cache)
        except OSError as exc:
            self._json({"error": "读不到 %s: %s" % (os.path.basename(path), exc)}, 500)

    # ── routing ───────────────────────────────────────────────────────────
    def do_GET(self):  # noqa: N802
        try:
            parsed = urlparse(self.path)
            route = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query)
            if route == "/":
                return self._file(os.path.join(HERE, "bt_dashboard.html"), "text/html; charset=utf-8")
            if route == "/tokens.css":
                return self._file(os.path.join(HERE, "tokens.css"), "text/css; charset=utf-8")
            if route == "/vendor/gsap.min.js":
                # 离线自托管：只读转发 frontend/node_modules 里的 GSAP（不改动 frontend/ 任何文件）
                if not os.path.isfile(GSAP_PATH):
                    return self._json({"error": "GSAP 未安装：%s 不存在（页面会自动降级为无动效）" % GSAP_PATH}, 404)
                return self._file(GSAP_PATH, "application/javascript; charset=utf-8",
                                  cache="public, max-age=3600")
            if route == "/api/snapshot":
                return self._json(self.store.snapshot())
            if route == "/api/symbol":
                sym = (qs.get("symbol") or [""])[0].strip().upper()
                if not sym:
                    return self._json({"error": "缺少 symbol 参数，例：/api/symbol?symbol=SH600977"}, 400)
                if not SYMBOL_RE.match(sym):
                    return self._json({"error": "symbol 形态应为 SH600977 / SZ000001 / BJ920139，收到 %r" % sym}, 400)
                return self._json(self.store.symbol_detail(sym))
            if route == "/api/day":
                day = (qs.get("day") or qs.get("date") or [""])[0].strip()
                if not day:
                    return self._json({"error": "缺少 day 参数，例：/api/day?day=20260302"}, 400)
                return self._json(self.store.day_detail(day))
            if route == "/favicon.ico":
                return self._send(b"", "image/x-icon", 204)
            return self._json({"error": "not found", "path": parsed.path}, 404)
        except BrokenPipeError:
            return
        except Exception as exc:
            sys.stderr.write("[bt_dashboard] ERROR %s\n%s\n" % (exc, traceback.format_exc()))
            try:
                self._json({"error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}, 500)
            except Exception:
                pass

    def do_HEAD(self):  # noqa: N802
        return self.do_GET()


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="回测监控只读看板（标准库 + psycopg2）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--root", default=os.path.join(REPO, "data", "_bt_year"),
                    help="逐日产物根目录（默认 data/_bt_year）")
    ap.add_argument("--bars", default=os.path.join(REPO, "data", "_bt_full", "bars.sqlite"))
    ap.add_argument("--mins", default=os.path.join(REPO, "data", "_bt_full", "mins"))
    ap.add_argument("--log", default=os.path.join(REPO, ".dsh-tmp", "wolfbt", "logs", "year_prod.log"))
    ap.add_argument("--pg", default=os.getenv("BT_PG_URL", DEFAULT_PG))
    ap.add_argument("--account", default=os.getenv("BT_ACCOUNT", "stock"))
    ap.add_argument("--ttl", type=float, default=3.0, help="PG/快照缓存秒数（默认 3s，页面 10s 轮询）")
    return ap


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    store = Store(args)
    Handler.store = store
    missing = [p for p in (args.root, args.bars) if not os.path.exists(p)]
    if missing:
        sys.stderr.write("[bt_dashboard] 提示：数据源不存在 %s（服务照常启动，页面会显示空态/告警）\n"
                         % ", ".join(missing))
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True

    def _graceful(signum, _frame):
        # SIGTERM / SIGINT 都走同一条收尾路径：关闭监听套接字，不留后台进程
        sys.stderr.write("\n[bt_dashboard] 收到信号 %d，正在停止…\n" % signum)
        raise KeyboardInterrupt

    for _sig in ("SIGTERM", "SIGINT"):
        if hasattr(signal, _sig):
            try:
                signal.signal(getattr(signal, _sig), _graceful)
            except (ValueError, OSError):
                pass
    sys.stderr.write("[bt_dashboard] 只读服务已启动: http://%s:%d/  (account=%s, root=%s)\n"
                     % (args.host, args.port, args.account, args.root))
    sys.stderr.flush()
    try:
        httpd.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        sys.stderr.write("\n[bt_dashboard] 收到中断，正在停止…\n")
    finally:
        httpd.shutdown()
        httpd.server_close()
        sys.stderr.write("[bt_dashboard] 已停止（未留后台进程）\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
