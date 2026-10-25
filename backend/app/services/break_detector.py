# -*- coding: utf-8 -*-
"""break_detector.py — 破位确认状态机（BreakStop v1 / P2）。

纯函数模块：输入 P1 的 level + 日线 bars，输出 `BreakSignal`。
**只判状态，不做动作、不下单、不写库**；动作在 P3 的 `break_policy`。

状态机（对齐 `docs/plan-breakdown-stop-loss.md` §3.4）：

    none
      │ 盘中价 < level（phase=intraday）
      ▼
    intraday_break（B0，只记录；不确认、不卖）
      │ 收盘 < level*(1-buffer)
      ▼
    close_break
      ├─ vol_ratio ≥ direct（1.2） ──────────────► confirmed
      ├─ vol_ratio < shrink（0.8） ─────────────► shrink_break
      │     ├─ 反抽触及 level 但收盘仍 < level ──► rebound_fail（= confirmed）
      │     └─ N 日内收盘 > level*(1+buffer) ───► reclaimed
      └─ 其余（量比中间值） ─────────────────────► close_break（等下一日）
            └─ 3 根 15min 收盘都不收回 + 量比 ≥ shrink ─► confirmed

关键口径（语料）：
  · **盘中跌破不算**（2025-11-21 连说三遍）→ phase=intraday 永远不 confirmed。
  · **放量跌破直接算；缩量跌破不算，等反抽不过颈线**（2026-03-04）。
  · **收盘确认**（2026-01-29「收盘跌破我才出」）。
  · **前一日低点**（2026-02-05 持有；2025-12-11 跌破 3 个 15min 不收回→反抽减仓）。
  · **量能多一倍**（2026-05-21）作为 `vol_strong` 展示，不改变 direct 分支。

防前视：`run_series` 只按 bars 顺序推进；某日信号只使用该日及之前的 bar。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULTS: Dict[str, Any] = {
    "buffer": 0.003,          # 收盘破位缓冲（0.3%）
    "vol_window": 20,         # 量比基准：前 20 日均量
    "vol_direct": 1.2,        # 放量直接确认
    "vol_shrink": 0.8,        # 缩量分支
    "vol_strong": 2.0,        # “量能多一倍”（展示）
    "reclaim_days": 2,        # 收盘站回视为假破位的窗口（交易日）
    "m15_confirm_bars": 3,    # 3 根 15min 收盘不收回
    "min_bars": 5,            # 少于此不计算量比
    # 工程护栏（**非语料**）：压力位在现价上方、从未站上过 → 不算破位。
    # 目的是防止 round_number/MA 远在上方时误报；可在对比实验中关掉。
    "require_seen_above": True,
}

STATUSES = ("none", "watch", "intraday_break", "close_break", "shrink_break",
            "rebound_fail", "confirmed", "reclaimed")
_BROKEN = ("close_break", "shrink_break", "rebound_fail", "confirmed")


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _norm_date(x: Any) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[:8]


def _cfg(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    if config:
        cfg.update({k: v for k, v in config.items() if v is not None})
    return cfg


def _norm_bars(bars: Iterable[Any]) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in bars or []:
        if isinstance(r, dict):
            d = _norm_date(r.get("trade_date") or r.get("date"))
            close = _f(r.get("close"))
            high = _f(r.get("high"), close)
            low = _f(r.get("low"), close)
            vol = _f(r.get("vol") or r.get("volume"))
            amt = _f(r.get("amount"))
        else:
            try:
                d = _norm_date(r[0])
                close, high, low = _f(r[4]), _f(r[2]), _f(r[3])
                amt = _f(r[5]) if len(r) > 5 else 0.0
                vol = _f(r[6]) if len(r) > 6 else 0.0
            except (TypeError, IndexError):
                continue
        if len(d) != 8 or close <= 0:
            continue
        out[d] = {"date": d, "open": _f(r.get("open"), close) if isinstance(r, dict) else close,
                  "high": max(high, close), "low": min(low, close), "close": close,
                  "vol": vol, "amount": amt}
    return [out[d] for d in sorted(out)]


def _vol_value(bar: Dict[str, Any]) -> float:
    v = _f(bar.get("vol"))
    if v > 0:
        return v
    return _f(bar.get("amount"))


def volume_ratio(bars: Sequence[Dict[str, Any]], i: int,
                 window: int = 20) -> Optional[float]:
    """第 i 根的量比：当前量 / 前 window 根均量。数据不足/当前量为 0 → None。"""
    if i < 0 or i >= len(bars):
        return None
    cur = _vol_value(bars[i])
    if cur <= 0:
        return None
    prev = [_vol_value(b) for b in bars[max(0, i - window):i]]
    prev = [v for v in prev if v > 0]
    if len(prev) < max(3, min(window, 5)):
        return None
    avg = sum(prev) / len(prev)
    return round(cur / avg, 4) if avg > 0 else None


def volume_branch(ratio: Optional[float], cfg: Dict[str, Any]) -> str:
    if ratio is None:
        return "unknown"
    if ratio >= float(cfg["vol_strong"]):
        return "strong"
    if ratio >= float(cfg["vol_direct"]):
        return "direct"
    if ratio < float(cfg["vol_shrink"]):
        return "shrink"
    return "mid"


def m15_no_reclaim(m15_bars: Optional[Sequence[Dict[str, Any]]], level_price: float,
                   cfg: Dict[str, Any]) -> Optional[bool]:
    """最近连续多少根 15min 收盘 < level；≥ m15_confirm_bars → True。无数据 → None。"""
    if not m15_bars:
        return None
    closes = []
    for b in m15_bars:
        if isinstance(b, dict):
            closes.append(_f(b.get("close")))
        else:
            try:
                closes.append(_f(b[4]))
            except (TypeError, IndexError):
                continue
    closes = [c for c in closes if c > 0]
    if not closes:
        return None
    n = 0
    for c in reversed(closes):
        if c < level_price:
            n += 1
        else:
            break
    return n >= int(cfg["m15_confirm_bars"])


def _status_signal(level: Dict[str, Any], bar: Dict[str, Any], status: str,
                   phase: str, vol_ratio: Optional[float], branch: str,
                   m15: Optional[bool], prev: Dict[str, Any],
                   cfg: Dict[str, Any], reason: str,
                   price: Optional[float] = None) -> Dict[str, Any]:
    lp = _f(level.get("price"))
    px = _f(price, _f(bar["close"]))
    below = px < lp
    below_buffer = px < lp * (1.0 - float(cfg["buffer"])) if phase == "close" else below
    confirmed = status in ("confirmed", "rebound_fail")
    return {
        "level_id": level.get("level_id"),
        "symbol": level.get("symbol"),
        "domain": level.get("domain"),
        "level_type": level.get("type"),
        "level_price": round(_f(level.get("price")), 4),
        "asof": bar["date"],
        "phase": phase,
        "status": status,
        "below": bool(below),
        "below_buffer": bool(below_buffer),
        "confirmed": bool(confirmed),
        "close": round(_f(bar["close"]), 4),
        "high": round(_f(bar["high"]), 4),
        "low": round(_f(bar["low"]), 4),
        "vol_ratio": vol_ratio,
        "volume_branch": branch,
        "vol_strong": bool(vol_ratio is not None and vol_ratio >= float(cfg["vol_strong"])),
        "m15_no_reclaim": m15,
        "break_date": prev.get("break_date"),
        "reclaim_deadline": prev.get("reclaim_deadline"),
        "reason": reason,
    }


def _signal_for_day(level: Dict[str, Any], bars: Sequence[Dict[str, Any]], i: int,
                    phase: str, prev: Dict[str, Any], cfg: Dict[str, Any],
                    m15_bars: Optional[Sequence[Dict[str, Any]]] = None,
                    quote_price: Optional[float] = None) -> Dict[str, Any]:
    bar = bars[i]
    lp = _f(level.get("price"))
    if lp <= 0:
        return _status_signal(level, bar, "none", phase, None, "unknown", None, prev, cfg,
                              "level price 无效")
    close = _f(bar["close"])
    vr = volume_ratio(bars, i, int(cfg["vol_window"]))
    branch = volume_branch(vr, cfg)
    m15 = m15_no_reclaim(m15_bars, lp, cfg)
    prev_status = str(prev.get("status") or "none")
    buf = float(cfg["buffer"])
    above = close > lp * (1.0 + buf)
    below = close < lp * (1.0 - buf)
    touch = _f(bar["low"]) <= lp <= _f(bar["high"]) or _f(bar["low"]) <= lp

    # ── 盘中：只判 intraday_break，永不 confirmed ──
    if phase == "intraday":
        price = _f(quote_price, close)
        if price < lp and _f(bar["low"]) <= lp:
            return _status_signal(level, bar, "intraday_break", phase, vr, branch, m15, prev,
                                  cfg, "盘中价 %.3f < level %.3f（未收盘，不确认）" % (price, lp),
                                  price=price)
        if prev_status in _BROKEN:
            return _status_signal(level, bar, prev_status, phase, vr, branch, m15, prev, cfg,
                                  "盘中未破，维持 %s" % prev_status, price=price)
        return _status_signal(level, bar, "none", phase, vr, branch, m15, prev, cfg,
                              "盘中价未破 level", price=price)

    # ── 收盘：先看站回，再看跌破 ──
    if above:
        if prev_status in _BROKEN:
            return _status_signal(level, bar, "reclaimed", phase, vr, branch, m15, prev, cfg,
                                  "收盘 %.3f 站回 level %.3f（假破位/收回）" % (close, lp))
        return _status_signal(level, bar, "none", phase, vr, branch, m15, prev, cfg,
                              "收盘在 level 上方")

    if not below:
        # 缓冲带内：默认不确认；但“收盘已微破 + 3×15min 不收回”可越过缓冲带确认
        #（2025-12-11：跌破昨天低点超过 3 个 15min 没收回 → 反抽减仓）
        if close < lp and m15 is True and branch in ("direct", "strong", "mid") and prev_status not in _BROKEN:
            return _status_signal(level, bar, "confirmed", phase, vr, branch, m15, prev, cfg,
                                  "收盘微破 %.3f < level %.3f + 3×15min 未收回 → 确认" % (close, lp))
        st = "watch" if touch else ("none" if prev_status == "none" else prev_status)
        return _status_signal(level, bar, st, phase, vr, branch, m15, prev, cfg,
                              "收盘 %.3f 在 level %.3f 缓冲带内（未确认）" % (close, lp))

    # 已跌破：但若这个 level **从未被站上过**（它其实在现价上方=压力），不能算破位
    if (cfg.get("require_seen_above", True)
            and not prev.get("seen_above") and prev_status not in _BROKEN):
        st = "watch" if touch else "none"
        return _status_signal(level, bar, st, phase, vr, branch, m15, prev, cfg,
                              "level %.3f 在现价上方且从未站上 → 不构成破位" % lp)

    # 已跌破
    if prev_status in ("confirmed", "rebound_fail"):
        return _status_signal(level, bar, prev_status, phase, vr, branch, m15, prev, cfg,
                              "延续破位（此前已确认）")

    if prev_status == "shrink_break":
        # 缩量破位后的反抽：触及 level 但收盘仍破 → 反抽失败，确认
        if _f(bar["high"]) >= lp:
            return _status_signal(level, bar, "rebound_fail", phase, vr, branch, m15, prev, cfg,
                                  "缩量破位后反抽触及 %.3f 但收盘仍破 → 反抽失败" % lp)
        return _status_signal(level, bar, "shrink_break", phase, vr, branch, m15, prev, cfg,
                              "缩量破位后尚未反抽")

    # 首次收盘跌破 / 延续 close_break：按量能分支
    if branch in ("direct", "strong"):
        return _status_signal(level, bar, "confirmed", phase, vr, branch, m15, prev, cfg,
                              "收盘破位 + 量比 %.2f（≥%.2f）→ 直接确认"
                              % (vr or 0, float(cfg["vol_direct"])))
    if branch == "shrink":
        return _status_signal(level, bar, "shrink_break", phase, vr, branch, m15, prev, cfg,
                              "收盘破位但量比 %.2f（<%.2f）→ 缩量，等反抽不过再确认"
                              % (vr or 0, float(cfg["vol_shrink"])))
    if m15 is True and branch != "shrink":
        return _status_signal(level, bar, "confirmed", phase, vr, branch, m15, prev, cfg,
                              "收盘破位 + 3×15min 未收回 → 确认")
    return _status_signal(level, bar, "close_break", phase, vr, branch, m15, prev, cfg,
                          "收盘破位，量比 %.2f（中间值）→ 等下一日" % (vr or 0))


def run_series(level: Dict[str, Any], bars: Iterable[Any], phase: str = "close",
               config: Optional[Dict[str, Any]] = None,
               m15_by_date: Optional[Dict[str, Sequence[Dict[str, Any]]]] = None,
               start: int = 0) -> List[Dict[str, Any]]:
    """按日推进状态机，返回每日 `BreakSignal`（PIT：第 i 日只用 bars[:i+1]）。"""
    cfg = _cfg(config)
    bs = _norm_bars(bars)
    if not bs:
        return []
    m15_by_date = m15_by_date or {}
    out: List[Dict[str, Any]] = []
    prev: Dict[str, Any] = {"status": "none", "break_date": None,
                            "reclaim_deadline": None, "seen_above": False}
    for i in range(max(0, int(start)), len(bs)):
        # 盘中序列按日重置：昨天的 intraday_break 不应延续到今天
        if phase == "intraday":
            prev = {"status": "none", "break_date": None, "reclaim_deadline": None}
        sig = _signal_for_day(level, bs, i, phase, prev, cfg,
                              m15_bars=m15_by_date.get(bs[i]["date"]))
        # break_date / reclaim_deadline（按交易日推进）
        if sig["status"] in ("close_break", "shrink_break", "confirmed", "rebound_fail") \
                and not prev.get("break_date"):
            bd = bs[i]["date"]
            j = min(len(bs) - 1, i + int(cfg["reclaim_days"]))
            sig["break_date"] = bd
            sig["reclaim_deadline"] = bs[j]["date"]
        else:
            sig["break_date"] = prev.get("break_date")
            sig["reclaim_deadline"] = prev.get("reclaim_deadline")
        out.append(sig)
        # 更新状态（seen_above：曾收盘站上 level，才允许之后算破位）
        _lp = _f(level.get("price"))
        _seen = bool(prev.get("seen_above")) or (
            _lp > 0 and _f(bs[i]["close"]) > _lp * (1.0 + float(cfg["buffer"])))
        if sig["status"] == "reclaimed":
            prev = {"status": "reclaimed", "break_date": None,
                    "reclaim_deadline": None, "seen_above": _seen}
        else:
            prev = {"status": sig["status"], "break_date": sig["break_date"],
                    "reclaim_deadline": sig["reclaim_deadline"], "seen_above": _seen}
    return out


def detect(level: Dict[str, Any], bars: Iterable[Any], phase: str = "close",
           config: Optional[Dict[str, Any]] = None,
           m15_bars: Optional[Sequence[Dict[str, Any]]] = None,
           quote_price: Optional[float] = None) -> Dict[str, Any]:
    """返回最后一根 bar 的 `BreakSignal`（便捷入口）。"""
    bs = _norm_bars(bars)
    if not bs:
        return {}
    cfg = _cfg(config)
    if phase != "intraday":
        sigs = run_series(level, bs, phase=phase, config=cfg,
                          m15_by_date=({bs[-1]["date"]: m15_bars} if m15_bars else {}))
        return sigs[-1] if sigs else {}
    lp = _f(level.get("price"))
    seen = any(_f(b["close"]) > lp * (1.0 + float(cfg["buffer"])) for b in bs[:-1]) if lp > 0 else False
    prev: Dict[str, Any] = {"status": "none", "break_date": None,
                            "reclaim_deadline": None, "seen_above": seen}
    return _signal_for_day(level, bs, len(bs) - 1, phase, prev, cfg,
                           m15_bars=m15_bars, quote_price=quote_price)
