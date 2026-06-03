#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池更新任务 - 定时任务入口
由 config/tasks.yaml 中的 stock_pool_refresh 调度触发

功能:
  1. 调用 core/stock_pool_manager.py 更新全A股股票池
  2. 调用雪球 API 同步 ETF 板块池
默认无任何过滤条件（全部上市股票入库）
"""

import sys
from pathlib import Path

# 确保能导入 core 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.stock_pool_manager import StockPoolManager, update_etf_pool


def main():
    """股票池更新入口（个股 + ETF 一并更新）"""
    print("=" * 60)
    print("[股票池更新任务] 开始执行...")
    print("=" * 60)

    manager = StockPoolManager()

    # Step 1: 更新全A股个股
    count = manager.update_stock_pool(min_market_cap=0, exclude_st=False)

    # Step 2: 更新 ETF 板块池
    etf_count = update_etf_pool()

    print("=" * 60)
    print(f"[股票池更新任务] 完成！共入库 {count} 只股票，{etf_count} 只 ETF")
    print("=" * 60)

    return count


if __name__ == '__main__':
    main()
