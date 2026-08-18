# -*- coding: utf-8 -*-
"""做T账户·趋势突破短线（t-trend-breakout-short-term）。

独立于主账户回踩池/长期池，全链路只作用于 account_id='t'：
  日频入池（主力净流入>0 & 5日累计>0、市值<100亿、放量突破近20日高点、MA20转上）
  -> 盘中实时复核（东财 f62 主力净流入>0 + 量比） -> build_t_position(build_mode='trend_break')
  -> 短线出场（+5% 减半 / +8% 清仓 / -5% 硬止损 / 持有5交易日超时平仓）。

账户隔离：扫描结果只写 t_build_scan_results(source='trend_break')，
建仓走 build_t_position（内部固定 account_id='t'），平仓走 gateway_execute(account_id='t')；
不触碰 stock/golden_pit 资金、持仓或候选池；t 资金不足即跳过，不跨账户划转。

默认关闭灰度：T_TREND_BREAK_ENABLED=1 才在 worker 注册运行。
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

ENABLED = os.getenv("T_TREND_BREAK_ENABLED", "0") == "1"
SCAN_MAX_DAILY = int(os.getenv("TREND_BREAK_SCAN_DAILY_MAX", "50"))
SCAN_INTERVAL_S = float(os.getenv("TREND_BREAK_SCAN_INTERVAL_S", "1"))
MCAP_MAX_YI = float(os.getenv("TREND_BREAK_MCAP_MAX_YI", "100"))
HIGH_N = int(os.getenv("TREND_BREAK_HIGH_N", "20"))
VOL_MULT = float(os.getenv("TREND_BREAK_VOL_MULT", "1.5"))
TP5 = float(os.getenv("TREND_BREAK_TP5", "0.05"))
TP8 = float(os.getenv("TREND_BREAK_TP8", "0.08"))
SL5 = float(os.getenv("TREND_BREAK_SL5", "0.05"))
HOLD_DAYS = int(os.getenv("TREND_BREAK_HOLD_DAYS", "5"))
REALTIME_CONFIRM = os.getenv("TREND_BREAK_REALTIME_CONFIRM", "1") == "1"
MONITOR_INTERVAL_S = float(os.getenv("TREND_BREAK_MONITOR_INTERVAL_S", "60"))

_instance = None

# A 股篮子（东财 clist fs）
_EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81"


# ────────────────────────────────────────────────────────────────
# 账户隔离常量
# ────────────────────────────────────────────────────────────────
def _account_id() -> str:
    """趋势突破短线固定只服务做T账户。"""
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


def fetch_top_inflow(top_n: int = SCAN_MAX_DAILY) -> list:
    """东财 push2 clist：按主力净流入(f62)降序取 A 股 TOP-N（实时资金榜）。"""
    from core.utils.em_sector_flow import _http_get, _parse_response, EM_PUSH2_URL
    params = {
        "fid": "f62", "po": "1", "pz": str(min(top_n, 200)), "pn": "1",
        "np": "1", "fltt": "2", "invt": "2",
        "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
        "fs": _EM_FS, "fields": "f12,f14,f2,f62",
    }
    url = EM_PUSH2_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    try:
        raw = _http_get(url, timeout=12, referer="https://data.eastmoney.com/zjlx/dpzjlx.html")
    except Exception as e:
        logger.warning("[t-trend-break] 资金榜获取失败: %s", e)
        return []
    if not raw:
        return []
    data = _parse_response(raw)
    dd = data.get("data", {}) if isinstance(data, dict) else {}
    rows = dd.get("diff") if isinstance(dd, dict) else None
    if isinstance(rows, dict):
        rows = list(rows.values())
    out = []
    for r in (rows or []):
        code = str(r.get("f12") or "").strip()
        if not code:
            continue
        try:
            main_net = float(r.get("f62") or 0)
            price = float(r.get("f2") or 0)
        except (TypeError, ValueError):
            main_net, price = 0.0, 0.0
        out.append({"code": code, "name": str(r.get("f14") or ""), "price": price, "main_net": main_net})
    return out


def _get_pro():
    from app.core.trading._api_config import get_tushare_pro
    return get_tushare_pro()


def fetch_daily_bars(ts_code: str, start_date: str = "20250101", end_date: str = "") -> list:
    """日线 qfq（Tushare pro_bar）。异常返回 []。"""
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")
    try:
        pro = _get_pro()
        df = pro.pro_bar(api=pro, ts_code=ts_code, adj="qfq", asset="E", freq="D",
                         start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning("[t-trend-break] 日线获取失败 %s: %s", ts_code, str(e)[:80])
        return []
    if df is None or df.empty:
        return []
    df = df.sort_values("trade_date")
    return [
        {"date": str(r["trade_date"]), "open": float(r["open"]), "high": float(r["high"]),
         "low": float(r["low"]), "close": float(r["close"]), "vol": float(r["vol"])}
        for _, r in df.iterrows()
    ]


def fetch_moneyflow_main_net(ts_code: str, days: int = 5) -> Tuple[Optional[float], float]:
    """日频主力净流入：返回 (当日净额, 近 days 日累计)，单位万元。异常 -> (None, 0)。"""
    try:
        pro = _get_pro()
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
    except Exception as e:
        logger.warning("[t-trend-break] 资金流获取失败 %s: %s", ts_code, str(e)[:80])
        return None, 0.0
    if df is None or df.empty:
        return None, 0.0
    df = df.sort_values("trade_date")
    today_net = None
    acc = 0.0
    for _, r in df.iterrows():
        try:
            main = (float(r.get("buy_lg_amount") or 0) + float(r.get("buy_elg_amount") or 0)
                    - float(r.get("sell_lg_amount") or 0) - float(r.get("sell_elg_amount") or 0))
            if main == 0:
                main = float(r.get("net_amount") or 0)
        except (TypeError, ValueError):
            main = 0.0
        acc += main
        today_net = main
    return today_net, acc


def fetch_mcap_yi(ts_code: str) -> Optional[float]:
    """最新总市值（亿元）；异常返回 None。"""
    try:
        pro = _get_pro()
        df = pro.daily_basic(ts_code=ts_code, fields="ts_code,trade_date,total_mv")
    except Exception as e:
        logger.warning("[t-trend-break] 市值获取失败 %s: %s", ts_code, str(e)[:60])
        return None
    if df is None or df.empty:
        return None
    row = df.sort_values("trade_date").iloc[-1]
    mv = float(row.get("total_mv") or 0)  # 万元
    return mv / 10000.0 if mv > 0 else None


def fetch_realtime(code: str) -> Optional[Dict[str, Any]]:
    """盘中实时复核：东财 stock/get f62(主力净额) f10(量比)。失败返回 None。"""
    try:
        from core.utils.em_sector_flow import _http_get, _parse_response
        c = code[-6:]
        secid = ("1." if c[0] == "6" else "0.") + c
        url = ("https://push2.eastmoney.com/api/qt/stock/get?secid=" + secid +
               "&fields=f2,f10,f62&ut=8dec03ba335b81bf4ebdf7b29ec27d15")
        raw = _http_get(url, timeout=8, referer="https://quote.eastmoney.com/")
        if not raw:
            return None
        data = _parse_response(raw)
        d = data.get("data", {}) if isinstance(data, dict) else {}
        if not isinstance(d, dict):
            return None
        price = float(d.get("f2") or 0)
        main_net = float(d.get("f62") or 0)
        vol_ratio = float(d.get("f10") or 0)
        return {"price": price, "main_net": main_net, "vol_ratio": vol_ratio}
    except Exception as e:
        logger.warning("[t-trend-break] 实时复核失败 %s: %s", code, str(e)[:60])
        return None


# ────────────────────────────────────────────────────────────────
# 日频选股入池
# ────────────────────────────────────────────────────────────────
def day_filter(code: str) -> Tuple[bool, List[str], float]:
    """日频突破条件（账户无关纯函数，便于测试）：
    mcap<100亿、当日主力净流入>0、5日累计>0、放量突破近20日高点、MA20 转上。
    """
    reasons: List[str] = []
    ts = _ts_code(code)
    bars = fetch_daily_bars(ts)
    if len(bars) < HIGH_N + 12:
        reasons.append("日线数据不足")
        return False, reasons, 0.0
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    vols = [b["vol"] for b in bars]
    today_high = highs[-1]
    prior_high = max(highs[-HIGH_N - 1:-1]) if HIGH_N > 1 else 0.0
    ma20 = sum(closes[-20:]) / 20.0
    ma20_prev = sum(closes[-21:-1]) / 20.0
    vol_ma = sum(vols[-21:-1]) / 20.0 if len(vols) >= 22 else (sum(vols[:-1]) / max(len(vols) - 1, 1))
    ok = True
    if prior_high <= 0 or today_high <= prior_high:
        ok = False; reasons.append("未突破近%d日高点" % HIGH_N)
    if vol_ma <= 0 or vols[-1] < VOL_MULT * vol_ma:
        ok = False; reasons.append("量能未达%.1f倍均量" % VOL_MULT)
    if ma20 < ma20_prev:
        ok = False; reasons.append("MA20 未转上")
    mcap = fetch_mcap_yi(ts)
    if mcap is not None and mcap >= MCAP_MAX_YI:
        ok = False; reasons.append("市值>=%.0f亿" % MCAP_MAX_YI)
    elif mcap is None:
        reasons.append("市值数据缺失(放行)")
    today_net, acc5 = fetch_moneyflow_main_net(ts, days=5)
    if today_net is not None and today_net <= 0:
        ok = False; reasons.append("当日主力净流入<=0")
    if acc5 <= 0:
        ok = False; reasons.append("5日主力净流入<=0")
    score = 0.5 + (0.2 if (today_net or 0) > 0 else 0) + (0.15 if acc5 > 0 else 0) + (0.15 if ma20 >= ma20_prev else 0)
    return ok, reasons, round(score, 3)


def _insert_scan_result(symbol: str, name: str, score: float, reasons: List[str], trend: str):
    """写入 t 建仓扫描结果（source='trend_break'，仅 t 账户候选）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "INSERT INTO t_build_scan_results (trade_date, symbol, score, reasons, trend, status, source, created_at) "
                "VALUES (:d, :sym, :sc, :rs, :tr, 'pending', 'trend_break', now()) "
                "ON CONFLICT DO NOTHING"
            ), {"d": today, "sym": symbol, "sc": score, "rs": json.dumps(reasons, ensure_ascii=False), "tr": trend})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-trend-break] 扫描结果写入失败 %s: %s", symbol, str(e)[:100])


