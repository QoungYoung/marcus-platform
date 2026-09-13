# -*- coding: utf-8 -*-
import psycopg2
DB = "postgresql://marcus:marcus123@postgres:5432/marcus_trading"
conn = psycopg2.connect(DB); cur = conn.cursor()
q = ("SELECT id, event_type, trigger_price, quote_price, suggest_bid_price, suggest_ask_price, "
     "status, mode, reason, created_at FROM t_triggers WHERE symbol='SH588170' AND created_at > '2026-09-06' ORDER BY id")
cur.execute(q)
rows = cur.fetchall()
print("--- SH588170 t_triggers today (", len(rows), ") ---")
for r in rows:
    print("id=%s ev=%s trigp=%s quotep=%s bid=%s ask=%s status=%s mode=%s reason=%.60s created=%s" % (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8] or "", r[9]))
cur.execute("SELECT id, direction, price, volume, amount, reason, account_id, trade_date, voided FROM paper_trades WHERE symbol='SH588170' AND trade_date > '2026-09-06' ORDER BY id")
tr = cur.fetchall()
print("--- SH588170 paper_trades today (", len(tr), ") ---")
for r in tr:
    print("id=%s dir=%s price=%s vol=%s amt=%s reason=%.60s acct=%s date=%s void=%s" % (r[0], r[1], r[2], r[3], r[4], r[5] or "", r[6], r[7], r[8]))
cur.close(); conn.close()
