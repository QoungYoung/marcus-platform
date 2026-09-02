# -*- coding: utf-8 -*-
# 主线判定 agent：研报(报告) -> dsh /chat 产业催化分 -> 候选(catalyst>=0.7) -> 写入 main_line_state.json
# 交易 agent 每日读取 main_line_state 做"确认"(候选+动量/资金)并注入上下文。
# 2026-09-01 改造: report_rc(机构评级, 无历史) → research_report(券商研报标题, 有历史); 支持 --date/--out 历史重放。
import json, os, sys, time, requests, warnings, argparse
from concurrent.futures import ThreadPoolExecutor
warnings.filterwarnings('ignore')

# 配置
CHAT_URL=os.getenv('MAIN_LINE_CHAT_URL','http://marcus-dsh:3001/chat')
PROMAX_URL='https://pcd.mobcvb.cn/tushare/pro'
PROMAX_KEY='tsr_1FjRkziz3M7m0aLcTk0ZgnK03__xO3EYq0ZdwQqdwSE'
CATALYST_TH=0.7
STATE_FILE=os.getenv('MAIN_LINE_STATE_FILE', 'data/main_line_state.json')
REPORT_WINDOW_DAYS=int(os.getenv('MAIN_LINE_REPORT_WINDOW', '7'))  # 每周窗口, 回补近7天研报
STALE_DAYS=7  # catalyst 超过7天则重算(周频)

THEMES={
 'AI/算力/科技':['300308.SZ','002475.SZ','300502.SZ'],
 '半导体/芯片':['688981.SH','002371.SZ','603986.SH'],
 '新能源/电池':['300750.SZ','002594.SZ','601012.SH'],
 '军工/航天':['600760.SH','600893.SH','000768.SZ'],
 '资源/周期':['601899.SH','601088.SH','600028.SH'],
 '金融':['600030.SH','601318.SH','600036.SH'],
 '消费/内需':['600519.SH','000858.SZ','000333.SZ'],
 '医药':['600276.SH','300760.SZ','600196.SH'],
 '稳增长/基建':['601668.SH','600585.SH','601390.SH'],
}

def ts_promax(api, **p):
    for _ in range(2):
        try:
            r=requests.get(f'{PROMAX_URL}/{api}', params=p, headers={'X-API-Key':PROMAX_KEY}, verify=False, timeout=45)
            return r.status_code, r.json()
        except Exception:
            time.sleep(1)
    return None, {'ERR':'x'}

def fetch_report_titles(theme, start, end):
    """研报标题: research_report(券商研报, 有历史) items=[trade_date,title,url,report_type,author,name,ts_code,org]
    2026-09-01: 线程池并行拉 3 只代表股, 单日期耗时 ~4min→~1min"""
    codes=THEMES.get(theme, [])
    def get_one(code):
        st,d=ts_promax('research_report', ts_code=code, start_date=start, end_date=end)
        items=d.get('data',{}).get('items') if isinstance(d.get('data'),dict) else None
        if not items: return []
        return [it[1] for it in items if len(it)>1 and it[1]]
    with ThreadPoolExecutor(max_workers=6) as ex:
        lists=list(ex.map(get_one, codes))
    titles=[t for lst in lists for t in lst]
    seen=set(); out=[]
    for t in titles:
        if t not in seen: seen.add(t); out.append(t)
    return out[:15]

def call_agent(date, themes):
    parts=['你是主线判断 agent，面对日期 '+date+'，请对下面 9 个主题分别评估产业催化分(0~1)与主线阶段(早候选/确认/已发酵/无)。'
           '基于各主题在事件日前的研报标题(点内)判断是否存在大厂资本开支/发布会/技术突破/政策/订单/量产/涨价等产业级催化。'
           '仅输出一个 JSON 对象(不要markdown、不要多余文字)：']
    for th in THEMES:
        rs=themes.get(th) or []
        parts.append('【'+th+'】研报:'+chr(10)+(chr(10).join('- '+r for r in rs[:12]) if rs else '(无)'))
    parts.append('输出JSON: {"themes": {"主题名": {"catalyst_score": 0.0, "main_line_stage": "确认"}}}')
    body={'message': chr(10).join(parts), 'session_id': 'ml_'+date.replace('-','')}
    r=requests.post(CHAT_URL, json=body, headers={'Content-Type':'application/json'}, timeout=180, verify=False)
    r.raise_for_status()
    reply=r.json().get('reply','')
    # 提取 themes
    obj=json.loads(reply)
    if 'themes' not in obj and isinstance(obj, dict): obj=obj.get('data', obj)
    return obj.get('themes', {})

