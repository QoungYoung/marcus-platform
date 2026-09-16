# -*- coding: utf-8 -*-
"""bt_prod_report.py — **生产链年跑**的收益报告（本地 PG 模拟盘）。

与 `jobs/bt_report.py`（旧链 `bt_account` 内存账户）的区别：本脚本读的是
`bt_days --prod` → `bt_prod_run.py` 跑出来的**生产模拟盘**（本地 PG 的
`paper_account_info / paper_positions / paper_trades / t_triggers / t_conditions`）。

口径（必须随数字一起报）：
  · 账户：`stock` 模拟盘，起点 25 万（`--initial` 可改）；**只本机 PG**，与生产库无关。
  · 净值：**重建**——`cash = 初始资金 + Σ(buy: -(价×量+买费) / sell: +(价×量-卖费))`，
    再按当日收盘价市值计价（`data/_bt_full/bars.sqlite`，缺失日用最近一个 ≤ 当日的收盘）。
    费率取**生产口径** `resolve_commission()`（默认 `wen0.86_2026`：买 0.0396% / 卖 0.0896%）。
    ⚠️ 生产 `paper_daily_snapshot` 表**没有写入方**（引擎只建表），所以曲线是我们重建的，
    不是引擎逐日落的账；与 `paper_account_info.available_cash`（引擎真账）对账见输出末行。
  · 分腿型：`paper_trades.reason` 形如「条件命中自动执行（custom_prevlow）」→ 取括号里的
    trigger_kind；分主题：PG `stock_concept_map`（一股多概念 → 每个概念各计一次，汇总行标注）。

用法：
  .venv/bin/python jobs/bt_prod_report.py [--account stock] [--start 20260105] [--end 20260914]
      [--initial 250000] [--json out.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path[:0] = []
import bt_env  # noqa: E402

PG_URL = os.getenv("BT_PG_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")


def _q(sql: str, args=()) -> List[dict]:
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(PG_URL)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _fee_rates() -> Tuple[float, float, str]:
    try:
        # 生产费率在 backend/app/core/trading/backtest_paper.py，但它依赖 apps/paper-trading
        # 下的 `trade_direction`/`paper_engine` 等模块 → 两个路径都要加（否则退回常量）
        for _sub in ("backend", "apps/paper-trading", "core"):
            _p = os.path.join(bt_env.REPO, _sub)
            if os.path.isdir(_p) and _p not in sys.path:
                sys.path.insert(0, _p)
        from app.core.trading.backtest_paper import resolve_commission
        b, s, prof = resolve_commission()
        return float(b), float(s), str(prof)
    except Exception as e:
        print("[报告] ⚠️ 取生产费率失败(%s)，退回 wen0.86_2026 常量" % str(e)[:60], file=sys.stderr)
        return 0.000396, 0.000896, "wen0.86_2026(fallback)"


def _all_trade_days(bars_db: str, lo: str, hi: str) -> List[str]:
    """窗口内**全部交易日**（本地日线库的 distinct trade_date）——净值曲线必须逐日连续，
    不能只在"有成交的日子"取点（否则回撤/收益都会被少数几天代表）。"""
    if not (lo and hi):
        return []
    try:
        c = sqlite3.connect(bars_db)
        rows = [r[0] for r in c.execute(
            "SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (lo, hi))]
        c.close()
        return rows
    except Exception:
        return []


class Closes:
    """当日收盘价（本地日线库）。"""

    def __init__(self, bars_db: str):
        self.bars_db = bars_db
        self._cache: Dict[str, List[Tuple[str, float]]] = {}

    def series(self, symbol: str) -> List[Tuple[str, float]]:
        if symbol in self._cache:
            return self._cache[symbol]
        ts = symbol[2:8] + "." + symbol[:2] if symbol[:2] in ("SH", "SZ") else symbol
        rows: List[Tuple[str, float]] = []
        try:
            c = sqlite3.connect(self.bars_db)
            rows = [(r[0], float(r[1])) for r in c.execute(
                "SELECT trade_date, close FROM bars WHERE ts_code=? AND close IS NOT NULL ORDER BY trade_date",
                (ts,))]
            c.close()
        except Exception as e:
            print("[报告] ⚠️ %s 收盘取数失败：%s" % (symbol, str(e)[:60]), file=sys.stderr)
        self._cache[symbol] = rows
        return rows

    def at(self, symbol: str, day: str) -> Optional[float]:
        last = None
        for d, c in self.series(symbol):
            if d <= day:
                last = c
            else:
                break
        return last


def _norm_day(v) -> str:
    """任意日期表示 → `YYYYMMDD`。

    ⚠️ 2026-09-16 修复：`paper_trades.trade_date` 存的是 **ISO `YYYY-MM-DD`**，
    原来直接 `[:8]` 切片会得到 `2026-01-` 这种**残key** → 曲线只剩 1-2 个点、
    按日收盘的市值查询字符串比较全不成立（`20260106 <= "2026-02-"` 为 False）
    → 净值/收益率整块失真（实测把 -1.64% 这种数字算了出来）。
    """
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[:8] if len(d) >= 8 else ""


def _leg_kind(reason: str) -> str:
    m = re.search(r"（([A-Za-z0-9_]+)）", str(reason or ""))
    if m:
        return m.group(1)
    r = str(reason or "")
    for k in ("stop_loss", "high_sell", "custom_vwap_sell", "custom_support_sell", "custom_prevlow",
              "custom_m5dump", "wolf_zheng_t_buy", "wolf_defensive_t_reduce", "wolf_profit_take_sell"):
        if k in r:
            return k
    return (r[:24] or "unknown")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="stock")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--initial", type=float, default=250000.0)
    ap.add_argument("--bars-db", default=os.path.join(bt_env.DATA, "_bt_full", "bars.sqlite"))
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    acct = (_q("SELECT * FROM paper_account_info WHERE account_id=%s", (a.account,)) or [{}])[0]
    pos = _q("SELECT symbol, volume, avg_price, entry_date FROM paper_positions "
             "WHERE account_id=%s AND volume>0", (a.account,))
    tr = _q("SELECT id, symbol, direction, price, volume, amount, profit, reason, created_at, trade_date "
            "FROM paper_trades WHERE account_id=%s AND coalesce(voided,0)=0 ORDER BY id", (a.account,))
    trig = _q("SELECT status, count(*) c FROM t_triggers WHERE account_id=%s GROUP BY status", (a.account,))
    for t in tr:
        t["day"] = _norm_day(t.get("trade_date") or t.get("created_at"))

    buy_fee, sell_fee, prof = _fee_rates()
    init = float(a.initial)
    closes = Closes(a.bars_db)

    # ── 重建现金/持仓/净值曲线 ──
    cash = init
    holdings: Dict[str, List[Any]] = {}     # symbol -> [vol, cost]
    trade_days = sorted({t["day"] for t in tr if t.get("day")})
    lo = a.start or (trade_days[0] if trade_days else "")
    hi = a.end or (trade_days[-1] if trade_days else "")
    days = _all_trade_days(closes.bars_db, lo, hi) or trade_days
    if a.start:
        days = [d for d in days if d >= a.start]
    if a.end:
        days = [d for d in days if d <= a.end]
    by_day: Dict[str, List[dict]] = collections.defaultdict(list)
    for t in tr:
        by_day[t["day"]].append(t)
    curve = []
    for d in days:
        for t in by_day[d]:
            vol = int(t.get("volume") or 0)
            px = float(t.get("price") or 0)
            if str(t.get("direction") or "").lower() in ("buy", "买入"):
                cash -= px * vol * (1 + buy_fee)
                h = holdings.setdefault(t["symbol"], [0, 0.0])
                h[1] = (h[1] * h[0] + px * vol) / (h[0] + vol) if (h[0] + vol) else px
                h[0] += vol
            else:
                cash += px * vol * (1 - sell_fee)
                h = holdings.setdefault(t["symbol"], [0, 0.0])
                h[0] = max(h[0] - vol, 0)
        mv = 0.0
        for sym, (v, _c) in holdings.items():
            if v <= 0:
                continue
            px = closes.at(sym, d)
            if px:
                mv += px * v
        curve.append({"date": d, "equity": round(cash + mv, 2), "cash": round(cash, 2),
                      "mv": round(mv, 2)})

    # ── 统计 ──
    n_buy = sum(1 for t in tr if str(t.get("direction") or "").lower() in ("buy", "买入"))
    n_sell = len(tr) - n_buy
    n_trig = sum(int(x["c"]) for x in trig)
    n_exec = sum(int(x["c"]) for x in trig if x["status"] == "executed")
    by_kind: Dict[str, Dict[str, float]] = collections.defaultdict(lambda: {"n": 0, "pnl": 0.0, "amt": 0.0})
    for t in tr:
        k = _leg_kind(t.get("reason"))
        by_kind[k]["n"] += 1
        by_kind[k]["amt"] += float(t.get("amount") or 0)
        by_kind[k]["pnl"] += float(t.get("profit") or 0)
    by_sym: Dict[str, Dict[str, float]] = collections.defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for t in tr:
        by_sym[t["symbol"]]["n"] += 1
        by_sym[t["symbol"]]["pnl"] += float(t.get("profit") or 0)
    theme_of: Dict[str, List[str]] = {}
    try:
        for r in _q("SELECT ts_code, concept_name FROM stock_concept_map"):
            code6 = str(r["ts_code"]).split(".")[0]
            theme_of.setdefault(code6, []).append(str(r["concept_name"]))
    except Exception:
        pass
    by_theme: Dict[str, Dict[str, float]] = collections.defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for t in tr:
        for th in (theme_of.get(str(t["symbol"])[2:8]) or ["(未映射)"])[:3]:
            by_theme[th]["n"] += 1
            by_theme[th]["pnl"] += float(t.get("profit") or 0)

    last = curve[-1] if curve else {"equity": init, "cash": init, "mv": 0.0, "date": "-"}
    ret = (last["equity"] / init - 1) * 100 if init else 0.0
    peak, mdd = -1e18, 0.0
    for p in curve:
        peak = max(peak, p["equity"])
        if peak > 0:
            mdd = min(mdd, p["equity"] / peak - 1)
    realized = round(sum(float(t.get("profit") or 0) for t in tr), 2)

    print("═" * 78)
    print("生产链年跑 · 收益报告（本地 PG 模拟盘 account=%s）" % a.account)
    print("═" * 78)
    def _fmt(d):
        return "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 else (d or "-")
    print("窗口        : %s → %s（%d 个交易日；其中 %d 天有成交）"
          % (_fmt(days[0] if days else ""), _fmt(days[-1] if days else ""), len(days), len(trade_days)))
    print("起点/期末   : %.0f → %.2f  收益率 %+.2f%%" % (init, last["equity"], ret))
    print("期末构成    : 现金 %.2f + 持仓市值 %.2f" % (last["cash"], last["mv"]))
    print("最大回撤    : %.2f%%" % (mdd * 100))
    print("已实现盈亏  : %+.2f（引擎落库 profit 求和）" % realized)
    # 收益分解：总权益变动 = 已实现 + 浮动（+费用口径差）。正已实现 + 负总收益 = 未平仓浮亏 > 已实现。
    _float_pnl = round(last["equity"] - init - realized, 2)
    print("浮动盈亏    : %+.2f（未平仓按窗口末日收盘计价）" % _float_pnl)
    print("分解校验    : 初始 %.0f + 已实现 %+.2f + 浮动 %+.2f = %.2f（= 期末权益）"
          % (init, realized, _float_pnl, init + realized + _float_pnl))
    print("成交        : 买 %d 笔 / 卖 %d 笔；触发 %d 次、其中 executed %d（成交/触发 %.0f%%）"
          % (n_buy, n_sell, n_trig, n_exec, (100.0 * n_exec / n_trig) if n_trig else 0.0))
    print("费率口径    : %s（买 %.6f / 卖 %.6f）" % (prof, buy_fee, sell_fee))
    if by_kind:
        print("─" * 78)
        print("分腿型：")
        for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]["n"]):
            print("   %-26s %4d 笔  成交额 %12.0f  已实现 %+11.2f" % (k, v["n"], v["amt"], v["pnl"]))
    if by_theme:
        print("分主题（一股多概念各计一次，仅列前 12）：")
        for k, v in sorted(by_theme.items(), key=lambda kv: -kv[1]["n"])[:12]:
            print("   %-26s %4d 笔  已实现 %+11.2f" % (k, v["n"], v["pnl"]))
    if by_sym:
        print("分标的（前 12）：")
        for k, v in sorted(by_sym.items(), key=lambda kv: -kv[1]["pnl"])[:12]:
            print("   %-10s %4d 笔  已实现 %+11.2f" % (k, v["n"], v["pnl"]))
    # ── 触发→成交漏斗（2026-09-16 新增）：把"噪声触发"与"真实成交"分开量化 ──
    funnel = _q("SELECT event_type, status, count(*) c FROM t_triggers WHERE account_id=%s "
                "GROUP BY 1,2", (a.account,))
    arms = {r["trigger_kind"]: int(r["c"]) for r in _q(
        "SELECT trigger_kind, count(*) c FROM t_conditions WHERE account_id=%s GROUP BY 1", (a.account,))}
    per: Dict[str, Dict[str, int]] = {}
    for r in funnel:
        per.setdefault(r["event_type"], {})[r["status"]] = int(r["c"])
    if per:
        print("─" * 78)
        print("触发→成交漏斗（按腿型；arm=布过的条件数，trigger=触发总数，exec=成交，fill%=exec/trigger）")
        print("   %-24s %6s %9s %6s %6s %8s %8s" % ("腿型", "arm", "trigger", "exec", "blocked", "pending", "fill%"))
        for k in sorted(per, key=lambda x: -sum(per[x].values())):
            v = per[k]
            tot = sum(v.values())
            ex = v.get("executed", 0)
            print("   %-24s %6s %9d %6d %8d %8d %7.1f%%" % (
                k, arms.get(k, "-"), tot, ex, v.get("blocked", 0), v.get("pending", 0),
                100.0 * ex / tot if tot else 0.0))
        # 被拦原因归类（只列占比最高的几类，便于判断"噪声"还是"真拦截"）
        cls = collections.Counter()
        for r in _q("SELECT reason FROM t_triggers WHERE account_id=%s AND status='blocked'", (a.account,)):
            rr = str(r.get("reason") or "")
            if "仅底仓无T仓可卖" in rr:
                cls["(噪声) 仅底仓无T仓可卖"] += 1
            elif "[G6]" in rr:
                cls["[G6] 当日笔数上限"] += 1
            elif "趋势约束阻止卖出" in rr:
                cls["趋势约束阻止卖出"] += 1
            elif "决策对象准入拒绝" in rr:
                cls["决策对象准入拒绝"] += 1
            elif "资金不足" in rr:
                cls["资金不足"] += 1
            else:
                cls["其他"] += 1
        print("   被拦原因归类：" + "；".join("%s %d" % (k, n) for k, n in cls.most_common(6)))
    print("─" * 78)
    print("期末持仓 %d 只：%s" % (len(pos), ", ".join("%s×%s" % (p["symbol"], p["volume"]) for p in pos[:12]) or "无"))
    _eng = float(acct.get("available_cash") or 0) + float(acct.get("frozen_cash") or 0)
    print("引擎真账  : available_cash=%s + frozen=%s = %.2f（与重建现金 %.2f 对账，差异=费用口径）"
          % (acct.get("available_cash"), acct.get("frozen_cash"), _eng, last["cash"]))
    print("⚠️ 口径：净值曲线为**重建**（引擎不写 paper_daily_snapshot）；分主题用 stock_concept_map 映射。")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"account": a.account, "initial": init, "curve": curve,
                       "by_kind": {k: v for k, v in by_kind.items()},
                       "by_theme": dict(by_theme), "by_symbol": dict(by_sym),
                       "fee": {"buy": buy_fee, "sell": sell_fee, "profile": prof},
                       "trades": tr}, f, ensure_ascii=False, indent=1)
        print("明细已写 %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