def scan_once() -> List[str]:
    """单轮日频扫描：TOP-N 资金流入 -> day_filter -> 入池（只写 t 账户候选）。"""
    universe = fetch_top_inflow(SCAN_MAX_DAILY)
    if not universe:
        logger.warning("[t-trend-break] 资金榜为空，跳过本轮扫描（可能数据源异常）")
        return []
    hits = []
    for item in universe:
        code = item["code"]
        sym = _normalize(code)
        try:
            ok, reasons, score = day_filter(code)
        except Exception as e:
            logger.warning("[t-trend-break] 过滤异常 %s: %s", sym, str(e)[:80])
            continue
        if ok:
            _insert_scan_result(sym, item["name"] or sym, score, reasons,
                                "trend_break 放量突破%d日高+MA20转上" % HIGH_N)
            hits.append(sym)
            logger.info("[t-trend-break] ✅ %s %s 入 t 候选池（trend_break）", sym, item.get("name"))
        time.sleep(SCAN_INTERVAL_S)
    logger.info("[t-trend-break] 本轮扫描 %d 只，入池 %d 只", len(universe), len(hits))
    return hits


# ────────────────────────────────────────────────────────────────
# 建仓（trend_break 模式，只动 t 账户）
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
                "WHERE trade_date = :d AND source = 'trend_break' AND status = 'pending' "
                "ORDER BY score DESC LIMIT 10"
            ), {"d": today}).mappings().all()
            return [dict(r) for r in rows]
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-trend-break] 候选读取失败: %s", str(e)[:100])
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
                "WHERE trade_date = :d AND symbol = :sym AND source = 'trend_break'"
            ), {"st": status, "note": (note or "")[:240], "d": today, "sym": symbol})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-trend-break] 候选状态更新失败 %s: %s", symbol, str(e)[:80])


