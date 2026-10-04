# -*- coding: utf-8 -*-
"""sync_carry_state.py —— 把**跨日结转状态**（8 个 carry JSON ✓）镜像进**臂库**（账本 §9.538 ✓）

**为什么** ✓（用户方案：「每个臂做一个 sqlite，数据落库…每个臂的数据都可以查」✓）：
  · 这些文件是**跨日状态**（会**影响决策** ✗），却散在文件里 ⇒ **不原子/难查/易被重置切碎** ✗
  · 已知受害者：`ambush_promoted.json`（路径读错 ⇒ 转正仓被当埋伏仓 ✗，§9.536 ✓）
**本脚本**：**只读**镜像 ✓（不写回、不改原逻辑 ⇒ **零风险** ✓），把它们收进 `arm_state` 表 ✓
用法：`.venv/bin/python jobs/sync_carry_state.py [--account drabt35] [--root data/_bt_t35]`
"""
from __future__ import annotations
import json, os, sys

CARRY = ["position_tiers.json", "roundtrip_state.json", "t_base_floor_rebase.json", "tranche_state.json",
         "wolf_253_chain.json", "wolf_hedge_refill.json", "wolf_passive_stop.json", "wolf_ticket_ban.json",
         "ambush_promoted.json", "nav.jsonl"]


def main() -> int:
    acc, root = "drabt35", os.path.join("data", "_bt_t35")
    for i, a in enumerate(sys.argv):
        if a == "--account" and i + 1 < len(sys.argv): acc = sys.argv[i + 1]
        if a == "--root" and i + 1 < len(sys.argv): root = sys.argv[i + 1]
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jobs"))
    import arm_db as adb
    c = adb.connect(acc, root)
    n = 0
    print("  ── 跨日状态镜像 ✓（只读 ✓）──")
    # carry 文件可能落在当天目录里（逐日结转）=> 先在臂根找，再在最新天目录找
    _days = sorted([d for d in os.listdir(root) if d.isdigit() and len(d) == 8]) if os.path.isdir(root) else []
    for fn in CARRY:
        p = os.path.join(root, fn)
        if not os.path.exists(p) and _days:
            p = os.path.join(root, _days[-1], fn)
        if not os.path.exists(p):
            print("    %-28s ⇒ 不存在 ✗" % fn); continue
        try:
            if fn.endswith(".jsonl"):
                rows = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
                adb.put_state(c, acc, fn, rows[-1] if rows else {})
            else:
                adb.put_state(c, acc, fn, json.load(open(p, encoding="utf-8")))
            n += 1
            print("    %-28s ⇒ **已入臂库 ✓**" % fn)
        except Exception as e:
            print("    %-28s ⇒ 读取失败: %s" % (fn, str(e)[:50]))
    print("  ⇒ 共 %d 项 ✓；查询：SELECT key, length(value) FROM arm_state;" % n)
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
