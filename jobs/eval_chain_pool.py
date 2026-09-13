# -*- coding: utf-8 -*-
"""eval_chain_pool.py — 他剩下两条判据的验收：**链条联动**（"光芯存算"）+ **龙头带动**（"光前三就 3 只票"）。

语料依据
  · 链条联动：「**光芯存算**」是他的固定说法；「半导体好的时候出去 **光好的时候回来**」（2026-06-30）；
    「缩量的情况下**大光动都不动** …这能有什么持续性？…不玩就对了」（2026-08-06）。
  · 龙头带动：「**光前三 就 3 只票** 二线光 10 只 光纤 5 只…」（2026-06-03）；
    「今天的突破…**必须要有光**，因为做光的资金不可能在突破的节点**不把中继推新高**」（2026-05-11）。

**要回答**：把这两条加进池，能否把"他的方向落我们池里"的比例（recall 55%）和"完全不在池内"的 26% 天改善，
同时不牺牲选择质量（top1 命中 48%、top3 74%、top1 后 5 日超额 +1.425%）？

数据：本地 `data/bars_2026.csv.gz`（2026 行情）+ `data/stock_pool_local.db`（概念库）
     + `data/crowding_pit/rotation_crowd_pit_*.json`（15 子方向的概念列表）
     + 生产 PG 经进程内隧道的**少量**读取（他的 doing、gate 资格：各 ~150 行）。

口径：`r5` = 主题/子方向近 5 日等权相对强度；`share5` = 近 5 日成交额占全市场比；
**链内联动** = 该子方向所属"链"中 r5>0 的子方向个数；**龙头带动** = 该子方向内按近 5 日成交额排名前 3 的个股，
其近 5 日涨幅均值为正。
"""
import collections
import glob
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, '.dsh-tmp/wolfbt')
from local_pg import ensure_tunnel, DSN      # noqa: E402

BARS = 'data/bars_2026.csv.gz'
POOLDB = 'data/stock_pool_local.db'
COLS = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close', 'pct_chg',
        'vol', 'amount', 'total_mv', 'turnover_rate']
