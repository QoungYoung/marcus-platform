# -*- coding: utf-8 -*-
"""defense_cash.py — 防御档位 G0-G3 单一仲裁（v0，2026-10-25 直接上线）。

设计原则（docs/defense-cash-strategy.md）：
  · 信号层可以分散：wave / systemic_risk / macro_state，各自只报事实；
  · 仲裁层唯一：本模块取**最严**档位，输出 target/buckets/allow_*；
  · 执行层只读：wolf_discipline / position_tier / calc_position / prompt 都读这里。

⚠️ 破位/止损由 v3 `wolf_stop_layer` 独立处理，**不再作为 defense_cash 的输入**：
  · G 级只决定组合总仓上限；
  · 单票破位目标由 v3 现场算，不回灌组合档位，避免循环依赖与“单票 cap 当组合 cap”。

四档（2026-10-25 用户拍板）：
  G0 正常：不设总仓上限 / t_only·side 50
  G1 警戒：50%
  G2 防御：30%，禁新开/禁加仓
  G3 空仓：0%（既然“下跌趋势就不做”，就不留 T/底仓仓位）

关键闸门：
  · 段位 4-3 筑底 + 无 G3 硬触发 → 封顶 G1（价格弱不等于 G2）；
  · 取最严、不取平均；数据缺失不降级（不默认 G0 放行，但也不凭空升档）。

数据源（只读）：
  · wave_state（PG 优先，文件兜底）→ level/operation
  · data/systemic_risk.json → level 0/1/2
  · data/macro_state.json → macro_switches.flags
  · 本模块不读账户，也不读 break_actions（破位由 v3 处理）

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


def _target_buckets(level: int, op: str):
    if level == 3:
        return 0.0, {"base": 0, "t": 0, "cash": 100}
    if level == 2:
        return 30.0, {"base": 25, "t": 5, "cash": 70}
    if level == 1:
        return 50.0, {"base": 40, "t": 10, "cash": 50}
    # G0：不设总仓上限（狼大「主升趋势75%以上、盘中满仓滚动」）
    return None, None


def evaluate(segment: Optional[str] = None) -> Dict[str, Any]:
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
        "allow_new": level == 0,
        "allow_add": level == 0,
        "allow_t": True if level <= 1 else "reduce_only",
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
    """G1/G2/G3 的总仓上限；G0 返回 None（不设总仓上限）。"""
    try:
        st = get_state()
        if int(st.get("level_num") or 0) <= 0:
            return None
        return float(st.get("target_pct"))
    except Exception:
        return None


def directive() -> str:
    """注入 prompt 的防御状态块；G0 返回空（不打扰）。"""
    st = get_state()
    if int(st.get("level_num") or 0) <= 0:
        return ""
    rs = "；".join((st.get("reasons") or [])[:4])
    return ("## 防御档位（defense_cash）\n"
            "- 档位：**%s**｜总仓上限 **%.0f%%**｜底仓 %.0f%% / T %.0f%% / 现金 %.0f%%\n"
            "- 允许：%s｜禁止：%s\n"
            "- 触发：%s\n"
            % (st.get("level"), float(st.get("target_pct") or 0),
               float((st.get("buckets") or {}).get("base") or 0),
               float((st.get("buckets") or {}).get("t") or 0),
               float((st.get("buckets") or {}).get("cash") or 0),
               "只减不加" if st.get("allow_t") == "reduce_only" else "持有/T",
               "新开、加仓、补仓" if int(st.get("level_num") or 0) >= 2 else "追高、满仓",
               rs or "-"))


if __name__ == "__main__":
    print(json.dumps(get_state(refresh=True), ensure_ascii=False, indent=2))
