# -*- coding: utf-8 -*-
"""build_macro_state.py — P2 宏观/机构行为 state v1 采集器
数据源:
  - 收益率(CN/US 2/5/10/30Y): akshare bond_zh_us_rate (1990起, 服务器已装)
  - 美元指数实时: 新浪 DINIW
  - 两融/GJD宽基份额/北向: wave_agent.get_market_context(tushare)
输出: data/macro_state.json  (宏观段原始值, 不在这里做买卖判定)
用法: python -u apps/main_line/build_macro_state.py [YYYY-MM-DD]
"""
import os, sys, json, re, datetime as _dt
import pandas as pd
try:
    import akshare as ak
except Exception as e:
    print('ERR akshare', e); sys.exit(1)
DATA=os.environ.get('DATA_DIR','data')
def dstr_default():
    return _dt.date.today().isoformat()
def _num(x):
    try:
        v=float(x); return v if pd.notna(v) else None
    except Exception: return None
def rate_snapshot(target):
    df=ak.bond_zh_us_rate()
    df['日期']=pd.to_datetime(df['日期'])
    df=df.sort_values('日期').reset_index(drop=True)
    sub=df[df['日期']<=pd.Timestamp(target)]
    if len(sub)==0: return None,None
    i=sub.index[-1]; prev=sub.index[-2] if len(sub)>=2 else None
    def pick(row, col): return _num(row.get(col))
    cols=['中国国债收益率2年','中国国债收益率5年','中国国债收益率10年','中国国债收益率30年','中国国债收益率10年-2年',
          '美国国债收益率2年','美国国债收益率5年','美国国债收益率10年','美国国债收益率30年','美国国债收益率10年-2年']
    cur={c:pick(sub.loc[i],c) for c in cols}
    prevrow=None
    if prev is not None:
        prevrow={c:pick(sub.loc[prev],c) for c in cols}
    return cur,prevrow
