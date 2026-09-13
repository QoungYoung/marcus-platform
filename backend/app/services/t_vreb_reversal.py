# -*- coding: utf-8 -*-
"""量窒息 + 顺风反包（5条件A 主板）→ 做T底仓建仓候选。

接入做T系统（openspec change integrate-vreb-reversal-into-t-system）：
- 信号 = 缩量20日低点 + 平盘±1% + 小实体(十字星) → 次日放量≥1.5x 大红柱覆盖前高
- 5条件A = 所属行业当日≥2% + 沪深300站上MA20 + 当日≥1% + MA20向上
- 只做主板(剔除300/301/688/689)、剔除确认日一字板(收盘>=0.999*up_limit)
- 做T评分(0-1)：0.25 非冲高度 + 0.20 涨幅适中 + 0.15 缩量干净 + 0.15 行业强度 + 0.10 市场强度 + 0.15 日内振幅
- 逆风判定 + 沪深300 月度震荡/趋势分类器（只做趋势向上月）
数据源沿用本工作区 parquet；可按 env 覆盖路径。
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Optional

import duckdb
import numpy as np
import pandas as pd

_STOCK_DAILY = os.environ.get("VREB_STOCK_DAILY", "data/股票数据/行情数据/stock_daily.parquet")
_CI_L1 = os.environ.get("VREB_CI_L1", "data/指数数据/ci_l1_daily.parquet")
_CSI300 = os.environ.get("VREB_CSI300", "data/指数数据/index_daily/000300.SH.parquet")

_SCORE_NONPEAK = 0.25
_SCORE_MODERATE = 0.20
_SCORE_CLEAN = 0.15
_SCORE_INDUSTRY = 0.15
_SCORE_MARKET = 0.10
_SCORE_AMP = 0.15

_MAIN_BOARD_PREFIX = ("60", "00")
_CHINEXT = ("300", "301")
_STAR = ("688", "689")


def _is_main_board(ts_code: str) -> bool:
    c = ts_code.split(".")[0]
    if c.startswith(_CHINEXT) or c.startswith(_STAR):
        return False
    return c.startswith(_MAIN_BOARD_PREFIX)


def _regime_classifier_daily(ma20_slope: float, above: bool) -> str:
    if pd.isna(ma20_slope):
        return "震荡"
    if ma20_slope > 0.5 and above:
        return "趋势向上"
    if ma20_slope < -0.5 and not above:
        return "趋势向下"
    return "震荡"


def monthly_regime(as_of: Optional[date] = None) -> Dict[str, Any]:
    """沪深300 月度震荡/趋势分类。返回 {'regime','month','map'}。"""
    con = duckdb.connect()
    try:
        df = con.execute(
            f"SELECT CAST(trade_date AS DATE) d, close FROM read_parquet('{_CSI300}') ORDER BY d"
        ).fetchdf()
    finally:
        con.close()
    df = df[df["d"] >= pd.Timestamp("2010-01-01")].reset_index(drop=True)
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma20_10"] = df["ma20"].shift(10)
    df["ma20_slope"] = (df["ma20"] / df["ma20_10"] - 1) * 100
    df["above"] = df["close"] > df["ma20"]
    df["reg"] = [_regime_classifier_daily(s, a) for s, a in zip(df["ma20_slope"], df["above"])]
    df["ym"] = df["d"].dt.to_period("M").astype(str)
    pivot = df.groupby("ym")["reg"].value_counts(normalize=True).unstack(fill_value=0)
    regime_map: Dict[str, str] = {}
    for m, r in pivot.iterrows():
        fl = r.get("震荡", 0); up = r.get("趋势向上", 0); dn = r.get("趋势向下", 0)
        if fl >= 0.5:
            regime_map[m] = "震荡"
        elif up >= dn and up >= 0.4:
            regime_map[m] = "趋势向上"
        elif dn >= up and dn >= 0.4:
            regime_map[m] = "趋势向下"
        else:
            regime_map[m] = "震荡"
    target = (as_of or date.today()).strftime("%Y-%m")
    return {"regime": regime_map.get(target, "震荡"), "month": target, "map": regime_map}


def is_counter_tailwind(regime_state: Dict[str, Any]) -> bool:
    if regime_state.get("halt"):
        return True
    if regime_state.get("below_ma20"):
        return True
    if regime_state.get("day_drop", 0) < 0:
        return True
    if regime_state.get("industry_breadth", 1.0) < 0.5:
        return True
    return False


def _score(row: pd.Series) -> float:
    nonpeak = max(0.0, min(1.0, (0.995 - row["cs"]) / 0.03))
    moderate = max(0.0, min(1.0, 1 - abs(row["pct1"] - 4.0) / 2.2))
    clean = max(0.0, min(1.0, (0.9 - row["vr_min"]) / 0.3))
    industry = max(0.0, min(1.0, (row["ind_pct_T1"] - 2.0) / 3.0))
    market = max(0.0, min(1.0, (row["mkt_pct"] - 1.0) / 2.0))
    amp_s = max(0.0, min(1.0, row["amp"] / 0.05))
    return float(np.clip(
        _SCORE_NONPEAK * nonpeak + _SCORE_MODERATE * moderate + _SCORE_CLEAN * clean
        + _SCORE_INDUSTRY * industry + _SCORE_MARKET * market + _SCORE_AMP * amp_s,
        0.0, 1.0))


def compute_vreb_reversal_candidates(trade_date: Optional[date] = None,
                                     top_n: int = 5) -> List[Dict[str, Any]]:
    """生成量窒息+顺风反包(5条件A 主板)做T底仓建仓候选，按做T评分降序、每日取 top_n。"""
    if not is_enabled():
        return []
    con = duckdb.connect()
    try:
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE base AS
            SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol,
                   adj_factor, up_limit, close*adj_factor ac
            FROM read_parquet('{_STOCK_DAILY}')
            WHERE is_st=0 AND suspend_type='N' AND listed_days>=120
              AND vol>0 AND open>0 AND high>0 AND low>0 AND close>0 AND pre_close>0
              AND pct_chg BETWEEN -30 AND 30
              AND high>=low AND high>=open AND high>=close AND low<=open AND low<=close
        """)
        leads = ", ".join("LEAD(high," + str(k) + ") OVER w h" + str(k) for k in range(2, 7))
        leadl = ", ".join("LEAD(low," + str(k) + ") OVER w l" + str(k) for k in range(2, 7))
        leadc = ", ".join("LEAD(ac," + str(k) + ") OVER w / ac - 1 AS f" + str(k) for k in range(1, 7))
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE win AS
            SELECT b.*, MIN(vol) OVER w20 minv20, AVG(vol) OVER w20 avgv20,
                   LEAD(trade_date) OVER w date1, LEAD(pct_chg) OVER w pct1,
                   LEAD(vol) OVER w vol1, LEAD(high) OVER w high1, LEAD(low) OVER w low1,
                   LEAD(close) OVER w close1, LEAD(open) OVER w open1, LEAD(up_limit) OVER w up1,
                   {leads}, {leadl}, {leadc}
            FROM base b WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date),
                 w20 AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
        """)
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE sig AS
            SELECT * FROM win
            WHERE (vol<minv20) AND (pct_chg>=-1 AND pct_chg<=1)
              AND (high>low AND abs(close-open)/(high-low)<=0.15 AND abs(close-open)<=0.01*close)
              AND pct1>=2 AND vol1>=1.5*vol AND close1>high
        """)
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE sec_map AS
            SELECT stock, min(ts_code) ind
            FROM (SELECT DISTINCT unnest(con_codes) stock, ts_code FROM read_parquet('{_CI_L1}')) t
            GROUP BY stock
        """)
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE ind_daily AS
            SELECT ts_code ind, trade_date, pct_change ind_pct FROM read_parquet('{_CI_L1}')
        """)
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE idxf AS
            SELECT CAST(trade_date AS DATE) d, pct_chg mkt_pct, close,
              avg(close) OVER (ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) ma20,
              avg(close) OVER (ORDER BY trade_date ROWS BETWEEN 25 PRECEDING AND 6 PRECEDING) ma20_lag5
            FROM read_parquet('{_CSI300}')
        """)
        df = con.execute("""
            SELECT s.*, i1.ind_pct AS ind_pct_T1, x.mkt_pct,
              (x.close > x.ma20) AS above, (x.ma20 > x.ma20_lag5) AS ma20up
            FROM sig s
            LEFT JOIN sec_map sm ON s.ts_code = sm.stock
            LEFT JOIN ind_daily i1 ON s.date1 = i1.trade_date AND sm.ind = i1.ind
            LEFT JOIN idxf x ON CAST(s.date1 AS DATE) = x.d
        """).fetchdf()
    finally:
        con.close()

    g = df[(df["ind_pct_T1"] >= 2) & df["above"] & (df["mkt_pct"] >= 1) & df["ma20up"]].copy()
    g = g[g["ts_code"].map(_is_main_board)].copy()
    g["cs"] = g["close1"] / g["high1"]
    g["vr_min"] = g["vol"] / g["minv20"]
    g["amp"] = (g["high1"] - g["low1"]) / g["low1"]
    g = g[g["close1"] < g["up1"] * 0.999].copy()
    g["score"] = g.apply(_score, axis=1)
    g = g.sort_values(["date1", "score"], ascending=[True, False])
    if trade_date is not None:
        g = g[g["date1"] == pd.Timestamp(trade_date)]
    else:
        latest = g["date1"].max()
        g = g[g["date1"] == latest]
    top = g.groupby("date1", sort=False).head(top_n)
    reg = monthly_regime(trade_date or (g["date1"].max().date() if len(g) else None))
    out: List[Dict[str, Any]] = []
    for _, r in top.iterrows():
        reasons: List[str] = []
        if r["cs"] < 0.97:
            reasons.append("非冲高")
        if 2 <= r["pct1"] <= 6:
            reasons.append("涨幅中等")
        if r["vr_min"] < 0.75:
            reasons.append("缩量干净")
        if r["ind_pct_T1"] >= 2:
            reasons.append("行业%.1f%%" % r["ind_pct_T1"])
        if r["amp"] >= 0.03:
            reasons.append("振幅%.0f%%" % round(r["amp"] * 100))
        reasons.append("顺风")
        out.append({
            "trade_date": str(r["date1"])[:10], "symbol": r["ts_code"],
            "score": round(float(r["score"]), 3), "reasons": reasons,
            "trend": "多头", "source": "vreb_reversal",
            "cs": round(float(r["cs"]), 3), "pct1": round(float(r["pct1"]), 2),
            "amp": round(float(r["amp"]), 3), "month_regime": reg["regime"],
        })
    return out


def persist_vreb_candidates(trade_date: Optional[date] = None, top_n: int = 5,
                            require_trend_up: bool = True) -> Dict[str, Any]:
    """计算并把 vreb-反包候选写入 t_build_scan_results（source='vreb_reversal'）。

    7.2 月度门控：require_trend_up=True 时仅当月为「趋势向上」才写入；震荡/下跌月跳过并返回 note。
    返回 {'written': [{id,symbol,score}], 'month_regime':..., 'skipped':bool, 'note':...}
    """
    import json as _json
    from sqlalchemy import text
    from app.database import SessionLocal

    if not is_enabled():
        return {"written": [], "month_regime": "disabled", "skipped": True,
                "note": "VREB_REVERSAL_ENABLED=0 已禁用(6.3回滚开关)"}
    cands = compute_vreb_reversal_candidates(trade_date=trade_date, top_n=top_n)
    reg = cands[0]["month_regime"] if cands else monthly_regime(trade_date)["regime"]
    if not cands:
        return {"written": [], "month_regime": reg, "skipped": False, "note": "无候选"}
    if require_trend_up and reg != "趋势向上":
        return {"written": [], "month_regime": reg, "skipped": True,
                "note": "当月为%s，按7.2只做趋势向上月，跳过写入" % reg}
    written = []
    db = SessionLocal()
    try:
        for c in cands:
            exists = db.execute(text(
                "SELECT 1 FROM t_build_scan_results WHERE trade_date=:td AND symbol=:sym AND source='vreb_reversal'"
            ), {"td": c["trade_date"], "sym": c["symbol"]}).fetchone()
            if exists:
                continue
            row = db.execute(text(
                "INSERT INTO t_build_scan_results "
                "(trade_date, symbol, score, reasons, trend, status, source) "
                "VALUES (:td, :sym, :score, :reasons, :trend, 'pending', 'vreb_reversal') "
                "RETURNING id"
            ), {
                "td": c["trade_date"], "sym": c["symbol"], "score": float(c["score"]),
                "reasons": _json.dumps(c["reasons"], ensure_ascii=False),
                "trend": str(c["trend"])[:250],
            }).fetchone()
            written.append({"id": row[0], "symbol": c["symbol"], "score": c["score"]})
        db.commit()
    finally:
        db.close()
    return {"written": written, "month_regime": reg, "skipped": False,
            "note": "已写入 %d 条(源=vreb_reversal)" % len(written)}


def confirmation_window_conditions(symbol: str, confirm_date: str,
                                   clean_price: float) -> Dict[str, Any]:
    """确认日后1-5交易日窗口的低吸/高抛做T条件（用 list_t_fields 字段 expression）。"""
    return {
        "symbol": symbol, "confirm_date": confirm_date, "window_days": 5,
        "low_buy": {"and": [
            {"field": "quote.current", "op": "<=", "value": round(clean_price * 0.97, 2)},
            {"field": "vol_ratio", "op": ">=", "value": 1.5},
        ]},
        "high_sell": {"and": [
            {"field": "quote.current", "op": ">=", "value": round(clean_price * 1.03, 2)},
            {"field": "vol_ratio", "op": ">=", "value": 1.5},
        ]},
        "note": "仅在确认日+1..5交易日顺风窗口内有效，逆风(regime_gate)不触发",
    }


def fetch_daily_via_tushare(ts_code: str, start: str, end: str) -> List[Dict[str, Any]]:
    """1.1 拉个股日线（统一走 datahubco/promax 中继，见 core/tushare_relay.py），补本地截止后的缺失。

    返回 [{trade_date, open, high, low, close, pct_chg, vol}...]（best-effort，失败返回 []）。
    调用方可并入日线数据以刷新 VREB_STOCK_DAILY 覆盖范围。
    """
    try:
        from app.core.trading._api_config import get_tushare_pro
        df = get_tushare_pro().daily(ts_code=ts_code, start_date=start, end_date=end)
    except Exception:
        return []
    if df is None or len(df) == 0:
        return []
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            out.append({
                "trade_date": str(row["trade_date"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "pct_chg": float(row.get("pct_chg") or 0.0),
                "vol": float(row.get("vol") or 0.0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def is_enabled() -> bool:
    """6.3 回滚开关：VREB_REVERSAL_ENABLED != '0' 才启用（默认启用）。"""
    return os.environ.get("VREB_REVERSAL_ENABLED", "1") != "0"


def vreb_regime_gate(regime_state: Dict[str, Any]) -> str:
    """5.2 叠加本策略逆风判定：逆风 → 'BLOCKED'（不开新做T），否则继承 regime_state 的 gate。"""
    if is_counter_tailwind(regime_state):
        return "BLOCKED"
    return str(regime_state.get("gate_low_buy", "ALLOWED"))


def validate_tail_close_reference(symbol: str, as_of: date,
                                  price: float, tol_pct: float = 2.0) -> Dict[str, Any]:
    """3.2 防前视：校验建仓参考价是否接近确认日(最新信号日)尾盘收盘价。

    若传入价为确认日开盘价/盘中高点（偏离收盘>tol_pct），返回 ok=False（拒绝，防前视）。
    """
    close = None
    try:
        import pandas as _pd
        import duckdb as _dd
        con = _dd.connect()
        try:
            df = con.execute(
                "SELECT CAST(trade_date AS DATE) d, close FROM read_parquet(?) WHERE ts_code=? "
                "ORDER BY d DESC LIMIT 1", [_STOCK_DAILY, symbol]).fetchdf()
        finally:
            con.close()
        if len(df):
            close = float(df.iloc[0]["close"])
    except Exception:
        close = None
    if close is None:
        return {"ok": True, "note": "无法取到日线，跳过校验（由网关终检）"}
    dev = abs(price - close) / close * 100.0
    return {"ok": dev <= tol_pct, "close": round(close, 3), "price": round(price, 3),
            "dev_pct": round(dev, 2), "note": "偏离收盘%.2f%%" % dev}


def auto_gen_vreb_window_conditions(symbol: str, confirm_date: str, clean_price: float,
                                    account_id: str = "t") -> Dict[str, Any]:
    """4.1 为已建底仓在确认日后1-5窗口生成 low_buy/high_sell 做T条件并入库(t_conditions)。"""
    from app.services.t_db import upsert_condition
    w = confirmation_window_conditions(symbol, confirm_date, clean_price)
    created = []
    for direction, expr in (("buy", w["low_buy"]), ("sell", w["high_sell"])):
        cond = {
            "account_id": account_id, "symbol": symbol,
            "trigger_kind": "custom", "direction": direction,
            "expression": expr, "reinform_price": clean_price,
            "start_time": "09:30", "end_time": "15:00",
            "regime_gate": "ALLOWED",
        }
        cid = upsert_condition(cond)
        created.append({"direction": direction, "condition_id": cid})
    return {"symbol": symbol, "confirm_date": confirm_date, "created": created,
            "window": "confirm+1..5 trading days", "note": w["note"]}

