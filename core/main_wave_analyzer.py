#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主升浪五维评价引擎 (Main Wave Analyzer)
========================================
复刻「短线炒股分析员」的评价选股系统：

  1. K线走势还原（近N日形态标注）
  2. 走势结构拆解（底部区间 / 第一浪 / 洗盘 / 底部蓄力 / 第二浪主升）
  3. 主升浪五维判定（均线 / MACD / RSI / 缺口 / 趋势）
  4. 量价背离信号（天量滞涨 / 缩量涨停 / 天量见顶 / 高位换手）
  5. 资金面分单信号（超大单 / 大单 / 中单 / 小单净占比）
  6. 基本面排雷（PE对比 / 市值核对 / 股东减持 / 异动公告）
  7. 阶段结论 + 风险定级 + 操作建议 + 观察信号
  8. 候选股评分排序（选股）

数据源（均可降级）：
  - Tushare: daily / stk_factor_pro / moneyflow / daily_basic / limit_list_d /
             stk_holdertrade / stock_basic
  - 东方财富公告 API: 异动/风险提示公告
  - akshare(可选): 涨停池(连板数/封单/行业) + 盘口(量比/封单/卖盘)

CLI 入口见 jobs/main_wave_analyzer.py；FastAPI 端点见 backend/app/api/main_wave.py。
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from core._api_config import get_tushare_pro
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core._api_config import get_tushare_pro

# ─────────────────────────────────────────────────────────────
# 常量 / 配置
# ─────────────────────────────────────────────────────────────

# 行业平均 PE(TTM) 基线（估）：用于「PE vs 行业」对比。
# 医药生物 38 为市场常用参考；其他行业取近似值，引擎标注「估值」。
INDUSTRY_PE_BASELINE: Dict[str, float] = {
    "医药": 38.0, "医药生物": 38.0, "化学制药": 38.0, "中药": 38.0,
    "中成药": 38.0, "医药商业": 35.0, "原料药": 35.0, "化学制剂": 38.0,
    "生物制品": 45.0, "医疗器械": 40.0, "医疗服务": 45.0,
    "建筑装饰": 15.0, "装修装饰": 15.0, "装饰": 15.0,
    "计算机": 45.0, "软件": 50.0, "半导体": 55.0, "电子": 35.0,
    "通信": 30.0, "传媒": 30.0, "食品饮料": 30.0, "白酒": 32.0,
    "汽车": 20.0, "电力设备": 25.0, "新能源": 25.0, "有色金属": 25.0,
    "钢铁": 12.0, "煤炭": 10.0, "石油石化": 12.0, "基础化工": 20.0,
    "机械设备": 25.0, "国防军工": 50.0, "银行": 6.0, "非银金融": 12.0,
    "房地产": 12.0, "商贸零售": 20.0, "农林牧渔": 20.0, "家用电器": 15.0,
    "纺织服饰": 15.0, "轻工制造": 15.0, "公用事业": 15.0, "交通运输": 15.0,
    "环保": 20.0, "社会服务": 25.0, "美容护理": 35.0,
}
DEFAULT_INDUSTRY_PE = 30.0

# 涨停判定阈值（主板/中小板 10%，创业板/科创板 20%）
LIMIT_UP_PCT = {0: 9.8, 1: 19.8}      # 0=主板(60/00), 1=创业/科创(30/68)
# 跌停判定
LIMIT_DOWN_PCT = {0: -9.8, 1: -19.8}

# 天数窗口
LOOKBACK_DAYS = 120      # 拉取的K线天数
STRUCTURE_WINDOW = 24    # 找底部区间的时间窗口（近一个月视角）
PLATFORM_MIN_DAYS = 5    # 平台期最少天数
ACCUMULATION_MIN_DAYS = 8  # 底部蓄力最少天数


# ─────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────

def _to_ts_code(symbol: str) -> str:
    """统一为 tushare 标准格式 xxxxxx.SH/.SZ。"""
    s = str(symbol).strip().upper()
    if "." in s:
        return s
    if s.startswith("SH"):
        return f"{s[2:]}.SH"
    if s.startswith("SZ"):
        return f"{s[2:]}.SZ"
    if s.startswith(("5", "6", "9")):
        return f"{s}.SH"
    if s.startswith(("0", "2", "3")):
        return f"{s}.SZ"
    return s


def _short_symbol(ts_code: str) -> str:
    return ts_code.split(".")[0]


def _board_type(ts_code: str) -> int:
    """0=主板(10%)，1=创业/科创(20%)。"""
    code = _short_symbol(ts_code)
    if code.startswith(("30", "68")):
        return 1
    return 0


def _limit_pct(ts_code: str) -> float:
    return LIMIT_UP_PCT[_board_type(ts_code)]


def _fmt_wan(wan: float, suffix: str = "") -> str:
    """万元 → 人类可读。"""
    if abs(wan) >= 10000:
        return f"{wan / 10000:+.2f}亿{suffix}"
    return f"{wan:+.0f}万{suffix}"


def _fmt_amount_yi(amount_kq: float) -> str:
    """成交额（千元）→ 亿元/万元。"""
    yi = amount_kq / 100000.0
    if abs(yi) >= 1:
        return f"{yi:.2f}亿"
    wan = amount_kq / 10.0
    return f"{wan:.0f}万"


def _fmt_vol(vol_shou: float) -> str:
    """成交量（手）→ 万手/手。"""
    if vol_shou >= 10000:
        return f"{vol_shou / 10000:.1f}万手"
    return f"{vol_shou:.0f}手"


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def _is_limit_up(pct_chg: float, ts_code: str, close: float = 0.0) -> bool:
    return pct_chg >= _limit_pct(ts_code) - 0.5


def _is_limit_down(pct_chg: float, ts_code: str) -> bool:
    return pct_chg <= -(LIMIT_UP_PCT[_board_type(ts_code)]) + 0.5


def _safe_float(v) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _load_env() -> None:
    """加载 .env（幂等）。"""
    if os.environ.get("_MW_ENV_LOADED"):
        return
    root = Path(__file__).resolve().parents[1]
    env = root / ".env"
    if env.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env)
        except Exception:
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    os.environ["_MW_ENV_LOADED"] = "1"


# ─────────────────────────────────────────────────────────────
# 数据层（Tushare + 东财公告 + akshare 可选）
# ─────────────────────────────────────────────────────────────

