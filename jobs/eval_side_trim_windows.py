# -*- coding: utf-8 -*-
"""E1c：**条件分布** —— 只看我们链判为 `side` 的日子，找他的「减仓择时」线索。

背景（E1/E1b 两次负面结果，见 docs/wave-operation-table.md §4.2/§4.3）：
  · 我们的 `operation` 是**状态**（结构没坏、可参与），不是**动作**（买卖）⇒ 拿它比他的操作，
    必然被「全答空」的多数类基线压死 ✗（集 A 76% / 集 B 71%）。
  · 但 `side` 内部**不是同质的**：E1b 的分档命中显示 `side` 日子里他仍在减仓（§4.3 原因 ②）。
  ⇒ 本实验换度量、不换链：**在我们的 `side` 日集合内**，用他的原话当 ground truth，
    检验「位置/事件」类线索能否把他的**减仓日**挑出来（precision / 基础率 / lift / 召回）。

问法：P(他真在减仓 | 我们=side ∧ 线索命中) 是否显著高于 P(他真在减仓 | 我们=side)？

──────────────── 样本（**冻结复用**，不放宽任何一道门）────────────────
直接复用 E1b 已固化的 golden set（`jobs/eval_wave_ladder_ops_degree.py` 的 LABELS/COND_LABELS，
三道门：指数/账户语境 ∧ 第一人称已发生动作 ∧ 非条件句；每条带日期+原话；hold 单列不进二分类）✓，
只保留**我们链当日 operation=side**（日线生产口径，默认窗口参数，一字未改）✓。
集 A = 严格 17 条；集 B = A + 3 条可验证条件句 = 19 条 —— 均 ≥15，**故不扩挖、不放宽** ✓。
另加**敏感性集 C**：同一候选文件里、过同一道门但 E1b 未纳入的 2 条（逐条写明原话与日期），
只作稳健性检验，不参与验收判定 ✓。

──────────────── 候选线索（每条都有出处+日期，且**可算**）────────────────
P1 站上 BOLL 中轨（close ≥ MA20）—— 2026-04-23「大盘BOLL中轨我减仓了」
P2 收破 BOLL 中轨（当日收破）—— 2026-02-13「对了 收破4113就减仓 今天中轨在4113」
P3 贴中轨（|close-MA20| ≤ σ20，P1/P2 的对称**可算**形式，σ 用 BOLL 自身单位）
P4 逼近关键压力位（close ≥ 0.98 × 前 20 日最高**不含当日**）—— 2026-03-19「等上面几个压力位…
   诱多进场…然后减出去」＋2026-03-20「等上面压力位减出去 保证周末有一定（仓位空间）」
E1 股指交割日 ±1 交易日（每月第三个周五）—— 2026-01-14「今天会减到55%-60%仓位 直到过本周股指交割日」
E2 交割周（含第三个周五那一周的交易日）—— 同上（他 01-14 就在减，属交割周内）
E3 长假前 1~2 个交易日（长假闸 = 相邻交易日相隔 ≥4 自然日；普通周末只有 3 日 ⇒ 不会误判）——
   2025-04-29「趁今天拉升 我降仓位到10%，剩1个稀土 1个免税**过节**」
E4 周五（周末风险）—— 2025-06-13「我需要减仓应对周末…风险」＋2026-03-20「保证周末有一定…」
E4b 周五·发帖日口径（**同一线索的口径变体**，因为 2025-10-19 / 2026-03-22 是**周末发帖**：
   周末帖的「周末风险」当周已过 ⇒ 交易日口径会把周末帖算成「周五」；两口径都报，不做挑选 ✗）
V1 放量未涨（成交额 ≥ 250 日 P80 分位 ∧ 当日涨幅 ≤ 0）—— 2026-03-22「4000破位导致一部分量化策略抛盘…
   （放量跌穿再到放量反弹）」＋同日「我会在这两天有机会的时候再减到50%内」
V2 放量未涨（均量口径：成交额 ≥ 1.2 × 20 日均额 ∧ 涨幅 ≤ 0）—— 同 V1（换阈值口径，报双份）
V0 （对照，**无出处** ✗）当日收跌 —— 用来量化「无信息线索」的 lift 量级（≙ 基础率）

参数取**原则性值**（不按标注集调 ✗）：BOLL 用默认 (20, 2) ⇒ 中轨 = MA20、单位 = σ20；
前 20 日最高**不含当日**（防自指，§记忆 8095129962c1 的坑）；成交额 250 日 P80 分位 ⇐ 卡上
「成交额分位异常」的分位口径；1.2× 均额 = 20% 放量（与本仓其它阈值同量级）；长假闸 ≥4 自然日。

──────────────── 度量（换掉命中率 ✗）────────────────
主指标 = **precision**（被线索标记的 side 日里，他真在减仓的比例）；对照组 = **基础率**
（全部 side 样本里他减仓的比例）；**lift = precision / 基础率**；另报**标记天数**与**召回率**。
显著性用**单侧 Fisher 精确检验**（超几何，小样本精确）。

验收闸（**先声明，后看数** —— 三条必须同时满足，否则判无效 ✗，哪怕 lift > 1）：
  ① 样本内标记 ≥ 6 天（支撑度；1~2 天的窗口不叫线索）；
  ② 单侧 Fisher p < 0.05（precision 显著高于基础率）；
  ③ 集 A / 集 B 方向一致（不靠单套样本）。
（说明：基础率 ~0.72 时，即使一条线索 100% 精确也要 ≥10 个标记日才可能 p<0.05 ⇒ ① 是必要下限 ✓。）

只做只读统计：**不改 operation 映射、不动主指数数据、不加影子字段**（无证据的字段就是包装 ✗，
与 §4.3 的处置一致）✓。

用法：.venv/bin/python jobs/eval_side_trim_windows.py
"""
import bisect
import collections
import datetime as dt
import json
import math
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, "jobs")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.chdir(ROOT)
import wave_ladder as W  # noqa: E402
import eval_wave_ladder_ops_degree as E1B  # noqa: E402  冻结样本的单一来源 ✓

