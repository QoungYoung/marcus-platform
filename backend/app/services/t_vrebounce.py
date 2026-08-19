# -*- coding: utf-8 -*-
"""做T账户·V反短线（t-vrebounce-short-term）。

基于回测验证的 V反 状态（2009-2026 全样本、次日开盘进场、TP8/SL5/超时12日、
<100亿 市值分桶 PF1.45-1.50、2025+ 样本外 PF1.20-1.43；对比趋势突破同口径 PF~1.05）：

  日频入池：MA20 仍下行（ma20 < 5 日前 ma20）
            & 15 日低点反弹 ≥25%（close[t]/min(low[t-14..t]) - 1 ≥ 0.25）
            & 末端超买（KDJ J ≥ 90 或 RSI6 ≥ 65）
            & 触发日收盘偏离 MA20 ≤ 3%（bias20，防追高——stk_factor_pro 因子分析：
              失败单共性为反弹过猛/偏离均线过远，全样本 2009-2026 加该过滤 PF 1.38→2.14）
            & 总市值 < 100 亿
  -> 盘中实时复核（东财实时价 > 0，可选主力净流入 > 0，默认关）
  -> build_t_position(build_mode='vrebounce')（只动 t 账户）
  -> 短线出场（+8% 清仓 / -5% 硬止损 / 持有 12 交易日超时平仓；
     刻意不做 +5% 减半——回测证明过早兑现盈利拉低 PF，
     且旧实现按每次扫描反复减半，属缺陷）。

账户隔离：扫描结果只写 t_build_scan_results(source='vrebounce')，
建仓走 build_t_position（内部固定 account_id='t'），平仓走 gateway_execute(account_id='t')；
不触碰 stock/golden_pit 资金、持仓或候选池；t 资金不足即跳过，不跨账户划转。

默认关闭灰度：T_VREB_ENABLED=1 才在 worker 注册运行。
"""
import os
import time
import json
import math
import threading
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

ENABLED = os.getenv("T_VREB_ENABLED", "0") == "1"
SCAN_MAX_DAILY = int(os.getenv("VREB_SCAN_DAILY_MAX", "50"))
SCAN_INTERVAL_S = float(os.getenv("VREB_SCAN_INTERVAL_S", "1"))
MCAP_MAX_YI = float(os.getenv("VREB_MCAP_MAX_YI", "100"))
REB_MIN = float(os.getenv("VREB_REB_MIN", "0.25"))       # 15日低点反弹 ≥25%
REB_DAYS = int(os.getenv("VREB_REB_DAYS", "15"))         # 反弹观察窗口（交易日）
BIAS20_MAX = float(os.getenv("VREB_BIAS20_MAX", "0.03"))  # 触发日收盘距 MA20 偏离上限（防追高）
VOLR_MAX = float(os.getenv("VREB_VOLR_MAX", "1.0"))    # 触发日量比上限（缩量企稳，≤1.0 胜率显著更高）
B60_MAX = float(os.getenv("VREB_B60_MAX", "0.0"))      # 触发日收盘距 MA60 偏离上限（仍在 MA60 下方/附近）
J_OVER = float(os.getenv("VREB_J_OVER", "90"))           # KDJ J 超买阈值
RSI_OVER = float(os.getenv("VREB_RSI_OVER", "65"))       # RSI6 超买阈值
TP8 = float(os.getenv("VREB_TP8", "0.08"))
SL5 = float(os.getenv("VREB_SL5", "0.05"))
HOLD_DAYS = int(os.getenv("VREB_HOLD_DAYS", "12"))       # 持有 N 交易日超时平仓
REALTIME_CONFIRM = os.getenv("VREB_REALTIME_CONFIRM", "0") == "1"  # 默认只验价（回测无资金流条件）
MONITOR_INTERVAL_S = float(os.getenv("VREB_MONITOR_INTERVAL_S", "60"))

_instance = None

# A 股篮子（东财 clist fs）
_EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81"


# ────────────────────────────────────────────────────────────────
# 账户隔离常量
# ────────────────────────────────────────────────────────────────
def _account_id() -> str:
    """V反短线固定只服务做T账户。"""
    return "t"


# ────────────────────────────────────────────────────────────────
# 数据获取（可测试注入）
# ────────────────────────────────────────────────────────────────
def _normalize(code: str) -> str:
    code = (code or "").strip().upper()
    if code.startswith(("SH", "SZ", "BJ")):
        return code
    if code and code[0] == "6":
        return "SH" + code
    if code and code[0] in ("0", "3"):
        return "SZ" + code
    return "BJ" + code


