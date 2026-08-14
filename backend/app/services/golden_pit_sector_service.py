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
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.services.golden_pit_config import (
    ALL_INDEX_CONFIGS,
    COMBO_W_MF,
    build_entry_exit_defaults,
    COMBO_W_OVS,
    DCA_CARRIER_DEFAULTS,
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

# 已优化指数: 回测确认的备选参数优先（生产已落地，pgsql 集中管理）
ENTRY_EXIT_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "588000": {
        "use_fixed_greed": True,
        "pit_greed": 0.28, "entry_greed": 0.33, "entry_offset": 0,
        "exit_full_pct": 85, "exit_half_pct": 40, "exit_fallback_days": 20,
        "turning_days": 1,
    },
    "159915": {
        "use_fixed_greed": True,
        "pit_greed": 0.30, "entry_greed": 0.35, "entry_offset": 0,
        "exit_full_pct": 75, "exit_half_pct": 70, "exit_fallback_days": 30,
        "turning_days": 1,
    },
}
# 全部指数出入场参数默认（588000/159915=回测备选；其余从 ALL_INDEX_CONFIGS 硬编码提取；运行值在 pgsql 表集中管理）
ENTRY_EXIT_DEFAULTS: Dict[str, Dict[str, Any]] = build_entry_exit_defaults(ENTRY_EXIT_OVERRIDES)

