#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


from _api_config import DEEPSEEK_API_KEY, DEEPSEEK_API_HOST, DEEPSEEK_MODEL, TUSHARE_TOKEN
# -*- coding: utf-8 -*-
"""
DeepSeek AI 新闻综合分析器 (合并版)

功能：单次 API 调用完成情绪分析 + 影响力分级
优势：减少 50% API 调用，节省 10-15 秒
"""

import sys
import json
import urllib.request
import urllib.error
import ssl
from typing import List, Dict
import time



def analyze_news_combined(news_list: List[dict]) -> dict:
    """
    综合分析：情绪 + 影响力
    
    返回:
    {
        "sentiment": {"score": 62.5, "positive": 16, ...},
        "impact_analysis": [...],
        "summary": {"s_level_count": 2, "a_level_count": 1, ...}
    }
    """
    if not news_list:
        return _empty_result()
    
    # 构建新闻列表（最多 15 条）
    news_items = []
    for i, n in enumerate(news_list[:8]):
        title = n.get('title', '')[:60]
        source = n.get('source', '')
        news_items.append(f"{i+1}. [{source}] {title}")
    
    news_text = "\n".join(news_items)
    
    system_prompt = """你是 A 股新闻分析师。完成两项任务：

## 任务 1: 情绪分析
- 评分 0-100，>60 正面，<40 负面
- 统计正/负/中性新闻数量
- 识别热点概念并单独评分 —— 只能从下方【标准概念词汇表】中选择概念名，不得自创

## 任务 2: 影响力分级
- S 级：国家政策 + 资金、价格大幅变动
- A 级：行业事件、产品发布、业绩超预期
- B 级：公司层面利好
- C 级：传闻/建议

输出 JSON:
{
  "sentiment": {
    "score": 62.5,
    "positive": 10,
    "negative": 3,
    "neutral": 2,
    "total": 15,
    "hot_concepts": ["半导体", "AI"],
    "concept_scores": {"半导体": 85, "AI": 78}
  },
  "impact_analysis": [
    {"news_id": 1, "title": "标题", "impact_level": "S", "impact_scope": "板块", "affected_sectors": ["半导体"], "credibility_score": 90, "expected_impact": "预期", "reason": "理由"}
  ],
  "summary": {
    "s_level_count": 1,
    "a_level_count": 2,
    "b_level_count": 3,
    "c_level_count": 2,
    "top_sectors": [{"sector": "半导体", "impact_score": 95, "news_count": 2}]
  }
}"""

    # 注入概念词汇表
    concept_vocab = ""
    try:
        from news_analyzer import get_concept_vocabulary_sample
        concept_vocab = get_concept_vocabulary_sample(limit=80)
    except Exception:
        pass

    if concept_vocab:
        system_prompt += f"\n\n【标准概念词汇表】hot_concepts 和 concept_scores 的 key 只能从以下概念名中选择：\n{concept_vocab}"

    user_prompt = f"""分析以下新闻：

{news_text}

输出JSON: {{
  "sentiment": {{"score":0-100,"positive":N,"negative":N,"neutral":N,"hot_concepts":[],"concept_scores":{{}}}},
  "summary": {{"s_level_count":N,"a_level_count":N,"b_level_count":N,"c_level_count":N,"top_sectors":[]}}
}}"""
    
    # 带重试的 API 调用（处理 HTTP 500 等偶发错误）
    for attempt in range(3):
        try:
            result = _call_api(system_prompt, user_prompt)
        except Exception as e:
            print(f"[综合分析] ⚠️ API 调用异常（尝试 {attempt+1}/3）: {e}", file=sys.stderr)
            result = None

        if result and 'sentiment' in result and ('summary' in result or 'impact_analysis' in result):
            sentiment = result['sentiment']
            summary = result.get('summary', {})
            print(f"[综合分析] ✅ 成功（重试{attempt}次）情绪={sentiment['score']:.1f}, S 级={summary.get('s_level_count', 0)}, A 级={summary.get('a_level_count', 0)}", file=sys.stderr)
            return result

        if attempt < 2:
            wait = (attempt + 1) * 5  # 2s→5s, 4s→10s，给 API 限流恢复时间
            print(f"[综合分析] ⏳ {wait}s 后重试...", file=sys.stderr)
            time.sleep(wait)

    print(f"[综合分析] ❌ 3次尝试均失败", file=sys.stderr)
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
            "max_tokens": 4096,
            "stream": False
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}'
        }
        
        print(f"[综合分析] 调用 API...", file=sys.stderr)
        start = time.time()
        
        data = json.dumps(request_body).encode('utf-8')
        req = urllib.request.Request(
            f'https://{DEEPSEEK_API_HOST}/v1/chat/completions',
            data=data,
            headers=headers,
            method='POST'
        )
        
        context = ssl.create_default_context()
        with urllib.request.urlopen(req, context=context, timeout=None) as response:  # 无超时限制：用户哲学优先结果准确性
            resp_data = response.read().decode('utf-8')
        
        elapsed = time.time() - start
        print(f"[综合分析] 响应时间：{elapsed:.2f}秒", file=sys.stderr)
        
        api_resp = json.loads(resp_data)
        
        if 'choices' not in api_resp or not api_resp['choices']:
            return None
        
        content = api_resp['choices'][0]['message']['content'].strip()
        if content.startswith('```json'):
            content = content[7:]
        if content.endswith('```'):
            content = content[:-3]
        content = content.strip()

        # 解析 JSON：先尝试直接 loads，再去 markdown 包裹，最后找第一个 { 用 raw_decode
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # 去掉 markdown code fence
            content2 = content
            if content2.startswith('```json'):
                content2 = content2[7:]
            elif content2.startswith('```'):
                content2 = content2[3:]
            content2 = content2.strip()
            if content2.endswith('```'):
                content2 = content2[:-3].strip()
            try:
                result = json.loads(content2)
            except json.JSONDecodeError:
                # raw_decode 能正确处理嵌套括号，但需要从 JSON 开始处调用
                # 找第一个 { 作为入口
                brace_pos = content2.find('{')
                if brace_pos >= 0:
                    try:
                        result, _ = json.JSONDecoder().raw_decode(content2[brace_pos:])
                    except Exception:
                        return None
                else:
                    return None
        
        # 验证必需字段（支持简化版和完整版）
        if 'sentiment' not in result:
            return None
        
        # 确保 summary 字段完整
        if 'summary' not in result:
            if 'impact_analysis' in result:
                result['summary'] = {
                    's_level_count': len([n for n in result['impact_analysis'] if n.get('impact_level') == 'S']),
                    'a_level_count': len([n for n in result['impact_analysis'] if n.get('impact_level') == 'A']),
                    'b_level_count': len([n for n in result['impact_analysis'] if n.get('impact_level') == 'B']),
                    'c_level_count': len([n for n in result['impact_analysis'] if n.get('impact_level') == 'C']),
                    'top_sectors': []
                }
            else:
                result['summary'] = {
                    's_level_count': 0, 'a_level_count': 0, 'b_level_count': 0, 'c_level_count': 0, 'top_sectors': []
                }
        
        return result
        
    except Exception as e:
        print(f"[综合分析] 异常：{e}", file=sys.stderr)
        return None


def _empty_result() -> dict:
    return {
        'sentiment': {
            'score': 50.0,
            'positive': 0,
            'negative': 0,
            'neutral': 0,
            'total': 0,
            'hot_concepts': [],
            'concept_scores': {}
        },
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
    
    print('=== 综合分析测试 ===\n')
    result = analyze_news_combined(test_news)
    
    print(f"\n情绪分数：{result['sentiment']['score']}")
    print(f"正面：{result['sentiment']['positive']} | 负面：{result['sentiment']['negative']}")
    print(f"\nS 级：{result['summary']['s_level_count']}")
    print(f"A 级：{result['summary']['a_level_count']}")
    print(f"B 级：{result['summary']['b_level_count']}")
    print(f"C 级：{result['summary']['c_level_count']}")
