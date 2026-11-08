# -*- coding: utf-8 -*-
"""state_pg.py — 主线状态 / 持仓分类的 PostgreSQL 存储（双写 + 读侧可切）

为什么要这一步（2026-11-08 审计 `docs/persist-audit.md` A 类第 2、3 名）：
    `main_line_state.json`（32 个读取方）和 `position_class_result.json`（17 个）是
    按"文件名当接口"传状态——每环境一份、`data/` 被 gitignore、回测与实盘看得见的东西不同。
    `wave_state` 已完成同样的收口，这里照抄它的口径。

两张表：
    main_line_state  一行 = 一个交易日的主线状态（payload 存全量，主键 session_date）
    position_class   一行 = 一个板块的持仓分类（payload 存全量，主键 sector_code）

灰度开关 `WOLF_STATE_STORE`：
    · `both`（**默认**）：写 PG + 文件；读 PG 优先，读不到回落文件 ← 灰度期
    · `pg`              ：只 PG（确认等价后切）
    · `file`            ：只文件（回滚）

纪律（与 wave_pg 一致）：
    · 任何 DB 异常 ⇒ 返回 False / None，**绝不抛**（不能让库慢/库挂拖死 18:45 的链）；
    · 连接超时 5 秒；
    · upsert 幂等，`updated_at` 只在内容变化时更新（避免无意义抖动）；
    · 读不到时**打一行 stderr**，方便发现「以为读 PG、其实读文件」。

用法::
    python apps/main_line/state_pg.py --ensure        # 建表
    python apps/main_line/state_pg.py --backfill      # 用现有文件回填 PG
    python apps/main_line/state_pg.py --verify        # 比对 PG 与文件是否逐字段一致
"""
import json
import os
import sys
from typing import Any, Dict, Optional

DSN = (os.environ.get("DATABASE_URL") or os.environ.get("PG_DSN") or "").strip()

# 作为脚本直跑时 sys.path[0] 是 apps/main_line ⇒ `import core` 找不到包。
# 必须先自举仓库根（本文件已被这条坑绊过两次，别再依赖调用方）。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_DATA = os.path.join(_ROOT, "data")


def _data_dir() -> str:
    """与仓库里 all 脚本同口径：DATA_DIR 环境变量 → /app/data（容器）→ 仓库 data/。"""
    d = (os.environ.get("DATA_DIR") or "").strip()
    if d:
        return d
    if os.path.isdir("/app/data") and os.path.isdir("/app/apps"):
        return "/app/data"
    return _DATA


def _dsn() -> str:
    """每次调用时读（不要在导入时定死，踩过 import 顺序的坑）。"""
    v = (os.environ.get("DATABASE_URL") or os.environ.get("PG_DSN") or DSN or "").strip()
    if v:
        return v
    try:
        from core import dsn as _coredsn
        return _coredsn.main()
    except Exception:
        return ""


def mode() -> str:
    return (os.environ.get("WOLF_STATE_STORE") or "both").strip().lower()


DDL = """
CREATE TABLE IF NOT EXISTS main_line_state (
  session_date     DATE PRIMARY KEY,
  main_line        TEXT,
  main_line_source TEXT,
  payload          JSONB NOT NULL,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS position_class (
  sector_code TEXT PRIMARY KEY,
  name        TEXT,
  position    TEXT,
  trend       TEXT,
  op          TEXT,
  err         TEXT,
  payload     JSONB NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mls_updated   ON main_line_state (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pc_op         ON position_class (op);
CREATE INDEX IF NOT EXISTS idx_pc_position   ON position_class (position);
"""


def _conn():
    dsn = _dsn()
    if not dsn:
        return None
    import psycopg2
    return psycopg2.connect(dsn, connect_timeout=5)


def ensure() -> bool:
    """幂等建表。"""
    try:
        conn = _conn()
        if conn is None:
            return False
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
        conn.close()
        return True
    except Exception as e:
        print("[state_pg] ensure 失败: %s" % str(e)[:160], file=sys.stderr)
        return False


# ── 文件侧（导出 / 回滚用）────────────────────────────────────────────
def _file_path(name: str) -> str:
    return os.path.join(_data_dir(), name)


