#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AKShare 新闻资讯查询引擎

支持：
- 个股相关新闻查询
- 财经新闻查询
- 数据持久化存储
- 从本地读取最近 N 天新闻
"""

import akshare as ak
import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import pandas as pd
import sys


class AKShareEngine:
    """
    AKShare 新闻资讯查询引擎
    
    功能：
    - 个股新闻查询
    - 财经新闻查询
    - 数据持久化
    - 数据导出
    """
    
    # 新闻源映射
    NEWS_SOURCES = {
        'em': '东方财富',
        'ths': '同花顺',
        'sina': '新浪财经',
        'qq': '腾讯财经'
    }
    
    def __init__(self, data_dir: str = "./data"):
        """
        初始化引擎
        
        Args:
            data_dir: 数据目录
        """
        self.data_dir = os.path.expanduser(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        
        # 初始化数据库
        self.db_file = os.path.join(self.data_dir, "news.db")
        self._init_database()
        
        print(f"✓ AKShare 引擎已初始化", file=sys.stderr)
    
    def _init_database(self):
        """初始化 SQLite 数据库"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 创建新闻表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT,
                title TEXT NOT NULL,
                content TEXT,
                source TEXT,
                publish_time TEXT,
                url TEXT,
                created_at TEXT NOT NULL
            )
        ''')
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_keyword ON news (keyword)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_time ON news (publish_time)')
        
        conn.commit()
        conn.close()
        print(f"✓ 数据库已初始化：{self.db_file}", file=sys.stderr)
    
    def get_stock_news(self, keyword: str, limit: int = 20) -> Optional[List[dict]]:
        """
        获取个股相关新闻
        
        Args:
            keyword: 股票名称或代码（如 '茅台'、'600519'）
            limit: 返回数量
            
        Returns:
            新闻列表
        """
        try:
            # 调用 AKShare API
            df = ak.stock_news_em(symbol=keyword)
            
            # 限制返回数量
            if len(df) > limit:
                df = df.head(limit)
            
            # 转换为字典列表
            news_list = []
            for _, row in df.iterrows():
                news = {
                    'keyword': row.get('关键词', keyword),
                    'title': row.get('新闻标题', ''),
                    'content': row.get('新闻内容', ''),
                    'source': row.get('文章来源', '东方财富'),
                    'publish_time': row.get('发布时间', ''),
                    'url': row.get('新闻链接', '')
                }
                news_list.append(news)
                
                # 保存到数据库
                self._save_news(news)
            
            print(f"✓ 获取 {keyword} 新闻：{len(news_list)}条", file=sys.stderr)
            return news_list
            
        except Exception as e:
            print(f"❌ 获取新闻失败：{e}", file=sys.stderr)
            return None
    
    def get_finance_news(self, limit: int = 50, days: int = 3, from_local: bool = True) -> Optional[List[dict]]:
        """
        获取财经新闻
        
        Args:
            limit: 返回数量
            days: 从本地读取最近 N 天的新闻 (默认 3 天)
            from_local: 是否从本地数据库读取 (默认 True)
            
        Returns:
            新闻列表
        """
        if from_local:
            # 从本地数据库读取
            return self.get_cached_news_by_days(days=days, limit=limit)
        
        try:
            # 使用东方财富财经新闻（备用，不推荐使用）
            df = ak.stock_news_em(symbol='财经')
            
            if len(df) > limit:
                df = df.head(limit)
            
            news_list = []
            for _, row in df.iterrows():
                title = row.get('新闻标题', '')
                content = row.get('新闻内容', '')
                
                news = {
                    'keyword': '财经',
                    'title': title,
                    'content': content,
                    'source': '东方财富',
                    'publish_time': row.get('发布时间', ''),
                    'url': row.get('新闻链接', '')
                }
                news_list.append(news)
                self._save_news(news)
            
            print(f"✓ 获取财经新闻：{len(news_list)}条", file=sys.stderr)
            return news_list
            
        except Exception as e:
            print(f"❌ 获取财经新闻失败：{e}", file=sys.stderr)
            return None
    
    def get_hot_news(self, limit: int = 20) -> Optional[List[dict]]:
        """
        获取热门新闻
        
        Args:
            limit: 返回数量
            
        Returns:
            新闻列表
        """
        try:
            # 尝试获取热门新闻
            df = ak.stock_news_em(symbol='热门')
            
            if len(df) > limit:
                df = df.head(limit)
            
            news_list = []
            for _, row in df.iterrows():
                news = {
                    'keyword': '热门',
                    'title': row.get('内容', ''),
                    'content': '',
                    'source': '东方财富',
                    'publish_time': row.get('发布时间', ''),
                    'url': row.get('新闻链接', '')
                }
                news_list.append(news)
            
            print(f"✓ 获取热门新闻：{len(news_list)}条", file=sys.stderr)
            return news_list
            
        except Exception as e:
            print(f"❌ 获取热门新闻失败：{e}", file=sys.stderr)
            return None
    
    def _save_news(self, news: dict):
        """保存新闻到数据库"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO news (keyword, title, content, source, publish_time, url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                news.get('keyword', ''),
                news.get('title', ''),
                news.get('content', ''),
                news.get('source', ''),
                news.get('publish_time', ''),
                news.get('url', ''),
                datetime.now().isoformat()
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠ 保存新闻失败：{e}", file=sys.stderr)
    
    def get_cached_news(self, keyword: str = None, limit: int = 20) -> List[dict]:
        """
        从缓存获取新闻
        
        Args:
            keyword: 关键词（可选）
            limit: 返回数量
            
        Returns:
            新闻列表
        """
        try:
            conn = sqlite3.connect(self.db_file)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            query = 'SELECT * FROM news WHERE 1=1'
            params = []
            
            if keyword:
                query += ' AND keyword = ?'
                params.append(keyword)
            
            query += ' ORDER BY publish_time DESC LIMIT ?'
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()
            
            return [dict(row) for row in rows]
            
        except Exception as e:
            print(f"❌ 获取缓存新闻失败：{e}", file=sys.stderr)
            return []
    
    def get_cached_news_by_days(self, days: int = 3, limit: int = 50, category: str = None) -> List[dict]:
        """
        从缓存获取最近 N 天的新闻
        
        Args:
            days: 天数（默认 3 天）
            limit: 返回数量
            category: 板块过滤（可选）
            
        Returns:
            新闻列表
        """
        try:
            conn = sqlite3.connect(self.db_file)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cutoff_time = (datetime.now() - timedelta(days=days)).isoformat()
            
            query = '''
                SELECT * FROM news 
                WHERE publish_time >= ? 
                AND publish_time != ''
            '''
            params = [cutoff_time]
            
            if category:
                query += ' AND category = ?'
                params.append(category)
            
            query += ' ORDER BY publish_time DESC LIMIT ?'
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()
            
            print(f"✓ 从本地读取最近{days}天新闻：{len(rows)}条", file=sys.stderr)
            return [dict(row) for row in rows]
            
        except Exception as e:
            print(f"❌ 获取缓存新闻失败：{e}", file=sys.stderr)
            return []
    
    def get_news_by_sentiment(self, sentiment: str, days: int = 3, limit: int = 20) -> List[dict]:
        """
        按情绪获取新闻
        
        Args:
            sentiment: 情绪类型 (positive/negative/neutral)
            days: 天数
            limit: 返回数量
            
        Returns:
            新闻列表
        """
        try:
            conn = sqlite3.connect(self.db_file)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cutoff_time = (datetime.now() - timedelta(days=days)).isoformat()
            
            cursor.execute('''
                SELECT * FROM news 
                WHERE sentiment = ?
                AND publish_time >= ?
                AND publish_time != ''
                ORDER BY publish_time DESC
                LIMIT ?
            ''', (sentiment, cutoff_time, limit))
            
            rows = cursor.fetchall()
            conn.close()
            
            return [dict(row) for row in rows]
            
        except Exception as e:
            print(f"❌ 获取情绪新闻失败：{e}", file=sys.stderr)
            return []
    
    def get_category_stats(self, days: int = 1) -> Dict[str, dict]:
        """
        获取各板块统计
        
        Args:
            days: 天数
            
        Returns:
            {板块：{total, positive, negative, neutral, score}}
        """
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cutoff_time = (datetime.now() - timedelta(days=days)).isoformat()
            
            cursor.execute('''
                SELECT category, 
                       COUNT(*) as total,
                       SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) as positive,
                       SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) as negative,
                       SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) as neutral
                FROM news 
                WHERE publish_time >= ? AND publish_time != ''
                GROUP BY category
                ORDER BY total DESC
            ''', (cutoff_time,))
            
            rows = cursor.fetchall()
            conn.close()
            
            stats = {}
            for row in rows:
                category, total, positive, negative, neutral = row
                stats[category] = {
                    'total': total,
                    'positive': positive,
                    'negative': negative,
                    'neutral': neutral,
                    'score': 50 + (positive - negative) / max(total, 1) * 50
                }
            
            return stats
            
        except Exception as e:
            print(f"❌ 获取板块统计失败：{e}", file=sys.stderr)
            return {}
    
    def export_to_json(self, news_list: List[dict], filename: str):
        """
        导出新闻到 JSON
        
        Args:
            news_list: 新闻列表
            filename: 文件名
        """
        filepath = os.path.join(self.data_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(news_list, f, ensure_ascii=False, indent=2)
        
        print(f"✓ 数据已导出：{filepath}", file=sys.stderr)
    
    def export_to_csv(self, news_list: List[dict], filename: str):
        """
        导出新闻到 CSV
        
        Args:
            news_list: 新闻列表
            filename: 文件名
        """
        filepath = os.path.join(self.data_dir, filename)
        
        if not news_list:
            print("⚠ 没有数据可导出", file=sys.stderr)
            return
        
        df = pd.DataFrame(news_list)
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        
        print(f"✓ 数据已导出：{filepath}", file=sys.stderr)
    
    def show_news(self, news_list: List[dict], limit: int = 20):
        """
        显示新闻列表
        
        Args:
            news_list: 新闻列表
            limit: 显示数量
        """
        if not news_list:
            print("📭 暂无新闻", file=sys.stderr)
            return
        
        print("\n" + "=" * 80)
        print("📰 AKShare 财经新闻", file=sys.stderr)
        print("=" * 80)
        
        for i, news in enumerate(news_list[:limit]):
            pub_time = news.get('publish_time', 'N/A')
            title = news.get('title', 'N/A')
            source = news.get('source', 'N/A')
            url = news.get('url', '')
            
            print(f"\n[{pub_time}] {title}", file=sys.stderr)
            print(f"  来源：{source}", file=sys.stderr)
            if url:
                print(f"  链接：{url}", file=sys.stderr)
        
        print("\n" + "=" * 80)


def main():
    """测试引擎"""
    print("=" * 60)
    print("AKShare 新闻资讯查询引擎测试", file=sys.stderr)
    print("=" * 60)
    
    # 创建引擎
    engine = AKShareEngine()
    
    # 测试个股新闻
    print("\n[1] 测试个股新闻（茅台）...", file=sys.stderr)
    news = engine.get_stock_news('茅台', limit=5)
    if news:
        engine.show_news(news)
    
    # 测试财经新闻
    print("\n[2] 测试财经新闻...", file=sys.stderr)
    finance_news = engine.get_finance_news(limit=5)
    if finance_news:
        engine.show_news(finance_news)
    
    # 测试缓存
    print("\n[3] 从缓存读取...", file=sys.stderr)
    cached = engine.get_cached_news(limit=3)
    print(f"缓存新闻数量：{len(cached)}", file=sys.stderr)
    
    print("\n" + "=" * 60)
    print("测试完成", file=sys.stderr)
    print("=" * 60)


if __name__ == "__main__":
    main()
