#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 简易选股器 — 从 hot_concepts + stock_concept_map 快速出候选股
由 auto_trade.py 通过 subprocess 调用，输出 JSON 数组到 stdout
"""

import sys
import json
import sqlite3
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]

def main():
    hot_concepts = json.loads(sys.argv[1]) if len(sys.argv) > 1 else []
    if not hot_concepts:
        # 从环境变量读取
        env_concepts = os.environ.get('_STOCK_SELECTOR_HOT_CONCEPTS', '[]')
        hot_concepts = json.loads(env_concepts)

    if not hot_concepts:
        print(json.dumps([]))
        return

    db = WORKSPACE / "data" / "stock_pool.db"
    if not db.exists():
        print(json.dumps([]))
        return

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    candidates = []
    seen = set()

    for concept in hot_concepts[:5]:
        rows = conn.execute('''
            SELECT p.ts_code, p.symbol, p.name, p.market_cap
            FROM stock_pool p
            JOIN stock_concept_map m ON p.ts_code = m.ts_code
            WHERE m.concept_name = ? AND p.is_st = 0
            ORDER BY p.market_cap DESC
            LIMIT 5
        ''', (concept,)).fetchall()

        for r in rows:
            sym = r['symbol']
            if sym in seen:
                continue
            seen.add(sym)
            candidates.append({
                'symbol': sym,
                'name': r['name'],
                'market_cap': r['market_cap'],
                'concept': concept,
            })

    conn.close()
    print(json.dumps(candidates, ensure_ascii=False))

if __name__ == '__main__':
    import os
    main()
