# -*- coding: utf-8 -*-
"""eval_pool_alignment.py — 「我们的主线判定与他到底有多一致」的量化（本地跑，经 SSH 隧道只读聚合）。

不只看 top1 命中，还算**池层面的 recall/precision**（池 = 我们对"当期主线在哪几个方向"的判断）：
  · recall    = |池 ∩ 他当日 doing| / |他当日 doing| —— 他做的方向里，有多少落在我们的池里
  · precision = |池 ∩ 他当日 doing| / |池|             —— 我们池里的方向，有多少是他真在做的
  · top1/top3 = 我们的选择是否命中他的方向（top3 用"池内 r5 排名前 3"）
另外给**逐日明细**（他的方向全部在池内 / 部分在 / 完全不在 的天数分布），以及分月 recall。

口径：池 = 主题近 5 日成交额占比 top3 ∩ 近 5 日相对强度 > 0（T6）；主题用 13 主题（概念库）。
数据：生产 PG（隧道只读；每个查询只出聚合/少量行）。
"""
import collections
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.dsh-tmp/wolfbt'))
from local_pg import ensure_tunnel, DSN      # noqa: E402
ensure_tunnel()

THEMES = ["AI/算力/科技", "半导体/芯片", "医药", "新能源/电池", "机器人/智能制造", "汽车/智驾",
          "消费/内需", "农业", "电力/公用", "稳增长/基建", "资源/周期", "金融", "军工/航天"]
