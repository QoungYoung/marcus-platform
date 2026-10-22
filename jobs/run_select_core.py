# -*- coding: utf-8 -*-
"""在生产跑 select_core：45 子主题 + 风向标软 overlay。"""
from __future__ import annotations
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "backend/app/services"),
           os.path.join(ROOT, "app/services"), ROOT):
    if os.path.isdir(_p): sys.path.insert(0, _p)
from wolf_card.selection_rules import select_core
from wolf_card.subthemes import parent as sub_parent

OUT = os.path.join(ROOT, "data/core_select")
os.makedirs(OUT, exist_ok=True)


def main():
    reg = json.load(open(os.path.join(ROOT, "data/core_p05/confirmed_daily.json"), encoding="utf-8")) \
        if os.path.exists(os.path.join(ROOT, "data/core_p05/confirmed_daily.json")) \
        else json.load(open(os.path.join(ROOT, "config/core_p05_confirmed.json"), encoding="utf-8"))
    date8 = str(reg.get("date") or "20261020")
    items, flat = [], []
    for it in reg.get("items", []):
        sub = it.get("subtheme")
        try:
            r = select_core(sub_parent(sub), date8, top=5, subtheme=sub)
        except Exception as e:
            r = {"theme": sub_parent(sub), "subtheme": sub, "picks": [], "error": str(e)}
        items.append(r)
        for p in r.get("picks") or []:
            flat.append({"date": date8, "theme": r.get("theme"), "subtheme": sub,
                         "code": p.get("code"), "name": p.get("name"),
                         "stage": (r.get("stage") or {}).get("stage"),
                         "rule": p.get("rule"),
                         })
    out = {"date": date8, "source": "select_core+bellwether_soft_overlay",
           "n_subthemes": len(items), "n_picks": len(flat), "items": items}
    json.dump(out, open(os.path.join(OUT, "latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    import pandas as pd
    pd.DataFrame(flat).to_csv(os.path.join(OUT, f"select_core_{date8}.csv"), index=False, encoding="utf-8-sig")
    print(json.dumps({"date": date8, "subthemes": len(items), "picks": len(flat)}, ensure_ascii=False))
    if flat:
        print(pd.DataFrame(flat)[["subtheme","code","name","stage"]].head(20).to_string(index=False))
    else:
        print("no picks (stage/role rules returned empty); this is not a task failure")


if __name__ == "__main__":
    main()