def _ts_code(code: str) -> str:
    c = code
    if code.startswith(("SH", "SZ", "BJ")):
        c = code[2:]
    return f"{c}.{'SH' if c and c[0]=='6' else 'SZ'}"


def fetch_top_gain(top_n: int = SCAN_MAX_DAILY) -> list:
    """tushare daily 全市场近 6 个交易日 -> 5 日涨幅榜 TOP-N（V反 候选需近期强势反弹）。

    生产环境东财 push2 不可达（TLS 握手后空响应），改用 tushare（与日线同源）。
    返回 [{"code": "002384", "name": "东山精密", "price": x, "gain5": y}]。
    """
    try:
        pro = _get_pro()
        # 从今天往回找最近 6 个交易日（跳过无数据日）
        dates = []
        d = datetime.now()
        guard = 0
        while len(dates) < 6 and guard < 20:
            ds = d.strftime("%Y%m%d")
            try:
                df = pro.daily(trade_date=ds)
                if df is not None and len(df) > 0:
                    dates.append(ds)
            except Exception:
                pass
            d -= timedelta(days=1)
            guard += 1
        if len(dates) < 2:
            logger.warning("[t-vrebounce] tushare 日线不足（%s）", len(dates))
            return []
        dates = dates[::-1]  # 升序
        frames = []
        for ds in dates:
            df = pro.daily(trade_date=ds)
            if df is not None and len(df):
                frames.append(df[["ts_code", "close"]].rename(columns={"close": "c_" + ds}))
        if len(frames) < 2:
            return []
        import pandas as pd
        merged = frames[0]
        for f in frames[1:]:
            merged = merged.merge(f, on="ts_code", how="outer")
        col_old = "c_" + dates[0]
        col_new = "c_" + dates[-1]
        merged = merged.dropna(subset=[col_old, col_new])
        merged["gain5"] = (merged[col_new] / merged[col_old] - 1) * 100.0
        merged["price"] = merged[col_new]
        merged = merged.sort_values("gain5", ascending=False).head(max(top_n, 10))
        out = []
        for _, r in merged.iterrows():
            code = str(r["ts_code"]).split(".")[0]
            out.append({"code": code, "name": "", "price": float(r["price"]), "gain5": float(r["gain5"])})
        return out
    except Exception as e:
        logger.warning("[t-vrebounce] 涨幅榜获取失败: %s", str(e)[:150])
        return []


def _get_pro():
    from app.core.trading._api_config import get_tushare_pro
    return get_tushare_pro()


