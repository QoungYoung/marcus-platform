# -*- coding: utf-8 -*-
"""风向标只读提示：只推状态变化，不选股、不调仓。"""
from __future__ import annotations
import argparse, json, os
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data/core_p1/bellwether_state_latest.json")
HIST = os.path.join(ROOT, "data/core_p1/bellwether_history.csv")
LAST = os.path.join(ROOT, "data/core_p1/last_alert.json")


def changes():
    x = json.load(open(STATE, encoding="utf-8"))
    date = str(x.get("date"))
    items = x.get("items") or []
    prev = {}
    if os.path.exists(HIST) and os.path.getsize(HIST) > 0:
        try:
            h = pd.read_csv(HIST, dtype={"date": str})
        except Exception:
            h = pd.DataFrame()
        older = h[h.date < date] if len(h) else h
        if len(older):
            last_date = older.date.max()
            prev = {str(r.subtheme): str(r.state) for _, r in older[older.date == last_date].iterrows()}
    if not prev:
        # 生产 history 可能为空：用 PG 上一交易日兜底
        try:
            import sys
            for _p in (os.path.join(ROOT, "backend"), ROOT, "/app"):
                if _p not in sys.path: sys.path.insert(0, _p)
            from sqlalchemy import text
            from app.database import engine
            with engine.connect() as conn:
                row = conn.execute(text("SELECT max(trade_date) FROM bellwether_daily WHERE trade_date < :d"), {"d": date}).scalar()
                if row:
                    rs = conn.execute(text("SELECT subtheme, state FROM bellwether_daily WHERE trade_date = :d"), {"d": str(row)}).mappings()
                    prev = {str(r["subtheme"]): str(r["state"]) for r in rs}
        except Exception as e:
            print("[bellwether-alert] PG prev fallback failed:", str(e)[:120])
    out = []
    for it in items:
        sub = str(it.get("subtheme")); st = str(it.get("state"))
        old = prev.get(sub)
        if old and old != st:
            out.append({"subtheme": sub, "old": old, "new": st,
                        "bellwether_code": it.get("bellwether_code")})
    return date, out


def push(text):
    try:
        from core.qq_notifier import send_c2c_message
        openid = (os.environ.get("QQ_BOT_RECIPIENT") or "").strip()
        if not openid:
            print("[bellwether-alert] 未配置 QQ_BOT_RECIPIENT"); return False
        return bool(send_c2c_message(openid, text))
    except Exception as e:
        print("[bellwether-alert] 推送异常:", str(e)[:120]); return False


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry", action="store_true"); a = ap.parse_args()
    date, ch = changes()
    if not ch:
        print(json.dumps({"date": date, "changes": 0}, ensure_ascii=False)); return
    lines = [f"【风向标变化】{date}", ""]
    for c in ch:
        lines.append(f"{c['subtheme']}：{c['old']} → {c['new']}")
        if c.get("bellwether_code"): lines.append(f"  龙头：{c['bellwether_code']}")
    lines += ["", "说明：环境提示，不是买卖建议；不参与选股/仓位。"]
    text = "\n".join(lines)
    print(text)
    if not a.dry:
        push(text)
        json.dump({"date": date, "changes": ch}, open(LAST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"date": date, "changes": len(ch), "dry": a.dry}, ensure_ascii=False))


if __name__ == "__main__":
    main()