def dxy_now():
    import ssl,urllib.request
    ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    req=urllib.request.Request('https://hq.sinajs.cn/list=DINIW',
        headers={'Referer':'https://finance.sina.com.cn/','User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=15,context=ctx) as r:
        txt=r.read().decode('gbk','replace')
    m=re.search(r'"([^"]*)"',txt)
    if not m: return None,None
    parts=m.group(1).split(',')
    if len(parts)>=9:
        try: return float(parts[1]), parts[0]  # value, time
        except Exception: pass
    return None,None
def _derive_switches(out):
    """Wolf 四类开关 v2（阈值先按语料经验值, 供 A/B 调）:
    ①崩盘清单-债市异动 ②流动性/曲线 ③两融热钱/杀杠杆 ④GJD撤退/护盘 + 北向增量"""
    y=(out.get('yields') or {}); us=(y.get('us') or {}); cn=(y.get('cn') or {}); ch=(y.get('chg') or {})
    m=(out.get('market') or {}); g=(m.get('gjd') or {})
    sw={'us_yield_spike': bool((ch.get('us10_1d') is not None and ch['us10_1d']>=0.10) or (ch.get('us30_1d') is not None and ch['us30_1d']>=0.10)),
        'cn30_spike': bool(ch.get('cn30_1d') is not None and ch['cn30_1d']>=0.15),
        'us_curve_inverted': bool(us.get('10年_2年') is not None and us['10年_2年']<0),
        'margin_heat': bool(m.get('margin_20d_chg') is not None and m['margin_20d_chg']>=3.0 and (m.get('margin_net_buy') or 0)>0),
        'margin_burst': bool((m.get('margin_20d_chg') is not None and m['margin_20d_chg']<=-5.0) or (m.get('margin_net_buy') is not None and m['margin_net_buy']<=-100.0)),
        'gjd_support': bool((g.get('sh300_chg20') is not None and g['sh300_chg20']>0) and (g.get('sh50_chg20') is not None and g['sh50_chg20']>0)),
        'gjd_withdraw': bool((g.get('sh300_chg20') is not None and g['sh300_chg20']<-2.0) or (g.get('sh50_chg20') is not None and g['sh50_chg20']<-2.0)),
        'north_in': bool((m.get('north_5d') or 0)>0)}
    flags=[k for k,v in sw.items() if v]
    out['macro_switches']={'flags':flags,'detail':sw}
    out['macro_switches_text']=_switches_text(sw)
    return sw

def _switches_text(sw):
    NL=chr(10); L=[]
    if sw['us_yield_spike'] or sw['cn30_spike']:
        L.append('- 崩盘清单预警(债市异动): 美债/中债长端单日异动 → 不追高, 防守优先')
    if sw['us_curve_inverted']:
        L.append('- 美债10Y-2Y倒挂: 衰退/流动性风险背景')
    if sw['margin_heat']:
        L.append('- 两融热钱活跃: 主升可跟随, 但勿追高热点/高位基石')
    if sw['margin_burst']:
        L.append('- 杀杠杆阶段: 不接飞刀, 等GJD兜底; 难做浪段以指数ETF为主')
    if sw['gjd_support']:
        L.append('- GJD护盘(宽基份额增): 支撑位附近可低吸/做T')
    if sw['gjd_withdraw']:
        L.append('- GJD撤退(宽基份额减): 政策底未坐实/资金面弱, 不抢反弹')
    if sw['north_in']:
        L.append('- 北向/增量资金流入: 允许跟随主线')
    else:
        L.append('- 北向/增量资金偏弱: 不追高')
    if not L: L.append('- 宏观开关: 中性(未触发异常)')
    return '## 宏观/机构开关（Wolf v2）' + NL + NL.join(L) + NL + NL

def main():
    target=sys.argv[1] if len(sys.argv)>1 else dstr_default()
    print('target',target,flush=True)
    out={'date':target,'yields':{},'dxy':{},'market':{},'meta':{'generated_at':_dt.datetime.now().isoformat()}}
    try:
        cur,prev=rate_snapshot(target)
        if cur:
            out['yields']['cn']={k.replace('中国国债收益率','').replace('-','_'):cur[k] for k in ['中国国债收益率2年','中国国债收益率5年','中国国债收益率10年','中国国债收益率30年','中国国债收益率10年-2年']}
            out['yields']['us']={k.replace('美国国债收益率','').replace('-','_'):cur[k] for k in ['美国国债收益率2年','美国国债收益率5年','美国国债收益率10年','美国国债收益率30年','美国国债收益率10年-2年']}
            if prev:
                out['yields']['chg']={
                  'cn30_5d_dummy':None,'cn30_1d': (cur['中国国债收益率30年']-prev['中国国债收益率30年']) if cur['中国国债收益率30年'] is not None and prev['中国国债收益率30年'] is not None else None,
                  'us10_1d': (cur['美国国债收益率10年']-prev['美国国债收益率10年']) if cur['美国国债收益率10年'] is not None and prev['美国国债收益率10年'] is not None else None,
                  'us30_1d': (cur['美国国债收益率30年']-prev['美国国债收益率30年']) if cur['美国国债收益率30年'] is not None and prev['美国国债收益率30年'] is not None else None}
            print('yields ok',out['yields']['cn'].get('30年'),out['yields']['us'].get('30年'),flush=True)
        else:
            print('yields no data',flush=True)
    except Exception as e:
        print('yields ERR',str(e)[:200],flush=True); out['yields']['error']=str(e)[:200]
    try:
        v,tm=dxy_now()
        out['dxy']={'value':v,'time':tm}
        print('dxy',v,tm,flush=True)
    except Exception as e:
        print('dxy ERR',str(e)[:120],flush=True); out['dxy']['error']=str(e)[:120]
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import wave_agent as wa
        ctx=wa.get_market_context(target)
        out['market']={k:ctx.get(k) for k in ['idx_close','idx_r5','idx_r20','margin_rzrqye','margin_20d_chg','margin_net_buy','margin_rzrqye_pct','north_5d','north_today','gjd','turnover_rate','pe_ttm','vol_ratio_5_60','vol_pct120']}
        print('market ctx keys',list(out['market'].keys()),flush=True)
    except Exception as e:
        print('market ERR',str(e)[:200],flush=True); out['market']['error']=str(e)[:200]
    try:
        _derive_switches(out)
    except Exception as e:
        out['macro_switches']={'flags':[],'err':str(e)[:120]}
    path=os.path.join(DATA,'macro_state.json')
    json.dump(out,open(path,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print('WROTE',path,flush=True); print('DONE',flush=True)
if __name__=='__main__':
    main()
