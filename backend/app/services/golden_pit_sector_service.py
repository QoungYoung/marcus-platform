# -*- coding: utf-8 -*-
"""黄金坑板块拆分选筹服务。

在 guide_only 宽基（588000/159915）确认入坑后，从 SECTOR_ETF_POOL 中按
combo 信号（超跌 oversold120 + 中信二级 5 日资金流 mf5_norm）动态选筹，
输出结构化板块组合供 DCA 执行 / 状态展示 / 报告复用。

信号口径与回测 scripts/backtest_golden_pit_sector_moneyflow.py 一致:
  - mf5_norm    = 近5日板块累计净流入 / 近20日平均|日净流入| (资金强度)
  - oversold120 = 距120日高点回撤 (反转基准)
  - combo       = -(rank(mf5_norm, 降序) + rank(oversold120, 升序))
  - 要求 mf5_norm > 0（资金逆势流入）且 oversold120 < 0（超跌中）
选 TOP N 板块，按 combo 分数归一化权重，单板块权重上限截断。

配置来源: 默认值来自 .env（golden_pit_config 模块级常量），运行时覆盖来自
PostgreSQL golden_pit_sector_config 表（黄金坑页面配置弹窗）。
"""
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from app.services.golden_pit_config import (
    COMBO_W_MF,
    COMBO_W_OVS,
    GOLDEN_PIT_SECTOR_SPLIT_ENABLED,
    SECTOR_ETF_POOL,
    SECTOR_EXIT_DOWN_DAYS,
    SECTOR_MAX_WEIGHT,
    SECTOR_MF_DAYS,
    SECTOR_MF_MA_DAYS,
    SECTOR_MIN_VALID,
    SECTOR_OVS_DAYS,
    SECTOR_POOL_SOURCE,
    SECTOR_SIGNAL_MODE,
    SECTOR_TOP_N,
    TECH_SECTOR_POOL,
)

logger = logging.getLogger(__name__)

DATA_ROOT = Path(__file__).parent.parent.parent.parent / "data" / "backtest" / "股票数据"
INDUSTRY_FLOW_FILE = DATA_ROOT / "资金流向数据" / "moneyflow_ind_dc.parquet"

# 模块级 TTL 缓存: 资金流(2h) / K线(2h) / 选筹结果(15min)
_cache: Dict[str, Any] = {}


def _cache_get(key: str, ttl: int) -> Any:
    item = _cache.get(key)
    if item and time.time() - item[0] < ttl:
        return item[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


# ═══════════════════════════════════════════════════════════
# 配置读写（PostgreSQL golden_pit_sector_config 表）
# ═══════════════════════════════════════════════════════════

SECTOR_CONFIG_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "enabled": {
        "label": "板块拆分启用",
        "description": "false=dry-run 展示选筹；true=板块 ETF 下单（588000/159915 不再直接买入）",
        "value_type": "bool", "sort_order": 1, "default": GOLDEN_PIT_SECTOR_SPLIT_ENABLED,
    },
    "top_n": {
        "label": "选筹 TOP N", "description": "坑内选筹板块数量",
        "value_type": "number", "sort_order": 2, "default": SECTOR_TOP_N,
    },
    "max_weight": {
        "label": "单板块权重上限", "description": "归一化后单板块权重上限（0~1）",
        "value_type": "number", "sort_order": 3, "default": SECTOR_MAX_WEIGHT,
    },
    "combo_w_ovs": {
        "label": "combo 超跌权重", "description": "combo 超跌分权重（保留字段）",
        "value_type": "number", "sort_order": 4, "default": COMBO_W_OVS,
    },
    "combo_w_mf": {
        "label": "combo 资金流权重", "description": "combo 资金流分权重（保留字段）",
        "value_type": "number", "sort_order": 5, "default": COMBO_W_MF,
    },
    "ovs_days": {
        "label": "超跌窗口(日)", "description": "距N日高点回撤窗口",
        "value_type": "number", "sort_order": 6, "default": SECTOR_OVS_DAYS,
    },
    "mf_days": {
        "label": "资金流累计窗口(日)", "description": "近N日累计净流入",
        "value_type": "number", "sort_order": 7, "default": SECTOR_MF_DAYS,
    },
    "mf_ma_days": {
        "label": "资金流均值窗口(日)", "description": "近N日平均|日净流入|",
        "value_type": "number", "sort_order": 8, "default": SECTOR_MF_MA_DAYS,
    },
    "min_valid": {
        "label": "有效信号板块数下限", "description": "有效信号不足则空仓等待板块信号",
        "value_type": "number", "sort_order": 9, "default": SECTOR_MIN_VALID,
    },
    "exit_down_days": {
        "label": "板块退出回落天数", "description": "板块ETF二次拐点: 连续回落天数",
        "value_type": "number", "sort_order": 10, "default": SECTOR_EXIT_DOWN_DAYS,
    },
    "signal_mode": {
        "label": "板块信号模式", "description": "greed=超跌+板块贪婪；moneyflow=超跌+资金流（回滚）",
        "value_type": "string", "sort_order": 11, "default": SECTOR_SIGNAL_MODE,
    },
    "pool_source": {
        "label": "板块选筹池", "description": "tech7=7只场内科技ETF(tech-hardware贪婪,默认)；prod10=原10板块(funds-greed,回滚)",
        "value_type": "string", "sort_order": 12, "default": SECTOR_POOL_SOURCE,
    },
}


