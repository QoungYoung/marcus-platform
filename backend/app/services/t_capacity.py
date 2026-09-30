# -*- coding: utf-8 -*-
"""做T仓位两分法（狼大语料）：**底仓 ≥65% + 日内做T仓 20%**。

语料依据（docs/wolf-behavior-blueprint.md:205 / 442 参数#1）：
    2026-09-03「所以我这里**仓位不会低于65%收盘，日内做T仓位20%**…」
蓝图 205 行另有一处阶段结构「50% 底仓 + 30% 日内T + 20% 黑天鹅」——
**用户 2026-09-17 拍板取"底仓≥65% + 日内T 20%"**，本模块即按此实现。

为什么要它（2026-09-17 实测）：回放里平均只投出 **43.9%**、单笔买入中位仅 **200 股**
（AI 自选量），资金投不出去 ⇒ 池子涨 7% 的月份账户只做到 -1.8%。
两分法的作用就是**给下单量一个锚**：
  · **底仓（≥65%）**：建仓/加仓按"距 65% 目标的缺口"分步补（每次不超过缺口的 1/3），
    并给单笔设一个**最小有效规模**，治"200 股建仓"；
  · **日内T仓（≤20%）**：做T腿的**增量名义额**受 20% 额度约束，超了就收敛（不是整单拒绝）。

口径与安全：
  · **纯函数**（`two_tier` / `clamp_buy_volume`）便于单测；取数副作用只在 `account_equity`；
  · 全部由 `WOLF_T_CAPACITY_2TIER`（**默认关**）门控 ⇒ 关时行为与加本模块前逐字一致；
  · 取数失败一律 **fail-open**（返回 None → 调用方不施加额外约束）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional, Tuple, List

BASE_FLOOR_PCT = 65.0        # 语料：仓位不会低于 65% 收盘（底仓下限目标）
T_SLEEVE_PCT = 20.0          # 语料：日内做T仓位 20%
def _build_step() -> float:
    """建仓分几步补满缺口（默认 3 = 每次补缺口 1/3）。

    2026-09-21 用户拍板方案③：回测用 **2**（= 狼大 2026-06-15「突破下跌趋势第一根开头我加一半
    突破后的第二根红K尾盘 打满」的两批口径）。库内默认 3 ⇒ 生产零影响。
    """
    try:
        v = float(os.getenv("WOLF_BUILD_STEP_FRACTION", "3") or 3)
        return v if v >= 1 else 3.0
    except Exception:
        return 3.0


BUILD_STEP_FRACTION = 3.0    # 兼容常量（旧引用/单测）；实际取值走 _build_step()
MIN_BUILD_NOTIONAL = 8000.0  # 单笔建仓最小有效规模（治"200 股建仓"；回测实测中位 6983 元）

ENABLED = str(os.getenv("WOLF_T_CAPACITY_2TIER", "0")).strip().lower() in ("1", "true", "yes", "on")
_STATS: Dict[str, Any] = {"calls": 0, "clamped_t": 0, "boosted_build": 0, "skipped": 0, "err": 0}
_EQ_CACHE: Dict[str, Any] = {"at": 0.0, "v": None}


def enabled() -> bool:
    return ENABLED


def stats() -> Dict[str, Any]:
    return dict(_STATS)


def stats_reset() -> None:
    for _k in _STATS:
        _STATS[_k] = 0



# ── L3：**目标底仓 = 点位→仓位对照表**（语料，2026-09-25 §9.32/§9.36）────────────────
# 语料（逐字，blueprint 仓位管理行）：「3888 收盘没站上 50%、3850 收盘跌破 35%、
#   3816 企稳加回 50%、站稳 3922-3930 上 70%」＋「按阶段定档（主升 75%+／调整 50%／有风险 30%／下跌不做）
#   ＋ **收盘定档**」⇒ 目标仓位是**收盘价对关键位的阶梯函数**，不是固定 65%。
# 机制：`WOLF_TARGET_TABLE='3816:50,3850:35,3888:50,3930:70'`（阈值:目标%，逗号分隔；库内默认空=关）。
#   取值规则：`close ≥ 最大阈值 ≤ close 的那一档`（低于全部阈值 ⇒ 取最低档的值）。
# 为什么单独成函数：点位随行情变（我们回测窗口的上证在 4100+，他那张表的点位是另一段的）
#   ⇒ **点位必须可配**，且取数/判断用**收盘**（他的口径）。

# ── L3：**腿型额度比例**（正T买 : 趋势突破建仓 = 1 : ratio；2026-09-25 §9.44）──────────
# 用户拍板（2026-09-25）：抄 **T5 的 1:1.3**（正T买 : 趋势突破）—— T5 是四臂里唯一赚钱的，
#   其资金结构是"两条腿都正、且正 alpha 腿拿到足够名义"（§9.24）。
# 口径（本实现）：**正T买当日累计额度 = 上一交易日建仓腿名义 / ratio**（拿不到参照 ⇒ 不介入，fail-open）。
#   为什么用"上一交易日"：同日先后顺序不定，用前一日参照保证额度非零且稳定；
#   为什么是"累计"：③ 只管单笔，本规则管当日总量（与他「10% 仓位做T」的仓位口径一致）。
# 开关：`WOLF_LEG_RATIO_ZT_TB`（默认 0 = 关）；可与 `WOLF_ZT_DAY_BUDGET` 叠加（取更严）。
def leg_ratio() -> float:
    """正T买 : 建仓腿 的目标比例分母（1:ratio）；0 = 关。"""
    try:
        v = float(os.getenv("WOLF_LEG_RATIO_ZT_TB", "0") or 0)
        return v if v > 0 else 0.0
    except Exception:
        return 0.0


def ratio_zt_budget(ref_build_notional: float, ratio: Optional[float] = None) -> float:
    """按比例算出的"当日正T买额度"（元）；无参照/未开 ⇒ 0（调用方据此不介入）。"""
    r = leg_ratio() if ratio is None else float(ratio or 0)
    if r <= 0:
        return 0.0
    return max(0.0, float(ref_build_notional or 0)) / r


def clamp_ratio_budget(volume: int, price: float, used_today: float,
                       ref_build_notional: float, ratio: Optional[float] = None,
                       lot: int = 100) -> Tuple[int, str]:
    """腿型比例额度（纯函数）：当日正T买累计 ≤ 上一交易日建仓腿名义 / ratio。只缩不放大。"""
    v = int(volume or 0)
    p = float(price or 0)
    r = leg_ratio() if ratio is None else float(ratio or 0)
    if r <= 0:
        return v, "腿型比例未开（WOLF_LEG_RATIO_ZT_TB=0）"
    if v <= 0 or p <= 0:
        return v, "腿型比例未介入（无量/无价）"
    budget = ratio_zt_budget(ref_build_notional, r)
    if budget <= 0:
        return v, "腿型比例未介入（无参照建仓名义 ≤0）"
    remain = budget - float(used_today or 0)
    if remain <= 0:
        return 0, "腿型比例额度已用尽（已用 %.0f / 额度 %.0f 元 = 参照 %.0f / %.2f）" % (
            used_today, budget, ref_build_notional, r)
    if p * v <= remain:
        return v, "腿型比例额度内（用 %.0f/%.0f）" % (used_today + p * v, budget)
    _lot = max(int(lot or 100), 1)
    v2 = int(remain / p / _lot) * _lot
    return max(v2, 0), "腿型比例额度：剩余 %.0f 元（参照 %.0f/%.2f）⇒ %d→%d 股" % (
        remain, ref_build_notional, r, v, v2)


def _ratio_state_path() -> str:
    d = os.environ.get("DATA_DIR", "")
    return os.path.join(d or "/tmp", "t_leg_ratio_state.json")


def ratio_state_load() -> Dict[str, float]:
    """读 {交易日: 建仓腿名义}（用于"上一交易日"参照）；坏文件 ⇒ {}（fail-open，但**留痕**）。"""
    try:
        with open(_ratio_state_path(), encoding="utf-8") as f:
            j = json.load(f)
        days = j.get("days") if isinstance(j, dict) else None
        return {str(k): float(v) for k, v in (days or {}).items()} if isinstance(days, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print("[t-capacity] 腿型比例状态读取失败(视为空): %s: %s" % (type(e).__name__, str(e)[:70]), flush=True)
        return {}


def ratio_state_add(day: str, notional: float, path: str = "") -> None:
    """累加当日建仓腿名义并落盘（同日多笔累加；跨日保留历史，取最近的前一日用）。"""
    try:
        st = ratio_state_load()
        st[str(day)] = float(st.get(str(day), 0.0)) + float(notional or 0)
        keep = dict(sorted(st.items())[-30:])          # 只留最近 30 天
        p = path or _ratio_state_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"days": keep}, f, ensure_ascii=False)
    except Exception as e:
        print("[t-capacity] 腿型比例状态落盘失败: %s: %s" % (type(e).__name__, str(e)[:70]), flush=True)


def ratio_ref_notional(today: str) -> float:
    """取**最后一个早于 today** 的建仓腿名义（即"上一交易日"）；无 ⇒ 0。"""
    st = ratio_state_load()
    prev = [d for d in st if str(d) < str(today)]
    if not prev:
        return 0.0
    return float(st[max(prev)])

def parse_target_table(spec: Optional[str] = None) -> List[Tuple[float, float]]:
    """解析 `'3816:50,3850:35,...'` → [(阈值, 目标%)...]（按阈值升序）；非法项丢弃（fail-open）。"""
    raw = spec if spec is not None else os.getenv("WOLF_TARGET_TABLE", "")
    out: List[Tuple[float, float]] = []
    for part in str(raw or "").replace("，", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        a, b = part.split(":", 1)
        try:
            out.append((float(a), float(b)))
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def level_target_pct(close: Optional[float], table: Optional[List[Tuple[float, float]]] = None) -> Optional[float]:
    """收盘价 → 目标底仓 %（阶梯查表）；无表/收盘不可用 ⇒ None（fail-open，不臆造）。"""
    t = parse_target_table() if table is None else table
    if not t:
        return None
    try:
        c = float(close or 0)
    except Exception:
        return None
    if c <= 0:
        return None
    pick = t[0][1]
    for th, pct in t:
        if c >= th:
            pick = pct
        else:
            break
    return float(pick)


def target_drives_floor() -> bool:
    """目标表是否**驱动**建仓缺口（`WOLF_TARGET_TABLE_DRIVES_FLOOR`，库内默认 0）。
    关 ⇒ 建仓缺口仍用 `BASE_FLOOR_PCT`（65%，逐字旧行为）；开且有表 ⇒ 用点位表的目标。"""
    return str(os.getenv("WOLF_TARGET_TABLE_DRIVES_FLOOR", "0")).strip().lower() in ("1", "true", "yes", "on")



# ── L3：**阶段档目标**（点位表拿不到时的备选；语料同源，2026-09-25 §9.44）──────────────
# 语料（逐字，blueprint 仓位管理行）：「按阶段定档（**主升 75%+／调整 50%／有风险 30%／下跌不做**）
#   ＋ **收盘定档**」。⇒ 拿不到他的点位表时，用**阶段→目标%**的映射做备选，数字全部来自语料。
# 开关：`WOLF_TARGET_PHASE_TABLE`（形如 `build:75,t_only:50,defense:30,exit:0`；库内默认空 = 关）。
#   键取自系统已有的 L2 档位（operation）：build / t_only / defense / exit。
# 优先级：**点位表 > 阶段档 > BASE_FLOOR_PCT(65)**（前两者都要 `WOLF_TARGET_TABLE_DRIVES_FLOOR=1` 才驱动缺口）。
def parse_phase_table(spec: Optional[str] = None) -> Dict[str, float]:
    """解析 `'build:75,t_only:50,defense:30,exit:0'` → {档位: 目标%}；非法项丢弃（fail-open）。"""
    raw = spec if spec is not None else os.getenv("WOLF_TARGET_PHASE_TABLE", "")
    out: Dict[str, float] = {}
    for part in str(raw or "").replace("，", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, v = part.split(":", 1)
        try:
            out[k.strip().lower()] = float(v)
        except Exception:
            continue
    return out


def phase_target_pct(operation: Optional[str], table: Optional[Dict[str, float]] = None) -> Optional[float]:
    """L2 档位（operation）→ 目标底仓 %；无表/档位未知 ⇒ None（fail-open，不臆造）。"""
    t = parse_phase_table() if table is None else table
    if not t:
        return None
    return t.get(str(operation or "").strip().lower())


def base_floor_pct_for(operation: Optional[str] = None, close: Optional[float] = None) -> float:
    """生效的底仓目标 %（优先级：点位表 > 阶段档 > 65）。驱动开关关 ⇒ 恒 65（逐字旧行为）。"""
    if not target_drives_floor():
        return float(BASE_FLOOR_PCT)
    v = level_target_pct(close)
    if v is not None:
        return float(v)
    v2 = phase_target_pct(operation)
    if v2 is not None:
        return float(v2)
    return float(BASE_FLOOR_PCT)

def base_floor_pct(close: Optional[float] = None) -> float:
    """当前生效的"底仓目标 %"：表驱动开且有表 ⇒ 点位表；否则 `BASE_FLOOR_PCT`（65）。"""
    if target_drives_floor():
        v = level_target_pct(close)
        if v is not None:
            return float(v)
    return float(BASE_FLOOR_PCT)


def two_tier(equity: float, pos_value: float, close: Optional[float] = None) -> Dict[str, float]:
    """两分法额度（纯函数）：底仓目标 / 日内T 预算 / 底仓缺口。

    `close` 只在"点位表驱动"开关打开时参与（决定目标 %）；默认 None ⇒ 逐字旧行为（65%）。
    """
    eq = max(float(equity or 0), 0.0)
    pv = max(float(pos_value or 0), 0.0)
    base_target = eq * base_floor_pct(close) / 100.0
    t_budget = eq * T_SLEEVE_PCT / 100.0
    return {"equity": eq, "pos_value": pv, "base_target": base_target,
            "base_gap": max(0.0, base_target - pv), "t_budget": t_budget,
            "exposure_pct": (pv / eq * 100.0) if eq > 0 else 0.0}


def clamp_buy_volume(volume: int, price: float, equity: float, pos_value: float,
                     is_build: bool, lot: int = 100) -> Tuple[int, str]:
    """按两分法收敛买腿股数（纯函数）。

    is_build=True（无底仓建仓）：以"缺口/3"为上限、以 MIN_BUILD_NOTIONAL 为下限抬量；
    is_build=False（有底仓=做T/加仓）：上限 = 日内T 预算内剩余额度。
    返回 (股数, 归因说明)；`volume<=0` 或 `price<=0` 时原样返回（不臆造）。
    """
    v = int(volume or 0)
    p = float(price or 0)
    if v <= 0 or p <= 0:
        return v, "两分法未介入（无量/无价）"
    t = two_tier(equity, pos_value)
    if t["equity"] <= 0:
        return v, "两分法未介入（总资产不可用）"
    lot = max(int(lot or 100), 1)
    if is_build:
        cap = int((t["base_gap"] / _build_step()) / p / lot) * lot
        import math as _m
        floor = int(_m.ceil(MIN_BUILD_NOTIONAL / p / lot)) * lot   # 向上取整到整手，确保达到最小有效规模
        if cap <= 0:
            return 0, "两分法：底仓已达 65% 目标，本次建仓不再放行（缺口=0）"
        new = max(min(v, cap), min(floor, cap))
        why = ("两分法建仓：缺口 %.0f 元→本次上限 %d 股；最小有效规模 %d 股"
               % (t["base_gap"], cap, floor))
        if new > v:
            _STATS["boosted_build"] += 1
            why += "；**抬量** %d→%d 股" % (v, new)
        elif new < v:
            _STATS["clamped_t"] += 1
            why += "；收敛 %d→%d 股" % (v, new)
        return new, why
    cap_t = int(t["t_budget"] / p / lot) * lot
    if cap_t <= 0:
        _STATS["skipped"] += 1
        return 0, "两分法：日内T仓预算不足 1 手（20%% 额度已用尽）"
    if v > cap_t:
        _STATS["clamped_t"] += 1
        return cap_t, "两分法日内T仓：单笔收敛 %d→%d 股（≤20%% 额度 %.0f 元）" % (v, cap_t, t["t_budget"])
    return v, "两分法日内T仓：在 20%% 额度内（%.0f 元）" % t["t_budget"]


# ── B（2026-09-19 用户拍板）：卖侧也受"日内做T仓 20%"约束 ────────────────────────
# 语料：2026-09-03「仓位不会低于65%收盘，**日内做T仓位20%**」；蓝图 205 行「50% 底仓 + 30% 日内T + 20% 黑天鹅」。
# 现状：t_capacity 的 20% 预算只作用于**买腿**（clamp_buy_volume），卖腿完全不受约束 ⇒ 实测
#   data/_bt_jan5 里 14 笔卖出 13 笔是止盈高抛、把底仓卖光（仓位长期 10~30%）。
# 口径：**当日该账户卖出名义额 ≤ equity × 20%**（跨标的累计，已含今日已卖）；**止损/破位豁免**
#   （语料"止血动作必须能执行"）。开关 `WOLF_T_CAPACITY_SELL`（库内默认关、回测开；随 2TIER 总闸）。
SELL_SIDE = str(os.getenv("WOLF_T_CAPACITY_SELL",
                          "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
_SELL_EXEMPT_KW = ("止损", "破位", "清仓", "强制", "风控", "避险", "减半", "derisk")


def sold_today_notional(account_id: str, day: str = "") -> float:
    """当日该账户卖出名义额（元）。取不到 → 0.0（fail-open：宁可放行也不误拦止血）。"""
    try:
        import psycopg2
        from datetime import datetime as _dt
        d8 = str(day or "").replace("-", "") or _dt.now().strftime("%Y%m%d")
        dsh = "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8])
        dsn = os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
        conn = psycopg2.connect(dsn, connect_timeout=4)
        try:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SET statement_timeout=4000")
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM paper_trades "
                        "WHERE account_id=%s AND direction='卖出' AND voided=0 AND created_at::date=%s",
                        (account_id, dsh))
            r = cur.fetchone()
            return float((r or [0])[0] or 0)
        finally:
            conn.close()
    except Exception:
        _STATS["err"] += 1
        return 0.0


# ── ②（2026-09-30 账本 §9.331 ✓ 用户「把 ② 也改掉」）：**保护/减仓类可穿透底仓止血** ✓ ─────
#   语料 ✓：「**止血动作必须能执行**」；病灶记录 ✓：「想减仓的 0107–0112 全被
#   『仅底仓无T仓可卖』拦掉，拖到 0113 尾盘才砍」✗／「快克智能被挡 3,701 次，3 月已实现 −4,279」✗
#   ⇒ 按腿型（不再靠关键词碰运气 ✓）豁免；开关 `WOLF_SELL_EXEMPT_REDUCE`（**库内默认 0 = 关** ✓）
_REDUCE_PROTECT_KINDS = {"custom_vwap_sell", "custom_support_sell", "custom_level_sell",
                         "custom_trail_sell", "wolf_defensive_t_reduce", "wolf_confirm_sell",
                         "wolf_boll_upper_sell", "wolf_board_half_sell",
                         "wolf_passive_stop_sell", "stop_loss", "wolf_early_swing_sell"}
_SELL_EXEMPT_REDUCE = str(os.getenv("WOLF_SELL_EXEMPT_REDUCE", "0")).strip().lower() \
    in ("1", "true", "yes", "on")


def clamp_sell_volume(volume: int, price: float, equity: float, sold_today: float = 0.0,
                      reason: str = "", is_stop_loss: bool = False, lot: int = 100,
                      trigger_kind: str = "") -> Tuple[int, str]:
    """按"日内T仓 20%"收敛卖腿股数（纯函数）。返回 (股数, 归因)；止损/破位豁免、开关关不动。"""
    v = int(volume or 0)
    p = float(price or 0)
    if v <= 0 or p <= 0:
        return v, "卖侧未介入（无量/无价）"
    if not SELL_SIDE:
        return v, "卖侧关(WOLF_T_CAPACITY_SELL=0)"
    if is_stop_loss or any(k in str(reason or "") for k in _SELL_EXEMPT_KW):
        return v, "卖侧豁免（止损/破位/风控类，止血必须能执行）"
    if _SELL_EXEMPT_REDUCE and str(trigger_kind or "") in _REDUCE_PROTECT_KINDS:
        return v, "卖侧豁免（保护/减仓类腿型=%s，止血必须能执行）" % trigger_kind
    eq = max(float(equity or 0), 0.0)
    if eq <= 0:
        return v, "卖侧未介入（总资产不可用）"
    budget = eq * T_SLEEVE_PCT / 100.0
    remain = max(0.0, budget - max(float(sold_today or 0.0), 0.0))
    lot = max(int(lot or 100), 1)
    cap = int(remain / p / lot) * lot
    if cap <= 0:
        _STATS["skipped"] += 1
        return 0, ("卖侧拦截：日内T仓预算（20%% = %.0f 元）已用尽（今日已卖 %.0f 元）"
                   % (budget, float(sold_today or 0.0)))
    if v > cap:
        _STATS["clamped_t"] += 1
        return cap, ("卖侧日内T仓：收敛 %d→%d 股（20%% 预算 %.0f 元，今日已卖 %.0f，余 %.0f）"
                     % (v, cap, budget, float(sold_today or 0.0), remain))
    return v, "卖侧日内T仓：在 20%% 预算内（余 %.0f 元）" % remain


# ── 底仓穿透（2026-09-19 用户拍板 A）────────────────────────────────────────────
# 语料：2025-11-23「高开下沿对应盘中不能跌破…**减仓到40%甚至更低**」；2025-08-13「缩量上涨后白线黄线
#   同时放量跌破开盘点位…马上**减仓到盘中50%**」⇒ 他**会减底仓**，「做T卖腿只卖 T 仓」不是他的口径。
# 现状（缺陷）：做T卖腿量 = 可卖 − 底仓 floor ⇒ 只剩底仓时恒为 0 ⇒ 标 blocked 且当日静默 ⇒
#   只能等到 14:45 破位口径一次性清仓。实测代价：SH600183 生益科技 jan10 那笔 −1,314、jan11 −1,640
#   （想减仓的 0107–0112 全被『仅底仓无T仓可卖』拦掉，拖到 0113 尾盘才砍）；同族记录见
#   `t_gateway.py:86`「快克智能因『仅底仓无T仓可卖』被挡 3,701 次，3 月已实现 −4,279」。
# 修法：**破位/减仓语义**的腿允许穿透底仓（盘中按『减半』、尾盘按『清仓』，复用 _stop_exit_volume 口径）；
#   普通做T卖腿（high_sell/custom_vwap_sell 无破位语义）仍只动 T 仓。开关 WOLF_SELL_BASE_EXEMPT（库内默认 0）。
BASE_EXEMPT = str(os.getenv("WOLF_SELL_BASE_EXEMPT", "0")).strip().lower() in ("1", "true", "yes", "on")

# ── 底仓穿透「收紧版」：只认破位/减仓**语义**，忽略「趋势转弱」（`WOLF_BASE_PENETRATE_STRICT`，
#    **库内默认 0** ⇒ 旧行为逐字不变；2026-09-25 用户拍板「单独上 (b)」）──────────────
#    量化（账本 §9.129，全臂日志 + 前复权行情）：
#      · 实跑 63 次底仓穿透**全部来自 struct_weak（趋势转弱）** ✗，那条「破位/减仓语义」关键词路径
#        **一次都没命中** ✗ ⇒ 该闸实际只剩"趋势转弱"在起作用；
#      · 穿透**之后**走势（去重 9 个事件）：+5 日中位 **+1.47%（为正 67%）**、+10 日中位 **+2.24%
#        （为正 78%）** ✗✗ ⇒ **多数情况卖在反弹前**（典型：0211 SZ002156 在 T14/T22/T26/T28 四臂重演，
#        卖后 +9.42%）✗。
#    置 1 ⇒ `struct_weak` 不再单独构成穿透理由（破位/减仓语义、kind 白名单、G4、止盈扩展照旧 ✓）
BASE_PENETRATE_STRICT = str(os.getenv("WOLF_BASE_PENETRATE_STRICT", "0")).strip().lower() in ("1", "true", "yes", "on")
_BASE_EXEMPT_KINDS = {"wolf_defensive_t_reduce"}      # 防御性减仓：本身就是『减仓避一下』语义
_BASE_EXEMPT_KW = ("破位", "减仓", "减半", "清仓", "derisk", "风控", "避险", "止损", "趋势转弱")
# ── 止盈/兑现类也允许穿透底仓（`WOLF_BASE_EXEMPT_TAKE`，**库内默认 0**；2026-09-22 起自动化开发线）──
# 为什么单独立一个开关而不并进上面：2026-09-19 拍板 A 的范围是「破位/减仓语义」，**刻意**把
#   普通做T卖腿（high_sell/custom_vwap_sell 无破位语义）留在"只动 T 仓"；2026-09-21 方案③ 又用
#   语料 2026-08-05「做T套利的仓位是做T套利的 底仓是底仓」加固了这条。所以扩展作用面必须显式开关、
#   默认关、由人拍板。
# 语料依据（支持扩展的那一侧）：
#   · 2026-09-01「**吃一口减一半**」（blueprint:56/151/296/318/397：减仓卖出 1,317 条 4.9%，"减半/兑现/清仓"）
#   · 2025-11-23「…**减仓到40%甚至更低**」、2025-08-13「…马上**减仓到盘中50%**」
#   · 账本已记录：「做T卖腿只卖 T 仓、底仓不可减」**不是他的口径**，是我们为"底仓保护"加的自设约束
#     （docs/wolf-buy-parameter-ledger.md 第 2755 行）
# 卖量口径**复用**已拍板的穿透口径 `_stop_exit_volume`（盘中减半 / 尾盘清仓），不另造。
# ⚠️ 侵蚀风险（必须知情）：反复"减半"会把底仓啃光（588170 事故同族）⇒ 开启后应配合
#   「每票每轮最多 N 笔」或"仅浮盈 ≥ 阈值"再观察；本开关只负责作用面，不负责节流。
_BASE_EXEMPT_TAKE = str(os.getenv("WOLF_BASE_EXEMPT_TAKE", "0")).strip().lower() in ("1", "true", "yes", "on")

# ── G4 被动止盈线的**结构化**穿透资格（2026-09-23；`WOLF_BASE_EXEMPT_G4` 库内默认 0）──────
# 病灶（实测，见账本 §8.16）：`wolf_passive_stop_sell`（狼大 2025-06-09「不破被动止盈根本不会卖」）
#   能不能穿底仓，**取决于 AI 有没有在成交 reason 里写「止损」二字** ——
#   因为 AI 看着腿型名（字面含 stop）把 G4 写成「被动**止损**卖腿触发…」，于是命中 `_BASE_EXEMPT_KW`。
#   ⇒ 语义由**错标签**承载：一旦有人「好心」把标签修正成「被动止盈」，G4 会**静默失去**该资格。
# 本开关让资格由**腿型**决定（与 2026-09-22 拍板 A′ 把 `t_protect` 改结构化同向）：
#   开启后 `wolf_passive_stop_sell` 无论 reason 怎么写、甚至没写「止损」也能穿底仓。
# ⚠️ **这会改变行为**（那些原本被挡的 G4 卖腿会开始减底仓）⇒ 库内默认 0、需显式开启；
#    开启前请先读账本 §8.16 与 §8.11（G4 的触发/成交/被拦漏斗）。
_G4_STRUCT = str(os.getenv("WOLF_BASE_EXEMPT_G4", "0")).strip().lower() in ("1", "true", "yes", "on")
_BASE_EXEMPT_G4_KINDS = {"wolf_passive_stop_sell"}
_BASE_EXEMPT_TAKE_KINDS = {"high_sell", "wolf_fib_target_sell", "wolf_profit_take_sell",
                           "wolf_dao_t_sell", "wolf_board_half_sell", "wolf_confirm_sell"}
_BASE_EXEMPT_TAKE_KW = ("高抛", "止盈", "吃一口", "兑现", "减半")
# ⚠️ 2026-09-30 收紧（账本 §9.299 ✓ 用户口径 ✓）：
#   「**浮盈≥3% 就兑现**」**只针对做T** ✓（**不是全仓** ✗）；
#   **全仓**只在「**突破／压力位／加速结束**」时减半 ✓
#   ⇒ 可动底仓的白名单收窄为：
#       压力位 `wolf_fib_target_sell`(0.618 ✓)／`wolf_boll_upper_sell`(BOLL 上轨 ✓，**新增** ✓)
#       加速   `wolf_board_half_sell`(板上 ✓)／`wolf_confirm_sell`(加速结束确认 ✓)
#   ⇒ 做T类**移出**（**只动 T 仓** ✓）：`wolf_profit_take_sell` ✗／`high_sell` ✗／`wolf_dao_t_sell` ✗
#   ⇒ **关键词表也收窄** ✗（原来含"兑现/高抛/止盈"⇒ 会把移出的腿**绕过** ✗）
if str(os.getenv("WOLF_BASE_EXEMPT_STRICT", "0")).strip().lower() in ("1", "true", "yes", "on"):
    _BASE_EXEMPT_TAKE_KINDS = {"wolf_fib_target_sell", "wolf_boll_upper_sell",
                               "wolf_board_half_sell", "wolf_confirm_sell"}
    _BASE_EXEMPT_TAKE_KW = ("压力位", "上轨", "板上", "加速结束", "突破")
# 节流：**每轮（自上次清仓以来）最多允许 N 次止盈类穿透**（默认 2，贴语料「一个票最多卖 2 笔」）。
#   为什么必须节流：穿透的卖量是"减半" ⇒ 不限次会把底仓啃光（588170 事故同族）。
#   0 = 不限次（不建议）；库内默认 2，仅在本扩展开关打开时才起作用。
_BASE_EXEMPT_TAKE_MAX = int(float(os.getenv("WOLF_BASE_EXEMPT_TAKE_MAX", "2") or 2))


def base_penetrate_allowed(trigger_kind: str = "", reason: str = "", struct_weak: bool = False) -> bool:
    """该卖腿是否允许穿透底仓（破位/减仓/**趋势转弱**语义；开关关时恒 False = 与历史逐位一致）。

    struct_weak（2026-09-19 深夜补，用户拍板 A 的原话是「破位/**趋势转弱**类减仓腿」）：
      个股已处于**下跌结构**（现价<MA20 且 MA10<MA20，见 wolf_stock_structure.is_downtrend）时，
      普通做T卖腿也允许减底仓 —— 否则只剩底仓的持仓在阴跌里**减不掉**，只能等 14:45 破位清仓砍在低点
      （实测 jan10/jan11 的"dip 买入 → low/mid 离场"亏损合计 −4,918 / −2,208，是最集中的亏损族）。
    """
    if not BASE_EXEMPT:
        return False
    if struct_weak and not BASE_PENETRATE_STRICT:
        return True          # 收紧版（WOLF_BASE_PENETRATE_STRICT=1）⇒ 趋势转弱**不再**单独构成穿透理由 ✓
    _k = str(trigger_kind or "").strip()
    _r = str(reason or "")
    if _k in _BASE_EXEMPT_KINDS:
        return True
    # G4 结构化资格（默认关）：让「被动止盈线跌破」由**腿型**承载，而不是靠 reason 里的「止损」两字
    if _G4_STRUCT and _k in _BASE_EXEMPT_G4_KINDS:
        return True
    if any(k in _r for k in _BASE_EXEMPT_KW):
        return True
    # 止盈/兑现类扩展作用面（开关关时恒 False ⇒ 与既有行为逐位一致）
    if _BASE_EXEMPT_TAKE and (_k in _BASE_EXEMPT_TAKE_KINDS or any(k in _r for k in _BASE_EXEMPT_TAKE_KW)):
        return True
    return False


def take_penetrate_used(account_id: str, symbol: str) -> int:
    """**本轮**（自上次清仓以来）已完成的止盈类卖出笔数。取数失败 ⇒ 返回 0（保守=当作没用过）。

    口径：把该票的成交按时间**倒序**扫，遇到"持仓归零"就停（那是上一轮的结尾）；
    期间 `direction=卖出` 且 reason 命中止盈关键词的计一笔。
    """
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL", ""), connect_timeout=4)
        try:
            conn.set_session(readonly=True, autocommit=True)
            cur = conn.cursor()
            cur.execute("SELECT direction, volume, reason FROM paper_trades "
                        "WHERE account_id=%s AND symbol=%s AND COALESCE(voided,0)=0 "
                        "ORDER BY created_at DESC, id DESC LIMIT 40", (account_id, symbol))
            rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return 0
    pos = 0
    used = 0
    for direction, volume, reason in rows:
        v = int(volume or 0)
        if str(direction) in ("卖出", "sell"):
            pos += v
            if any(k in str(reason or "") for k in _BASE_EXEMPT_TAKE_KW):
                used += 1
        else:
            pos -= v
            if pos <= 0:                      # 回到上一轮之前 ⇒ 停
                break
    return used


def take_penetrate_quota_ok(account_id: str, symbol: str) -> bool:
    """本轮止盈类穿透是否还有名额（`WOLF_BASE_EXEMPT_TAKE_MAX`；0 = 不限次）。"""
    if _BASE_EXEMPT_TAKE_MAX <= 0:
        return True
    return take_penetrate_used(account_id, symbol) < _BASE_EXEMPT_TAKE_MAX


def zhengt_cap_pct() -> float:
    """正T买单笔名义上限（占**总资产**百分比）—— `WOLF_ZHENGT_MAX_NOTIONAL`，库内默认 **0 = 关**。

    语料（逐字）：2026-03-31「必须是有底仓、自己一直关注的方向（没有底仓的不做）；**只用10%仓位做T**」；
    另见 2026-08-… 「用10%仓位做做T」/「用10%仓位或半仓内做T」。
    只作用于 `wolf_zheng_t_buy`（正T买腿），不碰建仓腿/条件腿。关 ⇒ 调用方逐字旧行为（生产零影响）。
    """
    try:
        v = float(os.getenv("WOLF_ZHENGT_MAX_NOTIONAL", "0") or 0)
        return v if v > 0 else 0.0
    except Exception:
        return 0.0


def clamp_zhengt_notional(volume: int, price: float, equity: Optional[float],
                          pct: Optional[float] = None, lot: int = 100) -> Tuple[int, str]:
    """正T买单笔名义上限（纯函数）：名义 ≤ pct% × 总资产，**向下取整到整手**。

    返回 (股数, 归因)；关（pct<=0）/ 无量无价 / 总资产不可用 ⇒ 原样返回（fail-open，不臆造）。
    实测依据（2026-09-25，账本 §9.18③）：正T买腿按名义占比分档，**>10% 那批的收益率也系统性更差**
    （T13 −1.99% vs −1.61%、T14 −2.19% vs −1.04%、T11 −2.32% vs −1.86%、T5 −1.64% vs +2.13%），
    故 10% 上限不只是"缩规模"，同时也在压掉最差的那批。
    """
    v = int(volume or 0)
    p = float(price or 0)
    _pct = zhengt_cap_pct() if pct is None else float(pct or 0)
    if _pct <= 0:
        return v, "正T名义上限未开（WOLF_ZHENGT_MAX_NOTIONAL=0）"
    if v <= 0 or p <= 0:
        return v, "正T名义上限未介入（无量/无价）"
    try:
        eq = float(equity or 0)
    except Exception:
        eq = 0.0
    if eq <= 0:
        return v, "正T名义上限未介入（总资产不可用）"
    _lot = max(int(lot or 100), 1)
    cap_amt = eq * _pct / 100.0
    cap_v = int(cap_amt / p / _lot) * _lot
    if cap_v <= 0:
        return 0, ("正T名义上限：总资产 %.0f × %.0f%% = %.0f 元 < 1 手（%.0f 元）⇒ 本次不发"
                   % (eq, _pct, cap_amt, p * _lot))
    if v > cap_v:
        return cap_v, ("正T名义上限：%.0f 股(%.0f 元) > 总资产 %.0f 的 %.0f%%(%.0f 元) ⇒ 收敛到 %d 股"
                       "（狼大 2026-03-31「只用10%%仓位做T」）" % (v, v * p, eq, _pct, cap_amt, cap_v))
    return v, "正T名义上限：在 %.0f%% 额度内（%.0f/%.0f 元）" % (_pct, v * p, cap_amt)


def account_equity(account_id: str, ledger: Optional[dict] = None,
                   price_hint: Optional[float] = None, ttl: float = 30.0) -> Optional[float]:
    """总资产 ≈ 可用现金 + 持仓市值（成本近似）。取数失败返回 None（fail-open）。30s TTL 缓存。"""
    now = time.time()
    if _EQ_CACHE["v"] is not None and (now - float(_EQ_CACHE["at"] or 0)) < ttl:
        return float(_EQ_CACHE["v"])
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL", ""))
        try:
            cur = conn.cursor()
            cur.execute("SELECT available_cash FROM paper_account_info WHERE account_id=%s", (account_id,))
            row = cur.fetchone()
            cash = float(row[0] or 0) if row else 0.0
            pv = 0.0
            if ledger:
                for _s, it in (ledger or {}).items():
                    try:
                        pv += float(it.get("volume") or 0) * float(it.get("avg_price") or it.get("cost") or 0)
                    except Exception:
                        continue
            else:
                cur.execute("SELECT COALESCE(SUM(volume*avg_price),0) FROM paper_positions WHERE account_id=%s",
                            (account_id,))
                pv = float((cur.fetchone() or [0])[0] or 0)
            v = cash + pv
        finally:
            conn.close()
        _EQ_CACHE.update({"at": now, "v": v})
        return v
    except Exception:
        return None
