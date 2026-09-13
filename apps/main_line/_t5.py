# -*- coding: utf-8 -*-
import json
res=json.load(open("/app/data/position_class_result.json",encoding="utf-8"))
for nm in ["人工智能","液冷概念","英伟达概念","算力概念"]:
    for v in res.values():
        if v.get("name")==nm:
            print(nm, "pos=",v.get("position"), "action=",v.get("action"), "signals=",v.get("signals"))
            break
