#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0 探针 · 数据源客户端 — 腾讯 ifzq 分钟K线 / 新浪分钟K线 / 腾讯 qt 实时行情 / brze tushare 代理。

仅只读访问公开行情接口，不写库、不交易。全部支持本地 Windows 与服务器运行。

数据源结论（2026-08-14 实测）：
- 腾讯 ifzq mkline：m1 500根(≈2.5日) / m5 320根(≈6日) / m15 320根(≈20日) / m30 320根(≈40日) / m60 320根(≈4月)，免费免token ✅
- 新浪分钟线：1/5/15/60min 均可取，含 amount，免费免token ✅
- 腾讯 qt 实时：current/高/低/开/昨收/量/额/换手率/振幅，免费免token ✅（xueqiu_engine 已用）
- brze tushare 代理（token 已续期）：stk_mins 历史分钟 ✅ / rt_k 实时日线 ✅ / rt_min 实时分钟 ✅ / rt_min_daily 当日完整分钟 ✅
  - 延迟实测：rt_k 327ms / rt_min 360ms / stk_mins 384ms / rt_min_daily 420ms（免费源更快：新浪133ms/腾讯qt193ms）
  - 用法：单线程串行 + 间隔≥1s + 失败重试（卖家文档要求，防限流）；freq 实时分钟用大写 1MIN/5MIN/60MIN

实时性决策（用户指令"哪个实时性更强用哪个"）：
- 实时触发判断（30s 轮询）→ 腾讯 qt（最快 193ms，免费）
- 分钟级数据（可T质量/量比基准/企稳确认）→ 腾讯 ifzq + 新浪双源（快、免费、交叉验证<0.1%）
- brze tushare → 权威校验源/降级冗余（官方字段完整：stk_mins 历史分钟、rt_k 实时日线）
"""
import json
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ── brze tushare 代理（token 续期已确认）────────────────────────────
BRZE_URL = "https://tu.brze.top"
BRZE_TOKEN = "SC9b-_EoiR-gUuR1hHMIddmTqHvF6D_DGOizKGo2KQk"
_BRZE_PRO = None  # 惰性初始化


def _get_brze_pro():
    """获取 brze 代理的 tushare pro 实例（惰性单例）。"""
    global _BRZE_PRO
    if _BRZE_PRO is None:
        import tushare as ts
        from tushare.pro import client as _ts_client
        _ts_client.DataApi._DataApi__http_url = BRZE_URL  # 替换代理
        _BRZE_PRO = ts.pro_api(BRZE_TOKEN)
    return _BRZE_PRO


def brze_call(fn, name: str = "brze", retries: int = 3, sleep_s: float = 3.0):
    """brze 调用包装：单线程串行 + 失败重试（卖家文档要求，防限流罚时）。"""
    last_err = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            time.sleep(sleep_s)  # 文档建议 sleep 1-3s 再重试
    raise last_err


def fetch_brze_stk_mins(ts_code: str, freq: str = "60min",
                        start_date: str = None, end_date: str = None,
                        trade_date: str = None) -> Optional[List[dict]]:
    """brze stk_mins 历史分钟K线（官方字段：ts_code/trade_time/open/close/high/low/vol/amount）。

    Args:
        ts_code: '600519.SH'
        freq: '1min'/'5min'/'15min'/'30min'/'60min'
        start_date/end_date: 'YYYY-MM-DD HH:MM:SS'（范围查询）
        trade_date: 'YYYYMMDD'（单日查询，与范围二选一）
    """
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


def fetch_brze_rt_k(ts_code: str) -> Optional[dict]:
    """brze rt_k 实时日线（官方字段：ts_code/name/pre_close/open/high/low/close/vol/amount/num/trade_time）。"""
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


def fetch_brze_rt_min(ts_code: str, freq: str = "1MIN") -> Optional[dict]:
    """brze rt_min 实时分钟（盘中最新一条分钟线；freq 大写 1MIN/5MIN/60MIN）。"""
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


def fetch_brze_rt_min_daily(ts_code: str, freq: str = "1min") -> Optional[List[dict]]:
    """brze rt_min_daily 当日完整分钟K线（最近一个交易日；freq 小写 1min/5min/60min）。"""
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

# ── 腾讯 ifzq 分钟K线 ──────────────────────────────────────────────
IFZQ_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
TENCENT_FREQS = {"m1": "m1", "m5": "m5", "m15": "m15", "m30": "m30", "m60": "m60"}


def fetch_tencent_mkline(symbol: str, freq: str = "m5", count: int = 320) -> Optional[List[dict]]:
    """拉取腾讯 ifzq 分钟K线。

    Args:
        symbol: 如 'sh600519' / 'sz000001'
        freq: m1/m5/m15/m30/m60
        count: 根数（m1 上限约500，其余约320）
    Returns:
        [{'time','open','close','high','low','vol','amount'?}] 时间升序，或 None
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
            # 腾讯 mkline 行: [time, open, close, high, low, vol, (amount|{}), ...]
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
        print(f"[tencent-mkline] {symbol} {freq} ERROR: {type(e).__name__}: {str(e)[:120]}")
        return None