SINCE = "20250101"          # 标注集覆盖的时期（统计参照用）
MIN_FLAGS, ALPHA = 6, 0.05  # 验收闸 ①②（先声明）

# ────────────── 敏感性集 C（同一候选文件、同一道门，E1b 未纳入的 2 条）──────────────
# 依据：data/_wave_ops_candidates.json（jobs/mine_wave_ops_golden.py 产出）；
# 过门复核：① 指数/账户语境（仓位）✓ ② 第一人称**已发生**动作 ✓ ③ 无「如果/一旦/只要」条件句 ✓。
SET_C: Dict[str, Tuple[str, str, str]] = {
    "2025-10-14": ("空", "原来我50%仓位，大部分是机器人…我认为机器人目前没什么行情，我相对应减仓我能减的，调去别的地方",
                   "「我相对应减仓我能减的」＝已减 + 总仓位语境（50%→）"),
    "2025-11-13": ("空", "我减了啊 到我技术位置我肯定减的 我原来20%仓位 接下来低位还要补回科技的",
                   "「我减了啊」＝已减 + 「我原来20%仓位」账户级"),
}


# ────────────── 交易日历 / 事件集合 ──────────────
def _d(d8: str) -> dt.date:
    return dt.date(int(d8[:4]), int(d8[4:6]), int(d8[6:8]))


def _third_friday(y: int, m: int) -> dt.date:
    fr = [dt.date(y, m, d) for d in range(1, 32) if _valid(y, m, d) and dt.date(y, m, d).weekday() == 4]
    return fr[2]


def _valid(y: int, m: int, d: int) -> bool:
    try:
        dt.date(y, m, d)
        return True
    except ValueError:
        return False


