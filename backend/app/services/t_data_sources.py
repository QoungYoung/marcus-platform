# -*- coding: utf-8 -*-
"""做T系统 · 分钟线数据源服务层（沉淀自 P0 探针，已验证）。

三源方案（P0 实测 2026-08-14）：
- 腾讯 ifzq mkline（主）：m1 500根(≈2.5日) / m5 320根(≈6日) / m15 320根 / m60 320根，免费免token ✅
- 新浪 minline（备）：1/5/15/60min 均可取含 amount，免费免token，与腾讯价差 <0.1% ✅
- brze tushare 代理（权威校验）：stk_mins 历史分钟 / rt_k 实时日线 / rt_min 实时分钟 / rt_min_daily 当日完整分钟
  - 卖家要求：单线程串行 + 间隔≥1s + 失败 sleep 1-3s 重试；实时分钟 freq 用大写 1MIN/5MIN/60MIN

用途：可T质量打分（选股）、量比归一基准、分时企稳确认、regime L2 指数分钟线。
不做实时触发源（实时触发走腾讯 qt 30s 轮询）。
"""
import json
import threading
import time
import urllib.request
from typing import Dict, List, Optional

from app.config import get_settings

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ── 全局并发/冷却控制（brze 必须单线程串行；腾讯/新浪可小并发） ──
_brze_lock = threading.Lock()
_last_brze_call_ts: Dict[str, float] = {}
_BRZE_MIN_INTERVAL = 1.0  # 卖家要求间隔≥1s


def _brze_rate_limit():
    """brze 单线程串行 + 间隔≥1s 控制。"""
    with _brze_lock:
        now = time.time()
        wait = _BRZE_MIN_INTERVAL - (now - _last_brze_call_ts.get("brze", 0))
        if wait > 0:
            time.sleep(wait)
        _last_brze_call_ts["brze"] = time.time()


def _get_brze_pro():
    """获取 brze 代理的 tushare pro 实例（惰性单例）。"""
    settings = get_settings()
    token = getattr(settings, "BRZE_TOKEN", "") or "SC9b-_EoiR-gUuR1hHMIddmTqHvF6D_DGOizKGo2KQk"
    url = getattr(settings, "BRZE_URL", "") or "https://tu.brze.top"
    import tushare as ts
    from tushare.pro import client as _ts_client
    _ts_client.DataApi._DataApi__http_url = url  # 替换代理
    return ts.pro_api(token)


def brze_call(fn, name: str = "brze", retries: int = 3, sleep_s: float = 3.0):
    """brze 调用包装：串行 + 失败重试（卖家文档要求，防限流罚时）。"""
    last_err = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            time.sleep(sleep_s)
    raise last_err


# ── brze tushare 代理 ─────────────────────────────────────────────

def fetch_brze_stk_mins(ts_code: str, freq: str = "60min",
                        start_date: str = None, end_date: str = None,
                        trade_date: str = None) -> Optional[List[dict]]:
    """brze stk_mins 历史分钟K线（权威字段：ts_code/trade_time/open/close/high/low/vol/amount）。"""
    try:
        _brze_rate_limit()
        pro = _get_brze_pro()
        params = {"ts_code": ts_code, "freq": freq}
        if trade_date:
            params["trade_date"] = trade_date
        else:
            params["start_date"] = start_date
            params["end_date"] = end_date
        df = brze_call(lambda: pro.stk_mins(**params), "stk_mins")
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
                "vol": float(r["vol"]),
                "amount": float(r["amount"]) if "amount" in df.columns else 0.0,
            })
        bars.sort(key=lambda x: x["time"])
        return bars
    except Exception as e:
        print(f"[t-data] brze stk_mins 失败 {ts_code}: {str(e)[:120]}")
        return None


def fetch_brze_rt_k(ts_code: str) -> Optional[dict]:
    """brze rt_k 实时日线（权威校验实时价）。"""
    try:
        _brze_rate_limit()
        pro = _get_brze_pro()
        df = brze_call(lambda: pro.rt_k(ts_code=ts_code), "rt_k")
        if df is None or len(df) == 0:
            return None
        r = df.iloc[0]
        return {
            "ts_code": str(r.get("ts_code", "")),
            "name": str(r.get("name", "")),
            "pre_close": float(r.get("pre_close", 0) or 0),
            "open": float(r.get("open", 0) or 0),
            "high": float(r.get("high", 0) or 0),
            "low": float(r.get("low", 0) or 0),
            "close": float(r.get("close", 0) or 0),
            "vol": float(r.get("vol", 0) or 0),
            "amount": float(r.get("amount", 0) or 0),
            "trade_time": str(r.get("trade_time", "")),
        }
    except Exception as e:
        print(f"[t-data] brze rt_k 失败 {ts_code}: {str(e)[:120]}")
        return None


