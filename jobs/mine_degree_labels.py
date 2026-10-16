# -*- coding: utf-8 -*-
"""E1 步骤 B：从语料挖**带日期的级别标注**（他自己的话），并归成 up/adjust/down 三类。"""
import collections, json, re, sys
sys.path.insert(0, "jobs")
from eval_wave_ladder_vs_corpus import read_corpus

IDX = re.compile(r"大盘|上证|指数|周线|级别")
# 级别关键词 → 极性类（只取**语义清晰**的；4-5/4-1 这类模棱两可的排除 ✗）
KW = [
    (r"3-3", "up"), (r"主升级别|主升段|最强主升", "up"), (r"趋势级别反转|趋势级别起步", "up"),
    (r"反转大1浪|大1浪", "up"), (r"主升级别大3浪|大3浪", "up"), (r"大5浪", "up"),
    (r"3-2", "adjust"), (r"4-2", "adjust"), (r"4-3", "adjust"), (r"4-4", "adjust"),
    (r"大2浪", "adjust"), (r"大4浪", "adjust"), (r"调整浪|修复浪|震荡", "adjust"),
    (r"下跌1浪|主跌1浪", "down"), (r"失败5|失败五", "down"), (r"末段|衰竭", "down"), (r"C杀|C浪", "down"),
]
rows = [r for r in read_corpus() if r[0] >= "2016"]
out = []
for d8, dt, txt in rows:
    # ★ 不再 break（首命中即停会把"大级别"标注吞掉 ✗ —— 一条帖子里同时说「大4浪的 4-3 筑底」是常态 ✓）：
    #   一条帖子里每个关键词各记一条 ✓，再由分析端按度分组 ✓
    for pat, pol in KW:
        m = re.search(pat, txt)
        if not m:
            continue
        seg = txt[max(0, m.start() - 60):m.end() + 60]
        if not IDX.search(seg):
            continue
        out.append({"date": d8, "dt": dt, "pol": pol, "kw": m.group(0),
                    "text": re.sub(r"\s+", " ", seg)[:110]})
# 按 (日期, 极性) 去重
# ★ 去重键必须是 (日期, 极性, 关键词) —— 用 (日期, 极性) 会把同一帖里的「大4浪」和「4-3」合并掉 ✗
#   （两者极性都是 adjust ⇒ 大级别标注被吞，实测大级别样本只剩 3 条 ✗）
seen, uniq = set(), []
for o in sorted(out, key=lambda x: x["date"]):
    k = (o["date"], o["pol"], o["kw"])
    if k in seen:
        continue
    seen.add(k)
    uniq.append(o)
print("带日期的级别标注：%d 条（去重后）" % len(uniq))
print("极性分布：", dict(collections.Counter(o["pol"] for o in uniq)))
print("年份分布：", dict(collections.Counter(o["date"][:4] for o in uniq)))
print("\n样例（近 8 条）：")
for o in uniq[-8:]:
    print("  %s [%s] %s ｜ %s" % (o["date"], o["pol"], o["kw"], o["text"][:80]))
json.dump(uniq, open("data/_degree_labels.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n→ data/_degree_labels.json（供吻合率计算 ✓）")
