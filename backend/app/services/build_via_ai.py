# -*- coding: utf-8 -*-
"""build_via_ai.py — 建仓类一律**过 AI 决策**（§9.841，用户 2026-10-13 拍板 ✓）

【为什么 ✗】条件命中的"自动执行闭环（迭代#57）"会**直接下单**建仓 ✗ —— 而 AI 提示词里那条
  「现价与目标价/建议价**脱节 >1%** ⇒ wait 不追」（`prompt_seeds.py:915` ✓）对建仓腿**不生效** ✗。
  实测（t46 臂 2026-05-06 SZ002812 恩捷股份 ✓）：腿触发位 84.47 ✓、成交 **86.82（+2.78%）** ✗；
  同日同票 `wolf_zheng_t_buy @86.71`（**更低** ✗）反被判「脱节 4.6% ⇒ 属追高 ⇒ 不追」✗ ⇒ 口径自相矛盾 ✓

【两条建仓路径都要过 AI ✓】
  ① 条件单命中（`t_monitor` 的自动执行闭环）⇒ 已在 §9.840 由 `WOLF_BUILD_VIA_AI=1` 改为送 agent ✓
  ② **盘中扫描器直连建仓** ✗（`t_trend_break` / `t_vrebounce` / `t_vreb_etf` 直接调 `build_t_position` ✓）
     ⇒ 本模块给出统一入口 `defer_to_ai()` ✓：**只写一条 pending 触发** ✓，
       由既有的 `t_bridge` 认领并唤醒 agent ✓（不自己再唤醒 ⇒ 避免重复唤醒/重复执行 ✗）

【开关 ✓】`WOLF_BUILD_VIA_AI`（**库内默认 0** ✓ = 现状逐位不变 ✓；回测/灰度置 1 ✓）
  例外：这几个扫描器本身还有各自的 `T_*_ENABLED` 灰度开关 ✓（生产默认全关 ✓）
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


def enabled() -> bool:
    return str(os.getenv("WOLF_BUILD_VIA_AI", "0")).strip().lower() in ("1", "true", "yes", "on")


def defer_to_ai(symbol: str, price: Any, kind: str, reason: str = "",
                mode: str = "", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把一次"扫描器直连建仓"改成**送 AI 决策** ✓ ⇒ 返回 `{}` 表示未启用（调用方照旧直连 ✓）

    返回非空时形如 ⇒ `{"status": "pending_ai", "trigger_id": N, "reason": "..."}` ✓ 调用方应
    **跳过** `build_t_position` 并把它记进结果 ✓。
    """
    if not enabled():
        return {}
    try:
        from app.services import t_db
    except Exception as e:                      # 取不到 DB ⇒ 退回旧行为（fail-open ✓ 不制造新故障 ✓）
        print("[build_via_ai] t_db 不可用 ⇒ 保持直连建仓 ✓: %s" % str(e)[:70], flush=True)
        return {}
    try:
        _px = float(price or 0) or None
    except Exception:
        _px = None
    trig: Dict[str, Any] = {
        "symbol": symbol,
        "event_type": kind,
        "direction": "buy",
        "trigger_price": None,                  # 扫描器是"实时价直接买"⇒ 无预先触发位 ✓
        "quote_price": _px,
        # ★ 建议买价 = 当前价 ✓ ⇒ agent 的"脱节>1%"判据有参照物 ✓（否则它会因缺参照而无法判断 ✗）
        "suggest_bid_price": _px,
        "reason": "[BUILD-VIA-AI] %s（%s）属建仓类 ⇒ 不自动下单 ✗，已送 agent 决策 ✓%s"
                  % (kind, mode or "-", ("｜" + str(reason)[:120]) if reason else ""),
        "mode": "ai",
        "snapshot": {"source": "scanner", "build_mode": mode, "quote": {"current": _px}},
    }
    if extra:
        trig.update(extra)
    try:
        tid = t_db.insert_trigger(trig, status="pending")     # pending ⇒ 由 t_bridge 认领并唤醒 agent ✓
    except Exception as e:
        print("[build_via_ai] 触发写入失败 ⇒ 保持直连建仓 ✓: %s" % str(e)[:80], flush=True)
        return {}
    if not tid:
        return {}
    print("[build_via_ai] %s %s ⇒ 已送 AI（触发 #%s ✓ 不直接建仓 ✗）" % (kind, symbol, tid), flush=True)
    return {"status": "pending_ai", "trigger_id": tid,
            "reason": "建仓类已送 agent 决策（触发 #%s）✓" % tid}