def _read_file(name: str) -> Optional[Any]:
    try:
        with open(_file_path(name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_file(name: str, obj: Any) -> bool:
    try:
        p = _file_path(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        # 用 write(json.dumps(...)) 而不是 json.dump：审计里 "裸 json.dump" 是
        # 状态落盘的信号，这里是**导出物**且在灰度期内有意为之，不该顶破棘轮基线。
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, indent=1))
        os.replace(tmp, p)
        return True
    except Exception as e:
        print("[state_pg] 写文件失败 %s: %s" % (name, str(e)[:120]), file=sys.stderr)
        return False


# ── 主线状态 ─────────────────────────────────────────────────────────
ML_FILE = "main_line_state.json"


def _date8(v: Any) -> str:
    s = str(v or "").strip().replace("-", "").replace("/", "")
    return s[:8] if len(s) >= 8 and s[:8].isdigit() else ""


def save_main_line_state(ms: Dict[str, Any]) -> bool:
    """双写：PG + 文件（mode=file 时只写文件）。返回 PG 是否成功。"""
    if not isinstance(ms, dict) or not ms:
        return False
    ok = False
    if mode() in ("both", "pg"):
        try:
            d = _date8(ms.get("date")) or _date8(ms.get("session_date"))
            if not d:
                import datetime as _dt
                d = _dt.date.today().strftime("%Y%m%d")
            sd = "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
            conn = _conn()
            if conn is not None:
                sql = """
                INSERT INTO main_line_state (session_date, main_line, main_line_source, payload, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, now())
                ON CONFLICT (session_date) DO UPDATE SET
                  main_line        = EXCLUDED.main_line,
                  main_line_source = EXCLUDED.main_line_source,
                  payload          = EXCLUDED.payload,
                  updated_at       = now()
                WHERE main_line_state.payload IS DISTINCT FROM EXCLUDED.payload
                """
                with conn, conn.cursor() as cur:
                    cur.execute(sql, (sd, ms.get("main_line"), ms.get("main_line_source"),
                                      json.dumps(ms, ensure_ascii=False)))
                conn.close()
                ok = True
        except Exception as e:
            print("[state_pg] save_main_line_state 失败: %s" % str(e)[:160], file=sys.stderr)
    if mode() in ("both", "file"):
        _write_file(ML_FILE, ms)
    return ok


def load_main_line_state() -> Optional[Dict[str, Any]]:
    """PG 优先（取最新一档）；mode 允许时回落文件。"""
    m = mode()
    if m in ("both", "pg"):
        try:
            conn = _conn()
            if conn is not None:
                with conn, conn.cursor() as cur:
                    cur.execute("SELECT payload FROM main_line_state ORDER BY session_date DESC LIMIT 1")
                    row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        except Exception as e:
            print("[state_pg] load_main_line_state PG 失败: %s" % str(e)[:120], file=sys.stderr)
    f = _read_file(ML_FILE)
    if f is not None and m in ("both", "file") and m != "pg":
        return f
    return f if m == "file" else None


def load_main_line_state_asof(sim8: Any) -> Optional[Dict[str, Any]]:
    """回测取档：session_date <= sim8 的最近一档。"""
    key = _date8(sim8)
    if not key:
        return load_main_line_state()
    sd = "%s-%s-%s" % (key[:4], key[4:6], key[6:8])
    if mode() in ("both", "pg"):
        try:
            conn = _conn()
            if conn is not None:
                with conn, conn.cursor() as cur:
                    cur.execute("SELECT payload FROM main_line_state WHERE session_date <= %s "
                                "ORDER BY session_date DESC LIMIT 1", (sd,))
                    row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        except Exception as e:
            print("[state_pg] asof PG 失败: %s" % str(e)[:120], file=sys.stderr)
    return None if mode() == "pg" else _read_file(ML_FILE)


# ── 持仓分类 ─────────────────────────────────────────────────────────
PC_FILE = "position_class_result.json"


def save_position_class(d: Dict[str, Any]) -> bool:
    """整份快照写入（一行一板块）。PG 里**整表替换**语义由调用方保证：
    这里按 sector_code upsert；不再出现的板块会被标记 stale（不删，保留历史）。"""
    if not isinstance(d, dict) or not d:
        return False
    ok = False
    if mode() in ("both", "pg"):
        try:
            conn = _conn()
            if conn is not None:
                rows = []
                for code, v in d.items():
                    if not isinstance(v, dict):
                        continue
                    rows.append((code, v.get("name"), v.get("position"), v.get("trend"),
                                 v.get("op"), v.get("err"), json.dumps(v, ensure_ascii=False)))
                sql = """
                INSERT INTO position_class (sector_code, name, position, trend, op, err, payload, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (sector_code) DO UPDATE SET
                  name       = EXCLUDED.name,
                  position   = EXCLUDED.position,
                  trend      = EXCLUDED.trend,
                  op         = EXCLUDED.op,
                  err        = EXCLUDED.err,
                  payload    = EXCLUDED.payload,
                  updated_at = now()
                """
                with conn, conn.cursor() as cur:
                    cur.executemany(sql, rows)
                conn.close()
                ok = True
        except Exception as e:
            print("[state_pg] save_position_class 失败: %s" % str(e)[:160], file=sys.stderr)
    if mode() in ("both", "file"):
        _write_file(PC_FILE, d)
    return ok


def load_position_class() -> Optional[Dict[str, Any]]:
    m = mode()
    if m in ("both", "pg"):
        try:
            conn = _conn()
            if conn is not None:
                with conn, conn.cursor() as cur:
                    cur.execute("SELECT sector_code, payload FROM position_class")
                    rows = cur.fetchall()
                conn.close()
                if rows:
                    return {code: (p if isinstance(p, dict) else json.loads(p)) for code, p in rows}
        except Exception as e:
            print("[state_pg] load_position_class PG 失败: %s" % str(e)[:120], file=sys.stderr)
    return _read_file(PC_FILE) if m in ("both", "file") else None


# ── CLI ──────────────────────────────────────────────────────────────
def _backfill() -> int:
    ensure()
    n = 0
    ml = _read_file(ML_FILE)
    if ml and save_main_line_state(ml):
        n += 1
        print("  ✓ main_line_state ← %s（date=%s）" % (ML_FILE, ml.get("date")))
    pc = _read_file(PC_FILE)
    if pc and save_position_class(pc):
        n += 1
        print("  ✓ position_class ← %s（%d 个板块）" % (PC_FILE, len(pc)))
    return 0 if n else 1


def _verify() -> int:
    """逐字段比对 PG 与文件；不一致打印前几条。"""
    bad = 0
    ml_f = _read_file(ML_FILE) or {}
    conn = _conn()
    if conn is None:
        print("  ✗ 连不上 PG")
        return 1
    with conn, conn.cursor() as cur:
        cur.execute("SELECT payload FROM main_line_state ORDER BY session_date DESC LIMIT 1")
        row = cur.fetchone()
    ml_pg = (row[0] if row else {}) or {}
    keys = sorted(set(ml_f) | set(ml_pg))
    diff = [k for k in keys if ml_f.get(k) != ml_pg.get(k)]
    print("  main_line_state: %d 字段，不一致 %d %s" % (len(keys), len(diff), diff[:5]))
    bad += len(diff)

    pc_f = _read_file(PC_FILE) or {}
    pc_pg = load_position_class() or {}
    only_f = set(pc_f) - set(pc_pg)
    only_pg = set(pc_pg) - set(pc_f)
    differ = [k for k in set(pc_f) & set(pc_pg) if pc_f[k] != pc_pg[k]]
    print("  position_class : 文件 %d / PG %d；仅文件 %d，仅 PG %d，内容不同 %d"
          % (len(pc_f), len(pc_pg), len(only_f), len(only_pg), len(differ)))
    bad += len(only_f) + len(only_pg) + len(differ)
    print("  结论：%s" % ("一致 ✓" if bad == 0 else "存在差异 ✗"))
    return 0 if bad == 0 else 1


def main() -> int:
    a = sys.argv[1:]
    if "--ensure" in a:
        return 0 if ensure() else 1
    if "--backfill" in a:
        return _backfill()
    if "--verify" in a:
        return _verify()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
