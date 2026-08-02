# -*- coding: utf-8 -*-
"""Track dark track TOP10 overlap day by day from 7/11 to 7/20."""
import tushare as ts
import io
from datetime import datetime, timedelta

pro = ts.pro_api('a5c495cbe5e14729ad756381efe1fd72')
pro._DataApi__http_url = 'https://ts.gyzcloud.top/api'

TECH_BLACKLIST = [
    '首板', '连板', '昨日涨停', '昨日触板', '昨高', '昨涨',
    '百日新高', '近期新高', '百日新低', '微盘股', '微盘精选', '题材股',
]

def is_tech(name):
    for kw in TECH_BLACKLIST:
        if kw in name:
            return True
    return False

def get_trading_days(end_date, n_days):
    lookback = n_days * 4
    start = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=lookback)).strftime('%Y%m%d')
    df_cal = pro.trade_cal(exchange='SSE', start_date=start, end_date=end_date)
    days = df_cal[df_cal['is_open'] == 1]['cal_date'].tolist()
    days.sort(reverse=True)
    return days[:n_days]

def calc_dark_track(as_of_date, days=5):
    """Calculate dark track TOP10 as of a given date."""
    trading_days = get_trading_days(as_of_date, days)
    trading_days.sort(reverse=True)
    start_date = trading_days[-1]
    end_date = trading_days[0]

    df = pro.moneyflow_ind_dc(start_date=start_date, end_date=end_date, content_type='概念')
    if df is None or df.empty:
        return None, trading_days

    actual_data_date = str(df['trade_date'].max())

    agg = {}
    for _, row in df.iterrows():
        name = str(row['name'])
        pct = float(row.get('pct_change', 0) or 0)
        net = float(row.get('net_amount', 0) or 0)
        if name not in agg:
            agg[name] = {'name': name, 'total_pct': 0.0, 'up_days': 0, 'today_net': 0.0}
        agg[name]['total_pct'] += pct
        if pct > 0:
            agg[name]['up_days'] += 1

    df_latest = df[df['trade_date'] == actual_data_date]
    tushare_gate = {}
    for _, row in df_latest.iterrows():
        name = str(row['name'])
        net = float(row.get('net_amount', 0) or 0)
        tushare_gate[name] = tushare_gate.get(name, 0) + net

    for name, entry in agg.items():
        entry['today_net'] = tushare_gate.get(name, 0)

    candidates = [v for v in agg.values() if v['today_net'] > 0]
    candidates = [c for c in candidates if not is_tech(c['name'])]

    if not candidates:
        return [], trading_days

    sorted_by_pct = sorted(candidates, key=lambda x: x['total_pct'], reverse=True)
    pct_rank_map = {}
    for rank, item in enumerate(sorted_by_pct, 1):
        score = max(0, 11 - rank) if rank <= 10 else 0
        if rank > 1:
            prev = sorted_by_pct[rank - 2]
            if abs(item['total_pct'] - prev['total_pct']) < 0.001:
                score = pct_rank_map[prev['name']]
        pct_rank_map[item['name']] = score

    max_up = max(c['up_days'] for c in candidates) if candidates else 1
    for item in candidates:
        raw = item['up_days'] / max_up * 10 if max_up > 0 else 0
        item['composite'] = round(pct_rank_map.get(item['name'], 0) * 0.5 + round(raw, 1) * 0.5, 1)

    candidates.sort(key=lambda x: x['composite'], reverse=True)
    return candidates[:15], trading_days


# ==========================================
# Day-by-day overlap analysis
# ==========================================

# Trading days from 7/11 to 7/20
all_tdays = get_trading_days('20260720', 20)
all_tdays.sort()
print(f"All trading days: {all_tdays}")
print()

# Filter to 7/11 ~ 7/20
target_dates = [d for d in all_tdays if '20260711' <= d <= '20260720']
print(f"Target dates: {target_dates}")
print()

# Calculate dark track for each day
daily_top10 = {}
for date in target_dates:
    candidates, tdays = calc_dark_track(date, days=5)
    daily_top10[date] = [c['name'] for c in candidates[:10]] if candidates else []
    data_end = tdays[0] if tdays else '?'
    top3 = ', '.join(daily_top10[date][:3]) if daily_top10[date] else '(空)'
    top1_score = candidates[0]['composite'] if candidates else 0
    print(f"{date} (数据截止{data_end}): TOP3=[{top3}]")

print()
print("=" * 80)
print("逐日 TOP10 重叠率分析")
print("=" * 80)

prev_top10 = None
switch_day = None

for i, date in enumerate(target_dates):
    current = daily_top10[date]
    if not current:
        print(f"\n{date}: 无数据")
        continue

    if prev_top10 is None:
        print(f"\n{date}: 首次计算 (baseline)")
        print(f"  TOP10: {', '.join(current[:5])}...")
    else:
        overlap = [c for c in current if c in prev_top10]
        overlap_count = len(overlap)

        if overlap_count >= 7:
            status = "主线延续 ✓"
        elif overlap_count >= 4:
            status = "主线松动 ⚡"
        else:
            status = "⚠️ 风格切换 ⚠️"
            if switch_day is None:
                switch_day = date

        print(f"\n{date}: 重叠={overlap_count}/10 → {status}")
        print(f"  重叠概念: {overlap if overlap else '(无)'}")
        print(f"  今日TOP5: {current[:5]}")
        print(f"  昨日TOP5: {prev_top10[:5]}")
        print(f"  退出TOP10: {[c for c in prev_top10 if c not in current][:5]}")
        print(f"  新进TOP10: {[c for c in current if c not in prev_top10][:5]}")

    prev_top10 = current

print()
print("=" * 80)
if switch_day:
    print(f"🔄 风格切换日: {switch_day}")
    print(f"   旧方向: {daily_top10[target_dates[target_dates.index(switch_day)-1]][:3]}")
    print(f"   新方向: {daily_top10[switch_day][:3]}")
else:
    print("未检测到明确的风格切换日（重叠率始终 ≥4）")

# Also show the full transition table
print()
print("=" * 80)
print("完整轮动轨迹 (每日 TOP5)")
print("=" * 80)
for date in target_dates:
    top5 = daily_top10[date][:5] if daily_top10[date] else ['?']*5
    print(f"  {date}: 1.{top5[0]:<14} 2.{top5[1]:<14} 3.{top5[2]:<14} 4.{top5[3]:<14} 5.{top5[4]:<14}")
