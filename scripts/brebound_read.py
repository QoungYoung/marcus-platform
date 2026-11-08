"""B反 四维读数（人类读法，非打分）。

给定日期，用我们自己的数据输出：
  级别 / 结构 / 谁在买 / 够不够得着 + 一句判断 + 一句反证
不做评分、不做阈值触发。

用法: python scripts/brebound_read.py 20260803 20261019 20260203
"""
from __future__ import annotations
import os, sys, json, glob
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bottom_struct import detect as detect_bottom

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "data", "research")
IDX_SWING, ETF_SWING = 0.06, 0.10   # 结构参数：多少算"一段"（人眼画线尺度，未优化）


WS_DIR = os.path.join(ROOT, "data", "wave_state_history")

def wave_snapshot(day):
    """读当天的浪型快照（as-of），没有就取之前最近的一份。"""
    files = sorted(glob.glob(os.path.join(WS_DIR, "*.json")))
    cand = [f for f in files if os.path.basename(f)[:8] <= day]
    if not cand: return None, None
    f = cand[-1]
    try: j = json.load(open(f))
    except Exception: return None, None
    return j, os.path.basename(f)[:8]

def wave_line(day):
    j, used = wave_snapshot(day)
    if not j: return ["级别｜（无浪型快照）"]
    ft = j.get("features", {}) or {}
    rs = (j.get("reasons") or "").split("；")[0][:60]
    out = [f"级别｜{j.get('level')} / {j.get('sub_level')} · operation={j.get('operation')} · conf {j.get('confidence')}"
           f"  （快照 {used}）"]
    if rs: out.append(f"      浪型链：{rs}")
    if ft.get("wave1_top"):
        out.append(f"      结构点：origin {ft.get('origin'):.2f}({ft.get('origin_date')}) · "
                   f"1浪顶 {ft.get('wave1_top'):.2f}({ft.get('wave1_top_date')}) · "
                   f"前高 {ft.get('prior_peak'):.2f}({ft.get('prior_peak_date')})")
    legs = ft.get("sub_legs") or []
    # ── 自洽检查：陈旧腿 / 1浪顶==前高 / 腿太少 ──
    bad = []
    if legs:
        last = str(legs[-1].get("date"))
        try:
            import datetime as _dt
            d1 = _dt.date(int(last[:4]), int(last[4:6]), int(last[6:8]))
            d0 = _dt.date(int(used[:4]), int(used[4:6]), int(used[6:8]))
            if (d0 - d1).days > 400: bad.append(f"最后一腿是 {last}（距今 {(d0-d1).days} 天，陈旧）")
        except Exception: pass
    if ft.get("wave1_top") and ft.get("prior_peak") and abs(ft["wave1_top"]-ft["prior_peak"]) < 0.5:
        bad.append(f"1浪顶与『前高』同为 {ft['wave1_top']:.0f}（自相矛盾）")
    if len(legs) < 2: bad.append("腿少于 2 条")
    out.append("      " + ("❌ **快照不可用**：" + "；".join(bad) + " ⇒ 只信下面的价格摆动" if bad else "✅ 快照自洽"))
    if legs:
        out.append("      腿：" + " · ".join(
            f"{x.get('label')}{x.get('kind','')[:1]} {x.get('value'):.2f}({str(x.get('date'))[4:]})" for x in legs))
    # 漂移提示：与最新快照对比 1 浪顶
    latest = sorted(glob.glob(os.path.join(WS_DIR, "*.json")))[-1]
    try:
        lj = json.load(open(latest)); lw = (lj.get("features") or {}).get("wave1_top")
        if lw and ft.get("wave1_top") and abs(lw - ft["wave1_top"]) > 1:
            out.append(f"      ⚠ 漂移：最新快照({os.path.basename(latest)[:8]})的 1浪顶 = {lw:.2f}，与当日不同")
    except Exception: pass
    return out

