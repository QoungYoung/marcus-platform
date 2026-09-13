# -*- coding: utf-8 -*-
"""add_pos_512480.py — 按用户买入时点入库 512480 半导体ETF 到系统模拟仓。"""
import psycopg2, datetime
DB = "postgresql://marcus:marcus123@postgres:5432/marcus_trading"
conn = psycopg2.connect(DB); cur = conn.cursor()
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='paper_positions' ORDER BY ordinal_position")
print("paper_positions cols:", cur.fetchall())
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='paper_trades' ORDER BY ordinal_position")
print("paper_trades cols:", cur.fetchall())
cur.execute("SELECT symbol, account_id, volume, avg_price FROM paper_positions WHERE symbol LIKE %s", ("%512480%",))
print("existing:", cur.fetchall())
# 检查 t_conditions / 是否已有该标的
cur.execute("SHOW search_path")
print("search_path:", cur.fetchone())
conn.close()
