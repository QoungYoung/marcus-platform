# -*- coding: utf-8 -*-
"""backtest_macro_wolf.py — macro_state v2 开关 vs Wolf 宏观表态 A/B
labels: Wolf 有明确宏观/机构表态的日期 → 期望开关(仅测能推导的项)
优先读取 data/macro_state_history.json（关键时点回填）→ source=history；
无回填日期时按实时源重算 → source=live。
输出: data/crowding_pit/macro_wolf_backtest.json
"""
import os, sys, json, time
DATA=os.environ.get('DATA_DIR','data')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_macro_state as bm
import wave_agent as wa
LABELS=[
 {'id':'M01','date':'2026-01-12','expect':{'margin_heat':True,'us_yield_spike':False,'cn30_spike':False},'wolf':'两融只看有没有消息降温,没降温你怕什么(30Y崩盘清单未触发)'},
 {'id':'M02','date':'2026-01-14','expect':{'margin_heat':True},'wolf':'高位融资进来的钱已是基石/热钱追热点'},
 {'id':'M03','date':'2026-01-16','expect':{'margin_heat':True},'wolf':'两融不仅没缩还快速增加,享受3-3'},
 {'id':'M04','date':'2026-01-20','expect':{'margin_heat':False,'margin_burst':True},'wolf':'融资盘爆仓状态,谨慎接盘等国家队'},
 {'id':'M05','date':'2026-07-23','expect':{'margin_burst':True},'wolf':'4-4资金面=杀杠杆阶段'},
 {'id':'M06','date':'2026-07-31','expect':{'gjd_support':True},'wolf':'整个7月上面净流入各类宽基4500E+护盘'},
 {'id':'M07','date':'2026-08-03','expect':{'gjd_support':True},'wolf':'GJD把指数稳在3800护盘'},
 {'id':'M08','date':'2026-03-02','expect':{'us_yield_spike':False,'cn30_spike':False,'margin_burst':False},'wolf':'美元上涨避险,风险可控(非A股崩盘日)'},
 {'id':'M09','date':'2026-07-28','expect':{'margin_burst':True,'gjd_support':True},'wolf':'政策性兜底;一个月3WE两融爆到2.7WE,杀杠杆阶段'},
 {'id':'M10','date':'2026-03-18','expect':{'north_in':False},'wolf':'今天砸盘的是外资,之前买红利的外资回新兴市场了'},
]

HIST_FILE=os.path.join(DATA,'macro_state_history.json')
def load_history():
    """data/macro_state_history.json（backfill_macro_state_history.py 生成）→ {date: 当日快照}"""
    try:
        j=json.load(open(HIST_FILE,encoding='utf-8'))
        return j.get('states',{}) or {}
    except Exception:
        return {}
HIST=load_history()

def run_one(label):
    d=label['date']
    if d in HIST:
        h=HIST[d]
        flags=set((h.get('macro_switches') or {}).get('flags') or [])
        out={'date':d,'yields':h.get('yields') or {},'market':h.get('market') or {},
             'macro_switches':h.get('macro_switches') or {},'source':'history'}
        return out, flags
    out={'date':d,'yields':{},'market':{},'source':'live'}
    try:
        cur,prev=bm.rate_snapshot(d)
        if cur:
            out['yields']['cn']={k.replace('中国国债收益率','').replace('-','_'):cur[k] for k in ['中国国债收益率2年','中国国债收益率5年','中国国债收益率10年','中国国债收益率30年','中国国债收益率10年-2年']}
            out['yields']['us']={k.replace('美国国债收益率','').replace('-','_'):cur[k] for k in ['美国国债收益率2年','美国国债收益率5年','美国国债收益率10年','美国国债收益率30年','美国国债收益率10年-2年']}
            if prev:
                out['yields']['chg']={'us10_1d':cur['美国国债收益率10年']-prev['美国国债收益率10年'],'us30_1d':cur['美国国债收益率30年']-prev['美国国债收益率30年'],'cn30_1d':cur['中国国债收益率30年']-prev['中国国债收益率30年']}
    except Exception as e: out['yields']['error']=str(e)[:100]
    try:
        ctx=wa.get_market_context(d)
        out['market']={k:ctx.get(k) for k in ['margin_rzrqye','margin_20d_chg','margin_net_buy','north_5d','gjd']}
        out['market']['lhb']=bm._lhb_snapshot(wa._ts_pro(), d)
    except Exception as e: out['market']['error']=str(e)[:100]
    try:
        bm._derive_switches(out)
        flags=(out.get('macro_switches') or {}).get('flags') or []
    except Exception as e: flags=[]; out['switch_err']=str(e)[:100]
    return out, set(flags)

def main():
    rows=[]
    for L in LABELS:
        t=time.time()
        out,flags=run_one(L)
        expect=L['expect']; hits=[]; miss=[]
        for k,exp in expect.items():
            actual=k in flags
            (hits if actual==exp else miss).append({'flag':k,'expect':exp,'actual':actual})
        rows.append({**L,'flags':sorted(flags),'hits':hits,'miss':miss,'sec':round(time.time()-t,1),
                     'source':out.get('source'),
                     'yields':out.get('yields',{}).get('cn',{}).get('30年'),'margin20d':out.get('market',{}).get('margin_20d_chg'),
                     'margin_net':out.get('market',{}).get('margin_net_buy'),
                     'gjd_h300':(out.get('market',{}).get('gjd') or {}).get('sh300_chg20')})
        print('==',L['id'],L['date'],'flags',sorted(flags),'miss',miss,'src',out.get('source'),flush=True)
        if out.get('source')=='live':
            time.sleep(1)
    os.makedirs(os.path.join(DATA,'crowding_pit'),exist_ok=True)
    json.dump(rows,open(os.path.join(DATA,'crowding_pit','macro_wolf_backtest.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    ok=sum(1 for r in rows if not r['miss']); print('AGG ok',ok,'/',len(rows),flush=True); print('DONE',flush=True)

if __name__=='__main__':
    main()
