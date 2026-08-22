# -*- coding: utf-8 -*-
"""做T账户·科技ETF V反短线（t-vreb-etf）。

基于 ETF 分钟级回测（T+1 正确口径 + 跳空滑点）：
A 股科技 ETF（半导体/科创/军工/医药/新能源等）V反 状态 TP6/SL4/H8：
胜率 75%、单笔 +2.67%、PF 4.54（2025+ OOS PF 9.56），326 笔/2009-2026。

信号（ETF 版，波动率等比缩放自股票版）：
  日频入池：MA20 仍下行 & 15日低点反弹 ≥12% & (J≥85 或 RSI6≥62)
            & bias20 ≤3%（防追高）
            & 放行分支：量比≤1.0 且 收盘≤MA60，或 CCI≤-10（深度超卖）
  盘中实时复核（腾讯现价） -> build_t_position(build_mode='vreb_etf')
  出场：+6% 清仓 / -4% 硬止损 / 持有 8 交易日超时（T+1 由网关保证，D0 不可卖）

数据源：tushare fund_daily(trade_date=...) 全市场基金日线（每天 1 次调用增量落库 t_vreb_daily）；
ETF 池：fund_basic 股票型 + 科技关键词（排除跨境 QDII，跨境是 T+0 不同规则）。

账户隔离：只作用于 account_id='t'；扫描 source='vreb_etf'；
默认关闭灰度：T_VREB_ETF_ENABLED=1 才注册运行。
"""
import os
import time
import json
import threading
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

ENABLED = os.getenv("T_VREB_ETF_ENABLED", "0") == "1"
SCAN_MAX_DAILY = int(os.getenv("VREB_ETF_SCAN_DAILY_MAX", "200"))
SCAN_INTERVAL_S = float(os.getenv("VREB_ETF_SCAN_INTERVAL_S", "0.3"))
REB_MIN = float(os.getenv("VREB_ETF_REB_MIN", "0.12"))       # 15日低点反弹 ≥12%（ETF 波动缩放）
REB_DAYS = int(os.getenv("VREB_ETF_REB_DAYS", "15"))
J_OVER = float(os.getenv("VREB_ETF_J_OVER", "85"))
RSI_OVER = float(os.getenv("VREB_ETF_RSI_OVER", "62"))
BIAS20_MAX = float(os.getenv("VREB_ETF_BIAS20_MAX", "0.03"))
VOLR_MAX = float(os.getenv("VREB_ETF_VOLR_MAX", "1.0"))
B60_MAX = float(os.getenv("VREB_ETF_B60_MAX", "0.0"))
CCI_RELEASE_MAX = float(os.getenv("VREB_ETF_CCI_RELEASE_MAX", "-10.0"))
TP = float(os.getenv("VREB_ETF_TP", "0.06"))                 # +6% 清仓
SL = float(os.getenv("VREB_ETF_SL", "0.04"))                 # -4% 硬止损
HOLD_DAYS = int(os.getenv("VREB_ETF_HOLD_DAYS", "8"))        # 8 交易日超时
REALTIME_CONFIRM = os.getenv("VREB_ETF_REALTIME_CONFIRM", "0") == "1"
MONITOR_INTERVAL_S = float(os.getenv("VREB_ETF_MONITOR_INTERVAL_S", "60"))
LOOKBACK_DAYS = 70  # ≥65 才能算 MA60/b60
BULL_ADAPT = os.getenv("VREB_ETF_BULL_ADAPT", "1") == "1"
_bull_cache = {"date": "", "bull": True}


def _bull_state() -> bool:
    """上证 vs MA250：牛市要求超买确认；非牛弱反抽放行（缓存每日一次）。"""
    global _bull_cache
    today = date.today().isoformat()
    if _bull_cache["date"] == today:
        return _bull_cache["bull"]
    bull = True
    try:
        pro = _get_pro()
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
        df = pro.index_daily(ts_code="000001.SH", start_date=start, end_date=end)
        if df is not None and len(df) >= 251:
            df = df.sort_values("trade_date")
            close = [float(x) for x in df["close"].tolist()]
            bull = close[-1] > sum(close[-250:]) / 250.0
    except Exception as e:
        logger.warning("[t-vreb-etf] 牛熊状态获取失败（默认牛市）: %s", str(e)[:80])
    _bull_cache = {"date": today, "bull": bull}
    logger.info("[t-vreb-etf] 牛熊状态: %s（上证 vs MA250）", "牛" if bull else "非牛")
    return bull

