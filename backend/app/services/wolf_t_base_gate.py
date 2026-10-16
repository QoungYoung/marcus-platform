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
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple


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

# ══════════════════════════════════════════════════════════════════════════
# ★ 2026-10-15 ✓（用户「T出要有T回对应」→ 选 A1）：**T出登记待回补 ⇒ 并入做T宇宙**
#
# 病灶 ✗（实测 t68 全窗，证据确凿）：
#   · 正T买腿（T回）**0 条** ✗，而 603986 在 0115 14:00 被「确认制T出」卖了 100 股 ✓
#   · 原因 = **做T宇宙自锁** ✗：`t_monitor._check_wolf_t_rules()` 只在
#       `{c.symbol for c in list_active_conditions(...) if _is_wolf_t_condition(c)}`
#     里找标的 ⇒ **T出本身不产生任何做T类条件腿** ⇒ 该标的进不了这个集合
#     ⇒ 永远不会被拿去做T判定 ⇒ 也就永远不会有 T回 ✓（鸡生蛋 ✓）
#   · 而 T出 与 高抛 的闸还**不对称** ✗：0115 日志原文
#       `sell 200股@249.96: rejected **无 T 仓不发高抛**`，可同日 14:00 的确认制T出却卖成了 ✓
#
# 本模块只做**一件事** ✓：把"有过确认制T出、且尚未买回、且仍有底仓"的标的**并入做T宇宙** ✓。
#   · 不新造条件行 ✗（避免写坏 `t_conditions` schema ✓）
#   · 不放宽任何闸 ✓：形态闸（`zheng_t_buy_quote`）、底仓闸、做T时段窗、churn/冷静期、
#     `refill_blocked`、网关「当日正T回补笔数上限」**全部照旧生效** ✓
#   · 开关 `WOLF_T_OUT_REFILL`（**库内默认 0 ⇒ 生产逐字不变** ✓）
#   · 窗口天数 `WOLF_T_OUT_REFILL_DAYS`（默认 3 ✓ —— 语料 2025-04-15 成文流程第 7 条
#     「如果**三日内**没有达到预期证明自己预期有问题」✓）
# ══════════════════════════════════════════════════════════════════════════

def refill_enabled() -> bool:
    """T出登记待回补（并入做T宇宙）开关 ✓ 默认关 ✓。"""
    return os.getenv("WOLF_T_OUT_REFILL", "0").strip().lower() in ("1", "true", "yes", "on")


def ambush_exclude_enabled() -> bool:
    """埋伏仓不做T（`WOLF_T_EXCLUDE_AMBUSH`，库内默认 0 = 关闭）✓"""
    return str(os.getenv("WOLF_T_EXCLUDE_AMBUSH", "0")).strip().lower() in ("1", "true", "yes", "on")


def ambush_held_symbols(account_id: str, days: int = 30) -> set:
    """返回「**埋伏仓**」标的集合 ✓（用户 2026-10-16：「**埋伏腿不该有T出T入**」）

    口径 ✓（一句话）：该票**最近一笔买入**的理由里含 `wolf_ambush_buy` ⇒ 这是埋伏建仓 ⇒ **不做 T** ✓
      为什么用"最近一笔买入"✗：埋伏是**建仓**语义 ✓（低位先手 ✓），做T是"趋势票/T仓"的事 ✓
        （语料：「低吸只在**趋势票**上摊薄」「尽量不要把做T的仓位**变加仓**」✓）；
        若之后又被低吸/T回买过 ⇒ 说明已进入做T链 ⇒ 不再算纯埋伏仓 ✓（保守 ✓）。
      大小写：臂里 symbol 是 `SH600584`、触发是 `sh600584` ⇒ 统一大写放入集合 ✓
      纯只读 ✓；任何异常 ⇒ 空集 ⇒ **退化为旧行为** ✓（绝不影响主链路 ✓）
    """
    out = set()
    try:
        if not ambush_exclude_enabled():
            return out
        from sqlalchemy import text as _text
        from app.database import SessionLocal
        n = int(days or 30)
        with SessionLocal() as db:
            rows = db.execute(_text(
                "SELECT symbol, reason, trade_date FROM paper_trades "
                "WHERE account_id = :a AND direction = '买入' "
                "ORDER BY trade_date DESC, id DESC LIMIT 4000"), {"a": account_id}).fetchall()
        seen = set()
        for sym, reason, _d in rows or []:
            s_up = str(sym or "").strip().upper()
            if not s_up or s_up in seen:
                continue
            seen.add(s_up)                     # 每个票只看**最近一笔买入** ✓
            if "wolf_ambush_buy" in str(reason or ""):
                out.add(s_up)
        return out
    except Exception:
        return set()


