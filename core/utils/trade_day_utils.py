#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易日检测工具模块

提供自动检测最近交易日的功能，支持多种检测方式：
1. 检查 memory 目录中的日志文件（最可靠）
2. 检查 VN.PY 数据库中的交易记录
3. 检查本地行情数据库
4. 使用 Tushare 交易日历 API（备用）

Marcus 策略：错过比亏损更难受 —— 但首先要确认今天是交易日 📈
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple
import tushare as ts

try:
    from core._api_config import get_tushare_pro
except ImportError:
    from _api_config import get_tushare_pro

# Marcus workspace - auto-detect from project root
WORKSPACE = Path(__file__).parent.parent.parent  # core/utils/ -> marcus-platform/
MEMORY_DIR = WORKSPACE / "memory"
VNPY_DIR = WORKSPACE / "apps" / "paper-trading"
DATA_DIR = WORKSPACE / "data"


def get_latest_trade_log() -> Optional[str]:
    """
    方式 1: 从 memory 目录的日志文件中检测最近交易日
    
    扫描以下类型的文件（按优先级排序）：
    - YYYY-MM-DD-trades.jsonl (交易日志) — 最高优先级
    - YYYY-MM-DD-scans.jsonl (扫描日志) — 高优先级
    - YYYY-MM-DD-scan.md (扫描报告) — 高优先级
    - YYYY-MM-DD.md (每日总结) — 中优先级
    - YYYY-MM-DD-*.md (其他日报) — 低优先级
    
    排除非交易日文件：
    - *-news-analysis.md (新闻分析，可能在非交易日生成)
    
    Returns:
        最近交易日（YYYY-MM-DD 格式），如果没有找到则返回 None
    """
    if not MEMORY_DIR.exists():
        return None
    
    # 按优先级存储日期：优先级越高，权重越大
    # 格式：{date_str: priority}
    trade_dates = {}
    
    # 优先级定义
    HIGH_PRIORITY_PATTERNS = ['-trades.jsonl', '-scans.jsonl', '-scan.md']
    MID_PRIORITY_PATTERNS = ['.md']  # 基础 .md 文件
    EXCLUDE_PATTERNS = ['-news-analysis.md']  # 排除非交易日文件
    
    def get_priority(filename: str) -> int:
        """获取文件优先级 (0=排除，1=低，2=中，3=高)"""
        # 检查是否应该排除
        for pattern in EXCLUDE_PATTERNS:
            if pattern in filename:
                return 0
        
        # 检查高优先级
        for pattern in HIGH_PRIORITY_PATTERNS:
            if pattern in filename:
                return 3
        
        # 检查是否是基础日报（仅 YYYY-MM-DD.md 格式）
        if filename.endswith('.md'):
            parts = filename.split('-')
            if len(parts) == 3 and parts[0].isdigit() and len(parts[0]) == 4:
                return 2  # 中优先级
        
        # 其他 .md 文件
        if filename.endswith('.md'):
            return 1
        
        return 0
    
    def extract_date(filename: str) -> Optional[str]:
        """从文件名提取日期"""
        parts = filename.split('-')
        if len(parts) >= 3 and parts[0].isdigit() and len(parts[0]) == 4:
            date_str = f"{parts[0]}-{parts[1]}-{parts[2][:2]}"
            try:
                datetime.strptime(date_str, '%Y-%m-%d')
                return date_str
            except ValueError:
                pass
        return None
    
    # 扫描主目录
    for f in MEMORY_DIR.iterdir():
        if f.is_file():
            date_str = extract_date(f.name)
            if date_str:
                priority = get_priority(f.name)
                if priority > 0:
                    # 如果已有该日期，保留更高优先级
                    if date_str not in trade_dates or trade_dates[date_str] < priority:
                        trade_dates[date_str] = priority
    
    # 扫描子目录（高优先级）
    for subdir in ['auto-trade-logs', 'market-scan-logs']:
        sub_dir = MEMORY_DIR / subdir
        if sub_dir.exists():
            for f in sub_dir.iterdir():
                if f.is_file():
                    date_str = extract_date(f.name)
                    if date_str:
                        # 子目录中的文件优先级更高
                        if date_str not in trade_dates or trade_dates[date_str] < 3:
                            trade_dates[date_str] = 3
    
    if not trade_dates:
        return None
    
    # 返回最近的日期（优先选择高优先级的日期）
    sorted_dates = sorted(trade_dates.keys(), reverse=True)
    
    # 找到最高优先级的最近日期
    max_priority = max(trade_dates.values())
    for date_str in sorted_dates:
        if trade_dates[date_str] == max_priority:
            return date_str
    
    return sorted_dates[0]


