# -*- coding: utf-8 -*-
"""做T账户·科技ETF动量趋势（t-mom-etf）。

回测（tech7 池，2024-2026，T+1+跳空滑点+费用）：20日动量 TOP3 等权、双周(10交易日)轮动、
贪婪分位>0.9 空仓门控（arkvol tech-hardware-greed）——年化 +62%（理想口径）/2026 +44%，
换仓 48 次/年；不做独立止损（回测证明动量转负止损是负资产：年化 62%→41%）。

信号：tech7 池 20 日动量降序 TOP3 -> 贪婪 250 日分位 >0.9 剔除 -> 每 10 交易日调仓。
执行：复用 t 网关（build_t_position build_mode='mom_etf'，短线档 sizing 30%），T+1 由网关保证。
数据：复用 golden_pit_sector_service._compute_signal_momentum / _load_tech_greed_map
      + golden_pit_tech_status._percentile（TTL 缓存）。
账户隔离：只作用于 account_id='t'；扫描 source='mom_etf'；默认关闭灰度 T_MOM_ETF_ENABLED=1。
"""
import os
import time
import json
import threading
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

ENABLED = os.getenv("T_MOM_ETF_ENABLED", "0") == "1"
MOM_WINDOW = int(os.getenv("MOM_ETF_MOM_WINDOW", "20"))
TOP_N = int(os.getenv("MOM_ETF_TOP_N", "3"))
REBALANCE_EVERY = int(os.getenv("MOM_ETF_REBALANCE_EVERY", "10"))   # 交易日
GREED_CAP = float(os.getenv("MOM_ETF_GREED_CAP", "0.9"))            # 贪婪分位 > cap 剔除
GREED_FAIL_STOP = int(os.getenv("MOM_ETF_GREED_FAIL_STOP", "3"))    # 贪婪连续失败阈值
SCAN_INTERVAL_S = float(os.getenv("MOM_ETF_SCAN_INTERVAL_S", "0.5"))
MONITOR_INTERVAL_S = float(os.getenv("MOM_ETF_MONITOR_INTERVAL_S", "60"))

_instance = None
_greed_fail_count = 0
_last_rebalance_dt = None  # YYYY-MM-DD（worker 内存；跨重启由 DB 事件日期兜底）


def _account_id() -> str:
    return "t"


def _get_pool() -> Dict[str, Dict[str, Any]]:
    """tech7 池（与 arkvol tech-hardware-greed 对齐，来自黄金坑配置）。"""
    from app.services.golden_pit_config import TECH_SECTOR_POOL
    return TECH_SECTOR_POOL


# ────────────────────────────────────────────────────────────────
# 信号：20 日动量 / 贪婪门控（复用黄金坑，TTL 缓存）
# ────────────────────────────────────────────────────────────────
def _mom_signal(pool_key: str, entry: Dict[str, Any], as_of: str) -> Optional[float]:
    """20 日动量（close[d]/close[d-20]-1），复用 golden_pit_sector_service。"""
    try:
        from app.services.golden_pit_sector_service import _compute_signal_momentum
        sig = _compute_signal_momentum(pool_key, entry, as_of)
        return float(sig["momentum"]) if sig else None
    except Exception as e:
        logger.warning("[t-mom-etf] 动量计算失败 %s: %s", entry.get("etf_code"), str(e)[:80])
        return None


def _greed_pct(etf6: str, as_of: str) -> Optional[float]:
    """标的 250 日贪婪分位（截至 as_of，防前视）；数据缺失返回 None（不过热）。"""
    try:
        from app.services.golden_pit_sector_service import _load_tech_greed_map
        from app.services.golden_pit_tech_status import _percentile
        gmap = _load_tech_greed_map()
        series = gmap.get(etf6, {})
        if not series:
            return None
        hist = {d: v for d, v in series.items() if v is not None and str(d)[:10] <= as_of}
        if len(hist) < 20:
            return None
        return _percentile(hist, 250)
    except Exception as e:
        logger.warning("[t-mom-etf] 贪婪分位计算失败 %s: %s", etf6, str(e)[:80])
        return None


def _target_portfolio(as_of: str) -> Tuple[List[str], List[str], bool]:
    """返回 (目标 etf6 列表, reasons, greed_ok)。
    greed_ok=False 表示贪婪数据不可用（降级无门控）。"""
    global _greed_fail_count
    pool = _get_pool()
    scored = []
    greed_ok = True
    for pk, entry in pool.items():
        etf6 = entry["etf_code"][2:]
        m = _mom_signal(pk, entry, as_of)
        if m is None:
            continue
        gp = _greed_pct(etf6, as_of)
        if gp is None:
            _greed_fail_count += 1
            if _greed_fail_count >= GREED_FAIL_STOP:
                greed_ok = False
        else:
            _greed_fail_count = 0
        scored.append({"etf6": etf6, "mom": m, "greed": gp, "name": entry["name"]})
    scored.sort(key=lambda x: -x["mom"])
    selected = []
    for s in scored:
        if len(selected) >= TOP_N:
            break
        if s["greed"] is not None and s["greed"] > GREED_CAP:
            continue
        selected.append(s)
    reasons = []
    if selected:
        reasons.append("; ".join("%s mom=%.1f%% greed=%s" % (
            s["name"], s["mom"] * 100, "NA" if s["greed"] is None else "%.2f" % s["greed"]) for s in selected))
    else:
        reasons.append("无满足目标组合（动量不足或贪婪过热）")
    return selected, reasons, greed_ok



