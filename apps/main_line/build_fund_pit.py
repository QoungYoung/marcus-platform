# -*- coding: utf-8 -*-
"""build_fund_pit.py — Phase1: 事件日 PIT 公募持仓快照
步骤: ①事件日 fund_share top60(历史trade_date可用) → ②union基金 fund_portfolio 全历史拉取
      → ③每事件日取 ann_date<=事件日的每基金最新报告 + 每基金top10(按mkv) → data/crowding_pit/stock_crowd_<date>.json
用法: python -u apps/main_line/build_fund_pit.py [--events E06,E13] [--dry-run]
"""
import os, sys, json, urllib.request, gzip, time, collections, datetime as _dt

TOKEN = os.getenv('TUSHARE_TOKEN','')
URL = os.getenv('TUSHARE_API_URL','')
DB = os.getenv('DATABASE_URL','')
DATA = os.environ.get('DATA_DIR','data')
ALLOWED_ENDS = ['20250630','20250930','20251231','20260331','20260630']
EVENTS = [
    ('E06','2025-12-31'), ('E07','2026-01-05'), ('E08','2026-01-14'),
    ('E09','2026-02-28'), ('E10','2026-03-19'), ('E11','2026-04-22'),
    ('E12','2026-06-05'), ('E13','2026-07-08'),
]
def arg_events():
    if '--events' in sys.argv:
        i = sys.argv.index('--events'); want = set(sys.argv[i+1].split(','))
        return [(e,d) for e,d in EVENTS if e in want]
    return EVENTS

