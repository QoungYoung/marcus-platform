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


def scan_procs(root: "str | None" = None) -> list[dict]:
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
        except OSError as _e_sil1:
            _silent_alert("bt_dashboard.py:180", _e_sil1)
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
            if base in ("bt_pack_mins.py", "bt_day_legs.py", "bt_day_legs_switch.py",
                        "bt_seed_day.py", "bt_low_logic_week.py", "bt_low_logic_asof.py",
                        "bt_account.py", "bt_backfill_mins_union.py"):
                kind = "bt_job:" + base[:-3]
                break
        if not kind:
            continue
        # ── 根目录归属过滤（2026-09-18）─────────────────────────────────────────
        # 不加这层：另起一个看板看**旧根**时，只要机上有**别的跑批进程**，页面就显示"在跑"
        # （实测 8799 看 data/_bt_year 却报 alive/proc_found=true、current_day=20260112，
        #   其实是 8801 那跑 data/_bt_size 的进程被扫到了 ⇒ 用户以为"你跑了两个"）。
        if root:
            _r = os.path.abspath(root)
            if "--root" in parts:
                try:
                    _i = parts.index("--root")
                    if os.path.abspath(parts[_i + 1]) != _r:
                        continue
                except Exception as _e_sil1:
                    print("[silent:bt_dashboard.py:219] %s: %s" % (type(_e_sil1).__name__, str(_e_sil1)[:110]), flush=True)
            else:
                _default = os.path.abspath(os.path.join(REPO, "data", "_bt_year"))
                if _r != _default:
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


def _tag_log_for_root(root: str, min_mtime: float = 0.0) -> str | None:
    """按跑批根选日志（2026-09-19 修复"看板追不到新跑批"）。

    实测病灶：跑批进程的 `/proc/<pid>/fd/1` 读出来是 pipe（`readlink` 为空/pipe:[…]）⇒
    旧逻辑退回 "logs 目录里最新的 .log" —— 而**看板自己的 dashboard.log 永远最新**，
    于是页面把 `dashboard.log` 当跑批日志钉住：既看不到新日志，days_total 也停在上一跑的 10 天。
    做法：优先 logs/ 里**文件名含根 tag**（data/_bt_jan6 → jan6）且 mtime ≥ 进程启动时间的日志；
    否则取 mtime ≥ 启动时间的最新非看板日志。
    """
    logs_dir = os.path.join(REPO, ".dsh-tmp", "wolfbt", "logs")
    tag = os.path.basename(os.path.abspath(root or "")).replace("_bt_", "") or ""
    best_tag, best_any = None, None
    try:
        for fn in os.listdir(logs_dir):
            if not fn.endswith(".log") or fn == "dashboard.log":
                continue
            fp = os.path.join(logs_dir, fn)
            try:
                mt = os.path.getmtime(fp)
            except OSError as _e_sil2:
                _silent_alert("bt_dashboard.py:265", _e_sil2)
                continue
            if mt < float(min_mtime or 0.0) - 60:
                continue
            if tag and tag in fn and (best_tag is None or mt > best_tag[0]):
                best_tag = (mt, fp)
            if best_any is None or mt > best_any[0]:
                best_any = (mt, fp)
    except OSError:
        return None
    return (best_tag or best_any or (0.0, None))[1]


def live_run_log(root: "str | None" = None) -> str | None:
    """当前跑批的日志文件 = 活的 bt_days 进程的 stdout（/proc/<pid>/fd/1）。

    为什么需要：跑批可以换日志名（例如硬隔离年跑写 year_prod_iso.log），而仪表盘只认 --log，
    结果「新开的一跑没被追踪到」——页面停在上一跑的 170/170、current_day=null。
    以进程 stdout 为准，换名字/换目录都能自动追上。
    """
    for p in scan_procs(root):      # 只认本看板根目录的跑批（2026-09-18）
        if p.get("kind") != "bt_days":
            continue
        try:
            target = os.readlink("/proc/%d/fd/1" % p["pid"])
        except OSError as _e_sil3:
            _silent_alert("bt_dashboard.py:290", _e_sil3)
            continue
        if target.startswith(REPO) and target.endswith(".log"):
            return os.path.abspath(target)
    # 2026-09-18：指定了非默认根时**不做全局回退** —— 否则会把"别的跑的日志"借过来，
    #   页面显示在跑（实测 8799 看旧根 data/_bt_year，却因借到 data/_bt_size 的日志而报 fresh）。
    if root:
        _t = _tag_log_for_root(root)
        if _t:
            return _t
        if os.path.abspath(root) != os.path.abspath(os.path.join(REPO, "data", "_bt_year")):
            return None
    # 没有在跑的跑批时：退回「最近一次跑的日志」——logs 目录里最新的、开头带交易日区间头的那份。
    # （否则跑完以后页面会掉回 --log 默认的老日志，又显示上一跑的 170/170）
    logs_dir = os.path.join(REPO, ".dsh-tmp", "wolfbt", "logs")
    head_re = re.compile(r"\d+\s*个交易日[：:]\s*\d{8}")
    best = None
    try:
        for fn in os.listdir(logs_dir):
            if not fn.endswith(".log"):
                continue
            fp = os.path.join(logs_dir, fn)
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                    head = "".join(next(fh, "") for _ in range(8))
                if not head_re.search(head):
                    continue
                mt = os.path.getmtime(fp)
            except OSError as _e_sil4:
                _silent_alert("bt_dashboard.py:318", _e_sil4)
                continue
            if best is None or mt > best[0]:
                best = (mt, fp)
    except OSError:
        return None
    return best[1] if best else None


# ──────────────────────────────────────────────────────────────────────────────
# 计价空间自动判定（2026-09-24，「0129 亏了一万多」的真相）
# ──────────────────────────────────────────────────────────────────────────────
# 背景：T13/T14 用 `WOLF_ADJ_PRICE=1` ⇒ **成交价与现金都在前复权空间**；而净值重建一直用
# **不复权** `data/_bt_full/bars.sqlite` 给持仓计价 ⇒ 市值虚影 = Σ 股数 ×(不复权收 − 复权收)。
# 实测（drabt14，0128 收盘）：SZ003026 1100 股，不复权收 34.740 / 复权收 23.878
#   （原因：adj_factors.json 里 20260605「10转4.5」倍数 1.4549 被"前复权"提前折进了回测期）
#   ⇒ 虚影 +11,949，当日持仓虚影合计 +12,166 ⇒ 看板 0128 +9.69%、0129 单日 −11,098，
#   而真实是 +4.95% / −4,298（虚影全期累计为 0，期末权益不受影响，只错在权益水平/单日/回撤）。
#
# 判据：只用行情自身，不读配置、不读 pins ——
#   逐笔比 |成交价 ÷ 收盘 − 1|，且**只在两套收盘不同**（该票当日之后有除权事件）的成交上判：
#   正确空间下该比值≈1（只剩盘中偏离），错误空间下≈1/f（f 可达 1.45 ⇒ 差 31%）。多数票胜出。
def _median(xs: list[float]):
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2.0


