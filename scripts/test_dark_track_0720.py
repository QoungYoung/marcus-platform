# -*- coding: utf-8 -*-
"""Test dark track calculation as of 2026-07-20 close."""
import tushare as ts
from datetime import datetime, timedelta

pro = ts.pro_api('a5c495cbe5e14729ad756381efe1fd72')
pro._DataApi__http_url = 'https://ts.gyzcloud.top/api'

now = datetime(2026, 7, 20, 16, 0, 0)  # 7/20 close

# Get trading days
lookback = 5 * 4
df_cal = pro.trade_cal(
    exchange='SSE',
    start_date=(now - timedelta(days=lookback)).strftime('%Y%m%d'),
    end_date='20260720'
)
open_days = df_cal[df_cal['is_open'] == 1]['cal_date'].tolist()
open_days.sort(reverse=True)
trading_days = open_days[:5]
trading_days.sort(reverse=True)
start_date = trading_days[-1]
end_date = trading_days[0]
print(f'Trading days: {trading_days}')
print(f'Range: {start_date} ~ {end_date}')
print()

# Query Tushare
df = pro.moneyflow_ind_dc(start_date=start_date, end_date=end_date, content_type='概念')
actual_data_date = str(df['trade_date'].max())
print(f'Actual data date: {actual_data_date}')
print(f'Total rows: {len(df)}')
print()

# Aggregate by concept
agg = {}
for _, row in df.iterrows():
    name = str(row['name'])
    pct = float(row.get('pct_change', 0) or 0)
    net = float(row.get('net_amount', 0) or 0)
    if name not in agg:
        agg[name] = {'name': name, 'total_pct': 0.0, 'up_days': 0, 'total_net': 0.0, 'today_net': 0.0}
    agg[name]['total_pct'] += pct
    if pct > 0:
        agg[name]['up_days'] += 1
    agg[name]['total_net'] += net

# Gate: latest date from Tushare
df_latest = df[df['trade_date'] == actual_data_date]
tushare_gate = {}
for _, row in df_latest.iterrows():
    name = str(row['name'])
    net = float(row.get('net_amount', 0) or 0)
    tushare_gate[name] = tushare_gate.get(name, 0) + net

for name, entry in agg.items():
    entry['today_net'] = tushare_gate.get(name, 0)

# Filter: today net > 0
candidates = [v for v in agg.values() if v['today_net'] > 0]
print(f'After gate (today net > 0): {len(candidates)} concepts')

# Filter tech concepts
TECH_BLACKLIST = [
    '首板', '连板', '昨日涨停', '昨日触板', '昨高', '昨涨',
    '百日新高', '近期新高', '百日新低',
    '微盘股', '微盘精选', '题材股',
]

def is_tech(name):
    for kw in TECH_BLACKLIST:
        if kw in name:
            return True
    return False

candidates = [c for c in candidates if not is_tech(c['name'])]
print(f'After tech filter: {len(candidates)} concepts')
print()

if not candidates:
    print('No candidates!')
    exit()

# Scoring
sorted_by_pct = sorted(candidates, key=lambda x: x['total_pct'], reverse=True)
pct_rank_map = {}
for rank, item in enumerate(sorted_by_pct, 1):
    score = max(0, 11 - rank) if rank <= 10 else 0
    if rank > 1:
        prev = sorted_by_pct[rank - 2]
        if abs(item['total_pct'] - prev['total_pct']) < 0.001:
            score = pct_rank_map[prev['name']]
    pct_rank_map[item['name']] = score

max_up = max(c['up_days'] for c in candidates)
up_score_map = {}
for item in candidates:
    raw = item['up_days'] / max_up * 10 if max_up > 0 else 0
    up_score_map[item['name']] = round(raw, 1)

for item in candidates:
    name = item['name']
    item['pct_score'] = pct_rank_map.get(name, 0)
    item['up_score'] = up_score_map.get(name, 0)
    item['composite'] = round(item['pct_score'] * 0.5 + item['up_score'] * 0.5, 1)

candidates.sort(key=lambda x: x['composite'], reverse=True)

# Print results
import io
out = io.StringIO()
out.write(f"{'':<3} {'概念':<20} {'综合':<6} {'5日累计%':<10} {'涨天':<5} {'今日净流入(亿)':<14} {'涨幅分':<7} {'涨天分':<7}\n")
out.write('-' * 90 + '\n')
for i, c in enumerate(candidates[:40], 1):
    m = '[★]' if c['composite'] >= 5 else '   '
    out.write(f"{m}{i:<2} {c['name']:<20} {c['composite']:<6.1f} {c['total_pct']:<10.2f} {c['up_days']:<5} {c['today_net']/1e8:<14.2f} {c['pct_score']:<7} {c['up_score']:<7}\n")
print(out.getvalue())

# Check power/dividend concepts
print()
print('=== 电力/红利/银行/煤炭 相关概念检查 ===')
power_kw = ['电力', '电', '绿电', '新能源', '光伏', '风电', '水电', '核电', '储能', '红利', '高股息', '煤炭', '银行']
found = []
for c in candidates:
    for kw in power_kw:
        if kw in c['name']:
            found.append(c)
            print(f"  {c['name']}: 综合{c['composite']}, 5日累计{c['total_pct']:.2f}%, 涨天{c['up_days']}, 今日净流入{c['today_net']/1e8:.2f}亿")
            break

if not found:
    print('  (无相关概念)')
