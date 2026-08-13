# -*- coding: utf-8 -*-
"""ETF 池刷新 — 使用 Tushare etf_basic 全量同步基础信息（ETF 名称等）。

现有 etf_pool 数据来自雪球/东财（data JSONB 含行情快照），
本模块只增补/修正基础信息：名称 + Tushare 元数据，不覆盖已有行情字段。
"""
import json
from datetime import datetime


def _ts_code_to_symbol(ts_code: str) -> str:
    """510300.SH -> SH510300；159001.SZ -> SZ159001。"""
    code, _, exchange = ts_code.partition(".")
    return f"{exchange}{code}"


def _clean(value):
    """NaN -> None，其余原样返回（jsonb 不支持 NaN）。"""
    if value is None:
        return None
    try:
        if value != value:  # pandas NaN
            return None
    except Exception:
        pass
    return value


def etf_rows_from_df(df) -> list:
    """把 etf_basic 返回的 DataFrame 映射为 upsert 行（纯函数，便于测试）。

    只保留已上市（list_status == 'L'）的沪深 ETF，symbol 统一为 SH/SZ 前缀格式。
    """
    rows = []
    for _, r in df.iterrows():
        ts_code = str(r.get("ts_code") or "").strip()
        exchange = str(r.get("exchange") or "").upper()
        status = str(r.get("list_status") or "").strip()
        if not ts_code or exchange not in ("SH", "SZ") or status != "L":
            continue
        symbol = _ts_code_to_symbol(ts_code)
        name = str(_clean(r.get("csname")) or _clean(r.get("extname")) or "").strip()
        if not name:
            continue
        meta = {
            "ts_code": ts_code,
            "csname": name,
            "index_code": _clean(r.get("index_code")),
            "index_name": _clean(r.get("index_name")),
            "list_date": _clean(r.get("list_date")),
            "list_status": status,
            "etf_type": _clean(r.get("etf_type")),
            "mgr_name": _clean(r.get("mgr_name")),
            "mgt_fee": _clean(r.get("mgt_fee")),
        }
        rows.append({"symbol": symbol, "name": name, "data": meta})
    return rows


def sync_etf_pool_from_tushare(pro=None) -> dict:
    """调用 Tushare etf_basic 全量刷新 etf_pool（upsert，保留已有行情快照）。

    返回: {"total": ..., "inserted": ..., "updated": ..., "updated_at": ...}
    """
    if pro is None:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
    df = pro.etf_basic()
    rows = etf_rows_from_df(df)
    if not rows:
        raise RuntimeError("etf_basic 未返回可用的已上市沪深 ETF 行")

    from sqlalchemy import text
    from app.database import SessionLocal

    stmt = text(
        """
        INSERT INTO etf_pool (symbol, name, sector, catalyst_type, priority, data, updated_at)
        VALUES (:symbol, :name, '', '', 3, :data, :now)
        ON CONFLICT (symbol) DO UPDATE SET
            name = EXCLUDED.name,
            data = EXCLUDED.data || COALESCE(etf_pool.data, '{}'::jsonb),
            updated_at = EXCLUDED.updated_at
        RETURNING (xmax = 0) AS inserted
        """
    )
    now = datetime.now().isoformat()
    inserted = updated = 0
    db = SessionLocal()
    try:
        for row in rows:
            res = db.execute(
                stmt,
                {
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "data": json.dumps(row["data"], ensure_ascii=False),
                    "now": now,
                },
            )
            if res.fetchone()[0]:
                inserted += 1
            else:
                updated += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {
        "total": len(rows),
        "inserted": inserted,
        "updated": updated,
        "updated_at": now,
    }
