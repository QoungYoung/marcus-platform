# -*- coding: utf-8 -*-
"""eval_wolf_pool_prior.py — 「复刻他的主线判定」：用**方向池先验**约束 D1（2026 为样本外）。

**思路**：D1 现状（gate 资格 ∩ 近 5 日相对强度 top1）在他方向上的命中是 top1 23% / top3 48%（随机 7.7%/23%），
但 139 天里有 61 天选的是他**基本不做**的方向（农业 22、医药 20、电力 19）。他的方向分布极不平均
（2026：半导体 46、AI 46、资源 32、新能源 26、军工 17；农业 **0**、消费/金融/稳增长/汽车各 1）。
→ 假设：**他的主线判定 = 「结构先验（只做某几类方向）× 状态信号（谁在动）」**，而我们只实现了后半段。

**样本外设计**（避免用 2026 定池再在 2026 上测）：
  · `P_prior` = 由 **2021-2022 + 2025** 语料抽出的他实际做的方向（`jobs/wolf_period_directions.py`）映射到 13 主题；
  · `P_his2026` = 2026 语料方向 top5（**样本内**，仅作上界参照）；
变体：V0 现状｜P1 池=2026top5｜P2 池=跨期先验｜P3 先验池取 top3｜P4 先验池且不要 gate 资格｜P5 先验池∩gate 且 top3。

**内存纪律**：全部聚合下推 SQL（临时成分表 + group by，出库 ~170×13 行）。
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
OUT = os.path.join(DATA, 'eval_wolf_pool_prior.json')

# 语料方向名 → 13 主题（人工映射，依据 `wolf_period_directions.json` 抽出的名字；只做归并，不改语义）
DIR2THEME = {
    '光': 'AI/算力/科技', '光通信': 'AI/算力/科技', 'CPO': 'AI/算力/科技', '光模块': 'AI/算力/科技',
    'AI': 'AI/算力/科技', 'AI硬': 'AI/算力/科技', 'AI软': 'AI/算力/科技', '人工智能': 'AI/算力/科技',
    '算力': 'AI/算力/科技', '国算': 'AI/算力/科技', 'AIDC': 'AI/算力/科技', '数据中心': 'AI/算力/科技',
    '服务器': 'AI/算力/科技', 'PCB': 'AI/算力/科技', '液冷': 'AI/算力/科技', '游戏': 'AI/算力/科技',
    '传媒': 'AI/算力/科技', '科技': 'AI/算力/科技', '半导体': '半导体/芯片', '芯片': '半导体/芯片',
    '存储': '半导体/芯片', '存储模组': '半导体/芯片', '光刻': '半导体/芯片', '封测': '半导体/芯片',
    '材料': '半导体/芯片', '电子化学品': '半导体/芯片',
    '医药': '医药', '疫苗': '医药', '创新药': '医药', '医疗': '医药',
    '新能源': '新能源/电池', '锂电': '新能源/电池', '锂电池': '新能源/电池', '电池': '新能源/电池',
    '光伏': '新能源/电池', '储能': '新能源/电池', '风电': '新能源/电池', '固态': '新能源/电池',
    '机器人': '机器人/智能制造', '工业母机': '机器人/智能制造', '机床': '机器人/智能制造',
    '汽车': '汽车/智驾', '智驾': '汽车/智驾', '整车': '汽车/智驾',
    '白酒': '消费/内需', '消费': '消费/内需', '食品': '消费/内需', '家电': '消费/内需', '免税': '消费/内需',
    '农业': '农业', '猪': '农业', '养殖': '农业', '种业': '农业',
    '电力': '电力/公用', '电网': '电力/公用', '核电': '电力/公用', '公用': '电力/公用',
    '基建': '稳增长/基建', '地产': '稳增长/基建', '建筑': '稳增长/基建', '水利': '稳增长/基建',
    '有色': '资源/周期', '化工': '资源/周期', '煤炭': '资源/周期', '石油': '资源/周期', '原油': '资源/周期',
    '黄金': '资源/周期', '稀土': '资源/周期', '小金属': '资源/周期', '贵金属': '资源/周期', '纸': '资源/周期',
    '金融': '金融', '券商': '金融', '银行': '金融', '保险': '金融', '互金': '金融',
    '军工': '军工/航天', '航天': '军工/航天', '商航': '军工/航天', '卫星': '军工/航天', '航空': '军工/航天',
}


def stat(v):
    n = len(v)
    if not n:
        return {'n': 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {'n': n, 'mean': round(m, 3), 't': round(m / (sd / n ** 0.5), 2) if sd else None,
            'pos': round(sum(1 for x in v if x > 0) / n, 3)}


def load_prior_pool():
    """跨期先验池（<=2025）：各期方向按出现次数累加 → 13 主题得分。"""
    p = os.path.join(DATA, 'wolf_period_directions.json')
    if not os.path.exists(p):
        return {}, {}
    d = json.load(open(p, encoding='utf-8'))
    score, detail = collections.Counter(), collections.Counter()
    for per, obj in (d.get('periods') or {}).items():
        for it in (obj.get('directions') or []):
            th = DIR2THEME.get((it.get('name') or '').strip())
            if th:
                score[th] += int(it.get('count') or 1)
                detail['%s:%s' % (per, it.get('name'))] = th
    return dict(score), dict(detail)


def main():
    db = SessionLocal()
    uni, lead, allc = MS.load_universe()
    if not uni:
        print('[pool] 主题成分为空', flush=True)
        return 2
    db.execute(text("SET LOCAL work_mem = '64MB'"))
    db.execute(text("DROP TABLE IF EXISTS _pp_memb"))
    db.execute(text("CREATE TEMP TABLE _pp_memb (ts_code varchar(16), theme varchar(32))"))
    pairs = [{'ts': c, 'th': th} for th, codes in uni.items() for c in codes]
    for k in range(0, len(pairs), 2000):
        db.execute(text("INSERT INTO _pp_memb (ts_code, theme) VALUES (:ts, :th)"), pairs[k:k + 2000])
    rows = db.execute(text("""
        SELECT b.trade_date AS d, m.theme AS th, avg(b.pct_chg)::float8 AS pc
        FROM mkt_bars_daily b JOIN _pp_memb m ON m.ts_code = b.ts_code
        WHERE b.pct_chg IS NOT NULL GROUP BY 1, 2
    """)).all()
    mrows = db.execute(text("SELECT trade_date d, avg(pct_chg)::float8 pc FROM mkt_bars_daily "
                            "WHERE pct_chg IS NOT NULL GROUP BY 1")).all()
    g = pd.DataFrame(rows, columns=['d', 'th', 'pc'])
    pc = g.pivot_table(index='d', columns='th', values='pc', aggfunc='first').sort_index().astype('float64') / 100.0
    mk = pd.Series({r[0]: r[1] for r in mrows}).reindex(pc.index).astype('float64') / 100.0
    idx = pc.index.tolist()
    cmp5 = (1 + pc).rolling(5).apply(np.prod, raw=True)
    cmp5m = (1 + mk).rolling(5).apply(np.prod, raw=True)
    r5 = cmp5.sub(cmp5m, axis=0)
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

    prior_score, prior_detail = load_prior_pool()
    pool2026 = [t for t, _ in his_cnt.most_common(5)]
    pool_prior = [t for t, _ in sorted(prior_score.items(), key=lambda kv: -kv[1]) if t in uni][:5]
    print('[pool] 他 2026 方向分布 top8: %s' % his_cnt.most_common(8), flush=True)
    print('[pool] 跨期先验池(<=2025) 得分: %s' % sorted(prior_score.items(), key=lambda kv: -kv[1])[:10], flush=True)
    print('[pool] P_his2026 = %s' % pool2026, flush=True)
    print('[pool] P_prior   = %s' % pool_prior, flush=True)

    variants = collections.defaultdict(list)
    picks = collections.defaultdict(collections.Counter)
    pool_top3 = {}
    pool_diag = collections.defaultdict(list)   # 池内对照：期望命中率 / 池内等权收益（隔离 r5 选择的价值）
    for i in range(12, len(idx) - 5):
        d = idx[i]
        r5d = {t: v for t, v in r5.iloc[i].items() if v == v}
        if len(r5d) < 4:
            continue
        gs = [t for t in (gates.get(d) or []) if t in r5d] or list(r5d)
        pool = sorted(gs, key=lambda t: -r5d[t])
        pool_top3[d] = pool[:3]

        def pick(sub, use_gate=True, k=1, mid_positive=False):
            cand = [t for t in pool if (t in sub)] if use_gate else \
                   sorted([t for t in r5d if t in sub], key=lambda t: -r5d[t])
            if mid_positive:      # 中期(20日)相对强度为正的闸（不设方向池）
                cand = [t for t in cand if (r20d.get(t) or 0) > 0] or cand
            return cand[:k]

        # P6/P7：**他最近实际在做的方向**（滚动，PIT 可用——他的发言是公开的）→ 当期"主线池"
        roll5 = sorted({t for dd in idx[max(0, i - 5):i] for t in (his.get(dd) or [])})
        roll10 = sorted({t for dd in idx[max(0, i - 10):i] for t in (his.get(dd) or [])})
        roll20 = sorted({t for dd in idx[max(0, i - 20):i] for t in (his.get(dd) or [])})
        roll15 = sorted({t for dd in idx[max(0, i - 15):i] for t in (his.get(dd) or [])})
        # P8/P9：**结构判据**（不看语料）——更长窗口的主线 + 成交额占比
        r20d = {t: (float((1 + pc[t].iloc[max(0, i - 19):i + 1]).prod() - 1)
                    - float((1 + mk.iloc[max(0, i - 19):i + 1]).prod() - 1)) for t in r5d}
        struct_top = [t for t, _ in sorted(r20d.items(), key=lambda kv: -kv[1])][:3]
        struct_top1 = struct_top[:1]
        both_agree = sorted(set(roll10) & set(struct_top))
        for name, sub, ug, k in (('V0_基线(现状D1)', list(r5d), True, 1),
                                 ('P1_池=2026top5∩gate(样本内上界)', pool2026, True, 1),
                                 ('P2_池=跨期静态先验∩gate', pool_prior, True, 1),
                                 ('P3_跨期先验∩gate_top3', pool_prior, True, 3),
                                 ('P4_跨期先验(不用gate)', pool_prior, False, 1),
                                 ('P5_跨期先验(不用gate)_top3', pool_prior, False, 3),
                                 ('P6_池=他近10日doing∩gate', roll10, True, 1),
                                 ('P7_池=他近20日doing∩gate', roll20, True, 1),
                                 ('P8_池=结构(近20日相对强度top3)∩gate', struct_top, True, 1),
                                 ('P9_池=P6∪P8∩gate', sorted(set(roll10) | set(struct_top)), True, 1),
                                 ('P10_池=P6∩P8(他做且中期强)', both_agree, True, 1),
                                 ('P11_池=他近5日doing∩gate', roll5, True, 1),
                                 ('P12_池=他近20日doing∩P8结构池', sorted(set(roll20) & set(struct_top)), True, 1),
                                 ('P13_闸=r20>0(不设池)∩gate', list(r5d), True, 1),
                                 ('P14_池=他近5日∩结构top3', sorted(set(roll5) & set(struct_top)), True, 1),
                                 ('P15_池=他近10日∩结构top3(不用gate)', sorted(set(roll10) & set(struct_top)), False, 1),
                                 ('P16_池=他近10日∩结构top1', sorted(set(roll10) & set(struct_top1)), True, 1),
                                 ('P17_池=他近15日∩结构top3', sorted(set(roll15) & set(struct_top)), True, 1)):
            _mid = (name == 'P13_闸=r20>0(不设池)∩gate')  # 该变体用"中期为正"闸而非方向池
            sel = pick(sub, ug, k, _mid)
            if not sel:
                continue
            fs = fwd5.iloc[i]
            if name in ('P6_池=他近10日doing∩gate', 'P8_池=结构(近20日相对强度top3)∩gate',
                        'P10_池=P6∩P8(他做且中期强)', 'P11_池=他近5日doing∩gate',
                        'P12_池=他近20日doing∩P8结构池'):
                cand = [t for t in pool if t in sub]        # 池内（受 gate 限制）
                if cand:
                    hitn = len(set(cand) & set(his.get(d) or [])) / len(cand)
                    pv = [fs.get(t) for t in cand]
                    pv = [float(x) for x in pv if x is not None and x == x]
                    if pv:
                        pool_diag[name].append((d, len(cand), hitn, sum(pv) / len(pv)))
            vals = [fs.get(t) for t in sel]
            vals = [float(v) for v in vals if v is not None and v == v]
            if not vals:
                continue
            variants[name].append((d, sel[0], sum(vals) / len(vals)))
            picks[name][sel[0]] += 1

    print('\n【验收】所选方向后 5 日超额（%）；他方向 top1/top3 命中；P2 是样本外（池来自 <=2025）', flush=True)
    print('| 变体 | n | 均值 | t | 胜率 | 他落top1 | 他落top3 | 农业+医药选中 |')
    print('|---|---|---|---|---|---|---|---|')
    names = ['V0_基线(现状D1)', 'P1_池=2026top5∩gate(样本内上界)', 'P2_池=跨期静态先验∩gate',
             'P3_跨期先验∩gate_top3', 'P4_跨期先验(不用gate)', 'P5_跨期先验(不用gate)_top3',
             'P6_池=他近10日doing∩gate', 'P7_池=他近20日doing∩gate',
             'P8_池=结构(近20日相对强度top3)∩gate', 'P9_池=P6∪P8∩gate',
             'P10_池=P6∩P8(他做且中期强)', 'P11_池=他近5日doing∩gate',
             'P12_池=他近20日doing∩P8结构池', 'P13_闸=r20>0(不设池)∩gate',
             'P14_池=他近5日∩结构top3', 'P15_池=他近10日∩结构top3(不用gate)',
             'P16_池=他近10日∩结构top1', 'P17_池=他近15日∩结构top3']
    out = {'pools': {'his2026': pool2026, 'prior': pool_prior, 'prior_score': prior_score,
                     'his_cnt': dict(his_cnt), 'prior_detail': prior_detail},
           'variants': {}, 'names': names}
    for name in names:
        v = variants.get(name) or []
        if not v:
            print('| %s | (无样本) | | | | | | |' % name, flush=True)
            continue
        s = stat([x[2] for x in v])
        hd = [d for d, _, _ in v if his.get(d)]
        n1 = sum(1 for d, t, _ in v if t in (his.get(d) or []))
        n3 = sum(1 for d, t, _ in v if set(his.get(d) or []) & set((pool_top3.get(d) or [t])))
        bad = picks[name]['农业'] + picks[name]['医药'] + picks[name]['电力/公用']
        out['variants'][name] = {**s, 'his_top1': round(n1 / max(1, len(hd)), 3),
                                 'his_top3': round(n3 / max(1, len(hd)), 3), 'n_his_days': len(hd),
                                 'picks': dict(picks[name]), 'agri_med_elec': bad}
        print('| %s | %s | %s | %s | %s | %d/%d=%.0f%% | %d/%d=%.0f%% | %d |'
              % (name, s.get('n'), s.get('mean'), s.get('t'), s.get('pos'),
                 n1, len(hd), 100.0 * n1 / max(1, len(hd)), n3, len(hd), 100.0 * n3 / max(1, len(hd)), bad),
              flush=True)
    print('\n【池内对照】隔离「r5 选择」本身的价值：池内随机命中的期望 vs 我们选的 top1', flush=True)
    print('| 池 | 平均池大小 | 池内期望命中率(随机) | 池内等权后5日超额 | top1 命中率 | top1 收益 |', flush=True)
    print('|---|---|---|---|---|---|', flush=True)
    out['pool_diag'] = {}
    for name, recs in pool_diag.items():
        if not recs:
            continue
        sz = sum(r[1] for r in recs) / len(recs)
        hf = sum(r[2] for r in recs) / len(recs)
        pm = stat([r[3] for r in recs])
        tv = out['variants'].get(name) or {}
        out['pool_diag'][name] = {'avg_size': round(sz, 2), 'rand_hit': round(hf, 3),
                                  'pool_mean': pm, 'top1_hit': tv.get('his_top1'), 'top1': tv}
        print('| %s | %.2f | %.0f%% | %s | %.0f%% | %s |'
              % (name.split('_')[0], sz, 100 * hf, pm.get('mean'),
                 100 * (tv.get('his_top1') or 0), tv.get('mean')), flush=True)

    print('\n【分段稳定性 H1/H2】', flush=True)
    mid = idx[len(idx) // 2 + 12]
    print('| 变体 | H1 均值 | H1 t | H2 均值 | H2 t |', flush=True)
    print('|---|---|---|---|---|', flush=True)
    for name in names:
        v = variants.get(name) or []
        if not v:
            continue
        h1 = stat([x[2] for x in v if x[0] <= mid])
        h2 = stat([x[2] for x in v if x[0] > mid])
        out['variants'][name]['H1'] = h1
        out['variants'][name]['H2'] = h2
        print('| %s | %s | %s | %s | %s |' % (name, h1.get('mean'), h1.get('t'), h2.get('mean'), h2.get('t')), flush=True)

    print('\n【选中分布】', flush=True)
    for name in names:
        if picks.get(name):
            print('   %-26s %s' % (name, '、'.join('%s(%d)' % (t, n) for t, n in picks[name].most_common(6))), flush=True)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n[pool] 结果 → %s' % OUT, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
