# -*- coding: utf-8 -*-
"""做T回测 · 历史数据预取与缓存（t-backtest data layer）。

数据源（见 design.md D3）：
- 标的/指数分钟线：brze tushare 代理 `stk_mins` / `index_min`，按 trade_date 逐日拉取
  （单次调用行数有上限，逐日天然规避；卖家要求单线程串行 + 间隔≥1s，见 t_data_sources._brze_rate_limit）
- 指数日线：tushare 直连 `index_daily` 主源（项目统一入口 get_tushare_pro，.env TUSHARE_TOKEN/TUSHARE_API_URL=gyzcloud 代理），
  降级东财（免费 klt=101）→ brze（regime L1 近似 / 昨收基准）
- 交易日历：gyzcloud 代理 `trade_cal`（.env 配置，主源）→ 降级 brze → 工作日近似

缓存布局（data/t_backtest/{task_id}/）：
- m5/{symbol}.json          → [{time, open, close, high, low, vol, amount}, ...]（全部交易日合并，按时间升序）
- index_m5/{ts_code}.json   → 同上（指数分钟，regime L2/L3 盘中涨跌幅用）
- index_daily/{ts_code}.json→ [{trade_date, open, close, high, low, vol}, ...]（按日期升序）
- gaps.json                 → 预取缺口（{type, key, trade_date, reason}）

防前视：缓存按 (symbol, bar_time) 索引；回放评估点只读 bar_time <= tick 的数据；
日线基准只用 trade_date < T 的数据（由回放引擎保证，本模块只保证落盘与读取接口）。
"""
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.t_data_sources import UA, _brze_rate_limit, _get_brze_pro, _to_ts_code, brze_call

# 指数代码（与 t_regime.INDEX_SYMBOLS 对齐，tushare 格式）
INDEX_TS_CODES = {
    "hs300": "000300.SH",
    "sh": "000001.SH",
    "sz": "399001.SZ",
}

M5_FREQ = "5min"
_RETRIES = 3
_SLEEP_S = 3.0
# brze index_min 已知无权限（探针验证）——跳过指数 m5 预取，regime 走日线收盘口径（见 t_backtest.caliber_notes）
SKIP_INDEX_M5 = True


# ────────────────────────────────────────────────────────────────
# 交易日历
# ────────────────────────────────────────────────────────────────

def _fetch_trade_cal_gyzcloud(start_date: str, end_date: str) -> Optional[List[str]]:
    """gyzcloud 代理 trade_cal（.env TUSHARE_TOKEN/TUSHARE_API_URL，主源）。

    brze trade_cal 曾返回 "tenant key expired"，故交易日历改走 gyzcloud（150次/分钟限频，串行+间隔 0.4s）。
    """
    try:
        from app.core.trading._api_config import get_tushare_pro
        _brze_rate_limit()  # 复用全局串行/冷却（与 brze 共用，避免并发调用）
        pro = get_tushare_pro()
        df = pro.trade_cal(
            exchange="SSE",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
        )
        if df is not None and len(df) > 0:
            days = [str(r["cal_date"]) for _, r in df.iterrows() if int(r.get("is_open", 0)) == 1]
            return sorted(days)
    except Exception as e:
        print(f"[t-backtest-data] gyzcloud trade_cal 失败: {e}")
    return None


def _fetch_trade_cal_brze(start_date: str, end_date: str) -> Optional[List[str]]:
    """brze 代理 trade_cal（降级通道）。"""
    try:
        _brze_rate_limit()
        pro = _get_brze_pro()
        df = brze_call(lambda: pro.trade_cal(
            exchange="SSE",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
        ), "trade_cal")
        if df is not None and len(df) > 0:
            days = [str(r["cal_date"]) for _, r in df.iterrows() if int(r.get("is_open", 0)) == 1]
            return sorted(days)
    except Exception as e:
        print(f"[t-backtest-data] brze trade_cal 失败: {e}")
    return None


def resolve_trade_days(start_date: str, end_date: str) -> List[str]:
    """返回 [start, end] 内的 A 股交易日（YYYYMMDD，升序）。

    主源 gyzcloud 代理 trade_cal（.env 配置）→ 降级 brze → 最后工作日近似。
    """
    for fn in (_fetch_trade_cal_gyzcloud, _fetch_trade_cal_brze):
        days = fn(start_date, end_date)
        if days:
            return days
    # 降级：周一至周五近似（不含节假日）
    out = []
    d = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    while d <= end:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


# ────────────────────────────────────────────────────────────────
# 分钟线预取（brze stk_mins / index_min，逐日）
# ────────────────────────────────────────────────────────────────