def fetch_daily_bars(ts_code: str, start_date: str = "20250101", end_date: str = "") -> list:
    """日线 qfq（Tushare pro_bar）。异常返回 []。"""
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")
    try:
        import tushare as ts
        pro = _get_pro()
        df = ts.pro_bar(api=pro, ts_code=ts_code, adj="qfq", asset="E", freq="D",
                        start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning("[t-vrebounce] 日线获取失败 %s: %s", ts_code, str(e)[:80])
        return []
    if df is None or df.empty:
        return []
    df = df.sort_values("trade_date")
    return [
        {"date": str(r["trade_date"]), "open": float(r["open"]), "high": float(r["high"]),
         "low": float(r["low"]), "close": float(r["close"]), "vol": float(r["vol"])}
        for _, r in df.iterrows()
    ]


def fetch_mcap_yi(ts_code: str) -> Optional[float]:
    """最新总市值（亿元）；异常返回 None。"""
    try:
        pro = _get_pro()
        df = pro.daily_basic(ts_code=ts_code, fields="ts_code,trade_date,total_mv")
    except Exception as e:
        logger.warning("[t-vrebounce] 市值获取失败 %s: %s", ts_code, str(e)[:60])
        return None
    if df is None or df.empty:
        return None
    row = df.sort_values("trade_date").iloc[-1]
    mv = float(row.get("total_mv") or 0)  # 万元
    return mv / 10000.0 if mv > 0 else None


def fetch_realtime(code: str) -> Optional[Dict[str, Any]]:
    """盘中实时复核：腾讯 qt 行情（现价/量比）。失败返回 None。

    东财在生产不可达，改用腾讯（做T主系统同源，实测稳定）。
    """
    try:
        from app.services.t_data_sources import _normalize_symbol, fetch_tencent_quote
        sym = _normalize_symbol(code)  # 'SZ002384' -> 'sz002384'
        q = fetch_tencent_quote([sym])
        item = q.get(sym) or {}
        price = float(item.get("current") or 0)
        if price <= 0:
            return None
        return {"price": price, "main_net": 0.0, "vol_ratio": float(item.get("amplitude") or 0)}
    except Exception as e:
        logger.warning("[t-vrebounce] 实时复核失败 %s: %s", code, str(e)[:60])
        return None


# ────────────────────────────────────────────────────────────────
# 指标（与回测同源：KDJ(9) J 值 / RSI6 / MA20）
# ────────────────────────────────────────────────────────────────
def _kdj_rsi6(seq: List[Dict[str, Any]]) -> Tuple[List[float], List[float]]:
    """返回 (J 序列, RSI6 序列)。与 scripts 回测实现一致（K/D 初值 50、RSI 简单均值）。"""
    n = len(seq)
    J = [50.0] * n
    r6 = [None] * n
    K = 50.0
    D = 50.0
    lows = [x["low"] for x in seq]
    highs = [x["high"] for x in seq]
    closes = [x["close"] for x in seq]
    for i in range(n):
        lo = min(lows[max(0, i - 8):i + 1])
        hi = max(highs[max(0, i - 8):i + 1])
        rsv = 0.0 if hi == lo else (closes[i] - lo) / (hi - lo) * 100
        K = 2 / 3 * K + 1 / 3 * rsv
        D = 2 / 3 * D + 1 / 3 * K
        J[i] = 3 * K - 2 * D
        if i >= 6:
            ups = dns = 0.0
            for k in range(i - 5, i + 1):
                dl = closes[k] - closes[k - 1]
                if dl > 0:
                    ups += dl
                else:
                    dns -= dl
            r6[i] = 100 * ups / (ups + dns) if (ups + dns) > 0 else 50.0
    return J, r6


# ────────────────────────────────────────────────────────────────
# 全市场基础数据落库 + 向量化扫描（V反 状态）
# ────────────────────────────────────────────────────────────────
def _latest_trade_date(pro) -> Optional[str]:
    """最近有 daily 数据的交易日（YYYYMMDD），从今天往回最多试探 12 天。"""
    d = datetime.now()
    for _ in range(12):
        ds = d.strftime("%Y%m%d")
        try:
            df = pro.daily(trade_date=ds)
            if df is not None and len(df) > 0:
                return ds
        except Exception:
            pass
        d -= timedelta(days=1)
    return None


def _trade_dates_between(pro, start: Optional[str], end: str) -> List[str]:
    """(start, end] 之间的交易日（升序 YYYYMMDD），试探法。start=None 时返回空。"""
    if not start:
        return []
    out = []
    d = datetime.strptime(end, "%Y%m%d")
    guard = 0
    while d.strftime("%Y%m%d") > start and guard < 20:
        ds = d.strftime("%Y%m%d")
        if ds > start:
            try:
                df = pro.daily(trade_date=ds)
                if df is not None and len(df) > 0:
                    out.append(ds)
            except Exception:
                pass
        d -= timedelta(days=1)
        guard += 1
    return out[::-1]


def _recent_trade_dates(pro, n: int, end: str) -> List[str]:
    """end 往前 n 个交易日（升序 YYYYMMDD），试探法。

    guard 上限按 n×1.5 个自然日放宽（65 个交易日约需 91+ 个自然日，60 会截断）。
    """
    out = []
    d = datetime.strptime(end, "%Y%m%d")
    guard = 0
    guard_max = max(int(n * 1.5) + 10, 100)
    while len(out) < n and guard < guard_max:
        ds = d.strftime("%Y%m%d")
        try:
            df = pro.daily(trade_date=ds)
            if df is not None and len(df) > 0:
                out.append(ds)
        except Exception:
            pass
        d -= timedelta(days=1)
        guard += 1
    return out[::-1]


def _st_codes(pro) -> set:
    """当前 ST/*ST 名单（全市场一次调用）。"""
    try:
        df = pro.stock_basic(list_status="L", fields="ts_code,name")
        if df is None or df.empty:
            return set()
        return set(df.loc[df["name"].astype(str).str.contains("ST", na=False), "ts_code"])
    except Exception as e:
        logger.warning("[t-vrebounce] ST 名单获取失败: %s", str(e)[:80])
        return set()


def _upsert_market(df) -> None:
    """批量 upsert 全市场日线基础数据到 t_vreb_daily（分批 8000 行）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "ts": str(r["ts_code"]), "d": str(r["trade_date"])[:10],
            "o": float(r["open"] or 0), "h": float(r["high"] or 0), "l": float(r["low"] or 0),
            "c": float(r["close"] or 0), "v": float(r["vol"] or 0),
            "mv": float(r["total_mv"] or 0), "st": bool(r["is_st"]),
        })
    stmt = text(
        "INSERT INTO t_vreb_daily (ts_code, trade_date, open, high, low, close, vol, total_mv, is_st) "
        "VALUES (:ts, :d, :o, :h, :l, :c, :v, :mv, :st) "
        "ON CONFLICT (ts_code, trade_date) DO UPDATE SET "
        "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close, "
        "vol=EXCLUDED.vol, total_mv=EXCLUDED.total_mv, is_st=EXCLUDED.is_st"
    )
    db = SessionLocal()
    try:
        for i in range(0, len(rows), 8000):
            db.execute(stmt, rows[i:i + 8000])
        db.commit()
    finally:
        db.close()


def ensure_market_data(lookback_days: int = 65) -> bool:
    """全市场日线基础数据增量落库：首次拉近 lookback_days 个交易日（≥65 才能算 MA60/b60），
    之后只拉新增交易日；若库内交易日不足 65 天则清空重拉。

    返回 True 表示数据已是最新（含本次更新成功）。
    """
    import pandas as pd
    pro = _get_pro()
    latest = _latest_trade_date(pro)
    if latest is None:
        logger.warning("[t-vrebounce] 无法确定最近交易日")
        return False
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        max_d = db.execute(text("SELECT max(trade_date) FROM t_vreb_daily")).scalar()
        max_d = str(max_d)[:10].replace("-", "") if max_d is not None else None
        n_days = db.execute(text(
            "SELECT count(DISTINCT trade_date) FROM t_vreb_daily")).scalar() or 0
    finally:
        db.close()
    if n_days > 0 and n_days < lookback_days:
        # 历史不足（如从 40 天升级到 65 天）：清空重拉，避免漏算 MA60
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM t_vreb_daily"))
            db.commit()
        finally:
            db.close()
        max_d = None
        logger.info("[t-vrebounce] 基础数据历史不足 %d 天，清空重拉（%d 天）", n_days, lookback_days)
    if max_d and max_d >= latest:
        return True  # 已最新，无需拉取
    need = _trade_dates_between(pro, max_d, latest) if max_d else _recent_trade_dates(pro, lookback_days, latest)
    if not need:
        return True
    st_codes = _st_codes(pro)
    frames = []
    for ds in need:
        try:
            df = pro.daily(trade_date=ds)
            if df is None or df.empty:
                continue
            db2 = pro.daily_basic(trade_date=ds, fields="ts_code,total_mv")
            mv = db2.set_index("ts_code")["total_mv"] if db2 is not None and len(db2) else pd.Series(dtype=float)
            sub = df[["ts_code", "trade_date", "open", "high", "low", "close", "vol"]].copy()
            sub["total_mv"] = sub["ts_code"].map(mv)
            sub["is_st"] = sub["ts_code"].isin(st_codes)
            frames.append(sub)
            time.sleep(0.3)
        except Exception as e:
            logger.warning("[t-vrebounce] 基础数据拉取失败 %s: %s", ds, str(e)[:100])
    if not frames:
        return False
    allf = pd.concat(frames, ignore_index=True)
    _upsert_market(allf)
    logger.info("[t-vrebounce] 基础数据落库 %d 行（%s..%s）", len(allf), need[0], need[-1])
    return True


def _load_market_frame() -> Optional[Any]:
    """读 t_vreb_daily 最近 45 个自然日（约 30+ 交易日）全市场数据。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    import pandas as pd
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT ts_code, trade_date, open, high, low, close, vol, total_mv, is_st "
            "FROM t_vreb_daily WHERE trade_date >= (SELECT max(trade_date) - INTERVAL '45 days' FROM t_vreb_daily)"
        )).mappings().all()
    finally:
        db.close()
    if not rows:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    df["trade_date"] = df["trade_date"].astype(str).str[:10]
    return df


