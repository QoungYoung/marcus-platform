# -*- coding: utf-8 -*-
"""E1b：把度量从「他的命名」换成「他的操作」—— 日线档 vs 日线档×周线档（度），每组都给基线。

背景（E1 失败原因 ①）：他的「4-3 筑底 / 震荡」是**位置命名**、不是方向 ⇒ 拿极性比属错配 ✗。
本脚本改用**他的动作**做 ground truth：买/加/接 = 多；减/止盈/不参与/防守 = 空。

标注规则（可复核，逐条写入 LABELS；每条带**日期 + 原话**）：
  · **指数/账户级**才算：原话必须是 大盘/指数/上证/A股 或他的**总仓位**（讲单票/单板块的 ⇒ 不可比 ✗）；
  · **第一人称已发生/正在做的动作**（我买了 / 我减仓了 / 我降到 10% / 我仓位不会增加）；
  · **条件句不算**（如果/一旦/只要/要么/等…就…）—— 与 E1 golden set「条件句不算状态」同口径 ✓；
    但若条件**可用数据判定且成立**（例：2025-03-26「只要不破 3334 就抄底」— 3334 确实没破）⇒
    计入 COND 集（第二个样本集，**两套都报** ✓，防条件句排除法偏向某一侧 ✗）；
  · 明确「不加也不减 / 持股不动」⇒ 记 `hold`（**不进二分类**，单列，防污染基线 ✓）；
  · 「只做T / 做差价」⇒ 不进二分类（他自己把 T 与加减仓分开说 ✓）。

三组对照（**每组都报基线**）：
  ① 日线档（生产口径 operation）→ 操作方向；
  ② 日线档 × 周线档（度）→ 操作方向。主检验 R2「回调豁免」：日线说空（defense/exit）而**周线大级别未破**
     （rung ≤ 1）⇒ 读作 3-2 主升内回调 ⇒ 多（依据原表：3-2 回调 = side「持仓等回调」；
     4-5/失败5 = defense —— 两者日线不可分 ⇒ 正是要用「度」分的那一刀）；
  ③ 基线：多数类（照众数）/ 全给「空」/ 全给「多」（＝永远 side/t_only，卡上点名的基线）。

判分口径：他的动作 ∈ {多, 空}；我们 build/side = 多、defense/exit = 空、**t_only = 未表态 ⇒ 记不中**
（对 ①②③ 一致，不偏袒任一组 ✓）；另给「t_only 记作空」的敏感性。统计用 McNemar 精确检验。

用法：.venv/bin/python jobs/eval_wave_ladder_ops_degree.py
"""
import bisect
import collections
import importlib
import json
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.chdir(ROOT)
import wave_ladder as W  # noqa: E402

# 周线做大级别的**原则性**取值（同 E1，不改 ✗）：RALLY=26 周（≈日线 20 日的同一度）／HI=104 周（≈60 日）／
# ORIGIN=200 周（≈900 日的同量级）；MIN_BARS=60 周（≈80 交易日）。**不许按标注集调** ✗。
WEEKLY_ENV = {"WOLF_WAVE_LADDER_RALLY": "26", "WOLF_WAVE_LADDER_HI": "104",
              "WOLF_WAVE_LADDER_ORIGIN_BARS": "200", "WOLF_WAVE_LADDER_MIN_BARS": "60"}
OVERRIDE = tuple(WEEKLY_ENV) + ("WOLF_WAVE_LADDER_RALLY",)