# ── 板块个性化参数（tech7 + 非科技板块每板块一套 ovs_days/entry_greed_cap/exit_down_days）──
# 覆盖池: TECH_SECTOR_POOL(7) ∪ SECTOR_ETF_POOL(10)，主键=etf6；默认=全局值；
# 运行值在 pgsql sector_params JSON 集中管理，可按板块在弹窗调整。
# 回测调优(2026-08-13, data/backtest/_sector_params_tune.py v2): 样本=2025起4个板块窗口(3个唯一),
# 超跌窗口/贪婪上限在该样本无区分度(保持全局默认 120/0.95), 有效杠杆=连跌退出天数:
#   512930 人工智能/515050 5G: 2026-04 坑中 2 连跌即撤, 规避 -56%~-68% 崩段 (exit3→exit2)
#   588200 科创芯片: 2026-04 需容忍 4 连跌, 否则提前卖丢 +44.8% 主升 (exit3→exit4)
#   159949 创业板50: 5 连跌容忍, 2026-04 持有到段末 (+10.3%→+19.6%)
#   159929 生物医药/512660 军工: 4 连跌容忍, 避免浅回调被震出 (exit3→exit4)
SECTOR_PARAM_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "159949": {"exit_down_days": 5},
    "512930": {"exit_down_days": 2},
    "515050": {"exit_down_days": 2},
    "588200": {"exit_down_days": 4},
    "159929": {"exit_down_days": 4},
    "512660": {"exit_down_days": 4},
}
SECTOR_PARAM_DEFAULTS: Dict[str, Dict[str, Any]] = {}
for _pk, _e in list(TECH_SECTOR_POOL.items()) + list(SECTOR_ETF_POOL.items()):
    _etf6 = _e["etf_code"][2:]
    if _etf6 in SECTOR_PARAM_DEFAULTS:
        continue
    SECTOR_PARAM_DEFAULTS[_etf6] = {
        "ovs_days": int(SECTOR_OVS_DAYS),
        "entry_greed_cap": 0.95,
        "exit_down_days": int(SECTOR_EXIT_DOWN_DAYS),
    }
    SECTOR_PARAM_DEFAULTS[_etf6].update(SECTOR_PARAM_OVERRIDES.get(_etf6, {}))

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
    "entry_greed_cap": {
        "label": "入场贪婪分位上限", "description": "greed模式选筹: 250日贪婪分位>上限的过热板块不追(回测结论: 入场贪婪过滤)",
        "value_type": "number", "sort_order": 13, "default": 0.95,
    },
    "dca_carrier_enabled": {
        "label": "DCA 执行载体启用", "description": "false=dry-run 展示目标载体；true=按载体配置买入（fixed_combo/broad）",
        "value_type": "bool", "sort_order": 13, "default": False,
    },
    "dca_carrier_588000": {
        "label": "科创50 DCA 载体", "description": "JSON: {\"mode\":\"sector_selection\"|\"fixed_combo\"|\"broad\",\"codes\":[{\"code\":\"588200\",\"weight\":0.5},...]}",
        "value_type": "json", "sort_order": 14, "default": json.dumps(DCA_CARRIER_DEFAULTS["588000"], ensure_ascii=False),
    },
    "dca_carrier_159915": {
        "label": "创业板指 DCA 载体", "description": "JSON: {\"mode\":\"sector_selection\"|\"fixed_combo\"|\"broad\",\"codes\":[{\"code\":\"159949\",\"weight\":1.0}]}",
        "value_type": "json", "sort_order": 15, "default": json.dumps(DCA_CARRIER_DEFAULTS["159915"], ensure_ascii=False),
    },
    "hold_until_exit": {
        "label": "持仓保留(只截新入)", "description": "true=已持仓板块未触发退出前保留在目标组合, 仅新候选按 TOP N 截断(回测只截新入)",
        "value_type": "bool", "sort_order": 16, "default": False,
    },
    "fallback_broad": {
        "label": "选筹失败回退宽基", "description": "true=板块选筹为空时买入宽基本身ETF(信号恢复自动切回板块), false=跳过当日买入",
        "value_type": "bool", "sort_order": 17, "default": False,
    },
    "regime_mode": {
        "label": "牛熊选筹模式", "description": "auto=按科技趋势腿激活数切换; oversold=固定超跌(贪婪)选筹; trend=固定趋势(动量)选筹; bh=宽基躺平",
        "value_type": "string", "sort_order": 18, "default": "oversold",
    },
    "regime_trend_threshold": {
        "label": "趋势腿激活阈值", "description": "regime_mode=auto 时: 趋势腿激活数>=阈值切趋势(动量)选筹, 否则超跌选筹",
        "value_type": "number", "sort_order": 19, "default": 5,
    },
    "regime_carrier_enabled": {
        "label": "Regime 决定载体", "description": "true=按牛熊状态自动选执行载体: oversold→板块选筹, trend→固定高弹性组合, bh→宽基; false=保持5.4静态载体优先级",
        "value_type": "bool", "sort_order": 20, "default": False,
    },
    "carrier_best_only": {
        "label": "载体只买最优一只", "description": "true=fixed_combo 多候选ETF只买性价比最高一只(超跌+贪婪/趋势动量评分), false=按配置权重等权买入全部候选",
        "value_type": "bool", "sort_order": 21, "default": True,
    },
    "hold_bear_pct_threshold": {
        "label": "熊市保护贪婪分位", "description": "hold_until_exit 熊市保护: regime=oversold 且宽基贪婪250日分位<=阈值时保留持仓、暂停新增候选",
        "value_type": "number", "sort_order": 21, "default": 0.2,
    },
    "industry_pool_enabled": {
        "label": "全行业监测启用",
        "description": "true=全行业 DCA 触发与资金池裁决生效（默认 dry-run 展示计划/实际）；false=仅指数级 DCA（安全默认）",
        "value_type": "bool", "sort_order": 30, "default": False,
    },
    "industry_pool": {
        "label": "全行业池",
        "description": "JSON 行业清单 [{\"id\",\"name\",\"greed_code\",\"etf_code\",\"priority\",\"max_total_pct\",\"min_days_in_pit\"}]；缺省回退内置 24 行业",
        "value_type": "json", "sort_order": 31, "default": "[]",
    },
    "industry_execute": {
        "label": "行业轨真实下单",
        "description": "true 且 industry_pool_enabled=true: 行业 DCA 按资金池裁决真实下单（模拟盘）；false: 仅 dry-run 计划/展示",
        "value_type": "bool", "sort_order": 36, "default": False,
    },
    "cash_min_pct": {
        "label": "行业池现金下限",
        "description": "资金池保留现金占净值比例（0~1），高于此才分配行业定投",
        "value_type": "number", "sort_order": 32, "default": 0.2,
    },
    "industry_pit_pct": {
        "label": "行业贪婪分位阈值",
        "description": "250日贪婪分位<=阈值视为入坑条件之一（统一参数，不做行业个性化）",
        "value_type": "number", "sort_order": 33, "default": 0.15,
    },
    "industry_drawdown_pct": {
        "label": "行业回撤阈值",
        "description": "60日高点回撤>=阈值视为入坑条件之一",
        "value_type": "number", "sort_order": 34, "default": 0.20,
    },
    "industry_entry_cap": {
        "label": "行业过热过滤",
        "description": "贪婪分位>cap 不追新仓",
        "value_type": "number", "sort_order": 35, "default": 0.85,
    },
    "sector_params": {
        "label": "板块个性化参数",
        "description": "JSON: {etf6: {ovs_days, entry_greed_cap, exit_down_days}}（按板块覆盖全局超跌窗口/贪婪分位上限/连跌退出天数，集中管理）",
        "value_type": "json", "sort_order": 24,
        "default": json.dumps(SECTOR_PARAM_DEFAULTS, ensure_ascii=False),
    },
    "entry_exit_588000": {
        "label": "科创50 出入场参数",
        "description": 'JSON 覆盖出入场参数（集中管理）: {"use_fixed_greed":true,"pit_greed":0.28,"entry_greed":0.33,"exit_full_pct":85,"exit_half_pct":40,"exit_fallback_days":20}',
        "value_type": "json", "sort_order": 22,
        "default": json.dumps(ENTRY_EXIT_DEFAULTS["588000"], ensure_ascii=False),
    },
    "entry_exit_159915": {
        "label": "创业板指 出入场参数",
        "description": 'JSON 覆盖出入场参数（集中管理）: {"use_fixed_greed":true,"pit_greed":0.30,"entry_greed":0.35,"exit_full_pct":75,"exit_half_pct":70,"exit_fallback_days":30}',
        "value_type": "json", "sort_order": 23,
        "default": json.dumps(ENTRY_EXIT_DEFAULTS["159915"], ensure_ascii=False),
    },
}

