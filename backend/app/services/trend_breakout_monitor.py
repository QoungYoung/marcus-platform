# -*- coding: utf-8 -*-
"""独立于回踩池的趋势突破监控器（主股票账户）。

定位：回踩池（candidate_pool_monitor）只在 waiting + 回调到位 + 结构转好时低吸，
会错过强势 V 反/突破（如东山精密 002384 8/3 后 +34%）。
本监控器独立扫描“当日主力资金净流入 TOP-N”的个股，命中
  【放量突破近 N 日高点】∧【MA20 转上】∧【主力净流入（由 TOP-N 本身保证）】
即把该标的加入【长期候选池】（不可移除）持久跟踪；
后续由 long_term_pool_monitor 在回调到关键位时低吸建仓。

参数（环境变量，可调）：
  TREND_BREAK_ENABLED=1         总开关
  TREND_BREAK_INTERVAL=300      扫描周期(秒)
  TREND_BREAK_TOP_N=30          资金流入候选数量
  TREND_BREAK_HIGH_N=20         突破参照的 N 日高点
  TREND_BREAK_VOL_MULT=1.5      放量倍数(相对近20日均量)
"""
import os
import time
import threading
import logging
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger(__name__)

_interval = int(os.getenv("TREND_BREAK_INTERVAL", "300"))
_top_n = int(os.getenv("TREND_BREAK_TOP_N", "30"))
_high_n = int(os.getenv("TREND_BREAK_HIGH_N", "20"))
_vol_mult = float(os.getenv("TREND_BREAK_VOL_MULT", "1.5"))

_instance = None

# A 股股票篮子（沪深主板+创业板+科创+北证），东财 clist fs
_EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81"
_FIELDS = "f12,f14,f2,f3,f62,f5"


def _normalize_symbol(code: str) -> str:
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


def _fetch_top_inflow(top_n: int) -> list:
    """东财 push2 clist：按主力净流入(f62)降序取 A 股 TOP-N。"""
    from core.utils.em_sector_flow import _http_get, _parse_response, EM_PUSH2_URL
    params = {
        "fid": "f62", "po": "1", "pz": str(min(top_n, 200)), "pn": "1",
        "np": "1", "fltt": "2", "invt": "2",
        "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
        "fs": _EM_FS, "fields": _FIELDS,
    }
    url = EM_PUSH2_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    raw = _http_get(url, timeout=12, referer="https://data.eastmoney.com/zjlx/dpzjlx.html")
    if not raw:
        return []
    data = _parse_response(raw)
    dict_data = data.get("data", {}) if isinstance(data, dict) else {}
    rows = dict_data.get("diff") if isinstance(dict_data, dict) else None
    if isinstance(rows, dict):
        rows = list(rows.values())
    out = []
    for r in (rows or []):
        code = str(r.get("f12", "") or "").strip()
        if not code:
            continue
        try:
            main_net = float(r.get("f62") or 0)
        except (TypeError, ValueError):
            main_net = 0.0
        out.append({
            "code": code,
            "name": str(r.get("f14") or ""),
            "price": float(r.get("f2") or 0),
            "main_net": main_net,
        })
    return out


def _detect_breakout(ts_code: str) -> bool:
    """放量突破：今日最高突破前 high_n 日高点 + 量>vol_mult×近20日均量 + MA20 转上。"""
    from app.core.trading._api_config import get_tushare_pro
    try:
        pro = get_tushare_pro()
        df = pro.pro_bar(ts_code=ts_code, adj="qfq", limit=_high_n + 22)
    except Exception:
        return False
    if df is None or len(df) < _high_n + 12:
        return False
    df = df.sort_values("trade_date").reset_index(drop=True)
    high = df["high"].astype(float)
    vol = df["vol"].astype(float)
    close = df["close"].astype(float)
    today_high = float(high.iloc[-1])
    prior_high = float(high.iloc[-_high_n:-1].max()) if _high_n > 1 else 0.0
    today_vol = float(vol.iloc[-1])
    vol_ma = float(vol.iloc[-21:-1].mean()) if len(vol) >= 22 else float(vol.iloc[:-1].mean())
    ma20_last = float(close.iloc[-20:].mean())
    ma20_prev = float(close.iloc[-21:-1].mean())
    if prior_high <= 0 or today_high <= prior_high:
        return False
    if vol_ma <= 0 or today_vol < _vol_mult * vol_ma:
        return False
    if ma20_last <= ma20_prev:
        return False
    return True


class TrendBreakoutMonitor:
    def __init__(self, interval=None, top_n=None):
        self.interval = interval or _interval
        self.top_n = top_n or _top_n
        self._stop = threading.Event()
        self._thread = None
        self._last_scan = None
        self._last_found: List[str] = []

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="trend-breakout")
        self._thread.start()
        logger.info("[趋势突破] 监控已启动 (interval=%ss topN=%d)", self.interval, self.top_n)
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        return {"running": self.is_running(),
                "last_scan": self._last_scan,
                "last_found": list(self._last_found)}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_now()
            except Exception as e:
                logger.warning("[趋势突破] 扫描异常: %s", e)
            self._stop.wait(self.interval)

    def scan_now(self):
        """单轮扫描：TOP-N 资金流入 → 突破检测 → 命中入长期池。"""
        found = []
        try:
            universe = _fetch_top_inflow(self.top_n)
        except Exception as e:
            logger.warning("[趋势突破] 获取资金榜失败: %s", e)
            return found
        from app.services.long_term_pool import get_long_term_pool
        pool = get_long_term_pool()
        today = datetime.now().strftime("%Y-%m-%d")
        hits = 0
        for item in universe:
            code, name = item["code"], item["name"]
            sym = _normalize_symbol(code)
            try:
                if not _detect_breakout(_ts_code(code)):
                    continue
            except Exception:
                continue
            if pool.get_by_symbol(sym):
                continue
            if pool.add(sym, name=name or sym, notes=f"趋势突破 {today}"):
                hits += 1
                found.append(sym)
                logger.info("[趋势突破] ✅ %s %s 加入长期候选池（放量突破 %d 日高 + MA20 转上）",
                            sym, name, _high_n)
            time.sleep(0.4)
        self._last_scan = datetime.now().isoformat()
        self._last_found = found
        logger.info("[趋势突破] 本轮扫描 %d 只，命中 %d 只入长期池", len(universe), hits)
        return found


def get_trend_breakout_monitor(interval=None, top_n=None):
    global _instance
    if _instance is None:
        _instance = TrendBreakoutMonitor(interval=interval, top_n=top_n)
    return _instance


def start_trend_breakout_monitor(interval=None, top_n=None) -> bool:
    if os.getenv("TREND_BREAK_ENABLED", "1") == "0":
        logger.info("[趋势突破] 已禁用（TREND_BREAK_ENABLED=0）")
        return False
    return get_trend_breakout_monitor(interval=interval, top_n=top_n).start()


def stop_trend_breakout_monitor() -> None:
    if _instance:
        _instance.stop()


def get_trend_breakout_status() -> dict:
    if _instance:
        return _instance.status()
    return {"running": False}
