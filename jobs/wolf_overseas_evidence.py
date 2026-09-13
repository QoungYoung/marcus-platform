# -*- coding: utf-8 -*-
"""wolf_overseas_evidence.py — 定向取证：他对「海外链 / 公募持仓重」的态度是**长期不做**还是**阶段性**？

背景问题（用户 2026-09-13）：「避开海外链里那几个大公募持仓个股」是一直不做，
还是这段时间因为太拥挤/地缘等因素所以不做？

方法：复用 wolf_d12_evidence 的切片/调用逻辑，只换 prompt 与输出文件；按需要挑月份切片。
输出 `/app/data/wolf_overseas_evidence.json`。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_d12_evidence import call_dsh, _extract_json, slices_for, DATA   # noqa: E402

OUT = os.path.join(DATA, "wolf_overseas_evidence.json")
FILES = [os.path.join(DATA, "corpus", "wolf-daily-log-xls2026.md"),
         os.path.join(DATA, "corpus", "wolf-daily-log-nga.md")]
ONLY = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]

PROMPT = """你是交易语料判据抽取器。下面是同一位交易者（"狼大"）在 **{period}** 的逐日行为记录（`>` 引用为原话）。

**唯一任务**：抽取他关于 **「海外链」**（光模块/光通信/CPO/北美算力产业链等海外映射方向）以及
**「公募/公墓持仓集中」** 的全部表态，回答一个具体问题：
**他是不做海外链（长期体系性回避），还是只在某段时间/某些条件下不做（阶段性）？**

请分层抽取：
- **K1 参与/看多**：他做海外链、看好海外链、或说海外链优点的原话（含"信仰资金多""不怕外围"等）。
- **K2 回避/退出**：他明确避开、减仓、不参与海外链的原话，**并把理由归类**：
   `利空/地缘`、`筹码-公募被迫卖`、`筹码-机构外资困住`、`位置高/反弹周期`、`没成交量出不来`、`其他`。
- **K3 条件与重返**：他的条件句（"如果…就…"）、重返/介入条件、时间限定（"这段时间""反弹周期内""等…再"）。

严格要求：
1. 每条含**日期**与**逐字原话**（不得改写/拼接/润色）。
2. **找不到就不输出**，绝不编造或用自己的话冒充他的话。
3. 区分"**长期性**表述"（如"我从来不做""不是我的体系"）与"**阶段性/条件性**"表述（如"这段时间""如果…""等…再"），在 `scope` 字段标明：`long_term` / `conditional` / `unclear`。
4. 只输出 JSON：{{"items":[{{"date":"YYYY-MM-DD","kind":"K1|K2|K3","scope":"long_term|conditional|unclear","reason_tag":"利空/地缘|筹码-公募|筹码-机构外资|位置高|无量出不来|其他|无","quote":"原话","note":"一句话"}}]}}

===== 语料开始 =====
{body}
===== 语料结束 ======"""


def main():
    state = {"items": [], "slices": {}, "failed": []}
    if os.path.exists(OUT):
        try:
            state = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            pass
    jobs = []
    for f in FILES:
        if not os.path.exists(f):
            print("[ov] 缺文件 %s" % f, flush=True)
            continue
        tag = os.path.basename(f).replace("wolf-daily-log-", "").replace(".md", "")
        for period, body in sorted(slices_for(f).items()):
            if period.split("-")[1] not in [o.split("-")[1] for o in ONLY] or period[:4] != "2026":
                continue
            sid = tag + "_" + period
            if sid not in [tag + "_" + o for o in ONLY if tag.startswith("xls")] and tag.startswith("xls"):
                continue
            jobs.append((sid, period, body))
    print("[ov] 切片 %d 个：%s" % (len(jobs), ", ".join(j[0] for j in jobs)), flush=True)
    for sid, period, body in jobs:
        if state["slices"].get(sid, {}).get("n_items") is not None and "--force" not in sys.argv:
            print("[ov] 跳过 %s (%s 条)" % (sid, state["slices"][sid]["n_items"]), flush=True)
            continue
        t0 = time.time()
        try:
            reply = call_dsh("wolfov_" + sid, PROMPT.format(period=period, body=body))
            obj = _extract_json(reply)
            items = (obj or {}).get("items") or []
            for it in items:
                it["slice"] = sid
            state["items"] = [x for x in state["items"] if x.get("slice") != sid] + items
            state["slices"][sid] = {"n_items": len(items), "secs": round(time.time() - t0, 1)}
            print("[ov] %s → %d 条 (%.0fs)" % (sid, len(items), time.time() - t0), flush=True)
        except Exception as e:
            state["failed"].append({"slice": sid, "err": "%s: %s" % (type(e).__name__, str(e)[:120])})
            print("[ov] %s 失败 %s" % (sid, type(e).__name__), flush=True)
        json.dump(state, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(5.0)
    print("\n[ov] 共 %d 条 → %s" % (len(state["items"]), OUT), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
