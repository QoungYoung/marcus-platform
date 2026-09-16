# -*- coding: utf-8 -*-
"""bt_prod_progress.py — 生产链年跑的进度/成绩速览。

读 `data/_bt_year/_summary/prod_<day>.json`（`jobs/bt_prod_run.py` 每日产出的结果），
汇总：跑了几天 / 布腿 / 触发 / 成交 / 持仓 / 决策对象是否允许买入 / 被拦原因 TOP。

用法：python jobs/bt_prod_progress.py [--root data/_bt_year] [--tail 12]
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

sys.path[:0] = []
import bt_env  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(bt_env.DATA, "_bt_year"))
    ap.add_argument("--tail", type=int, default=12)
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.root, "_summary", "prod_*.json")))
    if not files:
        print("还没有跑完任何一天（找 %s/_summary/prod_*.json）" % a.root)
        return 1
    days, legs, trig, fills = [], 0, 0, 0
    status = collections.Counter()
    reasons = collections.Counter()
    blocked = 0
    pos_last, last = 0, None
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        days.append(d["day"])
        legs += len(d.get("armed") or [])
        trig += len(d.get("triggers") or [])
        fills += len(d.get("trades") or [])
        pos_last = len(d.get("positions") or [])
        last = d
        for t in d.get("triggers") or []:
            status[t.get("status")] += 1
            if t.get("status") == "blocked":
                blocked += 1
                reasons[(t.get("reason") or "")[:60]] += 1
    print("已完成 %d 天：%s → %s" % (len(days), days[0], days[-1]))
    print("布腿 %d 条 / 触发 %d 次 / 成交 %d 笔 / 期末持仓 %d 只" % (legs, trig, fills, pos_last))
    print("触发状态：%s" % dict(status))
    if blocked:
        print("被拦 TOP5：")
        for r, n in reasons.most_common(5):
            print("  %4d  %s" % (n, r))
    if last:
        dec = last.get("decision") or {}
        print("最后一天决策对象：cut=%s 允许买入=%s 缺失层=%s" % (dec.get("cut"), dec.get("allowed"), dec.get("missing")))
        acct = (last.get("account") or [{}])[0]
        if acct:
            print("账户：可用资金 %s / 初始 %s" % (acct.get("available_cash"), acct.get("initial_capital")))
    print("最近 %d 天：" % min(a.tail, len(days)))
    for f in files[-a.tail:]:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        dec = d.get("decision") or {}
        print("  %s 腿%3d 触发%3d 成交%2d 持仓%2d 允许买入=%s" % (
            d["day"], len(d.get("armed") or []), len(d.get("triggers") or []),
            len(d.get("trades") or []), len(d.get("positions") or []), dec.get("allowed")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
