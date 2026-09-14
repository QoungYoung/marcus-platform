# -*- coding: utf-8 -*-
"""R9「不突破 + 卖单加大 + 无带动」的**数据底座**（2026-09-14 立项）。

狼大依据：出货确认要看**卖单是否在加大**（盘口/分时语义）——原审计判"数据不足"其实不准确：
腾讯 qt 单次返回 **88 个字段**，其中就含**五档**（买一~买五 = [9]~[18]、卖一~卖五 = [19]~[28]），
只是 `t_data_sources.fetch_tencent_quote` 只解析了 9 个字段。

**为什么先只"存 + 记"、不上动作**：DB 里没有任何历史盘口表 → 五档**无法回测**。
按本仓纪律「无证据不上线」，先积累 4–8 周快照，再决定是否作为卖腿判据。

本模块只做三件事：`parse_depth()`（纯函数，可测）/ `depth_metrics()`（卖压代理）/ `record()`（落 PG）。
落库表：`t_orderbook_snapshots`（首次写入自动建表）。
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

QT_URL = "http://qt.gtimg.cn/q="
TABLE = "t_orderbook_snapshots"


def _norm(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    if s.startswith(("SH", "SZ")):
        return s[:2].lower() + s[2:]
    if "." in s:
        code, _, mkt = s.partition(".")
        return ("sh" if mkt.upper() == "SH" else "sz") + code
    return s.lower()


def parse_depth(fields: List[str]) -> Dict[str, Any]:
    """从 qt 字段数组解析五档。越界/非数 → 该档为 None（**不把缺失当 0**）。"""
    def _f(i):
        try:
            v = float(fields[i])
            return v if v > 0 else None
        except (IndexError, TypeError, ValueError):
            return None

    bid = [(_f(9 + 2 * i), _f(10 + 2 * i)) for i in range(5)]     # 买一~买五（价, 量）
    ask = [(_f(19 + 2 * i), _f(20 + 2 * i)) for i in range(5)]    # 卖一~卖五（价, 量）
    return {"bid": bid, "ask": ask}


def depth_metrics(depth: Dict[str, Any], cur: Optional[float] = None) -> Dict[str, Any]:
    """卖压代理指标（**全是代理**，语料只给定性"卖单加大"）：
    · bid_vol/ask_vol：五档买卖总量（手）
    · wall_ratio = ask_vol / (bid_vol + ask_vol)：卖压占比（>0.5 偏空）
    · ask_big_share：卖一档占五档卖量的比例（"大单压在卖一"）
    · near_ratio：现价上方 1% 内的卖量 / 总卖量（"贴着上方的卖墙"）
    """
    b = [v for _p, v in (depth.get("bid") or []) if v]
    a = [v for _p, v in (depth.get("ask") or []) if v]
    bv, av = sum(b), sum(a)
    out = {"bid_vol": bv, "ask_vol": av,
           "wall_ratio": round(av / (av + bv), 4) if (av + bv) > 0 else None,
           "ask_big_share": round(a[0] / av, 4) if av > 0 and a else None,
           "near_ratio": None}
    if cur and av > 0:
        near = sum(v for p, v in (depth.get("ask") or []) if p and v and p <= cur * 1.01)
        out["near_ratio"] = round(near / av, 4)
    return out


def fetch_depth(symbols: List[str], timeout: int = 8) -> Dict[str, Dict[str, Any]]:
    """取五档：直接打腾讯 qt（与 t_data_sources 同一源，只是**多解析五档**）。"""
    out: Dict[str, Dict[str, Any]] = {}
    if not symbols:
        return out
    try:
        url = QT_URL + ",".join(_norm(s) for s in symbols)
        raw = urllib.request.urlopen(url, timeout=timeout).read().decode("gbk", "replace")
    except Exception as e:  # noqa: BLE001
        print("[orderbook] qt 拉取失败: %s" % str(e)[:120])
        return out
    for line in raw.split(";"):
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        sym = key.strip().replace("v_", "")
        f = val.strip().strip('"').split("~")
        if len(f) < 40:
            continue
        d = parse_depth(f)
        cur = None
        try:
            cur = float(f[3]) or None
        except (IndexError, ValueError):
            pass
        out[sym.upper()] = {"symbol": sym.upper(), "name": f[1] if len(f) > 1 else "",
                            "current": cur, "depth": d, "metrics": depth_metrics(d, cur)}
    return out


def _ensure_table(cur) -> None:
    cur.execute("""CREATE TABLE IF NOT EXISTS %s (
        id BIGSERIAL PRIMARY KEY,
        snap_ts TIMESTAMP NOT NULL,
        symbol TEXT NOT NULL,
        current NUMERIC,
        bid_vol NUMERIC, ask_vol NUMERIC, wall_ratio NUMERIC,
        ask_big_share NUMERIC, near_ratio NUMERIC,
        depth JSONB, tag TEXT
    )""" % TABLE)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ob_sym_ts ON %s (symbol, snap_ts)" % TABLE)


def record(symbols: List[str], tag: str = "", dsn: Optional[str] = None) -> int:
    """取一次五档并落库，返回写入行数。**只记不用**（R9 积累期）。"""
    data = fetch_depth(symbols)
    if not data:
        return 0
    import psycopg2
    dsn = dsn or os.getenv("DATABASE_URL",
                           "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        _ensure_table(cur)
        n = 0
        for sym, rec in data.items():
            m = rec.get("metrics") or {}
            cur.execute(
                "INSERT INTO %s (snap_ts,symbol,current,bid_vol,ask_vol,wall_ratio,ask_big_share,"
                "near_ratio,depth,tag) VALUES (%%s,%%s,%%s,%%s,%%s,%%s,%%s,%%s,%%s,%%s)" % TABLE,
                (ts, sym, rec.get("current"), m.get("bid_vol"), m.get("ask_vol"),
                 m.get("wall_ratio"), m.get("ask_big_share"), m.get("near_ratio"),
                 json.dumps(rec.get("depth"), ensure_ascii=False), tag))
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()
