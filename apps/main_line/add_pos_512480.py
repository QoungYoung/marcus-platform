# -*- coding: utf-8 -*-
import psycopg2, datetime
DB = "postgresql://marcus:marcus123@postgres:5432/marcus_trading"
conn = psycopg2.connect(DB); cur = conn.cursor(); conn.autocommit = False
SYM = "SH512480"; ACCT = "stock"; VOL = 15400; PX = 0.972; DT = "2026-09-04"
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
cur.execute("SELECT symbol FROM paper_positions WHERE symbol=%s AND account_id=%s", (SYM, ACCT))
if cur.fetchone():
    cur.execute("UPDATE paper_positions SET volume=%s, avg_price=%s, entry_date=%s, updated_at=%s WHERE symbol=%s AND account_id=%s",
                (VOL, PX, DT, now, SYM, ACCT)); print("updated")
else:
    cur.execute("INSERT INTO paper_positions (symbol, entry_date, highest_price, updated_at, volume, frozen, avg_price, account_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (SYM, DT, PX, now, VOL, 0, PX, ACCT)); print("inserted")
cur.execute("INSERT INTO paper_trades (symbol, direction, price, volume, amount, profit, created_at, trade_date, reason, account_id, orderid) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            # 方向必须写中文「买入」：曾用英文 "buy" 导致读取端（FIFO/统计）静默忽略该行
            (SYM, "买入", PX, VOL, round(PX*VOL, 2), 0.0, now, DT, "user_manual_buy_20260904_T", ACCT, "MANUAL_" + now.replace(" ", "_").replace(":", "")))
conn.commit()
cur.execute("SELECT symbol, account_id, volume, avg_price, entry_date FROM paper_positions WHERE symbol=%s", (SYM,))
print("positions:", cur.fetchall())
cur.execute("SELECT symbol, direction, price, volume, amount, trade_date, account_id, reason FROM paper_trades WHERE symbol=%s ORDER BY id DESC LIMIT 2", (SYM,))
print("trades:", cur.fetchall())
cur.close(); conn.close()
