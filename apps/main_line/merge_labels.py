# -*- coding: utf-8 -*-
"""双标注一致性对比 + 裁决合并 → data/wolf_labels_v2.json
用法:
  python apps/main_line/merge_labels.py                # 对比 A/B
  python apps/main_line/merge_labels.py resolve verdict.json   # 裁决后合并输出 v2
裁决文件 verdict.json: {"2026-08-04|半导体|high": "A"|"B"|"drop", ...}  (key=date|theme|judgment)
未出现在裁决中的分歧: 仅A保留→A, 仅B保留→B, 同键expect分歧→取A
"""
import json, os, sys

DATA = os.environ.get("DATA_DIR", "data")
def load(p):
    return json.load(open(os.path.join(DATA, p), encoding="utf-8"))
def key(l):
    return (l.get("date"), l.get("theme"), l.get("judgment"))
def key_s(l):
    return "|".join(key(l))

def compare():
    A = load("label_annotator_A.json"); B = load("label_annotator_B.json")
    ka = {key_s(l): l for l in A}; kb = {key_s(l): l for l in B}
    allk = sorted(set(ka) | set(kb))
    both = [k for k in allk if k in ka and k in kb]
    exp_diff = [k for k in both if (ka[k].get("expect") or False) != (kb[k].get("expect") or False)]
    agree = len(both) - len(exp_diff)
    print(f"标注员A: {len(A)} | 标注员B: {len(B)}")
    print(f"同键一致(judgment): {agree} | 同键expect分歧: {len(exp_diff)} | 仅A: {len(set(ka)-set(kb))} | 仅B: {len(set(kb)-set(ka))}")
    exact = sum(1 for k in allk if k in ka and k in kb and (ka[k].get("expect") or False) == (kb[k].get("expect") or False))
    if allk: print(f"精确一致率(judgment+expect): {exact}/{len(allk)} = {exact/len(allk)*100:.0f}%")
    print()
    print("=== 仅A ===")
    for k in sorted(set(ka)-set(kb)): print(f"  {k} | {ka[k].get('note','')[:60]}")
    print("=== 仅B ===")
    for k in sorted(set(kb)-set(ka)): print(f"  {k} | {kb[k].get('note','')[:60]}")
    print("=== expect 分歧(同键) ===")
    for k in sorted(exp_diff): print(f"  {k} A={ka[k].get('expect')} B={kb[k].get('expect')} | {ka[k].get('note','')[:45]}")

def resolve(verdict_file):
    A = load("label_annotator_A.json"); B = load("label_annotator_B.json")
    ka = {key_s(l): l for l in A}; kb = {key_s(l): l for l in B}
    vd = load(verdict_file) if os.path.exists(os.path.join(DATA, verdict_file)) else {}
    out = []
    for k in sorted(set(ka) | set(kb)):
        v = vd.get(k, "auto")
        if v == "drop": continue
        if k in ka and k in kb:
            ea = ka[k].get("expect") or False; eb = kb[k].get("expect") or False
            if v == "A": pick = ka[k]
            elif v == "B": pick = kb[k]
            elif ea != eb and v == "auto": pick = ka[k]   # 默认取A
            else: pick = ka[k]
        elif k in ka: pick = ka[k] if v != "drop" else None
        else: pick = kb[k] if v != "drop" else None
        if pick is None: continue
        out.append({"date": pick["date"], "theme": pick["theme"], "judgment": pick["judgment"],
                    "expect": pick.get("expect"), "note": pick.get("note", "")[:120]})
    # 按 sec 分组写 v2
    mainline = [l for l in out if l["judgment"] == "mainline"]
    position = [l for l in out if l["judgment"] != "mainline"]
    v2 = {"_meta": "wolf_labels_v2: 双子代理标注(标注员A/B) + 人工裁决合并; 2025-08-20~2026-08-31",
          "mainline": mainline, "position": position}
    json.dump(v2, open(os.path.join(DATA, "wolf_labels_v2.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"合并完成: mainline {len(mainline)} + position {len(position)} = {len(out)} 条 → data/wolf_labels_v2.json")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "resolve":
        resolve(sys.argv[2] if len(sys.argv) > 2 else "verdict.json")
    else:
        compare()
