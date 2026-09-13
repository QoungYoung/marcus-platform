# -*- coding: utf-8 -*-
"""eval_leg_metrics.py — 阶段 0「换尺子」：分类型腿度量 + 生产实际离场口径。

═══════════════════════════════════════════════════════════════════════════════
为什么要它（docs/plan-exit-alignment.md §5 阶段 0）
  · 买点是中性的：254/253 触发条件 T+5 胜率 47% ≈ 同池同日随机买入 49%；
  · 兑现方式才是胜率来源：+3% 小止盈 → 53%；−3% 止损 → 39%；
  · **尺子错了**：拿"买入后持有 T+5 收盘"量做 T 腿得 47%，而生产**已实现卖出腿**
    （paper_trades：direction='卖出' and profit<>0）是 **63.6% / +2,828.5**。
  → 在错的尺子上做任何结论都无效，所以先换尺子，再按 §5 阶段 1 做规则变体。

三套样本（口径见 docs/leg-metrics-spec.md）
  A. **生产腿（主尺子）**：PG paper_trades（剔除 voided）→ 按 (account, symbol) FIFO 配对成腿；
     生产口径 = 该腿的已实现盈亏（DB profit，含费）；对照口径 = 持 T+5 / ±3% 规则模拟。
  B. **同主题同日基线**：同一主题的成分股（theme_universe），入场日收盘买入、持有同一窗口（等权），
     给绝对与超额两个口径。
  C. **旧尺子大样本（参照系）**：.dsh-tmp/wolfbt/bt_pit/trades.jsonl（254/253 **首次触发** n=428，
     由已删除的 gate 链重建）——**只用于**复现 47%/53%/39% 与三个开关的事件式复算，
     不作为新生产的基线（方向层已换成结构池）。

运行（**本地跑，不要在 512MB 的生产容器里跑**）
  .venv/bin/python jobs/eval_leg_metrics.py                 # 用缓存
  .venv/bin/python jobs/eval_leg_metrics.py --refresh       # 重拉 paper_trades / ETF 日线 / 指数
输出
  <cache>/eval_leg_metrics.json（默认 .dsh-tmp/wolfbt/legmetrics/）
  docs/leg-metrics-baseline.md（人读的基线表，提交进 git）

统计纪律（plan §9 L8）
  · n<100 只作探索，不得写进验收；
  · 重叠样本（同一时点多条腿）→ 报**块状 t**（按周分块），不只看日度 t；
  · 关键结论必须带"同池/同主题同日基线对照"与分段（H1/H2）。
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import json
import math
import os
import statistics as st
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(ROOT, os.environ.get("LEG_CACHE_DIR", ".dsh-tmp/wolfbt/legmetrics"))
REPLAY = os.path.join(ROOT, os.environ.get("LEG_REPLAY_DIR", ".dsh-tmp/wolfbt/bt_pit"))
REPORT_MD = os.path.join(ROOT, "docs", "leg-metrics-baseline.md")

ACCOUNTS = ("t", "stock")            # 策略账户；golden_pit = 黄金坑 DCA，单列不混入
LEG_TYPES = ("做T低吸", "低位埋伏", "确认加仓", "逃顶/减仓")

# ETF → 主题（我们的代理映射，用于给 ETF 腿找同主题成分基线；非他的口径）
ETF_THEME = {
    "515880.SH": "AI/算力/科技",   # 通信 ETF
    "515050.SH": "AI/算力/科技",   # 5G 通信 ETF
    "512930.SH": "AI/算力/科技",   # 人工智能 ETF
    "159732.SZ": "AI/算力/科技",   # 消费电子 ETF
    "588170.SH": "半导体/芯片",     # 科创芯片 ETF
    "159516.SZ": "半导体/芯片",     # 半导体设备 ETF
    "512480.SH": "半导体/芯片",     # 半导体 ETF
    "588200.SH": "半导体/芯片",     # 科创芯片 ETF
    "159995.SZ": "半导体/芯片",     # 芯片 ETF
    "588000.SH": None,            # 科创 50（宽基，无同主题口径）
    "159915.SZ": None,            # 创业板 ETF（宽基）
    "159949.SZ": None,            # 创业板 50（宽基）
}


# ────────────────────────────── 0. 小工具 ──────────────────────────────
def log(*a):
    print(*a, flush=True)


def f(x, nd=2):
    try:
        return round(float(x), nd)
    except Exception:
        return x


def _ts(s):
    """容错时间解析：'2026-08-25T10:17:23.719649' / '2026-08-25 13:15:39.889512' / '20260825'。"""
    s = str(s or "").strip().replace("T", " ")
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.strptime(s, "%Y%m%d")
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _d8s(s):
    """统一成 8 位交易日：'2026-08-25' → '20260825'（账本用横线、行情用 8 位，必须对齐）。"""
    s = str(s or "").strip().replace("-", "").replace("/", "")[:8]
    return s if s.isdigit() and len(s) == 8 else ""


def norm_symbol(s):
    """生产账本用 'SH603259' / 'SZ159915'，行情用 '603259.SH' / '159915.SZ' → 统一成后者。"""
    s = str(s or "").strip().upper()
    if not s:
        return s
    if "." in s:
        return s
    if len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ"):
        return s[2:] + "." + s[:2]
    return s


def stat(vals):
    """n / mean / median / 胜率 / 日度 t。"""
    v = [float(x) for x in vals if x is not None and x == x]
    if not v:
        return {"n": 0}
    m = sum(v) / len(v)
    sd = st.pstdev(v) if len(v) > 1 else 0.0
    return {"n": len(v), "mean": f(m), "median": f(st.median(v)),
            "win": f(sum(1 for x in v if x > 0) / len(v), 4),
            "t": f(m / (sd / math.sqrt(len(v))), 2) if sd > 0 else None,
            "min": f(min(v)), "max": f(max(v))}


def block_t(pairs, block="W"):
    """块状 t：pairs = [(date8, value)]；同周（或同月）内的重叠样本先取块内均值再算 t。

    重叠样本（同一时点多条腿）会把日度 t 虚高（plan §9 L8 的教训）→ 关键结论看块状 t。
    """
    if not pairs:
        return {"blocks": 0}
    buckets = collections.defaultdict(list)
    for d, v in pairs:
        if v is None:
            continue
        dt = _ts(d) if len(str(d)) >= 8 else None
        if dt is None:
            buckets[str(d)].append(float(v))
        elif block == "W":
            iso = dt.isocalendar()
            buckets["%04dW%02d" % (iso[0], iso[1])].append(float(v))
        else:
            buckets["%04d-%02d" % (dt.year, dt.month)].append(float(v))
    means = [sum(x) / len(x) for x in buckets.values() if x]
    if len(means) < 2:
        return {"blocks": len(means), "mean": f(means[0]) if means else None, "t": None}
    m = sum(means) / len(means)
    sd = st.pstdev(means)
    return {"blocks": len(means), "mean": f(m),
            "t": f(m / (sd / math.sqrt(len(means))), 2) if sd > 0 else None}


# ────────────────────────────── 1. 生产成交流水 ──────────────────────────────
def _pg_connect():
    """本地：进程内 SSH 隧道（.dsh-tmp/wolfbt/local_pg.py）；容器内：DATABASE_URL/LEG_PG_DSN。"""
    import psycopg2
    dsn_env = os.environ.get("LEG_PG_DSN")
    if dsn_env:
        return psycopg2.connect(dsn_env)
    helper = os.path.join(ROOT, ".dsh-tmp", "wolfbt")
    if os.path.isdir(helper):
        sys.path.insert(0, helper)
        from local_pg import ensure_tunnel, DSN  # type: ignore
        ensure_tunnel()
        return psycopg2.connect(**DSN)
    raise RuntimeError("没有可用的 PG 连接（LEG_PG_DSN 未设且无本地隧道助手）")


def fetch_paper_trades(refresh=False):
    """生产成交流水（含 t / stock / golden_pit 三账户）。缓存到 <cache>/paper_trades.json。"""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "paper_trades.json")
    if os.path.exists(p) and not refresh:
        d = json.load(open(p, encoding="utf-8"))
        d["meta"].setdefault("_accounts", {})
        return d["rows"], d["meta"]
    conn = _pg_connect()
    cur = conn.cursor()
    cur.execute("""SELECT id, orderid, symbol, direction, price, volume, amount, profit,
                          created_at, trade_date, COALESCE(voided,0), reason, account_id
                   FROM paper_trades ORDER BY id""")
    cols = ["id", "orderid", "symbol", "direction", "price", "volume", "amount", "profit",
            "created_at", "trade_date", "voided", "reason", "account_id"]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    accounts = {}
    try:
        cur.execute("SELECT account_id, initial_capital FROM paper_accounts")
        accounts = {r[0]: float(r[1] or 0) for r in cur.fetchall()}
    except Exception:
        pass
    conn.close()
    meta = {"_source": "PG paper_trades", "_pulled_at": datetime.now().isoformat(timespec="seconds"),
            "_rebuilt": True, "_n": len(rows), "_accounts": accounts}
    json.dump({"meta": meta, "rows": rows}, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return rows, meta


def _num(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def build_legs(rows):
    """把成交流水配对成腿。

    口径（写进 docs/leg-metrics-spec.md §2）：
      1. **剔除已撤销行**（voided=1）——撤销单不入账本（生产 void 时会回滚持仓）；
      2. 按 (account_id, symbol) 分账本，created_at 升序做 **FIFO**：卖出从最早的未平买入里扣；
      3. **一条卖出 = 一条腿**（entry 价 = 被扣买入的加权成本），跨 lot 时合成一条；
      4. 买入没有对应卖出 → 算 **未平腿**（open lot），只做标记，不进已实现口径。

    返回 (legs, open_lots, diag)。
    """
    rows = [dict(r) for r in rows if int(_num(r.get("voided"))) == 0]
    rows.sort(key=lambda r: (_ts(r.get("created_at")) or datetime(1970, 1, 1), r.get("id")))
    book = collections.defaultdict(collections.deque)
    legs, add_lots, diag = [], [], collections.Counter()
    for r in rows:
        acct = str(r.get("account_id") or "")
        sym = norm_symbol(r.get("symbol"))
        k = (acct, sym)
        d = str(r.get("direction") or "")
        qty = int(_num(r.get("volume")))
        px = _num(r.get("price"))
        if d in ("买入", "buy", "Buy"):
            had = len(book[k]) > 0
            if had and acct in ACCOUNTS:      # 「确认加仓」= 已有持仓上的加仓动作
                add_lots.append({"account": acct, "symbol": sym, "qty": qty, "px": px,
                                 "date": _d8s(r.get("trade_date")), "reason": r.get("reason") or ""})
            book[k].append({"qty": qty, "px": px, "dt": _ts(r.get("created_at")),
                            "date": _d8s(r.get("trade_date")), "reason": r.get("reason") or "",
                            "id": r.get("id"), "had_position": had})
            continue
        if d not in ("卖出", "sell", "Sell"):
            diag["unknown_direction"] += 1
            continue
        need, lots, cost_qty, cost_amt = qty, [], 0, 0.0
        pre_qty = sum(l["qty"] for l in book[k])      # 卖出前该标的的持仓
        while need > 0 and book[k]:
            lot = book[k][0]
            take = min(need, lot["qty"])
            lots.append(lot)
            cost_qty += take
            cost_amt += take * lot["px"]
            lot["qty"] -= take
            need -= take
            if lot["qty"] <= 0:
                book[k].popleft()
        entry_px = (cost_amt / cost_qty) if cost_qty else None
        leg = {
            "account": acct, "symbol": sym, "symbol_raw": str(r.get("symbol") or ""),
            "entry_px": entry_px, "exit_px": px, "qty": qty, "matched_qty": cost_qty,
            "unmatched_qty": need,
            "entry_date": lots[0]["date"] if lots else None,
            "entry_dt": lots[0]["dt"].isoformat(sep=" ") if lots and lots[0]["dt"] else None,
            "exit_date": _d8s(r.get("trade_date")),
            "exit_dt": (_ts(r.get("created_at")).isoformat(sep=" ") if _ts(r.get("created_at")) else None),
            "buy_reason": " | ".join(sorted({str(l["reason"]) for l in lots if l.get("reason")})),
            "sell_reason": r.get("reason") or "",
            "db_profit": _num(r.get("profit")),
            "sell_id": r.get("id"),
            "n_lots": len(lots),
            "had_position": bool(lots and lots[0].get("had_position")),
        }
        if leg["entry_px"] and leg["exit_px"]:
            leg["realized_pct"] = (leg["exit_px"] / leg["entry_px"] - 1.0) * 100.0
            cost = leg["entry_px"] * qty
            leg["db_pct"] = (leg["db_profit"] / cost * 100.0) if cost else None
        else:
            leg["realized_pct"] = leg["db_pct"] = None
        if leg["entry_dt"] and leg["exit_dt"]:
            m = (_ts(leg["exit_dt"]) - _ts(leg["entry_dt"])).total_seconds() / 60.0
            leg["hold_min"] = round(m, 1)
        # 腿型分类（口径见 spec §3）
        # 「卖出后是否仍有持仓」只看**卖出前已存在**的持仓（不能用之后新买的 lot 顶替）
        remain = pre_qty - cost_qty
        leg["remain_after"] = remain
        leg["leg_type"], leg["exit_kind"] = classify_leg(leg, remain, leg["had_position"])
        legs.append(leg)
        diag["legs_matched_full" if need == 0 else "legs_partial_no_entry"] += 1
    open_lots = []
    for (acct, sym), dq in book.items():
        for lot in dq:
            if lot["qty"] > 0:
                open_lots.append({"account": acct, "symbol": sym, "qty": lot["qty"],
                                  "entry_px": lot["px"], "entry_date": lot["date"],
                                  "entry_dt": lot["dt"].isoformat(sep=" ") if lot["dt"] else None,
                                  "reason": lot["reason"]})
    return legs, open_lots, add_lots, diag


BUY_LOW = ("low_buy", "254", "253", "低吸", "回补", "加仓")
SELL_TAKE = ("high_sell", "board_half", "high_sell_then_buy_back", "止盈", "兑现")
SELL_DEF = ("减仓", "defensive", "逃顶", "custom_prevhigh", "分批减")
SELL_STOP = ("stop_loss", "止损")


def classify_leg(leg, remain=0, had_position=False):
    """腿型 + 卖腿性质。规则确定性（同输入同输出），口径写在 spec §3。

    腿型按"这一笔动作在结构上是什么"分（plan §6 的四类）：
      · 逃顶/减仓 = **卖出后仍有持仓**的部分减仓（stock 账户；t 账户的 T 出算做 T 兑现）
      · 做T低吸   = t 账户（做 T 专用账户，plan §10.6）
      · 确认加仓 = 建仓**之前该标的已有持仓**（同一账户）的加仓买入
      · 低位埋伏 = stock 账户的首次建仓买入（含最终清仓的那一条腿）
    """
    sr = leg.get("sell_reason") or ""
    acct = leg.get("account")
    if any(t in sr for t in SELL_STOP):
        exit_kind = "止损"
    elif any(t in sr for t in SELL_DEF):
        exit_kind = "防守减仓"
    elif any(t in sr for t in SELL_TAKE):
        exit_kind = "兑现"
    else:
        exit_kind = "其他卖出"
    if acct == "golden_pit":
        return "黄金坑DCA", exit_kind
    if remain > 0 and acct == "stock":
        return "逃顶/减仓", exit_kind
    if acct == "t":
        return "做T低吸", exit_kind
    if acct == "stock":
        return ("确认加仓" if had_position else "低位埋伏"), exit_kind
    return "其他", exit_kind


# ────────────────────────────── 2. 行情 ──────────────────────────────
class Bars(object):
    """统一日线口径：{sym: [ (date8, open, high, low, close, amount), ... ]} 升序。

    来源优先级（都取本地缓存，避免读生产容器）：
      ① data/bars_2026.csv.gz                 （2026-01-05→09-11，股票 5,582 只）
      ② data/mkt_bars_local.db                （2024-11-01→2025-12-31，股票）
      ③ .dsh-tmp/wolfbt/bt_pit/daily/*.json   （2024-11→2026-09-10，股票；只有 close/low/amount）
      ④ <cache>/etf_daily.json                （ETF + 缺失股票，经 core/tushare_relay 拉取）
    """

    def __init__(self):
        self._m = {}
        self._csv = None
        self._etf = None

    def _load_csv(self):
        if self._csv is not None:
            return
        self._csv = {}
        p = os.path.join(DATA, "bars_2026.csv.gz")
        if not os.path.exists(p):
            log("[bars] 缺 data/bars_2026.csv.gz（2026 股票日线）")
            return
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if len(row) < 10 or not row[0] or row[1] == "trade_date":
                    continue
                sym, d = row[0], row[1]
                try:
                    o, h, l, c, amt = float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[9])
                except (ValueError, IndexError):
                    continue
                self._csv.setdefault(sym, []).append((d, o, h, l, c, amt))
        for v in self._csv.values():
            v.sort()

    def _load_sqlite(self, sym):
        import sqlite3
        p = os.path.join(DATA, "mkt_bars_local.db")
        if not os.path.exists(p):
            return []
        try:
            con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
            cur = con.execute("""SELECT trade_date, open, high, low, close, amount
                                 FROM mkt_bars_daily WHERE ts_code=? ORDER BY trade_date""", (sym,))
            out = [(str(r[0]), float(r[1] or 0), float(r[2] or 0), float(r[3] or 0),
                    float(r[4] or 0), float(r[5] or 0)) for r in cur.fetchall()]
            con.close()
            return out
        except Exception:
            return []

    def _load_btpit(self, sym):
        p = os.path.join(REPLAY, "daily", "%s.json" % sym[:6])
        if not os.path.exists(p):
            return []
        try:
            rows = json.load(open(p, encoding="utf-8"))
        except Exception:
            return []
        out = []
        for r in rows:
            if len(r) < 4:
                continue
            d, c, lo, amt = str(r[0]), float(r[1]), float(r[2]), float(r[3])
            out.append((d, lo, lo, lo, c, amt))     # 缺 open/high 字段 → 用 low 显式降级
        out.sort()
        return out

    def _load_etf(self, sym):
        if self._etf is None:
            p = os.path.join(CACHE, "etf_daily.json")
            self._etf = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
        v = self._etf.get(sym)
        return [tuple(x) for x in v] if v else []

    def fetch_missing(self, syms, start="20240101", end="20260911"):
        """慢通道（网络）：只对本地缺的标的拉一次，落 <cache>/etf_daily.json。"""
        os.makedirs(CACHE, exist_ok=True)
        p = os.path.join(CACHE, "etf_daily.json")
        cache = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
        sys.path.insert(0, ROOT)
        from core.tushare_relay import relay_query     # 统一取数入口（datahubco 优先）
        todo = [s for s in syms if not cache.get(s)]
        log("[bars] relay 拉取 %d 只：%s" % (len(todo), ",".join(todo[:8]) + ("..." if len(todo) > 8 else "")))
        for i, s in enumerate(todo):
            rows = []
            for fn in (("fund_daily", "daily", "fund_daily") if s[:1] in ("5", "1") else ("daily", "fund_daily")):
                try:
                    df = relay_query(fn, ts_code=s, start_date=start, end_date=end)
                    if df is None or len(df) == 0:
                        continue
                    rows = [[str(x["trade_date"]), float(x.get("open") or 0), float(x.get("high") or 0),
                             float(x.get("low") or 0), float(x.get("close") or 0),
                             float(x.get("amount") or 0)] for _, x in df.iterrows()]
                    rows.sort()
                    break
                except Exception as e:
                    log("   %s via %s 失败：%s" % (s, fn, str(e)[:60]))
            cache[s] = rows
            if i % 5 == 0:
                json.dump(cache, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(cache, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        self._etf = cache
        return cache

    def get(self, sym):
        sym = norm_symbol(sym)
        if sym in self._m:
            return self._m[sym]
        self._load_csv()
        rows = list((self._csv or {}).get(sym) or [])
        for extra in (self._load_sqlite(sym) if len(rows) < 30 else [],
                      self._load_btpit(sym) if len(rows) < 30 else [],
                      self._load_etf(sym) if len(rows) < 30 else []):
            if extra:
                have = {r[0] for r in rows}
                rows = sorted(rows + [r for r in extra if r[0] not in have])
        self._m[sym] = rows
        return rows

    def idx(self, sym, date8):
        days = [r[0] for r in self.get(sym)]
        return days.index(date8) if date8 in days else None


# ────────────────────────────── 3. 三把尺子 ──────────────────────────────
def snap_idx(bars, sym, date8, back=False):
    """交易日对齐：账本 `trade_date` 可能落在非交易日（周末补录，实测 2026-08-23/08-29）。

    默认取**之后第一个交易日**（成交实际发生在下一个交易日）；`back=True` 取**之前最后一个**（用于卖出/窗口末端）。
    """
    days = [r[0] for r in bars.get(sym)]
    if not days or not date8:
        return None
    if date8 in days:
        return days.index(date8)
    if back:
        prev = [i for i, d in enumerate(days) if d <= date8]
        return prev[-1] if prev else None
    nxt = [i for i, d in enumerate(days) if d >= date8]
    return nxt[0] if nxt else None


def ruler_hold(bars, sym, entry_date, entry_px, hold=5):
    """尺子 A：入场后**持有到 T+hold 收盘**（老尺子）。"""
    if not entry_px:
        return None
    i = snap_idx(bars, sym, entry_date)
    if i is None:
        return None
    rows = bars.get(sym)
    j = min(i + hold, len(rows) - 1)
    if j <= i:
        return None
    return (rows[j][4] / entry_px - 1.0) * 100.0


def ruler_rules(bars, sym, entry_date, entry_px, hold=5, tp=None, sl=None):
    """尺子 B：日内规则模拟（沿用既有 _bt_exit.py 口径：SL 用 low、TP 用 close）。

    与老脚本一致 → 可复现 47% / 53% / 39%（口径可比优先于口径完美；差异写在 spec §5）。
    """
    if not entry_px:
        return None
    i = snap_idx(bars, sym, entry_date)
    if i is None:
        return None
    rows = bars.get(sym)
    for k in range(1, hold + 1):
        if i + k >= len(rows):
            break
        c, lo = rows[i + k][4], rows[i + k][3]
        if sl is not None and (lo / entry_px - 1.0) * 100.0 <= sl:
            return float(sl)
        if tp is not None and (c / entry_px - 1.0) * 100.0 >= tp:
            return (c / entry_px - 1.0) * 100.0
    j = min(i + hold, len(rows) - 1)
    return (rows[j][4] / entry_px - 1.0) * 100.0


def _cal_days(leg):
    a, b = _ts(leg.get("entry_date")), _ts(leg.get("exit_date"))
    return (b - a).days if (a and b) else 0


def max_dd(bars, sym, entry_date, entry_px, hold=20):
    """持仓窗口内的最大回撤（用日线 low；≤ 0）。"""
    i = snap_idx(bars, sym, entry_date)
    if i is None or not entry_px:
        return None
    rows = bars.get(sym)
    seg = rows[i:i + hold + 1]
    if len(seg) < 2:
        return None
    return (min(r[3] for r in seg) / entry_px - 1.0) * 100.0


# ────────────────────────────── 4. 同主题同日基线 ──────────────────────────────
class Themes(object):
    def __init__(self):
        self.tu = {}
        p = os.path.join(REPLAY, "theme_universe.json")
        if os.path.exists(p):
            self.tu = json.load(open(p, encoding="utf-8"))
        self.rev = collections.defaultdict(set)
        for th, v in self.tu.items():
            for lst in (v.get("members") or {}).values():
                for s in lst:
                    self.rev[str(s)].add(th)

    def of(self, sym):
        sym = norm_symbol(sym)
        th = self.rev.get(sym)
        if th:
            return sorted(th)[0]
        return ETF_THEME.get(sym)

    def members(self, theme):
        v = self.tu.get(theme) or {}
        return sorted({norm_symbol(s) for lst in (v.get("members") or {}).values() for s in lst})


def baseline_same_theme(bars, themes, sym, entry_date, exit_date=None, hold=5):
    """同主题同日基线：同主题成分股在**同一入场日收盘**买入、持有到同一窗口（等权均值）。

    返回 (基线均值%, 成分数) 或 (None, 0)。宽基 ETF（无主题）返回 (None, 0) 并计入覆盖率。
    """
    th = themes.of(sym)
    if not th:
        return None, 0
    rets = []
    for s in themes.members(th):
        if s == sym:
            continue
        b = bars.get(s)
        if not b:
            continue
        i = snap_idx(bars, s, entry_date)
        if i is None:
            continue
        j = snap_idx(bars, s, exit_date, back=True) if exit_date else None
        j = i + hold if (j is None or j <= i) else j
        days = [r[0] for r in b]
        j = min(j, len(days) - 1)
        if j <= i:
            continue
        e, x = b[i][4], b[j][4]
        if e > 0:
            rets.append((x / e - 1.0) * 100.0)
    if not rets:
        return None, 0
    return sum(rets) / len(rets), len(rets)


# ────────────────────────────── 5. 生产腿指标 ──────────────────────────────
def production_tables(legs, bars, themes):
    """A 生产腿：分类型指标 + 口径对照（生产实际 vs 持 T+5 vs ±3% 规则）。"""
    out = {"by_type": {}, "ruler_compare": {}, "legs": []}
    # 口径：profit==0 的卖出腿是"未平/等量换手/手工平账"，**不计入胜率**（与 blueprint §5.2 一致）；
    # 但逐腿明细里保留并标 flat=true —— 其中若干笔从价格看是真亏损（DB 未计盈亏），见 spec §7。
    for l in legs:
        l["flat"] = (abs(_num(l.get("db_profit"))) < 1e-9)
    strat = [l for l in legs if l["account"] in ACCOUNTS and l["entry_px"] and l["realized_pct"] is not None
             and not l["flat"]]
    out["_flat_excluded"] = sum(1 for l in legs if l["account"] in ACCOUNTS and l.get("flat"))

    comp = []
    for l in strat:
        h5 = ruler_hold(bars, l["symbol"], l["entry_date"], l["entry_px"], 5)
        tp3 = ruler_rules(bars, l["symbol"], l["entry_date"], l["entry_px"], 5, tp=3)
        sl3 = ruler_rules(bars, l["symbol"], l["entry_date"], l["entry_px"], 5, sl=-3)
        base, nb = baseline_same_theme(bars, themes, l["symbol"], l["entry_date"], l.get("exit_date"))
        comp.append({"acct": l["account"], "type": l["leg_type"],
                     "prod": l["db_pct"] if l["db_pct"] is not None else l["realized_pct"],
                     "hold_t5": h5, "tp3": tp3, "sl3": sl3, "base": base, "n_base": nb})
        l["hold_t5_pct"] = h5
        l["base_pct"], l["base_n"] = base, nb

    def _rc(cs, label):
        return {
            "生产实际已实现（DB profit 含费）": stat([c["prod"] for c in cs]),
            "持T+5收盘（老尺子）": stat([c["hold_t5"] for c in cs]),
            "+3%止盈": stat([c["tp3"] for c in cs]),
            "-3%止损": stat([c["sl3"] for c in cs]),
            "同主题同日基线（持同窗口）": stat([c["base"] for c in cs]),
            "生产实际超额（vs 同主题）": stat([c["prod"] - c["base"] for c in cs if c["base"] is not None]),
            "持T+5超额（vs 同主题）": stat([c["hold_t5"] - c["base"] for c in cs if c["base"] is not None]),
            "_n_strat_legs": len(cs),
            "_base_cov": "%d/%d" % (sum(1 for c in cs if c["base"] is not None), len(cs)),
            "_label": label,
        }

    out["ruler_compare"] = _rc(comp, "t+stock 全部策略腿")
    out["ruler_compare_by_account"] = {a: _rc([c for c in comp if c["acct"] == a], "%s 账户" % a)
                                       for a in ACCOUNTS}
    out["ruler_compare_by_type"] = {t: _rc([c for c in comp if c["type"] == t], t) for t in LEG_TYPES}

    for t in LEG_TYPES:
        g = [l for l in strat if l["leg_type"] == t]
        if not g:
            out["by_type"][t] = {"n": 0}
            continue
        wins = [l["realized_pct"] for l in g if l["realized_pct"] > 0]
        loss = [l["realized_pct"] for l in g if l["realized_pct"] <= 0]
        rec = {
            "n": len(g), "win": f(len(wins) / len(g), 4),
            "mean_realized_pct": f(sum(l["realized_pct"] for l in g) / len(g)),
            "median_realized_pct": f(st.median([l["realized_pct"] for l in g])),
            "pl_ratio": f((sum(wins) / len(wins)) / abs(sum(loss) / len(loss)), 2) if wins and loss else None,
            "sum_profit": f(sum(l["db_profit"] for l in g)),
            "block_t": block_t([(l["exit_date"], l["realized_pct"]) for l in g]),
            "hold_min_median": f(st.median([l.get("hold_min") or 0 for l in g]), 1),
            "hold_cal_days_median": f(st.median([_cal_days(l) for l in g]), 1),
            "exc_vs_base": stat([l["realized_pct"] - l["base_pct"] for l in g if l.get("base_pct") is not None]),
            "base": stat([l["base_pct"] for l in g if l.get("base_pct") is not None]),
            "hold_t5": stat([l["hold_t5_pct"] for l in g]),
        }
        q = sorted([l["realized_pct"] for l in g])
        rec["pct_quantiles"] = {k: f(q[min(len(q) - 1, int(len(q) * p))]) for k, p in
                                (("p10", .1), ("p25", .25), ("p50", .5), ("p75", .75), ("p90", .9))}
        if t == "低位埋伏":
            rec["max_dd"] = stat([max_dd(bars, l["symbol"], l["entry_date"], l["entry_px"], 20) for l in g])
        if t == "逃顶/减仓":
            avoided = []
            for l in g:
                i = bars.idx(l["symbol"], l["exit_date"])
                if i is None:
                    continue
                rows = bars.get(l["symbol"])
                seg = rows[i + 1:i + 6]
                if not seg:
                    continue
                avoided.append((min(r[3] for r in seg) / l["exit_px"] - 1.0) * 100.0)
            rec["avoided_drop"] = stat(avoided)
        out["by_type"][t] = rec

    # ── 回合级（episode）：stock 账户每个标的从首次买入到清仓的完整回合 ──
    eps = []
    by_sym = collections.defaultdict(list)
    for l in legs:
        if l["account"] == "stock":
            by_sym[l["symbol"]].append(l)
    for sym, ls in sorted(by_sym.items()):
        ls = sorted(ls, key=lambda x: (x["exit_date"] or "", x["sell_id"] or 0))
        entry = min(ls, key=lambda x: (x["entry_date"] or "99999999"))
        last = max(ls, key=lambda x: (x["exit_date"] or ""))
        bars_s = bars.get(sym)
        days = [r[0] for r in bars_s]
        open_pos = sum(x["remain_after"] for x in ls[-1:]) > 0
        exit_px = last["exit_px"]
        if open_pos and days:
            exit_px = bars_s[-1][4]
        if not (entry.get("entry_px") and exit_px):
            continue
        ep = {"symbol": sym, "entry_date": entry["entry_date"], "entry_px": entry["entry_px"],
              "exit_date": last["exit_date"] if not open_pos else days[-1],
              "exit_px": exit_px, "open": bool(open_pos),
              "interval_pct": (exit_px / entry["entry_px"] - 1.0) * 100.0,
              "max_dd": max_dd(bars, sym, entry["entry_date"], entry["entry_px"],
                               max(1, (days.index(last["exit_date"]) - days.index(entry["entry_date"]))
                                   if (entry["entry_date"] in days and last["exit_date"] in days) else 20)),
              "cal_days": _cal_days({"entry_date": entry["entry_date"], "exit_date": last["exit_date"]}),
              "n_sells": len(ls), "profit_sum": sum(x["db_profit"] for x in ls)}
        base, nb = baseline_same_theme(bars, themes, sym, entry["entry_date"],
                                       last["exit_date"] if not open_pos else days[-1])
        ep["base_pct"], ep["base_n"] = base, nb
        ep["excess_pct"] = (ep["interval_pct"] - base) if base is not None else None
        eps.append(ep)
    if eps:
        out["episodes_stock"] = {
            "n": len(eps), "open": sum(1 for e in eps if e["open"]),
            "interval_pct": stat([e["interval_pct"] for e in eps]),
            "max_dd": stat([e["max_dd"] for e in eps]),
            "excess_pct": stat([e["excess_pct"] for e in eps]),
            "cal_days_median": f(st.median([e["cal_days"] for e in eps]), 1),
            "win": f(sum(1 for e in eps if e["interval_pct"] > 0) / len(eps), 4),
            "detail": eps,
        }

    keep = ("account", "symbol", "symbol_raw", "leg_type", "exit_kind", "entry_date", "exit_date", "entry_px",
            "exit_px", "qty", "realized_pct", "db_pct", "db_profit", "hold_min", "hold_t5_pct",
            "base_pct", "buy_reason", "sell_reason", "flat", "remain_after")
    out["legs"] = [{k: l.get(k) for k in keep} for l in strat]
    out["flat_legs"] = [{k: l.get(k) for k in keep} for l in legs
                        if l["account"] in ACCOUNTS and l.get("flat")]
    return out


def add_lot_metrics(add_lots, legs, bars, themes):
    """确认加仓腿（动作级）：延续率（加仓后到回合结束是否为正）+ 加仓点 5 日超额。

    加仓"回合结束"口径：用同一 (account, symbol) 最后一条腿的 exit_date；若之后无卖出，
    用该标的最后一根日线收盘（标 open=true）。
    """
    if not add_lots:
        return {"n": 0}
    last_exit = {}
    for l in legs:
        k = (l["account"], l["symbol"])
        if l.get("exit_date") and (k not in last_exit or l["exit_date"] > last_exit[k]):
            last_exit[k] = l["exit_date"]
    rows = []
    for a in add_lots:
        k = (a["account"], a["symbol"])
        end = last_exit.get(k)
        b = bars.get(a["symbol"])
        days = [r[0] for r in b]
        if not days or not a.get("date") or a["date"] not in days:
            continue
        i = days.index(a["date"])
        if end and end in days and days.index(end) > i:
            j = min(days.index(end), len(days) - 1)
        else:
            j = min(i + 5, len(days) - 1)
        if j <= i or not a["px"]:
            continue
        cont = (b[j][4] / a["px"] - 1.0) * 100.0
        h5 = (b[min(i + 5, len(days) - 1)][4] / a["px"] - 1.0) * 100.0
        base, nb = baseline_same_theme(bars, themes, a["symbol"], a["date"],
                                       days[j] if j < len(days) else None)
        rows.append({"account": a["account"], "symbol": a["symbol"], "date": a["date"], "add_px": a["px"],
                     "cont_pct": cont, "t5_pct": h5, "base_pct": base, "base_n": nb,
                     "excess_pct": (h5 - base) if base is not None else None,
                     "reason": a["reason"][:60]})
    cont = [r["cont_pct"] for r in rows]
    return {"n": len(rows),
            "cont_rate": f(sum(1 for x in cont if x > 0) / len(cont), 4) if cont else None,
            "cont_pct": stat(cont),
            "t5_pct": stat([r["t5_pct"] for r in rows]),
            "excess_pct": stat([r["excess_pct"] for r in rows]),
            "hold_cal_days_median": None,
            "detail": rows}


def t2_rows(prod):
    """四类腿基线表行（做T=腿级；低位埋伏=回合级；确认加仓=加仓动作级；逃顶/减仓=部分减仓腿级）。"""
    bt = prod.get("by_type") or {}
    rows = []
    for t in LEG_TYPES:
        r = bt.get(t) or {}
        if t == "低位埋伏":                      # 腿级会被 FIFO 吃掉 → 用回合级口径
            ep = prod.get("episodes_stock") or {}
            if ep.get("n"):
                rows.append([t + "（回合级）", ep.get("n"), ep.get("win"),
                             (ep.get("interval_pct") or {}).get("mean"),
                             (ep.get("interval_pct") or {}).get("median"), None,
                             (ep.get("detail") and f(sum(e["profit_sum"] for e in ep["detail"]))),
                             None, ep.get("cal_days_median"),
                             (ep.get("excess_pct") or {}).get("mean"),
                             block_t([(e["exit_date"], e["interval_pct"]) for e in ep["detail"]]).get("t")])
            else:
                rows.append([t + "（回合级）", 0, None, None, None, None, None, None, None, None, None])
            continue
        if t == "确认加仓":
            al = prod.get("add_lots") or {}
            if al.get("n"):
                rows.append([t + "（动作级）", al.get("n"), al.get("cont_rate"),
                             (al.get("cont_pct") or {}).get("mean"),
                             (al.get("cont_pct") or {}).get("median"), None, None, None, None,
                             (al.get("excess_pct") or {}).get("mean"),
                             block_t([(x["date"], x["cont_pct"]) for x in al["detail"]]).get("t")])
            else:
                rows.append([t + "（动作级）", 0, None, None, None, None, None, None, None, None, None])
            continue
        rows.append([t, r.get("n"), r.get("win"), r.get("mean_realized_pct"), r.get("median_realized_pct"),
                     r.get("pl_ratio"), r.get("sum_profit"), r.get("hold_min_median"),
                     r.get("hold_cal_days_median"), (r.get("exc_vs_base") or {}).get("mean"),
                     (r.get("block_t") or {}).get("t")])
    return rows


T2_HEAD = ["腿型", "n", "胜率", "均值%", "中位%", "盈亏比", "累计盈亏", "持有中位(分钟)",
           "持有中位(日历日)", "同主题超额%", "块状t"]


def t2_supplement(prod):
    """补充口径：低位埋伏的区间/回撤、逃顶的"避开的跌幅"、确认加仓的延续率。"""
    rows = []
    ep = prod.get("episodes_stock") or {}
    if ep.get("n"):
        rows.append(["低位埋伏（回合级）", ep.get("n"),
                     f"区间均值 {(ep.get('interval_pct') or {}).get('mean')}% / 中位 {(ep.get('interval_pct') or {}).get('median')}%",
                     f"最大回撤 均值 {(ep.get('max_dd') or {}).get('mean')}% / 中位 {(ep.get('max_dd') or {}).get('median')}%",
                     f"持有中位 {ep.get('cal_days_median')} 日历日（含未平 {ep.get('open')} 个回合）",
                     f"同主题超额 {(ep.get('excess_pct') or {}).get('mean')}%"])
    bt = prod.get("by_type") or {}
    ev = (bt.get("逃顶/减仓") or {}).get("avoided_drop") or {}
    if ev.get("n"):
        rows.append(["逃顶/减仓", ev.get("n"),
                     f"卖出后 5 日最低 vs 卖出价：均值 {ev.get('mean')}% / 中位 {ev.get('median')}%",
                     f"负值=卖出后确实更低（避开），胜率(跌) {(1 - (ev.get('win') or 0)):.2f}" if ev.get("win") is not None else "",
                     "对照=不减仓的反事实（用日线 low）", "口径：只减了一部分，余仓未计"])
    al = prod.get("add_lots") or {}
    if al.get("n"):
        rows.append(["确认加仓（动作级）", al.get("n"),
                     f"延续率 {al.get('cont_rate')}（加仓价→回合结束为正）",
                     f"加仓后 5 日 {(al.get('t5_pct') or {}).get('mean')}% / 中位 {(al.get('t5_pct') or {}).get('median')}%",
                     f"加仓点 5 日超额 {(al.get('excess_pct') or {}).get('mean')}%", "回合结束=该标的最后一条卖腿"])
    return rows


T2_SUPP_HEAD = ["腿型", "n", "口径A", "口径B", "口径C", "口径D"]


def cushion_metrics(legs, accounts, keep=0.5):
    """C1 利润垫（WOLF_CUSHION_CAP）：cap_mult = (本金 + keep×累计已实现) / 本金。

    口径：只**放宽**允许仓位上限 → 事件式（单笔）口径下**不改变任何一笔腿的收益**。
    这里给的是账户级"上限放宽倍数"路径，用来判断这个开关在样本期里可能有多大的量级。
    """
    out = {}
    for acct in ACCOUNTS:
        g = sorted([l for l in legs if l["account"] == acct and not l.get("flat")],
                   key=lambda l: (l.get("exit_date") or "", l.get("sell_id") or 0))
        principal = float(accounts.get(acct) or 0)
        if not g or principal <= 0:
            out[acct] = {"n": 0, "principal": principal}
            continue
        cum, path = 0.0, []
        for l in g:
            cum += float(l.get("db_profit") or 0)
            path.append({"date": l.get("exit_date"), "realized_cum": round(cum, 2),
                         "cap_mult": round(1 + keep * cum / principal, 5)})
        out[acct] = {"n": len(g), "principal": principal, "realized_total": round(cum, 2),
                     "keep": keep, "cap_mult_final": path[-1]["cap_mult"],
                     "cap_mult_max": max(p["cap_mult"] for p in path),
                     "cap_mult_min": min(p["cap_mult"] for p in path),
                     "path": path[-5:],
                     "note": "只放宽上限；单笔口径无影响 → 增量只能在账户层体现，且需 cap 真正触顶"}
    return out


# ────────────────────────────── 6. 旧尺子大样本（428 触发腿） ──────────────────────────────
def load_replay_legs():
    p = os.path.join(REPLAY, "trades.jsonl")
    if not os.path.exists(p):
        return []
    tr = [json.loads(l) for l in open(p, encoding="utf-8")]
    first = {}
    for t in tr:
        k = (t["arm_date"], t["ts_code"])
        if k not in first or t["time"] < first[k]["time"]:
            first[k] = t
    return list(first.values())


class ReplayBars(object):
    """bt_pit/daily/*.json：(date, close, low, amount)；缺 open/high → 用 close/low 显式降级。"""

    def __init__(self):
        self._m = {}

    def get(self, sym):
        if sym not in self._m:
            p = os.path.join(REPLAY, "daily", "%s.json" % sym[:6])
            try:
                self._m[sym] = sorted((str(r[0]), float(r[1]), float(r[1]), float(r[2]), float(r[1]), float(r[3]))
                                      for r in json.load(open(p, encoding="utf-8")))
            except Exception:
                self._m[sym] = []
        return self._m[sym]

    def idx(self, sym, date8):
        days = [r[0] for r in self.get(sym)]
        return days.index(date8) if date8 in days else None


def replay_ruler_table(legs, base):
    """复现老尺子的 47% / 53% / 39%。"""
    rules = [("T+5收盘", {}), ("+3%止盈", {"tp": 3}), ("+5%止盈", {"tp": 5}),
             ("-3%止损", {"sl": -3}), ("+3%/-3%", {"tp": 3, "sl": -3}), ("+5%/-3%", {"tp": 5, "sl": -3})]
    out = {}
    for nm, cfg in rules:
        vals, pairs = [], []
        for t in legs:
            v = ruler_rules(base, t["ts_code"], t["arm_date"], t["entry_px"], 5, **cfg)
            if v is None:
                continue
            vals.append(v)
            pairs.append((t["arm_date"], v))
        rec = dict(stat(vals), block_t=block_t(pairs))
        _days = sorted(t["arm_date"] for t in legs)
        mid = _days[len(_days) // 2]
        rec["H1"] = dict(stat([v for t, v in zip(legs, vals) if t["arm_date"] <= mid]))
        rec["H2"] = dict(stat([v for t, v in zip(legs, vals) if t["arm_date"] > mid]))
        rec["_split_at"] = mid
        out[nm] = rec
    return out


def index_daily(refresh=False):
    """指数日线（上证）→ 供"顶部阶段"与周末避险复算。缓存 <cache>/index_daily.json。"""
    sym = "000001.SH"
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "index_daily.json")
    cache = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    if sym in cache and not refresh:
        return cache[sym]
    sys.path.insert(0, ROOT)
    from core.tushare_relay import relay_query
    df = relay_query("index_daily", ts_code=sym, start_date="20250101", end_date="20260911")
    rows = sorted([[str(r["trade_date"]), float(r["close"])] for _, r in df.iterrows()])
    cache[sym] = rows
    json.dump(cache, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return rows


def boll_at(closes, i, n=20, k=2.0):
    if i < n:
        return None
    win = closes[i - n:i]
    mid = sum(win) / n
    sd = (sum((x - mid) ** 2 for x in win) / n) ** 0.5
    return (mid, mid + k * sd) if sd > 0 else None


def switch_replay(legs, base, idx_sh):
    """三个既有开关的**事件式**复算（样本=428 触发腿；口径=规则模拟，非生产已实现）。

    ① WOLF_BOLL_MID_EXIT  ：顶部阶段（上证 120 日区间分位≥0.8）∧ 放量 ∧ 收盘跌破 BOLL 中轨 ∧ 浮盈>0 → 全止盈
    ② WOLF_WEEKEND_HEDGE  ：周末前最后交易日仍"缩量 ∧ 未拉升" → 减半，下一交易日收盘拿回
    ③ WOLF_CUSHION_CAP    ：只放宽持仓上限 → 事件式口径下**不改变单笔收益**（见生产腿账户核验）
    """
    sh = {d: c for d, c in idx_sh}
    shd = sorted(sh)
    topq = {}
    for i, d in enumerate(shd):
        if i < 120:
            continue
        seg = [sh[x] for x in shd[i - 119:i + 1]]
        lo, hi = min(seg), max(seg)
        topq[d] = (sh[d] - lo) / (hi - lo) if hi > lo else 0.0

    def vstat(pairs, rule, fired=None):
        rec = dict(stat([v for _, v in pairs]), block_t=block_t(pairs), rule=rule)
        _d = sorted(d for d, _ in pairs)
        if _d:
            mid = _d[len(_d) // 2]
            rec["H1"] = stat([v for d, v in pairs if d <= mid])
            rec["H2"] = stat([v for d, v in pairs if d > mid])
        if fired is not None:
            rec["fired"] = fired
        return rec

    res = {}
    # 基线
    res["base"] = vstat([(t["arm_date"], ruler_rules(base, t["ts_code"], t["arm_date"], t["entry_px"], 5))
                         for t in legs], "254 触发买入 → 持有到 T+5 收盘")
    # ① 中轨全止盈
    v, fired = [], 0
    for t in legs:
        rows = base.get(t["ts_code"])
        days = [r[0] for r in rows]
        if t["arm_date"] not in days:
            continue
        i0 = days.index(t["arm_date"])
        closes = [r[4] for r in rows]
        e = t["entry_px"]
        ret = None
        for j in range(i0 + 1, min(i0 + 6, len(rows))):
            bo = boll_at(closes, j)
            if not bo:
                continue
            mid, _up = bo
            prev_amt = rows[j - 1][5]
            if (topq.get(rows[j][0], 0) >= 0.8 and closes[j] < mid and prev_amt > 0
                    and rows[j][5] >= prev_amt and closes[j] > e):
                ret = (closes[j] / e - 1.0) * 100.0
                fired += 1
                break
        if ret is None:
            j = min(i0 + 5, len(rows) - 1)
            ret = (rows[j][4] / e - 1.0) * 100.0
        v.append((t["arm_date"], ret))
    res["boll_mid_exit"] = vstat(v, "顶部阶段 ∧ 放量 ∧ 收盘破中轨 ∧ 浮盈>0 → 全止盈", fired)

    # ② 周末避险（减半 → 下一交易日收盘拿回；未计成本）
    v2, fired2 = [], 0
    for t in legs:
        rows = base.get(t["ts_code"])
        days = [r[0] for r in rows]
        if t["arm_date"] not in days:
            continue
        i0 = days.index(t["arm_date"])
        e = t["entry_px"]
        j5 = min(i0 + 5, len(rows) - 1)
        ret = (rows[j5][4] / e - 1.0) * 100.0
        for j in range(i0 + 1, j5):
            dt = _ts(rows[j][0])
            if not dt or dt.weekday() != 4:
                continue
            seg = rows[max(0, j - 5):j]
            amt_prev = (sum(r[5] for r in seg) / len(seg)) if seg else 0
            shrink = amt_prev > 0 and rows[j][5] < amt_prev
            pct = (rows[j][4] / rows[j - 1][4] - 1.0) * 100.0
            if shrink and pct < 0.30:
                pm = rows[j + 1][4]
                ret = (0.5 * (rows[j][4] / e) + 0.5 * (rows[j5][4] / pm) - 1.0) * 100.0
                fired2 += 1
            break
        v2.append((t["arm_date"], ret))
    res["weekend_hedge"] = vstat(v2, "周五缩量∧未拉升 → 收盘减半、下周一收盘拿回", fired2)

    res["cushion_cap"] = {"note": "只放宽持仓上限 → 单笔口径无影响；账户层面核验见 spec §6"}
    return res


# ────────────────────────────── 7. 主流程 ──────────────────────────────
def md_table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="重新拉 paper_trades / ETF 日线 / 指数")
    ap.add_argument("--no-report", action="store_true", help="不写 docs/leg-metrics-baseline.md")
    args = ap.parse_args()

    os.makedirs(CACHE, exist_ok=True)
    rows, meta = fetch_paper_trades(refresh=args.refresh)
    legs, opens, add_lots, diag = build_legs(rows)
    bars, themes = Bars(), Themes()

    need = sorted({norm_symbol(l["symbol"]) for l in legs if l["account"] in ACCOUNTS} |
                  {norm_symbol(l["symbol"]) for l in opens if l["account"] in ACCOUNTS})
    missing = [s for s in need if len(bars.get(s)) < 30]
    if missing:
        bars.fetch_missing(missing)

    prod = production_tables(legs, bars, themes)
    prod["open_lots"] = opens
    prod["diag"] = dict(diag)
    prod["add_lots"] = add_lot_metrics(add_lots, legs, bars, themes)
    prod["cushion"] = cushion_metrics(legs, meta.get("_accounts") or {})

    log("\n【T1】同一批生产腿：老尺子 vs 生产实际（paper_trades 拉取于 %s）" % meta.get("_pulled_at"))
    for key, rc in ([("t+stock", prod["ruler_compare"])] +
                    [(a, prod["ruler_compare_by_account"].get(a) or {}) for a in ACCOUNTS]):
        log("\n  —— %s（n=%s，同主题基线覆盖 %s）" % (key, rc.get("_n_strat_legs"), rc.get("_base_cov")))
        log(md_table(["口径", "n", "胜率", "均值%", "中位%", "t"],
                     [[k, v.get("n"), v.get("win"), v.get("mean"), v.get("median"), v.get("t")]
                      for k, v in rc.items() if not k.startswith("_")]))

    log("\n【T2】四类腿基线（生产纸面账本；n<100 只作探索）")
    log("  ⚠️ 另有 profit=0 的卖出腿（未平/等量换手/手工平账）n=%s，已剔除不计胜率（明细见 JSON flat_legs）"
        % prod.get("_flat_excluded"))
    log(md_table(T2_HEAD, t2_rows(prod)))
    log("\n  补充口径（blueprint §6 的分类型主指标）：")
    log(md_table(T2_SUPP_HEAD, t2_supplement(prod)))

    rlegs = load_replay_legs()
    rb = ReplayBars()
    rep = {}
    if rlegs:
        rep["ruler"] = replay_ruler_table(rlegs, rb)
        log("\n【T3】旧尺子复现（254/253 首次触发 n=%d；口径=日线规则模拟）" % len(rlegs))
        log(md_table(["规则", "n", "胜率", "均值%", "中位%", "日度t", "块状t"],
                     [[k, v.get("n"), v.get("win"), v.get("mean"), v.get("median"), v.get("t"),
                       (v.get("block_t") or {}).get("t")] for k, v in rep["ruler"].items()]))
        try:
            idx_sh = index_daily(refresh=args.refresh)
            rep["switches"] = switch_replay(rlegs, rb, idx_sh)
            log("\n【T4】三个既有开关的事件式复算（样本=428 触发腿，规则模拟口径）")
            bv = rep["switches"].get("base") or {}
            log("   %-16s n=%s 胜率=%s 均值=%s 中位=%s 块状t=%s" %
                ("base（持有T+5）", bv.get("n"), bv.get("win"), bv.get("mean"), bv.get("median"),
                 (bv.get("block_t") or {}).get("t")))
            for k in ("boll_mid_exit", "weekend_hedge"):
                v = rep["switches"].get(k) or {}
                log("   %-16s n=%s 胜率=%s(Δ%+.3f) 均值=%s(Δ%+.2f) 块状t=%s H1=%s/H2=%s 命中=%s"
                    % (k, v.get("n"), v.get("win"), (v.get("win") or 0) - (bv.get("win") or 0),
                       v.get("mean"), (v.get("mean") or 0) - (bv.get("mean") or 0),
                       (v.get("block_t") or {}).get("t"), (v.get("H1") or {}).get("mean"),
                       (v.get("H2") or {}).get("mean"), v.get("fired")))
            for a, v in (prod.get("cushion") or {}).items():
                log("   C1 利润垫[%s] 已实现=%s 本金=%s → cap_mult=%.5f（只放宽上限；单笔口径无影响）"
                    % (a, v.get("realized_total"), v.get("principal"), v.get("cap_mult_final") or 1.0))
        except Exception as e:
            log("   [T4] 指数取数失败：%s" % str(e)[:120])
            rep["switches"] = {"error": str(e)[:200]}
    else:
        log("\n【T3/T4】缺 %s/trades.jsonl（旧尺子样本），跳过" % REPLAY)

    out = {"meta": {**meta, "_cache": CACHE, "_replay": REPLAY,
                    "_spec": "docs/leg-metrics-spec.md",
                    "_note": "生产腿 n<100 → 探索性；旧尺子样本由已删除的 gate 链重建，仅作参照"},
           "production": prod, "replay": rep}
    op = os.path.join(CACHE, "eval_leg_metrics.json")
    json.dump(out, open(op, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("\n→ %s" % op)
    if not args.no_report:
        write_report(out)
        log("→ %s" % REPORT_MD)
    return 0


def write_report(out):
    prod = out["production"]
    rc = prod["ruler_compare"]
    L = ["# 腿度量基线表（阶段 0「换尺子」产物，自动生成）\n",
         "> 生成脚本 jobs/eval_leg_metrics.py；口径见 docs/leg-metrics-spec.md。",
         "> 数据源：%s（%s）。**生产腿样本 n=%s <100 → 只作探索，不得作为验收依据。**\n"
         % (out["meta"].get("_source"), out["meta"].get("_pulled_at"), rc.get("_n_strat_legs")),
         "## 1 口径差（同一批生产腿，两把尺子）\n"]
    for key, rcx in ([("全部策略腿（t+stock）", rc)] +
                     [("%s 账户" % a, (prod.get("ruler_compare_by_account") or {}).get(a) or {})
                      for a in ACCOUNTS]):
        L += ["\n### %s（n=%s，同主题基线覆盖 %s）\n" % (key, rcx.get("_n_strat_legs"), rcx.get("_base_cov")),
              md_table(["口径", "n", "胜率", "均值%", "中位%", "t"],
                       [[k, v.get("n"), v.get("win"), v.get("mean"), v.get("median"), v.get("t")]
                        for k, v in rcx.items() if not k.startswith("_")])]
    L += [
         "## 2 四类腿基线\n",
         md_table(T2_HEAD, t2_rows(prod)),
         "\n补充口径（blueprint §6 的分类型主指标）：\n",
         md_table(T2_SUPP_HEAD, t2_supplement(prod))]
    if out.get("replay", {}).get("ruler"):
        L += ["\n## 3 旧尺子复现（254/253 首次触发 n=428，仅作参照）\n",
              md_table(["规则", "n", "胜率", "均值%", "中位%", "日度t", "块状t", "H1均值%", "H2均值%"],
                       [[k, v.get("n"), v.get("win"), v.get("mean"), v.get("median"), v.get("t"),
                         (v.get("block_t") or {}).get("t"), (v.get("H1") or {}).get("mean"),
                         (v.get("H2") or {}).get("mean")]
                        for k, v in out["replay"]["ruler"].items()])]
    sw = out.get("replay", {}).get("switches")
    if sw and "error" not in sw:
        bv = sw.get("base") or {}
        rows4 = []
        for k in ("base", "boll_mid_exit", "weekend_hedge"):
            v = sw.get(k) or {}
            rows4.append([k, v.get("n"), v.get("win"),
                          None if k == "base" else f((v.get("win") or 0) - (bv.get("win") or 0), 4),
                          v.get("mean"),
                          None if k == "base" else f((v.get("mean") or 0) - (bv.get("mean") or 0)),
                          v.get("median"), (v.get("block_t") or {}).get("t"),
                          (v.get("H1") or {}).get("mean"), (v.get("H2") or {}).get("mean"),
                          v.get("fired")])
        L += ["\n## 4 三个既有开关的事件式复算（样本=428 触发腿，规则模拟）\n",
              md_table(["开关", "n", "胜率", "Δ胜率", "均值%", "Δ均值%", "中位%", "块状t",
                        "H1均值%", "H2均值%", "命中次数"], rows4)]
        cus = prod.get("cushion") or {}
        if cus:
            L += ["\n- C1 利润垫（WOLF_CUSHION_CAP）：只放宽持仓上限 → 事件式口径下**不改变单笔收益**。",
                  "  生产样本上的放宽倍数（cap_mult = 1 + 0.5×累计已实现/本金）：",
                  md_table(["账户", "已实现合计", "本金", "cap_mult"],
                           [[a, v.get("realized_total"), v.get("principal"), v.get("cap_mult_final")]
                            for a, v in cus.items() if v.get("n")]),
                  "  → 量级 <1%，且需 cap 真正触顶才有账户级增量；样本期内**无法给出改善/劣化结论**。\n"]
    open(REPORT_MD, "w", encoding="utf-8").write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
