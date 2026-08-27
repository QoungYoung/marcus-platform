# -*- coding: utf-8 -*-
"""黄金坑数据库存取 — 快照历史、同步与保存。"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from app.services.golden_pit_config import ALL_INDEX_CONFIGS, CHINA_INDICES
from app.services.golden_pit_indicators import _price_based_greed


def get_history(index: str = "all", days: int = 60) -> Dict[str, Any]:
    """从 DB 快照表获取历史贪婪值趋势数据，用于前端折线图。"""
    from app.database import SessionLocal
    from app.models.golden_pit import GoldenPitSnapshot

    db = SessionLocal()
    try:
        result_series: Dict[str, List[Dict]] = {}
        result_indices: Dict[str, str] = {}

        codes = [code for code in ALL_INDEX_CONFIGS if index == "all" or code == index]
        if not codes:
            return {
                "as_of": datetime.now().strftime("%Y-%m-%d"),
                "series": result_series,
                "indices": result_indices,
            }

        # 一次查询所有指数历史，内存分组
        rows = (
            db.query(GoldenPitSnapshot)
            .filter(GoldenPitSnapshot.fund_code.in_(codes))
            .order_by(GoldenPitSnapshot.date.asc())
            .all()
        )
        grouped: Dict[str, List[Any]] = {}
        for r in rows:
            grouped.setdefault(r.fund_code, []).append(r)

        for code in codes:
            code_rows = grouped.get(code)
            if not code_rows:
                continue
            series = [
                {
                    "date": r.date,
                    "greed": round(r.greed_value, 4),
                    "close": round(r.close_price, 4) if r.close_price else 0,
                }
                for r in code_rows
            ]
            result_series[code] = series[-days:] if len(series) > days else series
            result_indices[code] = ALL_INDEX_CONFIGS[code]["name"]

        latest_dates = {r["date"] for s in result_series.values() for r in s}
        as_of = max(latest_dates) if latest_dates else datetime.now().strftime("%Y-%m-%d")

        return {
            "as_of": as_of,
            "series": result_series,
            "indices": result_indices,
        }
    finally:
        db.close()


def get_snapshots(days: int = 30) -> List[Dict[str, Any]]:
    """从数据库读取历史快照。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit import GoldenPitSnapshot

        db = SessionLocal()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = (
                db.query(GoldenPitSnapshot)
                .filter(GoldenPitSnapshot.date >= cutoff)
                .order_by(GoldenPitSnapshot.date.asc(), GoldenPitSnapshot.fund_code.asc())
                .all()
            )
            return [
                {
                    "date": r.date,
                    "fund_code": r.fund_code,
                    "index_name": r.index_name,
                    "greed_value": r.greed_value,
                    "close_price": r.close_price,
                    "percentile": r.percentile,
                    "status": r.status,
                    "decline_rate_5d": r.decline_rate_5d,
                }
                for r in rows
            ]
        finally:
            db.close()
    except Exception as e:
        logger.warning("读取黄金坑快照失败: %s", e)
        return []


def load_previous_percentile(as_of: Optional[str] = None) -> Dict[str, float]:
    """从数据库加载最近一个交易日（今天之前）的分位值。

    以最近存在的快照日期为对比基准，避免周一/节后首个交易日
    按自然日取昨天/前天导致无快照、穿越预警静默丢失。
    """
    try:
        from app.database import SessionLocal
        from app.models.golden_pit import GoldenPitSnapshot

        db = SessionLocal()
        try:
            today = as_of or datetime.now().strftime("%Y-%m-%d")
            latest_date_row = (
                db.query(GoldenPitSnapshot.date)
                .filter(GoldenPitSnapshot.date < today)
                .order_by(GoldenPitSnapshot.date.desc())
                .first()
            )
            if not latest_date_row:
                return {}
            latest_date = latest_date_row[0]
            rows = (
                db.query(GoldenPitSnapshot)
                .filter(GoldenPitSnapshot.date == latest_date)
                .all()
            )
            return {r.fund_code: (r.percentile or 50.0) for r in rows}
        finally:
            db.close()
    except Exception:
        return {}


