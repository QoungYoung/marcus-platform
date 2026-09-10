# -*- coding: utf-8 -*-
"""wolf_context.py — 狼大"分语境"闸门（P1-3, 2026-09-10）

用途: 判定 253（指数 5min 急杀 → 低吸）当前语境**是否允许**。

狼大语义（分语境, 非时钟窗）:
    筑底 / 上行  → 急杀可以接
    未筑底（下跌中 / 诱多反弹 / 最后一跌）→ 不该在急杀那一刻接

狼大原话取证（检索脚本 .dsh-tmp/wolfbt/gitwork/wolf_evidence2.py）:
  · 2025-06-05「要么继续震荡，但是主线板块筑底行情…**急杀可以买，缓跌不买**」   → 筑底语境可接
  · 2025-07-28「现在不是大盘跳水要不要出，而是**跳水了你高位减仓的钱敢不敢买**才对」→ 上行回踩可接
  · 2026-05-15「这是一个区间 只要磨出底部结构 就是抄底迹象…**肯定不是急杀的时候买啊**」→ 未磨出底部结构时不接

判据来源: `wave_agent.py` 的 `sub_level` 标签。该标签**本身即按狼大原话定义**
（见 wave_agent.py 提示词 253-256 行: 4-3=筑底/可分批接主线, B反=典型诱多别抄底,
 C杀=最后杀跌/买在确定而非买在最低, 4-5=常为失败5浪/防御等企稳…），故本模块不复刻、不另造语境体系。

**本闸门不复刻任何时钟窗** —— 语料中狼大无时段规则; 原生产/回测里的 "09:45-14:40" 是回测变体产物,
不是狼大规则（语料中的 9:45/14:00 均为发帖时间戳）。

环境变量:
  WOLF_253_CONTEXT=0        关闭本闸门（回退到"任何语境都允许 253"）
  WOLF_253_CONTEXT_UNKNOWN  unknown/block（默认 block）—— 语境未收录且 operation 无法兜底时是否放行
"""
import json
import os

DATA = os.environ.get("DATA_DIR", "/app/data")

# ── 语境 → 253 是否允许（映射依据 wave_agent.py 中狼大原始标注, 逐条附出处语义）──
ALLOW = {
    "3-1": "大3-1 起步建仓（狼大: 3-1起步建仓）",
    "3-2": "大3-2 调整（狼大: 持仓等回调, 短调整不恐）",
    "3-3": "大3-3 最强主升（狼大: 重仓追）",
    "3-4": "大3-4 中继（狼大: 波段做T）",
    "3-5": "大3-5 末段（仍在上行; 但狼大提示其后进入大4, 需减仓切换）",
    "4-3": "大4-3 筑底（狼大: 可走4-4但别预期太高, 调仓换股分批接主线, 轻仓）"
           " —— 正是'磨出底部结构'的语境",
    "W底": "W底 确认建仓（狼大: 确认建仓）",
    "下跌浪4反弹": "下跌浪中的4反弹（狼大: 确认4必有5, 一旦确认先卖 → 反弹段可做）",
}

BLOCK = {
    "4-1": "大4-1 回调初（狼大: 观望降仓）",
    "4-2": "大4-2 ≈大盘ABC的B反（狼大: 反弹诱多, 只做T不追, 等结束规避C）",
    "4-4": "大4-4 反弹（狼大: 只按箱体做波动做T, 减少操作, 不要碰个股分化 → 不宜新开）",
    "4-5": "大4-5 常为失败5浪（狼大: 最后一跌, 防御等企稳）",
    "失败5": "失败5浪（狼大: 最后一跌, 防御等企稳）",
    "B反": "B反（狼大: 典型诱多别抄底, 等B反走完还是C）",
    "C杀": "C杀（狼大: 最后杀跌, **买在确定而非买在最低**）→ 不在急杀那一刻接, 等确认",
    "双头/M顶": "双头/M顶（狼大: 见顶防跌）",
    "衰竭浪": "衰竭浪（狼大: 多次诱多出货, 买对主线否则4浪一起摁下来）",
    "ABC": "完整ABC三段（A跌B反C杀）→ 段内位置不明, 不在急杀接",
}

# 下跌级别 → 直接禁止
BLOCK_LEVELS = {"d2", "down"}


def load_wave():
    try:
        with open(os.path.join(DATA, "wave_state.json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def context_of(wave=None):
    """返回当前语境标签 (level, sub_level, operation)。"""
    w = wave if wave is not None else load_wave()
    return (str(w.get("level") or "").strip(),
            str(w.get("sub_level") or "").strip(),
            str(w.get("operation") or "").strip())


def m5dump_allowed(wave=None):
    """253（指数急杀低吸）在当前语境是否允许 → (bool, reason)。

    判定顺序: sub_level 白/黑名单 → level 黑名单 → operation 兜底 → unknowns 策略(默认 block)。
    """
    if os.getenv("WOLF_253_CONTEXT", "1").strip() in ("0", "false", "no"):
        return True, "闸门关闭(WOLF_253_CONTEXT=0)"

    lv, sl, op = context_of(wave)

    if not (lv or sl or op):
        return False, "语境缺失(wave_state 不可读/为空) → fail-closed"

    if sl in ALLOW:
        return True, "语境允许: sub_level=%s（%s）" % (sl, ALLOW[sl])
    if sl in BLOCK:
        return False, "语境禁止: sub_level=%s（%s）" % (sl, BLOCK[sl])
    if lv in BLOCK_LEVELS:
        return False, "语境禁止: level=%s（下跌段）, sub_level=%s" % (lv, sl)

    # 未收录的 sub_level → 依 operation 兜底（wave_agent 的 operation 亦按狼大语义给出）
    if op in ("build", "t_only", "side"):
        return True, "语境未收录(sub_level=%s), operation=%s → 放行" % (sl, op)
    if op in ("defense", "exit"):
        return False, "语境禁止: operation=%s（狼大: 防御/兑现）, sub_level=%s" % (op, sl)

    if os.getenv("WOLF_253_CONTEXT_UNKNOWN", "block").strip().lower() == "allow":
        return True, "语境未知(lv=%s/sl=%s/op=%s) → 按配置放行" % (lv, sl, op)
    return False, "语境未知(lv=%s/sl=%s/op=%s) → fail-closed" % (lv, sl, op)


if __name__ == "__main__":
    import sys
    lv, sl, op = context_of()
    ok, why = m5dump_allowed()
    print(json.dumps({"level": lv, "sub_level": sl, "operation": op,
                      "m5dump_allowed": ok, "reason": why}, ensure_ascii=False))
    sys.exit(0)