class Cal:
    """交易日历（直接用主指数日线的日期轴；**只读**，不写任何数据文件 ✓）。"""

    def __init__(self, rows: Sequence[Tuple[str, float, float, float]]):
        self.rows = rows
        self.d8 = [r[0] for r in rows]
        self.i = {d: k for k, d in enumerate(self.d8)}
        # 长假闸：相邻交易日相隔 ≥4 自然日（普通周末 = 3 日 ⇒ 不误判 ✓）
        self.pre_holiday = set()          # 长假前 1~2 个交易日
        for k in range(1, len(rows)):
            if (_d(self.d8[k]) - _d(self.d8[k - 1])).days >= 4:
                self.pre_holiday.update({k - 1, k - 2})
        self.deliv_day, self.deliv_week = set(), set()
        for y in range(2016, 2030):
            for m in range(1, 13):
                tf = _third_friday(y, m).strftime("%Y%m%d")
                if tf not in self.i:
                    continue
                k = self.i[tf]
                self.deliv_day.update({k - 1, k, k + 1})                    # 交割日 ±1 交易日 ✓
                for kk in range(k - 4, k + 2):                              # 交割周（±1 日外扩到整周）
                    if 0 <= kk < len(rows) and abs((_d(self.d8[kk]) - _d(tf)).days) <= 3:
                        self.deliv_week.add(kk)