# 其余指数出入场参数集中管理（entry_exit_<fund_code> JSON，与板块拆分同表；弹窗可改）
for _idx, (_code, _defaults) in enumerate(ENTRY_EXIT_DEFAULTS.items()):
    if _code in ("588000", "159915"):
        continue
    _name = ALL_INDEX_CONFIGS.get(_code, {}).get("name", _code)
    SECTOR_CONFIG_DEFAULTS[f"entry_exit_{_code}"] = {
        "label": f"{_name} 出入场参数",
        "description": f"JSON 覆盖出入场参数（集中管理，默认=CHINA_INDICES/回测优化值）: {json.dumps(_defaults, ensure_ascii=False)}",
        "value_type": "json",
        "sort_order": 24 + _idx,
        "default": json.dumps(_defaults, ensure_ascii=False),
    }


SECTOR_CONFIG_DEFAULTS["industry_pool"]["default"] = "[]"
try:
    from app.services.golden_pit_industry_service import INDUSTRY_POOL as _INDUSTRY_POOL
    SECTOR_CONFIG_DEFAULTS["industry_pool"]["default"] = json.dumps(
        [{k: i[k] for k in ("id", "name", "greed_code", "etf_code", "priority", "max_total_pct", "min_days_in_pit")} for i in _INDUSTRY_POOL],
        ensure_ascii=False)
except Exception:  # noqa: BLE001 - 循环依赖/未就绪时保持空，运行时回退内置
    pass


DCA_CARRIER_MODES = ("sector_selection", "fixed_combo", "broad")