class DataFetcher:
    """统一数据获取，带进程内缓存与降级。"""

    def __init__(self, use_akshare: bool = True, verbose: bool = False):
        _load_env()
        self._pro = None
        self._cache: Dict[str, Any] = {}
        self.verbose = verbose
        self.use_akshare = use_akshare

    # ---- tushare ----
    @property
    def pro(self):
        if self._pro is None:
            self._pro = get_tushare_pro()
        return self._pro

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[main_wave] {msg}", file=sys.stderr)

    def _cached(self, key: str, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    # ---- 日K线 ----
    def klines(self, symbol: str, days: int = LOOKBACK_DAYS,
               end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        ts_code = _to_ts_code(symbol)
        key = f"kl:{ts_code}:{days}:{end_date}"
        return self._cached(key, lambda: self._fetch_klines(ts_code, days, end_date))

    def _fetch_klines(self, ts_code: str, days: int,
                      end_date: Optional[str]) -> List[Dict[str, Any]]:
        """带重试的日K线获取（限流/超时自动重试 3 次）。"""
        df = None
        for attempt in range(3):
            try:
                pro = self.pro
                end = end_date or datetime.now().strftime("%Y%m%d")
                start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=days * 2)).strftime("%Y%m%d")
                df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
                if df is not None and not df.empty:
                    break
                self._log(f"daily 无数据 {ts_code} (attempt {attempt + 1})")
                df = None
            except Exception as e:
                self._log(f"daily FAIL {ts_code} (attempt {attempt + 1}): {e!r}")
                df = None
            if attempt < 2:
                time.sleep(1.2 + attempt)
        if df is None or df.empty:
            return []

        df = df.sort_values("trade_date", ascending=True).reset_index(drop=True)
        bars = []
        for _, r in df.iterrows():
            bars.append({
                "trade_date": str(r["trade_date"]),
                "open": _safe_float(r["open"]),
                "high": _safe_float(r["high"]),
                "low": _safe_float(r["low"]),
                "close": _safe_float(r["close"]),
                "pre_close": _safe_float(r.get("pre_close", 0) or 0),
                "pct_chg": _safe_float(r.get("pct_chg", 0) or 0),
                "vol": _safe_float(r.get("vol", 0) or 0),        # 手
                "amount": _safe_float(r.get("amount", 0) or 0),  # 千元
            })
        # 补齐 pre_close / pct_chg（个别行可能缺失）
        for i, b in enumerate(bars):
            if i > 0 and (b["pre_close"] <= 0 or b["pct_chg"] == 0):
                prev_close = bars[i - 1]["close"]
                b["pre_close"] = prev_close
                b["pct_chg"] = _pct(b["close"], prev_close)
        return bars

    # ---- 技术因子（盘后确认）----
    def technical(self, symbol: str, end_date: Optional[str] = None) -> Dict[str, Any]:
        ts_code = _to_ts_code(symbol)
        key = f"tech:{ts_code}:{end_date}"
        return self._cached(key, lambda: self._fetch_technical(ts_code, end_date))

    def _fetch_technical(self, ts_code: str, end_date: Optional[str]) -> Dict[str, Any]:
        """返回 {latest: {...}, history: [...]}；latest 为 <= end_date 的最后一条。"""
        try:
            pro = self.pro
            end = end_date or datetime.now().strftime("%Y%m%d")
            start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=40)).strftime("%Y%m%d")
            df = pro.stk_factor_pro(ts_code=ts_code, start_date=start, end_date=end)
            if df is None or df.empty:
                return {"latest": {}, "history": []}
            df = df.sort_values("trade_date", ascending=True).reset_index(drop=True)
            keep = ["trade_date", "close", "macd_bfq", "macd_dif_bfq", "macd_dea_bfq",
                    "rsi_bfq_6", "rsi_bfq_12", "rsi_bfq_24",
                    "boll_upper_bfq", "boll_mid_bfq", "boll_lower_bfq"]
            cols = [c for c in keep if c in df.columns]
            sub = df[cols].copy()
            for c in cols:
                if c != "trade_date":
                    sub[c] = sub[c].apply(_safe_float)
            history = sub.to_dict("records")
            return {"latest": history[-1] if history else {}, "history": history}
        except Exception as e:
            self._log(f"stk_factor FAIL {ts_code}: {e!r}")
            return {"latest": {}, "history": []}

    # ---- 资金流向（分单）----
    def moneyflow(self, symbol: str, days: int = 20,
                  end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        ts_code = _to_ts_code(symbol)
        key = f"mf:{ts_code}:{days}:{end_date}"
        return self._cached(key, lambda: self._fetch_moneyflow(ts_code, days, end_date))

    def _fetch_moneyflow(self, ts_code: str, days: int,
                         end_date: Optional[str]) -> List[Dict[str, Any]]:
        try:
            pro = self.pro
            end = end_date or datetime.now().strftime("%Y%m%d")
            start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=days * 2)).strftime("%Y%m%d")
            df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
            if df is None or df.empty:
                return []
            df = df.sort_values("trade_date", ascending=True).reset_index(drop=True)
            rows = []
            for _, r in df.iterrows():
                rows.append({
                    "trade_date": str(r["trade_date"]),
                    "buy_elg": _safe_float(r.get("buy_elg_amount", 0) or 0),
                    "sell_elg": _safe_float(r.get("sell_elg_amount", 0) or 0),
                    "buy_lg": _safe_float(r.get("buy_lg_amount", 0) or 0),
                    "sell_lg": _safe_float(r.get("sell_lg_amount", 0) or 0),
                    "buy_md": _safe_float(r.get("buy_md_amount", 0) or 0),
                    "sell_md": _safe_float(r.get("sell_md_amount", 0) or 0),
                    "buy_sm": _safe_float(r.get("buy_sm_amount", 0) or 0),
                    "sell_sm": _safe_float(r.get("sell_sm_amount", 0) or 0),
                    "buy_elg_vol": _safe_float(r.get("buy_elg_vol", 0) or 0),
                    "sell_elg_vol": _safe_float(r.get("sell_elg_vol", 0) or 0),
                    "buy_lg_vol": _safe_float(r.get("buy_lg_vol", 0) or 0),
                    "sell_lg_vol": _safe_float(r.get("sell_lg_vol", 0) or 0),
                    "net_mf_amount": _safe_float(r.get("net_mf_amount", 0) or 0),
                })
            return rows
        except Exception as e:
            self._log(f"moneyflow FAIL {ts_code}: {e!r}")
            return []

    # ---- 每日基本面 ----
    def daily_basic(self, symbol: str, end_date: Optional[str] = None) -> Dict[str, Any]:
        ts_code = _to_ts_code(symbol)
        key = f"basic:{ts_code}:{end_date}"
        return self._cached(key, lambda: self._fetch_daily_basic(ts_code, end_date))

    def _fetch_daily_basic(self, ts_code: str, end_date: Optional[str]) -> Dict[str, Any]:
        try:
            pro = self.pro
            end = end_date or datetime.now().strftime("%Y%m%d")
            start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
            df = pro.daily_basic(
                ts_code=ts_code, start_date=start, end_date=end,
                fields="trade_date,ts_code,pe_ttm,total_mv,circ_mv,turnover_rate,volume_ratio,float_share,total_share")
            if df is None or df.empty:
                return {}
            df = df.sort_values("trade_date", ascending=True).reset_index(drop=True)
            row = df.iloc[-1].to_dict()
            out = {}
            for k in ["trade_date", "pe_ttm", "total_mv", "circ_mv", "turnover_rate",
                      "volume_ratio", "float_share", "total_share"]:
                out[k] = _safe_float(row.get(k, 0) or 0) if k != "trade_date" else str(row.get(k, ""))
            return out
        except Exception as e:
            self._log(f"daily_basic FAIL {ts_code}: {e!r}")
            return {}

    # ---- 涨停池 / 连板数 ----
    def limit_info(self, symbol: str, trade_date: Optional[str] = None) -> Dict[str, Any]:
        ts_code = _to_ts_code(symbol)
        key = f"lim:{ts_code}:{trade_date}"
        return self._cached(key, lambda: self._fetch_limit_info(ts_code, trade_date))

    def _fetch_limit_info(self, ts_code: str, trade_date: Optional[str]) -> Dict[str, Any]:
        try:
            pro = self.pro
            day = trade_date or datetime.now().strftime("%Y%m%d")
            df = pro.limit_list_d(
                trade_date=day,
                fields="trade_date,ts_code,name,close,pct_chg,limit_type,limit_times,amount,turnover_ratio,fd_amount")
            if df is None or df.empty:
                return {}
            m = df[df["ts_code"] == ts_code]
            if m.empty:
                return {}
            r = m.iloc[0].to_dict()
            return {
                "trade_date": str(r.get("trade_date", day)),
                "limit_times": _safe_float(r.get("limit_times", 0) or 0),
                "limit_type": r.get("limit_type"),
                "fd_amount": _safe_float(r.get("fd_amount", 0) or 0),  # 封单金额(元)
                "turnover_ratio": _safe_float(r.get("turnover_ratio", 0) or 0),
            }
        except Exception as e:
            self._log(f"limit_list_d FAIL {ts_code}: {e!r}")
            return {}

    # ---- 股东增减持 ----
    def holdertrades(self, symbol: str, days: int = 60,
                     end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        ts_code = _to_ts_code(symbol)
        key = f"ht:{ts_code}:{days}:{end_date}"
        return self._cached(key, lambda: self._fetch_holdertrades(ts_code, days, end_date))

    def _fetch_holdertrades(self, ts_code: str, days: int,
                            end_date: Optional[str]) -> List[Dict[str, Any]]:
        try:
            pro = self.pro
            end = end_date or datetime.now().strftime("%Y%m%d")
            start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=days)).strftime("%Y%m%d")
            df = pro.stk_holdertrade(ts_code=ts_code, start_date=start, end_date=end)
            if df is None or df.empty:
                return []
            df = df.sort_values("ann_date", ascending=False)
            rows = []
            for _, r in df.iterrows():
                rows.append({
                    "ann_date": str(r.get("ann_date", "")),
                    "holder_name": str(r.get("holder_name", "")),
                    "in_de": str(r.get("in_de", "")),          # IN=增持 DE=减持
                    "change_vol": _safe_float(r.get("change_vol", 0) or 0),
                    "change_ratio": _safe_float(r.get("change_ratio", 0) or 0),
                    "after_ratio": _safe_float(r.get("after_ratio", 0) or 0),
                    "avg_price": _safe_float(r.get("avg_price", 0) or 0),
                })
            return rows
        except Exception as e:
            self._log(f"stk_holdertrade FAIL {ts_code}: {e!r}")
            return []

    # ---- 股票基础信息 ----
    def stock_info(self, symbol: str) -> Dict[str, Any]:
        ts_code = _to_ts_code(symbol)
        key = f"info:{ts_code}"
        return self._cached(key, lambda: self._fetch_stock_info(ts_code))

    def _fetch_stock_info(self, ts_code: str) -> Dict[str, Any]:
        try:
            pro = self.pro
            df = pro.stock_basic(ts_code=ts_code,
                                 fields="ts_code,symbol,name,area,industry,market,list_date")
            if df is None or df.empty:
                return {}
            r = df.iloc[0].to_dict()
            return {k: (str(v) if v is not None else "") for k, v in r.items()}
        except Exception as e:
            self._log(f"stock_basic FAIL {ts_code}: {e!r}")
            return {}

    # ---- 东方财富公告（异动/风险提示）----
    def announcements(self, symbol: str, days: int = 20) -> List[Dict[str, Any]]:
        key = f"ann:{_to_ts_code(symbol)}:{days}"
        return self._cached(key, lambda: self._fetch_announcements(symbol, days))

    def _fetch_announcements(self, symbol: str, days: int) -> List[Dict[str, Any]]:
        import urllib.request
        import ssl
        code = _short_symbol(_to_ts_code(symbol))
        url = ("https://np-anotice-stock.eastmoney.com/api/security/ann"
               f"?sr=-1&page_size=30&page_index=1&ann_type=A&client_source=web&stock_list={code}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = json.loads(urllib.request.urlopen(
                req, timeout=8, context=ssl.create_default_context()).read().decode("utf-8"))
            lst = (data.get("data") or {}).get("list") or []
            out = []
            now = datetime.now()
            for a in lst:
                try:
                    d = datetime.strptime(str(a.get("notice_date", ""))[:10], "%Y-%m-%d")
                except Exception:
                    continue
                if (now - d).days > days:
                    continue
                title = str(a.get("title", ""))
                out.append({"date": d.strftime("%Y%m%d"), "date_fmt": d.strftime("%m/%d"),
                            "title": title})
            return out
        except Exception as e:
            self._log(f"公告 API FAIL {symbol}: {e!r}")
            return []

    # ---- 盘口数据（akshare 东财盘口：封单/卖盘/量比）----
    def board_data(self, symbol: str) -> Dict[str, Any]:
        key = f"board:{_to_ts_code(symbol)}"
        return self._cached(key, lambda: self._fetch_board(symbol))

    def _fetch_board(self, symbol: str) -> Dict[str, Any]:
        if not self.use_akshare:
            return {}
        try:
            import akshare as ak
            df = ak.stock_bid_ask_em(symbol=_short_symbol(_to_ts_code(symbol)))
            if df is None or df.empty:
                return {}
            kv = {}
            for _, row in df.iterrows():
                item = str(row.get("item", ""))
                kv[item] = row.get("value")
            sell_counts = []
            for i in range(1, 6):
                try:
                    sell_counts.append(float(kv.get(f"卖{i}量", 0) or 0))
                except (TypeError, ValueError):
                    sell_counts.append(0.0)
            try:
                buy1 = float(kv.get("买1量", 0) or 0)
            except (TypeError, ValueError):
                buy1 = 0.0
            return {
                "price": kv.get("最新"),
                "vol_ratio": kv.get("量比"),
                "turnover": kv.get("换手"),
                "pe": kv.get("市盈率"),
                "volume": kv.get("成交量"),
                "amount": kv.get("成交额"),
                "sell_total": sum(sell_counts),
                "sell_vols": sell_counts,
                "buy1_vol": buy1,          # 买一量（一字板时=封单，手）
                "limit_up_price": kv.get("涨停"),
            }
        except Exception as e:
            self._log(f"盘口 FAIL {symbol}: {e!r}")
            return {}

    # ---- akshare 涨停池（可选增强）----
    def zt_pool(self, trade_date: Optional[str] = None) -> List[Dict[str, Any]]:
        key = f"zt:{trade_date}"
        return self._cached(key, lambda: self._fetch_zt_pool(trade_date))

    def _fetch_zt_pool(self, trade_date: Optional[str]) -> List[Dict[str, Any]]:
        if not self.use_akshare:
            return []
        try:
            import akshare as ak
            day = trade_date or datetime.now().strftime("%Y%m%d")
            df = ak.stock_zt_pool_em(date=day)
            if df is None or df.empty:
                return []
            rows = []
            for _, r in df.iterrows():
                rows.append({
                    "code": str(r.get("代码", "")),
                    "name": str(r.get("名称", "")),
                    "close": _safe_float(r.get("最新价", 0) or 0),
                    "pct_chg": _safe_float(r.get("涨跌幅", 0) or 0),
                    "limit_times": _safe_float(r.get("连板数", 0) or 0),
                    "fd_amount": _safe_float(r.get("封单资金", r.get("封板资金", 0)) or 0),   # 元
                    "turnover": _safe_float(r.get("换手率", 0) or 0),
                    "industry": str(r.get("所属行业", "")),
                    "amount": _safe_float(r.get("成交额", 0) or 0),
                })
            return rows
        except Exception as e:
            self._log(f"akshare 涨停池 FAIL: {e!r}")
            return []


# ─────────────────────────────────────────────────────────────
# 分析层：K线形态标注 / 缺口 / 走势结构
# ─────────────────────────────────────────────────────────────

def _avg_prev(series: List[float], idx: int, n: int) -> float:
    """bar idx 之前 n 根的平均值（含 idx 本身之前的）。"""
    if idx < 1:
        return 0.0
    seg = series[max(0, idx - n): idx]
    return sum(seg) / len(seg) if seg else 0.0


