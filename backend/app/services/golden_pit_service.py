# -*- coding: utf-8 -*-
"""黄金坑评分引擎 v2 — 按宽基指数分别追踪，三重确认底部区域。

模型来源: arkvol.com 作者「壬戍帅潘安」的三重判断体系:
  1. 蛋糕理论 (global capital flow) — A股资金外流达历史低位
  2. 宽基贪婪 (per-index greed) — 贪婪值 < 0.35 确认黄金坑, < 0.40 预警
  3. 细分板块 (sector fund greed) — 板块基金跌到极端值
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


from app.services.arkvol_service import ArkvolService, ArkvolServiceError

logger = logging.getLogger(__name__)

from app.services.golden_pit_config import (
    ALL_INDEX_CONFIGS,
    CHINA_INDICES,
    DEFENSE_INDICES,
    SEMI_BOOST_INDICES,
    FAKE_SIGNAL_REBOUND_DAYS,
    GREED_ABSOLUTE_PIT,
    PERCENTILE_GOLDEN_PIT,
    PERCENTILE_WARNING,
    PERCENTILE_WINDOW_DAYS,
    PIT_WINDOW_DAYS,
    POSITION_TIERS,
    PRE_TURN_CUMULATIVE_CAP,
    SIGNAL_QUALITY_LABEL,
    STATUS_MAP,
    TURNING_CONSECUTIVE_DAYS,
    _compute_resonance,
    _describe_entry_strategy,
    _describe_exit_strategy,
    _display_config,
    _strategy_label,
    _trend_label,
    get_trend_factor,
)
from app.services.golden_pit_indicators import (
    _add_trading_days,
    _calculate_decline_rate,
    _calculate_percentile,
    _calculate_price_percentile,
    _detect_exit_signal,
    _detect_p10_entry,
    _detect_trend,
    _determine_status,
    _price_based_greed,
    _price_decline_rate,
    _trading_days_between,
)
from app.services import golden_pit_repository as _repository
from app.services import golden_pit_report as _report
from app.services import golden_pit_sector_service as _sector


class GoldenPitService:
    """黄金坑评分服务 v2 — 逐宽基指数追踪。"""

    def __init__(self, arkvol: Optional[ArkvolService] = None):
        self._arkvol = arkvol or ArkvolService()
        self._last_known_greed: Dict[str, float] = {}  # 用于盘中阈值穿越检测
        self._cache: Dict[str, tuple] = {}  # page_id → (data, timestamp)
        self._kline_cache: Dict[str, tuple] = {}  # etf_code → (bars, timestamp)

    # ═══════════════════════════════════════════════════════════════
    # Data fetching helpers
    # ═══════════════════════════════════════════════════════════════

    def _cached_fetch(self, page_id: str, ttl: int = 7200) -> Dict[str, Any]:
        """带 TTL 缓存的 ArkVol API 调用。数据每日更新一次，默认缓存 2 小时。"""
        now = time.time()
        if page_id in self._cache:
            data, ts = self._cache[page_id]
            if now - ts < ttl:
                return data
        data = self._arkvol.fetch_page(page_id)
        self._cache[page_id] = (data, now)
        return data

    def _cached_ai_summary(self, ttl: int = 7200) -> Dict[str, Any]:
        """带 TTL 缓存的 ai-summary 调用。"""
        cache_key = "ai-summary"
        now = time.time()
        if cache_key in self._cache:
            data, ts = self._cache[cache_key]
            if now - ts < ttl:
                return data
        data = self._arkvol.fetch_ai_summary()
        self._cache[cache_key] = (data, now)
        return data

    def _cached_tech_greed(self, ttl: int = 7200) -> Dict[str, Any]:
        """带 TTL 缓存的 tech-hardware-greed 调用（588200/512480 等科技 ETF 贪婪）。"""
        cache_key = "tech-hardware-greed"
        now = time.time()
        if cache_key in self._cache:
            data, ts = self._cache[cache_key]
            if now - ts < ttl:
                return data
        data = self._arkvol.fetch_tech_greed()
        self._cache[cache_key] = (data, now)
        return data

    def _cached_fund_series(self, fund_code: str, ttl: int = 7200) -> List[Dict]:
        """带 TTL 缓存的单基金贪婪序列（防御标的展示用）。"""
        cache_key = f"fund-series:{fund_code}"
        now = time.time()
        if cache_key in self._cache:
            data, ts = self._cache[cache_key]
            if now - ts < ttl:
                return data
        payload = self._arkvol.fetch_fund_series(fund_code)
        series = payload.get("data", []) if isinstance(payload, dict) else []
        self._cache[cache_key] = (series, now)
        return series

    def _cached_pi_kline(self, etf_code: str, limit: int = 250, ttl: int = 7200) -> List[Dict]:
        """带 TTL 缓存的 ETF 日K线（防御标的价格分位用）。"""
        cache_key = f"kline:{etf_code}"
        now = time.time()
        if cache_key in self._kline_cache:
            bars, ts = self._kline_cache[cache_key]
            if now - ts < ttl:
                return bars
        bars = self._fetch_pi_server_kline(etf_code, limit=limit)
        self._kline_cache[cache_key] = (bars, now)
        return bars

    @staticmethod
    def _fetch_pi_server_kline(etf_code: str, limit: int = 250) -> List[Dict]:
        """通过 Tushare 获取 ETF 日K线，统一为 {date, close} 格式。"""
        from datetime import datetime as dt, timedelta
        from app.core.trading._api_config import get_tushare_pro

        try:
            pro = get_tushare_pro()
            if pro is None:
                logger.warning("Tushare pro 不可用，无法获取 %s K线", etf_code)
                return []

            # 符号标准化：SH562660 → 562660.SH
            s = etf_code.strip().upper()
            if s.startswith("SH"):
                ts_code = f"{s[2:]}.SH"
            elif s.startswith("SZ"):
                ts_code = f"{s[2:]}.SZ"
            elif s.startswith("159") or s.startswith("16"):
                ts_code = f"{s}.SZ"
            else:
                ts_code = f"{s}.SH"

            end_date = dt.now().strftime("%Y%m%d")
            start_date = (dt.now() - timedelta(days=limit * 2)).strftime("%Y%m%d")

            df = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)

            if df is None or df.empty:
                ts_code_sz = f"{s[2:]}.SZ" if s.startswith("SH") else f"{s[2:]}.SH"
                if ts_code_sz != ts_code:
                    df = pro.fund_daily(ts_code=ts_code_sz, start_date=start_date, end_date=end_date)

            if df is None or df.empty:
                return []

            df = df.sort_values("trade_date", ascending=True)
            normalized = []
            for _, row in df.iterrows():
                ts = str(row["trade_date"])
                normalized.append({
                    "date": f"{ts[:4]}-{ts[4:6]}-{ts[6:]}",
                    "close": float(row["close"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                })

            return normalized[-limit:] if len(normalized) > limit else normalized
        except Exception as e:
            logger.warning("Tushare ETF kline 获取失败 (%s): %s", etf_code, e)
            return []

    def _attach_sector_split(self, status: Dict[str, Any], as_of: str) -> None:
        """附加板块拆分选筹摘要（guide_only 宽基用）。失败不影响主状态。"""
        try:
            selection = _sector.select_sectors(as_of=as_of)
        except Exception as e:
            logger.warning("板块拆分选筹失败: %s", e)
            selection = {"as_of": as_of, "enabled": _sector.get_sector_config().get("enabled"),
                         "selected": [], "all": [], "empty_reason": f"选筹服务异常: {e}"}
        status["sector_split_enabled"] = _sector.get_sector_config().get("enabled")
        status["sector_selection"] = selection
        for idx in status.get("indices", []):
            if idx.get("guide_only"):
                idx["sector_summary"] = {
                    "selected": selection.get("selected", []),
                    "empty_reason": selection.get("empty_reason", ""),
                }

    def get_status(self) -> Dict[str, Any]:
        """获取完整的 per-index 黄金坑状态 + 窗口信息 + 三重确认 + 预测。

        优先从 DB 快照读取（每日 15:30 定时落库），无数据时回退 ArkVol API。
        """
        db_result = self._get_status_from_db()
        if db_result is not None:
            return db_result
        return self._get_status_from_api()

    def _get_status_from_api(self) -> Dict[str, Any]:
        """从 ArkVol API 获取完整状态。使用 ai-summary (POST, 轻量) 替代 alla (GET, 重型)。"""
        with ThreadPoolExecutor(max_workers=3) as executor:
            f_ai = executor.submit(self._cached_ai_summary)
            f_gcf = executor.submit(self._cached_fetch, "global-capital-flow")
            f_tech = executor.submit(self._cached_fetch, "alla-tech")
            ai_data = f_ai.result()
            gcf_data = f_gcf.result()
            tech_data = f_tech.result()
            global_macro = self._parse_global_macro_overlay(gcf_data)

        as_of = ai_data.get("asof", "")

        # 从 ai-summary snapshot 提取指数数据（替代 _extract_arkvol_indices）
        arkvol_indices = self._extract_from_ai_summary(ai_data)

        # Pi Server 指数并行
        pi_server_indices: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=1) as executor:
            f_pi = executor.submit(self._extract_pi_server_indices, as_of)
            pi_server_indices = f_pi.result()

        # 半导体增强 + 防御组合并行
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_tech = executor.submit(self._extract_tech_indices, as_of)
            f_def = executor.submit(self._extract_defense_indices, as_of)
            tech_indices = f_tech.result()
            defense_indices = f_def.result()

        all_indices = arkvol_indices + pi_server_indices + tech_indices + defense_indices
        all_indices.sort(key=lambda x: x["priority"])

        # ── 全球宏观后处理 ──
        self._apply_global_macro_to_indices(all_indices, global_macro)

        # 三重确认 + 预测
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_conf = executor.submit(self._compute_triple_confirmation, all_indices, gcf_data, tech_data)
            f_pred = executor.submit(self._predict_next_entry, all_indices)
            confirmation = f_conf.result()
            prediction = f_pred.result()

        window = self._detect_golden_pit_window(all_indices)

        # 优先用 AI 摘要结论，拼接本地分析
        ai_conclusion = ai_data.get("conclusion", "")
        local_summary = _report.build_v2_summary(all_indices, window, confirmation, prediction)
        summary = ai_conclusion + "\n\n——\n" + local_summary if ai_conclusion else local_summary

        status = {
            "as_of": as_of,
            "golden_pit_window": window,
            "indices": all_indices,
            "triple_confirmation": confirmation,
            "prediction": prediction,
            "summary": summary,
            "global_macro": global_macro,
        }
        self._attach_sector_split(status, as_of)
        return status

    @staticmethod
    def _arkvol_code_map() -> Dict[str, str]:
        """构建 ArkVol fund_code → CHINA_INDICES key 的映射。

        支持 arkvol_code 字段: 当配置中 data_source="arkvol" 且指定了 arkvol_code 时，
        ArkVol API 返回的 fund_code 与 CHINA_INDICES 的 key 不同，需要映射。
        例如: 513310 (ETF) → ArkVol 019455 (韩国指数)
        """
        mapping = {}
        for key, cfg in CHINA_INDICES.items():
            if cfg.get("data_source") == "arkvol":
                arkvol_key = cfg.get("arkvol_code", key)
                mapping[arkvol_key] = key
        return mapping

    def _extract_from_ai_summary(self, ai_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 ai-summary 返回的 snapshot 数组重建指数状态。"""
        arkvol_map = self._arkvol_code_map()  # ArkVol fund_code → config key
        snapshot_list = ai_data.get("snapshot", [])
        as_of = ai_data.get("asof", "")

        result = []
        seen_codes = set()
        for snap in snapshot_list:
            snap_code = str(snap.get("fund_code", ""))
            seen_codes.add(snap_code)
            if snap_code not in arkvol_map:
                continue
            config_key = arkvol_map[snap_code]
            cfg = CHINA_INDICES[config_key]

            history = snap.get("history", [])
            sorted_series = sorted(history, key=lambda x: x.get("date", ""))
            current_greed = float(sorted_series[-1].get("greed", 0)) if sorted_series else 0.0

            percentile = _calculate_percentile(current_greed, sorted_series)
            # 用 ArkVol 的 change_5 替代本地计算的 decline_rate
            change_5 = snap.get("change_5", 0) or 0
            change_20 = snap.get("change_20", 0) or 0
            decline_rate = round(-change_5, 4)

            status = _determine_status(cfg, current_greed, percentile)

            absolute_triggered = current_greed < GREED_ABSOLUTE_PIT

            index_info = self._build_index_info(
                code=config_key, cfg=cfg, value=current_greed, close=0,
                percentile=percentile, decline_rate=decline_rate,
                status=status, absolute_triggered=absolute_triggered,
                data_source="arkvol", sorted_series=sorted_series,
                as_of=as_of,
            )
            index_info["change_5"] = round(change_5, 4)
            index_info["change_20"] = round(change_20, 4)
            result.append(index_info)

        missing = set(arkvol_map.keys()) - seen_codes
        if missing:
            logger.warning("ai-summary 未返回以下基金代码 (已配置但缺失): %s", missing)

        return result

    def get_history(self, index: str = "all", days: int = 60) -> Dict[str, Any]:
        """从 DB 快照表获取历史贪婪值趋势数据，用于前端折线图。"""
        return _repository.get_history(index=index, days=days)


    def _get_status_from_db(self) -> Optional[Dict[str, Any]]:
        """尝试从 DB 快照重建完整状态。最新快照不存在或历史不足 60 天时返回 None。"""
        try:
            from app.database import SessionLocal
            from app.models.golden_pit import GoldenPitSnapshot

            db = SessionLocal()
            try:
                # 查询 DB 中最新的快照日期（而非 today），避免与 save_daily_snapshot 的 as_of 日期不匹配
                latest_date_row = (
                    db.query(GoldenPitSnapshot.date)
                    .order_by(GoldenPitSnapshot.date.desc())
                    .first()
                )
                if not latest_date_row:
                    return None
                latest_date = latest_date_row[0]

                today_snaps = (
                    db.query(GoldenPitSnapshot)
                    .filter(GoldenPitSnapshot.date == latest_date)
                    .all()
                )
                if not today_snaps:
                    return None

                snap_map = {s.fund_code: s for s in today_snaps}

                # 批量读取历史序列 (一次 IN 查询 + 内存分组)；含成长/防御/半导体增强
                db_codes = [code for code in ALL_INDEX_CONFIGS if code in snap_map]
                all_rows = (
                    db.query(GoldenPitSnapshot)
                    .filter(GoldenPitSnapshot.fund_code.in_(db_codes))
                    .order_by(GoldenPitSnapshot.date.desc())
                    .all()
                )
                series_by_code: Dict[str, List[Any]] = {}
                for r in all_rows:
                    series_by_code.setdefault(r.fund_code, []).append(r)

                indices = []
                for code, cfg in ALL_INDEX_CONFIGS.items():
                    snap = snap_map.get(code)
                    if not snap:
                        continue

                    code_rows = series_by_code.get(code, [])
                    if len(code_rows) < 60:
                        logger.info("DB 快照历史不足 (%s: %d天)，回退 API", code, len(code_rows))
                        return None
                    code_rows = list(reversed(code_rows))  # date 升序
                    sorted_series = [
                        {"date": r.date, "greed": r.greed_value, "close": r.close_price or 0}
                        for r in code_rows
                    ]

                    index_info = self._build_index_info(
                        code=code, cfg=cfg,
                        value=snap.greed_value,
                        close=snap.close_price or 0,
                        percentile=snap.percentile if snap.percentile is not None else 50.0,
                        decline_rate=snap.decline_rate_5d or 0.0,
                        status=snap.status,
                        absolute_triggered=(snap.greed_value or 0) < GREED_ABSOLUTE_PIT,
                        data_source=("defense_price" if cfg.get("tier") == "defense_rotation" else cfg.get("data_source", "arkvol")),
                        sorted_series=sorted_series,
                        as_of=latest_date,
                    )
                    index_info["change_5"] = snap.change_5
                    index_info["change_20"] = snap.change_20
                    indices.append(index_info)
            finally:
                db.close()

            if not indices:
                return None

            # 补全防御/半导体标的（DB 快照尚未积累历史时，实时从 API 构建，保证 DCA 门控与展示完整）
            existing_codes = {i["fund_code"] for i in indices}
            tech_missing = any(c not in existing_codes for c in SEMI_BOOST_INDICES)
            with ThreadPoolExecutor(max_workers=2) as executor:
                f_def_extra = executor.submit(self._extract_defense_indices, latest_date)
                f_tech_extra = executor.submit(self._extract_tech_indices, latest_date) if tech_missing else None
                def_extra = f_def_extra.result()
                tech_extra = f_tech_extra.result() if f_tech_extra else []
            def_map = {i["fund_code"]: i for i in def_extra}
            for item in tech_extra:
                if item["fund_code"] not in existing_codes:
                    indices.append(item)
                    existing_codes.add(item["fund_code"])
            for item in def_extra:
                if item["fund_code"] not in existing_codes:
                    indices.append(item)
                    existing_codes.add(item["fund_code"])
            # ????????????? ArkVol ????????????
            for item in indices:
                ex = def_map.get(item["fund_code"])
                if ex:
                    item["pit_greed_threshold"] = ex["pit_greed_threshold"]
                    item["entry_greed_threshold"] = ex["entry_greed_threshold"]
                    item["exit_greed_threshold"] = ex["exit_greed_threshold"]
            indices.sort(key=lambda x: x["priority"])

            with ThreadPoolExecutor(max_workers=2) as executor:
                f_gcf = executor.submit(self._cached_fetch, "global-capital-flow")
                f_tech = executor.submit(self._cached_fetch, "alla-tech")
                gcf_data = f_gcf.result()
                tech_data = f_tech.result()
                global_macro = self._parse_global_macro_overlay(gcf_data)

            # ── 全球宏观后处理 ──
            self._apply_global_macro_to_indices(indices, global_macro)

            confirmation = self._compute_triple_confirmation(indices, gcf_data, tech_data)
            prediction = self._predict_next_entry(indices)
            window = self._detect_golden_pit_window(indices)
            summary = _report.build_v2_summary(indices, window, confirmation, prediction)

            status = {
                "as_of": latest_date,
                "golden_pit_window": window,
                "indices": indices,
                "triple_confirmation": confirmation,
                "prediction": prediction,
                "summary": summary,
                "global_macro": global_macro,
                "_source": "db",
            }
            self._attach_sector_split(status, latest_date)
            return status
        except Exception as e:
            logger.warning("从 DB 重建黄金坑状态失败，回退 API: %s", e)
            return None

    def get_snapshots(self, days: int = 30) -> List[Dict[str, Any]]:
        """从数据库读取历史快照。"""
        return _repository.get_snapshots(days=days)


    def sync_full_series_to_db(self) -> int:
        """从 ArkVol 全量接口拉取历史序列，批量写入 GoldenPitSnapshot。"""
        return _repository.sync_full_series_to_db(self._arkvol, self._arkvol_code_map())


    def save_daily_snapshot(self, now: Optional[datetime] = None) -> List[Any]:
        """保存每日快照到数据库。先同步全量历史序列，再写入当天状态快照。"""
        return _repository.save_daily_snapshot(self, now=now)


    def format_morning_report(self, status: Optional[Dict[str, Any]] = None) -> str:
        """生成 QQ 盘前报告 (8:50 AM)。"""
        if status is None:
            status = self.get_status()
        return _report.format_morning_report(status)


    def check_threshold_crossings(self, status: Optional[Dict[str, Any]] = None) -> List[str]:
        """检测阈值穿越，返回需要推送的预警消息列表。"""
        return _report.check_threshold_crossings(self, status)


    def _load_previous_percentile(self, as_of: Optional[str] = None) -> Dict[str, float]:
        """从数据库加载最近一个交易日（今天之前）的分位值。"""
        return _repository.load_previous_percentile(as_of=as_of)


    def _extract_pi_server_indices(self, as_of: str) -> List[Dict[str, Any]]:
        """从 Pi Server ETF K 线数据提取价格分位驱动的指数状态。"""
        pi_codes = {code: cfg for code, cfg in CHINA_INDICES.items() if cfg.get("data_source") == "pi_server"}
        if not pi_codes:
            return []

        result = []
        for code, cfg in pi_codes.items():
            etf_code = cfg.get("etf_code", "")
            if not etf_code:
                continue

            bars = self._fetch_pi_server_kline(etf_code, limit=250)
            if not bars or len(bars) < 10:
                logger.warning("Pi Server %s K线数据不足，跳过", etf_code)
                continue

            # 用最近 120 根 bar 做滚动窗口
            window_bars = bars[-120:]
            closes_120 = [float(b.get("close", 0)) for b in window_bars]
            current_price = closes_120[-1] if closes_120 else 0.0
            current_close = closes_120[-1] if closes_120 else 0.0

            # 从价格位置计算分位和合成 greed
            percentile = _calculate_price_percentile(current_price, closes_120)
            synthetic_greed = _price_based_greed(current_price, closes_120)
            decline_rate = _price_decline_rate(closes_120)

            status = _determine_status(cfg, synthetic_greed, percentile)

            absolute_triggered = synthetic_greed < GREED_ABSOLUTE_PIT
            data_source = "pi_server_price"

            # 构建一个与 ArkVol series 兼容的 sorted_series (用价格代替 greed)
            sorted_series = [
                {"date": b.get("date", ""), "greed": _price_based_greed(
                    float(b.get("close", 0)), closes_120
                ), "close": float(b.get("close", 0))}
                for b in bars
            ]

            index_info = self._build_index_info(
                code=code, cfg=cfg, value=synthetic_greed, close=current_close,
                percentile=percentile, decline_rate=decline_rate,
                status=status, absolute_triggered=absolute_triggered,
                data_source=data_source, sorted_series=sorted_series,
                as_of=as_of,
            )
            result.append(index_info)

        return result

    def _extract_tech_indices(self, as_of: str) -> List[Dict[str, Any]]:
        """从 ArkVol tech-hardware-greed 接口构建半导体增强标的（588200/512480）状态。"""
        try:
            tech_data = self._cached_tech_greed()
        except Exception as e:
            logger.warning("获取 tech-hardware-greed 失败: %s", e)
            return []
        data = tech_data.get("data", {}) or {}
        result = []
        for code, cfg in SEMI_BOOST_INDICES.items():
            rows = data.get(cfg.get("arkvol_code", code), [])
            if not rows:
                logger.warning("tech-hardware-greed 未返回 %s (%s)，跳过", code, cfg.get("name"))
                continue
            sorted_series = sorted(rows, key=lambda x: str(x.get("date", "")))
            current_greed = float(sorted_series[-1].get("greed", 0))
            closes = [float(r.get("close", 0)) for r in sorted_series if r.get("close")]
            percentile = _calculate_percentile(current_greed, sorted_series)
            status = _determine_status(cfg, current_greed, percentile)
            index_info = self._build_index_info(
                code=code, cfg=cfg, value=current_greed,
                close=closes[-1] if closes else 0,
                percentile=percentile,
                decline_rate=_calculate_decline_rate(sorted_series),
                status=status,
                absolute_triggered=current_greed < GREED_ABSOLUTE_PIT,
                data_source="arkvol_tech", sorted_series=sorted_series,
                as_of=as_of,
            )
            result.append(index_info)
        return result

    def _extract_defense_indices(self, as_of: str) -> List[Dict[str, Any]]:
        """构建防御组合标的状态：250日价格分位信号 + ArkVol 贪婪展示。

        入坑/撤场以价格分位为准（回测校准阈值），ArkVol 贪婪仅作展示字段。
        """
        result = []

        def _build_one(code: str, cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            etf_code = cfg.get("etf_code", "")
            bars = self._cached_pi_kline(etf_code, limit=250) if etf_code else []
            if not bars or len(bars) < 10:
                logger.warning("防御标的 %s K线数据不足，跳过", etf_code)
                return None
            window_bars = bars[-250:]
            closes = [float(b.get("close", 0)) for b in window_bars if b.get("close")]
            if not closes:
                return None
            current_price = closes[-1]
            percentile = _calculate_price_percentile(current_price, closes)
            synthetic_greed = _price_based_greed(current_price, closes)
            decline_rate = _price_decline_rate(closes)

            arkvol_greed = None
            arkvol_series = None
            arkvol_code = cfg.get("arkvol_code", "")
            if arkvol_code:
                try:
                    arkvol_series = self._cached_fund_series(arkvol_code)
                    if arkvol_series:
                        arkvol_greed = float(arkvol_series[-1].get("greed", 0))
                except Exception as e:
                    logger.warning("防御标的 %s ArkVol 贪婪获取失败: %s", arkvol_code, e)

            status = _determine_status(cfg, synthetic_greed, percentile)
            if not cfg.get("entry_enabled", True):
                status = "normal"

            # 价格合成贪婪序列（趋势/退出信号复用同一状态机）
            sorted_series = [
                {"date": b.get("date", ""),
                 "greed": _price_based_greed(float(b.get("close", 0)), closes),
                 "close": float(b.get("close", 0))}
                for b in bars
            ]
            index_info = self._build_index_info(
                code=code, cfg=cfg,
                value=arkvol_greed if arkvol_greed is not None else synthetic_greed,
                close=current_price, percentile=percentile,
                decline_rate=decline_rate, status=status,
                absolute_triggered=False,
                data_source="defense_price", sorted_series=sorted_series,
                as_of=as_of,
            )
            index_info["arkvol_greed"] = round(arkvol_greed, 4) if arkvol_greed is not None else None
            index_info["price_percentile"] = round(percentile, 1)
            # ????????? ArkVol ????????????????????????
            if arkvol_series:
                pit_ref, entry_ref, exit_ref = self._arkvol_ref_lines(arkvol_series, cfg)
                index_info["pit_greed_threshold"] = pit_ref
                index_info["entry_greed_threshold"] = entry_ref
                index_info["exit_greed_threshold"] = exit_ref
            return index_info

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(_build_one, code, cfg): code
                for code, cfg in DEFENSE_INDICES.items()
            }
            for fut in as_completed(futures):
                try:
                    item = fut.result()
                    if item is not None:
                        result.append(item)
                except Exception as e:
                    logger.warning("构建防御标的状态失败 (%s): %s", futures[fut], e)
        result.sort(key=lambda x: x["priority"])
        return result

    def _build_index_info(
        self,
        code: str,
        cfg: Dict[str, Any],
        value: float,
        close: float,
        percentile: float,
        decline_rate: float,
        status: str,
        absolute_triggered: bool,
        data_source: str,
        sorted_series: List[Dict],
        as_of: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """构建统一的指数状态字典，含 Day 1 检测和仓位分级。"""
        tier = cfg.get("tier", "drop")
        position_weight = cfg.get("position_weight", 0.0)
        today_str = as_of or (now or datetime.now()).strftime("%Y-%m-%d")

        index_info = {
            "fund_code": code,
            "index_name": cfg["name"],
            "guide_only": cfg.get("guide_only", False),
            "priority": cfg["priority"],
            "tier": tier,
            "position_weight": position_weight,
            "greed": round(value, 4),
            "prev_greed": round(float(sorted_series[-2].get("greed", 0)), 4) if len(sorted_series) >= 2 else None,
            "close": round(close, 4),
            "percentile": round(percentile, 1),
            "status": status,
            "decline_rate": round(decline_rate, 4),
            "absolute_triggered": absolute_triggered,
            "data_source": data_source,
            "signal_quality": cfg.get("signal_quality", "unknown"),
            "expected_15d": cfg.get("exp_15d"),
            "expected_20d": cfg.get("exp_20d"),
            # 入场 / 出场策略 (回测最优参数)
            "entry_strategy": _describe_entry_strategy(cfg),
            "exit_strategy": _describe_exit_strategy(cfg),
            "entry_offset": cfg.get("entry_offset", 0),
            "pit_greed": cfg.get("pit_greed"),
            "entry_greed": cfg.get("entry_greed"),
            "pit_pct": cfg.get("pit_pct"),
            "entry_pct": cfg.get("entry_pct"),
            "exit_full_pct": cfg.get("exit_full_pct"),
            "exit_half_pct": cfg.get("exit_half_pct"),
            "exit_fallback_days": cfg.get("exit_fallback_days"),
            "exit_mode": cfg.get("exit_mode"),
            "exit_down_days": cfg.get("exit_down_days"),
            # DCA 策略参数 (v5)
            "dca_strategy": cfg.get("dca_strategy", "uniform_10"),
            "dca_label": _strategy_label(cfg.get("dca_strategy", "uniform_10")),
            "dca_fallback": cfg.get("dca_fallback", 10),
            "trend_factors": cfg.get("trend_factors"),
            "position_multiplier": cfg.get("position_multiplier", 1.0),
            # 趋势标签 (DCA v5 展示用)
            "trend_label": "—",
            # Day 1 检测字段
            "p10_entry_date": None,
            "days_in_warning": 0,
            "is_fake_signal": False,
            # 趋势检测字段
            "trend": "declining",
            "days_rising": 0,
            "signal_trigger_greed": None,  # DCA二次信号检测用: 信号触发日的贪婪值
            "turning_point_confirmed": False,
            "turning_start_date": None,
            "last_change": 0.0,
            # 仓位分级
            "position_tier": None,
            "position_tier_label": None,
            # 退出信号
            "exit_signal": None,
            "exit_reason": "",
            # ETA
            "days_to_pit": None,
            "eta_date": None,
            "entry_date": None,
            "days_in_pit": None,
        }

        # ── Day 1 检测: 用全量序列固定阈值找首次穿越日 ──
        entry_pct = cfg.get("entry_pct", PERCENTILE_WARNING)
        fixed_entry = cfg.get("entry_greed") if cfg.get("use_fixed_greed") else None
        if sorted_series and len(sorted_series) >= 60:
            p10_entry_date, days_in_warning, is_first_cross = _detect_p10_entry(
                sorted_series, today_str, entry_pct=entry_pct,
                fixed_threshold=fixed_entry,
            )
            index_info["p10_entry_date"] = p10_entry_date
            index_info["days_in_warning"] = days_in_warning

            # 假信号检测: 曾破P10但已反弹, 且从未到P5
            if status == "normal" and p10_entry_date and days_in_warning <= FAKE_SIGNAL_REBOUND_DAYS:
                index_info["is_fake_signal"] = True

            # 二次信号检测用: 最近30天窗口内的最低贪婪值 (新低>5%触发重置)
            if sorted_series and len(sorted_series) >= 2:
                window_greeds = [float(s.get("greed", 0)) for s in sorted_series[-30:]]
                index_info["signal_trigger_greed"] = round(min(window_greeds), 4)

            # ── 趋势检测 + 仓位分级 ──
            if tier not in ("drop", "watch"):
                td = cfg.get("turning_days", TURNING_CONSECUTIVE_DAYS)
                trend = _detect_trend(sorted_series, turning_days=td)
                index_info["trend"] = trend["trend"]
                index_info["days_rising"] = trend["days_rising"]
                index_info["turning_point_confirmed"] = trend["turning_confirmed"]
                index_info["last_change"] = trend["last_change"]

                if trend["turning_confirmed"]:
                    # 拐点起始日 = 第一天的回升日期
                    if trend["days_rising"] < len(sorted_series):
                        idx_turn = len(sorted_series) - trend["days_rising"] - 1
                        index_info["turning_start_date"] = sorted_series[max(0, idx_turn)].get("date", "")
                    if trend["days_rising"] >= 4:
                        index_info["position_tier"] = "full"
                        index_info["position_tier_label"] = "强势上涨"
                    elif trend["days_rising"] >= 3:
                        index_info["position_tier"] = "accelerate"
                        index_info["position_tier_label"] = "趋势加速"
                    else:
                        index_info["position_tier"] = "turning"
                        index_info["position_tier_label"] = "拐点确认"
                else:
                    index_info["position_tier"] = "pre_turn"
                    index_info["position_tier_label"] = "跌势未止"

                # 计算当前趋势因子 (DCA v5 展示用)
                trend_factor = get_trend_factor(
                    trend=index_info.get("trend", "declining"),
                    days_rising=index_info.get("days_rising", 0),
                    fund_code=code,
                    current_greed=index_info.get("greed", 0.0),
                    entry_greed=index_info.get("entry_greed") or 999.0,
                )
                index_info["trend_factor"] = round(trend_factor, 2)
                index_info["trend_label"] = _trend_label(index_info.get("trend", "declining"), trend_factor)

            else:
                index_info["position_tier"] = None
                index_info["position_tier_label"] = "跳过 (不入金)"

            if tier != "defense_rotation":  # 防御轮动为永久持有模式，不因自身P分位单独止盈
                # ── 退出信号检测 (per-index 参数) ──
                exit_full_pct = cfg.get("exit_full_pct", 50)
                exit_half_pct = cfg.get("exit_half_pct", 30)
                exit_info = _detect_exit_signal(
                    sorted_series,
                    index_info["turning_point_confirmed"],
                    index_info["percentile"],
                    exit_full_pct=exit_full_pct,
                    exit_half_pct=exit_half_pct,
                    exit_down_days=cfg.get("exit_down_days", 0),
                    turn_started=bool(index_info.get("turning_start_date")),
                )
                index_info["exit_signal"] = exit_info["signal"]
                index_info["exit_reason"] = exit_info["reason"]

                # ── 兜底退出: 拐点确认后超过 exit_fallback_days 天，强制清仓 ──
                if (index_info["exit_signal"] is None
                        and index_info["turning_point_confirmed"]
                        and index_info["turning_start_date"]):
                    fallback = cfg.get("exit_fallback_days")
                    if fallback:
                        days_since_turn = _trading_days_between(
                            index_info["turning_start_date"], today_str
                        )
                        if days_since_turn >= fallback:
                            index_info["exit_signal"] = "fallback_exit"
                            index_info["exit_reason"] = (
                                f"拐点确认{index_info['turning_start_date']}后已过"
                                f"{days_since_turn}天≥{fallback}天兜底线，强制退出"
                            )

        # ── ETA 预测 (预警区 → 黄金坑) ──
        # 基准与状态判定一致: use_fixed_greed → 固定 pit_greed; 否则 → 滚动窗口 P(pit_pct)
        if status == "warning" and decline_rate > 0.0001:
            if cfg.get("use_fixed_greed") and cfg.get("pit_greed") is not None:
                pit_threshold = cfg["pit_greed"]
            else:
                pit_pct = cfg.get("pit_pct", PERCENTILE_GOLDEN_PIT)
                window_vals = [float(s.get("greed", 0)) for s in sorted_series]
                window_vals = window_vals[-PERCENTILE_WINDOW_DAYS:] if len(window_vals) > PERCENTILE_WINDOW_DAYS else window_vals
                window_sorted = sorted(window_vals)
                pit_threshold = window_sorted[min(int(len(window_sorted) * pit_pct / 100), len(window_sorted) - 1)] if window_sorted else None
            if pit_threshold is not None:
                gap = value - pit_threshold
                if gap > 0:
                    days_to = max(1, round(gap / decline_rate))
                    index_info["days_to_pit"] = days_to
                    index_info["eta_date"] = _add_trading_days(today_str, days_to)

        # ── 黄金坑入坑日期回测 ──
        # 用滚动窗口计算固定 P(pit_pct) 贪婪阈值，与 _calculate_percentile 逻辑一致。
        if status == "golden_pit" and sorted_series and len(sorted_series) >= 60:
            greeds = [float(s.get("greed", 0)) for s in sorted_series]
            dates = [s.get("date", "") for s in sorted_series]

            if cfg.get("use_fixed_greed") and cfg.get("pit_greed") is not None:
                pit_threshold = cfg["pit_greed"]
            else:
                pit_pct = cfg.get("pit_pct", PERCENTILE_GOLDEN_PIT)
                window_greeds = greeds[-PERCENTILE_WINDOW_DAYS:] if len(greeds) > PERCENTILE_WINDOW_DAYS else greeds
                all_sorted = sorted(window_greeds)
                threshold_idx = int(len(all_sorted) * pit_pct / 100)
                pit_threshold = all_sorted[min(threshold_idx, len(all_sorted) - 1)]

            # 从今天往前找：贪婪值 > 阈值 = 不在坑内，其后一天就是 Day 1
            entry_idx = 0  # 默认：全部历史数据都在坑内
            for i in range(len(greeds) - 1, -1, -1):
                if greeds[i] > pit_threshold:
                    entry_idx = i + 1
                    break
            if entry_idx < len(greeds):
                index_info["entry_date"] = dates[entry_idx]
                index_info["days_in_pit"] = len(greeds) - entry_idx

        # ?? ??????????????????????????? ? 500 ??????????
        fixed_pit = cfg.get("pit_greed") if cfg.get("use_fixed_greed") else None
        fixed_entry = cfg.get("entry_greed") if cfg.get("use_fixed_greed") else None
        index_info["pit_greed_threshold"] = (
            round(float(fixed_pit), 4) if fixed_pit is not None
            else self._rolling_percentile_value(sorted_series, cfg.get("pit_pct"), PERCENTILE_GOLDEN_PIT)
        )
        index_info["entry_greed_threshold"] = (
            round(float(fixed_entry), 4) if fixed_entry is not None
            else self._rolling_percentile_value(sorted_series, cfg.get("entry_pct"), PERCENTILE_WARNING)
        )
        index_info["exit_greed_threshold"] = self._rolling_percentile_value(
            sorted_series, cfg.get("exit_full_pct"), 50
        )

        return index_info

    @staticmethod
    def _rolling_percentile_value(
        sorted_series: List[Dict], pct: Optional[int], default_pct: int
    ) -> Optional[float]:
        """????? P(pct) ???????? _calculate_percentile/?????????

        use_fixed_greed=False ?????????/??/??????
        """
        if not sorted_series:
            return None
        vals = [float(s.get("greed", 0)) for s in sorted_series]
        if len(vals) > PERCENTILE_WINDOW_DAYS:
            vals = vals[-PERCENTILE_WINDOW_DAYS:]
        pct_val = pct if pct is not None else default_pct
        svals = sorted(vals)
        if not svals:
            return None
        idx = min(int(len(svals) * pct_val / 100), len(svals) - 1)
        return round(svals[idx], 4)

    @staticmethod
    def _arkvol_ref_lines(arkvol_series: List[Dict], cfg: Dict[str, Any]) -> tuple:
        """???????????? ArkVol ????????????

        ?????????GoldenPitSnapshot ???????? ArkVol ?????
        ??????????? P(pit_pct)/P(entry_pct)/P(exit_full_pct) ???????
        """
        pit = GoldenPitService._rolling_percentile_value(
            arkvol_series, cfg.get("pit_pct"), PERCENTILE_GOLDEN_PIT
        )
        entry = GoldenPitService._rolling_percentile_value(
            arkvol_series, cfg.get("entry_pct"), PERCENTILE_WARNING
        )
        exit_ = GoldenPitService._rolling_percentile_value(
            arkvol_series, cfg.get("exit_full_pct"), 50
        )
        return pit, entry, exit_

    def _detect_golden_pit_window(self, indices: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
        """检测黄金坑窗口。

        窗口定义:
          - waiting: 有指数在黄金坑/预警中，但拐点未确认 → 轻仓等待
          - buying:  至少一个指数拐点确认 → 窗口开启，加仓买入
          - idle:    无信号
        窗口从第一个指数拐点确认日开始，关闭条件是所有指数贪婪值回升到合理位置。
        """
        today_str = (now or datetime.now()).strftime("%Y-%m-%d")

        tradeable = [i for i in indices if i.get("tier") in ("core", "satellite", "defense")]

        turning = [i for i in tradeable if i.get("turning_point_confirmed")]
        signals = [i for i in tradeable if i["status"] in ("warning", "golden_pit")]

        pit_count = sum(1 for i in signals if i["status"] == "golden_pit")
        warning_count = sum(1 for i in signals if i["status"] == "warning")

        base = {
            "phase": "idle",
            "start_date": None,
            "exit_date": None,
            "midpoint_date": None,
            "leading_index": None,
            "leading_tier": None,
            "current_day": 0,
            "pit_count": pit_count,
            "warning_count": warning_count,
            "turning_count": len(turning),
            "resonance_multiplier": _compute_resonance(pit_count),
        }

        # 统一计算窗口起始日: 取所有信号中最早的 entry_date/eta_date
        # 保证 waiting→buying 阶段切换时 window_start 不变，DCA 日志不会丢失
        all_signals = signals + [t for t in turning if t not in signals]
        candidate_dates = []
        for s in all_signals:
            d = s.get("entry_date") or s.get("eta_date")
            if d:
                candidate_dates.append(d)
        window_start = min(candidate_dates) if candidate_dates else today_str
        current_day = _trading_days_between(window_start, today_str) + 1 if window_start else 1

        if turning:
            turning.sort(key=lambda x: x.get("turning_start_date") or "9999")
            leader = turning[0]
            turning_start = leader.get("turning_start_date")
            days_since_turning = _trading_days_between(turning_start, today_str) + 1 if turning_start else 0
            return {
                **base,
                "active": True,
                "phase": "buying",
                "start_date": window_start,
                "exit_date": _add_trading_days(window_start, PIT_WINDOW_DAYS),
                "midpoint_date": _add_trading_days(window_start, PIT_WINDOW_DAYS // 2),
                "turning_start_date": turning_start,
                "days_since_turning": max(1, days_since_turning),
                "leading_index": leader["index_name"],
                "leading_tier": leader.get("tier"),
                "current_day": max(1, current_day),
                "turning_leader_rising": leader.get("days_rising", 0),
            }
        elif signals:
            signals.sort(key=lambda x: x.get("p10_entry_date") or "9999")
            return {
                **base,
                "active": False,
                "phase": "waiting",
                "start_date": window_start,
                "exit_date": _add_trading_days(window_start, PIT_WINDOW_DAYS),
                "midpoint_date": _add_trading_days(window_start, PIT_WINDOW_DAYS // 2),
                "leading_index": signals[0]["index_name"],
                "leading_tier": signals[0].get("tier"),
                "current_day": max(1, current_day),
            }
        else:
            return {
                **base,
                "active": False,
                "phase": "idle",
            }

    # ═══════════════════════════════════════════════════════════════
    # Global macro overlay
    # ═══════════════════════════════════════════════════════════════

    def _parse_global_macro_overlay(self, gcf_data: Dict[str, Any]) -> Dict[str, Any]:
        """从 global-capital-flow 数据解析宏观叠加层。

        Returns:
            liquidity_gate, sentiment_score, sentiment_label,
            global_trend, global_macro_coefficient, summary
        """
        score = float(gcf_data.get("sentiment_score", 50))
        label = str(gcf_data.get("sentiment_label", "未知"))

        liquidity_gate = "closed" if score <= 20 else "open"

        if score <= 20:
            macro_coef = 0.0
        elif score <= 35:
            macro_coef = 0.5
        elif score <= 75:
            macro_coef = 1.0
        else:
            macro_coef = 0.8

        global_trend = self._compute_global_trend(gcf_data)
        capital_flow = self._compute_capital_flow_persistence(gcf_data)

        trend_labels = {"rising": "回升中", "declining": "下降中", "flat": "持平", "unknown": "未知"}
        summary = (
            f"全球风险偏好: {label}({score:.0f}), "
            f"闸门: {'关闭' if liquidity_gate == 'closed' else '开启'}, "
            f"趋势: {trend_labels.get(global_trend, '未知')}, "
            f"仓位系数: {macro_coef:.1f}x"
        )

        return {
            "liquidity_gate": liquidity_gate,
            "sentiment_score": score,
            "sentiment_label": label,
            "global_trend": global_trend,
            "global_macro_coefficient": macro_coef,
            "capital_flow": capital_flow,
            "summary": summary,
        }

    def _compute_global_trend(self, gcf_data: Dict[str, Any]) -> str:
        """从 GCF 数据计算全球风险偏好趋势方向。

        优先用 items 中各市场的 momentum_5 均值（已由 API 预计算），
        其次从 series 提取 A 股 share 占比序列推断趋势。
        """
        # 方案 1: items 中每个市场有 momentum_5 字段，取各市场均值
        items = gcf_data.get("items", [])
        if items:
            momentums = [
                it.get("momentum_5", 0) or 0
                for it in items
                if it.get("eligible")
            ]
            if momentums:
                avg = sum(momentums) / len(momentums)
                if avg > 0.02:
                    return "rising"
                elif avg < -0.02:
                    return "declining"
                else:
                    return "flat"

        # 方案 2: 从 series 提取 A 股 share 占比近 5 天趋势
        series_list = gcf_data.get("series", [])
        if series_list and len(series_list) >= 5:
            a_shares = []
            for item in series_list[-5:]:
                shares = item.get("shares", {})
                a_shares.append(float(shares.get("a_share", 0)))
            if len(a_shares) >= 3:
                rising = sum(1 for i in range(1, len(a_shares)) if a_shares[i] > a_shares[i - 1])
                declining = sum(1 for i in range(1, len(a_shares)) if a_shares[i] < a_shares[i - 1])
                if rising >= 3:
                    return "rising"
                elif declining >= 3:
                    return "declining"
                else:
                    return "flat"

        return "unknown"

    def _compute_capital_flow_persistence(
        self, gcf_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """从 series.shares 计算各市场资金持续流向。

        Returns:
            {markets: {id: {name, direction, consecutive_days, cumulative_pp}}, summary: str}
        """
        items = gcf_data.get("items", [])
        series_list = gcf_data.get("series", [])

        if not series_list or len(series_list) < 3:
            return {"markets": {}, "summary": ""}

        # 构建 market id → 显示名 映射
        name_map = {it["id"]: it.get("name", it["id"]) for it in items if it.get("id")}

        # 从 series 提取各市场每日 share 值
        dates_series = [
            (s.get("date", ""), s.get("shares", {}))
            for s in series_list
            if isinstance(s.get("shares"), dict)
        ]
        if len(dates_series) < 3:
            return {"markets": {}, "summary": ""}

        market_ids = list(dates_series[-1][1].keys())
        markets = {}

        for mid in market_ids:
            # 提取该市场最近 60 天的 share 序列
            shares_seq = [
                (d, float(shares.get(mid, 0)))
                for d, shares in dates_series[-60:]
            ]
            if len(shares_seq) < 3:
                continue

            # 从最新日开始向前，统计连续同向天数和累计变化
            latest_share = shares_seq[-1][1]
            direction = None
            consecutive_days = 0
            cumulative_pp = 0.0

            for i in range(len(shares_seq) - 1, 0, -1):
                curr = shares_seq[i][1]
                prev = shares_seq[i - 1][1]
                # 忽略 0 值
                if curr == 0 and prev == 0:
                    continue
                change = curr - prev

                day_direction = "inflow" if change > 0.0001 else ("outflow" if change < -0.0001 else "flat")

                if day_direction == "flat":
                    break

                if direction is None:
                    direction = day_direction

                if day_direction != direction:
                    break

                consecutive_days += 1
                cumulative_pp += change

            if direction is None:
                direction = "flat"

            direction_label = {"inflow": "流入", "outflow": "流出", "flat": "持平"}
            markets[mid] = {
                "name": name_map.get(mid, mid),
                "current_share": round(latest_share, 2),
                "direction": direction,
                "direction_label": direction_label.get(direction, "持平"),
                "consecutive_days": consecutive_days,
                "cumulative_pp": round(cumulative_pp, 2),
            }

        # 生成摘要: 先流出再流入
        outflows = [
            (mid, m) for mid, m in markets.items()
            if m["direction"] == "outflow" and m["consecutive_days"] >= 2
        ]
        inflows = [
            (mid, m) for mid, m in markets.items()
            if m["direction"] == "inflow" and m["consecutive_days"] >= 2
        ]
        outflows.sort(key=lambda x: x[1]["consecutive_days"], reverse=True)
        inflows.sort(key=lambda x: x[1]["consecutive_days"], reverse=True)

        parts = []
        for mid, m in outflows:
            parts.append(f"{m['name']}连续{m['consecutive_days']}日流出({m['cumulative_pp']:+.1f}pp)")
        for mid, m in inflows:
            parts.append(f"{m['name']}连续{m['consecutive_days']}日流入({m['cumulative_pp']:+.1f}pp)")

        # 构建份额变化曲线（最近 60 个交易日）
        share_history = []
        for d, shares in dates_series[-60:]:
            entry = {"date": d}
            for mid, val in shares.items():
                entry[mid] = round(float(val), 2)
            share_history.append(entry)

        return {
            "markets": markets,
            "summary": "; ".join(parts) if parts else "",
            "share_history": share_history,
        }

    def _apply_global_macro_to_indices(
        self, indices: List[Dict[str, Any]], global_macro: Dict[str, Any]
    ) -> None:
        """将全球宏观数据应用到各指数: 拐点验证 + 宏观退出信号。"""
        global_trend = global_macro.get("global_trend", "unknown")
        global_score = global_macro.get("sentiment_score", 50)

        for idx in indices:
            turning_confirmed = idx.get("turning_point_confirmed", False)

            # ── 拐点验证: 全球趋势背离时 cap 仓位 ──
            if turning_confirmed and global_trend in ("declining", "flat", "unknown"):
                trend_cn = {"rising": "回升中", "declining": "下降中", "flat": "持平", "unknown": "未知"}.get(global_trend, global_trend)
                idx["turning_validation"] = "divergent"
                idx["turning_validation_reason"] = (
                    f"全球风险偏好趋势{trend_cn}，"
                    f"A股拐点可能为假信号，仓位限制在拐点前水平"
                )
                if idx.get("position_tier") not in (None, "pre_turn"):
                    idx["position_tier"] = "pre_turn"
                    idx["position_tier_label"] = "全球背离 · 暂缓建仓"
            elif turning_confirmed:
                idx["turning_validation"] = "validated"

            # ── 宏观退出: 全球极度贪婪 → 提前止盈 ──
            if turning_confirmed and global_score > 80:
                existing = idx.get("exit_signal")
                if existing not in ("full_exit", "half_exit"):
                    idx["exit_signal"] = "half_exit"
                    idx["exit_reason"] = (
                        f"全球风险偏好极度贪婪({global_score:.0f})，建议减持50%"
                    )

    def _compute_triple_confirmation(
        self, indices: List[Dict[str, Any]],
        gcf_data: Optional[Dict[str, Any]] = None,
        tech_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """三重确认状态。可传入预取的 API 数据避免重复请求。"""
        # Layer 1: 蛋糕理论 — 全球资金流向
        layer1 = {"label": "蛋糕理论", "status": "未知", "confirmed": False}
        try:
            gcf = gcf_data or {}
            score = gcf.get("sentiment_score")
            if score is not None:
                score = float(score)
                if score < 30:
                    layer1 = {"label": "蛋糕理论", "status": "A股资金外流处历史低位", "confirmed": True}
                else:
                    layer1 = {"label": "蛋糕理论", "status": f"资金外流未到底 (score={score})", "confirmed": False}
        except Exception as e:
            layer1 = {"label": "蛋糕理论", "status": f"数据不可用: {e}", "confirmed": False}

        # Layer 2: 宽基贪婪
        pit_names = [i["index_name"] for i in indices if i["status"] == "golden_pit"]
        warning_names = [i["index_name"] for i in indices if i["status"] == "warning"]
        double_confirm = [i["index_name"] for i in indices if i["status"] == "golden_pit" and i.get("absolute_triggered")]
        layer2_confirmed = len(pit_names) > 0
        layer2_status = f"{len(pit_names)}个在黄金坑" if pit_names else f"{len(warning_names)}个预警"
        if pit_names:
            layer2_status += f" ({', '.join(pit_names)})"
        if double_confirm:
            layer2_status += f" | 双重确认: {', '.join(double_confirm)}"
        layer2 = {
            "label": "宽基贪婪",
            "status": layer2_status,
            "confirmed": layer2_confirmed,
            "details": [f"{i['index_name']}: {i['status']}" for i in sorted(indices, key=lambda x: x["priority"])],
        }

        # Layer 3: 细分板块
        layer3 = {"label": "细分板块", "status": "未知", "confirmed": False}
        try:
            tech = tech_data or {}
            items = tech.get("items", [])
            if items:
                extreme_sectors = []
                for item in items:
                    greed = item.get("greed")
                    name = item.get("etf_name") or item.get("index_name") or item.get("name", "")
                    if greed is not None and float(greed) < GREED_ABSOLUTE_PIT:
                        extreme_sectors.append(name)
                if extreme_sectors:
                    layer3 = {"label": "细分板块", "status": f"{len(extreme_sectors)}个板块已入黄金坑", "confirmed": True}
                else:
                    layer3 = {"label": "细分板块", "status": "暂未触发", "confirmed": False}
        except Exception as e:
            layer3 = {"label": "细分板块", "status": f"数据不可用: {e}", "confirmed": False}

        return {"layer1": layer1, "layer2": layer2, "layer3": layer3}

    def _predict_next_entry(self, indices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """预测下一个进入黄金坑的指数。"""
        candidates = [
            i for i in indices
            if i["status"] != "golden_pit" and i.get("days_to_pit") is not None
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x.get("days_to_pit", 999))
        next_idx = candidates[0]
        return {
            "next_index": next_idx["index_name"],
            "eta_days": next_idx["days_to_pit"],
            "eta_date": next_idx["eta_date"],
            "decline_rate": next_idx["decline_rate"],
        }

    def _build_v2_summary(
        self,
        indices: List[Dict[str, Any]],
        window: Dict[str, Any],
        confirmation: Dict[str, Any],
        prediction: Optional[Dict[str, Any]],
    ) -> str:
        """生成 v2 自然语言解读。"""
        parts = []

        pit_indices = [i for i in indices if i["status"] == "golden_pit"]
        warning_indices = [i for i in indices if i["status"] == "warning"]

        phase = window.get("phase", "idle")
        if phase == "buying":
            rising = window.get("turning_leader_rising", 0)
            parts.append(f"买入窗口已开启: {window['leading_index']}拐点确认，{window['start_date']}起第{window['current_day']}天。")
            parts.append(f"拐点确认{window['turning_count']}个指数，已回升{rising}天，加仓节奏50%→75%→100%。")
            strong = [i["index_name"] for i in pit_indices if i.get("signal_quality") == "strong"]
            if strong:
                parts.append(f"强信号: {', '.join(strong)}（回测Win%≥80%），优先加仓。")
        elif phase == "waiting":
            parts.append(f"黄金坑信号：{window['pit_count']}个指数已入坑/{window['warning_count']}个预警，但贪婪值仍在下跌中。")
            parts.append("黄金坑≠买入窗口。需等待贪婪值连续回升（拐点确认）后，才会开启买入窗口。当前仅轻仓累积(单次≤3%/累计≤15%)。")
        else:
            parts.append("当前无黄金坑信号，各宽基指数情绪正常。")

        if prediction and prediction.get("next_index"):
            parts.append(f"预测: {prediction['next_index']} 预计 {prediction['eta_days']} 天后进入黄金坑。")

        layers_ok = sum(1 for k in ["layer1", "layer2", "layer3"] if confirmation[k]["confirmed"])
        if layers_ok == 3:
            parts.append("三重确认全部达成，黄金坑信号高度可靠。")
        elif layers_ok >= 2:
            parts.append(f"三重确认达成{layers_ok}/3，信号可靠性中等。")

        return "".join(parts)

    # ═══════════════════════════════════════════════════════════════
    # 向后兼容 v1 API
    # ═══════════════════════════════════════════════════════════════

    def get_score(self) -> Dict[str, Any]:
        """v1 兼容: 返回简化综合评分。"""
        status = self.get_status()
        indices = status["indices"]
        # 将 per-index 状态转为综合评分: 按最差状态 + 最低 percentile 转换
        # 只统计可交易指数 (core/satellite/defense)，排除 drop/watch
        tradeable = [i for i in indices if i.get("tier") in ("core", "satellite", "defense")]
        pit_count = sum(1 for i in tradeable if i["status"] == "golden_pit")
        warn_count = sum(1 for i in tradeable if i["status"] == "warning")
        min_pct = min((i["percentile"] for i in tradeable), default=50.0)
        # 评分 = 100 - 最低分位，最低分位越低分数越高
        inverted = max(0, min(100, round(100 - min_pct, 1)))

        factors = [
            {
                "key": "per_index",
                "name": "宽基指数追踪",
                "weight": 1.0,
                "description": "逐指数贪婪值追踪",
                "raw": f"{pit_count}在坑/{warn_count}预警",
                "raw_label": f"{pit_count}在坑/{warn_count}预警",
                "score": round(inverted, 1),
                "weighted": round(inverted, 1),
            }
        ]

        # 按信号最强的指数的信号质量确定颜色深度
        strong_pit = sum(1 for i in tradeable if i["status"] == "golden_pit" and i.get("signal_quality") == "strong")
        double_confirmed = sum(1 for i in tradeable if i["status"] == "golden_pit" and i.get("absolute_triggered"))

        return {
            "score": round(inverted, 1),
            "level": "golden_pit" if pit_count > 0 else ("alert" if warn_count > 0 else "normal"),
            "level_label": (
                f"黄金坑区域 ({pit_count}个指数, {double_confirmed}个双重确认)"
                if pit_count > 0
                else ("预警区域" if warn_count > 0 else "正常区域")
            ),
            "level_color": (
                "#dc2626" if double_confirmed > 0
                else ("#ef4444" if pit_count > 0
                      else ("#f97316" if warn_count > 0 else "#22c55e"))
            ),
            "as_of": status["as_of"],
            "factors": factors,
            "summary": status["summary"],
            "errors": None,
        }

    def get_factors(self) -> Dict[str, Any]:
        """v1 兼容: 返回因子明细。"""
        score = self.get_score()
        return {"as_of": score["as_of"], "factors": score["factors"]}


# ── 进程级单例 ──
_service_singleton: Optional["GoldenPitService"] = None


def get_golden_pit_service() -> "GoldenPitService":
    """返回进程级单例，供 API/DCA/调度器共享 TTL 缓存，
    避免 ArkVol 请求跨任务重复拉取。
    """
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = GoldenPitService()
    return _service_singleton
