# -*- coding: utf-8 -*-
"""defense_cash.py — 防御档位 G0-G3 单一仲裁（v0，2026-10-25 直接上线）。

设计原则（docs/defense-cash-strategy.md）：
  · 信号层可以分散：wave / systemic_risk / macro_state / 账户回撤 / 破位（B1-B4），各自只报事实；
  · 仲裁层唯一：本模块取**最严**档位，输出 target/buckets/allow_*；
  · 执行层只读：p2_entry_gate / position_tier / calc_position / prompt 都读这里。

⚠️ 破位减仓是**防御的最高 urgency 执行腿**，不是与防御并列的策略：
  · G 级决定组合总仓上限；
  · 破位/止损决定 symbol override（单票可压到 0/50%）；
  · 先执行 override，再算组合残余；v0 尚未接入 break_policy/wolf_stop_layer 的 override。

四档（2026-10-25 用户拍板）：
  G0 正常：build 75 / t_only·side 50
  G1 警戒：50%
  G2 防御：30%，禁新开/禁加仓
  G3 空仓/极低：15%（底仓 10 + T 5），现金 85%

关键闸门：
  · 段位 4-3 筑底 + 无 G3 硬触发 → 封顶 G1（价格弱不等于 G2）；
  · 取最严、不取平均；数据缺失不降级（不默认 G0 放行，但也不凭空升档）。

数据源（只读）：
  · wave_state（PG 优先，文件兜底）→ level/operation
  · data/systemic_risk.json → level 0/1/2
  · data/macro_state.json → macro_switches.flags
  · 账户回撤由调用方传入（本模块不读账户）

开关：`DEFENSE_CASH=0` 可整体退回旧口径（默认 1）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

_CACHE: Dict[str, Any] = {"at": 0.0, "state": None}
_TTL = float(os.getenv("DEFENSE_CASH_TTL", "30"))


def enabled() -> bool:
    return os.getenv("DEFENSE_CASH", "1").strip().lower() not in ("0", "false", "no")


def _data_dirs():
    out = []
    dd = os.getenv("DATA_DIR")
    if dd:
        out.append(dd)
    try:
        from app.config import get_settings
        out.append(os.path.join(str(get_settings().workspace_path), "data"))
    except Exception:
        pass
    out.append(os.path.join(os.getcwd(), "data"))
    seen, uniq = set(), []
    for d in out:
        if d and d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def _load(name: str) -> dict:
    for d in _data_dirs():
        p = os.path.join(d, name)
        try:
            if os.path.exists(p):
                return json.load(open(p, encoding="utf-8")) or {}
        except Exception:
            continue
    return {}


def _wave() -> dict:
    try:
        from app.services import wave_store
        w = wave_store.get_wave() or {}
        if w:
            return w
    except Exception:
        pass
    return _load("wave_state.json")


def _systemic_level() -> int:
    try:
        return int((_load("systemic_risk.json") or {}).get("level") or 0)
    except Exception:
        return 0


def _macro_flags():
    try:
        ms = _load("macro_state.json") or {}
        return set((ms.get("macro_switches") or {}).get("flags") or [])
    except Exception:
        return set()


_BREAK_SELL = {"reduce_t", "reduce_half", "reduce_two_thirds", "cap_50", "cap_30",
               "cap_0_30", "cap_custom", "clear"}


def _break_actions() -> list:
    """读当日（或最近交易日）破位动作。破位是防御的最高 urgency 执行腿之一。

    `break_actions` 由 break_stop_service 写入；失败/无表 → []（不阻断防御主链）。
    """
    try:
        import psycopg2
        dsn = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:18789/marcus_trading")
        with psycopg2.connect(dsn, connect_timeout=2) as conn:
            cur = conn.cursor()
            cur.execute("""SELECT asof, symbol, domain, severity, action, target_pct,
                                  target_is_cap, block_new_buy, allow_zero, status, reason
                           FROM break_actions
                           WHERE asof = (SELECT max(asof) FROM break_actions WHERE asof <= CURRENT_DATE)
                           ORDER BY id""")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close()
            return rows
    except Exception:
        return []


def _break_level(severity: str, action: str) -> int:
    """破位严重度 → G 级（与 break_policy 的 B0-B4 对齐）。"""
    sev = str(severity or "").strip().upper()
    act = str(action or "").strip()
    if sev == "B3-3":
        return 3
    if sev == "B3-2":
        return 2
    if sev == "B2":
        return 2 if act in _BREAK_SELL else 1
    if sev in ("B3-1", "B1", "B4"):
        return 1
    return 0


def _break_override_target(row: dict):
    """单票 override：返回剩余比例（0-1）或 None（不覆盖）。"""
    try:
        if str(row.get("domain") or "").lower() == "index":
            return None
        if str(row.get("action") or "") not in _BREAK_SELL:
            return None
        if row.get("target_pct") is None:
            return None
        t = float(row.get("target_pct"))
        if str(row.get("action")) in ("cap_0_30", "clear") and str(row.get("allow_zero") or "0") in ("1", "true", "yes"):
            return 0.0
        return max(0.0, min(1.0, t))
    except Exception:
        return None


def _target_buckets(level: int, op: str):
    if level == 3:
        return 15.0, {"base": 10, "t": 5, "cash": 85}
    if level == 2:
        return 30.0, {"base": 25, "t": 5, "cash": 70}
    if level == 1:
        return 50.0, {"base": 40, "t": 10, "cash": 50}
    # G0：按浪型
    if op == "build":
        return 75.0, {"base": 50, "t": 25, "cash": 25}
    return 50.0, {"base": 40, "t": 10, "cash": 50}


def evaluate(account_drawdown_pct: Optional[float] = None,
             segment: Optional[str] = None,
             break_actions: Optional[list] = None) -> Dict[str, Any]:
    """返回防御状态。纯函数（除读文件/wave_store 外无副作用）。"""
    if not enabled():
        return {"level": "G0", "level_num": 0, "target_pct": None,
                "buckets": None, "allow_new": True, "allow_add": True,
                "allow_t": True, "reasons": ["DEFENSE_CASH=0 已关闭"], "source": "disabled"}

    w = _wave() or {}
    op = str(w.get("operation") or "side").strip().lower()
    wl = str(w.get("level") or "").strip().lower()
    sys_level = _systemic_level()
    flags = _macro_flags()
    reasons = []
    level = 0

    # G3 硬触发
    if sys_level >= 2:
        level = 3
        reasons.append("systemic_risk level=%s（大光破位/彻底止盈）" % sys_level)
    if op == "exit" or wl == "down":
        level = max(level, 3)
        reasons.append("wave level=%s operation=%s（下跌趋势不做）" % (wl or "?", op or "?"))

    # G2 硬触发
    if op == "defense":
        level = max(level, 2)
        reasons.append("wave operation=defense（防御不建仓）")
    if sys_level == 1:
        level = max(level, 2)
        reasons.append("systemic_risk level=1（银行双头+科技不反）")
    if "margin_burst" in flags:
        level = max(level, 2)
        reasons.append("macro margin_burst（杀杠杆/两融净卖）")

    # G1 触发
    if op in ("t_only", "side"):
        level = max(level, 1)
        reasons.append("wave operation=%s（调整/只做T）" % op)
    if flags & {"gjd_withdraw", "us_yield_spike", "cn30_spike"}:
        level = max(level, 1)
        reasons.append("macro flags=%s" % ",".join(sorted(flags & {"gjd_withdraw", "us_yield_spike", "cn30_spike"})))

    # 破位（break_policy B1-B4）：不是防御的兄弟，是防御的最高 urgency 执行腿
    bas = break_actions if break_actions is not None else _break_actions()
    overrides = {}
    block_new_break = False
    for row in (bas or []):
        lv = _break_level(row.get("severity"), row.get("action"))
        if lv:
            level = max(level, lv)
            reasons.append("破位%s %s %s（%s）" % (
                row.get("severity"), row.get("symbol") or "", row.get("action") or "",
                str(row.get("reason") or "")[:60]))
        if str(row.get("block_new_buy") or "0") in ("1", "true", "yes"):
            block_new_break = True
        ov = _break_override_target(row)
        if ov is not None:
            overrides[str(row.get("symbol"))] = ov

    # 账户回撤阶梯
    try:
        dd = float(account_drawdown_pct) if account_drawdown_pct is not None else None
    except (TypeError, ValueError):
        dd = None
    if dd is not None:
        if dd >= 12:
            level = max(level, 3); reasons.append("账户回撤 %.1f%% ≥12%%" % dd)
        elif dd >= 8:
            level = max(level, 2); reasons.append("账户回撤 %.1f%% ≥8%%" % dd)
        elif dd >= 5:
            level = max(level, 1); reasons.append("账户回撤 %.1f%% ≥5%%" % dd)

    # 段位闸门：4-3 筑底 + 无 G3 硬触发 → 封顶 G1
    seg = str(segment or w.get("sub_level") or "").strip()
    if seg.startswith("4-3") and level < 3:
        if level > 1:
            reasons.append("段位=4-3 筑底 ⇒ 封顶 G1（价格弱不等于 G2）")
        level = min(level, 1)

    target, buckets = _target_buckets(level, op)
    name = {0: "G0", 1: "G1", 2: "G2", 3: "G3"}[level]
    return {
        "level": name,
        "level_num": level,
        "target_pct": target,
        "buckets": buckets,
        "allow_new": level == 0 and not block_new_break,
        "allow_add": level == 0,
        "allow_t": True if level <= 1 else "reduce_only",
        "symbol_overrides": overrides,
        "break_actions": bas or [],
        "reasons": reasons,
        "wave": {"level": wl, "operation": op, "sub_level": seg},
        "systemic_level": sys_level,
        "macro_flags": sorted(flags),
        "segment": seg,
        "asof": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "defense_cash",
    }


def get_state(refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if (not refresh) and _CACHE["state"] is not None and (now - _CACHE["at"]) < _TTL:
        return _CACHE["state"]
    st = evaluate()
    _CACHE.update({"at": now, "state": st})
    return st


def current_level() -> int:
    try:
        return int(get_state().get("level_num") or 0)
    except Exception:
        return 0


def current_target_pct() -> Optional[float]:
    """G1/G2/G3 的总仓上限；G0 返回 None（按浪型原逻辑）。"""
    try:
        st = get_state()
        if int(st.get("level_num") or 0) <= 0:
            return None
        return float(st.get("target_pct"))
    except Exception:
        return None


def gate_for_entry() -> Dict[str, Any]:
    """给 p2_entry_gate/check_entry_filters 的统一入口。"""
    st = get_state()
    lv = int(st.get("level_num") or 0)
    return {
        "hard_block": lv >= 2,
        "multiplier": 0.5 if lv == 1 else 1.0,
        "level": st.get("level"),
        "target_pct": st.get("target_pct"),
        "symbol_overrides": st.get("symbol_overrides") or {},
        "reasons": st.get("reasons") or [],
    }


def directive() -> str:
    """注入 prompt 的防御状态块；G0 返回空（不打扰）。"""
    st = get_state()
    if int(st.get("level_num") or 0) <= 0:
        return ""
    rs = "；".join((st.get("reasons") or [])[:4])
    return ("## 防御档位（defense_cash）\n"
            "- 档位：**%s**｜总仓上限 **%.0f%%**｜底仓 %.0f%% / T %.0f%% / 现金 %.0f%%\n"
            "- 允许：%s｜禁止：%s\n"
            "- 单票 override：%s\n"
            "- 触发：%s\n"
            % (st.get("level"), float(st.get("target_pct") or 0),
               float((st.get("buckets") or {}).get("base") or 0),
               float((st.get("buckets") or {}).get("t") or 0),
               float((st.get("buckets") or {}).get("cash") or 0),
               "只减不加" if st.get("allow_t") == "reduce_only" else "持有/T",
               "新开、加仓、补仓" if int(st.get("level_num") or 0) >= 2 else "追高、满仓",
               (", ".join("%s→%.0f%%" % (k, float(v) * 100) for k, v in (st.get("symbol_overrides") or {}).items()) or "无"),
               rs or "-"))


if __name__ == "__main__":
    print(json.dumps(get_state(refresh=True), ensure_ascii=False, indent=2))