def zigzag(high, low, pct):
    """返回 [(i, price, 'H'/'L')]，只含确认的转折点。"""
    piv = []
    if len(high) < 3: return piv
    up = high[0] >= low[0]
    ext_i, ext_p = 0, (high[0] if up else low[0])
    for i in range(1, len(high)):
        if up:
            if high[i] >= ext_p: ext_i, ext_p = i, high[i]
            elif low[i] <= ext_p*(1-pct):
                piv.append((ext_i, ext_p, 'H')); up = False; ext_i, ext_p = i, low[i]
        else:
            if low[i] <= ext_p: ext_i, ext_p = i, low[i]
            elif high[i] >= ext_p*(1+pct):
                piv.append((ext_i, ext_p, 'L')); up = True; ext_i, ext_p = i, high[i]
    piv.append((ext_i, ext_p, 'H' if up else 'L'))
    return piv

def load_etf(code="588170.SH"):
    p = os.path.join(D, "fund_daily_588170_split_adj.csv")
    d = pd.read_csv(p, dtype={"trade_date": str}).sort_values("trade_date").reset_index(drop=True)
    return d

def load_idx():
    d = pd.read_csv(os.path.join(D, "index_daily_000001_long.csv"), dtype={"trade_date": str})
    return d.sort_values("trade_date").reset_index(drop=True)

def load_sz():
    p = os.path.join(D, "index_daily_399001_SZ.csv")
    if not os.path.exists(p): return None
    d = pd.read_csv(p, dtype={"trade_date": str}).sort_values("trade_date")
    return d.set_index("trade_date")["amount"]/1e5


MINS = os.path.join(D, "mins")

def mins_tail(day, code="588170.SH"):
    """尾盘量能：两个口径——
       ① 盘中尾段 14:30–14:50 占「14:50 前已成交」的比例（14:50 时已知）
       ② 收盘尾段 14:50–15:00 占全天比例（收盘后才知道，含集合竞价）
       另附同刻量比（14:50 累计量 / 前 20 日同刻均值），实盘用这个量比。"""
    p = os.path.join(MINS, f"{code.replace('.','_')}_{day}.json")
    if not os.path.exists(p): return None
    try: df = pd.DataFrame(json.load(open(p)))
    except Exception: return None
    if not len(df): return None
    t = df.trade_time.str[11:16]; v = df.vol
    tot = v.sum()
    if tot <= 0: return None
    pre = v[t <= "14:50"].sum(); mid = v[(t >= "14:30") & (t <= "14:50")].sum()
    close_tail = v[t > "14:50"].sum()
    return dict(mid_share=mid/pre*100 if pre else np.nan,
                close_share=close_tail/tot*100, pre_share=pre/tot*100,
                same_time_vr=None)