_instance = None

# 科技关键词（股票型 ETF 名称）
_TECH_KEYWORDS = ["半导体", "芯片", "科创", "人工智能", "计算机", "软件", "电子", "通信",
                  "军工", "国防", "机器人", "新能源", "光伏", "储能", "电池", "汽车",
                  "智能", "大数据", "云计算", "游戏", "传媒", "医药", "医疗", "生物",
                  "创新药", "5G", "消费电子", "数字经济", "工业母机", "高端装备",
                  "科技", "TMT", "信息", "互联", "算力", "软件服务", "信创", "光模块",
                  "PCB", "消费电子", "智能制造", "卫星", "航天"]
# 跨境/非A股排除（跨境 ETF 是 T+0，不同规则）
_EXCLUDE_KEYWORDS = ["港股", "恒生", "纳指", "标普", "日经", "美股", "海外", "QDII",
                     "德国", "法国", "日本", "亚太", "东南亚", "全球", "原油", "黄金",
                     "白银", "商品", "债券", "货币", "红利低波", "国企红利"]


# ────────────────────────────────────────────────────────────────
# 账户隔离
# ────────────────────────────────────────────────────────────────
def _account_id() -> str:
    return "t"


def _normalize(code: str) -> str:
    code = (code or "").strip().upper()
    if code.startswith(("SH", "SZ")):
        return code
    return ("SH" + code) if code.startswith(("5", "6")) else ("SZ" + code)


# ────────────────────────────────────────────────────────────────
# ETF 池
# ────────────────────────────────────────────────────────────────
def _get_pro():
    from app.core.trading._api_config import get_tushare_pro
    return get_tushare_pro()


def _etf_pool() -> List[str]:
    """科技类 A 股 ETF 列表（fund_basic 全量 + 名称关键词；排除跨境 QDII）。"""
    try:
        pro = _get_pro()
        df = pro.fund_basic(market="E")
        if df is None or df.empty:
            return []
        df = df[(df["fund_type"] == "股票型") & (df["name"].astype(str).str.contains("ETF", na=False))]
        out = []
        for _, r in df.iterrows():
            name = str(r["name"] or "")
            if any(k in name for k in _EXCLUDE_KEYWORDS):
                continue
            if any(k in name for k in _TECH_KEYWORDS):
                out.append(str(r["ts_code"]))
        return sorted(set(out))
    except Exception as e:
        logger.warning("[t-vreb-etf] ETF 池获取失败: %s", str(e)[:100])
        return []


def fetch_realtime(code: str) -> Optional[Dict[str, Any]]:
    """腾讯 qt 实时价（做T主系统同源）。"""
    try:
        from app.services.t_data_sources import _normalize_symbol, fetch_tencent_quote
        sym = _normalize_symbol(code)
        q = fetch_tencent_quote([sym])
        item = q.get(sym) or {}
        price = float(item.get("current") or 0)
        if price <= 0:
            return None
        return {"price": price, "main_net": 0.0, "vol_ratio": float(item.get("amplitude") or 0)}
    except Exception as e:
        logger.warning("[t-vreb-etf] 实时复核失败 %s: %s", code, str(e)[:60])
        return None


# ────────────────────────────────────────────────────────────────
# 全市场基金日线落库（复用 t_vreb_daily 表）
# ────────────────────────────────────────────────────────────────
def _latest_fund_trade_date(pro) -> Optional[str]:
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
        days = sorted(str(x) for x in cal["cal_date"].tolist())
    except Exception:
        days = []
    for ds in reversed(days):
        try:
            df = pro.fund_daily(trade_date=ds)
            if df is not None and len(df) > 0:
                return ds
        except Exception:
            pass
    return days[-1] if days else None


def _fund_dates_between(pro, start: Optional[str], end: str) -> List[str]:
    if not start:
        return []
    s = (datetime.strptime(start, "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d")
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=s, end_date=end, is_open="1")
        days = sorted(str(x) for x in cal["cal_date"].tolist())
    except Exception:
        return []
    return [d for d in days if d > start]


def _recent_fund_dates(pro, n: int, end: str) -> List[str]:
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=int(n * 1.5) + 15)).strftime("%Y%m%d")
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
        days = sorted(str(x) for x in cal["cal_date"].tolist())
    except Exception:
        return []
    return days[-n:] if len(days) >= n else days


