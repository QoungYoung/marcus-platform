# -*- coding: utf-8 -*-
import json
res=json.load(open("/app/data/position_class_result.json",encoding="utf-8"))
targets=["人工智能","AIGC概念","液冷概念","DeepSeek概念","算力概念","PCB","AI应用","英伟达概念"]
print("=== 主线AI/算力概念 ===")
for v in res.values():
    if v.get("name") in targets:
        fe=v.get("features",{}); f=v.get("fund_flow",{})
        print(v["name"], v.get("position"), v.get("trend"), "op="+str(v.get("op")), "距高="+str(fe.get("vs_1y_high_pct"))+"%", "箱体="+str(fe.get("box_pos_pct"))+"%")