def annotate_bars(bars: List[Dict[str, Any]], ts_code: str) -> List[Dict[str, Any]]:
    """为每根K线打形态标签（涨停/一字/天量/假阴/洗盘/突破/跌停等）。"""
    n = len(bars)
    out = []
    vols = [b["vol"] for b in bars]
    closes = [b["close"] for b in bars]
    limit_pct = _limit_pct(ts_code)

    for i, b in enumerate(bars):
        b = dict(b)
        flags: List[str] = []
        vol = b["vol"]
        avg5 = _avg_prev(vols, i, 5)
        avg20 = _avg_prev(vols, i, 20)
        is_lu = _is_limit_up(b["pct_chg"], ts_code)
        is_ld = _is_limit_down(b["pct_chg"], ts_code)
        tianliang = avg5 > 0 and vol >= 2.0 * avg5
        # 一字/近似一字（开盘封死：open≈high≈close）
        one_word = is_lu and (b["high"] == b["low"] or
                              (abs(b["open"] - b["close"]) < 0.005 * b["close"] and
                               abs(b["open"] - b["high"]) < 0.005 * b["close"]))
        fake_yin = b["close"] > b["pre_close"] and b["close"] < b["open"]
        upper_shadow = b["high"] > b["close"] and (b["high"] - b["close"]) / b["close"] > 0.02

        # 涨停形态
        if is_lu:
            if one_word:
                flags.append("一字板")
                if tianliang:
                    flags.append("天量一字")
                elif avg5 > 0 and vol < 0.7 * avg5:
                    flags.append("缩量一字")
            elif tianliang:
                flags.append("天量开板")
            else:
                flags.append("涨停")
        if is_ld:
            flags.append("跌停")
        # 天量滞涨：天量 + 涨幅很小 + 有上影
        if tianliang and not is_lu and b["pct_chg"] < 3.0 and upper_shadow:
            flags.append("天量滞涨")
        # 天量见顶：天量 + 未涨停 + 长上影
        if tianliang and not is_lu and upper_shadow and b["pct_chg"] < 8.0:
            flags.append("天量见顶")
        # 假阴线
        if fake_yin:
            flags.append("假阴")
        # 高开暴跌洗盘：前一日涨停 + 今日高开 + 盘中大幅回落
        if i >= 1:
            prev = bars[i - 1]
            prev_lu = _is_limit_up(prev["pct_chg"], ts_code)
            gap_up = b["open"] > prev["close"] * 1.01
            big_drop = b["close"] < b["open"] * 0.95
            close_down = b["close"] < prev["close"] * 0.99
            if prev_lu and gap_up and big_drop and close_down:
                flags.append("高开暴跌洗盘")
        # 放量突破：放量 + 创近20日新高
        if not is_lu and b["pct_chg"] > 5 and avg5 > 0 and vol >= 1.5 * avg5:
            hi20 = max(closes[max(0, i - 20): i]) if i > 0 else b["close"]
            if b["close"] > hi20 * 1.01:
                flags.append("放量突破")
        # 缩量筑底：缩量 + 窄幅
        if not is_lu and not is_ld and avg20 > 0 and vol < 0.7 * avg20:
            rng = (b["high"] - b["low"]) / b["close"] if b["close"] else 1
            if rng < 0.04:
                flags.append("缩量筑底")

        # 标签：涨停连板计数（如 二板/三板）
        if is_lu:
            streak = 0
            j = i
            while j >= 0 and _is_limit_up(bars[j]["pct_chg"], ts_code):
                streak += 1
                j -= 1
            if streak >= 2 and not one_word:
                flags.append(f"{streak}连板")

        b["_flags"] = flags
        b["_is_limit_up"] = is_lu
        b["_is_limit_down"] = is_ld
        b["_tianliang"] = tianliang
        out.append(b)
    return out


def bar_label(b: Dict[str, Any]) -> str:
    """生成单根K线的一句话形态（供表格显示）。"""
    fl = b.get("_flags", [])
    if not fl:
        pct = b.get("pct_chg", 0)
        if pct > 2:
            return "放量上攻" if b.get("_tianliang") else "上涨"
        if pct < -2:
            return "下跌"
        return "横盘"
    label = " ".join(fl)
    # 组合成 bot 风格的短标签
    if "天量滞涨" in fl:
        return "⚠️ 天量假阴" if "假阴" in fl else "⚠️ 天量滞涨"
    if "天量见顶" in fl:
        return "天量见顶"
    if "高开暴跌洗盘" in fl:
        return "高开暴跌洗盘"
    if "一字板" in fl:
        if "天量一字" in fl:
            return "天量一字板"
        if "缩量一字" in fl:
            return "缩量一字板"
        return "一字板"
    if "跌停" in fl:
        return "跌停"
    if "涨停" in fl:
        base = "涨停"
        if "天量开板" in fl:
            base = "天量开板"
        if "假阴" in fl:
            return "⚠️ 天量假阴"
        return base
    if "放量突破" in fl:
        return "放量突破"
    if "缩量筑底" in fl:
        return "缩量筑底"
    return label


def detect_gaps(bars: List[Dict[str, Any]], create_window: int = 12,
                filled_lookback: int = 6) -> Dict[str, Any]:
    """
    向上跳空缺口检测（涨停缺口）。
    缺口区间 = (前收 high, 当日 low)；之后某日 low <= 缺口上沿 视为触碰/回补。
    返回 {unfilled: [...], filled: [...], unfilled_count, filled_recent_count}
    """
    gaps: List[Dict[str, Any]] = []
    for i in range(1, len(bars)):
        prev = bars[i - 1]
        cur = bars[i]
        if cur["low"] > prev["high"] * 1.001 and cur["pct_chg"] > 3:
            gaps.append({
                "date": cur["trade_date"],
                "date_fmt": cur["trade_date"][4:6] + "/" + cur["trade_date"][6:8],
                "lo": prev["high"],
                "hi": cur["low"],
                "created_idx": i,
                "filled_idx": None,
                "filled_date": None,
            })
    # 回补检测
    for g in gaps:
        for j in range(g["created_idx"] + 1, len(bars)):
            if bars[j]["low"] <= g["hi"]:
                g["filled_idx"] = j
                g["filled_date"] = bars[j]["trade_date"]
                break
    n = len(bars)
    unfilled = []
    filled = []
    for g in gaps:
        if g["filled_idx"] is None:
            unfilled.append(g)
        else:
            filled.append(g)
    # 只看近 create_window 根内创建的缺口（bot 数的是主升段缺口）
    unfilled_recent = [g for g in unfilled if n - 1 - g["created_idx"] < create_window]
    filled_recent = [g for g in filled if n - 1 - g["filled_idx"] < filled_lookback]
    return {
        "unfilled": unfilled_recent,
        "unfilled_all": unfilled,
        "filled_recent": filled_recent,
        "unfilled_count": len(unfilled_recent),
        "filled_recent_count": len(filled_recent),
    }


def wave_structure(bars: List[Dict[str, Any]], ts_code: str) -> Dict[str, Any]:
    """
    走势结构拆解：底部区间 / 第一浪 / 洗盘 / 底部蓄力 / 第二浪（主升浪）。
    """
    n = len(bars)
    if n < 15:
        return {}
    last_close = bars[-1]["close"]

    # 底部：最近 STRUCTURE_WINDOW 根（不含最近3根）的最低价
    lo_idx = max(0, n - STRUCTURE_WINDOW)
    hi_idx = max(lo_idx, n - 3)
    base_bar = min(bars[lo_idx:hi_idx], key=lambda b: b["low"])
    base_low = base_bar["low"]
    base_date = base_bar["trade_date"]

    # 启动点：底部之后第一个涨停（最近 20 根内）——之后会用主升浪段覆盖
    launch = None
    for i in range(base_idx := bars.index(base_bar) + 1, n):
        if bars[i]["_is_limit_up"]:
            launch = {"idx": i, "date": bars[i]["trade_date"], "close": bars[i]["close"],
                      "pct": bars[i]["pct_chg"]}
            break

    # 涨停统计（全窗口）
    limit_up_count = 0
    limit_dates = []
    max_consecutive = 0
    cur_streak = 0
    for i, b in enumerate(bars):
        if b["_is_limit_up"]:
            limit_up_count += 1
            limit_dates.append(b["trade_date"])
            cur_streak += 1
            max_consecutive = max(max_consecutive, cur_streak)
        else:
            cur_streak = 0

    # 主升浪（当前这一段）涨停数：从最近一个涨停往回找，
    # 允许中间最多隔 1 个非涨停交易日（8/19 假阴不算断板）
    main_wave_lu_count = 0
    main_wave_start = None
    j = n - 1
    while j >= 0 and not bars[j]["_is_limit_up"]:
        j -= 1
    if j >= 0:
        main_wave_lu_count = 1
        main_wave_start = j
        k = j - 1
        while k >= 0:
            if bars[k]["_is_limit_up"]:
                gap_days = j - k - 1
                if gap_days > 1:
                    break
                main_wave_lu_count += 1
                main_wave_start = k
                j = k
            k -= 1

    # 洗盘：高开暴跌洗盘（只取近一个月的结构，避免取到远古洗盘）
    washout = None
    for i, b in enumerate(bars[max(0, n - STRUCTURE_WINDOW):], start=max(0, n - STRUCTURE_WINDOW)):
        if "高开暴跌洗盘" in b.get("_flags", []):
            washout = {
                "idx": i, "date": b["trade_date"], "date_fmt": b["trade_date"][4:6] + "/" + b["trade_date"][6:8],
                "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"],
                "vol": b["vol"], "pct": b["pct_chg"],
            }
            break

    # 主升浪启动点：优先取「当前这一段」的第一个涨停（第二浪）
    if main_wave_start is not None:
        launch = {"idx": main_wave_start,
                  "date": bars[main_wave_start]["trade_date"],
                  "close": bars[main_wave_start]["close"],
                  "pct": bars[main_wave_start]["pct_chg"]}

    # 底部蓄力天数：洗盘/底部之后 到 主升浪启动点
    base_idx = bars.index(base_bar)
    acc_from = washout["idx"] if (washout and washout["idx"] > base_idx) else base_idx
    accumulation_days = 0
    if launch:
        accumulation_days = max(0, launch["idx"] - acc_from - 1)

    # 平台区间：底部到主升浪启动点之间的收盘价范围
    platform = {"lo": None, "hi": None, "days": 0, "center": None}
    if launch:
        seg = bars[base_idx: launch["idx"]]  # 平台期不含启动日
        if seg:
            closes = [b["close"] for b in seg]
            platform = {
                "lo": min(closes), "hi": max(closes),
                "days": len(seg), "center": sum(closes) / len(closes),
            }

    # 第二浪（主升浪）：主升浪启动点之后
    second_wave = None
    if launch:
        seg = bars[launch["idx"]: n]
        second_wave = {
            "days": len(seg),
            "high": max(b["high"] for b in seg),
            "high_date": max(seg, key=lambda b: b["high"])["trade_date"],
            "close": seg[-1]["close"],
        }

    # 近一月涨幅（约21个交易日）
    month_ago_close = bars[n - 22]["close"] if n >= 22 else bars[0]["close"]
    month_pct = _pct(last_close, month_ago_close)

    # 从底部 / 从主升浪启动涨幅
    run_from_base = _pct(last_close, base_low)
    run_from_launch = _pct(last_close, launch["close"]) if launch else 0.0

    # 高点回撤：用 min(最新低点, 最新收盘) 计算（盘中低点口径，金螳螂 -19.8%）
    high_pullback = 0.0
    if launch:
        wave_high = max(b["high"] for b in bars[launch["idx"]: n])
        ref = min(bars[-1]["low"], bars[-1]["close"])
        high_pullback = _pct(ref, wave_high)

    return {
        "base_low": round(base_low, 3),
        "base_date": base_date,
        "base_date_fmt": base_date[4:6] + "/" + base_date[6:8],
        "last_close": round(last_close, 3),
        "launch": launch,
        "limit_up_count": limit_up_count,
        "main_wave_lu_count": main_wave_lu_count,
        "main_wave_start": main_wave_start,
        "main_wave_dates": [b["trade_date"] for b in bars[main_wave_start:]
                             if b.get("_is_limit_up")] if main_wave_start is not None else [],
        "limit_dates": limit_dates,
        "max_consecutive": max_consecutive,
        "washout": washout,
        "accumulation_days": accumulation_days,
        "platform": platform,
        "second_wave": second_wave,
        "month_pct": round(month_pct, 1),
        "run_from_base": round(run_from_base, 1),
        "run_from_launch": round(run_from_launch, 1),
        "high_pullback": round(high_pullback, 1),
    }


# ─────────────────────────────────────────────────────────────
# 分析层：五维判定
# ─────────────────────────────────────────────────────────────

def compute_mas(bars: List[Dict[str, Any]]) -> Dict[str, float]:
    closes = [b["close"] for b in bars]
    out = {}
    for p in (5, 10, 20, 60):
        if len(closes) >= p:
            out[f"ma{p}"] = round(sum(closes[-p:]) / p, 3)
        else:
            out[f"ma{p}"] = None
    return out


