# -*- coding: utf-8 -*-
"""eval_wave_ladder.py — 浪型假设链的**离线验收**：as-of 逐日回放 + 与他公开姿态对照（2026-10-15）。

规则（他的原话，帖 47288722 2026-10-15，lou 1221/1224）：
  · 「整波浪型从主升阶段4浪调整转为主跌1浪」＝档位要**同步下调**操作策略 / 预期管理 / 仓位管理；
  · 「可能性有 失败4-5、成功4-5、下跌1浪这三种，当每一种达不到的时候自动降级为下一种」。
实现：`apps/main_line/wave_ladder.py`（纯函数，**不用 pandas**，as-of 只用 ≤ 当日的数据 ✓）。

口径：全部是**我们的代理**（⛔），他没给量化（详见模块 docstring 的 ⛔ 标注）——
  · 成功4-5 = 收盘**严格**创 60 日新高；失败4-5 = 近 20 日摸到前 60 日最高的 ≥98% 后回落 ≥3%；
  · 下跌1浪 = 跌幅未收回的 ≥0.5% 跳空（且已破 MA60）∨ 收盘破前 60 日最低 ∨ 破关键位（环境变量）；
  · 单向锁 + 两条重定浪（60 日新高 / 自低点反抽 ≥8%）。

用法::

    python jobs/eval_wave_ladder.py                       # 2026 全年 + 对照表
    python jobs/eval_wave_ladder.py --start 20260601 --end 20261014
    python jobs/eval_wave_ladder.py --json               # 逐日明细（管道给别的脚本）

已知偏差（写清才敢用）：**2026-08-19 起锁在「下跌1浪」直到 10-14**，而他 9~10 月上旬公开说的是
「调整浪 / 反抽」⇒ 这一档我们**偏悲观约 2 个月**（原因：他的“重定浪型”是人工判断，日线收盘代理看不到）。
"""
import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

import wave_ladder as W  # noqa: E402

# 他公开说过话的锚点（原话见模块 docstring / 帖 47288722）
ANCHORS = [
    ("20260323", "「按纪律破位砍」（他在防）、03-25「减回50%内」"),
    ("20260409", "「今天白线在上 肯定没有买点」⇒ 减回 50-55%"),
    ("20260731", "「整波为调整浪」（救赎版开帖）+ 07-31~08-03 只做半导体 ETF 反抽"),
    ("20260805", "「看到上面守了3800 ⇒ 肯定按 4 接 5 做」⇒ 参与"),
    ("20260807", "「就四天 半导体吃了快20个点」⇒ 在吃主升段"),
    ("20261009", "「求帅比解答…」+「避海外链、只吃差价」⇒ 防"),
    ("20261012", "「这个钱我挣的是差价…半导体吃完后还是会向下」"),
    ("20261013", "「下跌行情最重要的是买到对的…赌自己最看好的那一头」"),
    ("20261014", "（10-15 09:56）「从主升阶段4浪调整转为主跌1浪；策略转为自救」"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260101")
    ap.add_argument("--end", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = W.load_rows()
    if not rows:
        print("无指数数据 ⇒ fail-open（不产出结论）")
        return 0
    evs = W.walk(rows, start=args.start, end=(args.end or None))
    if not evs:
        print("区间内无可用 bar（需要 ≥%d 根）" % W.MIN_BARS)
        return 0
    if args.json:
        print(json.dumps(evs, ensure_ascii=False))
        return 0

    print("数据：%s  %s -> %s（%d 根）" % (W.index_code(), rows[0][0], rows[-1][0], len(rows)))
    print("阈值：NEAR=%.3f GAP=%.3f RESET=%.3f 关键位=%.0f" % (
        W.NEAR_MIN, W.GAP_MIN, W.RESET_RALLY, W.floor_level()))
    print("\n=== 档位时间轴（只打变化点）===")
    prev = None
    for e in evs:
        if e["rung_index"] != prev:
            tag = "[重定浪]" if e["reset"] else ("[降级]" if e["degraded"] else "")
            print("%s -> [%d]%s op=%-8s invest=%.2f %s | %s" % (
                e["date"], e["rung_index"], e["name"], e["operation"], e["invest"], tag, e["reasons"]))
            if e["reset_reason"]:
                print("            ↳ %s" % e["reset_reason"])
        prev = e["rung_index"]

    print("\n=== 与他公开姿态对照 ===")
    by = {e["date"]: e for e in evs}
    for d, quote in ANCHORS:
        e = by.get(d)
        print("%s -> [%d]%s op=%s | %s\n      他：%s" % (
            d, e["rung_index"], e["name"], e["operation"], e["reasons"], quote) if e
            else "%s -> （区间外）| 他：%s" % (d, quote))

    print("\n=== 结构检查 ===")
    days = collections.Counter(e["rung_index"] for e in evs)
    print("各档天数：", {W.RUNGS[k]["name"]: v for k, v in sorted(days.items())})
    print("降级次数：%d | 重定浪次数：%d | 单向性：%s" % (
        sum(1 for e in evs if e["degraded"]), sum(1 for e in evs if e["reset"]),
        "✓ 无「无重定浪的升档」" if all(
            not (b["rung_index"] < a["rung_index"] and not b["reset"]) for a, b in zip(evs, evs[1:]))
        else "✗ 存在非法升档"))
    last = evs[-1]
    print("最新：%s [%d]%s op=%s invest=%.2f（%s）" % (
        last["date"], last["rung_index"], last["name"], last["operation"], last["invest"], last["reasons"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