def pending_refill_symbols(account_id: str, days: Optional[int] = None) -> Dict[str, int]:
    """返回 {symbol: 待回补股数} ✓ ——「有过确认制T出 ∧ 之后未足额买回 ∧ 仍有持仓」✓。

    纯只读 ✓；任何异常 ⇒ 返回 {} ✓（绝不因本功能影响卖出/买入主链路 ✓）。
    """
    out: Dict[str, int] = {}
    try:
        if not refill_enabled():
            return out
        n = int(days if days is not None else (os.getenv("WOLF_T_OUT_REFILL_DAYS", "3") or 3))
        from sqlalchemy import text as _text
        from app.database import SessionLocal
        # ⚠️ 锚点必须是**该账户自己的最近交易日** ✗，不能用真实 today ✓
        #   实测（2026-10-15）：臂里成交的 trade_date 是**模拟日**（2026-01-15 ✓），
        #   用 `datetime.now()` 取窗口 ⇒ 一条都取不到 ⇒ 待回补恒为空 ✗（本函数第一版就踩了这个坑 ✓）。
        db = SessionLocal()
        try:
            _mx = db.execute(_text("SELECT MAX(trade_date) FROM paper_trades WHERE account_id=:a"),
                             {"a": account_id}).scalar()
            if not _mx:
                return {}
            dmax = str(_mx)[:10]
            # ⚠️ 窗口取长（60 天 ✓）：用户口径「**T出要有 T回 对应**」✓ ⇒ 待回补**持续到买回为止** ✓，
            #    不受 `WOLF_T_OUT_REFILL_DAYS`（那是"预期检查"的 3 日 ✓ 语料 2025-04-15「三日内」✓）限制 ✓
            d0 = (datetime.strptime(dmax, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d")
            rows = db.execute(_text(
                "SELECT symbol, trade_date, direction, COALESCE(volume,0), COALESCE(reason,'') "
                "FROM paper_trades WHERE account_id=:a AND trade_date >= :d0 ORDER BY trade_date, id"),
                {"a": account_id, "d0": d0}).fetchall()
        finally:
            db.close()
        # 按时间**顺序递推** ✓：T出 ⇒ 待回补 += vol ✓；其后的买入 ⇒ 抵扣 ✓
        #   ⚠️ 不能用"窗口内买入总和"抵扣 ✗ —— 实测：603986 0115 T出 100 股，
        #     而窗口里 0113 的建仓买入 200 股被拿去抵扣 ⇒ 待回补算成 0 ✗（第一版就错在这 ✓）。
        seq: Dict[str, int] = {}
        for sym, d, direction, vol, reason in (rows or []):
            s2 = str(sym or "")
            if not s2:
                continue
            v = int(vol or 0)
            if str(direction) == "卖出":
                if "确认制T出" in str(reason):
                    seq[s2] = seq.get(s2, 0) + v
            elif str(direction) == "买入":
                if s2 in seq:
                    seq[s2] = max(0, seq[s2] - v)
        out.update({k: v for k, v in seq.items() if v > 0})
    except Exception as _e:
        try:
            from app.services import gate_alarm as _ga
            _ga.note("wolf_t_base_gate:pending_refill", _e)
        except Exception:
            pass
        return {}
    return out


def last_tsell_price(account_id: str, days: Optional[int] = None) -> Dict[str, float]:
    """{symbol: **该笔待回补 T出 的成交价**} ✓ —— 供"**挂被动单在卖出位置**"用 ✓。

    语料 ✓（`docs/wolf-behavior-blueprint.md:232`）：「**被动挂单回补** | 已止损出局、等回到卖出位置
      → **挂被动单、不盯分时**（"下去不补，上来回到这个位置才补"）」✓
    为什么必须用它 ✗：实测 t68 —— T回 用**当时现价** ⇒ 必然撞两条纪律 ⇒
      「加仓口径：买入价 305.13 高于本轮首笔买入价 263.24 的 +4.0%」✗ ／
      「下去不补：最近卖出价 47.43，现价 45.29 更低」✗ ⇒ 14 blocked / 1 executed ✓
      ⇒ 挂"卖出位置"的被动单，两条纪律**自动同时满足** ✓。
    纯只读 ✓；异常 ⇒ {} ✓（绝不影响主链路 ✓）。
    """
    out: Dict[str, float] = {}
    try:
        if not refill_enabled():
            return out
        from sqlalchemy import text as _text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            _mx = db.execute(_text("SELECT MAX(trade_date) FROM paper_trades WHERE account_id=:a"),
                             {"a": account_id}).scalar()
            if not _mx:
                return {}
            dmax = str(_mx)[:10]
            d0 = (datetime.strptime(dmax, "%Y-%m-%d") - timedelta(days=2 * 30)).strftime("%Y-%m-%d")
            rows = db.execute(_text(
                "SELECT symbol, direction, COALESCE(volume,0), COALESCE(price,0), COALESCE(reason,'') "
                "FROM paper_trades WHERE account_id=:a AND trade_date >= :d0 ORDER BY trade_date, id"),
                {"a": account_id, "d0": d0}).fetchall()
        finally:
            db.close()
        left: Dict[str, int] = {}
        price: Dict[str, float] = {}
        for sym, direction, vol, px, reason in (rows or []):
            s2 = str(sym or "")
            if not s2:
                continue
            v = int(vol or 0)
            if str(direction) == "卖出":
                if "确认制T出" in str(reason):
                    left[s2] = left.get(s2, 0) + v
                    if float(px or 0) > 0:
                        price[s2] = float(px)          # 最近一笔 T出 价 ✓
            elif str(direction) == "买入":
                if s2 in left:
                    left[s2] = max(0, left[s2] - v)
                    if left[s2] == 0:
                        price.pop(s2, None)
        out.update({k: v for k, v in price.items() if left.get(k, 0) > 0})
    except Exception:
        return {}
    return out
