# -*- coding: utf-8 -*-
"""arm_db.py —— **每臂一个 sqlite**（账本 §9.533，用户方案 ✓）

**用户的想法**（原话）：「如果你单臂隔离，那就**每个臂做一个 sqlite**，然后**数据落库**，
这样也**不用存在这么多 json**，每个臂的数据**都可以查**」✓ —— 这个方案同时解掉今天三个坑 ✓：

| 坑 ✓ | 为什么它解掉 ✓ |
|---|---|
| 多臂数字打架 ✗（看板 `drabt16` vs 我算 `drabt35` ✓）| **每臂独立库** ⇒ 天然隔离 ✓ |
| 状态写文件 ⇒ **不原子/难查/被重置切碎** ✗（`ambush_promoted.json` 0 个 ✓）| **落库可查** ✓ |
| 回测写共享 PG ⇒ **有污染生产风险** ✗ | 它是**沙箱内的文件** ⇒ 不碰生产 ✓ |

**位置**：`<arm_root>/<account>.sqlite`（例 `data/_bt_t35/drabt35.sqlite` ✓，随沙箱一起拷贝/回放 ✓）
**表**：`nav`（净值 ✓）｜`pos_meta`（highest_price／转正 ✓）｜`legs`（腿 ✓）｜`events`（事件留痕 ✓）

用法：
  from arm_db import connect, put_nav, put_pos_meta, put_event
  `.venv/bin/python jobs/arm_db.py --account drabt35 --show`   # 看内容 ✓
"""
from __future__ import annotations
import json, os, sqlite3, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = """
CREATE TABLE IF NOT EXISTS nav (
  account TEXT, day TEXT, at TEXT, cash REAL, mv REAL, equity REAL, ret REAL, n_pos INTEGER,
  PRIMARY KEY (account, day, at));
CREATE TABLE IF NOT EXISTS pos_meta (
  account TEXT, symbol TEXT, entry_date TEXT, avg_price REAL, highest_price REAL,
  promoted INTEGER DEFAULT 0, promoted_at TEXT, promote_price REAL, updated_at TEXT,
  PRIMARY KEY (account, symbol));
CREATE TABLE IF NOT EXISTS legs (
  account TEXT, day TEXT, symbol TEXT, kind TEXT, theme TEXT, stage TEXT, src TEXT, payload TEXT,
  PRIMARY KEY (account, day, symbol, kind));
CREATE TABLE IF NOT EXISTS arm_state (
  account TEXT, key TEXT, value TEXT, updated_at TEXT, PRIMARY KEY (account, key));
CREATE TABLE IF NOT EXISTS events (
  account TEXT, day TEXT, at TEXT, kind TEXT, symbol TEXT, detail TEXT);
"""


def arm_root() -> str:
    """臂根目录：显式 root > ARM_ROOT > 由 DATA_DIR 推导 > 回测默认。

    为什么需要（账本 §9.539）：`DATA_DIR` 可能是“臂根”也可能是“臂根/<天>”，
    不统一推导 ⇒ 生产/回测会写到不同地方（今天 `ambush_promoted.json` 就吃过这个亏）。
    """
    r = os.getenv("ARM_ROOT")
    if r:
        return r
    d = str(os.getenv("DATA_DIR") or "").strip()
    if d:
        b = os.path.basename(d.rstrip("/"))
        return os.path.dirname(d.rstrip("/")) if (len(b) == 8 and b.isdigit()) else d
    return os.path.join(REPO, "data", "_bt_t35")


def db_path(account: str = "", root: str = "") -> str:
    acc = account or os.getenv("T_MONITOR_ACCOUNT", "drabt35") or "drabt35"
    r = root or arm_root()
    return os.path.join(r, "%s.sqlite" % acc)


def connect(account: str = "", root: str = "") -> sqlite3.Connection:
    p = db_path(account, root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA temp_store=MEMORY")
    c.executescript(SCHEMA)
    return c


def put_nav(c, account, day, cash, mv, equity, ret, n_pos, at=""):
    c.execute("INSERT OR REPLACE INTO nav (account,day,at,cash,mv,equity,ret,n_pos) VALUES (?,?,?,?,?,?,?,?)",
              (account, day, at or time.strftime("%H:%M:%S"), cash, mv, equity, ret, n_pos))
    c.commit()


def put_pos_meta(c, account, symbol, entry_date="", avg_price=0.0, highest_price=0.0,
                 promoted=False, promoted_at="", promote_price=0.0):
    c.execute("INSERT OR REPLACE INTO pos_meta (account,symbol,entry_date,avg_price,highest_price,"
              "promoted,promoted_at,promote_price,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
              (account, symbol, entry_date, avg_price, highest_price, 1 if promoted else 0,
               promoted_at, promote_price, time.strftime("%Y-%m-%d %H:%M:%S")))
    c.commit()


def put_state(c, account, key, value):
    """通用跨日状态（账本 §9.538 ✓）：把原本散在 **8 个 carry JSON** 里的状态收进臂库 ✓。"""
    c.execute("INSERT OR REPLACE INTO arm_state (account,key,value,updated_at) VALUES (?,?,?,?)",
              (account, str(key), json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value,
               time.strftime("%Y-%m-%d %H:%M:%S")))
    c.commit()


def get_state(c, account, key, default=None):
    r = c.execute("SELECT value FROM arm_state WHERE account_id=? AND key=?", (account, key)).fetchone() \
        if False else c.execute("SELECT value FROM arm_state WHERE account=? AND key=?", (account, key)).fetchone()
    if not r:
        return default
    try:
        return json.loads(r[0])
    except Exception:
        return r[0]


def put_event(c, account, day, kind, symbol="", detail=""):
    c.execute("INSERT INTO events (account,day,at,kind,symbol,detail) VALUES (?,?,?,?,?,?)",
              (account, day, time.strftime("%H:%M:%S"), kind, symbol, str(detail)[:400]))
    c.commit()


def main() -> int:
    acc, show = "drabt35", False
    for i, a in enumerate(sys.argv):
        if a == "--account" and i + 1 < len(sys.argv): acc = sys.argv[i + 1]
        if a == "--show": show = True
    c = connect(acc)
    print("  臂库 ✓: %s" % db_path(acc))
    if show:
        for t in ("nav", "pos_meta", "legs", "events"):
            try:
                n = c.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
            except Exception:
                n = -1
            print("    %-10s %6d 行 ✓" % (t, n))
        try:
            for r in c.execute("SELECT day,at,cash,mv,equity,ret FROM nav ORDER BY day DESC, at DESC LIMIT 3"):
                print("    nav ✓:", r)
        except Exception:
            pass
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
