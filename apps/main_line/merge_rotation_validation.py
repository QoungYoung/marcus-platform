# -*- coding: utf-8 -*-
"""merge_rotation_validation.py — 汇总 wave/position 回放 → docs/p2-rotation-validation-report.md
用法: python apps/main_line/merge_rotation_validation.py [wave_json] [position_json] [out_md]
"""
import os, sys, json
DATA=os.environ.get("DATA_DIR","data")
def load(p):
    try: return json.load(open(p,encoding="utf-8"))
    except Exception: return {}
def family_ok(expect, op):
    if expect in ("na",): return "na"
    if not op: return "no-data"
    if expect=="build": return "OK" if op=="build" else ("PARTIAL" if op in ("t_only","side") else "MISMATCH")
    if expect=="tside": return "OK" if op in ("t_only","side") else ("PARTIAL" if op in ("build",) else "MISMATCH")
    return "na"
def gate_text(op):
    return {"build":"主升/主浪→只做主线内细分轮动, 禁切出主线(板块级高低切)","t_only":"只做T/4-4→允许防御性高切低(候选LOW+无2孕线+未放量破前低)","side":"观望/调仓换股→同t_only(可防御切低, 降低随意调仓)","defense":"防御→禁止轮动, 防守为主","exit":"兑现→禁止轮动/离场"}.get(op, "unknown")

def main(wave_p, pos_p, out_md):
    wave=load(wave_p); pos=load(pos_p)
    if not wave and not pos:
        print("no data"); return
    md=["# P2 轮动 Case 验证报告（wave_agent 历史重放 + position_class 时点回放）","",
        "> 生成：2026-09-02（晚）。依据 docs/p2-rotation-cases.md 22+ 例；wave=wave_agent(date) 两级判定；position=concept_hist 按日期切片回放。",""]
    md.append("| ID | 日期 | 组 | 期望wave | 实际level/sub/op | 判定 | 相关概念position回放 |")
    md.append("|---|---|---|---|---|---|---|")
    for cid in wave:
        w=wave.get(cid) or {}; p=pos.get(cid) or {}
        op=w.get("operation"); lv=w.get("level"); sub=w.get("sub_level") or ""
        ex=w.get("expect_wave")
        fd=family_ok(ex, op)
        pos_txt=""
        for tg in (p.get("targets") or [])[:3]:
            hs=tg.get("hits") or []
            if not hs: continue
            bits=[]
            for h in hs[:2]:
                bits.append("%s=%s(%s,%s)" % (h.get("name",""), h.get("position"), h.get("fund",{}).get("dir"), (h.get("structure") or "")[:6]))
            pos_txt+="; ".join(bits)+" || "
        pos_txt=pos_txt.strip(" |")
        md.append("| %s | %s | %s | %s | %s/%s/%s | %s | %s |" % (cid, w.get("date"), cid[:1], ex, lv or "-", sub or "-", op or (w.get("err") or "-"), fd, pos_txt[:240]))
    md.append("")
    md.append("## gate 语义（按实际 wave op）")
    md.append("")
    ops=sorted({w.get("operation") for w in wave.values() if w.get("operation")})
    for o in ops: md.append("- %s → %s" % (o, gate_text(o)))
    md.append("")
    md.append("## 结论要点（供人工裁决）")
    md.append("- 自动判定只做 wave op 与文档预期(op family)对照；position 回放列供 C1/B4/A6 等目标验证。")
    md.append("- OK=符合预期 op family；PARTIAL=可接受但更保守/更进取；MISMATCH=与语料预期相悖，需复核 case 语境或 wave_agent 判定。")
    open(out_md,"w",encoding="utf-8").write("\n".join(md))
    print("WROTE", out_md, len(md), "lines")
if __name__=="__main__":
    wp=sys.argv[1] if len(sys.argv)>1 else os.path.join(DATA,"rotation_wave_replay.json")
    pp=sys.argv[2] if len(sys.argv)>2 else os.path.join(DATA,"rotation_position_replay.json")
    om=sys.argv[3] if len(sys.argv)>3 else "docs/p2-rotation-validation-report.md"
    main(wp, pp, om)
