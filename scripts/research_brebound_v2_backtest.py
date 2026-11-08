"""B反 框架 v2 回测：五步链条 = 信号日 → 确认窗口 → 执行（时序，不是同日打分）。

门槛（全部 as-of）：
  1 结构终结：延伸浪（≥3 个下移 pivot low）+ 终结迹象（降速 / 底分型 / 放量承接）之一
  2 有人接（非纯自救）：信号后 1~3 日量能不全枯（均值 ≥0.9× 信号日）
  3 指数起来：确认日指数收盘 > MA5 或 当日涨 ≥+0.5%
  4 题材破位：确认日其它板块 ≥60% 在 MA20 下方（资金被迫回主线）

执行：在信号后 [1,3] 日内第一个满足「所需门槛」的日子收盘买入；
结果：目标=最近可达前低/60日高（空间≥6%）；失效=B起点低× (1-clamp(1.5ATR,3%,6%))；
      30 周内路径，破失效按收盘价成交，未决按期末平。
"""
from __future__ import annotations
import glob, json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_brebound_multietf as M

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "research")

def pivots_low(low, i, w=2):
    return [j for j in range(w, i - 1) if low[j] == min(low[j-w:j+w+1])]

def threshold1(f, i):
    lo, hi, c, v = f.low.values, f.high.values, f.close.values, f.vol.values
    j0 = max(1, i-59); k = int(np.argmax(hi[j0:i+1])) + j0
    legs = [p for p in pivots_low(lo, i) if p > k]
    if len(legs) < 3: return False
    down = sum(1 for a, b in zip(legs, legs[1:]) if lo[b] < lo[a])
    if down < 2: return False
    if c[i]/hi[k]-1 > -0.15: return False          # 必须是一段像样的下跌
    if c[i]/lo[legs[-1]]-1 > 0.05: return False    # 必须贴着最后一个低点
    # 终结迹象
    slow = (i >= 10) and abs(c[i]/c[i-5]-1) < abs(c[i-5]/c[i-10]-1)*0.7
    frac = (i >= 2) and lo[i-1] < lo[i-2] and lo[i-1] < lo[i] and \
           (c[i-1]-lo[i-1])/max(1e-9, hi[i-1]-lo[i-1]) >= 0.5
    absorb = (c[i]-lo[i])/max(1e-9, hi[i]-lo[i]) >= 0.6 and v[i] >= 1.5*np.nanmean(v[i-19:i+1])
    return bool(slow or frac or absorb)

