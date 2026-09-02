# -*- coding: utf-8 -*-
# 狼大波数级浪型判定 agent：喂上证指数结构+点位+量能 -> dsh /chat 按狼大多级别Elliott出 (大级别level, 子浪sub_level, 操作operation)
# 两级结构：level=大级别 (d1反转/d2调整大2/d3主升大3/d4大4回调/d5末段衰竭/down下跌浪)
#          sub_level=子浪/局部 (3-1..3-5 / 4-1..4-5 / 失败5 / ABC / B反 / C杀 / W底 / 双头M顶 / 衰竭)
#          operation=build(建仓追)/t_only(只做T)/side(观望调仓换股)/defense(防御不建仓)/exit(兑现降仓)
import os, sys, json, time, requests, warnings
warnings.filterwarnings('ignore')
import pandas as pd, numpy as np

CHAT_URL=os.getenv('WAVE_CHAT_URL','http://marcus-dsh:3001/chat')
STATE_FILE=os.getenv('WAVE_STATE_FILE','data/wave_state.json')
CSV='data/指数数据/index_daily/000001.SH.csv'
BT=chr(96)*3  # 代码围栏

def _ts_pro():
    try:
        import tushare as ts
        tok=os.getenv('TUSHARE_TOKEN','')
        if not tok: return None
        pro=ts.pro_api(tok)
        url=os.getenv('TUSHARE_API_URL','')
        if url: pro._DataApi__http_url=url
        return pro
    except Exception:
        return None

def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default

def get_gjd_flow(date):
    """读取宽基ETF(510300沪深300/510050上证50)份额净申赎 → GJD/国家队资金流。
    返回 {sh300_chg5/chg20/net5/inflow_5d, sh50_chg5/chg20/net5/inflow_5d,...}。
    净申购(份额增)=真金白银托底；份额无增=只是超跌反弹。不可用项缺失。"""
    pro = _ts_pro()
    d = pd.Timestamp(date)
    ds = d.strftime('%Y%m%d')
    start = (d - pd.Timedelta(days=45)).strftime('%Y%m%d')
    out = {}
    if pro is None:
        return out
    for label, code in [("sh300", "510300.SH"), ("sh50", "510050.SH")]:
        sh = _safe(lambda: pro.fund_share(ts_code=code, start_date=start, end_date=ds), pd.DataFrame())
        if sh is None or not len(sh):
            continue
        sh = sh.copy(); sh['trade_date'] = sh['trade_date'].astype(str); sh = sh.sort_values('trade_date')
        fd = pd.to_numeric(sh['fd_share'], errors='coerce')
        out[label + "_share"] = round(float(fd.iloc[-1]), 0) if pd.notna(fd.iloc[-1]) else None
        if len(fd) >= 6 and pd.notna(fd.iloc[-6]) and float(fd.iloc[-6]):
            out[label + "_chg5"] = round((float(fd.iloc[-1]) / float(fd.iloc[-6]) - 1) * 100, 2)
            out[label + "_net5"] = round(float(fd.iloc[-1] - fd.iloc[-6]), 0)
        if len(fd) >= 21 and pd.notna(fd.iloc[-21]) and float(fd.iloc[-21]):
            out[label + "_chg20"] = round((float(fd.iloc[-1]) / float(fd.iloc[-21]) - 1) * 100, 2)
        nav = _safe(lambda: pro.fund_nav(ts_code=code, start_date=start, end_date=ds), pd.DataFrame())
        if nav is not None and len(nav):
            nav = nav.copy(); nav['nav_date'] = nav['nav_date'].astype(str); nav = nav.sort_values('nav_date')
            un = pd.to_numeric(nav['unit_nav'], errors='coerce')
            if len(un) >= 1 and pd.notna(un.iloc[-1]) and out.get(label + "_net5") is not None:
                out[label + "_inflow_5d"] = round((out[label + "_net5"] * 10000 * float(un.iloc[-1])) / 1e8, 1)
    return out

