"""Test Eastmoney akshare locally"""
import akshare as ak
df = ak.stock_individual_fund_flow_rank(indicator='今日')
print(f'OK: {len(df)} stocks')
print(f'cols: {list(df.columns)[:5]}')
r = df.iloc[0]
code = r['代码']
name = r['名称']
main = r['今日主力净流入-净额']
print(f'first: {code} {name} main={main:,.0f}')
for c in ['600519','600362','000878']:
    row = df[df['代码'].astype(str) == c]
    if row.empty:
        row = df[df['代码'].astype(str) == '0000878']
    if row.empty:
        print(f'  {c}: not found')
    else:
        r = row.iloc[0]
        lg = r.get('今日超大单净流入-净额', 0)
        md = r.get('今日大单净流入-净额', 0)
        print(f'  {c} {r["名称"]}: main={r["今日主力净流入-净额"]:,.0f} lg={lg:,.0f} md={md:,.0f}')