def _fetch_mins_one_day(ts_code: str, trade_date: str, is_index: bool,
                        freq: str = M5_FREQ) -> Optional[List[dict]]:
    """拉取单日分钟线（股票 stk_mins / 指数 index_min）。返回 [{time, open, close, high, low, vol, amount}]。"""
    try:
        _brze_rate_limit()
        pro = _get_brze_pro()
        fn = (lambda: pro.index_min(ts_code=ts_code, freq=freq, trade_date=trade_date)) if is_index \
            else (lambda: pro.stk_mins(ts_code=ts_code, freq=freq, trade_date=trade_date))
        df = brze_call(fn, "index_min" if is_index else "stk_mins")
        if df is None or len(df) == 0:
            return None
        bars = []
        for _, r in df.iterrows():
            bars.append({
                "time": str(r["trade_time"]),
                "open": float(r["open"]),
                "close": float(r["close"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "vol": float(r.get("vol", 0) or 0),
                "amount": float(r["amount"]) if "amount" in df.columns else 0.0,
            })
        bars.sort(key=lambda x: x["time"])
        return bars
    except Exception as e:
        print(f"[t-backtest-data] 分钟拉取失败 {ts_code} {trade_date}: {str(e)[:120]}")
        return None


def prefetch_m5(symbol: str, trade_days: List[str], cache_dir: Path,
                is_index: bool = False, ts_code: Optional[str] = None) -> Dict[str, Any]:
    """预取标的/指数 m5（逐日），合并落盘为单 JSON。返回缺口清单。

    Args:
        symbol: 业务符号（如 SH600519 或 hs300）；ts_code 为 tushare 代码（如 600519.SH）。
        trade_days: YYYYMMDD 交易日列表（升序）。
        cache_dir: 任务缓存目录。
        is_index: 指数走 index_min。
    """
    ts = ts_code or _to_ts_code(symbol)
    sub = "index_m5" if is_index else "m5"
    target = cache_dir / sub / f"{symbol}.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: Dict[str, List[dict]] = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            existing = {}

    gaps: List[Dict[str, Any]] = []
    for td in trade_days:
        # 幂等续拉：当日已有完整 48 根（或 45+ 视为完整）则跳过
        day_bars = existing.get(td)
        if day_bars and len(day_bars) >= 40:
            continue
        bars = None
        for _attempt in range(_RETRIES):
            bars = _fetch_mins_one_day(ts, td, is_index)
            if bars:
                break
            time.sleep(_SLEEP_S)
        if bars:
            existing[td] = bars
            target.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            print(f"[t-backtest-data] {symbol} {td}: {len(bars)} 根 m5")
        else:
            gaps.append({"type": "index_m5" if is_index else "m5", "key": symbol, "trade_date": td, "reason": "拉取失败/空数据"})
    return {"fetched": len(trade_days) - len(gaps), "gaps": gaps}


def prefetch_m1(symbol: str, trade_days: List[str], cache_dir: Path,
                ts_code: Optional[str] = None) -> Dict[str, Any]:
    """预取标的 1 分钟线（brze stk_mins freq=1min，逐日），落盘 m1/{symbol}.json。

    供假跌破守卫的分钟级企稳确认使用；仅对进入做T阶段的标的预取以控制体积。
    """
    ts = ts_code or _to_ts_code(symbol)
    target = cache_dir / "m1" / f"{symbol}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, List[dict]] = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            existing = {}
    gaps: List[Dict[str, Any]] = []
    for td in trade_days:
        day_bars = existing.get(td)
        if day_bars and len(day_bars) >= 200:  # 全天上限 240，≥200 视为完整
            continue
        bars = None
        for _attempt in range(_RETRIES):
            bars = _fetch_mins_one_day(ts, td, False, freq="1min")
            if bars:
                break
            time.sleep(_SLEEP_S)
        if bars:
            existing[td] = bars
            target.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            print(f"[t-backtest-data] {symbol} {td}: {len(bars)} 根 1min")
        else:
            gaps.append({"type": "m1", "key": symbol, "trade_date": td, "reason": "拉取失败/空数据"})
    return {"fetched": len(trade_days) - len(gaps), "gaps": gaps}


# ────────────────────────────────────────────────────────────────
# 指数日线（regime L1 近似 + 昨收基准）
# ────────────────────────────────────────────────────────────────

_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def _eastmoney_secid(symbol: str) -> str:
    """业务指数代码 → 东财 secid（sh000300 → 1.000300；sz399001 → 0.399001）。"""
    s = symbol.strip().lower()
    if s.startswith("sh"):
        return "1." + s[2:]
    if s.startswith("sz"):
        return "0." + s[2:]
    return ("1." if s.startswith(("5", "6", "9")) else "0.") + s


def fetch_eastmoney_daily(symbol: str, start_date: str, end_date: str,
                          timeout: int = 10) -> Optional[List[dict]]:
    """东财日线（免费，klt=101 fqt=1）——brze index_daily 权限受限时的指数日线降级源。

    symbol: 'sh000300' / 'sz399001' / 'sh000001'。
    返回 [{trade_date(YYYYMMDD), open, close, high, low, vol}]。
    """
    import urllib.parse
    params = urllib.parse.urlencode({
        "secid": _eastmoney_secid(symbol),
        "klt": "101", "fqt": "1",
        "beg": start_date.replace("-", ""), "end": end_date.replace("-", ""),
        "lmt": "1000000", "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
    })
    url = f"{_EM_KLINE_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        klines = (data.get("data") or {}).get("klines") or []
        bars = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                bars.append({
                    "trade_date": str(parts[0]).replace("-", ""),
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "vol": float(parts[5]),
                })
            except (ValueError, IndexError):
                continue
        bars.sort(key=lambda b: b["trade_date"])
        return bars if bars else None
    except Exception as e:
        print(f"[t-backtest-data] 东财日线失败 {symbol}: {str(e)[:120]}")
        return None


def _fetch_tushare_index_daily(ts_code: str, start_date: str, end_date: str) -> Optional[List[dict]]:
    """tushare 直连指数日线（项目统一入口 get_tushare_pro，.env TUSHARE_TOKEN）。

    与另一会话的决策对齐：指数日线主源走 tushare（index_daily 跨日单次调用）。
    """
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        df = pro.index_daily(
            ts_code=ts_code,
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
        )
        if df is None or len(df) == 0:
            return None
        bars = [{
            "trade_date": str(r["trade_date"]),
            "open": float(r["open"]),
            "close": float(r["close"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "vol": float(r.get("vol", 0) or 0),
        } for _, r in df.iterrows()]
        bars.sort(key=lambda b: b["trade_date"])
        return bars
    except Exception as e:
        print(f"[t-backtest-data] tushare index_daily 失败 {ts_code}: {str(e)[:120]}")
        return None


def prefetch_index_daily(ts_codes: List[str], start_date: str, end_date: str,
                         cache_dir: Path) -> Dict[str, Any]:
    """预取指数日线。优先 tushare 直连（主源），降级东财（免费），最后 brze。返回 {ts_code: [bars]} 落盘。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {}
    gaps: List[Dict[str, Any]] = []
    # 业务符号映射（东财用 sh/sz 前缀；ts_code 为 tushare 格式）
    ts_to_biz = {"000300.SH": "sh000300", "000001.SH": "sh000001", "399001.SZ": "sz399001"}
    for ts in ts_codes:
        bars = _fetch_tushare_index_daily(ts, start_date, end_date)
        if not bars:
            time.sleep(0.6)  # tushare 频控（AGENTS.md 约定）
            biz = ts_to_biz.get(ts)
            if biz:
                bars = fetch_eastmoney_daily(biz, start_date, end_date)
        if not bars:
            bars = _fetch_brze_index_daily(ts, start_date, end_date)
        if not bars:
            gaps.append({"type": "index_daily", "key": ts, "trade_date": "", "reason": "tushare+东财+brze 均失败"})
            continue
        result[ts] = bars
        out = cache_dir / "index_daily" / f"{ts}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(bars, ensure_ascii=False), encoding="utf-8")
        print(f"[t-backtest-data] 指数日线 {ts}: {len(bars)} 根")
    return {"fetched": len(result), "gaps": gaps}


def _fetch_brze_index_daily(ts_code: str, start_date: str, end_date: str) -> Optional[List[dict]]:
    """brze index_daily 降级通道（东财失败时）。"""
    try:
        _brze_rate_limit()
        pro = _get_brze_pro()
        df = brze_call(lambda: pro.index_daily(
            ts_code=ts_code,
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
        ), "index_daily")
        if df is None or len(df) == 0:
            return None
        bars = [{
            "trade_date": str(r["trade_date"]),
            "open": float(r["open"]),
            "close": float(r["close"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "vol": float(r.get("vol", 0) or 0),
        } for _, r in df.iterrows()]
        bars.sort(key=lambda x: x["trade_date"])
        return bars
    except Exception as e:
        print(f"[t-backtest-data] brze index_daily 失败 {ts_code}: {str(e)[:120]}")
        return None


# ────────────────────────────────────────────────────────────────
# 缓存读取（回放期零网络）
# ────────────────────────────────────────────────────────────────

def load_m5(symbol: str, cache_dir: Path) -> List[dict]:
    """读取标的/指数 m5 缓存，合并全部交易日，按 time 升序。"""
    for sub in ("m5", "index_m5"):
        p = cache_dir / sub / f"{symbol}.json"
        if p.exists():
            try:
                merged: List[dict] = []
                for day_bars in json.loads(p.read_text(encoding="utf-8")).values():
                    merged.extend(day_bars)
                merged.sort(key=lambda b: b["time"])
                return merged
            except (ValueError, OSError):
                return []
    return []


def load_m1(symbol: str, cache_dir: Path) -> List[dict]:
    """读取标的 1min 缓存，合并全部交易日，按 time 升序。"""
    p = cache_dir / "m1" / f"{symbol}.json"
    if p.exists():
        try:
            merged: List[dict] = []
            for day_bars in json.loads(p.read_text(encoding="utf-8")).values():
                merged.extend(day_bars)
            merged.sort(key=lambda b: b["time"])
            return merged
        except (ValueError, OSError):
            return []
    return []


def load_index_daily(ts_code: str, cache_dir: Path) -> List[dict]:
    """读取指数日线缓存（升序）。"""
    p = cache_dir / "index_daily" / f"{ts_code}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
    return []


# ────────────────────────────────────────────────────────────────
# 标的日线（建仓规则 as_of 用，tushare daily）
# ────────────────────────────────────────────────────────────────

def prefetch_stock_daily(symbols: List[str], start_date: str, end_date: str,
                         cache_dir: Path) -> Dict[str, Any]:
    """预取标的日线（tushare daily，跨日单次调用）。落 stock_daily/{symbol}.json。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {}
    gaps: List[Dict[str, Any]] = []
    for sym in symbols:
        try:
            from app.core.trading._api_config import get_tushare_pro
            _brze_rate_limit()
            pro = get_tushare_pro()
            df = pro.daily(
                ts_code=_to_ts_code(sym),
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
            if df is None or len(df) == 0:
                gaps.append({"type": "stock_daily", "key": sym, "trade_date": "", "reason": "无数据"})
                continue
            bars = [{
                "trade_date": str(r["trade_date"]),
                "open": float(r["open"]), "close": float(r["close"]),
                "high": float(r["high"]), "low": float(r["low"]),
                "vol": float(r.get("vol", 0) or 0), "amount": float(r.get("amount", 0) or 0),
            } for _, r in df.iterrows()]
            bars.sort(key=lambda b: b["trade_date"])
            # 日换手率（假跌破守卫的缩量/洗盘判据输入；失败不阻断）
            try:
                dfb = pro.daily_basic(
                    ts_code=_to_ts_code(sym),
                    start_date=start_date.replace("-", ""),
                    end_date=end_date.replace("-", ""),
                    fields="trade_date,turnover_rate",
                )
                if dfb is not None and len(dfb) > 0:
                    tr_map = {str(r["trade_date"]): float(r.get("turnover_rate") or 0) for _, r in dfb.iterrows()}
                    for b in bars:
                        b["turnover_rate"] = tr_map.get(b["trade_date"], 0.0)
            except Exception as e:
                print(f"[t-backtest-data] 日换手率失败 {sym}: {str(e)[:100]}（跳过，守卫生效时用 vol 代理）")
            result[sym] = bars
            out = cache_dir / "stock_daily" / f"{sym}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(bars, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[t-backtest-data] 标的日线失败 {sym}: {str(e)[:120]}")
            gaps.append({"type": "stock_daily", "key": sym, "trade_date": "", "reason": str(e)[:100]})
    return {"fetched": len(result), "gaps": gaps}


def prefetch_chips(symbols: List[str], start_date: str, end_date: str,
                   cache_dir: Path) -> Dict[str, Any]:
    """预取筹码分布（tushare cyq_perf：成本 5/15/50/85/95 分位、获利比例、加权均价）。

    落盘 chips/{symbol}.json（按 trade_date 升序）。供假跌破守卫的支撑位/成本峰感知。
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {}
    gaps: List[Dict[str, Any]] = []
    for sym in symbols:
        try:
            from app.core.trading._api_config import get_tushare_pro
            _brze_rate_limit()
            pro = get_tushare_pro()
            df = pro.cyq_perf(
                ts_code=_to_ts_code(sym),
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
            if df is None or len(df) == 0:
                gaps.append({"type": "chips", "key": sym, "trade_date": "", "reason": "无数据"})
                continue
            bars = [{
                "trade_date": str(r["trade_date"]),
                "cost_5pct": float(r.get("cost_5pct") or 0),
                "cost_15pct": float(r.get("cost_15pct") or 0),
                "cost_50pct": float(r.get("cost_50pct") or 0),
                "cost_85pct": float(r.get("cost_85pct") or 0),
                "cost_95pct": float(r.get("cost_95pct") or 0),
                "winner_rate": float(r.get("winner_rate") or 0),
                "weight_avg": float(r.get("weight_avg") or 0),
            } for _, r in df.iterrows()]
            bars.sort(key=lambda b: b["trade_date"])
            result[sym] = bars
            out = cache_dir / "chips" / f"{sym}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(bars, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[t-backtest-data] 筹码分布失败 {sym}: {str(e)[:100]}")
            gaps.append({"type": "chips", "key": sym, "trade_date": "", "reason": str(e)[:100]})
    return {"fetched": len(result), "gaps": gaps}


def load_chips(symbol: str, cache_dir: Path) -> List[dict]:
    """读取筹码分布缓存（按 trade_date 升序）。"""
    p = cache_dir / "chips" / f"{symbol}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
    return []


def prefetch_all_daily_by_trade_date(trade_days: List[str], cache_dir: Path,
                                     symbol_filter: Optional[set] = None) -> Dict[str, Any]:
    """按交易日批量预取全市场日线（pro.daily(trade_date=...) 单次返回当日全市场）。

    替代逐股拉取（5000+ 次请求 → 交易日数次请求），供 rolling_scan 全市场扫描使用。
    落盘 stock_daily/{symbol}.json（升序合并，幂等：已存在且含该日则跳过该日）。

    Args:
        trade_days: YYYYMMDD 交易日列表
        cache_dir: 回测缓存目录
        symbol_filter: 可选，只保留这些 symbol 的日线（如粗筛活跃池）；None=全市场
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    from app.core.trading._api_config import get_tushare_pro
    pro = get_tushare_pro()
    fetched = 0
    gaps: List[Dict[str, Any]] = []
    for td in trade_days:
        try:
            df = pro.daily(trade_date=td)
        except Exception as e:
            print(f"[t-backtest-data] 全市场日线失败 {td}: {str(e)[:100]}")
            gaps.append({"type": "stock_daily", "key": "*", "trade_date": td, "reason": str(e)[:80]})
            continue
        if df is None or len(df) == 0:
            continue
        # 按股票聚合到文件（每只股票 append 当日行）
        by_sym: Dict[str, List[dict]] = {}
        for _, r in df.iterrows():
            code = str(r["ts_code"] or "")
            sym = code.split(".")[0] if "." in code else code
            if symbol_filter is not None and sym not in symbol_filter:
                continue
            by_sym.setdefault(sym, []).append({
                "trade_date": str(r["trade_date"]),
                "open": float(r["open"]), "close": float(r["close"]),
                "high": float(r["high"]), "low": float(r["low"]),
                "vol": float(r.get("vol", 0) or 0),
                "amount": float(r.get("amount", 0) or 0),
                "pre_close": float(r.get("pre_close", 0) or 0),
            })
        for sym, bars in by_sym.items():
            out = cache_dir / "stock_daily" / f"{sym}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            existing: List[dict] = []
            if out.exists():
                try:
                    existing = json.loads(out.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    existing = []
            # 幂等合并（按 trade_date 去重）
            seen = {str(b["trade_date"]) for b in existing}
            new = [b for b in bars if str(b["trade_date"]) not in seen]
            if new:
                merged = sorted(existing + new, key=lambda b: str(b["trade_date"]))
                out.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
                fetched += 1
        print(f"[t-backtest-data] 全市场日线 {td}: {len(by_sym)} 只")
    return {"fetched": fetched, "gaps": gaps}


def load_stock_daily(symbol: str, cache_dir: Path, as_of: Optional[str] = None) -> List[dict]:
    """读取标的日线缓存（升序）。as_of（YYYYMMDD）时只返回 trade_date ≤ as_of 的数据（防前视）。"""
    p = cache_dir / "stock_daily" / f"{symbol}.json"
    if not p.exists():
        return []
    try:
        bars = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if as_of:
        bars = [b for b in bars if str(b["trade_date"]).replace("-", "") <= as_of.replace("-", "")]
    return bars


def write_gaps(cache_dir: Path, gaps: List[Dict[str, Any]]):
    """落盘缺口清单（追加合并）。"""
    p = cache_dir / "gaps.json"
    existing: List[Dict[str, Any]] = []
    if p.exists():
        try:
            existing = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    merged = {f"{g.get('type')}|{g.get('key')}|{g.get('trade_date')}": g for g in existing + gaps}
    p.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=1), encoding="utf-8")


# ────────────────────────────────────────────────────────────────
# 探针（数据可得性验证，tasks 1.1）
# ────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────
# 行业数据（add-sector-rotation）：申万一级 L1 sw_daily 预取/缓存/强度
# ────────────────────────────────────────────────────────────────

_SW_STRENGTH_SIGMA = 1.0   # logistic 尺度（%）：5日累计 +5% → 0.993、0 → 0.5、-5% → 0.007（避免 0.04 过早饱和）
_LIVE_INDUSTRY_CACHE: Dict[str, Any] = {}


def _fetch_sw_l1_classify() -> List[Dict[str, str]]:
    """申万一级行业清单（index_classify L1）→ [(index_code, name), ...]。"""
    from app.core.trading._api_config import get_tushare_pro
    _brze_rate_limit()
    pro = get_tushare_pro()
    df = pro.index_classify(level="L1", src="SW2021")
    out: List[Dict[str, str]] = []
    if df is not None and len(df) > 0:
        for _, r in df.iterrows():
            out.append({"index_code": str(r["index_code"]), "name": str(r["industry_name"])})
    return out


def prefetch_industry_daily(trade_days: List[str], cache_dir: Path,
                            lead_days: int = 7) -> Dict[str, Any]:
    """预取申万一级行业日线（sw_daily，31 个 L1 行业）。

    - 按 ts_code × 区间逐行业拉取，逐行业间隔 ≥0.35s 限速
    - 落盘 industry_daily/{yyyymmdd}.json = {industry_name: {index_code, pct_change, close}}
    - 幂等：已存在且含该日则跳过（防重复任务重拉）
    - lead_days: 区间向前额外覆盖的自然日（行业近 5 日涨幅需要窗口前数据）
    """
    from app.core.trading._api_config import get_tushare_pro
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not trade_days:
        return {"fetched": 0, "gaps": []}
    pro = get_tushare_pro()
    start, end = trade_days[0], trade_days[-1]
    try:
        s0 = datetime.strptime(start, "%Y%m%d") - timedelta(days=lead_days * 2)
        start = s0.strftime("%Y%m%d")
    except (ValueError, TypeError):
        pass
    industries = _fetch_sw_l1_classify()
    if not industries:
        return {"fetched": 0, "gaps": [{"type": "industry_daily", "key": "*",
                                        "trade_date": "", "reason": "行业清单不可用"}]}
    fetched, gaps = 0, []
    agg: Dict[str, Dict[str, dict]] = {}  # trade_date -> {industry_name: rec}
    for it in industries:
        code, name = it["index_code"], it["name"]
        try:
            _brze_rate_limit()
            df = pro.sw_daily(ts_code=code, start_date=start, end_date=end)
        except Exception as e:
            print(f"[t-backtest-data] 行业日线失败 {name}({code}): {str(e)[:100]}")
            gaps.append({"type": "industry_daily", "key": code,
                         "trade_date": "", "reason": str(e)[:80]})
            continue
        if df is None or len(df) == 0:
            continue
        for _, r in df.iterrows():
            td = str(r["trade_date"])
            agg.setdefault(td, {})[name] = {
                "index_code": code,
                "pct_change": float(r.get("pct_change") or 0.0),
                "close": float(r.get("close") or 0.0),
            }
        time.sleep(0.35)
    for td, recs in agg.items():
        out = cache_dir / "industry_daily" / f"{td}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        existing: Dict[str, dict] = {}
        if out.exists():
            try:
                existing = json.loads(out.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                existing = {}
        if set(existing.keys()) >= set(recs.keys()):
            continue  # 该日已完整
        merged = dict(existing)
        merged.update(recs)
        out.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        fetched += 1
    print(f"[t-backtest-data] 行业日线预取完成: {len(agg)} 个交易日, {len(industries)} 行业, gaps={len(gaps)}")
    return {"fetched": fetched, "gaps": gaps}


def load_industry_daily(trade_date: str, cache_dir: Path) -> Dict[str, dict]:
    """读取单日全行业行情 {industry_name: {index_code, pct_change, close}}（空 dict = 缺失）。"""
    p = cache_dir / "industry_daily" / (str(trade_date).replace("-", "") + ".json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def prefetch_industry_map(cache_dir: Path) -> Dict[str, Any]:
    """拉取 index_member_all 全市场成分 → ts_code → 申万 L1，落盘 industry_map.json。

    已实测（2026-08-16）：index_member_all 单次返回全市场约 5895 行，每行含
    l1_code/l1_name/ts_code——无需逐行业拉取，一次构建全量映射（design D2 主路径）。
    """
    from app.core.trading._api_config import get_tushare_pro
    _brze_rate_limit()
    pro = get_tushare_pro()
    df = pro.index_member_all()
    mapping: Dict[str, Dict[str, str]] = {}
    if df is not None and len(df) > 0:
        for _, r in df.iterrows():
            ts = str(r.get("ts_code") or "")
            l1 = str(r.get("l1_code") or "")
            if ts and l1:
                mapping[ts] = {"l1_code": l1, "l1_name": str(r.get("l1_name") or "")}
    out = cache_dir / "industry_map.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    print(f"[t-backtest-data] 行业映射落盘: {len(mapping)} 只（index_member_all 实测可用）")
    return {"fetched": len(mapping)}


def load_industry_map(cache_dir: Path) -> Dict[str, Dict[str, str]]:
    """读取行业归属映射 {ts_code: {l1_code, l1_name}}。"""
    p = cache_dir / "industry_map.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def industry_strength_from_pct(pct_5d: Optional[float]) -> Optional[float]:
    """行业强度标准化（logistic，design D3）：0 → 0.5，+5% → 0.993，-5% → 0.007。"""
    if pct_5d is None:
        return None
    try:
        x = float(pct_5d) / _SW_STRENGTH_SIGMA
        return 1.0 / (1.0 + math.exp(-x))
    except (ValueError, TypeError, OverflowError):
        return None


def industry_5d_pct(industry_name: str, as_of: str, cache_dir: Path) -> Optional[float]:
    """行业近 5 交易日累计涨幅（防前视：只用 trade_date < as_of 的最近 6 条收盘）。

    数据不足返回 None（调用方按缺失处理：不过滤、强度取中性 0.5）。
    """
    as_of8 = str(as_of).replace("-", "")
    day_dir = cache_dir / "industry_daily"
    if not day_dir.is_dir():
        return None
    closes: List[float] = []
    for f in sorted(day_dir.glob("*.json")):
        td = f.stem
        if td >= as_of8:
            continue  # 当日及以后不可用（防前视）
        rec = load_industry_daily(td, cache_dir).get(industry_name)
        if rec and float(rec.get("close") or 0) > 0:
            closes.append(float(rec["close"]))
    if len(closes) < 6:
        return None
    six = closes[-6:]
    prev5 = six[0]
    if prev5 <= 0:
        return None
    return (six[-1] / prev5 - 1.0) * 100.0

def industry_context_for(symbol: str, as_of: str, cache_dir: Path,
                         map_cache: Optional[Dict[str, Dict[str, str]]] = None) -> Optional[Dict[str, Any]]:
    """候选的行业上下文（回测缓存口径，防前视，design D3）。

    Returns {name, l1_code, pct_5d, strength} 或 None（无行业/数据不足）。
    """
    ts = _to_ts_code(symbol)
    mapping = map_cache if map_cache is not None else load_industry_map(cache_dir)
    rec = (mapping or {}).get(ts) or {}
    name = str(rec.get("l1_name") or "")
    if not name:
        return None
    pct = industry_5d_pct(name, as_of, cache_dir)
    strength = industry_strength_from_pct(pct) if pct is not None else None
    return {"name": name, "l1_code": str(rec.get("l1_code") or ""),
            "pct_5d": pct if pct is not None else 0.0,
            "strength": strength if strength is not None else 0.5}


def _refresh_live_industry_daily(end_date: Optional[str] = None) -> None:
    """生产实时：拉最近 6 个交易日全行业行情（模块级缓存）。"""
    end = (end_date or datetime.now()).strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    days = resolve_trade_days(start, end)[-6:]
    cache_key = (days[-1] if days else end)
    if _LIVE_INDUSTRY_CACHE.get("_key") == cache_key:
        return
    from app.core.trading._api_config import get_tushare_pro
    pro = get_tushare_pro()
    fresh: Dict[str, Dict[str, dict]] = {}
    for td in days:
        try:
            _brze_rate_limit()
            df = pro.sw_daily(trade_date=td)
        except Exception as e:
            print(f"[t-backtest-data] 实时行业日线失败 {td}: {str(e)[:80]}")
            continue
        if df is None or len(df) == 0:
            continue
        for _, r in df.iterrows():
            nm = str(r.get("name") or "")
            if nm:
                fresh.setdefault(td, {})[nm] = {"pct_change": float(r.get("pct_change") or 0.0),
                                               "close": float(r.get("close") or 0.0)}
        time.sleep(0.35)
    _LIVE_INDUSTRY_CACHE.clear()
    _LIVE_INDUSTRY_CACHE.update(fresh)
    _LIVE_INDUSTRY_CACHE["_key"] = cache_key


def industry_strength_live(symbol: str) -> Optional[Dict[str, Any]]:
    """生产实时行业强度（sw_daily 最近 6 交易日 + index_member_all 映射，模块级缓存）。"""
    ts = _to_ts_code(symbol)
    name = ""
    try:
        from app.core.trading._api_config import get_tushare_pro
        _brze_rate_limit()
        pro = get_tushare_pro()
        df = pro.index_member_all()
        if df is not None and len(df) > 0:
            hit = df[df["ts_code"] == ts]
            if len(hit) > 0:
                name = str(hit.iloc[0].get("l1_name") or "")
    except Exception as e:
        print(f"[t-backtest-data] 实时行业映射失败 {symbol}: {str(e)[:80]}")
        return None
    if not name:
        return None
    _refresh_live_industry_daily()
    closes: List[float] = []
    for td in sorted(_LIVE_INDUSTRY_CACHE.keys()):
        if td == "_key":
            continue
        rec = (_LIVE_INDUSTRY_CACHE.get(td) or {}).get(name)
        if rec and float(rec.get("close") or 0) > 0:
            closes.append(float(rec["close"]))
    if len(closes) < 6:
        return None
    prev5 = closes[-6]
    if prev5 <= 0:
        return None
    pct = (closes[-1] / prev5 - 1.0) * 100.0
    strength = industry_strength_from_pct(pct)
    return {"name": name, "pct_5d": round(pct, 3),
            "strength": strength if strength is not None else 0.5}

def run_probe(symbol: str = "600519.SH", trade_days: int = 30,
              end_date: Optional[str] = None) -> Dict[str, Any]:
    """实测 brze stk_mins / index_min 的可得性。

    拉取最近 trade_days 个交易日：标的 m5（stk_mins）+ hs300 m5（index_min）+ 指数日线。
    统计成功率/耗时/缺口/行数，输出报告（探针脚本用）。
    """
    end = (end_date or datetime.now().strftime("%Y-%m-%d"))
    start = (datetime.now() - timedelta(days=trade_days * 2)).strftime("%Y-%m-%d")
    days = resolve_trade_days(start, end)[-trade_days:]
    probe_dir = Path(os.environ.get("T_BACKTEST_DATA_ROOT", "data/t_backtest")) / "_probe"

    report: Dict[str, Any] = {
        "symbol": symbol,
        "trade_days": len(days),
        "window": f"{days[0]}~{days[-1]}" if days else "EMPTY",
        "stk_mins": {"ok": 0, "fail": 0, "total_bars": 0, "avg_ms": 0.0, "gaps": []},
        "index_min": {"ok": 0, "fail": 0, "total_bars": 0, "avg_ms": 0.0, "gaps": []},
        "index_daily": {"ok": False, "bars": 0, "detail": ""},
    }

    t0 = time.time()
    stk = prefetch_m5(symbol, days, probe_dir, is_index=False, ts_code=symbol)
    report["stk_mins"]["ok"] = stk.get("fetched", 0)
    report["stk_mins"]["fail"] = len(stk.get("gaps", []))
    report["stk_mins"]["gaps"] = stk.get("gaps", [])
    report["stk_mins"]["total_bars"] = len(load_m5(symbol, probe_dir))
    report["stk_mins"]["avg_ms"] = round((time.time() - t0) / max(len(days), 1) * 1000, 1)

    t0 = time.time()
    idx = prefetch_m5("hs300", days, probe_dir, is_index=True, ts_code=INDEX_TS_CODES["hs300"])
    report["index_min"]["ok"] = idx.get("fetched", 0)
    report["index_min"]["fail"] = len(idx.get("gaps", []))
    report["index_min"]["gaps"] = idx.get("gaps", [])
    report["index_min"]["total_bars"] = len(load_m5("hs300", probe_dir))
    report["index_min"]["avg_ms"] = round((time.time() - t0) / max(len(days), 1) * 1000, 1)

    daily = prefetch_index_daily(list(INDEX_TS_CODES.values()), start, end, probe_dir)
    report["index_daily"]["ok"] = len(daily.get("gaps", [])) == 0 and daily.get("fetched", 0) > 0
    report["index_daily"]["bars"] = sum(
        len(load_index_daily(ts, probe_dir)) for ts in INDEX_TS_CODES.values()
    )
    report["index_daily"]["detail"] = "; ".join(str(g["key"]) + ": " + g.get("reason", "") for g in daily.get("gaps", []))

    # 缺口落盘 + 报告写盘
    write_gaps(probe_dir, stk.get("gaps", []) + idx.get("gaps", []))
    out = probe_dir / "probe_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
