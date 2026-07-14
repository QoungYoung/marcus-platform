#!/usr/bin/env python3

import sys
from pathlib import Path

# 本地模块
sys.path.insert(0, str(Path(__file__).parent))
from _api_config import DEEPSEEK_API_KEY, DEEPSEEK_API_HOST, DEEPSEEK_MODEL, TUSHARE_TOKEN

# workspace_detector
sys.path.insert(0, str(Path(__file__).parent))
from workspace_detector import WORKSPACE
# -*- coding: utf-8 -*-
"""
新闻定时采集器（AI 增强版）

功能：
- 每 10 分钟自动采集财经新闻
- 自动去重（基于标题 + 发布时间）
- AI 智能识别板块/情绪（DeepSeek）
- 仅分析新增新闻，复用已有结果
- 持久化存储到 SQLite

定时：09, 19, 29, 39, 49, 59 分执行
"""

import akshare as ak
import sqlite3
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import hashlib
import json


# ============================================================================
# AI 分析器配置（DeepSeek）
# ============================================================================


# Tushare API 配置（支持 TUSHARE_API_URL 环境变量切换代理）
TUSHARE_URL = os.getenv("TUSHARE_API_URL", "https://api.tushare.pro")

# ============================================================================
# 行业分类配置
# ============================================================================

# 导入细分行业关键词映射（109 个，与股票池 industry 字段匹配）
try:
    from industry_keywords import INDUSTRY_KEYWORDS, match_industry
except ImportError:
    # fallback 到简化版
    INDUSTRY_KEYWORDS = {
        '半导体': ['芯片', '半导体', '集成电路'],
        '软件服务': ['软件', 'AI', '云计算'],
        '医药生物': ['医药', '疫苗', '创新药'],
        '电气设备': ['电气', '电网', '光伏'],
        '汽车整车': ['汽车', '新能源车', '特斯拉'],
        '银行': ['银行', '工行', '建行'],
        '白酒': ['白酒', '茅台', '五粮液'],
    }
    
    def match_industry(text: str) -> list:
        text_lower = text.lower()
        matched = []
        for industry, keywords in INDUSTRY_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in text_lower:
                    matched.append(industry)
                    break
        return matched


# 申万一级行业列表
SW_INDUSTRIES_L1 = [
    '农林牧渔', '基础化工', '钢铁', '有色金属', '电子',
    '汽车', '家用电器', '食品饮料', '纺织服饰', '轻工制造',
    '医药生物', '公用事业', '交通运输', '房地产', '商贸零售',
    '社会服务', '银行', '非银金融', '综合', '建筑材料',
    '建筑装饰', '电力设备', '机械设备', '国防军工', '计算机',
    '传媒', '通信', '煤炭', '石油石化', '环保', '美容护理'
]

# 正面/负面情绪关键词（fallback 用，已在 industry_keywords.py 中定义）
# 这里保留是为了兼容旧代码
if 'POSITIVE_KEYWORDS' not in globals():
    POSITIVE_KEYWORDS = ['增长', '利好', '突破', '超预期', '业绩', '中标', '合作', '创新', '上涨', '盈利', '预增', '重组', '获批', '大单']
    NEGATIVE_KEYWORDS = ['下跌', '亏损', '风险', '违规', '处罚', '下滑', '诉讼', '调查', '暴跌', '衰退', '预亏', '减持', '违约', '退市']


# ============================================================================
# Tushare 行业分类获取（本地缓存）
# ============================================================================

INDUSTRY_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'sw_industries.json')