def get_latest_trade_from_db() -> Optional[str]:
    """
    方式 2: 从 VN.PY 数据库或本地行情数据库中检测最近交易日
    
    Returns:
        最近交易日（YYYYMMDD 格式），如果没有找到则返回 None
    """
    # 尝试 VN.PY 数据库
    vnpy_db = VNPY_DIR / "data" / "paper_trading.db"
    if vnpy_db.exists():
        try:
            conn = sqlite3.connect(str(vnpy_db))
            cursor = conn.cursor()
            # 查询最近的交易记录
            cursor.execute('''
                SELECT trade_time FROM trades 
                ORDER BY trade_time DESC LIMIT 1
            ''')
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                trade_time = row[0]
                # 解析时间
                if 'T' in trade_time:
                    return trade_time.split('T')[0]
                return trade_time[:10]
        except Exception as e:
            print(f"[交易日检测] VN.PY 数据库查询失败：{e}")
    
    # 尝试本地行情数据库
    quote_db = DATA_DIR / "daily_quotes.db"
    if quote_db.exists():
        try:
            conn = sqlite3.connect(str(quote_db))
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT trade_date FROM daily_quotes 
                ORDER BY trade_date DESC LIMIT 1
            ''')
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                # YYYYMMDD -> YYYY-MM-DD
                date_str = row[0]
                if len(date_str) == 8:
                    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                return date_str
        except Exception as e:
            print(f"[交易日检测] 行情数据库查询失败：{e}")
    
    return None


def get_latest_trade_from_tushare() -> Optional[str]:
    """
    方式 3: 使用 Tushare 交易日历 API 获取最近交易日
    
    Returns:
        最近交易日（YYYY-MM-DD 格式），如果 API 失败则返回 None
    """
    try:
        pro = get_tushare_pro()
        today = datetime.now()
        
        # 最多往前查 10 天
        for i in range(1, 11):
            check_date = today - timedelta(days=i)
            date_str = check_date.strftime('%Y%m%d')
            
            # 查询交易日历
            df = pro.trade_cal(exchange='SSE', start_date=date_str, end_date=date_str)
            if len(df) > 0:
                row = df.iloc[0]
                if row['is_open'] == 1:
                    return check_date.strftime('%Y-%m-%d')
        
        return None
    except Exception as e:
        print(f"[交易日检测] Tushare API 查询失败：{e}")
        return None


def get_latest_trade_day(method: str = 'auto') -> Tuple[Optional[str], str]:
    """
    自动检测最近交易日（主函数）
    
    Args:
        method: 检测方式
            - 'auto': 自动选择（优先日志文件，其次数据库，最后 Tushare）
            - 'log': 仅从日志文件检测
            - 'db': 仅从数据库检测
            - 'tushare': 仅从 Tushare API 检测
    
    Returns:
        Tuple[最近交易日 (YYYY-MM-DD), 检测方法]
        如果检测失败，返回 (None, 'failed')
    """
    if method == 'log':
        result = get_latest_trade_log()
        return (result, 'log') if result else (None, 'failed')
    
    elif method == 'db':
        result = get_latest_trade_from_db()
        return (result, 'db') if result else (None, 'failed')
    
    elif method == 'tushare':
        result = get_latest_trade_from_tushare()
        return (result, 'tushare') if result else (None, 'failed')
    
    else:  # auto
        # 方式 1: 日志文件（最可靠，只在交易日生成）
        result = get_latest_trade_log()
        if result:
            return (result, 'log')
        
        # 方式 2: 数据库
        result = get_latest_trade_from_db()
        if result:
            return (result, 'db')
        
        # 方式 3: Tushare API（备用）
        result = get_latest_trade_from_tushare()
        if result:
            return (result, 'tushare')
        
        return (None, 'failed')


def is_today_trade_day() -> Tuple[bool, str]:
    """
    判断今天是否是交易日
    
    Returns:
        Tuple[是否交易日，说明]
    """
    today = datetime.now().strftime('%Y-%m-%d')
    today_num = datetime.now().strftime('%w')  # 0=周日，6=周六
    
    # 周末肯定不是交易日
    if today_num in ['0', '6']:
        return (False, f'今天 {today} 是周末')
    
    # 使用 Tushare 确认
    try:
        pro = get_tushare_pro()
        date_str = datetime.now().strftime('%Y%m%d')
        df = pro.trade_cal(exchange='SSE', start_date=date_str, end_date=date_str)
        if len(df) > 0:
            if df.iloc[0]['is_open'] == 1:
                return (True, f'今天 {today} 是交易日')
            else:
                return (False, f'今天 {today} 是节假日')
    except Exception as e:
        print(f"[交易日检测] Tushare API 查询失败：{e}")
    
    # 默认假设是交易日（保守策略）
    return (True, f'今天 {today} 默认视为交易日（API 不可用）')


def get_next_trade_day() -> Tuple[Optional[str], str]:
    """
    获取下一个交易日
    
    Returns:
        Tuple[下一个交易日 (YYYY-MM-DD), 检测方法]
    """
    today = datetime.now()
    
    # 最多往后查 10 天
    for i in range(1, 11):
        check_date = today + timedelta(days=i)
        date_str = check_date.strftime('%Y%m%d')
        
        try:
            pro = get_tushare_pro()
            df = pro.trade_cal(exchange='SSE', start_date=date_str, end_date=date_str)
            if len(df) > 0 and df.iloc[0]['is_open'] == 1:
                return (check_date.strftime('%Y-%m-%d'), 'tushare')
        except Exception:
            # API 失败时，简单跳过周末
            day_of_week = check_date.strftime('%w')
            if day_of_week not in ['0', '6']:
                return (check_date.strftime('%Y-%m-%d'), 'fallback')
    
    return (None, 'failed')


# ============= 测试 =============

if __name__ == '__main__':
    print("=" * 50)
    print("交易日检测工具测试")
    print("=" * 50)
    
    # 测试自动检测
    trade_day, method = get_latest_trade_day(method='auto')
    print(f"\n最近交易日：{trade_day}")
    print(f"检测方法：{method}")
    
    # 测试今天是否交易日
    is_trade, reason = is_today_trade_day()
    print(f"\n今天是否交易日：{is_trade}")
    print(f"说明：{reason}")
    
    # 测试下一个交易日
    next_day, method = get_next_trade_day()
    print(f"\n下一个交易日：{next_day}")
    print(f"检测方法：{method}")
    
    print("\n" + "=" * 50)
