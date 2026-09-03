# -*- coding: utf-8 -*-
import os, sys, json
sys.path.insert(0,'/app/apps/main_line')
import plan_compiler_v0 as pc
st=json.load(open('/app/data/main_line_state_2026-02-25.json',encoding='utf-8'))
cat=st.get('catalyst') or {}
print('2026-02-25 main_line=', st.get('main_line'), '| catalyst=', {k:round(v,2) if v is not None else None for k,v in cat.items()})
# 子主题 -> 父主题 catalyst
PARENT={
 '液冷(数据中心)':('液冷', 'AI/算力/科技'),
 '算力(国算)':('算力', 'AI/算力/科技'),
 '数据中心':('数据中心', 'AI/算力/科技'),
 'CPO(光模块)':('CPO', 'AI/算力/科技'),
 '人工智能':('人工智能', 'AI/算力/科技'),
 '半导体':('半导体', '半导体/芯片'),
 '国产芯片':('国产芯片', '半导体/芯片'),
 '存储芯片':('存储芯片', '半导体/芯片'),
 'AI芯片':('AI芯片', '半导体/芯片'),
 '化工原料':('化工原料', '资源/周期'),
 '黄金':('黄金', '资源/周期'),
 '军工':('军工', '军工/航天'),
 '券商':('券商', '金融'),
 '创新药':('创新药', '医药'),
}
CANDS=[('液冷(数据中心)','BK1138.DC',1.0),('算力(国算)','BK1134.DC',1.0),('数据中心','BK0922.DC',1.0),
 ('半导体','BK0917.DC',1.0),('CPO(光模块)','BK1128.DC',1.0),('国产芯片','BK0891.DC',1.0),
 ('存储芯片','BK1137.DC',1.0),('AI芯片','BK1127.DC',1.0),('人工智能','BK0800.DC',1.0),
 ('化工原料','BK0512.DC',0.5),('黄金','BK0547.DC',0.4),('军工','BK0490.DC',0.5),
 ('券商','BK0711.DC',0.5),('创新药','BK1106.DC',0.4)]
rows=[]
for name,code,ml in CANDS:
    s=pc.theme_pit_score(code,'2026-02-25',mainline=ml)
    if not s: continue
    kw,parent=PARENT[name]
    c=cat.get(parent)
    c=float(c) if isinstance(c,(int,float)) else 0.0
    rows.append((name,s['thesis_score'],c,s))
# 主按 thesis 降序, 同分再按 catalyst 降序(algo: base + 0.1*catalyst as final to reflect tiebreak)
rows.sort(key=lambda x:(x[1], x[2]), reverse=True)
print('\n=== 2026-02-25 结构优先 + 研报催化 tiebreaker ===')
for name,base,c,s in rows:
    final=round(base+0.1*c,3)
    print('%-14s base=%.2f cat=%.2f final=%.2f | rel=%.2f 主线=%.2f' % (name, base, c, final, s['rel'], s['mainline']))