def _get_industries_from_tushare(cache_only: bool = False) -> List[str]:
    """
    从 Tushare 获取申万一级行业分类（本地缓存）
    
    Args:
        cache_only: True=仅从缓存读取，False=缓存失效时从 API 获取
    
    Returns:
        行业名称列表
    """
    import urllib.request
    import urllib.error
    import json
    import ssl
    from pathlib import Path
    
    # 1. 尝试从缓存读取
    if os.path.exists(INDUSTRY_CACHE_FILE):
        try:
            with open(INDUSTRY_CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            
            industries = cache.get('industries', [])
            updated_at = cache.get('updated_at', '')
            
            if industries:
                print(f"[Tushare] 从缓存加载 {len(industries)} 个一级行业 (更新：{updated_at})", file=sys.stderr)
                return industries
        except Exception as e:
            print(f"[Tushare] 缓存读取失败：{e}", file=sys.stderr)
    
    # 2. 缓存不存在或为空
    if cache_only:
        print(f"[Tushare] 缓存模式：无可用缓存，使用 fallback", file=sys.stderr)
        return list(INDUSTRY_KEYWORDS.keys())
    
    # 3. 从 API 获取并保存缓存
    try:
        payload = {
            'api_name': 'index_classify',
            'token': TUSHARE_TOKEN,
            'params': {'src': 'SW2021', 'level': 'L1'}
        }
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            TUSHARE_URL,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        
        context = ssl.create_default_context()
        with urllib.request.urlopen(req, context=context, timeout=15) as response:
            response_data = response.read().decode('utf-8')
        
        api_response = json.loads(response_data)
        
        if api_response.get('code') == 0:
            fields = api_response['data']['fields']
            items = api_response['data']['items']
            
            industries = []
            for item in items:
                row = dict(zip(fields, item))
                name = row.get('industry_name', '')
                if name:
                    industries.append(name)
            
            # 保存缓存
            cache_dir = os.path.dirname(INDUSTRY_CACHE_FILE)
            os.makedirs(cache_dir, exist_ok=True)
            
            cache_data = {
                'industries': industries,
                'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'source': 'SW2021_L1'
            }
            
            with open(INDUSTRY_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
            print(f"[Tushare] 获取到 {len(industries)} 个一级行业，已保存缓存", file=sys.stderr)
            return industries
        else:
            print(f"[Tushare] API 获取失败：{api_response.get('msg', '')}", file=sys.stderr)
            return list(INDUSTRY_KEYWORDS.keys())
    
    except Exception as e:
        print(f"[Tushare] 异常：{e}", file=sys.stderr)
        return list(INDUSTRY_KEYWORDS.keys())  # fallback 到硬编码


def _load_industries_cached() -> List[str]:
    """
    快速加载缓存的行业列表（不请求 API）
    用于 AI 分析时的 Prompt 构建
    """
    return _get_industries_from_tushare(cache_only=True)


# ============================================================================
# AI 分析函数（DeepSeek）
# ============================================================================

def _ai_analyze_combined_batch(news_items: list) -> dict:
    """
    批量分析多条新闻（一次 API 调用搞定行业+情绪+影响力）
    输入: [{title, content}, ...]  最多 5 条
    返回: {results: [{sectors, sentiment_score, event_type, impact_level, confidence}, ...]}
    """
    import urllib.request
    import urllib.error
    import ssl
    import time as time_module

    industry_list = list(INDUSTRY_KEYWORDS.keys())
    industry_str = '、'.join(industry_list[:25])

    news_texts = []
    for i, n in enumerate(news_items):
        news_texts.append(f"[{i+1}] 标题：{n['title'][:60]}\n内容：{(n.get('content') or '')[:200]}")

    system_prompt = f"""你是专业的 A 股交易消息面分析师。同时完成两件事：
1. 判断每条新闻的相关板块、情绪、事件类型
2. 判断每条新闻对 A 股的影响力等级

板块列表：{industry_str}
事件类型：业绩公告、政策发布、技术突破、并购重组、产品发布、行业数据、市场动态、其他
影响力等级：
- S 级：国家政策/领导讲话、重大地缘冲突、汇率/大宗商品剧烈波动、资金大幅异动
- A 级：行业政策、产品/技术突破、业绩超预期、重要合作
- B 级：公司回购、业绩增长、分析师评级调整
- C 级：专家建议、市场观望、无实质内容

输出严格 JSON 数组格式（每条新闻一个对象）：
[
  {{"sectors": ["半导体"], "sentiment_score": 75, "event_type": "技术突破", "impact_level": "A", "confidence": 0.85}},
  {{"sectors": ["综合"], "sentiment_score": 50, "event_type": "其他", "impact_level": "C", "confidence": 0.6}}
]"""

    user_prompt = "分析以下 " + str(len(news_items)) + " 条新闻：\n\n" + "\n".join(news_texts) + "\n\n只返回 JSON 数组，不要其他文字。"

    try:
        request_body = {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
            "stream": False
        }

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}'
        }

        data = json.dumps(request_body).encode('utf-8')
        req = urllib.request.Request(
            f'https://{DEEPSEEK_API_HOST}/v1/chat/completions',
            data=data,
            headers=headers,
            method='POST'
        )

        context = ssl.create_default_context()
        start = time_module.time()
        with urllib.request.urlopen(req, context=context, timeout=None) as response:  # 无超时：用户哲学优先结果准确性
            response_data = response.read().decode('utf-8')
        print(f"[AI 综合分析] 批量 {len(news_items)} 条，耗时 {time_module.time()-start:.1f}秒", file=sys.stderr)

        api_response = json.loads(response_data)
        ai_content = api_response['choices'][0]['message']['content'].strip()

        if ai_content.startswith('```json'):
            ai_content = ai_content[7:]
        if ai_content.endswith('```'):
            ai_content = ai_content[:-3]
        ai_content = ai_content.strip()

        results = json.loads(ai_content)
        return {'results': results, 'count': len(news_items)}

    except Exception as e:
        print(f"[AI 综合分析] 失败：{e}", file=sys.stderr)
        return None


