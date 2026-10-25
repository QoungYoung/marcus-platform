# -*- coding: utf-8 -*-
"""break_policy.py — 破位动作策略（BreakStop v1 / P3）。

纯函数：输入 P2 的 `BreakSignal` + 段位 + 持仓盈亏垫，输出 `BreakAction`。
**不读库、不下单、不判断行情**；只做“给定信号 → 动作/目标仓位”。

2026-10-23 用户拍板 A：
  · 指数级破位 = 唯一自动止损；个股级默认只提示。
  · 指数只用上证。
  · B3 默认 50%、深度 30%（**cap，不是 floor**）。
  · down-wave（wave.level=down / operation=exit）→ cap 0-30%，默认 0；
    只有存在短线/题材仓时才允许到 30%（2025-11-23「剩下 30% 仓位做短线热点」）。
  · 30% 不是地板：2025-11-21「3900 → 20%」证明可以更低。

严重度：
  B0 盘中破位（只观察）
  B1 收盘破次要位、量比 <1.0（提示）
  B2 个股/板块破平台/趋势/颈线（默认提示；验证后才减仓）
  B3-1 指数破 1 个大级别结构 + 量 → cap 50%
  B3-2 指数破 ≥2 根大级别 MA / 平台/趋势 + 量 / 系统性事件 → cap 30%
  B3-3 down-wave → cap 0-30%，默认 0，禁新开
  B4 13 日未碰前高 / 波段低点 −3% 未收回（默认提示）
"""
from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULTS: Dict[str, Any] = {
    # ⚠️ 以下 cap 数字有语料（50/30），但“B3-1 vs B3-2 的分层阈值”是**代理口径**，不是原话
    "index_cap": 0.50,          # B3-1（2025-12-03 / 2025-11-23）
    "index_cap_deep": 0.30,     # B3-2（2025-09-07 / 2025-10-12 / 2026-09-17）
    "down_cap": 0.30,           # B3-3 有短线仓时的上限
    "down_default": 0.0,        # B3-3 无短线仓时的默认目标
    "allow_zero": True,         # down-wave 允许 0
    "stock_exec": False,        # 个股 B2 是否自动减仓（默认只提示）
    "stock_reduce": 0.50,       # 个股减仓比例
    "time_stop_exec": False,    # B4 是否自动减仓（默认只提示）
    "deep_major_min": 2,        # B3-2：至少破几根大级别 MA
    "deep_level_types": ("platform_low_20", "platform_low_60", "trendline",
                         "neckline", "gap_lower"),
    "profit_high": 20.0,        # 大盈（被动止盈）
    "loss_panic": -10.0,        # 深亏：无指数破位时不自动砍
}

