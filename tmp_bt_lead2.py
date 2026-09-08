# -*- coding: utf-8 -*-
import sys, os, json, sqlite3, time, requests, urllib3
sys.path.insert(0,'/app'); sys.path.insert(0,'/app/apps/main_line')
urllib3.disable_warnings()
DATA='/app/data'; GZ='https://ts.gyzcloud.top/api'; TOK=os.getenv('TUSHARE_TOKEN','a5c495cbe5e14729ad756381efe1fd72')
SEED = {
 "AI/算力/科技": [
   {"role":"upstream","label":"上游·芯片/光器件/材料设备","concepts":["AI芯片","存储芯片","光通信模块","CPO概念","PCB","液冷概念"],
    "kw":["芯片","半导体","光模块","光器件","PCB","液冷","制冷","散热","存储","处理器","CPU","面板","显示","晶圆","设备"]},
   {"role":"mid","label":"中游·算力基建/云","concepts":["算力概念","数据中心","云计算","边缘计算","东数西算"],
    "kw":["算力","服务器","数据中心","IDC","云计算","算网"]},
   {"role":"downstream","label":"下游·模型/应用/终端","concepts":["AI应用","AIGC概念","多模态AI","AI智能体","AI语料","DeepSeek概念","ChatGPT概念","Kimi概念","智谱AI","AI眼镜","人工智能"],
    "kw":["AI","人工智能","大模型","应用","软件","语料","智能体","眼镜","数字人","信息服务","互联网"]}],
 "传媒/游戏": [
   {"role":"upstream","label":"内容/研发制作","concepts":["网络游戏","影视概念","短剧互动游戏"],
    "kw":["游戏","影视","剧","内容","研发","IP"]},
   {"role":"downstream","label":"平台/分发/服务","concepts":["在线教育","职业教育","体育产业"],
    "kw":["平台","教育","培训","体育","赛事","发行"]}],
 "消费/内需": [
   {"role":"upstream","label":"品牌/制造","concepts":["白酒","新消费","消费电子概念"],
    "kw":["白酒","酒","消费","电子","品牌","制造"]},
   {"role":"downstream","label":"渠道/场景","concepts":["免税概念","新零售","零售概念","旅游概念","文娱消费"],
    "kw":["免税","零售","商超","旅游","景区","餐饮","文娱"]}],
}
def gzcal(s,e):
    r=requests.post(GZ, json={"api_name":"trade_cal","token":TOK,"params":{"exchange":"SSE","start_date":s,"end_date":e},"fields":"cal_date,is_open"}, timeout=20)
    return sorted(x[0] for x in ((r.json().get('data') or {}).get('items') or []) if x[1]==1)
def gzclose(d):
    r=requests.post(GZ, json={"api_name":"daily","token":TOK,"params":{"trade_date":d},"fields":"ts_code,trade_date,close"}, timeout=60)
    items=((r.json().get('data') or {}).get('items') or [])
    return {x[0]:float(x[2]) for x in items if x[2] is not None}
def concept_stocks(db,cname,limit=8):
    cur=db.cursor(); cur.execute("SELECT ts_code FROM stock_concept_map WHERE concept_name=? LIMIT ?",(cname,limit))
    return [r[0] for r in cur.fetchall()]
