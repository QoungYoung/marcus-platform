# -*- coding: utf-8 -*-
import sys, json
from pathlib import Path
for p in ["/app/app", "/app/core", "/app"]:
    if p not in sys.path: sys.path.insert(0, p)
from app.database import SessionLocal
from sqlalchemy import text

expr_json = json.dumps({"and": [
    {"field": "index.intraday_dd", "op": ">=", "value": 2.0},
    {"field": "index.intraday_dd", "op": "<", "value": 3.0},
]}, ensure_ascii=False)
db = SessionLocal()
try:
    db.execute(text(
        "UPDATE t_conditions SET expression = CAST(:expr AS jsonb), trigger_kind='low_buy', direction='buy',"
        " status='active', armed=1, publisher='wolf_t', session_id='wolf-zt-dip-ykld', stop_loss_price=153.98"
        " WHERE id=249 AND account_id='stock'"
    ), {"expr": expr_json})
    db.commit()
    print("OK updated 249")
except Exception as e:
    print("ERR:", str(e)[:300])
finally:
    db.close()