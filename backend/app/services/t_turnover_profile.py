# -*- coding: utf-8 -*-
"""做T · 个股换手基准 benchmark_turnover_profile 生成器（2026-09-03）。

背景：TMonitor 盘中量比归一 = [当前累计换手×(240/已开盘分钟)] / 基准。
旧 wolf 条件未配 benchmark → 兜底 MIN_TURNOVER_BASE=0.5%，对高换手个股
系统 vol_ratio 会系统性偏大（药明 10:22：行情量比1.50 vs 系统4.49）。
修复：给条件配该股近5个交易日的换手基准，使 vol_ratio 贴近行情量比。

基准口径（same_minute_avg，供 calc_volume_ratio_at 使用）：
  = 近 N 个已完成交易日的日换手率均值（%）。
  分子是"当前节奏外推到全天"，分母用个股自身全天换手基准，
  比值≈"今天成交节奏是平时几倍"，与行情软件量比同量级。

数据源：
  1) 主：Tushare daily_basic（turnover_rate，近N已完成交易日，>0 有效）；
  2) 兜底：腾讯 m5 分钟量代理（今日 turnover_rate/vol 标定 × 近N日日均量），
     daily_basic 失败/无权限时仍可用，保证生产不空转。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


def _to_ts_code(symbol: str) -> str:
    """兼容 SH603259 / sh603259 / 603259 → 603259.SH。"""
    s = str(symbol).strip().upper()
    if s.startswith(("SH", "SZ", "BJ")):
        s = s[2:]
    if "." in s:
        return s
    return s + (".SH" if s.startswith(("6", "9", "5")) else ".SZ")


def _to_tencent_symbol(symbol: str) -> str:
    s = str(symbol).strip().lower()
    if s.startswith("sh") or s.startswith("sz") or s.startswith("bj"):
        return s
    code = s.split(".")[0]
    return ("sh" if code.startswith(("6", "9", "5")) else "sz") + code


def compute_turnover_profile(symbol: str, n_days: int = 5,
                             end_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """近 N 个已完成交易日个股换手基准。失败/数据不足返回 None（调用方保留兜底）。"""
    today = (end_date or datetime.now().strftime("%Y%m%d"))
    # ── 主：Tushare daily_basic ──
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        start = (datetime.strptime(today, "%Y%m%d") - timedelta(days=n_days * 5 + 5)).strftime("%Y%m%d")
        df = pro.daily_basic(
            ts_code=_to_ts_code(symbol),
            start_date=start, end_date=today,
            fields="trade_date,turnover_rate",
        )
        if df is not None and len(df) > 0:
            rows = []
            for _, r in df.iterrows():
                td = str(r["trade_date"])
                tr = float(r.get("turnover_rate") or 0)
                if td < today and tr > 0:          # 已完成交易日 & 有效换手
                    rows.append((td, tr))
            rows.sort(reverse=True)
            pick = rows[:n_days]
            if len(pick) >= 1:
                avg = round(sum(v for _, v in pick) / len(pick), 4)
                return {
                    "same_minute_avg": avg,
                    "basis": "tushare_daily_basic_{}d_avg".format(len(pick)),
                    "dates": [d for d, _ in pick],
                    "values": [v for _, v in pick],
                    "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
    except Exception as e:
        print(f"[t-turnover-profile] daily_basic 失败 {symbol}: {str(e)[:120]} → 尝试 m5 代理")
    # ── 兜底：腾讯 m5 量代理 ──
    try:
        from app.services.t_data_sources import fetch_tencent_quote, fetch_minute_bars
        q = fetch_tencent_quote([_to_tencent_symbol(symbol)]).get(_to_tencent_symbol(symbol)) or {}
        cur_tr = float(q.get("turnover_rate") or 0)
        cur_vol = float(q.get("vol") or 0)
        if cur_tr > 0 and cur_vol > 0:
            scale = cur_tr / cur_vol            # %/手 标定（同源单位抵消）
            bars = fetch_minute_bars(symbol, freq="m5", count=n_days * 48 + 120) or []
            by_day: Dict[str, List] = {}
            for b in bars:
                t = str(b.get("time") or b.get("trade_time"))[:10]
                by_day.setdefault(t, []).append(b)
            today_ymd = datetime.strptime(today, "%Y%m%d").strftime("%Y-%m-%d")
            vols = []
            dates = []
            for d in sorted(by_day.keys()):
                if d >= today_ymd:
                    continue
                day_vol = sum(float(b.get("vol") or b.get("volume") or 0) for b in by_day[d])
                if day_vol > 0:
                    vols.append(day_vol); dates.append(d)
            if len(vols) >= 1:
                pick_v = vols[-n_days:]; pick_d = dates[-n_days:]
                avg_vol = sum(pick_v) / len(pick_v)
                avg_tr = round(scale * avg_vol, 4)
                return {
                    "same_minute_avg": avg_tr,
                    "basis": "m5_vol_proxy_{}d_avg".format(len(pick_v)),
                    "dates": pick_d,
                    "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
    except Exception as e:
        print(f"[t-turnover-profile] m5 代理失败 {symbol}: {str(e)[:120]}")
    return None