def buyer_shape(E, I, i):
    """谁在买：市场额比 × 标量比 × 收位 × 次日延续"""
    seg = E.iloc[max(0, i-19):i+1]
    vr = E.vol.iloc[i]/seg.vol.mean()
    pos = (E.close.iloc[i]-E.low.iloc[i])/max(1e-9, E.high.iloc[i]-E.low.iloc[i])
    j = I.index[I.trade_date == E.trade_date.iloc[i]]
    if not len(j): return None
    j = j[0]
    amt = I.amount.iloc[j]/1e5; a20 = I.amount.iloc[max(0, j-19):j+1].mean()/1e5
    ar = amt/a20
    sz = load_sz()
    total = amt + (sz.loc[E.trade_date.iloc[i]] if (sz is not None and E.trade_date.iloc[i] in sz.index) else np.nan)
    tot20 = (I.amount.iloc[max(0,j-19):j+1].mean()/1e5) + \
            ((sz.iloc[max(0,j-19):j+1].mean()) if sz is not None else 0)
    # ── 两日状态机：只用「昨日 + 今日」，收盘前即可判断 ──
    def shape(k):
        v = E.vol.iloc[k]/E.vol.iloc[max(0,k-19):k+1].mean()
        pz = (E.close.iloc[k]-E.low.iloc[k])/max(1e-9, E.high.iloc[k]-E.low.iloc[k])
        return v, pz
    v0, p0 = shape(i)
    v1, p1 = shape(i-1) if i >= 1 else (np.nan, np.nan)
    broke_y = E.low.iloc[i] < E.low.iloc[i-1] if i >= 1 else False
    seq = [round((E.close.iloc[k]-E.low.iloc[k])/max(1e-9, E.high.iloc[k]-E.low.iloc[k])*100) for k in range(max(0,i-2), i+1)]
    nxt = (f"昨日 量比{v1:.2f}×、收位{p1*100:.0f}%；今日 量比{v0:.2f}×、收位{p0*100:.0f}%"
           f"、{'破' if broke_y else '未破'}昨低；近3日收位 {seq[0]}/{seq[1]}/{seq[2]}%")
    if v0 >= 1.3 and p0 < 0.4:
        kind = "放量收下半区＝割肉/派发（还在出）"
    elif v0 >= 1.3 and p0 >= 0.6:
        if (p1 >= 0.6) and (not broke_y) and E.close.iloc[i] > E.close.iloc[i-1]:
            kind = "昨日承接 + 今日做上去了 ✅（承接延续）"
        else:
            kind = "今日放量收上半区，但昨日没承接／今日破昨低 ⇒ 试探或自救，未确认"
    elif v0 < 0.9 and p0 >= 0.6 and not broke_y:
        # 2026-04-20 的纠正：「有量目前就不是高点，缩量就容易出高点」
        # 2025-10-16：「缩量代表没人卖，稍微买一点就上去了」
        kind = "缩量收上半区＝**惜售型上涨**（没人卖），可小仓试；转放量滞涨/放量下跌即为高点"
    elif v0 < 0.9 and p0 >= 0.6:
        kind = "缩量收上半区但破了昨低＝惜售没接住，观察"
    elif v0 < 0.9:
        kind = "缩量收下半区＝卖压在枯竭，但也没人接（观察，不做）"
    else:
        kind = "量能中性"
    follow = ""
    return dict(amt_yi=amt, amt_r=ar, total_yi=total, tot_r=(total/tot20 if tot20 else np.nan),
                vr=vr, pos=pos, nxt=nxt, follow=follow, kind=kind)

def structure(high, low, dates, i, swing):
    piv = [p for p in zigzag(high[:i+1], low[:i+1], swing) if p[0] < i]
    if len(piv) < 3: return None
    lows = [p for p in piv if p[2]=='L']; highs = [p for p in piv if p[2]=='H']
    top_i, top_p, _t = max(highs, key=lambda x: x[1])
    before = [p for p in lows if p[0] < top_i]
    origin = min(before, key=lambda x: x[1]) if before else None
    if origin and len(origin)==3: pass
    first_h = [p for p in highs if origin and p[0] > origin[0]]
    w1 = first_h[0] if first_h else None
    after = [p for p in piv if p[0] > top_i]
    downs = [p for p in after if p[2]=='L']
    return dict(origin=origin, w1=w1, top=(top_i, top_p), downs=downs,
                pivots=piv, dates=dates)