def main():
    from app.api.market import _get_tushare_pro
    pro=_get_tushare_pro()
    names={}
    try:
        sb=pro.stock_basic(exchange='',list_status='L',fields='ts_code,name')
        if sb is not None and not sb.empty: names=dict(zip(sb['ts_code'],sb['name']))
    except Exception as e: print('sb err',str(e)[:80])
    db=sqlite3.connect(os.path.join(DATA,'stock_pool.db'))
    segs=[]
    for theme,ss in SEED.items():
        for s in ss:
            codes=[]; seen=set()
            for c in s["concepts"]:
                for ts in concept_stocks(db,c):
                    if ts not in seen: seen.add(ts); codes.append(ts)
            segs.append((theme,s,codes))
    opens=gzcal('20260601','20260907')
    cand=[o for o in opens if opens.index(o)+20 < len(opens) and o>='20260701']
    hs=[cand[i] for i in range(0,len(cand),5)][-6:]
    print('opens',len(opens),'sections',hs,flush=True)
    closes={}; need=set()
    for h in hs:
        need.add(h); need.add(opens[opens.index(h)+5]); need.add(opens[opens.index(h)+20])
    for d in sorted(need): closes[d]=gzclose(d); print('daily',d,len(closes[d]),flush=True)
    latest_mv={}
    db_=pro.daily_basic(trade_date=opens[-1],fields='ts_code,total_mv')
    if db_ is not None and not db_.empty: latest_mv={str(r['ts_code']):float(r['total_mv']) for _,r in db_.iterrows()}
    def fina_ok(ts,seg):
        try:
            df=pro.fina_mainbz(ts_code=ts)
            if df is None or df.empty: return False
            df=df.sort_values('bz_sales',ascending=False)
            items=df[~df['bz_item'].isin(['行业','产品','地区'])].head(6)
            tops=[str(r2.get('bz_item') or '')[:40] for _,r2 in items.iterrows()]
            return any(k.lower() in t.lower() for t in tops for k in seg["kw"])
        except Exception: return False
    fina_ok_cache={}
    for theme,s,codes in segs:
        mvt=sorted(codes,key=lambda c:-latest_mv.get(c,0))[:12]
        for ts in list(dict.fromkeys(mvt+codes[:6])):
            fina_ok_cache[ts]=fina_ok(ts,s); time.sleep(0.15)
        okc=sum(1 for t in mvt[:6] if fina_ok_cache.get(t))
        print('fina cache',theme,s['label'],'n',len(mvt)+min(6,len(codes)),'mv6ok',okc,flush=True)
    acc=[]
    for theme,s,codes in segs:
        d0=codes[:2]; d0v=[c for c in d0 if fina_ok_cache.get(c)]
        row={'theme':theme,'seg':s['label'],'n':len(codes),'h':[]}
        for h in hs:
            h5=opens[opens.index(h)+5]; h20=opens[opens.index(h)+20]
            c0,c5,c20=closes[h],closes[h5],closes[h20]
            mv={}
            try:
                dd=pro.daily_basic(trade_date=h,fields='ts_code,total_mv')
                if dd is not None and not dd.empty: mv={str(r['ts_code']):float(r['total_mv']) for _,r in dd.iterrows()}
            except Exception: pass
            def r5(ts): return (c5.get(ts)/c0[ts]-1)*100 if ts in c0 and c0[ts] and ts in c5 and c5[ts] else None
            def r20(ts): return (c20.get(ts)/c0[ts]-1)*100 if ts in c0 and c0[ts] and ts in c20 and c20[ts] else None
            mvt5=[c for c in sorted(codes,key=lambda c:-mv.get(c,0)) if mv.get(c)][:5]
            mvt5ok=[c for c in mvt5 if fina_ok_cache.get(c)]
            def avg(f,g):
                vv=[f(t) for t in g if f(t) is not None]
                return round(sum(vv)/len(vv),2) if vv else None
            rec={'h':h,'all5':avg(r5,codes),'all20':avg(r20,codes),
                 'D0_5':avg(r5,d0),'D0_20':avg(r20,d0),
                 'MV5_5':avg(r5,mvt5),'MV5_20':avg(r20,mvt5),
                 'MVok_5':avg(r5,mvt5ok),'MVok_20':avg(r20,mvt5ok),
                 'D0ok':len(d0v),'MVokN':len(mvt5ok),
                 'mv_snaps':[(c.split('.')[0],names.get(c,'')) for c in mvt5[:3]]}
            row['h'].append(rec)
        acc.append(row)
        hh=row['h']
        def mm(k):
            vv=[x[k] for x in hh if x[k] is not None]; return round(sum(vv)/len(vv),2) if vv else None
        print(f"[{theme}][{s['label']}] n={row['n']} | ALL 5d={mm('all5')} 20d={mm('all20')} | D0(概念序2) 5d={mm('D0_5')} 20d={mm('D0_20')} | MV5 5d={mm('MV5_5')} 20d={mm('MV5_20')} | MVfinaOK 5d={mm('MVok_5')} 20d={mm('MVok_20')} | D0ok={mm('D0ok')} MVokN={mm('MVokN')}",flush=True)
    json.dump(acc,open('/tmp/lead_bt2.json','w'),ensure_ascii=False,indent=1)
    print('WROTE /tmp/lead_bt2.json')
main()