def get_market_context(date):
    """date: 'YYYY-MM-DD'. 返回量能/资金(两融)/北向/GJD护盘(HS300.SH50超额)/换手情绪特征 dict。
    全部源独立 try/except，不可用项置 None。狼大判读要点见 build_prompt。"""
    pro=_ts_pro()
    d=pd.Timestamp(date)
    ds=d.strftime('%Y%m%d')
    ctx={'date':date}
    if pro is None:
        return ctx
    def idxdf(code):
        return _safe(lambda: pro.index_daily(ts_code=code, start_date=(d-pd.Timedelta(days=220)).strftime('%Y%m%d'), end_date=ds), pd.DataFrame())
    idx=idxdf('000001.SH'); hi=idxdf('000300.SH'); s5=idxdf('000016.SH')
    if idx is not None and len(idx):
        i=idx.copy(); i['trade_date']=i['trade_date'].astype(str); i=i.sort_values('trade_date')
        ctx['idx_close']=round(float(i['close'].iloc[-1]),1)
        ctx['idx_r5']=round(float(i['close'].iloc[-1]/i['close'].iloc[-6]-1)*100,1) if len(i)>=6 else None
        ctx['idx_r20']=round(float(i['close'].iloc[-1]/i['close'].iloc[-21]-1)*100,1) if len(i)>=21 else None
        vol=i['vol']
        if len(vol)>=60:
            ctx['vol_ratio_5_60']=round(float(vol.tail(5).mean()/vol.tail(60).mean()),2)
            ctx['vol_z20']=round(float((vol.iloc[-1]-vol.tail(20).mean())/vol.tail(20).std()),2)
        if len(vol)>=120:
            ctx['vol_pct120']=round(float((vol.tail(120)<=vol.iloc[-1]).mean()),2)
    def xr(df, base):
        if df is None or not len(df) or base is None or not len(base): return None
        a=df.copy(); b=base.copy(); a['trade_date']=a['trade_date'].astype(str); b['trade_date']=b['trade_date'].astype(str)
        m=a.merge(b,on='trade_date',suffixes=('_a','_b')); m=m.sort_values('trade_date')
        if len(m)<21: return None
        r5=round((m['close_a'].iloc[-1]/m['close_a'].iloc[-6]-1-(m['close_b'].iloc[-1]/m['close_b'].iloc[-6]-1))*100,1)
        r20=round((m['close_a'].iloc[-1]/m['close_a'].iloc[-21]-1-(m['close_b'].iloc[-1]/m['close_b'].iloc[-21]-1))*100,1)
        return {'xr5':r5,'xr20':r20}
    ctx['hs300_xr']=xr(hi,idx)
    ctx['sh50_xr']=xr(s5,idx)
    ib=_safe(lambda: pro.index_dailybasic(ts_code='000001.SH', start_date=(d-pd.Timedelta(days=10)).strftime('%Y%m%d'), end_date=ds), pd.DataFrame())
    if ib is not None and len(ib):
        ib=ib.sort_values('trade_date')
        ctx['turnover_rate']=round(float(ib['turnover_rate'].iloc[-1]),2)
        ctx['pe_ttm']=round(float(ib['pe_ttm'].iloc[-1]),1) if pd.notna(ib['pe_ttm'].iloc[-1]) else None
    mg=_safe(lambda: pro.margin(start_date=(d-pd.Timedelta(days=140)).strftime('%Y%m%d'), end_date=ds), pd.DataFrame())
    if mg is not None and len(mg):
        mg2=mg.copy(); mg2['trade_date']=mg2['trade_date'].astype(str)
        agg=mg2.groupby('trade_date')[['rzye','rzrqye','rzmre','rzche']].sum().reset_index().sort_values('trade_date')
        cur=agg.iloc[-1]
        ctx['margin_rzrqye']=round(float(cur['rzrqye'])/1e8,1)
        ctx['margin_20d_chg']=round(float(cur['rzrqye']/agg['rzrqye'].iloc[-21]-1)*100,1) if len(agg)>=21 else None
        ctx['margin_net_buy']=round(float(cur['rzmre']-cur['rzche'])/1e8,1)
        ctx['margin_rzrqye_pct']=round(float((agg['rzrqye'].iloc[-60:].values<=cur['rzrqye']).mean()),2) if len(agg)>=60 else None
    hs=_safe(lambda: pro.moneyflow_hsgt(start_date=(d-pd.Timedelta(days=30)).strftime('%Y%m%d'), end_date=ds), pd.DataFrame())
    if hs is not None and len(hs):
        hs=hs.sort_values('trade_date'); nm=pd.to_numeric(hs['north_money'], errors='coerce')
        ctx['north_5d']=round(float(nm.tail(5).sum()),1) if nm.tail(5).notna().any() else None
        ctx['north_today']=round(float(nm.iloc[-1]),1) if pd.notna(nm.iloc[-1]) else None
    ctx['gjd']=get_gjd_flow(date)   # 真实GJD/宽基ETF(510300/510050)份额净申赎
    return ctx

def load_close():
    if os.path.exists(CSV):
        df=pd.read_csv(CSV, parse_dates=['trade_date'])
        return pd.Series(df['close'].values, index=pd.DatetimeIndex(df['trade_date']))
    return pd.read_parquet('data/指数数据/index_daily/000001.SH.parquet')['close']

