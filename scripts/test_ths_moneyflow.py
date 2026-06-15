#!/usr/bin/env python3
"""测试个股资金流接口（东方财富 + 同花顺）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from datetime import datetime

print(f"[TIME] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
now = datetime.now()
is_trading = now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (15, 0)
print(f"[TRADE] 交易时段: {'是' if is_trading else '否'}")
print()

test_codes = ["600519", "600362", "000878", "999999"]

# ── 0. 测试东方财富排名 ──
print("=" * 60)
print("0. 东方财富 stock_individual_fund_flow_rank (优先源)")
print("=" * 60)
df_em = None
try:
    import akshare as ak
    df_em = ak.stock_individual_fund_flow_rank(indicator="今日")
    print(f"[OK] 获取成功: {len(df_em)} 只股票")
    print(f"   列名前5: {list(df_em.columns)[:5]}")

    for code in test_codes:
        row = df_em[df_em["代码"] == code]
        if row.empty:
            print(f"[FAIL] {code}: 未找到")
        else:
            r = row.iloc[0]
            main = r.get("今日主力净流入-净额", 0)
            main_pct = r.get("今日主力净流入-净占比", "")
            print(f"[OK] {code} {r['名称']}: "
                  f"主力={main:,.0f} ({main_pct}%)  "
                  f"超大单={r.get('今日超大单净流入-净额',0):,.0f}  "
                  f"大单={r.get('今日大单净流入-净额',0):,.0f}")
except Exception as e:
    print("[FAIL] 东方财富: " + str(e))

print()

# ── 1. 测试同花顺即时 ──
print("=" * 60)
print("1. 同花顺 stock_fund_flow_individual (降级源)")
print("=" * 60)
df_ths = None
try:
    df_ths = ak.stock_fund_flow_individual(symbol="即时")
    print(f"[OK] 获取成功: {len(df_ths)} 只股票")
    print(f"   列名: {list(df_ths.columns)}")

    for code in test_codes:
        try:
            row = df_ths[df_ths["股票代码"] == int(code)]
            if row.empty:
                print(f"[FAIL] {code}: 未找到")
            else:
                r = row.iloc[0]
                print(f"[OK] {code} {r['股票简称']}: "
                      f"流入={r['流入资金']} 流出={r['流出资金']} 净额={r['净额']}")
        except Exception as e:
            print(f"[FAIL] {code}: " + str(e))
except Exception as e:
    print("[FAIL] 同花顺: " + str(e))

print()

# ── 2. 对比两个数据源 ──
print("=" * 60)
print("2. 双源对比（共同股票）")
print("=" * 60)
if df_em is not None and df_ths is not None:
    em_codes = set(df_em["代码"].astype(str).str.zfill(6).tolist())
    ths_codes = set(str(int(c)).zfill(6) for c in df_ths["股票代码"].tolist())
    common = em_codes & ths_codes
    print(f"   东方财富覆盖: {len(em_codes)} 只")
    print(f"   同花顺覆盖:   {len(ths_codes)} 只")
    print(f"   共同覆盖:     {len(common)} 只")
    print(f"   仅在东方财富: {len(em_codes - ths_codes)} 只")
    print(f"   仅在同花顺:   {len(ths_codes - em_codes)} 只")

print()
print("[OK] 全部测试完成")
