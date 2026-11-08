#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 简易选股器 — 从 hot_concepts + stock_concept_map 快速出候选股
由 auto_trade.py 通过 subprocess 调用，输出 JSON 数组到 stdout
"""

# ── DSN 收口（scripts/migrate_hardcoded_dsn.py 自动插入，见 core/dsn.py）──
import os as _os_dsn, sys as _sys_dsn
_ROOT_DSN = _os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.abspath(__file__)))
if _ROOT_DSN not in _sys_dsn.path:
    _sys_dsn.path.insert(0, _ROOT_DSN)
from core import dsn as _dsn_src  # noqa: E402

import sys
import json
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]

def _pg_conn():
    """PostgreSQL 连接（psycopg2，来自 DATABASE_URL）。"""
    import os
    import psycopg2
    url = _dsn_src.main()
    return psycopg2.connect(url)


def main():
    hot_concepts = json.loads(sys.argv[1]) if len(sys.argv) > 1 else []
    if not hot_concepts:
        # 从环境变量读取
        env_concepts = os.environ.get('_STOCK_SELECTOR_HOT_CONCEPTS', '[]')
        hot_concepts = json.loads(env_concepts)

    if not hot_concepts:
        print(json.dumps([]))
        return

    conn = _pg_conn()
    cursor = conn.cursor()

    candidates = []
    seen = set()

    for concept in hot_concepts[:5]:
        cursor.execute('''
            SELECT p.ts_code, p.symbol, p.name, p.market_cap
            FROM stock_pool p
            JOIN stock_concept_map m ON p.ts_code = m.ts_code
            WHERE m.concept_name = %s AND p.is_st = 0
            ORDER BY p.market_cap DESC
            LIMIT 5
        ''', (concept,))
        rows = cursor.fetchall()

        for r in rows:
            sym = r[1]
            if sym in seen:
                continue
            seen.add(sym)
            candidates.append({
                'symbol': sym,
                'name': r[2],
                'market_cap': r[3],
                'concept': concept,
            })

    conn.close()
    print(json.dumps(candidates, ensure_ascii=False))

if __name__ == '__main__':
    import os
    main()
