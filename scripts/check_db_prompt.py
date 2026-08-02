# -*- coding: utf-8 -*-
"""Check if prompt_seeds on cloud DB has been updated."""
import psycopg2

conn = psycopg2.connect('postgresql://marcus:marcus123@81.70.44.68:5432/marcus_trading')
cur = conn.cursor()

# Check if table exists
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE '%prompt%'")
tables = [r[0] for r in cur.fetchall()]
print(f"Prompt tables: {tables}")

for table in tables:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    count = cur.fetchone()[0]
    print(f"\n{table}: {count} rows")

    cur.execute(f"SELECT * FROM {table} LIMIT 3")
    cols = [d[0] for d in cur.description]
    print(f"  Columns: {cols}")

    for row in cur.fetchall():
        # Show first 150 chars of text columns
        vals = []
        for i, v in enumerate(row):
            if isinstance(v, str) and len(v) > 150:
                vals.append(f"{cols[i]}={v[:150]}...")
            else:
                vals.append(f"{cols[i]}={v}")
        print(f"  ---")
        print(f"  {' | '.join(vals)}")

# Specifically check for dual-track / continuity keywords
print("\n=== Checking for new continuity logic ===")
keywords = ['连续性', '重叠', '风格切换', '混乱期', '子判定', '新主线形成']
for table in tables:
    for kw in keywords:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE prompt_text LIKE '%{kw}%'")
            c = cur.fetchone()[0]
            if c > 0:
                print(f"  '{kw}' found in {table}: {c} rows")
        except:
            pass

conn.close()
