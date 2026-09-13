# -*- coding: utf-8 -*-
"""classify_concept_taxonomy.py — 每周增量分类新概念 → 开集 taxonomy
复用 build_concept_taxonomy.build(full=False): 只分类 position_class 里新增/未归类的活跃概念,
并为受影响主题重拆子方向; 清理不再活跃的旧概念(从 concept_theme 移除); 版本随日期更新。
用法: python -u apps/main_line/classify_concept_taxonomy.py
"""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_concept_taxonomy as bct
DATA = bct.DATA
TAX = bct.TAX

def _prune():
    tax = bct.load(TAX)
    if not isinstance(tax, dict): return
    cth = tax.get("concept_theme") or {}
    pos = bct.load("position_class_result.json")
    active = {str(v.get("name")) for v in pos.values() if isinstance(v, dict) and v.get("name")}
    active_norm = {c.replace(" ", "").replace("　", "") for c in active}
    stale = [k for k in cth if k not in active_norm and k not in set(bct._norm(c) for c in active)]
    for k in stale:
        cth.pop(k, None)
    # 重写原文件, 保留 themes(重建)
    tax["version"] = time.strftime("%Y-%m-%d")
    tax["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(tax, open(os.path.join(DATA, TAX), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("classify_concept_taxonomy: pruned %d stale concepts" % len(stale))

def main():
    _prune()
    bct.build(full=False)
    print("classify_concept_taxonomy done", flush=True)

if __name__ == "__main__":
    main()