KW = {
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
    K = int(sys.argv[sys.argv.index('--k') + 1]) if '--k' in sys.argv else 3
    start = sys.argv[sys.argv.index('--start') + 1] if '--start' in sys.argv else '20260105'
    end = sys.argv[sys.argv.index('--end') + 1] if '--end' in sys.argv else '20260911'
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    # 主题成分临时表（与其它脚本一致：概念名 LIKE 匹配）
    cur.execute("CREATE TEMP TABLE _a_memb (ts_code varchar(16), theme varchar(32))")
    rows = []
    for th, kws in KW.items():
        for kw in kws:
            cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name LIKE %s", ('%' + kw + '%',))
            for (ts,) in cur.fetchall():
                if ts:
                    rows.append((ts, th))
    cur.executemany("INSERT INTO _a_memb VALUES (%s,%s)", list(set(rows)))
    cur.execute("""
        SELECT b.trade_date, m.theme, avg(b.pct_chg)::float8, sum(b.amount)::float8
        FROM mkt_bars_daily b JOIN _a_memb m ON m.ts_code=b.ts_code
        WHERE b.trade_date >= %s AND b.trade_date <= %s AND b.pct_chg IS NOT NULL GROUP BY 1,2
    """, (start, end))
    g = pd.DataFrame(cur.fetchall(), columns=['d', 'th', 'pc', 'amt'])
    cur.execute("SELECT trade_date, avg(pct_chg)::float8, sum(amount)::float8 FROM mkt_bars_daily "
                "WHERE trade_date >= %s AND trade_date <= %s AND pct_chg IS NOT NULL GROUP BY 1", (start, end))
    m = pd.DataFrame(cur.fetchall(), columns=['d', 'pc', 'amt'])
    cur.execute("SELECT trade_date, payload FROM wolf_actual_mainline WHERE status='ok'")
    his_raw = cur.fetchall()
    cur.execute("SELECT dir_text, theme FROM wolf_dir_theme_map")
    dmap = dict(cur.fetchall())
    conn.close()

    pc = g.pivot_table(index='d', columns='th', values='pc', aggfunc='first').sort_index().astype(float) / 100.0
    amt = g.pivot_table(index='d', columns='th', values='amt', aggfunc='first').reindex(pc.index).astype(float)
    mk = m.set_index('d')['pc'].astype(float).reindex(pc.index) / 100.0
    mamt = m.set_index('d')['amt'].astype(float).reindex(pc.index)
    idx = pc.index.tolist()
    r5 = ((1 + pc).rolling(5).apply(np.prod, raw=True).sub((1 + mk).rolling(5).apply(np.prod, raw=True), axis=0))
    share5 = amt.rolling(5).sum().div(mamt.rolling(5).sum(), axis=0)
    f5 = (1 + pc).rolling(5).apply(np.prod, raw=True).shift(-5)
    f5m = (1 + mk).rolling(5).apply(np.prod, raw=True).shift(-5)
    fwd5 = (f5.sub(f5m, axis=0)) * 100

    import json
    his = {}
    for d, payload in his_raw:
        p = payload if isinstance(payload, dict) else json.loads(payload or '{}')
        s = sorted({dmap.get((x.get('dir') or '').strip()) for x in (p.get('doing') or [])} - {None, '非主题'})
        if s:
            his[d] = s
    print('[align] 期间 %s→%s | 交易日 %d | 有他方向的交易日 %d' % (start, end, len(idx), len(his)), flush=True)

    rec, prec, h1, h3, allin, nonein, bymonth = [], [], [], [], 0, 0, collections.defaultdict(lambda: [[], []])
    rets = []
    picks, poolsel = collections.Counter(), collections.Counter()
    for i in range(20, len(idx) - 5):
        d = idx[i]
        hs = set(his.get(d) or [])
        if not hs:
            continue
        r5d = {t: v for t, v in r5.iloc[i].items() if v == v}
        sh = {t: v for t, v in share5.iloc[i].items() if v == v}
        if len(r5d) < 4 or len(sh) < 5:
            continue
        top3 = {t for t, _ in sorted(sh.items(), key=lambda kv: -kv[1])[:K]}
        pool = {t for t in top3 if (r5d.get(t) or 0) > 0}          # T6 池
        if not pool:
            continue
        rec.append(len(pool & hs) / len(hs))
        prec.append(len(pool & hs) / len(pool))
        bymonth[d[:6]][0].append(len(pool & hs) / len(hs))
        ranked = [t for t in sorted(r5d, key=lambda t: -r5d[t]) if t in pool]     # 池内按 r5 排
        hit1 = 1 if (ranked and ranked[0] in hs) else 0
        if ranked:
            h1.append(hit1)
            poolsel[ranked[0]] += 1
        h3.append(1 if set(ranked[:3]) & hs else 0)
        fv = fwd5.iloc[i].get(ranked[0]) if ranked else None
        if fv is not None and fv == fv:
            rets.append(float(fv))
        bymonth[d[:6]][1].append(hit1)
        if hs <= pool:
            allin += 1
        if not (hs & pool):
            nonein += 1
        picks[d] = ranked[0] if ranked else None

    n = len(rec)
    print('\n【池层面的一致性】（池 = 量能占比 top%d ∩ r5>0；n=%d 天）' % (K, n), flush=True)
    print('  recall    他做的方向落在我们池里的比例：**%.0f%%**' % (100 * sum(rec) / n), flush=True)
    print('  precision 我们池里的方向是他真在做的比例：**%.0f%%**' % (100 * sum(prec) / n), flush=True)
    print('  他的方向**全部**在池内：%d 天（%.0f%%）｜**完全不在**池内：%d 天（%.0f%%）'
          % (allin, 100 * allin / n, nonein, 100 * nonein / n), flush=True)
    print('  top1 命中：%.0f%%    top3 命中：%.0f%%    top1 后5日超额：%s (n=%d)'
          % (100 * sum(h1) / max(1, len(h1)), 100 * sum(h3) / max(1, len(h3)),
             stat(rets), len(rets)), flush=True)
    print('\n【分月 recall / top1】', flush=True)
    print('| 月份 | recall | top1 命中率 |', flush=True)
    print('|---|---|---|', flush=True)
    for mo in sorted(bymonth):
        r, h = bymonth[mo]
        print('| %s | %.0f%% | %.0f%% |' % (mo, 100 * sum(r) / len(r), 100 * sum(h) / len(h)), flush=True)
    print('\n【我们 top1 选中的方向分布】%s' % '、'.join('%s(%d)' % (t, c) for t, c in poolsel.most_common(8)),
          flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
