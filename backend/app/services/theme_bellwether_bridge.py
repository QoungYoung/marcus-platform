# -*- coding: utf-8 -*-
"""theme_bellwether_bridge.py — 把已有 bellwether_daily 接进题材 facts。"""
from __future__ import annotations
import os
from typing import Any, Dict, Set


def _conn():
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"])


def load_items(date8: str) -> Dict[str, Dict[str, Any]]:
    d = "%s-%s-%s" % (date8[:4], date8[4:6], date8[6:8])
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT max(trade_date) FROM bellwether_daily WHERE trade_date <= %s", (date8,))
            row = cur.fetchone()
            if not row or not row[0]: return {}
            raw = row[0]
            dd = str(raw).replace("-", "")[:8]
            cur.execute("""SELECT subtheme,bellwether_code,bellwether_name,state,metrics,source
                           FROM bellwether_daily WHERE trade_date=%s""", (raw,))
            out = {}
            for sub, code, name, state, metrics, source in cur.fetchall():
                m = metrics or {}
                if dd != date8:
                    continue
                out[sub] = {"subtheme": sub, "date": dd, "bellwether_code": code,
                            "bellwether_name": name, "state": state,
                            "successor_code": m.get("successor_code"),
                            "successor": m.get("successor"), "source": source}
            return out


def attach(rec: Dict[str, Any], members: Set[str], date8: str) -> Dict[str, Any]:
    """按成员重叠把题材映射到 bellwether 子主题；命中则把 wind/leader 作为证据。"""
    try:
        from app.services.wolf_card.subthemes import stocks, parent
        items = load_items(date8)
    except Exception:
        return rec
    if not items: return rec
    best = None
    for sub, it in items.items():
        try: codes = set(stocks(sub))
        except Exception: continue
        if not codes or not members: continue
        ov = len(codes & members) / min(len(codes), len(members))
        if best is None or ov > best[0]: best = (ov, sub, it)
    if not best or best[0] < 0.15: return rec
    ov, sub, it = best
    rec["bellwether"] = {**it, "overlap": round(ov, 3), "parent": parent(sub)}
    # 现有风向标仍在成分内、未 dead：用它作为 leader 证据
    code = it.get("bellwether_code")
    if code in members and it.get("state") not in ("dead", "rotated"):
        rec.setdefault("roles", {})["leader"] = code
        rec["leader"] = code
        rec["leader_source"] = "bellwether"
    return rec
