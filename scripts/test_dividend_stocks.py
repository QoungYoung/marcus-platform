# -*- coding: utf-8 -*-
"""Get 红利股/红利破净股 leaders with market cap on 7/20."""
import tushare as ts
import io

pro = ts.pro_api('a5c495cbe5e14729ad756381efe1fd72')
pro._DataApi__http_url = 'https://ts.gyzcloud.top/api'

# Get both concepts
for concept_code, concept_name in [('BK1635.DC', '红利股'), ('BK1636.DC', '红利破净股')]:
    print(f"\n{'='*60}")
    print(f"=== {concept_name} ({concept_code}) ===")

    df_m = pro.dc_member(trade_date='20260720', ts_code=concept_code)
    if df_m is None or df_m.empty:
        print("  无数据")
        continue

    codes = df_m['con_code'].tolist()
    names_list = df_m['name'].tolist()
    print(f"成分股总数: {len(codes)}")

    # Get quotes (batch 100 at a time)
    all_quotes = {}
    for i in range(0, len(codes), 80):
        batch = codes[i:i+80]
        try:
            df_q = pro.daily(ts_code=','.join(batch), trade_date='20260720')
            if df_q is not None and not df_q.empty:
                for _, r in df_q.iterrows():
                    all_quotes[r['ts_code']] = (r['close'], r['pct_chg'], r['amount'])
        except:
            pass

    # Get daily basic for market cap
    all_basic = {}
    for i in range(0, len(codes), 80):
        batch = codes[i:i+80]
        try:
            df_b = pro.daily_basic(ts_code=','.join(batch), trade_date='20260720')
            if df_b is not None and not df_b.empty:
                for _, r in df_b.iterrows():
                    all_basic[r['ts_code']] = (r.get('total_mv', 0) or 0, r.get('pe', 0) or 0, r.get('pb', 0) or 0)
        except:
            pass

    # Build results
    results = []
    for code, name in zip(codes, names_list):
        q = all_quotes.get(code)
        b = all_basic.get(code)
        if q and b:
            close, pct, amount = q
            total_mv, pe, pb = b
            cost = close * 100
            results.append((name, code, close, pct, amount/1e5, total_mv/1e8, pe, pb, cost))

    # Sort by market cap desc
    results.sort(key=lambda x: x[5], reverse=True)

    out = io.StringIO()
    out.write(f"\n{'名称':<12} {'代码':<12} {'收盘':<8} {'涨幅%':<7} {'成交(亿)':<9} {'总市值(亿)':<10} {'PE':<8} {'PB':<6} {'1手':<8}\n")
    out.write('-' * 90 + '\n')
    for r in results[:40]:
        out.write(f"{r[0]:<12} {r[1]:<12} {r[2]:<8.2f} {r[3]:<7.2f} {r[4]:<9.2f} {r[5]:<10.0f} {r[6]:<8.1f} {r[7]:<6.2f} {r[8]:<8.0f}\n")
    print(out.getvalue())

    # Show top gainers among large caps (市值>500亿)
    large = [r for r in results if r[5] > 500]
    large.sort(key=lambda x: x[3], reverse=True)
    out2 = io.StringIO()
    out2.write(f"\n--- 大市值(>500亿)涨幅TOP15 ---\n")
    out2.write(f"{'名称':<12} {'收盘':<8} {'涨幅%':<7} {'成交(亿)':<9} {'总市值(亿)':<10} {'PE':<8} {'PB':<6}\n")
    out2.write('-' * 65 + '\n')
    for r in large[:15]:
        out2.write(f"{r[0]:<12} {r[2]:<8.2f} {r[3]:<7.2f} {r[4]:<9.2f} {r[5]:<10.0f} {r[6]:<8.1f} {r[7]:<6.2f}\n")
    print(out2.getvalue())
