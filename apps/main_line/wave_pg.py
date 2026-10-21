# -*- coding: utf-8 -*-
"""浪型状态 → PostgreSQL（双写 ✓ 全程 fail-open ✓）。

表 `wave_state`：**session_date 主键**（= 服务交易日，每天恰好一份 ✓ 天然幂等 ✓），
`payload` 存完整 ws（JSONB ✓ 未来加字段不用改表 ✓）。

阶段（2026-10-21 用户拍板「能放到 pg 里，其他接口也从 pg 读」）：
  · 步 1/2（本文件已就绪）：建表（幂等 DDL ✓）+ **双写** + 回填；
    ⇒ 此刻**文件仍是权威** ✓ 读侧一行未改 ✓ 生产零行为变化 ✓
  · 步 3（后续）：读侧切 PG（开关 `WOLF_WAVE_STORE` ✓ 文件兜底 ✓）

纪律：
  · 任何 DB 异常 ⇒ **返回 False，绝不抛** ✓（文件写入是主路径，不能被 DB 拖死 ✗）；
  · `ON CONFLICT` 带 `WHERE data_date <= EXCLUDED.data_date` ⇒ **旧数据不会覆盖新数据** ✓；
  · 超时 5 秒 ✓（不能因为库慢拖住 08:10 的定时任务 ✗）。
"""
import json
import os
from typing import Any, Dict, Optional

DSN = (os.environ.get("DATABASE_URL") or os.environ.get("PG_DSN") or "").strip()

DDL = """
CREATE TABLE IF NOT EXISTS wave_state (
  session_date DATE PRIMARY KEY,
  data_date    DATE NOT NULL,
  level        TEXT,
  sub_level    TEXT,
  operation    TEXT,
  mode         TEXT,
  rung         INTEGER,
  family       TEXT,
  stage        TEXT,
  status       TEXT,
  confidence   DOUBLE PRECISION,
  source       TEXT,
  payload      JSONB NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wave_state_data_date ON wave_state (data_date);
"""

_SQL_UPSERT = """
INSERT INTO wave_state
  (session_date, data_date, level, sub_level, operation, mode, rung,
   family, stage, status, confidence, source, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (session_date) DO UPDATE SET
  data_date  = EXCLUDED.data_date,
  level      = EXCLUDED.level,
  sub_level  = EXCLUDED.sub_level,
  operation  = EXCLUDED.operation,
  mode       = EXCLUDED.mode,
  rung       = EXCLUDED.rung,
  family     = EXCLUDED.family,
  stage      = EXCLUDED.stage,
  status     = EXCLUDED.status,
  confidence = EXCLUDED.confidence,
  source     = EXCLUDED.source,
  payload    = EXCLUDED.payload,
  updated_at = now()
WHERE wave_state.data_date <= EXCLUDED.data_date
"""


def _date8(s: Any) -> Optional[str]:
    d = str(s or "").replace("-", "").strip()
    if len(d) < 8 or not d[:8].isdigit():
        return None
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:8])


def _conn():
    if not DSN:
        return None
    import psycopg2
    return psycopg2.connect(DSN, connect_timeout=5)


def upsert(ws: Dict[str, Any]) -> bool:
    """写一行（幂等 ✓）。任何异常 ⇒ False，**绝不抛** ✓。"""
    try:
        if not ws:
            return False
        sd = _date8(ws.get("session_date"))
        dd = _date8(ws.get("date")) or sd
        if not sd or not dd:
            return False
        conn = _conn()
        if conn is None:
            return False
        try:
            with conn, conn.cursor() as cur:
                cur.execute(DDL)
                # ★ rung 在 ws 里是 **dict**（{'key','name','index','expect'}）✗ 不是 int ⇒ 取 index ✓
                _r = ws.get("rung")
                _r = _r.get("index") if isinstance(_r, dict) else _r
                try:
                    _r = int(_r) if _r is not None and str(_r).strip() != "" else None
                except Exception:
                    _r = None
                cur.execute(_SQL_UPSERT, (
                    sd, dd, ws.get("level"), ws.get("sub_level"), ws.get("operation"), ws.get("mode"),
                    _r, ws.get("family"), ws.get("stage"), ws.get("status"),
                    ws.get("confidence"), ws.get("source"), json.dumps(ws, ensure_ascii=False)))
        finally:
            conn.close()
        return True
    except Exception as e:
        print("[wave_pg] upsert 失败（忽略 ✓）: %s" % str(e)[:140], flush=True)
        return False


def upsert_many(rows) -> int:
    """批量写（回填用 ✓）→ 成功条数。"""
    n = 0
    for ws in rows:
        if upsert(ws):
            n += 1
    return n


def load_latest() -> Optional[Dict[str, Any]]:
    """最新一档（按 session_date ✓）。读不到 ⇒ None（fail-open ✓）。"""
    try:
        conn = _conn()
        if conn is None:
            return None
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT payload FROM wave_state ORDER BY session_date DESC LIMIT 1")
                r = cur.fetchone()
                return r[0] if r else None
        finally:
            conn.close()
    except Exception as e:
        print("[wave_pg] load_latest 失败: %s" % str(e)[:120], flush=True)
        return None


def load_asof(sim8: Any) -> Optional[Dict[str, Any]]:
    """回测取档：`session_date <= sim8` 的最近一行 ✓（**替代** wave_state_history/<sim8>.json ✓）。"""
    try:
        d = _date8(sim8)
        if not d:
            return None
        conn = _conn()
        if conn is None:
            return None
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT payload FROM wave_state WHERE session_date <= %s "
                            "ORDER BY session_date DESC LIMIT 1", (d,))
                r = cur.fetchone()
                return r[0] if r else None
        finally:
            conn.close()
    except Exception as e:
        print("[wave_pg] load_asof 失败: %s" % str(e)[:120], flush=True)
        return None


def stats() -> Dict[str, Any]:
    """行数 + 日期范围（验收用 ✓）。"""
    try:
        conn = _conn()
        if conn is None:
            return {"ok": False, "why": "no DSN"}
        try:
            with conn, conn.cursor() as cur:
                cur.execute(DDL)
                cur.execute("SELECT count(*), min(session_date)::text, max(session_date)::text FROM wave_state")
                n, lo, hi = cur.fetchone()
                return {"ok": True, "rows": n, "min": lo, "max": hi}
        finally:
            conn.close()
    except Exception as e:
        return {"ok": False, "why": str(e)[:120]}