def trend_state(bars: List[Dict[str, Any]], tech: Dict[str, Any],
                ma: Dict[str, float]) -> str:
    """返回 strong_bullish / bullish / sideways / bearish。"""
    if not bars:
        return "unknown"
    last = bars[-1]
    close = last["close"]
    ma5, ma10, ma20, ma60 = ma.get("ma5"), ma.get("ma10"), ma.get("ma20"), ma.get("ma60")
    aligned = all(x is not None for x in (ma5, ma10, ma20, ma60)) and         ma5 > ma10 > ma20 > ma60
    t = tech.get("latest", {})
    dif, dea = _safe_float(t.get("macd_dif_bfq", 0)), _safe_float(t.get("macd_dea_bfq", 0))
    golden = dif > dea
    if aligned and close > (ma5 or 0) and golden and last["_is_limit_up"]:
        return "strong_bullish"
    if aligned and close > (ma10 or 0) and golden:
        return "strong_bullish" if last["pct_chg"] > 3 else "bullish"
    recent_limit_down = any(b.get("_is_limit_down") for b in bars[-3:])
    if close < (ma5 or 0) and recent_limit_down:
        return "sideways"          # 天量见顶+跌停后，即使指标仍金叉，趋势已转横盘
    if (ma20 and close > ma20) and golden:
        return "bullish"
    if ma20 and ma60 and close <= ma20 and close >= ma60:
        return "sideways"
    if last.get("_is_limit_down") or (ma5 and ma10 and ma5 < ma10 and ma20 and close < ma20):
        return "bearish"
    return "sideways"


def _calc_rsi_series(closes: List[float], period: int) -> Optional[float]:
    """Wilder RSI：用最近 period+1 根收盘价计算，与实时指标模块口径一致。"""
    need = period + 1
    if len(closes) < need:
        return None
    prices = closes[-(need):]
    gains = losses = 0.0
    for i in range(1, len(prices)):
        chg = prices[i] - prices[i - 1]
        if chg > 0:
            gains += chg
        else:
            losses += abs(chg)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


def five_dimensions(bars: List[Dict[str, Any]], tech: Dict[str, Any],
                    gaps: Dict[str, Any]) -> Dict[str, Any]:
    """五维判定（均线/MACD/RSI/缺口/趋势）+ 量价背离信号。"""
    if not bars:
        return {}
    ma = compute_mas(bars)
    last = bars[-1]
    close = last["close"]
    t = tech.get("latest", {})
    hist = tech.get("history", [])

    # ① 均线
    ma5, ma10, ma20, ma60 = ma.get("ma5"), ma.get("ma10"), ma.get("ma20"), ma.get("ma60")
    aligned = all(x is not None for x in (ma5, ma10, ma20, ma60)) and         ma5 > ma10 > ma20 > ma60
    aligned_soft = all(x is not None for x in (ma5, ma10, ma20)) and ma5 > ma10 > ma20
    converging = (ma5 is not None and ma10 is not None and ma5 <= ma10) or         (ma5 is not None and close < ma5)
    spread = round((ma5 - ma60) / ma60 * 100, 1) if ma5 and ma60 else None
    ma_verdict = {
        "aligned": aligned,
        "aligned_soft": aligned_soft,
        "converging": converging,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "spread_pct": spread,
        "desc": (f"MA5({ma5}) > MA10({ma10}) > MA20({ma20}) > MA60({ma60})" if aligned
                 else "多头排列（收敛中）" if aligned_soft else "均线走坏"),
        "ok": aligned,
    }

    # ② MACD
    dif = _safe_float(t.get("macd_dif_bfq", 0))
    dea = _safe_float(t.get("macd_dea_bfq", 0))
    bar = _safe_float(t.get("macd_bfq", 0))
    golden = dif > dea
    dif_falling = False
    if len(hist) >= 2:
        prev = hist[-2]
        dif_falling = dif < _safe_float(prev.get("macd_dif_bfq", dif))
    macd_verdict = {
        "golden": golden,
        "dif": dif, "dea": dea, "bar": bar,
        "dif_falling": dif_falling,
        "desc": f"DIF({dif:.3f}) > DEA({dea:.3f})，MACD柱={bar:.3f}" if golden
                else f"DIF({dif:.3f}) < DEA({dea:.3f})",
        "ok": golden and bar > 0,
    }

    # ③ RSI：优先用收盘价自算（Wilder），与「连续涨停打满」的口径一致；
    #    stk_factor_pro 作为兜底
    closes_all = [b["close"] for b in bars]
    rsi6 = _calc_rsi_series(closes_all, 6)
    rsi12 = _calc_rsi_series(closes_all, 12)
    rsi24 = _calc_rsi_series(closes_all, 24)
    if rsi6 is None:
        rsi6 = _safe_float(t.get("rsi_bfq_6", 0))
        rsi12 = _safe_float(t.get("rsi_bfq_12", 0))
        rsi24 = _safe_float(t.get("rsi_bfq_24", 0))
    extreme = rsi6 >= 90
    overbought = rsi6 >= 70
    rsi_verdict = {
        "rsi6": rsi6, "rsi12": rsi12, "rsi24": rsi24,
        "extreme": extreme, "overbought": overbought,
        "desc": (f"RSI6={rsi6:.0f}! RSI12={rsi12:.2f} RSI24={rsi24:.2f} —— 极端超买，指标打满"
                 if extreme else
                 f"RSI6={rsi6:.2f} RSI12={rsi12:.2f} RSI24={rsi24:.2f} —— 超买区"
                 if overbought else
                 f"RSI6={rsi6:.2f} RSI12={rsi12:.2f} RSI24={rsi24:.2f} —— 中性"),
        "ok": not extreme,
    }

    # ④ 缺口
    gap_verdict = {
        "unfilled_count": gaps.get("unfilled_count", 0),
        "filled_recent_count": gaps.get("filled_recent_count", 0),
        "unfilled": gaps.get("unfilled", []),
        "filled_recent": gaps.get("filled_recent", []),
        "desc": (f"{gaps['unfilled_count']}个未补缺口" if gaps.get("unfilled_count")
                 else "无未补缺口"),
        "ok": gaps.get("unfilled_count", 0) <= 1,
    }

    # ⑤ 趋势
    ts = trend_state(bars, tech, ma)
    trend_verdict = {
        "state": ts,
        "desc": {"strong_bullish": "强势上涨", "bullish": "上涨",
                 "sideways": "横盘震荡", "bearish": "下跌趋势",
                 "unknown": "未知"}.get(ts, ts),
        "ok": ts in ("strong_bullish", "bullish"),
    }

    # ⑥ 量价关系（额外维度，bot 的核心）
    vols = [b["vol"] for b in bars]
    avg5 = _avg_prev(vols, len(bars) - 1, 5)
    tianliang_stagnation = any("天量滞涨" in b.get("_flags", [])
                               for b in bars[-4:])
    shrink_limit_up = False
    if last["_is_limit_up"] and avg5 > 0:
        shrink_limit_up = last["vol"] < 0.5 * avg5
    tianliang_top = any("天量见顶" in b.get("_flags", []) for b in bars[-4:])
    has_limit_down = any(b.get("_is_limit_down") for b in bars[-3:])
    vol_price = {
        "tianliang_stagnation": tianliang_stagnation,
        "shrink_limit_up": shrink_limit_up,
        "tianliang_top": tianliang_top,
        "has_limit_down": has_limit_down,
        "desc": "",
        "ok": not (tianliang_stagnation or shrink_limit_up or tianliang_top or has_limit_down),
    }
    parts = []
    if tianliang_stagnation:
        parts.append("天量滞涨")
    if shrink_limit_up:
        parts.append("缩量涨停")
    if tianliang_top:
        parts.append("天量见顶")
    if has_limit_down:
        parts.append("跌停")
    vol_price["desc"] = " + ".join(parts) if parts else "量价健康"

    return {
        "ma": ma_verdict, "macd": macd_verdict, "rsi": rsi_verdict,
        "gap": gap_verdict, "trend": trend_verdict, "vol_price": vol_price,
        "ma_values": ma,
    }


