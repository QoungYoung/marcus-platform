# -*- coding: utf-8 -*-
"""黄金坑窗口内板块分化盘点 — 科创50/创业板指入坑后，细分板块收益对比。

入坑检测: greed_value < pit_greed 阈值（与 backtest_golden_pit_stocks.py 一致）
数据源: golden_pit_snapshots + 中信一级/二级/申万一级行业指数日线
输出: docs/golden-pit-sector-divergence-report.md + data/backtest/pit_sector_divergence.json
"""
import json
import io
import statistics
from collections import defaultdict

import psycopg2
import pandas as pd

DB = 'postgresql://marcus:marcus123@127.0.0.1:18789/marcus_trading'
IDX_DIR = r'F:\pythonProject\AITrade\marcus-platform\data\backtest\指数数据'
OUT_JSON = r'F:\pythonProject\AITrade\marcus-platform\data\backtest\pit_sector_divergence.json'
OUT_MD = r'F:\pythonProject\AITrade\marcus-platform\docs\golden-pit-sector-divergence-report.md'

FUNDS = ['588000', '159915']
FUND_NAMES = {'588000': '科创50', '159915': '创业板指'}
PIT_GREED = {'588000': 0.348, '159915': 0.328}
PERIODS = [15, 20, 30]

CI_L1_FOCUS = ['电子', '通信', '计算机', '医药', '国防军工', '电力设备及新能源', '机械', '传媒', '汽车']
CI_L2_FOCUS = ['半导体', '通信设备', '计算机设备', '计算机软件', '消费电子', '新能源动力系统', '生物医药Ⅱ', '专用机械']
SW_L1_FOCUS = ['电子', '通信', '计算机', '医药生物', '国防军工', '电力设备', '机械设备']


def load_sector_pivots():
    pivots = {}
    df = pd.read_parquet(IDX_DIR + '/ci_l1_daily.parquet').reset_index()
    pivots['CI_L1'] = df.pivot_table(index='trade_date', columns='l1_name', values='close')
    df = pd.read_parquet(IDX_DIR + '/ci_l2_daily.parquet').reset_index()
    pivots['CI_L2'] = df.pivot_table(index='trade_date', columns='l2_name', values='close')
    df = pd.read_parquet(IDX_DIR + '/sw_l1_daily.parquet').reset_index()
    pivots['SW_L1'] = df.pivot_table(index='trade_date', columns='name', values='close')
    for k in pivots:
        pivots[k].index = pd.to_datetime(pivots[k].index)
    return pivots


def load_pit_data():
    conn = psycopg2.connect(DB, connect_timeout=5)
    cur = conn.cursor()
    cur.execute("""
        SELECT date, fund_code, greed_value, close_price
        FROM golden_pit_snapshots
        WHERE fund_code IN ('588000','159915') ORDER BY fund_code, date
    """)
    rows = cur.fetchall()
    conn.close()
    greed = defaultdict(list)
    etf_px = defaultdict(dict)
    for d, fc, g, close in rows:
        ts = pd.Timestamp(str(d)[:10])
        if g is not None:
            greed[fc].append((ts, float(g)))
        if close is not None:
            etf_px[fc][ts] = float(close)
    return greed, etf_px


def detect_pit_windows(greed_rows, threshold):
    """greed < threshold 的连续日(间隔<=3)合并为窗口，取首日入坑。"""
    events = []
    cur = None
    for d, g in sorted(greed_rows):
        if g >= threshold:
            if cur is not None:
                events.append(cur)
                cur = None
            continue
        if cur is not None and (d - cur['last']).days <= 3:
            cur['last'] = d
            cur['days'] += 1
            cur['lowest'] = min(cur['lowest'], g)
            continue
        if cur is not None:
            events.append(cur)
        cur = {'entry_date': d, 'last': d, 'days': 1, 'lowest': g}
    if cur is not None:
        events.append(cur)
    return events


def fwd_return(series, entry, n):
    cal = sorted(series.index)
    if entry not in cal:
        return None
    i = cal.index(entry)
    j = i + n
    if j >= len(cal):
        return None
    c0, c1 = series[entry], series[cal[j]]
    if pd.isna(c0) or pd.isna(c1) or c0 <= 0:
        return None
    return float(c1) / float(c0) - 1.0