def fetch_brze_rt_min(ts_code: str, freq: str = "1MIN") -> Optional[dict]:
    """brze rt_min 实时分钟（盘中最新一条；freq 大写 1MIN/5MIN/60MIN）。"""
    try:
        _brze_rate_limit()
        pro = _get_brze_pro()
        df = brze_call(lambda: pro.rt_min(ts_code=ts_code, freq=freq), "rt_min")
        if df is None or len(df) == 0:
            return None
        r = df.iloc[0]
        return {
            "time": str(r["trade_time"]),
            "open": float(r["open"]),
            "close": float(r["close"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "vol": float(r["vol"]),
            "amount": float(r["amount"]) if "amount" in df.columns else 0.0,
        }
    except Exception as e:
        print(f"[t-data] brze rt_min 失败 {ts_code}: {str(e)[:120]}")
        return None


def fetch_brze_rt_min_daily(ts_code: str, freq: str = "1min") -> Optional[List[dict]]:
    """brze rt_min_daily 当日完整分钟K线（freq 小写 1min/5min/60min）。"""
    try:
        _brze_rate_limit()
        pro = _get_brze_pro()
        df = brze_call(lambda: pro.rt_min_daily(ts_code=ts_code, freq=freq), "rt_min_daily")
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
                "vol": float(r["vol"]),
                "amount": float(r["amount"]) if "amount" in df.columns else 0.0,
            })
        bars.sort(key=lambda x: x["time"])
        return bars
    except Exception as e:
        print(f"[t-data] brze rt_min_daily 失败 {ts_code}: {str(e)[:120]}")
        return None


# ── 腾讯 ifzq 分钟K线（主源） ─────────────────────────────────────

IFZQ_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
TENCENT_FREQS = {"m1": "m1", "m5": "m5", "m15": "m15", "m30": "m30", "m60": "m60"}


def fetch_tencent_mkline(symbol: str, freq: str = "m5", count: int = 320) -> Optional[List[dict]]:
    """拉取腾讯 ifzq 分钟K线（主源）。

    Args:
        symbol: 如 'sh600519' / 'sz000001'
        freq: m1/m5/m15/m30/m60
        count: 根数（m1 上限约500，其余约320）
    """
    if freq not in TENCENT_FREQS:
        raise ValueError(f"不支持的频率: {freq}")
    url = f"{IFZQ_URL}?param={symbol},{freq},,{count}"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        node = data.get("data", {}).get(symbol, {})
        bars = node.get(freq, []) or node.get("qfq" + freq, [])
        result = []
        for b in bars:
            try:
                item = {
                    "time": str(b[0]),
                    "open": float(b[1]),
                    "close": float(b[2]),
                    "high": float(b[3]),
                    "low": float(b[4]),
                    "vol": float(b[5]),
                }
                if len(b) > 6 and isinstance(b[6], (int, float, str)):
                    try:
                        item["amount"] = float(b[6])
                    except (TypeError, ValueError):
                        pass
                result.append(item)
            except (IndexError, TypeError, ValueError):
                continue
        result.sort(key=lambda x: x["time"])
        return result if result else None
    except Exception as e:
        print(f"[t-data] 腾讯mkline 失败 {symbol} {freq}: {str(e)[:120]}")
        return None


# ── 新浪分钟K线（备源） ───────────────────────────────────────────

SINA_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x=/CN_MarketDataService.getKLineData"
SINA_SCALES = {"1": 1, "5": 5, "15": 15, "30": 30, "60": 60}