# ────────────── golden set A（严格：无条件句）date → (方向, 原话, 依据) ──────────────
LABELS: Dict[str, Tuple[str, str, str]] = {
    # 多：他明确在买/加/回补（指数或总仓位级别）
    "2025-02-20": ("多", "指数方面，不用担心，已经完成了2浪调整阶段，接下来低点都是三浪的起头",
                   "说指数已完成 2 浪调整、低点都是三浪起头（建仓方向）；同日「止损在很下面的位置」"),
    "2025-03-11": ("多", "指数我不担心，问题是个股要怎么选而已了 目前又回到90%仓位了",
                   "「又回到 90% 仓位」＝已把仓位加回来"),
    "2025-05-26": ("多", "也就是说这里我买了向上可以看到3393，有几十个点的幅度期望",
                   "「这里我买了」＋指数点位 3393；同日「可以吃到60%仓位」"),
    "2025-07-24": ("多", "主升行情的调整别人都不是拿来做T的。是拿来进场的",
                   "他的**操作原则**原话（讲大盘 3180/3316/3456 入场位）：「调整是拿来进场的」"),
    "2025-10-19": ("多", "这是大盘 周线级别的图，我从这个图来判断建仓和清仓的趋势级别操作……现在是主升级别大3浪",
                   "周线图判定：「趋势级别反转…主升级别大3浪」⇒ 建仓方向（E1 已定性 ✓）"),
    "2026-04-02": ("多", "这里不存在打满 可以4层仓位加到6层 明天T出回到4层",
                   "「4 层仓位加到 6 层」＝当日在加仓（T 出是次日的事）"),
    # 空：他明确在减/清/不参与/防守（指数或总仓位级别）
    "2025-04-29": ("空", "趁今天拉升 我降仓位到10%，剩1个稀土 1个免税过节",
                   "「降仓位到 10%」＝大幅减仓过节"),
    "2025-05-22": ("空", "宁可不赚，或者少赚，也要防止大亏的可能",
                   "「宁可…也要防止大亏」＋「明天再不反抽我也要撤掉今天买的」＋30% 防御仓位"),
    "2025-06-13": ("空", "少打几个字 一样 仓位降低到10%以下",
                   "「仓位降低到 10% 以下」；同日「我需要减仓应对周末…风险」"),
    "2025-10-10": ("空", "所以我目前不会超过50%仓位，盘中甚至还可以少一点。不做正T",
                   "「不会超过 50% 仓位…非常保守」（只看指数级别低点）"),
    "2025-10-13": ("空", "反抽到-2以上的我都减了，没到的没办法动 目前37%仓位",
                   "「我都减了」＋37% 仓位；同日「我减是因为我的策略要把整体仓位控下来」"),
    "2025-11-23": ("空", "接下来就不是继续加仓，而是把抄底仓位获利出局 减少亏损",
                   "「把抄底仓位获利出局」＝减"),
    "2025-11-26": ("空", "我防守一波 机器人减了点仓位，目前重组和金银类较多。 保持50%仓位附近",
                   "「我防守一波」＋减仓＋保持 50%；同日「防止C杀」"),
    "2025-12-03": ("空", "A股没有机会 就不做了 这次B反的涨幅我2天就吃到了 所以直接转防守",
                   "「A 股没有机会就不做了…直接转防守」"),
    "2026-01-14": ("空", "今天会减到55%-60%仓位 直到过本周股指交割日",
                   "「今天会减到 55%-60% 仓位」；同日「过不过新高我都会减仓」"),
    "2026-01-15": ("空", "我减仓是因为目前是调整阶段 等上涨阶段我又会继续满仓",
                   "「我减仓是因为目前是调整阶段」＝已减、理由是调整"),
    "2026-03-09": ("空", "今天没有安全一说 我仓位不会增加",
                   "「我仓位不会增加」＋「70% 仓位内」（拒绝加仓）"),
    "2026-03-22": ("空", "我会在这两天有机会的时候再减到50%内",
                   "「再减到 50% 内」；同段「一旦放量回到我卖出位置我就买回」＝卖后买回的条件"),
    "2026-03-27": ("空", "做ETF大盘没有趋势做不了……这里太难了 我选择不做。 目前43%仓位 忍到下个月16号之后",
                   "「大盘没有趋势做不了…我选择不做」＋降到 43% 仓位"),
    "2026-04-09": ("空", "下午不管涨还是跌 我都会找个地方减回50%-55%仓位 我昨天没有减 今天会减",
                   "「我都会找个地方减回 50%-55% 仓位」"),
    "2026-04-16": ("空", "我目前已经降低到66%仓位 还有15%是药 已经转到防守形态 我可以少挣钱 我不想亏钱",
                   "「降低到 66% 仓位…已经转到防守形态」"),
    "2026-04-23": ("空", "大盘BOLL中轨我减仓了",
                   "「大盘 BOLL 中轨我减仓了」＝指数级减仓"),
    "2026-04-28": ("空", "特别是今天 我不做加法 哪个冲了减哪个 减到我的合理仓位",
                   "「我不做加法…减到我的合理仓位」"),
    "2026-05-15": ("空", "首先 降低资金仓位，不上杠杆",
                   "「降低资金仓位，不上杠杆」＝总仓位级降仓"),
    "2026-08-04": ("空", "目前走大2浪 不参与 坐等企稳和指数共振走主升大3",
                   "「走大 2 浪 不参与」＋坐等（明确不参与）"),
    # hold：明确「不加也不减」（单列，不进二分类）
    "2025-04-21": ("hold", "我这里不会加仓了 但是也不卖 持股不动 目前70%仓位",
                   "不加也不卖 ⇒ 中性，不进二分类"),
    "2025-05-06": ("hold", "留一半 然后不动 我换别的继续埋伏 总仓位不加不减 收盘30%",
                   "「总仓位不加不减」⇒ 中性"),
    "2026-02-13": ("hold", "没什么大事 我7层仓位过年 如果站上中轨我就80%的",
                   "维持 7 层过年，加仓是**条件句** ⇒ 中性"),
    "2026-04-14": ("hold", "仓位也很简单 50%的底仓 30%左右做日内……剩下20%左右应对川普说话黑天鹅的抄底",
                   "描述仓位结构（50/30/20）⇒ 无可判方向"),
}
# golden set B（+可验证条件句：条件已用指数数据核对，成立 ⇒ 动作确实发生 ✓）
COND_LABELS: Dict[str, Tuple[str, str, str]] = {
    "2025-03-26": ("多", "盘中只要不是放量下杀过3334都是我的抄底位 抄底50%-70%仓位，做个两天",
                   "条件（不破 3334）成立 ✓：03-26 收 3368.70、03-28 收 3351.31 均在 3334 上 ⇒ 抄底成立"),
    "2026-02-11": ("多", "只要明后天没有指数级别大涨 我活跃仓位80%仓位以上过节",
                   "条件（无指数级别大涨）成立 ✓：02-12 +0.05%、02-13 -1.26% ⇒ 80% 仓位过节"),
    "2025-10-21": ("空", "我预期在3900上方减仓回45%仓位以下",
                   "条件（指数到 3900 上方）当日即成立 ✓：10-21 收 3916.33 ⇒ 减仓计划生效"),
}
# 不可比（记录在案，防「挑着用」✗）
SKIPS = {
    "2025-06-05": "条件句＋多空混说：「要么带量突破 空翻多 50%仓位加到80% 要么继续震荡」",
    "2025-06-24": "讲个股（全部仓位在电池里）⇒ 非指数/账户级",
    "2025-10-15": "讲机器人板块的调整（板块 3-4）⇒ 非指数级",
    "2025-10-22": "条件句且无法验证：「等下一个进场指数区间我就开始摁进去了」",
    "2025-10-29": "第三人称建议＋条件句：「激进点的如果明天开盘符合加仓条件…可以回补仓位了」",
    "2025-11-22": "讲黄金/期货多单 ⇒ 非 A 股指数",
    "2026-01-20": "多空混说：同日「大约明后天我会买回游戏ETF」＋「GJD最近要调整 我就仓位小点」",
    "2026-01-22": "多空混说：同日「买回了部分游戏ETF」＋「所以你们就是没减仓」",
    "2026-05-06": "讲半导体方向（高位不加仓）⇒ 非指数/账户级",
    "2026-05-11": "多空混说：「这里虽然不要加仓是对的 但是这里空仓一定是错的」",
}