def live_bank_signal(today):
    """实时抓最近交易日的银行行业净流入+涨幅 → bank 信号(周一开盘前自动回退到上周五)"""
    try:
        import urllib3; urllib3.disable_warnings()
        from datetime import datetime, timedelta
        d0 = today
        for back in range(8):   # 最多回退 7 天找最近有数据的交易日(周一开盘前→上周五)
            st, d = ts_promax('moneyflow_ind_dc', content_type='行业', trade_date=d0.replace('-',''))
            data = d.get('data') or {}
            items = data.get('items') or []
            if items: break
            d0 = (datetime.strptime(d0, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        fields=data.get('fields') or []
        all_na=[]; bk=None
        for it in items:
            z=dict(zip(fields, it))
            nm=str(z.get('name') or '')
            if z.get('net_amount') is not None:
                all_na.append((nm, float(z['net_amount'])))
            if nm=='银行' and bk is None:
                bk=z
        if not bk or bk.get('net_amount') is None or not all_na: return None
        na=float(bk['net_amount'])
        rank=sum(1 for _,n in all_na if n<na)
        fund=(rank+1)/len(all_na)
        pct=bk.get('pct_change')
        try: pct=float(pct) if pct is not None else 0.0
        except Exception: pct=0.0
        rank_f=bk.get('rank')
        try: rank_f=int(rank_f) if rank_f is not None else None
        except Exception: rank_f=None
        is_top=(rank_f==1) or (fund>=0.99)
        if is_top and pct>=1.0:
            fund_v=1.0; rel=min(1.0, pct/2.0)
        else:
            fund_v=min(fund, 0.3); rel=0.1 if pct<1.0 else 0.3
        return {'catalyst':0.0,'fund':fund_v,'rel':rel,'conc':fund_v}
    except Exception as e:
        print('[main_line] bank signal err:', str(e)[:80], file=sys.stderr)
        return None

def fusion_signals(hist, today, catalyst):
    """9 概念主题信号(资金/强度/集中度) + 银行 → 融合 score"""
    os.environ.setdefault('DATA_DIR', '/app/data')
    import fusion_mainline as fm
    sig=fm.theme_signals(hist, today, {'catalyst': catalyst})
    bk=live_bank_signal(today)
    if bk:
        sig['银行']=bk   # 实时银行覆盖重放文件默认(生产必须用实时)
    else:
        sig.pop('银行', None)   # 无实时银行数据则去掉银行主题(避免默认0.5混入)
    # 最优融合权重(2026-09-01 网格搜索): cat=0, fund=0.3, rel=0.2, conc=0.5
    sc={th: 0.3*v['fund']+0.2*v['rel']+0.5*v['conc'] for th,v in sig.items()}
    return sig, sc

def main():
    ap=argparse.ArgumentParser(description='主线判定 agent (research_report 研报 + 资金/集中度/银行融合)')
    ap.add_argument('--date', default=None, help='评估日期 YYYY-MM-DD (历史重放用, 默认今天)')
    ap.add_argument('--out', default=None, help='输出文件 (默认 data/main_line_state.json; 历史重放建议 data/main_line_state_<date>.json)')
    ap.add_argument('--window', type=int, default=REPORT_WINDOW_DAYS, help='研报回补窗口天数 (默认7)')
    ap.add_argument('--no-fusion', action='store_true', help='只输出研报catalyst版(兼容旧行为)')
    args=ap.parse_args()
    today=args.date or time.strftime('%Y-%m-%d')
    if args.date:
        end_dt=time.strptime(today,'%Y-%m-%d')
        start_dt=time.localtime(time.mktime(end_dt)-args.window*86400)
        start=time.strftime('%Y%m%d', start_dt)
        end=today.replace('-','')
        out_file=args.out or f'data/main_line_state_{today.replace("-","")}.json'
    else:
        start=time.strftime('%Y%m%d', time.localtime(time.time()-args.window*86400))
        end=time.strftime('%Y%m%d')
        out_file=args.out or STATE_FILE
        # 生产模式: 先增量更新概念资金缓存(上周五→最新交易日), 融合信号依赖最新数据
        if not args.no_fusion:
            try:
                rc=os.system('python /app/apps/main_line/build_concept_matrix.py --incremental >/tmp/concept_inc.log 2>&1')
                print('[main_line] concept incremental rc=%s'%rc, file=sys.stderr)
            except Exception as e:
                print('[main_line] concept incremental err:', str(e)[:80], file=sys.stderr)
    themes={}
    with ThreadPoolExecutor(max_workers=5) as ex:
        fetched=list(ex.map(lambda th: (th, fetch_report_titles(th, start, end)), list(THEMES.keys())))
    themes=dict(fetched)
    print('[main_line] date=%s 研报数:'%today, {k:len(v) for k,v in themes.items()}, file=sys.stderr)
    agent_themes=call_agent(today, themes)
    catalyst={}
    for th in THEMES:
        v=agent_themes.get(th) or {}
        catalyst[th]=v.get('catalyst_score')
    state={'date':today,'catalyst':catalyst,'updated_at':time.strftime('%Y-%m-%d %H:%M:%S')}
    if args.no_fusion:
        def cand(x): return (x is not None) and (x>=CATALYST_TH)
        state['candidates']=[th for th in THEMES if cand(catalyst.get(th))]
        ranked=sorted([th for th in THEMES if catalyst.get(th) is not None], key=lambda t:-(catalyst[t] or 0))
        state['main_line']=ranked[0] if ranked else None
    else:
        # 融合: 概念信号 + 银行 + score
        hist_path=os.path.join(os.environ.get('DATA_DIR','/app/data'),'concept_hist.json')
        hist={}
        if os.path.exists(hist_path):
            try: hist=json.load(open(hist_path,encoding='utf-8'))
            except Exception: hist={}
        sig, sc=fusion_signals(hist, today, catalyst)
        ranked=sorted(sc.keys(), key=lambda k:-sc[k])
        main_line=ranked[0] if ranked else None
        candidates=ranked[:2] if len(ranked)>=2 else ranked
        state['candidates']=candidates
        state['main_line']=main_line
        state['fusion']={th: {'score':round(sc[th],3),
                              'catalyst':round(sig[th].get('catalyst',0),2),
                              'fund':round(sig[th].get('fund',0),2),
                              'rel':round(sig[th].get('rel',0),2),
                              'conc':round(sig[th].get('conc',0),2)} for th in sig}
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file,'w',encoding='utf-8') as f: json.dump(state, f, ensure_ascii=False, indent=2)
    print(json.dumps(state, ensure_ascii=False, indent=2))

if __name__=='__main__': main()