def main():
    pivots = load_sector_pivots()
    greed, etf_px = load_pit_data()
    etf_series = {fc: pd.Series(etf_px[fc]) for fc in FUNDS}

    group_cfg = [
        ('中信一级', 'CI_L1', CI_L1_FOCUS),
        ('中信二级(含半导体细分)', 'CI_L2', CI_L2_FOCUS),
        ('申万一级', 'SW_L1', SW_L1_FOCUS),
    ]

    result = {}
    lines = []
    lines.append('# 黄金坑窗口内板块分化盘点报告')
    lines.append('')
    lines.append('> 生成日期: 2026-08-10 | 入坑检测: greed < pit_greed 阈值 | 行业数据截至 2026-06-26 | 分析对象: 科创50/创业板指')
    lines.append('')

    for fc in FUNDS:
        events = detect_pit_windows(greed[fc], PIT_GREED[fc])
        name = FUND_NAMES[fc]
        result[fc] = {'name': name, 'events': events}
        lines.append(f'## {name} ({fc}) — {len(events)} 个黄金坑窗口')
        lines.append('')
        lines.append('| 入坑日 | 坑内天数 | 最低贪婪 | ' + ' | '.join(['指数d%d' % p for p in PERIODS]) + ' |')
        lines.append('|---|--:|---:|' + '---:|' * len(PERIODS))
        for e in events:
            idx_rets = []
            for p in PERIODS:
                r = fwd_return(etf_series[fc], e['entry_date'], p)
                e['index_d%d' % p] = r
                idx_rets.append(r)
            lines.append('| %s | %d | %.3f | ' % (e['entry_date'].strftime('%Y-%m-%d'), e['days'], e['lowest'])
                         + ' | '.join(['%+.1f%%' % (r * 100) if r is not None else 'N/A' for r in idx_rets]) + ' |')
        lines.append('')

        piv = pivots['CI_L1']
        lines.append('### 窗口明细: 各板块 d30 收益 vs 指数 (中信一级)')
        lines.append('')
        lines.append('| 入坑日 | 指数d30 | 最强板块 | 最弱板块 | 板块分歧(强-弱) |')
        lines.append('|---|---:|---|---|---:|')
        for e in events:
            entry = e['entry_date']
            rets = {sec: fwd_return(piv[sec], entry, 30) for sec in CI_L1_FOCUS if sec in piv.columns}
            rets = {k: v for k, v in rets.items() if v is not None}
            idx_ret = e.get('index_d30')
            if rets:
                best = max(rets, key=rets.get)
                worst = min(rets, key=rets.get)
                lines.append('| %s | %s | %s %+.1f%% | %s %+.1f%% | %+.2f%% |' % (
                    entry.strftime('%Y-%m-%d'),
                    '%+.1f%%' % (idx_ret * 100) if idx_ret is not None else 'N/A',
                    best, rets[best] * 100, worst, rets[worst] * 100, (rets[best] - rets[worst]) * 100))
            else:
                lines.append('| %s | N/A | — | — | — |' % entry.strftime('%Y-%m-%d'))
        lines.append('')

        for gname, gkey, gfocus in group_cfg:
            piv = pivots[gkey]
            agg = defaultdict(list)
            best_by_window = []
            spread_by_window = []
            for e in events:
                entry = e['entry_date']
                idx_ret = e.get('index_d30')
                rets = {}
                for sec in gfocus:
                    if sec not in piv.columns:
                        continue
                    r = fwd_return(piv[sec], entry, PERIODS[2])
                    if r is not None:
                        rets[sec] = r
                if rets:
                    best_by_window.append(max(rets.values()))
                    spread_by_window.append(max(rets.values()) - min(rets.values()))
                for sec, r in rets.items():
                    if idx_ret is not None:
                        agg[sec].append(r - idx_ret)
            lines.append(f'### 板块 d30 收益 vs {name} 指数 — {gname}')
            lines.append('')
            lines.append('| 板块 | 样本窗口 | 平均超额 | 跑赢胜率 |')
            lines.append('|---|--:|---:|---:|')
            for sec in sorted(agg, key=lambda s: -statistics.mean(agg[s])):
                xs = agg[sec]
                lines.append('| %s | %d | %+.2f%% | %.0f%% |' % (sec, len(xs), statistics.mean(xs) * 100,
                                                                 sum(1 for x in xs if x > 0) / len(xs) * 100))
            if best_by_window:
                lines.append('')
                lines.append('- 每窗口板块分歧(最强-最弱): 平均 %+.2f%%, 最大 %+.2f%%' % (
                    statistics.mean(spread_by_window) * 100, max(spread_by_window) * 100))
                lines.append('- 若每窗口都恰好选中最强板块, d30 平均收益 %+.2f%%' % (statistics.mean(best_by_window) * 100))
            lines.append('')

    with io.open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    with io.open(OUT_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