def _vreb_candidates_vectorized(df) -> List[Dict[str, Any]]:
    """全市场向量化 V反 筛选（最新交易日，与 day_filter 同公式）：
    MA20 仍下行 & 15日反弹≥25% & (J≥90 或 RSI6≥65) & bias20≤3% & 非ST & <100亿。
    返回 [{symbol, score, reasons, trend}]。
    """
    latest = df["trade_date"].max()
    out = []
    for code, g in df.groupby("ts_code"):
        g = g.sort_values("trade_date")
        n = len(g)
        if n < 30:
            continue
        if str(g["trade_date"].iloc[-1])[:10] != str(latest)[:10]:
            continue
        close = g["close"].values
        low = g["low"].values
        high = g["high"].values
        vol = g["vol"].values if "vol" in g.columns else None
        t = n - 1
        if t < 59:
            continue
        ma20 = close[t - 19:t + 1].mean()
        ma20_prev5 = close[t - 24:t - 4].mean()
        ma60 = close[t - 59:t + 1].mean()
        md = ma20 < ma20_prev5
        lo15 = low[t - 14:t + 1].min()
        rb = (close[t] / lo15 - 1) if lo15 > 0 else 0.0
        K = 50.0
        D = 50.0
        for i in range(max(0, t - 25), t + 1):
            lo9 = low[max(0, i - 8):i + 1].min()
            hi9 = high[max(0, i - 8):i + 1].max()
            rsv = 0.0 if hi9 == lo9 else (close[i] - lo9) / (hi9 - lo9) * 100
            K = 2 / 3 * K + 1 / 3 * rsv
            D = 2 / 3 * D + 1 / 3 * K
        J = 3 * K - 2 * D
        ups = dns = 0.0
        for i in range(t - 5, t + 1):
            dl = close[i] - close[i - 1]
            if dl > 0:
                ups += dl
            else:
                dns -= dl
        r6 = 100 * ups / (ups + dns) if ups + dns > 0 else 50.0
        ov = (J >= J_OVER) or (r6 >= RSI_OVER)
        b20 = (close[t] / ma20 - 1) if ma20 > 0 else 99.0
        b60 = (close[t] / ma60 - 1) if ma60 > 0 else 99.0
        volr = 99.0
        if vol is not None and t >= 21:
            vma = vol[t - 21:t - 1].mean()
            if vma > 0:
                volr = vol[t] / vma
        if not (md and rb >= REB_MIN and ov and b20 <= BIAS20_MAX and b60 <= B60_MAX and volr <= VOLR_MAX):
            continue
        if bool(g["is_st"].iloc[t]):
            continue
        mv = float(g["total_mv"].iloc[t] or 0)
        if mv > 0 and mv / 10000.0 >= MCAP_MAX_YI:
            continue
        score = 0.5 + (0.2 if ov else 0) + (0.15 if rb >= REB_MIN + 0.05 else 0) + \
                (0.15 if md else 0) + (0.1 if b20 <= BIAS20_MAX * 0.5 else 0)
        out.append({
            "symbol": _normalize(code.split(".")[0]),
            "score": round(score, 3),
            "reasons": [],
            "trend": "vrebounce MA20下行+15日反弹≥%.0f%%+超买" % (REB_MIN * 100),
        })
    out.sort(key=lambda x: -x["score"])
    return out


