# -*- coding: utf-8 -*-
"""stock_crowding.py — 筹码/拥挤度代理（PG 缓存）。

数据源：Tushare relay（datahubco/promax）
  top10_holders   前十大股东：top10_holder_pct / etf_ratio / institutional_ratio
  daily_basic     股本/流通盘：float_ratio / turnover_rate / circ_mv
注意：institutional_ratio 是前十大口径，不是全机构持仓。
"""
from __future__ import annotations
import os
from datetime import date

TABLE = "stock_crowding"

def _conn():
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)

def ensure():
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                symbol TEXT NOT NULL,
                as_of DATE NOT NULL,
                top10_holder_pct DOUBLE PRECISION,
                etf_ratio DOUBLE PRECISION,
                institutional_ratio DOUBLE PRECISION,
                float_ratio DOUBLE PRECISION,
                turnover_rate DOUBLE PRECISION,
                circ_mv DOUBLE PRECISION,
                status TEXT NOT NULL DEFAULT 'unknown',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY(symbol, as_of)
            )""")

def _periods(day8: str):
    y = int(day8[:4]); m = int(day8[4:6])
    # 最近两个季度末，先新后旧
    q = (m - 1) // 3
    out = []
    for _ in range(3):
        mm = q * 3
        if mm == 0:
            out.append(f"{y-1}1231"); y -= 1; q = 4
        else:
            out.append(f"{y}{mm:02d}{'31' if mm in (3,12) else '30'}")
            q -= 1
    return out

def _relay():
    try:
        from tushare_relay import relay_query
        return relay_query
    except Exception:
        return None

def fetch(symbol: str, day8: str) -> dict:
    ensure()
    q = _relay()
    if not q:
        return {"symbol": symbol, "status": "unknown"}
    top = None
    for p in _periods(day8):
        try:
            df = q("top10_holders", ts_code=symbol, period=p, limit=50)
            if df is not None and len(df):
                top = df; break
        except Exception:
            continue
    top10 = etf = inst = None
    if top is not None:
        try:
            rows = top.to_dict("records")
            top10 = round(sum(float(r.get("hold_ratio") or 0) for r in rows), 2)
            names = " ".join(str(r.get("holder_name") or "") for r in rows)
            etf = round(sum(float(r.get("hold_ratio") or 0) for r in rows
                            if any(k in str(r.get("holder_name") or "") for k in ("ETF", "交易型开放式", "指数基金", "指数"))), 2)
            inst = round(sum(float(r.get("hold_ratio") or 0) for r in rows
                             if any(k in str(r.get("holder_type") or "") for k in ("基金", "保险", "QFII", "券商", "金融", "社保"))), 2)
        except Exception:
            pass
    fr = tr = cmv = None
    try:
        # daily_basic 盘中常无当日，回退最近 7 天
        from datetime import datetime, timedelta
        d = datetime.strptime(day8, "%Y%m%d")
        for i in range(0, 8):
            dd = (d - timedelta(days=i)).strftime("%Y%m%d")
            df = q("daily_basic", ts_code=symbol, trade_date=dd, limit=1)
            if df is not None and len(df):
                r = df.iloc[0]
                ts = float(r.get("total_share") or 0); fs = float(r.get("float_share") or 0)
                fr = round(fs / ts * 100, 2) if ts > 0 else None
                tr = float(r.get("turnover_rate") or 0)
                cmv = float(r.get("circ_mv") or 0)
                break
    except Exception:
        pass
    status = "known" if (top10 is not None and fr is not None) else ("partial" if (top10 is not None or fr is not None) else "unknown")
    rec = {"symbol": symbol, "as_of": day8, "top10_holder_pct": top10, "etf_ratio": etf,
           "institutional_ratio": inst, "float_ratio": fr, "turnover_rate": tr,
           "circ_mv": cmv, "status": status}
    try:
        d = f"{day8[:4]}-{day8[4:6]}-{day8[6:8]}"
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(f"""INSERT INTO {TABLE}(symbol,as_of,top10_holder_pct,etf_ratio,institutional_ratio,float_ratio,turnover_rate,circ_mv,status,updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                    ON CONFLICT(symbol,as_of) DO UPDATE SET top10_holder_pct=EXCLUDED.top10_holder_pct,etf_ratio=EXCLUDED.etf_ratio,
                    institutional_ratio=EXCLUDED.institutional_ratio,float_ratio=EXCLUDED.float_ratio,turnover_rate=EXCLUDED.turnover_rate,
                    circ_mv=EXCLUDED.circ_mv,status=EXCLUDED.status,updated_at=now()""",
                    (symbol,d,top10,etf,inst,fr,tr,cmv,status))
    except Exception:
        pass
    return rec

def get(symbol: str, day8: str) -> dict:
    ensure()
    d = f"{day8[:4]}-{day8[4:6]}-{day8[6:8]}"
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(f"SELECT symbol,top10_holder_pct,etf_ratio,institutional_ratio,float_ratio,turnover_rate,circ_mv,status FROM {TABLE} WHERE symbol=%s AND as_of=%s",(symbol,d))
                r=cur.fetchone()
                if r: return {"symbol":r[0],"top10_holder_pct":r[1],"etf_ratio":r[2],"institutional_ratio":r[3],
                              "float_ratio":r[4],"turnover_rate":r[5],"circ_mv":r[6],"status":r[7],"as_of":day8}
    except Exception:
        pass
    return fetch(symbol, day8)