def _ai_analyze_news(title: str, content: str = '') -> dict:
    """
    使用 DeepSeek AI 分析单条新闻
    返回：{sectors: [], sentiment_score: 0-100, event_type: '', confidence: 0-1}
    """
    import urllib.request
    import urllib.error
    import ssl
    
    # 细分行业列表（109 个，与股票池 industry 字段匹配）
    industry_list = list(INDUSTRY_KEYWORDS.keys())
    industry_str = '、'.join(industry_list[:30]) + '等'  # 列前 30 个
    
    try:
        system_prompt = f"""你是一个专业的 A 股交易消息面分析师。分析财经新闻，输出 JSON：
{{
    "sectors": ["半导体", "软件服务"],  // 相关板块（可多个，从细分行业选）
    "sentiment_score": 75,  // 0-100，>60 正面，<40 负面
    "event_type": "技术突破",  // 事件类型
    "confidence": 0.9  // 置信度 0-1
}}

A 股细分行业包括：{industry_str}
事件类型：业绩公告、政策发布、技术突破、并购重组、产品发布、行业数据、市场动态、其他

注意：板块名称必须使用细分行业标准名称（如"半导体"不是"电子"，"软件服务"不是"计算机"，"白酒"不是"食品饮料"）"""

        user_prompt = f"""分析这条新闻：
标题：{title}
内容：{content[:500] if content else ''}

输出严格 JSON 格式，只返回 JSON，不要其他文字。"""

        request_body = {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 256,
            "stream": False
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}'
        }
        
        data = json.dumps(request_body).encode('utf-8')
        req = urllib.request.Request(
            f'https://{DEEPSEEK_API_HOST}/v1/chat/completions',
            data=data,
            headers=headers,
            method='POST'
        )
        
        context = ssl.create_default_context()
        with urllib.request.urlopen(req, context=context, timeout=None) as response:  # 无超时：用户哲学优先结果准确性
            response_data = response.read().decode('utf-8')
        
        api_response = json.loads(response_data)
        ai_content = api_response['choices'][0]['message']['content'].strip()
        
        # 清理 markdown
        if ai_content.startswith('```json'):
            ai_content = ai_content[7:]
        if ai_content.endswith('```'):
            ai_content = ai_content[:-3]
        ai_content = ai_content.strip()
        
        result = json.loads(ai_content)
        
        # 验证字段
        if 'sectors' not in result:
            result['sectors'] = ['财经综合']
        if 'sentiment_score' not in result:
            result['sentiment_score'] = 50
        if 'event_type' not in result:
            result['event_type'] = '其他'
        if 'confidence' not in result:
            result['confidence'] = 0.5
        
        return result
        
    except Exception as e:
        print(f"[AI 分析] 失败：{e}", file=sys.stderr)
        return None


