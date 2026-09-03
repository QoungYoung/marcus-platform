# -*- coding: utf-8 -*-
"""audit_alignment_per_trade.py — 把 v2 一致率(89.7%, 35/39)拆成逐笔可审计依据

数据：data/stepwise_253_backtest_B.json(39行: 254/253/分步回补) + data/switch_stock_backtest.json(切换)
规则：
  row aligned = stepwise_B.within5 或 所属事件 switch_executed（切换通道计数，E10 为关键增量）
  channel_primary: 切换通道生效时标记 switch；否则取 stepB 动作里第一条 kind(253急杀/254低吸/254后3日分步回补)
输出: data/alignment_audit.json + stdout 汇总
"""
import os, json, collections
DATA=os.environ.get('DATA_DIR','data')
def load(p):
    try: return json.load(open(os.path.join(DATA,p),encoding='utf-8'))
    except Exception: return {}
WOLF_INTENT = {"E05": "add_base(液冷加深)", "E06": "refill(AI硬回补)", "E08": "low_buy_etf(设备低吸/ETF不清)",
              "E09": "switch(高切低封测)", "E10": "switch(海外链→国算)", "E11": "switch(设备→材料)",
              "E12": "switch_low(光→半导体低吸)", "E13": "add(国算加仓)"}

K2={'253':'253急杀','254':'254低吸','step_refill':'254后3日分步回补'}
def main():
    step=load('stepwise_253_backtest_B.json') or []
    sw=load('switch_stock_backtest.json') or {}
    sw_events={e['event']:e for e in (sw.get('events') or []) if e.get('switch_executed')}
    rows=[]
    for r in step:
        if r.get('error'): continue
        eid=r['event']; sym=r['symbol']
        sw_ok=eid in sw_events
        aligned=bool(r.get('within5')) or sw_ok
        ch=None; evidence=None
        acts=r.get('actions') or []
        if sw_ok and not r.get('within5'):
            ch='switch(链级卖旧+个股选买)'; evidence={'switch':sw_events[eid].get('buy_ok'),'date':sw_events[eid].get('date')}
        elif r.get('within5'):
            a=acts[0] if acts else None
            ch=K2.get(a.get('kind')) if a else 'within5'
            evidence={'action':a,'offset_note':'actions按窗口内排序'}
        rows.append({'event':eid,'wolf_intent':WOLF_INTENT.get(eid,''),'wolf_trade_date':r.get('wolf_trade_date') or r.get('wolf_date'),'symbol':sym,
                     'aligned':aligned,'within5_step':bool(r.get('within5')),
                     'switch_ok':sw_ok,'channel':ch,'evidence':evidence,
                     'wolf_T5':r.get('wolf_T5'),'sys_T5_mean':r.get('sys_T5_mean')})
    aligned_rows=[x for x in rows if x['aligned']]
    cnt=collections.Counter(x['channel'] for x in aligned_rows)
    per_event=collections.defaultdict(lambda:{'n':0,'aligned':0})
    for x in rows:
        pe=per_event[x['event']]; pe['n']+=1; pe['aligned']+=int(x['aligned'])
    out={'method':'alignment_audit v1','aligned_rows':len(aligned_rows),'total_rows':len(rows),
         'historical_three_way':True,'note':'历史回测三方：Wolf意图×系统回测通道(254/253/分步/切换)×是否±5日对齐',
         'channel_counts':dict(cnt),'per_event':{k:{'n':v['n'],'aligned':v['aligned']} for k,v in sorted(per_event.items())},
         'rows':rows}
    path=os.path.join(DATA,'alignment_audit.json')
    json.dump(out,open(path,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print('aligned',len(aligned_rows),'/',len(rows))
    print('channels',dict(cnt))
    print('per_event',out['per_event'])
    print('WROTE',path)
    return 0
if __name__=='__main__':
    raise SystemExit(main())
