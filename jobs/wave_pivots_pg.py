# -*- coding: utf-8 -*-
"""wave_pivots 落库（PG）：`wave_pivots`（锚点本体）+ `wave_pivots_runs`（每次重建留痕/新鲜度）。

为什么：`data/wave_pivots.json` 是 16:30 任务天天重写的单文件 —— 没有版本、审计、新鲜度记录
（2026-09-14 事故即源于此：文件被覆盖/停更都无从发现）。落库后：可查询、可幂等 upsert、可看"上次重建是何时"。
**JSON 仍保留双写**：离线重放不依赖 DB，两者互为兜底（DB 也会被写坏）。
"""
import json, os
from datetime import datetime

DSN = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
PIVOTS_FILE = os.getenv("WAVE_PIVOTS_FILE", os.path.join(os.getenv("DATA_DIR", "/app/data"), "wave_pivots.json"))


def _conn():
    import psycopg2
    return psycopg2.connect(DSN)


def ensure_tables(cur) -> None:
    cur.execute("""CREATE TABLE IF NOT EXISTS wave_pivots (
        id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL DEFAULT '000001.SH',
        pivot_date DATE NOT NULL, value NUMERIC NOT NULL, kind TEXT NOT NULL,
        pivot_window INTEGER, source TEXT, created_at TIMESTAMP DEFAULT now(),
        UNIQUE (symbol, pivot_date, kind, pivot_window))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS wave_pivots_runs (
        id BIGSERIAL PRIMARY KEY, run_at TIMESTAMP DEFAULT now(),
        source TEXT, pivot_window INTEGER, n_pivots INTEGER, last_date DATE, note TEXT)""")


def upsert_pivots(pivots, window=None, source=None, symbol="000001.SH", note="") -> int:
    conn = _conn()
    try:
        cur = conn.cursor()
        ensure_tables(cur)
        n = 0
        for p in pivots or []:
            try:
                cur.execute("""INSERT INTO wave_pivots (symbol,pivot_date,value,kind,pivot_window,source)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (symbol,pivot_date,kind,pivot_window) DO UPDATE
                      SET value=EXCLUDED.value, source=EXCLUDED.source""",
                    (symbol, str(p["date"])[:10], float(p["value"]), str(p.get("type") or p.get("kind") or ""),
                     window, source))
                n += 1
            except Exception as e:
                print("[wave_pivots_pg] 单行跳过 %s: %s" % (p, str(e)[:60]))
        last = max([str(x["date"])[:10] for x in (pivots or [])], default=None)
        cur.execute("""INSERT INTO wave_pivots_runs (source,pivot_window,n_pivots,last_date,note)
                       VALUES (%s,%s,%s,%s,%s)""", (source, window, n, last, note))
        conn.commit()
        return n
    finally:
        conn.close()


def upsert_from_json(path=None) -> int:
    p = path or PIVOTS_FILE
    d = json.load(open(p, encoding="utf-8"))
    return upsert_pivots(d.get("pivots") or [], window=d.get("window"), source=d.get("source"),
                         note="import from json")


def load_pivots(symbol="000001.SH"):
    """读锚点：**优先 DB**（WOLF_PIVOTS_SOURCE=db，默认），失败/为空 → 回退 JSON（fail-open）。"""
    src = os.getenv("WOLF_PIVOTS_SOURCE", "db").strip().lower()
    if src == "db":
        try:
            conn = _conn()
            try:
                cur = conn.cursor()
                cur.execute("""SELECT pivot_date,value,kind,pivot_window,source FROM wave_pivots
                               WHERE symbol=%s ORDER BY pivot_date""", (symbol,))
                rows = cur.fetchall()
            finally:
                conn.close()
            if rows:
                w = rows[0][3]
                return {"source": rows[0][4], "window": w,
                        "pivots": [{"date": str(r[0]), "value": float(r[1]), "type": r[2]} for r in rows]}
            print("[wave_pivots_pg] DB 无数据 → 回退 JSON")
        except Exception as e:
            print("[wave_pivots_pg] DB 读取失败(%s) → 回退 JSON" % str(e)[:60])
    try:
        return json.load(open(PIVOTS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def age_days(symbol="000001.SH"):
    """距上次重建的天数（新鲜度告警依据）。"""
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT max(run_at) FROM wave_pivots_runs")
            r = cur.fetchone()
        finally:
            conn.close()
        if r and r[0]:
            return (datetime.now() - r[0]).days
    except Exception:
        pass
    return None