def _upsert_fund_daily(df) -> None:
    """批量 upsert 基金日线到 t_vreb_daily（is_st=False）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "ts": str(r["ts_code"]), "d": str(r["trade_date"])[:10],
            "o": float(r["open"] or 0), "h": float(r["high"] or 0), "l": float(r["low"] or 0),
            "c": float(r["close"] or 0), "v": float(r["vol"] or 0), "mv": 0.0, "st": False,
        })
    if not rows:
        return
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


def ensure_etf_market_data() -> bool:
    """基金日线增量落库：首次拉 LOOKBACK_DAYS 个交易日全市场，之后每天增量 1-2 次调用。"""
    pro = _get_pro()
    latest = _latest_fund_trade_date(pro)
    if latest is None:
        logger.warning("[t-vreb-etf] 无法确定最近基金交易日")
        return False
    # 注意：t_vreb_daily 同时存股票与基金数据，必须按本模块 ETF 池判断基金数据是否最新
    pool = _etf_pool()
    ph = ",".join("'" + c + "'" for c in pool) if pool else "''"
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        max_d = db.execute(text(
            "SELECT max(trade_date) FROM t_vreb_daily WHERE ts_code IN (" + ph + ")"
        )).scalar()
        max_d = str(max_d)[:10].replace("-", "") if max_d is not None else None
    finally:
        db.close()
    if max_d and max_d >= latest:
        return True
    need = _fund_dates_between(pro, max_d, latest) if max_d else _recent_fund_dates(pro, LOOKBACK_DAYS, latest)
    if not need:
        return True
    frames = []
    for ds in need:
        try:
            df = pro.fund_daily(trade_date=ds)
            if df is not None and len(df):
                frames.append(df[["ts_code", "trade_date", "open", "high", "low", "close", "vol"]])
            time.sleep(0.25)
        except Exception as e:
            logger.warning("[t-vreb-etf] 基金日线拉取失败 %s: %s", ds, str(e)[:80])
    if not frames:
        return False
    import pandas as pd
    allf = pd.concat(frames, ignore_index=True)
    _upsert_fund_daily(allf)
    logger.info("[t-vreb-etf] 基金日线落库 %d 行（%s..%s）", len(allf), need[0], need[-1])
    return True


def _load_etf_frame(pool: List[str]) -> Optional[Any]:
    from sqlalchemy import text
    from app.database import SessionLocal
    import pandas as pd
    if not pool:
        return None
    ph = ",".join("'" + c + "'" for c in pool)
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT ts_code, trade_date, open, high, low, close, vol, total_mv, is_st "
            "FROM t_vreb_daily WHERE ts_code IN (" + ph + ") "
            "AND trade_date >= (SELECT max(trade_date) - INTERVAL '90 days' FROM t_vreb_daily)"
        )).mappings().all()
    finally:
        db.close()
    if not rows:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    df["trade_date"] = df["trade_date"].astype(str).str[:10]
    return df


# ────────────────────────────────────────────────────────────────
# ETF 版向量化筛选（与回测同公式）
# ────────────────────────────────────────────────────────────────
def _etf_candidates(df) -> List[Dict[str, Any]]:
    latest = df["trade_date"].max()
    out = []
    for code, g in df.groupby("ts_code"):
        g = g.sort_values("trade_date")
        n = len(g)
        if n < 65 or str(g["trade_date"].iloc[-1])[:10] != str(latest)[:10]:
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
        lo15 = low[t - REB_DAYS + 1:t + 1].min()
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
        # 牛熊自适应：牛市要求超买确认；非牛弱反抽放行
        ov_ok = (not BULL_ADAPT) or ov or (not _bull_state())
        b20 = (close[t] / ma20 - 1) if ma20 > 0 else 99.0
        b60 = (close[t] / ma60 - 1) if ma60 > 0 else 99.0
        volr = 99.0
        if vol is not None and t >= 21:
            vma = vol[t - 21:t - 1].mean()
            if vma > 0:
                volr = vol[t] / vma
        cci = None
        if t >= 13:
            tp14 = close[t - 13:t + 1].mean()
            md14 = sum(abs(close[k] - tp14) for k in range(t - 13, t + 1)) / 14.0
            if md14 > 0:
                cci = (close[t] - tp14) / (0.015 * md14)
        if not (md and rb >= REB_MIN and ov_ok and b20 <= BIAS20_MAX):
            continue
        release_combo = (volr <= VOLR_MAX) and (b60 <= B60_MAX)
        release_cci = (cci is not None) and (cci <= CCI_RELEASE_MAX)
        if not (release_combo or release_cci):
            continue
        score = 0.5 + (0.2 if ov else 0) + (0.15 if rb >= REB_MIN + 0.04 else 0) + \
                (0.15 if md else 0) + (0.1 if b20 <= BIAS20_MAX * 0.5 else 0)
        out.append({
            "symbol": _normalize(code.split(".")[0]),
            "score": round(score, 3),
            "reasons": [],
            "trend": "vreb_etf 科技ETF MA20下行+15日反弹≥%.0f%%+超买" % (REB_MIN * 100),
        })
    out.sort(key=lambda x: -x["score"])
    return out


# ────────────────────────────────────────────────────────────────
# 扫描 / 建仓 / 出场（镜像 t_vrebounce）
# ────────────────────────────────────────────────────────────────
def _insert_scan_result(symbol: str, name: str, score: float, reasons: List[str], trend: str):
    from sqlalchemy import text
    from app.database import SessionLocal
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "DELETE FROM t_build_scan_results WHERE trade_date = :d AND source = 'vreb_etf'"
            ), {"d": today})
            db.execute(text(
                "INSERT INTO t_build_scan_results (trade_date, symbol, score, reasons, trend, status, source, created_at) "
                "VALUES (:d, :sym, :sc, :rs, :tr, 'pending', 'vreb_etf', now())"
            ), {"d": today, "sym": symbol, "sc": score, "rs": json.dumps(reasons, ensure_ascii=False), "tr": trend})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-vreb-etf] 扫描结果写入失败 %s: %s", symbol, str(e)[:100])


def scan_once() -> List[str]:
    try:
        ensure_etf_market_data()
    except Exception as e:
        logger.warning("[t-vreb-etf] 基础数据更新失败: %s", str(e)[:150])
        return []
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_build_scan_results SET status = 'expired' "
                "WHERE source = 'vreb_etf' AND status = 'pending' AND trade_date < :d"
            ), {"d": datetime.now().strftime("%Y-%m-%d")})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-vreb-etf] 过期候选归档失败: %s", str(e)[:80])
    pool = _etf_pool()
    df = _load_etf_frame(pool)
    if df is None or df.empty:
        logger.warning("[t-vreb-etf] 基础数据为空，跳过扫描")
        return []
    cands = _etf_candidates(df)
    hits = []
    for c in cands[:SCAN_MAX_DAILY]:
        _insert_scan_result(c["symbol"], c["symbol"], c["score"], c["reasons"], c["trend"])
        hits.append(c["symbol"])
        logger.info("[t-vreb-etf] ✅ %s 入 t 候选池（vreb_etf, score=%.2f）", c["symbol"], c["score"])
    logger.info("[t-vreb-etf] 科技ETF扫描 %d 只，入池 %d 只", int(df["ts_code"].nunique()), len(hits))
    return hits


def _auto_build_window() -> bool:
    now = datetime.now()
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 45) <= hm <= (13 * 60)


def _pending_candidates() -> List[Dict[str, Any]]:
    from sqlalchemy import text
    from app.database import SessionLocal
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT id, symbol, score FROM t_build_scan_results "
                "WHERE trade_date = :d AND source = 'vreb_etf' AND status = 'pending' "
                "ORDER BY score DESC LIMIT 10"
            ), {"d": today}).mappings().all()
            return [dict(r) for r in rows]
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-vreb-etf] 候选读取失败: %s", str(e)[:100])
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
                "WHERE trade_date = :d AND symbol = :sym AND source = 'vreb_etf'"
            ), {"st": status, "note": (note or "")[:240], "d": today, "sym": symbol})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-vreb-etf] 候选状态更新失败 %s: %s", symbol, str(e)[:80])


def try_build_candidates() -> List[Dict[str, Any]]:
    from app.services.t_build import build_t_position
    if not _auto_build_window():
        return []
    results = []
    for cand in _pending_candidates():
        sym = cand["symbol"]
        rt = fetch_realtime(sym)
        if REALTIME_CONFIRM:
            if rt is None or rt.get("price", 0) <= 0:
                _mark_candidate(sym, "pending", note="实时复核未通过")
                results.append({"symbol": sym, "status": "wait_realtime"})
                continue
        price = (rt or {}).get("price") or 0
        if price <= 0:
            results.append({"symbol": sym, "status": "no_price"})
            continue
        try:
            out = build_t_position(sym, price, reason="科技ETF V反建仓（vreb_etf）",
                                   decision_source="ai_led", build_mode="vreb_etf")
            status = out.get("status")
            if status == "success":
                _mark_candidate(sym, "executed", note="科技ETF V反建仓成交")
            else:
                reason = str(out.get("reason") or "")
                transient = any(k in reason for k in
                                ("非交易时段", "冷静期", "封板", "熔断", "时段", "人工确认", "human_confirm"))
                _mark_candidate(sym, "pending" if transient else "blocked", note=reason[:200])
            results.append({"symbol": sym, "status": status, "reason": out.get("reason")})
        except Exception as e:
            logger.warning("[t-vreb-etf] 建仓异常 %s: %s", sym, str(e)[:120])
            results.append({"symbol": sym, "status": "error", "reason": str(e)[:120]})
        time.sleep(SCAN_INTERVAL_S)
    return results


def _vreb_etf_positions() -> List[Dict[str, Any]]:
    from sqlalchemy import text
    from app.database import SessionLocal
    try:
        db = SessionLocal()
        try:
            events = db.execute(text(
                "SELECT symbol, executed_price, created_at FROM t_build_events "
                "WHERE account_id = 't' AND event_type = 'build_position' AND status = 'executed' "
                "AND reason LIKE '%vreb_etf%' ORDER BY created_at DESC"
            )).mappings().all()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-vreb-etf] 建仓事件读取失败: %s", str(e)[:100])
        return []
    from app.services.t_gateway import get_sellable_ledger
    ledger = get_sellable_ledger()
    out = []
    for ev in events:
        sym = ev["symbol"]
        item = ledger.get(sym)
        if not item or item.get("sellable", 0) <= 0:
            continue
        out.append({"symbol": sym, "volume": int(item["sellable"]),
                    "avg_price": float(ev["executed_price"] or 0),
                    "built_at": str(ev["created_at"])[:10]})
    return out


def _trading_days_since(built_date: str) -> int:
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
    """科技ETF出场：+6% 清 / -4% 硬止损 / 8交易日超时（T+1 由网关保证）。"""
    from app.services.t_gateway import gateway_execute
    results = []
    for pos in _vreb_etf_positions():
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
        if pnl <= -SL:
            reason, is_stop = f"vreb_etf 止损 -{SL*100:.0f}%（pnl {pnl*100:.1f}%）", True
        elif pnl >= TP:
            reason = f"vreb_etf 止盈 +{TP*100:.0f}% 清仓"
        elif _trading_days_since(pos["built_at"]) >= HOLD_DAYS:
            reason = f"vreb_etf 持有{HOLD_DAYS}交易日超时平仓"
        if not reason or vol < 100:
            continue
        try:
            gw = gateway_execute(sym, "sell", cur, vol, reason=reason,
                                 decision_source="ai_led", is_stop_loss=is_stop)
            results.append({"symbol": sym, "volume": vol, "pnl_pct": round(pnl * 100, 2),
                            "reason": reason, "gateway": gw.get("status")})
        except Exception as e:
            logger.warning("[t-vreb-etf] 平仓异常 %s: %s", sym, str(e)[:120])
        time.sleep(SCAN_INTERVAL_S)
    return results


# ────────────────────────────────────────────────────────────────
# 监控线程
# ────────────────────────────────────────────────────────────────
class VrebEtfMonitor:
    def __init__(self, interval: float = None):
        self.interval = interval or MONITOR_INTERVAL_S
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_scan = None
        self._scanned_today = ""
        self._last_results: Dict[str, Any] = {}

    def start(self) -> bool:
        if not ENABLED:
            logger.info("[t-vreb-etf] 未启用（T_VREB_ETF_ENABLED=0），仅登记不运行")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="t-vreb-etf")
        self._thread.start()
        logger.info("[t-vreb-etf] 监控已启动（interval=%ss, account=%s）", self.interval, _account_id())
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
                logger.warning("[t-vreb-etf] 主循环异常: %s", str(e)[:150])
            self._stop.wait(self.interval)


def get_monitor(interval: float = None) -> VrebEtfMonitor:
    global _instance
    if _instance is None:
        _instance = VrebEtfMonitor(interval=interval)
    return _instance


def start_vreb_etf_monitor(interval: float = None) -> bool:
    return get_monitor(interval=interval).start()


def stop_vreb_etf_monitor() -> None:
    if _instance:
        _instance.stop()


def get_status() -> dict:
    if _instance:
        return _instance.status()
    return {"enabled": ENABLED, "running": False, "account": _account_id()}
