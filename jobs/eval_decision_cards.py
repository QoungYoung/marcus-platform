#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""eval_decision_cards.py — 决策卡评分（错过 / 错做 / 做小）并落 PG。

为什么需要：
    决策卡只落库不评分，agent 就学不到"什么时候该不动、什么时候该动"。
    只罚做错、不罚错过 ⇒ 理性收敛结果是永远不动（11/04 就是例子）。

评分口径（先做可算的三类，不猜盘感）：
    missed          卡上 action=none/trim/exit，但当日结构成立（b_rebound cond_count>=2 或缩转放监控 fired）
                    ⇒ 记"错过"，并记录标的次日收益（反事实）
    false_positive  action=probe/push，但标的次日收益 <= -1.5%
    under_sized     action=probe/push，但 size_pct < 结构允许的上限（b_rebound position_pct，最低 10）
    ok              其余

标的解析：target 文本里的 6 位代码 → 否则按关键词映射（半导体/PCB/CPO/存储）→ 否则指数 000001.SH。

用法：
    python jobs/eval_decision_cards.py                 # 评今天
    python jobs/eval_decision_cards.py --date 20261110
    python jobs/eval_decision_cards.py --days 5         # 补评最近 5 天未评的卡
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

KEYMAP = [("半导体", "512480.SH"), ("材料", "512480.SH"), ("PCB", "002384.SZ"),
          ("覆铜", "002384.SZ"), ("CPO", "300308.SZ"), ("光模块", "300308.SZ"),
          ("存储", "603986.SH")]


def pick_symbol(target: str) -> str:
    t = str(target or "")
    m = re.search(r"\b(\d{6})\b", t)
    if m:
        code = m.group(1)
        return code + (".SH" if code[0] in ("5", "6", "9") else ".SZ")
    for k, sym in KEYMAP:
        if k in t:
            return sym
    return "000001.SH"


def classify(action: str, size_pct, cond_count: int, monitor_fired: bool,
             fwd_ret, expected_cap) -> tuple:
    """纯函数：→ (verdict, note)。fwd_ret/expected_cap 允许 None。"""
    a = str(action or "").strip().lower()
    cap = float(expected_cap) if expected_cap is not None else 10.0
    if a in ("none", "trim", "exit", ""):
        if int(cond_count or 0) >= 2 or monitor_fired:
            r = ("%+.2f%%" % fwd_ret) if fwd_ret is not None else "?"
            return "missed", "结构成立（cond=%s/3, 监控=%s）但 action=%s；次日 %s" % (
                cond_count, monitor_fired, a or "空", r)
        return "ok", "结构未成立，不动合理"
    if a in ("probe", "push"):
        if fwd_ret is not None and fwd_ret <= -1.5:
            return "false_positive", "动了但次日 %.2f%%" % fwd_ret
        if size_pct is not None and float(size_pct) + 1e-9 < cap:
            return "under_sized", "size=%s%% < 结构允许 %s%%" % (size_pct, cap)
        return "ok", "按结构出手，size=%s%%，次日 %s" % (
            size_pct, ("%.2f%%" % fwd_ret) if fwd_ret is not None else "?")
    return "ok", "未识别 action"


def _conn():
    import psycopg2
    from core import dsn
    return psycopg2.connect(dsn.main(), connect_timeout=5)


def _ensure(cur):
    cur.execute("""create table if not exists trade_decision_card_eval(
        card_id bigint primary key,
        trade_date date,
        action varchar(16),
        target_symbol varchar(12),
        expected_cap numeric,
        size_pct numeric,
        fwd_ret_pct numeric,
        verdict varchar(24),
        note text,
        evaluated_at timestamptz default now())""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=_dt.date.today().strftime("%Y%m%d"))
    ap.add_argument("--days", type=int, default=0, help="补评最近 N 天未评的卡")
    a = ap.parse_args()
    day = a.date.replace("-", "")[:8]
    d8 = "%s-%s-%s" % (day[:4], day[4:6], day[6:8])

    with _conn() as conn:
        with conn.cursor() as cur:
            _ensure(cur)
            if a.days > 0:
                since = (_dt.datetime.strptime(day, "%Y%m%d") - _dt.timedelta(days=a.days)).strftime("%Y-%m-%d")
                cur.execute("select c.id,c.trade_date,c.action,c.target,c.size_pct from trade_decision_card c "
                            "left join trade_decision_card_eval e on e.card_id=c.id "
                            "where e.card_id is null and c.trade_date >= %s order by c.id", (since,))
            else:
                cur.execute("select c.id,c.trade_date,c.action,c.target,c.size_pct from trade_decision_card c "
                            "left join trade_decision_card_eval e on e.card_id=c.id "
                            "where e.card_id is null and c.trade_date=%s order by c.id", (d8,))
            cards = cur.fetchall()
            n = {"missed": 0, "false_positive": 0, "under_sized": 0, "ok": 0}
            for cid, cdate, action, target, size_pct in cards:
                sym = pick_symbol(target)
                cd8 = str(cdate).replace("-", "")
                # 结构状态（当日）
                cur.execute("select coalesce(cond_count,0), position_pct from b_rebound_daily where session_date=%s",
                            (cd8,))
                r = cur.fetchone() or (0, None)
                cond, cap = int(r[0] or 0), r[1]
                cur.execute("select count(*) from volume_turn_intraday where session_date=%s and turn and price_ok",
                            (cd8,))
                fired = bool(cur.fetchone()[0])
                # 次日收益：标的（股票/ETF 走 mkt_bars_daily；指数走 index_daily）
                fwd = None
                try:
                    if sym.endswith((".SH", ".SZ")) and sym not in ("000001.SH",):
                        cur.execute("select close from mkt_bars_daily where ts_code=%s and trade_date>=%s "
                                    "order by trade_date limit 2", (sym, cd8))
                    else:
                        cur.execute("select close from index_daily where ts_code='000001.SH' and trade_date>=%s "
                                    "order by trade_date limit 2", (cd8,))
                    rows = [float(x[0]) for x in cur.fetchall()]
                    if len(rows) < 2 and sym not in ("000001.SH",):
                        # ETF 不在 mkt_bars_daily：走 Tushare fund_daily（512480/588170 等）
                        from core.tushare_relay import relay_query
                        _end = (_dt.datetime.strptime(cd8, "%Y%m%d") + _dt.timedelta(days=7)).strftime("%Y%m%d")
                        fd = relay_query("fund_daily", ts_code=sym, start_date=cd8, end_date=_end)
                        fd = fd.sort_values("trade_date")
                        rows = [float(x) for x in fd["close"].tolist()[:2]]
                    if len(rows) >= 2 and rows[0]:
                        fwd = round((rows[1] / rows[0] - 1) * 100, 2)
                except Exception:
                    fwd = None
                verdict, note = classify(action, size_pct, cond, fired, fwd, cap)
                n[verdict] = n.get(verdict, 0) + 1
                cur.execute("""insert into trade_decision_card_eval(card_id,trade_date,action,target_symbol,
                    expected_cap,size_pct,fwd_ret_pct,verdict,note) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (card_id) do update set verdict=excluded.verdict, note=excluded.note,
                    fwd_ret_pct=excluded.fwd_ret_pct, evaluated_at=now()""",
                    (cid, cdate, action, sym, cap, size_pct, fwd, verdict, note))
            conn.commit()
            print("[eval] cards=%d %s" % (len(cards), n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