def try_build_candidates() -> List[Dict[str, Any]]:
    """盘中实时复核候选并建仓（只经 t 建仓网关，account_id='t'）。"""
    from app.services.t_build import build_t_position
    results = []
    for cand in _pending_candidates():
        sym = cand["symbol"]
        rt = fetch_realtime(sym)
        if REALTIME_CONFIRM:
            if rt is None or rt.get("main_net", 0) <= 0:
                _mark_candidate(sym, "pending", note="实时复核未通过/数据不可用，等待降级")
                results.append({"symbol": sym, "status": "wait_realtime"})
                continue
        price = (rt or {}).get("price") or 0
        if price <= 0:
            results.append({"symbol": sym, "status": "no_price"})
            continue
        try:
            out = build_t_position(sym, price, reason="趋势突破短线建仓（trend_break）",
                                   decision_source="ai_led", build_mode="trend_break")
            status = out.get("status")
            _mark_candidate(sym, "executed" if status == "success" else "blocked",
                            note=str(out.get("reason") or "")[:200])
            results.append({"symbol": sym, "status": status, "reason": out.get("reason")})
        except Exception as e:
            logger.warning("[t-trend-break] 建仓异常 %s: %s", sym, str(e)[:120])
            results.append({"symbol": sym, "status": "error", "reason": str(e)[:120]})
        time.sleep(SCAN_INTERVAL_S)
    return results