# ────────────────────────────────────────────────────────────────
# 日频选股入池（V反 状态）
# ────────────────────────────────────────────────────────────────
def day_filter(code: str) -> Tuple[bool, List[str], float]:
    """日频 V反 条件（账户无关纯函数，便于测试）：
    mcap<100亿 & MA20 仍下行 & 15日低点反弹≥25% & (J≥90 或 RSI6≥65)。
    """
    reasons: List[str] = []
    ts = _ts_code(code)
    bars = fetch_daily_bars(ts)
    if len(bars) < 60:
        reasons.append("日线数据不足")
        return False, reasons, 0.0
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b["vol"] for b in bars]
    n = len(closes)
    ma20 = sum(closes[-20:]) / 20.0
    ma20_prev5 = sum(closes[-25:-5]) / 20.0
    ma60 = sum(closes[-60:]) / 60.0
    lo15 = min(lows[-REB_DAYS:])
    rb = (closes[-1] / lo15 - 1) if lo15 > 0 else 0.0
    bias20 = (closes[-1] / ma20 - 1) if ma20 > 0 else 0.0
    b60 = (closes[-1] / ma60 - 1) if ma60 > 0 else 0.0
    vol_ma20 = sum(vols[-21:-1]) / 20.0 if len(vols) >= 21 else (sum(vols[:-1]) / max(len(vols) - 1, 1))
    volr = (vols[-1] / vol_ma20) if vol_ma20 > 0 else 99.0
    J, r6 = _kdj_rsi6(bars)
    ov = (J[-1] >= J_OVER) or (r6[-1] is not None and r6[-1] >= RSI_OVER)
    ok = True
    if bias20 > BIAS20_MAX:
        ok = False
        reasons.append("反弹过猛偏离MA20 %.1f%%（上限%.0f%%，防追高）" % (bias20 * 100, BIAS20_MAX * 100))
    if b60 > B60_MAX:
        ok = False
        reasons.append("收盘高于MA60 %.1f%%（上限%.0f%%）" % (b60 * 100, B60_MAX * 100))
    if volr > VOLR_MAX:
        ok = False
        reasons.append("量比 %.2f 超上限（缩量企稳，上限%.1f）" % (volr, VOLR_MAX))
    if ma20 >= ma20_prev5:
        ok = False
        reasons.append("MA20 未仍下行")
    if rb < REB_MIN:
        ok = False
        reasons.append("15日低点反弹<%.0f%%（实际%.1f%%）" % (REB_MIN * 100, rb * 100))
    if not ov:
        ok = False
        reasons.append("末端未超买（J=%.0f, RSI6=%s）" % (J[-1], "NA" if r6[-1] is None else "%.0f" % r6[-1]))
    mcap = fetch_mcap_yi(ts)
    if mcap is not None and mcap >= MCAP_MAX_YI:
        ok = False
        reasons.append("市值>=%.0f亿" % MCAP_MAX_YI)
    elif mcap is None:
        reasons.append("市值数据缺失(放行)")
    score = 0.5 + (0.2 if ov else 0) + (0.15 if rb >= REB_MIN + 0.05 else 0) + \
            (0.15 if ma20 < ma20_prev5 else 0) + (0.1 if bias20 <= BIAS20_MAX * 0.5 else 0)
    return ok, reasons, round(score, 3)


