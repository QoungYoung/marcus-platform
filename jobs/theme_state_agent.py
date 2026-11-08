#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_state_agent.py — agent 判断题材阶段/龙头风向是否死，替代 B4 硬否决。"""
from __future__ import annotations
import argparse, json, os, re, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'jobs'),os.path.join(ROOT,'backend'),os.path.join(ROOT,'core')):
    if p not in sys.path: sys.path.insert(0,p)


def extract(text):
    t=(text or '').strip()
    if t.startswith('```'): t=re.sub(r'^```[a-zA-Z]*\s*','',t); t=re.sub(r'\s*```$','',t)
    try: return json.loads(t)
    except Exception: pass
    s=t.find('{'); e=t.rfind('}')
    if s>=0 and e>s:
        try: return json.loads(t[s:e+1])
        except Exception: pass
    return None


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default='20261102'); ap.add_argument('--timeout',type=float,default=180)
    a=ap.parse_args()
    from theme_sleeve_store import load, save
    data=load(a.date,'facts')
    if not data: print('no facts'); return 2
    ev=[]
    for t in data.get('themes') or []:
        ev.append({'theme_id':t.get('theme_id'),'name':t.get('name'),'tier':t.get('tier'),
                   'first_linkage':t.get('first_linkage'),'ret1':t.get('ret1'),'ret5':t.get('ret5'),'breadth':t.get('breadth'),'amount_ratio':t.get('amount_ratio'),
                   'b4':t.get('b4'),'overhang':t.get('overhang'),'internal_linkage':t.get('internal_linkage'),
                   'roles':t.get('roles'),'role_detail':t.get('role_detail')})
    prompt='''你是题材阶段判定器。只输出 JSON。狼大原话：高潮/炸板切外溢；龙头风向标死了就麻溜跑；大哥先涨、二哥不掉；第一波不挣钱第二波才挣钱。
给定结构化证据，判断每个题材：
- state：healthy / rebound / leader_dead / wind_dead / climax / spillover / unclear
- action：buy / hold / trim / avoid
- new_buy：true/false，是否允许新开仓（hold/trim 也可以 new_buy=true，只是仓位打折）
- size_multiplier：0-1，新开仓位系数
- confidence 0-1
- reasons：只能引用 b4/open_max/leader/wind/overhang/first_linkage/ret1/breadth
- counter_evidence
重要：
1) B4 open_max 只是炸板证据，不能单凭它判整个题材死；只有龙头/风向标死、或高潮外溢才 avoid/trim。
2) reasons 与 counter_evidence 必须一致：如果你在 counter_evidence 写了「高潮/炸板/二哥掉」，new_buy 就必须是 false。
3) b4.ladder 的 2 板为 0 且 z>0，说明没有晋级、炸板多，按「高潮/炸板切外溢」不得给 new_buy=true 追龙头。
4) 判断开板优先用 role_detail.leader.open_times_strict / b4.open_times_strict：这是“首次封板后连续>=2分钟低于涨停价”的真开板次数；原始 open_times 会把 1–3 分钱下探也算一次，不能直接说“8次炸板”。
5) counter_evidence 写了 first_linkage=false / 第一波偏弱 / 套牢重 / 广度窄，new_buy 必须为 false，不能一边列风险一边开仓。
6) first_time 在 13:00-14:00 是午后资金进量，不是「尾盘才封/主动性弱」；不要把 13:33 当尾盘。
7) sync.burst 没有狼大出处，不是否决依据；sync 只作描述，不能写「burst=0 所以弱」。
8) climax 只用于「高位/广泛高潮」：涨停家数多、炸板群、无晋级、板块整体亢奋。小题材只有 2-3 家涨停、龙头一封到底、风向标午后进量封板，不是 climax，应判 healthy 或 unclear。
9) 首日/首波不能判 climax：如果 first_linkage=true、最高只有 2 板、3 板以上=0、且没有连续多日加速，即使 breadth 高、涨停多，也只能判 healthy（首日强启动），不能判 climax，不能因此 need_switch=true。
10) climax 需要至少一个条件：3 板及以上高度、连续 2–3 日加速、炸板群扩散、龙头高位分歧。只有“首日广度打满 + 2 板”不构成 climax。
11) 判断切换不要用“候选 ret1/breadth 必须强过源”。狼大看的是：源是否放量滞涨（amount_ratio 高但 ret1 不再上行/涨停不增加/风向标未封），以及是否出现新题材首日（first_linkage、breadth 从低跳高、涨停从 0→有）。源高位 + 候选首日 = 外溢先手，允许 probe，不要求候选当天强过源。
12) 不要设固定数字阈值（amount_ratio>=1.5、limit_up>=3 之类）。用证据判断：量价是否背离、涨停是否扩张、龙头/风向标是否封住、候选是否首日。阈值化会拟合，不作为规则。
13) 源 healthy 不等于不能切：源 breadth/涨停已高位、ret5 大幅正、连续多日加速，同时出现新题材首日 → 可以切；只有源低位温和上涨且无新题材首日时才不切。
14) 源有大哥 + 大哥二哥梯队未断 + 细分主线还在 → 即使高位/炸板/涨停不增，也不能切；高位/炸板本身不是切换理由。只有大哥死、二哥梯队断、细分主线不在，才 need_switch=true。
15) rebound（反抽）必须单独判：前 1–5 日出现过 wind_dead/leader_dead；今天高开/冲高后走弱、涨停数不增、风向标未封或回落、无晋级；即使 breadth 高、龙头封死，也只能判 rebound，不能判 healthy。rebound → action=avoid/trim，new_buy=false，need_switch=true。
输入：%s
输出：{"themes":[{"theme_id":"","state":"","action":"","new_buy":true,"size_multiplier":1.0,"confidence":0.0,"reasons":[""],"counter_evidence":[""]}]}
''' % json.dumps(ev,ensure_ascii=False)
    import requests
    url=os.getenv('WOLF_V3_AGENT_URL') or os.getenv('PI_SERVER_URL') or 'http://dsh:3001/chat'
    try:
        r=requests.post(url,json={'message':prompt,'session_id':'theme_state_%s'%a.date},headers={'Content-Type':'application/json'},timeout=a.timeout,verify=False)
        r.raise_for_status(); raw=r.json().get('reply','')
    except Exception as e:
        print(json.dumps({'ok':False,'error':'agent_call_failed','detail':str(e)[:200]},ensure_ascii=False)); return 3
    obj=extract(raw)
    if not obj: print(json.dumps({'ok':False,'error':'bad_json','raw':raw[:400]},ensure_ascii=False)); return 4
    by={x.get('theme_id'):x for x in obj.get('themes') or []}
    for t in data.get('themes') or []:
        x=by.get(t.get('theme_id'))
        if not x: continue
        b4=t.get('b4') or {}
        # climax 量级校验：小题材/单一龙头封死不是广泛高潮
        try:
            u=int(b4.get('u') or 0); z=int(b4.get('z') or 0); om=int(b4.get('open_max') or 0)
        except Exception:
            u=z=om=0
        if x.get('state')=='climax' and u<=5 and z<=2 and om<=2:
            x['state']='unclear'
            x['new_buy']=False
            x['size_multiplier']=0
            x['reasons']=(x.get('reasons') or [])+['climax 量级不足：小题材/龙头封死，不按广泛高潮处理']
        if x.get('state') in ('climax','spillover','leader_dead','wind_dead','avoid'):
            # 外溢候选：其他未处于 climax/avoid 的题材龙头，而不是本题材内部非龙头
            spill=[]
            for o in data.get('themes') or []:
                if o.get('theme_id')==t.get('theme_id'): continue
                oa=by.get(o.get('theme_id')) or {}
                if oa.get('state') in ('climax','spillover','leader_dead','wind_dead','avoid'): continue
                osym=(o.get('roles') or {}).get('leader')
                if osym: spill.append({'theme_id':o.get('theme_id'),'name':o.get('name'),'symbol':osym})
            x['spillover']=spill
        t['agent_state']=x
    save(a.date,'facts',data)
    p=os.path.join(ROOT,'data','mainline',a.date,'theme_sleeve_facts.json')
    try: json.dump(data,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    except Exception: pass
    print(json.dumps({'ok':True,'themes':[{k:by.get(t.get('theme_id'),{}).get(k) for k in ('theme_id','state','action','confidence')} for t in data.get('themes') or []]},ensure_ascii=False,indent=1))
    return 0
if __name__=='__main__': raise SystemExit(main())