# ────────────────────────────────────────────────────────────────
# 短线出场（+5% 减半 / +8% 清 / -5% 硬止损 / 5交易日超时）
# ────────────────────────────────────────────────────────────────
def _trend_break_positions() -> List[Dict[str, Any]]:
    """t 账户中由 trend_break 建仓的持仓（含成本与建仓日期）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    try:
        db = SessionLocal()
        try:
            events = db.execute(text(
                "SELECT symbol, executed_price, created_at FROM t_build_events "
                "WHERE account_id = 't' AND event_type = 'build_position' AND status = 'executed' "
                "AND (decision_source IN ('ai_led','trend_break') OR reason LIKE '%trend_break%') "
                "ORDER BY created_at DESC"
            )).mappings().all()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-trend-break] 建仓事件读取失败: %s", str(e)[:100])
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
    for pos in _trend_break_positions():
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
            reason, is_stop = f"trend_break 止损 -{SL5*100:.0f}%（pnl {pnl*100:.1f}%）", True
        elif pnl >= TP8:
            reason = f"trend_break 止盈 +{TP8*100:.0f}% 清仓"
        elif pnl >= TP5:
            vol = max(int(vol * 0.5) // 100 * 100, 100 if pos["volume"] >= 200 else vol)
            reason = f"trend_break 止盈 +{TP5*100:.0f}% 减半"
        elif _trading_days_since(pos["built_at"]) >= HOLD_DAYS:
            reason = f"trend_break 持有{HOLD_DAYS}交易日超时平仓"
        if not reason or vol < 100:
            continue
        try:
            gw = gateway_execute(sym, "sell", cur, vol,
                                 reason=reason, decision_source="ai_led",
                                 is_stop_loss=is_stop)
            results.append({"symbol": sym, "volume": vol, "pnl_pct": round(pnl * 100, 2),
                            "reason": reason, "gateway": gw.get("status")})
        except Exception as e:
            logger.warning("[t-trend-break] 平仓异常 %s: %s", sym, str(e)[:120])
            results.append({"symbol": sym, "reason": reason, "error": str(e)[:120]})
        time.sleep(SCAN_INTERVAL_S)
    return results


# ────────────────────────────────────────────────────────────────
# 监控线程（默认关闭灰度）
# ────────────────────────────────────────────────────────────────
class TrendBreakMonitor:
    def __init__(self, interval: float = None):
        self.interval = interval or MONITOR_INTERVAL_S
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_scan = None
        self._scanned_today = ""
        self._last_results: Dict[str, Any] = {}

    def start(self) -> bool:
        if not ENABLED:
            logger.info("[t-trend-break] 未启用（T_TREND_BREAK_ENABLED=0），仅登记不运行")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="t-trend-break")
        self._thread.start()
        logger.info("[t-trend-break] 监控已启动（interval=%ss, account=%s）", self.interval, _account_id())
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
                logger.warning("[t-trend-break] 主循环异常: %s", str(e)[:150])
            self._stop.wait(self.interval)


def get_monitor(interval: float = None) -> TrendBreakMonitor:
    global _instance
    if _instance is None:
        _instance = TrendBreakMonitor(interval=interval)
    return _instance


def start_trend_break_monitor(interval: float = None) -> bool:
    return get_monitor(interval=interval).start()


def stop_trend_break_monitor() -> None:
    if _instance:
        _instance.stop()


def get_status() -> dict:
    if _instance:
        return _instance.status()
    return {"enabled": ENABLED, "running": False, "account": _account_id()}
