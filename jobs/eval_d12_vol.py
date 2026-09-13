# -*- coding: utf-8 -*-
"""eval_d12_vol.py — D12 验收：按语料依据加「波动幅度 / 量能」门槛，修 D1 把防御方向推第一的偏差。

**为什么是这两个门槛（语料原话由 dsh 抽取，见 `/app/data/wolf_d12_evidence.json`）**
  · **波动幅度**（他反复讲，5 条）：
    「选方向看波动幅度，**只做波动大的方向，不碰日均波动仅 2 个点的银行**」（2026-08-03）
    「选方向看波动幅度，**波动大、5-10 个点机会多才做**」（2026-08-03）
    「选大科技因**单日套利空间（3-10 个点）**远大于红利」（2026-08-04）
    「选半导体而非红利，因**波动率差距大**」（2026-08-05）
    「（要不要买银行）我说了 我不去 **就是绝对不让自己陷入低波动的陷阱里**」（2026-08-04）
  · **量能**（方向层）：「**量能由缩转放**是介入该方向的信号」（2026-08-12）、「**缩量不参与**，等缩转放再考虑」（2026-08-04）、
    「缩量的情况下大光动都不动…这能有什么**持续性**？…不玩就对了」（2026-08-06）

**口径**：`amp` = 个股当日振幅 `(high-low)/pre_close`（他说的"日均波动几个点"就是它）→ 主题取成分股均值；
`amp20` = 近 20 日均值；`volr` = 主题近 5 日成交额 / 前 20 日日均成交额×5（>1 放量、<1 缩量）。
变体（都在 gate 资格池内做，主信号仍是近 5 日相对强度 r5）：
  V0 基线（现状 D1）｜W1 剔除波动最低 1/4｜W2 剔除 amp20 < 2%（他的绝对锚）｜W3 剔除缩量最低 1/4｜W4 = W1+W3
度量：所选方向后 5 日超额、t、胜率、他方向落 top1/top3、**农业+医药被选天数**。

**内存纪律**（2026-09-12 实测教训）：容器 cgroup 512MB，把 94 万行行情拉进 pandas 做全市场 pivot
会被 OOM 杀掉，**连生产 backend（uvicorn）一起被杀、容器重启**。因此本脚本**全部聚合下推到 SQL**
（临时成分表 + group by），出库只有 ~170×13 行，pandas 侧只做小宽表运算。
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
OUT = os.path.join(DATA, 'eval_d12_vol.json')


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
    if not uni:
        print('[d12] 主题成分为空，退出', flush=True)
        return 2
    # 主题成分临时表（13 主题 → 代码；一只票可在多主题）
    db.execute(text("SET LOCAL work_mem = '64MB'"))
    db.execute(text("CREATE TEMP TABLE _memb (ts_code varchar(16), theme varchar(32)) ON COMMIT PRESERVE ROWS"))
    pairs = [{"ts": c, "th": th} for th, codes in uni.items() for c in codes]
    for k in range(0, len(pairs), 2000):
        db.execute(text("INSERT INTO _memb (ts_code, theme) VALUES (:ts, :th)"), pairs[k:k + 2000])
    print('[d12] 成分对 %d 条' % len(pairs), flush=True)
    rows = db.execute(text("""
        SELECT b.trade_date AS d, m.theme AS th,
               avg(b.pct_chg)::float8 AS pc,
               avg((b.high - b.low) / b.pre_close)::float8 AS amp,
               sum(b.amount)::float8 AS amt
        FROM mkt_bars_daily b JOIN _memb m ON m.ts_code = b.ts_code
        WHERE b.pre_close > 0 AND b.pct_chg IS NOT NULL
        GROUP BY 1, 2
    """)).all()
    mrows = db.execute(text("""
        SELECT trade_date AS d, avg(pct_chg)::float8 AS pc
        FROM mkt_bars_daily WHERE pct_chg IS NOT NULL GROUP BY 1
    """)).all()
    print('[d12] 主题日行 %d | 全市场日 %d' % (len(rows), len(mrows)), flush=True)
    g = pd.DataFrame(rows, columns=['d', 'th', 'pc', 'amp', 'amt'])
    pc = g.pivot_table(index='d', columns='th', values='pc', aggfunc='first').sort_index().astype('float64')
    amp = g.pivot_table(index='d', columns='th', values='amp', aggfunc='first').reindex(pc.index).astype('float64')
    amt = g.pivot_table(index='d', columns='th', values='amt', aggfunc='first').reindex(pc.index).astype('float64')
    del g
    mk = pd.Series({r[0]: r[1] for r in mrows}).reindex(pc.index).astype('float64')
    pc = pc / 100.0        # pct_chg 是百分数 → 转小数（mk 同源，必须一起转，否则基准错位：2026-09-12 踩过）
    mk = mk / 100.0
    cmp5 = (1 + pc).rolling(5).apply(np.prod, raw=True)
    cmp5m = (1 + mk).rolling(5).apply(np.prod, raw=True)
    r5 = cmp5.sub(cmp5m, axis=0)
    f5 = (1 + pc).rolling(5).apply(np.prod, raw=True).shift(-5)
    f5m = (1 + mk).rolling(5).apply(np.prod, raw=True).shift(-5)
    fwd5 = f5.sub(f5m, axis=0)
    amp20 = amp.rolling(20, min_periods=10).mean()
    volr = amt.rolling(5).sum() / (amt.rolling(20).mean() * 5).replace(0, np.nan)
    # 持续性（语料 2026-08-11「以方向是否有持续性判断行情」/08-06「缩量…这能有什么持续性」）：
    # = 近 5 日里主题跑赢全市场（逐日超额 > 0）的天数
    rel = pc.sub(mk, axis=0)
    pos5 = (rel > 0).rolling(5).sum()
    idx = pc.index.tolist()

    dmap = {r['dir_text']: r['theme'] for r in db.execute(
        text("SELECT dir_text, theme FROM wolf_dir_theme_map")).mappings().all()}
    his = {}
    for r in db.execute(text("SELECT trade_date, payload FROM wolf_actual_mainline "
                             "WHERE status='ok'")).mappings().all():
        p = r['payload'] if isinstance(r['payload'], dict) else json.loads(r['payload'] or '{}')
        s = sorted({dmap.get((x.get('dir') or '').strip()) for x in (p.get('doing') or [])} - {None, '非主题'})
        if s:
            his[r['trade_date']] = s
    gates = {}
    for r in db.execute(text("SELECT trade_date, payload FROM daily_artifacts "
                             "WHERE artifact_key='mainline_gate'")).mappings().all():
        p = r['payload'] if isinstance(r['payload'], dict) else json.loads(r['payload'] or '{}')
        gates[r['trade_date']] = [x['theme'] for x in (p.get('rows') or []) if x.get('gate')]
    db.close()

    variants = collections.defaultdict(list)
    pool_top3, picks_by_theme = {}, collections.defaultdict(collections.Counter)
    amp_rank_sum, amp_rank_n = collections.Counter(), collections.Counter()
    for i in range(25, len(idx) - 5):
        d = idx[i]
        r5d = {t: v for t, v in r5.iloc[i].items() if v == v}
        if len(r5d) < 4:
            continue
        gs = [t for t in (gates.get(d) or []) if t in r5d] or list(r5d)
        pool = sorted(gs, key=lambda t: -r5d[t])
        pool_top3[d] = pool[:3]
        a20 = {t: v for t, v in ((t, amp20.iloc[i].get(t)) for t in pool) if v is not None and v == v}
        vr = {t: v for t, v in ((t, volr.iloc[i].get(t)) for t in pool) if v is not None and v == v}
        p5 = {t: v for t, v in ((t, pos5.iloc[i].get(t)) for t in pool) if v is not None and v == v}
        if len(a20) >= 6:
            for rk, t in enumerate(sorted(a20, key=lambda x: -a20[x])):
                amp_rank_sum[t] += rk
                amp_rank_n[t] += 1

        def pick(mode):
            cand = list(pool)
            # W7/W8：语料支撑的**方向黑名单**——他明确不做 A 股医药
            # （2026-04-03「我不做大A医药股 只做港药套利」／04-17「大A的医药个股鬼故事太多了 跑还出不去」）
            if mode in ('W7', 'W8'):
                cand = [t for t in cand if t != '医药'] or cand
            if mode in ('W1', 'W4') and len(a20) >= 3:
                bad = {t for t, _ in sorted(a20.items(), key=lambda kv: kv[1])[:max(1, len(a20) // 4)]}
                cand = [t for t in cand if t not in bad] or cand
            if mode == 'W2':
                cand = [t for t in cand if a20.get(t, 0.0) >= 0.02] or cand
            if mode == 'W8':
                pass
            if mode in ('W5', 'W6'):
                cand = [t for t in cand if p5.get(t, 0) >= 3] or cand          # 持续性：近5日里 ≥3 天跑赢
            if mode in ('W3', 'W4', 'W6') and len(vr) >= 3:
                bad = {t for t, _ in sorted(vr.items(), key=lambda kv: kv[1])[:max(1, len(vr) // 4)]}
                cand = [t for t in cand if t not in bad] or cand
            return cand[0] if cand else None

        fs = fwd5.iloc[i]
        for mode in ('V0', 'W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7', 'W8'):
            t = pick(mode)
            if not t:
                continue
            f = fs.get(t)
            if f is None or f != f:
                continue
            variants[mode].append((d, t, float(f) * 100))
            picks_by_theme[mode][t] += 1

    names = {'V0': 'V0 基线（现状 D1）', 'W1': 'W1 剔除低波动1/4', 'W2': 'W2 剔除日均波动<2%',
             'W3': 'W3 剔除缩量1/4', 'W4': 'W4 波动+量能', 'W5': 'W5 持续性(近5日≥3天跑赢)',
             'W6': 'W6 波动+量能+持续性', 'W7': 'W7 排除医药(语料黑名单)', 'W8': 'W8 波动+量能+持续性+排除医药'}
    out = {'diagnostics': {'amp_rank_avg': {t: round(amp_rank_sum[t] / max(1, amp_rank_n[t]), 2)
                                            for t in amp_rank_n},
                           'amp20_avg_pct': {t: round(float(amp20[t].mean()) * 100, 2) for t in amp20.columns}},
           'variants': {}, 'names': names}
    print('\n【诊断】各主题平均振幅（20日）| 在 gate 池内的振幅排名（0=当日最活跃）', flush=True)
    for t in sorted(out['diagnostics']['amp20_avg_pct'], key=lambda x: -out['diagnostics']['amp20_avg_pct'][x]):
        print('   %-16s amp20=%.2f%%  平均振幅排名 %.1f（%d 天）'
              % (t, out['diagnostics']['amp20_avg_pct'][t],
                 amp_rank_sum[t] / max(1, amp_rank_n[t]), amp_rank_n[t]), flush=True)
    print('\n【验收】所选方向后 5 日超额', flush=True)
    print('| 变体 | n | 均值 | t | 胜率 | 他落top1 | 他落top3 | 农业+医药选中 |')
    print('|---|---|---|---|---|---|---|---|')
    for mode in ('V0', 'W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7', 'W8'):
        picks = variants[mode]
        s = stat([f for _, _, f in picks])
        hd = [d for d, _, _ in picks if his.get(d)]
        n1 = sum(1 for d, t, _ in picks if t in (his.get(d) or []))
        n3 = sum(1 for d, t, _ in picks if set(his.get(d) or []) & set((pool_top3.get(d) or [t])[:3]))
        bad = picks_by_theme[mode]['农业'] + picks_by_theme[mode]['医药']
        out['variants'][mode] = {**s, 'his_top1': round(n1 / max(1, len(hd)), 3),
                                 'his_top3': round(n3 / max(1, len(hd)), 3), 'n_his_days': len(hd),
                                 'picks': dict(picks_by_theme[mode]), 'agri_med_days': bad}
        print('| %s | %s | %s | %s | %s | %d/%d=%.0f%% | %d/%d=%.0f%% | %d |'
              % (names[mode], s.get('n'), s.get('mean'), s.get('t'), s.get('pos'),
                 n1, len(hd), 100.0 * n1 / max(1, len(hd)),
                 n3, len(hd), 100.0 * n3 / max(1, len(hd)), bad), flush=True)
    # 关键问题：被选中的"防御方向"到底是拖累还是贡献？按选中主题分组看后5日超额
    by_theme = collections.defaultdict(list)
    for d, t, fv in variants['V0']:
        by_theme[t].append(fv)
    out['v0_by_theme'] = {t: stat(v) for t, v in sorted(by_theme.items(), key=lambda kv: -len(kv[1]))}
    print('\n【V0 基线：按被选中主题分组】后 5 日超额（判断"防御方向"是不是拖累）', flush=True)
    print('| 主题 | 被选天数 | 均值 | t | 胜率 |', flush=True)
    print('|---|---|---|---|---|', flush=True)
    for t, v in out['v0_by_theme'].items():
        print('| %s | %s | %s | %s | %s |' % (t, v.get('n'), v.get('mean'), v.get('t'), v.get('pos')), flush=True)
    agri = [f for d, t, f in variants['V0'] if t in ('农业', '医药')]
    rest = [f for d, t, f in variants['V0'] if t not in ('农业', '医药')]
    out['v0_agri_med_vs_rest'] = {'agri_med': stat(agri), 'rest': stat(rest)}
    print('\n农业+医药合计 %s ｜ 其余 %s' % (out['v0_agri_med_vs_rest']['agri_med'],
                                            out['v0_agri_med_vs_rest']['rest']), flush=True)
    # 分段（H1/H2）稳定性检验：均值增益如果只出现在一半，就是噪声
    mid = idx[len(idx) // 2 + 12]
    print('\n【分段稳定性】H1 = %s…%s ｜ H2 = %s…%s' % (idx[0], mid, mid, idx[-1]), flush=True)
    print('| 变体 | H1 均值 | H1 t | H2 均值 | H2 t | 两段都优于基线? |', flush=True)
    print('|---|---|---|---|---|---|', flush=True)
    half = {}
    for mode in ('V0', 'W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7', 'W8'):
        h1 = stat([f for d, _, f in variants[mode] if d <= mid])
        h2 = stat([f for d, _, f in variants[mode] if d > mid])
        half[mode] = {'H1': h1, 'H2': h2}
        print('| %s | %s | %s | %s | %s | — |' % (names[mode], h1.get('mean'), h1.get('t'),
                                                   h2.get('mean'), h2.get('t')), flush=True)
    b1 = half['V0']['H1'].get('mean') or 0
    b2 = half['V0']['H2'].get('mean') or 0
    print('\n  基线 V0：H1 %s / H2 %s' % (b1, b2), flush=True)
    for mode in ('W1', 'W3', 'W4', 'W5', 'W6', 'W7', 'W8'):
        o1 = (half[mode]['H1'].get('mean') or 0) > b1
        o2 = (half[mode]['H2'].get('mean') or 0) > b2
        print('  %-4s H1 %s、H2 %s → %s' % (mode, '优' if o1 else '劣', '优' if o2 else '劣',
                                            '两段都优' if (o1 and o2) else '**不是两段都优**'), flush=True)
    out['split_half'] = half

    print('\n【各变体选中分布】', flush=True)
    for mode in ('V0', 'W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7', 'W8'):
        print('   %-4s %s' % (mode, '、'.join('%s(%d)' % (t, n)
                                             for t, n in picks_by_theme[mode].most_common(6))), flush=True)
    try:
        json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('\n[d12] 结果 → %s' % OUT, flush=True)
    except Exception as e:
        print('[d12] 写结果失败 %s' % str(e)[:60], flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
