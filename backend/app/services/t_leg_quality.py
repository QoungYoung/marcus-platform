# -*- coding: utf-8 -*-
"""候选腿质量闸（2026-09-18 用户"查候选腿质量"后的修复）。

背景（审计实测）：_bt_size 跑批 15 个买入标的全部来自候选腿（布腿账户修好后才真正进场），但质量参差——
SZ002429 +25.8% / SZ002602 +8.7% 赚钱，SH600658 -12.1% / SH603660 -11.8% 亏钱；合计已实现 +6,277 / 浮动 -4,651。
根因：T_BUY_TIER_LIMIT_ENABLED=0（AI 自由跑）时 t_gateway._max_buy_volume_ex() 走两分法容量锚，
不调用 t_build.build_sizing() ⇒ build_score_min=0.78 等建仓门槛从未生效；且 legs_switch.jsonl 没有 score 字段。

三条修复（开关 WOLF_LEG_QUALITY，库内默认 0；回测驱动 setdefault=1）：
  ① 建仓腿质量闸：无底仓建仓时现算 t_build.build_score()（as_of=回放日），score < 门槛（默认 0.78）→ 不批；
  ② 打分留痕：把 score/pass/reasons 打进日志与拒绝原因（补 legs 缺 score 的问题）；
  ③ 首买不达预期禁加仓：已有底仓时，若首买已满 WOLF_LEG_QUALITY_ADD_DAYS(默认 3) 个交易日且浮亏 ≤ -2% → 禁加仓。
全部 fail-open（算不出分/取数失败 → 放行，只记 stats）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

ON = str(os.getenv("WOLF_LEG_QUALITY", "0")).strip().lower() in ("1", "true", "yes", "on")
MIN_SCORE = float(os.getenv("WOLF_LEG_QUALITY_MIN", "0.78"))
ADD_DAYS = int(float(os.getenv("WOLF_LEG_QUALITY_ADD_DAYS", "3")))
ADD_PNL = float(os.getenv("WOLF_LEG_QUALITY_ADD_PNL", "-2.0"))
SOURCE = os.getenv("WOLF_LEG_QUALITY_SOURCE", "switch")

_STATS: Dict[str, Any] = {"checked": 0, "score_block": 0, "add_block": 0, "scored": 0,
                          "errors": 0, "last": None}


def enabled() -> bool:
    return ON


def stats() -> Dict[str, Any]:
    return {"on": ON, "min_score": MIN_SCORE, "add_days": ADD_DAYS, "add_pnl": ADD_PNL, **_STATS}


def stats_reset() -> None:
    for k in ("checked", "score_block", "add_block", "scored", "errors"):
        _STATS[k] = 0


def score_of(symbol: str, as_of: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """现算建仓打分（复用生产口径 build_score）。失败 → None（fail-open）。"""
    try:
        from app.services import t_build
        _a = None
        if as_of and len(str(as_of)) >= 8:
            _s = str(as_of).replace("-", "")
            _a = "%s-%s-%s" % (_s[:4], _s[4:6], _s[6:8])
        r = t_build.build_score(symbol, source=SOURCE, as_of=_a)
        _STATS["scored"] += 1
        _STATS["last"] = {"symbol": symbol, "score": round(float(r.get("score") or 0), 3),
                          "pass": bool(r.get("pass_gate"))}
        return r
    except Exception as e:
        _STATS["errors"] += 1
        print("[leg-quality] 打分失败(放行) %s: %s" % (symbol, str(e)[:80]), flush=True)
        return None


def score_ok(symbol: str, as_of: Optional[str] = None) -> Tuple[bool, str]:
    """① 建仓腿质量闸：返回 (ok, why)。"""
    if not ON:
        return True, ""
    r = score_of(symbol, as_of)
    if not r:
        return True, ""
    sc = float(r.get("score") or 0)
    if sc < MIN_SCORE:
        _STATS["score_block"] += 1
        return False, ("候选腿质量不达标：build_score=%.3f < %.2f（%s）—— 参数#53：score<0.77 的样本全部亏损；"
                       "此前因 T_BUY_TIER_LIMIT_ENABLED=0 绕过 build_sizing，该门槛从未生效"
                       % (sc, MIN_SCORE, "; ".join(map(str, (r.get("reasons") or [])[:2]))))
    return True, ""


def add_guard(first_buy_date: str, days_held: int, pnl_pct: float) -> Tuple[bool, str]:
    """③ 首买不达预期禁加仓（纯函数）。"""
    if not ON:
        return True, ""
    if days_held >= ADD_DAYS and pnl_pct <= ADD_PNL:
        _STATS["add_block"] += 1
        return False, ("加仓否决：首买(%s)已 %d 个交易日、浮亏 %.2f%%（≤%.1f%%）→ 不达预期不加码"
                       % (first_buy_date, days_held, pnl_pct, ADD_PNL))
    return True, ""


def first_buy_info(account_id: str, symbol: str) -> Tuple[Optional[str], int]:
    """首买日 + 至今交易日粗估（自然日 ×5/7，避免依赖交易日历）。"""
    import datetime as _dt
    import psycopg2
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL", ""), connect_timeout=4)
        try:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SET statement_timeout=4000")
            cur.execute("SELECT min(created_at) FROM paper_trades WHERE account_id=%s AND symbol=%s "
                        "AND direction IN (chr(20080)+chr(20837),chr(98)+chr(117)+chr(121)) "
                        "AND COALESCE(voided,0)=0", (account_id, symbol))
            row = cur.fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None, 0
        s = str(row[0])[:10]
        d0 = _dt.date(int(s[:4]), int(s[5:7]), int(s[8:10]))
        return s, max(0, int((_dt.date.today() - d0).days * 5 / 7))
    except Exception:
        return None, 0


def check(symbol: str, account_id: str, price: float, has_position: bool,
          avg_cost: float = 0.0, day: Optional[str] = None) -> Tuple[bool, str]:
    """总入口：无底仓 → ① 打分门槛；有底仓 → ③ 加仓不达预期。均 fail-open。"""
    if not ON:
        return True, ""
    _STATS["checked"] += 1
    if not has_position:
        return score_ok(symbol, as_of=day)
    try:
        fb, held = first_buy_info(account_id, symbol)
        if fb and avg_cost and price:
            pnl = (float(price) / float(avg_cost) - 1) * 100
            return add_guard(fb, held, pnl)
    except Exception:
        _STATS["errors"] += 1
    return True, ""
