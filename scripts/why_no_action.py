# -*- coding: utf-8 -*-
"""漏做面归因：条件成立后为什么没执行（§9.824，用户指派 ✓）

对象：两臂（t41 + c2610122052）真实触发里的两组
  · **做T低吸**：low_buy / wolf_zheng_t_buy（44 触发 / 0 executed ✗）
  · **高抛兑现**：high_sell 族（685 触发 / 7 executed ✗）
输出：每组按(status × 理由家族 ✓)拆开 + 抽样原文 ✓ ⇒ 看清"卡在哪一道" ✓
"""
import glob, json, re
from collections import Counter, defaultdict

GROUPS = {
    "做T低吸": ("low_buy", "wolf_zheng_t_buy"),
    "高抛兑现": ("high_sell", "wolf_dao_t_sell", "wolf_profit_take_sell", "wolf_fib_target_sell",
              "wolf_board_half_sell", "wolf_confirm_sell", "custom_vwap_sell", "wolf_passive_stop_sell"),
}

def fam(r):
    for k, name in (("无弹药", "无T仓弹药"), ("可为 0", "无T仓弹药"), ("裸空", "无T仓弹药"),
                    ("无底仓", "无底仓"), ("无券", "无底仓"), ("可卖 0", "无底仓"), ("as-of", "as-of持仓不足"),
                    ("做T时段窗", "做T时段窗"), ("COND-TIMING", "B/C时点闸"), ("floor", "底仓floor"),
                    ("收敛", "底仓floor"), ("加仓口径", "加仓口径"), ("浪型", "浪型闸"),
                    ("准入", "决策准入"), ("档位", "决策准入"), ("G8", "G8上影线"), ("涨停", "涨停/无法成交"),
                    ("同价", "同价来回/重复"), ("下去不补", "下去不补"), ("冷却", "跨日冷却"),
                    ("脱节", "AI判据:价差脱节"), ("追跌", "AI判据:恐慌追跌"), ("追高", "AI判据:追高"),
                    ("弹药", "无T仓弹药")):
        if k in r:
            return name
    return "其它"

rows = defaultdict(list)
for root in ("data/_bt_t41", "data/_bt_c2610122052"):
    for fp in glob.glob(root + "/_summary/prod_*.json"):
        try: d = json.load(open(fp, encoding="utf-8"))
        except Exception: continue
        for t in (d.get("triggers") or []):
            et = str(t.get("event_type"))
            for g, kinds in GROUPS.items():
                if et in kinds:
                    rows[g].append({"day": d.get("day"), "et": et, "st": str(t.get("status")),
                                    "r": str(t.get("reason") or "")})
for g, rs in rows.items():
    print("=== %s：%d 触发 ===" % (g, len(rs)))
    st = Counter(r["st"] for r in rs)
    print("  status:", dict(st))
    by = Counter((r["st"], fam(r["r"])) for r in rs)
    print("  %-14s %-22s %s" % ("status", "理由家族", "条数"))
    for (s, f), n in by.most_common(10):
        print("  %-14s %-22s %d" % (s, f, n))
    for r in rs:
        if r["st"] in ("blocked", "cancelled") and fam(r["r"]) == "其它":
            print("    [样本·其它] %s %s | %s" % (r["day"], r["et"], r["r"][:110]))
            break
    print()
