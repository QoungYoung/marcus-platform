# -*- coding: utf-8 -*-
import json
res=json.load(open("/app/data/position_class_result.json",encoding="utf-8"))
targets=["人工智能","AIGC概念","液冷概念","DeepSeek概念","算力概念","PCB","AI应用","英伟达概念"]
print("=== 主线AI/算力概念 @当前 ===")
for v in res.values():
    if v.get("name") in targets:
        fe=v.get("features",{}); f=v.get("fund_flow",{})
        print(f"{v['name']:10} {v.get('position'):5} {v.get('trend'):6} op={v.get('op'):7} 距高={fe.get('vs_1y_high_pct')}% 箱体={fe.get('box_pos_pct')}% MA60={fe.get('vs_ma60_pct')}% 净流入={f.get('strength')}亿 资金={f.get('dir')}")
# 过度触发核查: HIGH 但距1年高<-25% 的个数
hi_low=sum(1 for v in res.values() if v.get("position")=="HIGH" and (v.get("features",{}).get("vs_1y_high_pct") or 0)<-25)
hi_total=sum(1 for v in res.values() if v.get("position")=="HIGH")
print(f"\nHIGH total={hi_total}, 其中距1年高<-25%(跌深反弹)={hi_low}")
