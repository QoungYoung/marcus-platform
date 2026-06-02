#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


from _api_config import DEEPSEEK_API_KEY, DEEPSEEK_API_HOST, DEEPSEEK_MODEL, TUSHARE_TOKEN
# -*- coding: utf-8 -*-
"""
DeepSeek Chat AI 新闻情绪分析器

使用 DeepSeek Chat API 进行 AI 语义理解分析
专用于股票交易消息面分析

特性：
- 快速稳定（8-10 秒响应）
- 60 秒超时
- 专为股票交易优化的 Prompt
"""

import sys
import json
import urllib.request
import urllib.error
import ssl
from typing import List, Dict
from pathlib import Path
import time
import re

# DeepSeek API 配置


def filter_news_with_deepseek(news_list: List[dict]) -> List[dict]:
    """使用 DeepSeek AI 过滤新闻（去重 + 排除无用信息）"""
    if not news_list:
        return []
    
    news_items = []
    for i, n in enumerate(news_list[:50]):
        title = n.get('title', '')[:100]
        content = n.get('content', '')[:200]
        category = n.get('category', '综合')
        news_items.append(f"{i+1}. [{category}] {title}")
    
    news_text = "\n".join(news_items)
    
    system_prompt = """你是一个专业的 A 股交易新闻过滤助手。请过滤以下财经新闻中的无用信息。

过滤规则：
1. 排除与 A 股投资无关（纯外交辞令、社会八卦、无投资影响的政治新闻）
2. 排除重复新闻（相同/相似标题只保留 1 条）
3. 排除信息量过低（无数字、无实质内容、纯观点建议、政策建议阶段）
4. 排除非上市公司新闻

重要：以下新闻必须保留：
- 地缘政治影响大宗商品：中东局势→石油→油气板块
- 国际关系影响供应链：中美关系→芯片/科技→半导体板块
- 宏观经济数据：GDP、CPI、PMI、降准降息→大盘走势
- 行业政策落地：不是"建议"，是已执行的政策
- 大宗商品价格波动：石油、黄金、铜、锂等→相关板块

返回格式（严格 JSON 数组）：
[{"index": 1, "title": "新闻标题", "reason": "保留理由"}]

只返回保留的新闻索引和标题，不要其他文字。"""
    
    user_prompt = f"请过滤以下财经新闻：\n\n{news_text}\n\n输出严格 JSON 数组格式，只包含保留的新闻。"
    
    filtered = _call_deepseek_api(system_prompt, user_prompt)
    
    if filtered and isinstance(filtered, list):
        result = []
        for item in filtered:
            idx = item.get('index')
            if idx and idx <= len(news_list):
                result.append(news_list[idx - 1])
        
        print(f"[DeepSeek 过滤] {len(news_list)}条 → {len(result)}条", file=sys.stderr)
        return result
    
    return news_list