PIVOTS_FILE = os.getenv("WAVE_PIVOTS_FILE", "data/wave_pivots.json")

def _detect_double_bottom(cs):
    """在收盘序列(cs, DatetimeIndex)上扫双底：两个相距15-240天的近低点(8%内)且中间有>=5%反弹。
    返回描述串，无则空串。"""
    try:
        if cs is None or len(cs) < 60: return ""
        vals = cs.values; idx = cs.index
        lows = []
        for i in range(15, len(cs)-15):
            win = vals[i-15:i+16]
            if vals[i] == win.min():
                lows.append((pd.Timestamp(idx[i]), float(vals[i])))
        if len(lows) < 2: return ""
        last = lows[-1]
        for j in range(len(lows)-2, -1, -1):
            other = lows[j]
            gap = (last[0]-other[0]).days
            if 15 <= gap <= 240 and abs(last[1]/other[1]-1) <= 0.08:
                between = [i for i in idx if other[0] < i < last[0]]
                if between:
                    midmax = float(cs.loc[between].max())
                    if midmax >= min(other[1], last[1]) * 1.05:
                        return f"{other[0].date()} {round(other[1],1)} + {last[0].date()} {round(last[1],1)}"
        return ""
    except Exception:
        return ""

def get_anchor_context(date, close, cs=None):
    """返回历史大级别锚点上下文 dict：当前价 vs 最近大级别顶/底、是否双底、近期关键位。"""
    try:
        data = json.load(open(PIVOTS_FILE, encoding="utf-8"))
        piv = data.get("pivots", [])
    except Exception:
        return {}
    t = pd.Timestamp(date)
    prev = [x for x in piv if pd.Timestamp(x["date"]) <= t]
    if not prev:
        return {}
    lastH = max([x for x in prev if x["type"]=="H"], key=lambda x:x["date"], default=None)
    lastL = max([x for x in prev if x["type"]=="L"], key=lambda x:x["date"], default=None)
    dbl = _detect_double_bottom(cs) if cs is not None else ""
    if not dbl:
        lows = sorted([x for x in prev if x["type"]=="L"], key=lambda x:x["date"])
        for i in range(len(lows)-1):
            a,b = lows[i], lows[i+1]
            gap = (pd.Timestamp(b["date"])-pd.Timestamp(a["date"])).days
            if gap<=190 and abs(b["value"]/a["value"]-1)<=0.05:
                dbl = f"{a['date']} {a['value']} + {b['date']} {b['value']}"
                break
    recent = sorted(prev, key=lambda x:x["date"])[-4:]
    out = {"date": date}
    if lastH: out["last_high"]={"date":lastH["date"],"value":lastH["value"]}
    if lastL: out["last_low"]={"date":lastL["date"],"value":lastL["value"]}
    out["double_bottom"]=dbl
    out["recent"]=[{"date":x["date"],"value":x["value"],"type":x["type"]} for x in recent]
    if close is not None and lastH and lastL:
        rng = lastH["value"]-lastL["value"] or 1
        out["pos_vs_range"] = round((close-lastL["value"])/rng*100,1)
        out["vs_last_high"] = round((close/lastH["value"]-1)*100,1)
        out["vs_last_low"] = round((close/lastL["value"]-1)*100,1)
    return out

def read_main_line():
    """读取主线判定 main_line_state.json -> dict，供 agent 判断主线逻辑。"""
    try:
        path = os.getenv("MAIN_LINE_STATE_FILE", os.path.join(os.environ.get("DATA_DIR","data"), "main_line_state.json"))
        if not os.path.exists(path): return {}
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}

