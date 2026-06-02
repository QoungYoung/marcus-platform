#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池更新任务 - 定时任务入口
由 config/tasks.yaml 中的 stock_pool_refresh 调度触发

功能: 调用 core/stock_pool_manager.py 更新全A股股票池
默认无任何过滤条件（全部上市股票入库）
"""

import sys
from pathlib import Path

# 确保能导入 core 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.stock_pool_manager import StockPoolManager


def main():
    """股票池更新入口（无过滤条件，全市场股票入库）"""
    print("=" * 60)
    print("[股票池更新任务] 开始执行...")
    print("=" * 60)

    manager = StockPoolManager()

    # 不设任何过滤：min_market_cap=0, exclude_st=False
    count = manager.update_stock_pool(min_market_cap=0, exclude_st=False)

    print("=" * 60)
    print(f"[股票池更新任务] 完成！共入库 {count} 只股票")
    print("=" * 60)

    return count


if __name__ == '__main__':
    main()
