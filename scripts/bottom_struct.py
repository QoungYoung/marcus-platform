"""底部结构检测 v3：加「中间态」和均线（v2 只有 未成立/磨底中/突破/已走远，
跳过了他最看重的「右脚候选」这一档）。

状态机（按时间推进）：
  无结构 → 左脚已现 → 右脚候选 → 双脚成立(待确认) → 颈线突破
判据全是账本原话：**双脚价差 ≤2%**、间隔 ≥1 根、颈线=两脚之间最高点、
突破要放量；并附 MA5/MA10 与「站上没站上」（他 11/04 13:44 三条件之一）。
"""
from __future__ import annotations
import numpy as np, pandas as pd


def pivots_low(l, w=5):
    return [i for i in range(w, len(l) - w) if l[i] == min(l[i - w:i + w + 1])]


def detect(h, l, c, o, v, j, w=5, tol=0.02, min_gap=3, lookback=60):
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

    # ★ 2026-11-04 修（用户：为什么还是"待确认"）：**结构成立 ≠ 颈线突破**。
    #   他的原话：「昨天就是右脚，今天才是**三根K线出结构**」
    #   ⇒ 右脚之后出现底分型（中间那根高低点都最低）就是「结构成立」；
    #     颈线突破是他之后才谈的确认，不该卡住"成立"这一步。
    # ★ 2026-11-04 修（用户：10-30 也出双脚了？）：
    #   10-30 低 3595.26 只是**当天的新低**（收位 5.8%，几乎收最低），
    #   却被当成"右脚"和 10-23 的 3574.27 配成双底 ⇒ 误报（其后 11-02/11-03 继续新低 ✗）。
    #   规则：**当天才出的低点，只有收回上半区（收位 ≥60%）才配叫"右脚候选"**；
    #   收在下方的是"又下一个新低"，右脚根本没成立，不能配对。
    _fresh = (l_right == j)
    if _fresh and pos < 0.6:
        return dict(state=(f"左脚已现·今日又探低收低（低 {l[l_right]:.2f}，收位 {pos*100:.0f}%）"
                           f"⇒ 不是右脚，等真正收回的脚"),
                    l_left=round(float(l[l_right]), 2), l_right=None, **base)
    _dk = (l_right >= 1 and l_right + 1 <= j
           and l[l_right] <= l[l_right - 1] and l[l_right] <= l[l_right + 1]
           and h[l_right] <= h[l_right - 1] and h[l_right] <= h[l_right + 1])
    # ── 量能特征（他 2025-03-19「上穿带量突破 → 缩量回踩均线 → 继续带量上涨」）──
    #   记录两脚与突破日的量比，用来描述"抛压有没有衰减"，**不硬判**（他没给量化门槛）
    _v20 = float(np.mean(v[max(0, j - 19):j + 1])) or 1.0
    vol_left = round(float(v[l_left]) / _v20, 2)
    vol_right = round(float(v[l_right]) / _v20, 2)
    vol_today = round(float(v[j]) / _v20, 2)

    if broke:
        st = f"颈线突破（颈线 {neck:.2f}，今日收 {c[j]:.2f}）"
    elif _dk:
        st = (f"双脚成立·三根K线出结构（左 {l[l_left]:.2f} / 右 {l[l_right]:.2f}；"
              f"颈线 {neck:.2f} 待突破，还差 {abs(dist):.1f}%）")
    elif days_right <= 1 and pos >= 0.6:
        st = f"右脚候选（{days_right} 日前探 {l[l_right]:.2f} 后收回，收位 {pos*100:.0f}%）"
    else:
        st = (f"双脚已现·等底分型（左 {l[l_left]:.2f} / 右 {l[l_right]:.2f}；"
              f"需右脚步之后再收出一根，且该根高低点都不高于它）")
    # ★ 他 2025-03-19「上穿带量突破均线 → **缩量回踩均线** → 继续带量上涨」：
    #   放量突破颈线之后，若今日缩量且仍站在颈线上方 ⇒ 这是"回踩确认"这一步（比"待突破"更靠后）
    _break_idx = None
    for k in range(l_right + 1, j + 1):
        if c[k] > neck * 1.005 and v[k] > 1.2 * float(np.mean(v[max(0, k - 19):k + 1])):
            _break_idx = k
            break
    if _break_idx is not None and c[j] > neck and \
            v[j] < 0.8 * float(np.mean(v[max(0, j - 19):j + 1]) or 1.0):
        st = (f"放量突破后缩量回踩（{j - _break_idx} 日前放量过颈线 {neck:.2f}，"
              f"今日缩量 {round(float(v[j]) / (float(np.mean(v[max(0, j - 19):j + 1])) or 1.0), 2)}× 仍在其上）")
    return dict(state=st, l_left=round(float(l[l_left]), 2), l_right=round(float(l[l_right]), 2),
                neck=round(neck, 2), dist_neck=round(dist, 2), days_since_right=days_right,
                right_pos=round(pos * 100),
                vol_left=vol_left, vol_right=vol_right, vol_today=vol_today,
                vol_right_vs_left=(round(vol_right / vol_left, 2) if vol_left else None),
                vol_note="两脚与今日的量比（各自 ÷ 20 日均量）；他 2025-03-19「带量突破 → 缩量回踩 → 带量上涨」",
                **base)


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
