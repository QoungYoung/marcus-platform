# -*- coding: utf-8 -*-
"""wolf_t_rules.py — 做T规则向狼大看齐(2026-09-03)
① 正T买点: 标的/板块日内回撤≥2.5% 或 触前低 + 缩量 + 站回黄线(累计VWAP/前日MA5) → 低吸T仓
   纪律: 只做T、保留底仓、目标3-5点、无底仓无T资格
② 倒T卖点: 高位(近5日累计涨幅≥10%或接近前高) + 放量滞涨(冲高回落/缩量滞涨) → 先卖后买
返回 signals + 纪律块, 供 TMonitor/交易agent 使用。
"""
import os, json

def _sort(bars): return sorted(bars, key=lambda x: str(x.get('time') or x.get('trade_time')))

def day_stats(bars):
    bs=_sort(bars)
    if not bs: return None
    return {'high': max(float(b['high']) for b in bs), 'low': min(float(b['low']) for b in bs),
            'close': float(bs[-1]['close']), 'vol': sum(float(b.get('vol') or 0) for b in bs),
            'cum_vwap': (sum(float(b.get('amount') or 0) for b in bs)/sum(float(b.get('vol') or 0) for b in bs)) if sum(float(b.get('vol') or 0) for b in bs)>0 else 0,
            'last': str(bs[-1].get('time') or '')}

def zheng_t_buy(bars, prev_days, cfg=None):
    """正T低吸: 日内回撤≥2.5% 或 触前低 + 缩量(量比≤0.7) + 站回黄线(close≥cumVWAP 且 ≥前1日MA5)。"""
    st=day_stats(bars)
    if not st: return False, 'no_bars'
    if len(prev_days)<1: return False, 'no_prev'
    dd=(st['low']/st['high']-1)*100 if st['high']>0 else 0
    prev_low=min(day_stats(p)['low'] for p in prev_days[:5]) if prev_days else 0
    hit_dd = dd <= -2.5
    hit_prev_low = bool(prev_low and st['low'] <= prev_low*1.005)
    # 缩量: 当日量 vs 前5日均量
    prev_vols=[day_stats(p)['vol'] for p in prev_days[:5]] if prev_days else []
    avg_vol=sum(prev_vols)/len(prev_vols) if prev_vols else 0
    shrink = avg_vol>0 and st['vol'] <= avg_vol*0.9   # 狼大"温和缩量"(非固定0.7)
    prev_closes=[day_stats(p)['close'] for p in prev_days[:5]]
    # 狼大正T=日内/标的-2.5%或触前低 + 温和缩量 → 低吸(买在低点, 不要求收盘回补/不破位)
    amp=(st['high']/st['low']-1)*100 if st['low']>0 else 0
    ok = (hit_dd or hit_prev_low) and shrink and amp>=3.0
    reason='日内回撤%.1f%%%s 缩量vr=%.2f 振幅%.1f%%' % (dd, ('/触前低' if hit_prev_low else ''), (st['vol']/avg_vol if avg_vol else 0), amp)
    return bool(ok), reason

def dao_t_sell(bars, prev_days, cfg=None):
    """倒T卖点(高位缩转放先卖后买): 近5日累计涨幅≥10% 且 当日放量滞涨(冲高回落/收<前高)。"""
    st=day_stats(bars)
    if not st or len(prev_days)<5: return False, 'no_bars'
    pcls=[day_stats(p)['close'] for p in prev_days[:5]]
    start=pcls[0]
    gain=(st['close']/start-1)*100 if start else 0
    prev_high=max(day_stats(p)['high'] for p in prev_days[:5])
    high_pos = gain>=10.0 or st['close']>=prev_high*0.97
    # 放量滞涨: 当日放量(>前5日均量*1.3) 且 收盘<日内最高(冲高回落)
    pvols=[day_stats(p)['vol'] for p in prev_days[:5]]
    avg_vol=sum(pvols)/len(pvols) if pvols else 0
    vol_up = avg_vol>0 and st['vol']>=avg_vol*1.3
    fade = st['close'] < st['high']*0.995
    ok = high_pos and vol_up and fade
    reason='近5日gain=%.1f%% 接近前高=%s 放量=%s 冲高回落=%s' % (gain, st['close']>=prev_high*0.97, vol_up, fade)
    return bool(ok), reason