def _insert_scan_result(symbol: str, name: str, score: float, reasons: List[str], trend: str):
    """写入 t 建仓扫描结果（source='vrebounce'，仅 t 账户候选）。

    幂等：先清空当日同 source 的旧候选再插入（扫描结果是当日全量快照），
    避免同日重复扫描产生重复行（t_build_scan_results 无 (trade_date,symbol,source) 唯一约束）。
    """
    from sqlalchemy import text
    from app.database import SessionLocal
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "DELETE FROM t_build_scan_results WHERE trade_date = :d AND source = 'vrebounce'"
            ), {"d": today})
            db.execute(text(
                "INSERT INTO t_build_scan_results (trade_date, symbol, score, reasons, trend, status, source, created_at) "
                "VALUES (:d, :sym, :sc, :rs, :tr, 'pending', 'vrebounce', now())"
            ), {"d": today, "sym": symbol, "sc": score, "rs": json.dumps(reasons, ensure_ascii=False), "tr": trend})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-vrebounce] 扫描结果写入失败 %s: %s", symbol, str(e)[:100])


def scan_once() -> List[str]:
    """盘后全市场扫描：基础日线增量落库 -> 向量化 V反 筛选 -> 写 t 候选池。

    不依赖涨幅榜（涨幅榜 TOP 是已暴涨妖股，与 V反 形态错配且漏票）；
    全市场扫描与回测口径一致，tushare 调用每日仅 1~3 次（增量）。
    """
    try:
        ensure_market_data()
    except Exception as e:
        logger.warning("[t-vrebounce] 基础数据更新失败: %s", str(e)[:150])
        return []
    # 过期候选归档：早于今天的 pending 永远不会被盘中建仓（只处理当日），标为 expired
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_build_scan_results SET status = 'expired' "
                "WHERE source = 'vrebounce' AND status = 'pending' AND trade_date < :d"
            ), {"d": datetime.now().strftime("%Y-%m-%d")})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-vrebounce] 过期候选归档失败: %s", str(e)[:100])
    df = _load_market_frame()
    if df is None or df.empty:
        logger.warning("[t-vrebounce] 基础数据为空，跳过扫描")
        return []
    cands = _vreb_candidates_vectorized(df)
    hits = []
    for c in cands:
        sym = c["symbol"]
        _insert_scan_result(sym, sym, c["score"], c["reasons"], c["trend"])
        hits.append(sym)
        logger.info("[t-vrebounce] ✅ %s 入 t 候选池（vrebounce, score=%.2f）", sym, c["score"])
    logger.info("[t-vrebounce] 全市场扫描 %d 只，入池 %d 只", int(df["ts_code"].nunique()), len(hits))
    return hits