SIGN = {"build": "多", "side": "多", "t_only": "T", "defense": "空", "exit": "空"}
SHORT = {"side": "side多", "build": "build多", "defense": "def空", "exit": "exit空", "t_only": "t_only"}
BIG_UP, BIG_FAIL = "未破", "已失败"


def _walk(env: Dict[str, str], rows) -> List[dict]:
    """按给定窗口参数重载 wave_ladder 再走链（E1 同法：默认值一字未改 ✓，只在 env 覆盖时变 ✓）。"""
    for k in OVERRIDE:
        os.environ.pop(k, None)
    os.environ.update(env)
    importlib.reload(W)
    return W.walk(rows)


def load_weekly(code: str = "000001.SH") -> List[Tuple[str, float, float, float]]:
    p = os.path.join("data", "index_weekly_%s.json" % code.replace(".", "_"))
    d = json.load(open(p, encoding="utf-8"))
    return [(r[0], r[1], r[2], r[3] or 0) for r in d["rows"]]


def at(evs: List[dict], d8: str) -> Optional[dict]:
    by = [e["date"] for e in evs]
    i = bisect.bisect_right(by, d8) - 1
    return evs[i] if i >= 0 else None


def big_level(ev_w: Optional[dict]) -> str:
    """周线档 → 大级别两态：rung0/1（4浪调整/成功4-5）= **未破**；rung2/3 = **已失败** ✓。
    依据：RUNGS 按恶化程度排序，rung2 = 失败4-5、rung3 = 下跌1浪（跌破主升 1 浪顶 ＝ 铁律②失效）✓。"""
    if ev_w is None:
        return "?"
    return BIG_FAIL if ev_w["rung_index"] >= 2 else BIG_UP


