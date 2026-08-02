# -*- coding: utf-8 -*-
"""Get 酿酒概念 stocks quotes on 7/20."""
import tushare as ts
import io, sys

pro = ts.pro_api('a5c495cbe5e14729ad756381efe1fd72')
pro._DataApi__http_url = 'https://ts.gyzcloud.top/api'

# 酿酒概念主要个股
stocks = [
    ('600519.SH', '贵州茅台'),
    ('000858.SZ', '五粮液'),
    ('000568.SZ', '泸州老窖'),
    ('600809.SH', '山西汾酒'),
    ('000596.SZ', '古井贡酒'),
    ('002304.SZ', '洋河股份'),
    ('603369.SH', '今世缘'),
    ('603816.SH', '迎驾贡酒'),
    ('000799.SZ', '酒鬼酒'),
    ('600702.SH', '舍得酒业'),
    ('603589.SH', '口子窖'),
    ('600559.SH', '老白干酒'),
    ('600779.SH', '水井坊'),
    ('600132.SH', '重庆啤酒'),
    ('600600.SH', '青岛啤酒'),
    ('000869.SZ', '张裕A'),
]

codes = [s[0] for s in stocks]
name_map = dict(stocks)

df = pro.daily(ts_code=','.join(codes), trade_date='20260720')

out = io.StringIO()
if df is not None and not df.empty:
    out.write('=== 酿酒概念成分股 2026-07-20 行情 ===\n\n')
    out.write(f"{'代码':<12} {'名称':<10} {'收盘':<10} {'涨幅%':<8} {'成交额(亿)':<12} {'换手%':<8} {'1手成本':<10}\n")
    out.write('-' * 70 + '\n')
    results = []
    for _, row in df.iterrows():
        ts_code = row['ts_code']
        name = name_map.get(ts_code, '?')
        close = row['close']
        pct = row['pct_chg']
        amount = row['amount'] / 1e5
        turnover = row.get('turnover_rate', 0) or 0
        cost_100 = close * 100
        afford = '可买' if cost_100 <= 100000 else f'超{int(cost_100 - 100000)}'
        out.write(f"{ts_code:<12} {name:<10} {close:<10.2f} {pct:<8.2f} {amount:<12.2f} {turnover:<8.2f} {cost_100:<10.0f} {afford}\n")
        results.append((name, close, pct, amount, turnover, cost_100))

    # Sort by pct change
    results.sort(key=lambda x: x[2], reverse=True)
    out.write('\n--- 按涨幅排序 (可买入的) ---\n')
    out.write(f"{'名称':<10} {'收盘':<10} {'涨幅%':<8} {'成交额(亿)':<12} {'换手%':<8} {'1手成本':<10}\n")
    out.write('-' * 60 + '\n')
    for r in results:
        if r[4] <= 100000:
            out.write(f"{r[0]:<10} {r[1]:<10.2f} {r[2]:<8.2f} {r[3]:<12.2f} {r[4]:<8.2f} {r[5]:<10.0f}\n")
else:
    out.write('No data returned from Tushare\n')
    # Try individual stocks
    out.write('Trying individual stocks...\n')
    for code, name in stocks:
        try:
            df2 = pro.daily(ts_code=code, trade_date='20260720')
            if df2 is not None and not df2.empty:
                row = df2.iloc[0]
                out.write(f"{code} {name}: close={row['close']}, pct={row['pct_chg']}\n")
        except Exception as e:
            out.write(f"{code} {name}: ERROR - {e}\n")

print(out.getvalue())