def parse_dca_carrier(raw: Any, default_mode: str = "sector_selection") -> Dict[str, Any]:
    """解析 dca_carrier_<fund> JSON；非法或缺失回退 sector_selection 并记录原因。

    返回 {"mode", "codes"(可选), "reason"(回退时)}；fixed_combo 要求 codes 非空且权重和为 1。
    """
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw) if (isinstance(raw, str) and raw.strip()) else {}
        except (ValueError, TypeError):
            logger.warning("DCA 载体配置 JSON 非法, 回退 sector_selection: %s", raw)
            return {"mode": "sector_selection", "reason": "invalid_json"}
    mode = data.get("mode") or default_mode
    if mode not in DCA_CARRIER_MODES:
        logger.warning("DCA 载体配置 mode 未知(%s), 回退 sector_selection", mode)
        return {"mode": "sector_selection", "reason": "unknown_mode"}
    codes = data.get("codes") or []
    if mode == "fixed_combo":
        if not codes:
            logger.warning("DCA 载体 fixed_combo 缺少 codes, 回退 sector_selection")
            return {"mode": "sector_selection", "reason": "empty_codes"}
        weight_sum = 0.0
        for c in codes:
            try:
                w = float(c.get("weight", 0.0) or 0.0)
            except (TypeError, ValueError):
                w = -1.0
            if w < 0 or not c.get("code"):
                logger.warning("DCA 载体 fixed_combo 标的/权重非法: %s", c)
                return {"mode": "sector_selection", "reason": "invalid_member"}
            weight_sum += w
        if abs(weight_sum - 1.0) > 1e-6:
            logger.warning("DCA 载体 fixed_combo 权重和=%.2f, 回退 sector_selection", weight_sum)
            return {"mode": "sector_selection", "reason": f"weight_sum={weight_sum:.2f}"}
    return {"mode": mode, "codes": codes}


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

    # 无条件 seed（内部仅补缺失键），确保已有配置的部署也会补入新配置项
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
            elif vtype in ("string", "json"):
                cfg[key] = str(raw).strip()
            else:
                cfg[key] = float(raw)
        except (ValueError, TypeError):
            cfg[key] = default
    cfg["dca_carriers"] = {
        fc: parse_dca_carrier(cfg.get(f"dca_carrier_{fc}"),
                              DCA_CARRIER_DEFAULTS.get(fc, {}).get("mode", "sector_selection"))
        for fc in DCA_CARRIER_DEFAULTS
    }
    _cache_set("sector_config", cfg)
    return cfg


def get_index_entry_exit(fund_code: str) -> Dict[str, Any]:
    """返回 <fund_code> 的出入场参数覆盖（golden_pit_sector_config 表 entry_exit_<fund_code>，集中管理）。"""
    key = f"entry_exit_{fund_code}"
    raw = get_sector_config().get(key)
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        logger.warning("%s JSON 非法, 忽略覆盖: %s", key, raw)
        return {}


def get_sector_params(etf_code: str) -> Dict[str, Any]:
    """返回板块个性化参数（pgsql sector_params JSON 覆盖默认；etf_code 支持 6 位或 SH/SZ 前缀）。"""
    etf6 = (etf_code or "").strip()
    if etf6[:2] in ("SH", "SZ", "BJ"):
        etf6 = etf6[2:]
    defaults = dict(SECTOR_PARAM_DEFAULTS.get(etf6, {}))
    raw = get_sector_config().get("sector_params")
    over: Dict[str, Any] = {}
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
            over = data.get(etf6, {}) if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            logger.warning("sector_params JSON 非法: %s", raw)
    defaults.update(over or {})
    return defaults


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
            elif vtype == "json":
                if isinstance(raw, str):
                    json.loads(raw)  # 校验
                    val = raw.strip()
                else:
                    val = json.dumps(raw, ensure_ascii=False)
                row.config_value = val
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
    params = get_sector_params(entry["etf_code"])  # 板块个性化参数（默认=全局值）
    ovs_days = int(params.get("ovs_days") or cfg.get("ovs_days", SECTOR_OVS_DAYS))
    greed_cap = float(params.get("entry_greed_cap") or cfg.get("entry_greed_cap", 0.95))  # 入场贪婪分位上限（过热不追）

    etf6 = entry["etf_code"][2:]
    g = greed_map.get(etf6, {}).get(as_of)
    if g is None:
        return None

    # 入场贪婪过滤（回测结论: 别在贪婪分位接近100%时追新仓）: 250日分位 > cap 的过热板块跳过
    # 分位仅用 <= as_of 的历史（避免历史 as_of/dry-run 前视未来贪婪；实时 as_of=今天 行为不变）
    hist = sorted(
        (d, v) for d, v in greed_map.get(etf6, {}).items()
        if v is not None and d <= as_of
    )
    if len(hist) >= 20 and greed_cap < 1.0:
        recent = [v for _, v in hist[-250:]]
        pct = sum(1 for v in recent if v <= g) / len(recent)
        if pct > greed_cap:
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