# ────────────────────────────────────────────────────────────────
# 建仓（vrebounce 模式，只动 t 账户）
# ────────────────────────────────────────────────────────────────
def _pending_candidates() -> List[Dict[str, Any]]:
    from sqlalchemy import text
    from app.database import SessionLocal
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT id, symbol, score FROM t_build_scan_results "
                "WHERE trade_date = :d AND source = 'vrebounce' AND status = 'pending' "
                "ORDER BY score DESC LIMIT 10"
            ), {"d": today}).mappings().all()
            return [dict(r) for r in rows]
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-vrebounce] 候选读取失败: %s", str(e)[:100])
        return []


def _mark_candidate(symbol: str, status: str, note: str = ""):
    from sqlalchemy import text
    from app.database import SessionLocal
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_build_scan_results SET status = :st, built_at = now(), trend = :note "
                "WHERE trade_date = :d AND symbol = :sym AND source = 'vrebounce'"
            ), {"st": status, "note": (note or "")[:240], "d": today, "sym": symbol})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-vrebounce] 候选状态更新失败 %s: %s", symbol, str(e)[:80])


def _auto_build_window() -> bool:
    """自动建仓窗口：9:45（冷静期结束）至 13:00（午后禁自动建仓）。"""
    now = datetime.now()
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 45) <= hm <= (13 * 60)


def try_build_candidates() -> List[Dict[str, Any]]:
    """盘中实时复核候选并建仓（只经 t 建仓网关，account_id='t'）。

    仅在自动建仓窗口（9:45-13:00）内尝试；窗口外直接跳过（不误改候选状态）。
    瞬时性拒绝（非交易时段/封板/熔断等）保持 pending 下轮重试，只有硬失败才落 blocked。
    """
    from app.services.t_build import build_t_position
    if not _auto_build_window():
        logger.info("[t-vrebounce] 非自动建仓窗口（需 9:45-13:00），跳过建仓尝试")
        return []
    results = []
    for cand in _pending_candidates():
        sym = cand["symbol"]
        rt = fetch_realtime(sym)
        if REALTIME_CONFIRM:
            if rt is None or rt.get("price", 0) <= 0:
                _mark_candidate(sym, "pending", note="实时复核未通过/数据不可用，等待降级")
                results.append({"symbol": sym, "status": "wait_realtime"})
                continue
        price = (rt or {}).get("price") or 0
        if price <= 0:
            results.append({"symbol": sym, "status": "no_price"})
            continue
        try:
            out = build_t_position(sym, price, reason="V反短线建仓（vrebounce）",
                                   decision_source="ai_led", build_mode="vrebounce")
            status = out.get("status")
            if status == "success":
                _mark_candidate(sym, "executed", note="V反建仓成交")
            else:
                reason = str(out.get("reason") or "")
                transient = any(k in reason for k in
                                ("非交易时段", "冷静期", "封板", "熔断", "时段", "时机未确认",
                                 "人工确认", "human_confirm"))
                _mark_candidate(sym, "pending" if transient else "blocked", note=reason[:200])
            results.append({"symbol": sym, "status": status, "reason": out.get("reason")})
        except Exception as e:
            logger.warning("[t-vrebounce] 建仓异常 %s: %s", sym, str(e)[:120])
            results.append({"symbol": sym, "status": "error", "reason": str(e)[:120]})
        time.sleep(SCAN_INTERVAL_S)
    return results