def main():
    ws = M.load_ws()
    codes = sorted(os.path.basename(p)[:-4].replace('_', '.') for p in glob.glob(os.path.join(OUT, 'etf_pool', '*.csv')))
    frames = [f for f in (M.prep(c, ws) for c in codes) if f is not None and len(f)]
    for f in frames:
        c, h, l = f.close.values, f.high.values, f.low.values
        tr = np.maximum(h-l, np.maximum(abs(h-np.roll(c, 1)), abs(l-np.roll(c, 1))))
        f['atr'] = pd.Series(tr).rolling(20).mean().values

    # 指数
    idx = pd.read_csv(os.path.join(OUT, 'index_daily_000001_long.csv'), dtype={'trade_date': str}).sort_values('trade_date')
    idx = idx.reset_index(drop=True)
    idx['ma5'] = idx.close.rolling(5).mean()
    idx['pct'] = idx.close.pct_change()*100
    IM = idx.set_index('trade_date')

    # 其它板块
    other = {}
    for p in glob.glob(os.path.join(OUT, 'sector_pool', '*.csv')):
        d = pd.read_csv(p, dtype={'trade_date': str}).sort_values('trade_date')
        d['ma20'] = d.close.rolling(20).mean()
        other[os.path.basename(p)[:-4]] = d.set_index('trade_date')['close']/d.set_index('trade_date')['ma20']-1

    rows = []
    for f in frames:
        c, h, l, v = f.close.values, f.high.values, f.low.values, f.vol.values
        dates = f.trade_date.values
        for i in range(120, len(f)-40):
            if np.isnan(f.atr.values[i]) or not threshold1(f, i): continue
            sig = dates[i]
            if sig not in IM.index: continue
            lo20 = f.lo20.values[i]
            tg = None
            for x in (f.piv_lo.values[i], f.hi60.values[i]):
                if x is not None and not np.isnan(x) and x/c[i]-1 >= 0.06: tg = x; break
            if tg is None: continue
            buf = min(max(1.5*f.atr.values[i]/c[i], 0.03), 0.06)
            inv = lo20*(1-buf)
            if inv >= c[i]: continue
            # 确认窗口 [i+1, i+3]
            pick = {}
            for j in range(i+1, min(i+4, len(f))):
                vol_ok = np.nanmean(v[i+1:j+1]) >= 0.9*v[i]
                dj = dates[j]
                if dj not in IM.index: continue
                ir = IM.loc[dj]
                idx_ok = (ir.close > ir.ma5) or (ir.pct >= 0.5)
                vals = [s.get(dj) for s in other.values()]
                vals = [x for x in vals if x is not None and not np.isnan(x)]
                sec_ok = (np.mean([x < 0 for x in vals]) >= 0.6) if vals else False
                pick.setdefault('first', j)
                if vol_ok and 'v2' not in pick: pick['v2'] = j
                if vol_ok and idx_ok and 'v3' not in pick: pick['v3'] = j
                if vol_ok and idx_ok and sec_ok and 'v4' not in pick: pick['v4'] = j
            for name, j in pick.items():
                e = c[j]; last = min(j+30, len(f)-1); ret = None
                for k2 in range(j+1, last+1):
                    if c[k2] < inv: ret = (c[k2]/e-1)*100; break
                    if h[k2] >= tg: ret = (tg/e-1)*100; break
                if ret is None: ret = (c[last]/e-1)*100
                rows.append(dict(code=f.code.iloc[0], sig=sig, date=dates[j], variant=name,
                                 ret=ret, delay=j-i, sig_px=c[i], entry=e,
                                 slip=(e/c[i]-1)*100, rr=(tg-e)/(e-inv)))
    D = pd.DataFrame(rows)
    D.to_csv(os.path.join(OUT, 'b_rebound_v2_chain.csv'), index=False, encoding='utf-8-sig')

    def bb(x, B=4000, seed=5):
        x = np.asarray(x, float); n = len(x)
        if n < 10: return (np.nan, np.nan)
        blk = min(30, max(1, n//3)); rng = np.random.default_rng(seed); o = []
        for _ in range(B):
            st = rng.integers(0, max(1, n-blk+1), int(np.ceil(n/blk)))
            o.append(x[np.concatenate([np.arange(k, min(k+blk, n)) for k in st])[:n]].mean())
        return np.percentile(o, [2.5, 97.5])

    NAMES = {'first': 'V1 仅门槛1（信号次日入场）', 'v2': 'V2 +门槛2 有人接',
             'v3': 'V3 +门槛3 指数起来', 'v4': 'V4 +门槛4 题材破位（全链）'}
    print(f"信号日 {D[D.variant=='first'].sig.nunique()} 个，标的 {D.code.nunique()} 只\n")
    print(f"{'变体':<30}{'事件':>5}{'独立日':>6}{'均值':>8}{'CI':>17}{'胜率':>6}{'R:R':>6}{'延迟':>5}{'滑点':>7}")
    for k in ('first', 'v2', 'v3', 'v4'):
        s = D[D.variant == k]
        if not len(s): print(f"{NAMES[k]:<30} 无样本"); continue
        e = s.drop_duplicates('date')
        ci = bb(e.ret.values)
        print(f"{NAMES[k]:<30}{len(s):>5}{len(e):>6}{s.ret.mean():>+8.2f}"
              f"{ci[0]:>+8.1f}~{ci[1]:>+7.1f}{(s.ret>0).mean()*100:>5.0f}%"
              f"{s.rr.median():>6.2f}{s.delay.mean():>5.1f}{s.slip.mean():>+6.2f}%")

if __name__ == '__main__':
    main()