def structure_read(I, j, day, ws_legs):
    """结构：用【指数】判断——关键点破没破 + 本轮是否延伸 + 有没有终结迹象。
    ETF 只做工具层，不参与结构判断。"""
    ic, ih, il = I.close.values, I.high.values, I.low.values
    dates = I.trade_date.values
    piv = [p for p in zigzag(ih[:j+1], il[:j+1], IDX_SWING) if p[0] < j]
    out = []
    # ① 关键结构点：破 / 未破
    key = [("1浪顶", 3674.4048), ("前高", 4258.8631)]
    for x in (ws_legs or [])[-3:]:
        if x.get("kind") == "down" and x.get("value"):
            key.append((f"{x['label']}低点", float(x["value"])))
    seen = set()
    for name, lv in key:
        if round(lv, 1) in seen: continue
        seen.add(round(lv, 1))
        d = ic[j] - lv
        tag = f"已破（{day} 收 {ic[j]:.0f}，破 {abs(d):.0f} 点 / {abs(d)/lv*100:.2f}%）" if d < 0 \
              else f"未破（距 {d:+.0f} 点 / {d/lv*100:+.2f}%）"
        out.append(f"       {name} {lv:.2f} → {tag}")
    # ② 本轮延伸：以「最近一次创 20 日新低」为锚，看是否还在延伸
    nl = [k for k in range(max(1,j-19), j+1) if il[k] <= il[max(0,k-19):k+1].min() + 1e-9]
    newlow_recent = sum(1 for k in nl if k >= j-9)
    last_nl = nl[-1] if nl else None
    a1, a2, a3 = ih[max(0,j-19):j+1].max(), ih[max(0,j-39):max(0,j-19)].max(), ih[max(0,j-59):max(0,j-39)].max()
    desc = (a1 < a2) and (a2 < a3)
    if last_nl is not None:
        days_since = j - last_nl
        reb = ih[last_nl:j+1].max()/il[last_nl] - 1
        out.append(f"       最近一次创 20 日新低：{str(dates[last_nl])[4:]}（{days_since} 日前），此后最大反抽 {reb*100:+.1f}%")
    out.append(f"       近 10 日创 20 日新低 {newlow_recent} 次；20 日高点逐级下移={'是' if desc else '否'}"
               f"（{a3:.0f}→{a2:.0f}→{a1:.0f}）")
    if last_nl is not None and days_since <= 2 and newlow_recent >= 2:
        out.append("       ⇒ **仍在延伸：刚创新低 + 近两周多次新低**（没有终结）")
    elif last_nl is not None and days_since >= 5 and reb >= 0.03:
        out.append(f"       ⇒ 已脱离低点 {days_since} 日、反抽 {reb*100:.1f}%"
                   + ("，但**高点仍逐级下移**（反弹性质，不是趋势反转）" if desc else "，高点也在抬升（可能是结构转换）"))
    else:
        out.append("       ⇒ 方向未定：新低不连续，反抽也不足 3%")
    # ③ 终结迹象三项
    frac = (j >= 2) and il[j] < il[j-1] and il[j] < il[j-2] and (ic[j]-il[j])/max(1e-9, ih[j]-il[j]) >= 0.5
    slow = (j >= 10) and abs(ic[j]/ic[j-5]-1) < abs(ic[j-5]/ic[j-10]-1)*0.7 and il[j] > il[max(0,j-19):j].min()
    vr_up = (j >= 20) and (I.vol.values[j] >= 1.5*I.vol.values[j-19:j+1].mean()) and \
            (ic[j]-il[j])/max(1e-9, ih[j]-il[j]) >= 0.6
    marks = [n for n, v in (("底分型", frac), ("下跌降速", slow), ("放量收上半区", vr_up)) if v]
    out.append(f"       终结迹象：{'、'.join(marks) if marks else '**无**（底分型/降速/放量承接 三项都没出现）'}")
    return out