def sync_extra_series_to_db(service) -> int:
    """同步防御组合（per-fund series）与半导体增强（tech-hardware-greed）历史序列。

    防御标的以 ArkVol 贪婪作展示/快照；半导体增强以 tech-hardware-greed 贪婪为准。
    """
    try:
        from app.database import SessionLocal
        from app.models.golden_pit import GoldenPitSnapshot
        from app.services.golden_pit_config import DEFENSE_INDICES, SEMI_BOOST_INDICES

        pending: List[tuple] = []  # (fund_code, index_name, rows)

        # 半导体增强: tech-hardware-greed（588200/512480）
        try:
            tech = service._cached_tech_greed()
            data = tech.get("data", {}) or {}
            for code, cfg in SEMI_BOOST_INDICES.items():
                rows = data.get(cfg.get("arkvol_code", code), [])
                if rows:
                    pending.append((code, cfg["name"], rows))
        except Exception as e:
            logger.warning("同步半导体增强序列失败: %s", e)

        # 防御组合: per-fund series（ArkVol 贪婪展示用）
        for code, cfg in DEFENSE_INDICES.items():
            arkvol_code = cfg.get("arkvol_code", "")
            if arkvol_code:
                try:
                    series = service._cached_fund_series(arkvol_code)
                    if series:
                        pending.append((code, cfg["name"], series))
                except Exception as e:
                    logger.warning("同步防御标的 %s 序列失败: %s", code, e)
                continue
            # 无 arkvol_code 的 defense_price 标的（如 515080 中证红利）：
            # 用 Tushare ETF 日K线合成价格贪婪历史，保证 DB 历史 ≥60 天，
            # 否则 _get_status_from_db 判空会整体回退 API（历史遗留修复）
            if cfg.get("data_source") != "defense_price":
                continue
            etf_code = cfg.get("etf_code", "")
            if not etf_code:
                continue
            try:
                bars = service._cached_tushare_kline(etf_code, limit=500)
                if not bars:
                    continue
                closes = [float(b.get("close", 0)) for b in bars]
                rows = []
                for i, b in enumerate(bars):
                    window = closes[:i + 1]
                    if len(window) < 2 or not window[-1]:
                        continue
                    rows.append({
                        "date": b.get("date", ""),
                        "greed": _price_based_greed(window[-1], window),
                        "close": window[-1],
                    })
                if rows:
                    pending.append((code, cfg["name"], rows))
            except Exception as e:
                logger.warning("同步防御标的 %s 价格历史失败: %s", code, e)

        if not pending:
            return 0

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db = SessionLocal()
        total_inserted = 0
        try:
            all_dates = set()
            for _, _, rows in pending:
                all_dates.update(r.get("date", "") for r in rows if r.get("date"))

            existing = set()
            for row in (
                db.query(GoldenPitSnapshot.fund_code, GoldenPitSnapshot.date)
                .filter(
                    GoldenPitSnapshot.fund_code.in_([c for c, _, _ in pending]),
                    GoldenPitSnapshot.date.in_(all_dates),
                )
                .all()
            ):
                existing.add((row[0], row[1]))

            for fund_code, index_name, rows in pending:
                sorted_rows = sorted(rows, key=lambda x: x.get("date", ""))
                greeds = [float(r.get("greed", 0)) for r in sorted_rows]
                new_rows = []
                for i, row in enumerate(sorted_rows):
                    date = row.get("date", "")
                    if not date or (fund_code, date) in existing:
                        continue
                    window = greeds[:i + 1][-500:]
                    pct = None
                    if len(window) >= 2:
                        cur = window[-1]
                        pct = round(sum(1 for g in window if g < cur) / len(window) * 100, 1)
                    new_rows.append({
                        "date": date,
                        "fund_code": fund_code,
                        "index_name": index_name,
                        "greed_value": float(row.get("greed", 0)),
                        "close_price": float(row.get("close", 0)) if row.get("close") else None,
                        "percentile": pct,
                        "status": "normal",
                        "created_at": now,
                    })
                if new_rows:
                    db.execute(GoldenPitSnapshot.__table__.insert(), new_rows)
                    total_inserted += len(new_rows)
            db.commit()
            logger.info("防御/半导体序列已同步: %d 条新增", total_inserted)
        finally:
            db.close()
        return total_inserted
    except Exception as e:
        logger.error("同步防御/半导体序列失败: %s", e)
        return 0


