# -*- coding: utf-8 -*-
"""add_pos_512480.py — 按用户买入时点入库 512480 半导体ETF 到系统模拟仓。"""

# ── DSN 收口（scripts/migrate_hardcoded_dsn.py 自动插入，见 core/dsn.py）──
import os as _os_dsn, sys as _sys_dsn
_ROOT_DSN = _os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.abspath(__file__))))
if _ROOT_DSN not in _sys_dsn.path:
    _sys_dsn.path.insert(0, _ROOT_DSN)
from core import dsn as _dsn_src  # noqa: E402
import psycopg2, datetime
DB = _dsn_src.main()
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
