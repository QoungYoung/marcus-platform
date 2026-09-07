# -*- coding: utf-8 -*-
"""sector_g3_judge.py — 板块洗盘收敛期(G3)盘前判定(2026-09-07 落地, 狼大式盘前定调)

全自动(无 ETF 硬编码)：持仓→stock_concept_map 概念→fusion 主题；板块代理=主题成员概念
(concept_hist 521 概念)合成——close 日收益近20日std < 0.8×近60日std 且 |net_amount| 5日均 ≤0.8×60日中位
=主力洗盘收敛期 → 该主题持仓当日不自动 T出(no_t_gate 读取)。

纯昨收数据→盘前(08:15 调度)秒级完成，写 data/sector_g3_state.json。
"""
import os, sys, json, time
os.environ.setdefault("DATA_DIR", "/app/data")   # fusion_mainline 用 DATA_DIR 定位 concept_hist
sys.path.insert(0, "/app/app")
sys.path.insert(0, "/app/apps/main_line")
DATA = os.environ.get("DATA_DIR", "/app/data")

def main():
    try:
        from sector_g3 import build_state
        st = build_state()
    except Exception as e:
        print("[sector_g3_judge] FAIL", str(e)[:200], file=sys.stderr)
        return 1
    st["_meta"] = {"updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "note": "板块级G3洗盘收敛(相对自身常态: 收益波动20/60<0.8 + |net|5/60<=0.8)"}
    p = os.path.join(DATA, "sector_g3_state.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    conv = [k for k, v in st.items() if isinstance(v, dict) and v.get("converged")]
    print("[sector_g3_judge] WROTE", p, "| 收敛板块:", conv, file=sys.stderr)
    print(json.dumps({k: v for k, v in st.items() if k != "_meta"}, ensure_ascii=False)[:900])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
