# -*- coding: utf-8 -*-
import os, json
D='/app/data/recent_sync'
def bars(code):
    p=os.path.join(D,code+'.json')
    try: d=json.load(open(p,encoding='utf-8'))
    except: return []
    return sorted(d.get('20260903',[]), key=lambda x:str(x.get('time') or x.get('trade_time')))
def t_sell_confirmed(bars, buy_idx, look=5, vol_dry=0.8, up=1.005):
    """确认制分时T出(只用已发生的bar): 买后高点→停量(后look均量<高点前0.8)→当前close未破前高*1.005 且 高于买点 → 卖在当前bar."""
    if buy_idx is None or buy_idx+look+2 >= len(bars): return None
    closes=[float(b['close']) for b in bars]; vols=[float(b.get('vol') or 0) for b in bars]
    buy=closes[buy_idx]
    # 找到买后第一高点(仅用过去)
    hi=closes[buy_idx+1]; hi_idx=buy_idx+1
    for j in range(buy_idx+2, len(bars)):
        if closes[j]>hi: hi=closes[j]; hi_idx=j
        # 判断: 距高点>=2bar, 高后均量<高前均量*0.8, 当前收<前高*1.005 且 当前收>buy
        if j>=hi_idx+2:
            va=sum(vols[j-look:j+1])/look if j>=look else 0
            vb=sum(vols[hi_idx-look:hi_idx])/look if hi_idx>=look else 0
            dry = vb>0 and va < vb*vol_dry
            not_break = closes[j] < hi*up
            above_buy = closes[j] > buy*1.005
            # 二次拉升已试过且未过前高(确认) → 卖
            second_tried = any(closes[k]>=hi*0.98 for k in range(hi_idx+1, j+1))
            if dry and not_break and above_buy and second_tried:
                return {'sell':round(closes[j],3),'sell_time':str(bars[j].get('time') or '')[:16],'hi':round(hi,3),
                        'pnl':round((closes[j]/buy-1)*100,2),'hi_pnl':round((hi/buy-1)*100,2),'pullback':round((closes[j]/hi-1)*100,2)}
    return None
# 买点 = 触发 -2.5% 低吸(仍可用未来找低点? 买点本身在盘中触发时就知道; 用触发bar)
def buy_idx(bars, thr=2.5):
    hi=max(float(b['high']) for b in bars)
    run=10**18
    for i,b in enumerate(bars):
        run=min(run,float(b['low']))
        if hi>0 and (run/hi-1)*100<=-thr: return i
    return None
for c in ['603259','159516','588170']:
    bs=bars(c)
    bi=buy_idx(bs)
    r=t_sell_confirmed(bs, bi)
    buy=float(bs[bi]['low']) if bi is not None else None
    print(c, 'buy@', round(buy,3) if buy else None, '->', r)