def zheng_t_buy_quote(quote, prev_days):
    """基于实时 quote(当日 high/low/current/vol) + 前5日日线 做正T:
    日内回撤>=3% 或 触前低 + 温和缩量(vol<=前5日均量*0.9) -> 低吸候选。"""
    try:
        current=float(quote.get('current') or 0); high=float(quote.get('high') or 0)
        low=float(quote.get('low') or 0); vol=float(quote.get('vol') or 0)*100.0   # 腾讯qt vol=手 → 股(与recent_sync单位一致)
    except Exception:
        return False, 'no_quote'
    if high<=0 or current<=0: return False, 'no_quote'
    dd=(low/high-1)*100
    prev_low=min([p.get('low') for p in prev_days if p.get('low')], default=0)
    hit_dd = dd <= -2.5
    hit_prev = bool(prev_low and low <= prev_low*1.005)
    pvols=[p.get('vol') for p in prev_days if p.get('vol')]
    avg_vol=sum(pvols)/len(pvols) if pvols else 0
    shrink = avg_vol>0 and vol <= avg_vol*0.9
    amp=(high/low-1)*100 if low>0 else 0
    ok=(hit_dd or hit_prev) and shrink and amp>=3.0
    return bool(ok), '日内回撤%.1f%%%s 量比%.2f 振幅%.1f%%' % (dd, ('/触前低' if hit_prev else ''), (vol/avg_vol if avg_vol else 0), amp)


def defensive_t_reduce_quote(quote, prev_days, wave_op='t_only'):
    """防御性减T(风险/结构驱动, 狼大08-27/09-01): wave∈只做T/末段 且 (量能不足 或 滞涨(冲高回落)) → 减已持T仓."""
    if wave_op not in ('t_only','side','defense','exit'):
        return False, 'wave allow build'
    try:
        current=float(quote.get('current') or 0); high=float(quote.get('high') or 0)
        vol=float(quote.get('vol') or 0)*100.0
    except Exception:
        return False, 'no_quote'
    pvols=[p.get('vol') for p in prev_days if p.get('vol')]
    avg=sum(pvols)/len(pvols) if pvols else 0
    vol_low = avg>0 and vol <= avg*0.8
    fade = high>0 and current < high*0.99
    ok = vol_low and fade
    return bool(ok), '量能不足(vr=%.2f) 滞涨(收/高=%.2f)' % ((vol/avg if avg else 0), (current/high if high else 0))


def dao_t_sell_quote(quote, prev_days):
    """基于实时 quote 做倒T: 高位(近5日涨幅>=10% 或 接近前高) + 放量滞涨(current<high*0.995)。"""
    try:
        current=float(quote.get('current') or 0); high=float(quote.get('high') or 0)
        vol=float(quote.get('vol') or 0)*100.0   # 手→股
    except Exception:
        return False, 'no_quote'
    if high<=0 or current<=0: return False, 'no_quote'
    pcloses=[p.get('close') for p in prev_days if p.get('close')]
    prev_high=max([p.get('high') for p in prev_days if p.get('high')], default=0)
    gain=(current/prev_days[-1]['close']-1)*100 if prev_days and prev_days[-1].get('close') else 0
    high_pos = gain>=10.0 or (prev_high and current>=prev_high*0.97)
    pvols=[p.get('vol') for p in prev_days if p.get('vol')]
    avg_vol=sum(pvols)/len(pvols) if pvols else 0
    vol_up = avg_vol>0 and vol>=avg_vol*1.3
    fade = current < high*0.995
    ok = high_pos and vol_up and fade
    return bool(ok), '近5日gain=%.1f%% 接近前高=%s 放量=%s 冲高回落=%s' % (gain, (prev_high and current>=prev_high*0.97), vol_up, fade)


def t_sell_confirmed(bars, buy_idx, look=5, vol_dry=0.8, up=1.005):
    """确认制分时T出(只用已知bar, 与外推未来无关): 买后高点 -> 停量(后look均量<高前0.8)
    -> 二次拉升(已知bar)不过前高(<=前高*1.005) -> 在确认bar卖(接受从高点回撤)."""
    if buy_idx is None or buy_idx+look+2 >= len(bars): return None
    closes=[float(b['close']) for b in bars]; vols=[float(b.get('vol') or 0) for b in bars]
    buy=closes[buy_idx]
    if buy<=0: return None
    hi=closes[buy_idx+1]; hi_idx=buy_idx+1
    for j in range(buy_idx+2, len(bars)):
        if closes[j]>hi: hi=closes[j]; hi_idx=j
        if j>=hi_idx+2:
            va=sum(vols[j-look:j+1])/look if j>=look else 0
            vb=sum(vols[hi_idx-look:hi_idx])/look if hi_idx>=look else 0
            dry = vb>0 and va < vb*vol_dry
            not_break = closes[j] < hi*up
            above_buy = closes[j] > buy*1.005
            second_tried = any(closes[k]>=hi*0.98 for k in range(hi_idx+1, j+1))
            if dry and not_break and above_buy and second_tried:
                return {'sell':round(closes[j],3),'sell_time':str(bars[j].get('time') or '')[:16],
                        'hi':round(hi,3),'pnl':round((closes[j]/buy-1)*100,2)}
    return None