def detect_price_space(trades: list[dict], bars_db: str, bars_adj: str = "",
                       samples: int = 200, min_affected: int = 3, gap: float = 0.03) -> dict:
    """判定某账户的成交价在哪套价格空间：返回 {'space','n_affected','wins_adj','wins_raw','why',...}。

    · space='adj' ⇒ 曲线/持仓计价必须用 bars_adj.sqlite；
    · space='raw' ⇒ 用不复权 bars.sqlite（默认；两套无差异时也返回它）。
    环境变量 `WOLF_CURVE_SPACE=raw|adj` 可强制覆盖（排障用）。
    """
    forced = (os.getenv("WOLF_CURVE_SPACE") or "").strip().lower()
    out = {"space": "raw", "n_affected": 0, "n_used": 0, "wins_adj": 0, "wins_raw": 0,
           "med_raw": None, "med_adj": None, "why": ""}
    if forced in ("raw", "adj"):
        out.update(space=forced, why="WOLF_CURVE_SPACE=%s 强制" % forced)
        return out
    if not trades or not bars_adj or not os.path.exists(bars_adj) or not os.path.exists(bars_db):
        out["why"] = "无复权库或成交为空"
        return out
    try:
        con_raw = sqlite3.connect("file:%s?mode=ro" % quote(bars_db), uri=True, timeout=5.0)
        con_adj = sqlite3.connect("file:%s?mode=ro" % quote(bars_adj), uri=True, timeout=5.0)
        con_raw.execute("PRAGMA temp_store=MEMORY")
        con_adj.execute("PRAGMA temp_store=MEMORY")
    except Exception as exc:
        out["why"] = "打开行情库失败: %s" % str(exc)[:60]
        return out
    e_raw, e_adj = [], []
    try:
        # 取样：① 均匀抽样（覆盖不同日期）∪ ② **每只标的第一笔**（"该票是否受复权影响"是票级属性，
        # 一笔即够；这样能把"有未来除权事件的票"尽量都覆盖到，避免只抽到 3 笔affected就下结论）。
        step = max(1, len(trades) // max(1, samples))
        cand: dict = {}
        for t in trades[::step][:samples]:
            cand.setdefault((t.get("symbol"), compact_day(t.get("trade_date")), t.get("price")), t)
        seen_sym: set = set()
        for t in trades:
            sym = t.get("symbol")
            if sym in seen_sym:
                continue
            seen_sym.add(sym)
            cand.setdefault((sym, compact_day(t.get("trade_date")), t.get("price")), t)
        for t in list(cand.values())[:samples * 3]:
            ts_code = to_ts_code(t.get("symbol") or "")
            day = compact_day(t.get("trade_date"))
            px = float(t.get("price") or 0)
            if not ts_code or not day or px <= 0:
                continue
            r = con_raw.execute("SELECT close FROM bars WHERE ts_code=? AND trade_date=?",
                                (ts_code, day)).fetchone()
            a = con_adj.execute("SELECT close FROM bars WHERE ts_code=? AND trade_date=?",
                                (ts_code, day)).fetchone()
            if not r or not a or not r[0] or not a[0]:
                continue
            cr, ca = float(r[0]), float(a[0])
            if abs(cr / ca - 1.0) < gap:      # 两套收盘无差别 ⇒ 对该票无信息
                continue
            e_raw.append(abs(px / cr - 1.0))
            e_adj.append(abs(px / ca - 1.0))
    finally:
        con_raw.close()
        con_adj.close()
    n = len(e_raw)
    out["n_affected"] = n
    if n == 0:
        out["why"] = "成交票均无复权事件（两套收盘相同）⇒ 用不复权"
        return out
    out["n_used"] = n
    out["med_raw"] = round(_median(e_raw), 5)
    out["med_adj"] = round(_median(e_adj), 5)
    wins_adj = sum(1 for x, y in zip(e_raw, e_adj) if y < x * 0.8)
    wins_raw = sum(1 for x, y in zip(e_raw, e_adj) if x < y * 0.8)
    out["wins_adj"], out["wins_raw"] = wins_adj, wins_raw
    m_raw, m_adj = _median(e_raw), _median(e_adj)
    if wins_adj >= max(min_affected, 0.8 * n):
        out.update(space="adj", why="%d/%d 笔成交更贴合复权收盘" % (wins_adj, n))
    elif wins_raw >= max(min_affected, 0.8 * n):
        out.update(space="raw", why="%d/%d 笔成交更贴合不复权收盘" % (wins_raw, n))
    elif n >= 2 and wins_adj == n and m_adj is not None and m_raw and m_adj <= 0.5 * m_raw:
        # 受影响样本少（< min_affected）但**方向完全一致**且中位数差 2 倍以上 ⇒ 仍可判
        out.update(space="adj", why="%d/%d 笔（小样本）且中位误差 %.4f ≪ %.4f" % (wins_adj, n, m_adj, m_raw))
    elif n >= 2 and wins_raw == n and m_raw is not None and m_adj and m_raw <= 0.5 * m_adj:
        out.update(space="raw", why="%d/%d 笔（小样本）且中位误差 %.4f ≪ %.4f" % (wins_raw, n, m_raw, m_adj))
    else:
        out["why"] = "样本分歧（adj %d / raw %d，共 %d）⇒ 保守用不复权" % (wins_adj, wins_raw, n)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Store：把磁盘/PG 的只读视图聚合成快照
# ──────────────────────────────────────────────────────────────────────────────
class Store:
    def __init__(self, args: argparse.Namespace):
        self.root = os.path.abspath(args.root)
        self.bars_db = os.path.abspath(args.bars)
        # 复权日线库（计价空间自动判定用；默认与 --bars 同目录的 bars_adj.sqlite）
        _ba = getattr(args, "bars_adj", "") or os.path.join(os.path.dirname(self.bars_db), "bars_adj.sqlite")
        self.bars_adj_db = os.path.abspath(_ba)
        self._space_cache: dict = {}          # account → (ts, info)
        self._curve_space = "raw"             # 本轮快照实际采用的计价空间
        self._curve_space_why = ""
        self.mins_dir = os.path.abspath(args.mins)
        self.log_path = os.path.abspath(args.log)
        self.pg_url = args.pg
        self.account = args.account
        self.ttl = max(0.5, float(args.ttl))

        self._lock = threading.RLock()
        self._sig = None
        self._files: dict[str, dict] = {}
        # ── 自动跟随（用户："不能不换端口自动追踪到最新进程吗"）────────────────────────
        #   默认开（--no-auto 关闭）：每次快照前若有 role=run 的跑批进程，就把它
        #   cmdline 里的 --root/--account 切成看板的根/账户并清缓存；没有则保持当前。
        self.auto_follow = bool(getattr(args, "auto", True))
        self._follow = {"root": self.root, "account": self.account, "auto": self.auto_follow,
                        "switches": 0, "reason": "初始"}
        self._follow_at = 0.0
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
        self._live_log_at = 0.0     # 「当前跑日志」解析时间（按 ttl 缓存）
        self._proc_seen: dict = {}  # pid → (cpu_ticks, io_bytes) 上次采样（判"在干活"）
        self._manifest_at = 0.0     # 运行清单逻辑起点缓存（5s）
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
            except OSError as _e_sil5:
                _silent_alert("bt_dashboard.py:503", _e_sil5)
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

    def autofollow(self) -> None:
        """自动跟随最新在跑的跑批（最多每 10s 检查一次）。"""
        if not self.auto_follow:
            return
        now = time.time()
        if now - self._follow_at < 10.0:
            return
        self._follow_at = now
        try:
            runs = [p for p in scan_procs(None) if p.get("kind") == "bt_days"]
        except Exception:
            runs = []
        if not runs:                   # 兜底：日志推断（沙箱禁 /proc）
            runs = [p for p in runs_from_logs() if p.get("kind") == "bt_days"]
        if not runs:
            self._follow["reason"] = "无在跑跑批（保持当前根）"
            return
        runs.sort(key=lambda p: -(p.get("started_epoch") or 0))
        p = runs[0]
        cmd = p.get("cmdline") or ""
        m_root = re.search(r"--root\s+(\S+)", cmd)
        if not m_root and p.get("root"):            # 日志推断的记录直接给 root/account
            m_root = type("M", (), {"group": staticmethod(lambda i=1: p.get("root"))})()
        m_acc = re.search(r"--account\s+(\S+)", cmd)
        new_root = os.path.abspath(m_root.group(1)) if m_root else None
        new_acc = m_acc.group(1) if m_acc else None
        if not new_acc:
            try:
                with open("/proc/%d/environ" % p["pid"], "rb") as fh:
                    env = fh.read().decode("utf-8", "replace")
                m2 = re.search(r"T_MONITOR_ACCOUNT=([^\x00\n]+)", env)
                new_acc = m2.group(1).strip() if m2 else None
            except Exception:
                new_acc = None
        changed = []
        if new_root and new_root != self.root and os.path.isdir(os.path.join(new_root, "_summary")):
            self.root = new_root
            self._files = {}
            self._log, self._log_sig = {}, None
            self._live_log_at = 0.0
            changed.append("root=%s" % os.path.basename(new_root))
        if new_acc and new_acc != self.account:
            self.account = new_acc
            self._pg, self._pg_at, self._pg_err = [], 0.0, None
            changed.append("account=%s" % new_acc)
        if changed:
            self._follow.update({"root": self.root, "account": self.account,
                                 "switches": self._follow["switches"] + 1,
                                 "reason": "跟随 pid=%s %s" % (p.get("pid"), ",".join(changed))})
        else:
            self._follow["reason"] = "已在跟随 pid=%s（%s）" % (p.get("pid"), os.path.basename(self.root))

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
                            except Exception as _e_sil6:
                                _silent_alert("bt_dashboard.py:681", _e_sil6)
                                continue
                            sym, theme = rec.get("symbol"), rec.get("theme")
                            if sym and theme:
                                pairs.setdefault(sym, []).append((day, theme))
                except OSError as _e_sil7:
                    _silent_alert("bt_dashboard.py:686", _e_sil7)
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
    def _sqlite(self, path: str = "") -> sqlite3.Connection:
        conn = sqlite3.connect("file:%s?mode=ro" % quote(path or self.bars_db), uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 计价空间（2026-09-24）：见模块顶部 detect_price_space 的说明 ──────────
    def bars_for_space(self, space: str) -> str:
        """该空间对应的日线库路径（复权库缺失时安全退回不复权）。"""
        if space == "adj" and os.path.exists(self.bars_adj_db):
            return self.bars_adj_db
        return self.bars_db

    def price_space(self, trades: list[dict], account: str = "", force: bool = False) -> str:
        """带缓存的空间判定（同一账户不重复扫；TTL 用 self.ttl 的 60 倍，切换臂时随账户键失效）。"""
        acct = account or self.account
        now = time.time()
        hit = self._space_cache.get(acct)
        if hit and not force and now - hit[0] < max(30.0, self.ttl * 60):
            return hit[1]["space"]
        info = detect_price_space(trades, self.bars_db, self.bars_adj_db)
        self._space_cache[acct] = (now, info)
        return info["space"]

    def price_space_info(self, account: str = "") -> dict:
        hit = self._space_cache.get(account or self.account)
        return hit[1] if hit else {}

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

    def closes(self, symbols: list[str], start: str, end: str, bars: str = "") -> dict[str, list[tuple[str, float]]]:
        """每个标的在 [start,end] 的收盘序列（升序），供净值重建做「最近 ≤ 当日」取值。

        `bars`：显式指定日线库（复权臂必须传 bars_adj.sqlite —— 成交价在前复权空间）。
        注：日历/个股K线视图仍走 `self.bars_db`（不复权），不在此处改动。
        """
        out: dict[str, list[tuple[str, float]]] = {}
        try:
            conn = self._sqlite(bars)
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
        except Exception as _e_sil2:
            print("[silent:bt_dashboard.py:804] %s: %s" % (type(_e_sil2).__name__, str(_e_sil2)[:110]), flush=True)
        return out

    # ── 父进程日志（心跳来源之二）──────────────────────────────────────────
    def _proc_busy(self, procs: list) -> list:
        """活进程"在干活"信号：/proc/<pid> 的 **CPU 时间或 IO 字节**相比上次采样有增长。

        为什么需要：年跑里 seed/pack 这类步进会连续几分钟**不写任何日志**（实测 bt_pack_mins
        在 D 状态跑 2~3 分钟），只按文件 mtime 判活会让页面误报"心跳停了"。
        CPU tick 对 I/O 阻塞型进程不增长，故同时看 rchar/wchar。
        """
        out = []
        now = time.time()
        for p in procs:
            pid = p.get("pid")
            if not pid:
                continue
            if p.get("kind") == "bt_dashboard":
                continue      # 看板自身永远在忙 → 会把真实停摆掩盖成"一直新鲜"
            try:
                with open("/proc/%d/stat" % pid, "r") as fh:
                    rest = fh.read().rsplit(") ", 1)[1].split()
                cpu = int(rest[11]) + int(rest[12])          # utime + stime
            except Exception as _e_sil8:
                _silent_alert("bt_dashboard.py:829", _e_sil8)
                continue
            io = 0
            try:
                with open("/proc/%d/io" % pid, "r") as fh:
                    for ln in fh:
                        if ln.startswith(("rchar:", "wchar:")):
                            io += int(ln.split(":")[1])
            except Exception as _e_sil3:
                print("[silent:bt_dashboard.py:836] %s: %s" % (type(_e_sil3).__name__, str(_e_sil3)[:110]), flush=True)
            prev = self._proc_seen.get(pid)
            if prev and (cpu > prev[0] or io > prev[1]):
                out.append(("proc:%d(%s)" % (pid, p.get("kind") or "?"), now))
            self._proc_seen[pid] = (cpu, io)
        return out

    def manifest_start_day(self) -> str:
        """运行清单里的**逻辑起点**（`run_manifest.json` 的 start_day；续跑时继承上一次）。

        为什么：看板原先按"进程启动时刻"过滤/定窗口，**每次续跑重启都会丢掉之前跑过的日子**
        （实测曲线只剩 1 个点、已实现 −209 vs PG +10,574）。身份应由持久化清单决定。
        读不到/格式不对返回 ""（调用方回退到日志头部口径）。
        """
        now = time.time()
        if self._manifest_at and now - self._manifest_at < 5.0:
            return self._manifest_start
        self._manifest_at = now
        _v = ""
        try:
            _mf = safe_read_json(os.path.join(REPO, ".dsh-tmp", "wolfbt", "run_manifest.json")) or {}
            _sd = str(_mf.get("start_day") or "")
            if re.match(r"^\d{8}$", _sd):
                _v = _sd
        except Exception:
            _v = ""
        self._manifest_start = _v
        return _v

    def _refresh_log_path(self) -> None:
        """把 self.log_path 钉到「当前跑的日志」（活的 bt_days 的 stdout），解析结果按 ttl 缓存。

        换日志文件时必须清掉解析缓存，否则页面会一直显示上一跑的进度。
        """
        now = time.time()
        if now - self._live_log_at < self.ttl:
            return
        self._live_log_at = now
        live = live_run_log(self.root)      # 按根归属，避免借到别的跑的日志
        if live and live != self.log_path:
            self.log_path = live
            self._log, self._log_sig = {}, None

    def log_info(self) -> dict:
        with self._lock:
            self._refresh_log_path()
            try:
                st = os.stat(self.log_path)
                sig = (self.log_path, st.st_mtime, st.st_size)
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
            except Exception as _e_sil4:
                print("[silent:bt_dashboard.py:898] %s: %s" % (type(_e_sil4).__name__, str(_e_sil4)[:110]), flush=True)
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
            except Exception as _e_sil5:
                print("[silent:bt_dashboard.py:916] %s: %s" % (type(_e_sil5).__name__, str(_e_sil5)[:110]), flush=True)
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
        procs = scan_procs(self.root)      # 只认本看板根目录的跑批进程（2026-09-18）
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
        # 逻辑起点优先（run_manifest.json）：续跑时继承上一次的 start_day，
        # 否则"按进程启动时刻过滤"会把之前跑过的日子全判成上一次的遗留
        # （实测：重启后曲线只剩 1 个点、已实现/触发全归零）。
        _mstart = self.manifest_start_day()
        if _mstart and re.match(r"^\d{8}$", _mstart):
            # 有效区间＝[逻辑起点, 本次已跑到的最后一天]：
            # 下界用清单（续跑继承），上界用主日志的 last_completed——
            # 否则会把"上一次跑"留下的后半段逐日产物（0122..0914）当成本次结果混进来。
            _lc = last_completed or ""
            rule = "manifest-start(%s)+last_completed(%s)" % (_mstart, _lc or "-")
            stale = [d for d in self._days
                     if d < _mstart or (bool(_lc) and d > _lc)]
        elif run_start:
            rule = "proc-start"
            stale = [d for d in self._days if (self._files[d]["_mtime"] or 0) < run_start - 5.0]
        elif self._days and completed_count and len(self._days) > completed_count + 3 and last_completed:
            rule = "log-last-completed"
            stale = [d for d in self._days if d > last_completed]

        sig = (sig, _mstart)
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
        self.autofollow()            # 自动跟随最新在跑的跑批（--no-auto 可关）
        self._refresh_log_path()
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
                except OSError as _e_sil6:
                    print("[silent:bt_dashboard.py:1153] %s: %s" % (type(_e_sil6).__name__, str(_e_sil6)[:110]), flush=True)
        activity.extend(self._proc_busy(act.get("procs") or []))
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
        # 窗口起点：清单逻辑起点优先（续跑继承 20260105），否则回退日志头部起点。
        # 否则续跑后窗口从续跑那天开始，之前已重放的日子（0105-0120）整段消失（实测曲线只剩 1 点）。
        _wstart = self.manifest_start_day() or run_start
        window = [d for d in cal if _wstart and run_end and _wstart <= d <= run_end]
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

        fresh = bool(seconds_since is not None and seconds_since <= 120)
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
        # ── 计价空间（2026-09-24）：复权臂（WOLF_ADJ_PRICE=1）成交价在前复权空间，
        #    净值/持仓必须用**复权收盘**计价，否则持仓市值被放大 f 倍
        #    （实例：drabt14 0128 虚影 +12,166 ⇒ 单日 +9.69% 实为 +4.95%）。
        self._curve_space = self.price_space(trades_pg)
        _sp_info = self.price_space_info() or {}
        self._curve_space_why = _sp_info.get("why", "")
        curve_bars = self.bars_for_space(self._curve_space)

        # ── 净值重建（口径见页脚/覆盖率面板）───────────────────────────────
        symbols = sorted({t["symbol"] for t in trades_pg} | {p["symbol"] for p in pg["positions"]})
        curve_start = (self.manifest_start_day() or run_start
                       or (active_days[0] if active_days else None))
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
            # 曲线只画到**本次跑真正重放到的最后一天**：否则会把"当前冻结的持仓"
            # 用后面的收盘价一路标记到窗口末尾 ⇒ 出现凭空的 +14% 与 -24% 回撤（用户实测"收益乱了"）。
            # 本次真正重放到哪天：优先主日志的 last_completed（续跑时它=当前进度），
            # 逐日产物里还有上一次跑留下的后半段，不能拿 max 当进度。
            _run_last = compact_day((self.log_info() or {}).get("last_completed"))
            if not _run_last:
                try:
                    _ad = (self.active_view() or {}).get("days") or []
                    _run_last = max(_ad) if _ad else None
                except Exception:
                    _run_last = None
            if _run_last:
                curve_end = min(curve_end, _run_last)
            cal_win = [d for d in window if curve_start <= d <= curve_end]
            closes = self.closes(symbols, curve_start, curve_end, bars=curve_bars)
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
                    # ── 对账必须**同日**（2026-09-18 修）──
                    # 曲线止于 `last_completed`，而 `paper_account_info.available_cash` 是**实时**的：
                    # 进行中那一天（如 2026-01-12）的成交已经落到 PG，却还没进曲线。
                    # 原先直接把两者相减 ⇒ 把"进行中日的现金流"误报成"费用模型口径差异"
                    # （用户实测 -31,517.15 元，实测正好等于当日净卖出扣费额；费用真实残差只有几十元）。
                    # 修法：按**同一截止日**对账——把「成交日 > curve_end」的现金流从账户现金里剔除，
                    # 并把该笔现金流单列出来（口径不同就不再混进误差）。
                    _flow_after = 0.0
                    _days_after = set()
                    for _t in trades_pg:
                        _d = compact_day(_t.get("trade_date"))
                        if not _d or _d <= (curve_end or ""):
                            continue
                        _amt = float(_t["price"]) * int(_t["volume"])
                        _flow_after += (-_amt * BUY_FEE) if _t["direction"] == "买入" else (_amt * SELL_FEE)
                        _days_after.add(_d)
                    if _flow_after:
                        account_block["cash_in_progress_flow"] = round(_flow_after, 2)
                        account_block["cash_in_progress_days"] = sorted(_days_after)
                        account_block["available_cash_same_cut"] = round(
                            float(account_block["available_cash"]) - _flow_after, 2)
                        recon_delta = round(last["cash"] - account_block["available_cash_same_cut"], 2)
                    else:
                        recon_delta = round(last["cash"] - account_block["available_cash"], 2)
                    account_block["cash_model"] = last["cash"]
                    account_block["cash_delta_vs_account"] = recon_delta
                account_block["as_of"] = last["date"]
                account_block["as_of_partial"] = bool(last.get("partial"))
                account_block["equity_basis"] = "重建口径（见页脚说明）"
                account_block["price_space"] = self._curve_space

        # ── 持仓 ────────────────────────────────────────────────────────────
        positions = []
        last_day_iso = iso_day(curve_end)
        close_map = {}
        if symbols and curve_start and curve_end:
            for s, seq in self.closes(symbols, curve_start, curve_end, bars=curve_bars).items():
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
                             "text": "心跳静止：最后活动 %.0f 秒前（%s），超过 120 秒阈值（seed/pack 等静默步进已计入进程 CPU/IO 活动）" % (seconds_since, src or "-")})
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
                             "text": "重建现金与账户可用现金差 %.2f 元（已按同一截止日对账；剩余差异属费用/舍入口径）"
                                     % recon_delta})
        if account_block.get("cash_in_progress_flow"):
            warnings.append({"level": "info",
                             "text": "进行中日 %s 现金流 %+.2f 元尚未计入曲线（已在同口径对账中剔除，非误差）"
                                     % ("、".join(account_block.get("cash_in_progress_days") or []),
                                        account_block["cash_in_progress_flow"])})
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
            "follow": dict(self._follow),
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
                "bars_adj_db": self.bars_adj_db,
                # 本账户成交价所在价格空间（raw=不复权 bars.sqlite / adj=前复权 bars_adj.sqlite）
                # 以及据此实际用于净值与持仓计价的日线库（2026-09-24 修复计价虚影）
                "price_space": self._curve_space,
                "price_space_why": self._curve_space_why,
                "curve_bars_db": self.bars_for_space(self._curve_space),
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
        # 计价空间（2026-09-24）：复权臂若用不复权收盘计价，持仓市值会被放大 f 倍（0128 虚影 +1.2 万）
        _sp = getattr(self, "_curve_space", "raw")
        parts.append("**计价空间**：本账户成交价判定为 `%s` ⇒ 净值与持仓用 %s 计价（%s）。"
                     % ("前复权(adj)" if _sp == "adj" else "不复权(raw)",
                        "`bars_adj.sqlite`" if _sp == "adj" else "`bars.sqlite`",
                        getattr(self, "_curve_space_why", "") or "无复权事件/样本不足"))
        parts.append("触发按 prod_*.json 的 id 首次出现日归属（这些文件是当日全量快照，已按 id 去重）。")
        parts.append("波浪层 %s/%s 天 ok；分钟缺口为**估算**（armed 标的 × 当日 5min 文件存在性）。"
                     % (wave.get("ok"), wave.get("total")))
        parts.append("沙箱复用：日志显示 seed 沙箱按日递增复用（cut 逐日前移），不是每天重建。")
        if stale_n:
            parts.append("⚠ 本次统计只用**当前这次跑批**的产物：判定规则 %s，已排除 %d 个上一次遗留的 prod_*.json。"
                         % (rule, stale_n))
        if recon_delta is not None:
            parts.append("重建现金 vs 账户可用现金差 %.2f 元（同一截止日口径）。" % recon_delta)
        # 注：进行中现金流明细已由 warnings 面板给出（本函数作用域没有 account_block，勿在此引用）
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

    # ── 当日盈亏构成（2026-09-22 用户："当下钻能看到成交，但看不到盈亏构成"）──────────
    #   严格恒等式（与看板自己的净值重建同一套口径）：
    #     Δequity = Σ 持仓浮动[昨持×（今收−昨收）] + Σ 买入贡献[（今收 − 含费买价）×量]
    #               + Σ 卖出贡献[（含费卖价 − 今收）×量]
    #   另附"引擎口径已实现"（Σ paper_trades.profit，相对**成本**）—— 它是用户逐笔看到的那个数，
    #   与上面三项**口径不同**（前者对成本、后者对昨收/今收），故单列并给出对账差。
    _TAKE_KINDS = ("wolf_profit_take_sell", "wolf_fib_target_sell", "high_sell", "wolf_confirm_sell")
    _STOP_KINDS = ("stop_loss", "custom_support_sell", "wolf_passive_stop_sell", "derisk_cut")

    def pnl_breakdown(self, d: str, pg: dict, act: dict) -> dict:
        """当日盈亏构成（按票 + 按腿型大类 + 对账）。`d` = YYYYMMDD。"""
        out: dict = {"ok": False, "note": ""}
        # ① 交易日历 → 前一交易日
        days = [x for x in (act.get("days") or []) if x <= d]
        _snap = self.snapshot()                          # 内部有缓存，重复调用不额外取数
        # ⚠️ `pnl` 在 **daily** 里（曲线 `curve` 只有 equity/cash/mv/realized/float，没有 pnl）
        row = next((c for c in (_snap.get("daily") or []) if c.get("day") == d), None)
        crow = next((c for c in (_snap.get("curve") or []) if c.get("day") == d), None)
        prev = days[-2] if len(days) >= 2 else None
        # 首日不再直接放弃：账户自初始现金起步 ⇒ 昨持恒为 0，浮动项必为 0，三项分解照样成立，
        # 且 curve 首日 pnl 就是相对初始资金的 Δ权益 ⇒ 仍可对账（只是"昨收"无从取得）。
        first_day = prev is None
        # ② 重放成交，得到"昨持"与"今持"
        vols_prev: dict = {}
        vols_eod: dict = {}
        for t in sorted(pg.get("trades") or [], key=lambda r: r.get("id") or 0):
            dd = compact_day(t.get("trade_date"))
            if not dd or dd > d:
                continue
            s_ = t["symbol"]
            v_ = int(t["volume"])
            if dd < d:
                vols_prev[s_] = vols_prev.get(s_, 0) + (v_ if t["direction"] == "买入" else -v_)
            vols_eod[s_] = vols_eod.get(s_, 0) + (v_ if t["direction"] == "买入" else -v_)
        today_tr = [t for t in (pg.get("trades") or []) if compact_day(t.get("trade_date")) == d]
        syms = sorted({t["symbol"] for t in today_tr} | {s_ for s_, v_ in vols_eod.items() if v_})
        # ③ 收盘价（昨收 / 今收）—— 同样必须按账户的计价空间取库
        _sp = self.price_space(pg.get("trades") or [])
        cl = self.closes(syms, prev or d, d, bars=self.bars_for_space(_sp)) if syms else {}
        c_prev: dict = {}
        c_now: dict = {}
        for s_ in syms:
            for dd2, px in (cl.get(s_) or []):
                if prev is not None and dd2 <= prev:
                    c_prev[s_] = px
                if dd2 <= d:
                    c_now[s_] = px
        # ④ 三项分解（按票）
        by_sym: dict = {}
        def _row(s_):
            return by_sym.setdefault(s_, {"symbol": s_, "name": self.name_for(s_),
                                          "float": 0.0, "buy": 0.0, "sell": 0.0,
                                          "realized": 0.0, "sells": 0, "buys": 0,
                                          "vol_prev": vols_prev.get(s_, 0), "vol_eod": vols_eod.get(s_, 0),
                                          "close_prev": c_prev.get(s_), "close": c_now.get(s_)})
        for s_ in syms:
            v0, v1 = vols_prev.get(s_, 0), vols_eod.get(s_, 0)
            p0, p1 = c_prev.get(s_), c_now.get(s_)
            if v0 and p0 and p1:
                _row(s_)["float"] += v0 * (p1 - p0)
            if v1 and not (p0 and p1):
                if first_day and not v0:
                    pass                     # 首日昨持 0，浮动项本就为 0，节级 note 已说明，不再逐行重复
                elif first_day:
                    _row(s_)["note"] = "首日却有昨持，缺昨收 ⇒ 浮动项未计入"
                else:
                    _row(s_)["note"] = "缺收盘价，该票浮动未计入"
        for t in today_tr:
            s_ = t["symbol"]; px = float(t["price"]); v_ = int(t["volume"]); p1 = c_now.get(s_)
            r_ = _row(s_)
            if t["direction"] == "买入":
                r_["buys"] += 1
                if p1:
                    r_["buy"] += (p1 - px * BUY_FEE) * v_
            else:
                r_["sells"] += 1
                r_["realized"] += float(t.get("profit") or 0.0)
                if p1:
                    r_["sell"] += (px * SELL_FEE - p1) * v_
        for r_ in by_sym.values():
            r_["total"] = round(r_["float"] + r_["buy"] + r_["sell"], 2)
            for k_ in ("float", "buy", "sell", "realized"):
                r_[k_] = round(r_[k_], 2)
        rows = sorted(by_sym.values(), key=lambda r_: -(abs(r_.get("total") or 0)))
        # 活库窗口保护：跑批每日 `--prod-reset-first` 会**先清空再重建**活库，读取正好落在
        # 该窗口时 paper_trades 为空（且被 TTL 缓存）⇒ 会得出"构成 0、对账差 −曲线值"的假象。
        # 此时不报构成，只回曲线值 + 说明，避免把一个瞬态当成"当日不赚不亏"。
        if not today_tr and not rows and (row or {}).get("pnl"):
            out.update({"partial": True, "day": iso_day(d), "prev_day": iso_day(prev) if prev else None,
                        "curve_pnl": (row or {}).get("pnl"), "curve_equity": (crow or {}).get("equity"),
                        "note": "活库当前查不到该日流水 —— 跑批每日 reset-first 会短暂清空并重建活库"
                                "（本次读取落在该窗口，或该日流水已被后续重建覆盖）。曲线盈亏取自逐日产物，"
                                "不受影响；稍后刷新即可看到构成。"})
            return out
        total = round(sum(r_.get("total") or 0 for r_ in rows), 2)
        realized = round(sum(r_.get("realized") or 0 for r_ in rows), 2)
        # ⑤ 按腿型大类汇总"引擎口径已实现"
        cls: dict = {"止盈类": 0.0, "破位/止损类": 0.0, "其他": 0.0}
        for t in today_tr:
            if t["direction"] == "买入":
                continue
            k_ = parse_kind(t.get("reason")) or ""
            p_ = float(t.get("profit") or 0.0)
            if k_ in self._TAKE_KINDS:
                cls["止盈类"] += p_
            elif k_ in self._STOP_KINDS:
                cls["破位/止损类"] += p_
            else:
                cls["其他"] += p_
        notes = ["「引擎口径已实现」= 逐笔 profit（相对成本），与上面三项口径不同（前者对成本、后者对昨收/今收），仅供对照"]
        if first_day:
            notes.insert(0, "首日：账户自初始资金起步，昨持恒为 0 ⇒ 浮动项为 0，曲线基准取初始资金")
        out.update({
            "ok": True,
            "day": iso_day(d),
            "prev_day": iso_day(prev) if prev else None,
            "first_day": first_day,
            "rows": rows,
            "total": total,
            "float_total": round(sum(r_.get("float") or 0 for r_ in rows), 2),
            "buy_total": round(sum(r_.get("buy") or 0 for r_ in rows), 2),
            "sell_total": round(sum(r_.get("sell") or 0 for r_ in rows), 2),
            "realized_engine": realized,           # Σ profit（相对成本，逐笔口径）
            "realized_by_class": {k_: round(v_, 2) for k_, v_ in cls.items()},
            "curve_pnl": (row or {}).get("pnl"),
            "curve_equity": (crow or {}).get("equity"),
            "check_diff": (round(total - float((row or {}).get("pnl") or 0.0), 2)
                           if row and row.get("pnl") is not None else None),
            "formula": "Δ权益 = 持仓浮动[昨持×(今收−昨收)] + 买入贡献[(今收−含费买价)×量] + 卖出贡献[(含费卖价−今收)×量]",
            "note": "；".join(notes),
        })
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
            "pnl_breakdown": self.pnl_breakdown(d, pg, act),
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
            if route == "/api/progress":
                return self._json(day_progress(self.store.root, getattr(self.store, "account", "") or ""))
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
            if route == "/api/runs":
                return self._json(runs_payload())
            if route == "/api/log":
                path = (qs.get("path") or [""])[0].strip()
                if not path:
                    return self._json({"error": "缺少 path 参数"}, 400)
                try:
                    return self._json(read_log_tail(path, int((qs.get("tail") or ["600"])[0] or 600)))
                except ValueError as ve:
                    return self._json({"error": str(ve)}, 400)
            if route == "/favicon.ico":
                return self._send(b"", "image/x-icon", 204)
            return self._json({"error": "not found", "path": parsed.path}, 404)
        except BrokenPipeError:
            return
        except Exception as exc:
            sys.stderr.write("[bt_dashboard] ERROR %s\n%s\n" % (exc, traceback.format_exc()))
            try:
                self._json({"error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}, 500)
            except Exception as _e_sil7:
                print("[silent:bt_dashboard.py:2049] %s: %s" % (type(_e_sil7).__name__, str(_e_sil7)[:110]), flush=True)

    def do_HEAD(self):  # noqa: N802
        return self.do_GET()


LOGDIR = os.path.join(REPO, ".dsh-tmp", "wolfbt", "logs")


def _allowed_log_path(p: str) -> str:
    """日志白名单（2026-09-18）：只允许读回测日志——logs/ 目录、或 data/_bt* 下的 .log。"""
    q = os.path.abspath(p if os.path.isabs(p) else os.path.join(REPO, p))
    ok = (q.startswith(LOGDIR + os.sep) or q.startswith(os.path.join(REPO, "data", "_bt")))
    if not ok or not q.endswith(".log") or not os.path.isfile(q):
        raise ValueError("路径不在允许范围内（只允许 .dsh-tmp/wolfbt/logs/*.log 与 data/_bt*/*.log）")
    return q


def read_log_tail(path: str, tail: int = 600) -> dict:
    q = _allowed_log_path(path)
    tail = max(1, min(int(tail or 600), 5000))
    try:
        with open(q, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as e:
        return {"path": q, "error": str(e)[:120], "lines": []}
    return {"path": q, "size": os.path.getsize(q), "mtime": os.path.getmtime(q),
            "total_lines": len(lines), "tail": tail,
            "truncated": len(lines) > tail, "lines": [ln.rstrip("\n") for ln in lines[-tail:]]}


def _run_roots() -> list:
    """data/_bt* 下已完成/落地的跑批根：天数、首末交易日、最新 mtime、逐日日志清单。"""
    out = []
    base = os.path.join(REPO, "data")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        if not name.startswith("_bt"):
            continue
        root = os.path.join(base, name)
        summ = os.path.join(root, "_summary")
        if not os.path.isdir(summ):
            continue
        days, logs, mt = [], [], 0.0
        try:
            for fn in os.listdir(summ):
                fp = os.path.join(summ, fn)
                try:
                    mt = max(mt, os.path.getmtime(fp))
                except OSError as _e_sil8:
                    print("[silent:bt_dashboard.py:2102] %s: %s" % (type(_e_sil8).__name__, str(_e_sil8)[:110]), flush=True)
                if fn.startswith("prod_") and fn.endswith(".json"):
                    days.append(fn[5:13])
                if fn.endswith(".log"):
                    logs.append({"name": fn, "path": fp, "mtime": os.path.getmtime(fp),
                                 "size": os.path.getsize(fp)})
        except OSError as _e_sil9:
            _silent_alert("bt_dashboard.py:2110", _e_sil9)
            continue
        days = sorted(d for d in days if d.isdigit())
        logs.sort(key=lambda r: r["mtime"], reverse=True)
        out.append({"kind": "root", "name": name, "root": root, "days": len(days),
                    "first_day": days[0] if days else None, "last_day": days[-1] if days else None,
                    "mtime": mt, "logs": logs[:120]})
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out



def runs_from_logs(max_age_s: float = 900.0) -> list:
    """**不依赖 /proc** 的跑批发现（沙箱禁止读别的进程 /proc ⇒ scan_procs 永远为空）。

    口径：`<LOGDIR>/size_run_<tag>.log` 若最近 max_age_s 秒内被写过 ⇒ 视为在跑的跑批，
    合成一条与 scan_procs 同形的记录（source=log 供页面标注）。取不到 ⇒ []。
    """
    out = []
    now = time.time()
    try:
        for fn in os.listdir(LOGDIR):
            if not (fn.startswith("size_run_") and fn.endswith(".log")):
                continue
            fp = os.path.join(LOGDIR, fn)
            try:
                mt = os.path.getmtime(fp)
            except OSError as _e_sil10:
                _silent_alert("bt_dashboard.py:2137", _e_sil10)
                continue
            if now - mt > max_age_s:
                continue
            tag = fn[len("size_run_"):-len(".log")]
            out.append({"pid": None, "kind": "bt_days", "role": "run", "source": "log",
                        "cmdline": "(由日志推断：%s)" % fn, "ppid": None,
                        "root": os.path.join(REPO, "data", "_bt_" + tag),
                        "account": "drab" + tag, "started_epoch": mt,
                        "stdout": fp, "follow_log": fp, "elapsed_s": None})
    except OSError:
        return []
    out.sort(key=lambda r: -(r.get("started_epoch") or 0))
    return out


def runs_payload() -> dict:
    """页面"进程 / 跑批"面板数据：在跑进程 + 已完成跑批根 + 日志文件。"""
    procs = scan_procs(None)
    if not procs:                      # 沙箱读不到 /proc ⇒ 由日志 mtime 推断（见 runs_from_logs）
        procs = runs_from_logs()
    newest_log = None
    try:
        cands = [os.path.join(LOGDIR, f) for f in os.listdir(LOGDIR) if f.endswith(".log")]
        if cands:
            newest_log = max(cands, key=os.path.getmtime)
    except OSError as _e_sil9:
        print("[silent:bt_dashboard.py:2162] %s: %s" % (type(_e_sil9).__name__, str(_e_sil9)[:110]), flush=True)
    for pr in procs:
        cmd = pr.get("cmdline") or ""
        m_root = re.search(r"--root\s+(\S+)", cmd)
        m_acc = re.search(r"--account\s+(\S+)", cmd)
        pr["root"] = m_root.group(1) if m_root else None
        pr["account"] = m_acc.group(1) if m_acc else None
        # 进程角色（2026-09-18 用户："明确看到在跑进程有两个" —— 面板把所有 bt_* 都列成"在跑"）：
        #   run=bt_days（真正的跑批）；child=bt_prod_run/bt_day_legs/... （某次跑批的当日子进程，不是第二跑）；
        #   dashboard=看板自身；service=as-of API 等配套。页面据此分组/置灰并显示父子关系。
        try:
            with open("/proc/%d/stat" % pr["pid"], "r") as _fh:
                pr["ppid"] = int(_fh.read().rsplit(") ", 1)[1].split()[1])
        except Exception:
            pr["ppid"] = None
        _k = str(pr.get("kind") or "")
        if _k == "bt_days":
            pr["role"] = "run"
        elif _k == "bt_dashboard":
            pr["role"] = "dashboard"
        elif _k.startswith("bt_job:") or _k == "bt_prod_run":
            pr["role"] = "child"
        else:
            pr["role"] = "service"
        pr["elapsed_s"] = (round(time.time() - pr["started_epoch"], 1)
                           if pr.get("started_epoch") else None)
        try:
            pr["stdout"] = os.path.realpath("/proc/%d/fd/1" % pr["pid"])
        except OSError:
            pr["stdout"] = None
        if not (pr["stdout"] or "").endswith(".log"):
            pr["stdout"] = newest_log        # stdout 是管道时退到最新日志（页面直接可看）
        # 2026-09-19：newest_log 可能就是看板自己的 dashboard.log ⇒ 页面钉错文件。
        #   这里按根 tag 再算一个 follow_log（排除看板日志），前端"跟随最新"优先用它。
        try:
            _st = float(pr.get("started_epoch") or 0)
            _fl = _tag_log_for_root(pr.get("root") or "", min_mtime=_st) if pr.get("root") else None
            pr["follow_log"] = _fl or (pr.get("stdout") if (pr.get("stdout") or "").endswith(".log")
                                       and not (pr.get("stdout") or "").endswith("dashboard.log") else None)
        except Exception:
            pr["follow_log"] = pr.get("stdout")
    logs = []
    try:
        for fn in os.listdir(LOGDIR):
            if not fn.endswith(".log"):
                continue
            fp = os.path.join(LOGDIR, fn)
            logs.append({"kind": "log", "name": fn, "path": fp,
                         "mtime": os.path.getmtime(fp), "size": os.path.getsize(fp)})
    except OSError as _e_sil10:
        print("[silent:bt_dashboard.py:2212] %s: %s" % (type(_e_sil10).__name__, str(_e_sil10)[:110]), flush=True)
    logs.sort(key=lambda r: r["mtime"], reverse=True)
    return {"running": procs, "roots": _run_roots(), "logs": logs[:80], "now": now_iso()}


def day_progress(root: str, account: str = "") -> dict:
    """**当日进度 + 事件时间线**（账本 §9.588 ✓）——「走到什么时刻、发生了什么事」✓

    返回 ✓：
      · `day`／`day_index`／`days_total`（进度条用 ✓）
      · `stage`（当前阶段 ✓）＋ `stages`（阶段清单，含 done/current ✓）
      · `events`（当日成交/触发，带时间 ✓）
      · `log_tail`（最近几行原始日志 ✓）
    """
    import glob as _g
    import json as _j
    import re as _re
    import time as _t
    out = {"ok": True, "day": None, "day_index": 0, "days_total": 0,
           "stage": "", "stages": [], "events": [], "log_tail": [], "log_mtime": None}
    try:
        day_dirs = sorted(os.path.basename(p) for p in _g.glob(os.path.join(root, "2026*")) if os.path.isdir(p))
        out["days_total"] = len(day_dirs)
        summ = os.path.join(root, "_summary")
        done = {os.path.basename(p)[5:13] for p in _g.glob(os.path.join(summ, "prod_2026*.json"))}
        pend = [d for d in day_dirs if d not in done]
        cur = pend[0] if pend else (day_dirs[-1] if day_dirs else None)
        out["day"] = cur
        out["day_index"] = (day_dirs.index(cur) + 1) if cur in day_dirs else 0
        # 阶段清单（按真实流水线顺序 ✓）
        STAGES = [("carry", "跨日结转"), ("low_logic", "low_logic"), ("ensure_mins", "补分钟档"),
                  ("pack", "打包分钟"), ("legs", "布腿/候选"), ("prod", "生产链(触发→网关→模拟盘)"),
                  ("summary", "写当日汇总")]
        out["stages"] = [{"key": k, "label": v, "state": "pending"} for k, v in STAGES]
        # 从日志尾部推当前阶段 ✓
        # ★ 账本 §9.611 ✓：**取最新鲜的那份日志** ✗ —— 天级日志只在阶段边界写 ✓
        #   只看它 ⇒ 会"看起来卡住"✗（用户 2026-10-05 因此以为卡住 ✓）
        #   ⇒ 在 天级日志 与 当日/隔日各阶段日志 里，挑 mtime 最新的那份 ✓
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _cands = [os.path.join(_base, ".dsh-tmp", "wolfbt", "logs", "size_run_t35d.log")]
        _summ = os.path.join(root, "_summary")
        for _pat in ("*_%s.log" % cur, "*_%s.log" % (day_dirs[day_dirs.index(cur) + 1]
                                                    if cur in day_dirs and day_dirs.index(cur) + 1 < len(day_dirs) else cur),
                     "switch_*.log", "confirm_*.log", "arm_*.log"):
            try:
                _cands += sorted(_g.glob(os.path.join(_summ, _pat)), key=os.path.getmtime)[-2:]
            except Exception:
                pass
        f, _best = None, -1.0
        for _c in _cands:
            try:
                if os.path.isfile(_c) and os.path.getmtime(_c) > _best:
                    f, _best = _c, os.path.getmtime(_c)
            except Exception:
                pass
        out["log_file"] = os.path.basename(f) if f else ""
        lines = []
        if f and os.path.isfile(f):
            out["log_mtime"] = os.path.getmtime(f)
            out["log_age_s"] = max(0, int(__import__("time").time() - out["log_mtime"]))
            with open(f, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-400:]
        tail = "".join(lines)
        # ★ 账本 §9.611 ✓：**阶段优先按"最新阶段日志的文件名"判定** ✓（永远正确 ✓）
        #   原因：天级日志只在阶段边界写 ✗，而各阶段日志名已直接标明阶段 ✓
        _bn = (out.get("log_file") or "")
        stage = ""
        _byname = (("minsfill_", "ensure_mins"), ("fetchmins_", "ensure_mins"), ("adj_mins_", "ensure_mins"),
                   ("pack_", "pack"), ("arm_", "legs"), ("confirm_", "legs"), ("switch_", "legs"),
                   ("prod_", "prod"))
        for _pre, _st in _byname:
            if _bn.startswith(_pre):
                stage = _st
                break
        # 兜底：再按日志内容找标记 ✓
        if not stage:
        for k in ("summary", "prod", "legs", "pack", "ensure_mins", "low_logic", "carry"):
            pat = {"carry": "跨日状态结转", "low_logic": "low_logic：", "ensure_mins": "ensure_mins",
                   "pack": "打包目录", "legs": "拉分钟名单", "prod": "prod_2026", "summary": "prod_2026"}[k]
            if pat in tail:
                stage = k
                break
        out["stage"] = stage
        idx = [x["key"] for x in out["stages"]].index(stage) if stage in [x["key"] for x in out["stages"]] else -1
        for i, st in enumerate(out["stages"]):
            st["state"] = "done" if (idx >= 0 and i < idx) else ("current" if i == idx else "pending")
        # 事件（当日成交/触发 ✓）
        sp = os.path.join(summ, "prod_%s.json" % cur) if cur else ""
        if sp and os.path.isfile(sp):
            try:
                j = _j.load(open(sp, encoding="utf-8"))
                for t in (j.get("trades") or []):
                    out["events"].append({"kind": "trade", "text": "%s %s %s@%s" % (
                        t.get("symbol"), t.get("direction"), t.get("volume"), t.get("price"))})
                for g in (j.get("triggers") or [])[:40]:
                    out["events"].append({"kind": "trigger", "text": "%s %s %s" % (
                        g.get("symbol"), g.get("event_type"), g.get("status"))})
            except Exception as _e_ev:
                print("[dashboard] day_progress 读 summary 失败: %s" % str(_e_ev)[:60], flush=True)
        out["log_tail"] = [ln.rstrip("\n")[:150] for ln in lines[-8:]]
        # ★ 当日成交（带真实时刻 ✓，从 PG 取 ⇒ 「什么时刻发生了什么事」✓）
        out["trades_pg"] = []
        if account and cur:
            try:
                import psycopg2 as _pg2
                _cn = _pg2.connect(os.environ.get("DATABASE_URL") or
                                   "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading",
                                   connect_timeout=6)
                _cn.set_session(readonly=True, autocommit=True)
                _c2 = _cn.cursor()
                # ★ 按**仿真时间**排序（账本 §9.592 ✓）：原先按 id（写入顺序 ✗）⇒ 列表时间乱序 ✗
                _c2.execute("SELECT created_at::text, symbol, direction, volume, price FROM paper_trades "
                            "WHERE account_id=%s ORDER BY created_at DESC, id DESC LIMIT 30", (account,))
                # 取"最新 30 条"后由前端按时间正序展示 ✓（SQL 用 DESC 才能拿到最新的 ✓）
                for _r in _c2.fetchall():
                    out["trades_pg"].append({"t": str(_r[0])[11:19], "d": str(_r[0])[:10], "sym": _r[1],
                                             "dir": _r[2], "vol": _r[3], "px": float(_r[4] or 0)})
                _cn.close()
            except Exception as _e_pg:
                print("[dashboard] day_progress 取成交失败: %s" % str(_e_pg)[:70], flush=True)
        # ★ 股票名称映射（账本 §9.593 ✓）：stock_pool ⇒ {6位代码: 名称} ✓
        out["names"] = {}
        try:
            _syms = {str(t.get("sym") or "") for t in (out.get("trades_pg") or [])}
            _syms |= {str(e.get("text") or "").split(" ")[0] for e in (out.get("events") or [])}
            _codes = sorted({x[-6:] for x in _syms if len(x) >= 6 and x[-6:].isdigit()})
            if _codes:
                import psycopg2 as _pg3
                _cn3 = _pg3.connect(os.environ.get("DATABASE_URL") or
                                    "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading",
                                    connect_timeout=6)
                _cn3.set_session(readonly=True, autocommit=True)
                _c3 = _cn3.cursor()
                _c3.execute("SELECT ts_code, name FROM stock_pool WHERE ts_code = ANY(%s) OR symbol = ANY(%s)",
                            (["%s.SH" % c for c in _codes] + ["%s.SZ" % c for c in _codes],
                             _codes))
                for _ts, _nm in _c3.fetchall():
                    out["names"][str(_ts).split(".")[0][-6:]] = str(_nm or "")
                _cn3.close()
        except Exception as _e_nm:
            print("[dashboard] day_progress 取名称失败: %s" % str(_e_nm)[:70], flush=True)
    except Exception as _e_dp:
        print("[dashboard] day_progress 失败: %s" % str(_e_dp)[:80], flush=True)
        out["ok"] = False
        out["error"] = str(_e_dp)[:120]
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="回测监控只读看板（标准库 + psycopg2）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--root", default=os.path.join(REPO, "data", "_bt_year"),
                    help="逐日产物根目录（默认 data/_bt_year）")
    ap.add_argument("--bars", default=os.path.join(REPO, "data", "_bt_full", "bars.sqlite"))
    ap.add_argument("--bars-adj", dest="bars_adj", default="",
                    help="前复权日线库（复权臂计价用；默认取 --bars 同目录的 bars_adj.sqlite）")
    ap.add_argument("--mins", default=os.path.join(REPO, "data", "_bt_full", "mins"))
    ap.add_argument("--log", default=os.path.join(REPO, ".dsh-tmp", "wolfbt", "logs", "year_prod.log"))
    ap.add_argument("--pg", default=os.getenv("BT_PG_URL", DEFAULT_PG))
    ap.add_argument("--account", default=os.getenv("BT_ACCOUNT", "stock"))
    ap.add_argument("--no-auto", dest="auto", action="store_false", default=True,
                    help="关闭自动跟随最新跑批（默认自动跟随）")
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
            except (ValueError, OSError) as _e_sil11:
                print("[silent:bt_dashboard.py:2257] %s: %s" % (type(_e_sil11).__name__, str(_e_sil11)[:110]), flush=True)
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
