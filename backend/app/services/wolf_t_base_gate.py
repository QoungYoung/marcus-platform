# -*- coding: utf-8 -*-
"""wolf_t_base_gate.py — **正T买腿的前置：无底仓不发正T买腿**（狼大 2026-08-25）。

═══════════════════════════════════════════════════════════════════════════
## 语料（逐字）
· 2026-08-25 10:18：「除了那种我提前很久就定好计划的 完全无关盘面的操作我才做进去
  **今天但凡看短期的我一个子都没抄进去**。**我能不割肉已经算定力了**。」
· 2026-08-25（同一段）：「**我今天没抄底 没有资格T**。」
  → 同源归纳（docs/wolf-exit-playbook.md §2①）：**先有低吸仓位才有资格做T**。
· 2025-04-15 成文流程第 7 条：「**尽量不要把做T的仓位变加仓**；
  如果三日内没有达到预期证明自己预期有问题」
  → 即：做T腿**不承担建仓职责**；要建仓走建仓腿/条件腿。

## 为什么需要（实测证据，2026-09-21 · drabj13 全窗 828 条买类触发）
· **无底仓的正T买腿 89 条**：T+1 −1.64%(t=−2.87)｜**T+3 −2.66%(t=−4.20)**｜T+5 −2.37%(t=−3.30)｜
  T+10 −2.75%(t=−2.72)，胜率 33~37%  ⇒ **稳定负期望**。
· **有底仓的正T买腿 66 条**：T+1 +0.44%｜T+3 +0.71%｜T+5 +0.97%｜T+10 +0.40%，胜率 53~62%。
· 副作用：无底仓的正T买腿在网关侧会退化成"重新建仓"（`t_gateway.classify_escalation` 规则③
  「首开非底仓标的（新开仓风险）」→ 转人工/超时取消）。2026-03-24 那天 19 条买腿就是这么消失的
  （当天买腿成交 0 条），而 03-30 同类买腿经 AI 决策正常成交 —— 差别只在"有没有底仓"。

## 口径
· `enabled()`：环境变量 `WOLF_T_BUY_REQUIRE_BASE`，**库内默认 0（关）**；回测 `jobs/bt_env_pins.sh` 设 1。
· `allow(has_base)`：有底仓 → (True, "")；无底仓 → (False, 原因)。
· 作用面：**只作用于 `wolf_zheng_t_buy`（正T买腿）**；不碰建仓腿/条件腿/回补/卖腿。
· 关掉时逐字返回旧行为（不拦），生产零影响。
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
import os
from typing import Tuple


def enabled() -> bool:
    return os.getenv("WOLF_T_BUY_REQUIRE_BASE", "0").strip().lower() in ("1", "true", "yes", "on")


def ai_review() -> bool:
    """**新开仓改 AI 审核**（`WOLF_NEWPOS_AI_REVIEW`，库内默认 0）。

    背景：`t_gateway.classify_escalation` 规则③把「首开非底仓标的（新开仓风险）」升级为 **human**，
    在 AI 主导/回测架构里没有人来确认 ⇒ `t_gateway.TRIGGER_EXEC_TIMEOUT_MIN=2` 超时 **cancelled**
    （2026-03-24 上一轮实测 19 条买腿就是这样消失的，当天 0 成交）。
    打开后：规则③改为升级到 **agent** ⇒ 由 AI 按它自己的四项否决做裁决；AI 仍不可达时标
    `await_retry`（保留活性）而不是 `human_confirm`（黑洞）。

    生产默认关（人工确认仍是默认策略）；回测 `jobs/bt_env_pins.sh` 打开。
    """
    return os.getenv("WOLF_NEWPOS_AI_REVIEW", "0").strip().lower() in ("1", "true", "yes", "on")


def allow(has_base: bool) -> Tuple[bool, str]:
    """无底仓是否允许发正T买腿 → (allow, 原因)。开关关时一律放行（逐字旧行为）。"""
    if not enabled():
        return True, ""
    if has_base:
        return True, ""
    return False, ("无底仓不发正T买腿（狼大 2026-08-25「**我今天没抄底 没有资格T**」；"
                   "2025-04-15 成文流程「尽量不要把做T的仓位变加仓」）")
