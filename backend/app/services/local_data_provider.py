# -*- coding: utf-8 -*-
"""
本地历史数据提供器 — 懒加载 parquet 数据。
不做全量预加载，随查随取，按日期/标的缓存。
"""
import random
import logging
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class LocalDataProvider:
    """本地历史数据提供器（懒加载）"""

    DATA_ROOT = Path(__file__).parent.parent.parent.parent / "data" / "backtest" / "股票数据"
    MINUTE_JITTER_RANGE = 3  # +0~3分钟随机延迟

    def __init__(self):
        self._date_range: Optional[Tuple[date, date]] = None
        self._loaded_symbols: List[str] = []
        # 懒加载缓存
        self._daily_cache: Dict[str, pd.DataFrame] = {}     # date_str -> DataFrame (当日全量)
        self._moneyflow_cache: Dict[str, pd.DataFrame] = {}  # date_str -> DataFrame
        self._minute_cache: Dict[str, pd.DataFrame] = {}     # sym -> DataFrame (该标的全部分钟)
        # 股票名缓存: ts_code (000001.SZ) -> name
        self._name_cache: Dict[str, str] = {}
        self._name_df: Optional[pd.DataFrame] = None
        self._daily_path = self.DATA_ROOT / "行情数据" / "stock_daily.parquet"
        self._moneyflow_path = self.DATA_ROOT / "资金流向数据" / "moneyflow.parquet"
        self._minute_dir = self.DATA_ROOT / "行情数据" / "stock_1min"
        # 大盘指数分钟数据 (反未来函数核心数据源)
        # DATA_ROOT = F:/.../data/backtest/股票数据
        # DATA_ROOT.parent = F:/.../data/backtest
        # index_1min 在 F:/.../data/backtest/指数数据/index_1min/
        self._index_1min_dir = self.DATA_ROOT.parent / "指数数据" / "index_1min"
        self._index_1min_cache: Dict[str, pd.DataFrame] = {}  # ts_code -> DataFrame (该指数全部分钟)

    # ── 初始化（极轻量，仅加载股票列表） ──

    def load(self, start_date: date, end_date: date, symbols: List[str] = None,
             include_chinext: bool = True):
        """设置回测区间和股票池。不做全量预加载。

        Args:
            include_chinext: 是否包含创业板(代码以 300/301 开头, 深交所)
                              默认 True 保持向后兼容
        """
        self._date_range = (start_date, end_date)
        self._include_chinext = include_chinext
        if symbols is None:
            symbols = self._load_symbols_from_basic(include_chinext=include_chinext)
        self._loaded_symbols = symbols
        # 清空日线缓存(数据范围变了)
        self._daily_cache.clear()
        self._minute_cache.clear()
        self._index_1min_cache.clear()
        logger.info(f"回测数据就绪: {start_date} ~ {end_date}, {len(symbols)} 只标的 "
                    f"(懒加载模式, 创业板={'含' if include_chinext else '不含'})")

    def _load_symbols_from_basic(self, include_chinext: bool = True) -> List[str]:
        path = self.DATA_ROOT / "行情数据" / "stock_basic_data.parquet"
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        # 同步填充 name 缓存（一次读取，多次使用）
        if self._name_df is None:
            self._name_df = df.set_index("ts_code")[["name"]].to_dict()["name"]
        active = df[(df["list_status"] == "L") & (df["exchange"].isin(["SZSE", "SSE"]))]
        symbols = active["ts_code"].tolist()
        # 创业板过滤: ts_code 形如 300XXX.SZ / 301XXX.SZ
        if not include_chinext:
            before = len(symbols)
            symbols = [s for s in symbols if not (s.endswith(".SZ") and s[:3] in ("300", "301"))]
            logger.info(f"股票池: {len(symbols)} 只 (沪深, 排除北交所 + 创业板 {before - len(symbols)} 只)")
        else:
            logger.info(f"股票池: {len(symbols)} 只 (沪深, 排除北交所, 含创业板)")
        return symbols

    def get_stock_name(self, symbol: str) -> str:
        """按代码查股票名称（支持多种格式）
        Args:
            symbol: 000001.SZ / SH600519 / 600519 / 000001
        Returns:
            股票名称，未找到返回空字符串
        """
        if not symbol:
            return ""
        s = symbol.strip().upper()
        # 归一化为 ts_code 格式 (000001.SZ)
        if s.startswith("SH") and "." not in s and len(s) > 2:
            ts_code = s[2:] + ".SH"
        elif s.startswith("SZ") and "." not in s and len(s) > 2:
            ts_code = s[2:] + ".SZ"
        elif "." not in s and len(s) == 6:
            ts_code = s + (".SH" if s.startswith("6") else ".SZ")
        else:
            ts_code = s

        if ts_code in self._name_cache:
            return self._name_cache[ts_code]
        if self._name_df is None:
            # 首次调用 → 加载
            self._load_symbols_from_basic()
        name = (self._name_df or {}).get(ts_code, "")
        self._name_cache[ts_code] = name or ""
        return name or ""

    # ── 日线 ──

    def _load_daily_date(self, date_str: str) -> pd.DataFrame:
        """懒加载：读取指定日期的日线数据"""
        if date_str in self._daily_cache:
            return self._daily_cache[date_str]

        logger.info(f"  [数据] 正在读取 {date_str} 日线...")
        ts = pd.Timestamp(date_str)
        df = pd.read_parquet(self._daily_path, filters=[("trade_date", "=", ts)])
        if not df.empty and df.index.nlevels == 2:
            df.index = df.index.droplevel(0)
        self._daily_cache[date_str] = df
        logger.info(f"  [数据] {date_str} 日线读取完成: {len(df)} 条")
        return df

    def get_daily_quote(self, sym: str, trade_date: date) -> Optional[dict]:
        date_str = trade_date.isoformat()
        df = self._load_daily_date(date_str)
        # 归一化 sym (688025 / SH688025 / 688025.SH → 688025.SH)
        norm_sym = self._normalize_sym(sym)
        try:
            row = df.loc[norm_sym]
            return {
                "symbol": sym, "date": date_str,
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "pre_close": float(row["pre_close"]), "change_pct": float(row["pct_chg"]),
                "volume": int(row["vol"]), "amount": float(row["amount"]),
                "turnover_rate": float(row.get("turnover_rate", 0)),
            }
        except KeyError:
            return None

    def get_all_daily_quotes(self, trade_date: date) -> pd.DataFrame:
        """返回当日全量日线 DataFrame（高效，不做 dict 转换）"""
        date_str = trade_date.isoformat()
        return self._load_daily_date(date_str)

    @staticmethod
    def daily_row_to_dict(row) -> dict:
        """DataFrame 行转字典（按需调用）"""
        return {
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "pre_close": float(row["pre_close"]), "change_pct": float(row["pct_chg"]),
            "volume": int(row["vol"]), "amount": float(row["amount"]),
        }

    # ── 分钟行情（懒加载 per-stock） ──

    @staticmethod
    def _normalize_sym(sym: str) -> str:
        """sym 归一化: 接受多种格式, 输出 ts_code (000001.SZ / 688025.SH)
        接受: "688025" / "SH688025" / "688025.SH" / "sh688025"
        输出: "688025.SH" (默认 6 开头 = SH, 0/3 开头 = SZ)
        """
        s = sym.strip()
        # 已经是 ts_code 格式 (含 .)
        if "." in s and len(s.split(".")) == 2 and len(s.split(".")[0]) == 6:
            return s.upper()
        # SH688025 / SZ000001 → 688025.SH
        u = s.upper()
        if u.startswith("SH") and len(u) > 2 and u[2:].isdigit():
            return u[2:] + ".SH"
        if u.startswith("SZ") and len(u) > 2 and u[2:].isdigit():
            return u[2:] + ".SZ"
        # 纯 6 位数字
        if len(s) == 6 and s.isdigit():
            return s + (".SH" if s.startswith("6") else ".SZ")
        # 无法识别 → 原样返回 (后续 _load 会返回 None, 由调用方处理)
        return s

    def _load_minute_stock(self, sym: str) -> Optional[pd.DataFrame]:
        """懒加载：按需读取单只标的的分钟数据
        sym 归一化: 接受 "688025" / "SH688025" / "688025.SH" 等多种格式
        """
        norm_sym = self._normalize_sym(sym)
        if norm_sym in self._minute_cache:
            return self._minute_cache[norm_sym]
        file_path = self._minute_dir / f"{norm_sym}.parquet"
        if not file_path.exists():
            return None
        df = pd.read_parquet(file_path)
        if not df.empty:
            self._minute_cache[norm_sym] = df
            return df
        return None

    def get_minute_quote(self, sym: str, trade_date: date, target_hour: int,
                         target_minute: int) -> Optional[dict]:
        """获取模拟时刻的分钟行情（+0~3分钟随机延迟）"""
        df = self._load_minute_stock(sym)
        if df is None or df.empty:
            return None

        date_str = trade_date.isoformat()
        try:
            day_data = df.loc[date_str]
        except KeyError:
            return None
        if day_data.empty:
            return None

        # 计算时间窗口: [target_minute, target_minute + JITTER_RANGE] 分钟内
        # ⚠️ 修复: 当 target_minute + JITTER_RANGE 跨小时(>=60) 时,小时也要进位
        jitter = self.MINUTE_JITTER_RANGE
        end_minute_total = target_minute + jitter
        if end_minute_total >= 60:
            # 跨小时,上限取 59 分(本小时最后)
            time_max = f"{date_str} {target_hour:02d}:59:59"
        else:
            time_max = f"{date_str} {target_hour:02d}:{end_minute_total:02d}:59"
        time_min = f"{date_str} {target_hour:02d}:{target_minute:02d}:00"
        available = day_data.loc[time_min:time_max]
        if available.empty:
            # 边界场景: 该时刻数据缺失 → 返回 None(让调用方拒绝交易)
            return None

        idx = random.randint(0, len(available) - 1)
        row = available.iloc[idx]
        actual_time = str(available.index[idx])

        # 从日线取昨收（分钟数据本身不含 pre_close 列）
        day_q = self.get_daily_quote(sym, trade_date)
        pre_close = float(day_q["pre_close"]) if day_q else 0.0

        return {
            "symbol": sym, "time": actual_time,
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "pre_close": pre_close,
            "volume": int(row["vol"]), "amount": float(row["amount"]),
            "turnover_rate": float(row.get("turnover_rate", 0)) if "turnover_rate" in row.index else 0.0,
        }

    def get_minute_quotes_batch(self, trade_date: date, target_hour: int,
                                target_minute: int,
                                symbols: List[str] = None) -> Dict[str, dict]:
        """批量获取分钟行情。symbols 为空时只取 top 20 涨跌标的 + 有分钟文件的标的。"""
        if symbols is None:
            # 只取有分钟文件的可能性最高的（从 stock_1min 目录预扫描过的）
            symbols = list(self._minute_cache.keys())  # 已缓存的优先
            if not symbols:
                return {}
        result = {}
        for sym in symbols[:50]:  # 最多 50 只
            q = self.get_minute_quote(sym, trade_date, target_hour, target_minute)
            if q:
                result[sym] = q
        return result

    def get_minute_quotes_for_held(self, trade_date: date, target_hour: int,
                                    target_minute: int,
                                    held_symbols: List[str]) -> Dict[str, dict]:
        """仅获取持仓标的的分钟行情"""
        result = {}
        for sym in held_symbols:
            q = self.get_minute_quote(sym, trade_date, target_hour, target_minute)
            if q:
                result[sym] = q
        return result

    # ── 大盘指数分钟行情（反未来函数核心数据源） ──

    def _load_index_1min(self, ts_code: str) -> Optional[pd.DataFrame]:
        """懒加载单只大盘指数的全部分钟数据 (data/backtest/指数数据/index_1min/<code>.parquet)"""
        if ts_code in self._index_1min_cache:
            return self._index_1min_cache[ts_code]
        file_path = self._index_1min_dir / f"{ts_code}.parquet"
        if not file_path.exists():
            return None
        try:
            df = pd.read_parquet(file_path)
            if df is None or df.empty:
                return None
            self._index_1min_cache[ts_code] = df
            return df
        except Exception as e:
            logger.warning(f"[data] 加载 {ts_code} 指数分钟失败: {e}")
            return None

    def get_index_minute_window(self, ts_code: str, trade_date: date,
                                 target_hour: int, target_minute: int) -> Optional[dict]:
        """获取大盘指数在 9:30 ~ target_time 的窗口聚合数据（反未来函数）

        Returns:
            dict with current_price/open/high/low/close/vol/amount/pre_close/change_pct/trade_time
        """
        df = self._load_index_1min(ts_code)
        if df is None or df.empty:
            return None

        date_str = trade_date.isoformat()
        time_max_str = f"{date_str} {target_hour:02d}:{target_minute:02d}:59"

        # 1. 用 trade_time 列做时序切片 (兼容 MultiIndex 和单层索引)
        # 重新加载,确保 trade_time 是可用的列
        if "trade_time" not in df.columns:
            # MultiIndex (trade_date, trade_time) → reset_index
            df = df.reset_index()
        # 此时 df 有 trade_time 列
        try:
            df["trade_time"] = pd.to_datetime(df["trade_time"])
        except Exception:
            return None

        # 2. 切片: trade_date 当天 且 trade_time <= target_time
        try:
            td_ts = pd.Timestamp(trade_date)
            mask = (df["trade_time"].dt.date == td_ts.date()) & (df["trade_time"] <= pd.Timestamp(time_max_str))
        except Exception:
            return None
        window = df[mask].sort_values("trade_time").reset_index(drop=True)

        if window is None or window.empty:
            return None

        # 3. 聚合
        try:
            last_row = window.iloc[-1]
            current_price = float(last_row["close"])
            open_val = float(window.iloc[0]["open"])
            high_val = float(window["high"].max())
            low_val = float(window["low"].min())
            vol_val = float(window["vol"].sum()) if "vol" in window.columns else 0.0
            amt_val = float(window["amount"].sum()) if "amount" in window.columns else 0.0
        except Exception as e:
            logger.warning(f"[data] {ts_code} 指数分钟聚合失败: {e}")
            return None

        # 4. 昨收 (从指数 1min 推算: 取 trade_date 之前最后一条 close)
        pre_close = 0.0
        try:
            prev_mask = df["trade_time"].dt.date < td_ts.date()
            prev_df = df[prev_mask]
            if not prev_df.empty:
                pre_close = float(prev_df.sort_values("trade_time").iloc[-1]["close"])
        except Exception:
            pass

        # 5. 真实涨跌幅
        change_pct = round((current_price - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0.0

        return {
            "ts_code": ts_code,
            "trade_date": date_str,
            "current_price": round(current_price, 2),
            "open": round(open_val, 2),
            "high": round(high_val, 2),
            "low": round(low_val, 2),
            "close": round(current_price, 2),
            "vol": round(vol_val, 0),
            "amount": round(amt_val, 0),
            "pre_close": round(pre_close, 2),
            "change_pct": change_pct,
        }

    # ── 板块资金流向 ──

    # 10 个中证一级行业指数代码（与 index_1min/ 里的 000032~000041 对应）
    # 这些是"中证一级行业"大类：能源/材料/工业/可选/消费/医药/金融/信息/通信/公用
    CSI_L1_INDICES: Dict[str, str] = {
        "000032.SH": "中证能源",
        "000033.SH": "中证材料",
        "000034.SH": "中证工业",
        "000035.SH": "中证可选",
        "000036.SH": "中证消费",
        "000037.SH": "中证医药",
        "000038.SH": "中证金融",
        "000039.SH": "中证信息",
        "000040.SH": "中证通信",
        "000041.SH": "中证公用",
    }

    def get_realtime_sector_pct(self, trade_date: date, hour: int, minute: int,
                                  include_themes: bool = True,
                                  theme_top_n: int = 15) -> dict:
        """盘中实时行业/主题涨跌幅（反未来函数: 0~3分钟延迟, 与个股分钟快照一致）

        数据源: data/backtest/指数数据/index_1min/
        - 10 个中证一级行业指数 (000032~000041.SH) — 行业大类实时涨跌
        - 287 个主题指数 — 题材/概念实时涨跌 (按涨幅排序, 返回 top_n)

        Args:
            trade_date: 模拟交易日
            hour, minute: 目标时刻 (HH:MM)
            include_themes: 是否包含主题指数
            theme_top_n: 主题指数返回 top N

        Returns:
            {
                "trade_date": "2026-02-03",
                "phase_time": "09:35",
                "data_freshness": "intraday_estimate",
                "caveat": "盘中 09:35 实时估算, 0~3分钟延迟",
                "industries": [{"ts_code": "000034.SH", "name": "中证工业", "pct_change": 2.15, "pre_close": 3500, "current": 3575}, ...],
                "themes": [{"ts_code": "399368.SZ", "name": "中证军工", "pct_change": 4.20, ...}, ...]  # 按涨幅降序
            }
        """
        result = {
            "trade_date": trade_date.isoformat(),
            "phase_time": f"{hour:02d}:{minute:02d}",
            "data_freshness": "intraday_estimate",
            "caveat": f"盘中 {hour:02d}:{minute:02d} 实时估算（指数 1min 数据, 0~3 分钟延迟）",
            "industries": [],
            "themes": [],
        }

        # 1. 10 个中证一级行业指数
        for ts_code, name in self.CSI_L1_INDICES.items():
            try:
                w = self.get_index_minute_window(ts_code, trade_date, hour, minute)
                if w:
                    result["industries"].append({
                        "ts_code": ts_code,
                        "name": name,
                        "pct_change": float(w.get("change_pct", 0)),
                        "current": float(w.get("current_price", 0)),
                        "pre_close": float(w.get("pre_close", 0)),
                    })
            except Exception:
                continue

        # 2. 主题指数 (从 index_basic 里筛 category='主题指数' 且 has_1min 的)
        if include_themes:
            try:
                ib = self._load_index_basic_cached()
                if ib is not None and not ib.empty:
                    themes = ib[(ib["category"] == "主题指数") & (ib["has_1min"] == True)]
                    for _, row in themes.iterrows():
                        ts_code = row["ts_code"]
                        try:
                            w = self.get_index_minute_window(ts_code, trade_date, hour, minute)
                            if w:
                                result["themes"].append({
                                    "ts_code": ts_code,
                                    "name": str(row["name"]),
                                    "pct_change": float(w.get("change_pct", 0)),
                                    "current": float(w.get("current_price", 0)),
                                    "pre_close": float(w.get("pre_close", 0)),
                                })
                        except Exception:
                            continue
                    # 按涨幅降序
                    result["themes"].sort(key=lambda x: x["pct_change"], reverse=True)
                    result["themes"] = result["themes"][:theme_top_n]
            except Exception as e:
                logger.warning(f"[data] 主题指数实时计算失败: {e}")

        return result

    _index_basic_cache: Optional[pd.DataFrame] = None

    def _load_index_basic_cached(self) -> Optional[pd.DataFrame]:
        """懒加载 index_basic (含 has_1min 标记)"""
        if self._index_basic_cache is not None:
            return self._index_basic_cache
        path = self._index_1min_dir.parent / "index_basic.parquet"
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        if df.empty:
            return None
        # 标记哪些有 1min 文件
        import os
        files = set(f.replace(".parquet", "") for f in os.listdir(self._index_1min_dir) if f.endswith(".parquet"))
        df = df.copy()
        df["has_1min"] = df["ts_code"].isin(files)
        self._index_basic_cache = df
        return df

    def _load_sector_date(self, file_name: str, date_str: str) -> pd.DataFrame:
        """懒加载板块级资金流数据"""
        path = self.DATA_ROOT / "资金流向数据" / file_name
        if not path.exists():
            return pd.DataFrame()
        ts = pd.Timestamp(date_str)
        df = pd.read_parquet(path, filters=[("trade_date", "=", ts)])
        return df

    def get_industry_flow(self, trade_date: date, top_n: int = 8) -> list:
        """获取行业资金流向排名（前日）
        moneyflow_ind_dc.parquet: net_amount 单位 = 元 (除以 1e8 得亿元)"""
        df = self._load_sector_date("moneyflow_ind_dc.parquet", trade_date.isoformat())
        if df.empty:
            return []
        df = df.nlargest(top_n, "net_amount") if "net_amount" in df.columns else df.head(top_n)
        return [
            {"name": row.get("name", ""), "pct_change": float(row.get("pct_change", 0) or 0),
             "net_amount": round(float(row.get("net_amount", 0) or 0) / 1e8, 4)}
            for _, row in df.iterrows()
        ]

    def get_concept_flow(self, trade_date: date, top_n: int = 8) -> list:
        """获取概念板块资金流向排名（前日）
        moneyflow_cnt_ths.parquet: net_amount 单位 = 万元 (除以 1e4 得亿元)"""
        df = self._load_sector_date("moneyflow_cnt_ths.parquet", trade_date.isoformat())
        if df.empty:
            return []
        df = df.nlargest(top_n, "net_amount") if "net_amount" in df.columns else df.head(top_n)
        return [
            {"name": row.get("name", ""), "pct_change": float(row.get("pct_change", 0) or 0),
             "net_amount": round(float(row.get("net_amount", 0) or 0) / 1e4, 4),  # 万→亿
             "lead_stock": str(row.get("lead_stock", ""))}
            for _, row in df.iterrows()
        ]

    def get_market_flow(self, trade_date: date) -> dict:
        """获取全市场资金流向（前日）
        moneyflow_mkt_dc.parquet: net_amount / buy_* 单位 = 元 (除以 1e8 得亿元)"""
        df = self._load_sector_date("moneyflow_mkt_dc.parquet", trade_date.isoformat())
        if df.empty:
            return {}
        row = df.iloc[0]
        return {
            "net_amount": round(float(row.get("net_amount", 0) or 0) / 1e8, 4),
            "buy_elg": round(float(row.get("buy_elg_amount", 0) or 0) / 1e8, 4),
            "buy_lg": round(float(row.get("buy_lg_amount", 0) or 0) / 1e8, 4),
            "close_sh": float(row.get("close_sh", 0) or 0),
            "pct_sh": float(row.get("pct_change_sh", 0) or 0),
        }

    # ── 资金流向 ──

    def _load_moneyflow_date(self, date_str: str) -> pd.DataFrame:
        if date_str in self._moneyflow_cache:
            return self._moneyflow_cache[date_str]
        ts = pd.Timestamp(date_str)
        df = pd.read_parquet(self._moneyflow_path, filters=[("trade_date", "=", ts)])
        if not df.empty and df.index.nlevels == 2:
            df.index = df.index.droplevel(0)
        self._moneyflow_cache[date_str] = df
        return df

    def get_intraday_low(self, sym: str, trade_date: date) -> Optional[float]:
        """取个股当日分钟最低价 (用于估算盘中最大回撤)"""
        df = self._load_minute_stock(sym)
        if df is None or df.empty or "low" not in df.columns:
            return None
        date_str = trade_date.isoformat()
        td_ts = pd.Timestamp(trade_date)
        try:
            if isinstance(df.index, pd.MultiIndex) and df.index.nlevels >= 2:
                same_day = df.index.get_level_values(0).date == td_ts.date()
                day_data = df.loc[same_day]
            elif isinstance(df.index, pd.DatetimeIndex):
                day_data = df.loc[df.index.date == td_ts.date()]
            else:
                return None
            if day_data.empty:
                return None
            return round(float(day_data["low"].min()), 2)
        except Exception:
            return None

    def get_minute_flow_bias(self, sym: str, trade_date: date, 
                              target_hour: int, target_minute: int) -> Optional[dict]:
        """用分钟K线 OHLC 估算 09:30→phase_time 的净买卖倾向
        原理: 蜡烛实体位置反映多空力量, 阳线且上影线短 → 买盘主导
        返回: {bias: -1.0~1.0, buy_vol_est: float, sell_vol_est: float, total_vol: float, candle_count: int}
              bias > 0 → 买入主导, bias < 0 → 卖出主导
        """
        df = self._load_minute_stock(sym)
        if df is None or df.empty:
            return None
        needed = ["open", "high", "low", "close", "volume"]
        if not all(c in df.columns for c in needed):
            return None

        date_str = trade_date.isoformat()
        td_ts = pd.Timestamp(trade_date)
        time_min = pd.Timestamp(f"{date_str} 09:30:00")
        cur_minutes = target_hour * 60 + target_minute + 1  # +1 补偿 stock_1min 偏移
        if cur_minutes >= 24 * 60:
            time_max = pd.Timestamp(f"{date_str} 15:01:00")
        else:
            time_max = pd.Timestamp(f"{date_str} {cur_minutes // 60:02d}:{cur_minutes % 60:02d}:00")

        try:
            if isinstance(df.index, pd.MultiIndex) and df.index.nlevels >= 2:
                l0 = df.index.get_level_values(0)
                l1 = df.index.get_level_values(1)
                mask = (l0.date == td_ts.date()) & (l1 >= time_min) & (l1 < time_max)
                candles = df.loc[mask]
            elif isinstance(df.index, pd.DatetimeIndex):
                mask = (df.index.date == td_ts.date()) & (df.index >= time_min) & (df.index < time_max)
                candles = df.loc[mask]
            else:
                return None
        except Exception:
            return None

        if candles is None or candles.empty:
            return None

        buy_vol = 0.0
        sell_vol = 0.0
        candle_count = 0

        for _, row in candles.iterrows():
            o, h, l, c, v = float(row["open"]), float(row["high"]), float(row["low"]), \
                            float(row["close"]), float(row["volume"])
            if v <= 0 or h <= l:
                continue
            spread = h - l
            if spread <= 0:
                continue

            if c >= o:
                # 阳线: 实体在下方, 上影线 = 空方残余
                # 买方力量 = 实体 + 下影线, 卖方 = 上影线
                body = c - o
                upper_wick = h - c
                lower_wick = o - l
                buy_strength = (body + lower_wick) / spread  # 0~1
                buy_vol += v * buy_strength
                sell_vol += v * (1 - buy_strength)
            else:
                # 阴线: 实体在上方, 下影线 = 多方残余
                body = o - c
                upper_wick = h - o
                lower_wick = c - l
                sell_strength = (body + upper_wick) / spread
                sell_vol += v * sell_strength
                buy_vol += v * (1 - sell_strength)
            candle_count += 1

        total_vol = buy_vol + sell_vol
        if total_vol <= 0:
            return None

        bias = round((buy_vol - sell_vol) / total_vol, 4)  # -1(纯卖) ~ +1(纯买)
        return {
            "bias": bias,
            "buy_vol_est": round(buy_vol, 0),
            "sell_vol_est": round(sell_vol, 0),
            "total_vol": round(total_vol, 0),
            "candle_count": candle_count,
        }

    def get_moneyflow(self, sym: str, trade_date: date) -> Optional[dict]:
        date_str = trade_date.isoformat()
        df = self._load_moneyflow_date(date_str)
        norm_sym = self._normalize_sym(sym)
        try:
            row = df.loc[norm_sym]
            return {
                "symbol": sym, "date": date_str,
                "net_mf_amount": float(row["net_mf_amount"]),
                "net_mf_vol": int(row["net_mf_vol"]),
                "buy_elg_amount": float(row["buy_elg_amount"]),
                "sell_elg_amount": float(row["sell_elg_amount"]),
                "buy_lg_amount": float(row["buy_lg_amount"]),
                "sell_lg_amount": float(row["sell_lg_amount"]),
            }
        except KeyError:
            return None

    def get_moneyflow_intraday_weight(self, sym: str, trade_date: date,
                                       target_hour: int, target_minute: int) -> Optional[dict]:
        """B2: 成交额加权缩放权重
        用 stock_1min 的累计成交额占比代替"时间均匀"假设
        返回:
          {
            "weight": float,           # 0~1, phase_time 时刻的成交额占全天比例
            "target_amount": float,    # 09:30~phase_time 累计成交额
            "total_amount": float,     # 全天累计成交额
            "basis": "amount_weighted" # 标识这是 B2 算法
          }
        或 None (无分钟数据时)
        """
        df = self._load_minute_stock(sym)
        if df is None or df.empty:
            return None
        if "amount" not in df.columns:
            return None

        date_str = trade_date.isoformat()
        td_ts = pd.Timestamp(trade_date)

        # ── 切片: 09:30 ~ target_time (含) 当日所有分钟 ──
        # 注意: stock_1min 把"该分钟"记到下一分钟(如 13:00 实际写在 13:01:00),
        # 所以 time_max 要 +1 分钟, 13:00 的切片要包含 13:01:00
        time_min = pd.Timestamp(f"{date_str} 09:30:00")
        # target_minute + 1 分钟 (跨小时则小时也进位)
        end_total = target_hour * 60 + target_minute + 1
        if end_total >= 24 * 60:
            time_max = pd.Timestamp(f"{date_str} 15:01:00")  # 上限
        else:
            end_h = end_total // 60
            end_m = end_total % 60
            time_max = pd.Timestamp(f"{date_str} {end_h:02d}:{end_m:02d}:00")

        # 兼容 MultiIndex (trade_date, trade_time) 与 单层 DatetimeIndex
        try:
            if isinstance(df.index, pd.MultiIndex):
                if df.index.nlevels >= 2:
                    level0 = df.index.get_level_values(0)  # trade_date
                    level1 = df.index.get_level_values(1)  # trade_time
                    same_day = level0.date == td_ts.date()
                    # 切片: 同日 且 trade_time 属于 [09:30, time_max]
                    in_window = same_day & (level1 >= time_min) & (level1 < time_max)
                    window = df.loc[in_window]
                else:
                    return None
            elif isinstance(df.index, pd.DatetimeIndex):
                same_day = df.index.date == td_ts.date()
                in_window = same_day & (df.index >= time_min) & (df.index < time_max)
                window = df.loc[in_window]
            else:
                return None
        except Exception:
            return None

        if window is None or window.empty:
            return None

        target_amount = float(window["amount"].sum())

        # ── 全天成交额 ──
        try:
            if isinstance(df.index, pd.MultiIndex):
                day_all = df.loc[same_day]
            else:
                day_all = df.loc[same_day]
        except Exception:
            return None
        if day_all is None or day_all.empty:
            return None
        total_amount = float(day_all["amount"].sum())
        if total_amount <= 0:
            return None

        weight = round(target_amount / total_amount, 6)
        # 保护: weight 必须在 [0.15, 1] 内
        # 下限 0.15 补偿开盘资金集中效应 (集合竞价+开盘5分钟的成交额远高于
        # 全天均值, 纯成交额比例会系统性低估早盘的真实资金流, 导致 Pi 在
        # 09:35窗口看到所有标的"5日主力≈0"而误杀全板块)
        weight = min(max(weight, 0.15), 1.0)

        return {
            "weight": weight,
            "target_amount": target_amount,
            "total_amount": total_amount,
            "basis": "amount_weighted",
        }

    def get_market_overview(self, trade_date: date) -> dict:
        quotes = self.get_all_daily_quotes(trade_date)
        if not quotes:
            return {"date": trade_date.isoformat(), "total": 0, "gainers": 0, "losers": 0}
        gainers = sum(1 for q in quotes.values() if q["change_pct"] > 0)
        losers = sum(1 for q in quotes.values() if q["change_pct"] < 0)
        avg_change = sum(q["change_pct"] for q in quotes.values()) / len(quotes) if quotes else 0
        return {
            "date": trade_date.isoformat(), "total": len(quotes),
            "gainers": gainers, "losers": losers,
            "flat": len(quotes) - gainers - losers,
            "avg_change_pct": round(avg_change, 2),
        }

    def get_sector_moneyflow_weight(self, trade_date: date, target_hour: int,
                                     target_minute: int,
                                     proxy_symbol: str = "600519.SH") -> dict:
        """板块/概念资金流的盘中缩放权重 (B2: 成交额加权)

        与 get_moneyflow_intraday_weight 单票版共用代理, 但语义层面:
        - 适用于行业/概念/大盘资金流的"估算盘中累计"
        - 盘前 < 09:30 → weight=0
        - 盘后 >= 15:00 → weight=1.0 (EOD 落盘)
        - 盘中 → 用 proxy_symbol 的 stock_1min 成交额占比作代理
        Returns:
          {weight, basis, proxy_symbol, target_amount, total_amount}
        """
        market_open = 9 * 60 + 30
        market_close = 15 * 60
        cur = target_hour * 60 + target_minute
        if cur < market_open:
            return {"weight": 0.0, "basis": "pre_market_zero", "proxy_symbol": None,
                    "target_amount": 0.0, "total_amount": 0.0}
        if cur >= market_close:
            return {"weight": 1.0, "basis": "eod_full", "proxy_symbol": None,
                    "target_amount": 0.0, "total_amount": 0.0}
        # 盘中: 调单票 B2
        w = self.get_moneyflow_intraday_weight(proxy_symbol, trade_date, target_hour, target_minute)
        if w is not None:
            raw_weight = w["weight"]
            # -- K线方向修正: 用分钟蜡烛图估算实际买卖倾向 (替代简单价格比较) --
            direction_discount = 1.0
            direction_note = ""
            try:
                bias_info = self.get_minute_flow_bias(proxy_symbol, trade_date,
                                                       target_hour, target_minute)
                if bias_info:
                    bias = bias_info["bias"]
                    n = bias_info["candle_count"]
                    if bias < -0.5:
                        direction_discount = 0.5
                        direction_note = f"K线强卖{bias:.2f}({n}根)修正0.5x"
                    elif bias < -0.2:
                        direction_discount = 0.7
                        direction_note = f"K线弱卖{bias:.2f}({n}根)修正0.7x"
                    elif bias < 0.1:
                        direction_discount = 0.85
                        direction_note = f"K线中性{bias:.2f}({n}根)修正0.85x"
                    # bias >= 0.1: 买盘主导, 不修正
            except Exception:
                pass
            weight = round(raw_weight * direction_discount, 6)
            basis = "amount_weighted_candle_adj" if direction_discount < 1.0 else "amount_weighted"
            return {
                "weight": weight,
                "basis": basis,
                "proxy_symbol": proxy_symbol,
                "target_amount": w["target_amount"],
                "total_amount": w["total_amount"],
                "direction_discount": direction_discount,
                "direction_note": direction_note,
            }
        # B1 fallback: 时间均匀
        weight = round((cur - market_open) / (market_close - market_open), 4)
        return {"weight": weight, "basis": "time_linear", "proxy_symbol": None,
                "target_amount": 0.0, "total_amount": 0.0}

    def get_market_moneyflow_intraday(self, trade_date: date, target_hour: int,
                                        target_minute: int,
                                        proxy_symbol: str = "600519.SH") -> Optional[dict]:
        """B2 大盘盘中资金流 (第一步: 用单只票做代理)
        思路:
          - 大盘资金流 EOD: moneyflow_mkt_dc.parquet (东财"主力"口径, 与 get_market_flow 一致)
          - 盘中权重: 用 proxy_symbol (默认 600519.SH) 的 stock_1min 成交额占比
            作为"全市场"的代理 (单只票近似, 全市场聚合留待第二步)
        Returns:
          {
            "eod_net_amount":  float,  # 当日全市场 EOD 净流入(元)
            "intraday_net":    float,  # 估算的盘中累计(元) = eod * weight
            "weight":          float,  # 0~1
            "basis":           "single_symbol_proxy" | "time_linear" | "eod_full",
            "proxy_symbol":    str,
          }
        """
        # 1. 算当日全市场 EOD 净流入 (用 moneyflow_mkt_dc.parquet, 与 get_market_flow 口径一致)
        date_str = trade_date.isoformat()
        try:
            df_mkt = self._load_sector_date("moneyflow_mkt_dc.parquet", date_str)
            if df_mkt is None or df_mkt.empty:
                return None
            row = df_mkt.iloc[0]
            eod_net_amount = float(row.get("net_amount", 0) or 0)
            eod_buy_elg = float(row.get("buy_elg_amount", 0) or 0)
            eod_buy_lg = float(row.get("buy_lg_amount", 0) or 0)
            eod_buy_md = float(row.get("buy_md_amount", 0) or 0)
            eod_buy_sm = float(row.get("buy_sm_amount", 0) or 0)
            # sell 侧: mkt_dc 没给 sell_* 列, 但有 net_amount 公式: net = buy - sell
            # 假设: sell_elg ≈ buy_elg - (买-卖的差) 不易推, 这里只回报 buy 侧, sell 留 0
        except Exception as e:
            logger.warning(f"[market_intraday] {date_str} 读 moneyflow_mkt_dc EOD 失败: {e}")
            return None

        # 2. 算 phase 权重
        market_open = 9 * 60 + 30
        market_close = 15 * 60
        cur = target_hour * 60 + target_minute
        if cur < market_open:
            weight = 0.0
            basis = "pre_market_zero"
        elif cur < market_close:
            proxy_w = self.get_moneyflow_intraday_weight(proxy_symbol, trade_date,
                                                          target_hour, target_minute)
            if proxy_w is not None:
                weight = proxy_w["weight"]
                basis = "single_symbol_proxy"
            else:
                weight = round((cur - market_open) / (market_close - market_open), 4)
                basis = "time_linear"
        else:
            weight = 1.0
            basis = "eod_full"

        return {
            "eod_net_amount": eod_net_amount,
            "eod_buy_elg": eod_buy_elg,
            "eod_buy_lg": eod_buy_lg,
            "eod_buy_md": eod_buy_md,
            "eod_buy_sm": eod_buy_sm,
            "weight": weight,
            "intraday_net": round(eod_net_amount * weight, 2),
            "intraday_buy_elg": round(eod_buy_elg * weight, 2),
            "intraday_buy_lg": round(eod_buy_lg * weight, 2),
            "intraday_buy_md": round(eod_buy_md * weight, 2),
            "intraday_buy_sm": round(eod_buy_sm * weight, 2),
            "intraday_main_net": round((eod_buy_elg + eod_buy_lg) * weight, 2),
            "basis": basis,
            "proxy_symbol": proxy_symbol if basis == "single_symbol_proxy" else None,
        }


local_data = LocalDataProvider()
