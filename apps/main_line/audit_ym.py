# -*- coding: utf-8 -*-
import psycopg2
DB = "postgresql://marcus:marcus123@postgres:5432/marcus_trading"
conn = psycopg2.connect(DB); cur = conn.cursor()
cur.execute("SELECT id, event_type, quote_price, suggest_bid_price, suggest_ask_price, status, mode, reason, created_at FROM t_triggers WHERE symbol='SH603259' AND created_at > '2026-09-06' ORDER BY id")
rows = cur.fetchall()
print("--- 药明(SH603259) t_triggers today (", len(rows), ") ---")
for r in rows:
    print("id=%s ev=%s quotep=%s bid=%s ask=%s status=%s mode=%s reason=%.90s created=%s" % (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7] or "", r[8]))
cur.execute("SELECT id, direction, price, volume, reason, trade_date, voided FROM paper_trades WHERE symbol='SH603259' AND trade_date > '2026-09-06' ORDER BY id")
tr = cur.fetchall()
print("--- 药明 paper_trades today (", len(tr), ") ---")
for r in tr:
    print("id=%s dir=%s price=%s vol=%s reason=%.90s date=%s void=%s" % (r[0], r[1], r[2], r[3], r[4] or "", r[5], r[6]))
cur.close(); conn.close()
