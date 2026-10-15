# -*- coding: utf-8 -*-
"""等级模型·第一实验（纯测量）：**同一套链跑在周线上**，看它能不能把"3-2(主升内回调)"与"4浪(大级别调整)"分开。

对照样本（他亲口、且明确说是看**周线图**）：
  · 2025-10-19「这是大盘**周线级别**的图…双底后形成趋势级别反转 → 反转大1浪 → 调整大2浪 → 现在是主升级别大3浪」+「最强3-2」
  · 2026-07-31「整波为调整浪」「按 4-2→4-3 来看」
  · 2026-05-15/08-05「B反」「二卖点」
"""
import json, sys
sys.path.insert(0, "apps/main_line")
import wave_ladder as W

def load_weekly(code="000001.SH"):
    d = json.load(open("data/index_weekly_%s.json" % code.replace(".", "_"), encoding="utf-8"))
    return [(r[0], r[1], r[2], r[3]) for r in d["rows"]]

rows = load_weekly()
print("周线：%s → %s（%d 根）" % (rows[0][0], rows[-1][0], len(rows)))
evs = W.walk(rows)
by = {e["date"]: e for e in evs}
print("周线档序列长度 %d ｜ 状态切换次数 %d" % (len(evs), sum(1 for i in range(1, len(evs)) if evs[i]["rung_index"] != evs[i-1]["rung_index"])))

print("\n=== 关键日期上的**周线档**（他标注 vs 我们）===")
labels = {"20251017": "他：主升级别大3浪（内部=最强3-2）",
          "20260731": "他：整波为调整浪（4-2→4-3）",
          "20260515": "他：B反 / 二卖点",
          "20240930": "他：周线双底 → 趋势级别反转（大1浪起点区）",
          "20260323": "他：下跌1浪（失败配置）"}
for d in sorted(labels):
    e = by.get(d) or max([x for x in evs if x["date"] <= d], key=lambda x: x["date"], default=None)
    if not e:
        print("  %s → 无（早于周线起点）" % d); continue
    ws = W.to_wave_state(e)
    print("  %s ｜ 周线档 rung%d %-6s → level=%-4s sub=%-6s op=%-8s ｜ %s" % (
        d, e["rung_index"], e.get("name"), ws.get("level"), ws.get("sub_level"), ws.get("operation"), labels[d]))

print("\n=== 2025-06 ~ 2025-11 周线档（看他 10-19 那段之前之后怎么切）===")
for e in evs:
    if "20250601" <= e["date"] <= "20251130":
        f = e["features"]
        print("  %s rung%d %-8s op=%-8s ｜ 距60周高 %+.1f%% 距20周低 %+.1f%% 缩量新高=%s 长上影=%s" % (
            e["date"], e["rung_index"], e.get("name"), e["operation"],
            100 * (f["close"] / f["hi60_prev"] - 1), 100 * (f["close"] / f["lo20"] - 1),
            f.get("shrink_high"), f.get("upper_shadow")))
