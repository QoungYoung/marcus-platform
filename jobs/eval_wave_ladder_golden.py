# -*- coding: utf-8 -*-
"""golden set 人工标注 + 评分（2026-10-15 首版）。

标注规则（可复核）：
  · 只收**指数级**且**与我们的级别相同**的发言（他的 3-2/3-3/3-5 属于上一级 ⇒ 标 not_comparable ✗）；
  · 条件句（如果/一旦…）不算状态 ⇒ skip；
  · 他的说法 → 我们的 rung：4-1..4-4/调整浪 ⇒ 0；4-5 走完/成功 ⇒ 1；失败5/4-5 失败 ⇒ 2；
    「转下跌/下跌5浪级别/主跌」⇒ 3。
"""
import bisect, collections, json, os, sys
sys.path.insert(0, "apps/main_line")
import wave_ladder as W

# (日期, 关键词) → (标注 rung 或 None=不可比, 依据)
LABELS = {
    ("2026-07-03", "主升"):   (0, "「3-5 已经走出来」⇒ 3 浪结束、进入 4 浪调整（跨级别桥接，可比的相邻态）"),
    ("2026-07-23", "4-4"):    (0, "「现在是要做 4-4 而大概率有失败 4-5」⇒ 仍在 4 浪调整里"),
    ("2026-07-24", "5浪/五浪"): (3, "「大盘走了完整 ABC…现在判断是转成**下跌5浪**级别」⇒ 结构转下 ✗ 与我们分歧"),
    ("2026-07-27", "4-3"):    (0, "「指数级别 主升 4-3 转 4-4 的阶段」"),
    ("2026-07-27", "4-4"):    (0, "「真想做 4-4…只做指数级别 3 天站上 5 日线后面那段」"),
    ("2026-07-31", "4-3"):    (0, "「大盘都走到 4-3 的底了 也就是至少 ABC 杀完了」"),
    ("2026-08-03", "4-2"):    (0, "「也就是现 4-2 阶段」"),
    ("2026-08-03", "4-4"):    (0, "「那指数怎么走出 4-4？」"),
    ("2026-08-05", "4-4"):    (0, "「B 反是一次性的…指数的 4-4 都是一次性的」"),
    ("2026-08-05", "5浪/五浪"): (2, "「后面 4-5 对应就是高位科技的 C 杀 **可能 4-5 是失败5**」⇒ 失败5 预期"),
    ("2026-03-31", "C杀"):    (None, "「C 杀可能都不一定到那里 我说指数」——无状态断言 ⇒ skip"),
    ("2026-05-22", "C杀"):    (None, "「C 杀没结束」——说的是指数 C 段，级别与本链不同 ⇒ skip"),
    ("2026-05-21", "B反"):    (None, "「B 反高点/结束迹象」——B 反属 ABC 段，级别不同 ⇒ skip"),
    ("2026-04-14", "B反"):    (None, "板块 B 反 ⇒ skip"),
    ("2025-02-20", "2浪调整"):  (None, "他此时在数 2 浪/3 浪（上一级）⇒ 不可比"),
    ("2025-03-14", "3浪"):    (None, "「走完延申浪接大 3 浪」⇒ 上一级 ⇒ 不可比"),
    ("2025-06-25", "5浪/五浪"): (None, "「维持 5 浪末的判断」（3 浪内部的 5 末）⇒ 上一级 ⇒ 不可比"),
    ("2025-09-20", "主升"):    (None, "「大盘 3-2…机器人 3-3」⇒ 上一级 ⇒ 不可比"),
    ("2025-10-15", "主升"):    (None, "「现在是调整区间 不是主升区间」⇒ 无级别、无档位断言 ⇒ skip"),
    ("2025-10-19", "主升"):    (None, "周线图讲「反转大 1 浪和调整大 2 浪」⇒ 上一级 ⇒ 不可比"),
    ("2025-10-21", "主升"):    (None, "「大级别 2 浪调整…主升浪 3 浪里面的 3-2 调整」⇒ 上一级 ⇒ 不可比"),
    ("2026-01-05", "调整浪"):  (None, "「这段是个调整浪 只是 3-2 浪和 4 浪都可以」——他自己都不定 ⇒ skip"),
    ("2026-03-30", "主升"):    (None, "讲 ETF 卖飞 ⇒ 无档位断言 ⇒ skip"),
    ("2026-04-19", "调整浪"):  (None, "讲畏高心态 ⇒ 无档位断言 ⇒ skip"),
    ("2026-05-29", "双头"):    (None, "券商龙虎榜 ⇒ 无关 ⇒ skip"),
}