def sync_full_series_to_db(arkvol, arkvol_map: Dict[str, str]) -> int:
    """从 ArkVol 全量接口拉取历史序列，批量写入 GoldenPitSnapshot。

    每日盘前调用，确保 DB 中始终有完整历史（日频更新）。查询已有的
    (date, fund_code) 对去重，只插入新记录。
    """
    try:
        from app.database import SessionLocal
        from app.models.golden_pit import GoldenPitSnapshot

        full_data = arkvol.fetch_full_series()
        series_data = full_data.get("data", {})
        if not series_data:
            logger.warning("ArkVol 全量 series 返回空数据")
            return 0

        arkvol_map = arkvol_map
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        db = SessionLocal()
        total_inserted = 0
        try:
            # 先收集所有待写入项，再一次 IN 查询已存在 (fund_code, date) 对
            pending: List[tuple] = []
            all_dates = set()
            for arkvol_code, rows in series_data.items():
                config_key = arkvol_map.get(arkvol_code)
                if config_key is None:
                    continue
                dates = [r.get("date", "") for r in rows if r.get("date")]
                if not dates:
                    continue
                pending.append((config_key, CHINA_INDICES[config_key]["name"], rows))
                all_dates.update(dates)

            existing = set()
            if pending:
                for row in (
                    db.query(GoldenPitSnapshot.fund_code, GoldenPitSnapshot.date)
                    .filter(
                        GoldenPitSnapshot.fund_code.in_([c for c, _, _ in pending]),
                        GoldenPitSnapshot.date.in_(all_dates),
                    )
                    .all()
                ):
                    existing.add((row[0], row[1]))

            for config_key, index_name, rows in pending:
                new_rows = []
                for row in rows:
                    date = row.get("date", "")
                    if not date or (config_key, date) in existing:
                        continue
                    new_rows.append({
                        "date": date,
                        "fund_code": config_key,
                        "index_name": index_name,
                        "greed_value": float(row.get("greed", 0)),
                        "close_price": float(row.get("close", 0)) if row.get("close") else None,
                        "status": "normal",
                        "created_at": now,
                    })

                if new_rows:
                    db.execute(
                        GoldenPitSnapshot.__table__.insert(),
                        new_rows,
                    )
                    total_inserted += len(new_rows)

            db.commit()
            logger.info("全量历史序列已同步: %d 条新增, %d 个指数",
                        total_inserted, len(series_data))
        finally:
            db.close()
        return total_inserted
    except Exception as e:
        logger.error("同步全量历史序列失败: %s", e)
        return 0


def save_daily_snapshot(service, now: Optional[datetime] = None) -> List[Any]:
    """保存每日快照到数据库。先同步全量历史序列，再写入当天状态快照。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit import GoldenPitSnapshot

        # 清除缓存，确保盘前同步拿到最新 API 数据
        service._cache.pop("ai-summary", None)
        service._cache.pop("global-capital-flow", None)
        service._cache.pop("alla-tech", None)
        service._cache.pop("tech-hardware-greed", None)
        from app.services.golden_pit_config import DEFENSE_INDICES
        for _code, _cfg in DEFENSE_INDICES.items():
            service._cache.pop(f"fund-series:{_cfg.get('arkvol_code', '')}", None)
        service._kline_cache.clear()

        # 1. 同步全量历史序列 + 防御/半导体序列（日频更新，增量写入）
        sync_full_series_to_db(service._arkvol, service._arkvol_code_map())
        sync_extra_series_to_db(service)

        # 2. 写入当天带状态/百分位的快照
        status = service._get_status_from_api()
        now_dt = now or datetime.now()
        today = status["as_of"] or now_dt.strftime("%Y-%m-%d")
        now = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        db = SessionLocal()
        snapshots = []
        try:
            # 删除当天已有记录，避免重复（幂等）
            db.query(GoldenPitSnapshot).filter(
                GoldenPitSnapshot.date == today
            ).delete()

            for idx in status["indices"]:
                snap = GoldenPitSnapshot(
                    date=today,
                    fund_code=idx["fund_code"],
                    index_name=idx["index_name"],
                    greed_value=idx["greed"],
                    close_price=idx.get("close"),
                    percentile=idx.get("percentile"),
                    status=idx["status"],
                    decline_rate_5d=idx.get("decline_rate"),
                    change_5=idx.get("change_5"),
                    change_20=idx.get("change_20"),
                    created_at=now,
                )
                db.add(snap)
                snapshots.append(snap)
            db.commit()
            logger.info("黄金坑快照已保存: %s, %d 条", today, len(snapshots))
        finally:
            db.close()
        return snapshots
    except Exception as e:
        logger.error("保存黄金坑快照失败: %s", e)
        return []

# ═══════════════════════════════════════════════════════════════
# QQ 报告与预警
# ═══════════════════════════════════════════════════════════════
