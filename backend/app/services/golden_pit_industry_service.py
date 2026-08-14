# -*- coding: utf-8 -*-
"""全行业监测 DCA + 优先级资金池（行业轨，dry-run 优先）。

对应 openspec change full-industry-dca-priority-pool:
- 行业信号 = 250 日贪婪分位(<=industry_pit_pct) AND 60 日回撤(>=industry_drawdown_pct)（贪婪历史<20 天仅价格触发）
- 优先级资金池裁决: 现金下限 cash_min_pct 之上按 priority 从高到低逐个分配计划金额, 未分配额度滚动次日
- 出场: 窗口完成(win_days=15)后 收盘>=成本*(1+tp) 止盈 / 窗口结束+time_exit(60)交易日时间止损 / 满仓后跌破成本*(1-stop) 止损
- 坑间: 出场资金 -> 防御组合(红利/黄金/国债/银行/有色等权, 复用 DEFENSE_TAKEOVER_WEIGHTS); 新坑现金不足 -> 按防御持仓比例赎回回补

安全: 默认 dry-run（只生成计划与报告，不下单）。industry_execute=true 且 industry_pool_enabled=true 才走下单分支（灰度期）。
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 行业池（首版 24 个: 有贪婪历史 + 有场内 ETF 收益代理；与回测 INDUSTRIES 一致）──
# id: 稳定标识（用于 DCA 日志 strategy=industry/<id> 与配置 industry_pool JSON）
INDUSTRY_POOL: List[Dict[str, Any]] = [
    {"id": "semicon",        "name": "半导体",     "greed_code": "512480", "etf_code": "512480", "priority": 1,  "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "kcchip",         "name": "科创芯片",   "greed_code": "588200", "etf_code": "588200", "priority": 2,  "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "innov_drug",     "name": "创新药",     "greed_code": "015916", "etf_code": "159992", "priority": 3,  "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "ai",             "name": "人工智能",   "greed_code": "512930", "etf_code": "512930", "priority": 4,  "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "bigdata",        "name": "大数据",     "greed_code": "515400", "etf_code": "515400", "priority": 5,  "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "5g",             "name": "5G通信",     "greed_code": "515050", "etf_code": "515050", "priority": 6,  "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "comm_device",    "name": "通信设备",   "greed_code": "515880", "etf_code": "515880", "priority": 7,  "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "consume_elec",   "name": "消费电子",   "greed_code": "018301", "etf_code": "159732", "priority": 8,  "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "computer",       "name": "计算机",     "greed_code": "512720", "etf_code": "512720", "priority": 9,  "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "software",       "name": "软件",       "greed_code": "159852", "etf_code": "159852", "priority": 10, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "cyb50",          "name": "创业板50",   "greed_code": "159949", "etf_code": "159949", "priority": 11, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "nev",            "name": "新能源车",   "greed_code": "015528", "etf_code": "515030", "priority": 12, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "broker",         "name": "券商",       "greed_code": "004070", "etf_code": "512000", "priority": 13, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "media_game",     "name": "传媒游戏",   "greed_code": "012769", "etf_code": "512980", "priority": 14, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "med",            "name": "医药",       "greed_code": "018396", "etf_code": "159929", "priority": 15, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "military",       "name": "军工",       "greed_code": "022243", "etf_code": "512660", "priority": 16, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "pv",             "name": "光伏",       "greed_code": "168501", "etf_code": "515790", "priority": 17, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "chem",           "name": "化工",       "greed_code": "017836", "etf_code": "159870", "priority": 18, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "nonferrous",     "name": "有色",       "greed_code": "013081", "etf_code": "512400", "priority": 19, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "coal",           "name": "煤炭",       "greed_code": "015566", "etf_code": "515220", "priority": 20, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "power",          "name": "电力",       "greed_code": "020096", "etf_code": "159611", "priority": 21, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "bank",           "name": "银行",       "greed_code": "014028", "etf_code": "512800", "priority": 22, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "baijiu",         "name": "白酒",       "greed_code": "009941", "etf_code": "512690", "priority": 23, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
    {"id": "realestate",     "name": "房地产",     "greed_code": "004937", "etf_code": "512200", "priority": 24, "max_total_pct": 0.12, "min_days_in_pit": 2, "proxy_type": "etf"},
]
INDUSTRY_BY_ID: Dict[str, Dict[str, Any]] = {i["id"]: i for i in INDUSTRY_POOL}
INDUSTRY_BY_ETF: Dict[str, Dict[str, Any]] = {i["etf_code"]: i for i in INDUSTRY_POOL}

# 防御承接组合（与 DEFENSE_TAKEOVER_WEIGHTS 同权重，仅用代码）
DEFENSE_CODES = ["515080", "512800", "518880", "511010", "512400"]

# 统一进出场参数（design.md D6: 不做行业个性化，差异仅通过 priority 表达）
INDUSTRY_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "execute": False,          # true 且 enabled: 行业轨真实下单（模拟盘灰度期）；false: 仅 dry-run 计划
    "cash_min_pct": 0.20,
    "pit_pct": 0.15,          # 250 日贪婪分位阈值
    "drawdown_pct": 0.20,     # 60 日高点回撤阈值
    "entry_cap": 0.85,        # 过热过滤: 贪婪分位>cap 不追
    "max_total_pct": 0.12,    # 单行业上限 = 账户净值 × 12%
    "min_days": 2,            # 连续 in_pit 开窗天数
    "win_days": 15,           # DCA 窗口（前 5 日等权 20%/日）
    "tp_pct": 0.15,
    "time_exit_days": 60,
    "stop_loss": 0.10,
}
INDUSTRY_DCA_WEIGHTS = [0.2, 0.2, 0.2, 0.2, 0.2] + [0.0] * 10
STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "industry_dca_state.json")


# ── 简单 TTL 缓存 ──
_CACHE: Dict[str, Any] = {}


def _cache_get(key: str, ttl: int) -> Any:
    item = _CACHE.get(key)
    if item and time.time() - item[0] < ttl:
        return item[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (time.time(), value)


# ── 配置 ──
def get_industry_config() -> Dict[str, Any]:
    """读取全行业监测配置（golden_pit_sector_config 新键；缺失回退默认值）。"""
    cfg = dict(INDUSTRY_DEFAULTS)
    try:
        from app.services.golden_pit_sector_service import get_sector_config as _sc
        sc = _sc()
        cfg["enabled"] = bool(sc.get("industry_pool_enabled", cfg["enabled"]))
        cfg["execute"] = bool(sc.get("industry_execute", cfg["execute"]))
        for k in ("cash_min_pct", "pit_pct", "drawdown_pct", "entry_cap", "max_total_pct"):
            v = sc.get(f"industry_{k}") if k not in ("enabled",) else None
            if k == "cash_min_pct":
                v = sc.get("cash_min_pct")
            try:
                if v is not None:
                    cfg[k] = float(v)
            except (TypeError, ValueError):
                pass
        # 行业池: 配置 JSON 优先，缺失回退内置 24 行业
        raw = sc.get("industry_pool")
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
                if isinstance(data, list) and data:
                    merged = []
                    for item in data:
                        if not isinstance(item, dict) or not item.get("id"):
                            continue
                        base = INDUSTRY_BY_ID.get(item["id"], {})
                        merged.append({**base, **item})
                    if merged:
                        cfg["pool"] = merged
            except (ValueError, TypeError):
                logger.warning("industry_pool JSON 非法, 回退内置 24 行业")
        if "pool" not in cfg:
            cfg["pool"] = [dict(i) for i in INDUSTRY_POOL]
    except Exception as e:  # noqa: BLE001
        logger.warning("读取全行业监测配置失败, 用默认: %s", e)
        cfg["pool"] = [dict(i) for i in INDUSTRY_POOL]
    return cfg


def get_industry_by_id(industry_id: str) -> Optional[Dict[str, Any]]:
    cfg = get_industry_config()
    for i in cfg.get("pool", []):
        if i.get("id") == industry_id:
            return i
    return INDUSTRY_BY_ID.get(industry_id)


# ── 数据加载（TTL 缓存；失败返回空，不阻断主流程）──
def load_industry_greed(ttl: int = 3600) -> Dict[str, str]:
    """加载 24 行业贪婪历史 {greed_code: {date: greed}}（arkvol funds-greed/fund/{code}）。"""
    cached = _cache_get("industry_greed", ttl)
    if cached is not None:
        return cached
    out: Dict[str, str] = {}
    try:
        from app.services.arkvol_service import ArkvolService
        svc = ArkvolService()
        for ind in INDUSTRY_POOL:
            gc = ind["greed_code"]
            for i in range(3):
                try:
                    g = svc.fetch_fund_series(gc, days=2000)
                    arr = (g.get("data") or []) if g.get("success") else []
                    out[gc] = {r["date"]: float(r["greed"]) for r in arr if r.get("date") and r.get("greed") is not None}
                    break
                except Exception as e:  # noqa: BLE001
                    logger.warning("行业贪婪拉取失败 %s: %s", gc, repr(e)[:60])
                    time.sleep(2 * (i + 1))
            time.sleep(0.15)
    except Exception as e:  # noqa: BLE001
        logger.warning("行业贪婪数据加载异常: %s", e)
    _cache_set("industry_greed", out)
    return out


def load_industry_px(ttl: int = 3600) -> Dict[str, Dict[str, float]]:
    """加载行业 ETF 价格 {etf6: {date: close}}（tushare fund_daily，2024-01 起）。"""
    cached = _cache_get("industry_px", ttl)
    if cached is not None:
        return cached
    out: Dict[str, Dict[str, float]] = {}
    codes = {i["etf_code"] for i in INDUSTRY_POOL} | set(DEFENSE_CODES)
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        for c in sorted(codes):
            ts = c + (".SH" if c.startswith("5") else ".SZ")
            df = None
            for i in range(3):
                try:
                    df = pro.fund_daily(ts_code=ts, start_date="20240101", end_date=datetime.now().strftime("%Y%m%d"))
                    break
                except Exception as e:  # noqa: BLE001
                    logger.warning("行业价格拉取失败 %s: %s", ts, repr(e)[:60])
                    time.sleep(2 * (i + 1))
            if df is None or df.empty:
                continue
            m: Dict[str, float] = {}
            for d, v in zip(df["trade_date"], df["close"]):
                ds = str(d)
                if len(ds) == 8:
                    ds = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"
                m[ds] = float(v)
            out[c] = m
            time.sleep(0.15)
    except Exception as e:  # noqa: BLE001
        logger.warning("行业 ETF 价格加载异常: %s", e)
    _cache_set("industry_px", out)
    return out


def _account_summary() -> Dict[str, float]:
    """黄金坑模拟盘账户摘要（资金池视图口径）；DB 不可用回退初始 25 万。"""
    try:
        from sqlalchemy import text as _text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            row = db.execute(_text(
                "SELECT initial_capital, available_cash, frozen_cash FROM paper_account_info WHERE account_id = 'golden_pit'"
            )).fetchone()
        finally:
            db.close()
        if row:
            return {
                "initial_capital": float(row.initial_capital or 0),
                "cash": float(row.available_cash or 0) + float(row.frozen_cash or 0),
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("读取黄金坑账户摘要失败: %s", e)
    return {"initial_capital": 250000.0, "cash": 250000.0}


# ── 信号（纯函数）──
def percentile_250(hist: List[float], g: float) -> Optional[float]:
    recent = hist[-250:]
    if not recent:
        return None
    return sum(1 for v in recent if v <= g) / len(recent)


def drawdown_n(px: Dict[str, float], as_of: str, lookback: int = 60) -> float:
    days = [x for x in sorted(px) if x <= as_of]
    if len(days) < 2:
        return 0.0
    closes = [px[x] for x in days[-lookback:]]
    hi = max(closes)
    return closes[-1] / hi - 1.0


def industry_signal(ind: Dict[str, Any], greed_map: Dict[str, float], px: Dict[str, float],
                    as_of: str, pit_pct: float, drawdown_pct: float, entry_cap: float) -> Dict[str, Any]:
    """计算单个行业当日信号（纯函数，可单测）。

    贪婪值取 as_of 当日或最近可用值（arkvol 序列滞后一天）；贪婪历史<20 天仅价格触发。
    """
    gc = ind.get("greed_code", "")
    items = sorted((k, v) for k, v in (greed_map or {}).items() if k <= as_of)
    hist = [v for _, v in items]
    g = items[-1][1] if items else None
    pct = percentile_250(hist, g) if g is not None and len(hist) >= 20 else None
    dd = drawdown_n(px, as_of)
    overheat = (pct is not None and pct > entry_cap)
    in_pit = (dd <= -drawdown_pct) and (pct is None or pct <= pit_pct) and not overheat
    return {"greed": g, "greed_pct": pct, "drawdown": dd, "in_pit": in_pit, "overheat": overheat}


# ── 资金池裁决（纯函数）──
def ration(plans: List[Dict[str, Any]], available_cash: float) -> Dict[str, Any]:
    """按 (priority) 从高到低逐个分配计划金额，额度滚动次日。

    plans: [{"id", "priority", "amount", "window"}]；window 含 leftover 时由调用方累计。
    返回 {"allocations": [{"id","actual"}], "cut_items": [{"id","skipped"}], "total_actual", "total_planned"}。
    """
    ordered = sorted(plans, key=lambda x: (x.get("priority", 99), x.get("id", "")))
    allocations = []
    cut_items = []
    remaining = max(0.0, available_cash)
    for p in ordered:
        amt = min(float(p.get("amount", 0.0)), remaining)
        if amt >= 1e-6:
            allocations.append({"id": p["id"], "actual": round(amt, 2), "priority": p.get("priority", 99)})
            remaining -= amt
        else:
            cut_items.append({"id": p["id"], "skipped": round(float(p.get("amount", 0.0)), 2),
                              "priority": p.get("priority", 99), "reason": "cash_exhausted"})
    return {
        "allocations": allocations,
        "cut_items": cut_items,
        "total_actual": round(sum(a["actual"] for a in allocations), 2),
        "total_planned": round(sum(float(p.get("amount", 0.0)) for p in ordered), 2),
    }


# ── 窗口状态（dry-run 引擎: 内存 + JSON 持久化）──
def _load_state() -> Dict[str, Any]:
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取行业窗口状态失败: %s", e)
    return {"windows": {}, "exited": [], "last_as_of": None}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, default=str)
    except Exception as e:  # noqa: BLE001
        logger.warning("保存行业窗口状态失败: %s", e)


def save_industry_state(state: Dict[str, Any]) -> None:
    """保存行业窗口状态（execute 模式成交后由调用方落盘，避免未成交先推进）。"""
    _save_state(state)


def _trading_days_between(px: Dict[str, float], a: str, b: str) -> int:
    days = [x for x in sorted(px) if a <= x <= b]
    return max(0, len(days) - 1)


def advance_industry_windows(as_of: str, signals: Dict[str, Dict[str, Any]], px: Dict[str, Dict[str, float]],
                             pool: List[Dict[str, Any]], cfg: Dict[str, Any],
                             nav: float, state: Optional[Dict[str, Any]] = None,
                             execute: bool = False) -> Dict[str, Any]:
    """行业轨日推进（dry-run 默认；execute=true 时返回可下单指令）。

    返回 {windows, plans, exits, cut_items, planned_total, actual_total, notes}。
    """
    if state is None:
        state = _load_state()
    # 当日幂等: 同一天已推进过（execute 或 dry-run）则重放今日记录，不重复推进/重复下单
    if state.get("last_as_of") == as_of:
        today = state.get("today", {})
        return {
            "windows": state.get("windows", {}),
            "plans": today.get("plans", []),
            "allocations": today.get("allocations", []),
            "exits": today.get("exits", []),
            "cut_items": today.get("cut_items", []),
            "planned_total": today.get("planned_total", 0.0),
            "actual_total": today.get("actual_total", 0.0),
            "notes": today.get("notes", []),
            "state": state,
            "replayed": True,
        }
    windows: Dict[str, Dict[str, Any]] = state.get("windows", {})
    max_total = nav * float(cfg["max_total_pct"])
    plans: List[Dict[str, Any]] = []
    exits: List[Dict[str, Any]] = []
    notes: List[str] = []
    cut_items: List[Dict[str, Any]] = []
    today_exit_ids = set()

    # 1) 未入坑重置 + in_pit 累积（同日重复推进不重复计数: last_advance 守卫）
    in_pit_ids = {iid for iid, sig in signals.items() if sig["in_pit"]}
    for iid, w in list(windows.items()):
        if w.get("status") in ("exited", "closed"):
            continue
        if iid not in in_pit_ids:
            w["pit_days"] = 0

    # 2) 开窗
    for ind in pool:
        iid = ind["id"]
        sig = signals.get(iid)
        if not sig:
            continue
        w = windows.get(iid)
        if w and w.get("status") not in ("exited", "closed"):
            if sig["in_pit"] and w.get("last_advance") != as_of:
                w["pit_days"] = w.get("pit_days", 0) + 1
                w["last_advance"] = as_of
            continue
        if not sig["in_pit"]:
            continue
        # 无窗口/已关闭: 重新计数
        if w is None or w.get("status") in ("exited", "closed"):
            w = {"win_start": as_of, "pit_days": 1, "win_day": 0, "invested": 0.0,
                 "leftover": 0.0, "qty": 0.0, "cost": 0.0, "status": "signal",
                 "max_total": max_total, "last_advance": as_of}
            windows[iid] = w
            continue
    for iid, w in list(windows.items()):
        if w.get("status") == "signal" and w.get("pit_days", 0) >= int(cfg["min_days"]):
            w["status"] = "accumulating"
            notes.append(f"🕳 {INDUSTRY_BY_ID.get(iid, {}).get('name', iid)} 确认入坑，开启 DCA 窗口")

    # 3) 当日计划（DCA 权重 + 未分配滚动）
    cash_floor = nav * float(cfg["cash_min_pct"])
    for iid, w in list(windows.items()):
        if w.get("status") != "accumulating":
            continue
        day = w["win_day"]
        if day >= int(cfg["win_days"]):
            w["status"] = "held"
            continue
        dw = INDUSTRY_DCA_WEIGHTS[day] if day < len(INDUSTRY_DCA_WEIGHTS) else 0.0
        remaining = w["max_total"] - w["invested"]
        plan = min(remaining, w["max_total"] * dw + w["leftover"])
        if plan >= 1e-6:
            plans.append({"id": iid, "priority": INDUSTRY_BY_ID.get(iid, {}).get("priority", 99),
                          "amount": round(plan, 2), "window": w})

    # 4) 资金池裁决
    available = max(0.0, nav - cash_floor)
    res = ration(plans, available)
    actual_by_id = {a["id"]: a["actual"] for a in res["allocations"]}
    for iid, w in list(windows.items()):
        if w.get("status") != "accumulating":
            continue
        plan = next((p for p in plans if p["id"] == iid), None)
        if plan is None:
            continue
        act = actual_by_id.get(iid, 0.0)
        if act >= 1e-6:
            etf = INDUSTRY_BY_ID.get(iid, {}).get("etf_code")
            pxd = px.get(etf, {}).get(as_of) if etf else None
            w["invested"] += act
            w["leftover"] = max(0.0, plan["amount"] - act)
            if pxd:
                w["qty"] = w.get("qty", 0.0) + act / pxd
                w["cost"] = w.get("cost", 0.0) + act
            w["last_px"] = pxd
        else:
            w["leftover"] = w.get("leftover", 0.0) + plan["amount"]
            cut_items.append({"id": iid, "skipped": round(plan["amount"], 2),
                              "priority": plan["priority"], "reason": "cash_exhausted"})
        w["win_day"] += 1

    # 5) 出场判定（窗口完成后 TP / 时间止损 / 满仓止损）
    for iid, w in list(windows.items()):
        if w.get("status") not in ("accumulating", "held"):
            continue
        if w["win_day"] < int(cfg["win_days"]):
            continue
        w["status"] = "held"
        etf = INDUSTRY_BY_ID.get(iid, {}).get("etf_code")
        pxd = px.get(etf, {}).get(as_of) if etf else None
        if not pxd or w.get("qty", 0.0) <= 0:
            continue
        avg = w["cost"] / w["qty"]
        tp = pxd >= avg * (1 + float(cfg["tp_pct"]))
        fully_in = w["invested"] >= w["max_total"] * 0.99
        sl = fully_in and pxd <= avg * (1 - float(cfg["stop_loss"]))
        days_held = _trading_days_between(px.get(etf, {}), w["win_start"], as_of)
        time_over = days_held >= int(cfg["win_days"]) + int(cfg["time_exit_days"])
        if tp or sl or time_over:
            reason = "TP" if tp else ("SL" if sl else "TO")
            ret = (pxd - avg) / avg
            exits.append({"id": iid, "name": INDUSTRY_BY_ID.get(iid, {}).get("name", iid),
                          "etf_code": etf, "qty": round(w.get("qty", 0.0), 4),
                          "start": w["win_start"], "end": as_of, "ret": ret,
                          "invested": w["invested"], "reason": reason, "win_day": w["win_day"]})
            w["status"] = "exited"
            w["exit_reason"] = reason
            w["exit_ret"] = ret
            today_exit_ids.add(iid)
            notes.append(f"🏁 {INDUSTRY_BY_ID.get(iid, {}).get('name', iid)} 出场[{reason}] 收益 {ret*100:+.2f}%")

    state["windows"] = {k: v for k, v in windows.items() if v.get("status") != "exited"}
    if today_exit_ids:
        state.setdefault("exited", []).extend(exits)
        state["exited"] = state["exited"][-200:]
    state["last_as_of"] = as_of
    state["today"] = {
        "as_of": as_of,
        "plans": [dict(p) for p in plans],
        "allocations": res.get("allocations", []),
        "exits": exits,
        "cut_items": res.get("cut_items", []),
        "planned_total": res.get("total_planned", 0.0),
        "actual_total": res.get("total_actual", 0.0),
        "notes": notes,
    }
    return {
        "windows": {k: v for k, v in windows.items()},
        "plans": plans,
        "allocations": res.get("allocations", []),
        "exits": exits,
        "cut_items": res.get("cut_items", []),
        "planned_total": res.get("total_planned", 0.0),
        "actual_total": res.get("total_actual", 0.0),
        "notes": notes,
        "state": state,
    }


def industry_monitor_snapshot(as_of: Optional[str] = None, advance: Optional[bool] = None) -> Dict[str, Any]:
    """全行业监测快照（industries[] + cash_pool），供 status 接口与前端面板。失败返回空结构。

    advance=None: 执行模式(industry_execute=true)下只读不推进（窗口由 DCA 任务推进），
                  否则 dry-run 模拟推进（当日幂等，重复调用重放今日记录）。
    advance 显式传入可覆盖。
    """
    try:
        as_of = as_of or datetime.now().strftime("%Y-%m-%d")
        cfg = get_industry_config()
        execute_mode = bool(cfg.get("enabled")) and bool(cfg.get("execute"))
        do_advance = bool(cfg.get("enabled")) and (advance if advance is not None else (not execute_mode))
        cache_key = f"industry_snap:{do_advance}"
        snap_cached = _cache_get(cache_key, 120)
        if snap_cached and snap_cached.get("as_of") == as_of:
            return snap_cached
        greed = load_industry_greed()
        px = load_industry_px()
        acct = _account_summary()
        nav = acct.get("cash", acct.get("initial_capital", 250000.0))

        signals: Dict[str, Dict[str, Any]] = {}
        industries: List[Dict[str, Any]] = []
        for ind in cfg.get("pool", []):
            iid = ind["id"]
            sig = industry_signal(ind, greed.get(ind.get("greed_code", ""), {}),
                                  px.get(ind.get("etf_code", ""), {}), as_of,
                                  float(cfg["pit_pct"]), float(cfg["drawdown_pct"]), float(cfg["entry_cap"]))
            signals[iid] = sig
            close = px.get(ind.get("etf_code", ""), {}).get(as_of)
            industries.append({
                "id": iid, "name": ind["name"], "greed_code": ind.get("greed_code"),
                "etf_code": ind.get("etf_code"), "priority": ind.get("priority", 99),
                "close": close, "greed": sig["greed"], "greed_pct": sig["greed_pct"],
                "drawdown": round(sig["drawdown"], 4), "in_pit": sig["in_pit"],
                "overheat": sig["overheat"], "window_day": 0, "planned_amount": 0.0,
                "actual_amount": 0.0, "total_invested": 0.0,
            })

        if do_advance:
            # 窗口进度与当日计划（dry-run 模拟推进；当日幂等）
            adv = advance_industry_windows(as_of, signals, px, cfg.get("pool", []), cfg, nav)
        else:
            # 执行模式只读视图：读取已落盘状态，今日记录由 DCA 任务写入
            state = _load_state()
            today = state.get("today", {}) if state.get("last_as_of") == as_of else {}
            adv = {
                "windows": state.get("windows", {}),
                "plans": today.get("plans", []),
                "allocations": today.get("allocations", []),
                "exits": today.get("exits", []),
                "cut_items": today.get("cut_items", []),
                "planned_total": today.get("planned_total", 0.0),
                "actual_total": today.get("actual_total", 0.0),
                "notes": today.get("notes", []),
            }
        win_by_id = {k: v for k, v in adv["windows"].items()}
        plan_by_id = {p["id"]: p for p in adv["plans"]}
        actual_by_id = {a["id"]: a["actual"] for a in adv["allocations"]} if "allocations" in adv else {}
        for it in industries:
            iid = it["id"]
            w = win_by_id.get(iid)
            if w:
                it["window_day"] = w.get("win_day", 0)
                it["total_invested"] = round(w.get("invested", 0.0), 2)
            p = plan_by_id.get(iid)
            if p:
                it["planned_amount"] = round(p["amount"], 2)
                it["actual_amount"] = round(actual_by_id.get(iid, 0.0), 2)

        cash_floor = nav * float(cfg["cash_min_pct"])
        cash_pool = {
            "total_nav": round(nav, 2),
            "cash": round(acct.get("cash", nav), 2),
            "cash_min_pct": float(cfg["cash_min_pct"]),
            "cash_floor": round(cash_floor, 2),
            "available_cash": round(max(0.0, nav - cash_floor), 2),
            "planned_total": round(adv["planned_total"], 2),
            "actual_total": round(adv["actual_total"], 2),
            "cut_items": adv["cut_items"][:10],
            "enabled": bool(cfg["enabled"]),
            "execute": execute_mode,
            "dry_run": not execute_mode,
        }
        result = {"as_of": as_of, "enabled": bool(cfg["enabled"]), "industries": industries, "cash_pool": cash_pool,
                  "notes": adv["notes"]}
        _cache_set(cache_key, result)
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("全行业监测快照生成失败: %s", e)
        return {"as_of": as_of or "", "enabled": False, "industries": [], "cash_pool": {}, "notes": [], "error": str(e)}


def format_monitor_text(snap: Dict[str, Any]) -> str:
    """行业监测文本摘要（QQ 通知/报告兼容）。"""
    if not snap or not snap.get("industries"):
        return "（全行业监测无数据）"
    cp = snap.get("cash_pool", {})
    lines = [f"🏭 全行业监测 · {snap.get('as_of', '')}",
             f"   资金池: 净值¥{cp.get('total_nav', 0):.0f} 可用¥{cp.get('available_cash', 0):.0f} 计划¥{cp.get('planned_total', 0):.0f}→实际¥{cp.get('actual_total', 0):.0f}" +
             (f" 裁剪{len(cp.get('cut_items', []))}项" if cp.get("cut_items") else "")]
    for it in snap.get("industries", []):
        if not it.get("in_pit"):
            continue
        lines.append(f"   🕳 {it['name']} 分位{it.get('greed_pct') or '-':} 回撤{it.get('drawdown', 0)*100:.0f}% "
                     f"窗口第{it.get('window_day', 0)}天 计划¥{it.get('planned_amount', 0):.0f}→实际¥{it.get('actual_amount', 0):.0f} 累计¥{it.get('total_invested', 0):.0f}")
    for n in snap.get("notes", [])[-5:]:
        lines.append(f"   {n}")
    return "\n".join(lines)
