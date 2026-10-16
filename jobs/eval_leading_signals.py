# -*- coding: utf-8 -*-
'''领先指标功效检验 v2：两融退潮 / 缩量 / 指数未破位（先声明信号 ✓）
信号腿：L1 融资余额 20 日变化率 < 0 | L2 沪市成交额 20 日均值变化率 < 0（缩量）| L3 沪指 > MA60（未破位）
组合：L1∧L3、L2∧L3、L1∧L2∧L3 三条分别测 ✓ → 看哪条真带信息
度量：5/10/20 日收益、20 日最大回撤、P(10 日内单日 <=-2%)；安慰剂 = 平移 ±10/±20 日
'''
import json, random, statistics as st, sys
sys.path.insert(0, 'core'); sys.path.insert(0, 'apps/main_line')
from tushare_relay import relay_query
import wave_ladder as W

rows = W.load_rows()
cn = {r[0]: r[1] for r in rows}
amt = {r[0]: r[3] for r in rows}
dates = sorted(cn)
idx = {d: i for i, d in enumerate(dates)}

df = relay_query('margin')
mg = {}
for _, r in df.iterrows():
    d = str(r.get('trade_date') or '').replace('-', '')
    if len(d) == 8:
        try:
            mg[d] = float(r.get('rzye'))
        except Exception:
            pass
md = sorted(d for d in mg if d in cn)
L1 = {md[i]: (mg[md[i]] / mg[md[i-20]] - 1) < 0 for i in range(20, len(md))}
ma60 = {dates[i]: st.mean(cn[x] for x in dates[i-59:i+1]) for i in range(59, len(dates))}
L3 = {d: cn[d] > ma60[d] for d in ma60}
a20 = {}
for i in range(20, len(dates)):
    a20[dates[i]] = st.mean(amt[dates[k]] for k in range(i-20, i)) / st.mean(amt[dates[k]] for k in range(i-40, i-20)) - 1 < 0 if i >= 40 else None
L2 = {d: v for d, v in a20.items() if v is not None}
print('两融可用 %d 天 | 成交额可用 %d 天 | MA60 可用 %d 天' % (len(L1), len(L2), len(L3)))

def combo(legs):
    return [d for d in dates if all(L.get(d) for L in legs)]

sig1 = combo([L1, L3]); sig2 = combo([L2, L3]); sig3 = combo([L1, L2, L3])
for nm, s in (('L1两融退潮∧L3未破位', sig1), ('L2缩量∧L3未破位', sig2), ('L1∧L2∧L3', sig3)):
    print('%-22s 信号日 %4d（占 %.1f%%）最近: %s' % (nm, len(s), 100.0*len(s)/len(dates), (s[-5:] if s else '-')))

def fwd(day_list):
    r5, r10, r20, dd, tail = [], [], [], [], []
    for d in day_list:
        i = idx.get(d)
        if i is None or i + 20 >= len(dates):
            continue
        r5.append(cn[dates[i+5]]/cn[d]-1); r10.append(cn[dates[i+10]]/cn[d]-1); r20.append(cn[dates[i+20]]/cn[d]-1)
        seg = [cn[dates[k]] for k in range(i, i+21)]
        dd.append(min(x/cn[d]-1 for x in seg))
        tail.append(1.0 if any(cn[dates[k]]/cn[dates[k-1]]-1 <= -0.02 for k in range(i+1, min(i+11, len(dates)))) else 0.0)
    return r5, r10, r20, dd, tail

def show(tag, s):
    r5, r10, r20, dd, tail = fwd(s)
    if not r5:
        print('  %-22s 无样本' % tag); return None
    print('  %-22s n=%-4d 5日 %+.2f%% | 10日 %+.2f%% | 20日 %+.2f%% | 回撤 %+.2f%% | P(10日内<=-2%%) %.0f%%' % (
        tag, len(r5), 100*st.mean(r5), 100*st.mean(r10), 100*st.mean(r20), 100*st.mean(dd), 100*st.mean(tail)))
    return (st.mean(r5), st.mean(r20), st.mean(dd), st.mean(tail))

print('')
print('=== 信号 vs 基线（全样本）===')
show('全样本基线', dates[60:-20])
for nm, s in (('L1∧L3', sig1), ('L2∧L3', sig2), ('L1∧L2∧L3', sig3)):
    show(nm, s)

print('')
print('=== 安慰剂（平移 ±10/±20 交易日，20 次重抽；看 20 日收益与回撤）===')
random.seed(9)
for nm, s in (('L1∧L3', sig1), ('L2∧L3', sig2), ('L1∧L2∧L3', sig3)):
    for sh in (10, 20):
        r20s, dds = [], []
        for _ in range(20):
            k = random.choice([-1, 1])*sh
            moved = [dates[idx[d]+k] for d in s if d in idx and 0 <= idx[d]+k < len(dates)]
            r5, r10, r20, dd, tail = fwd(moved)
            if r20:
                r20s.append(st.mean(r20)); dds.append(st.mean(dd))
        if r20s:
            print('  %-10s ±%2d: 20日收益 %.2f%%~%.2f%%（中位 %.2f%%）｜回撤中位 %.2f%%' % (
                nm, sh, 100*min(r20s), 100*max(r20s), 100*st.median(r20s), 100*st.median(dds)))
