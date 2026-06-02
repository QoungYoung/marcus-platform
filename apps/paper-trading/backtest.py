#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VN.PY 策略回测脚本
使用 CTA 策略框架进行历史数据回测
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from vnpy_ctastrategy.backtesting import BacktestingEngine, OptimizationSetting
from vnpy.trader.constant import Interval


def run_backtest():
    """运行回测"""
    print("=" * 80)
    print("VN.PY 策略回测")
    print("=" * 80)
    
    # 创建回测引擎
    engine = BacktestingEngine()
    
    # 设置回测参数
    print("\n配置回测参数...")
    engine.set_parameters(
        vt_symbol="SH600519",  # 贵州茅台
        interval=Interval.DAILY,  # 日线
        start=datetime(2023, 1, 1),
        end=datetime(2024, 12, 31),
        rate=0.0003,      # 手续费率
        slippage=0.5,     # 滑点
        size=100,         # 合约乘数（股票 100 股/手）
        pricetick=0.01,   # 最小价格变动
        capital=1000000,  # 初始资金
    )
    
    # 加载示例策略
    print("加载策略...")
    try:
        from strategies.boll_strategy import BollChannelStrategy
        engine.add_strategy(BollChannelStrategy, {
            'boll_window': 20,
            'boll_dev': 2,
            'fixed_size': 10  # 每次交易 10 手（1000 股）
        })
        print("✓ 布林带策略已加载")
    except ImportError:
        print("⚠ 策略文件不存在，使用内置策略")
        from vnpy_ctastrategy.strategies.atr_rsi_strategy import AtrRsiStrategy
        engine.add_strategy(AtrRsiStrategy, {})
    
    # 加载历史数据
    print("\n加载历史数据...")
    try:
        import akshare as ak
        print("使用 AKShare 获取数据...")
        
        # 获取贵州茅台历史数据
        df = ak.stock_zh_a_hist(
            symbol="600519",
            period="daily",
            start_date="20230101",
            end_date="20241231",
            adjust="qfq"
        )
        
        # 转换数据格式
        df = df.rename(columns={
            '日期': 'datetime',
            '开盘': 'open_price',
            '最高': 'high_price',
            '最低': 'low_price',
            '收盘': 'close_price',
            '成交量': 'volume',
            '成交额': 'turnover'
        })
        
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df[['datetime', 'open_price', 'high_price', 'low_price', 'close_price', 'volume']]
        
        # 加载到引擎
        engine.load_bar_data(df)
        print(f"✓ 已加载 {len(df)} 条 K 线数据")
        
    except Exception as e:
        print(f"⚠ 无法获取实时数据：{e}")
        print("使用模拟数据进行回测...")
        # 这里可以添加模拟数据生成逻辑
    
    # 运行回测
    print("\n运行回测...")
    engine.run_backtesting()
    
    # 计算统计指标
    print("\n计算统计指标...")
    df = engine.calculate_statistics()
    
    # 显示结果
    print("\n" + "=" * 80)
    print("回测结果")
    print("=" * 80)
    
    if df is not None:
        for col in df.index:
            print(f"  {col}: {df.loc[col]}")
    
    # 显示图表（需要 GUI 环境）
    try:
        print("\n生成图表...")
        engine.show_chart()
        print("✓ 图表已显示")
    except Exception as e:
        print(f"⚠ 无法显示图表：{e}")
    
    print("\n" + "=" * 80)
    print("回测完成")
    print("=" * 80)
    
    return df


def run_optimization():
    """运行参数优化"""
    print("=" * 80)
    print("VN.PY 策略参数优化")
    print("=" * 80)
    
    engine = BacktestingEngine()
    
    engine.set_parameters(
        vt_symbol="SH600519",
        interval=Interval.DAILY,
        start=datetime(2023, 1, 1),
        end=datetime(2024, 12, 31),
        rate=0.0003,
        slippage=0.5,
        size=100,
        pricetick=0.01,
        capital=1000000,
    )
    
    try:
        from strategies.boll_strategy import BollChannelStrategy
        engine.add_strategy(BollChannelStrategy, {})
    except ImportError:
        print("⚠ 策略文件不存在")
        return
    
    # 设置优化参数
    setting = OptimizationSetting()
    setting.set_target("sharpe_ratio")  # 优化目标：夏普比率
    setting.add_parameter("boll_window", 15, 25, 1)  # 布林带周期 15-25
    setting.add_parameter("boll_dev", 1.5, 2.5, 0.1)  # 布林带偏差 1.5-2.5
    setting.add_parameter("fixed_size", 5, 20, 5)  # 交易数量 5-20
    
    print("\n运行参数优化...")
    print(f"  优化目标：{setting.target_name}")
    print(f"  参数范围:")
    for param in setting.params:
        print(f"    {param[0]}: {param[1]} ~ {param[2]}, step={param[3]}")
    
    # 运行优化
    results = engine.run_optimization(setting, use_ga=False)
    
    print("\n" + "=" * 80)
    print("优化结果")
    print("=" * 80)
    
    if results:
        print(f"\n最佳参数组合:")
        best = results[0]
        for key, value in best[0].items():
            print(f"  {key}: {value}")
        print(f"\n{setting.target_name}: {best[1]:.4f}")
    
    return results


if __name__ == "__main__":
    import pandas as pd
    
    if len(sys.argv) > 1 and sys.argv[1] == "optimize":
        run_optimization()
    else:
        run_backtest()