# ────────────── 三种组合规则（**先声明，后看数**；没有一个是从标注集拟合出来的 ✓）──────────────
def r2_callback_exempt(s_d: str, big: str) -> str:
    """② 主检验「回调豁免」：日线为准；日线说空而周线大级别未破 ⇒ 读作 3-2 主升内回调 ⇒ 多。"""
    return "多" if (s_d == "空" and big == BIG_UP) else s_d


def r2b_both_ways(s_d: str, big: str) -> str:
    """② 对称敏感性：再加「日线说多而周线已失败 ⇒ 读作 4-4/B反 ⇒ 空」。"""
    if s_d == "空" and big == BIG_UP:
        return "多"
    if s_d == "多" and big == BIG_FAIL:
        return "空"
    return s_d


def r1_degree_first(s_d: str, big: str) -> str:
    """② 极端敏感性「度优先」：完全不听日线。"""
    return "多" if big == BIG_UP else "空"


def r3_both_agree(s_d: str, big: str) -> str:
    """② 保守敏感性「两边一致才看多」：否则不参与。"""
    return "多" if (s_d == "多" and big == BIG_UP) else "空"


RULES = [("② R2 回调豁免（主检验）", r2_callback_exempt),
         ("② R2b 双向", r2b_both_ways),
         ("② R1 度优先", r1_degree_first),
         ("② R3 一致才看多", r3_both_agree)]


def mcnemar(a_ok: List[bool], b_ok: List[bool]) -> Tuple[int, int, float]:
    """精确 McNemar（①vs②）：只看不一致对，二项检验（双侧）。"""
    b01 = sum(1 for x, y in zip(a_ok, b_ok) if (not x) and y)   # ①错②对
    b10 = sum(1 for x, y in zip(a_ok, b_ok) if x and (not y))   # ①对②错
    n = b01 + b10
    if n == 0:
        return b01, b10, 1.0
    k = min(b01, b10)
    p = 2 * sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return b01, b10, min(1.0, p)