# ── 新浪分钟K线 ────────────────────────────────────────────────────
SINA_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x=/CN_MarketDataService.getKLineData"
SINA_SCALES = {"1": 1, "5": 5, "15": 15, "30": 30, "60": 60}


def fetch_sina_minline(symbol: str, scale: int = 5, datalen: int = 300) -> Optional[List[dict]]:
    """拉取新浪分钟K线。

    Args:
        symbol: 如 'sh600519' / 'sz000001' / 'sh000300'(指数)
        scale: 1/5/15/30/60 分钟
        datalen: 根数
    Returns:
        [{'time','open','high','low','close','volume','amount'}] 时间升序，或 None
    """
    url = f"{SINA_URL}?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        # 响应形如: /*<script>...*/\nvar x=([{...},...]);
        start = raw.find("([")
        end = raw.rfind("])")
        if start < 0 or end < 0 or end <= start:
            print(f"[sina-min] {symbol} scale={scale} 解析失败: {raw[:100]}")
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
        print(f"[sina-min] {symbol} scale={scale} ERROR: {type(e).__name__}: {str(e)[:120]}")
        return None


# ── 腾讯 qt 实时行情 ───────────────────────────────────────────────
QT_URL = "https://qt.gtimg.cn/q="


def fetch_tencent_quote(symbols: List[str], timeout: int = 8) -> Dict[str, Optional[dict]]:
    """批量拉取腾讯 qt 实时行情（可逗号拼接多只）。

    Args:
        symbols: 如 ['sh600519', 'sz000001', 'sh000300'(指数)]
    Returns:
        {symbol: {'current','open','high','low','pre_close','vol','amount','turnover_rate','amplitude','name'} | None}
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
            # 腾讯字段: 1名称 2代码 3当前 4昨收 5今开 6成交量(手) ... 37换手率? 38PE? 详见 parse
            if len(fields) < 40:
                result[sym] = None
                continue
            try:
                current = float(fields[3])
                pre_close = float(fields[4])
                open_ = float(fields[5])
                vol = float(fields[6])           # 手
                amount = float(fields[37]) if fields[37] else 0.0  # 万元
                high = float(fields[33])
                low = float(fields[34])
                turnover = float(fields[38]) if fields[38] else 0.0  # 换手率%
                amplitude = float(fields[43]) if len(fields) > 43 and fields[43] else 0.0  # 振幅%
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
            except (ValueError, IndexError) as e:
                result[sym] = None
        return result
    except Exception as e:
        print(f"[tencent-qt] {symbols} ERROR: {type(e).__name__}: {str(e)[:120]}")
        return {s: None for s in symbols}


if __name__ == "__main__":
    # 自检
    q = fetch_tencent_quote(["sh600519", "sz000001", "sh000300"])
    for k, v in q.items():
        print(k, "OK" if v else "FAIL", v.get("current") if v else "")
    b = fetch_tencent_mkline("sh600519", "m5", 100)
    print("tencent m5 bars:", len(b) if b else 0)
    s = fetch_sina_minline("sh600519", 5, 100)
    print("sina m5 bars:", len(s) if s else 0)
    # brze 三接口自检（串行）
    rk = fetch_brze_rt_k("600519.SH")
    print("brze rt_k:", "OK" if rk else "FAIL", rk.get("close") if rk else "")
    time.sleep(1)
    rm = fetch_brze_rt_min("600519.SH", "1MIN")
    print("brze rt_min:", "OK" if rm else "FAIL", rm.get("close") if rm else "")
    time.sleep(1)
    rmd = fetch_brze_rt_min_daily("600519.SH", "1min")
    print("brze rt_min_daily bars:", len(rmd) if rmd else 0)
    time.sleep(1)
    sm = fetch_brze_stk_mins("600519.SH", "60min", trade_date="20260814")
    print("brze stk_mins 60min:", len(sm) if sm else 0)
