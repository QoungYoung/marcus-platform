# -*- coding: utf-8 -*-
# 浪型级别判定: 用大盘指数(点内)结构(均线/高低点/突破/回撤/动量) -> 主升/反弹B反/调整
import os, json, pandas as pd, numpy as np

def _load_sh():
    csv_p = 'data/指数数据/index_daily/000001.SH.csv'
    parq_p = 'data/指数数据/index_daily/000001.SH.parquet'
    if os.path.exists(csv_p):
        df = pd.read_csv(csv_p, parse_dates=['trade_date'])
        return pd.Series(df['close'].values, index=pd.DatetimeIndex(df['trade_date']))
    return pd.read_parquet(parq_p)['close']

def features(close, date):
    d = pd.Timestamp(date)
    cs = close[close.index <= d].dropna()
    if len(cs) < 260:
        return None
    c = cs.iloc[-1]
    ma20 = cs.iloc[-20:].mean(); ma60 = cs.iloc[-60:].mean(); ma200 = cs.iloc[-200:].mean()
    trend = int(c > ma20 > ma60)
    r20 = c / cs.iloc[-21] - 1 if len(cs) >= 21 else 0.0
    r60 = c / cs.iloc[-61] - 1 if len(cs) >= 61 else 0.0
    prior_high = cs.iloc[-60:-20].max() if len(cs) >= 60 else cs.max()
    breakout = int(c >= prior_high)
    near_high = int(c >= prior_high * 0.97)
    high60 = cs.iloc[-60:].max() if len(cs) >= 60 else c
    dd = c / high60 - 1
    low20 = cs.iloc[-20:].min(); low_prior = cs.iloc[-60:-20].min() if len(cs) >= 60 else low20
    higher_low = int(low20 > low_prior)
    # 低点是否在上升(近20低 > 前60低)
    lower_high = int(cs.iloc[-20:].max() < cs.iloc[-60:-20].max()) if len(cs) >= 60 else 0
    return dict(c=c, trend=trend, r20=r20, r60=r60, breakout=breakout, near_high=near_high,
                dd=dd, higher_low=higher_low, lower_high=lower_high, close=c)

def classify(f):
    if f is None:
        return '无数据'
    if f['trend'] and (f['breakout'] or f['near_high']) and f['higher_low'] and f['r20'] > 0:
        return '主升浪'
    # 反弹B反: 弱势但回踩后低点抬高/站回MA20/跌幅趋缓
    if (not f['trend']) and f['higher_low'] and (f['close'] > f['c']*0) and (f['r60'] < 0) and (f['r20'] > 0):
        return '反弹(B反)'
    if f['close'] < f['c']*0 and f['r20'] < 0 and f['dd'] < -0.10:
        return '调整(下杀)'
    if f['trend']:
        return '主升(待确认)'
    # 默认
    return '震荡/待明确'

def judge_wave(date):
    if not os.path.exists('data/指数数据/index_daily/000001.SH.parquet'):
        return '无数据'
    close = _load_sh()
    f = features(close, date)
    return classify(f), f

def read_wave_context(date=None):
    """生成注入交易 prompt 的浪型级别上下文块。"""
    if date is None:
        close = _load_sh()
        date = str(close.index[-1].date())
    lbl, f = judge_wave(date)
    if f is None:
        return ''
    NL = chr(10)
    return ('## 浪型级别（wave_level）' + NL
            + '- 当前浪型级别：' + lbl + NL
            + '- 指数结构：趋势=' + str(f['trend']) + ' r20=' + str(round(f['r20']*100,1)) + '% r60=' + str(round(f['r60']*100,1)) + '% 距高点=' + str(round(f['dd']*100,1)) + '% 低点抬高=' + str(f['higher_low']) + ' 突破前高=' + str(f['breakout']) + NL
            + '- 操作：主升浪→可持仓波段/产业链建仓；反弹(B反)/调整→只做T、不追主升、降仓。' + NL + NL)


if __name__ == '__main__':
    import sys, time
    tests = ['2022-01-25','2022-06-10','2025-02-06','2025-07-11','2026-01-05','2026-02-26','2026-04-15','2026-06-26']
    for d in tests:
        lbl, f = judge_wave(d)
        dd = round(f['dd']*100,1) if f else '?'
        print(d, '->', lbl, f' | trend={f["trend"] if f else "?"} r20={round(f["r20"]*100,1) if f else "?"}% dd={dd}% higher_low={f["higher_low"] if f else "?"} breakout={f["breakout"] if f else "?"}')