def index_features(date=None):
    close=load_close()
    if date is None: date=str(close.index[-1].date())
    d=pd.Timestamp(date); cs=close[close.index<=d].dropna()
    if len(cs)<200: return None
    c=cs.iloc[-1]; ma5=cs.iloc[-5:].mean(); ma20=cs.iloc[-20:].mean(); ma60=cs.iloc[-60:].mean(); ma200=cs.iloc[-200:].mean()
    hi30=cs.iloc[-30:].max(); lo30=cs.iloc[-30:].min()
    days_since_high = len(cs.iloc[-60:]) - int(np.argmax((cs.iloc[-60:]==cs.iloc[-60:].max()).values))
    days_since_low  = len(cs.iloc[-30:]) - int(np.argmin((cs.iloc[-30:]==cs.iloc[-30:].min()).values))
    dd30 = c/hi30-1
    above5_3d = int(all((cs.iloc[-1-i]>cs.iloc[-1-i-4:len(cs)-i].mean()) for i in range(3)))
    r20 = c/cs.iloc[-21]-1 if len(cs)>=21 else 0.0
    f=dict(date=str(d.date()), close=round(c,1), ma5=round(ma5,1), ma20=round(ma20,1), ma60=round(ma60,1),
           ma200=round(ma200,1), hi30=round(hi30,1), lo30=round(lo30,1), dd30=round(dd30*100,1),
           days_since_high=days_since_high, days_since_low=days_since_low, above5_3d=above5_3d,
           r20=round(r20*100,1), pos_in_box=round((c-lo30)/(hi30-lo30)*100,1) if hi30>lo30 else 50.0)
    f['market']=get_market_context(str(d.date()))   # 量能/资金/GJD/情绪
    f['anchors']=get_anchor_context(str(d.date()), f['close'], cs)   # 历史大级别锚点
    f['main_line']=read_main_line()   # 主线判定
    return f

def build_prompt(f):
    m=f.get('market',{}) or {}
    mr = ("当前市场环境(量能/资金/GJD/情绪)："+json.dumps(m, ensure_ascii=False)) if m else "当前市场环境：无法获取(仅价格结构)。"
    a=f.get('anchors',{}) or {}
    ar = ("历史大级别锚点(大级别牛熊分界)："+json.dumps(a, ensure_ascii=False)) if a else ""
    ml=f.get('main_line',{}) or {}
    mlr = ("主线判定(main_line_state)："+json.dumps(ml, ensure_ascii=False)) if ml else ""
    return ('''你是狼大波数级浪型判定 agent。基于上证指数当前结构，按狼大的多级别Elliott框架做浪型判定。狼大核心铁律：小级别服从大级别；结构确认+量价+GJD行为+先预判后确认+千人千浪；低位看逻辑、高位看量价。**特别注意：必须把量能/资金(GJD行为)/情绪纳入判定，不能只看价格均线。**
当前指数结构(点内)：''' + json.dumps(f, ensure_ascii=False) + '''
''' + mr + '''
''' + ar + '''
''' + mlr + '''
【信号权重与组合（按狼大：先定级别位置，量能/资金/GJD/情绪为次级确认）】
核心：先判级别位置(骨架)，再用量能/资金/情绪确认该位置预期走向；位置优先于单维信号。
- 量能(按位置)：
  * 主升中段(3-1/3-3/3-4)：放量突破前高→确认主升(可追build)；缩量回踩→洗盘/健康(持仓，不转防御)；缩量滞涨在**主升中段≠顶**。
  * 上涨末段(3-5/4-5/d5/双头M顶)：缩量滞涨+量价背离→顶部(减仓exit/defense)。
  * 低位(上升浪W底确认/4-3筑底/C杀末)：地量+缩量止跌=下跌动能衰竭→底部线索(可轻仓side/准备)；需放量突破颈线才确认重仓build。
  * 调整/下跌中(4-1/4-2/C杀中段/下跌浪)：放量急杀→仍下跌(防御)；缩量止跌→接近底(观察)。
- 两融杠杆(仅作末段/顶部辅助，不单独触发)：只在**上涨末段+量价背离+破位**时，两融高/融资净卖出才强化顶部(exit/defense)；主升初期/中段即使两融高也只作未来顶预警，**不改变当前主升可建仓**，除非放量跌破MA20/破位。下跌中两融净卖出/去杠杆=风险(防御)；但C杀末地量+去杠杆尾声=底部(side)。
- GJD行为/宽基ETF(510300/510050份额净申赎)：**只做次级确认，绝不改变结构级别**。
  * 结构已定「底部/双底/W底/4-3筑底/C杀末地量」+ 份额近5/20日净申购(份额增)→确认底部(可轻仓side，重仓仍需放量)；**结构仍处「大4浪/下跌/防御」→即使份额净买也不反转，仍防御**(GJD只缓解下跌速度，不反转大级别)。
  * 结构已定「上涨末段(3-5/4-5/d5/双头M顶)」+ 份额净赎回(份额减)→强化顶部(exit/defense)；**主升中段(3-2/3-3/3-4)份额流出只是短线扰动，不改变主升属性，勿因此转t_only/防御**。
  * HS300/上证50相对上证超额=辅助：超额为正+指数跌→宽基托底；蓝筹也大跌(超额为负)、无承接→风险大。
- 情绪/换手：换手低位+地量=情绪冰点(底部/洗盘)；放量+高换手+普涨=情绪过热(顶，需结合上涨末段)。
- 北向：弱参考(2024-08后日频失真)。
- 决策：最终operation由【级别位置】主导，量能/资金/情绪只做确认或证伪——位置往上+信号配合→build；位置末段+信号背离→exit/defense；位置底部+缩量止跌→side/轻仓；位置下跌中→defense。
【狼大完整浪型框架】(大级别 + 子浪，先判大级别再定子浪操作)
大级别 level(取其一)：
- d1 上升浪/反转大1浪(底部确认后建仓)
- d2 调整大2浪(反转后回调，含ABC，不参与坐等企稳走主升)
- d3 主升级别大3浪(最强段，建仓/追/产业链)
- d4 大4浪(3-5后无小子浪直接大4，防御调仓换股，参考21年后主跌段)
- d5 末段/衰竭浪(兑现降仓，防顶)
- down 下跌浪(结构未完成，只确认底不确认主升，空仓防御)
大3内部子浪 sub_level：3-1起步建仓 / 3-2调整(最多约3个月,持仓等回调,短调整不恐) / 3-3最强主升(重仓追) / 3-4中继(波段做T) / 3-5末段(之后直接大4浪,减仓切换)
大4内部子浪 sub_level：4-1回调初(观望降仓) / 4-2(约等于大盘ABC的B反,反弹诱多,只做T不追,等结束规避C) / 4-3筑底(可走4-4但别预期太高,调仓换股分批接主线,轻仓) / 4-4反弹(方向向上但幅度有限,机构调仓+杀杠杆阶段,只按箱体做波动做T,减少操作,不要碰个股分化,指数=沪深300/半导体ETF更安全) / 4-5(常为失败5浪,最后一跌,防御等企稳,失败5之后对应主升)
局部/板块 sub_level：ABC(完整三段A跌B反C杀) / B反(典型诱多别抄底,等B反走完还是C) / C杀(最后杀跌,买在确定而非买在最低,买对方向) / W底(确认建仓) / 双头/M顶(见顶防跌) / 衰竭浪(多次诱多出货,买对主线,否则4浪一起摁下来) / 下跌浪4反弹(确认4必有5,一旦确认先卖)
【操作映射 operation】(严格照狼大取其一)
- build 建仓/追(主升、d3+3-3、d1/上升浪、W底确认)
- t_only 只做T不追不新建主升(4-4、4-2/B反、3-4、ABC期间高位)
- side 观望/调仓换股(4-3筑底、大2、ABC、3-2回调、下跌浪构筑期)
- defense 防御不建仓降仓(4-5/失败5、C杀、4-1、down下跌浪、调整)
- exit 兑现/降仓规避(3-5末段、d5末段衰竭、双头/M顶、下跌浪4反弹先卖)
仅输出一个 JSON 对象(不要markdown、不要多余文字、不要代码块)：
{"level":"d1/d2/d3/d4/d5/down","sub_level":"3-1/3-2/3-3/3-4/3-5/4-1/4-2/4-3/4-4/4-5/失败5/ABC/B反/C杀/W底/双头M顶/衰竭","operation":"build/t_only/side/defense/exit","confidence":0.0,"reasons":"简述(引用结构/量价/点位)"}''')

