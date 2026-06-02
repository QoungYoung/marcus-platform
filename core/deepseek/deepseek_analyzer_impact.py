#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


from _api_config import DEEPSEEK_API_KEY, DEEPSEEK_API_HOST, DEEPSEEK_MODEL, TUSHARE_TOKEN
# -*- coding: utf-8 -*-
"""
DeepSeek AI 新闻影响力分析器 (独立版本)

功能：识别新闻影响力等级 (S/A/B/C) 和影响范围
"""

import sys
import json
import urllib.request
import urllib.error
import ssl
from typing import List, Dict
import time
import re



def analyze_news_impact(news_list: List[dict]) -> dict:
    """
    分析新闻影响力等级
    
    返回:
    {
        "impact_analysis": [...],
        "summary": {"s_level_count": 1, "a_level_count": 2, ...}
    }
    """
    if not news_list:
        return _empty_result()
    
    # 精简新闻列表（最多 10 条，避免 JSON 超长）
    news_items = []
    for i, n in enumerate(news_list[:10]):
        title = n.get('title', '')[:50]
        source = n.get('source', '')
        news_items.append(f"{i+1}. {title}")
    
    news_text = "\n".join(news_items)
    
    system_prompt = """A 股新闻影响力分析师。判断每条新闻的影响力等级。

S 级：国家政策 + 资金、价格大幅变动
A 级：行业事件、产品发布、业绩超预期  
B 级：公司回购、业绩增长、合作
C 级：专家建议、传闻、无实质内容

输出 JSON:
{"impact_analysis":[{"news_id":1,"title":"标题","impact_level":"S","impact_scope":"板块","affected_sectors":["半导体"],"credibility_score":90,"expected_impact":"预期","reason":"理由"}],"summary":{"s_level_count":1,"a_level_count":2,"b_level_count":3,"c_level_count":2,"top_sectors":[{"sector":"半导体","impact_score":95,"news_count":2}]}}"""
    
    user_prompt = f"分析以下新闻：\n\n{news_text}\n\n输出严格 JSON。"
    
    result = _call_api(system_prompt, user_prompt)
    
    if result and 'impact_analysis' in result:
        print(f"[影响力分析] S 级={result['summary']['s_level_count']}, A 级={result['summary']['a_level_count']}", file=sys.stderr)
        return result
    
    print(f"[影响力分析] 调用失败", file=sys.stderr)
    return _empty_result()


def _call_api(system_prompt: str, user_prompt: str) -> dict:
    try:
        request_body = {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 2048,
            "stream": False
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}'
        }
        
        print(f"[影响力] 调用 API...", file=sys.stderr)
        start = time.time()
        
        data = json.dumps(request_body).encode('utf-8')
        req = urllib.request.Request(
            f'https://{DEEPSEEK_API_HOST}/v1/chat/completions',
            data=data,
            headers=headers,
            method='POST'
        )
        
        context = ssl.create_default_context()
        with urllib.request.urlopen(req, context=context, timeout=60) as response:
            resp_data = response.read().decode('utf-8')
        
        print(f"[影响力] 响应时间：{time.time()-start:.2f}秒", file=sys.stderr)
        
        api_resp = json.loads(resp_data)
        
        if 'choices' not in api_resp or not api_resp['choices']:
            return None
        
        content = api_resp['choices'][0]['message']['content'].strip()
        if content.startswith('```json'):
            content = content[7:]
        if content.endswith('```'):
            content = content[:-3]
        content = content.strip()
        
        # 尝试解析 JSON，增加容错
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取第一个完整 JSON 对象
            match = re.search(r'\{.*"summary".*\}', content, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group())
                except:
                    return None
            else:
                return None
        
        # 验证必需字段
        if 'impact_analysis' not in result or 'summary' not in result:
            return None
        
        # 确保 summary 有所有必需字段
        summary = result['summary']
        for key in ['s_level_count', 'a_level_count', 'b_level_count', 'c_level_count', 'top_sectors']:
            if key not in summary:
                summary[key] = 0 if key != 'top_sectors' else []
        
        return result
        
    except Exception as e:
        print(f"[影响力] 异常：{e}", file=sys.stderr)
        return None


def _empty_result() -> dict:
    return {
        'impact_analysis': [],
        'summary': {
            's_level_count': 0,
            'a_level_count': 0,
            'b_level_count': 0,
            'c_level_count': 0,
            'top_sectors': []
        }
    }


if __name__ == '__main__':
    test_news = [
        {'title': '光伏组件价格实质性上调 20%', 'source': '财联社'},
        {'title': '腾讯推出 WorkBuddy 兼容 OpenClaw', 'source': '东方财富'},
        {'title': '专家建议加大 AI 投入', 'source': '证券时报'},
        {'title': '液冷服务器概念活跃，海鸥股份涨停', 'source': '财联社'},
        {'title': '大基金三期 3000 亿投资半导体', 'source': '新华社'},
    ]
    
    print('=== 影响力分析测试 ===\n')
    result = analyze_news_impact(test_news)
    
    print(f"S 级：{result['summary']['s_level_count']}")
    print(f"A 级：{result['summary']['a_level_count']}")
    print(f"B 级：{result['summary']['b_level_count']}")
    print(f"C 级：{result['summary']['c_level_count']}")