def fetch_sina_minline(symbol: str, scale: int = 5, datalen: int = 300) -> Optional[List[dict]]:
    """拉取新浪分钟K线（备源/双源交叉验证）。

    Args:
        symbol: 如 'sh600519' / 'sz000001' / 'sh000300'(指数)
        scale: 1/5/15/30/60 分钟
    """
    url = f"{SINA_URL}?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        start = raw.find("([")
        end = raw.rfind("])")
        if start < 0 or end < 0 or end <= start:
            print(f"[t-data] 新浪min 解析失败 {symbol}: {raw[:100]}")
            return None
        arr = json.loads(raw[start + 1:end + 1])
        result = []
        for r in arr:
            try:
                result.append({
                    "time": str(r["day"]),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "vol": float(r["volume"]),
                    "amount": float(r.get("amount") or 0),
                })
            except (KeyError, TypeError, ValueError):
                continue
        result.sort(key=lambda x: x["time"])
        return result if result else None
    except Exception as e:
        print(f"[t-data] 新浪min 失败 {symbol}: {str(e)[:120]}")
        return None


# ── 腾讯 qt 实时行情（触发源） ─────────────────────────────────────

QT_URL = "https://qt.gtimg.cn/q="


def fetch_tencent_quote(symbols: List[str], timeout: int = 8) -> Dict[str, Optional[dict]]:
    """批量拉取腾讯 qt 实时行情（实时触发判断源，P0 实测 100% 成功率/单轮 avg 186ms）。

    Args:
        symbols: 如 ['sh600519', 'sz000001', 'sh000300'(指数)]
    """
    if not symbols:
        return {}
    q = ",".join(symbols)
    url = QT_URL + q
    result = {}
    try:
        req = urllib.request.Request(url, headers=UA)
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("gbk", errors="replace")
        elapsed = time.time() - t0
        for line in raw.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            var_name, _, val = line.partition("=")
            sym = var_name.replace("v_", "").strip()
            if not val.startswith('"'):
                result[sym] = None
                continue
            fields = val.strip('"\n').split("~")
            if len(fields) < 40:
                result[sym] = None
                continue
            try:
                current = float(fields[3])
                pre_close = float(fields[4])
                open_ = float(fields[5])
                vol = float(fields[6])
                amount = float(fields[37]) if fields[37] else 0.0
                high = float(fields[33])
                low = float(fields[34])
                turnover = float(fields[38]) if fields[38] else 0.0
                amplitude = float(fields[43]) if len(fields) > 43 and fields[43] else 0.0
                result[sym] = {
                    "name": fields[1],
                    "current": current,
                    "pre_close": pre_close,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "vol": vol,
                    "amount": amount,
                    "turnover_rate": turnover,
                    "amplitude": amplitude,
                    "change_pct": round((current - pre_close) / pre_close * 100, 2) if pre_close else 0.0,
                    "elapsed_s": round(elapsed, 3),
                }
            except (ValueError, IndexError):
                result[sym] = None
        return result
    except Exception as e:
        print(f"[t-data] 腾讯qt 失败: {str(e)[:120]}")
        return {s: None for s in symbols}


def _normalize_symbol(symbol: str) -> str:
    """股票代码 → 腾讯/新浪格式（sh600519 / sz000001）。"""
    s = symbol.strip().upper()
    if s.startswith(("SH", "SZ")):
        return s[:2].lower() + s[2:]
    if "." in s:
        code, _, market = s.partition(".")
        return ("sh" if market == "SH" else "sz") + code
    if s.startswith(("6", "9", "5")):
        return "sh" + s
    return "sz" + s


def _to_ts_code(symbol: str) -> str:
    """股票代码 → tushare 格式（600519.SH / 000001.SZ），兼容腾讯格式（sz000636 / sh600519）。"""
    s = symbol.strip().upper()
    if s.startswith(("SH", "SZ")):
        s = s[2:]
    if "." in s:
        return s
    return s + (".SH" if s.startswith(("6", "9", "5")) else ".SZ")


def fetch_minute_bars(symbol: str, freq: str = "m5", count: int = 320) -> Optional[List[dict]]:
    """统一分钟线入口：腾讯主源，失败降级新浪。freq 支持 m1/m5/m15/m30/m60 或 1/5/15/30/60。"""
    if freq in ("1", "5", "15", "30", "60"):
        freq = "m" + freq
    bars = fetch_tencent_mkline(_normalize_symbol(symbol), freq=freq, count=count)
    if bars:
        return bars
    scale = int(freq.lstrip("m"))
    return fetch_sina_minline(_normalize_symbol(symbol), scale=scale, datalen=count)


def fetch_intraday_minutes_today(symbol: str, freq: str = "1min") -> Optional[List[dict]]:
    """当日完整分钟K线（brze rt_min_daily 权威源；失败降级腾讯 m1）。"""
    ts_code = _to_ts_code(symbol)
    bars = fetch_brze_rt_min_daily(ts_code, freq=freq)
    if bars:
        return bars
    mfreq = "m1" if freq == "1min" else "m" + freq.replace("min", "")
    return fetch_tencent_mkline(_normalize_symbol(symbol), freq=mfreq, count=500)
