# -*- coding: utf-8 -*-
"""eval_structural_pool.py — 「结构判据 × 当期主线池」能不能 PIT 地复现他的池？

**为什么要它**：P10（他近 10 日 doing ∩ 近 20 日相对强度 top3）命中 50%、收益 +1.73%，但那是**跟随他的发言**（有滞后）。
用户要的是：**用结构判据自己算出"当期主线池"**。他在语料里给的线索（全部有原话）：
  · 量能集中度：「**光+半导体加起来是市场 50% 成交量**…要降到 25%-30% 才可能重新走起来」（2026-08-24）；
    「**量能（2-2.5WE）只够支撑一个高位板块主反**」（2026-08-05）；「成交量占全天的 **45% 以上**，那就是真出完货了」（2026-06-05）
  · 主线动没动：「看看**主线题材动没动**就知道了」（2026-01-13）；「不要在没有行情的时候重仓在这段时间**没有行情的方向**」（2026-02-07）
  · 持续性：「做板块他没有**主线题材持续性**…一律不做」（2026-03-27）
  · 链条联动：「**光芯存算**」一起看；「大光拔估值 3 天后→二线光 2-3 天→光纤 3 天→光上游 3 天」（2026-05-29）
→ 本脚本把这几条做成**可算的结构判据**，逐一检验：
  **① 能否分开"他做的方向 vs 他不做的方向"（诊断）**；**② 用它当池，能否同时拿到高命中率与高收益（验收）**。

口径：主题=13 主题（`MS.load_universe` 概念库）；`amt_share5` = 主题近 5 日成交额 / 全市场近 5 日成交额；
`r5/r20` = 主题近 5/20 日相对强度（等权复利 − 全市场等权复利）；`pos20` = 近 20 日里跑赢市场的天数；
`linked` = 该主题所属链条内**同为“在动”的子方向个数**（用 15 子方向口径）。
内存纪律：聚合全部下推 SQL（临时成分表 + group by）。
"""
import collections
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, '/app')
sys.path.insert(0, '/app/backend')
from app.database import SessionLocal                      # noqa: E402
from sqlalchemy import text                                 # noqa: E402
from app.services import wolf_mainline_select as MS          # noqa: E402

DATA = os.environ.get('DATA_DIR', '/app/data')
OUT = os.path.join(DATA, 'eval_structural_pool.json')

# 15 子方向 → 13 主题（用于"链条联动"口径）
SUB2THEME = {
    '科技/AI(总集)': 'AI/算力/科技', 'AI应用': 'AI/算力/科技', 'AI终端': 'AI/算力/科技',
    '国算/算力': 'AI/算力/科技', '液冷': 'AI/算力/科技', '光通信': 'AI/算力/科技',
    '铜缆/电源': 'AI/算力/科技', '存储': '半导体/芯片', '材料': '半导体/芯片',
    '芯片/半导体': '半导体/芯片', '医药医疗': '医药', '电力': '电力/公用',
    '金融': '金融', '军工航天': '军工/航天', '有色贵金属': '资源/周期',
}


