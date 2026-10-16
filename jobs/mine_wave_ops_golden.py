# -*- coding: utf-8 -*-
"""E1b 步骤 A：从语料挖**他明确动手**的日子（golden set 候选），供人工标注买卖方向。

与 E1 的区别：E1 挖的是**他的命名**（4-3/筑底/震荡…）⇒ 与我们的方向输出错配 ✗；
本脚本挖的是**他的动作**（买/加/接 vs 减/止盈/不参与）⇒ 与 operation 同轴 ✓。

三道门（沿用 golden set 方法论）：
  1. **指数/账户语境**：命中词 ±60 字内必须有 大盘|指数|上证|市场|仓位|总仓（讲个股的不算 ✗）；
  2. **第一人称/指令式动作**：必须是他自己的动作（我买了 / 我减了 / 别追 / 不参与），转述别人不算 ✗；
  3. **非条件句**：含 如果|一旦|假如|要是|万一 的句子 skip（条件计划不是已发生的动作 ✗，同 E1 口径）。

输出 data/_wave_ops_candidates.json（date / dt / guess / quote），guess 只是机器猜测，**最终方向靠人工标注** ✓。
"""
import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
sys.path.insert(0, "jobs")
os.chdir(ROOT)   # 语料 xlsx 是相对路径（read_corpus）⇒ 固定在工作区根 ✓
from eval_wave_ladder_vs_corpus import read_corpus  # noqa: E402

MIN_YEAR = "2025"
IDX = re.compile(r"大盘|指数|上证|科创|双创|市场|仓位|总仓|半仓|满仓|空仓|几成")
SCENARIO = re.compile(r"如果|一旦|假如|要是|万一")
# 动作词表：**买方向**（做多/加）与**卖方向**（减/不参与）。次序＝优先级（同一句里先看哪个更明确）
BUY = re.compile(
    r"我(们)?[^。！？；]{0,10}(加仓|加了|买回|买入|买了|接了|接回|低吸|建仓|补仓|补了)"
    r"|(?:可以|要|应该|得)接(?![着下])|分批接|按4接5|按\s*4\s*接\s*5")
SELL = re.compile(
    r"我(们)?[^。！？；]{0,10}(减仓|减了|减掉|止盈|清了|清仓|卖了|降仓|走了)"
    r"|不参与|不追高?|不加仓|不新开|别追|不要追|防守|不做(?!多)|空仓")
# 他说「不动 / 躺着 / 锁仓」＝既不加也不减（持仓观望）⇒ 单列 hold，不计入多空方向 ✗
HOLD = re.compile(r"我(们)?[^。！？；]{0,8}(不动|不卖|不折腾|躺着|锁了|锁仓|保持仓位)")


def mine():
    rows = [r for r in read_corpus() if r[0] >= MIN_YEAR]
    out, funnel = [], collections.Counter()
    for d8, dt, txt in rows:
        funnel["语料行"] += 1
        if SCENARIO.search(txt):
            funnel["条件句 skip"] += 1
            continue
        hit = []
        for side, rx in (("多", BUY), ("空", SELL), ("hold", HOLD)):
            m = rx.search(txt)
            if not m:
                continue
            seg = txt[max(0, m.start() - 60):m.end() + 60]
            if not IDX.search(seg):
                funnel["无指数/账户语境 skip"] += 1
                continue
            hit.append((side, m.start(), re.sub(r"\s+", " ", seg)[:130]))
        if not hit:
            continue
        # 同一条帖子里多空都出现 ⇒ 取**最先出现**的那个（他的上下文顺序＝当时结论在前 ✓）
        side, _, quote = sorted(hit, key=lambda x: x[1])[0]
        out.append({"date": d8, "dt": dt, "guess": side, "quote": quote})
    # 去重：(日期, 方向, 原话前 40 字)
    seen, uniq = set(), []
    for o in sorted(out, key=lambda x: (x["date"], x["guess"])):
        k = (o["date"], o["guess"], o["quote"][:40])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(o)
    print("语料（>= %s）%d 行 ｜ funnel %s" % (MIN_YEAR, len(rows), dict(funnel)))
    print("候选 %d 条 ｜ 机器猜测分布 %s" % (len(uniq), dict(collections.Counter(o["guess"] for o in uniq))))
    print("\n候选清单（人工标注用；★ = 机器猜测）：")
    for o in uniq:
        print("  %s [%s] %s" % (o["dt"][:10], o["guess"], o["quote"][:104]))
    p = os.path.join("data", "_wave_ops_candidates.json")
    json.dump(uniq, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n→ %s（%d 条）" % (p, len(uniq)))
    return 0


if __name__ == "__main__":
    raise SystemExit(mine())