def t_cycle_pnl(bars, thr=2.5):
    """做T闭环收益(执行版, 与狼大一致): 正T买@触-{thr}%低吸点 -> 确认制分时T出(停量+二次不过前高, 仅用已知bar).
    未确认→按收盘离场兜底; 返回 {buy, buy_time, sell, sell_time, pnl(确认或收盘), pnl_dayhigh(参考), confirm}."""
    bs=_sort(bars)
    if len(bs)<6: return None
    hi=max(float(b['high']) for b in bs)
    run_low=10**18; buy=None; buy_idx=None
    for i,b in enumerate(bs):
        run_low=min(run_low, float(b['low']))
        if hi>0 and (run_low/hi-1)*100 <= -thr:
            buy=float(b['low']); buy_idx=i; break
    if buy is None or buy<=0: return None
    res=t_sell_confirmed(bs, buy_idx)
    if res:
        return {'buy':round(buy,3),'buy_time':str(bs[buy_idx].get('time') or '')[:16],
                'sell':res['sell'],'sell_time':res['sell_time'],'pnl':res['pnl'],
                'pnl_dayhigh':round((hi/buy-1)*100,2),'confirm':True}
    return {'buy':round(buy,3),'buy_time':str(bs[buy_idx].get('time') or '')[:16],
            'sell':None,'sell_time':None,'pnl':None,
            'pnl_dayhigh':round((hi/buy-1)*100,2),'confirm':False,'note':'未确认→黄线/持有至次日/周五减T仓(非强制日结)'}


def discipline_context():
    """正T/倒T 纪律块. """
    return ("## 做T纪律(向狼大看齐)\n"
            "- 正T买点=标的/板块日内回撤≥2.5% 或 触前低 + 缩量 + 站回黄线(VWAP/前日MA5) → 低吸T仓\n"
            "- 只做T、不追主升；T仓与底仓分离，底仓不卖；无底仓无T资格\n"
            "- T出目标 3-5 点；分时T出(放量→高点→停量→二次不过前高) / 黄线跌破离场\n"
            "- 倒T=高位(近5日涨幅≥10%或接近前高)+放量滞涨(冲高回落)→先卖后买\n")
# -*- coding: utf-8 -*-
def benchmark_index(sym):
    """个股→所属基准指数代码: 30/301/300开头→创业板399006; 688→科创50(000688); 00/002/159→深证成指399001; 否则上证000001."""
    s=str(sym)
    if s.startswith(('30','301','300')): return '399006'
    if s.startswith('688'): return '000688'
    if s.startswith(('00','002','159')): return '399001'
    return '000001'
# -*- coding: utf-8 -*-
def _dh_get(api, **p):
    import requests, urllib3
    urllib3.disable_warnings()
    DH='http://datahubco.com/app-api/openapi/v1/tushare'; K='dba548a206a453c197f9175189b757374fa6db9554bb29e69efea127'
    r=requests.get(f'{DH}/{api}',params=p,headers={'X-API-Key':K},timeout=40)
    d=r.json(); dd=d.get('data') or {}
    return (dd.get('items') or []), (dd.get('fields') or [])

_MEMBERS_CACHE = {}
def _index_members(index_code):
    if index_code in _MEMBERS_CACHE: return _MEMBERS_CACHE[index_code]
    items, fields = _dh_get('index_weight', index_code=index_code)
    if not items or not fields: return []
    fmap={f:i for i,f in enumerate(fields)}
    maxdt=max(str(it[fmap.get('trade_date',2)]) for it in items)
    mem={str(it[fmap.get('con_code',1)]) for it in items if str(it[fmap.get('trade_date',2)])==maxdt}
    _MEMBERS_CACHE[index_code]=mem
    return mem

def resolve_stock_index(sym, idx_pool=None):
    """通过 index_weight(指数成分权重) 反查个股归属指数. 返回 (指数代码, 权重) 或 (None,None).
    idx_pool 默认覆盖主要宽基: 000001/000016/000300/000905/000852/399001/399006/000688."""
    idx_pool = idx_pool or ['000001.SH','000016.SH','000300.SH','000905.SH','000852.SH','399001.SZ','399006.SZ','000688.SH']
    for idx in idx_pool:
        members=_index_members(idx)
        if sym in members:
            return idx, None
    return None, None

