# -*- coding: utf-8 -*-
"""p2_gate_daily_report.py — P2 Gate 每日观察报告
读 data/p2_gate_log.jsonl(当日/近N日), 聚合命中类别/软硬/标的 → data/p2_gate_daily_report.json + stdout
用法: python -u jobs/p2_gate_daily_report.py [--days N]
"""
import os, sys, json, glob
from datetime import datetime, timedelta, date

DATA = os.environ.get("DATA_DIR", "data")
def norm(d):
    return d[:10] if isinstance(d, str) and len(d) >= 10 else str(d)
def main():
    days = 1
    if '--days' in sys.argv:
        i = sys.argv.index('--days')
        try: days = max(1, int(sys.argv[i+1]))
        except Exception: pass
    cutoff = (date.today() - timedelta(days=days-1)).isoformat()
    path = os.path.join(DATA, 'p2_gate_log.jsonl')
    rows=[]
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                line=line.strip()
                if not line: continue
                try: r=json.loads(line)
                except Exception: continue
                at=norm(r.get('at',''))
                if at >= cutoff:
                    rows.append(r)
    cat={'wave_defense':0,'systemic':0,'margin_burst':0,'lhb_foreign_sell':0,'gjd_withdraw':0,'yield_spike':0,'other':0}
    hard=soft=0
    import collections
    sym=collections.Counter()
    reasons=[]
    for r in rows:
        if r.get('hard_block'): hard+=1
        else: soft+=1
        sym[r.get('symbol') or r.get('ts_code') or '?']+=1
        rs=[str(x) for x in (r.get('reasons') or [])]
        reasons+=rs
        if any('P2浪型' in x for x in rs): cat['wave_defense']+=1
        elif any('系统性风险' in x for x in rs): cat['systemic']+=1
        elif any('margin_burst' in x for x in rs): cat['margin_burst']+=1
        elif any('lhb_foreign_sell' in x for x in rs): cat['lhb_foreign_sell']+=1
        elif any('gjd_withdraw' in x for x in rs): cat['gjd_withdraw']+=1
        elif any('yield_spike' in x or '债市异动' in x for x in rs): cat['yield_spike']+=1
        else: cat['other']+=1
    out={'report_date':date.today().isoformat(),'window_days':days,'total_hits':len(rows),
         'hard_block':hard,'soft':soft,'by_reason':cat,
         'top_symbols':[{'symbol':s,'hits':n} for s,n in sym.most_common(10)],
         'sample_reasons':reasons[:10]}
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(os.path.join(DATA,'p2_gate_daily_report.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)
    print('P2 Gate 观察报告 %s | 近%d日 | hits=%d (hard=%d soft=%d)' % (out['report_date'],days,len(rows),hard,soft), flush=True)
    for k,v in cat.items():
        if v: print('  %-16s %d' % (k,v), flush=True)
    for s in out['top_symbols'][:6]:
        print('  top %s %d' % (s['symbol'], s['hits']), flush=True)
    print('WROTE', os.path.join(DATA,'p2_gate_daily_report.json'), flush=True)
if __name__=='__main__':
    main()