def _load_sector_config_rows() -> List[Dict[str, Any]]:
    """从 DB 读取配置行（空表返回空列表；DB 不可用时返回空）。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_sector_config import GoldenPitSectorConfig

        db = SessionLocal()
        try:
            rows = (
                db.query(GoldenPitSectorConfig)
                .order_by(GoldenPitSectorConfig.sort_order)
                .all()
            )
            return [
                {
                    "config_key": r.config_key,
                    "config_value": r.config_value or "",
                    "label": r.label,
                    "description": r.description or "",
                    "value_type": r.value_type,
                    "sort_order": r.sort_order,
                }
                for r in rows
            ]
        finally:
            db.close()
    except Exception as e:
        logger.warning("读取板块拆分配置失败: %s", e)
        return []


def _seed_sector_config_defaults() -> None:
    """首次使用时将默认配置写入 DB（仅补缺失项）。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_sector_config import GoldenPitSectorConfig

        db = SessionLocal()
        try:
            existing = {r.config_key for r in db.query(GoldenPitSectorConfig).all()}
            for key, meta in SECTOR_CONFIG_DEFAULTS.items():
                if key in existing:
                    continue
                db.add(GoldenPitSectorConfig(
                    config_key=key,
                    config_value=str(meta["default"]),
                    label=meta["label"],
                    description=meta.get("description"),
                    value_type=meta["value_type"],
                    sort_order=meta["sort_order"],
                ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("seed 板块拆分配置失败: %s", e)


def get_sector_config() -> Dict[str, Any]:
    """读取板块拆分配置（DB 优先，缺失项回退默认值）。带 60s 缓存。"""
    cached = _cache_get("sector_config", 60)
    if cached is not None:
        return cached

    rows = _load_sector_config_rows()
    if not rows:
        _seed_sector_config_defaults()
        rows = _load_sector_config_rows()

    cfg: Dict[str, Any] = {}
    by_key = {r["config_key"]: r for r in rows}
    for key, meta in SECTOR_CONFIG_DEFAULTS.items():
        row = by_key.get(key)
        default = meta["default"]
        if row is None:
            cfg[key] = default
            continue
        raw = row.get("config_value", "")
        vtype = row.get("value_type") or meta["value_type"]
        try:
            if vtype == "bool":
                cfg[key] = str(raw).strip().lower() in ("1", "true", "yes", "on")
            elif vtype == "string":
                cfg[key] = str(raw).strip()
            else:
                cfg[key] = float(raw)
        except (ValueError, TypeError):
            cfg[key] = default
    _cache_set("sector_config", cfg)
    return cfg


def list_sector_config() -> List[Dict[str, Any]]:
    """返回配置项列表（含 value/label/description/value_type/sort_order），供页面弹窗。"""
    cfg = get_sector_config()
    rows = _load_sector_config_rows()
    by_key = {r["config_key"]: r for r in rows}
    items = []
    for key, meta in SECTOR_CONFIG_DEFAULTS.items():
        r = by_key.get(key, {})
        items.append({
            "config_key": key,
            "value": cfg.get(key, meta["default"]),
            "label": r.get("label") or meta["label"],
            "description": r.get("description") or meta.get("description", ""),
            "value_type": r.get("value_type") or meta["value_type"],
            "sort_order": r.get("sort_order") or meta["sort_order"],
        })
    return sorted(items, key=lambda x: x["sort_order"])


def update_sector_config(values: Dict[str, Any]) -> Dict[str, Any]:
    """批量更新板块拆分配置（类型校验），返回更新后的完整配置。"""
    _seed_sector_config_defaults()
    from app.database import SessionLocal
    from app.models.golden_pit_sector_config import GoldenPitSectorConfig

    db = SessionLocal()
    try:
        rows = {r.config_key: r for r in db.query(GoldenPitSectorConfig).all()}
        for key, raw in (values or {}).items():
            meta = SECTOR_CONFIG_DEFAULTS.get(key)
            if meta is None:
                raise ValueError(f"未知配置项: {key}")
            row = rows.get(key)
            if row is None:
                row = GoldenPitSectorConfig(
                    config_key=key, label=meta["label"],
                    description=meta.get("description"),
                    value_type=meta["value_type"], sort_order=meta["sort_order"],
                )
                db.add(row)
            vtype = meta["value_type"]
            if vtype == "bool":
                val = bool(raw) if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "yes", "on")
                row.config_value = "true" if val else "false"
            elif vtype == "string":
                val = str(raw).strip()
                row.config_value = val
            else:
                val = float(raw)
                row.config_value = str(val)
        db.commit()
    finally:
        db.close()
    _invalidate_selection_cache()
    return get_sector_config()


def _invalidate_selection_cache() -> None:
    """配置变更后失效选筹与配置缓存（保留资金流/K线缓存）。"""
    for k in list(_cache.keys()):
        if k.startswith("selection:") or k == "sector_config":
            _cache.pop(k, None)


def _load_industry_flow_df() -> pd.DataFrame:
    """加载中信二级行业资金流（moneyflow_ind_dc），仅保留 行业 类型。"""
    cached = _cache_get("industry_flow_df", 7200)
    if cached is not None:
        return cached
    if not INDUSTRY_FLOW_FILE.exists():
        logger.warning("板块资金流数据缺失: %s", INDUSTRY_FLOW_FILE)
        return pd.DataFrame()
    try:
        df = pd.read_parquet(INDUSTRY_FLOW_FILE)
        if "content_type" in df.columns:
            df = df[df["content_type"] == "行业"]
        if "name" not in df.columns or "net_amount" not in df.columns:
            logger.warning("moneyflow_ind_dc 缺少 name/net_amount 列")
            return pd.DataFrame()
        _cache_set("industry_flow_df", df)
        return df
    except Exception as e:
        logger.warning("读取 moneyflow_ind_dc 失败: %s", e)
        return pd.DataFrame()


def _flow_series(df: pd.DataFrame, flow_name: str, as_of: str) -> Dict[str, float]:
    """取某行业名的日净流入序列（date → net_amount），截至 as_of。"""
    if df.empty or "trade_date" not in df.index.names:
        return {}
    sub = df[df["name"] == flow_name]
    if sub.empty:
        return {}
    as_of_ts = pd.Timestamp(as_of)
    mask = sub.index.get_level_values("trade_date") <= as_of_ts
    sub = sub[mask]
    if sub.empty:
        return {}
    series = sub.groupby(level="trade_date")["net_amount"].sum()
    return {pd.Timestamp(d).strftime("%Y-%m-%d"): float(v) for d, v in series.items()}


def _fetch_etf_kline(etf_code: str, limit: int = 300) -> List[Dict[str, Any]]:
    """带 TTL 缓存的板块 ETF 日K线（tushare fund_daily）。"""
    key = f"kline:{etf_code}"
    cached = _cache_get(key, 7200)
    if cached is not None:
        return cached
    from app.services.golden_pit_service import GoldenPitService
    bars = GoldenPitService._fetch_pi_server_kline(etf_code, limit=limit)
    _cache_set(key, bars)
    return bars


def _load_sector_greed_map() -> Dict[str, Dict[str, float]]:
    """加载板块贪婪历史 {etf6位: {date: greed}}（arkvol funds-greed/fund，TTL 7200s）。"""
    cached = _cache_get("sector_greed_map", 7200)
    if cached is not None:
        return cached
    from app.services.arkvol_service import ArkvolService
    svc = ArkvolService()
    out: Dict[str, Dict[str, float]] = {}
    for sector, entry in SECTOR_ETF_POOL.items():
        code = entry.get("greed_code")
        if not code:
            continue
        etf6 = entry["etf_code"][2:]
        try:
            payload = svc.fetch_fund_series(code, days=2000)
        except Exception as e:
            logger.warning("板块贪婪加载失败 %s(%s): %s", sector, code, e)
            continue
        data = (payload.get("data") or []) if isinstance(payload, dict) else []
        g: Dict[str, float] = {}
        for r in data:
            d = r.get("date")
            if d and r.get("greed") is not None:
                g[str(d)] = float(r["greed"])
        if g:
            out[etf6] = g
    _cache_set("sector_greed_map", out)
    return out


def _load_tech_greed_map() -> Dict[str, Dict[str, float]]:
    """加载 tech7 池贪婪历史 {etf6: {date: greed}}（arkvol tech-hardware-greed/series，TTL 7200s）。"""
    cached = _cache_get("sector_tech_greed_map", 7200)
    if cached is not None:
        return cached
    from app.services.arkvol_service import ArkvolService
    svc = ArkvolService()
    try:
        payload = svc.fetch_tech_greed(days=2000)
    except Exception as e:
        logger.warning("tech-hardware 贪婪加载失败: %s", e)
        return {}
    data = (payload.get("data") or {}) if isinstance(payload, dict) else {}
    out: Dict[str, Dict[str, float]] = {}
    for code, arr in data.items():
        g: Dict[str, float] = {}
        for r in arr or []:
            d = r.get("date")
            if d and r.get("greed") is not None:
                g[str(d)] = float(r["greed"])
        if g:
            out[str(code)] = g
    _cache_set("sector_tech_greed_map", out)
    return out


def _compute_signal(
    pool_key: str,
    entry: Dict[str, Any],
    flow_df: pd.DataFrame,
    as_of: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """计算单个板块的 mf5_norm / oversold120 / combo。数据不足返回 None。"""
    cfg = cfg or get_sector_config()
    mf_days = int(cfg.get("mf_days", SECTOR_MF_DAYS))
    mf_ma_days = int(cfg.get("mf_ma_days", SECTOR_MF_MA_DAYS))
    ovs_days = int(cfg.get("ovs_days", SECTOR_OVS_DAYS))

    flow_name = entry.get("flow_name", pool_key)
    series = _flow_series(flow_df, flow_name, as_of)
    dates = sorted(series.keys())
    if len(dates) < mf_ma_days:
        return None

    cum5 = sum(series[d] for d in dates[-mf_days:])
    ma20 = sum(abs(series[d]) for d in dates[-mf_ma_days:]) / mf_ma_days
    if ma20 <= 0:
        return None
    mf5_norm = cum5 / ma20

    kline = _fetch_etf_kline(entry["etf_code"], limit=ovs_days + 80)
    if len(kline) < ovs_days + 1:
        return None
    closes = [float(b["close"]) for b in kline if b.get("close")]
    if len(closes) < ovs_days + 1:
        return None
    high120 = max(closes[-ovs_days:])
    last_close = closes[-1]
    if high120 <= 0:
        return None
    oversold120 = last_close / high120 - 1.0

    # 逆势流入 + 超跌门槛（可配置阈值, 默认 0）
    if mf5_norm <= 0 or oversold120 >= 0:
        return None

    return {
        "sector": pool_key,
        "name": entry["name"],
        "etf_code": entry["etf_code"],
        "mf5_norm": round(mf5_norm, 4),
        "oversold120": round(oversold120, 4),
    }


def _compute_signal_greed(
    pool_key: str,
    entry: Dict[str, Any],
    greed_map: Dict[str, Dict[str, float]],
    as_of: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """greed 模式单板块信号：超跌中(oversold120<0)且当日贪婪可查。数据不足返回 None。"""
    cfg = cfg or get_sector_config()
    ovs_days = int(cfg.get("ovs_days", SECTOR_OVS_DAYS))

    etf6 = entry["etf_code"][2:]
    g = greed_map.get(etf6, {}).get(as_of)
    if g is None:
        return None

    kline = _fetch_etf_kline(entry["etf_code"], limit=ovs_days + 80)
    if len(kline) < ovs_days + 1:
        return None
    closes = [float(b["close"]) for b in kline if b.get("close")]
    if len(closes) < ovs_days + 1:
        return None
    high120 = max(closes[-ovs_days:])
    last_close = closes[-1]
    if high120 <= 0:
        return None
    oversold120 = last_close / high120 - 1.0
    if oversold120 >= 0:
        return None

    return {
        "sector": pool_key,
        "name": entry["name"],
        "etf_code": entry["etf_code"],
        "greed": round(g, 4),
        "oversold120": round(oversold120, 4),
    }


def _rank_combo(valid: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按回测口径计算 combo: -(rank(mf5_norm,降序) + rank(oversold,升序))。"""
    mf_ranks = {
        s["sector"]: i + 1
        for i, s in enumerate(sorted(valid, key=lambda x: x["mf5_norm"], reverse=True))
    }
    ovs_ranks = {
        s["sector"]: i + 1
        for i, s in enumerate(sorted(valid, key=lambda x: x["oversold120"]))
    }
    for s in valid:
        s["combo"] = round(-(mf_ranks[s["sector"]] + ovs_ranks[s["sector"]]), 4)
    return valid


def _rank_combo_greed(valid: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """greed 模式 combo: -(rank(greed,升序=恐慌) + rank(oversold120,升序=超跌))。"""
    g_ranks = {
        s["sector"]: i + 1
        for i, s in enumerate(sorted(valid, key=lambda x: x["greed"]))
    }
    ovs_ranks = {
        s["sector"]: i + 1
        for i, s in enumerate(sorted(valid, key=lambda x: x["oversold120"]))
    }
    for s in valid:
        s["combo"] = round(-(g_ranks[s["sector"]] + ovs_ranks[s["sector"]]), 4)
    return valid


def _normalize_weights(selected: List[Dict[str, Any]], max_weight: float) -> List[Dict[str, Any]]:
    """按 combo 分数归一化权重，单板块上限截断，超额按其余板块比例再分配。"""
    if not selected:
        return selected
    min_combo = min(s["combo"] for s in selected)
    raw = [s["combo"] - min_combo + 1.0 for s in selected]
    total = sum(raw)
    for s, r in zip(selected, raw):
        s["weight"] = r / total if total > 0 else 1.0 / len(selected)

    # 单板块上限截断（多轮迭代直到无超限）
    n = len(selected)
    if n > 1 and max_weight < 1.0:
        weights = [s["weight"] for s in selected]
        for _ in range(n):
            capped = [w > max_weight for w in weights]
            if not any(capped):
                break
            excess = sum((w - max_weight) for w, c in zip(weights, capped) if c)
            weights = [max_weight if c else w for w, c in zip(weights, capped)]
            uncapped = [w for w, c in zip(weights, capped) if not c]
            if not uncapped or excess <= 0:
                break
            uncap_total = sum(uncapped)
            weights = [
                w if c else w + excess * w / uncap_total
                for w, c in zip(weights, capped)
            ]
        for s, w in zip(selected, weights):
            s["weight"] = round(w, 4)
        # 舍入误差调整: 将残差并入最大权重，保证总和=1
        diff = 1.0 - sum(s["weight"] for s in selected)
        if abs(diff) > 1e-9:
            heaviest = max(selected, key=lambda x: x["weight"])
            heaviest["weight"] = round(heaviest["weight"] + diff, 4)
    else:
        for s in selected:
            s["weight"] = round(s["weight"], 4)
        diff = 1.0 - sum(s["weight"] for s in selected)
        if abs(diff) > 1e-9:
            selected[0]["weight"] = round(selected[0]["weight"] + diff, 4)
    return selected


def select_sectors(
    as_of: Optional[str] = None,
    top_n: Optional[int] = None,
    enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """主入口: 按当前 signal_mode 对板块池计算 combo 信号并选出 TOP N 板块组合。

    greed 模式（默认）: 有效信号 = 超跌中(oversold120<0)且板块贪婪可查，
    combo = -(rank(greed 升序) + rank(oversold120 升序))；
    moneyflow 模式（回滚）: 走既有「超跌 + 中信二级5日资金流」逻辑。

    Args:
        as_of: 数据截止日（默认今天）。dry-run 可与回测窗口对齐。
        top_n: 覆盖 SECTOR_TOP_N。
        enabled: 覆盖 GOLDEN_PIT_SECTOR_SPLIT_ENABLED（默认取配置）。

    Returns:
        {"as_of", "enabled", "signal_mode",
         "selected": [{sector,name,etf_code,combo,(greed|mf5_norm),oversold120,weight}],
         "all": [...], "empty_reason"}
    """
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")
    cfg = get_sector_config()
    is_enabled = bool(cfg.get("enabled")) if enabled is None else enabled
    top_n = int(top_n or cfg.get("top_n", SECTOR_TOP_N))
    max_weight = float(cfg.get("max_weight", SECTOR_MAX_WEIGHT))
    min_valid = int(cfg.get("min_valid", SECTOR_MIN_VALID))
    signal_mode = str(cfg.get("signal_mode", SECTOR_SIGNAL_MODE)).strip().lower()
    # pool_source 仅在 greed 模式生效: tech7=场内科技7只(tech-hardware贪婪, 默认); prod10=原10板块(funds-greed, 回滚)
    # moneyflow 模式固定使用 SECTOR_ETF_POOL（依赖中信二级行业资金流映射）
    pool_source = str(cfg.get("pool_source", SECTOR_POOL_SOURCE)).strip().lower()
    use_tech_pool = signal_mode == "greed" and pool_source == "tech7"
    pool = TECH_SECTOR_POOL if use_tech_pool else SECTOR_ETF_POOL

    cache_key = f"selection:{as_of}:{top_n}:{signal_mode}:{pool_source}"
    cached = _cache_get(cache_key, 900)
    if cached is not None:
        cached["enabled"] = is_enabled
        return cached

    if not pool:
        pool_name = "TECH_SECTOR_POOL" if use_tech_pool else "SECTOR_ETF_POOL"
        return {
            "as_of": as_of, "enabled": is_enabled, "signal_mode": signal_mode,
            "pool_source": pool_source, "selected": [], "all": [],
            "empty_reason": f"{pool_name} 未配置",
        }

    valid = []
    if signal_mode == "greed":
        greed_map = _load_tech_greed_map() if use_tech_pool else _load_sector_greed_map()
        for pool_key, entry in pool.items():
            sig = _compute_signal_greed(pool_key, entry, greed_map, as_of, cfg)
            if sig:
                valid.append(sig)
    else:
        flow_df = _load_industry_flow_df()
        for pool_key, entry in pool.items():
            sig = _compute_signal(pool_key, entry, flow_df, as_of, cfg)
            if sig:
                valid.append(sig)

    if len(valid) < min_valid:
        result = {
            "as_of": as_of, "enabled": is_enabled, "signal_mode": signal_mode,
            "pool_source": pool_source, "selected": [], "all": valid,
            "empty_reason": f"有效信号板块数 {len(valid)} < {min_valid}，空仓等待板块信号",
        }
        _cache_set(cache_key, result)
        return result

    valid = _rank_combo_greed(valid) if signal_mode == "greed" else _rank_combo(valid)
    valid.sort(key=lambda x: x["combo"], reverse=True)
    selected = _normalize_weights(valid[:top_n], max_weight)

    result = {
        "as_of": as_of,
        "enabled": is_enabled,
        "signal_mode": signal_mode,
        "pool_source": pool_source,
        "selected": selected,
        "all": valid,
        "empty_reason": "" if selected else "combo 信号均未过门槛，空仓等待板块信号",
    }
    _cache_set(cache_key, result)
    return result


def format_selection(selection: Dict[str, Any]) -> str:
    """选筹结果 → 可读文本（报告/日志用），兼容 greed / moneyflow 两种信号维度。"""
    if not selection.get("selected"):
        reason = selection.get("empty_reason", "无信号")
        return f"🧭 板块拆分: 空仓等待（{reason}）"
    parts = []
    for s in selection["selected"]:
        if "greed" in s:
            dim = f"greed={s['greed']:.2f}"
        else:
            dim = f"mf5={s['mf5_norm']:.2f}"
        parts.append(
            f"{s['name']}({s['sector']}) {s['weight'] * 100:.0f}% combo={s['combo']} "
            f"{dim} ovs={s['oversold120'] * 100:.1f}%"
        )
    mode = "执行" if selection.get("enabled") else "展示(dry-run)"
    signal = selection.get("signal_mode", "moneyflow")
    return f"🧭 板块拆分[{mode}/{signal}]: " + " | ".join(parts)
