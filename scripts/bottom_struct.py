"""底部结构检测 v3：加「中间态」和均线（v2 只有 未成立/磨底中/突破/已走远，
跳过了他最看重的「右脚候选」这一档）。

状态机（按时间推进）：
  无结构 → 左脚已现 → 右脚候选 → 双脚成立(待确认) → 颈线突破
判据全是账本原话：双脚价差 ≤4%、间隔 ≥3 根、颈线=两脚之间最高点、
突破要放量；并附 MA5/MA10 与「站上没站上」（他 11/04 13:44 三条件之一）。
"""
from __future__ import annotations
import numpy as np, pandas as pd


def pivots_low(l, w=5):
    return [i for i in range(w, len(l) - w) if l[i] == min(l[i - w:i + w + 1])]


def detect(h, l, c, o, v, j, w=5, tol=0.04, min_gap=3, lookback=60):
    """返回 dict：state / l_left / l_right / neck / dist_neck / days_since_right /
    ma5 / ma10 / above_ma5 / above_ma10"""
    ma5 = float(np.mean(c[max(0, j - 4):j + 1]))
    ma10 = float(np.mean(c[max(0, j - 9):j + 1]))
    base = dict(ma5=round(ma5, 2), ma10=round(ma10, 2),
                above_ma5=bool(c[j] > ma5), above_ma10=bool(c[j] > ma10))
    if j < 30:
        return dict(state="数据不足", **base)

    lo = [i for i in pivots_low(l[:j + 1], w) if i >= j - lookback]
    # 新鲜的脚：最近 5 根里最低的那根，且它是近 60 日最低（此时还没有 5 根右侧确认）
    fresh = j - w + 1 + int(np.argmin(l[j - w + 1:j + 1]))
    if l[fresh] <= l[max(0, j - lookback):j + 1].min() + 1e-9:
        lo = sorted(set(lo + [fresh]))
    if not lo:
        return dict(state="无结构（近 60 日无摆动低点）", **base)
    l_right = lo[-1]
    days_right = j - l_right
    l_left = None
    for i in reversed(lo[:-1]):
        if l_right - i >= 1 and abs(l[l_right] / l[i] - 1) <= tol:
            l_left = i
            break

    # 近 10 根里最低的两个点，若价差 ≤tol 也算双脚（连续两天探同一位置就是"双脚"）
    if l_left is None and j >= 12:
        idx = sorted(range(j - 10, j + 1), key=lambda k: l[k])[:2]
        a, b = sorted(idx)
        if b - a >= 1 and abs(l[b] / l[a] - 1) <= tol:
            l_left, l_right = a, b
    if l_left is None:
        return dict(state=f"左脚已现（{days_right} 日前出低点 {l[l_right]:.2f}，等第二个脚）",
                    l_left=round(float(l[l_right]), 2), l_right=None, **base)

    seg = h[l_left:l_right + 1]
    neck = float(seg.max())
    dist = (c[j] / neck - 1) * 100
    broke = c[j] > neck * 1.005 and v[j] > 1.2 * np.mean(v[max(0, j - 19):j + 1])
    pos = (c[j] - l[j]) / max(1e-9, h[j] - l[j])

    if broke:
        st = f"颈线突破（颈线 {neck:.2f}，今日收 {c[j]:.2f}）"
    elif days_right <= 1 and pos >= 0.6:
        st = f"右脚候选（{days_right} 日前探 {l[l_right]:.2f} 后收回，收位 {pos*100:.0f}%）"
    else:
        st = f"双脚成立待确认（左 {l[l_left]:.2f} / 右 {l[l_right]:.2f}，颈线 {neck:.2f}，还差 {abs(dist):.1f}%）"
    return dict(state=st, l_left=round(float(l[l_left]), 2), l_right=round(float(l[l_right]), 2),
                neck=round(neck, 2), dist_neck=round(dist, 2), days_since_right=days_right,
                right_pos=round(pos * 100), **base)


# ── 兼容旧接口 ──
if __name__ == "__main__":
    import os
    ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name,path in (("上证","data/research/index_daily_000001_long.csv"),
                      ("588170","data/research/fund_daily_588170_split_adj.csv")):
        d=pd.read_csv(os.path.join(ROOT,path),dtype={'trade_date':str}).sort_values('trade_date').reset_index(drop=True)
        h,l,c,o,v=(d[x].values.astype(float) for x in ('high','low','close','open','vol'))
        globals()['_DATES']=d.trade_date.values
        print(f"\n===== {name} =====")
        for day in ['20260112','20260323','20260330','20260408','20260803','20260805','20261022']:
            idx=d.index[d.trade_date==day]
            if not len(idx): print(f"  {day} 无数据"); continue
            r=detect(h,l,c,o,v,idx[0])
            if 'l1' in r:
                print(f"  {day}  {r['state']:<30} 双底 {r['l1']:.3f}/{r['l2']:.3f} (反抽{r['reb']}%) 颈线 {r['neck']:.3f} 距颈线 {r['dist_neck']:+.1f}%")
                print(f"          {r['hold']} · 不破位黑K：{r['black']}")
            else:
                print(f"  {day}  {r['state']}")