def analyze_news_with_deepseek(news_list: List[dict]) -> dict:
    """使用 DeepSeek Chat 分析新闻情绪"""
    if not news_list:
        return _empty_result()
    
    news_summary = []
    for i, n in enumerate(news_list[:30]):
        title = n.get('title', '')[:80]
        category = n.get('category', '综合')
        news_summary.append(f"{i+1}. [{category}] {title}")
    
    news_text = "\n".join(news_summary)
    
    system_prompt = """你是一个专业的 A 股交易消息面分析师。请分析以下财经新闻的情绪倾向和投资价值。

核心能力：
1. 理解上下文语义（如"增长放缓"是负面，不是正面）
2. 识别反讽（如"业绩大增，股价暴跌"是负面）
3. 区分影响程度（"暴涨"权重远高于"上涨"）
4. 提取真正的催化剂和风险
5. 识别热点概念 —— 只能从下方【标准概念词汇表】中选择概念名，不得自创

评分标准：
- 70+ = 强烈正面（重大利好、政策扶持、业绩暴增）→ 激进买入
- 55-70 = 温和正面（小幅增长、行业景气）→ 保守买入
- 45-55 = 中性（平淡、无明显倾向）→ 观望
- 30-45 = 温和负面（小幅下滑、风险隐忧）→ 减仓
- <30 = 强烈负面（暴雷、处罚、衰退）→ 清仓避险

输出严格 JSON 格式：
{
    "score": 58.5,
    "positive": 15,
    "negative": 2,
    "neutral": 10,
    "total": 27,
    "hot_concepts": ["半导体", "人工智能"],
    "concept_scores": {"半导体": 82.0, "人工智能": 75.0},
    "catalysts": ["AI 存储需求进入超级周期"],
    "risks": ["房地产销售下滑"],
    "analysis": "一句话总结",
    "confidence": 0.85,
    "trade_suggestion": "重点关注半导体、人工智能概念"
}"""

    # 注入概念词汇表（约80个常用概念）
    concept_vocab = ""
    try:
        from news_analyzer import get_concept_vocabulary_sample
        concept_vocab = get_concept_vocabulary_sample(limit=80)
    except Exception:
        pass

    if concept_vocab:
        system_prompt += f"\n\n【标准概念词汇表】hot_concepts 和 concept_scores 的 key 只能从以下概念名中选择：\n{concept_vocab}"

    user_prompt = f"请分析以下财经新闻：\n\n{news_text}\n\n输出严格 JSON 格式，必须包含 concept_scores 字段。"
    
    result = _call_deepseek_api(system_prompt, user_prompt)
    
    if result and result.get('score') is not None:
        print(f"[DeepSeek] 分数={result['score']}, 正面={result['positive']}, 负面={result['negative']}", file=sys.stderr)
        return result
    
    return _empty_result()


def _call_deepseek_api(system_prompt: str, user_prompt: str) -> dict:
    """调用 DeepSeek Chat API（非流式，快速稳定）"""
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
        
        print(f"[DeepSeek] 调用 API...", file=sys.stderr)
        start_time = time.time()
        
        data = json.dumps(request_body).encode('utf-8')
        req = urllib.request.Request(
            f'https://{DEEPSEEK_API_HOST}/v1/chat/completions',
            data=data,
            headers=headers,
            method='POST'
        )
        
        context = ssl.create_default_context()
        with urllib.request.urlopen(req, context=context, timeout=60) as response:
            response_data = response.read().decode('utf-8')
        
        elapsed = time.time() - start_time
        print(f"[DeepSeek] 响应时间：{elapsed:.2f}秒", file=sys.stderr)
        
        api_response = json.loads(response_data)
        
        if 'choices' not in api_response or not api_response['choices']:
            return None
        
        ai_content = api_response['choices'][0]['message']['content']
        ai_content = ai_content.strip()
        if ai_content.startswith('```json'):
            ai_content = ai_content[7:]
        if ai_content.endswith('```'):
            ai_content = ai_content[:-3]
        ai_content = ai_content.strip()
        
        try:
            result = json.loads(ai_content)
        except json.JSONDecodeError:
            json_match = re.search(r'\{[^{}]*\}', ai_content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise
        
        return result
        
    except Exception as e:
        print(f"[DeepSeek] 异常：{e}", file=sys.stderr)
        return None


def _empty_result() -> dict:
    """返回空结果"""
    return {
        'score': 50.0,
        'positive': 0,
        'negative': 0,
        'neutral': 0,
        'total': 0,
        'hot_concepts': [],
        'catalysts': [],
        'risks': [],
        'analysis': 'API 调用失败',
        'confidence': 0.0
    }


if __name__ == '__main__':
    test_news = [
        {'title': 'AI 芯片需求爆发，英伟达财报超预期', 'category': '半导体', 'content': ''},
        {'title': '新能源车销量增长放缓，特斯拉裁员 10%', 'category': '新能源', 'content': ''},
    ]
    
    print("\n=== DeepSeek Chat AI 新闻分析测试 ===\n")
    result = analyze_news_with_deepseek(test_news)
    
    print(f"\n情绪分数：{result['score']}/100")
    print(f"正面：{result['positive']} | 负面：{result['negative']} | 中性：{result['neutral']}")