def build_rows(labels: Dict[str, Tuple[str, str, str]], ev_d: List[dict], ev_w: List[dict]) -> List[dict]:
    by = [e["date"] for e in ev_d]
    out = []
    for d8, (act, quote, why) in sorted(labels.items()):
        d8n = d8.replace("-", "")
        i = bisect.bisect_right(by, d8n) - 1
        if i < 0:
            continue
        e_d, e_w = ev_d[i], at(ev_w, d8n)
        out.append({"date": d8, "his": act, "his_quote": quote, "why": why,
                    "our_op": e_d["operation"], "our_sign": SIGN.get(e_d["operation"], "?"),
                    "our_name": e_d["name"], "our_rung": e_d["rung_index"],
                    "w_rung": e_w["rung_index"] if e_w else None,
                    "w_name": e_w["name"] if e_w else None,
                    "big": big_level(e_w)})
    return out


def verdict(rows: List[dict], tag: str) -> dict:
    binary = [r for r in rows if r["his"] in ("多", "空")]
    n_short = sum(1 for r in binary if r["his"] == "空")
    n_long = len(binary) - n_short
    daily_ok = [r["our_sign"] == r["his"] for r in binary]

    def _acc(fn) -> Tuple[int, List[bool]]:
        flags = [fn(r) == r["his"] for r in binary]
        return sum(flags), flags

    def _acc_t_is_short(fn) -> int:
        return sum(1 for r in binary if (fn(r) if fn(r) != "T" else "空") == r["his"])

    print("\n=== %s ｜ 样本 %d（多 %d / 空 %d，多数类 %s %.0f%%）===" % (
        tag, len(binary), n_long, n_short, "空" if n_short >= n_long else "多",
        100.0 * max(n_long, n_short) / len(binary)))
    print("%-28s %10s %8s %10s" % ("预测", "命中", "命中率", "T记作空"))
    print("%-28s %10d %7.0f%% %9.0f%%   %s" % (
        "① 日线档（生产 operation）", sum(daily_ok), 100.0 * sum(daily_ok) / len(binary),
        100.0 * _acc_t_is_short(lambda r: r["our_sign"]) / len(binary), "build/side=多、defense/exit=空、t_only=未表态"))
    wk_ok, _ = _acc(lambda r: "多" if r["big"] == BIG_UP else "空")
    print("%-28s %10d %7.0f%% %9s   %s" % ("（参考）周线档单独用", wk_ok, 100.0 * wk_ok / len(binary), "—",
                                           "周线 rung≤1 ⇒ 多，rung≥2 ⇒ 空"))
    rule_flags = {}
    for name, fn in RULES:
        ok, flags = _acc(lambda r, fn=fn: fn(r["our_sign"], r["big"]))
        rule_flags[name] = flags
        print("%-28s %10d %7.0f%% %9.0f%%   %s" % (name, ok, 100.0 * ok / len(binary),
                                                   100.0 * _acc_t_is_short(lambda r, fn=fn: fn(r["our_sign"], r["big"])) / len(binary),
                                                   fn.__doc__.split("：")[1][:30]))
    for name, k in (("③ 基线：多数类（照众数）", n_short if n_short >= n_long else n_long),
                    ("③ 基线：全给「空」", n_short), ("③ 基线：全给「多」（side/t_only）", n_long)):
        print("%-28s %10d %7.0f%% %9s   %s" % (name, k, 100.0 * k / len(binary), "—", ""))
    b01, b10, p = mcnemar(daily_ok, rule_flags[RULES[0][0]])
    print("McNemar 精确检验（① vs ②R2）：①错②对 %d ｜ ①对②错 %d ｜ 双侧 p = %.3f" % (b01, b10, p))
    by_op = collections.defaultdict(lambda: [0, 0])
    for r, ok in zip(binary, daily_ok):
        by_op[r["our_op"]][0] += 1
        by_op[r["our_op"]][1] += int(ok)
    print("① 分档命中（我们的档 → 他的动作）：%s" % " ｜ ".join(
        "%s %d/%d" % (SHORT.get(k, k), v[1], v[0]) for k, v in sorted(by_op.items())))
    print("列联表（周线度 × 日线方向 → 他实际在买的比例；t_only 不在此表 ✓）：")
    for big in (BIG_UP, BIG_FAIL):
        for sg in ("多", "空"):
            cell = [r for r in binary if r["big"] == big and r["our_sign"] == sg]
            if not cell:
                print("  %-3s × 日线%-2s：n=0" % (big, sg))
                continue
            n_buy = sum(1 for r in cell if r["his"] == "多")
            note = "  ← R2 把这 %d 条「空」全翻成「多」✗" % (len(cell) - n_buy) if (big == BIG_UP and sg == "空") else ""
            print("  %-3s × 日线%-2s：n=%-2d 他买 %d（%.0f%%）%s" % (big, sg, len(cell), n_buy, 100.0 * n_buy / len(cell), note))
    return {"binary": binary, "daily": [sum(daily_ok), len(binary)], "weekly_only": [wk_ok, len(binary)],
            "rules": {k: [sum(v), len(binary)] for k, v in rule_flags.items()},
            "base_majority": max(n_long, n_short), "base_short": n_short, "base_long": n_long,
            "mcnemar_daily_vs_r2": [b01, b10, p]}