def call_agent(prompt, session='wave_'):
    r=requests.post(CHAT_URL, json={'message':prompt,'session_id':session+str(int(time.time()))}, headers={'Content-Type':'application/json'}, timeout=180, verify=False)
    r.raise_for_status(); return r.json().get('reply','')

def parse(reply):
    t=reply.strip()
    if t.startswith(BT):
        t=t.split(chr(10),1)[-1] if chr(10) in t else t; t=t.rstrip(chr(96)).strip()
    for cand in (t, t.replace(BT,'').strip()):
        try: return json.loads(cand)
        except Exception: pass
    i=t.find('{'); j=t.rfind('}')
    if i>=0 and j>i:
        for k in range(j, i, -1):
            sub=t[i:k+1]
            try: return json.loads(sub)
            except Exception: pass
    return {'raw':reply,'parse_failed':True}

def main():
    f=index_features()
    if f is None: print('no data'); return
    print('[*] 指数结构:', json.dumps(f, ensure_ascii=False), file=sys.stderr)
    prompt=build_prompt(f)
    reply=call_agent(prompt)
    res=parse(reply)
    res['date']=f['date']; res['features']=f
    # 校验 operation 取值，避免空/异常值污染 gate
    if not res.get('operation'):
        res['operation']='side'
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE,'w',encoding='utf-8') as fp: json.dump(res, fp, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__=='__main__': main()