# ────────────────────────────────────────────────────────────────
# 扫描 / 调仓 / 出场（镜像 t_vreb_etf）
# ────────────────────────────────────────────────────────────────
def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _insert_scan_result(etf6: str, score: float, reasons: List[str], trend: str):
    from sqlalchemy import text
    from app.database import SessionLocal
    sym = _normalize(etf6)
    today = _today()
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "INSERT INTO t_build_scan_results (trade_date, symbol, score, reasons, trend, status, source, created_at) "
                "VALUES (:d, :sym, :sc, :rs, :tr, 'pending', 'mom_etf', now())"
            ), {"d": today, "sym": sym, "sc": score,
                "rs": json.dumps(reasons, ensure_ascii=False), "tr": trend})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-mom-etf] 扫描结果写入失败 %s: %s", etf6, str(e)[:100])


def _normalize(code: str) -> str:
    code = (code or "").strip().upper()
    if code.startswith(("SH", "SZ")):
        return code
    return ("SH" + code) if code.startswith("5") else ("SZ" + code)


def scan_once() -> List[str]:
    """每日收盘后：计算目标组合（动量 TOP3 + 贪婪门控）写入候选池。"""
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_build_scan_results SET status = 'expired' "
                "WHERE source = 'mom_etf' AND status = 'pending' AND trade_date < :d"
            ), {"d": _today()})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-mom-etf] 过期候选归档失败: %s", str(e)[:80])
    as_of = _today()
    try:
        from sqlalchemy import text as _text
        from app.database import SessionLocal as _SL
        _db = _SL()
        try:
            _db.execute(_text(
                "DELETE FROM t_build_scan_results WHERE trade_date = :d AND source = 'mom_etf'"
            ), {"d": _today()})
            _db.commit()
        finally:
            _db.close()
    except Exception as e:
        logger.warning("[t-mom-etf] 当日候选清理失败: %s", str(e)[:80])
    target, reasons, greed_ok = _target_portfolio(as_of)
    if not greed_ok:
        logger.warning("[t-mom-etf] 贪婪数据连续失败达阈值，停止自动调仓（需人工干预）")
    hits = []
    for s in target:
        score = round(float(s["mom"]) * 100, 2)  # 以实际 20 日动量(%)为得分
        _insert_scan_result(s["etf6"], score, reasons, "mom_etf 动量TOP%d+贪婪门控" % TOP_N)
        hits.append(_normalize(s["etf6"]))
        logger.info("[t-mom-etf] ✅ %s 入 t 候选池（mom_etf, score=%.2f）", s["etf6"], score)
    logger.info("[t-mom-etf] 扫描 %d 只，目标组合 %d 只", len(_get_pool()), len(hits))
    return hits


def _mom_positions() -> List[Dict[str, Any]]:
    """t 账户中由 mom_etf 建仓的持仓。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    try:
        db = SessionLocal()
        try:
            events = db.execute(text(
                "SELECT symbol, executed_price, created_at FROM t_build_events "
                "WHERE account_id = 't' AND event_type = 'build_position' AND status = 'executed' "
                "AND reason LIKE '%mom_etf%' ORDER BY created_at DESC"
            )).mappings().all()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-mom-etf] 建仓事件读取失败: %s", str(e)[:100])
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


def _last_rebalance_date() -> Optional[str]:
    """最近一次 mom_etf 调仓事件日期（建仓或平仓）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT max(created_at) AS d FROM t_build_events "
                "WHERE account_id = 't' AND reason LIKE '%mom_etf%' "
                "AND event_type IN ('build_position', 'sell')"
            )).mappings().first()
            return str(row["d"])[:10] if row and row["d"] else None
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-mom-etf] 上次调仓日期读取失败: %s", str(e)[:80])
        return None


def _trading_days_between(d0: str, d1: str) -> int:
    """t_vreb_daily 中 (d0, d1] 的交易日数（mom 池标的共同日历近似）。"""
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            n = db.execute(text(
                "SELECT count(DISTINCT trade_date) FROM t_vreb_daily "
                "WHERE trade_date > :d0 AND trade_date <= :d1"
            ), {"d0": d0, "d1": d1}).scalar()
            return int(n or 0)
        finally:
            db.close()
    except Exception as e:
        logger.warning("[t-mom-etf] 交易日计数失败: %s", str(e)[:80])
        return 0


