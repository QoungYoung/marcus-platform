# -*- coding: utf-8 -*-
"""wolf_leader_rules.py — 狼大龙头口径的确定性护栏。

狼大口径（见 docs/wolf-playbook.md §6.1）：
- 板块内总市值前5 + 营收拐点 + 题材联动不可替换 的核心标；
- 老龙头/风向标辨识度；
- 涨停/封单只确认，不选谁；
- 配套段不得当 leader。
"""
from __future__ import annotations
import os

# 只做核心段；这些行业/名称属于配套、加工、材料、结构件，不能当 leader
SUPPORT_INDUSTRY = {
    "汽车配件", "汽车零部件", "元器件", "电子元件", "连接器", "电源设备",
    "金属制品", "通用机械", "专用设备", "机床制造", "电气设备", "塑料",
    "橡胶", "化工原料", "钢铁", "有色加工",
}
SUPPORT_NAME = ("连接器", "电源", "结构件", "轴承", "材料", "加工", "配件", "零部件", "链传动", "紧固件", "壳体", "线束")

def _row(symbol: str):
    try:
        import psycopg2
        with psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=3) as c:
            with c.cursor() as cur:
                cur.execute("select name,industry from stock_pool where ts_code=%s or ts_code=%s or symbol=%s limit 1",
                            (symbol, symbol.replace(".SH", "").replace(".SZ", ""), symbol.split('.')[0]))
                return cur.fetchone()
    except Exception:
        return None

def check(symbol: str):
    """返回 (ok,reason)。只做配套段硬拦；核心段/市值/营收由 B 和 facts 判。"""
    if not symbol:
        return False, "no_leader"
    r = _row(symbol)
    if not r:
        return True, "no_stock_basic"
    name, industry = (r[0] or ""), (r[1] or "")
    if industry in SUPPORT_INDUSTRY:
        return False, f"support_industry:{industry}"
    if any(k in name for k in SUPPORT_NAME):
        return False, f"support_name:{name}"
    return True, "ok"