def call(api, params, fields):
    if not TOKEN or not URL:
        raise RuntimeError('missing TUSHARE_TOKEN/TUSHARE_API_URL')
    body={'api_name':api,'token':TOKEN,'params':params,'fields':fields}
    req=urllib.request.Request(URL,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Accept-Encoding':'identity'})
    with urllib.request.urlopen(req,timeout=60) as resp: raw=resp.read()
    try: d=json.loads(raw.decode())
    except UnicodeDecodeError: d=json.loads(gzip.decompress(raw).decode())
    if d.get('code')!=0: raise RuntimeError('%s %s' % (api, str(d.get('msg'))[:120]))
    return d.get('data',{}).get('items') or []

def nearest_trade_date(dstr, max_back=10):
    """事件日前一天起的最近交易日份额(严格PIT: 当日份额收盘后才公布)"""
    d = _dt.date.fromisoformat(dstr)
    for i in range(1, max_back+1):
        ds = (d - _dt.timedelta(days=i)).strftime('%Y%m%d')
        try:
            it = call('fund_share', {'trade_date': ds}, 'ts_code')
            if it: return ds
        except Exception:
            continue
        time.sleep(0.05)
    raise RuntimeError('no fund_share date near '+dstr)

def share_top(ds, n=60):
    it = call('fund_share', {'trade_date': ds}, 'ts_code,trade_date,fd_share')
    arr=[]
    for r in it:
        try:
            if len(r)>=3 and r[0] and r[2] is not None: arr.append((str(r[0]), float(r[2])))
        except Exception: pass
    arr.sort(key=lambda x: -x[1])
    return [c for c,_ in arr[:n]]

def rows_for_fund(cur, fund):
    cur.execute('SELECT end_date FROM fund_portfolio_holdings WHERE fund_code=%s', (fund,))
    return {r[0] for r in cur.fetchall()}

def main():
    events = arg_events()
    print('events:', events, flush=True)
    if '--dry-run' in sys.argv:
        print('dry run: only probe share top counts', flush=True)
        for e,d in events:
            ds = nearest_trade_date(d)
            top = share_top(ds, 60)
            print(e,d,'share_date',ds,'top60',len(top),'top5',top[:5],flush=True)
        return 0
    conn = None
    try:
        import psycopg2
        conn = psycopg2.connect(DB); conn.autocommit=True
    except Exception as ex:
        print('ERR db', ex); return 1
    cur = conn.cursor()
    # --- 1) per-event share top60 (union funds) ---
    share_info = {}; fund_set = set()
    for e,d in events:
        ds = nearest_trade_date(d)
        print("  fetching fund_share", e, d, "-> share_date", ds, flush=True)
        top = share_top(ds, 60)
        share_info[e] = {'date':d,'share_date':ds,'top':top}
        fund_set |= set(top)
        print(e,d,'share_date',ds,'top60',len(top),flush=True)
    funds = sorted(fund_set)
    print('union funds:', len(funds), flush=True)
    # --- 2) pull fund_portfolio full history for union funds into fund_portfolio_holdings ---
    if '--no-fetch' not in sys.argv:
        for i,f in enumerate(funds,1):
            try:
                rows = call('fund_portfolio', {'ts_code': f},
                            'ts_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio,stk_float_ratio')
            except Exception as ex:
                print('skip',f,str(ex)[:80],flush=True); continue
            n=0
            for r in rows:
                if len(r)<8: continue
                end=str(r[2]); sym=str(r[3] or '')
                if end not in ALLOWED_ENDS or not sym: continue
                cur.execute(
                    'INSERT INTO fund_portfolio_holdings (fund_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio,stk_float_ratio)'
                    ' VALUES (%s,%s,%s,%s,%s,%s,%s,%s)'
                    ' ON CONFLICT (fund_code,symbol,end_date) DO UPDATE SET ann_date=EXCLUDED.ann_date,'
                    ' mkv=EXCLUDED.mkv, amount=EXCLUDED.amount, stk_mkv_ratio=EXCLUDED.stk_mkv_ratio, stk_float_ratio=EXCLUDED.stk_float_ratio',
                    (str(r[0]), str(r[1]) if r[1] is not None else None, end, sym,
                     float(r[4]) if r[4] is not None else None, float(r[5]) if r[5] is not None else None,
                     float(r[6]) if r[6] is not None else None, float(r[7]) if r[7] is not None else None))
                n += 1
            if i % 10 == 0 or i == len(funds):
                print('fetch progress', i, '/', len(funds), 'rows', n, flush=True)
            time.sleep(0.1)
    # --- 3) snapshot per event ---
    os.makedirs(os.path.join(DATA,'crowding_pit'), exist_ok=True)
    for e,cfg in share_info.items():
        d=cfg['date']; ds=d.replace('-',''); topset=cfg['top']
        cur.execute(
            'SELECT fund_code, ann_date, end_date, symbol, mkv, amount, stk_mkv_ratio, stk_float_ratio'
            ' FROM fund_portfolio_holdings WHERE fund_code = ANY(%s) AND ann_date <= %s AND end_date = ANY(%s)',
            (topset, ds, ALLOWED_ENDS))
        byfund=collections.defaultdict(list)
        for fcode, ann, end, sym, mkv, amt, mkv_r, flt in cur.fetchall():
            byfund[fcode].append({'ann':ann or '', 'end':end, 'sym':sym, 'mkv':mkv, 'amt':amt, 'mkv_r':mkv_r, 'flt':flt})
        end_use={}; stock=collections.defaultdict(lambda: {'n_funds':0,'sum_float':0.0,'sum_mkv':0.0,'sum_amount':0.0})
        n_effective=0
        for fcode, rows in byfund.items():
            if not rows: continue
            # latest report visible at event: max by (end_date); if ties prefer larger end
            best=max(rows, key=lambda x:(x['ann'], x['end']))
            end_use[fcode]=best['end']
            mine=[r for r in rows if r['end']==best['end']]
            mine.sort(key=lambda r: (r['mkv'] if isinstance(r['mkv'],(int,float)) else -1), reverse=True)
            top10=mine[:10]
            for r in top10:
                st=stock[r['sym']]
                st['n_funds']+=1
                st['sum_float']+= float(r['flt'] or 0)
                st['sum_mkv']+= float(r['mkv'] or 0)
                st['sum_amount']+= float(r['amt'] or 0)
            if top10: n_effective+=1
        for s in stock:
            stock[s]['sum_mkv_yi']=round(stock[s]['sum_mkv']/1e8, 2)
            stock[s]['sum_float']=round(stock[s]['sum_float'], 4)
        out={'date':d,'share_date':cfg['share_date'],'top_funds':topset,'effective_funds':n_effective,
             'end_usage':end_use,'stock':stock}
        path=os.path.join(DATA,'crowding_pit','stock_crowd_%s.json'%d)
        json.dump(out, open(path,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
        top_stocks=sorted(stock.items(), key=lambda kv: -kv[1]['n_funds'])[:5]
        print('SNAPSHOT',e,d,'effective_funds',n_effective,'stocks',len(stock),
              'top5',[(s, v['n_funds'], v['sum_mkv_yi']) for s,v in top_stocks],flush=True)
        print('WROTE',path,flush=True)
    cur.close(); conn.close()
    print('DONE', flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
