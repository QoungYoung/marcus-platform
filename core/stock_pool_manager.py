#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池管理器 - 基于 tushare 全A股基础数据
支持行业板块查询、概念板块查询、全量股票获取
"""

import sqlite3
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import pandas as pd

try:
    import tushare as ts
except ImportError:
    print("⚠️ tushare 未安装，部分功能不可用")
    ts = None

try:
    from core._api_config import get_tushare_pro
except ImportError:
    from _api_config import get_tushare_pro


def get_latest_trade_day(method='auto') -> Tuple[Optional[str], str]:
    """获取最近交易日"""
    if ts is None:
        return None, "tushare_unavailable"
    try:
        pro = get_tushare_pro()
        cal = pro.trade_cal()
        today = datetime.now().strftime('%Y%m%d')
        is_trade = cal[cal['cal_date'] == today]
        if len(is_trade) > 0 and is_trade.iloc[0]['is_open'] == 1:
            return today, 'today'
        # 取上一个交易日
        past = cal[cal['cal_date'] < today].sort_values('cal_date', ascending=False)
        for _, row in past.iterrows():
            if row['is_open'] == 1:
                return row['cal_date'], 'prev'
        return None, 'none'
    except Exception:
        return None, 'error'


# 初始化 pro_api（延迟）
def _pro():
    if ts is None:
        raise RuntimeError("tushare 未安装")
    return get_tushare_pro()

try:
    from workspace_detector import get_data_dir
    DATA_DIR = get_data_dir()
except ImportError:
    DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = DATA_DIR / "stock_pool.db"


class StockPoolManager:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_FILE)
        self._init_database()

    def _init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_pool (
                ts_code TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                area TEXT,
                industry TEXT,
                market TEXT,
                list_date TEXT,
                is_st INTEGER DEFAULT 0,
                market_cap REAL DEFAULT 0,
                updated_at TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sectors (
                sector_name TEXT UNIQUE NOT NULL,
                sector_type TEXT NOT NULL,
                stock_count INTEGER DEFAULT 0,
                updated_at TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_concept_map (
                ts_code TEXT NOT NULL,
                concept_name TEXT NOT NULL,
                PRIMARY KEY (ts_code, concept_name)
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_concept ON stock_concept_map(concept_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_industry ON stock_pool(industry)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_market ON stock_pool(market)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_is_st ON stock_pool(is_st)')
        
        conn.commit()
        conn.close()
        print(f"[股票池管理] ✓ 数据库初始化：{self.db_path}")

    def update_stock_pool(self, min_market_cap: float = 0, exclude_st: bool = False) -> int:
        """
        更新股票池（全A股，含科创板+创业板）
        
        Args:
            min_market_cap: 最小市值（亿元）
            exclude_st: 是否排除 ST 股
        
        Returns:
            更新的股票数量
        """
        print(f"[股票池管理] 开始更新股票池...")
        print(f"  - 最小市值：{min_market_cap}亿")
        print(f"  - 排除 ST: {exclude_st}")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            pro = get_tushare_pro()
            df = pro.stock_basic(
                exchange='',
                list_status='L',
                fields='ts_code,symbol,name,area,industry,market,list_date'
            )
            
            print(f"  - 获取到 {len(df)} 只股票")
            
            trade_day, method = get_latest_trade_day(method='auto')
            if trade_day:
                trade_date = trade_day.replace('-', '')
            else:
                yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
                trade_date = yesterday
            daily_basic = pro.daily_basic(trade_date=trade_date)
            
            updated_count = 0
            now = datetime.now().isoformat()
            
            for _, row in df.iterrows():
                ts_code = row['ts_code']
                symbol = row['symbol']
                name = row['name']
                
                # 排除 ST 股
                is_st = 1 if 'ST' in name or '*' in name else 0
                if exclude_st and is_st:
                    continue
                
                # 获取市值
                market_cap = 0
                if len(daily_basic) > 0:
                    match = daily_basic[daily_basic['ts_code'] == ts_code]
                    if len(match) > 0:
                        market_cap = match.iloc[0].get('total_mv', 0) / 10000  # 万→亿
                
                # 市值过滤
                if market_cap < min_market_cap:
                    continue
                
                # 插入或更新
                # 处理 pandas NaN（NaN 是 truthy 的，or '' 无效，必须显式判空）
                market = row['market'] if pd.notna(row.get('market')) else ''
                area = row['area'] if pd.notna(row.get('area')) else ''
                industry = row['industry'] if pd.notna(row.get('industry')) else ''
                list_date = row['list_date'] if pd.notna(row.get('list_date')) else ''
                
                cursor.execute('''
                    INSERT OR REPLACE INTO stock_pool 
                    (ts_code, symbol, name, area, industry, market, list_date, is_st, market_cap, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (ts_code, symbol, name, area, industry, 
                      market, list_date, is_st, market_cap, now))
                
                updated_count += 1
            
            # 更新板块统计
            cursor.execute('''
                SELECT industry, COUNT(*) as cnt FROM stock_pool 
                WHERE is_st = 0 GROUP BY industry
            ''')
            sectors = cursor.fetchall()
            
            for sector_name, count in sectors:
                cursor.execute('''
                    INSERT OR REPLACE INTO sectors (sector_name, sector_type, stock_count, updated_at)
                    VALUES (?, 'industry', ?, ?)
                ''', (sector_name, count, now))
            
            conn.commit()
            print(f"  - ✓ 更新完成：{updated_count} 只股票")
            print(f"  - ✓ 板块数量：{len(sectors)} 个")

            # 刷新概念板块映射（dc_index + dc_member）
            self._refresh_concept_map(trade_date)

            return updated_count
            
        except Exception as e:
            print(f"  - ✗ 更新失败：{e}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def _refresh_concept_map(self, trade_date: str) -> int:
        """
        刷新概念板块及成分股映射
        通过 dc_index + dc_member 接口建立股票-概念关联
        
        Args:
            trade_date: 交易日期，格式 YYYYMMDD
        
        Returns:
            更新的概念数量
        """
        print(f"\n[概念映射] 开始更新概念板块...")

        try:
            pro = get_tushare_pro()

            # Step 1: 获取所有概念板块（自动回退到上一个交易日）
            df_concepts = None
            attempt_dates = [trade_date]  # 优先用当日
            # 回退日期：依次尝试前1/2/3个交易日
            for offset in range(1, 4):
                fallback = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=offset)).strftime('%Y%m%d')
                attempt_dates.append(fallback)

            for attempt_date in attempt_dates:
                print(f"  - 尝试日期：{attempt_date}")
                df_concepts = pro.dc_index(
                    trade_date=attempt_date,
                    idx_type='概念板块',
                    fields='ts_code,name'
                )
                if df_concepts is not None and len(df_concepts) > 0:
                    trade_date = attempt_date  # 更新为实际使用的日期
                    print(f"  - ✓ 命中日期：{attempt_date}")
                    break
                else:
                    print(f"  - 日期 {attempt_date} 无数据")

            if df_concepts is None or len(df_concepts) == 0:
                print("  - ⚠ 连续4天均无概念板块数据，跳过")
                return 0

            print(f"  - 获取到 {len(df_concepts)} 个概念板块")

            # Step 2: 遍历每个概念，获取成分股
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            concept_count = 0
            total_mappings = 0

            for i, (_, concept_row) in enumerate(df_concepts.iterrows()):
                concept_code = concept_row['ts_code']
                concept_name = concept_row['name']

                try:
                    df_members = pro.dc_member(
                        ts_code=concept_code,
                        trade_date=trade_date,
                        fields='ts_code,con_code,name'
                    )

                    if df_members is None or len(df_members) == 0:
                        continue

                    member_count = len(df_members)

                    # 写入概念板块
                    cursor.execute('''
                        INSERT OR REPLACE INTO sectors (sector_name, sector_type, stock_count, updated_at)
                        VALUES (?, 'concept', ?, ?)
                    ''', (concept_name, member_count, now))

                    # 写入成分股映射
                    for _, member in df_members.iterrows():
                        cursor.execute('''
                            INSERT OR IGNORE INTO stock_concept_map (ts_code, concept_name)
                            VALUES (?, ?)
                        ''', (member['con_code'], concept_name))

                    concept_count += 1
                    total_mappings += member_count

                    # 每 50 个概念提交一次，避免内存溢出
                    if concept_count % 50 == 0:
                        conn.commit()
                        print(f"  - 已处理 {concept_count}/{len(df_concepts)} 个概念，"
                              f"累计 {total_mappings} 条映射")

                    # 避免触发 tushare 频率限制
                    time.sleep(0.15)

                except Exception as e:
                    print(f"  - ⚠ 概念 [{concept_name}] 获取失败：{e}")
                    continue

            conn.commit()
            conn.close()

            print(f"  - ✓ 概念映射完成：{concept_count} 个概念，{total_mappings} 条映射")
            return concept_count

        except Exception as e:
            print(f"  - ✗ 概念映射失败：{e}")
            return 0

    def get_stock_pool(self, sector: str = None, min_market_cap: float = 0) -> List[Dict]:
        """
        获取股票池
        
        Args:
            sector: 板块名称（可选）
            min_market_cap: 最小市值
        
        Returns:
            股票列表
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if sector:
            cursor.execute('''
                SELECT p.ts_code, p.symbol, p.name, p.area, p.industry, p.market, 
                       p.list_date, p.is_st, p.market_cap, p.updated_at
                FROM stock_pool p
                JOIN stock_concept_map m ON p.ts_code = m.ts_code
                WHERE m.concept_name = ? AND p.is_st = 0 AND p.market_cap >= ?
                ORDER BY p.market_cap DESC
            ''', (sector, min_market_cap))
        else:
            cursor.execute('''
                SELECT ts_code, symbol, name, area, industry, market, 
                       list_date, is_st, market_cap, updated_at
                FROM stock_pool 
                WHERE is_st = 0 AND market_cap >= ?
                ORDER BY market_cap DESC
            ''', (min_market_cap,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]

    def get_sectors(self) -> List[Dict]:
        """获取所有板块列表"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT sector_name, sector_type, stock_count, updated_at 
            FROM sectors 
            ORDER BY stock_count DESC
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]

    def get_stocks_by_sector(self, sector_name: str, limit: int = 50) -> List[Dict]:
        """获取指定板块的股票"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT ts_code, symbol, name, market_cap 
            FROM stock_pool 
            WHERE industry = ? AND is_st = 0 
            ORDER BY market_cap DESC 
            LIMIT ?
        ''', (sector_name, limit))
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {'ts_code': row[0], 'symbol': row[1], 'name': row[2], 'market_cap': row[3]}
            for row in rows
        ]

    def get_stocks_by_concept(self, concept_name: str, limit: int = 50) -> List[Dict]:
        """获取指定概念的股票"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT p.ts_code, p.symbol, p.name, p.market_cap
            FROM stock_pool p
            JOIN stock_concept_map m ON p.ts_code = m.ts_code
            WHERE m.concept_name = ? AND p.is_st = 0
            ORDER BY p.market_cap DESC
            LIMIT ?
        ''', (concept_name, limit))
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {'ts_code': row[0], 'symbol': row[1], 'name': row[2], 'market_cap': row[3]}
            for row in rows
        ]

    def get_pool_stats(self) -> Dict:
        """获取股票池统计信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM stock_pool WHERE is_st = 0')
        total = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM sectors')
        sector_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT MAX(updated_at) FROM stock_pool')
        last_update = cursor.fetchone()[0] or ''
        
        conn.close()
        
        return {
            'total_stocks': total,
            'sector_count': sector_count,
            'last_update': last_update
        }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='股票池管理')
    parser.add_argument('cmd', choices=['update', 'stats', 'sectors', 'list'],
                        help='命令：update=更新股票池, stats=统计信息, sectors=板块列表, list=股票列表')
    parser.add_argument('arg', nargs='?', help='板块名称（list命令用）')
    parser.add_argument('--min-cap', type=float, default=0, help='最小市值（亿元），默认0（不限制）')
    args = parser.parse_args()
    
    manager = StockPoolManager()
    
    if args.cmd == 'update':
        manager.update_stock_pool(min_market_cap=args.min_cap)
    
    elif args.cmd == 'stats':
        stats = manager.get_pool_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    
    elif args.cmd == 'sectors':
        sectors = manager.get_sectors()
        print(json.dumps(sectors[:30], ensure_ascii=False, indent=2))
    
    elif args.cmd == 'list':
        sector = args.arg if args.arg else None
        stocks = manager.get_stocks_by_sector(sector) if sector else manager.get_stock_pool()
        print(json.dumps(stocks[:20], ensure_ascii=False, indent=2))
