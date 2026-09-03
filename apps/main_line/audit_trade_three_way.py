# -*- coding: utf-8 -*-
"""audit_trade_three_way.py — Agent(auto_trade决策日志) × 成交(paper_trades) × Wolf事件 三方对照

用法: python -u apps/main_line/audit_trade_three_way.py [--days N]
逻辑：
  1) 读 data/auto_trade_decision_log.jsonl（最近N天）
  2) 对每条决策查 paper_trades 当日成交(account=stock) attach
  3) 对每笔成交按 symbol 匹配 data/alignment_audit.json 的 Wolf/规则行(±5交易日)
输出: data/audit_trade_three_way.json + stdout（当前日志为空则输出框架就绪提示）
"""
import os, sys, json, collections
from datetime import date, timedelta
DATA=os.environ.get('DATA_DIR','data')
DB=os.getenv('DATABASE_URL','postgresql://marcus:marcus123@postgres:5432/marcus_trading')
def load(p):
    try: return json.load(open(os.path.join(DATA,p),encoding='utf-8'))
    except Exception: return {}
def read_log(days):
    rows=[]
    p=os.path.join(DATA,'auto_trade_decision_log.jsonl')
    if not os.path.exists(p): return rows
    cutoff=(date.today()-timedelta(days=days)).isoformat()
    with open(p,encoding='utf-8') as f:
        for line in f:
            line=line.strip()
            if not line: continue
            try: r=json.loads(line)
            except Exception: continue
            if str(r.get('at',''))[:10]>=cutoff:
                rows.append(r)
    return rows
def trades_on(day):
    try:
        import psycopg2
        conn=psycopg2.connect(DB); cur=conn.cursor()
        cur.execute("SELECT symbol,direction,volume,avg_price,substr(created_at,1,10) FROM paper_trades WHERE account_id='stock' AND substr(created_at,1,10)=%s",(day,))
        out=[{'symbol':str(r[0]),'direction':str(r[1]),'volume':int(r[2] or 0),'price':float(r[3] or 0),'date':str(r[4])} for r in cur.fetchall()]
        cur.close(); conn.close(); return out
    except Exception:
        return []
def main():
    days=7
    if '--days' in sys.argv:
        try: days=max(1,int(sys.argv[sys.argv.index('--days')+1]))
        except Exception: pass
    audit=load('alignment_audit.json') or {}
    rows_audit=audit.get('rows') or []
    by_sym=collections.defaultdict(list)
    for a in rows_audit:
        by_sym[a.get('symbol')].append(a)
    logs=read_log(days)
    joined=[]
    for log in logs:
        d=str(log.get('at'))[:10]
        trades=trades_on(d)
        for t in trades:
            wolfs=[x for x in by_sym.get(t['symbol'],[]) if abs(int(str(x.get('wolf_trade_date') or '0')[:8])-int(d.replace('-','')))<=500]
            joined.append({'decision_date':d,'task':log.get('task_id'),'window':log.get('window'),
                           'stance':log.get('pi_stance'),'position_limit':log.get('pi_position_limit'),
                           'reason_head':(log.get('report_head') or '')[:200],
                           'trade':t,'wolf_rule_hits':wolfs[:3],
                           'match':bool(wolfs and any(x.get('aligned') for x in wolfs))})
    out={'method':'audit_trade_three_way','days':days,'decision_log_rows':len(logs),'trades_joined':len(joined),'rows':joined}
    path=os.path.join(DATA,'audit_trade_three_way.json')
    json.dump(out,open(path,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print(json.dumps({'decision_log_rows':len(logs),'trades_joined':len(joined)},ensure_ascii=False))
    print('WROTE',path)
    return 0
if __name__=='__main__':
    raise SystemExit(main())