def pct_rank(vals: Sequence[float], q: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def clue_of(cal: Cal, k: int, post_d8: Optional[str] = None) -> Dict[str, bool]:
    """第 k 个交易日的线索命中表（全部只用 ≤ 当日的数据 ✓ 无未来函数）。
    post_d8 = 他的**发帖日**（可能≠交易日：周末帖）⇒ 只用于 E4b 的「口径变体」。"""
    rows, d8 = cal.rows, cal.d8[k]
    close, high, amt = rows[k][1], rows[k][2], rows[k][3]
    ma20 = sum(r[1] for r in rows[k - 19:k + 1]) / 20.0
    sd20 = math.sqrt(sum((r[1] - ma20) ** 2 for r in rows[k - 19:k + 1]) / 20.0)
    amt20 = sum(r[3] for r in rows[k - 20:k]) / 20.0
    hi20x = max(r[2] for r in rows[k - 20:k])            # 不含当日 ✓
    amt250 = [r[3] for r in rows[max(0, k - 250):k]]     # 不含当日 ✓
    chg = close / rows[k - 1][1] - 1.0
    prev_above = rows[k - 1][1] >= (sum(r[1] for r in rows[k - 20:k]) / 20.0)
    return {
        "P1 站上中轨": close >= ma20,
        "P2 收破中轨": close < ma20 and prev_above,
        "P3 贴中轨(±1σ)": abs(close - ma20) <= sd20,
        "P4 逼近压力位": close >= 0.98 * hi20x,
        "E1 交割日±1": k in cal.deliv_day,
        "E2 交割周": k in cal.deliv_week,
        "E3 节前1~2日": k in cal.pre_holiday,
        "E4 周五": _d(d8).weekday() == 4,
        "E4b 周五(发帖日)": _d(post_d8 or d8).weekday() == 4,
        "V1 放量未涨(分位)": bool(amt250) and amt >= pct_rank(amt250, 0.80) and chg <= 0,
        "V2 放量未涨(1.2×)": amt >= 1.2 * amt20 and chg <= 0,
        "V0 当日收跌(对照)": chg < 0,
    }


def fisher_right(N: int, K: int, n: int, x: int) -> float:
    """单侧精确检验 P(X ≥ x)：N 个样本、K 个「空」、抽 n 个、其中 x 个「空」（超几何）。"""
    def c(a, b):
        return math.comb(a, b) if 0 <= b <= a else 0
    tot = c(N, n)
    if tot == 0:
        return 1.0
    return min(1.0, sum(c(K, i) * c(N - K, n - i) for i in range(x, min(n, K) + 1)) / tot)


# ────────────── 样本装配 ──────────────
def side_sample(ev: List[dict], labels: Dict[str, Tuple[str, str, str]]) -> Tuple[List[dict], List[dict]]:
    by = [e["date"] for e in ev]
    inside, outside = [], []
    for ds, (act, quote, why) in sorted(labels.items()):
        d8 = ds.replace("-", "")
        j = bisect.bisect_right(by, d8) - 1          # 他发帖日（可能是周末）→ 取 ≤ 当日的最后一个交易日
        e = ev[j] if j >= 0 else None
        row = {"date": ds, "his": act, "quote": quote, "why": why, "txn_date": e["date"] if e else None,
               "our_op": e["operation"] if e else "?", "our_name": e["name"] if e else "?"}
        (inside if row["our_op"] == "side" else outside).append(row)
    return inside, outside


def report(tag: str, sample: List[dict], cal: Cal, ev: List[dict]) -> dict:
    binary = [r for r in sample if r["his"] in ("多", "空")]
    holds = [r for r in sample if r["his"] == "hold"]
    n = len(binary)
    k_short = sum(1 for r in binary if r["his"] == "空")
    base = k_short / n if n else float("nan")
    for r in sample:
        cl = clue_of(cal, cal.i[r["txn_date"]], r["date"].replace("-", ""))
        r["clues"] = [kk for kk, v in cl.items() if v]
        r["fwd5"] = (cal.rows[cal.i[r["txn_date"]] + 5][1] / cal.rows[cal.i[r["txn_date"]]][1] - 1) * 100 \
            if cal.i[r["txn_date"]] + 5 < len(cal.rows) else None
        r["fwd10"] = (cal.rows[cal.i[r["txn_date"]] + 10][1] / cal.rows[cal.i[r["txn_date"]]][1] - 1) * 100 \
            if cal.i[r["txn_date"]] + 10 < len(cal.rows) else None

    names = list(clue_of(cal, cal.i[sample[0]["txn_date"]]))
    rows_out = []
    print("\n=== %s ｜ side 样本 %d（二分类 %d：多 %d / 空 %d）｜ hold %d 单列 ｜ 基础率 %.3f ===" % (
        tag, len(sample), n, n - k_short, k_short, len(holds), base))
    print("%-20s %6s %6s %8s %7s %7s %9s" % ("线索", "标记", "其中空", "precision", "lift", "召回", "Fisher p"))
    for kk in names:
        hit = [r for r in binary if kk in r["clues"]]
        tp = sum(1 for r in hit if r["his"] == "空")
        prec = tp / len(hit) if hit else float("nan")
        lift = prec / base if hit else float("nan")
        rec = tp / k_short if k_short else float("nan")
        p = fisher_right(n, k_short, len(hit), tp) if hit else 1.0
        rows_out.append({"clue": kk, "flags": len(hit), "tp": tp, "precision": prec, "lift": lift,
                         "recall": rec, "p_one_sided": p,
                         "pass": bool(len(hit) >= MIN_FLAGS and p < ALPHA)})
        print("%-20s %6d %6d %8.3f %7.3f %7.3f %9.3f %s" % (
            kk, len(hit), tp, prec, lift, rec, p, "✓过闸" if rows_out[-1]["pass"] else "✗"))
    print("（最小可分性：基础率 %.2f 时，100%% 精确的线索需 ≥ %d 个标记才可能 p<%.2f；"
          "实际最大标记数 %d ⇒ %s）" % (
              base, _min_for_significance(base), ALPHA,
              max([r["flags"] for r in rows_out] or [0]),
              "样本量本身不足以验收任何线索 ✗" if not any(r["pass"] for r in rows_out) else "有线索过闸 ✓"))
    return {"tag": tag, "n_binary": n, "n_long": n - k_short, "n_short": k_short, "base_rate": base,
            "holds": [r["date"] for r in holds], "clues": rows_out,
            "min_flags_for_sig": _min_for_significance(base)}


def _min_for_significance(base: float) -> int:
    k = 1
    while k < 200 and base ** k >= ALPHA:
        k += 1
    return k


def unions(sample: List[dict], base: float) -> List[dict]:
    """两两组合（**全部**报出来，不做挑选；多重比较会抬高假阳性，结论只当参考 ✓）。"""
    binary = [r for r in sample if r["his"] in ("多", "空")]
    n = len(binary)
    k_short = sum(1 for r in binary if r["his"] == "空")
    names = sorted({c for r in binary for c in r["clues"]})
    rows_out = []
    print("\n--- 两两组合（仅供参考：多重比较 ⇒ 假阳性风险 ↑；验收仍按单线索闸 ✓）---")
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            key = "%s ∨ %s" % (names[a].split()[0], names[b].split()[0])
            hit = [r for r in binary if names[a] in r["clues"] or names[b] in r["clues"]]
            tp = sum(1 for r in hit if r["his"] == "空")
            prec = tp / len(hit) if hit else float("nan")
            p = fisher_right(n, k_short, len(hit), tp) if hit else 1.0
            rows_out.append({"union": key, "flags": len(hit), "tp": tp, "precision": prec,
                             "lift": prec / base if hit else float("nan"), "recall": tp / k_short if k_short else 0.0,
                             "p_one_sided": p})
    rows_out.sort(key=lambda r: (-r["lift"], -r["flags"]))
    print("%-26s %6s %6s %8s %7s %7s %9s" % ("组合", "标记", "其中空", "precision", "lift", "召回", "Fisher p"))
    for r in rows_out[:8]:
        print("%-26s %6d %6d %8.3f %7.3f %7.3f %9.3f" % (
            r["union"], r["flags"], r["tp"], r["precision"], r["lift"], r["recall"], r["p_one_sided"]))
    return rows_out


def v_of(cal: Cal, k: int, kk: str) -> bool:
    return bool(clue_of(cal, k).get(kk))


def welch(a: List[float], b: List[float]) -> Tuple[float, float]:
    """Welch t 检验（双侧，正态近似）→ (t, p)。辅助诊断用，不作验收 ✗。"""
    if len(a) < 3 or len(b) < 3:
        return float("nan"), float("nan")
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    if se == 0:
        return float("nan"), float("nan")
    t = (ma - mb) / se
    return t, math.erfc(abs(t) / math.sqrt(2.0))


def population(ev: List[dict], cal: Cal) -> List[dict]:
    """参照：线索在**全部 side 日**上打了多少天（他的标注日之外的分布）＋前瞻收益辅助诊断。

    前瞻收益只作**辅助**（验收指标仍是 precision/lift ✓）：若「减仓窗口」真有风险含义，
    标记日的前瞻收益应更差；若反而更好，说明他是在**反抽里减仓**（风控/仓位管理性质）✓。
    """
    print("\n--- 参照：全部 side 日（%s 起，我们链口径）---" % SINCE)
    ks = [cal.i[e["date"]] for e in ev if e["operation"] == "side" and e["date"] >= SINCE]
    print("side 日 %d 天；线索标记天数 / 前瞻收益（Welch t 双侧 p，辅助诊断 ✗ 非验收）：" % len(ks))
    tally = collections.Counter()
    for k in ks:
        for kk, v in clue_of(cal, k).items():
            if kk.startswith("E4b"):
                continue          # 发帖日口径在全体 side 日上恒等于 E4（无帖日）⇒ 不重复报 ✓
            if v:
                tally[kk] += 1
    out = []
    for kk in sorted(tally):
        on = [k for k in ks if v_of(cal, k, kk)]
        off = [k for k in ks if k not in on]
        def fwd(sel, w):
            return [(cal.rows[k + w][1] / cal.rows[k][1] - 1) * 100 for k in sel if k + w < len(cal.rows)]
        a5, b5, a10, b10 = fwd(on, 5), fwd(off, 5), fwd(on, 10), fwd(off, 10)
        m = lambda v: sum(v) / len(v) if v else float("nan")
        t5, p5 = welch(a5, b5)
        t10, p10 = welch(a10, b10)
        print("   %-20s %4d 天（%2.0f%%）｜前瞻5日 标记 %+.2f%% vs 未标记 %+.2f%%（p=%.3f）"
              "｜前瞻10日 %+.2f%% vs %+.2f%%（p=%.3f）" % (
                  kk, tally[kk], 100.0 * tally[kk] / len(ks), m(a5), m(b5), p5, m(a10), m(b10), p10))
        out.append({"clue": kk, "flag_days": tally[kk],
                    "fwd5_on": m(a5), "fwd5_off": m(b5), "fwd5_p": p5,
                    "fwd10_on": m(a10), "fwd10_off": m(b10), "fwd10_p": p10})
    return out


def main() -> int:
    rows = W.load_rows()                       # 默认窗口（生产口径）✓ 只读
    cal = Cal(rows)
    ev = W.walk(rows)                          # 默认 env ✓
    print("主指数日线 %d 行（%s ~ %s）｜链事件 %d ｜ 窗口参数：全默认（生产口径，一字未改 ✓）" % (
        len(rows), rows[0][0], rows[-1][0], len(ev)))

    labs_a  = dict(E1B.LABELS)
    labs_b  = dict(E1B.LABELS, **E1B.COND_LABELS)
    labs_c  = dict(labs_b, **SET_C)
    out = {}
    for tag, labs in (("集 A（严格，无条件句）", labs_a), ("集 B（A + 可验证条件句）", labs_b),
                      ("集 C 敏感性（B + 2 条同门候选）", labs_c)):
        inside, outside = side_sample(ev, labs)
        out[tag] = report(tag, inside, cal, ev)
        out[tag]["rows"] = inside
        print("  样本内 side 样本明细：")
        for r in inside:
            print("    %s 他=%-4s 链=%-8s 命中=%-28s | %s" % (
                r["date"], r["his"], r["our_name"], "|".join(x.split()[0] for x in r["clues"]) or "-",
                r["quote"][:34]))
        if outside:
            print("  非 side 未纳入（%d 条，保持「只问 side 日」的口径 ✓）：%s" % (
                len(outside), "，".join("%s=%s" % (r["date"], r["our_op"]) for r in outside)))

    print("\n=== 逐条复核（集 B，防「挑着用」✗）===")
    for r in out["集 B（A + 可验证条件句）"]["rows"]:
        print("  %s 他=%-4s 链=%-8s 命中=%-24s 前瞻5日=%s | %s" % (
            r["date"], r["his"], r["our_name"], "|".join(x.split()[0] for x in r["clues"]) or "-",
            ("%+.2f%%" % r["fwd5"]) if r["fwd5"] is not None else "—", r["quote"][:30]))
    print("\n  他 hold（不进二分类，防污染基线 ✓）：%s" % out["集 B（A + 可验证条件句）"]["holds"])
    print("  不可比/未纳入 %d 条（E1b 已逐条写明原因 ✓）：%s" % (
        len(E1B.SKIPS), "，".join(sorted(E1B.SKIPS))))
    print("  本次新增敏感性 2 条（同门候选，逐条写明日期+原话 ✓）：%s" % "，".join(
        "%s %s" % (k, v[1][:18]) for k, v in sorted(SET_C.items())))

    u = unions(out["集 B（A + 可验证条件句）"]["rows"], out["集 B（A + 可验证条件句）"]["base_rate"])
    out["unions_B"] = u
    out["population"] = population(ev, cal)

    passed = [(tag, c) for tag, d in out.items() if isinstance(d, dict) and "clues" in d
              for c in d["clues"] if c["pass"] and tag != "集 C 敏感性（B + 2 条同门候选）"]
    print("\n=== 判定 ===")
    print("过闸线索（标记≥%d ∧ 单侧 p<%.2f ∧ A/B 方向一致）：%s" % (
        MIN_FLAGS, ALPHA, "、".join("%s/%s" % (t, c["clue"]) for t, c in passed) or "无 ✗"))
    out["passed"] = [{"set": t, "clue": c["clue"]} for t, c in passed]
    out["verdict"] = ("negative: 无任何线索达到可验收标准（详见 docs/wave-operation-table.md §4.5）"
                      if not passed else "有线索过闸，需按卡上分支写影子窗口")
    print("结论：%s" % out["verdict"])
    print("未做（按硬约束 ✓）：不改 operation 映射 / 不动 data 主指数 / 不加影子字段 / 不部署")

    p = os.path.join("data", "_side_trim_windows.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n→ %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
