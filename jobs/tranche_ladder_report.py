# -*- coding: utf-8 -*-
"""tranche_ladder_report.py — 建仓执行链盘前报告(2026-09-07)
1) switch_builder.build_plan() → data/switch_builder_plan.json(DRY) + 升级联动 sync_normal_upgrade
2) 输出"建仓执行链"报告块: 三档状态/额度/升级信号/switch 清单(供 QQ/复盘)
"""
import os, sys, json
os.environ.setdefault("SWITCH_AUTO_EXEC", "1")   # 生产对接(2026-09-07 用户确认): 8:18 switch 布腿执行; 显式 env=0 可回退 DRY
sys.path.insert(0, "/app")          # import app 需父级(否则 No module named 'app')
sys.path.insert(0, "/app/app")
sys.path.insert(0, "/app/apps/main_line")
DATA = os.environ.get("DATA_DIR", "/app/data")

def fmt_block():
    try:
        from switch_builder import build_plan
        plan = build_plan()
    except Exception as e:
        return "建仓执行链报告失败: " + str(e)[:120]
    from tranche_ladder import _load
    st = _load("tranche_state.json")
    lines = ["## 建仓执行链(t_only试仓档)", "- TOP3: " + "/".join(plan["top3"]),
             "- 升级信号: " + ("成立" if plan["escalate"]["ok"] else "未成立") + (
                 " (" + ",".join(plan["escalate"]["triggers"]) + ")"),
             "- sell_old(卖旧): " + ("、".join(x["symbol"] for x in plan["sell_old"]) or "无"),
             "- keep(强的留): " + ("、".join(x["symbol"] for x in plan["keep"]) or "无"),
             "- buy_new(新方向低吸): " + ("、".join(x["code"] for x in plan["buy_new"]) or "无"),
             "- 档位状态: " + json.dumps({k: v.get("tier") for k, v in plan["tiers"].items()}, ensure_ascii=False)]
    if st:
        lines.append("- 额度记录: " + json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "updated"} for k, v in st.items()}, ensure_ascii=False))
    return chr(10).join(lines)

def main():
    try:
        from tranche_ladder import sync_normal_upgrade
        sync_normal_upgrade()
    except Exception as e:
        print("[tranche] sync err:", str(e)[:100], file=sys.stderr)
    print(fmt_block())

if __name__ == "__main__":
    main()