# 15 子方向 → 链（产业链口径；"科技/AI(总集)"是伞概念，不参与联动计数）
CHAIN = {
    'AI/科技链': ['光通信', '存储', '芯片/半导体', '材料', '国算/算力', '液冷', '铜缆/电源', 'AI应用', 'AI终端'],
    '医药链': ['医药医疗'], '资源链': ['有色贵金属'], '金融链': ['金融'],
    '军工链': ['军工航天'], '电力链': ['电力'],
}
SUB2THEME = {
    '科技/AI(总集)': 'AI/算力/科技', 'AI应用': 'AI/算力/科技', 'AI终端': 'AI/算力/科技',
    '国算/算力': 'AI/算力/科技', '液冷': 'AI/算力/科技', '光通信': 'AI/算力/科技',
    '铜缆/电源': 'AI/算力/科技', '存储': '半导体/芯片', '材料': '半导体/芯片',
    '芯片/半导体': '半导体/芯片', '医药医疗': '医药', '电力': '电力/公用',
    '金融': '金融', '军工航天': '军工/航天', '有色贵金属': '资源/周期',
}
THEME_KW = {   # 13 主题（与既有分析口径一致）
    "半导体/芯片": ["半导体", "芯片", "光刻", "封测", "存储"],
    "AI/算力/科技": ["算力", "人工智能", "光通信", "光器件", "CPO", "数据中心", "服务器"],
    "医药": ["医药", "创新药", "CXO", "疫苗", "医疗器械", "中药"],
    "新能源/电池": ["锂电", "电池", "光伏", "储能", "风电", "新能源"],
    "机器人/智能制造": ["机器人", "机床", "工业母机", "自动化", "智能制造"],
    "汽车/智驾": ["汽车", "智能驾驶", "车路云", "智能网联", "汽车零部件", "激光雷达"],
    "消费/内需": ["白酒", "食品", "饮料", "零售", "家电", "免税", "餐饮"],
    "农业": ["农业", "生猪", "养殖", "种业", "饲料", "种植"],
    "电力/公用": ["电力", "电网", "核电", "水电", "燃气", "绿电"],
    "稳增长/基建": ["基建", "建筑", "地产", "水泥", "钢铁", "工程机械", "水利"],
    "资源/周期": ["有色", "煤炭", "黄金", "稀土", "石油", "化工", "小金属"],
    "金融": ["券商", "银行", "保险", "多元金融"],
    "军工/航天": ["军工", "航天", "航空", "卫星", "国防", "低空"],
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
    print('[chain] 读本地行情…', flush=True)
    df = pd.read_csv(BARS, header=None, names=COLS, compression='gzip',
                     dtype={'ts_code': str, 'trade_date': str})
    df['pc'] = pd.to_numeric(df['pct_chg'], errors='coerce') / 100.0
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df = df[['trade_date', 'ts_code', 'pc', 'amount', 'close']].dropna(subset=['pc'])
    wide = df.pivot_table(index='trade_date', columns='ts_code', values='pc', aggfunc='first').sort_index()
    amt = df.pivot_table(index='trade_date', columns='ts_code', values='amount', aggfunc='sum').reindex(wide.index)
    cls = df.pivot_table(index='trade_date', columns='ts_code', values='close', aggfunc='first').reindex(wide.index)
    idx = wide.index.tolist()
    mk = wide.mean(axis=1)
    mamt = amt.sum(axis=1)
    print('[chain] 行情 %d 天 × %d 只' % (len(idx), wide.shape[1]), flush=True)

    con = sqlite3.connect(POOLDB)
    cmap = collections.defaultdict(set)
    for cn, ts in con.execute("SELECT concept_name, ts_code FROM stock_concept_map"):
        cmap[cn].add(ts)
    con.close()
    # 15 子方向成分（用拥挤度快照里的概念列表，取并集）
    subs = {}
    for p in glob.glob('data/crowding_pit/rotation_crowd_pit_*.json'):
        for sub, v in ((json.load(open(p, encoding='utf-8')).get('universe') or {}).items()):
            for c in (v.get('concepts') or []):
                subs.setdefault(sub, set()).update(cmap.get(c, set()))
    subs = {k: sorted(v & set(wide.columns)) for k, v in subs.items() if v}
    themes = {}
    for th, kws in THEME_KW.items():
        s = set()
        for kw in kws:
            for cn, codes in cmap.items():
                if kw in cn:
                    s |= codes
        themes[th] = sorted(s & set(wide.columns))
    print('[chain] 子方向 %d 个 | 主题 %d 个' % (len(subs), len(themes)), flush=True)

    def series(members):
        cs = [c for c in members if c in wide.columns]
        pc = wide[cs].mean(axis=1)
        r5 = (1 + pc).rolling(5).apply(np.prod, raw=True).sub((1 + mk).rolling(5).apply(np.prod, raw=True))
        sh = amt[cs].sum(axis=1).rolling(5).sum() / mamt.rolling(5).sum()
        return r5, sh, pc

    # 子方向 r5 / 占比 + 链内联动
    sub_r5, sub_sh, sub_pc = {}, {}, {}
    for sub, mem in subs.items():
        sub_r5[sub], sub_sh[sub], sub_pc[sub] = series(mem)
    link = pd.DataFrame({c: sum((sub_r5[s] > 0).astype(int) for s in ss) for c, ss in CHAIN.items()})
    # 龙头带动：子方向内按近 5 日成交额排名前 3 的个股，其近 5 日涨幅均值
    lead = {}
    a5 = amt.rolling(5).sum()
    r5stk = cls.pct_change(5)
    for sub, mem in subs.items():
        cs = [c for c in mem if c in a5.columns]
        if len(cs) < 3:
            continue
        A, R = a5[cs].to_numpy(), r5stk[cs].to_numpy()
        out = np.full(len(idx), np.nan)
        for i in range(len(idx)):
            a = A[i]
            if not np.isfinite(a).any():
                continue
            top = np.argsort(-np.nan_to_num(a, nan=-1e18))[:3]
            vals = R[i][top]
            vals = vals[np.isfinite(vals)]
            if len(vals):
                out[i] = vals.mean()
        lead[sub] = pd.Series(out, index=idx)
    theme_r5, theme_sh, theme_pc = {}, {}, {}
    for th, mem in themes.items():
        theme_r5[th], theme_sh[th], theme_pc[th] = series(mem)
    R5 = pd.DataFrame(theme_r5)
    SH = pd.DataFrame(theme_sh)
    # 主题级"链内联动"（该主题映射到的链里在动的子方向数）与"龙头带动"（子方向龙头均值）
    T2CHAIN = {}
    for ch, ss in CHAIN.items():
        for s in ss:
            T2CHAIN.setdefault(SUB2THEME.get(s), ch)
    theme_link = pd.DataFrame({th: link[T2CHAIN[th]] if th in T2CHAIN else pd.Series(99, index=idx)
                               for th in R5.columns})
    theme_lead = pd.DataFrame({th: (pd.concat([lead[s] for s in CHAIN[T2CHAIN[th]] if s in lead], axis=1).mean(axis=1)
                                     if th in T2CHAIN else pd.Series(1.0, index=idx)) for th in R5.columns})

    # 他的方向
    ensure_tunnel()
    import psycopg2
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    cur.execute("SELECT trade_date, payload FROM wolf_actual_mainline WHERE status='ok'")
    hr = cur.fetchall()
    cur.execute("SELECT dir_text, theme FROM wolf_dir_theme_map")
    dmap = dict(cur.fetchall())
    conn.close()
    his = {}
    for d, payload in hr:
        p = payload if isinstance(payload, dict) else json.loads(payload or '{}')
        s = sorted({dmap.get((x.get('dir') or '').strip()) for x in (p.get('doing') or [])} - {None, '非主题'})
        if s:
            his[d] = s

    PC = pd.DataFrame(theme_pc)          # 主题**绝对**日收益（前瞻收益必须用它算）
    f5 = (1 + PC).rolling(5).apply(np.prod, raw=True).shift(-5)
    f5m = (1 + mk).rolling(5).apply(np.prod, raw=True).shift(-5)
    fwd5 = (f5.sub(f5m, axis=0)) * 100

    K = 3
    variants = {
        'V0_T6(现状:量能top3∩r5>0)': lambda i, d, pool: pool,
        'C1_+链内联动≥2(单链主题放行)': lambda i, d, pool: {t for t in pool
            if (theme_link[t].iloc[i] >= 2) or (theme_link[t].iloc[i] >= 99)},
        'C2_+龙头带动(子方向龙头均值>0)': lambda i, d, pool: {t for t in pool if (theme_lead[t].iloc[i] or 0) > 0},
        'C3_+两者': lambda i, d, pool: {t for t in pool
            if ((theme_link[t].iloc[i] >= 2) or (theme_link[t].iloc[i] >= 99)) and (theme_lead[t].iloc[i] or 0) > 0},
        'C4_池替换=量能top3∩r5>0∩联动≥2(不解释单链)': lambda i, d, pool: {t for t in pool if theme_link[t].iloc[i] >= 2},
        'C5_子方向池:量能top3子∩r5>0→主题': lambda i, d, pool: sub_pool(i, 3),
        'C6_子方向池:量能top5子∩r5>0→主题': lambda i, d, pool: sub_pool(i, 5),
        'C7_+链内联动≥4': lambda i, d, pool: {t for t in pool
            if (theme_link[t].iloc[i] >= 4) or (theme_link[t].iloc[i] >= 99)},
        'C8_龙头强势(龙头均值>主题r5)': lambda i, d, pool: {t for t in pool
            if (theme_lead[t].iloc[i] or 0) > (R5[t].iloc[i] or 0)},
    }
    SR5 = pd.DataFrame(sub_r5)
    SSH = pd.DataFrame(sub_sh)
    SSUB2T = pd.Series(SUB2THEME)

    def sub_pool(i, k):
        r5d = {t: v for t, v in SR5.iloc[i].items() if v == v}
        sh = {t: v for t, v in SSH.iloc[i].items() if v == v}
        if len(r5d) < 5 or len(sh) < 5:
            return set()
        topk = [t for t, _ in sorted(sh.items(), key=lambda kv: -kv[1])[:k]]
        return {SUB2THEME.get(t) for t in topk if (r5d.get(t) or 0) > 0} - {None}

    # 诊断：V0(T6) 下"完全不在池内"的日子，他做的是什么方向、我们池里是什么
    gap_his, gap_pool, gap_days = collections.Counter(), collections.Counter(), []
    for i in range(20, len(idx) - 5):
        d = idx[i]
        hs = set(his.get(d) or [])
        if not hs:
            continue
        r5d = {t: v for t, v in R5.iloc[i].items() if v == v}
        sh = {t: v for t, v in SH.iloc[i].items() if v == v}
        if len(r5d) < 4 or len(sh) < 5:
            continue
        topK = {t for t, _ in sorted(sh.items(), key=lambda kv: -kv[1])[:K]}
        pool = {t for t in topK if (r5d.get(t) or 0) > 0}
        if not pool:
            continue
        if not (hs & pool):
            gap_days.append(d)
            for t in hs:
                gap_his[t] += 1
            for t in pool:
                gap_pool[t] += 1
    print('\n【诊断】"完全不在池内"的 %d 天里：他做的方向 %s' % (len(gap_days), gap_his.most_common(8)), flush=True)
    print('        同期我们池里的方向 %s' % (gap_pool.most_common(8),), flush=True)
    print('        日期：%s' % '、'.join(gap_days[:20]), flush=True)

    print('\n| 变体 | n天 | recall | precision | 全部在池内 | 完全不在池内 | top1命中 | top3命中 | top1超额 | t | 胜率 |',
          flush=True)
    print('|---|---|---|---|---|---|---|---|---|---|---|', flush=True)
    out = {}
    for name, fn in variants.items():
        rec, prec, h1, h3, allin, nonein, rets = [], [], [], [], 0, 0, []
        for i in range(20, len(idx) - 5):
            d = idx[i]
            hs = set(his.get(d) or [])
            if not hs:
                continue
            r5d = {t: v for t, v in R5.iloc[i].items() if v == v}
            sh = {t: v for t, v in SH.iloc[i].items() if v == v}
            if len(r5d) < 4 or len(sh) < 5:
                continue
            topK = {t for t, _ in sorted(sh.items(), key=lambda kv: -kv[1])[:K]}
            pool = fn(i, d, {t for t in topK if (r5d.get(t) or 0) > 0})
            if not pool:
                continue
            rec.append(len(pool & hs) / len(hs))
            prec.append(len(pool & hs) / len(pool))
            ranked = [t for t in sorted(r5d, key=lambda t: -r5d[t]) if t in pool]
            h1.append(1 if (ranked and ranked[0] in hs) else 0)
            h3.append(1 if set(ranked[:3]) & hs else 0)
            if ranked:
                fv = fwd5.iloc[i].get(ranked[0])
                if fv is not None and fv == fv:
                    rets.append(float(fv))
            allin += 1 if hs <= pool else 0
            nonein += 1 if not (hs & pool) else 0
        n = max(1, len(rec))
        s = stat(rets)
        out[name] = {'n': n, 'recall': round(sum(rec) / n, 3), 'precision': round(sum(prec) / n, 3),
                     'all_in': round(allin / n, 3), 'none_in': round(nonein / n, 3),
                     'top1': round(sum(h1) / max(1, len(h1)), 3), 'top3': round(sum(h3) / max(1, len(h3)), 3),
                     'ret': s}
        print('| %s | %d | %.0f%% | %.0f%% | %.0f%% | %.0f%% | %.0f%% | %.0f%% | %s | %s | %s |'
              % (name, n, 100 * sum(rec) / n, 100 * sum(prec) / n, 100 * allin / n, 100 * nonein / n,
                 100 * sum(h1) / max(1, len(h1)), 100 * sum(h3) / max(1, len(h3)), s.get('mean'), s.get('t'),
                 s.get('pos')), flush=True)
    json.dump(out, open('data/eval_chain_pool.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n[chain] → data/eval_chain_pool.json', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
