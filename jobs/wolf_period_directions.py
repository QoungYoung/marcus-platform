# -*- coding: utf-8 -*-
"""wolf_period_directions.py — 抽出他**各时期实际做的方向/板块**（做跨期"方向池"先验）。

为什么：复刻他的主线判定，关键问题不是"再调一个因子"，而是**他的方向池是不是跨期稳定的结构先验**
（他只做某几类方向）。若能证明稳定，就用**早期语料**定池、在 2026 上做**样本外**验收，避免用 2026 数据定池再在 2026 上测的自欺。

方法：把逐日整理稿按**季度**切片 → dsh 语义精读 → 输出该季度他**实际参与**的方向（带原话/日期）
与**明确回避**的方向；跨期汇总成"方向池"。
输出 `/app/data/wolf_period_directions.json`。

用法：python jobs/wolf_period_directions.py [--files a.md,b.md] [--force]
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_d12_evidence import call_dsh, _extract_json, DATA      # noqa: E402

OUT = os.path.join(DATA, "wolf_period_directions.json")

PROMPT = """你是交易语料判据抽取器。下面是同一位交易者（"狼大"）在 **{period}** 的逐日行为记录（`>` 引用为原话）。

**唯一任务**：列出他在这段时间**实际参与/操作过的方向、板块、题材**，以及**明确回避**的方向。

要求：
1. 每个方向给出：方向名（用他原话里的叫法，如"光""半导体""存储""石油""军工"）+ **出现次数**（你在语料里见到的频次，粗略计数即可）
   + **一句原话**（逐字，附日期）。
2. 区分**实际参与**（有买卖/持仓/做T）与**只是提到/讨论**（不参与）——只统计实际参与；讨论但不做的放进 `avoid`。
3. 按出现次数**从多到少**排序。
4. **找不到就不输出**，绝不编造。
5. 只输出 JSON：
{{"period":"{period}","directions":[{{"name":"方向名","count":整数,"quote":"原话","date":"YYYY-MM-DD"}}],
 "avoid":[{{"name":"方向名","quote":"原话","date":"YYYY-MM-DD"}}],
 "note":"一句话概括他这段时间的方向偏好（可空）"}}

===== 语料开始 =====
{body}
===== 语料结束 ======"""


def quarters_for(path):
    """两种版式都支持：
    A) 逐日版式（`### YYYY-MM-DD` 日块）→ 直接按月分季；
    B) 动作分类版式（表格行末尾带 `(YYYY-MM-DD)`）→ **按行内日期确定性重组**到季度（保留所属动作标题）。
    """
    txt = open(path, encoding="utf-8").read()
    parts = re.split(r"\n(?=#{2,3} \d{4}-\d{2}-\d{2})", txt)
    out = {}
    for p in parts:
        m = re.match(r"#{2,3} (\d{4})-(\d{2})-\d{2}", p)
        if not m:
            continue
        y, mo = m.group(1), int(m.group(2))
        out.setdefault("%sQ%d" % (y, (mo - 1) // 3 + 1), []).append(p)
    if out:
        return {k: "\n".join(v) for k, v in out.items()}
    # 版式 B
    buckets = {}
    section = ""
    pending = []
    for line in txt.splitlines():
        if line.startswith("### "):
            section = line
            pending.append(line)
            continue
        _tail = line.strip().rstrip("|").strip()      # 表格行尾有 " |"，要先剥掉才匹配得到日期
        m = re.search(r"[（(](\d{4})-(\d{2})-\d{2}[)）]$", _tail)   # 全角/半角括号都要认
        if line.startswith("|") and m:
            q = "%sQ%d" % (m.group(1), (int(m.group(2)) - 1) // 3 + 1)
            b = buckets.setdefault(q, {})
            b.setdefault(section, []).append(line)
        else:
            pending.append(line)
    merged = {}
    for q, secs in buckets.items():
        buf = ["# 期间 %s 的动作记录（按行内日期重组）" % q]
        for sec, rows in secs.items():
            buf.append("")
            buf.append(sec)
            buf.append("| 前提 | 执行 | 退出 | 原话（日期） |")
            buf.append("|---|---|---|---|")
            buf.extend(rows)
        merged[q] = "\n".join(buf)
    return merged


def main():
    files = [os.path.join(DATA, "corpus", "wolf-daily-log-xls2122.md"),
             os.path.join(DATA, "corpus", "wolf-daily-log-xls2025.md")]
    if "--files" in sys.argv:
        files = [f.strip() for f in sys.argv[sys.argv.index("--files") + 1].split(",")]
    force = "--force" in sys.argv
    # 只用 <= 该期间的语料定"方向池先验"，保证 2026 是**样本外**
    maxp = sys.argv[sys.argv.index("--max-period") + 1] if "--max-period" in sys.argv else "2025Q4"
    state = {"periods": {}, "failed": [], "max_period": maxp}
    if os.path.exists(OUT) and not force:
        try:
            state = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            pass
    jobs = []
    for f in files:
        if not os.path.exists(f):
            print("[pd] 缺文件 %s" % f, flush=True)
            continue
        for q, body in sorted(quarters_for(f).items()):
            if q > maxp:
                continue
            jobs.append((q, q, body))
    print("[pd] 切片 %d 个：%s" % (len(jobs), ", ".join(j[0] for j in jobs)), flush=True)
    for sid, period, body in jobs:
        if state["periods"].get(sid) and not force:
            print("[pd] 跳过 %s" % sid, flush=True)
            continue
        t0 = time.time()
        try:
            reply = call_dsh("wolfpd_" + sid, PROMPT.format(period=period, body=body))
            obj = _extract_json(reply)
            if not obj or "directions" not in obj:
                raise ValueError("无 directions 字段")
            state["periods"][sid] = obj
            print("[pd] %s → 参与 %d 个方向 / 回避 %d 个 (%.0fs)"
                  % (sid, len(obj.get("directions") or []), len(obj.get("avoid") or []),
                     time.time() - t0), flush=True)
        except Exception as e:
            state["failed"].append({"period": sid, "err": "%s: %s" % (type(e).__name__, str(e)[:100])})
            print("[pd] %s 失败 %s: %s" % (sid, type(e).__name__, str(e)[:80]), flush=True)
        json.dump(state, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(5.0)
    print("\n[pd] 完成 %d 期 → %s" % (len(state["periods"]), OUT), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