def _normalize_weights(selected: List[Dict[str, Any]], max_weight: float, score_key: str = "combo") -> List[Dict[str, Any]]:
    """按 score_key（默认 combo）分数归一化权重，单板块上限截断，超额按其余板块比例再分配。"""
    if not selected:
        return selected
    min_score = min(s[score_key] for s in selected)
    raw = [s[score_key] - min_score + 1.0 for s in selected]
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


def _compute_signal_momentum(
    pool_key: str,
    entry: Dict[str, Any],
    as_of: str,
) -> Optional[Dict[str, Any]]:
    """趋势模式信号: 20 日动量（close[d]/close[d-20]-1）。数据不足返回 None。"""
    kline = _fetch_etf_kline(entry["etf_code"], limit=120)
    closes = [float(b["close"]) for b in kline if b.get("close")]
    if len(closes) < 21:
        return None
    momentum = closes[-1] / closes[-21] - 1.0
    return {
        "sector": pool_key,
        "name": entry["name"],
        "etf_code": entry["etf_code"],
        "momentum": round(momentum, 4),
    }


def resolve_regime_mode(
    cfg: Optional[Dict[str, Any]] = None,
    tech_status: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """解析选筹模式: auto 按科技趋势腿激活数切换 trend/oversold；显式值直接返回；异常按 oversold 兜底。

    Returns:
        (mode, reason): mode ∈ {"oversold", "trend", "bh"}。
    """
    cfg = cfg or get_sector_config()
    mode = str(cfg.get("regime_mode", "oversold")).strip().lower()
    if mode not in ("auto", "oversold", "trend", "bh"):
        logger.warning("regime_mode 未知(%s), 回退 oversold", mode)
        return "oversold", "regime_mode 非法, 兜底超跌选筹"
    if mode != "auto":
        label = {"oversold": "超跌(贪婪)选筹", "trend": "趋势(动量)选筹", "bh": "宽基躺平"}.get(mode, mode)
        return mode, f"配置固定为{label}"
    threshold = int(cfg.get("regime_trend_threshold", 5))
    if tech_status is None:
        try:
            from app.services.golden_pit_tech_status import get_tech_status
            tech_status = get_tech_status()
        except Exception as e:
            logger.warning("auto 模式读取科技现状失败, 兜底 oversold: %s", e)
            return "oversold", "科技现状读取失败, 兜底超跌选筹"
    trend_up = int((tech_status or {}).get("trend_up_count", 0))
    total = int((tech_status or {}).get("total_count", 0))
    if trend_up >= threshold:
        return "trend", f"趋势腿激活 {trend_up}/{total} ≥ {threshold}"
    return "oversold", f"趋势腿激活 {trend_up}/{total} < {threshold}"


def resolve_carrier(
    fund_code: str,
    cfg: Optional[Dict[str, Any]] = None,
    tech_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """解析执行载体（regime → carrier 映射）。

    `regime_carrier_enabled=true` 时:
      oversold -> sector_selection（动态选筹, 含 hold_until_exit / fallback）
      trend    -> fixed_combo（复用 dca_carrier_<fund> codes; codes 缺失回退 broad）
      bh       -> broad（宽基躺平）
    关闭时返回 {}（DCA 层走 5.4 静态优先级 _carrier_active）。

    Returns:
        {"mode": "sector_selection"|"fixed_combo"|"broad", "codes": [...], "reason": str} 或 {}
    """
    cfg = cfg or get_sector_config()
    if not cfg.get("regime_carrier_enabled"):
        return {}
    regime_mode, regime_reason = resolve_regime_mode(cfg, tech_status)
    if regime_mode == "bh":
        return {"mode": "broad", "codes": [], "reason": f"regime=bh（{regime_reason}）"}
    if regime_mode == "trend":
        carrier = cfg.get("dca_carriers", {}).get(fund_code, {})
        codes = (carrier.get("codes") or []) if carrier.get("mode") == "fixed_combo" else []
        if codes:
            return {"mode": "fixed_combo", "codes": codes, "reason": f"regime=trend（{regime_reason}）→ 高弹性组合"}
        return {"mode": "broad", "codes": [], "reason": f"regime=trend（{regime_reason}）无 fixed_combo codes, 回退宽基"}
    return {"mode": "sector_selection", "codes": [], "reason": f"regime=oversold（{regime_reason}）→ 动态选筹"}


def _broad_greed_bearish(threshold: float) -> Optional[bool]:
    """宽基贪婪 250 日分位是否处于低位（任一 guide_only 宽基 <= threshold）。

    复用 golden_pit_tech_status._percentile / _load_broad_greed；数据缺失返回 None（跳过保护）。
    """
    try:
        from app.services.golden_pit_tech_status import _load_broad_greed, _percentile
        series_map = _load_broad_greed()
        pcts = []
        for code in ("588000", "159915"):
            series = series_map.get(code, {})
            if series:
                p = _percentile(series)
                if p is not None:
                    pcts.append(p)
        if not pcts:
            return None
        return min(pcts) <= threshold
    except Exception as e:
        logger.warning("宽基贪婪分位读取失败, 跳过熊市保护: %s", e)
        return None


def best_carrier_code(
    codes: List[str],
    as_of: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], str]:
    """fixed_combo 载体候选 ETF 性价比评分，返回 (最优 etf6, 理由)。

    评分维度与板块选筹一致:
      regime=trend   → 20 日动量最高（追强势）
      regime=oversold → greed 模式 combo=超跌+贪婪（恐慌最深=性价比最高）; moneyflow 模式 combo=超跌+资金流
    候选数据不足（未超跌/贪婪缺失）返回 (None, reason)，调用方回退等权组合。
    """
    cfg = cfg or get_sector_config()
    if not codes:
        return None, "无候选"
    pool: Dict[str, Dict[str, Any]] = {}
    for _pk, _e in list(TECH_SECTOR_POOL.items()) + list(SECTOR_ETF_POOL.items()):
        pool[_e["etf_code"][2:]] = {"pool_key": _pk, "entry": _e}
    scored: List[Dict[str, Any]] = []
    regime_mode, regime_reason = resolve_regime_mode(cfg)
    signal_mode = str(cfg.get("signal_mode", SECTOR_SIGNAL_MODE)).strip().lower()
    use_tech_pool = signal_mode == "greed" and str(cfg.get("pool_source", SECTOR_POOL_SOURCE)).strip().lower() == "tech7"
    for code in codes:
        c6 = (code or "").strip()
        if c6[:2] in ("SH", "SZ", "BJ"):
            c6 = c6[2:]
        hit = pool.get(c6)
        if not hit:
            continue
        pk, entry = hit["pool_key"], hit["entry"]
        if regime_mode == "trend":
            sig = _compute_signal_momentum(pk, entry, as_of)
            if sig:
                scored.append({"code": c6, "score": sig["momentum"], "dim": "动量"})
        elif signal_mode == "greed":
            greed_map = _load_tech_greed_map() if use_tech_pool else _load_sector_greed_map()
            sig = _compute_signal_greed(pk, entry, greed_map, as_of, cfg)
            if sig:
                sig["_code"] = c6
                scored.append(sig)
        else:
            flow_df = _load_industry_flow_df()
            sig = _compute_signal(pk, entry, flow_df, as_of, cfg)
            if sig:
                sig["_code"] = c6
                scored.append(sig)
    if not scored:
        return None, f"候选评分数据不足（regime={regime_mode}: {regime_reason}）"
    if regime_mode == "trend":
        best = max(scored, key=lambda x: x["score"])
        return best["code"], f"{best['dim']}最优 score={best['score']:.4f}（regime=trend）"
    if signal_mode == "greed":
        scored = _rank_combo_greed(scored)
        dim = "超跌+贪婪"
    else:
        scored = _rank_combo(scored)
        dim = "超跌+资金流"
    best = max(scored, key=lambda x: x["combo"])
    return best["_code"], f"{dim}最优 combo={best['combo']:.4f}（regime={regime_mode}）"


def select_sectors(
    as_of: Optional[str] = None,
    top_n: Optional[int] = None,
    enabled: Optional[bool] = None,
    holdings: Optional[List[str]] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """主入口: 按当前 signal_mode 与选筹模式对板块池计算信号并选出 TOP N 板块组合。

    oversold 模式（默认）: 有效信号 = 超跌中(oversold120<0)且板块贪婪可查，
    combo = -(rank(greed 升序) + rank(oversold120 升序))；
    moneyflow 模式（回滚）: 走既有「超跌 + 中信二级5日资金流」逻辑；
    trend 模式: 按 20 日动量（close[d]/close[d-20]-1）降序取 TOP N，不设超跌门槛。

    Args:
        as_of: 数据截止日（默认今天）。dry-run 可与回测窗口对齐。
        top_n: 覆盖 SECTOR_TOP_N。
        enabled: 覆盖 GOLDEN_PIT_SECTOR_SPLIT_ENABLED（默认取配置）。
        holdings: 当前板块持仓（6位或带 SH/SZ 前缀 ETF 代码）；hold_until_exit 开启时保留。
        mode: 选筹模式覆盖（oversold/trend）；None 时按 regime_mode 配置解析（auto 读取科技现状）。

    Returns:
        {"as_of", "enabled", "signal_mode", "pool_source", "regime_mode", "regime_reason",
         "selected": [...], "all": [...], "empty_reason"}
    """
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")
    cfg = get_sector_config()
    is_enabled = bool(cfg.get("enabled")) if enabled is None else enabled
    top_n = int(top_n or cfg.get("top_n", SECTOR_TOP_N))
    max_weight = float(cfg.get("max_weight", SECTOR_MAX_WEIGHT))
    min_valid = int(cfg.get("min_valid", SECTOR_MIN_VALID))
    hold_until_exit = bool(cfg.get("hold_until_exit", False))
    signal_mode = str(cfg.get("signal_mode", SECTOR_SIGNAL_MODE)).strip().lower()
    pool_source = str(cfg.get("pool_source", SECTOR_POOL_SOURCE)).strip().lower()
    use_tech_pool = signal_mode == "greed" and pool_source == "tech7"
    pool = TECH_SECTOR_POOL if use_tech_pool else SECTOR_ETF_POOL

    if mode:
        regime_mode = str(mode).strip().lower()
        regime_reason = "调用方指定"
    else:
        regime_mode, regime_reason = resolve_regime_mode(cfg)
    if regime_mode not in ("oversold", "trend"):
        regime_mode = "oversold"

    holdings = [h for h in (holdings or []) if isinstance(h, str) and h.strip()]
    hold_set = {
        h.strip()[-6:] if h.strip()[:2] in ("SH", "SZ", "BJ") else h.strip()
        for h in holdings
    }

    cache_key = (
        f"selection:{as_of}:{top_n}:{signal_mode}:{pool_source}:{regime_mode}"
        f":{','.join(sorted(hold_set)) if hold_set else '-'}"
    )
    cached = _cache_get(cache_key, 900)
    if cached is not None:
        cached["enabled"] = is_enabled
        return cached

    def _finalize(selected, valid, empty_reason):
        result = {
            "as_of": as_of,
            "enabled": is_enabled,
            "signal_mode": signal_mode,
            "pool_source": pool_source,
            "regime_mode": regime_mode,
            "regime_reason": regime_reason,
            "selected": selected,
            "all": valid,
            "empty_reason": empty_reason,
        }
        _cache_set(cache_key, result)
        return result

    if not pool:
        pool_name = "TECH_SECTOR_POOL" if use_tech_pool else "SECTOR_ETF_POOL"
        return _finalize([], [], f"{pool_name} 未配置")

    valid = []
    if regime_mode == "trend":
        for pool_key, entry in pool.items():
            sig = _compute_signal_momentum(pool_key, entry, as_of)
            if sig:
                valid.append(sig)
    elif signal_mode == "greed":
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

    # 持仓保留（只截新入）: 已持仓板块保留, 不参与 TOP N 截断；无持仓时维持原 min_valid 门控
    held = [s for s in valid if s["etf_code"][-6:] in hold_set] if hold_until_exit and hold_set else []

    # 熊市保护: oversold + 宽基贪婪分位低位 → 只保留持仓, 暂停新增候选（防只截新入熊市拖累）
    bear_protect = False
    if held and regime_mode == "oversold":
        bear_th = float(cfg.get("hold_bear_pct_threshold", 0.2))
        if bear_th < 1.0:
            bear_protect = _broad_greed_bearish(bear_th) is True

    if len(valid) < min_valid and not held:
        return _finalize(
            [], valid,
            f"有效信号板块数 {len(valid)} < {min_valid}，空仓等待板块信号",
        )

    if regime_mode == "trend":
        valid.sort(key=lambda x: x["momentum"], reverse=True)
        for s in valid:
            s["combo"] = round(s["momentum"], 4)  # 归一化复用 combo 槽位（trend 语义=动量）
    elif signal_mode == "greed":
        valid = _rank_combo_greed(valid)
        valid.sort(key=lambda x: x["combo"], reverse=True)
    else:
        valid = _rank_combo(valid)
        valid.sort(key=lambda x: x["combo"], reverse=True)

    # 只截新入: 持仓保留 ∪ 新候选 TOP N（熊市保护时新候选=0）
    if held:
        if bear_protect:
            new_candidates: List[Dict[str, Any]] = []
        else:
            new_candidates = [s for s in valid if s["etf_code"][-6:] not in hold_set]
        selected = _normalize_weights(held + new_candidates[:top_n], max_weight, score_key="combo")
        if not selected:
            return _finalize([], valid, "combo 信号均未过门槛，空仓等待板块信号")
        empty_reason = (
            f"熊市保护: 保留持仓 {len(held)} 只, 暂停新增候选"
            if bear_protect else
            f"持仓保留 {len(held)} 只, 新候选截断 TOP {top_n}"
            if len(valid) < min_valid else ""
        )
        return _finalize(selected, valid, empty_reason)

    selected = _normalize_weights(valid[:top_n], max_weight, score_key="combo")
    return _finalize(
        selected, valid,
        "" if selected else "combo 信号均未过门槛，空仓等待板块信号",
    )


def format_selection(selection: Dict[str, Any]) -> str:
    """选筹结果 → 可读文本（报告/日志用），兼容 greed / moneyflow / trend 三种信号维度。"""
    regime = selection.get("regime_mode", "")
    regime_txt = f"/{regime}" if regime else ""
    if not selection.get("selected"):
        reason = selection.get("empty_reason", "无信号")
        return f"🧭 板块拆分{regime_txt}: 空仓等待（{reason}）"
    parts = []
    for s in selection["selected"]:
        if "momentum" in s:
            dim = f"mom20={s['momentum'] * 100:.1f}%"
        elif "greed" in s:
            dim = f"greed={s['greed']:.2f}"
        else:
            dim = f"mf5={s['mf5_norm']:.2f}"
        ovs_txt = f" ovs={s['oversold120'] * 100:.1f}%" if "oversold120" in s else ""
        parts.append(
            f"{s['name']}({s['sector']}) {s['weight'] * 100:.0f}% combo={s['combo']} "
            f"{dim}{ovs_txt}"
        )
    mode = "执行" if selection.get("enabled") else "展示(dry-run)"
    signal = selection.get("signal_mode", "moneyflow")
    return f"🧭 板块拆分[{mode}/{signal}{regime_txt}]: " + " | ".join(parts)