def read(day, E, I):
    i_all = E.index[E.trade_date == day]
    if not len(i_all): return f"{day} 非交易日/无数据"
    i = i_all[0]
    c, h, l = E.close.values, E.high.values, E.low.values
    j = I.index[I.trade_date == day][0]
    ic, ih, il = I.close.values, I.high.values, I.low.values
    IS = structure(ih, il, I.trade_date.values, j, IDX_SWING)
    ES = structure(h, l, E.trade_date.values, i, ETF_SWING)
    bs = buyer_shape(E, I, i)
    out = [f"\n{'='*72}\n【{day}】 上证 {ic[j]:.0f} ({I.pct_chg.iloc[j]:+.2f}%)   588170 {c[i]:.3f} ({(c[i]/c[i-1]-1)*100:+.2f}%)"]
    # 级别
    out.extend(wave_line(day))
    # 结构（指数）
    wj, wused = wave_snapshot(day)
    wlegs = ((wj or {}).get('features') or {}).get('sub_legs') or []
    ok = (wj is not None) and abs(((wj.get('features') or {}).get('wave1_top') or 0) -
                                  ((wj.get('features') or {}).get('prior_peak') or 0)) > 1
    # 底部结构（W底/颈线/红三兵/突破）
    bs_ = detect_bottom(I.high.values, I.low.values, I.close.values, I.open.values, I.vol.values, j)
    out.append("底部结构｜" + bs_["state"] + (
        f"  双底 {bs_['l1']:.0f}/{bs_['l2']:.0f}（反抽 {bs_['reb']}%）颈线 {bs_['neck']:.0f}，现价距颈线 {bs_['dist_neck']:+.1f}%"
        if "l1" in bs_ else ""))
    out.append("结构｜（上证）")
    out.extend(structure_read(I, j, day, wlegs if ok else []))
    # 谁在买
    if bs:
        local = f"，**局部/全局 {(bs['vr']/bs['amt_r']):.2f}×**" if bs['amt_r'] else ""
        out.append(f"买盘｜沪市 {bs['amt_yi']:,.0f}亿 = {bs['amt_r']:.2f}×20均；两市 {bs['total_yi']:,.0f}亿 = {bs['tot_r']:.2f}×；"
                   f"ETF 量比 {bs['vr']:.2f}×，收位 {bs['pos']*100:.0f}%{local}")
        out.append(f"       {bs['nxt']}{'；' + bs['follow'] if bs['follow'] else ''} ⇒ **{bs['kind']}**")
        mt = mins_tail(E.trade_date.iloc[i])
        if mt:
            # 参照分位（24 天样本，588170 1min）：mid P10/50/80 = 4.8/7.8/9.9；close P10/50/80 = 3.6/5.1/6.0
            mid, close = mt['mid_share'], mt['close_share']
            lab_mid = ("尾段集中(>P80)" if mid >= 9.9 else "尾段偏淡(<P10)" if mid <= 4.8 else "尾段正常")
            lab_cls = ("收盘前偷袭(>P80)" if close >= 6.0 else "收盘前偏淡(<P10)" if close <= 3.6 else "收盘前正常")
            out.append(f"       尾盘｜14:50 时已知：14:30–14:50 占前段量 {mid:.0f}%（分位 P10/50/80=4.8/7.8/9.9）⇒ {lab_mid}")
            out.append(f"             收盘后可知：14:50–15:00 占全天 {close:.1f}%（P10/50/80=3.6/5.1/6.0，含集合竞价）⇒ {lab_cls}")
        else:
            out.append("       尾盘｜（该日无分钟数据，未计算；14:50 推送口径需实时分钟）")
        out.append("       （注：『自救 vs 主动做多』测不到资金主体，只测形状；沪市口径与他口头的『5500E』未校准）")
    # 够不够得着（工具层：ETF）
    if ES:
        above = sorted([p[1] for p in ES['pivots'] if p[1] > c[i]])[:3]
        below = sorted([p[1] for p in ES['pivots'] if p[1] < c[i]], reverse=True)[:3]
        # B 起点低点 = 最近一次创 20 日新低那天的最低价
        nl = [k for k in range(max(1,i-19), i+1) if l[k] <= l[max(0,k-19):k+1].min()+1e-9]
        b_low = l[nl[-1]] if nl else None
        out.append("空间｜上方结构点(由近到远)：" + "、".join(f"{x:.3f}(+{(x/c[i]-1)*100:.1f}%)" for x in above))
        out.append("       下方结构点(由近到远)：" + "、".join(f"{x:.3f}({(x/c[i]-1)*100:+.1f}%)" for x in below))
        if b_low:
            out.append(f"       失效首选 **B 起点低 {b_low:.3f}**（最近一次 20 日新低），−4% 缓冲后 ≈ {b_low*0.96:.3f}")
        elif below:
            out.append(f"       失效参考 最近下方结构点 {below[0]:.3f}，−4% 缓冲后 ≈ {below[0]*0.96:.3f}")
    return "\n".join(out)

if __name__ == "__main__":
    days = sys.argv[1:] or ["20260803", "20261019"]
    E, I = load_etf(), load_idx()
    for d in days: print(read(d, E, I))