def _rebalance_due() -> bool:
    """距上次调仓 >= REBALANCE_EVERY 交易日。"""
    last = _last_rebalance_date()
    if last is None:
        return True
    days = _trading_days_between(last, _today())
    return days >= REBALANCE_EVERY


def _sell_mom_position(sym: str, vol: int, cur: float, reason: str):
    from app.services.t_gateway import gateway_execute
    try:
        gw = gateway_execute(sym, "sell", cur, vol, reason=reason,
                             decision_source="ai_led", is_stop_loss=False)
        return gw.get("status")
    except Exception as e:
        logger.warning("[t-mom-etf] 平仓异常 %s: %s", sym, str(e)[:120])
        return "error"


def try_rebalance(force: bool = False) -> List[Dict[str, Any]]:
    """双周调仓：卖出不在目标组合的持仓、买入目标组合中未持有的标的。

    仅自动建仓窗口（9:45-13:00）执行；被护栏拦截的调仓次日重试。
    """
    global _last_rebalance_dt
    if not (force or _rebalance_due()):
        return []
    now = datetime.now()
    hm = now.hour * 60 + now.minute
    if not ((9 * 60 + 45) <= hm <= (13 * 60)):
        logger.info("[t-mom-etf] 非自动建仓窗口，跳过调仓")
        return []
    from app.services.t_gateway import get_sellable_ledger
    from app.services.t_build import build_t_position
    from app.services.t_data_sources import fetch_tencent_quote, _normalize_symbol
    target, reasons, _ = _target_portfolio(_today())
    target_syms = {_normalize(x["etf6"]) for x in target}
    results = []
    positions = _mom_positions()
    held_syms = {p["symbol"] for p in positions}
    # 1) 卖出不在目标组合的持仓（动量掉出 TOP3 / 贪婪过热）
    for p in positions:
        if p["symbol"] not in target_syms:
            q = fetch_tencent_quote([_normalize_symbol(p["symbol"])])
            item = (q or {}).get(_normalize_symbol(p["symbol"])) or {}
            cur = float(item.get("current") or p["avg_price"])
            st = _sell_mom_position(p["symbol"], p["volume"], cur, "mom_etf 调仓换出（掉出动量TOP%d）" % TOP_N)
            results.append({"symbol": p["symbol"], "action": "sell", "status": st})
            time.sleep(SCAN_INTERVAL_S)
    # 2) 买入目标组合中未持有的
    for sym in sorted(target_syms - held_syms):
        q = fetch_tencent_quote([_normalize_symbol(sym)])
        item = (q or {}).get(_normalize_symbol(sym)) or {}
        price = float(item.get("current") or 0)
        if price <= 0:
            results.append({"symbol": sym, "action": "buy", "status": "no_price"})
            continue
        out = build_t_position(sym, price, reason="科技ETF动量趋势调仓（mom_etf）",
                               decision_source="ai_led", build_mode="mom_etf")
        results.append({"symbol": sym, "action": "buy", "status": out.get("status"), "reason": out.get("reason")})
        time.sleep(SCAN_INTERVAL_S)
    _last_rebalance_dt = _today()
    logger.info("[t-mom-etf] 调仓完成：目标 %s，结果 %d 项", sorted(target_syms), len(results))
    return results


def check_exits() -> List[Dict[str, Any]]:
    """出场检查 = 调仓日自然换出（无独立止损）。非调仓日 no-op。"""
    return try_rebalance(force=False)



# ────────────────────────────────────────────────────────────────
# 监控线程（默认关闭灰度）
# ────────────────────────────────────────────────────────────────
class MomEtfMonitor:
    def __init__(self, interval: float = None):
        self.interval = interval or MONITOR_INTERVAL_S
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_scan = None
        self._scanned_today = ""
        self._last_results: Dict[str, Any] = {}

    def start(self) -> bool:
        if not ENABLED:
            logger.info("[t-mom-etf] 未启用（T_MOM_ETF_ENABLED=0），仅登记不运行")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="t-mom-etf")
        self._thread.start()
        logger.info("[t-mom-etf] 监控已启动（interval=%ss, account=%s）", self.interval, _account_id())
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
                    self._last_results["rebalance"] = try_rebalance()
                    self._last_results["exit"] = check_exits()
            except Exception as e:
                logger.warning("[t-mom-etf] 主循环异常: %s", str(e)[:150])
            self._stop.wait(self.interval)


def get_monitor(interval: float = None) -> MomEtfMonitor:
    global _instance
    if _instance is None:
        _instance = MomEtfMonitor(interval=interval)
    return _instance


def start_mom_etf_monitor(interval: float = None) -> bool:
    return get_monitor(interval=interval).start()


def stop_mom_etf_monitor() -> None:
    if _instance:
        _instance.stop()


def get_status() -> dict:
    if _instance:
        return _instance.status()
    return {"enabled": ENABLED, "running": False, "account": _account_id()}
