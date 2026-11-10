# -*- coding: utf-8 -*-
"""mainline_structure.py — 主线结构证据（只读 PG）：子链强弱 + 筹码成本。

为什么需要：
    11-04 他推仓位前看的是「哪个子链完成二段跳」「1200E 增量被套 -20%」，而交易 agent
    原来只拿到浪型/主线标签，没有子链强弱和筹码成本。这里把这两类证据算成**确定性字段**，
    由 trade_graph 注入 prompt；不参与门禁、不下单、不写任何状态。

纪律：
    · 只读 PG（mkt_bars_daily / stock_concept_map / index_daily）；任何异常返回空（fail-open）；
    · 不猜：覆铜板/电子布 在 stock_concept_map 里没有映射 ⇒ 显式输出 unknown；
    · 所有结论带 as_of，数据过期看得见。
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Dict, List, Optional

NL = chr(10)

# 子链 → 概念名（stock_concept_map）。覆铜板/电子布 暂无映射，显式 unknown。
SUBCHAINS = [
    ("半导体材料", ["半导体材料"]),
    ("半导体设备", ["半导体设备"]),
    ("先进封装", ["先进封装"]),
    ("PCB", ["PCB"]),
    ("PCB制造", ["印制电路板"]),
]
NO_MAPPING = ["覆铜板", "电子布"]


def _dsn() -> str:
    try:
        from core import dsn as _src
        return (_src.main() or "").strip()
    except Exception:
        return (os.environ.get("DATABASE_URL") or "").strip()


def _conn():
    import psycopg2
    d = _dsn()
    if not d:
        return None
    return psycopg2.connect(d, connect_timeout=5)


def norm_symbol(s: str) -> str:
    """SH600118 / 600118.SH / 600118 → 600118.SH（mkt_bars_daily 口径）。"""
    x = str(s or "").strip().upper().replace(" ", "")
    if not x:
        return ""
    if "." in x:
        return x
    if x[:2] in ("SH", "SZ", "BJ") and len(x) >= 8:
        return x[2:] + "." + x[:2]
    if len(x) == 6 and x.isdigit():
        return x + (".SH" if x[0] in ("5", "6", "9") else ".SZ")
    return x


def _latest_date(cur, as_of: Optional[str] = None) -> Optional[str]:
    """最新交易日；传 as_of(YYYYMMDD) 则取 <= 该日的最新一根（回放/验收用）。"""
    if as_of:
        cur.execute("select max(trade_date) from mkt_bars_daily where trade_date <= %s", (str(as_of)[:8],))
    else:
        cur.execute("select max(trade_date) from mkt_bars_daily")
    r = cur.fetchone()
    return str(r[0]) if r and r[0] else None


def _days_before(d8: str, days: int) -> str:
    """YYYYMMDD 往前推 N 个自然日（mkt_bars_daily.trade_date 是 varchar，不能和 date 直接比）。"""
    return (_dt.datetime.strptime(str(d8)[:8], "%Y%m%d") - _dt.timedelta(days=days)).strftime("%Y%m%d")


# ── 子链强弱 ────────────────────────────────────────────────
def subchain_strength(as_of: Optional[str] = None) -> List[Dict]:
    """每条子链：5/20 日等权收益、超指数 5 日、breadth(>MA20)、量比(5/20)。as_of 用于回放。"""
    conn = _conn()
    if conn is None:
        return []
    out: List[Dict] = []
    try:
        with conn, conn.cursor() as cur:
            dmax = _latest_date(cur, as_of)
            if not dmax:
                return []
            # 基准：上证 5 日
            cur.execute("select close from index_daily where ts_code='000001.SH' "
                        "and trade_date<=%s order by trade_date desc limit 21", (dmax,))
            idx = [float(r[0]) for r in cur.fetchall()][::-1]
            idx_ret5 = (idx[-1] / idx[-6] - 1) * 100 if len(idx) >= 6 else None

            for name, concepts in SUBCHAINS:
                cur.execute("select distinct ts_code from stock_concept_map where concept_name = any(%s)", (concepts,))
                syms = [r[0] for r in cur.fetchall()]
                if not syms:
                    out.append({"name": name, "unknown": "无成分"})
                    continue
                cur.execute(
                    "select ts_code, close, amount from mkt_bars_daily "
                    "where ts_code = any(%s) and trade_date <= %s and trade_date > %s "
                    "order by ts_code, trade_date", (syms, dmax, _days_before(dmax, 45)))
                rows = cur.fetchall()
                per: Dict[str, List] = {}
                for code, close, amt in rows:
                    per.setdefault(code, []).append((float(close), float(amt or 0)))
                r5s, r20s, above, amt5, amt20 = [], [], 0, 0.0, 0.0
                n = 0
                for code, arr in per.items():
                    if len(arr) < 21:
                        continue
                    c = [x[0] for x in arr]
                    a = [x[1] for x in arr]
                    n += 1
                    r5s.append((c[-1] / c[-6] - 1) * 100)
                    r20s.append((c[-1] / c[-21] - 1) * 100)
                    ma20 = sum(c[-20:]) / 20
                    if c[-1] > ma20:
                        above += 1
                    amt5 += sum(a[-5:]) / 5
                    amt20 += sum(a[-20:]) / 20
                if not n:
                    out.append({"name": name, "unknown": "无行情"})
                    continue
                avg5 = sum(r5s) / n
                out.append({
                    "name": name, "as_of": dmax, "members": n,
                    "ret5": round(avg5, 2),
                    "ret20": round(sum(r20s) / n, 2),
                    "excess5": (round(avg5 - idx_ret5, 2) if idx_ret5 is not None else None),
                    "breadth": round(above / n * 100),
                    "amt_ratio": (round(amt5 / amt20, 2) if amt20 > 0 else None),
                })
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def subchain_context(max_lines: int = 7, as_of: Optional[str] = None) -> str:
    rows = subchain_strength(as_of=as_of)
    if not rows:
        return ""
    as_of = next((r.get("as_of") for r in rows if r.get("as_of")), "")
    lines = ["## 子链强弱（PG，as_of %s）" % (as_of or "?")]
    for r in rows[:max_lines]:
        if r.get("unknown"):
            lines.append(f"- {r['name']}：unknown（{r['unknown']}，不猜）")
            continue
        ex = f"{r['excess5']:+.2f}%" if r.get("excess5") is not None else "?"
        ar = f"{r['amt_ratio']:.2f}" if r.get("amt_ratio") is not None else "?"
        lines.append(f"- {r['name']}：5日 {r['ret5']:+.2f}% ｜ 20日 {r['ret20']:+.2f}% ｜ "
                     f"超指数5日 {ex} ｜ breadth {r['breadth']}% ｜ 量比 {ar}（n={r['members']}）")
    if NO_MAPPING:
        lines.append("- " + "、".join(NO_MAPPING) + "：无概念映射（unknown，不猜）")
    return NL.join(lines) + NL + NL


# ── 筹码成本 ────────────────────────────────────────────────
def chip_cost(symbol: str, lookback: int = 60, as_of: Optional[str] = None) -> Dict:
    """成交额加权成本 + 现价上方筹码占比（被套比例）。as_of 用于回放。"""
    code = norm_symbol(symbol)
    if not code:
        return {}
    conn = _conn()
    if conn is None:
        return {}
    try:
        with conn, conn.cursor() as cur:
            if as_of:
                cur.execute("select trade_date, close, amount, pct_chg from mkt_bars_daily "
                            "where ts_code=%s and trade_date <= %s order by trade_date desc limit %s",
                            (code, str(as_of)[:8], lookback))
            else:
                cur.execute("select trade_date, close, amount, pct_chg from mkt_bars_daily "
                            "where ts_code=%s order by trade_date desc limit %s", (code, lookback))
            rows = cur.fetchall()
        if len(rows) < 5:
            return {}
        rows = rows[::-1]
        # 除权/拆股会让"加权成本"失真 → 单日 |涨跌|>30% 视为异常，直接标 unknown
        if any(abs(float(r[3] or 0)) > 30 for r in rows):
            return {"symbol": code, "unknown": "疑似除权/异常，成本不可用"}
        amts = [float(a or 0) for _, _, a, _ in rows]
        closes = [float(c) for _, c, _, _ in rows]
        total = sum(amts)
        if total <= 0:
            return {}
        vwap = sum(c * a for c, a in zip(closes, amts)) / total
        cur_close = closes[-1]
        trapped = sum(a for c, a in zip(closes, amts) if c > cur_close) / total
        return {
            "symbol": code, "as_of": str(rows[-1][0]), "days": len(rows),
            "vwap": round(vwap, 3), "close": round(cur_close, 3),
            "dist_pct": round((cur_close / vwap - 1) * 100, 2),
            "trapped_pct": round(trapped * 100),
        }
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def top_amount_symbols(concepts: List[str], limit: int = 3, as_of: Optional[str] = None) -> List[str]:
    """某概念里近 5 日成交额最大的 N 只（作为没有持仓/意图时的观察样本）。"""
    conn = _conn()
    if conn is None:
        return []
    try:
        with conn, conn.cursor() as cur:
            dmax = _latest_date(cur, as_of)
            if not dmax:
                return []
            cur.execute("select distinct ts_code from stock_concept_map where concept_name = any(%s)", (concepts,))
            syms = [r[0] for r in cur.fetchall()]
            if not syms:
                return []
            cur.execute("select ts_code, sum(amount) s from mkt_bars_daily "
                        "where ts_code = any(%s) and trade_date <= %s and trade_date > %s "
                        "group by 1 order by s desc limit %s",
                        (syms, dmax, _days_before(dmax, 15), limit))
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def chip_cost_context(symbols: List[str], cap: int = 8, as_of: Optional[str] = None) -> str:
    """给定标的（持仓 + P3 意图）→ 筹码成本块；空则用子链成交额 top3 兜底。"""
    codes: List[str] = []
    for s in symbols or []:
        c = norm_symbol(s)
        if c and c not in codes:
            codes.append(c)
    # 子链代表：即使有持仓/意图，也补上 3 条子链的成交额 top1 作为「反弹目标」观察样本
    # （他要看的是"套牢的钱在哪个方向"，不只是自己持仓）
    if len(codes) < 5:
        for concepts in (["半导体材料"], ["半导体设备"], ["PCB"]):
            for c in top_amount_symbols(concepts, 1, as_of=as_of):
                if c not in codes:
                    codes.append(c)
    if not codes:
        return ""
    rows = [r for r in (chip_cost(c, as_of=as_of) for c in codes[:cap]) if r]
    if not rows:
        return ""
    lines = ["## 筹码成本（成交额加权，近 60 日）"]
    for r in rows:
        if r.get("unknown"):
            lines.append(f"- {r['symbol']}：成本不可用（{r['unknown']}）")
            continue
        lines.append(f"- {r['symbol']}：加权成本 {r['vwap']} ｜ 现价 {r['close']} ｜ "
                     f"距成本 {r['dist_pct']:+.2f}% ｜ 上方筹码 {r['trapped_pct']}%（as_of {r['as_of']}）")
    return NL.join(lines) + NL + NL
