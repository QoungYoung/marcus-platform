#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AKShare 新闻资讯查询引擎（增强版）

支持：
- 多新闻源聚合（东方财富、新浪财经、同花顺）
- 个股新闻查询
- 行业新闻查询
- 财经快讯
- 数据持久化存储
"""

import akshare as ak
import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import pandas as pd
import sys


class AKShareEnhancedEngine:
    """
    AKShare 增强版新闻引擎
    
    功能：
    - 多新闻源聚合
    - 个股新闻
    - 行业新闻
    - 财经快讯
    - 数据持久化
    """
    
    # 行业关键词映射
    INDUSTRY_KEYWORDS = {
        '半导体': ['芯片', '半导体', '集成电路', '晶圆', '光刻', 'EDA', '封测'],
        '人工智能': ['AI', '人工智能', '大模型', '算力', 'GPU', 'NPU', '深度学习'],
        '新能源': ['光伏', '风电', '锂电', '电池', '储能', '氢能', '新能源车'],
        '消费电子': ['手机', '消费电子', '苹果', '华为', '智能穿戴', 'MR', '折叠屏'],
        '医药生物': ['医药', '疫苗', '创新药', '医疗器械', 'CXO', '中药', '生物'],
        '金融科技': ['金融科技', '数字货币', '区块链', '互金', '支付', '券商', '保险'],
        '汽车': ['汽车', '新能源车', '自动驾驶', '特斯拉', '比亚迪', '智能汽车'],
        '房地产': ['房地产', '地产', '楼市', '住房', '物业', 'REITs'],
        '通信': ['5G', '6G', '通信', '基站', '光纤', '卫星', '光模块'],
        '化工': ['化工', '塑料', '化肥', '农药', '有机硅', '氟化工', '石化'],
        '有色金属': ['锂', '钴', '稀土', '铜', '铝', '黄金', '小金属', '矿业'],
        '食品饮料': ['白酒', '啤酒', '食品', '饮料', '乳制品', '预制菜', '调味品'],
        '电力': ['电力', '火电', '水电', '核电', '绿电', '虚拟电厂', '电网'],
        '机械设备': ['机器人', '自动化', '工业母机', '激光', '注塑机', '工程机械'],
        '软件服务': ['软件', '云计算', 'SaaS', '数据要素', '信创', '鸿蒙', '操作系统'],
    }
    
    def __init__(self, data_dir: str = "./data"):
        """初始化引擎"""
        self.data_dir = os.path.expanduser(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.db_file = os.path.join(self.data_dir, "news.db")
        self._init_database()
        
        print(f"✓ AKShare 增强版引擎已初始化", file=sys.stderr)
    
    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='news'")
        table_exists = cursor.fetchone()
        
        if table_exists:
            # 检查是否有 category 列
            cursor.execute("PRAGMA table_info(news)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'category' not in columns:
                # 添加 category 列
                cursor.execute('ALTER TABLE news ADD COLUMN category TEXT')
                print(f"✓ 添加 category 列", file=sys.stderr)
            
            if 'sentiment' not in columns:
                cursor.execute('ALTER TABLE news ADD COLUMN sentiment TEXT')
                print(f"✓ 添加 sentiment 列", file=sys.stderr)
        else:
            # 创建新表
            cursor.execute('''
                CREATE TABLE news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT,
                    category TEXT,
                    title TEXT NOT NULL,
                    content TEXT,
                    source TEXT,
                    publish_time TEXT,
                    url TEXT,
                    sentiment TEXT,
                    created_at TEXT NOT NULL
                )
            ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_category ON news (category)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_keyword ON news (keyword)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_time ON news (publish_time)')
        
        conn.commit()
        conn.close()
        print(f"✓ 数据库已初始化：{self.db_file}", file=sys.stderr)
    
    def get_finance_news(self, limit: int = 30) -> List[dict]:
        """
        获取财经新闻（综合新闻）
        
        Returns:
            新闻列表
        """
        news_list = []
        
        try:
            # 东方财富财经新闻
            df = ak.stock_news_em(symbol='600519')  # 用茅台作为财经新闻代理
            for _, row in df.head(limit).iterrows():
                news_list.append({
                    'title': row.get('新闻标题', ''),
                    'content': row.get('新闻内容', ''),
                    'source': row.get('文章来源', '东方财富'),
                    'publish_time': row.get('发布时间', ''),
                    'url': row.get('相关链接', ''),
                    'category': '财经综合'
                })
        except Exception as e:
            print(f"[新闻] 获取财经新闻失败：{e}", file=sys.stderr)
        
        return news_list[:limit]
    
    def get_industry_news(self, industry: str = None, limit: int = 20) -> List[dict]:
        """
        获取行业新闻
        
        Args:
            industry: 行业名称（如 '半导体'、'新能源'）
            limit: 返回数量
        
        Returns:
            行业新闻列表
        """
        news_list = []
        
        # 如果指定了行业，用行业关键词搜索
        if industry and industry in self.INDUSTRY_KEYWORDS:
            keywords = self.INDUSTRY_KEYWORDS[industry]
            
            # 用关键词作为股票代码查询（AKShare 会返回相关新闻）
            for kw in keywords[:3]:  # 最多用 3 个关键词
                try:
                    df = ak.stock_news_em(symbol=kw)
                    for _, row in df.head(limit // 3).iterrows():
                        news_list.append({
                            'title': row.get('新闻标题', ''),
                            'content': row.get('新闻内容', ''),
                            'source': row.get('文章来源', '东方财富'),
                            'publish_time': row.get('发布时间', ''),
                            'url': row.get('相关链接', ''),
                            'category': industry,
                            'keyword': kw
                        })
                except:
                    continue
        
        # 如果没有指定行业，返回综合行业新闻
        if not industry:
            # 获取多个行业的新闻
            for ind in ['半导体', '新能源', '人工智能'][:3]:
                sub_news = self.get_industry_news(ind, limit // 3)
                news_list.extend(sub_news)
        
        return news_list[:limit]
    
    def get_stock_news_batch(self, symbols: List[str], limit_per_stock: int = 5) -> Dict[str, List[dict]]:
        """
        批量获取多只股票新闻
        
        Args:
            symbols: 股票代码列表 ['600519', '000858', ...]
            limit_per_stock: 每只股票返回数量
        
        Returns:
            {股票代码：新闻列表}
        """
        result = {}
        
        for symbol in symbols:
            try:
                df = ak.stock_news_em(symbol=symbol)
                news_list = []
                for _, row in df.head(limit_per_stock).iterrows():
                    news_list.append({
                        'title': row.get('新闻标题', ''),
                        'content': row.get('新闻内容', ''),
                        'source': row.get('文章来源', '东方财富'),
                        'publish_time': row.get('发布时间', ''),
                        'url': row.get('相关链接', ''),
                        'category': '个股新闻',
                        'symbol': symbol
                    })
                result[symbol] = news_list
            except Exception as e:
                result[symbol] = []
                print(f"[新闻] 获取 {symbol} 新闻失败：{e}", file=sys.stderr)
        
        return result
    
    def analyze_industry_sentiment(self, news_list: List[dict]) -> Dict[str, dict]:
        """
        分析各行业情绪
        
        Args:
            news_list: 新闻列表
        
        Returns:
            {行业：{positive: N, negative: N, neutral: N, score: XX}}
        """
        industry_stats = {}
        
        positive_keywords = ['增长', '利好', '突破', '超预期', '业绩', '中标', '合作', '创新', '上涨', '盈利']
        negative_keywords = ['下跌', '亏损', '风险', '违规', '处罚', '下滑', '诉讼', '调查', '暴跌', '衰退']
        
        for news in news_list:
            category = news.get('category', '其他')
            if category not in industry_stats:
                industry_stats[category] = {'positive': 0, 'negative': 0, 'neutral': 0, 'total': 0}
            
            industry_stats[category]['total'] += 1
            
            text = (news.get('title', '') + ' ' + news.get('content', '')).lower()
            
            pos_count = sum(1 for kw in positive_keywords if kw in text)
            neg_count = sum(1 for kw in negative_keywords if kw in text)
            
            if pos_count > neg_count:
                industry_stats[category]['positive'] += 1
            elif neg_count > pos_count:
                industry_stats[category]['negative'] += 1
            else:
                industry_stats[category]['neutral'] += 1
        
        # 计算情绪分数
        for ind, stats in industry_stats.items():
            total = stats['total']
            if total > 0:
                stats['score'] = 50 + (stats['positive'] - stats['negative']) / total * 50
            else:
                stats['score'] = 50
        
        return industry_stats
    
    def get_hot_concepts(self, news_list: List[dict], top_n: int = 5) -> List[str]:
        """
        从新闻中识别热点概念
        
        Args:
            news_list: 新闻列表
            top_n: 返回前 N 个
        
        Returns:
            热点概念列表
        """
        industry_count = {}
        
        for news in news_list:
            category = news.get('category', '')
            if category and category != '财经综合' and category != '个股新闻':
                industry_count[category] = industry_count.get(category, 0) + 1
        
        # 按提及次数排序
        sorted_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)
        return [ind[0] for ind in sorted_industries[:top_n]]
    
    def _save_news(self, news_list: List[dict], keyword: str = ''):
        """保存新闻到数据库"""
        if not news_list:
            return
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        for news in news_list:
            cursor.execute('''
                INSERT INTO news (keyword, category, title, content, source, publish_time, url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                keyword,
                news.get('category', ''),
                news.get('title', ''),
                news.get('content', ''),
                news.get('source', ''),
                news.get('publish_time', ''),
                news.get('url', ''),
                datetime.now().isoformat()
            ))
        
        conn.commit()
        conn.close()
    
    def get_recent_news_from_db(self, hours: int = 12, limit: int = 50) -> List[dict]:
        """
        从数据库获取最近 N 小时的新闻
        
        Args:
            hours: 获取过去多少小时的新闻（默认 12 小时）
            limit: 返回数量限制
        
        Returns:
            新闻列表
        """
        news_list = []
        
        try:
            from datetime import datetime, timedelta
            cutoff_time = (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S')
            
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT keyword, category, title, content, source, publish_time, url, sentiment
                FROM news 
                WHERE datetime(created_at) >= datetime('now', '-' || ? || ' hours')
                ORDER BY publish_time DESC
                LIMIT ?
            ''', (hours, limit))
            
            rows = cursor.fetchall()
            for row in rows:
                news_list.append({
                    'keyword': row[0],
                    'category': row[1],
                    'title': row[2],
                    'content': row[3],
                    'source': row[4],
                    'publish_time': row[5],
                    'url': row[6],
                    'sentiment': row[7]
                })
            
            conn.close()
            print(f"[数据库] 获取过去 {hours} 小时的新闻: {len(news_list)} 条", file=sys.stderr)
            
        except Exception as e:
            print(f"[数据库] 获取新闻失败: {e}", file=sys.stderr)
        
        return news_list


if __name__ == '__main__':
    # 测试
    engine = AKShareEnhancedEngine()
    
    print('\n=== 测试财经新闻 ===')
    finance_news = engine.get_finance_news(limit=5)
    for n in finance_news:
        print(f"  - {n['title'][:50]}...")
    
    print('\n=== 测试行业新闻（半导体） ===')
    industry_news = engine.get_industry_news('半导体', limit=5)
    for n in industry_news:
        print(f"  - [{n['category']}] {n['title'][:50]}...")
    
    print('\n=== 测试批量个股新闻 ===')
    batch_news = engine.get_stock_news_batch(['600519', '000858', '002415'], limit_per_stock=2)
    for symbol, news in batch_news.items():
        print(f"  {symbol}: {len(news)} 条")