def main() -> int:
    ev_d = _walk({}, W.load_rows())        # ① 日线（生产口径，默认窗口 ✓）
    ev_w = _walk(WEEKLY_ENV, load_weekly())  # ② 周线（大级别口径 ✓）
    rows_a = build_rows(LABELS, ev_d, ev_w)
    rows_b = build_rows(dict(LABELS, **COND_LABELS), ev_d, ev_w)
    holds = [r for r in rows_a if r["his"] == "hold"]
    print("golden set A（严格，无条件句）：可比 %d ｜ hold %d ｜ 未入集 %d ｜ 年份 %s" % (
        sum(1 for r in rows_a if r["his"] in ("多", "空")), len(holds), len(SKIPS),
        dict(collections.Counter(r["date"][:4] for r in rows_a if r["his"] in ("多", "空")))))
    print("golden set B（A + 可验证条件句 %d 条）：可比 %d" % (
        len(COND_LABELS), sum(1 for r in rows_b if r["his"] in ("多", "空"))))
    sa = verdict(rows_a, "集 A 严格")
    sb = verdict(rows_b, "集 B（含可验证条件句）")

    print("\n=== 逐条明细（② = R2 回调豁免）===")
    for r in rows_b:
        pred = r2_callback_exempt(r["our_sign"], r["big"])
        flag = "hold" if r["his"] == "hold" else ("✓" if pred == r["his"] else "✗")
        print("  %s 他=%-4s 日线=%-8s 周线=rung%d/%-7s(%-3s) ②=%-2s %-4s | %s" % (
            r["date"], r["his"], SHORT.get(r["our_op"], r["our_op"]), r["w_rung"],
            r["w_name"] or "?", r["big"], pred, flag, r["his_quote"][:32]))
    print("\n=== 他 hold（不加不减）时我们给的档 ===")
    for r in holds:
        print("  %s 日线=%-8s 周线=rung%d/%-3s | %s" % (
            r["date"], SHORT.get(r["our_op"], r["our_op"]), r["w_rung"], r["big"], r["his_quote"][:32]))
    print("\n未入集 %d 条（附原因，防挑样本 ✗）：" % len(SKIPS))
    for k, v in sorted(SKIPS.items()):
        print("  %s %s" % (k, v))

    json.dump({"A_strict": sa, "B_with_cond": sb, "hold": holds, "skips": SKIPS,
               "rows_A": rows_a, "rows_B": rows_b},
              open(os.path.join("data", "_wave_ops_golden.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n→ data/_wave_ops_golden.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
