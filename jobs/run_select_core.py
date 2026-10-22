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
    # 风向标只作为任务级环境上下文；不挂到每只 pick，也不产生乘数
    bw = {"date": None, "source": None, "usage": "advisory_only", "states": {},
          "dead_subthemes": [], "note": "不参与选股/过滤/仓位；picks 不携带 bellwether 字段是设计"}
    for fp in (os.path.join(ROOT, "data/core_p1/ai_state_latest.json"),
               os.path.join(ROOT, "data/core_p1/bellwether_state_latest.json")):
        if os.path.exists(fp):
            try:
                x = json.load(open(fp, encoding="utf-8"))
                cnt = {}
                dead = []
                for it in x.get("items", []):
                    st = it.get("state") or it.get("ai_state") or "unknown"
                    cnt[st] = cnt.get(st, 0) + 1
                    if st == "dead": dead.append(it.get("subtheme"))
                bw = {"date": x.get("date"), "source": x.get("source") or os.path.basename(fp),
                      "usage": "advisory_only", "states": cnt, "dead_subthemes": dead,
                      "note": "不参与选股/过滤/仓位；picks 不携带 bellwether 字段是设计"}
                break
            except Exception:
                pass
    # ★ 2026-10-22：选股输出带上**入场时机层**的状态 ✓
    #   目的：用户一眼看出"为什么今天只 probe、不建仓"（市场级入场未触发 ✓）
    _et = None
    try:
        _et = json.load(open(os.path.join(os.path.dirname(OUT), "entry_timing.json"), encoding="utf-8"))
        _et = {"open": _et.get("open"), "last_signal": (_et.get("history") or [{}])[-1] if _et.get("history") else None}
    except Exception:
        _et = {"state": "无信号文件（今日未触发 ✓）"}
    out = {"date": date8, "source": "select_core+bellwether_advisory", "entry_timing": _et,
           "n_subthemes": len(items), "n_picks": len(flat), "bellwether": bw, "items": items}
    json.dump(out, open(os.path.join(OUT, "latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    import pandas as pd
    pd.DataFrame(flat).to_csv(os.path.join(OUT, f"select_core_{date8}.csv"), index=False, encoding="utf-8-sig")
    print(json.dumps({"date": date8, "subthemes": len(items), "picks": len(flat),
                      "bellwether": bw}, ensure_ascii=False))
    if flat:
        print(pd.DataFrame(flat)[["subtheme","code","name","stage"]].head(20).to_string(index=False))
    else:
        print("no picks (stage/role rules returned empty); this is not a task failure")


if __name__ == "__main__":
    main()