def moneyflow_signal(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """资金分单信号（基于 tushare moneyflow 万元口径）。"""
    if not rows:
        return {"available": False}
    latest = rows[-1]
    total = (latest["buy_elg"] + latest["sell_elg"] + latest["buy_lg"] + latest["sell_lg"] +
             latest["buy_md"] + latest["sell_md"] + latest["buy_sm"] + latest["sell_sm"])
    if total <= 0:
        return {"available": False, "reason": "当日无资金流数据"}

    elg_net = latest["buy_elg"] - latest["sell_elg"]
    lg_net = latest["buy_lg"] - latest["sell_lg"]
    md_net = latest["buy_md"] - latest["sell_md"]
    sm_net = latest["buy_sm"] - latest["sell_sm"]
    main_net = elg_net + lg_net
    main_net_5d = sum((r["buy_elg"] - r["sell_elg"] + r["buy_lg"] - r["sell_lg"])
                      for r in rows[-5:])
    buy_elg_vol = latest.get("buy_elg_vol", 0)
    sell_elg_vol = latest.get("sell_elg_vol", 0)

    pct = lambda v: round(v / total * 100, 1)
    ddox = round((elg_net + lg_net) / total * 100, 2)
    vol_ratio = round(buy_elg_vol / sell_elg_vol, 3) if sell_elg_vol > 0 else 0.0

    signals = []
    if elg_net > 0 and lg_net < 0:
        signals.append("超大单拉板，中大单出逃")
    if sm_net > 0 and main_net < 0:
        signals.append("散户接盘")
    if main_net < 0:
        signals.append("主力净流出")
    if main_net_5d < 0:
        signals.append("近5日资金流出")
    if elg_net > 0 and lg_net >= 0:
        signals.append("超大单+大单同向流入")

    return {
        "available": True,
        "trade_date": latest["trade_date"],
        "bbd": round(elg_net, 1),                 # 近似：特大单净额（万元）
        "bbd_fmt": _fmt_wan(elg_net),
        "elg_net_pct": pct(elg_net),              # 特大单差(%)
        "lg_net_pct": pct(lg_net),                # 大单差(%)
        "md_net_pct": pct(md_net),                # 中单差(%)
        "sm_net_pct": pct(sm_net),                # 小单差(%)
        "main_net": round(main_net, 1),
        "main_net_fmt": _fmt_wan(main_net),
        "main_net_5d": round(main_net_5d, 1),
        "main_net_5d_fmt": _fmt_wan(main_net_5d),
        "ddx": ddox,                              # 近似：主力净占比
        "vol_ratio_elg": vol_ratio,               # 近似：超大单买卖量比（单数比）
        "signals": signals,
        "signal_text": "；".join(signals) if signals else "资金面中性",
    }


def fundamental_risk(info: Dict[str, Any], basic: Dict[str, Any],
                     holders: List[Dict[str, Any]], anns: List[Dict[str, Any]],
                     month_pct: float) -> Dict[str, Any]:
    """基本面排雷：PE / 市值核对 / 股东减持 / 异动公告 / 近1月涨幅。"""
    industry = info.get("industry", "")
    pe = basic.get("pe_ttm", 0) or 0.0
    basic = {**basic, "pe_ttm": pe}
    baseline = INDUSTRY_PE_BASELINE.get(industry, DEFAULT_INDUSTRY_PE)
    pe_ratio = round(pe / baseline, 2) if pe and baseline else None

    total_mv = basic.get("total_mv", 0)  # 万元
    circ_mv = basic.get("circ_mv", 0)
    total_share = basic.get("total_share", 0)  # 万股
    close = 0.0
    mv_check_note = ""
    # 市值核对：股本 × 价格 vs daily_basic.total_mv
    if total_share and close == 0:
        pass

    reduces = [h for h in holders if h.get("in_de") == "DE"]
    reduce_items = []
    for h in reduces[:5]:
        reduce_items.append({
            "holder": h.get("holder_name", ""),
            "date": h.get("ann_date", ""),
            "vol": h.get("change_vol", 0),
            "ratio": h.get("change_ratio", 0),
            "after_ratio": h.get("after_ratio", 0),
        })

    ann_hot = [a for a in anns if any(k in a["title"] for k in
               ("异常波动", "风险提示", "交易异动", "异动"))]

    return {
        "industry": industry,
        "pe_ttm": pe,
        "industry_pe_baseline": baseline,
        "pe_ratio_vs_industry": pe_ratio,
        "total_mv_yi": round(total_mv / 10000, 2) if total_mv else None,
        "circ_mv_yi": round(circ_mv / 10000, 2) if circ_mv else None,
        "total_share_yi": round(total_share / 10000, 3) if total_share else None,
        "mv_check_note": mv_check_note,
        "reduces": reduce_items,
        "reduce_count": len(reduces),
        "ann_hot": ann_hot[:6],
        "ann_hot_count": len(ann_hot),
        "month_pct": month_pct,
    }


# ─────────────────────────────────────────────────────────────
# 结论：阶段判定 / 风险定级 / 建议 / 观察信号
# ─────────────────────────────────────────────────────────────

def stage_verdict(five: Dict[str, Any], mf: Dict[str, Any], fund: Dict[str, Any],
                  wave: Dict[str, Any], bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """综合五维 + 资金 + 基本面 → 阶段 / 风险 / 建议 / 观察信号。"""
    red: List[str] = []
    green: List[str] = []

    rsi = five.get("rsi", {})
    ma = five.get("ma", {})
    macd = five.get("macd", {})
    gap = five.get("gap", {})
    trend = five.get("trend", {})
    vp = five.get("vol_price", {})
    lu_count = wave.get("main_wave_lu_count", wave.get("limit_up_count", 0))

    if ma.get("aligned"):
        green.append("均线多头排列")
    if macd.get("golden"):
        green.append("MACD金叉")
    if rsi.get("extreme"):
        red.append(f"RSI6={rsi.get('rsi6', 0):.0f} 指标打满")
    elif rsi.get("overbought"):
        green.append("RSI超买但未打满")
    if gap.get("unfilled_count", 0) >= 2:
        red.append(f"{gap['unfilled_count']}个未补缺口")
    elif gap.get("unfilled_count", 0) == 0:
        green.append("无未补缺口")
    if vp.get("tianliang_stagnation"):
        red.append("天量滞涨")
    if vp.get("shrink_limit_up") and lu_count >= 4:
        red.append("缩量涨停（高位没人接盘）")
    if vp.get("tianliang_top"):
        red.append("天量见顶")
    if vp.get("has_limit_down"):
        red.append("跌停出货")
    if trend.get("state") in ("sideways", "bearish"):
        red.append(f"趋势转{trend.get('desc', trend.get('state'))}")
    elif trend.get("state") == "strong_bullish":
        green.append("趋势强势上涨")

    # 资金面
    mf_avail = mf.get("available", False)
    if mf_avail:
        if mf.get("elg_net_pct", 0) > 0 and mf.get("lg_net_pct", 0) < 0:
            red.append("中大单出逃")
        if mf.get("main_net_5d", 0) < 0:
            red.append("近5日资金流出")
        if mf.get("sm_net_pct", 0) > 0 and mf.get("main_net", 0) < 0:
            red.append("散户接盘")
        if mf.get("main_net", 0) > 0 and mf.get("lg_net_pct", 0) >= 0:
            green.append("主力资金流入")

    # 基本面
    if fund.get("reduce_count", 0) > 0:
        red.append(f"大股东减持{fund['reduce_count']}笔")
    if fund.get("ann_hot_count", 0) >= 2:
        red.append(f"{fund['ann_hot_count']}次异动/风险提示公告")
    if (fund.get("pe_ratio_vs_industry") and fund["pe_ratio_vs_industry"] > 2.0
            and fund.get("pe_ttm", 0) > 60):
        red.append(f"PE {fund.get('pe_ttm', 0):.1f}倍 远超行业")
    elif fund.get("pe_ratio_vs_industry") and fund["pe_ratio_vs_industry"] <= 1.3:
        green.append("估值相对合理")
    if fund.get("month_pct", 0) > 100:
        red.append(f"近1月涨幅 {fund['month_pct']:.0f}%+")

    # 结构
    if lu_count >= 5:
        red.append(f"{lu_count}个涨停")
    if wave.get("washout"):
        green.append("有高开暴跌洗盘")
    if wave.get("accumulation_days", 0) >= 8:
        green.append(f"底部蓄力{wave['accumulation_days']}天")
    if wave.get("run_from_base", 0) > 80:
        red.append(f"底部涨幅{wave['run_from_base']:.0f}%")
    if wave.get("high_pullback", 0) < -15:
        red.append(f"高点回撤{wave['high_pullback']:.1f}%")

    # ── 阶段判定 ──
    tianliang_top = vp.get("tianliang_top")
    has_limit_down = vp.get("has_limit_down")
    tianliang_stag = vp.get("tianliang_stagnation")
    state = trend.get("state", "unknown")
    close = bars[-1]["close"] if bars else 0
    ma10 = five.get("ma", {}).get("ma10") or 0
    last = bars[-1] if bars else {}
    is_one_word = bool(last.get("_is_limit_up")) and         (last.get("high", 0) == last.get("low", 0) or
         (abs(last.get("open", 0) - last.get("close", 0)) < 0.005 * close and
          abs(last.get("open", 0) - last.get("high", 0)) < 0.005 * close))

    if tianliang_top and (has_limit_down or state in ("sideways", "bearish") or (ma10 and close < ma10)):
        stage = "已结束，进入回调"
        stage_icon = "🟥🟥🟥"
        risk = "极高"
    elif (lu_count >= 4 and rsi.get("rsi6", 0) >= 90) or tianliang_stag or             gap.get("unfilled_count", 0) >= 3 or             (fund.get("reduce_count", 0) > 0 and lu_count >= 2):
        stage = "末期加速赶顶"
        stage_icon = "🟥"
        risk = "极高" if len(red) >= 5 else "高"
    elif lu_count == 0:
        stage = "底部蓄力/启动前"
        stage_icon = "🟡"
        risk = "低" if len(red) <= 1 else "中"
    elif lu_count <= 2 and not vp.get("tianliang_stagnation") and not vp.get("tianliang_top"):
        stage = "加速初期"
        stage_icon = "🟢"
        risk = "低" if len(red) <= 2 else "中"
    else:
        stage = "主升中段"
        stage_icon = "🟡"
        risk = "中" if len(red) <= 3 else "高"

    # 风险等级细化（红灯计数兜底）
    red_count = len(red)
    if risk == "极高":
        pass
    elif red_count >= 5:
        risk = "极高"
    elif red_count >= 4:
        risk = "高"
    elif red_count >= 2:
        risk = "中" if risk not in ("高",) else risk
    else:
        risk = "低"

    # ── 建议 ──
    if stage.startswith("已结束"):
        advice = "不碰；反弹离场"
        advice_reason = "主升浪已走完，天量见顶+跌停确认出货，反弹是逃命波"
    elif stage.startswith("末期"):
        advice = "不碰/不追高"
        advice_reason = "指标打满+天量滞涨+缺口堆积，任何一个利空就能触发回调"
    elif stage == "加速初期":
        if is_one_word:
            advice = "一字板买不进，等开板观察"
            advice_reason = "当前一字封死买不进；若开板放量则转为观察，不追高"
        else:
            advice = "可小仓位试错（≤500元，-5%止损）"
            advice_reason = "主升浪初期，洗盘充分、无出货信号；严格控制试错仓位"
    elif stage == "主升中段":
        advice = "只做回踩低吸，不追板"
        advice_reason = "中段波动加大，追板容易接盘，回踩MA5/MA10低吸"
    else:
        advice = "观察突破信号"
        advice_reason = "仍在蓄力，等放量突破平台再介入"

    # ── 观察信号 ──
    watch = []
    if last.get("_is_limit_up"):
        watch.append("继续缩量一字板 → 还能涨但追不到")
        watch.append("开板放巨量 → 警惕出货")
    if vp.get("tianliang_stagnation") or vp.get("tianliang_top"):
        watch.append("再出现天量阴线 → 立即走")
    for g in gap.get("unfilled", [])[:2]:
        watch.append(f"回补缺口{g.get('date_fmt')}（{g.get('lo', 0):.2f}-{g.get('hi', 0):.2f}）→ 回调目标")
    if wave.get("launch"):
        watch.append(f"跌破{wave['launch']['close']:.2f}（启动涨停价）→ 趋势破坏")
    if stage.startswith("已结束") and ma10:
        watch.append(f"反抽MA10（{ma10:.2f}）不过 → 继续下跌")

    # ── 一句话 ──
    if stage.startswith("已结束"):
        one_liner = (f"{wave.get('base_date_fmt', '')}底部{wave.get('base_low', 0):.2f}涨到"
                     f"{(wave.get('second_wave') or {}).get('high', 0):.2f}，天量见顶+跌停出货，主升浪走完")
    elif stage.startswith("末期"):
        one_liner = (f"均线形态确实是主升浪，但RSI打满+天量滞涨+"
                     f"{gap.get('unfilled_count', 0)}个缺口{'+大股东减持' if fund.get('reduce_count') else ''}，"
                     f"多个信号同时指向赶顶阶段")
    elif stage == "加速初期":
        one_liner = (f"洗盘充分、估值合理、无出货信号，{lu_count}个涨停的主升浪初期，"
                     f"但RSI已{rsi.get('rsi6', 0):.0f}需防高位")
    else:
        one_liner = f"主升浪进行中（{stage}），红绿灯信号需持续跟踪"

    return {
        "stage": stage,
        "stage_icon": stage_icon,
        "risk_level": risk,
        "red_flags": red,
        "green_flags": green,
        "red_count": red_count,
        "advice": advice,
        "advice_reason": advice_reason,
        "watch_signals": watch,
        "one_liner": one_liner,
        "is_one_word": is_one_word,
    }


# ─────────────────────────────────────────────────────────────
# 选股评分（0-100）
# ─────────────────────────────────────────────────────────────

def score_stock(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """把单票分析压缩成 0-100 评分，供选股排序。"""
    five = analysis.get("five", {})
    v = analysis.get("verdict", {})
    wave = analysis.get("structure", {})
    mf = analysis.get("moneyflow", {})
    fund = analysis.get("fundamental", {})
    score = 0.0
    factors: List[Dict[str, Any]] = []
    add = lambda name, pts, reason: factors.append(
        {"name": name, "points": round(pts, 1), "reason": reason})

    # 趋势 (20)
    ts = five.get("trend", {}).get("state", "unknown")
    pts = {"strong_bullish": 20, "bullish": 14, "sideways": 6, "bearish": 0}.get(ts, 5)
    add("趋势", pts, f"trend={ts}")
    score += pts

    # 均线 (10)
    pts = 10 if five.get("ma", {}).get("aligned") else 5 if not five.get("ma", {}).get("converging") else 0
    add("均线", pts, "多头排列" if pts == 10 else "收敛/走坏")
    score += pts

    # MACD (10)
    macd = five.get("macd", {})
    pts = 10 if macd.get("golden") and macd.get("bar", 0) > 0 and not macd.get("dif_falling") else           6 if macd.get("golden") else 0
    add("MACD", pts, f"DIF>{'DEA' if macd.get('golden') else 'DEA?'}")
    score += pts

    # RSI (10)：55-85 甜区
    rsi6 = five.get("rsi", {}).get("rsi6", 0)
    if 55 <= rsi6 <= 85:
        pts = 10
    elif 85 < rsi6 <= 95:
        pts = 6
    elif rsi6 > 95:
        pts = 2
    elif 40 <= rsi6 < 55:
        pts = 7
    else:
        pts = 4
    add("RSI", pts, f"RSI6={rsi6:.0f}")
    score += pts

    # 缺口 (10)
    gc = five.get("gap", {}).get("unfilled_count", 0)
    pts = 10 if gc == 0 else 6 if gc == 1 else 3 if gc == 2 else 1
    if five.get("gap", {}).get("filled_recent_count", 0) > 0:
        pts -= 2
    pts = max(0, pts)
    add("缺口", pts, f"未补{gc}个")
    score += pts

    # 量价 (15)
    vp = five.get("vol_price", {})
    if vp.get("tianliang_top") or vp.get("has_limit_down"):
        pts = 0
    elif vp.get("tianliang_stagnation"):
        pts = 2
    elif vp.get("shrink_limit_up"):
        pts = 6
    else:
        pts = 15
    add("量价", pts, vp.get("desc", ""))
    score += pts

    # 资金 (10)
    if mf.get("available"):
        if mf.get("main_net", 0) > 0 and mf.get("lg_net_pct", 0) >= 0:
            pts = 10
        elif mf.get("main_net", 0) > 0:
            pts = 7
        elif mf.get("main_net_5d", 0) > 0:
            pts = 4
        else:
            pts = 2
    else:
        pts = 5
    add("资金", pts, mf.get("main_net_fmt", "无数据") if mf.get("available") else "资金数据不可用")
    score += pts

    # 洗盘/蓄力 (5)
    wash = bool(wave.get("washout"))
    acc = wave.get("accumulation_days", 0)
    pts = 5 if wash and acc >= 8 else 3 if wash else 1
    add("洗盘", pts, f"洗盘={'有' if wash else '无'}, 蓄力{acc}天")
    score += pts

    # 基本面 (5)
    pts = 5.0
    if fund.get("reduce_count", 0) > 0:
        pts -= 4
    if fund.get("ann_hot_count", 0) >= 2:
        pts -= 1.5
    if fund.get("pe_ratio_vs_industry") and fund["pe_ratio_vs_industry"] > 1.8:
        pts -= 2
    pts = max(0, pts)
    add("基本面", pts, f"PE={fund.get('pe_ttm', 0) or '-'}, 减持{fund.get('reduce_count', 0)}笔")
    score += pts

    # 连板位置 (5)
    lu = wave.get("limit_up_count", 0)
    pts = 5 if lu <= 2 else 3 if lu <= 4 else 1
    add("连板", pts, f"{lu}个涨停")
    score += pts

    # 回撤惩罚（已见顶）
    pull = wave.get("high_pullback", 0)
    if pull <= -15:
        score -= 8
        add("回撤", -8, f"高点回撤{pull:.1f}%")

    score = round(max(0.0, min(100.0, score)), 1)
    grade = "可关注" if score >= 75 else "观察" if score >= 60 else "回避"
    return {"score": score, "grade": grade, "factors": factors}


# ─────────────────────────────────────────────────────────────
# Markdown 报告（复刻「短线炒股分析员」风格）
# ─────────────────────────────────────────────────────────────

def build_markdown(a: Dict[str, Any], peers: Optional[List[Dict[str, Any]]] = None) -> str:
    name = a.get("name", "")
    code = a.get("symbol", "")
    five = a.get("five", {})
    v = a.get("verdict", {})
    wave = a.get("structure", {})
    mf = a.get("moneyflow", {})
    fund = a.get("fundamental", {})
    score = a.get("score", {})
    as_of = a.get("as_of", "")
    bars = a.get("bars", [])

    L: List[str] = []
    L.append(f"# {name}({code}) 主升浪技术画像")
    L.append(f"**数据日期**: {as_of[:4]}-{as_of[4:6]}-{as_of[6:8]}  |  最新价: {a.get('price', 0):.2f}")
    L.append("")

    # ── K线走势还原 ──
    L.append("## 📊 K线走势还原（近{}个交易日）".format(len(bars)))
    L.append("| 日期 | 收盘 | 涨跌 | 成交量(手) | 形态 |")
    L.append("|------|------|------|-----------|------|")
    for b in bars:
        L.append(f"| {b['trade_date'][4:6]}/{b['trade_date'][6:8]} | {b['close']:.2f} | "
                 f"{b['pct_chg']:+.1f}% | {_fmt_vol(b['vol'])} | {bar_label(b)} |")
    L.append("")

    # ── 走势结构 ──
    L.append("## 走势结构")
    if wave:
        plat = wave.get("platform") or {}
        plat_lo = plat.get("lo") or 0.0
        plat_hi = plat.get("hi") or 0.0
        L.append(f"- 底部区间: {wave.get('base_date_fmt')}低点 **{wave.get('base_low', 0):.2f}**"
                 f" → 平台 {plat_lo:.2f}-{plat_hi:.2f}"
                 f"（{plat.get('days', 0)}天）")
        if wave.get("washout"):
            w = wave["washout"]
            L.append(f"- 洗盘: {w.get('date_fmt')} 高开{w.get('high', 0):.2f}砸到{w.get('low', 0):.2f}，"
                     f"收{w.get('close', 0):.2f}（{w.get('pct', 0):+.1f}%），成交量{_fmt_vol(w.get('vol', 0))}")
        if wave.get("launch"):
            la = wave["launch"]
            L.append(f"- 启动: {la['date'][4:6]}/{la['date'][6:8]} 涨停 {la['close']:.2f}"
                     f"（{la.get('pct', 0):+.1f}%）")
        mw_dates = wave.get("main_wave_dates", [])
        mw_count = wave.get("main_wave_lu_count", wave.get("limit_up_count", 0))
        L.append(f"- 主升浪涨停: {mw_count}个（{', '.join(d[4:6] + '/' + d[6:8] for d in mw_dates[-6:])}）")
        L.append(f"- 底部涨幅: {wave.get('base_date_fmt')}的{wave.get('base_low', 0):.2f} → "
                 f"{wave.get('last_close', 0):.2f} = **+{wave.get('run_from_base', 0):.1f}%**；"
                 f"启动后 {wave.get('run_from_launch', 0):+.1f}%")
        if wave.get("high_pullback", 0) < -1:
            L.append(f"- 高点回撤: {wave.get('high_pullback', 0):+.1f}%")
        if wave.get("month_pct"):
            L.append(f"- 近1月涨幅: {wave.get('month_pct', 0):+.1f}%")
    L.append("")

    # ── 五维判定 ──
    L.append("## 主升浪五维判定")
    ma = five.get("ma", {})
    L.append("① 均线排列: " + ("完美多头 ✅" if ma.get("aligned") else "多头（收敛中）⚠️" if not ma.get("converging") else "走坏 🔴"))
    L.append(f"   {ma.get('desc', '')}")
    macd = five.get("macd", {})
    L.append("② MACD: " + ("强金叉 ✅" if macd.get("golden") and macd.get("bar", 0) > 0 else "金叉 ⚠️" if macd.get("golden") else "死叉 🔴"))
    L.append(f"   {macd.get('desc', '')}")
    rsi = five.get("rsi", {})
    L.append("③ RSI: " + ("极端超买 ⚠️⚠️" if rsi.get("extreme") else "超买 ⚠️" if rsi.get("overbought") else "中性 ✅"))
    L.append(f"   {rsi.get('desc', '')}")
    gap = five.get("gap", {})
    L.append(f"④ 缺口: {'⚠️ ' if gap.get('unfilled_count', 0) > 1 else '✅ '}{gap.get('desc', '')}"
             + (f"，近{len(gap.get('filled_recent', []))}个已回补" if gap.get("filled_recent") else ""))
    trend = five.get("trend", {})
    L.append(f"⑤ 趋势判定: **{trend.get('desc', '')}**"
             f"（{'系统仍识别为强势上涨' if trend.get('state') == 'strong_bullish' else '系统识别为横盘' if trend.get('state') == 'sideways' else ''}）")
    vp = five.get("vol_price", {})
    shrink_in_red = "缩量涨停" in " ".join(v.get("red_flags", []))
    if vp.get("shrink_limit_up") and not shrink_in_red:
        vp_line = "缩量涨停（初期惜售，一致看多）"
        vp_icon = "✅"
    elif vp.get("shrink_limit_up") and shrink_in_red:
        vp_line = "缩量涨停（高位没人接盘）"
        vp_icon = "🔴"
    else:
        vp_line = vp.get("desc", "")
        vp_icon = "✅" if vp.get("ok") else "🔴"
    L.append(f"⑥ 量价关系: {vp_icon} {vp_line}")
    L.append("")

    # 横向对比
    if peers:
        L.append("### 横向对比")
        L.append("| 维度 | " + " | ".join(p["name"] for p in peers) + " |")
        L.append("|------|" + "------|" * len(peers))
        rows = [
            ("行业", lambda p: p["industry"] or "-"),
            ("主升浪", lambda p: p["verdict"]["stage"]),
            ("从底部涨幅", lambda p: f"+{p['structure'].get('run_from_base', 0):.1f}%"),
            ("高点回撤", lambda p: f"{p['structure'].get('high_pullback', 0):+.1f}%"),
            ("涨停数", lambda p: f"{p['structure'].get('limit_up_count', 0)}"),
            ("RSI6", lambda p: f"{p['five']['rsi'].get('rsi6', 0):.0f}"),
            ("未补缺口", lambda p: f"{p['five']['gap'].get('unfilled_count', 0)}"),
            ("天量阴线", lambda p: "有" if p["five"]["vol_price"].get("tianliang_stagnation") or p["five"]["vol_price"].get("tianliang_top") else "无"),
            ("趋势", lambda p: p["five"]["trend"].get("desc", "-")),
            ("风险", lambda p: p["verdict"]["risk_level"]),
            ("评分", lambda p: f"{p.get('score', {}).get('score', 0)}"),
        ]
        for label, fn in rows:
            L.append("| " + label + " | " + " | ".join(str(fn(p)) for p in peers) + " |")
        L.append("")

    # ── 今日盘面数据 ──
    basic = a.get("basic", {})
    last = a.get("last_bar", {})
    L.append("## 今日盘面数据")
    L.append("| 指标 | 数值 | 含义 |")
    L.append("|------|------|------|")
    if last:
        L.append(f"| 收盘 | {last['close']:.2f} | {last['pct_chg']:+.2f}% |")
        L.append(f"| 成交量 | {_fmt_vol(last['vol'])} | "
                 f"{'前日' + _fmt_vol(bars[-2]['vol']) if len(bars) >= 2 else ''} |")
        L.append(f"| 成交额 | {_fmt_amount_yi(last['amount'])} | - |")
    if basic.get("turnover_rate"):
        L.append(f"| 换手率 | {basic['turnover_rate']:.2f}% | {'极度缩量' if basic['turnover_rate'] < 1 else '正常' if basic['turnover_rate'] < 8 else '充分换手'} |")
    if basic.get("volume_ratio"):
        L.append(f"| 量比 | {basic['volume_ratio']:.2f} | {'极度缩量' if basic['volume_ratio'] < 0.5 else '放量' if basic['volume_ratio'] > 2 else '正常'} |")
    if basic.get("pe_ttm"):
        L.append(f"| PE(TTM) | {basic['pe_ttm']:.2f} | {'合理' if fund.get('pe_ratio_vs_industry') and fund['pe_ratio_vs_industry'] <= 1.5 else '偏高 ⚠️'} |")
    if mf.get("available"):
        L.append(f"| 主力净流入 | {mf.get('main_net_fmt', '-')} | {mf.get('signal_text', '')} |")
    board = a.get("board", {})
    if board:
        if board.get("buy1_vol"):
            L.append(f"| 封单(买一) | {_fmt_vol(board['buy1_vol'])} | 排队等成交 |")
        if board.get("sell_total") is not None:
            L.append(f"| 卖盘 | {_fmt_vol(board['sell_total'])} | {'零卖单' if board['sell_total'] == 0 else '有卖压'} |")
    L.append("")

    # ── 资金面 ──
    if mf.get("available"):
        L.append("## 资金面信号")
        L.append("| 指标 | 数值 | 含义 |")
        L.append("|------|------|------|")
        L.append(f"| BBD(特大单净) | {mf.get('bbd_fmt', '-')} | 超大单净流入 |")
        L.append(f"| 特大单差 | {mf.get('elg_net_pct', 0):+.1f}% | {'超大单流入' if mf['elg_net_pct'] > 0 else '超大单流出'} |")
        L.append(f"| 大单差 | {mf.get('lg_net_pct', 0):+.1f}% | {'大单流入' if mf['lg_net_pct'] > 0 else '⚠️ 中大单净流出'} |")
        L.append(f"| 小单差 | {mf.get('sm_net_pct', 0):+.1f}% | {'散户接盘 ⚠️' if mf['sm_net_pct'] > 0 else '散户卖出'} |")
        vr = mf.get("vol_ratio_elg", 0)
        L.append(f"| 超大单量比 | {'—' if vr == 0 else f'{vr:.2f}'} | 大单少、小单多 → 游资主导 |")
        L.append(f"| DDX(主力占比) | {mf.get('ddx', 0):.2f} | {'超大单主导' if mf['ddx'] > 2 else '主力参与度一般'} |")
        L.append(f"| 近5日主力 | {mf.get('main_net_5d_fmt', '-')} | {'⚠️ 涨停前资金在流出' if mf['main_net_5d'] < 0 else '资金持续流入'} |")
        L.append("")
        L.append(f"**解读**: {mf.get('signal_text', '')}")
        L.append("")

    # ── 基本面风险 ──
    L.append("## 基本面风险")
    pe = fund.get("pe_ttm", 0) or 0.0
    baseline = fund.get("industry_pe_baseline", 0)
    L.append(f"- 行业: {fund.get('industry', '-')}；PE(TTM) {pe:.2f}倍"
             f"（{'行业平均约' + str(baseline) + '倍，' if baseline else ''}"
             f"{'接近2倍溢价 ⚠️' if fund.get('pe_ratio_vs_industry') and fund['pe_ratio_vs_industry'] > 1.8 else '估值相对合理'})")
    if fund.get("total_mv_yi"):
        L.append(f"- 总市值: {fund['total_mv_yi']:.1f}亿（流通 {fund.get('circ_mv_yi', 0):.1f}亿）")
    for r in fund.get("reduces", [])[:3]:
        L.append(f"- 🔴 减持: {r.get('holder', '')} {r.get('date', '')} 减持"
                 f" {r.get('vol', 0) / 10000:.0f}万股"
                 f"（占总股本 {r.get('ratio', 0):.2f}% 以上）")
    if fund.get("ann_hot_count"):
        L.append(f"- ⚠️ 异动/风险提示公告 {fund['ann_hot_count']}次: "
                 + "、".join(a["date_fmt"] for a in fund.get("ann_hot", [])[:5]))
    if fund.get("month_pct"):
        L.append(f"- 近1月涨幅 {fund['month_pct']:+.1f}%")
    L.append("")

    # ── 结论 ──
    L.append("## 结论")
    L.append(f"**主升浪阶段: {v.get('stage', '')} {v.get('stage_icon', '')}**")
    L.append("")
    L.append("| 维度 | 判定 |")
    L.append("|------|------|")
    L.append(f"| 均线排列 | {'✅ ' if ma.get('aligned') else '🔴 '}{ma.get('desc', '')} |")
    L.append(f"| MACD | {'✅ ' if macd.get('ok') else '🔴 '}{macd.get('desc', '')} |")
    L.append(f"| RSI | {'🔴 ' if rsi.get('extreme') else '⚠️ ' if rsi.get('overbought') else '✅ '}{rsi.get('desc', '')} |")
    L.append(f"| 缺口 | {'🔴 ' if gap.get('unfilled_count', 0) > 1 else '✅ '}{gap.get('desc', '')} |")
    L.append(f"| 量价关系 | {'🔴 ' if not vp.get('ok') else '✅ '}{vp.get('desc', '')} |")
    L.append(f"| 资金面 | {'🔴 ' if any(k in v.get('red_flags', []) for k in ('中大单出逃', '散户接盘', '近5日资金流出')) else '✅ '}{mf.get('signal_text', '资金数据不可用')} |")
    L.append(f"| 大股东 | {'🔴 高位减持' if fund.get('reduce_count') else '✅ 无减持'} |")
    L.append("")
    L.append(f"一句话: **{v.get('one_liner', '')}**")
    L.append(f"风险等级: **{v.get('risk_level', '')}** " + "🔴" * max(1, v.get("red_count", 1) // 2))
    L.append("")
    L.append(f"对你的建议: **{v.get('advice', '')}**")
    L.append(f"理由: {v.get('advice_reason', '')}")
    L.append("")
    L.append("红灯清单:")
    for f in v.get("red_flags", []):
        L.append(f"- 🔴 {f}")
    if not v.get("red_flags"):
        L.append("- 暂无（绿灯为主）")
    L.append("")
    L.append("观察信号:")
    for i, s in enumerate(v.get("watch_signals", [])[:4], 1):
        L.append(f"{i}. {s}")
    L.append("")
    L.append(f"评分: **{score.get('score', 0)}/100**（{score.get('grade', '')}）")
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────
# 编排：单票全量分析
# ─────────────────────────────────────────────────────────────

def analyze_stock(symbol: str, fetcher: Optional[DataFetcher] = None,
                  as_of: Optional[str] = None, days: int = LOOKBACK_DAYS,
                  verbose: bool = False) -> Dict[str, Any]:
    """
    对单只股票生成完整「主升浪评价」分析。

    Args:
        symbol: 600613 / SH600613 / 600613.SH 均可
        fetcher: 复用 DataFetcher（多票分析时避免重复初始化）
        as_of: 截止交易日 YYYYMMDD（默认最新）
        days: 拉取K线天数
    Returns:
        dict：包含 bars / structure / five / gaps / moneyflow / fundamental /
              verdict / score / markdown
    """
    f = fetcher or DataFetcher(verbose=verbose)
    ts_code = _to_ts_code(symbol)
    short = _short_symbol(ts_code)
    as_of = as_of or datetime.now().strftime("%Y%m%d")

    bars_raw = f.klines(ts_code, days=days, end_date=as_of)
    if not bars_raw:
        return {"symbol": short, "ts_code": ts_code, "error": "K线数据为空"}
    bars = annotate_bars(bars_raw, ts_code)
    # 只保留 <= as_of 的K线（tushare daily 已按 end 过滤，但再保险）
    bars = [b for b in bars if b["trade_date"] <= as_of]
    if not bars:
        return {"symbol": short, "ts_code": ts_code, "error": "无 as_of 之前的数据"}

    # 节流：tushare 有每分钟频率限制，批量扫描时避免突发限流
    info = f.stock_info(ts_code)
    time.sleep(0.25)
    tech = f.technical(ts_code, end_date=as_of)
    time.sleep(0.25)
    mf_rows = f.moneyflow(ts_code, days=20, end_date=as_of)
    time.sleep(0.25)
    basic = f.daily_basic(ts_code, end_date=as_of)
    time.sleep(0.25)
    limit_info = f.limit_info(ts_code, trade_date=bars[-1]["trade_date"])
    time.sleep(0.25)
    holders = f.holdertrades(ts_code, days=60, end_date=as_of)
    anns = [a for a in f.announcements(short, days=20) if a.get("date", "") <= as_of]

    gaps = detect_gaps(bars)
    wave = wave_structure(bars, ts_code)
    five = five_dimensions(bars, tech, gaps)
    mf = moneyflow_signal(mf_rows)
    # 把最新资金流日期对齐 as_of（如果 as_of 非最新交易日，取最后一条）
    if mf.get("available"):
        mf = {**mf, "as_of_aligned": mf.get("trade_date")}
    fund = fundamental_risk(info, basic, holders, anns, wave.get("month_pct", 0))

    # 市值核对：若 total_mv 缺失，用股本×价格估算
    if not basic.get("total_mv") and basic.get("total_share"):
        est_mv = basic["total_share"] * bars[-1]["close"]  # 万元
        basic = {**basic, "total_mv": est_mv}
        fund["total_mv_yi"] = round(est_mv / 10000, 2)

    verdict = stage_verdict(five, mf, fund, wave, bars)
    board = {}
    if bars[-1].get("_is_limit_up") and f.use_akshare:
        board = f.board_data(ts_code)
    analysis = {
        "board": board,
        "symbol": short,
        "ts_code": ts_code,
        "name": info.get("name", short),
        "industry": info.get("industry", ""),
        "as_of": as_of,
        "price": bars[-1]["close"],
        "last_bar": bars[-1],
        "bars": bars[-8:],
        "bars_all": bars,
        "structure": wave,
        "gaps": gaps,
        "five": five,
        "moneyflow": mf,
        "fundamental": fund,
        "basic": basic,
        "limit_info": limit_info,
        "verdict": verdict,
        "data_quality": {
            "bars": len(bars),
            "tech": bool(tech.get("latest")),
            "moneyflow": mf.get("available", False),
            "basic": bool(basic),
            "limit_info": bool(limit_info),
            "holders": len(holders),
            "anns": len(anns),
        },
    }
    analysis["score"] = score_stock(analysis)
    analysis["markdown"] = build_markdown(analysis)
    return analysis


def compare_stocks(analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """多票横向对比表 + 排序。"""
    valid = [a for a in analyses if not a.get("error")]
    valid.sort(key=lambda a: a.get("score", {}).get("score", 0), reverse=True)
    rows = []
    for a in valid:
        rows.append({
            "symbol": a["symbol"], "name": a["name"], "industry": a.get("industry", ""),
            "stage": a["verdict"]["stage"], "risk": a["verdict"]["risk_level"],
            "score": a.get("score", {}).get("score", 0), "grade": a.get("score", {}).get("grade", ""),
            "run_from_base": a["structure"].get("run_from_base", 0),
            "high_pullback": a["structure"].get("high_pullback", 0),
            "limit_up_count": a["structure"].get("limit_up_count", 0),
            "rsi6": a["five"]["rsi"].get("rsi6", 0),
            "unfilled_gaps": a["five"]["gap"].get("unfilled_count", 0),
            "has_tianliang_yin": a["five"]["vol_price"].get("tianliang_stagnation") or
                                 a["five"]["vol_price"].get("tianliang_top"),
            "trend": a["five"]["trend"].get("desc", "-"),
            "advice": a["verdict"]["advice"],
        })
    return {"count": len(valid), "rows": rows}


def score_candidates(symbols: List[str], fetcher: Optional[DataFetcher] = None,
                     as_of: Optional[str] = None, max_workers: int = 4,
                     verbose: bool = False) -> List[Dict[str, Any]]:
    """批量评分选股：多线程评估候选票并排序。"""
    import concurrent.futures as cf

    def one(sym: str) -> Dict[str, Any]:
        f = fetcher or DataFetcher(verbose=False)
        try:
            return analyze_stock(sym, fetcher=f, as_of=as_of, verbose=False)
        except Exception as e:
            return {"symbol": sym, "error": f"{e!r}"}

    results: List[Dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(one, s) for s in symbols]
        for fut in cf.as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({"symbol": "?", "error": f"{e!r}"})
    results.sort(key=lambda a: a.get("score", {}).get("score", 0) if not a.get("error") else -1,
                 reverse=True)
    return results


# ─────────────────────────────────────────────────────────────
# 全市场批量扫描（按交易日全量拉取 + 本地向量化评分）
# ─────────────────────────────────────────────────────────────

def _ema_series(values: List[float], period: int) -> List[float]:
    alpha = 2.0 / (period + 1)
    ema = None
    out = []
    for v in values:
        ema = v if ema is None else (v - ema) * alpha + ema
        out.append(ema)
    return out


def _local_macd(closes: List[float]) -> Tuple[float, float, float]:
    """本地 EMA MACD(12,26,9)：返回 (dif, dea, bar)。"""
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    difs = [a - b for a, b in zip(ema12, ema26)]
    deas = _ema_series(difs, 9)
    dif, dea = difs[-1], deas[-1]
    return round(dif, 4), round(dea, 4), round((dif - dea) * 2, 4)


def market_trade_dates(fetcher: "DataFetcher", end_date: Optional[str] = None,
                       days: int = 90) -> List[str]:
    """最近 N 个交易日（升序，YYYYMMDD）。"""
    pro = fetcher.pro
    end = end_date or datetime.now().strftime("%Y%m%d")
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=days * 2)).strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    if cal is None or cal.empty:
        return []
    dates = sorted(str(d) for d in cal["cal_date"].tolist())
    return dates[-days:]


def fetch_market_daily(fetcher: "DataFetcher", dates: List[str],
                       verbose: bool = False) -> Dict[str, List[tuple]]:
    """
    按交易日一次拉全市场日线，组织为 ts_code -> [(date, open, high, low, close,
    pct_chg, vol, amount, pre_close), ...]（升序）。紧凑元组省内存。
    """
    pro = fetcher.pro
    per: Dict[str, List[tuple]] = {}
    for i, d in enumerate(dates):
        t0 = time.time()
        try:
            df = pro.daily(trade_date=d)
            if df is None or df.empty:
                continue
            for r in df.itertuples():
                lst = per.setdefault(r.ts_code, [])
                lst.append((str(r.trade_date),
                            _safe_float(r.open), _safe_float(r.high), _safe_float(r.low),
                            _safe_float(r.close), _safe_float(getattr(r, "pct_chg", 0) or 0),
                            _safe_float(getattr(r, "vol", 0) or 0),
                            _safe_float(getattr(r, "amount", 0) or 0),
                            _safe_float(getattr(r, "pre_close", 0) or 0)))
        except Exception as e:
            if verbose:
                print(f"[market_daily] {d} FAIL: {e!r}", file=sys.stderr)
        if verbose and (i + 1) % 20 == 0:
            print(f"[market_daily] {i + 1}/{len(dates)} 天（{time.time() - t0:.1f}s/天）",
                  file=sys.stderr)
        time.sleep(0.35)
    return per


def _compact_to_bars(compact: List[tuple]) -> List[Dict[str, Any]]:
    out = []
    prev_close = 0.0
    for (d, o, h, l, c, pct, v, amt, pc) in compact:
        out.append({
            "trade_date": d, "open": o, "high": h, "low": l, "close": c,
            "pre_close": pc or prev_close, "pct_chg": pct, "vol": v, "amount": amt,
        })
        prev_close = c
    return out


def market_scan(fetcher: Optional["DataFetcher"] = None, as_of: Optional[str] = None,
                days: int = 90, top_n: int = 20, deep_n: int = 8,
                min_bars: int = 30, verbose: bool = False) -> Dict[str, Any]:
    """
    全市场批量选股：
      1) 按交易日全量拉 daily（days 天）
      2) 单日全量 daily_basic + moneyflow + limit_list_d + stock_basic
      3) 本地五维评分全部股票（不拉 stk_factor / 减持 / 公告）
      4) 对 TopN 做深度分析（含股东减持/异动公告/盘口）
    返回 {timing, pool_size, ranked, deep, compare}。
    """
    f = fetcher or DataFetcher(verbose=verbose)
    pro = f.pro
    as_of = as_of or datetime.now().strftime("%Y%m%d")
    timing: Dict[str, float] = {}
    t_all = time.time()

    # 1) 交易日历
    t0 = time.time()
    dates = market_trade_dates(f, as_of, days)
    timing["trade_cal"] = round(time.time() - t0, 1)

    # 2) 全量日线
    t0 = time.time()
    per = fetch_market_daily(f, dates, verbose=verbose)
    timing["daily_fetch"] = round(time.time() - t0, 1)

    # 3) 单日全量快照
    t0 = time.time()
    basic_all: Dict[str, dict] = {}
    try:
        df = pro.daily_basic(trade_date=as_of, fields="ts_code,pe_ttm,total_mv,circ_mv,turnover_rate,volume_ratio,total_share,float_share")
        if df is not None and not df.empty:
            for r in df.itertuples():
                basic_all[r.ts_code] = {
                    "pe_ttm": _safe_float(getattr(r, "pe_ttm", 0) or 0),
                    "total_mv": _safe_float(getattr(r, "total_mv", 0) or 0),
                    "circ_mv": _safe_float(getattr(r, "circ_mv", 0) or 0),
                    "turnover_rate": _safe_float(getattr(r, "turnover_rate", 0) or 0),
                    "volume_ratio": _safe_float(getattr(r, "volume_ratio", 0) or 0),
                    "total_share": _safe_float(getattr(r, "total_share", 0) or 0),
                    "float_share": _safe_float(getattr(r, "float_share", 0) or 0),
                }
    except Exception as e:
        if verbose:
            print(f"[market_basic] FAIL: {e!r}", file=sys.stderr)

    mf_all: Dict[str, dict] = {}
    try:
        df = pro.moneyflow(trade_date=as_of)
        if df is not None and not df.empty:
            for r in df.itertuples():
                mf_all[r.ts_code] = {
                    "trade_date": as_of,
                    "buy_elg": _safe_float(getattr(r, "buy_elg_amount", 0) or 0),
                    "sell_elg": _safe_float(getattr(r, "sell_elg_amount", 0) or 0),
                    "buy_lg": _safe_float(getattr(r, "buy_lg_amount", 0) or 0),
                    "sell_lg": _safe_float(getattr(r, "sell_lg_amount", 0) or 0),
                    "buy_md": _safe_float(getattr(r, "buy_md_amount", 0) or 0),
                    "sell_md": _safe_float(getattr(r, "sell_md_amount", 0) or 0),
                    "buy_sm": _safe_float(getattr(r, "buy_sm_amount", 0) or 0),
                    "sell_sm": _safe_float(getattr(r, "sell_sm_amount", 0) or 0),
                    "buy_elg_vol": _safe_float(getattr(r, "buy_elg_vol", 0) or 0),
                    "sell_elg_vol": _safe_float(getattr(r, "sell_elg_vol", 0) or 0),
                    "net_mf_amount": _safe_float(getattr(r, "net_mf_amount", 0) or 0),
                }
    except Exception as e:
        if verbose:
            print(f"[market_moneyflow] FAIL: {e!r}", file=sys.stderr)

    lim_all: Dict[str, dict] = {}
    try:
        df = pro.limit_list_d(trade_date=as_of, fields="ts_code,limit_times,limit_type,fd_amount")
        if df is not None and not df.empty:
            for r in df.itertuples():
                lim_all[r.ts_code] = {
                    "limit_times": _safe_float(getattr(r, "limit_times", 0) or 0),
                    "limit_type": getattr(r, "limit_type", None),
                    "fd_amount": _safe_float(getattr(r, "fd_amount", 0) or 0),
                }
    except Exception as e:
        if verbose:
            print(f"[market_limit] FAIL: {e!r}", file=sys.stderr)

    info_all: Dict[str, dict] = {}
    try:
        df = pro.stock_basic(fields="ts_code,symbol,name,industry,market")
        if df is not None and not df.empty:
            for r in df.itertuples():
                info_all[r.ts_code] = {
                    "symbol": str(getattr(r, "symbol", "")),
                    "name": str(getattr(r, "name", "")),
                    "industry": str(getattr(r, "industry", "")),
                }
    except Exception as e:
        if verbose:
            print(f"[market_info] FAIL: {e!r}", file=sys.stderr)
    timing["snapshot_fetch"] = round(time.time() - t0, 1)

    # 4) 本地批量评分
    t0 = time.time()
    ranked: List[Dict[str, Any]] = []
    skipped_st = skipped_short = 0
    for code, compact in per.items():
        info = info_all.get(code, {})
        if "ST" in info.get("name", "") or "*" in info.get("name", ""):
            skipped_st += 1
            continue
        if len(compact) < min_bars:
            skipped_short += 1
            continue
        try:
            bars = annotate_bars(_compact_to_bars(compact), code)
            closes = [b["close"] for b in bars]
            rsi6 = _calc_rsi_series(closes, 6)
            rsi12 = _calc_rsi_series(closes, 12)
            rsi24 = _calc_rsi_series(closes, 24)
            dif, dea, bar = _local_macd(closes)
            tech = {"latest": {
                "macd_dif_bfq": dif, "macd_dea_bfq": dea, "macd_bfq": bar,
                "rsi_bfq_6": rsi6 or 50, "rsi_bfq_12": rsi12 or 50, "rsi_bfq_24": rsi24 or 50,
            }, "history": []}
            gaps = detect_gaps(bars)
            five = five_dimensions(bars, tech, gaps)
            wave = wave_structure(bars, code)
            mf = moneyflow_signal([mf_all[code]]) if code in mf_all else {"available": False}
            fund = fundamental_risk(info, basic_all.get(code, {}), [], [],
                                    wave.get("month_pct", 0))
            verdict = stage_verdict(five, mf, fund, wave, bars)
            score = score_stock({
                "five": five, "verdict": verdict, "structure": wave,
                "moneyflow": mf, "fundamental": fund,
            })
            ranked.append({
                "symbol": code.split(".")[0], "ts_code": code,
                "name": info.get("name", code), "industry": info.get("industry", ""),
                "score": score["score"], "grade": score["grade"],
                "stage": verdict["stage"], "risk": verdict["risk_level"],
                "advice": verdict["advice"], "red_count": verdict["red_count"],
                "rsi6": round(rsi6 or 0, 1),
                "run_from_base": wave.get("run_from_base", 0),
                "main_wave_lu": wave.get("main_wave_lu_count", 0),
                "unfilled_gaps": five["gap"].get("unfilled_count", 0),
                "has_tianliang_yin": five["vol_price"].get("tianliang_stagnation") or
                                     five["vol_price"].get("tianliang_top"),
                "trend": five["trend"].get("state", ""),
                "limit_times": lim_all.get(code, {}).get("limit_times", 0),
                "pe_ttm": basic_all.get(code, {}).get("pe_ttm", 0),
            })
        except Exception as e:
            if verbose:
                print(f"[light_score] {code} FAIL: {e!r}", file=sys.stderr)
    timing["local_score"] = round(time.time() - t0, 1)

    ranked.sort(key=lambda x: x["score"], reverse=True)
    top = ranked[:top_n]

    # 5) TopN 深度分析（预填缓存，复用全量数据，避免重复调 tushare）
    t0 = time.time()
    deep_codes = [r["ts_code"] for r in top[:deep_n]]
    for code in deep_codes:
        compact = per.get(code)
        if not compact:
            continue
        bars_dict = _compact_to_bars(compact)
        closes = [b["close"] for b in bars_dict]
        dif, dea, bar = _local_macd(closes)
        rsi6 = _calc_rsi_series(closes, 6)
        rsi12 = _calc_rsi_series(closes, 12)
        rsi24 = _calc_rsi_series(closes, 24)
        f._cache[f"kl:{code}:{days}:{as_of}"] = bars_dict
        f._cache[f"tech:{code}:{as_of}"] = {"latest": {
            "macd_dif_bfq": dif, "macd_dea_bfq": dea, "macd_bfq": bar,
            "rsi_bfq_6": rsi6 or 50, "rsi_bfq_12": rsi12 or 50, "rsi_bfq_24": rsi24 or 50,
        }, "history": []}
        if code in mf_all:
            f._cache[f"mf:{code}:20:{as_of}"] = [mf_all[code]]
        if code in basic_all:
            f._cache[f"basic:{code}:{as_of}"] = {**basic_all[code], "trade_date": as_of}
        if code in lim_all:
            f._cache[f"lim:{code}:{as_of}"] = lim_all[code]
        if code in info_all:
            f._cache[f"info:{code}"] = info_all[code]
    deep = []
    for code in deep_codes:
        try:
            deep.append(analyze_stock(code, fetcher=f, as_of=as_of, days=days))
        except Exception as e:
            deep.append({"symbol": code, "error": f"{e!r}"})
    timing["deep_analysis"] = round(time.time() - t0, 1)
    timing["total"] = round(time.time() - t_all, 1)

    return {
        "as_of": as_of, "days": len(dates), "pool_size": len(ranked),
        "skipped_st": skipped_st, "skipped_short": skipped_short,
        "timing": timing, "ranked": top, "deep": deep,
        "compare": compare_stocks([d for d in deep if not d.get("error")]),
    }