def main():
    cands = json.load(open("data/_corpus_wave_candidates.json", encoding="utf-8"))
    evs = W.walk(W.load_rows())
    by = [e["date"] for e in evs]
    RUNG = {0: "4浪调整", 1: "成功4-5", 2: "失败4-5", 3: "下跌1浪"}
    rows, unlab = [], 0
    for c in cands:
        key = (c["dt"][:10], c["token"])
        if key not in LABELS:
            unlab += 1
            continue
        rung, why = LABELS[key]
        d8 = c["d8"]
        i = bisect.bisect_right(by, d8) - 1
        our = evs[i]["rung_index"] if i >= 0 else None
        if rung is None:
            rows.append({"d8": d8, "his": None, "our": our, "why": why, "text": c["text"][:90]})
            continue
        rows.append({"d8": d8, "his": rung, "our": our, "why": why, "text": c["text"][:90]})
    # ★ 去重：同一 (日期, 正文前 40 字) 只算一条（首跑发现 08-05 同一条因 token 不同被记 3 次 ✗）
    seen, ded = set(), []
    for x in rows:
        k = (x["d8"], x["his"])        # ★ 按「日 × 他的档位」去重：同一天同一档只算一个数据点 ✓
        if k in seen:
            continue
        seen.add(k)
        ded.append(x)
    rows = ded
    cmp_rows = [x for x in rows if x["his"] is not None and x["our"] is not None]
    ok = sum(1 for x in cmp_rows if x["his"] == x["our"])
    near = sum(1 for x in cmp_rows if abs(x["his"] - x["our"]) <= 1)
    print("候选 %d 条 → 已标注 %d（可比 %d / 不可比 %d）｜ 未标注 %d" % (
        len(cands), len(rows), len(cmp_rows), len(rows) - len(cmp_rows), unlab))
    print("严格一致 %d/%d = %.0f%% ｜ 相邻一致 %d/%d = %.0f%%\n" % (
        ok, len(cmp_rows), 100 * ok / len(cmp_rows), near, len(cmp_rows), 100 * near / len(cmp_rows)))
    # 操作层对照（他口头叫什么 vs 他实际怎么做 vs 我们的 operation）
    OPS = {0: "t_only", 1: "side", 2: "defense", 3: "exit"}
    our_ops = []
    for x in cmp_rows:
        i = bisect.bisect_right(by, x["d8"]) - 1
        x["our_op"] = evs[i]["operation"] if i >= 0 else None
        x["his_op"] = OPS.get(x["his"])
        our_ops.append(x["our_op"])
    print("可比的逐条（结构 + 操作）：")
    for x in cmp_rows:
        flag = "✓" if x["his"] == x["our"] else ("~" if abs(x["his"] - x["our"]) <= 1 else "✗")
        print("  %s %s 他结构=%-8s 我们=%-8s ｜ 我们op=%-8s | %s" % (
            flag, x["d8"], RUNG[x["his"]], RUNG[x["our"]], x["our_op"], x["why"][:46]))
    print("  操作层：我们给出的 op = %s" % dict(collections.Counter(our_ops)))
    print("\n不可比/无断言 %d 条（例）：" % (len(rows) - len(cmp_rows)))
    for x in [y for y in rows if y["his"] is None][:5]:
        print("  %s 我们=%-8s | %s" % (x["d8"], RUNG.get(x["our"], "?"), x["why"][:56]))
    json.dump(rows, open("data/_corpus_wave_golden_labeled.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n落盘 data/_corpus_wave_golden_labeled.json")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