def stat(v):
    n = len(v)
    if not n:
        return {'n': 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {'n': n, 'mean': round(m, 3), 't': round(m / (sd / n ** 0.5), 2) if sd else None,
            'pos': round(sum(1 for x in v if x > 0) / n, 3)}


def main():
    db = SessionLocal()
    uni, lead, allc = MS.load_universe()
    db.execute(text("SET LOCAL work_mem = '64MB'"))
    db.execute(text("DROP TABLE IF EXISTS _sp_memb"))
    db.execute(text("CREATE TEMP TABLE _sp_memb (ts_code varchar(16), theme varchar(32))"))
    pairs = [{'ts': c, 'th': th} for th, codes in uni.items() for c in codes]
    for k in range(0, len(pairs), 2000):
        db.execute(text("INSERT INTO _sp_memb (ts_code, theme) VALUES (:ts, :th)"), pairs[k:k + 2000])
    rows = db.execute(text("""
        SELECT b.trade_date AS d, m.theme AS th,
               avg(b.pct_chg)::float8 AS pc, sum(b.amount)::float8 AS amt
        FROM mkt_bars_daily b JOIN _sp_memb m ON m.ts_code = b.ts_code
        WHERE b.pct_chg IS NOT NULL GROUP BY 1, 2
    """)).all()
    mrows = db.execute(text("SELECT trade_date d, avg(pct_chg)::float8 pc, sum(amount)::float8 amt "
                            "FROM mkt_bars_daily WHERE pct_chg IS NOT NULL GROUP BY 1")).all()
    g = pd.DataFrame(rows, columns=['d', 'th', 'pc', 'amt'])
    pc = g.pivot_table(index='d', columns='th', values='pc', aggfunc='first').sort_index().astype('float64') / 100.0
    amt = g.pivot_table(index='d', columns='th', values='amt', aggfunc='first').reindex(pc.index).astype('float64')
    mk = pd.Series({r[0]: r[1] for r in mrows}).reindex(pc.index).astype('float64') / 100.0
    mamt = pd.Series({r[0]: r[2] for r in mrows}).reindex(pc.index).astype('float64')
    idx = pc.index.tolist()
    rel = pc.sub(mk, axis=0)                                   # 逐日相对强度
    r5 = ((1 + pc).rolling(5).apply(np.prod, raw=True).sub(
        (1 + mk).rolling(5).apply(np.prod, raw=True), axis=0))
    r20 = ((1 + pc).rolling(20).apply(np.prod, raw=True).sub(
        (1 + mk).rolling(20).apply(np.prod, raw=True), axis=0))
    pos20 = (rel > 0).rolling(20).sum()
    share5 = amt.rolling(5).sum().div(mamt.rolling(5).sum(), axis=0)      # 量能占比（5 日）
    share20 = amt.rolling(20).sum().div(mamt.rolling(20).sum(), axis=0)
    share_pct = share5.rolling(120, min_periods=40).rank(pct=True)        # 占比的自身历史分位
    f5 = (1 + pc).rolling(5).apply(np.prod, raw=True).shift(-5)
    f5m = (1 + mk).rolling(5).apply(np.prod, raw=True).shift(-5)
    fwd5 = (f5.sub(f5m, axis=0)) * 100

    dmap = {r['dir_text']: r['theme'] for r in db.execute(
        text("SELECT dir_text, theme FROM wolf_dir_theme_map")).mappings().all()}
    his, his_cnt = {}, collections.Counter()
    for r in db.execute(text("SELECT trade_date, payload FROM wolf_actual_mainline "
                             "WHERE status='ok'")).mappings().all():
        p = r['payload'] if isinstance(r['payload'], dict) else json.loads(r['payload'] or '{}')
        s = sorted({dmap.get((x.get('dir') or '').strip()) for x in (p.get('doing') or [])} - {None, '非主题'})
        if s:
            his[r['trade_date']] = s
            for t in s:
                his_cnt[t] += 1
    gates = {}
    for r in db.execute(text("SELECT trade_date, payload FROM daily_artifacts "
                             "WHERE artifact_key='mainline_gate'")).mappings().all():
        p = r['payload'] if isinstance(r['payload'], dict) else json.loads(r['payload'] or '{}')
        gates[r['trade_date']] = [x['theme'] for x in (p.get('rows') or []) if x.get('gate')]
    db.close()

    # ── 诊断①：各结构判据能否分开"他做的 vs 他不做的"方向 ──
    print('【诊断①】主题层面：他的出现天数 vs 结构判据均值（2026）', flush=True)
    print('| 主题 | 他出现天数 | 量能占比5日均值 | 占比历史分位均值 | r20 均值 | 近20日跑赢天数均值 |', flush=True)
    print('|---|---|---|---|---|---|', flush=True)
    diag = {}
    for th in sorted(uni, key=lambda t: -his_cnt[t]):
        d0 = {'his_days': his_cnt[th],
              'share5': round(float(share5[th].mean()) * 100, 2),
              'share_pct': round(float(share_pct[th].mean()) * 100, 1),
              'r20': round(float(r20[th].mean()) * 100, 2),
              'pos20': round(float(pos20[th].mean()), 1)}
        diag[th] = d0
        print('| %s | %d | %s%% | %s | %s%% | %s |' % (th, d0['his_days'], d0['share5'],
                                                      d0['share_pct'], d0['r20'], d0['pos20']), flush=True)

    # 分离度：他做的方向(当日) vs 当日其他方向的判据均值差
    sep = collections.defaultdict(lambda: [[], []])
    for i in range(25, len(idx) - 5):
        d = idx[i]
        hd = set(his.get(d) or [])
        if not hd:
            continue
        for th in uni:
            v = {'share5': float(share5[th].iloc[i]) if share5[th].iloc[i] == share5[th].iloc[i] else None,
                 'share_pct': float(share_pct[th].iloc[i]) if share_pct[th].iloc[i] == share_pct[th].iloc[i] else None,
                 'r20': float(r20[th].iloc[i]) if r20[th].iloc[i] == r20[th].iloc[i] else None,
                 'pos20': float(pos20[th].iloc[i]) if pos20[th].iloc[i] == pos20[th].iloc[i] else None}
            for k, x in v.items():
                if x is not None:
                    sep[k][0 if th in hd else 1].append(x)
    print('\n【诊断②】"他做的方向" vs "同日其他方向"的判据均值（分离度）', flush=True)
    print('| 判据 | 他做的 | 其他 | 差 | 判读 |', flush=True)
    print('|---|---|---|---|---|', flush=True)
    sep_out = {}
    for k, (a, b) in sep.items():
        if not a or not b:
            continue
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        rel = (ma - mb) / (abs(mb) or 1) * 100
        sep_out[k] = {'his': round(ma, 4), 'other': round(mb, 4), 'diff_pct': round(rel, 1)}
        print('| %s | %.4f | %.4f | %+.1f%% | %s |' % (k, ma, mb, rel,
              '有区分度' if abs(rel) > 10 else '区分度弱'), flush=True)

    # ── 诊断③：池会不会随时间轮换（还是等于"科技永远第一"的静态白名单）──
    print('\n【诊断③】量能占比 top3 的月度构成（看池是否随主线轮换）', flush=True)
    print('| 月份 | 量能占比 top3 | 他当月 top3 |', flush=True)
    print('|---|---|---|', flush=True)
    monthly = collections.defaultdict(lambda: collections.Counter())
    his_m = collections.defaultdict(collections.Counter)
    for i in range(25, len(idx)):
        d = idx[i]
        row = {t: v for t, v in share5.iloc[i].items() if v == v}
        if len(row) < 5:
            continue
        for t, _ in sorted(row.items(), key=lambda kv: -kv[1])[:3]:
            monthly[d[:6]][t] += 1
        for t in (his.get(d) or []):
            his_m[d[:6]][t] += 1
    month_out = {}
    for mo in sorted(monthly):
        a = '、'.join('%s(%d)' % (t, n) for t, n in monthly[mo].most_common(3))
        b = '、'.join('%s(%d)' % (t, n) for t, n in his_m[mo].most_common(3)) or '—'
        month_out[mo] = {'pool': a, 'his': b}
        print('| %s | %s | %s |' % (mo, a, b), flush=True)

    # ── 验收：不同结构池 ──
    variants = collections.defaultdict(list)
    picks = collections.defaultdict(collections.Counter)
    ptop3 = {}
    for i in range(25, len(idx) - 5):
        d = idx[i]
        r5d = {t: v for t, v in r5.iloc[i].items() if v == v}
        if len(r5d) < 4:
            continue
        r20d = {t: v for t, v in r20.iloc[i].items() if v == v}
        sh = {t: v for t, v in share5.iloc[i].items() if v == v}
        shp = {t: v for t, v in share_pct.iloc[i].items() if v == v}
        p20 = {t: v for t, v in pos20.iloc[i].items() if v == v}
        gs = [t for t in (gates.get(d) or []) if t in r5d] or list(r5d)
        rankr5 = sorted(r5d, key=lambda t: -r5d[t])
        top_by = lambda dd, k: set([t for t, _ in sorted(dd.items(), key=lambda kv: -kv[1])][:k])

        roll10 = sorted({t for dd in idx[max(0, i - 10):i] for t in (his.get(dd) or [])})
        zs = lambda dd: {t: (v - (sum(dd.values()) / len(dd))) / ((sum((x - sum(dd.values()) / len(dd)) ** 2
                        for x in dd.values()) / max(1, len(dd) - 1)) ** 0.5 or 1) for t, v in dd.items()}
        z_sh, z_r5 = zs(sh), zs(r5d)
        combo = {t: z_sh.get(t, 0) + z_r5.get(t, 0) for t in r5d}
        pools = {
            'T1_池=量能占比top1': top_by(sh, 1),
            'T2_池=量能占比top2': top_by(sh, 2),
            'T4_池=量能占比top4': top_by(sh, 4),
            'T5_池=量能占比top5': top_by(sh, 5),
            'T6_池=量能占比top3∩r5>0': {t for t in top_by(sh, 3) if (r5d.get(t) or 0) > 0},
            'T7_池=量能占比top3∩他近10日': top_by(sh, 3) & set(roll10),
            'T8_池=综合分(z占比+z_r5)top1': top_by(combo, 1),
            'T9_池=综合分top3': top_by(combo, 3),
            'S0_池=r20top3(对照P8)': top_by(r20d, 3),
            'S1_池=量能占比top3': top_by(sh, 3),
            'S2_池=量能占比top3∩r20>0': {t for t in top_by(sh, 3) if (r20d.get(t) or 0) > 0},
            'S3_池=r20top3∩占比分位>0.6': {t for t in top_by(r20d, 3) if (shp.get(t) or 0) > 0.6},
            'S4_池=持续性top3(近20日跑赢天数)': top_by(p20, 3),
            'S5_池=r20top3∩跑赢≥10天': {t for t in top_by(r20d, 3) if (p20.get(t) or 0) >= 10},
            'S6_池=占比分位>0.8∩r5>0': {t for t in r5d if (shp.get(t) or 0) > 0.8 and (r5d.get(t) or 0) > 0},
            'S7_池=(量能top5)∩(r20top5)∩(跑赢≥10天)': (top_by(sh, 5) & top_by(r20d, 5) &
                                                        {t for t in r5d if (p20.get(t) or 0) >= 10}),
            'S8_池=S7∪他的池口径(top3 by r20∧跑赢)': (top_by(sh, 5) & top_by(r20d, 5)) or top_by(r20d, 3),
        }
        for name, pool in pools.items():
            cand = [t for t in rankr5 if t in pool and t in gs]
            if not cand:
                cand = [t for t in rankr5 if t in pool]
            if not cand:
                continue
            fv = fwd5.iloc[i].get(cand[0])
            if fv is None or fv != fv:
                continue
            variants[name].append((d, cand[0], float(fv)))
            picks[name][cand[0]] += 1
            ptop3[(name, d)] = cand[:3]

    print('\n【验收】结构池 → gate → 池内 r5 top1；与"他的池"(P10 口径)对比', flush=True)
    print('| 池 | n | 后5日超额 | t | 胜率 | 他落top1 | 他落top3 | 选中分布(top4) |', flush=True)
    print('|---|---|---|---|---|---|---|---|', flush=True)
    out = {'diagnostics': diag, 'separation': sep_out, 'monthly_pool': month_out, 'variants': {}}
    for name in sorted(variants):
        v = variants[name]
        s = stat([x[2] for x in v])
        hd = [d for d, _, _ in v if his.get(d)]
        n1 = sum(1 for d, t, _ in v if t in (his.get(d) or []))
        n3 = sum(1 for d, t, _ in v if set(his.get(d) or []) & set(ptop3.get((name, d)) or [t]))
        v.sort(key=lambda x: x[0])
        mid = idx[len(idx) // 2 + 12]
        h1 = stat([x[2] for x in v if x[0] <= mid])
        h2 = stat([x[2] for x in v if x[0] > mid])
        dist = '、'.join('%s(%d)' % (t, n) for t, n in picks[name].most_common(4))
        out['variants'][name] = {**s, 'his_top1': round(n1 / max(1, len(hd)), 3),
                                 'his_top3': round(n3 / max(1, len(hd)), 3), 'picks': dict(picks[name]),
                                 'H1': h1, 'H2': h2}
        print('| %s | %s | %s | %s | %s | %d/%d=%.0f%% | %d/%d=%.0f%% | %s |'
              % (name, s.get('n'), s.get('mean'), s.get('t'), s.get('pos'),
                 n1, len(hd), 100.0 * n1 / max(1, len(hd)), n3, len(hd), 100.0 * n3 / max(1, len(hd)),
                 dist), flush=True)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n[sp] 结果 → %s' % OUT, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