def _fallback_analyze(title: str, content: str = '') -> dict:
    """
    fallback：关键词匹配（AI 失败时用）
    使用细分行业分类（109 个，与股票池 industry 字段匹配）
    """
    # 使用 industry_keywords.py 的 match_industry 函数
    sectors = match_industry(title + ' ' + content)
    
    if not sectors:
        sectors = ['综合']
    
    # 情绪分析
    text = (title + ' ' + content).lower()
    pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw.lower() in text)
    neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw.lower() in text)
    
    if pos_count + neg_count > 0:
        sentiment_score = 50 + (pos_count - neg_count) / (pos_count + neg_count) * 50
    else:
        sentiment_score = 50
    
    return {
        'sectors': sectors,
        'sentiment_score': sentiment_score,
        'event_type': '其他',
        'confidence': 0.5
    }


class NewsCollector:
    """新闻采集器"""
    
    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = str(WORKSPACE / "data")

        self.data_dir = os.path.expanduser(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)

        self.db_file = os.path.join(self.data_dir, "news.db")
        self._init_database()
    
    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='news'")
        table_exists = cursor.fetchone()
        
        if table_exists:
            # 表已存在，检查并添加缺失的列
            cursor.execute("PRAGMA table_info(news)")
            columns = [col[1] for col in cursor.fetchall()]
            
            # 添加缺失的列
            if 'category' not in columns:
                cursor.execute('ALTER TABLE news ADD COLUMN category TEXT')
                print(f"[采集器] 添加 category 列", file=sys.stderr)
            
            if 'sentiment' not in columns:
                cursor.execute('ALTER TABLE news ADD COLUMN sentiment TEXT')
                print(f"[采集器] 添加 sentiment 列", file=sys.stderr)
            
            if 'keyword' not in columns:
                cursor.execute('ALTER TABLE news ADD COLUMN keyword TEXT')
                print(f"[采集器] 添加 keyword 列", file=sys.stderr)
            
            if 'hash' not in columns:
                cursor.execute('ALTER TABLE news ADD COLUMN hash TEXT')
                print(f"[采集器] 添加 hash 列", file=sys.stderr)
            
            if 'concepts' not in columns:
                cursor.execute('ALTER TABLE news ADD COLUMN concepts TEXT')
                print(f"[采集器] 添加 concepts 列", file=sys.stderr)
        else:
            # 创建新表
            cursor.execute('''
                CREATE TABLE news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT,
                    source TEXT,
                    publish_time TEXT,
                    url TEXT,
                    category TEXT,
                    sentiment TEXT,
                    keyword TEXT,
                    concepts TEXT,
                    hash TEXT,
                    created_at TEXT NOT NULL
                )
            ''')
            print(f"[采集器] 创建 news 表", file=sys.stderr)
        
        # 创建索引（忽略已存在的）
        try:
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_time ON news (publish_time)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_category ON news (category)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_sentiment ON news (sentiment)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_hash ON news (hash)')
        except Exception as e:
            print(f"[采集器] 索引创建警告：{e}", file=sys.stderr)
        
        conn.commit()
        conn.close()
        print(f"[采集器] 数据库已初始化：{self.db_file}", file=sys.stderr)
    
    def _compute_hash(self, title: str, publish_time: str) -> str:
        """计算新闻唯一哈希（用于去重）"""
        text = f"{title}|{publish_time}"
        return hashlib.md5(text.encode('utf-8')).hexdigest()
    
    def _detect_category(self, title: str, content: str = '') -> str:
        """自动检测新闻所属板块"""
        text = (title + ' ' + content).lower()
        
        for industry, keywords in INDUSTRY_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in text:
                    return industry
        
        return '财经综合'
    
    def _save_news(self, news: dict):
        """保存单条新闻（去重）"""
        hash_val = self._compute_hash(news['title'], news['publish_time'])
        
        if self._is_duplicate(hash_val):
            return False  # 重复，跳过
        
        # 确保 category 不为 None
        category = news.get('category') or '财经综合'
        sentiment = news.get('sentiment') or 'neutral'
        keyword = news.get('keyword') or ''
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO news (title, content, source, publish_time, url, category, sentiment, keyword, concepts, hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                news['title'],
                news.get('content', ''),
                news.get('source', '东方财富'),
                news['publish_time'],
                news.get('url', ''),
                category,
                sentiment,
                keyword,
                news.get('concepts', ''),
                hash_val,
                datetime.now().isoformat()
            ))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # 哈希冲突，跳过
            return False
        finally:
            conn.close()
    
    def _detect_sentiment(self, title: str, content: str = '') -> str:
        """检测新闻情绪"""
        text = (title + ' ' + content).lower()
        
        pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw.lower() in text)
        neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw.lower() in text)
        
        if pos_count > neg_count:
            return 'positive'
        elif neg_count > pos_count:
            return 'negative'
        else:
            return 'neutral'
    
    def _is_duplicate_by_title(self, title: str) -> bool:
        """检查标题是否重复（跨时间）"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM news WHERE title = ?', (title,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    def _is_duplicate(self, hash_val: str) -> bool:
        """检查是否重复（哈希）"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM news WHERE hash = ?', (hash_val,))
        exists = cursor.fetchone() is not None

        conn.close()
        return exists
    
    def _analyze_news(self, title: str, content: str = '') -> dict:
        """
        分析新闻（仅 AI，置信度过低则标记跳过）
        返回：{category, sentiment, keyword, _skip: bool}
        """
        ai_result = _ai_analyze_news(title, content)

        if ai_result:
            confidence = ai_result.get('confidence', 1.0)
            # 置信度低于阈值 → 标记跳过，不入库
            if confidence < 0.6:
                print(f"[AI 分析] 置信度 {confidence:.2f} < 0.6，跳过: {title[:30]}...", file=sys.stderr)
                return {'category': None, 'sentiment': 'neutral', 'keyword': '其他', '_skip': True}

            sectors = ai_result.get('sectors', ['综合'])
            category = sectors[0] if sectors else '综合'

            score = ai_result.get('sentiment_score', 50)
            if score > 60:
                sentiment = 'positive'
            elif score < 40:
                sentiment = 'negative'
            else:
                sentiment = 'neutral'

            keyword = ai_result.get('event_type', '其他')

            print(f"[AI 分析] ✓ {title[:30]}... → {category}, {sentiment} (置信{confidence:.2f})", file=sys.stderr)
            return {'category': category, 'sentiment': sentiment, 'keyword': keyword, '_skip': False}

        # AI 失败 → 跳过
        print(f"[AI 分析] ✗ AI 失败，跳过: {title[:30]}...", file=sys.stderr)
        return {'category': None, 'sentiment': 'neutral', 'keyword': '其他', '_skip': True}
    
    def _is_news_duplicate(self, title: str, publish_time: str) -> bool:
        """快速检查新闻是否重复（基于标题 + 时间哈希）"""
        # 先用标题检查（相同标题不管发布时间都跳过）
        if self._is_duplicate_by_title(title):
            return True
        # 再用哈希检查（标题+时间组合）
        hash_val = self._compute_hash(title, publish_time)
        return self._is_duplicate(hash_val)
    
    def collect_finance_news(self, limit: int = 50) -> int:
        """
        采集财经新闻（AI 分析，仅分析新增）
        
        策略：直接使用 AKShare 财经新闻接口，无需个股代理
        - 财联社：最快、最专业（20 条）
        - 东方财富全球资讯：覆盖广（200 条）
        - 同花顺：补充（20 条）
        
        Returns:
            新增新闻数量
        """
        new_count = 0
        ai_count = 0
        skipped = 0
        
        all_news = []
        
        try:
            # 1. 财联社新闻（最快）
            try:
                df_cls = ak.stock_info_global_cls()
                for _, row in df_cls.iterrows():
                    all_news.append({
                        'title': row.get('标题', ''),
                        'content': row.get('内容', ''),
                        'source': '财联社',
                        'publish_time': f"{row.get('发布日期', '')} {row.get('发布时间', '')}",
                        'url': ''
                    })
            except Exception as e:
                print(f"[采集器] 财联社新闻失败：{e}", file=sys.stderr)
            
            # 2. 东方财富全球资讯（覆盖广）
            try:
                df_em = ak.stock_info_global_em()
                for _, row in df_em.iterrows():
                    all_news.append({
                        'title': row.get('标题', ''),
                        'content': row.get('摘要', ''),
                        'source': '东方财富',
                        'publish_time': row.get('发布时间', ''),
                        'url': row.get('链接', '')
                    })
            except Exception as e:
                print(f"[采集器] 东方财富新闻失败：{e}", file=sys.stderr)
            
            # 3. 同花顺新闻（补充）
            try:
                df_ths = ak.stock_info_global_ths()
                for _, row in df_ths.iterrows():
                    all_news.append({
                        'title': row.get('标题', ''),
                        'content': row.get('内容', ''),
                        'source': '同花顺',
                        'publish_time': row.get('发布时间', ''),
                        'url': row.get('链接', '')
                    })
            except Exception as e:
                print(f"[采集器] 同花顺新闻失败：{e}", file=sys.stderr)
            
            # 按发布时间排序，取最新 limit 条
            all_news = sorted(
                [n for n in all_news if n['title'] and n['publish_time']],
                key=lambda x: x['publish_time'],
                reverse=True
            )[:limit]
            
            # 去重检查，收集待分析的新闻
            to_analyze = []
            for news_item in all_news:
                if not self._is_news_duplicate(news_item['title'], news_item['publish_time']):
                    to_analyze.append(news_item)
                else:
                    skipped += 1
            
            # 动态批次：目标 3 批以内，避免频繁 API 调用触发限流
            total = len(to_analyze)
            BATCH_SIZE = max(5, (total + 2) // 3)  # 3 批内完成，最少 5 条/批
            for i in range(0, total, BATCH_SIZE):
                if i > 0:
                    import time as _time
                    _time.sleep(3)
                batch = to_analyze[i:i+BATCH_SIZE]
                batch_input = [{'title': n['title'], 'content': n.get('content', '')} for n in batch]
                
                result = _ai_analyze_combined_batch(batch_input)
                
                if result and 'results' in result:
                    for j, analysis in enumerate(result['results']):
                        news_item = batch[j]

                        confidence = analysis.get('confidence', 1.0)
                        # 置信度过低 → 跳过
                        if confidence < 0.6:
                            print(f"[采集器] 置信度 {confidence:.2f} < 0.6 跳过: {news_item['title'][:30]}...", file=sys.stderr)
                            continue

                        sectors = analysis.get('sectors', ['综合'])
                        score = analysis.get('sentiment_score', 50)
                        if score > 60:
                            sentiment = 'positive'
                        elif score < 40:
                            sentiment = 'negative'
                        else:
                            sentiment = 'neutral'

                        impact_level = analysis.get('impact_level', 'C')

                        # 只保留 S/A 级
                        if impact_level not in ['S', 'A']:
                            print(f"[采集器] 过滤 {impact_level} 级: {news_item['title'][:30]}...", file=sys.stderr)
                            continue

                        print(f"[采集器] ✓ 保留 {impact_level} 级: {news_item['title'][:30]}...", file=sys.stderr)

                        news = {
                            'title': news_item['title'],
                            'content': news_item['content'],
                            'source': news_item['source'],
                            'publish_time': news_item['publish_time'],
                            'url': news_item['url'],
                            'category': sectors[0] if sectors else '综合',
                            'sentiment': sentiment,
                            'keyword': analysis.get('event_type', '其他'),
                            'concepts': ''  # 概念由后续 get_news_analysis 回写
                        }

                        if self._save_news(news):
                            new_count += 1
                            ai_count += 1
                else:
                    # API 失败 → 跳过该批次
                    print(f"[采集器] 批量分析失败，跳过该批次 {len(batch)} 条", file=sys.stderr)
            
            print(f"[采集器] 财经新闻：{new_count}新增 / {skipped}跳过 / {ai_count}AI 分析 / 共{len(all_news)}条", file=sys.stderr)
            
        except Exception as e:
            print(f"[采集器] 采集财经新闻失败：{e}", file=sys.stderr)
        
        return new_count
    
    def collect_industry_news(self, industries: List[str] = None, limit_per_industry: int = 10) -> int:
        """
        采集行业新闻（AI 分析，仅分析新增）
        
        Args:
            industries: 行业列表，None 则采集所有行业
            limit_per_industry: 每个行业采集数量
        
        Returns:
            新增新闻数量
        """
        new_count = 0
        skipped = 0
        ai_count = 0
        
        if industries is None:
            industries = list(INDUSTRY_KEYWORDS.keys())
        
        for industry in industries:
            keywords = INDUSTRY_KEYWORDS[industry]
            
            # 用前 3 个关键词采集
            for kw in keywords[:3]:
                try:
                    df = ak.stock_news_em(symbol=kw)
                    
                    for _, row in df.head(limit_per_industry // 3).iterrows():
                        title = row.get('新闻标题', '')
                        publish_time = row.get('发布时间', '')
                        
                        if not title or not publish_time:
                            continue
                        
                        # 去重检查（先于 AI 分析）
                        if self._is_news_duplicate(title, publish_time):
                            skipped += 1
                            continue
                        
                        # 行业新闻：改用 AI 分析（去除关键词 fallback）
                        analysis = self._analyze_news(title, row.get('新闻内容', ''))
                        ai_count += 1

                        # 置信度过低或 AI 失败 → 跳过
                        if analysis.get('_skip'):
                            continue
                        
                        news = {
                            'title': title,
                            'content': row.get('新闻内容', ''),
                            'source': row.get('文章来源', '东方财富'),
                            'publish_time': publish_time,
                            'url': row.get('相关链接', ''),
                            'keyword': kw,
                            'category': analysis.get('category') or industry,
                            'sentiment': analysis.get('sentiment', 'neutral')
                        }
                        
                        if self._save_news(news):
                            new_count += 1
                            
                except Exception as e:
                    continue
        
        print(f"[采集器] 行业新闻：{new_count}新增 / {skipped}跳过 / {ai_count}分析", file=sys.stderr)
        return new_count
    
    def collect_all(self, finance_limit: int = 50) -> dict:
        """
        采集所有新闻（AI 增强版）
        
        Returns:
            采集统计
        """
        start_time = datetime.now()
        print(f"[采集器] ===== 开始新闻采集 {start_time.strftime('%Y-%m-%d %H:%M')} (仅财经) =====", file=sys.stderr)

        finance_new = self.collect_finance_news(limit=finance_limit)

        total_new = finance_new
        elapsed = (datetime.now() - start_time).total_seconds()

        print(f"[采集器] ===== 采集完成：新增 {total_new} 条 / 耗时:{elapsed:.1f}s =====", file=sys.stderr)

        return {
            'timestamp': datetime.now().isoformat(),
            'finance_new': finance_new,
            'total_new': total_new,
            'elapsed_seconds': elapsed,
            'ai_mode': True
        }
    
    def get_recent_news(self, days: int = 1, limit: int = 50, category: str = None) -> List[dict]:
        """
        获取最近 N 天的新闻
        
        Args:
            days: 天数
            limit: 返回数量
            category: 板块过滤（可选）
        
        Returns:
            新闻列表
        """
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
        
        return [dict(row) for row in rows]
    
    def get_news_by_category(self, category: str, limit: int = 20) -> List[dict]:
        """按板块获取新闻"""
        return self.get_recent_news(days=3, limit=limit, category=category)
    
    def get_positive_news(self, days: int = 1, limit: int = 20) -> List[dict]:
        """获取正面新闻"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cutoff_time = (datetime.now() - timedelta(days=days)).isoformat()
        
        cursor.execute('''
            SELECT * FROM news 
            WHERE sentiment = 'positive'
            AND publish_time >= ?
            AND publish_time != ''
            ORDER BY publish_time DESC
            LIMIT ?
        ''', (cutoff_time, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_negative_news(self, days: int = 1, limit: int = 20) -> List[dict]:
        """获取负面新闻"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cutoff_time = (datetime.now() - timedelta(days=days)).isoformat()
        
        cursor.execute('''
            SELECT * FROM news 
            WHERE sentiment = 'negative'
            AND publish_time >= ?
            AND publish_time != ''
            ORDER BY publish_time DESC
            LIMIT ?
        ''', (cutoff_time, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_category_stats(self, days: int = 1) -> Dict[str, dict]:
        """获取各板块统计"""
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
    
    def show_stats(self, days: int = 1):
        """显示统计信息"""
        stats = self.get_category_stats(days=days)
        
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"📊 新闻板块统计 (最近{days}天)", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        
        for category, data in sorted(stats.items(), key=lambda x: x[1]['total'], reverse=True)[:10]:
            score = data['score']
            emoji = '🟢' if score > 60 else '🔴' if score < 40 else '🟡'
            print(f"{emoji} {category}: {data['total']}条 (正:{data['positive']}, 负:{data['negative']}, 中:{data['neutral']}) 情绪分:{score:.1f}", file=sys.stderr)
        
        print(f"{'='*60}\n", file=sys.stderr)


def main():
    """主函数 - 执行一次采集"""
    # 确保 core/ 在搜索路径中（apps/news → apps → 项目根 → core）
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))

    collector = NewsCollector()
    result = collector.collect_all()
    collector.show_stats(days=1)

    # 冷却 8 秒，避免 collect_all 的批量分析触发 DeepSeek 频率限制
    import time
    print("[热点缓存] ⏳ 冷却 8s 避免 API 限流...", file=sys.stderr)
    time.sleep(8)
    
    # 同时写入 latest_hot_sectors.json（供盘中扫描使用）
    try:
        # 优先用 DeepSeek 概念分析，回退到行业统计
        cache_data = {
            'generated_at': datetime.now().isoformat(),
            'news_count': result.get('total_new', 0),
            'source': 'akshare_stats'
        }

        try:
            # 获取最近新闻，用 DeepSeek 提取热点概念
            from news_analyzer import get_news_analysis
            analysis = get_news_analysis(news_limit=30, use_ai=True)
            sentiment = analysis.get('sentiment', {})
            hot_concepts = sentiment.get('hot_concepts', [])[:8]
            concept_scores = analysis.get('concept_scores', {})
            overall_sentiment = sentiment.get('score', 50)

            if hot_concepts:
                cache_data.update({
                    'sentiment_score': overall_sentiment,
                    'hot_concepts': hot_concepts,
                    'concept_scores': concept_scores,
                    'summary': analysis.get('summary', {}),
                    'impact_analysis': analysis.get('impact_analysis', []),
                    'source': 'deepseek_concept'
                })
                print(f"[热点缓存] ✅ DeepSeek 概念: {hot_concepts}", file=sys.stderr)

                # 将提取的概念回写到最近新闻
                if hot_concepts:
                    try:
                        conn = sqlite3.connect(collector.db_file)
                        # 取最近 1 小时 S/A 级新闻，按概念关键词匹配
                        cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
                        rows = conn.execute(
                            "SELECT id, title, content FROM news WHERE created_at >= ? AND (concepts IS NULL OR concepts = '')",
                            (cutoff,)
                        ).fetchall()
                        updated = 0
                        for news_id, title, content in rows:
                            text = (title + ' ' + (content or '')).lower()
                            matched = [c for c in hot_concepts if c.lower() in text]
                            if matched:
                                conn.execute(
                                    "UPDATE news SET concepts = ? WHERE id = ?",
                                    (','.join(matched), news_id)
                                )
                                updated += 1
                        conn.commit()
                        conn.close()
                        if updated:
                            print(f"[概念回写] ✅ {updated} 条新闻已标记概念", file=sys.stderr)
                    except Exception as e:
                        print(f"[概念回写] ⚠️ 失败: {e}", file=sys.stderr)
            else:
                raise ValueError("DeepSeek 无概念输出")
        except Exception as e:
            print(f"[热点缓存] ⚠️ DeepSeek 概念提取失败: {e}，回退到行业统计", file=sys.stderr)
            # 回退：行业统计
            stats = collector.get_category_stats(days=1)
            sorted_sectors = sorted(stats.items(), key=lambda x: x[1]['score'], reverse=True)[:8]
            hot_concepts = [cat for cat, _ in sorted_sectors if stats[cat]['total'] > 0]
            concept_scores = {cat: round(stats[cat]['score'], 1) for cat, _ in sorted_sectors if stats[cat]['total'] > 0}
            total_news = sum(d['total'] for d in stats.values())
            overall_sentiment = round(sum(d['score'] * d['total'] for d in stats.values()) / total_news, 1) if total_news > 0 else 50
            cache_data.update({
                'sentiment_score': overall_sentiment,
                'hot_concepts': hot_concepts,
                'concept_scores': concept_scores,
                'summary': {'s_level_count': 0, 'a_level_count': 0},
                'impact_analysis': [],
            })

        cache_file = WORKSPACE / "data" / "latest_hot_sectors.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"[热点缓存] ✅ 已写入 {cache_file} (来源:{cache_data.get('source')})", file=sys.stderr)
    except Exception as e:
        print(f"[热点缓存] ⚠️ 写入失败: {e}", file=sys.stderr)
    
    # 输出 JSON 结果（供 cron 调用时解析）
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    import json
    main()