SEVERITIES = ("B0", "B1", "B2", "B3-1", "B3-2", "B3-3", "B4")
ACTIONS = ("observe", "alert", "restore", "reduce_t", "reduce_half",
           "reduce_two_thirds", "cap_50", "cap_30", "cap_0_30", "cap_custom", "clear")


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _cfg(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    if config:
        cfg.update({k: v for k, v in config.items() if v is not None})
    return cfg


def _sig_status(sig: Optional[Dict[str, Any]]) -> str:
    return str((sig or {}).get("status") or "none")


def _sig_confirmed(sig: Optional[Dict[str, Any]]) -> bool:
    return bool((sig or {}).get("confirmed")) or _sig_status(sig) in ("confirmed", "rebound_fail")


def _sig_intraday(sig: Optional[Dict[str, Any]]) -> bool:
    return _sig_status(sig) == "intraday_break"


def is_deep_break(index_signal: Optional[Dict[str, Any]], broken_major: int = 0,
                  systemic_event: bool = False,
                  config: Optional[Dict[str, Any]] = None) -> bool:
    """B3-2 深度破位：≥2 根大级别 MA / 平台类 + 放量 / 系统性事件。"""
    cfg = _cfg(config)
    if systemic_event:
        return True
    if int(broken_major or 0) >= int(cfg["deep_major_min"]):
        return True
    sig = index_signal or {}
    if str(sig.get("volume_branch") or "") in ("direct", "strong"):
        if str(sig.get("level_type") or "") in tuple(cfg["deep_level_types"]):
            return True
    return False


def _out(severity: str, action: str, reason: str,
         target_pct: Optional[float] = None,
         reduce_ratio: Optional[float] = None,
         allow_zero: bool = False, block_new_buy: bool = False,
         invalidation: str = "",
         source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sell = action in ("reduce_t", "reduce_half", "reduce_two_thirds", "cap_50",
                      "cap_30", "cap_0_30", "clear") and action != "observe"
    return {
        "severity": severity,
        "action": action,
        "target_pct": (round(float(target_pct), 4) if target_pct is not None else None),
        "target_is_cap": target_pct is not None,
        "reduce_ratio": (round(float(reduce_ratio), 4) if reduce_ratio is not None else None),
        "allow_zero": bool(allow_zero),
        "block_new_buy": bool(block_new_buy),
        "sell": bool(sell),
        "sell_order": (["weakest", "most_liquid"] if sell else []),
        "reason": reason,
        "invalidation": invalidation or "收盘站回破位 level",
        "source": dict(source or {}),
    }


def _cap_action(cap: float, deep: bool = False, down: bool = False,
                override: Optional[float] = None) -> str:
    if override is not None:
        return "cap_custom"
    if down:
        return "cap_0_30"
    return "cap_30" if deep else "cap_50"


def _apply_override(cap: float, override: Optional[float]) -> float:
    """点位→仓位表覆盖：C1「3922→40%」、C3「3850→35%」都高于默认 cap 30。
    “override 直接替换 cap”是**设计口径**（非原话）；只在 [0,1] 内接受，防止脏数据。"""
    if override is None:
        return cap
    try:
        o = float(override)
    except (TypeError, ValueError):
        return cap
    if o < 0 or o > 1:
        return cap
    return o


def decide(index_signal: Optional[Dict[str, Any]] = None,
           sector_signal: Optional[Dict[str, Any]] = None,
           stock_signal: Optional[Dict[str, Any]] = None,
           wave_op: str = "side",
           wave_level: str = "",
           position: Optional[Dict[str, Any]] = None,
           broken_major: int = 0,
           systemic_event: bool = False,
           limit_down: bool = False,
           volume_dry: bool = False,
           time_stop_confirmed: bool = False,
           target_override: Optional[float] = None,
           base_target: Optional[float] = None,
           reclaim: bool = False,
           config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """给定信号/段位/盈亏垫，返回动作。

    Args:
        index_signal: 上证破位信号（P2 BreakSignal）
        sector_signal: 板块指数破位信号
        stock_signal: 个股破位信号
        wave_op: build|t_only|side|defense|exit
        wave_level: d1|d2|d3|d4|d5|down（level=down 也按 down-wave 处理）
        position: {"pnl_pct": 5.0, "has_short_term_book": True, "pos_class": "high|low"}
        broken_major: 上证收盘跌破几根大级别 MA（来自 t_index_break.index_breakdown）
        systemic_event: 加息+无对冲等系统性事件
        limit_down / volume_dry: 跌停/地量 → 只提示不卖
        time_stop_confirmed: B4 的 13 日时间止损是否成立
        target_override: 点位→仓位表给的目标（权威；C1/C3 可高于默认 cap）
        base_target: **回测/基准假设**（非原话）；无破位时的恢复目标，None = 保持现状
        reclaim: **自定恢复口径**（非原话）；指数已收回关键位 → 允许 restore。
                 P5a 里用 MA60，是回测选择；他的话是“站回趋势线 3922”，不是 MA60。
        config: 覆盖 DEFAULTS
    """
    cfg = _cfg(config)
    pos = dict(position or {})
    op = str(wave_op or "side").lower()
    down_wave = (op == "exit") or (str(wave_level or "").strip().lower() == "down")
    pnl = _f(pos.get("pnl_pct"), 0.0)
    has_short = bool(pos.get("has_short_term_book"))
    inv = ""
    if index_signal:
        inv = "收盘站回 %s %s" % (index_signal.get("level_type") or "level",
                                  index_signal.get("level_price") or "")
    src = {"wave_op": op, "wave_level": wave_level, "index": _sig_status(index_signal),
           "sector": _sig_status(sector_signal), "stock": _sig_status(stock_signal),
           "broken_major": int(broken_major or 0), "pnl_pct": pnl}

    # ── 0) 跌停 / 地量：不割（2026-08-21「地量不割肉」；跌停卖不出去）──
    if limit_down or volume_dry:
        sev = "B3-3" if op == "exit" else ("B2" if (_sig_confirmed(stock_signal) or _sig_confirmed(sector_signal)) else "B1")
        why = "跌停/停牌，无法有效卖出" if limit_down else "地量不割肉（2026-08-21）"
        return _out(sev, "alert", why, allow_zero=False, block_new_buy=(op == "exit"),
                    invalidation=inv, source=src)

    # ── 1) down-wave：最高优先级（2026-01-17「下跌趋势就不做」；2026-08-27「转下跌1浪就止损」）──
    if down_wave:
        cap = float(cfg["down_cap"]) if has_short else float(cfg["down_default"])
        if not has_short:
            cap = 0.0
        if not cfg["allow_zero"]:
            cap = max(cap, float(cfg["down_default"]))
        cap = _apply_override(cap, target_override)
        return _out("B3-3", _cap_action(cap, down=True, override=target_override),
                    "operation=exit / 下跌 1 浪：cap %.0f%%（有短线仓才允许到 %.0f%%；2026-01-17「下跌趋势就不做」）"
                    % (cap * 100, float(cfg["down_cap"]) * 100),
                    target_pct=cap, allow_zero=True, block_new_buy=True,
                    invalidation=inv, source=src)

    # ── 2) 指数收盘确认：B3-1 / B3-2 ──
    if _sig_confirmed(index_signal):
        deep = is_deep_break(index_signal, broken_major, systemic_event, cfg)
        sev = "B3-2" if deep else "B3-1"
        cap = float(cfg["index_cap_deep"]) if deep else float(cfg["index_cap"])
        if op == "defense":
            cap = min(cap, float(cfg["index_cap_deep"]))
        cap = _apply_override(cap, target_override)
        return _out(sev, _cap_action(cap, deep=(deep or op == "defense"),
                                     override=target_override),
                    "%s：上证收盘破位（%s，量比 %s）→ cap %.0f%%，禁新开"
                    % (sev, (index_signal or {}).get("level_type") or "level",
                       (index_signal or {}).get("vol_ratio"), cap * 100),
                    target_pct=cap, block_new_buy=True,
                    invalidation=inv, source=src)

    # ── 3) 板块/个股确认：B2（默认只提示；stock_exec=1 才减仓）──
    sig = sector_signal if _sig_confirmed(sector_signal) else stock_signal
    if _sig_confirmed(sig):
        if not cfg["stock_exec"]:
            return _out("B2", "alert",
                        "个股/板块收盘破位（%s），默认只提示（WOLF_BREAK_STOCK_EXEC=0）"
                        % ((sig or {}).get("level_type") or "level"),
                        allow_zero=False, block_new_buy=False,
                        invalidation=inv, source=src)
        # 深亏 + 无指数破位：不自动砍（audit：个股止损负贡献）
        if pnl <= float(cfg["loss_panic"]):
            return _out("B2", "alert",
                        "个股破位但浮亏 %.1f%% ≤ %.1f%%，无指数破位 → 不自动砍（急跌不割肉）"
                        % (pnl, float(cfg["loss_panic"])),
                        allow_zero=False, block_new_buy=False,
                        invalidation=inv, source=src)
        ratio = float(cfg["stock_reduce"])
        if pnl > float(cfg["profit_high"]) and str(pos.get("pos_class") or "") == "high":
            ratio = max(ratio, 0.50)
        return _out("B2", "reduce_half" if ratio >= 0.5 else "reduce_t",
                    "个股/板块收盘破位 + stock_exec=1 → 减 %.0f%%（T 优先）" % (ratio * 100),
                    reduce_ratio=ratio, allow_zero=False, block_new_buy=False,
                    invalidation=inv, source=src)

    # ── 3.5) 已收回关键位：允许恢复基准仓位 ──
    if reclaim and base_target is not None:
        try:
            bt = float(base_target)
            if 0 <= bt <= 1:
                return _out("B0", "restore", "指数已收回关键位 → 恢复到基准仓位 %.0f%%" % (bt * 100),
                            target_pct=bt, invalidation=inv, source=src)
        except (TypeError, ValueError):
            pass

    # ── 4) 盘中破位：只观察 ──
    if _sig_intraday(index_signal) or _sig_intraday(sector_signal) or _sig_intraday(stock_signal):
        return _out("B0", "observe",
                    "盘中破位，未收盘确认（2025-11-21「盘中跌破不算」）",
                    invalidation=inv, source=src)

    # ── 5) B4 时间止损（默认只提示）──
    if time_stop_confirmed:
        if cfg["time_stop_exec"] and pnl > float(cfg["loss_panic"]):
            return _out("B4", "reduce_half",
                        "13 日未碰前高 / 波段低点 −3% 未收回 → 减半（time_stop_exec=1）",
                        reduce_ratio=0.50, invalidation=inv, source=src)
        return _out("B4", "alert",
                    "13 日未碰前高 / 波段低点 −3% 未收回 → 默认只提示（time_stop_exec=0）",
                    invalidation=inv, source=src)

    # ── 6) 收盘破位但未确认（B1）──
    for s in (index_signal, sector_signal, stock_signal):
        if _sig_status(s) in ("close_break", "shrink_break", "watch"):
            return _out("B1", "alert",
                        "收盘接近/跌破 level（%s），未确认 → 提示" % _sig_status(s),
                        invalidation=inv, source=src)

    if base_target is not None:
        try:
            bt = float(base_target)
            if 0 <= bt <= 1:
                return _out("B0", "restore", "无破位信号 → 恢复到基准仓位 %.0f%%" % (bt * 100),
                            target_pct=bt, invalidation=inv, source=src)
        except (TypeError, ValueError):
            pass
    return _out("B0", "observe", "无破位信号", invalidation=inv, source=src)