# ────────────────────────────────────────────────────────────────
# 短线出场（+8% 清 / -5% 硬止损 / 12交易日超时；无 +5% 减半）
# ────────────────────────────────────────────────────────────────
def _vrebounce_positions() -> List[Dict[str, Any]]:
    """t 账户中由 vrebounce 建仓的持仓（含成本与建仓日期）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    try:
        db = SessionLocal()
        try:
            events = db.execute(text(
                "SELECT symbol, executed_price, created_at FROM t_build_events "
                "WHERE account_id = 't' AND event_type = 'build_position' AND status = 'executed' "
                "AND reason LIKE '%vrebounce%' "
                "ORDER BY created_at DESC"
            )).mappings().all()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-vrebounce] 建仓事件读取失败: %s", str(e)[:100])
        return []
    from app.services.t_gateway import get_sellable_ledger
    ledger = get_sellable_ledger()
    out = []
    for ev in events:
        sym = ev["symbol"]
        item = ledger.get(sym)
        if not item or item.get("sellable", 0) <= 0:
            continue
        out.append({
            "symbol": sym,
            "volume": int(item["sellable"]),
            "avg_price": float(ev["executed_price"] or 0),
            "built_at": str(ev["created_at"])[:10],
        })
    return out


def _trading_days_since(built_date: str) -> int:
    """粗略交易日数（周末跳过）。"""
    try:
        d0 = date.fromisoformat(built_date)
    except Exception:
        return 0
    cnt = 0
    cur = d0
    while cur < date.today():
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            cnt += 1
    return cnt


def check_exits() -> List[Dict[str, Any]]:
    """短线出场检查（只卖 t 账户，account_id='t'）。"""
    from app.services.t_gateway import gateway_execute
    results = []
    for pos in _vrebounce_positions():
        sym = pos["symbol"]
        avg = pos["avg_price"]
        if avg <= 0:
            continue
        rt = fetch_realtime(sym)
        cur = (rt or {}).get("price") or avg
        pnl = cur / avg - 1
        vol = pos["volume"]
        reason = None
        is_stop = False
        if pnl <= -SL5:
            reason, is_stop = f"vrebounce 止损 -{SL5*100:.0f}%（pnl {pnl*100:.1f}%）", True
        elif pnl >= TP8:
            reason = f"vrebounce 止盈 +{TP8*100:.0f}% 清仓"
        elif _trading_days_since(pos["built_at"]) >= HOLD_DAYS:
            reason = f"vrebounce 持有{HOLD_DAYS}交易日超时平仓"
        if not reason or vol < 100:
            continue
        try:
            gw = gateway_execute(sym, "sell", cur, vol,
                                 reason=reason, decision_source="ai_led",
                                 is_stop_loss=is_stop)
            results.append({"symbol": sym, "volume": vol, "pnl_pct": round(pnl * 100, 2),
                            "reason": reason, "gateway": gw.get("status")})
        except Exception as e:
            logger.warning("[t-vrebounce] 平仓异常 %s: %s", sym, str(e)[:120])
            results.append({"symbol": sym, "reason": reason, "error": str(e)[:120]})
        time.sleep(SCAN_INTERVAL_S)
    return results


# ────────────────────────────────────────────────────────────────
# 监控线程（默认关闭灰度）
# ────────────────────────────────────────────────────────────────
class VRebounceMonitor:
    def __init__(self, interval: float = None):
        self.interval = interval or MONITOR_INTERVAL_S
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_scan = None
        self._scanned_today = ""
        self._last_results: Dict[str, Any] = {}

    def start(self) -> bool:
        if not ENABLED:
            logger.info("[t-vrebounce] 未启用（T_VREB_ENABLED=0），仅登记不运行")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="t-vrebounce")
        self._thread.start()
        logger.info("[t-vrebounce] 监控已启动（interval=%ss, account=%s）", self.interval, _account_id())
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        return {"enabled": ENABLED, "running": self.is_running(), "account": _account_id(),
                "last_scan": self._last_scan, "last_results": self._last_results}

    def _is_trading_time(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        hm = now.hour * 60 + now.minute
        return (9 * 60 + 25) <= hm <= (15 * 60)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                today = date.today().isoformat()
                if self._scanned_today != today:
                    self._last_scan = datetime.now().isoformat()
                    self._last_results["scan"] = scan_once()
                    self._scanned_today = today
                if self._is_trading_time():
                    self._last_results["build"] = try_build_candidates()
                    self._last_results["exit"] = check_exits()
            except Exception as e:
                logger.warning("[t-vrebounce] 主循环异常: %s", str(e)[:150])
            self._stop.wait(self.interval)


def get_monitor(interval: float = None) -> VRebounceMonitor:
    global _instance
    if _instance is None:
        _instance = VRebounceMonitor(interval=interval)
    return _instance


def start_vrebounce_monitor(interval: float = None) -> bool:
    return get_monitor(interval=interval).start()


def stop_vrebounce_monitor() -> None:
    if _instance:
        _instance.stop()


def get_status() -> dict:
    if _instance:
        return _instance.status()
    return {"enabled": ENABLED, "running": False, "account": _account_id()}
