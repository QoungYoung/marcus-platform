# -*- coding: utf-8 -*-
"""一次性补录脚本：给 stock 账户补 15,400 股 SH512480（2026-09-07 执行过一次）。

⚠️ 已停用，默认拒绝执行。原因：
  · 它补的那行 `MANUAL_2026-09-07_092010` 已于 2026-09-16 作废、不计入持仓
    （用户拍板保持现状），当前持仓以 paper_positions/FIFO 的 1,300 股为准；
  · 方向词表与现金结算修好之后，重跑会**真的**改变持仓（+15,400 股）与现金（约 −15,000），
    不再是"插一行不生效的数据"。
如需重跑：显式加 --force，并先确认这 15,400 股确实要计入 stock 账户持仓与现金。
"""
import sys

if "--force" not in sys.argv:
    sys.exit("已停用（见文件头说明）：确需重跑请加 --force")

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