def defensive_t_reduce_index(sym, idx_code, sym_hi_now, sym_hi_prev, idx_hi_now, idx_hi_prev, wave_op='t_only'):
    """防御减T(按个股所属指数): 该股归属指数 近高/创新高(>=前5日高*0.998) 且 该股未跟(<阶段高*0.998) → 防御."""
    if wave_op not in ('t_only','side','defense','exit'): return False
    idx_near_high = idx_hi_now is not None and idx_hi_prev and idx_hi_now >= idx_hi_prev*0.998
    sym_lag = sym_hi_now is not None and sym_hi_prev and sym_hi_now < sym_hi_prev*0.998
    return bool(idx_near_high and sym_lag)


_SW_MEMBER_CACHE = {}
def resolve_sw_sector(sym):
    """个股→申万一级行业指数代码(801150.SI 医药生物/801080.SI 电子等). 用 datahubco index_member_all(ts_code) 反查 l1_code.
    泛化: 非科技也适用(药明→医药生物, 华林→非银金融). 缓存避免反复请求."""
    s=str(sym)
    if s in _SW_MEMBER_CACHE: return _SW_MEMBER_CACHE[s]
    ts = s if s.endswith(('.SH','.SZ')) else (s+'.SH' if s.startswith('6') else s+'.SZ')
    items, fields = _dh_get('index_member_all', ts_code=ts)
    if not items or not fields:
        _SW_MEMBER_CACHE[s]=None; return None
    fmap={f:i for i,f in enumerate(fields)}
    l1 = items[0][fmap['l1_code']] if 'l1_code' in fmap else None
    _SW_MEMBER_CACHE[s]=l1
    return l1


_SW_DAILY_CACHE = {}
_SW_DAILY_TS = {}
def _sw_daily_high(idx_code):
    """申万行业指数日线 high 序列 {YYYYMMDD: high}. promax sw_daily, 5分钟缓存."""
    import time
    now=time.time()
    if idx_code in _SW_DAILY_CACHE and now-_SW_DAILY_TS.get(idx_code,0)<300:
        return _SW_DAILY_CACHE[idx_code]
    import requests, urllib3
    urllib3.disable_warnings()
    PM='https://pcd.mobcvb.cn/tushare/pro'; PK='tsr_1FjRkziz3M7m0aLcTk0ZgnK03__xO3EYq0ZdwQqdwSE'
    try:
        from datetime import datetime
        end=datetime.now().strftime('%Y%m%d')
        r=requests.get(f'{PM}/sw_daily',params={'ts_code':idx_code,'start_date':'20250101','end_date':end},headers={'X-API-Key':PK},verify=False,timeout=30)
        dd=r.json().get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
        fmap={f[i]:i for i in range(len(f))} if f else {}
        out={}
        if 'trade_date' in fmap and 'high' in fmap:
            for x in it:
                td=x[fmap['trade_date']]; hi=x[fmap['high']]
                if td is None or hi is None: continue   # 跳过宽窗口下 high=None 行
                out[str(td).replace('-','')]=float(hi)
        _SW_DAILY_CACHE[idx_code]=out; _SW_DAILY_TS[idx_code]=now
        return out
    except Exception:
        return _SW_DAILY_CACHE.get(idx_code) or {}


def defensive_t_reduce_sw(sym, sym_hi_now, sym_hi_prev, wave_op='t_only'):
    """防御减T(个股申万行业级, 泛化非科技): 该股所属申万一级行业指数 近高/创新高(>=前5日高*0.998)
    且 该股未跟(低<阶段高*0.998) → 防御减T. 返回 (ok, reason)."""
    if wave_op not in ('t_only','side','defense','exit'):
        return False, 'wave allow build'
    idx = resolve_sw_sector(sym)
    if not idx: return False, 'no_sw_sector'
    d = _sw_daily_high(idx)
    if not d: return False, 'no_sw_daily'
    keys = sorted(d)
    idx_now = d.get(keys[-1]) if keys else None
    idx_prev = max([d[k] for k in keys[:-1]][-5:]) if len(keys)>1 else None
    sym_lag = sym_hi_now is not None and sym_hi_prev and sym_hi_now < sym_hi_prev*0.998
    idx_near = idx_now is not None and idx_prev and idx_now >= idx_prev*0.998
    ok = bool(idx_near and sym_lag)
    ip = ((idx_now/idx_prev-1)*100 if (idx_now and idx_prev) else 0)
    sp = ((sym_hi_now/sym_hi_prev-1)*100 if (sym_hi_now and sym_hi_prev) else 0)
    return ok, '行业%s 近高=%.3f/%.3f(%+.2f%%) 个股未跟=%.3f/%.3f(%+.2f%%)' % (idx, idx_now or 0, idx_prev or 0, ip, sym_hi_now or 0, sym_hi_prev or 0, sp)
